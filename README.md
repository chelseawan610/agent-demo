# Travel Planning Agent

A single-agent travel planner on Microsoft Agent Framework. The model reads
natural language and writes prose; a Python workflow decides which tools run,
owns dates and boundaries, degrades on failure, and assembles every travel
fact from typed tool evidence.

**Input is bilingual (Chinese or English). User-facing output follows the input
language; JSON field names remain stable English API identifiers.**

## Quickstart

```bash
cp .env.example .env        # set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
uv sync --extra dev
PYTHONPATH=src uv run python -m travel_agent.app.cli
```

Then just describe a trip, in either language:

```text
I want to fly from Shanghai to Kuala Lumpur on 2026-10-30 for 5 days,
and I want to visit the Petronas Towers on day 3.
```

## Conversation

The plan is not the end of the run. After it prints, the agent keeps the typed
`SearchResults` in memory and classifies each follow-up turn, because the
branches differ in cost by an order of magnitude:

| You say | What happens | External calls |
| --- | --- | --- |
| `make it 4 days` | re-plans; executes the new SearchPlan | tools in the new plan |
| `more detail on day two` | re-runs the planner over existing evidence | none |
| `why isn't X scheduled?` | answers from the evidence already gathered | none |
| *(Enter)* | ends the session | none |

An unchanged `SearchPlan` reuses the previous tool results outright.

Scripted runs use `--follow-up` to enter the loop through a pipe, and
`--no-follow-up` to exit after the first plan. `scripts/demo_multi_turn.py`
records a whole conversation against the real model.

## What the program will not let the model do

Model-written values are not trusted on sight:

- Origin, dates, budget, and an explicit one-way override must be supported by
  a quote from the user's own message (direct clarification answers are exempt).
  A value that cannot be cited is cleared and asked about through
  the normal `required_missing_fields` flow, so an invented origin, date,
  budget, or one-way flag cannot silently remove a clarification question.
- 23 deterministic invariants run after the plan is assembled. A failure
  regenerates once with the failed check names fed back, then degrades to
  evidence-only assembly. Failures are reported to the user, not just logged.
- A schema failure is retried once with the validation error appended; the
  planner runs at temperature 0, so an identical prompt would simply repeat
  the identical bad output.

Design notes for each of these are in [docs/](docs/).

The [2026-10-28 code audit](docs/code-audit-20261028.md) records the latest
fixes, verification commands, and known limits. Intake eval fixtures use 2099
for explicit dates so a correct past-date guard does not invalidate the test
as time passes. Yearless-date expectations still use the next occurrence.
Archived model results predate this fixture revision and are not new scores.

---

This is a single-agent travel planner built on Microsoft Agent Framework (MAF).
The model understands natural language and writes the user-facing narrative;
the Python workflow chooses tools, owns date boundaries, handles degradation,
and enforces factual constraints.

## Architecture

```text
User input
  -> Intake Agent
  -> TripRequest (Pydantic)
  -> SearchPlan (deterministic)
  -> public API adapters / demo inventory / search-link tools
  -> SearchResults (typed evidence)
  -> Planner Agent -> TripNarrative (small prose object)
  -> deterministic evidence assembly
  -> TripPlan
  -> JSON or Markdown

Optional quality path:
  PlannerExecutor -> ReviewerExecutor (MAF WorkflowBuilder)
```

## Feature map

