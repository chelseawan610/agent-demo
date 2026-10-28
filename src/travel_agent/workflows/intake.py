import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Agent
from travel_agent.schemas import request as request_fields
from travel_agent.schemas.request import DEFAULT_SERVICES, BudgetSpec, TripRequest
from travel_agent.workflows.trip_planning import TraceObserver, plan_trip


PromptObserver = Callable[[str, str, dict[str, Any] | None], None]


# This is deliberately a small identity-safety map, not a natural-language
# parser.  The LLM still interprets the request; these exact aliases only stop
# a critical city identity from being silently changed before tool execution.
DESTINATION_ALIASES: dict[str, str] = {
    "东京": "Tokyo",
    "東京": "Tokyo",
    "tokyo": "Tokyo",
    "首尔": "Seoul",
    "汉城": "Seoul",
    "서울": "Seoul",
    "seoul": "Seoul",
    "香港": "Hong Kong",
    "hong kong": "Hong Kong",
    "hongkong": "Hong Kong",
    "纽约": "New York",
    "new york": "New York",
    "newyork": "New York",
    "伦敦": "London",
    "london": "London",
    "巴黎": "Paris",
    "paris": "Paris",
    "新加坡": "Singapore",
    "singapore": "Singapore",
}

def _normalized_city(value: str | None) -> str:
    """Normalize a canonical city for exact comparison only."""

    if not value:
        return ""
    return " ".join(value.casefold().split())


# Facts the user must have stated for the program to act on them. Each is a
# value a model can make plausible out of nothing, and each one silently
# removes a clarification question when it does: a filled field is simply not
# "missing", so ``required_missing_fields`` goes quiet.
EVIDENCE_REQUIRED_FIELDS = (
    "origin",
    "start_date",
    "return_date",
    "budget",
)

# Clarification labels map back to the field they were asking about. A bare
# answer ("上海", "十一月一号") cites nothing, but it is the user's answer to
# our own question, so the value stands.
ASKED_FIELD_LABELS = {
    request_fields.ORIGIN: "origin",
    request_fields.DEPARTURE_DATE: "start_date",
    request_fields.CHECK_IN_DATE: "start_date",
    request_fields.DESTINATION: "destination",
}

_WHITESPACE = re.compile(r"\s+")


def _normalize_for_quote(value: str) -> str:
    """Compare quotes the way a reader would, not byte for byte.

    NFKC folds full-width digits and punctuation onto their ASCII forms, so a
    model that writes ``２０２６`` still matches ``2026``. Case and internal
    whitespace are ignored for the same reason.
    """

    folded = unicodedata.normalize("NFKC", value or "").casefold()
    return _WHITESPACE.sub("", folded)


def drop_uncited_fields(
    request: TripRequest,
    source_text: str,
    *,
    exempt: set[str] | None = None,
) -> TripRequest:
    """Clear any fact the model could not quote from the user's own words.

    This replaces a per-field set of keyword and regular-expression guards.
    Those asked the program to decide, in every language and phrasing, whether
    a user had mentioned a date or a departure; the table was never complete
    and every new field needed another one. Here the model does the reading --
    it is already reading the sentence -- and names the span it relied on,
    while the program does the one thing a program is reliably better at:
    checking that the span is really there.

    Nothing new is asked of the user here. A cleared field is simply absent,
    so ``TripRequest.required_missing_fields`` raises it in the ordinary
    clarification flow, the same as if the model had left it null.
    """

    exempt = exempt or set()
    text = _normalize_for_quote(source_text)
    evidence = request.field_evidence or {}
    values = request.model_dump()
    cleared: list[str] = []

    for field in EVIDENCE_REQUIRED_FIELDS:
        if values.get(field) is None or field in exempt:
            continue
        quote = _normalize_for_quote(evidence.get(field, ""))
        # A one-character quote carries no information; almost any text
        # contains it. Requiring two keeps a citation from being a formality.
        if len(quote) >= 2 and quote in text:
            continue
        values[field] = None
        cleared.append(field)

    # One-way is the only flight type that removes a requirement, because a
    # round trip is what needs a return boundary. Round trip is the documented
    # default, so an uncited one_way falls back to it rather than to null.
    if values.get("flight_type") == "one_way" and "flight_type" not in exempt:
        quote = _normalize_for_quote(evidence.get("flight_type", ""))
        if not (len(quote) >= 2 and quote in text):
            values["flight_type"] = "round_trip"
            cleared.append("flight_type")

    # A cleared departure date must not keep a return date derived from it.
    if "start_date" in cleared:
        values["return_date"] = None

    values["requested_services"] = _cited_services(
        list(values.get("requested_services") or []), evidence, text
    )
    if not cleared and values["requested_services"] == request.requested_services:
        return request
    return TripRequest.model_validate(values)


