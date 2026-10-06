import pytest

from agent_framework.exceptions import ChatClientException

from travel_agent.workflows.trip_planning import (
    StructuredOutputError,
    build_deterministic_trip_plan,
    plan_trip,
    plan_trip_structured,
)
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolCallRecord, ToolResult
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.trip import TripPlan


class FakeAgent:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    async def run(self, request, **_kwargs):
        self.calls += 1
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class FakeResponse:
    def __init__(self, text):
        self.text = text


def test_food_evidence_is_copied_to_final_plan():
    request = TripRequest(destination="Tokyo", days=2, requested_services=["food"])
    results = SearchResults(
        foods=ToolResult.ok(
            "Overpass API",
            [
                PlaceCandidate(
                    name="Demo Sushi",
                    category="restaurant",
                    cuisine="sushi",
                    provider_id="sushi",
                    source="Overpass / OpenStreetMap",
                    venue_type="mixed",
                )
            ],
        )
    )

    plan = build_deterministic_trip_plan(request, results)

    assert plan.food[0].name == "Demo Sushi"
    assert plan.food[0].venue_type == "mixed"

    @property
    def value(self):
        # Simulate a provider whose lazy structured parser rejects the text.
        raise ValueError("invalid provider JSON")


@pytest.mark.asyncio
async def test_plan_trip_calls_agent_once_when_successful():
    agent = FakeAgent(["plan"])

    result = await plan_trip(agent, "东京三日游")

    assert result == "plan"
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_plan_trip_emits_a_redacted_trace_event():
    agent = FakeAgent(["plan"])
    events = []

    result = await plan_trip(
        agent,
        "东京三日游",
        stage="intake",
        on_trace=lambda *event: events.append(event),
    )

    assert result == "plan"
    assert events[0][0] == "intake"
    assert events[0][4] == "ok"


@pytest.mark.asyncio
async def test_plan_trip_reports_stage_and_structured_option_to_prompt_observer():
    agent = FakeAgent(["plan"])
    seen = []

    result = await plan_trip(
        agent,
        "东京三日游",
        options={"response_format": TripPlan},
        stage="intake",
        on_prompt=lambda stage, prompt, options: seen.append(
            (stage, prompt, options["response_format"])
        ),
    )

    assert result == "plan"
    assert seen == [("intake", "东京三日游", TripPlan)]


@pytest.mark.asyncio
async def test_plan_trip_retries_connection_failures(monkeypatch):
    agent = FakeAgent([ChatClientException("Connection error"), "plan"])
    monkeypatch.setattr("travel_agent.workflows.trip_planning.asyncio.sleep", _skip_sleep)

    result = await plan_trip(agent, "东京三日游")

    assert result == "plan"
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_plan_trip_retries_timeout_failures(monkeypatch):
    agent = FakeAgent([ChatClientException("Request timed out."), "plan"])
    monkeypatch.setattr("travel_agent.workflows.trip_planning.asyncio.sleep", _skip_sleep)

    result = await plan_trip(agent, "东京三日游")

    assert result == "plan"
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_auto_returns_trip_plan_instead_of_text_fallback():
    payload = (
        '{"status":"complete","destination":"东京","days":1,'
        '"summary":"demo","missing_information":[],"flights":[],'
        '"hotels":[],"itinerary":[],"notes":[]}'
    )
    planner = FakeAgent([FakeResponse(payload)])

    result = await plan_trip_structured(
        planner,
        "东京一日游",
        mode="auto",
        formatter=FakeAgent([]),
    )

    assert result.destination == "东京"
    assert result.days == 1


@pytest.mark.asyncio
async def test_convert_raises_when_all_json_repairs_fail():
    planner = FakeAgent([FakeResponse("普通文本")])
    formatter = FakeAgent(
        [FakeResponse("普通文本"), FakeResponse("普通文本"), FakeResponse("普通文本")]
    )

    with pytest.raises(StructuredOutputError):
        await plan_trip_structured(
            planner,
            "东京一日游",
            mode="convert",
            formatter=formatter,
        )


@pytest.mark.asyncio
async def test_request_context_overrides_invented_budget_and_optional_questions():
    payload = (
        '{"status":"needs_clarification","destination":"东京","days":3,'
        '"summary":"demo","budget":{"level":"high"},'
        '"missing_information":["具体的回程日期"],"flights":[],"hotels":[],'
        '"itinerary":[],"notes":[]}'
    )
    planner = FakeAgent([FakeResponse(payload)])
    context = TripRequest(
        origin="上海", destination="东京", start_date="2099-10-01", days=3
    )

    result = await plan_trip_structured(
        planner,
        "预算未指定",
        mode="auto",
        formatter=FakeAgent([]),
        request_context=context,
    )

    assert result.budget is None
    assert result.status == "complete"
    assert result.missing_information == []


