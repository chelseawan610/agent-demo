#!/usr/bin/env python3
"""Drive the real CLI multi-turn loop with a scripted model.

This exists to verify the conversation plumbing without a model key or
network: the CLI, intake merging, SearchPlan reuse, follow-up routing, and
rendering are all the real implementations. Only the model responses are
canned, so what this proves is that every branch is wired correctly --- not
that a real model classifies a turn the way the script assumes.

    PYTHONPATH=src .venv/bin/python scripts/replay_multi_turn.py
"""

from __future__ import annotations

import asyncio
import builtins
import sys

from travel_agent.app import cli
from travel_agent.schemas.followup import FollowUpIntent
from travel_agent.schemas.narrative import DayNarrative, TripNarrative
from travel_agent.schemas.request import TripRequest


import os

_EN = os.getenv("REPLAY_LANG", "zh") == "en"
USER_TURNS = [
    "I want to go to the Maldives for 3 days on 2026-10-29, I must visit "
    "Maafushi Island, my budget is only 30000 CNY. Plan the whole trip.",
    "from Shanghai",
    "make it 4 days",
    "write day two in more detail",
    "why is Maafushi Island not scheduled?",
    "",
] if _EN else [
    "我想要2026-10-29去马尔代夫玩3天，一定要去马富士岛，但我预算只有3万你帮我规划整个行程。",
    "从上海出发",          # answers the clarification question
    "改成4天",
    "第二天写详细一点",
    "为什么没安排马富士岛？",
    "",                     # end
]

MAAFUSHI = {
    "label": "马富士岛",
    "search_query": "Maafushi Island Maldives",
    "kind": "place",
    "place_name": "Maafushi",
    "place_type": "other",
}
BUDGET = {"amount": 30000, "currency": "CNY", "period": "total"}

# What a correct intake would return for each extraction call, in order.
INTAKE_SCRIPT = [
    # Turn 1: everything except the origin, so the CLI must ask for it.
    TripRequest(
        requested_services=["flights", "hotels", "activities", "guide"],
        # The user never said where they are departing from. This is a model
        # that fills it in anyway -- the case the origin guard must catch.
        origin=os.getenv("REPLAY_FAKE_ORIGIN") or None,
        flight_type=os.getenv("REPLAY_FAKE_FLIGHT_TYPE") or None,
        destination="Maldives",
        start_date="2026-10-29",
        days=3,
        budget=BUDGET,
        preferences=["马富士岛"],
        preference_details=[MAAFUSHI],
        stated_fields=["destination", "start_date", "days", "budget", "preferences"],
    ),
    # Clarification round: the user supplies the origin only.
    TripRequest(
        requested_services=["flights", "hotels", "activities", "guide"],
        origin="Shanghai",
        destination="Maldives",
        start_date="2026-10-29",
        days=3,
        budget=BUDGET,
        preferences=["马富士岛"],
        preference_details=[MAAFUSHI],
        stated_fields=["origin"],
    ),
    # Follow-up "改成4天": only the trip length is restated.
    TripRequest(
        requested_services=["flights", "hotels", "activities", "guide"],
        origin="Shanghai",
        destination="Maldives",
        start_date="2026-10-29",
        days=4,
        budget=BUDGET,
        preferences=["马富士岛"],
        preference_details=[MAAFUSHI],
        stated_fields=["days"],
    ),
]

ROUTER_SCRIPT = [
    FollowUpIntent(action="modify_request", reason="trip length changed"),
    FollowUpIntent(action="refine_narrative", reason="only presentation"),
    FollowUpIntent(action="ask_about_plan", reason="question about the plan"),
]


class FakeResponse:
    def __init__(self, value=None, text=""):
        self.value = value
        self.text = text
        self.usage_details = None


class ScriptedAgent:
    """Return the next canned answer for one agent role."""

    def __init__(self, name, script, *, text=None):
        self.name = name
        self.script = list(script)
        self.text = text
        self.calls = 0

    async def run(self, _prompt, **_kwargs):
        self.calls += 1
        if self.text is not None:
            return FakeResponse(text=self.text)
        if not self.script:
            raise AssertionError(f"{self.name}: no scripted response left")
        return FakeResponse(value=self.script.pop(0))


def install(monkey: dict) -> None:
    for name, value in monkey.items():
        setattr(cli, name, value)


async def main() -> int:
    intake = ScriptedAgent("intake", INTAKE_SCRIPT)
    router = ScriptedAgent("router", ROUTER_SCRIPT)
    planner = ScriptedAgent(
        "planner",
        [
            TripNarrative(
                summary="马尔代夫 3 日行程：以马富士岛为中心的海岛安排。",
                daily_titles=[DayNarrative(day=d, title=f"第 {d} 天") for d in (1, 2, 3)],
            ),
            TripNarrative(
                summary="马尔代夫 4 日行程：延长一天后重新分配的海岛安排。",
                daily_titles=[DayNarrative(day=d, title=f"第 {d} 天") for d in (1, 2, 3, 4)],
            ),
            TripNarrative(
                summary="马尔代夫 4 日行程：第二天的安排已写得更详细。",
                daily_titles=[DayNarrative(day=d, title=f"第 {d} 天") for d in (1, 2, 3, 4)],
            ),
        ],
    )
    answerer = ScriptedAgent(
        "answerer",
        [],
        text="马富士岛没有排进行程，因为地点解析工具不可用，没有返回已核验的坐标。",
    )

    # The planner agent is reused for plan questions, so route by stage.
    class PlannerOrAnswerer:
        async def run(self, prompt, **kwargs):
            if "用户在问一个关于已生成计划的问题" in prompt:
                return await answerer.run(prompt, **kwargs)
            return await planner.run(prompt, **kwargs)

    install(
        {
            "create_intake_agent": lambda model=None: intake,
            "create_travel_agent": lambda model=None: PlannerOrAnswerer(),
            "create_follow_up_router": lambda model=None: router,
            "create_trip_formatter": lambda model=None: None,
            "create_trip_reviewer": lambda model=None: None,
        }
    )

    turns = iter(USER_TURNS)

    def fake_input(prompt=""):
        try:
            value = next(turns)
        except StopIteration:
            value = ""
        print(f"{prompt}{value}")
        return value

    builtins.input = fake_input
    # Simulate a pipe, exactly as scripts/demo_multi_turn.py creates one.
    sys.stdin.isatty = lambda: False
    sys.argv = ["cli", "--output", "markdown", "--no-rag", "--follow-up"]

    code = await cli.main()
    print(f"\n[exit={code}] intake_calls={intake.calls} "
          f"router_calls={router.calls} planner_calls={planner.calls} "
          f"answer_calls={answerer.calls}")
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
