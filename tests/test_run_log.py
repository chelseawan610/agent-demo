import json
from datetime import datetime, timezone

from travel_agent.app.run_log import write_run_record
from travel_agent.app.trace import TokenPricing, TraceRecorder
from travel_agent.schemas.evidence import ToolCallRecord
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.request import TripRequest


def test_run_log_is_jsonl_and_does_not_store_input_by_default(tmp_path):
    path = tmp_path / "runs.jsonl"
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])

    write_run_record(
        path,
        run_id="run-1",
        mode="offline",
        model=None,
        status="complete",
        request_text="secret user text",
        request=request,
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["run_id"] == "run-1"
    assert "input" not in row
    assert "secret user text" not in path.read_text(encoding="utf-8")


def test_trace_recorder_keeps_usage_and_redacts_model_input(tmp_path):
    class Response:
        usage_details = {
            "input_token_count": 12,
            "output_token_count": 8,
            "total_token_count": 20,
        }

    recorder = TraceRecorder("test-model")
    recorder.record_llm(
        "intake",
        datetime.now(timezone.utc),
        12.5,
        Response(),
        "ok",
        None,
    )
    path = tmp_path / "trace.jsonl"
    write_run_record(
        path,
        run_id="trace-1",
        mode="auto",
        model="test-model",
        status="complete",
        trajectory=recorder.events,
    )

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    event = row["trajectory"][0]
    assert event["input_tokens"] == 12
    assert event["output_tokens"] == 8
    assert "prompt" not in event


def test_trace_recorder_builds_ids_cost_and_latency_metrics():
    class Response:
        usage_details = {
            "input_token_count": 1_000,
            "output_token_count": 500,
            "total_token_count": 1_500,
        }

    recorder = TraceRecorder(
        "test-model",
        run_id="run-1",
        trace_id="trace-1",
        pricing_by_model={
            "test-model": TokenPricing(
                input_usd_per_million=1.0,
                output_usd_per_million=2.0,
            )
        },
    )
    recorder.record_llm(
        "intake",
        datetime.now(timezone.utc),
        12.5,
        Response(),
        "ok",
        None,
    )
    results = SearchResults(
        tool_calls=[
            ToolCallRecord(
                run_id="run-1",
                trace_id="trace-1",
                tool_name="search_flights",
                status="ok",
                elapsed_ms=5,
                provider="fixture",
            )
        ]
    )

    trajectory = recorder.build_trajectory(results)
    metrics = recorder.build_metrics(results)

    assert {event.run_id for event in trajectory} == {"run-1"}
    assert {event.trace_id for event in trajectory} == {"trace-1"}
    assert metrics["llm_calls"] == 1
    assert metrics["tool_calls"] == 1
    assert metrics["total_tokens"] == 1_500
    assert metrics["estimated_cost_usd"] == 0.002
    assert metrics["p95_latency_ms"] == 12.5
    assert metrics["stages"]["intake"]["success_rate"] == 1.0
    assert metrics["providers"]["configured LLM"]["calls"] == 1


def test_run_log_keeps_typed_evidence_and_final_plan(tmp_path):
    path = tmp_path / "full-run.jsonl"
    results = SearchResults(
        tool_calls=[
            ToolCallRecord(
                tool_name="search_flights",
                status="ok",
                elapsed_ms=1,
                provider="fixture",
            )
        ]
    )
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])

    write_run_record(
        path,
        run_id="run-1",
        trace_id="trace-1",
        mode="auto",
        model="test-model",
        status="complete",
        request=request,
        tool_results=results,
        final_plan=request,
    )

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["trace_id"] == "trace-1"
    assert row["search_results"]["tool_calls"][0]["tool_name"] == "search_flights"
    assert row["final_plan"]["destination"] == "Tokyo"
