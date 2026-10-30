"""User-facing rendering.

Model-authored prose and fixed Markdown labels follow the user's language.
JSON field names and provider identifiers stay stable English API values.
"""

from travel_agent.schemas.trip import TripPlan
from travel_agent.schemas.request import TripRequest


DEMO_NOTICE = (
    "> Note: flight and hotel candidates are local demo data. They are not live "
    "inventory, prices, or bookings. Weather, places, and routes come from the "
    "public sources listed above; verify before you travel."
)

DEMO_NOTICE_ZH = (
    "> 注意：航班和酒店候选为本地演示数据，不代表实时库存、价格或已完成预订；"
    "天气、地点和路线来自上方列出的公共数据源，出行前仍需核实。"
)

VENUE_LABELS = {
    "indoor": "; indoor",
    "outdoor": "; outdoor",
    "mixed": "; indoor or outdoor",
    "unknown": "; venue type unverified",
}

MODE_LABELS = {"driving": "driving", "transit": "public transit"}
MODE_LABELS_ZH = {"driving": "驾车", "transit": "公共交通"}

MISSING_LABELS_ZH = {
    "destination": "目的地",
    "origin city": "出发城市",
    "departure date": "出发日期",
    "check-in date": "入住日期",
    "trip length, or return/check-out date": "旅行天数，或返程/退房日期",
    "return date, or trip length": "返程日期，或旅行天数",
    "trip length, or nights/check-out date": "旅行天数，或住宿晚数/退房日期",
    "number of nights, or check-out date": "住宿晚数，或退房日期",
}

SERVICE_LABELS_ZH = {
    "flights": "航班",
    "hotels": "酒店",
    "activities": "活动",
    "guide": "攻略",
    "food": "餐饮",
}


def _notice(language: str) -> str:
    return DEMO_NOTICE_ZH if language == "Chinese" else DEMO_NOTICE


def _scope_labels(request: TripRequest) -> list[str]:
    if request.response_language != "Chinese":
        return request.requested_services
    return [SERVICE_LABELS_ZH.get(service, service) for service in request.requested_services]


def _render_note(note: str, chinese: bool) -> str:
    """Translate stable program notes while preserving provider diagnostics."""

    if not chinese:
        return note
    known = {
        "The indoor/outdoor mix was not satisfied: the tools did not return enough candidates with a verified venue type.":
            "未满足室内外结合偏好：工具没有返回足够多、且场所类型已核验的候选。",
        "search_attractions on Overpass API is unavailable; no candidates returned.":
            "Overpass API 的景点搜索不可用，未返回候选。",
        "search_food on Overpass API is unavailable; no candidates returned.":
            "Overpass API 的餐饮搜索不可用，未返回候选。",
    }
    return known.get(note, note)


def _render_link_disclaimer(disclaimer: str, chinese: bool) -> str:
    """Keep the stable search-link disclaimer readable in both languages."""

    if not chinese:
        return disclaimer
    if disclaimer == "A search link only; no live price or inventory was read.":
        return "仅生成搜索入口，未自动读取实时价格或库存。"
    return disclaimer


def render_service_menu() -> str:
    """Explain the supported entry types before the user writes a request."""

    return "\n".join(
        [
            "# What would you like help with?",
            "",
            "1. Full trip plan (origin, destination, departure date, days or return date)",
            "2. Round-trip flights (origin, destination, departure date, return date or length)",
            "3. Hotels (destination, check-in date, nights or check-out date)",
            "4. Guide and activities (destination; days optional)",
            "",
            'You can also just describe what you want, such as "flights and hotels"',
            'or "机票和酒店". Chinese and English both work.',
            "If unsure, pick 1; the scope is adjusted from what you actually write.",
        ]
    )


