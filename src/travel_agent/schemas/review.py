from typing import Literal

from pydantic import BaseModel, Field


class ReviewResult(BaseModel):
    """A read-only quality report; it cannot change the TripPlan facts."""

    status: Literal["pass", "needs_revision"]
    issues: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    checked_constraints: list[str] = Field(default_factory=list)
