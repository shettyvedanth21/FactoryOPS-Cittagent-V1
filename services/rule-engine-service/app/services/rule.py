"""Rule service layer - business logic."""

from typing import Optional, List
from uuid import UUID
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import (
    Rule,
    RuleStatus,
    RuleScope,
    Alert,
    RuleType,
    CooldownMode,
)
from app.repositories.rule import RuleRepository, AlertRepository, ActivityEventRepository
from app.schemas.rule import (
    RuleCreate,
    RuleUpdate,
    RuleStatus as RuleStatusEnum,
    RuleType as RuleTypeSchema,
)

logger = logging.getLogger(__name__)


class RuleService:
    """Service layer for rule management business logic."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repository = RuleRepository(session)
        self._activity_service = ActivityEventService(session)

    async def create_rule(self, rule_data: RuleCreate) -> Rule:

        if not rule_data.notification_channels:
            raise ValueError("At least one notification channel is required")

        if rule_data.rule_type == RuleTypeSchema.THRESHOLD:
            if rule_data.property is None or rule_data.condition is None or rule_data.threshold is None:
                raise ValueError("property, condition and threshold are required for threshold rules")
        elif rule_data.rule_type == RuleTypeSchema.TIME_BASED:
            if not rule_data.time_window_start or not rule_data.time_window_end:
                raise ValueError("time_window_start and time_window_end are required for time-based rules")

        rule = Rule(
            tenant_id=rule_data.tenant_id,
            rule_name=rule_data.rule_name,
            description=rule_data.description,

            # store STRING in DB
            scope=rule_data.scope.value,
            device_ids=rule_data.device_ids,
            property=rule_data.property,

            # store STRING in DB
            condition=rule_data.condition.value if rule_data.condition else None,

            threshold=rule_data.threshold,
            rule_type=rule_data.rule_type.value,
            cooldown_mode=rule_data.cooldown_mode.value,
            time_window_start=rule_data.time_window_start,
            time_window_end=rule_data.time_window_end,
            timezone=rule_data.timezone,
            time_condition=rule_data.time_condition.value if rule_data.time_condition else None,
            triggered_once=False,

            # store STRING in DB
            status=RuleStatus.ACTIVE.value,

            notification_channels=[ch.value for ch in rule_data.notification_channels],
            cooldown_minutes=rule_data.cooldown_minutes,
        )

        created_rule = await self._repository.create(rule)
        await self._session.commit()

        logger.info(
            "Rule created successfully",
            extra={
                "rule_id": str(created_rule.rule_id),
                "rule_name": created_rule.rule_name,
                "scope": created_rule.scope,
                "device_count": len(created_rule.device_ids),
            }
        )

        try:
            await self._activity_service.create_for_rule(
                rule=created_rule,
                event_type="rule_created",
                title="Rule Created",
                message=f"Rule '{created_rule.rule_name}' created for property '{created_rule.property}'.",
                metadata_json={
                    "property": created_rule.property,
                    "condition": created_rule.condition,
                    "threshold": created_rule.threshold,
                    "scope": created_rule.scope,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist rule_created activity event", extra={"error": str(exc)})

        return created_rule

    async def get_rule(
        self,
        rule_id: UUID,
        tenant_id: Optional[str] = None
    ) -> Optional[Rule]:

        return await self._repository.get_by_id(str(rule_id), tenant_id)

    async def list_rules(
        self,
        tenant_id: Optional[str] = None,
        status: Optional[RuleStatusEnum] = None,
        device_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Rule], int]:

        status_value = status.value if status else None

        return await self._repository.list_rules(
            tenant_id=tenant_id,
            status=status_value,
            device_id=device_id,
            page=page,
            page_size=page_size,
        )

    async def update_rule(
        self,
        rule_id: UUID,
        rule_data: RuleUpdate,
        tenant_id: Optional[str] = None,
    ) -> Optional[Rule]:

        rule = await self._repository.get_by_id(str(rule_id), tenant_id)
        if not rule:
            logger.warning(
                "Attempted to update non-existent rule",
                extra={"rule_id": str(rule_id)}
            )
            return None

        update_data = rule_data.model_dump(exclude_unset=True)

        for field, value in update_data.items():

            if field == "scope" and value:
                value = value.value

            elif field == "condition" and value:
                value = value.value

            elif field == "notification_channels" and value:
                value = [ch.value for ch in value]
            elif field == "rule_type" and value:
                value = value.value
            elif field == "cooldown_mode" and value:
                value = value.value
            elif field == "time_condition" and value:
                value = value.value

            setattr(rule, field, value)

        if rule.scope == RuleScope.SELECTED_DEVICES.value and not rule.device_ids:
            raise ValueError("device_ids is required when scope is 'selected_devices'")

        if rule.rule_type == RuleType.TIME_BASED.value:
            if not rule.time_window_start or not rule.time_window_end:
                raise ValueError("time_window_start and time_window_end are required for time-based rules")
            rule.property = None
            rule.condition = None
            rule.threshold = None
            if not rule.time_condition:
                rule.time_condition = "running_in_window"
        else:
            if rule.property is None or rule.condition is None or rule.threshold is None:
                raise ValueError("property, condition and threshold are required for threshold rules")
            rule.time_window_start = None
            rule.time_window_end = None
            rule.time_condition = None

        # Manual reset for no-repeat when core condition is edited
        if rule.cooldown_mode == CooldownMode.NO_REPEAT.value:
            core_keys = {
                "rule_type",
                "property",
                "condition",
                "threshold",
                "scope",
                "device_ids",
                "time_window_start",
                "time_window_end",
                "timezone",
                "time_condition",
            }
            if any(k in update_data for k in core_keys):
                rule.triggered_once = False

        updated_rule = await self._repository.update(rule)
        await self._session.commit()

        logger.info(
            "Rule updated successfully",
            extra={"rule_id": str(updated_rule.rule_id)}
        )

        try:
            await self._activity_service.create_for_rule(
                rule=updated_rule,
                event_type="rule_updated",
                title="Rule Updated",
                message=f"Rule '{updated_rule.rule_name}' was updated.",
                metadata_json={
                    "property": updated_rule.property,
                    "condition": updated_rule.condition,
                    "threshold": updated_rule.threshold,
                    "scope": updated_rule.scope,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist rule_updated activity event", extra={"error": str(exc)})

        return updated_rule

    async def update_rule_status(
        self,
        rule_id: UUID,
        status: RuleStatusEnum,
        tenant_id: Optional[str] = None,
    ) -> Optional[Rule]:

        rule = await self._repository.update_status(
            str(rule_id),
            status.value
        )

        if rule:
            # Manual reset: pause -> active unlocks no-repeat rules
            if status.value == RuleStatus.ACTIVE.value and rule.cooldown_mode == CooldownMode.NO_REPEAT.value:
                rule.triggered_once = False
            await self._session.commit()
            logger.info(
                "Rule status updated",
                extra={
                    "rule_id": str(rule_id),
                    "new_status": status.value,
                }
            )
            try:
                await self._activity_service.create_for_rule(
                    rule=rule,
                    event_type="rule_status_changed",
                    title="Rule Status Changed",
                    message=f"Rule '{rule.rule_name}' status changed to '{status.value}'.",
                    metadata_json={"status": status.value},
                )
            except Exception as exc:
                logger.warning("Failed to persist rule_status_changed activity event", extra={"error": str(exc)})
        return rule

    async def delete_rule(
        self,
        rule_id: UUID,
        tenant_id: Optional[str] = None,
        soft: bool = True,
    ) -> bool:

        rule = await self._repository.get_by_id(str(rule_id), tenant_id)
        if not rule:
            return False

        await self._repository.delete(rule, soft=soft)
        await self._session.commit()

        logger.info(
            "Rule deleted successfully",
            extra={
                "rule_id": str(rule_id),
                "soft_delete": soft,
            }
        )

        try:
            await self._activity_service.create_for_rule(
                rule=rule,
                event_type="rule_deleted" if not soft else "rule_archived",
                title="Rule Deleted" if not soft else "Rule Archived",
                message=f"Rule '{rule.rule_name}' was {'deleted' if not soft else 'archived'}.",
                metadata_json={"soft_delete": soft},
            )
        except Exception as exc:
            logger.warning("Failed to persist rule delete activity event", extra={"error": str(exc)})

        return True

    async def get_active_rules_for_device(
        self,
        device_id: str,
        tenant_id: Optional[str] = None,
    ) -> List[Rule]:

        return await self._repository.get_active_rules_for_device(device_id, tenant_id)


class AlertService:
    """Service layer for alert management."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repository = AlertRepository(session)
        self._activity_service = ActivityEventService(session)

    async def create_alert(
        self,
        rule: Rule,
        device_id: str,
        actual_value: float,
        severity: str = "medium",
    ) -> Alert:
        is_time_based = rule.rule_type == RuleType.TIME_BASED.value
        threshold_value = rule.threshold if rule.threshold is not None else (1.0 if is_time_based else 0.0)
        if is_time_based:
            condition_text = f"running in window {rule.time_window_start}-{rule.time_window_end} IST"
        else:
            condition_text = f"{rule.property} {rule.condition} {rule.threshold}"

        message = (
            f"Rule '{rule.rule_name}' triggered for device {device_id}: "
            f"{condition_text} "
            f"(actual: {actual_value})"
        )

        alert = Alert(
            tenant_id=rule.tenant_id,
            rule_id=rule.rule_id,
            device_id=device_id,
            severity=severity,
            message=message,
            actual_value=actual_value,
            threshold_value=threshold_value,
            status="open",
        )

        created_alert = await self._repository.create(alert)
        await self._session.commit()

        logger.info(
            "Alert created",
            extra={
                "alert_id": str(created_alert.alert_id),
                "rule_id": str(rule.rule_id),
                "device_id": device_id,
            }
        )

        try:
            await self._activity_service.create_event(
                event_type="rule_triggered",
                title="Rule Triggered",
                message=(
                    f"Rule '{rule.rule_name}' triggered: {rule.property} {rule.condition} "
                    f"{rule.threshold} (actual: {actual_value})."
                ),
                tenant_id=rule.tenant_id,
                device_id=device_id,
                rule_id=str(rule.rule_id),
                alert_id=str(created_alert.alert_id),
                metadata_json={
                    "property": rule.property,
                    "condition": rule.condition,
                    "threshold": rule.threshold,
                    "actual_value": actual_value,
                    "severity": severity,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist rule_triggered activity event", extra={"error": str(exc)})

        return created_alert


class ActivityEventService:
    """Service layer for activity events."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repository = ActivityEventRepository(session)

    async def create_event(
        self,
        *,
        event_type: str,
        title: str,
        message: str,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        metadata_json: Optional[dict] = None,
    ):
        event = await self._repository.create(
            event_type=event_type,
            title=title,
            message=message,
            tenant_id=tenant_id,
            device_id=device_id,
            rule_id=rule_id,
            alert_id=alert_id,
            metadata_json=metadata_json,
        )
        await self._session.commit()
        return event

    async def create_for_rule(
        self,
        *,
        rule: Rule,
        event_type: str,
        title: str,
        message: str,
        metadata_json: Optional[dict] = None,
    ) -> None:
        target_devices: List[Optional[str]] = list(rule.device_ids or [])
        if rule.scope == RuleScope.ALL_DEVICES.value or not target_devices:
            target_devices = [None]

        for device_id in target_devices:
            await self._repository.create(
                event_type=event_type,
                title=title,
                message=message,
                tenant_id=rule.tenant_id,
                device_id=device_id,
                rule_id=str(rule.rule_id),
                metadata_json=metadata_json or {},
            )
        await self._session.commit()
