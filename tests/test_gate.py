"""The deterministic invariants must change behavior, not only be printed."""

import pytest

from travel_agent.schemas.request import (
    ORIGIN,
    TripRequest,
)
from travel_agent.schemas.trip import FlightOption
from travel_agent.workflows.gate import plan_with_invariant_gate
from travel_agent.workflows.intake import drop_uncited_fields
from travel_agent.workflows.search import build_search_plan, execute_search_plan
from travel_agent.workflows.trip_planning import build_deterministic_trip_plan


GHOST = FlightOption(
    origin="Tokyo",
    destination="Maldives",
    date="2099-10-29",
    airline="Ghost Air",
    departure="09:00",
    arrival="12:00",
    price="1",
)


@pytest.fixture
def fixture_plan(monkeypatch):
    """A guide-only request whose tool evidence contains no flights."""

    import travel_agent.workflows.search as workflow

    monkeypatch.setattr(
        workflow,
        "resolve_destination",
        lambda _: __import__(
            "travel_agent.schemas.evidence", fromlist=["ToolResult"]
        ).ToolResult.unavailable("test", "timeout", "offline test"),
    )
    request = TripRequest(
        requested_services=["activities", "guide"],
        destination="Maldives",
        start_date="2099-10-29",
        days=3,
    )
    search_plan = build_search_plan(request)
    results = execute_search_plan(search_plan)
    base = build_deterministic_trip_plan(request, results, offline=False)
    return request, search_plan, results, base


def _producer(plans, seen=None):
    sequence = iter(plans)

    async def produce(feedback=None):
        if seen is not None:
            seen.append(feedback)
        return next(sequence)()

    return produce


@pytest.mark.asyncio
async def test_a_clean_plan_is_not_regenerated(fixture_plan):
    request, search_plan, results, base = fixture_plan
    good = lambda: base.model_copy(deep=True)  # noqa: E731

    outcome = await plan_with_invariant_gate(
        _producer([good, good]), request, search_plan, results
    )

    assert outcome.attempts == 1
    assert not outcome.regenerated
    assert not outcome.degraded
    assert outcome.clean


@pytest.mark.asyncio
async def test_a_violated_invariant_triggers_one_regeneration(fixture_plan):
    request, search_plan, results, base = fixture_plan

    def bad():
        plan = base.model_copy(deep=True)
        plan.flights = [GHOST]
        return plan

    good = lambda: base.model_copy(deep=True)  # noqa: E731

    outcome = await plan_with_invariant_gate(
        _producer([bad, good]), request, search_plan, results
    )

    assert outcome.regenerated
    assert not outcome.degraded
    assert outcome.plan.flights == []


@pytest.mark.asyncio
async def test_a_persistent_violation_degrades_and_drops_the_invented_fact(
    fixture_plan,
):
    request, search_plan, results, base = fixture_plan

    def bad():
        plan = base.model_copy(deep=True)
        plan.flights = [GHOST]
        return plan

    outcome = await plan_with_invariant_gate(
        _producer([bad, bad]), request, search_plan, results
    )

    assert outcome.degraded
    # The invented flight must not survive into what the user reads.
    assert outcome.plan.flights == []
    assert any("Deterministic checks failed" in note for note in outcome.plan.notes)


@pytest.mark.asyncio
async def test_a_text_mode_result_passes_through(fixture_plan):
    request, search_plan, results, _ = fixture_plan

    async def produce(_feedback=None):
        return "raw text response"

    outcome = await plan_with_invariant_gate(produce, request, search_plan, results)

    assert outcome.plan == "raw text response"
    assert outcome.clean


# --- Origin evidence -------------------------------------------------------


MALDIVES_ZH = "我想要2099-10-29去马尔代夫玩3天，一定要去马富士岛，但我预算只有3万。"
MALDIVES_EN = (
    "I want to go to the Maldives for 3 days on 2099-10-29, I must visit "
    "Maafushi Island, my budget is only 30000 CNY."
)


def _full_request(origin):
    return TripRequest(
        requested_services=["flights", "hotels", "activities", "guide"],
        origin=origin,
        destination="Maldives",
        start_date="2099-10-29",
        days=3,
    )


@pytest.mark.parametrize("text", [MALDIVES_ZH, MALDIVES_EN])
def test_an_origin_the_user_never_gave_is_dropped_and_asked_for(text):
    # Without this a model-invented origin looks supplied, so intake never
    # asks and the flight tools receive the wrong city.
    guarded = drop_uncited_fields(_full_request("Tokyo"), text)

    assert guarded.origin is None
    assert guarded.status == "needs_clarification"
    assert ORIGIN in guarded.missing_fields


