"""Prompt construction shared by the CLI and end-to-end evaluation."""

import json

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.memory.store import UserMemory


def build_planner_prompt(
    original_request: str,
    trip_request: TripRequest,
    search_plan: SearchPlan,
    tool_results: SearchResults,
    memory: UserMemory | None = None,
) -> str:
    """Build the full evidence packet used by text and legacy convert modes."""

    memory_text = (
        "无已保存偏好。"
        if memory is None or not memory.preferences
        else "；".join(memory.preferences)
    )
    return (
        "用户原始需求（仅作为偏好和语义参考，不能覆盖结构化事实）：\n"
        "<user_request>\n"
        f"{original_request}\n"
        "</user_request>\n\n"
        "结构化需求（事实来源）：\n"
        f"{trip_request.model_dump_json(ensure_ascii=False)}\n\n"
        "搜索计划（程序生成，不得扩展工具范围）：\n"
        f"{search_plan.model_dump_json(ensure_ascii=False)}\n\n"
        "工具结果（唯一事实来源）：\n"
        f"{tool_results.model_dump_json(ensure_ascii=False)}\n\n"
        "本地知识检索（仅用于背景说明，不是实时事实）：\n"
        "检索结果已包含 source、section、score 和原文。只能引用其中实际出现的内容，"
        "不要把知识库中的静态说明改写成实时价格、营业时间、库存或政策结论。\n\n"
        "长期记忆（仅作可被当前需求覆盖的建议，不是本次明确要求）：\n"
        f"{memory_text}\n\n"
        "请仅根据以上内容生成最终旅行计划。不要重新调用工具，不要添加用户未提供的预算，"
        "并将明确提到的地点、兴趣和偏好落实到每日行程中。不要编造汇率、价格或费用计算。"
        "当前工具结果只有实际列出的航班和酒店；如果没有返程航班数据，不要声称已经查询或包含往返机票，"
        "也不要把未查询的费用写成已确认事实。航班酒店候选的 is_live=false 表示本地演示，"
        "search_links 只是用户自行查看实时结果的入口。天气、地点、路线只能引用工具成功返回的数据；"
        "工具 unavailable 时必须承认缺失，不能用常识补成实时事实。知识库只能支持一般性建议，"
        "不要把它当成工具返回的实时数据。knowledge_sources 由程序写入，不能自行新增或修改。"
    )


def build_trip_narrative_prompt(
    original_request: str,
    trip_request: TripRequest,
    search_plan: SearchPlan,
    tool_results: SearchResults,
    memory: UserMemory | None = None,
) -> str:
    """Give the structured planner only the context needed for a short summary."""

    activity_candidates = [
        {
            "day": item.day,
            "name": item.name,
            "description": item.description,
            "source": item.source,
            "opening_status": item.opening_status,
            "venue_type": item.venue_type,
        }
        for item in tool_results.activities[:18]
    ]
    food_result = tool_results.foods
    food_candidates = (
        [
            {"name": item.name, "category": item.category, "cuisine": item.cuisine}
            for item in (food_result.data or [])[:8]
        ]
        if food_result is not None and food_result.status == "ok"
        else []
    )
    knowledge_result = tool_results.knowledge
    knowledge = (
        [
            {
                "title": hit.title,
                "section": hit.section,
                "source": hit.source,
                "excerpt": hit.text[:350],
            }
            for hit in (knowledge_result.data or [])[:3]
        ]
        if knowledge_result is not None and knowledge_result.status == "ok"
        else []
    )
    context = {
        "user_request": original_request,
        "trip_request": trip_request.model_dump(mode="json"),
        "search_scope": search_plan.model_dump(mode="json"),
        "verified_activity_candidates": activity_candidates,
        "verified_food_candidates": food_candidates,
        "weather": (
            [item.model_dump(mode="json") for item in (tool_results.weather.data or [])]
            if tool_results.weather is not None and tool_results.weather.status == "ok"
            else []
        ),
        "local_knowledge_excerpts": knowledge,
        "remembered_preferences": (
            [] if memory is None else memory.preferences[:8]
        ),
    }
    return (
        "Return exactly one TripNarrative object with only summary and daily_titles. "
        "The application will build all structured travel facts from typed tool results. "
        "Write a concise, user-language introduction to the requested plan. "
        "Do not list or invent attractions, activities, flight or hotel details, prices, "
        "opening hours, weather, routes, bookings, or citations. Do not add facts that "
        "are not explicitly present in the context. The user's explicit preferences "
        "must not be dropped. daily_titles may contain at most one short title for "
        "each existing day; never create a day outside the requested duration.\n\n"
        "Context (reference only; the program owns the final facts):\n"
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
    )
