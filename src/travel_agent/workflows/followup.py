"""Follow-up turns on a plan the workflow already produced.

The application keeps owning the facts here. A follow-up may re-run the
planner or answer a question, but it reads the same typed ``SearchResults``
that produced the original plan, so no branch in this module can introduce a
flight, price, coordinate, or opening hour that no tool returned.
"""

import json
from typing import Any

from agent_framework import Agent

from travel_agent.schemas.followup import FollowUpIntent
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.workflows.trip_planning import TraceObserver, plan_trip


PromptObserver = Any


# Deterministic exits. A user who types these should not cost a model call,
# and "quit" must never be mistaken for a narrative instruction.
END_PHRASES = {
    "退出", "结束", "不用了", "没有了", "好了", "可以了", "谢谢", "再见",
    "exit", "quit", "no", "nope", "done", "thanks", "thank you", "bye",
}


def looks_like_end(text: str) -> bool:
    """Recognize only unambiguous exits; anything else goes to the model."""

    cleaned = text.strip().strip("。.!！~ ").casefold()
    return cleaned in END_PHRASES


async def classify_follow_up(
    agent: Agent,
    text: str,
    *,
    on_prompt: PromptObserver | None = None,
    on_trace: TraceObserver | None = None,
) -> FollowUpIntent:
    """Route one follow-up turn into a typed action.

    A classification failure degrades to ``modify_request``: re-running the
    workflow is slower but always correct, while guessing ``refine_narrative``
    would silently ignore a real change to dates or destination.
    """

    prompt = (
        "用户已经收到一份旅行计划，现在说了下面这句话。请判断应该如何处理。\n\n"
        f"用户的话：{text}\n\n"
        "action 的含义：\n"
        "- modify_request：旅行事实变了，需要重新规划。例如改天数、改日期、"
        "改城市、改预算、增加或去掉机票/酒店/餐厅/攻略。\n"
        "- refine_narrative：事实没变，只是想换一种表达。例如写详细一点、"
        "改成英文、换个语气、把某一天讲清楚。\n"
        "- ask_about_plan：在问已有计划里的问题，不需要改计划。例如"
        "“为什么第二天没有安排东京塔”“这家店几点开门”。\n"
        "- end：用户表示结束。\n"
        "- out_of_scope：要求订票、付款、改签，或与这次旅行无关。\n"
    )
    try:
        response = await plan_trip(
            agent,
            prompt,
            options={"response_format": FollowUpIntent},
            stage="followup.classify",
            on_prompt=on_prompt,
            on_trace=on_trace,
        )
        value = getattr(response, "value", None)
        if value is not None:
            return FollowUpIntent.model_validate(value)
        return FollowUpIntent.model_validate_json(response.text)
    except Exception:
        return FollowUpIntent(
            action="modify_request",
            reason="follow-up classification unavailable; re-planning is the safe default",
        )


def build_plan_question_prompt(
    question: str,
    request: TripRequest,
    search_results: SearchResults,
    plan: TripPlan,
) -> str:
    """Give the model only the evidence that already produced this plan."""

    itinerary = [
        {
            "day": day.day,
            "title": day.title,
            "activities": [
                {
                    "name": item.name,
                    "opening_hours": item.opening_hours,
                    "opening_status": item.opening_status,
                    "venue_type": item.venue_type,
                    "notes": item.notes,
                    "source": item.source,
                }
                for item in day.activities
            ],
        }
        for day in plan.itinerary
    ]
    unscheduled = [
        {
            "name": activity.name,
            "day": activity.day,
            "opening_status": activity.opening_status,
            "venue_type": activity.venue_type,
        }
        for activity in search_results.activities
    ]
    tool_status = [
        {
            "tool": record.tool_name,
            "stage": record.stage,
            "status": record.status,
            "provider": record.provider,
            "message": record.message,
        }
        for record in search_results.tool_calls
    ]
    return (
        "用户在问一个关于已生成计划的问题。只能根据下面的事实回答。\n\n"
        f"用户的问题：{question}\n\n"
        f"目的地：{plan.destination}，天数：{plan.days}，状态：{plan.status}\n"
        f"用户偏好：{json.dumps(request.preferences, ensure_ascii=False)}\n"
        f"已排入行程：{json.dumps(itinerary, ensure_ascii=False)}\n"
        f"工具返回的全部候选地点：{json.dumps(unscheduled, ensure_ascii=False)}\n"
        f"工具状态：{json.dumps(tool_status, ensure_ascii=False)}\n"
        f"计划备注：{json.dumps(plan.notes, ensure_ascii=False)}\n\n"
        "要求：用中文回答，三句话以内。只能引用上面出现过的事实；"
        "上面没有的信息就直接说明没有查到，不要补充常识或猜测。"
        "如果某个地点因为营业时间、天气排序或候选不足而没有排进行程，请说明原因。"
    )


async def answer_plan_question(
    agent: Agent,
    question: str,
    request: TripRequest,
    search_results: SearchResults,
    plan: TripPlan,
    *,
    on_prompt: PromptObserver | None = None,
    on_trace: TraceObserver | None = None,
) -> str:
    """Answer a read-only question without re-running tools or re-planning."""

    prompt = build_plan_question_prompt(question, request, search_results, plan)
    try:
        response = await plan_trip(
            agent,
            prompt,
            stage="followup.answer",
            on_prompt=on_prompt,
            on_trace=on_trace,
        )
    except Exception as error:
        return f"无法回答这个问题（{type(error).__name__}）。计划本身没有改变。"
    return (getattr(response, "text", "") or "").strip() or "没有可引用的信息。"