| Capability | What it does | Default behavior |
| --- | --- | --- |
| Natural-language Intake | Extracts destination, origin, dates, duration, scope, budget, preferences, language, and transport modes into `TripRequest` | Uses the configured OpenAI-compatible model and Pydantic validation |
| Clarification state machine | Asks only for hard missing fields and merges up to three answers | Runs before any travel-data tool call |
| Typed planning | Converts `TripRequest` into a deterministic `SearchPlan` | Prevents duplicate or out-of-scope tool calls |
| Travel retrieval | Geocoding, weather, named places, attractions, food, opening-hours metadata, and driving routes | Uses free public APIs with timeout, retry, caching, and partial degradation |
| Flight/hotel boundary | Returns clearly labelled demo candidates and Google Travel search links | Never scrapes prices or claims a booking was made |
| Evidence assembly | Copies tool facts into a validated `TripPlan` | The Planner cannot rewrite dates, prices, providers, or activity evidence |
| Bilingual rendering | Produces user-facing Markdown in the input language | JSON keys stay stable English identifiers |
| RAG | Retrieves stable travel guidance from local Markdown using BM25, vectors, or hybrid RRF | Optional; vector retrieval falls back to BM25 in hybrid mode |
| Memory | Stores explicit user preferences in a local JSON file | Opt-in; default runs persist nothing |
| Checkpoint | Saves and restores `intake`, `searched`, and `complete` workflow stages | Local application state, not distributed orchestration |
| Observability | Records run IDs, trace IDs, LLM/tool events, latency, errors, tokens, and estimated cost | JSONL logging is opt-in and redacted by default |
| Evaluation | Runs deterministic invariants, golden/held-out Intake cases, end-to-end fixtures, live API smoke tests, and optional review | Offline tests are the default CI gate |
| MCP | Exposes read-only geocoding, weather, and RAG tools through a local STDIO server | Optional compatibility layer; the default CLI calls Python adapters directly |

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/travel_agent/agents/` | MAF Agent factories for Intake, Planner, Formatter, Reviewer, and follow-up routing |
| `src/travel_agent/workflows/` | Intake, SearchPlan execution, Planner/evidence assembly, follow-up, checkpoint, offline, review, and MAF workflow wiring |
| `src/travel_agent/schemas/` | Pydantic request, search, evidence, trip-plan, narrative, review, trace, and checkpoint contracts |
| `src/travel_agent/tools/` | Provider clients, retry/caching HTTP transport, OSM/Open-Meteo/OSRM tools, search links, and supplier adapters |
| `src/travel_agent/clients/` | OpenAI-compatible client construction and transient-error fallback wrapper |
| `src/travel_agent/rag/` | Markdown chunking, BM25 retrieval, embedding retrieval, Chroma persistence, and hybrid fusion |
| `src/travel_agent/memory/` | Versioned local JSON preference store |
| `src/travel_agent/app/` | CLI, Markdown/JSON rendering, run logging, tracing, and metrics |
| `src/travel_agent/quality.py` and `validation.py` | Deterministic workflow invariants and final-plan validation |
| `evals/` | Intake model comparison, end-to-end model evaluation, workflow contracts, and metrics |
| `tests/` | Offline unit/contract tests plus opt-in live, LLM-live, and vector smoke tests |
| `knowledge/` | Local Markdown knowledge source for RAG |
| `scripts/` | Multi-turn demos, run analysis, transcript checks, and MCP direct-vs-protocol smoke test |
| `docs/` | Design notes, RAG, preference resolution, evaluation, logging, audit, demo, and teacher-facing material |

The most important boundary is `TripRequest -> SearchPlan -> SearchResults ->
TripNarrative -> TripPlan`. The model supplies semantic interpretation and
bounded prose; typed Python code supplies execution, facts, validation, and
observability.

The implementation follows two patterns from Microsoft's agent course:

- [Tool Use Design Pattern](https://github.com/microsoft/ai-agents-for-beginners/blob/main/04-tool-use/README.md): tools have explicit parameters, types, and error states.
- [Planning Design](https://github.com/microsoft/ai-agents-for-beginners/blob/main/07-planning-design/README.md): create a structured plan first, then execute dependencies in a controlled order.

The Planner has no direct tool permissions. Every tool call must come from a
validated `SearchPlan`, which prevents an uncontrolled model loop from changing
dates or repeatedly calling providers.

This is not a long-prompt-only implementation. Intake and Planner cross a
Pydantic boundary through MAF `response_format`. The normal Planner returns a
small `TripNarrative` (summary and daily titles); the program then assembles the
complete `TripPlan` deterministically from `TripRequest` and `SearchResults`.
Flights, hotels, weather, places, routes, sources, and activity dates cannot be
rewritten by the model. The model handles semantic understanding and prose;
the program owns boundaries, execution, failure states, and audit records.

## Data sources

| Capability | Provider | Authentication | Behavior |
| --- | --- | --- | --- |
| Destination geocoding | Open-Meteo Geocoding | None | City to latitude/longitude |
| Weather | Open-Meteo Forecast | None | Daily forecast for the trip range |
| Named preferences | Photon | None | Resolves provider-friendly place queries |
| Attractions and venue metadata | Overpass / OpenStreetMap | None | Named attractions, coordinates, opening-hours text, website and accessibility tags when mapped |
| Optional dining candidates | Overpass / OpenStreetMap | None | Restaurants, cafes, cuisine tags and opening-hours text when mapped |
| Route estimate | OSRM / OpenStreetMap | None | Driving distance, duration, and stop order |
| Public transit route | OpenTripPlanner adapter | GTFS/service configuration | Explicitly unavailable until a transit provider is configured |
| Flight/hotel live lookup | Google Travel link | None | Generates a link; does not scrape results |
| Flight/hotel candidates | Local demo adapter | None | Explicitly marked `is_live=false` |

Public APIs are best-effort services with no SLA. OpenStreetMap attribution must remain visible. This project is intended for a non-commercial portfolio demo with light traffic.

### Activity selection and opening hours

Activity assignment is deterministic and is not delegated to the Planner:

1. Intake separates concrete places from planning constraints: `place_name` and
   `place_type` describe a place, while `planning_hint` describes constraints
   such as indoor/outdoor balance. Photon resolves only concrete places and
   verifies them using destination coordinates, normalized names, and OSM types;
   a same-name restaurant cannot replace a harbour.
2. The program calculates daily capacity from the candidate count and trip
   length. It normally assigns one to three places per day rather than forcing
   exactly three.
3. When OSM provides a simple `opening_hours` rule, places marked `closed` for
   the planned date are excluded. Missing or unsupported rules remain
   `unknown` and the output says that opening hours were not verified.
4. Resolved places are assigned to days and nearby places are kept together
   where possible. A return trip gives the final day less capacity because the
   return flight time has not been selected.

`venue_mix=balanced` asks Overpass for parks, gardens, and other outdoor
candidates and alternates known indoor/outdoor candidates before truncation.
Daily assignment uses the same constraint. It does not invent a venue to satisfy
an indoor/outdoor request. Unknown types remain `unknown`; if there are not
enough candidates, the final plan is marked `partial` and explains the unmet
constraint. Concrete place requests require a verified full name after Unicode,
case, and whitespace normalization; substring guessing is not used. Unsupported
aliases, type mismatches, and distant same-name results return `unavailable`
instead of accepting a similar business. Area landmarks such as a harbour keep
their center point in JSON but are marked `route_eligible=false`, so they are not
passed to routing as a verified entrance. See
[docs/preference-resolution.md](docs/preference-resolution.md) for the full
example and implementation notes.

Only common weekly rules such as `Mo-Fr 10:00-18:00`, `Mo-Su off`, and `24/7`
are parsed safely. Holidays, temporary closures, and complex OSM rules are not
guessed; users should verify them on the official website. Activity candidates
retain `opening_hours`, `opening_status`, `website`, and
`estimated_visit_minutes`, so JSON can be consumed by a frontend or evaluation
program.

Weather is a deterministic ranking signal, not a hard-coded “rain means delete
the attraction” rule. When precipitation probability reaches 60% and OSM
explicitly marks a place as `outdoor`, the program prefers a drier travel day.
If no better day exists, the place remains in the plan. A user-named place is
never silently removed by weather. Places retain `venue_type` (`indoor`,
`outdoor`, `mixed`, or `unknown`), and a type is assigned only when OSM provides
evidence rather than model speculation.

### Dining and transport modes

Dining is an optional `food` service, not another Agent. Intake adds it to
`requested_services` only when the user explicitly asks for restaurants, food,
or a cuisine. SearchPlan then executes `search_food`. The tool reuses Overpass
place, opening-hours, website, and OSM attribution fields. It returns restaurant
candidates without inventing prices, ratings, menus, or booking status. When a
cuisine is requested, the program keeps only candidates whose OSM `cuisine` tag
matches the provider-friendly query. Missing tags or no matches produce an
explicit empty result instead of presenting an ordinary cafe as sushi or ramen.
Normal travel requests do not incur a dining call.

Routes default to OSRM driving estimates. When the user asks for a metro or
bus, the workflow preserves driving and adds a `transit` mode by calling
`optimize_transit_route`. It currently returns `not_configured` because the
free OSRM instance is not a transit planner; a real transit route needs an
OpenTripPlanner adapter backed by GTFS data. The interface is already stable,
so a future adapter will not change SearchPlan or TripPlan, and driving minutes
will never be presented as transit time. `transport_modes` records
`["driving", "transit"]`, so one route failure does not erase another result.

### When to split agents

The project does not create separate Agents for attractions, dining, and routes:
they are different tools and data sources, not independent reasoning goals.
Additional Agents would add context handoffs, schemas, retries, logs, and cost,
making the demo harder to verify.

The current boundaries are: `IntakeAgent` for request extraction, a
deterministic Search workflow for tools, `TravelPlanner` for narrative, and an
optional `Reviewer`. Consider a specialist such as `TransportAgent` or
`ExperienceAgent` only when a subtask has an independent goal and tool set, can
run independently, and needs separate evaluation. SearchPlan and adapters
already make those future boundaries reusable without rewriting the tool layer.

Booking.com and Skyscanner scraping is intentionally not implemented because their terms prohibit unauthorized automated scraping. Real flight and hotel inventory requires a registered provider such as Amadeus or Booking Demand API; the local adapters can be replaced later without changing `TripRequest`, `SearchPlan`, or `TripPlan`.

### Supplier adapters

`travel_agent.tools.adapters.TravelAdapters` is the boundary for paid
suppliers. The default `TravelAdapters.demo()` uses deterministic candidates
and marks them non-live. A future provider implements the small
`FlightAdapter` or `HotelAdapter` protocol and is injected into
`execute_search_plan(plan, adapters=...)`; the planning workflow and output
schema do not change. `UnconfiguredFlightAdapter` and
`UnconfiguredHotelAdapter` return `unavailable` instead of inventing prices.

## Setup

```bash
cd /Users/chelsea/Desktop/cs336/agent-demo
uv sync --extra dev --python /Users/chelsea/.local/bin/python3.12
cp .env.example .env
```

Set the existing OpenAI-compatible model connection in `.env`:

```dotenv
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=your_key
LLM_MODEL=deepseek/deepseek-v4.1-flash
LLM_REASONING_EFFORT=none
TRIP_OUTPUT_MODE=auto
LLM_TIMEOUT_SECONDS=90
TRAVEL_API_TIMEOUT_SECONDS=20
TRAVEL_OVERPASS_TIMEOUT_SECONDS=8
TRAVEL_OVERPASS_ATTEMPTS=1
```

The travel-data APIs need no API keys.

`TRAVEL_OVERPASS_TIMEOUT_SECONDS` and `TRAVEL_OVERPASS_ATTEMPTS` deliberately
use stricter defaults than the other public endpoints. Overpass is a shared
community service and can be slow or rate-limited; a timeout marks only the
attractions tool unavailable instead of blocking the whole plan. Increase these
values only for a deliberate live smoke test, not for every classroom run.

`deepseek/deepseek-v4.1-flash` is the current default because it passed the
expanded intake golden set reliably. `qwen/qwen3.7-flash` remains an inexpensive
comparison model in the evaluation command. The project still validates every response with
Pydantic and only uses the legacy formatter in `--mode convert`, so a model is
not trusted merely because it advertises JSON support. `none` disables hidden reasoning by
default so a small response budget is not consumed before JSON is emitted.
You can override it with another OpenAI-compatible model without changing
Python code; raise reasoning to `low` or `medium` only after an evaluation
shows a real quality gain. Some models may be unavailable in a particular
OpenRouter region; the evaluation command below is the check before changing
the default.

## Run

The simplest Markdown run (the only command most users need) is:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli
```

