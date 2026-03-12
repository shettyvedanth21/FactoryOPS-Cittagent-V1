from dataclasses import dataclass
from typing import Any


@dataclass
class QuickTemplate:
    intent: str
    mode: str  # sql | telemetry_top_energy_today
    sql: str | None
    chart_type: str
    chart_title: str
    chart_x_key: str | None
    chart_y_key: str | None
    allowed_followups: list[str]


def build_templates(manifest: dict[str, Any]) -> dict[str, QuickTemplate]:
    tables = set(manifest.get("tables", {}).keys())
    templates: dict[str, QuickTemplate] = {}

    if {"devices", "idle_running_log"}.issubset(tables):
        templates["factory_summary"] = QuickTemplate(
            intent="factory_summary",
            mode="sql",
            sql=(
                "SELECT d.device_id, d.device_name, d.legacy_status, d.last_seen_timestamp, "
                "COALESCE(i.idle_duration_sec/3600, 0) AS idle_hours_today, "
                "COALESCE(i.idle_energy_kwh, 0) AS idle_kwh_today "
                "FROM devices d "
                "LEFT JOIN idle_running_log i ON d.device_id = i.device_id "
                "AND DATE(i.period_start)=CURDATE() "
                "ORDER BY idle_kwh_today DESC LIMIT 50"
            ),
            chart_type="bar",
            chart_title="Idle Energy Today by Machine",
            chart_x_key="device_id",
            chart_y_key="idle_kwh_today",
            allowed_followups=[
                "Which machine has the highest idle cost today?",
                "Show recent alerts for the top idle machine",
                "Show energy trend for this machine",
            ],
        )

    if {"devices", "alerts", "rules"}.issubset(tables):
        templates["alerts_recent"] = QuickTemplate(
            intent="alerts_recent",
            mode="sql",
            sql=(
                "SELECT a.device_id, d.device_name, r.rule_name, a.severity, a.status, a.created_at "
                "FROM alerts a "
                "JOIN devices d ON d.device_id=a.device_id "
                "JOIN rules r ON r.rule_id=a.rule_id "
                "WHERE a.created_at >= CURDATE() "
                "AND a.created_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY) "
                "ORDER BY a.created_at DESC LIMIT 20"
            ),
            chart_type="table",
            chart_title="Alerts Today",
            chart_x_key=None,
            chart_y_key=None,
            allowed_followups=[
                "Which rule triggered most this week?",
                "Show unresolved alerts only",
                "Which machine has the most alerts today?",
            ],
        )

    if {"devices", "idle_running_log", "tariff_config"}.issubset(tables):
        templates["idle_waste"] = QuickTemplate(
            intent="idle_waste",
            mode="sql",
            sql=(
                "SELECT d.device_id, d.device_name, i.idle_duration_sec/3600 AS idle_hours, "
                "i.idle_energy_kwh, i.idle_cost, i.currency "
                "FROM idle_running_log i "
                "JOIN devices d ON d.device_id=i.device_id "
                "WHERE DATE(i.period_start)=CURDATE() AND i.idle_duration_sec>0 "
                "ORDER BY i.idle_cost DESC LIMIT 50"
            ),
            chart_type="bar",
            chart_title="Idle Cost Today by Machine",
            chart_x_key="device_id",
            chart_y_key="idle_cost",
            allowed_followups=[
                "Why is this machine idle so long?",
                "Show standby loss this week",
                "Open waste analysis report",
            ],
        )

    if "devices" in tables:
        templates["health_scores"] = QuickTemplate(
            intent="health_scores",
            mode="sql",
            sql=(
                "SELECT device_id, device_name, legacy_status, last_seen_timestamp "
                "FROM devices ORDER BY last_seen_timestamp ASC LIMIT 50"
            ),
            chart_type="table",
            chart_title="Machines by Last Seen",
            chart_x_key=None,
            chart_y_key=None,
            allowed_followups=[
                "Which machine was offline longest today?",
                "Show recent alerts for the first machine",
                "Show performance trend for this machine",
            ],
        )

    templates["top_energy_today"] = QuickTemplate(
        intent="top_energy_today",
        mode="telemetry_top_energy_today",
        sql=None,
        chart_type="bar",
        chart_title="Energy Today by Machine",
        chart_x_key="device_name",
        chart_y_key="kWh",
        allowed_followups=[
            "Why did it spike at 3pm?",
            "Show this machine trend for last 7 days",
            "What is this machine idle cost today?",
        ],
    )

    return templates
