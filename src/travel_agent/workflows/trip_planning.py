import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from agent_framework import Agent
from agent_framework.exceptions import ChatClientException
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.trip import ActivityItem, DayPlan, TripPlan
from travel_agent.rag.models import KnowledgeCitation
from travel_agent.validation import validate_trip_plan


class StructuredOutputError(ValueError):
    """Raised when a structured mode cannot produce a valid TripPlan."""


PromptObserver = Callable[[str, str, dict[str, Any] | None], None]
TraceObserver = Callable[
    [str, datetime, float, Any, str, Exception | None, str | None], None
]


def _agent_model(agent: Any) -> str | None:
    """Read a model label from a normal Agent or the fallback wrapper."""

    active_model = getattr(agent, "active_model", None)
    if active_model:
        return active_model
    direct_model = getattr(agent, "model", None)
    if direct_model:
        return direct_model
    client = getattr(agent, "client", None)
    return getattr(client, "model", None)


def _emit_trace(
    observer: TraceObserver | None,
    stage: str,
    started_at: datetime,
    started: float,
    response: Any,
    status: str,
    error: Exception | None = None,
    model: str | None = None,
) -> None:
    if observer is None:
        return
    try:
        observer(
            stage,
            started_at,
            (asyncio.get_running_loop().time() - started) * 1000,
            response,
            status,
            error,
            model,
        )
    except Exception:
        # Telemetry must never turn a successful agent response into a failure.
        return


async def plan_trip(
    agent: Agent,
    request: str,
    *,
    retries: int = 3,
    options: dict[str, Any] | None = None,
    tools: Any = None,
    stage: str = "agent",
    on_prompt: PromptObserver | None = None,
    on_trace: TraceObserver | None = None,
    session: Any | None = None,
) -> Any:
    """Run the travel agent with retries for temporary provider failures."""

    for attempt in range(1, retries + 1):
        started_at = datetime.now(timezone.utc)
        started = asyncio.get_running_loop().time()
        try:
            if on_prompt is not None:
                on_prompt(stage, request, options)
            run_kwargs: dict[str, Any] = {}
            if options is not None:
                run_kwargs["options"] = options
            if tools is not None:
                run_kwargs["tools"] = tools
            if session is not None:
                run_kwargs["session"] = session
            response = await agent.run(request, **run_kwargs)
            _emit_trace(
                on_trace,
                stage,
                started_at,
                started,
                response,
                "ok",
                model=_agent_model(agent),
            )
            return response
        except Exception as error:
            _emit_trace(
                on_trace,
                stage,
                started_at,
                started,
                None,
                "error",
                error,
                model=_agent_model(agent),
            )
            if not isinstance(error, ChatClientException):
                raise
            message = str(error)
            original_error = error.args[1] if len(error.args) > 1 else None
            status_code = getattr(original_error, "status_code", None)
            is_server_error = status_code is not None and 500 <= status_code < 600
            is_connection_error = "Connection error" in message
            is_timeout = "timed out" in message.lower() or "timeout" in message.lower()

            if not (is_server_error or is_connection_error or is_timeout) or attempt == retries:
                raise

            await asyncio.sleep(attempt * 2)


def parse_trip_plan(text: str) -> TripPlan:
    """Parse a JSON object returned by the formatter and validate its schema."""

    plan = TripPlan.model_validate(json.loads(text.strip()))
    return validate_trip_plan(plan)


def _response_value(response: Any) -> Any | None:
    """Read the framework's lazy structured value without crashing on bad JSON."""

    try:
        return response.value
    except Exception:
        return None


def _response_to_trip_plan(response: Any) -> TripPlan:
    """Accept either a parsed framework value or JSON contained in response.text."""

    value = _response_value(response)
    if value is not None:
        if isinstance(value, str):
            return parse_trip_plan(value)
        return validate_trip_plan(TripPlan.model_validate(value))
    return parse_trip_plan(response.text)


