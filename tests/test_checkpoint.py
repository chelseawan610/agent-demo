from travel_agent.schemas.checkpoint import WorkflowCheckpoint
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.workflows.checkpoint import CheckpointStore


def test_checkpoint_round_trip_preserves_typed_workflow_state(tmp_path):
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=3,
    )
    checkpoint = WorkflowCheckpoint.create(
        "run-1",
        "complete",
        request,
        original_request="Shanghai to Tokyo on 2099-10-01 for 3 days",
        search_plan=SearchPlan(),
        search_results=SearchResults(),
        final_plan=TripPlan(destination="Tokyo", days=3, summary="demo"),
    )
    store = CheckpointStore(tmp_path / "checkpoint.json")

    store.save(checkpoint)
    loaded = store.load()

    assert loaded.phase == "complete"
    assert loaded.original_request == "Shanghai to Tokyo on 2099-10-01 for 3 days"
    assert loaded.request.start_date.isoformat() == "2099-10-01"
    assert loaded.final_plan is not None
    assert loaded.final_plan.destination == "Tokyo"


def test_checkpoint_store_returns_none_when_optional_file_is_absent(tmp_path):
    store = CheckpointStore(tmp_path / "missing.json")

    assert store.load_or_none() is None