The CLI configures stdin, stdout, and stderr as UTF-8 and safely falls back when
the host terminal refuses reconfiguration. Chinese input therefore does not
fail solely because of a local locale mismatch. A small entity guard also
protects explicit cities: if the user writes Seoul or Tokyo, the model cannot
silently replace it with another city. Ambiguous multi-city input returns to
clarification before tools run.

Each Intake clarification round is stateless: the next round reads the
original request and the new answer, then the program merges confirmed fields.
The previous model JSON is not treated as a new user fact. This prevents one
bad parse from being reinforced through an MAF session or clarification context.
Prompt examples use placeholders so an example city cannot become a default
answer.

Use JSON when another program or an evaluation script needs to consume the result:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --output json
```

Example request:

```text
I am leaving from Shanghai for Tokyo on 2026-10-01 for three days.
My total budget is CNY 10,000. I want to visit Tokyo Tower and I like anime.
```

See [docs/demo.md](docs/demo.md) for the expected tool trace and JSON/Markdown shapes.

`--debug` shows `TripRequest`, `SearchPlan`, `ToolCallRecord`, and deterministic
workflow checks. It never prints the LLM API key:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --debug --output json
```

### Choosing CLI options

Most users do not need to memorize every option. The current options are grouped
by purpose:

| Group | Option | Purpose |
| --- | --- | --- |
| Common | `--output` | Select Markdown or JSON; Markdown is the default. |
| Common | `--mode` | Select the structured-output strategy; `auto` is the default. |
| Common | `--no-follow-up`, `--follow-up` | End after the first plan or keep a scripted follow-up loop. Interactive terminals keep follow-ups by default. |
| Debug/evaluation | `--debug` | Show the parsed request, SearchPlan, tool events, and checks. |
| Debug/evaluation | `--debug-prompts` | Show dynamic model messages for teaching and diagnosis. |
| Debug/evaluation | `--offline`, `--request-json` | Skip the LLM and test the structured tool workflow with a `TripRequest` JSON object or `@path`. |
| Debug/evaluation | `--review` | Run the read-only MAF Reviewer after planning; adds one model call. |
| Model/trace | `--model` | Override the `.env` primary model for one run or a comparison. |
| Model/trace | `--fallback-model` | Use a second model for transient primary-provider failures. |
| Model/trace | `--log-file` | Append a redacted run record to JSONL. |
| Model/trace | `--log-input`, `--log-content` | Explicitly persist the raw input or bounded model output; both need `--log-file`. |
| Local state | `--memory-file` | Enable optional long-term preference memory. |
| Local state | `--user-id` | Select the memory partition; defaults to `local-user`. |
| Local state | `--forget-memory` | Delete the current user's preferences; requires `--memory-file`. |
| Local state | `--checkpoint-file`, `--resume` | Save or restore an application workflow stage. |
| RAG | `--knowledge-dir` | Select the Markdown knowledge directory; defaults to `knowledge/`. |
| RAG | `--no-rag` | Disable knowledge retrieval for an A/B comparison. |
| RAG | `--rag-engine` | Select `hybrid`, `vector`, or `bm25`. |
| RAG | `--rag-model`, `--rag-db-dir` | Configure the embedding model and Chroma directory. |

