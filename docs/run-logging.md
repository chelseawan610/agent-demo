# Run logging and debugging

This project separates terminal output from a persistent JSONL run log. The
terminal is for a person; JSONL is evidence for debugging and evaluation.

## Save one normal model run

    PYTHONPATH=src uv run python -m travel_agent.app.cli \
      --output json \
      --debug \
      --log-file .local/runs.jsonl \
      --log-input \
      --log-content

Every completed or failed run appends one line to .local/runs.jsonl. The
directory is gitignored because it may contain private user text. Without
--log-file, nothing is persisted. --log-input stores the raw user message;
--log-content stores at most 4,000 characters of each model response. Prompts,
hidden reasoning, and API keys are never written.

For routine local evidence with less personal data, omit both sensitive flags:

    PYTHONPATH=src uv run python -m travel_agent.app.cli \
      --output markdown \
      --log-file .local/runs.jsonl

## Reproducible free example

The checked-in request uses the typed offline path, so it spends no model
tokens. It still exercises SearchPlan, RAG, public travel tools, partial
degradation, deterministic assembly, rendering, and run logging.

    PYTHONPATH=src uv run python -m travel_agent.app.cli \
      --offline \
      --request-json @examples/logging/request.json \
      --output json \
      --debug \
      --log-file .local/example-run.jsonl

JSONL is append-only. Running this command again adds another row instead of
overwriting previous evidence.

The repository includes an abridged, sanitized snapshot derived from a real
completed model run at
[example-run.abridged.jsonl](../examples/logging/example-run.abridged.jsonl).
It keeps the important handoff and failure fields but intentionally omits the
large evidence arrays. Public API results can change, so rerunning the command
need not produce identical places or latency. The schema and authority
boundaries should remain stable.

## What one JSONL row keeps

| Field | Meaning | First place to inspect when |
| --- | --- | --- |
| run_id, trace_id, created_at | Identity and UTC timestamp | Correlating terminal, tool, and model events |
| mode, model, fallback flags | Execution configuration | A result differs across model or mode |
| request | Validated TripRequest | City, date, service, language, or preference was misunderstood |
| search_plan | Deterministic tool plan | Correct intent generated the wrong queries |
| tool_calls | One status record per tool | A provider timed out, was skipped, or returned no match |
| search_results | Typed evidence returned by tools | Final output lacks weather, places, routes, or inventory |
| final_plan | Full validated TripPlan | Checking the machine-readable final answer |
| trajectory | Time-ordered LLM and tool events | Reconstructing where time and failures occurred |
| metrics | Tokens, latency, success rates, duplicate calls | Comparing runs or models |
| failure_attribution | Normalized category and workflow layer | Locating the owner of a partial or error result |
| input | Raw text, only with --log-input | Verifying whether Intake preserved the original wording |

final_plan is the final JSON object. --output json only prints that object to
the terminal; --log-file persists the wider execution evidence. Both are
produced from the same validated Pydantic objects with model_dump(mode=json),
which converts dates and nested models into ordinary JSON-compatible strings,
dictionaries, and lists.

## Fast bug localization

    jq '.request' .local/runs.jsonl
    jq '.search_plan' .local/runs.jsonl
    jq '.tool_calls[] | select(.status != "ok")' .local/runs.jsonl
    jq '.trajectory[] | select(.status != "ok")' .local/runs.jsonl
    jq '.final_plan' .local/runs.jsonl
    jq '.metrics' .local/runs.jsonl
    jq '.failure_attribution' .local/runs.jsonl

For a file with several runs, inspect the last run with tail -n 1:

    tail -n 1 .local/runs.jsonl | jq '.final_plan'
    tail -n 1 .local/runs.jsonl | jq '.failure_attribution'

The debugging rule is:

1. Wrong request: Intake or evidence validation problem.
2. Correct request, wrong search_plan: deterministic planning problem.
3. Correct plan, failed tool_calls: adapter or provider problem.
4. Correct evidence, wrong final_plan: assembly or invariant problem.
5. Correct final_plan, wrong Markdown: renderer problem.

Aggregate several runs without reading them one by one:

    PYTHONPATH=src uv run python scripts/analyze_runs.py .local/runs.jsonl
    PYTHONPATH=src uv run python scripts/show_run_errors.py .local/runs.jsonl
