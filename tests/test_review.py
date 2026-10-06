from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.review import ReviewResult
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.workflows.review import build_review_prompt, review_trip_plan


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.text = value.model_dump_json()


class FakeReviewer:
    async def run(self, *_args, **_kwargs):
        return FakeResponse(
            ReviewResult(
                status="pass",
                strengths=["保留了用户偏好"],
                checked_constraints=["日期", "工具证据"],
            )
        )


def _inputs():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=3,
        preferences=["Tokyo Tower"],
    )
    return request, SearchPlan(), SearchResults(), TripPlan(
        destination="Tokyo",
        days=3,
        summary="demo",
        preferences=["Tokyo Tower"],
    )


def test_review_prompt_contains_all_evidence_sections():
    request, search_plan, search_results, plan = _inputs()
    prompt = build_review_prompt(request, search_plan, search_results, plan)

    assert "REQUEST:" in prompt
    assert "SEARCH_PLAN:" in prompt
    assert "SEARCH_RESULTS:" in prompt
    assert "TRIP_PLAN:" in prompt
    assert "DETERMINISTIC_CHECKS" in prompt


async def test_review_returns_typed_read_only_result():
    request, search_plan, search_results, plan = _inputs()

    result = await review_trip_plan(
        FakeReviewer(), request, search_plan, search_results, plan
    )

    assert result.status == "pass"
    assert result.issues == []
