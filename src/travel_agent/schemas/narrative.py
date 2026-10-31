"""Small, model-authored part of a travel plan.

Travel facts stay in typed tool results; the model only writes a short summary.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DayNarrative(BaseModel):
    """Short prose label for one already-programmed travel day."""

    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1, le=14)
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class TripNarrative(BaseModel):
    """Bounded prose that can be safely merged into a program-built plan."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    daily_titles: list[DayNarrative] = Field(default_factory=list, max_length=14)

    @field_validator("summary")
    @classmethod
    def trim_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary must not be blank")
        return value