def _apply_request_constraints(
    plan: TripPlan,
    request_context: TripRequest | None,
    search_results: SearchResults | None = None,
) -> TripPlan:
    """Prevent the planner from inventing values already resolved by intake."""

    if request_context is None:
        return plan
    # A reviewer is a separate opt-in stage. Do not let the planner model
    # populate the optional field by itself and make it look like a review.
    plan.review = None
    # Intake is the source of truth for the user's request. The planner writes
    # narrative text, but it cannot change destination, duration, or budget.
    if request_context.destination is not None:
        plan.destination = request_context.destination
    requested_modes = (
        ["driving", "transit"]
        if request_context.transport_mode == "transit"
        else ["driving"]
    )
    if search_results is not None and search_results.route_modes:
        requested_modes = list(search_results.route_modes)
    # Keep the user's explicit transit request even if a hand-built or
    # partially failed SearchResults object omitted its route_modes metadata.
    if request_context.transport_mode == "transit" and "transit" not in requested_modes:
        requested_modes.append("transit")
    if "transit" in requested_modes and "driving" not in requested_modes:
        requested_modes.insert(0, "driving")
    plan.transport_mode = "transit" if "transit" in requested_modes else "driving"
    plan.transport_modes = requested_modes
    if request_context.days is not None:
        plan.days = request_context.days
    elif request_context.nights is not None:
        plan.days = request_context.nights + 1
    plan.budget = request_context.budget
    plan.preferences = request_context.preferences
    if not plan.summary.strip():
        plan.summary = (
            f"已根据确认后的需求整理 {plan.destination} "
            f"{plan.days} 日旅行计划。"
        )
    # Search tools are the source of truth for factual candidates. The planner
    # may organize them, but it may not replace them with invented options.
    if search_results is not None:
        plan.flights = search_results.flights
        plan.hotels = search_results.hotels
        plan.search_links = search_results.search_links
        plan.tool_status = search_results.tool_calls
        plan.weather = (
            search_results.weather.data
            if search_results.weather is not None
            and search_results.weather.status == "ok"
            and search_results.weather.data is not None
            else []
        )
        plan.routes = (
            search_results.routes.data
            if search_results.routes is not None
            and search_results.routes.status == "ok"
            and search_results.routes.data is not None
            else []
        )
        plan.food = (
            search_results.foods.data
            if search_results.foods is not None
            and search_results.foods.status == "ok"
            and search_results.foods.data is not None
            else []
        )
        knowledge_result = search_results.knowledge
        knowledge_hits = (
            knowledge_result.data
            if knowledge_result is not None
            and knowledge_result.status == "ok"
            and knowledge_result.data is not None
            else []
        )
        plan.knowledge_sources = [
            KnowledgeCitation(
                chunk_id=hit.chunk_id,
                source=hit.source,
                title=hit.title,
                section=hit.section,
                score=hit.score,
            )
            for hit in knowledge_hits
        ]
        preference_places = [
            result.data
            for result in search_results.preference_places
            if result.status == "ok" and result.data is not None
        ]
        attraction_places = (
            search_results.attractions.data
            if search_results.attractions is not None
            and search_results.attractions.status == "ok"
            and search_results.attractions.data is not None
            else []
        )
        food_places = (
            search_results.foods.data
            if search_results.foods is not None
            and search_results.foods.status == "ok"
            and search_results.foods.data is not None
            else []
        )
        places_by_id = {
            place.provider_id: place
            for place in [*preference_places, *attraction_places, *food_places]
        }
        plan.places = list(places_by_id.values())
        evidence_results = [
            search_results.destination,
            search_results.weather,
            search_results.attractions,
            search_results.routes,
            search_results.foods,
            search_results.knowledge,
            *search_results.preference_places,
        ]
        successful_providers = [
            record.provider
            for record in search_results.tool_calls
            if record.status == "ok"
        ]
        attributions = [
            result.attribution
            for result in evidence_results
            if result is not None
            and result.status == "ok"
            and result.attribution is not None
        ]
        plan.data_sources = list(
            dict.fromkeys([*successful_providers, *attributions])
        )
        if search_results.destination is not None and not search_results.activities:
            plan.itinerary = []
        elif search_results.activities:
            # Activities returned by tools are factual candidates. Keep their
            # day assignment instead of allowing the language model to move
            # one day's activity into another day while rewriting the plan.
            titles_by_day = {day.day: day.title for day in plan.itinerary}
            plan.itinerary = [
                DayPlan(
                    day=day_number,
                    title=titles_by_day.get(
                        day_number, f"{request_context.destination} Day {day_number}"
                    ),
                    activities=[
                        ActivityItem.model_validate(
                            activity.model_dump(
                                exclude={"destination", "day"}
                            )
                        )
                        for activity in search_results.activities
                        if activity.day == day_number
                    ],
                )
                for day_number in range(1, plan.days + 1)
            ]
        unavailable = [
            result
            for result in [
                search_results.destination,
                search_results.weather,
                search_results.attractions,
                search_results.routes,
                search_results.foods,
                search_results.knowledge,
                *search_results.preference_places,
            ]
            if result is not None and result.status == "unavailable"
        ]
        for result in unavailable:
            note = f"{result.provider} 暂时不可用：{result.message or result.error_code}"
            if note not in plan.notes:
                plan.notes.append(note)
        for record in search_results.tool_calls:
            if record.status == "unavailable":
                note = f"{record.provider} 的 {record.tool_name} 暂时不可用，未返回候选数据。"
                if note not in plan.notes:
                    plan.notes.append(note)
        if search_results.weather is not None and search_results.weather.status == "skipped":
            note = search_results.weather.message or "天气查询已跳过。"
            if note not in plan.notes:
                plan.notes.append(note)
    if request_context.status == "ready":
        # Preferences are optional; they must not turn a ready request into a
        # clarification merely because the planner proposed extra questions.
        plan.status = "complete"
        plan.missing_information = []
    return plan


