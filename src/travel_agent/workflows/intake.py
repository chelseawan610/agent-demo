import json
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Agent
from travel_agent.schemas.request import DEFAULT_SERVICES, BudgetSpec, TripRequest
from travel_agent.workflows.trip_planning import TraceObserver, plan_trip


PromptObserver = Callable[[str, str, dict[str, Any] | None], None]


def parse_trip_request(text: str) -> TripRequest:
    """Parse and validate the JSON returned by the intake agent."""

    candidate = text.strip()
    if candidate.startswith("```"):
        raise ValueError("intake response must be JSON, not a Markdown code fence")
    return TripRequest.model_validate(json.loads(candidate))


async def extract_trip_request(
    agent: Agent,
    text: str,
    *,
    on_prompt: PromptObserver | None = None,
    on_trace: TraceObserver | None = None,
    session: Any | None = None,
) -> TripRequest:
    """Ask the intake agent for one normalized TripRequest object."""

    response = await plan_trip(
        agent,
        text,
        options={"response_format": TripRequest},
        stage="intake",
        on_prompt=on_prompt,
        on_trace=on_trace,
        session=session,
    )
    response_value = None
    try:
        response_value = response.value
    except Exception:
        response_value = None
    if response_value is not None:
        request = TripRequest.model_validate(response_value)
    else:
        request = parse_trip_request(response.text)
    return request


async def collect_trip_request(
    agent: Agent,
    initial_text: str,
    *,
    ask_clarification: Callable[[TripRequest], Awaitable[str]] | None = None,
    max_rounds: int = 3,
    on_prompt: PromptObserver | None = None,
    on_trace: TraceObserver | None = None,
) -> tuple[TripRequest, str]:
    """Extract requirements and ask bounded follow-up questions when needed."""

    current_text = initial_text
    previous_request: TripRequest | None = None
    latest_answer: str | None = None
    session = None
    create_session = getattr(agent, "create_session", None)
    if callable(create_session):
        # MAF session is short-term conversation memory for this one intake
        # interaction; it is never reused as a long-term user profile.
        session = create_session()
    for round_number in range(max_rounds + 1):
        extraction_text = current_text
        if previous_request is not None and latest_answer is not None:
            extraction_text = (
                "原始旅行需求：\n"
                f"{initial_text}\n\n"
                "已经确认的结构化信息（必须保留）：\n"
                f"{previous_request.model_dump_json(ensure_ascii=False)}\n\n"
                "本轮用户补充信息（请合并进上述对象）：\n"
                f"{latest_answer}"
            )
        request = await extract_trip_request(
            agent,
            extraction_text,
            on_prompt=on_prompt,
            on_trace=on_trace,
            session=session,
        )
        if previous_request is not None:
            request = merge_trip_requests(previous_request, request)
        if request.status == "ready":
            budget_text = _format_budget(request.budget)
            planning_text = (
                f"请求范围：{', '.join(request.requested_services)}\n"
                f"{budget_text}\n"
                "未提供的天数、晚数或偏好由你选择合理默认值；"
                "未指定预算时不要擅自改成中等预算。\n"
                + current_text
            )
            return request, planning_text
        if ask_clarification is None or round_number == max_rounds:
            return request, current_text

        answer = await ask_clarification(request)
        if not answer.strip():
            return request, current_text
        previous_request = request
        latest_answer = answer.strip()
        current_text = f"{current_text}\n\n用户补充信息：{latest_answer}"

    return request, current_text


def _format_budget(budget: BudgetSpec | None) -> str:
    """Give the planner an explicit budget instruction instead of a vague hint."""

    if budget is None:
        return "预算：未指定"
    parts: list[str] = []
    if budget.level is not None:
        parts.append(f"档位={budget.level}")
    if budget.amount is not None:
        period = {"total": "总额", "per_day": "每天", "per_night": "每晚"}.get(
            budget.period or "total", "总额"
        )
        parts.append(f"金额={budget.amount:g} {budget.currency}/{period}")
    return "预算：" + ("，".join(parts) if parts else "未指定")


def merge_trip_requests(old: TripRequest, new: TripRequest) -> TripRequest:
    """Keep fields already extracted when a follow-up only adds new details."""

    values = new.model_dump()
    for field in (
        "origin",
        "destination",
        "start_date",
        "return_date",
        "days",
        "nights",
        "duration_profile",
        "budget",
    ):
        old_value = getattr(old, field)
        if field == "duration_profile" and old_value == "unknown":
            continue
        if old_value is not None:
            # Clarification turns fill gaps; they do not silently rewrite facts
            # that were already confirmed in an earlier turn.
            values[field] = old_value
    values["preferences"] = list(
        dict.fromkeys([*old.preferences, *values["preferences"]])
    )
    details = {
        detail.label: detail
        for detail in [*old.preference_details, *new.preference_details]
    }
    values["preference_details"] = list(details.values())
    values["requested_services"] = old.requested_services
    return TripRequest(**values)