def _cited_services(
    services: list[str],
    evidence: dict[str, str],
    text: str,
) -> list[str]:
    """Keep the scope the user named, or the documented default.

    Services are not like the scalar facts above: a bare "我要去东京玩" is
    defined to mean a complete trip, so an absent citation cannot mean "the
    user did not ask for it". The rule is therefore about explicitness. Once
    the user names any service at all, the named set is the whole request and
    an extra service the model added is dropped. When no service is named,
    nothing is removed and the default stands.
    """

    cited = {
        key.split(":", 1)[1]
        for key, quote in evidence.items()
        if key.startswith("service:")
        and len(_normalize_for_quote(quote)) >= 2
        and _normalize_for_quote(quote) in text
    }
    # An itinerary is what activities and guide jointly describe, so naming
    # either one keeps the pair rather than splitting a single intent.
    if cited & {"activities", "guide"}:
        cited |= {"activities", "guide"}
    default = set(DEFAULT_SERVICES)
    narrowed = bool(cited & default)

    kept: list[str] = []
    for service in services:
        if service in default:
            # Part of the documented "complete trip" default. It survives
            # unless the user named a different subset of the defaults, as in
            # "给我东京攻略", where flights and hotels were never asked for.
            if not narrowed or service in cited:
                kept.append(service)
        elif service in cited:
            # Outside the default set, so it is only ever present because the
            # user asked: "顺便推荐餐厅" keeps food, an uncited food does not.
            kept.append(service)
    return kept or services


def apply_evidence_guards(
    request: TripRequest,
    source_text: str,
    *,
    requested_fields: set[str] | None = None,
) -> TripRequest:
    """Keep every model-filled fact tied to something the user actually said."""

    exempt = {
        ASKED_FIELD_LABELS[label]
        for label in (requested_fields or set())
        if label in ASKED_FIELD_LABELS
    }
    # A duration boundary question ("回程日期或游玩天数") asks for whichever
    # boundary the user prefers, so an answer may legitimately supply either.
    if any("return" in label or "check-out" in label for label in (requested_fields or set())):
        exempt |= {"return_date"}
    request = protect_explicit_destination(request, source_text)
    request = drop_uncited_fields(request, source_text, exempt=exempt)
    return request


