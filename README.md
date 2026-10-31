# Travel Planning Agent

A bilingual travel-planning agent built with Microsoft Agent Framework (MAF).
The model understands the request and writes a small narrative; a typed Python
workflow decides which tools run, validates the result, and keeps external
facts authoritative.

This is a portfolio and teaching project. It is intentionally local, auditable,
and easy to extend. It does not book flights, reserve hotels, or process
payments.

## Highlights

- **Typed planning:** `TripRequest -> SearchPlan -> SearchResults -> TripPlan`.
- **Controlled tool use:** tool calls come from a deterministic `SearchPlan`,
  not from an unrestricted model loop.
- **Evidence-first output:** dates, prices, providers, weather, places, routes,
  and activity days come from typed tool results, not planner prose.
- **Failure-aware execution:** timeouts, rate limits, no-match results, and
  unconfigured adapters are preserved as explicit statuses.
- **Bilingual UX:** Chinese and English use the same workflow; user-facing
  Markdown follows the input language and JSON field names stay in English.
- **Auditable evaluation:** deterministic invariants, model evals, fixture E2E
  tests, live API smoke tests, optional review, and JSONL traces.

## Quickstart

### Requirements

- Python 3.11--3.13
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI-compatible LLM endpoint and API key

### Install

```bash
cd /Users/chelsea/Desktop/cs336/agent-demo
cp .env.example .env
```

Edit `.env` and set at least `LLM_API_KEY`. The example configuration uses
OpenRouter and `deepseek/deepseek-v4.1-flash`:

```dotenv
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=your_key_here
LLM_MODEL=deepseek/deepseek-v4.1-flash
LLM_REASONING_EFFORT=none
```

Install the project:

```bash
uv sync --extra dev
```

The travel-data services used by the default workflow do not require API keys.
They are public best-effort services and may be slow or rate-limited.

### Run

```bash
# Human-readable Markdown output
PYTHONPATH=src uv run python -m travel_agent.app.cli

# Machine-readable validated TripPlan
PYTHONPATH=src uv run python -m travel_agent.app.cli --output json

# Show request, SearchPlan, tool records, and workflow checks
PYTHONPATH=src uv run python -m travel_agent.app.cli --debug --output json
```

Example input:

```text
I am leaving from Shanghai for Tokyo on 2026-10-01 for three days.
My total budget is CNY 10,000. I want to visit Tokyo Tower and I like anime.
```

The same workflow accepts Chinese input. The CLI asks follow-up questions when
required trip boundaries are missing.

## Supported requests

| Request | Required information | Result |
| --- | --- | --- |
| Full trip | Origin, destination, departure date, and days/nights/return date | Flights, hotels, activities, weather, routes, and guidance |
| Flights | Origin, destination, and a return boundary | Round-trip by default; explicit `one_way` is supported |
| Hotels | Destination, check-in date, and nights/check-out date | Hotel candidates and a search link |
| Guide or activities | Destination | Activities, weather, places, routes, and guidance |
| Food | Explicit food or restaurant request | OSM restaurant candidates with cuisine/opening-hour evidence |
| Transit | Explicit bus/metro/public-transit request | Driving routes are preserved; transit is reported as unavailable until configured |

The first interactive prompt explains the four main entry types. Users can also
write combinations such as “flights and hotels” directly. Hard missing fields
are requested before any travel-data tool runs, with at most three clarification
rounds. A missing preference, budget, restaurant, or activity style does not
block a plan; the workflow leaves it optional or lets the model choose within
the confirmed boundaries.

## Architecture

```text
User input
  -> Intake Agent (MAF + Pydantic TripRequest)
  -> clarification and evidence guards
  -> deterministic SearchPlan
  -> travel adapters and public APIs
  -> typed SearchResults
  -> Planner Agent (small TripNarrative)
  -> deterministic evidence assembly and validation
  -> TripPlan
  -> Markdown or JSON
```

Optional quality path:

```text
PlannerExecutor -> ReviewerExecutor
       MAF WorkflowBuilder
```

The Planner does not receive tool permissions. The program owns dates, service
scope, activity-day assignment, provider status, and evidence copying. MAF
handles model execution and the typed Planner/Reviewer handoff; Pydantic
schemas are the application contracts.

## Tools and data boundaries

| Capability | Current implementation | Live data? |
| --- | --- | --- |
| City resolution | Open-Meteo Geocoding | Yes |
| Weather | Open-Meteo Forecast | Yes |
| Named places | Photon | Yes |
| Attractions and food | Overpass / OpenStreetMap | Yes |
| Opening-hour metadata | OSM tags with conservative parsing | Yes, when mapped |
| Driving route | OSRM | Yes; driving estimate only |
| Public transit | OpenTripPlanner adapter interface | Not configured |
| Flight candidates | Local demo adapter | No; `is_live=false` |
| Hotel candidates | Local demo adapter | No; `is_live=false` |
| Flight/hotel search | Google Travel link builder | Link only; no scraping |