These options exist because they support normal output, diagnosis, evaluation,
Memory, Checkpoint, and RAG experiments. Only `--output` is commonly needed by
an end user; mode, model, logging, review, checkpoint, and RAG options are
developer controls.

The CLI exits with code `0` for a completed plan, a partial plan, or a normal
`needs_clarification` response. It exits with code `1` for workflow/provider
failures or invalid checkpoint input, so shell scripts and CI can distinguish a
valid user-facing result from an execution failure.

Use `--debug-prompts` when teaching or debugging model input. It prints the
stage (`intake`, `planner.narrative`, `formatter.structured` in legacy
`convert`, and so on), the
dynamic user message, and the `response_format` option. The agent's static
system instructions remain in `src/travel_agent/agents/`; travel tools receive
typed Python arguments from `SearchPlan`, not an unconstrained tool loop.
Use `--model MODEL_ID` to compare a provider model for one run without editing
`.env`, for example `--model qwen/qwen3.7-flash`.

Enable explicit preference memory (optional). The default `user_id` is
`local-user`:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --output markdown
```

When multiple users share one computer and one Memory file, override
`--user-id` so their preferences stay separate:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --user-id chelsea

PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --user-id alice
```

`--user-id` is only a local preference partition key, not authentication. The
terminal has no account system; a real Web/API application should obtain the
user ID from its authenticated backend session. `.env` and `.local/` are in
`.gitignore`; do not package API keys, Memory, run logs, or checkpoints for
sharing.

