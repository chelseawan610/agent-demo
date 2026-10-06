"""Small deterministic metrics shared by the offline and model-backed evals."""

from collections import Counter
from math import ceil
from typing import Any


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return health metrics without asking a second LLM to judge the run."""

    passed = sum(bool(row.get("passed")) for row in rows)
    elapsed = sorted(
        float(row["elapsed_ms"])
        for row in rows
        if isinstance(row.get("elapsed_ms"), (int, float))
    )
    failure_categories = Counter(
        str(failure).split(":", 1)[0]
        for row in rows
        for failure in row.get("failures", [])
    )
    p95 = elapsed[max(ceil(len(elapsed) * 0.95) - 1, 0)] if elapsed else None
    pass_rate = passed / len(rows) if rows else 0.0
    return {
        "passed": passed,
        "total": len(rows),
        "pass_rate": round(pass_rate, 6),
        "failure_rate": round(1 - pass_rate, 6) if rows else 0.0,
        "p95_elapsed_ms": p95,
        "failure_categories": dict(failure_categories),
    }
