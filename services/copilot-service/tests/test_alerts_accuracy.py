import asyncio

from src.ai.copilot_engine import CopilotEngine
from src.db.query_engine import QueryResult
from src.templates.quick_questions import QuickTemplate


class _DummyModelClient:
    async def generate(self, messages, max_tokens=1000):
        return "{}"


def _alerts_template() -> QuickTemplate:
    return QuickTemplate(
        intent="alerts_recent",
        mode="sql",
        sql="SELECT 1",
        chart_type="table",
        chart_title="Alerts Today",
        chart_x_key=None,
        chart_y_key=None,
        allowed_followups=["Which machine has the most alerts today?"],
    )


def test_alerts_recent_uses_alerts_source_when_rows_present():
    engine = CopilotEngine(_DummyModelClient())
    template = _alerts_template()

    async def fake_execute_query(sql: str):
        return QueryResult(
            columns=["device_id", "device_name", "rule_name", "severity", "status", "created_at"],
            rows=[["COMPRESSOR-001", "Compressor 001", "High Power", "high", "active", "2026-03-12 09:00:00"]],
            row_count=1,
        )

    engine.query_engine.execute_query = fake_execute_query  # type: ignore[assignment]
    response = asyncio.run(engine._run_alerts_recent("Show recent alerts today", template))

    assert "alert" in response.answer.lower()
    assert response.data_table is not None
    assert response.data_table.rows[0][1] == "Compressor 001"


def test_alerts_recent_falls_back_to_activity_events():
    engine = CopilotEngine(_DummyModelClient())
    template = _alerts_template()
    calls = {"n": 0}

    async def fake_execute_query(sql: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return QueryResult(columns=[], rows=[], row_count=0)
        return QueryResult(
            columns=["device_id", "device_name", "rule_name", "severity", "status", "created_at"],
            rows=[["COMPRESSOR-002", "Compressor 002", "Rule Created", "info", "rule_created", "2026-03-12 09:29:06"]],
            row_count=1,
        )

    engine.query_engine.execute_query = fake_execute_query  # type: ignore[assignment]
    response = asyncio.run(engine._run_alerts_recent("Show recent alerts today", template))

    assert "activity event" in response.answer.lower()
    assert response.data_table is not None
    assert response.data_table.rows[0][1] == "Compressor 002"


def test_most_alerts_query_returns_grouped_counts():
    engine = CopilotEngine(_DummyModelClient())
    template = _alerts_template()
    calls = {"n": 0}

    async def fake_execute_query(sql: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return QueryResult(columns=[], rows=[], row_count=0)
        return QueryResult(
            columns=["device_id", "device_name", "rule_name", "severity", "status", "created_at"],
            rows=[
                ["COMPRESSOR-002", "Compressor 002", "Rule Created", "info", "rule_created", "2026-03-12 09:29:06"],
                ["COMPRESSOR-002", "Compressor 002", "Rule Triggered", "info", "rule_triggered", "2026-03-12 09:31:06"],
                ["COMPRESSOR-001", "Compressor 001", "Rule Triggered", "info", "rule_triggered", "2026-03-12 09:32:06"],
            ],
            row_count=3,
        )

    engine.query_engine.execute_query = fake_execute_query  # type: ignore[assignment]
    response = asyncio.run(engine._run_alerts_recent("Which machine has the most alerts today?", template))

    assert "most" in response.answer.lower()
    assert response.data_table is not None
    assert response.data_table.headers == ["Machine", "Count", "Source"]
    assert response.data_table.rows[0][0] == "Compressor 002"
    assert int(response.data_table.rows[0][1]) == 2