Public-tool behavior is bounded by timeouts, retry/backoff, caching, a user
agent, and normalized error categories. A provider failure removes only that
provider's evidence. The final JSON records `ok`, `unavailable`, or `skipped`
for each tool call instead of replacing a failed live result with fake demo
data.

### Preferences and activity planning

The Intake model separates concrete places from abstract constraints. A named
place such as `Tokyo Tower` is resolved and verified through Photon/OSM;
constraints such as indoor/outdoor balance become structured planning hints.
The workflow then:

1. retrieves named places and attraction candidates;
2. preserves OSM evidence such as category, coordinates, opening hours,
   website, accessibility, and estimated visit time;
3. excludes only places confirmed closed by a supported weekly rule;
4. assigns verified activities to days using candidate count, duration, weather,
   venue type, and route eligibility.

Unknown opening hours and venue types remain `unknown`; the model is not allowed
to guess them. A user-named place is not silently removed because of weather.
Full details are in [docs/preference-resolution.md](docs/preference-resolution.md).

### Supplier adapters

`travel_agent.tools.adapters.TravelAdapters` is the boundary for paid suppliers.
The default `demo()` implementation returns deterministic non-live candidates.
A future `FlightAdapter` or `HotelAdapter` can be injected without changing
`TripRequest`, `SearchPlan`, or `TripPlan`. Unconfigured suppliers return an
explicit unavailable result rather than invented prices.

## Output and local state

### Output modes

- `--output markdown`: readable report for a person.
- `--output json`: validated `TripPlan` for a frontend, script, or evaluator.
- `--debug`: adds the parsed request, SearchPlan, tool events, and checks.
- `--debug-prompts`: prints dynamic model messages and `response_format` for
  teaching and diagnosis. It does not print API keys.

Advanced options are grouped by purpose:

| Group | Options | Use |
| --- | --- | --- |
| Model | `--model`, `--fallback-model`, `--mode` | Compare models or choose the structured-output strategy. Keep `--mode auto` for normal runs. |
| Conversation | `--follow-up`, `--no-follow-up` | Keep or end scripted follow-up turns. Interactive runs keep the conversation by default. |
| Review | `--review` | Add one read-only MAF Reviewer call; it costs an extra model request. |
| Persistence | `--memory-file`, `--user-id`, `--forget-memory`, `--checkpoint-file`, `--resume` | Enable local preferences or save/restore workflow state. |
| Logging | `--log-file`, `--log-input`, `--log-content` | Persist redacted run evidence; raw input and model previews are opt-in. |
| RAG | `--knowledge-dir`, `--no-rag`, `--rag-engine`, `--rag-model`, `--rag-db-dir` | Configure or disable local knowledge retrieval. |
| Offline | `--offline`, `--request-json` | Test the typed workflow with structured JSON without an LLM. |

### Run logs

Logging is opt-in. Save one redacted JSONL record per run:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --output json \
  --debug \
  --log-file .local/runs.jsonl \
  --log-input \
  --log-content
```

The record contains `run_id`, `trace_id`, validated request, SearchPlan, typed
SearchResults, final plan, ordered LLM/tool trajectory, latency, token usage
when available, estimated cost when prices are configured, and failure
attribution. Raw input and bounded model output are stored only when explicitly
requested. Prompts, hidden reasoning, and API keys are never stored.

Useful inspection commands:

```bash
jq '.request' .local/runs.jsonl
jq '.tool_calls[] | select(.status != "ok")' .local/runs.jsonl
jq '.failure_attribution, .metrics' .local/runs.jsonl
tail -n 1 .local/runs.jsonl | jq '.final_plan'
```

See [docs/run-logging.md](docs/run-logging.md) for the record schema, a real
LLM example, and the failure-localization workflow.

### Memory and checkpoints

Both are optional local files:

```bash
# Explicit long-term preference memory
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --user-id local-user

# Save and resume application workflow state
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --checkpoint-file .local/checkpoint.json --output json

PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --checkpoint-file .local/checkpoint.json --resume --output markdown
```

Memory stores explicit preferences only. It is not a conversation database or
model-training store. `--user-id` is a local partition key, not authentication;
use a real backend session ID in a future Web/API application. Checkpoints are
application-level local persistence, not a distributed job queue.

## RAG and MCP

### RAG

The local knowledge base is Markdown under `knowledge/`. The default hybrid
retriever combines BM25 with an optional multilingual embedding index and keeps
citations in `TripPlan.knowledge_sources`. RAG is for stable background guidance;
it does not replace weather, places, opening hours, or prices.

```bash
# No extra embedding dependency
PYTHONPATH=src uv run python -m travel_agent.app.cli --rag-engine bm25

