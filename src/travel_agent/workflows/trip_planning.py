import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from agent_framework import Agent
from agent_framework.exceptions import ChatClientException
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.narrative import TripNarrative
from travel_agent.schemas.trip import ActivityItem, DayPlan, TripPlan
from travel_agent.rag.models import KnowledgeCitation
from travel_agent.validation import validate_trip_plan
from travel_agent.workflows.prompts import split_preferences_by_outcome


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


def _response_to_narrative(response: Any) -> TripNarrative:
    """Read the small model-authored object from MAF or raw JSON text."""

    value = _response_value(response)
    if value is not None:
        if isinstance(value, str):
            return TripNarrative.model_validate(json.loads(value))
        return TripNarrative.model_validate(value)
    return TripNarrative.model_validate(json.loads(response.text.strip()))


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
    requested_modes = list(request_context.transport_modes)
    if search_results is not None and search_results.route_modes:
        requested_modes = list(search_results.route_modes)
    # Keep the user's explicit transit request even if a hand-built or
    # partially failed SearchResults object omitted its route_modes metadata.
    if request_context.transport_mode == "transit" and not requested_modes:
        requested_modes = ["transit"]
    plan.transport_mode = "transit" if "transit" in requested_modes else "driving"
    plan.transport_modes = requested_modes
    if request_context.days is not None:
        plan.days = request_context.days
    elif request_context.nights is not None:
        plan.days = request_context.nights + 1
    plan.budget = request_context.budget
    plan.preferences = request_context.preferences
    if not plan.summary.strip():
        duration_text = (
            f"{plan.days}-day trip plan"
            if plan.days is not None
            else "flight candidates"
        )
        plan.summary = (
            f"A {plan.destination} "
            f"{duration_text}。"
        )
    # Search tools are the source of truth for factual candidates. The planner
    # may organize them, but it may not replace them with invented options.
    degraded = False
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
        if (
            request_context.requested_services
            and "food" in request_context.requested_services
            and request_context.preference_details
            and search_results.foods is not None
            and search_results.foods.status == "ok"
            and not plan.food
        ):
            note = (
                "No candidate carried a tag matching the requested cuisine; a generic "
                "eatery was not presented as that cuisine."
            )
            if note not in plan.notes:
                plan.notes.append(note)
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
                for day_number in range(1, (plan.days or 0) + 1)
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
        degraded = bool(unavailable) or any(
            record.status == "unavailable" for record in search_results.tool_calls
        )
        # A place the user named by hand is the one thing they will look for
        # first. When no provider could verify it, the plan has to say so in
        # its own words rather than leave the label in `preferences` looking
        # honoured, which is what let a summary claim a trip was built around
        # a landmark that never reached the itinerary.
        _, unsatisfied = split_preferences_by_outcome(request_context, search_results)
        if unsatisfied:
            degraded = True
            plan.notes.append(
                "These explicitly requested places could not be scheduled: "
                + ", ".join(unsatisfied)
                + ". A verified matching stop must reach the itinerary on the "
                "requested day, when specified. A lookup alone does not prove "
                "this preference was fulfilled."
            )
        # Only when a place search actually ran and produced nothing. An
        # itinerary that is empty because no search was attempted (a fixture
        # or a request whose scope excludes activities) is not a failure.
        attempted_places = (
            search_results.attractions is not None
            or bool(search_results.preference_places)
        )
        if not plan.itinerary and attempted_places:
            degraded = True
            plan.notes.append(
                "No daily itinerary was produced: the place tools returned no verifiable candidates."
            )

        hints = {detail.planning_hint for detail in request_context.preference_details}
        known_types = {item.venue_type for item in search_results.activities}
        if "venue_mix" in hints and not {"indoor", "outdoor"} <= known_types:
            degraded = True
            plan.notes.append(
                "The indoor/outdoor mix was not satisfied: the tools did not return enough candidates with a verified venue type."
            )
        # A strict indoor/outdoor request filters candidates in
        # ``assign_activities``. Filtering everything out must be reported the
        # same way, otherwise an empty itinerary looks like a complete plan.
        for strict_hint, venue_type, label in (
            ("indoor_only", "indoor", "indoor"),
            ("outdoor_only", "outdoor", "outdoor"),
        ):
            if strict_hint in hints and venue_type not in known_types:
                degraded = True
                plan.notes.append(
                    f"The {label}-only preference was not satisfied: no candidate was "
                    f"explicitly tagged {label}, so nothing was scheduled."
                )
        for result in unavailable:
            note = f"{result.provider} is unavailable: {result.message or result.error_code}"
            if note not in plan.notes:
                plan.notes.append(note)
        for record in search_results.tool_calls:
            if record.status == "unavailable":
                note = f"{record.tool_name} on {record.provider} is unavailable; no candidates returned."
                if note not in plan.notes:
                    plan.notes.append(note)
        if search_results.weather is not None and search_results.weather.status == "skipped":
            note = search_results.weather.message or "The weather lookup was skipped."
            if note not in plan.notes:
                plan.notes.append(note)
    if request_context.status == "ready":
        # Preferences are optional; they must not turn a ready request into a
        # clarification merely because the planner proposed extra questions.
        plan.status = "partial" if degraded else "complete"
        plan.missing_information = []
    return plan