def _explicit_destinations(text: str) -> list[tuple[int, str]]:
    """Return exact known city aliases in source order.

    English aliases use word boundaries so ``seoul`` is not matched inside a
    larger word.  CJK aliases are matched literally because they have no word
    separators.  Unknown cities are intentionally ignored and remain the
    model/provider responsibility.
    """

    folded = text.casefold()
    matches: list[tuple[int, str]] = []
    for alias, canonical in sorted(
        DESTINATION_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        alias_folded = alias.casefold()
        if alias_folded.isascii():
            pattern = rf"(?<![a-z]){re.escape(alias_folded)}(?![a-z])"
            positions = (match.start() for match in re.finditer(pattern, folded))
        else:
            positions = (
                match.start()
                for match in re.finditer(re.escape(alias_folded), folded)
            )
        matches.extend((position, canonical) for position in positions)
    return sorted(matches, key=lambda item: item[0])


def _destination_candidates(
    text: str,
    matches: list[tuple[int, str]],
) -> list[str]:
    """Choose explicit destination candidates without treating preferences as destinations."""

    folded = text.casefold()
    scored: list[tuple[int, str]] = []
    for position, canonical in matches:
        before = folded[max(0, position - 12) : position]
        after = folded[position : position + 12]
        score = 0
        if re.search(r"(?:去|到|前往|目的地是|前往|to\s+|visit\s+|travel\s+to\s+)$", before):
            score += 2
        if re.search(r"(?:旅行|旅游|游玩|玩|攻略|trip|travel|guide)", after):
            score += 1
        scored.append((score, canonical))

    highest = max((score for score, _ in scored), default=0)
    if highest > 0:
        return list(dict.fromkeys(canonical for score, canonical in scored if score == highest))
    return list(dict.fromkeys(canonical for _, canonical in scored))


def protect_explicit_destination(
    request: TripRequest,
    source_text: str,
) -> TripRequest:
    """Prevent a model city hallucination from reaching SearchPlan.

    When the user explicitly names one known city, that user-provided identity
    wins over a conflicting normalized model value.  With multiple cities, a
    unique directional cue such as ``from A to B`` / ``从 A 去 B`` is preferred.
    Ambiguous city mentions are left for the normal clarification flow instead
    of being silently sent to a provider.  The function does not try to guess
    unknown cities; the model and geocoder remain responsible for those.
    """

    matches = _explicit_destinations(source_text)
    if not matches:
        return request

    origin = _normalized_city(request.origin)
    candidates = _destination_candidates(source_text, matches)
    non_origin = [canonical for canonical in candidates if _normalized_city(canonical) != origin]
    if not non_origin:
        # Every alias this map recognized in the text is the origin, so the
        # destination is a city the map does not know ("从东京去马尔代夫",
        # "from Tokyo to Osaka"). There is no user evidence to correct the
        # destination with, and treating the origin as the destination would
        # send the wrong city to every provider.
        return request
    if len(non_origin) == 1:
        expected = non_origin[0]
    elif _normalized_city(request.destination) in {
        _normalized_city(candidate) for candidate in candidates
    }:
        # The model chose one of several explicit cities; without a unique
        # directional cue, preserve it instead of making a second guess.
        return request
    else:
        # Ambiguous source text should be clarified by the user, not sent to
        # search providers with a silently selected city.
        values = request.model_dump()
        values["destination"] = None
        return TripRequest.model_validate(values)
    if _normalized_city(request.destination) == _normalized_city(expected):
        return request

    values = request.model_dump()
    values["destination"] = expected
    return TripRequest.model_validate(values)


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
    answers: list[str] = []
    # Fields a previous round actually asked the user about. An answer to our
    # own question is user evidence even when it is a bare value.
    asked_fields: set[str] = set()
    for round_number in range(max_rounds + 1):
        extraction_text = current_text
        if previous_request is not None and answers:
            # Every user turn is replayed, not just the latest one, so round 3
            # still sees what the user said in round 2. Only user text is
            # replayed: feeding the previous model JSON back would let a
            # hallucinated destination reinforce itself.
            numbered = "\n".join(
                f"{index}. {answer}" for index, answer in enumerate(answers, start=1)
            )
            extraction_text = (
                "用户原始旅行需求（只读取用户原文）：\n"
                f"{initial_text}\n\n"
                "用户后续补充，按时间先后排列（只读取用户原文）：\n"
                f"{numbered}\n\n"
                "请只从以上用户文字提取字段；不要复制任何上一轮模型输出。"
                "若后面的补充与前面冲突，以最后一次用户说法为准。"
            )
        request = await extract_trip_request(
            agent,
            extraction_text,
            on_prompt=on_prompt,
            on_trace=on_trace,
        )
        source_turns = "\n".join([initial_text, *answers])
        # Merge first, then protect. The protect_* functions read every user
        # turn and are the last word on identity and scope; running them before
        # the merge let merged values silently overwrite their decision.
        if previous_request is not None:
            request = merge_trip_requests(previous_request, request)
        request = apply_evidence_guards(
            request, source_turns, requested_fields=asked_fields
        )
        if request.status == "ready":
            budget_text = _format_budget(request.budget)
            planning_text = (
                f"请求范围：{', '.join(request.requested_services)}\n"
                f"{budget_text}\n"
                "已确认的出发日期、回程日期、天数和晚数是程序边界，必须原样保留；"
                "未指定预算时不要擅自改成中等预算。攻略或活动-only 请求的天数可以按需求合理安排。\n"
                + current_text
            )
            return request, planning_text
        if ask_clarification is None or round_number == max_rounds:
            return request, current_text

        asked_fields.update(request.missing_fields)
        answer = await ask_clarification(request)
        if not answer.strip():
            return request, current_text
        previous_request = request
        answers.append(answer.strip())
        current_text = f"{current_text}\n\n用户补充信息：{answers[-1]}"

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


CORRECTABLE_FIELDS = (
    "origin",
    "destination",
    "start_date",
    "return_date",
    "flight_type",
    "days",
    "nights",
    "duration_profile",
    "budget",
)


def merge_trip_requests(old: TripRequest, new: TripRequest) -> TripRequest:
    """Keep confirmed facts, but let the newest user turn correct them.

    Two different situations used to be collapsed into one rule. A model can
    drift and rewrite a city the user already confirmed, which must be
    rejected; a user can also deliberately change their mind ("改成4天"),
    which must be accepted. ``new.stated_fields`` separates them: it lists the
    fields the latest user text actually spoke, so only those may overwrite an
    earlier value. Everything else still falls back to the confirmed value, and
    an empty ``stated_fields`` reproduces the older gap-filling behavior.
    """

    values = new.model_dump()
    stated = set(new.stated_fields)
    for field in CORRECTABLE_FIELDS:
        if field in stated and values.get(field) is not None:
            # Spoken in this turn: the user is stating or correcting it.
            continue
        old_value = getattr(old, field)
        if field == "duration_profile" and old_value == "unknown":
            continue
        if old_value is not None:
            values[field] = old_value

    # A corrected trip length must drop a night count that was only derived
    # from the previous length, otherwise "改成4天" keeps the old 2 nights.
    if "days" in stated and "nights" not in stated and not old.nights_explicit:
        values["nights"] = None
    # Likewise a corrected departure date invalidates a return date that the
    # user never stated; the duration boundary is re-derived from days.
    if "start_date" in stated and "return_date" not in stated and old.days is not None:
        values["return_date"] = None

    values["nights_explicit"] = bool(
        (old.nights_explicit and "nights" not in stated)
        or values.get("nights_explicit", False)
    )
    # Evidence travels with the fact it justifies. A later turn usually only
    # talks about what changed, so without this an origin confirmed and cited
    # in round one loses its citation in round three and is cleared as
    # unsupported. A newer citation for the same field still wins, which is
    # what a correction needs.
    values["field_evidence"] = {**old.field_evidence, **new.field_evidence}
    values["preferences"] = list(
        dict.fromkeys([*old.preferences, *values["preferences"]])
    )
    details = {
        detail.label: detail
        for detail in [*old.preference_details, *new.preference_details]
    }
    values["preference_details"] = list(details.values())
    if "requested_services" not in stated:
        values["requested_services"] = old.requested_services
    return TripRequest(**values)
