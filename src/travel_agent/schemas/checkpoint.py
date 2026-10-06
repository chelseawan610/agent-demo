from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan


class WorkflowCheckpoint(BaseModel):
    """Serializable state for the application-owned travel workflow."""

    run_id: str
    phase: Literal["intake", "searched", "complete"]
    saved_at: datetime
    request: TripRequest
    original_request: str | None = None
    search_plan: SearchPlan | None = None
    search_results: SearchResults | None = None
    final_plan: TripPlan | None = None

    @classmethod
    def create(cls, run_id: str, phase: str, request: TripRequest, **values):
        return cls(
            run_id=run_id,
            phase=phase,
            saved_at=datetime.now(timezone.utc),
            request=request,
            **values,
        )
