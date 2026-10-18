"""Aggregate persisted JSONL runs into a small failure-attribution report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


def read_runs(path: str | Path) -> list[dict[str, Any]]:
    """Read valid JSONL rows and ignore blank lines."""

    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def summarize_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize reliability and trace signals without judging prose quality."""

    categories: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    statuses = Counter(str(row.get("status", "unknown")) for row in rows)
    tool_calls = 0
    duplicate_calls = 0
    p95_values: list[float] = []
    costs: list[float] = []

    for row in rows:
        metrics = row.get("metrics") or {}
        tool_calls += int(metrics.get("tool_calls") or 0)
        duplicate_calls += int(metrics.get("duplicate_tool_calls") or 0)
        if isinstance(metrics.get("p95_latency_ms"), (int, float)):
            p95_values.append(float(metrics["p95_latency_ms"]))
        if isinstance(metrics.get("estimated_cost_usd"), (int, float)):
            costs.append(float(metrics["estimated_cost_usd"]))

        attribution = row.get("failure_attribution") or {}
        row_categories = attribution.get("categories") or metrics.get(
            "failure_categories", {}
        )
        categories.update(
            {str(key): int(value) for key, value in row_categories.items()}
        )
        row_stages = metrics.get("failure_by_stage") or {}
        stages.update(
            {
                str(stage): int(value.get("failures", 0))
                for stage, value in row_stages.items()
                if isinstance(value, dict)
            }
        )

    return {
        "runs": len(rows),
        "statuses": dict(statuses),
        "success_rate": round(statuses.get("complete", 0) / len(rows), 4)
        if rows
        else 0.0,
        "failure_categories": dict(categories),
        "failure_stages": dict(stages),
        "tool_calls": tool_calls,
        "average_tool_calls": round(tool_calls / len(rows), 2) if rows else 0.0,
        "duplicate_tool_calls": duplicate_calls,
        "average_run_p95_latency_ms": round(mean(p95_values), 2)
        if p95_values
        else None,
        "estimated_cost_usd": round(sum(costs), 8) if costs else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate Agent JSONL run logs")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    report = {
        str(path): summarize_runs(read_runs(path))
        for path in args.paths
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
