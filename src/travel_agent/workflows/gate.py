"""Act on the deterministic invariants instead of only reporting them.

``check_workflow_invariants`` was computed after the plan was built and then
printed under ``--debug``. A failing check changed nothing: the user still
received the plan, with no indication that the program's own contract had
been violated. This module closes that loop --- a failed invariant triggers
one regeneration, and a still-failing plan degrades to the deterministic
assembly, which satisfies the evidence invariants by construction because it
copies typed tool results instead of letting the model write them.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from travel_agent.quality import WorkflowCheck, check_workflow_invariants
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.workflows.trip_planning import build_deterministic_trip_plan


@dataclass(frozen=True)
class GateOutcome:
    """What the invariant gate did, so the caller can tell the user."""

    plan: TripPlan
    attempts: int
    regenerated: bool
    degraded: bool
    failures: tuple[WorkflowCheck, ...]

    @property
    def clean(self) -> bool:
        return not self.failures


def failed_checks(
    request: TripRequest,
    search_plan: SearchPlan,
    search_results: SearchResults,
    plan: TripPlan,
) -> list[WorkflowCheck]:
    return [
        check
        for check in check_workflow_invariants(
            request, search_plan, search_results, plan
        )
        if not check.passed
    ]


def describe_failures(failures: list[WorkflowCheck]) -> str:
    """Turn failed invariants into instructions the planner can act on."""

    lines = [f"- {check.name}：{check.message}" for check in failures]
    return (
        "The previous draft failed the program's deterministic checks:\n"
        + "\n".join(lines)
        + "\nRegenerate the narrative without changing any tool-provided fact."
    )


async def plan_with_invariant_gate(
    produce: Callable[[str | None], Awaitable[TripPlan]],
    request: TripRequest,
    search_plan: SearchPlan,
    search_results: SearchResults,
    *,
    attempts: int = 2,
) -> GateOutcome:
    """Produce a plan that satisfies the invariants, or say why it does not.

    ``produce`` receives the feedback for the previous attempt, or ``None``
    on the first. Passing it matters: the planner runs at temperature 0, so
    re-sending an identical prompt reproduces the identical bad plan. The
    names and messages of the failed invariants are the new information.
    A non-``TripPlan`` result (``--mode text``) is passed through untouched.
    """

    last_failures: list[WorkflowCheck] = []
    used = 0

    feedback: str | None = None
    for attempt in range(1, max(1, attempts) + 1):
        used = attempt
        plan = await produce(feedback)
        if not isinstance(plan, TripPlan):
            return GateOutcome(plan, attempt, attempt > 1, False, ())
        failures = failed_checks(request, search_plan, search_results, plan)
        if not failures:
            return GateOutcome(plan, attempt, attempt > 1, False, ())
        last_failures = failures
        feedback = describe_failures(failures)

    fallback = build_deterministic_trip_plan(
        request,
        search_results,
        offline=False,
        generation_source="deterministic_fallback",
    )
    remaining = failed_checks(request, search_plan, search_results, fallback)
    if remaining:
        # A model plan that already violated an invariant is never safer than
        # a typed evidence plan, even when the fallback has its own provider
        # limitation. Keep the fallback and report both the original model
        # violations and any invariant that remains after degradation.
        combined: list[WorkflowCheck] = []
        for check in [*last_failures, *remaining]:
            if check.name not in {item.name for item in combined}:
                combined.append(check)
        _note(fallback, combined, degraded=True)
        return GateOutcome(fallback, used, used > 1, True, tuple(combined))

    _note(fallback, last_failures, degraded=True)
    return GateOutcome(fallback, used, used > 1, True, tuple(remaining))


def _note(plan: TripPlan, failures: list[WorkflowCheck], *, degraded: bool) -> None:
    """Record the violation in the plan itself, not only in debug output."""

    names = ", ".join(check.name for check in failures) or "unknown check"
    message = (
        f"Deterministic checks failed ({names}); the facts on this page were "
        "assembled directly from tool evidence, so the wording is plainer."
        if degraded
        else f"Deterministic checks failed ({names}); rely on the tool results and sources below."
    )
    if message not in plan.notes:
        plan.notes.append(message)
