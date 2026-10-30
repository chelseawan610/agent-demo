from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.evidence import ToolResult
from travel_agent.rag.models import KnowledgeHit
from travel_agent.memory.store import UserMemory
from travel_agent.workflows.prompts import (
    build_planner_prompt,
    build_trip_narrative_prompt,
    split_preferences_by_outcome,
)
from travel_agent.schemas.evidence import PlaceCandidate
from travel_agent.schemas.trip import ActivityOption


def test_planner_prompt_contains_one_shared_evidence_packet():
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])
    prompt = build_planner_prompt(
        "东京攻略，不需要机票酒店。",
        request,
        SearchPlan(),
        SearchResults(),
    )

    assert "<user_request>" in prompt
    assert "Structured request (the source of truth for the request)" in prompt
    assert "Search plan (program generated" in prompt
    assert "Tool results (the only source of travel facts)" in prompt
    assert "Do not call tools again" in prompt


def test_planner_prompt_treats_memory_as_non_authoritative_context():
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])
    prompt = build_planner_prompt(
        "东京攻略，我想去博物馆。",
        request,
        SearchPlan(),
        SearchResults(),
        memory=UserMemory(
            user_id="u1",
            preferences=["anime"],
            updated_at="2026-01-01T00:00:00Z",
        ),
    )

    assert "anime" in prompt
    assert "not a requirement for this trip" in prompt
    assert "Structured request (the source of truth for the request)" in prompt


def test_planner_prompt_contains_rag_evidence_boundary():
    request = TripRequest(destination="Tokyo", requested_services=["guide"])
    results = SearchResults(
        knowledge=ToolResult.ok(
            "local markdown BM25",
            [
                KnowledgeHit(
                    chunk_id="guide.md:1:1",
                    source="guide.md",
                    title="Guide",
                    section="Tokyo",
                    text="Tokyo Tower is a landmark.",
                    score=1.2,
                )
            ],
        )
    )

    prompt = build_planner_prompt(
        "东京攻略",
        request,
        SearchPlan(knowledge_query="Tokyo"),
        results,
    )

    assert "Local knowledge retrieval" in prompt
    assert "Tokyo Tower is a landmark." in prompt
    assert "Knowledge hits support general advice only, never live data" in prompt


def test_narrative_prompt_matches_chinese_user_language_and_route_evidence():
    request = TripRequest(
        destination="San Francisco",
        requested_services=["activities", "guide"],
        days=2,
    )
    plan = SearchPlan(route_enabled=True, route_modes=["driving", "transit"])
    prompt = build_trip_narrative_prompt(
        "我想去旧金山玩两天，并看金门大桥。",
        request,
        plan,
        SearchResults(
            routes=ToolResult.skipped("OSRM + OpenTripPlanner", "no usable routes")
        ),
    )

    assert '"output_language":"Chinese"' in prompt
    assert "daily_titles must use the same language too" in prompt
    assert '"status":"not_requested"' not in prompt
    assert '"successful_routes":[]' in prompt
    assert "do not say that the plan includes, provides, or covers a route" in prompt


def test_narrative_prompt_matches_english_user_language():
    request = TripRequest(
        destination="Tokyo",
        requested_services=["activities", "guide"],
        days=2,
    )
    prompt = build_trip_narrative_prompt(
        "I want to visit Tokyo for two days.",
        request,
        SearchPlan(),
        SearchResults(),
    )

    assert '"output_language":"English"' in prompt
    assert "Write a concise introduction in English" in prompt


def test_narrative_prompt_prefers_intake_language_over_place_name_script():
    request = TripRequest(
        destination="Tokyo",
        response_language="English",
        requested_services=["activities", "guide"],
        days=2,
    )
    prompt = build_trip_narrative_prompt(
        "I want to visit 东京 for two days.",
        request,
        SearchPlan(),
        SearchResults(),
    )

    assert '"output_language":"English"' in prompt


def test_resolving_a_place_does_not_prove_it_was_scheduled():
    request = TripRequest(destination="City", requested_services=["guide"], preference_details=[
        {"label": "Museum", "kind": "place", "search_query": "Museum", "preferred_day": 2},
        {"label": "art", "kind": "interest", "search_query": "art"},
    ])
    results = SearchResults(preference_places=[
        ToolResult.ok("Photon", PlaceCandidate(name="Local Museum", provider_id="node:1", source="test")),
        ToolResult.skipped("Photon", "interest"),
    ])
    assert split_preferences_by_outcome(request, results) == ([], ["Museum"])
    results.activities = [ActivityOption(
        destination="City", day=1, name="Different provider spelling", provider_id="node:1",
    )]
    # Same entity, but not on the requested day.
    assert split_preferences_by_outcome(request, results) == ([], ["Museum"])
    results.activities[0].day = 2
    assert split_preferences_by_outcome(request, results) == (["Museum"], [])