@pytest.mark.parametrize(
    "text, quote",
    [
        ("从上海出发去马尔代夫玩3天", "从上海出发"),
        ("from Shanghai to the Maldives for 3 days", "from Shanghai"),
        ("上海出发，去马尔代夫", "上海出发"),
    ],
)
def test_a_cited_departure_keeps_the_models_origin(text, quote):
    request = _full_request("Shanghai")
    request.field_evidence = {"origin": quote}
    guarded = drop_uncited_fields(request, text)

    assert guarded.origin == "Shanghai"
    # The date in this fixture is not in the text either, so it is correctly
    # cleared; this case is only about the origin surviving.
    assert ORIGIN not in guarded.missing_fields


def test_the_check_does_nothing_when_there_is_no_origin():
    request = _full_request(None)

    assert drop_uncited_fields(request, MALDIVES_ZH).origin is None


@pytest.mark.asyncio
async def test_a_bare_answer_to_our_own_origin_question_is_accepted():
    # "上海" alone carries no departure marker, but it is the user's answer to
    # the question intake itself asked, so the guard must not reject it and
    # loop forever.
    from travel_agent.workflows.intake import collect_trip_request

    class FakeResponse:
        def __init__(self, value):
            self.value = value

    class FakeAgent:
        def __init__(self, values):
            self.values = iter(values)

        async def run(self, *_args, **_kwargs):
            return FakeResponse(next(self.values))

    agent = FakeAgent(
        [
            TripRequest(destination="Tokyo"),
            TripRequest(origin="Shanghai", start_date="2099-10-01", days=3),
        ]
    )
    asked = []

    async def ask(request):
        asked.append(list(request.missing_fields))
        return "上海，2099年10月1日，玩3天"

    request, _ = await collect_trip_request(
        agent, "我要去东京玩", ask_clarification=ask
    )

    assert ORIGIN in asked[0]
    assert request.origin == "Shanghai"
    assert request.status == "ready"


# --- Retrying what a retry can actually fix --------------------------------
# The planner runs at temperature 0, so a retry is only worth making when the
# prompt changes. Both retry layers therefore feed the failure back.


@pytest.mark.asyncio
async def test_the_gate_tells_the_planner_which_invariant_failed(fixture_plan):
    request, search_plan, results, base = fixture_plan
    seen: list[str | None] = []

    def bad():
        plan = base.model_copy(deep=True)
        plan.flights = [GHOST]
        return plan

    good = lambda: base.model_copy(deep=True)  # noqa: E731

    await plan_with_invariant_gate(
        _producer([bad, good], seen), request, search_plan, results
    )

    assert seen[0] is None
    assert "flight_evidence_is_authoritative" in seen[1]


@pytest.mark.asyncio
async def test_a_schema_failure_is_retried_with_the_error_appended():
    from travel_agent.workflows.trip_planning import plan_trip_structured

    request = TripRequest(
        requested_services=["activities", "guide"],
        destination="Maldives",
        start_date="2099-10-29",
        days=2,
    )
    search_plan = build_search_plan(request)
    results = execute_search_plan(search_plan)

    class Planner:
        def __init__(self):
            self.prompts: list[str] = []

        async def run(self, prompt, **_kwargs):
            self.prompts.append(prompt)
            invalid = "failed schema validation" not in prompt
            payload = (
                {"summary": "", "daily_titles": []}
                if invalid
                else {
                    "summary": "马尔代夫 2 日行程。",
                    "daily_titles": [
                        {"day": 1, "title": "一"},
                        {"day": 2, "title": "二"},
                    ],
                }
            )

            class Response:
                value = payload
                text = ""
                usage_details = None

            return Response()

    planner = Planner()
    plan = await plan_trip_structured(
        planner,
        "基础 prompt",
        mode="auto",
        request_context=request,
        search_results=results,
    )

    assert len(planner.prompts) == 2
    assert "failed schema validation" in planner.prompts[1]
    # Without the retry this degraded to deterministic template titles.
    assert plan.generation_source == "llm"
    assert plan.summary.startswith("马尔代夫")


@pytest.mark.asyncio
async def test_a_persistent_schema_failure_still_degrades():
    from travel_agent.workflows.trip_planning import plan_trip_structured

    request = TripRequest(
        requested_services=["activities", "guide"],
        destination="Maldives",
        start_date="2099-10-29",
        days=2,
    )
    results = execute_search_plan(build_search_plan(request))

    class AlwaysInvalid:
        calls = 0

        async def run(self, _prompt, **_kwargs):
            AlwaysInvalid.calls += 1

            class Response:
                value = {"summary": "", "daily_titles": []}
                text = ""
                usage_details = None

            return Response()

    plan = await plan_trip_structured(
        AlwaysInvalid(),
        "基础 prompt",
        mode="auto",
        request_context=request,
        search_results=results,
    )

    assert AlwaysInvalid.calls == 2
    assert plan.generation_source == "deterministic_fallback"