Memory stores only explicit travel preferences, such as “I like anime”. Intake
clarification uses a short-lived MAF `AgentSession`; it does not write the full
conversation to the long-term preference file and does not train the model.
The current request overrides historical preferences. Delete local preferences:
`--forget-memory --memory-file .local/memory.json --user-id local-user`。

Enable one read-only quality review:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --review --output json
```

This adds one `TripPlanReviewer` model call and passes the typed plan through the
MAF `WorkflowBuilder` edge `PlannerExecutor -> ReviewerExecutor`. The Reviewer
can return only a `ReviewResult` (pass / needs_revision, issues, strengths, and
checks); it cannot rewrite flights, prices, dates, or tool facts. If review
fails, the original plan is kept and the failure is added to `notes`.

Save and restore local workflow state:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --checkpoint-file .local/checkpoint.json --output json

PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --checkpoint-file .local/checkpoint.json --resume --output markdown
```

The checkpoint stores three application stages: `intake`, `searched`, and
`complete`. `complete` can be replayed without a model. `searched` skips intake
and external tools and restores the Planner (and optional Reviewer) with the
original request and evidence. This is not a server-side queue or distributed
MAF persistence. MAF WorkflowBuilder currently handles the Planner-to-Reviewer
handoff, while the application checkpoint stores the complete travel evidence.

For an optional provider-error fallback, configure or pass a second model:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --model deepseek/deepseek-v4.1-flash \
  --fallback-model qwen/qwen3.7-flash \
  --log-file evals/results/runs.jsonl
```

Fallback is used only for transient connection, timeout, rate-limit, and 5xx
errors. A schema or business-validation error is not silently hidden by a
second model. The JSONL run log contains the normalized request, plan, tool
statuses, model, and final status; raw input is omitted unless `--log-input`
is explicitly supplied. API keys are never written.

Each run record also contains a redacted `trajectory`: ordered `llm` and
`tool` events with a shared `run_id`/`trace_id`, stage/name, status,
provider/model, start time, elapsed milliseconds, tool inputs, and token usage
when the provider returns it. The record also keeps typed `search_results` and
the normalized `final_plan`, so a failed run can be inspected without replaying
the external APIs.
`input_tokens`, `output_tokens`, and `total_tokens` may be `null` because some
OpenAI-compatible gateways do not expose usage. Raw prompts and hidden model
reasoning are never written to this trajectory. This makes the file useful for
latency, failure, and cost analysis without turning it into a private prompt
transcript.

Tool events are assigned stable workflow stages (`retrieval`, `geocoding`,
`weather`, `places`, `routing`, or `inventory`) and normalized error
categories such as `provider_transient`, `provider_configuration`,
`provider_contract`, `provider_no_match`, or `workflow_dependency`. The run
record exposes `failure_attribution`, while `metrics` includes
`failure_categories`, `failure_by_stage`, `duplicate_tool_calls`, and
`retryable_failures`. Evaluation can therefore locate a failure without
parsing provider-specific exception text.

Use `--log-content` only when you explicitly need bounded LLM output previews
inside the trajectory. It stores at most 4,000 characters per model event;
prompts remain excluded. Set `LLM_INPUT_PRICE_USD_PER_MILLION` and
`LLM_OUTPUT_PRICE_USD_PER_MILLION` in `.env` to calculate an estimated USD cost.
The run-level `metrics` object reports token totals, per-stage and per-provider
P95 latency, success rates, provider error rate, and estimated cost. Leave
prices empty when the gateway's current price is unknown; cost then remains
`null` rather than pretending to be exact.

Public-tool transport behavior is configurable through `.env`:

```text
TRAVEL_API_TIMEOUT_SECONDS=20
TRAVEL_API_CACHE_TTL_SECONDS=300
TRAVEL_API_BACKOFF_SECONDS=1
TRAVEL_API_MAX_BACKOFF_SECONDS=8
```

Only timeouts, connection errors, 408/425/429, and 5xx responses are retried.
Schema errors, non-retryable 4xx responses, no-match results, and unconfigured
adapters are reported directly. A valid `Retry-After` header is honored up to
one minute.

For the complete field reference, a real-LLM command, and a sanitized JSONL
example suitable for GitHub, see [Run logging and debugging](docs/run-logging.md).

### Run without an LLM

Natural-language intake cannot be done by the CLI alone: it requires either a
hosted model or a local model. To demonstrate the rest of the workflow without
an LLM key, pass a normalized `TripRequest` JSON to `--offline`:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --offline \
  --output markdown \
  --debug \
  --request-json '{"requested_services":["flights","hotels","activities","guide"],"origin":"Shanghai","destination":"Tokyo","start_date":"2026-10-01","days":3,"flight_type":"round_trip","preferences":["Tokyo Tower"],"preference_details":[{"label":"Tokyo Tower","search_query":"Tokyo Tower Tokyo","kind":"place"}]}'
```