def render_trip_request(request: TripRequest) -> str:
    """Render missing intake fields before any travel tool is called."""

    chinese = request.response_language == "Chinese"
    missing = (
        [MISSING_LABELS_ZH.get(field, field) for field in request.missing_fields]
        if chinese
        else request.missing_fields
    )
    lines = [
        "# 需要补充旅行信息" if chinese else "# More trip details needed",
        "",
        (
            f"请求范围：{'、'.join(_scope_labels(request))}"
            if chinese
            else f"Requested scope: {', '.join(_scope_labels(request))}"
        ),
        "",
        (
            "调用旅行工具前，请补充："
            if chinese
            else "Before any travel tool runs, please provide:"
        ),
    ]
    lines.extend(f"- {field}" for field in missing)
    lines.extend(
        [
            "",
            (
                "可选信息：预算、交通方式和兴趣偏好。所需的天数或日期边界取决于上面的请求范围。"
                if chinese
                else "Optional: budget, transport mode, and interests. The duration "
                "boundary depends on the request type above."
            ),
            "",
            _notice(request.response_language or "English"),
        ]
    )
    return "\n".join(lines)


def format_plan(text: str) -> str:
    """Wrap the model response in a consistent, user-facing Markdown format."""

    body = text.strip()
    if not body:
        body = "The model did not produce a travel plan."

    if DEMO_NOTICE not in body:
        body = f"{body}\n\n{DEMO_NOTICE}"

    return f"# Travel plan\n\n{body}"


