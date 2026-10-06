from travel_agent.schemas.trip import TripPlan
from travel_agent.schemas.request import TripRequest


DEMO_NOTICE = (
    "> 注意：航班和酒店候选为本地演示数据，不代表实时库存、价格或已完成预订；"
    "天气、地点和路线来自相应公共数据源，出行前仍需核实。"
)


def render_trip_request(request: TripRequest) -> str:
    """Render missing intake fields before any travel tool is called."""

    lines = [
        "# 需要补充旅行信息",
        "",
        f"请求范围：{', '.join(request.requested_services)}",
        "",
        "在调用旅行工具前，请补充：",
    ]
    lines.extend(f"- {field}" for field in request.missing_fields)
    lines.extend(
        [
            "",
            "可选信息（不提供也可以）：旅行天数/住宿晚数、预算、返程日期、交通方式和兴趣偏好；"
            "系统会使用合理默认值或留空。",
            "",
            DEMO_NOTICE,
        ]
    )
    return "\n".join(lines)


def format_plan(text: str) -> str:
    """Wrap the model response in a consistent, user-facing Markdown format."""

    body = text.strip()
    if not body:
        body = "模型没有生成旅行计划。"

    if DEMO_NOTICE not in body:
        body = f"{body}\n\n{DEMO_NOTICE}"

    return f"# 旅行规划结果\n\n{body}"