@pytest.mark.asyncio
async def test_tool_evidence_overrides_model_facts_and_day_assignment():
    payload = (
        '{"status":"complete","destination":"Tokyo","days":2,'
        '"summary":"demo","flights":[],"hotels":[],"itinerary":['
        '{"day":1,"title":"First","activities":[]},'
        '{"day":2,"title":"Second","activities":[]}],"notes":[]}'
    )
    context = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=2,
        preferences=["东京塔"],
    )
    evidence = SearchResults(
        destination=ToolResult.ok("geo", GeoPoint(latitude=35, longitude=139)),
        activities=[
            {
                "destination": "Tokyo",
                "day": 2,
                "name": "东京塔",
                "provider_id": "tower",
                "source": "Photon",
                "verified": True,
                "opening_hours": "Mo-Su 09:00-22:00",
                "opening_status": "open",
                "website": "https://example.test/tower",
                "estimated_visit_minutes": 90,
                }
            ],
        preference_places=[
            ToolResult.ok(
                "Photon",
                PlaceCandidate(
                    name="东京塔",
                    provider_id="tower",
                    source="Photon",
                    verified=True,
                    opening_hours="Mo-Su 09:00-22:00",
                    website="https://example.test/tower",
                ),
            )
        ],
        search_links=[
            {
                "service": "flights",
                "provider": "Google Travel",
                "url": "https://example.test",
            }
        ],
        tool_calls=[
            ToolCallRecord(
                tool_name="resolve_destination",
                status="ok",
                elapsed_ms=1,
                provider="geo",
            )
        ],
    )

    result = await plan_trip_structured(
        FakeAgent([FakeResponse(payload)]),
        "demo",
        mode="auto",
        formatter=FakeAgent([]),
        request_context=context,
        search_results=evidence,
    )

    assert result.preferences == ["东京塔"]
    assert [(day.day, [item.name for item in day.activities]) for day in result.itinerary] == [
        (1, []),
        (2, ["东京塔"]),
    ]
    assert result.itinerary[1].activities[0].opening_hours == "Mo-Su 09:00-22:00"
    assert result.itinerary[1].activities[0].website == "https://example.test/tower"
    assert result.places[0].provider_id == "tower"
    assert result.search_links[0].provider == "Google Travel"
    assert result.data_sources == ["geo"]


@pytest.mark.asyncio
async def test_request_context_overrides_zero_days_and_restores_every_day():
    payload = (
        '{"status":"needs_clarification","destination":"Wrong","days":0,'
        '"summary":"","missing_information":["truncated"],"flights":[],'
        '"hotels":[],"itinerary":[],"notes":[]}'
    )
    context = TripRequest(
        origin="Shanghai",
        destination="Seoul",
        start_date="2099-11-01",
        days=4,
        nights=3,
    )
    evidence = SearchResults(
        activities=[
            {
                "destination": "Seoul",
                "day": 4,
                "name": "Hongdae",
                "provider_id": "hongdae",
                "source": "Photon",
                "verified": True,
            }
        ]
    )

    result = await plan_trip_structured(
        FakeAgent([FakeResponse(payload)]),
        "demo",
        mode="auto",
        formatter=FakeAgent([]),
        request_context=context,
        search_results=evidence,
    )

    assert result.destination == "Seoul"
    assert result.days == 4
    assert result.summary == "已根据确认后的需求整理 Seoul 4 日旅行计划。"
    assert [day.day for day in result.itinerary] == [1, 2, 3, 4]
    assert result.itinerary[3].activities[0].name == "Hongdae"


@pytest.mark.asyncio
async def test_ready_request_discards_model_clarification_and_reports_skipped_weather():
    payload = (
        '{"status":"needs_clarification","destination":"Seoul","days":3,'
        '"summary":"demo","missing_information":["Input data is truncated"],'
        '"flights":[],"hotels":[],"itinerary":[],"notes":[]}'
    )
    context = TripRequest(
        requested_services=["activities", "guide"],
        destination="Seoul",
        days=3,
    )
    evidence = SearchResults(
        weather=ToolResult.skipped(
            "Open-Meteo Forecast",
            "出发日期尚未进入 16 天预报窗口，请临近出行时再查询。",
        )
    )

    result = await plan_trip_structured(
        FakeAgent([FakeResponse(payload)]),
        "demo",
        mode="auto",
        formatter=FakeAgent([]),
        request_context=context,
        search_results=evidence,
    )

    assert result.status == "complete"
    assert result.missing_information == []
    assert not any("truncated" in note for note in result.notes)
    assert result.notes == ["出发日期尚未进入 16 天预报窗口，请临近出行时再查询。"]


async def _skip_sleep(_seconds):
    return None
