"""Rule evaluation engine for real-time telemetry processing."""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import Rule, RuleType, CooldownMode
from app.schemas.rule import EvaluationResult, TelemetryPayload
from app.schemas.telemetry import TelemetryIn
from app.services.rule import RuleService, AlertService
from app.repositories.rule import RuleRepository, AlertRepository
from app.notifications.adapter import NotificationAdapter

logger = logging.getLogger(__name__)


class RuleEvaluator:

    def __init__(self, session: AsyncSession):
        self._session = session
        self._rule_service = RuleService(session)
        self._alert_service = AlertService(session)
        self._rule_repository = RuleRepository(session)
        self._alert_repository = AlertRepository(session)
        self._notification_adapter = NotificationAdapter()

    async def _is_rule_blocked_by_cooldown(self, rule: Rule, device_id: str) -> bool:
        """
        Evaluate cooldown/no-repeat per rule+device.
        This prevents triggers from one device suppressing others.
        """
        latest = await self._alert_repository.latest_for_rule_device(
            rule_id=str(rule.rule_id),
            device_id=device_id,
        )
        if not latest or not latest.created_at:
            return False

        latest_created = latest.created_at
        if latest_created.tzinfo is None:
            latest_created = latest_created.replace(tzinfo=timezone.utc)

        if rule.cooldown_mode == CooldownMode.NO_REPEAT.value:
            # Manual reset is represented by rule.updated_at moving forward
            # (pause->active or edit). Only block if alert happened after reset point.
            reset_at = rule.updated_at or rule.created_at
            if reset_at is None:
                return True
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=timezone.utc)
            return latest_created >= reset_at

        cooldown_minutes = max(int(rule.cooldown_minutes or 0), 0)
        if cooldown_minutes == 0:
            return False
        return datetime.now(timezone.utc) < (latest_created + timedelta(minutes=cooldown_minutes))

    async def evaluate_telemetry(
        self,
        telemetry: TelemetryPayload,
        tenant_id: Optional[str] = None,
    ) -> tuple[int, int, List[EvaluationResult]]:

        device_id = telemetry.device_id

        rules = await self._rule_service.get_active_rules_for_device(
            device_id=device_id,
            tenant_id=tenant_id,
        )

        if not rules:
            logger.debug(
                "No active rules for device",
                extra={"device_id": device_id},
            )
            return 0, 0, []

        triggered_rules: List[EvaluationResult] = []

        for rule in rules:
            if await self._is_rule_blocked_by_cooldown(rule, device_id):
                continue

            result = await self._evaluate_single_rule(rule, telemetry)

            if result.triggered:
                triggered_rules.append(result)

                await self._alert_service.create_alert(
                    rule=rule,
                    device_id=device_id,
                    actual_value=result.actual_value,
                    severity=self._determine_severity(rule, result.actual_value),
                )

                await self._rule_repository.update_last_triggered(rule.rule_id)
                if rule.cooldown_mode == "no_repeat":
                    rule.triggered_once = True

                await self._send_notifications(rule, device_id, result)

        await self._session.commit()

        logger.info(
            "Rule evaluation completed",
            extra={
                "device_id": device_id,
                "rules_evaluated": len(rules),
                "rules_triggered": len(triggered_rules),
            },
        )

        return len(rules), len(triggered_rules), triggered_rules

    async def _evaluate_single_rule(
        self,
        rule: Rule,
        telemetry: TelemetryPayload,
    ) -> EvaluationResult:
        if rule.rule_type == RuleType.TIME_BASED.value:
            triggered, actual_value = self._evaluate_time_based_rule(rule, telemetry)
            condition = "running_in_window"
            threshold = 1.0
        else:
            actual_value = self._extract_property_value(telemetry, rule.property or "")
            triggered = self._evaluate_condition(
                actual_value=actual_value,
                threshold=rule.threshold if rule.threshold is not None else 0.0,
                operator=rule.condition or "=",
            )
            condition = rule.condition or "="
            threshold = rule.threshold if rule.threshold is not None else 0.0

        message = None
        if triggered:
            if rule.rule_type == RuleType.TIME_BASED.value:
                message = (
                    f"Device running during restricted window "
                    f"{rule.time_window_start}-{rule.time_window_end} IST"
                )
            else:
                message = (
                    f"{rule.property} is {actual_value} "
                    f"(threshold: {rule.condition} {rule.threshold})"
                )

        return EvaluationResult(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            triggered=triggered,
            actual_value=actual_value,
            threshold=threshold,
            condition=condition,
            message=message,
        )

    def _evaluate_time_based_rule(
        self,
        rule: Rule,
        telemetry: TelemetryPayload,
    ) -> tuple[bool, float]:
        if not rule.time_window_start or not rule.time_window_end:
            return False, 0.0

        if not self._is_running_signal(telemetry):
            return False, 0.0

        if self._is_timestamp_in_window_ist(telemetry.timestamp, rule.time_window_start, rule.time_window_end):
            return True, 1.0

        return False, 0.0

    def _is_running_signal(self, telemetry: TelemetryPayload) -> bool:
        dynamic_fields = telemetry.get_dynamic_fields()

        power = dynamic_fields.get("power")
        if power is None:
            power = dynamic_fields.get("active_power")
        if power is not None:
            return power > 0

        current = dynamic_fields.get("current")
        if current is None:
            return False

        voltage = dynamic_fields.get("voltage")
        if voltage is None:
            return current > 0

        return current > 0 and voltage > 0

    def _is_timestamp_in_window_ist(self, timestamp: datetime, start_hhmm: str, end_hhmm: str) -> bool:
        tz = ZoneInfo("Asia/Kolkata")
        ts = timestamp.astimezone(tz) if timestamp.tzinfo else timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        current_minutes = ts.hour * 60 + ts.minute

        start_h, start_m = (int(v) for v in start_hhmm.split(":"))
        end_h, end_m = (int(v) for v in end_hhmm.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def _extract_property_value(
        self,
        telemetry: TelemetryPayload,
        property_name: str,
    ) -> float:
        
        dynamic_fields = telemetry.get_dynamic_fields()
        
        if property_name in dynamic_fields:
            return dynamic_fields[property_name]
        
        value = getattr(telemetry, property_name, None)
        if value is not None and isinstance(value, (int, float)):
            return float(value)
        
        raise ValueError(f"Unknown property: {property_name}")

    def _evaluate_condition(
        self,
        actual_value: float,
        threshold: float,
        operator: str,
    ) -> bool:

        operators = {
            ">": lambda a, t: a > t,
            "<": lambda a, t: a < t,
            "==": lambda a, t: a == t,
            "=": lambda a, t: a == t,
            "!=": lambda a, t: a != t,
            ">=": lambda a, t: a >= t,
            "<=": lambda a, t: a <= t,
        }

        if operator not in operators:
            raise ValueError(f"Unknown operator: {operator}")

        return operators[operator](actual_value, threshold)

    def _determine_severity(self, rule: Rule, actual_value: float) -> str:
        if rule.rule_type == RuleType.TIME_BASED.value:
            return "medium"

        if not rule.threshold or rule.threshold == 0:
            deviation = abs(actual_value)
        else:
            deviation = abs((actual_value - rule.threshold) / rule.threshold)

        if deviation > 0.5:
            return "critical"
        elif deviation > 0.25:
            return "high"
        elif deviation > 0.1:
            return "medium"
        else:
            return "low"

    async def _send_notifications(
        self,
        rule: Rule,
        device_id: str,
        result: EvaluationResult,
    ) -> None:

        if not rule.notification_channels:
            return

        message = (
            f"🚨 Alert: {rule.rule_name}\n"
            f"Device: {device_id}\n"
            f"Condition: "
            f"{'running in restricted window' if rule.rule_type == RuleType.TIME_BASED.value else f'{rule.property} {rule.condition} {rule.threshold}'}\n"
            f"Actual: {result.actual_value}\n"
            f"Time: {datetime.utcnow().isoformat()}"
        )

        for channel in rule.notification_channels:
            try:
                await self._notification_adapter.send(
                    channel=channel,
                    message=message,
                    rule=rule,
                    device_id=device_id,
                )
                logger.info(
                    "Notification sent",
                    extra={
                        "channel": channel,
                        "rule_id": str(rule.rule_id),
                        "device_id": device_id,
                    },
                )
            except Exception as e:
                logger.error(
                    "Failed to send notification",
                    extra={
                        "channel": channel,
                        "rule_id": str(rule.rule_id),
                        "error": str(e),
                    },
                )

    async def evaluate(
        self,
        telemetry: TelemetryIn,
    ) -> List[Rule]:

        device_id = telemetry.device_id
        metric = telemetry.metric
        value = telemetry.value

        rules = await self._rule_repository.get_active_rules_for_device(device_id)

        matched_rules: List[Rule] = []

        for rule in rules:

            if rule.property != metric:
                continue

            if self._evaluate_condition(value, rule.threshold, rule.condition):
                matched_rules.append(rule)

        logger.debug(
            "Simple evaluation completed",
            extra={
                "device_id": device_id,
                "metric": metric,
                "value": value,
                "rules_evaluated": len(rules),
                "rules_matched": len(matched_rules),
            },
        )

        return matched_rules