def render_trip_plan(plan: TripPlan) -> str:
    """Render one validated TripPlan as user-facing Markdown."""

    if plan.status == "needs_clarification":
        lines = ["# 需要补充旅行信息", "", plan.summary or "请补充以下信息："]
        missing = plan.missing_information or plan.notes
        lines.extend(["", "## 需要补充"])
        lines.extend(f"- {item}" for item in missing)
        lines.extend(["", DEMO_NOTICE])
        return "\n".join(lines)

    lines = [f"# {plan.destination} {plan.days}日旅行计划", "", plan.summary]
    if plan.budget is not None:
        if plan.budget.amount is not None:
            lines.extend(
                [
                    "",
                    "## 预算",
                    f"{plan.budget.amount:g} {plan.budget.currency}"
                    f"（{plan.budget.period or 'total'}）",
                ]
            )
        elif plan.budget.level is not None:
            lines.extend(["", "## 预算", f"预算档位：{plan.budget.level}"])
    if plan.flights:
        lines.extend(["", "## 航班选项"])
        lines.extend(
            f"- {flight.airline}：{flight.origin} → {flight.destination}，"
            f"{flight.departure}-{flight.arrival}，{flight.price}"
            for flight in plan.flights
        )
    if plan.hotels:
        lines.extend(["", "## 酒店选项"])
        lines.extend(
            f"- {hotel.name}（{hotel.area}）：{hotel.nights}晚，"
            f"{hotel.price_per_night}/晚，评分 {hotel.rating}"
            for hotel in plan.hotels
        )
    if plan.search_links:
        lines.extend(["", "## 实时搜索入口"])
        for link in plan.search_links:
            label = "航班" if link.service == "flights" else "酒店"
            lines.append(f"- [{label}：{link.provider}]({link.url}) — {link.disclaimer}")
    if plan.transport_modes:
        mode_labels = {
            "driving": "驾车",
            "transit": "公共交通",
        }
        lines.extend(
            [
                "",
                "## 交通方式",
                "已请求：" + " + ".join(mode_labels[mode] for mode in plan.transport_modes),
            ]
        )
    if plan.weather:
        lines.extend(["", "## 天气参考"])
        for weather in plan.weather:
            rain = (
                f"，最高降水概率 {weather.precipitation_probability_max}%"
                if weather.precipitation_probability_max is not None
                else ""
            )
            lines.append(
                f"- {weather.date}：{weather.temperature_min_c:g}–"
                f"{weather.temperature_max_c:g}°C{rain}"
            )
    if plan.itinerary:
        lines.extend(["", "## 每日行程"])
        for day in plan.itinerary:
            lines.extend([f"### Day {day.day}：{day.title}"])
            if not day.activities:
                lines.append("- 暂无已核验的真实活动候选，可根据当天兴趣现场调整。")
            for activity in day.activities:
                detail = f"：{activity.description}" if activity.description else ""
                source = f"（{activity.source}）" if activity.source else ""
                if activity.opening_status == "open" and activity.opening_hours:
                    hours = f"；开放时间 {activity.opening_hours}"
                elif activity.opening_status == "closed":
                    hours = "；计划日期关闭，未建议进入"
                else:
                    hours = "；开放时间未核验"
                duration = (
                    f"；建议停留约 {activity.estimated_visit_minutes} 分钟"
                    if activity.estimated_visit_minutes is not None
                    else ""
                )
                venue = {
                    "indoor": "；室内",
                    "outdoor": "；室外",
                    "mixed": "；室内外皆可",
                    "unknown": "；室内外未核验",
                }[activity.venue_type]
                cuisine = f"；菜系 {activity.cuisine}" if activity.cuisine else ""
                website = (
                    f"；[官网]({activity.website})"
                    if activity.website
                    else ""
                )
                lines.append(
                    f"- {activity.name}{detail}{source}{venue}{cuisine}"
                    f"{hours}{duration}{website}"
                )
    if plan.food:
        lines.extend(["", "## 餐饮参考"])
        for place in plan.food:
            cuisine = f"；菜系 {place.cuisine}" if place.cuisine else ""
            venue = {
                "indoor": "；室内",
                "outdoor": "；室外",
                "mixed": "；室内外皆可",
                "unknown": "；室内外未核验",
            }[place.venue_type]
            hours = (
                f"；营业时间 {place.opening_hours}"
                if place.opening_hours
                else "；营业时间未核验"
            )
            website = f"；[官网]({place.website})" if place.website else ""
            source = f"（{place.source}）" if place.source else ""
            lines.append(f"- {place.name}{source}{cuisine}{venue}{hours}{website}")
    if plan.routes:
        lines.extend(["", "## 路线估算"])
        for route in plan.routes:
            mode = "公共交通" if route.profile == "transit" else "驾车"
            lines.append(
                f"- Day {route.day}：{' → '.join(route.ordered_stops)}；"
                f"约 {route.distance_meters / 1000:.1f} km，"
                f"{mode}估算 {route.duration_seconds / 60:.0f} 分钟"
            )
    unavailable = [record for record in plan.tool_status if record.status == "unavailable"]
    if unavailable:
        lines.extend(["", "## 暂不可用的数据"])
        lines.extend(
            f"- {record.tool_name}（{record.provider}）："
            f"{record.message or record.error_code or '未返回数据'}"
            for record in unavailable
        )
    if plan.data_sources:
        lines.extend(["", "## 数据来源", "- " + "；".join(plan.data_sources)])
    if plan.knowledge_sources:
        lines.extend(["", "## 知识参考"])
        lines.extend(
            f"- {item.title} / {item.section}（{item.source}，检索分数 {item.score:.2f}）"
            for item in plan.knowledge_sources
        )
    if plan.review is not None:
        lines.extend(["", "## 质量复核"])
        lines.append(f"- 结果：{plan.review.status}")
        if plan.review.strengths:
            lines.append("- 已满足：" + "；".join(plan.review.strengths))
        if plan.review.issues:
            lines.append("- 待关注：" + "；".join(plan.review.issues))
        if plan.review.checked_constraints:
            lines.append("- 已检查：" + "；".join(plan.review.checked_constraints))
    if plan.notes:
        lines.extend(["", "## 注意事项"])
        lines.extend(f"- {note}" for note in plan.notes)
    lines.extend(["", DEMO_NOTICE])
    return "\n".join(lines)