Offline mode still calls the free geocoding, weather, place, attraction, and
routing providers. It does not call an LLM and therefore uses deterministic
day titles; it is a workflow/tool smoke test, not a natural-language agent.
Use `--request-json @path/to/request.json` when the JSON is too long for a
shell command.

## Clarification behavior

- The first interactive turn explains four entry types: full trip, round-trip
  flights, hotels, and guide/activities. The user can choose a number or ignore
  the menu and write any combination such as “flights and hotels” directly.
- A full plan requires origin, destination, departure date, and one duration
  boundary: days, nights, or a return date.
- Flights-only defaults to round-trip and therefore requires a return date or
  trip length. An explicit one-way request is represented by `flight_type=one_way`
  and needs no return boundary.
- Hotels-only requires destination, check-in date, and nights or check-out date.
- Guide/activities requires only destination; its duration may be selected by
  Intake when the user leaves it open.
- Explicit days, nights, and return dates always win. A full plan or round-trip
  request stays in clarification when its duration boundary is missing. For
  guide/activities-only, Intake may recommend bounded `days` and records a
  `duration_profile` (`day_trip`, `weekend`, `short_break`, `standard`, or
  `extended`).
- When both dates are explicit, elapsed days use the date difference without an
  inclusive `+1`: 2026-10-20 to 2026-10-23 resolves to 3 days. Hotel nights use
  the explicit interval when available; an explicit 3-day request without a
  return date resolves to the usual 2-night stay.
- Dates such as `October 3`, `10.3`, and `2026.10.3` are normalized to ISO dates. If the year is omitted, the nearest upcoming occurrence relative to the runtime date is used.
- The CLI asks at most three follow-up rounds. Every round merges earlier answers, so supplying `Shanghai` once cannot be lost later.
- If required fields are still missing, it returns `needs_clarification` and calls no travel-data tools.
- Required fields are computed by `TripRequest.required_missing_fields()`, not by
  planner prose. This keeps the user-facing questions and the SearchPlan
  boundary identical.
- Interactive CLI clarification keeps asking while a hard field is still
  missing, up to three rounds. `--offline` is different: it accepts an already
  structured `TripRequest` and deliberately cannot ask natural-language
  follow-up questions.

## Failure behavior

Each external adapter returns a typed `ToolResult` with `ok`, `unavailable`, or `skipped`. A timeout or rate limit only removes that provider's data; successful data from other providers remains. Mock data is never substituted for a failed real API.

Weather, attraction, and route facts are copied from `SearchResults` into `TripPlan` after the model responds. In the normal `auto` path the model only writes the bounded `TripNarrative`; it cannot move an attraction to another day or replace tool evidence.

If the compact Planner response is invalid, the workflow emits the validation
error in the trajectory and builds a deterministic plan directly from typed
`SearchResults`. This is a controlled degradation: narrative text may be less
polished, but tool facts, dates, service scope, and provider status remain
inspectable. `TripPlan.generation_source` distinguishes `llm`,
`deterministic_fallback`, and `offline`; `model_fallback_used` in JSONL means a
second provider model was used, which is different from deterministic fallback.
In `--mode direct` the strict behavior is retained and the schema error is
returned to the caller, which is useful when testing a model's structured-output
support.

## Tests

Offline suite (default; no public API traffic):

```bash
PYTHONPATH=src uv run pytest -q
```

Live public-API smoke tests (serial, no accounts or keys):

```bash
PYTHONPATH=src uv run pytest -q -m live
```

Optional LLM intake smoke tests (uses the configured model and may consume tokens):

```bash
PYTHONPATH=src uv run pytest -q -m llm_live
```

The offline suite covers service routing, one-way/round-trip boundaries, cross-month dates, one-day trips, three-round clarification, typed output, provider errors, partial degradation, food scope, transit-adapter degradation, weather-aware activity assignment, memory contracts, and fact constraints. The live suite covers Tokyo/Seoul/New York geocoding, Tokyo weather, Photon preferences, Overpass attractions, and OSRM routing.

The current local result is `267 passed, 14 deselected`; the no-key public-API
smoke run is `7 passed, 2 skipped` when a provider is rate-limited or
temporarily unavailable. Public APIs have no SLA, so the default CI-style suite
remains offline.

Additional local contract tests cover JSON Memory read/write, corrupt-file
degradation, preference boundaries, checkpoint round-tripping, and Reviewer
prompt and `ReviewResult` parsing.

To compare intake models on the same golden cases (this calls the configured
LLM and may consume tokens):

```bash
PYTHONPATH=src uv run python -m evals.evaluate \
  --models qwen/qwen3.7-flash,deepseek/deepseek-v4.1-flash \
  --output evals/results/latest.json
```

The report currently contains 23 Chinese/English cases (18 golden cases plus
5 held-out paraphrases) and checks service scope, dates, duration, budget
preservation, preferences, readiness, latency, and per-case failures. It is an
engineering signal, not a claim that the model is always correct.

To aggregate persisted CLI runs and compare two designs or models:

```bash
PYTHONPATH=src uv run python scripts/analyze_runs.py \
  evals/results/typed-plan.jsonl evals/results/free-loop.jsonl
```

