from evals.metrics import summarize_rows


def test_eval_metrics_include_failure_rate_and_p95():
    report = summarize_rows(
        [
            {"passed": True, "elapsed_ms": 10, "failures": []},
            {"passed": False, "elapsed_ms": 20, "failures": ["services: wrong"]},
            {"passed": True, "elapsed_ms": 30, "failures": []},
        ]
    )

    assert report["pass_rate"] == 0.666667
    assert report["failure_rate"] == 0.333333
    assert report["p95_elapsed_ms"] == 30
    assert report["failure_categories"] == {"services": 1}
