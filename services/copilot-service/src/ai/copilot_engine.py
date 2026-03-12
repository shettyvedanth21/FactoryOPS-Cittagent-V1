import json
import logging
import re
from collections import Counter
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from src.ai.model_client import AIUnavailableError, ModelClient
from src.ai.prompt_templates import FORMATTER_SYSTEM_PROMPT, SQL_SYSTEM_PROMPT
from src.config import settings
from src.database import get_db_session
from src.db.query_engine import QueryEngine
from src.db.schema_loader import get_schema_context, get_schema_manifest
from src.integrations.data_service_client import DataServiceClient
from src.intent.router import QUICK_INTENTS, classify_intent, is_answerable_followup
from src.response.schema import Chart, ChartDataset, CopilotResponse, DataTable, PageLink
from src.templates.quick_questions import build_templates


DEVICE_ID_PATTERN = re.compile(r"\b(?=[A-Z0-9_-]*\d)[A-Z0-9_-]{3,}\b")
logger = logging.getLogger(__name__)
COPILOT_METRICS = Counter()


class CopilotEngine:
    def __init__(self, model_client: ModelClient):
        self.model_client = model_client
        self.query_engine = QueryEngine()
        self.data_service = DataServiceClient()

    async def process_question(
        self,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
    ) -> CopilotResponse:
        intent = classify_intent(message, history)

        if intent.intent == "unsupported":
            return CopilotResponse(
                answer="That module is not yet available in FactoryOPS.",
                reasoning="Requested module is outside currently integrated FactoryOPS data modules.",
                follow_up_suggestions=[
                    "Summarize today's factory performance",
                    "Which machine consumed the most power today?",
                    "Show recent alerts today",
                ],
                error_code="MODULE_NOT_AVAILABLE",
            )

        templates = build_templates(get_schema_manifest())

        if intent.intent in QUICK_INTENTS and intent.intent in templates:
            return await self._run_quick_template(
                intent=intent.intent,
                template=templates[intent.intent],
                message=message,
                history=history,
                tariff_rate=tariff_rate,
                currency=currency,
            )

        if intent.intent == "telemetry_trend":
            return await self._run_telemetry_trend(message=message, history=history)

        return await self._run_ai_sql(
            message=message,
            history=history,
            tariff_rate=tariff_rate,
            currency=currency,
        )

    async def _run_quick_template(
        self,
        intent: str,
        template,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
    ) -> CopilotResponse:
        if template.mode == "telemetry_top_energy_today":
            return await self._top_energy_today(template, tariff_rate, currency)

        result = await self.query_engine.execute_query(template.sql)
        if result.error:
            return CopilotResponse(
                answer="That question cannot be answered safely.",
                reasoning=f"Query blocked due to safety policy: {result.reason}",
                error_code=result.error,
            )
        if not result.rows:
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="Template query returned zero rows for the requested time window.",
                follow_up_suggestions=self._validated_followups(template.allowed_followups),
                error_code="NO_DATA",
            )

        payload = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "intent": intent,
        }
        deterministic_chart, chart_build_reason, chart_points = self._build_chart_from_rows(
            columns=result.columns,
            rows=result.rows,
            chart_type=template.chart_type,
            title=template.chart_title,
            x_key=template.chart_x_key,
            y_key=template.chart_y_key,
        )
        logger.info(
            "copilot_chart_build intent=%s template_id=%s chart_x_key=%s chart_y_key=%s chart_points=%s chart_build_status=%s",
            intent,
            template.intent,
            template.chart_x_key,
            template.chart_y_key,
            chart_points,
            "ok" if deterministic_chart else "failed",
        )
        if not deterministic_chart and chart_build_reason:
            COPILOT_METRICS["chart_build_failed"] += 1
            reasoning = (
                f"Source: ai_factoryops ({','.join(self._source_tables_for_intent(intent))}); "
                "Window: today; Metric: template-specific aggregate; Filters: template intent constraints. "
                f"Chart omitted: {chart_build_reason}"
            )
        else:
            reasoning = (
                f"Source: ai_factoryops ({','.join(self._source_tables_for_intent(intent))}); "
                "Window: today; Metric: template-specific aggregate; Filters: template intent constraints."
            )

        return await self._format_with_ai_or_fallback(
            message=message,
            payload=payload,
            reasoning=reasoning,
            chart_hint=template.chart_type,
            default_title=template.chart_title,
            default_followups=template.allowed_followups,
            tariff_rate=tariff_rate,
            currency=currency,
            force_chart=deterministic_chart,
            chart_omission_reason=chart_build_reason if deterministic_chart is None else None,
        )

    async def _top_energy_today(self, template, tariff_rate: float, currency: str) -> CopilotResponse:
        devices = await self._list_devices()
        if not devices:
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="No onboarded devices found in devices table.",
                error_code="NO_DATA",
            )

        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = datetime.now(timezone.utc)
        rows: list[list[Any]] = []

        for device in devices:
            device_id = device["device_id"]
            device_name = device["device_name"]
            try:
                telemetry = await self.data_service.fetch_telemetry(
                    device_id=device_id,
                    start=start,
                    end=end,
                    fields=["energy_kwh", "power"],
                    limit=5000,
                )
                items = telemetry.get("data", {}).get("items", [])
                values = [float(it.get("energy_kwh")) for it in items if it.get("energy_kwh") is not None]
                kwh = 0.0
                if len(values) >= 2:
                    kwh = max(0.0, max(values) - min(values))
                else:
                    power_points: list[tuple[datetime, float]] = []
                    for it in items:
                        power = it.get("power")
                        ts_raw = it.get("timestamp")
                        if power is None or ts_raw is None:
                            continue
                        try:
                            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                            power_points.append((ts, float(power)))
                        except Exception:
                            continue

                    if len(power_points) < 2:
                        continue

                    power_points.sort(key=lambda x: x[0])
                    watt_hours = 0.0
                    for i in range(1, len(power_points)):
                        prev_t, prev_p = power_points[i - 1]
                        cur_t, _ = power_points[i]
                        hours = max(0.0, (cur_t - prev_t).total_seconds() / 3600.0)
                        watt_hours += prev_p * hours
                    kwh = max(0.0, watt_hours / 1000.0)

                cost = round(kwh * tariff_rate, 2) if tariff_rate > 0 else None
                rows.append([device_id, device_name, round(kwh, 3), cost])
            except Exception:
                continue

        rows.sort(key=lambda x: x[2], reverse=True)
        if not rows:
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="Telemetry endpoint returned insufficient energy_kwh/power points for all devices today.",
                error_code="NO_DATA",
            )

        top = rows[0]
        total = sum(r[2] for r in rows)
        table_rows = []
        for row in rows[:10]:
            pct = round((row[2] / total) * 100, 2) if total > 0 else 0.0
            table_rows.append([row[1], row[2], row[3], pct])

        answer = (
            f"{top[1]} consumed the most energy today at {top[2]} kWh"
            + (f" ({currency} {top[3]:.2f})." if isinstance(top[3], (int, float)) else ".")
        )
        reasoning = (
            "Source: data-service telemetry (energy_kwh with power integration fallback) + devices table; "
            "Window: today (UTC start-of-day to now); "
            "Metric: max(energy_kwh)-min(energy_kwh) or integrated power over time per device; "
            "Filters: onboarded devices only."
        )
        chart = Chart(
            type="bar",
            title=template.chart_title,
            labels=[r[1] for r in rows[:10]],
            datasets=[ChartDataset(label="kWh", data=[r[2] for r in rows[:10]])],
        )
        page_links = [
            PageLink(label=f"View {top[1]}", route=f"/machines/{top[0]}"),
            PageLink(label="Open Energy Report", route="/reports"),
        ]

        followups = self._validated_followups(template.allowed_followups)

        return CopilotResponse(
            answer=answer,
            reasoning=reasoning,
            data_table=DataTable(
                headers=["Machine", "kWh", f"Cost {currency}", "% of Total"],
                rows=table_rows,
            ),
            chart=chart,
            page_links=page_links,
            follow_up_suggestions=followups,
        )

    async def _run_telemetry_trend(self, message: str, history: list[dict[str, str]]) -> CopilotResponse:
        device_id = await self._resolve_device_from_context(message, history)
        if not device_id:
            return CopilotResponse(
                answer="Please specify a device to analyze trend data.",
                reasoning="Telemetry trend intents require an explicit or contextual device identifier.",
                error_code="NO_DATA",
            )

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)

        try:
            payload = await self.data_service.fetch_telemetry(
                device_id=device_id,
                start=start,
                end=end,
                fields=["power"],
                limit=1500,
            )
            items = payload.get("data", {}).get("items", [])
            points = [i for i in items if i.get("power") is not None][:200]
            if not points:
                return CopilotResponse(
                    answer="No data found for this period.",
                    reasoning=f"No power telemetry points returned for device {device_id} in the last 7 days.",
                    error_code="NO_DATA",
                )

            labels = [str(p.get("timestamp", ""))[:16] for p in points]
            values = [float(p.get("power")) for p in points]
            answer = f"Showing power trend for {device_id} over the last 7 days."
            reasoning = (
                "Source: data-service telemetry endpoint; "
                "Window: last 7 days; Metric: power field points; Filters: selected device."
            )
            return CopilotResponse(
                answer=answer,
                reasoning=reasoning,
                data_table=DataTable(
                    headers=["Timestamp", "Power"],
                    rows=[[labels[i], values[i]] for i in range(min(len(labels), 30))],
                ),
                chart=Chart(
                    type="line",
                    title=f"Power Trend: {device_id}",
                    labels=labels,
                    datasets=[ChartDataset(label="Power", data=values)],
                ),
                page_links=[PageLink(label=f"View {device_id}", route=f"/machines/{device_id}")],
                follow_up_suggestions=self._validated_followups(
                    [
                        "Why did it spike at 3pm?",
                        "Show idle cost today for this machine",
                        "Show recent alerts for this machine",
                    ]
                ),
            )
        except Exception as exc:
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning=f"Telemetry fetch failed: {exc}",
                error_code="NO_DATA",
            )

    async def _run_ai_sql(
        self,
        message: str,
        history: list[dict[str, str]],
        tariff_rate: float,
        currency: str,
    ) -> CopilotResponse:
        history_text = "\n".join(
            f"{t.get('role', 'user').upper()}: {t.get('content', '')}" for t in history[-settings.max_history_turns :]
        )
        schema_context = get_schema_context()

        user_prompt = (
            f"SCHEMA:\n{schema_context}\n\n"
            f"CONVERSATION:\n{history_text}\n\n"
            f"USER QUESTION: {message}\n\n"
            "Write the query now."
        )

        try:
            sql = (await self.model_client.generate(
                messages=[
                    {"role": "system", "content": SQL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=settings.stage1_max_tokens,
            )).strip()
        except AIUnavailableError:
            return CopilotResponse(
                answer="AI service is temporarily unavailable. Please try again.",
                reasoning="Provider call failed during query generation stage.",
                error_code="AI_UNAVAILABLE",
            )

        if sql.upper() == "NO_DATA":
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="Query generation determined no reliable schema path for this request.",
                error_code="NO_DATA",
            )

        result = await self.query_engine.execute_query(sql)
        if result.error:
            return CopilotResponse(
                answer="That question cannot be answered safely.",
                reasoning=f"SQL safety validator blocked execution: {result.reason}",
                error_code=result.error,
            )
        if not result.rows:
            return CopilotResponse(
                answer="No data found for this period.",
                reasoning="Generated SQL returned zero rows.",
                error_code="NO_DATA",
            )

        payload = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "sql": sql,
        }
        return await self._format_with_ai_or_fallback(
            message=message,
            payload=payload,
            reasoning="Source: ai_factoryops MySQL query; Window/metric/filters based on generated SQL.",
            chart_hint="table",
            default_title="Query Results",
            default_followups=[
                "Show this as a 7-day trend",
                "Which machine contributed most to this result?",
                "Show recent alerts related to this machine",
            ],
            tariff_rate=tariff_rate,
            currency=currency,
        )

    async def _format_with_ai_or_fallback(
        self,
        message: str,
        payload: dict[str, Any],
        reasoning: str,
        chart_hint: str,
        default_title: str,
        default_followups: list[str],
        tariff_rate: float,
        currency: str,
        force_chart: Chart | None = None,
        chart_omission_reason: str | None = None,
    ) -> CopilotResponse:
        headers = payload.get("columns") or []
        rows = payload.get("rows") or []
        forced_table = DataTable(headers=headers, rows=rows[:50]) if headers and rows else None
        try:
            formatted_raw = await self.model_client.generate(
                messages=[
                    {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"USER QUESTION: {message}\n"
                            f"QUERY RESULTS JSON: {json.dumps(payload, default=str)}\n"
                            f"TARIFF: {currency} {tariff_rate}/kWh\n"
                            f"CHART_HINT: {chart_hint}\n"
                        ),
                    },
                ],
                max_tokens=settings.stage2_max_tokens,
            )
            parsed = json.loads(formatted_raw)

            response = CopilotResponse(**parsed)
            response.follow_up_suggestions = self._validated_followups(response.follow_up_suggestions or default_followups)
            response.data_table = response.data_table or forced_table
            if force_chart is not None:
                response.chart = force_chart
            elif chart_omission_reason:
                response.chart = None
            if not response.reasoning:
                response.reasoning = reasoning
            if chart_omission_reason and chart_omission_reason not in response.reasoning:
                response.reasoning = f"{response.reasoning} Chart omitted: {chart_omission_reason}"
            response.chart = self._sanitize_chart(response.chart)
            return response
        except json.JSONDecodeError as exc:
            COPILOT_METRICS["formatter_parse_failed"] += 1
            logger.warning("copilot_formatter_parse_failed error=%s", exc)
            return CopilotResponse(
                answer=self._fallback_answer(rows),
                reasoning=self._append_chart_omission(reasoning, chart_omission_reason),
                data_table=forced_table,
                chart=force_chart or self._fallback_chart(headers, rows, default_title, chart_hint),
                follow_up_suggestions=self._validated_followups(default_followups),
            )
        except Exception as exc:
            COPILOT_METRICS["formatter_parse_failed"] += 1
            logger.warning("copilot_formatter_validation_failed error=%s", exc)
            return CopilotResponse(
                answer=self._fallback_answer(rows),
                reasoning=self._append_chart_omission(reasoning, chart_omission_reason),
                data_table=forced_table,
                chart=force_chart or self._fallback_chart(headers, rows, default_title, chart_hint),
                follow_up_suggestions=self._validated_followups(default_followups),
            )

    @staticmethod
    def _fallback_answer(rows: list[list[Any]]) -> str:
        if not rows:
            return "No data found for this period."
        first = rows[0]
        return f"Found {len(rows)} records. Top row: {first}."

    @staticmethod
    def _fallback_chart(headers: list[str], rows: list[list[Any]], title: str, chart_hint: str) -> Chart | None:
        if chart_hint not in {"bar", "line"}:
            return None
        if len(headers) < 2 or not rows:
            return None
        fallback = CopilotEngine._build_chart_from_rows(
            columns=headers,
            rows=rows,
            chart_type=chart_hint,
            title=title,
            x_key=headers[0],
            y_key=headers[1],
        )
        return fallback[0]

    @staticmethod
    def _append_chart_omission(reasoning: str, chart_omission_reason: str | None) -> str:
        if not chart_omission_reason:
            return reasoning
        if chart_omission_reason in reasoning:
            return reasoning
        return f"{reasoning} Chart omitted: {chart_omission_reason}"

    @staticmethod
    def _sanitize_chart(chart: Chart | None) -> Chart | None:
        if chart is None:
            return None
        if not chart.datasets:
            return None
        dataset = chart.datasets[0]
        labels: list[str] = []
        values: list[float] = []
        for i, label in enumerate(chart.labels):
            if i >= len(dataset.data):
                break
            value = CopilotEngine._to_float(dataset.data[i])
            if value is None:
                continue
            labels.append(str(label))
            values.append(value)
        if not labels:
            return None
        return Chart(
            type=chart.type,
            title=chart.title,
            labels=labels,
            datasets=[ChartDataset(label=dataset.label, data=values)],
        )

    @staticmethod
    def _build_chart_from_rows(
        columns: list[str],
        rows: list[list[Any]],
        chart_type: str,
        title: str,
        x_key: str | None,
        y_key: str | None,
    ) -> tuple[Chart | None, str | None, int]:
        if chart_type not in {"bar", "line"}:
            return None, "chart type is not plottable", 0
        if not columns or not rows:
            return None, "no rows available for charting", 0
        if not x_key or not y_key:
            return None, "missing chart column mapping", 0

        x_idx = CopilotEngine._find_column_idx(columns, x_key)
        y_idx = CopilotEngine._find_column_idx(columns, y_key)
        if x_idx is None or y_idx is None:
            return None, f"mapped chart columns not present ({x_key}, {y_key})", 0

        labels: list[str] = []
        values: list[float] = []
        for row in rows[:50]:
            if x_idx >= len(row) or y_idx >= len(row):
                continue
            num = CopilotEngine._to_float(row[y_idx])
            if num is None:
                continue
            labels.append(str(row[x_idx]))
            values.append(num)

        if not labels:
            return None, f"no numeric data for '{y_key}'", 0

        return (
            Chart(
                type=chart_type,
                title=title,
                labels=labels,
                datasets=[ChartDataset(label=y_key, data=values)],
            ),
            None,
            len(values),
        )

    @staticmethod
    def _find_column_idx(columns: list[str], key: str) -> int | None:
        target = key.lower().strip()
        for idx, name in enumerate(columns):
            if str(name).lower().strip() == target:
                return idx
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, Decimal):
            return float(value)
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_device_from_text(text_value: str) -> str | None:
        match = DEVICE_ID_PATTERN.search(text_value.upper())
        return match.group(0) if match else None

    def _extract_device_from_history(self, history: list[dict[str, str]]) -> str | None:
        for turn in reversed(history):
            content = turn.get("content", "")
            match = DEVICE_ID_PATTERN.search(content.upper())
            if match:
                return match.group(0)
        return None

    async def _resolve_device_from_context(self, message: str, history: list[dict[str, str]]) -> str | None:
        devices = await self._list_devices()
        if not devices:
            return self._extract_device_from_text(message) or self._extract_device_from_history(history)

        direct_id = self._extract_device_from_text(message) or self._extract_device_from_history(history)
        if direct_id and any(d["device_id"].upper() == direct_id for d in devices):
            return direct_id

        haystacks = [message] + [turn.get("content", "") for turn in reversed(history[-settings.max_history_turns :])]
        for hay in haystacks:
            hay_upper = hay.upper()
            for device in devices:
                device_id = str(device.get("device_id", "")).upper()
                device_name = str(device.get("device_name", "")).upper()
                if not device_id:
                    continue
                if device_id in hay_upper:
                    return device_id
                if device_name and device_name in hay_upper:
                    return device_id
        return None

    @staticmethod
    def _source_tables_for_intent(intent: str) -> list[str]:
        mapping = {
            "factory_summary": ["devices", "idle_running_log"],
            "alerts_recent": ["alerts", "rules", "devices"],
            "idle_waste": ["idle_running_log", "devices"],
            "health_scores": ["devices"],
        }
        return mapping.get(intent, ["devices"])

    def _validated_followups(self, candidates: list[str]) -> list[str]:
        out: list[str] = []
        for candidate in candidates:
            if candidate and is_answerable_followup(candidate):
                out.append(candidate)
            if len(out) == 3:
                break
        return out

    async def _list_devices(self) -> list[dict[str, Any]]:
        async with get_db_session() as db:
            result = await db.execute(
                text("SELECT device_id, device_name FROM devices WHERE deleted_at IS NULL ORDER BY device_id LIMIT 200")
            )
            return [{"device_id": r[0], "device_name": r[1]} for r in result.fetchall()]