The report contains completion rate, failure categories, failing tool stages,
average tool-call count, duplicate calls, P95 latency summaries, and estimated
cost. It does not rate prose quality; that remains a separate deterministic,
LLM-review, or human-evaluation question.

For a full model-backed workflow check, using fixture travel evidence instead
of public HTTP calls, run:

```bash
PYTHONPATH=src uv run python -m evals.evaluate_end_to_end \
  --models deepseek/deepseek-v4.1-flash,qwen/qwen3.7-flash \
  --output evals/results/end-to-end.json
```

This costs model tokens, but it checks the complete `Intake → SearchPlan →
SearchResults → Planner → TripPlan` path. The fixtures make tool facts
deterministic, so a model cannot pass by receiving a different weather or POI
response on each run.

The held-out paraphrase set is kept separate from the prompt examples:

```bash
PYTHONPATH=src uv run python -m evals.evaluate \
  --models deepseek/deepseek-v4.1-flash,qwen/qwen3.7-flash \
  --split heldout \
  --output evals/results/heldout.json
```

The CLI's `--debug` mode also prints deterministic `WorkflowChecks`. These
checks validate the process after the model responds: requested services match
tool calls, final facts equal normalized tool evidence, activity and route days
stay in range, and tool statuses are preserved. This is intentionally separate
from an LLM judge. An LLM judge can score open-ended usefulness, but it must not
be trusted to decide whether a date, price, provider, or tool call actually
occurred.

Each offline evaluation row also includes `check_metadata`, `failure_details`,
and `tool_failures`. The `layer` field identifies where a failure occurred
(`intake`, `planning`, `evidence`, `constraint`, or `observability`), while
`category` identifies the contract that failed. This makes later ablations
auditable: compare the same cases with or without typed planning, RAG, or
deterministic constraints and report changes in failure categories, latency,
and tool-call count.

The repository also contains an optional LLM judge for one completed run:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --mode auto --output json --review
```

`--review` runs the MAF `PlannerExecutor -> ReviewerExecutor` workflow. The
reviewer receives the normalized request, `SearchPlan`, `SearchResults`, and
validated `TripPlan`, then returns a typed `ReviewResult`. It is opt-in because
it costs another model call; deterministic evidence checks remain the release
gate. The offline 10-case workflow report intentionally does not call an LLM
judge.

Run the complete workflow contract tests without a model or network:

```bash
PYTHONPATH=src uv run python -m evals.evaluate_workflow
```

This checks full-trip, guide-only, flights-only, hotels-only, already-purchased
flight, food-only, transit-adapter, RAG evidence, and missing-date routing. It is intentionally separate from the LLM
comparison: the former tests application-owned orchestration, while the latter
tests model-dependent requirement extraction.

### Model comparison and remaining work

`evals/evaluate.py` compares the Intake stage: the same Chinese and English
golden and held-out inputs are sent to Qwen and DeepSeek, and the evaluator
checks `TripRequest` service scope, dates, duration, budget, preferences, and
readiness. `evals/evaluate_end_to_end.py` uses fixed travel evidence to check
the complete `Intake -> SearchPlan -> SearchResults -> Planner -> TripPlan`
path. A model is not considered successful merely because it is connected; it
must pass the same typed-field and workflow-invariant cases. Prose quality is a
separate question. Factual correctness is checked by the program, not delegated
to another LLM's unsupported opinion.

Course concepts and their implementation in this repository:

| Topic | Current implementation | Possible next step |
| --- | --- | --- |
| Tool Use | Typed adapters, `ToolResult`, call records, and partial degradation | Add an official flight/hotel supplier adapter |
| Planning | `TripRequest -> SearchPlan` with fixed dependencies; MAF `WorkflowBuilder` for the quality path | Map longer execution state to a durable MAF workflow |
| Context engineering | Raw request, typed state, evidence bundle, and bounded Memory | Compress long conversations and monitor context budget |
| Memory | Optional local JSON preference store | Replace it with multi-account database or MAF/Mem0 storage |
| Metacognition | Optional read-only Reviewer plus deterministic checks | Add online feedback, regression sets, and human sampling |
| Production/evaluation | JSONL traces, latency/status, golden/held-out/E2E suites | Add a cost, token, and success-rate dashboard |
| Multi-agent | Intake, Planner, Formatter, and Reviewer boundaries; Planner-to-Reviewer MAF edge | Add specialists only when they have independent permissions and handoffs |

Booking, payment, database, server deployment, and free-loop multi-agent
execution are intentionally out of scope for this portfolio version. They
would increase risk and cost without replacing the current priority: reliable
evaluation and a clean boundary for real suppliers.

### Local RAG

The default `hybrid` RAG pipeline is: split Markdown by headings and paragraphs
-> BM25 lexical retrieval plus `intfloat/multilingual-e5-small` embedding
retrieval -> reciprocal-rank fusion (RRF) -> `KnowledgeHit` objects with
`source`, `section`, and `score` -> Planner evidence -> citations in
`TripPlan.knowledge_sources`. The knowledge base is for stable background
guidance, not weather, places, opening hours, or prices, and it is never treated
as real-time evidence. If vector dependencies are unavailable, `hybrid` falls
back to BM25; `vector` reports an explicit error. Retrieval also applies a
destination scope: generic sections may match, while destination-specific
sections must mention the current destination to avoid cross-destination
contamination.

#### RAG options and model requirements

RAG options have separate responsibilities:

| Option | Purpose | Default | When to change it |
| --- | --- | --- | --- |
| `--knowledge-dir` | Markdown knowledge-base directory | `knowledge` | The source files live elsewhere |
| `--rag-engine` | `bm25`, `vector`, or `hybrid` retrieval | `hybrid` | Use `bm25` with no extra dependency; use `vector`/`hybrid` for embeddings |
| `--rag-model` | **Embedding model** that converts queries and documents to vectors | `intfloat/multilingual-e5-small` | Compare semantic retrieval models |
| `--rag-db-dir` | Local Chroma vector-index directory | `.local/chroma_rag` | Isolate a knowledge base or change embedding models |
| `--no-rag` | Disable retrieval completely | Off | Debug the model/tool path in isolation |

`--rag-model` is not a chat-model option. It must be an embedding model name
loadable by `sentence-transformers.SentenceTransformer`, or a local model
directory. Do not pass an OpenRouter/chat model such as `deepseek-chat`,
`qwen-*`, or `gpt-*`. The chat model is selected by `--model` or `LLM_MODEL`;
the embedding model is selected by `--rag-model` or `RAG_EMBEDDING_MODEL`.
They serve different purposes.

The default model fits this small bilingual travel knowledge base. The first
`vector`/`hybrid` run downloads and caches it from Hugging Face, so it needs
network access, disk space, and some startup time, but no API key or GPU. An
arbitrary name is not automatically usable: the model must be public,
Sentence-Transformers-compatible, and small enough for the local machine.
Private or gated models additionally require Hugging Face credentials.

Install vector-retrieval dependencies first:

```bash
uv sync --extra dev --extra rag-vector
```

Common commands:

```bash
# Lowest-dependency mode; --rag-model is not used in this mode
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine bm25

