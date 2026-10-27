"""Typed routing for one user turn that arrives after a plan was delivered."""

from typing import Literal

from pydantic import BaseModel, Field


FollowUpAction = Literal[
    "modify_request",
    "refine_narrative",
    "ask_about_plan",
    "end",
    "out_of_scope",
]


class FollowUpIntent(BaseModel):
    """How to handle one follow-up turn.

    The branches exist because their cost differs by an order of magnitude.
    ``ask_about_plan`` and ``refine_narrative`` reuse the evidence already in
    ``SearchResults`` and make no network call at all, while
    ``modify_request`` may have to re-run external providers. Classifying
    first keeps "write day two in more detail" from re-querying every tool.
    """

    action: FollowUpAction
    reason: str = Field(
        default="",
        description="One short sentence explaining the choice, for the trace.",
    )
