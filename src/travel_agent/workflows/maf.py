"""Small MAF workflow composition for the optional planner review stage.

The main travel workflow stays application-owned because it contains typed
validation and external HTTP tools. This module uses MAF's WorkflowBuilder for
the genuinely agent-to-agent part: a planner hands a typed plan to a reviewer.
"""

from dataclasses import dataclass
from typing import Any

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from agent_framework import Agent
from typing_extensions import Never

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.quality import check_workflow_invariants
from travel_agent.workflows.review import review_trip_plan
from travel_agent.workflows.trip_planning import TraceObserver, plan_trip_structured


@dataclass
class PlanReviewInput:
    """In-memory input shared by the two MAF executors."""

    planner_prompt: str
    request: TripRequest
    search_plan: SearchPlan
    search_results: SearchResults
    mode: str


@dataclass
class PlanReviewPacket:
    """Typed handoff from PlannerExecutor to ReviewerExecutor."""

    plan: TripPlan
    request: TripRequest
    search_plan: SearchPlan
    search_results: SearchResults


class PlannerExecutor(Executor):
    """Run the existing validated planner and hand off a TripPlan."""

    def __init__(
        self,
        planner: Agent,
        formatter: Agent | None,
        on_prompt: Any = None,
        on_trace: TraceObserver | None = None,
        *,
        id: str,
    ) -> None:
        super().__init__(id=id)
        self._planner = planner
        self._formatter = formatter
        self._on_prompt = on_prompt
        self._on_trace = on_trace

    @handler
    async def run(
        self,
        packet: PlanReviewInput,
        ctx: WorkflowContext[PlanReviewPacket],
    ) -> None:
        plan = await plan_trip_structured(
            self._planner,
            packet.planner_prompt,
            formatter=self._formatter,
            mode=packet.mode,
            tools=[],
            request_context=packet.request,
            search_results=packet.search_results,
            on_prompt=self._on_prompt,
            on_trace=self._on_trace,
        )
        if not isinstance(plan, TripPlan):
            raise TypeError("MAF review workflow requires a structured TripPlan")
        await ctx.send_message(
            PlanReviewPacket(
                plan=plan,
                request=packet.request,
                search_plan=packet.search_plan,
                search_results=packet.search_results,
            )
        )


class ReviewerExecutor(Executor):
    """Review a plan without changing its factual evidence."""

    def __init__(
        self,
        reviewer: Agent,
        on_trace: TraceObserver | None = None,
        *,
        id: str,
    ) -> None:
        super().__init__(id=id)
        self._reviewer = reviewer
        self._on_trace = on_trace

    @handler
    async def run(
        self,
        packet: PlanReviewPacket,
        ctx: WorkflowContext[Never, TripPlan],
    ) -> None:
        try:
            packet.plan.review = await review_trip_plan(
                self._reviewer,
                packet.request,
                packet.search_plan,
                packet.search_results,
                packet.plan,
                on_trace=self._on_trace,
            )
            failed_checks = [
                check.message
                for check in check_workflow_invariants(
                    packet.request,
                    packet.search_plan,
                    packet.search_results,
                    packet.plan,
                )
                if not check.passed
            ]
            if failed_checks:
                packet.plan.review.status = "needs_revision"
                packet.plan.review.issues = list(
                    dict.fromkeys([*packet.plan.review.issues, *failed_checks])
                )
        except Exception as error:
            # Review is a quality signal, not a prerequisite for returning a
            # plan. Preserve the validated plan if the second model is down.
            packet.plan.notes.append(
                f"质量复核暂不可用：{type(error).__name__}"
            )
        await ctx.yield_output(packet.plan)


def build_plan_review_workflow(
    planner: Agent,
    reviewer: Agent,
    formatter: Agent | None,
    on_prompt: Any = None,
    on_trace: TraceObserver | None = None,
):
    """Build the Planner -> Reviewer MAF graph used by ``--review``."""

    planner_executor = PlannerExecutor(
        planner,
        formatter,
        on_prompt,
        on_trace,
        id="travel-planner",
    )
    reviewer_executor = ReviewerExecutor(
        reviewer,
        on_trace,
        id="travel-reviewer",
    )
    return (
        WorkflowBuilder(
            start_executor=planner_executor,
            output_from=[reviewer_executor],
            name="travel-plan-review",
            description="Generate a typed travel plan, then review it read-only.",
        )
        .add_edge(planner_executor, reviewer_executor)
        .build()
    )


async def run_plan_review_workflow(**kwargs: Any) -> TripPlan:
    """Run the graph and return its single typed workflow output."""

    planner = kwargs.pop("planner")
    reviewer = kwargs.pop("reviewer")
    formatter = kwargs.pop("formatter", None)
    on_prompt = kwargs.pop("on_prompt", None)
    on_trace = kwargs.pop("on_trace", None)
    workflow = build_plan_review_workflow(
        planner,
        reviewer,
        formatter,
        on_prompt,
        on_trace,
    )
    events = await workflow.run(PlanReviewInput(**kwargs))
    outputs = events.get_outputs()
    if len(outputs) != 1 or not isinstance(outputs[0], TripPlan):
        raise TypeError("MAF review workflow did not emit one TripPlan")
    return outputs[0]
