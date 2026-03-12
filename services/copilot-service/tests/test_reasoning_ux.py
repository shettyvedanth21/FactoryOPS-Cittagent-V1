import asyncio
from decimal import Decimal

from src.ai.copilot_engine import CopilotEngine
from src.ai.reasoning_composer import ReasoningComposer


class _NonJsonModelClient:
    async def generate(self, messages, max_tokens=1000):
        return "not valid json"


def test_reasoning_composer_sections_present():
    composed = ReasoningComposer.for_query_result(
        intent="factory_summary",
        message="Summarize today's factory performance",
        columns=["device_id", "device_name", "legacy_status", "idle_kwh_today"],
        rows=[["COMPRESSOR-003", "Compressor 003", "active", Decimal("1.642005")]],
    )
    assert composed.sections.what_happened
    assert composed.sections.why_it_matters
    assert composed.sections.how_calculated


def test_fallback_answer_has_no_python_repr():
    engine = CopilotEngine(_NonJsonModelClient())
    payload = {
        "columns": ["device_id", "device_name", "idle_cost"],
        "rows": [["COMPRESSOR-003", "Compressor 003", Decimal("13.9570")]],
        "row_count": 1,
    }

    response = asyncio.run(
        engine._format_with_ai_or_fallback(
            message="Which machine has the highest idle cost today?",
            payload=payload,
            reasoning="",
            chart_hint="bar",
            default_title="Idle Cost Today by Machine",
            default_followups=["Show standby loss this week"],
            tariff_rate=8.5,
            currency="INR",
            intent="idle_waste",
        )
    )
    assert "Decimal(" not in response.answer
    assert "datetime.datetime(" not in response.answer
    assert response.reasoning_sections is not None


def test_blocked_response_is_helpful_and_has_followups():
    engine = CopilotEngine(_NonJsonModelClient())
    response = engine._blocked_response(
        ["Summarize today's factory performance", "Show recent alerts today", "Show recent alerts today"]
    )
    assert "couldn’t run that safely" in response.answer
    assert response.reasoning_sections is not None
    assert len(response.follow_up_suggestions) == 2