# Hybrid retrieval with the default embedding model
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid \
  --rag-model intfloat/multilingual-e5-small \
  --rag-db-dir .local/chroma_rag

# Use a new index directory when changing the embedding model
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid \
  --rag-model BAAI/bge-m3 \
  --rag-db-dir .local/chroma_bge_m3
```

Use a new `--rag-db-dir` when changing models. Different embedding models may
have different dimensions and vector spaces; reusing an old Chroma directory
can cause dimension conflicts or mix incompatible indexes. CLI options override
environment variables. To avoid repeating long options, set them in `.env`:

`BAAI/bge-m3` above is only a comparison example. It is larger and heavier on
first download and CPU inference; the default
`intfloat/multilingual-e5-small` is better for first runs and classroom demos.

```dotenv
RAG_ENGINE=hybrid
RAG_EMBEDDING_MODEL=intfloat/multilingual-e5-small
RAG_DB_DIR=.local/chroma_rag
```

For a first project demo, use the defaults and pass `--rag-model` and a new
`--rag-db-dir` only when comparing embedding models.
`sentence-transformers` converts `query:` and `passage:` into local vectors;
Chroma `PersistentClient` stores vectors, source text, and metadata in
`.local/chroma_rag`. The first run downloads the model and works on CPU; no
embedding API key or GPU is required. `tests/test_rag.py` covers BM25, RRF, and
degradation. `tests/test_rag_vector.py` is an optional smoke test that downloads
the model:

```bash
PYTHONPATH=src uv run pytest -q -m rag_vector
```

To replace the embedding model or vector database, implement the same
`travel_agent.rag.retriever.KnowledgeRetriever.search()` interface. The
workflow, Pydantic evidence, citations, and Planner do not change. See
[docs/rag.md](docs/rag.md) for the complete design.

### Optional MCP read-only server

MCP is a protocol boundary, not a second copy of the travel business logic. The
server reuses the existing geocoding, weather, and RAG implementations and
exposes read-only tools:

```bash
uv sync --extra mcp
PYTHONPATH=src TRAVEL_KNOWLEDGE_DIR=knowledge \
  uv run python -m travel_agent.mcp_server
```

The default CLI does not depend on MCP. The command is for MCP Inspector or
another MCP-capable Agent client. Booking, payment, and write operations are
not exposed.

Run a direct-call comparison with the local MCP client:

```bash
PYTHONPATH=src uv run python scripts/mcp_smoke.py \
  --destination Tokyo --query "Tokyo Tower"
```

The script first calls the Python tools directly, then starts the local STDIO
MCP server and calls the same `weather` and `search_knowledge` tools. It compares
tool discovery, return status, knowledge chunk IDs, and per-stage latency.
Diagnostic logs go to stderr and the JSON report goes to stdout so the MCP
JSON-RPC channel is not polluted.

## Important limits

- OSRM output is a driving-road estimate, not public-transit time.
- Open-Meteo forecasts only work inside its supported forecast window.
- Photon and Overpass public instances may throttle or temporarily fail.
- A generated Google Travel link is not evidence that inventory or prices were read.
- No booking or payment action is performed.