def build_deterministic_trip_plan(
    request_context: TripRequest,
    search_results: SearchResults,
    *,
    offline: bool = True,
    generation_source: str | None = None,
) -> TripPlan:
    """Assemble a plan from typed request and tool evidence without an LLM.

    The offline CLI path and online LLM degradation share the same factual
    assembly, but their user-facing explanation must stay distinct. This
    function deliberately generates only generic titles and a factual summary;
    it does not pretend to perform natural language planning. The same
    evidence constraints used after an LLM run still protect dates,
    preferences, candidates, and tool status.
    """

    days = request_context.days
    if days is None and request_context.nights is not None:
        days = request_context.nights + 1
    if days is None and set(request_context.requested_services) & {
        "activities",
        "guide",
        "food",
    }:
        days = 1
    destination = request_context.destination or "an unspecified destination"
    duration_text = (
        f"{days}-day trip plan" if days is not None else "flight candidates"
    )
    source = generation_source or (
        "offline" if offline else "deterministic_fallback"
    )
    if source == "offline":
        summary = (
            f"Offline mode: a {duration_text} for {destination}, assembled from the "
            "free tool results for the already structured request. No model was called."
        )
        notes = ["Offline mode called no model; day titles use deterministic templates."]
    elif source == "deterministic_fallback":
        summary = f"A {duration_text} for {destination}, based on the confirmed request."
        notes = ["Model structured output failed validation; a deterministic factual plan was used."]
    else:
        summary = f"A {duration_text} for {destination}, based on the confirmed request."
        notes = []
    draft = TripPlan(
        generation_source=source,
        destination=destination,
        days=days,
        summary=summary,
        budget=request_context.budget,
        preferences=request_context.preferences,
        notes=notes,
    )
    return _apply_request_constraints(draft, request_context, search_results)


def _merge_narrative(
    narrative: TripNarrative,
    request_context: TripRequest,
    search_results: SearchResults,
) -> TripPlan:
    """Merge bounded prose into a deterministic fact plan."""

    titles: dict[int, str] = {}
    for item in narrative.daily_titles:
        if item.day in titles:
            raise StructuredOutputError(
                f"daily_titles contains duplicate day {item.day}"
            )
        if request_context.days is not None and item.day > request_context.days:
            raise StructuredOutputError(
                f"daily_titles contains out-of-range day {item.day}"
            )
        titles[item.day] = item.title

    plan = build_deterministic_trip_plan(
        request_context,
        search_results,
        offline=False,
        generation_source="llm",
    )
    plan.summary = narrative.summary
    plan.generation_source = "llm"
    plan.itinerary = [
        day.model_copy(update={"title": titles.get(day.day, day.title)})
        for day in plan.itinerary
    ]
    return plan


# One retry for a schema failure. The planner runs at temperature 0, so the
# retry is only worth making because the prompt changes: the validation error
# is appended, which is the single piece of new information available.
NARRATIVE_ATTEMPTS = 2