def build_deterministic_trip_plan(
    request_context: TripRequest,
    search_results: SearchResults,
) -> TripPlan:
    """Assemble a plan from typed request and tool evidence without an LLM.

    This is the offline/demo path. It deliberately generates only generic
    titles and a factual summary; it does not pretend to perform natural
    language planning. The same evidence constraints used after an LLM run
    still protect dates, preferences, candidates, and tool status.
    """

    days = request_context.days or (
        request_context.nights + 1 if request_context.nights is not None else 1
    )
    destination = request_context.destination or "未指定目的地"
    draft = TripPlan(
        destination=destination,
        days=days,
        summary=(
            f"离线模式：根据已结构化的 {destination} {days} 日需求，"
            "整理免费工具返回的旅行数据。未调用语言模型。"
        ),
        budget=request_context.budget,
        preferences=request_context.preferences,
        notes=["离线模式未调用 LLM；每日标题和说明使用确定性模板。"],
    )
    return _apply_request_constraints(draft, request_context, search_results)


async def plan_trip_structured(
    planner: Agent,
    request: str,
    *,
    formatter: Agent | None = None,
    mode: str = "auto",
    tools: Any = None,
    request_context: TripRequest | None = None,
    search_results: SearchResults | None = None,
    on_prompt: PromptObserver | None = None,
    on_trace: TraceObserver | None = None,
) -> TripPlan | Any:
    """Run a trip plan and guarantee TripPlan for every structured mode.

    ``auto`` means direct structured output first, then a formatter agent. It
    does not mean silently returning arbitrary text when both attempts fail.
    """

    if mode == "text":
        return await plan_trip(
            planner,
            request,
            tools=tools,
            stage="planner.text",
            on_prompt=on_prompt,
            on_trace=on_trace,
        )

    if mode in {"direct", "auto"}:
        response = None
        try:
            response = await plan_trip(
                planner,
                request,
                options={"response_format": TripPlan},
                tools=tools,
                stage="planner.direct",
                on_prompt=on_prompt,
                on_trace=on_trace,
            )
            return _apply_request_constraints(
                _response_to_trip_plan(response), request_context, search_results
            )
        except Exception:
            if mode == "direct":
                raise

        response_value = None
        if response is not None:
            try:
                response_value = response.value
            except Exception:
                response_value = None

        if response_value is not None:
            first_text = json.dumps(
                response_value,
                ensure_ascii=False,
                default=lambda value: value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else str(value),
            )
        elif response is not None:
            first_text = response.text
        else:
            first_text = (
                await plan_trip(
                    planner,
                    request,
                    tools=tools,
                    stage="planner.retry",
                    on_prompt=on_prompt,
                    on_trace=on_trace,
                )
            ).text
    elif mode == "convert":
        first_text = (
            await plan_trip(
                planner,
                request,
                tools=tools,
                stage="planner.text",
                on_prompt=on_prompt,
                on_trace=on_trace,
            )
        ).text
    else:
        raise ValueError("mode must be text, convert, direct, or auto")

    if formatter is None:
        raise ValueError("formatter is required for convert mode")

    conversion_prompt = (
        "Convert the following travel plan into the required TripPlan JSON. "
        "Return only a JSON object, with no Markdown fences or explanation.\n\n"
        + first_text
    )

    # Most OpenAI-compatible providers support response_format, but some
    # proxies reject it. Try it first, then retry the same formatter without
    # the option and validate the returned text ourselves.
    first_error: Exception | None = None
    try:
        response = await plan_trip(
            formatter,
            conversion_prompt,
            options={"response_format": TripPlan},
            stage="formatter.structured",
            on_prompt=on_prompt,
            on_trace=on_trace,
        )
        return _apply_request_constraints(
            _response_to_trip_plan(response), request_context, search_results
        )
    except Exception as error:
        first_error = error

    try:
        response = await plan_trip(
            formatter,
            conversion_prompt,
            stage="formatter.text",
            on_prompt=on_prompt,
            on_trace=on_trace,
        )
        return _apply_request_constraints(
            _response_to_trip_plan(response), request_context, search_results
        )
    except Exception:
        pass

    # One explicit repair pass handles models that returned Markdown or a
    # nearly-correct object. It is still validated by Pydantic afterwards.
    repair_prompt = (
        "Repair the following output. Return ONLY one valid JSON object matching "
        "TripPlan: include every required field, use [] for missing lists, and "
        "do not add Markdown or commentary.\n\n"
        + first_text
    )
    try:
        response = await plan_trip(
            formatter,
            repair_prompt,
            options={"response_format": TripPlan},
            stage="formatter.repair",
            on_prompt=on_prompt,
            on_trace=on_trace,
        )
        return _apply_request_constraints(
            _response_to_trip_plan(response), request_context, search_results
        )
    except Exception as error:
        raise StructuredOutputError(
            "结构化输出失败：模型未返回可校验的 TripPlan。"
        ) from (first_error or error)
