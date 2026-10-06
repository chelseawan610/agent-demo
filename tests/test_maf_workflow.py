from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.review import ReviewResult
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.workflows.maf import run_plan_review_workflow


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.text = value.model_dump_json()


class FakePlanner:
    async def run(self, *_args, **_kwargs):
        return FakeResponse(
            TripPlan(destination="Tokyo", days=1, summary="demo")
        )


class FakeReviewer:
    async def run(self, *_args, **_kwargs):
        return FakeResponse(
            ReviewResult(status="pass", strengths=["typed handoff"])
        )


class FailingReviewer:
    async def run(self, *_args, **_kwargs):
        raise RuntimeError("review provider unavailable")


async def test_maf_workflow_passes_a_typed_plan_to_the_reviewer():
    request = TripRequest(
        destination="Tokyo",
        days=1,
        requested_services=["activities", "guide"],
    )

    plan = await run_plan_review_workflow(
        planner=FakePlanner(),
        reviewer=FakeReviewer(),
        planner_prompt="demo",
        request=request,
        search_plan=SearchPlan(),
        search_results=SearchResults(),
        formatter=None,
        mode="auto",
    )

    assert plan.destination == "Tokyo"
    assert plan.review is not None
    assert plan.review.status == "pass"


async def test_maf_workflow_keeps_plan_when_reviewer_fails():
    request = TripRequest(
        destination="Tokyo",
        days=1,
        requested_services=["activities", "guide"],
    )

    plan = await run_plan_review_workflow(
        planner=FakePlanner(),
        reviewer=FailingReviewer(),
        planner_prompt="demo",
        request=request,
        search_plan=SearchPlan(),
        search_results=SearchResults(),
        formatter=None,
        mode="auto",
    )

    assert plan.review is None
    assert any("质量复核暂不可用" in note for note in plan.notes)