def render_trip_plan(plan: TripPlan) -> str:
    """Render one validated TripPlan as user-facing Markdown."""

    chinese = plan.response_language == "Chinese"
    notice = _notice(plan.response_language)
    mode_labels = MODE_LABELS_ZH if chinese else MODE_LABELS
    if plan.status == "needs_clarification":
        lines = [
            "# 需要补充旅行信息" if chinese else "# More trip details needed",
            "",
            plan.summary or ("请补充以下信息：" if chinese else "Please provide the following:"),
        ]
        missing = plan.missing_information or plan.notes
        lines.extend(["", "## 仍需补充" if chinese else "## Still needed"])
        lines.extend(f"- {item}" for item in missing)
        lines.extend(["", notice])
        return "\n".join(lines)

    title_suffix = "（部分数据不可用）" if chinese and plan.status == "partial" else (
        " (some data unavailable)" if plan.status == "partial" else ""
    )
    if plan.days is None:
        title = (
            f"{plan.destination} — 航班候选"
            if chinese
            else f"{plan.destination} — flight candidates"
        )
    elif chinese:
        title = f"{plan.destination} — {plan.days}日旅行计划"
    else:
        title = f"{plan.destination} — {plan.days}-day trip plan"
    lines = [f"# {title}{title_suffix}", "", plan.summary]
    if plan.generation_source == "deterministic_fallback":
        lines.extend(
            [
                "",
                (
                    "> 叙事生成不可用。本页所有事实均直接来自成功返回的工具结果。"
                    if chinese
                    else "> Narrative generation was unavailable. Every fact on this page "
                    "was assembled directly from the tool results that succeeded."
                ),
            ]
        )
    elif plan.generation_source == "offline":
        lines.extend(
            [
                "",
                (
                    "> 离线模式：没有调用语言模型。本页由本地确定性逻辑组装。"
                    if chinese
                    else "> Offline mode: no language model was called. This page was "
                    "assembled by local deterministic logic."
                ),
            ]
        )
    if plan.status == "partial":
        lines.extend(
            [
                "",
                (
                    "> 工作流已完成，但部分数据源不可用。下面只展示已成功获取并核验的结果。"
                    if chinese
                    else "> The workflow completed, but some data sources were "
                    "unavailable. Only results that were retrieved and verified "
                    "are shown below."
                ),
            ]
        )
    if plan.budget is not None:
        # The budget is recorded but no supplier is filtered by it: flight and
        # hotel candidates come from the demo adapter, which has no inventory
        # to price against. Saying so prevents a reader from assuming the plan
        # was checked against this number.
        budget_caveat = (
            "该预算仅被记录，未用于筛选候选；航班和酒店价格为演示数据。"
            if chinese
            else "This budget is recorded only; it was not used to filter "
            "candidates. Flight and hotel prices are demo data."
        )
        if plan.budget.amount is not None:
            lines.extend(
                [
                    "",
                    "## 预算" if chinese else "## Budget",
                    f"{plan.budget.amount:g} {plan.budget.currency} "
                    f"({plan.budget.period or 'total'})",
                    budget_caveat,
                ]
            )
        elif plan.budget.level is not None:
            lines.extend(
                [
                    "",
                    "## 预算" if chinese else "## Budget",
                    f"级别：{plan.budget.level}" if chinese else f"Level: {plan.budget.level}",
                    budget_caveat,
                ]
            )
    if plan.flights:
        lines.extend(["", "## 航班候选" if chinese else "## Flight options"])
        lines.extend(
            f"- {flight.airline}: {flight.origin} → {flight.destination}, "
            f"{flight.departure}-{flight.arrival}, {flight.price}"
            for flight in plan.flights
        )
    if plan.hotels:
        lines.extend(["", "## 酒店候选" if chinese else "## Hotel options"])
        lines.extend(
            (
                f"- {hotel.name}（{hotel.area}）：{hotel.nights}晚，"
                f"{hotel.price_per_night}/晚，评分 {hotel.rating}"
                if chinese
                else f"- {hotel.name} ({hotel.area}): {hotel.nights} nights, "
                f"{hotel.price_per_night}/night, rated {hotel.rating}"
            )
            for hotel in plan.hotels
        )
    if plan.search_links:
        lines.extend(["", "## 实时搜索入口" if chinese else "## Live search links"])
        for link in plan.search_links:
            label = (
                {"flights": "航班", "hotels": "酒店"}.get(link.service, link.service)
                if chinese
                else "Flights" if link.service == "flights" else "Hotels"
            )
            disclaimer = _render_link_disclaimer(link.disclaimer, chinese)
            lines.append(f"- [{label}: {link.provider}]({link.url}) — {disclaimer}")
    if plan.transport_modes:
        lines.extend(
            [
                "",
                "## 交通方式" if chinese else "## Transport",
                (
                    "已请求：" if chinese else "Requested: "
                )
                + " + ".join(mode_labels[mode] for mode in plan.transport_modes),
            ]
        )
    if plan.weather:
        lines.extend(["", "## 天气参考" if chinese else "## Weather"])
        for weather in plan.weather:
            rain = (
                (
                    f"，最高降水概率 {weather.precipitation_probability_max}%"
                    if chinese
                    else f", max precipitation probability {weather.precipitation_probability_max}%"
                )
                if weather.precipitation_probability_max is not None
                else ""
            )
            lines.append(
                f"- {weather.date}: {weather.temperature_min_c:g}–"
                f"{weather.temperature_max_c:g}°C{rain}"
            )
    if plan.itinerary:
        lines.extend(["", "## 每日行程" if chinese else "## Daily itinerary"])
        for day in plan.itinerary:
            lines.extend(
                [
                    f"### 第{day.day}天：{day.title}"
                    if chinese
                    else f"### Day {day.day}: {day.title}"
                ]
            )
            if not day.activities:
                lines.append(
                    "- 本日没有已核验的活动候选，可根据现场情况调整。"
                    if chinese
                    else "- No verified activity candidates for this day; adjust on "
                    "the ground as you prefer."
                )
            for activity in day.activities:
                detail = f": {activity.description}" if activity.description else ""
                source = f" ({activity.source})" if activity.source else ""
                if activity.opening_status == "open" and activity.opening_hours:
                    hours = (
                        f"；营业时间 {activity.opening_hours}"
                        if chinese
                        else f"; open {activity.opening_hours}"
                    )
                elif activity.opening_status == "closed":
                    hours = "；计划日期闭馆，不建议安排" if chinese else "; closed on the planned date, not suggested"
                else:
                    hours = "；营业时间未核验" if chinese else "; opening hours unverified"
                duration = (
                    (
                        f"；约 {activity.estimated_visit_minutes} 分钟"
                        if chinese
                        else f"; about {activity.estimated_visit_minutes} min"
                    )
                    if activity.estimated_visit_minutes is not None
                    else ""
                )
                venue = (
                    {
                        "indoor": "；室内",
                        "outdoor": "；室外",
                        "mixed": "；室内或室外",
                        "unknown": "；场所类型未核验",
                    }[activity.venue_type]
                    if chinese
                    else VENUE_LABELS[activity.venue_type]
                )
                cuisine = (
                    f"；菜系 {activity.cuisine}"
                    if chinese and activity.cuisine
                    else f"; cuisine {activity.cuisine}" if activity.cuisine else ""
                )
                website = (
                    f"；[官网]({activity.website})"
                    if chinese and activity.website
                    else f"; [website]({activity.website})" if activity.website else ""
                )
                route_note = (
                    (
                        "；区域中心点，不是已核验入口，因此未用于路线规划"
                        if chinese
                        else "; area centroid, not a verified entrance, excluded from routing"
                    )
                    if not activity.route_eligible
                    else ""
                )
                lines.append(
                    f"- {activity.name}{detail}{source}{venue}{cuisine}"
                    f"{hours}{duration}{website}{route_note}"
                )
    if plan.food:
        lines.extend(["", "## 餐饮候选" if chinese else "## Dining candidates"])
        for place in plan.food:
            cuisine = (
                f"；菜系 {place.cuisine}"
                if chinese and place.cuisine
                else f"; cuisine {place.cuisine}" if place.cuisine else ""
            )
            venue = (
                {
                    "indoor": "；室内",
                    "outdoor": "；室外",
                    "mixed": "；室内或室外",
                    "unknown": "；场所类型未核验",
                }[place.venue_type]
                if chinese
                else VENUE_LABELS[place.venue_type]
            )
            hours = (
                f"；营业时间 {place.opening_hours}"
                if chinese and place.opening_hours
                else f"; open {place.opening_hours}"
                if place.opening_hours
                else "；营业时间未核验" if chinese else "; opening hours unverified"
            )
            website = (
                f"；[官网]({place.website})"
                if chinese and place.website
                else f"; [website]({place.website})" if place.website else ""
            )
            source = f" ({place.source})" if place.source else ""
            lines.append(f"- {place.name}{source}{cuisine}{venue}{hours}{website}")
    if plan.routes:
        lines.extend(["", "## 路线估算" if chinese else "## Route estimates"])
        for route in plan.routes:
            mode = mode_labels["transit" if route.profile == "transit" else "driving"]
            lines.append(
                (
                    f"- 第{route.day}天：{' → '.join(route.ordered_stops)}；"
                    f"约 {route.distance_meters / 1000:.1f} 公里，"
                    f"{route.duration_seconds / 60:.0f} 分钟，方式：{mode}"
                    if chinese
                    else f"- Day {route.day}: {' → '.join(route.ordered_stops)}; "
                    f"about {route.distance_meters / 1000:.1f} km, "
                    f"{route.duration_seconds / 60:.0f} min by {mode}"
                )
            )
    unavailable = [record for record in plan.tool_status if record.status == "unavailable"]
    if unavailable:
        lines.extend(["", "## 暂不可用的数据" if chinese else "## Unavailable data"])
        lines.extend(
            f"- {record.tool_name} ({record.provider}): "
            f"{record.message or record.error_code or 'no data returned'}"
            for record in unavailable
        )
    if plan.data_sources:
        lines.extend(
            [
                "",
                "## 数据来源" if chinese else "## Data sources",
                "- " + "; ".join(plan.data_sources),
            ]
        )
    if plan.knowledge_sources:
        lines.extend(["", "## 知识库参考" if chinese else "## Knowledge references"])
        lines.extend(
            f"- {item.title} / {item.section} ({item.source}, score {item.score:.2f})"
            for item in plan.knowledge_sources
        )
    if plan.review is not None:
        lines.extend(["", "## 质量评估" if chinese else "## Quality review"])
        lines.append(
            f"- 结论：{plan.review.status}" if chinese else f"- Verdict: {plan.review.status}"
        )
        if plan.review.strengths:
            lines.append(
                ("- 已满足：" if chinese else "- Satisfied: ")
                + "; ".join(plan.review.strengths)
            )
        if plan.review.issues:
            lines.append(
                ("- 问题：" if chinese else "- Concerns: ")
                + "; ".join(plan.review.issues)
            )
        if plan.review.checked_constraints:
            lines.append(
                ("- 已检查：" if chinese else "- Checked: ")
                + "; ".join(plan.review.checked_constraints)
            )
    if plan.notes:
        lines.extend(["", "## 说明" if chinese else "## Notes"])
        lines.extend(f"- {_render_note(note, chinese)}" for note in plan.notes)
    lines.extend(["", notice])
    return "\n".join(lines)
