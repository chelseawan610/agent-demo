"""Run offline workflow regression cases without a model or network."""

import json

from evals.workflow import run_all


def main() -> int:
    report = run_all()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
