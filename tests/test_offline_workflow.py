from evals.workflow import run_all


def test_offline_workflow_regression_cases_pass():
    report = run_all()

    assert report["passed"] == report["total"]
