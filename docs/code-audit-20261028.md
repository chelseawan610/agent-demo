# Code audit — 2026-10-28

## Scope

This audit reviewed the uncommitted changes on `feature/single-agent` after
the Kuala Lumpur / Petronas Twin Towers replay. It covered intake, merge and
clarification state, SearchPlan execution, place identity, activity assignment,
RAG retrieval, follow-up rollback, output assembly, logging, tests, and the
optional MCP boundary.

## Why the old place check looked mechanical

The Intake model emits three different things:

* `label`: the user's wording, for example `双子塔`;
* `place_name`: a canonical provider-facing entity name, for example
  `Petronas Twin Towers`;
* `search_query`: the query sent to Photon, for example
  `Petronas Twin Towers Kuala Lumpur`.

The resolver must not treat the nearest Photon result as the requested place.
That would allow a restaurant, hotel, or viewpoint containing the same word to
replace a landmark. The previous implementation made exact normalized-name
intersection the main gate, so Photon’s official shorter name `Petronas Towers`
was rejected even though its type and coordinates were correct.

The current resolver keeps the independent checks but ranks provider evidence:

1. exact provider name or provider alias;
2. a conservative official abbreviation (at least two tokens, at least two
   thirds of the expected name, and no extra tokens);
3. destination distance and OSM type checks;
4. ambiguity detection before selecting a result.

Names and aliases come from the provider response (`name`, `alt_name`,
`official_name`, `short_name`, multilingual `name:*` fields); no landmark is
hard-coded. An unresolved synonym still becomes `unavailable` rather than a
made-up place. The final activity is identified by the provider ID, not by
comparing the user's words again.

## Other corrections in this audit

* Photon and Overpass now normalize OSM IDs to the same `node:`, `way:`, and
  `relation:` form, so the same place de-duplicates across providers.
* A resolved named place is not automatically called “satisfied”. It must be
  in the final activity assignment, and it must be on the requested day when
  `preferred_day` is present.
* Unknown opening hours no longer make a known-closed day eligible. The place
  can use a day whose status is unknown, but not a day proved closed.
* Weather penalties are aligned by calendar date, not by list position.
* BM25 uses global document statistics while applying a destination scope;
  scoped common terms no longer receive invalid negative scores.
* BM25 and vector retrieval skip over chunks that exceed the character budget
  instead of stopping before considering later, smaller chunks. Vector search
  also enforces `top_k` after scope filtering.
* A failed follow-up is transactional: the displayed plan and in-memory state
  roll back together. The failed attempt is still written to the run log with
  its error and attempted evidence, so recovery and audit do not conflict.
* Route invariants now use the activity horizon and do not demand a driving
  call when the user explicitly requested transit only.
* Evaluation fixtures use far-future explicit dates so a correct “past date”
  guard does not turn the test suite stale. This changes fixtures, not the
  runtime date policy.

## Verification

No paid model call was made for this audit.

```text
uv run pytest -q                         253 passed, 14 deselected
PYTHONPATH=src uv run python -m evals.evaluate_workflow
                                          10 / 10 passed
uv run pytest -q -m live                  7 passed, 2 skipped
uvx ruff check src tests scripts --select E9,F
                                          All checks passed
git diff --check                          clean
```

The two live skips were public-provider availability/rate-limit skips (including
the Overpass public-service skip). Public endpoints have no SLA; the workflow
reports that provider as unavailable and keeps successful
Photon/Open-Meteo/OSRM evidence instead of fabricating data.

The offline replay for the Claude case still resolves `Petronas Towers` and
places it on Day 3. Flights and hotels remain local demo adapters, and the
Google Travel URLs remain search links only; no live inventory or booking is
claimed.

## Remaining limits

The model still has to produce a useful `place_name` and `place_type`; the
program validates the provider result but cannot infer an arbitrary synonym
with no provider-side alias. Overpass, Photon, OSRM and Open-Meteo are free
public services with rate limits and occasional timeouts. The transit adapter
is an explicit not-configured boundary. These are reported in `ToolResult`,
`ToolCallRecord`, and the final plan instead of hidden.
