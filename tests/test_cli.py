from travel_agent.app import cli
from travel_agent.config import Settings
from travel_agent.schemas.evidence import ToolCallRecord
from travel_agent.schemas.followup import FollowUpIntent
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.workflows.trip_planning import build_deterministic_trip_plan

import pytest


class _FakeStream:
    def __init__(self, *, reject: bool = False):
        self.calls = []
        self.reject = reject

    def reconfigure(self, **kwargs):
        if self.reject:
            raise ValueError("not reconfigurable")
        self.calls.append(kwargs)


def test_cli_configures_all_stdio_streams_as_utf8(monkeypatch):
    streams = [_FakeStream(), _FakeStream(), _FakeStream(reject=True)]
    monkeypatch.setattr(cli.sys, "stdin", streams[0])
    monkeypatch.setattr(cli.sys, "stdout", streams[1])
    monkeypatch.setattr(cli.sys, "stderr", streams[2])

    cli._configure_stdio_encoding()

    assert streams[0].calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert streams[1].calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert streams[2].calls == []


@pytest.mark.asyncio
async def test_failed_followup_restores_the_whole_previous_state_and_logs_error(monkeypatch):
    """Exercise the actual CLI loop without model or public-network calls."""

    request = TripRequest(destination="City", days=2, requested_services=["guide"])
    revised = TripRequest(destination="City", days=4, requested_services=["guide"], stated_fields=["days"])
    answers = iter(["make it 4 days", "why is this still 2 days?", ""])
    logs = []
    question_states = []
    executions = []

    monkeypatch.setattr(cli.sys, "argv", ["cli", "--follow-up", "--no-rag"])
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **kwargs: Settings(
        base_url="https://example.test/v1", api_key="fixture", model="fixture-model",
    )))
    for name in ("create_travel_agent", "create_intake_agent", "create_follow_up_router"):
        monkeypatch.setattr(cli, name, lambda **kwargs: object())

    async def initial():
        return "a 2-day guide to City"

    async def collect(*_args, **_kwargs):
        return request, "a 2-day guide to City"

    async def extract(*_args, **_kwargs):
        return revised

    async def read(_prompt):
        return next(answers)

    async def classify(_agent, text, **_kwargs):
        return FollowUpIntent(action="modify_request" if text.startswith("make") else "ask_about_plan")

    def execute(plan, **_kwargs):
        executions.append(plan.activity_query.days)
        return SearchResults(tool_calls=[ToolCallRecord(
            tool_name="optimize_day_route", provider="fixture", status="skipped", elapsed_ms=0,
        ) for _ in range(plan.activity_query.days)])

    async def plan(*_args, request_context, search_results, **_kwargs):
        if request_context.days == 4:
            raise RuntimeError("injected planner failure")
        return build_deterministic_trip_plan(request_context, search_results)

    async def answer(_agent, _question, current_request, results, current_plan, **_kwargs):
        question_states.append((current_request.days, len(results.tool_calls), current_plan.days))
        return "The previous plan is unchanged."

    monkeypatch.setattr(cli, "ask_initial_request", initial)
    monkeypatch.setattr(cli, "collect_trip_request", collect)
    monkeypatch.setattr(cli, "extract_trip_request", extract)
    monkeypatch.setattr(cli, "read_user_line", read)
    monkeypatch.setattr(cli, "classify_follow_up", classify)
    monkeypatch.setattr(cli, "execute_search_plan", execute)
    monkeypatch.setattr(cli, "plan_trip_structured", plan)
    monkeypatch.setattr(cli, "answer_plan_question", answer)
    monkeypatch.setattr(cli, "write_run_record", lambda _path, **record: logs.append(record))

    assert await cli.main() == 0
    assert executions == [2, 4]
    assert question_states == [(2, 2, 2)]
    failed_turn = next(record for record in logs if record["status"] == "error")
    assert "injected planner failure" in failed_turn["error"]
    assert failed_turn["request"].days == 4
    assert len(failed_turn["tool_results"].tool_calls) == 4
    assert failed_turn["final_plan"] is None