# Optional local vector retrieval
uv sync --extra dev --extra rag-vector
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid \
  --rag-model intfloat/multilingual-e5-small \
  --rag-db-dir .local/chroma_rag
```

`--rag-model` is an embedding model, not a chat model. The first vector run
downloads the model locally and works on CPU; no embedding API key or GPU is
required. See [docs/rag.md](docs/rag.md) for chunking, scope filtering, vector
fallback, and replacement interfaces.

### MCP

MCP is an optional protocol boundary. It reuses the existing geocoding, weather,
and RAG code; it does not create a second travel workflow.

```bash
uv sync --extra mcp
PYTHONPATH=src TRAVEL_KNOWLEDGE_DIR=knowledge \
  uv run python -m travel_agent.mcp_server

PYTHONPATH=src uv run python scripts/mcp_smoke.py \
  --destination Tokyo --query "Tokyo Tower"
```

The default CLI calls Python adapters directly. The smoke test compares direct
Python calls with the local STDIO MCP server. Only read-only tools are exposed;
there is no booking or payment tool.

## Evaluation and tests

Deterministic checks are the release gate. The optional LLM Reviewer can score
usefulness, but it cannot override dates, prices, providers, or tool facts.

```bash
# Default offline suite: no model or public API traffic
PYTHONPATH=src uv run pytest -q

# Public no-key API smoke tests
PYTHONPATH=src uv run pytest -q -m live

# Optional configured-model tests; may consume tokens
PYTHONPATH=src uv run pytest -q -m llm_live

# Workflow contracts without model or network
PYTHONPATH=src uv run python -m evals.evaluate_workflow

# Compare Intake models on Chinese and English cases
PYTHONPATH=src uv run python -m evals.evaluate \
  --models qwen/qwen3.7-flash,deepseek/deepseek-v4.1-flash \
  --output evals/results/latest.json

# Full model-backed workflow with fixed travel evidence
PYTHONPATH=src uv run python -m evals.evaluate_end_to_end \
  --models deepseek/deepseek-v4.1-flash \
  --output evals/results/end-to-end.json
```

The offline suite covers service routing, dates and duration, clarification,
typed output, provider errors, partial degradation, food, transit, weather-aware
assignment, Memory, RAG evidence, and fact constraints. Reports include pass or
failure rate, P95 latency, failure category, failure layer, and per-case details.
The last verified local result is `267 passed, 14 deselected`.

Detailed references:

- [docs/demo.md](docs/demo.md): end-to-end example and output shapes.
- [docs/evaluation.md](docs/evaluation.md): evaluation contracts and metrics.
- [docs/run-logging.md](docs/run-logging.md): JSONL evidence and debugging.
- [docs/rag.md](docs/rag.md): RAG design and embedding options.
- [docs/code-audit-20261028.md](docs/code-audit-20261028.md): audit findings and limits.
- [docs/teacher-update.md](docs/teacher-update.md): concise project update.

## Repository structure

| Directory | Responsibility |
| --- | --- |
| `src/travel_agent/agents` | MAF Agent factories |
| `src/travel_agent/workflows` | Intake, planning, tool execution, follow-up, review, checkpoint |
| `src/travel_agent/schemas` | Pydantic contracts for requests, evidence, plans, traces, and review |
| `src/travel_agent/tools` | Public API clients, tool adapters, search links, and demo suppliers |
| `src/travel_agent/rag` | Markdown, BM25, embeddings, Chroma, and hybrid retrieval |
| `src/travel_agent/memory` | Local preference storage |
| `src/travel_agent/app` | CLI, rendering, tracing, metrics, and JSONL logging |
| `evals` | Model, end-to-end, and workflow evaluations |
| `tests` | Offline contracts plus opt-in live/model/vector smoke tests |
| `knowledge` | Markdown source for local RAG |
| `scripts` | Multi-turn, run-analysis, transcript, and MCP utilities |
| `docs` | Detailed design and teaching notes |

## Current limits and next steps

- Flight and hotel results are demo candidates, not live inventory.
- Google Travel links are search entry points; the project does not scrape them.
- Public APIs have no SLA and may return partial results.
- OSRM provides driving estimates; transit remains an adapter interface.
- There is no booking, payment, database, server deployment, or authentication
  layer in this local portfolio version.

The next production-oriented extension is to implement authenticated
`FlightAdapter` and `HotelAdapter` providers, then keep the existing typed
contracts, evidence checks, traces, and evaluation suite unchanged.

## License

See [LICENSE](LICENSE). Third-party public data and services retain their own
licenses and attribution requirements, including OpenStreetMap attribution.
