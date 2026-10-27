"""Contracts for follow-up turns, memory scope, and failure reporting."""

import pytest

from travel_agent.app.cli import (
    build_clarification_question,
    friendly_error,
    render_failure_summary,
)
from travel_agent.memory.store import durable_preferences
from travel_agent.schemas.evidence import ToolCallRecord
from travel_agent.schemas.followup import FollowUpIntent
from travel_agent.schemas.request import PreferenceSpec, TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.workflows.followup import classify_follow_up, looks_like_end


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.text = ""


class FakeRouter:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = 0

    async def run(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FakeResponse(self.value)


# --- Routing ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["退出", "谢谢", "no", "Done.", "再见"])
def test_obvious_exits_need_no_model_call(text):
    assert looks_like_end(text)


@pytest.mark.parametrize("text", ["改成4天", "第二天写详细点", "为什么没安排东京塔"])
def test_real_instructions_are_not_treated_as_exits(text):
    assert not looks_like_end(text)


@pytest.mark.asyncio
async def test_classifier_returns_the_typed_action():
    router = FakeRouter(FollowUpIntent(action="refine_narrative", reason="wording"))

    intent = await classify_follow_up(router, "写详细一点")

    assert intent.action == "refine_narrative"


@pytest.mark.asyncio
async def test_classifier_failure_degrades_to_replanning():
    # Guessing refine_narrative would silently drop a real date change, so an
    # unavailable classifier must fall back to the slower, correct branch.
    router = FakeRouter(error=TimeoutError("router down"))

    intent = await classify_follow_up(router, "改成4天")

    assert intent.action == "modify_request"


# --- Memory scope ----------------------------------------------------------


def test_memory_keeps_durable_tastes_and_drops_trip_specific_places():
    request = TripRequest(
        destination="Tokyo",
        requested_services=["guide"],
        preferences=["东京塔", "动漫", "素食"],
        preference_details=[
            PreferenceSpec(label="东京塔", search_query="Tokyo Tower", kind="place"),
            PreferenceSpec(label="动漫", search_query="anime", kind="interest"),
            PreferenceSpec(label="素食", search_query="vegetarian", kind="food"),
        ],
    )

    assert durable_preferences(request) == ["动漫", "素食"]


def test_memory_drops_a_preference_with_no_matching_detail():
    request = TripRequest(
        destination="Tokyo",
        requested_services=["guide"],
        preferences=["未知偏好"],
    )

    assert durable_preferences(request) == []


# --- Failure localization --------------------------------------------------


def test_failure_summary_names_the_failing_stage():
    results = SearchResults(
        tool_calls=[
            ToolCallRecord(
                tool_name="search_attractions",
                status="unavailable",
                elapsed_ms=12.0,
                provider="Overpass API",
                stage="places",
                error_code="timeout",
                error_category="provider_transient",
                retryable=True,
            ),
            ToolCallRecord(
                tool_name="weather",
                status="ok",
                elapsed_ms=8.0,
                provider="Open-Meteo",
                stage="weather",
            ),
        ]
    )

    summary = render_failure_summary(results)

    assert "stage=places" in summary
    assert "search_attractions" in summary
    assert "provider_transient" in summary
    assert "weather" not in summary.replace("stage=places", "")


def test_failure_summary_is_explicit_when_nothing_failed():
    assert "无" in render_failure_summary(SearchResults())


# --- User-facing wording ---------------------------------------------------


def test_clarification_asks_a_question_not_a_field_name():
    request = TripRequest(destination="Tokyo", requested_services=["flights"])

    question = build_clarification_question(request)

    assert "？" in question
    assert "missing_fields" not in question


def test_friendly_error_explains_a_timeout_without_a_stack_trace():
    message = friendly_error(TimeoutError("read timed out"))

    assert "超时" in message
    assert "Traceback" not in message