def with_validation_feedback(prompt: str, detail: str) -> str:
    """Append the schema failure so a retry is not a verbatim replay.

    ``validation_errors`` was already collected for the run log and the final
    exception text, but was never shown to the model that produced the
    invalid output.
    """

    return (
        f"{prompt}\n\n"
        "The previous output failed schema validation:\n"
        f"{detail}\n"
        "Fix only that problem and return a valid JSON object for the schema. "
        "Do not change any fact that came from a tool."
    )


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

    With typed request and search context, ``auto`` asks for a small
    ``TripNarrative`` and deterministically assembles the final ``TripPlan``.
    The legacy full-plan conversion path remains available for ``convert`` and
    callers that do not provide typed evidence.
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

    validation_errors: list[str] = []
    last_error: Exception | None = None

    def deterministic_fallback() -> TripPlan | None:
        """Keep typed tool facts usable when narrative generation is unavailable."""

        if request_context is None or search_results is None:
            return None
        fallback = build_deterministic_trip_plan(
            request_context,
            search_results,
            offline=False,
        )
        only_optional_skipped = (
            any(
                result is not None and result.status == "skipped"
                for result in (
                    search_results.weather,
                    search_results.attractions,
                    search_results.routes,
                    search_results.foods,
                    search_results.knowledge,
                )
            )
            and not any(
                result is not None and result.status == "unavailable"
                for result in (
                    search_results.weather,
                    search_results.attractions,
                    search_results.routes,
                    search_results.foods,
                    search_results.knowledge,
                )
            )
        )
        if only_optional_skipped:
            fallback.notes = [
                note
                for note in fallback.notes
                if not note.startswith("Model structured output failed validation")
            ]
        else:
            fallback.notes.append("The detailed validation errors are in the run log.")
        # A request that only asked for flights is incomplete when its sole
        # required provider returned no candidates. Skipped optional sources
        # do not make an otherwise usable plan partial.
        if (
            request_context.requested_services == ["flights"]
        ):
            fallback.status = "partial"
        return fallback

    # This is the normal CLI path. The model is not asked to reproduce the
    # large evidence object, so truncation or a provider-specific full-schema
    # limitation cannot erase successful tool results.
    if mode == "auto" and request_context is not None and search_results is not None:
        attempt_prompt = request
        for attempt in range(1, NARRATIVE_ATTEMPTS + 1):
            try:
                response = await plan_trip(
                    planner,
                    attempt_prompt,
                    options={"response_format": TripNarrative, "max_tokens": 1000},
                    tools=tools,
                    stage=(
                        "planner.narrative"
                        if attempt == 1
                        else f"planner.narrative.retry{attempt - 1}"
                    ),
                    on_prompt=on_prompt,
                    on_trace=on_trace,
                )
            except Exception as error:
                # Transport failures were already retried inside plan_trip and
                # a second schema attempt cannot help them.
                last_error = error
                if mode == "direct":
                    raise
                break
            try:
                narrative = _response_to_narrative(response)
                return _merge_narrative(narrative, request_context, search_results)
            except Exception as error:
                detail = f"planner.narrative: {type(error).__name__}: {error}"
                validation_errors.append(detail[:800])
                _emit_trace(
                    on_trace,
                    "planner.narrative.validation",
                    datetime.now(timezone.utc),
                    asyncio.get_running_loop().time(),
                    None,
                    "error",
                    StructuredOutputError(detail),
                    model=_agent_model(planner),
                )
                if mode == "direct":
                    raise StructuredOutputError(detail) from error
                if attempt < NARRATIVE_ATTEMPTS:
                    attempt_prompt = with_validation_feedback(request, detail)
        fallback = deterministic_fallback()
        if fallback is not None:
            return fallback
        raise StructuredOutputError(
            "Structured output failed: without typed request/search context no factual plan can be assembled."
        ) from (last_error or None)

    def parse_response(response: Any, stage_name: str) -> TripPlan | None:
        """Parse one model response and record schema failures as trace events."""

        try:
            plan = _apply_request_constraints(
                _response_to_trip_plan(response), request_context, search_results
            )
            plan.generation_source = "llm"
            return plan
        except Exception as error:
            detail = f"{stage_name}: {type(error).__name__}: {error}"
            validation_errors.append(detail[:800])
            # The HTTP/model call itself can succeed while the returned object
            # fails Pydantic validation. Keep that failure visible in the run
            # trajectory instead of reporting a misleading all-green stage.
            started_at = datetime.now(timezone.utc)
            _emit_trace(
                on_trace,
                f"{stage_name}.validation",
                started_at,
                asyncio.get_running_loop().time(),
                None,
                "error",
                StructuredOutputError(detail),
                model=_agent_model(planner),
            )
            return None

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
            parsed = parse_response(response, "planner.direct")
            if parsed is not None:
                return parsed
            raise StructuredOutputError(validation_errors[-1])
        except Exception as error:
            last_error = error
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
            try:
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
            except Exception as error:
                last_error = error
                fallback = deterministic_fallback()
                if fallback is not None:
                    return fallback
                raise
    elif mode == "convert":
        try:
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
        except Exception as error:
            last_error = error
            fallback = deterministic_fallback()
            if fallback is not None:
                return fallback
            raise
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
        parsed = parse_response(response, "formatter.structured")
        if parsed is not None:
            return parsed
        first_error = StructuredOutputError(validation_errors[-1])
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
        parsed = parse_response(response, "formatter.text")
        if parsed is not None:
            return parsed
        first_error = first_error or StructuredOutputError(validation_errors[-1])
    except Exception as error:
        first_error = first_error or error

    # Once typed evidence is available, another full JSON repair call cannot
    # add factual information. Use the deterministic evidence plan after the
    # two formatter compatibility attempts above; this bounds latency for the
    # default auto workflow. Pure convert mode keeps the extra repair pass
    # because it may not have a typed request/search context to fall back to.
    if mode == "auto" and request_context is not None and search_results is not None:
        fallback = deterministic_fallback()
        if fallback is not None:
            return fallback

    # One explicit repair pass handles models that returned Markdown or a
    # nearly-correct object when no typed evidence fallback is available. It is
    # still validated by Pydantic afterwards.
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
        parsed = parse_response(response, "formatter.repair")
        if parsed is not None:
            return parsed
    except Exception as error:
        first_error = first_error or error

    fallback = deterministic_fallback()
    if fallback is not None:
        # The tool evidence is already typed and authoritative. Returning a
        # deterministic plan keeps successful searches usable when a provider
        # truncates or misformats the narrative JSON.
        return fallback

    details = " | ".join(validation_errors[-3:])
    message = "Structured output failed: the model returned no valid TripPlan."
    if details:
        message += f" Details: {details}"
    raise StructuredOutputError(message) from (first_error or last_error)
