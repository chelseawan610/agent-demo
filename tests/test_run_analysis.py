from scripts.analyze_runs import summarize_runs


def test_summarize_runs_aggregates_failure_layers_and_cost():
    report = summarize_runs(
        [
            {
                "status": "complete",
                "metrics": {
                    "tool_calls": 3,
                    "duplicate_tool_calls": 1,
                    "p95_latency_ms": 100,
                    "estimated_cost_usd": 0.01,
                    "failure_categories": {"provider_configuration": 1},
                    "failure_by_stage": {"routing": {"failures": 1}},
                },
                "failure_attribution": {
                    "categories": {"provider_configuration": 1}
                },
            },
            {
                "status": "error",
                "metrics": {
                    "tool_calls": 1,
                    "p95_latency_ms": 200,
                    "failure_categories": {"provider_transient": 1},
                    "failure_by_stage": {"weather": {"failures": 1}},
                },
            },
        ]
    )

    assert report["runs"] == 2
    assert report["success_rate"] == 0.5
    assert report["failure_categories"] == {
        "provider_configuration": 1,
        "provider_transient": 1,
    }
    assert report["failure_stages"] == {"routing": 1, "weather": 1}
    assert report["average_tool_calls"] == 2.0
    assert report["duplicate_tool_calls"] == 1
    assert report["estimated_cost_usd"] == 0.01
