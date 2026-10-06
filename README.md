# Travel Planning Agent

一个基于 Microsoft Agent Framework（MAF）的单 Agent 旅行规划项目。模型负责理解自然语言和组织表达，Python workflow 负责确定工具调用、日期边界、失败降级和事实约束。

## Architecture

```text
User input
  -> Intake Agent
  -> TripRequest (Pydantic)
  -> SearchPlan (deterministic)
  -> public API adapters / demo inventory / search-link tools
  -> SearchResults (typed evidence)
  -> Planner Agent
  -> deterministic evidence constraints
  -> TripPlan
  -> JSON or Markdown

Optional quality path:
  PlannerExecutor -> ReviewerExecutor (MAF WorkflowBuilder)
```

这个实现采用微软课程中的两类模式：

- [Tool Use Design Pattern](https://github.com/microsoft/ai-agents-for-beginners/blob/main/04-tool-use/README.md)：工具具有明确参数、类型和错误状态。
- [Planning Design](https://github.com/microsoft/ai-agents-for-beginners/blob/main/07-planning-design/README.md)：先得到结构化计划，再按依赖顺序执行。

Planner 没有直接工具权限。所有工具调用只能来自 `SearchPlan`，因此不会出现模型无限循环调用或擅自改变日期的问题。

这不是“只把 prompt 写得更长”的实现：模型输出先经过 MAF 的
`response_format` 结构化响应，再由 Pydantic 校验；程序随后把日期、服务范围、
预算、工具事实和活动日期重新约束一次。模型负责语义理解和表达，程序负责边界、
执行、失败状态和可审计记录。

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

活动分配是一个确定性步骤，不交给 Planner 自由决定。流程是：

1. Photon 先尝试解析用户明确点名的地点；Overpass 获取目的地附近的景点候选。
2. 程序按候选数量和旅行天数计算每日容量，通常为 1--3 个地点，而不是固定每天三个。
3. 如果 OSM 提供简单的 `opening_hours` 规则，程序会把计划日期为 `closed` 的地点排除；缺少或暂时无法解析的规则保留为 `unknown`，并在输出中明确标注“开放时间未核验”。
4. 已解析的地点按天分配，并尽量让同一天的地点靠近；往返行程会给最后一天较小容量，因为返程航班时间尚未选定。

目前只安全解析常见的每周规则，例如 `Mo-Fr 10:00-18:00`、`Mo-Su off` 和 `24/7`。
节假日、临时闭馆和复杂的 OSM 规则不会被猜测，仍需查看官网。活动候选会保留
`opening_hours`、`opening_status`、`website`、`estimated_visit_minutes` 等字段，
所以 JSON 可以继续被前端或后续评估程序消费。

这层逻辑刻意不把天气硬编码成“下雨就删景点”。天气是一个确定性排序信号：当预报的
降雨概率达到 60% 且地点被 OSM 明确标成 `outdoor` 时，程序优先把它排到其它较干燥的
旅行日；如果没有合适的日期，地点仍会保留，不会被偷偷删除。用户明确点名的地点也
不会被天气过滤。地点现在还会保留 `venue_type`（`indoor`、`outdoor`、`mixed` 或
`unknown`），但只有 OSM 明确提供证据时才标记，不用模型猜测。

### Dining and transport modes

餐饮不是另一个 Agent，而是一个可选的 `food` service。用户明确说“推荐餐厅、吃饭、
美食或寿司”时，Intake 才把它加入 `requested_services`，SearchPlan 才会执行
`search_food`。这个工具复用 Overpass 的地点、营业时间、官网和 OSM attribution，
输出的是候选餐厅，不会编造价格、评分、菜单或预订状态。普通旅行请求不会因为餐饮
工具而增加网络调用。

路线默认使用 OSRM 的驾车估算。用户明确要求地铁或公交时，流程会保留驾车路线，并
额外加入 `transit` 路线模式，同时调用 `optimize_transit_route`。当前它会诚实返回
`not_configured`，因为免费的 OSRM 公共实例不是公交规划器；真正的公交路线需要
带 GTFS 数据的 OpenTripPlanner adapter。这样接口已经保留，未来替换适配器不会改
SearchPlan 或 TripPlan，也不会把驾车时间冒充公交时间。`transport_modes` 会明确记录
`["driving", "transit"]`，所以一类路线失败不会抹掉另一类成功结果。

### When to split agents

当前不应为景点、餐厅和路线各创建一个 Agent：它们是不同工具和数据源，不是不同的
推理目标。额外 Agent 会带来新的上下文传递、结构化输出、重试、日志和成本，反而让
这个 demo 难以验证。

现在的合理边界是：`IntakeAgent` 负责需求结构化，确定性 Search workflow 负责工具，
`TravelPlanner` 负责叙述，`Reviewer` 负责可选复核。只有当一个子任务拥有独立目标、
独立工具集合、可以并行执行，并且需要单独评估时，才考虑拆成 `TransportAgent`、
`ExperienceAgent` 等。当前代码已经把这些边界做成了 SearchPlan 和 adapter，后续
拆 Agent 时可以复用，而不需要重写工具层。

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
```

The travel-data APIs need no API keys.

`deepseek/deepseek-v4.1-flash` is the current default because it passed the
expanded intake golden set reliably. `qwen/qwen3.7-flash` remains an inexpensive
comparison model in the evaluation command. The project still validates every response with
Pydantic and has a formatter fallback, so a model is not trusted merely
because it advertises JSON support. `none` disables hidden reasoning by
default so a small response budget is not consumed before JSON is emitted.
You can override it with another OpenAI-compatible model without changing
Python code; raise reasoning to `low` or `medium` only after an evaluation
shows a real quality gain. Some models may be unavailable in a particular
OpenRouter region; the evaluation command below is the check before changing
the default.

## Run

Markdown output:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --mode auto \
  --output markdown \
  --debug
```

Machine-readable JSON:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --mode auto \
  --output json \
  --debug
```

Example request:

```text
我从上海出发，2026年10月1日去东京玩三天，总预算10000元，
我想去东京塔，还喜欢动漫。
```

See [docs/demo.md](docs/demo.md) for the expected tool trace and JSON/Markdown shapes.

`--debug` shows `TripRequest`, `SearchPlan`, and `ToolCallRecord`. It never prints the LLM API key.

Use `--debug-prompts` when teaching or debugging model input. It prints the
stage (`intake`, `planner.direct`, `formatter.structured`, and so on), the
dynamic user message, and the `response_format` option. The agent's static
system instructions remain in `src/travel_agent/agents/`; travel tools receive
typed Python arguments from `SearchPlan`, not an unconstrained tool loop.
Use `--model MODEL_ID` to compare a provider model for one run without editing
`.env`, for example `--model qwen/qwen3.7-flash`.

显式保存用户偏好（可选）：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --user-id chelsea \
  --output markdown
```

memory 只保存用户明确说出的旅行偏好，例如“喜欢动漫”；Intake 的本轮追问使用
一个 MAF `AgentSession` 保存短期上下文。它不把整段对话写入长期偏好文件，
也不会自动训练模型。当前请求和历史偏好冲突时，当前请求优先。删除本地偏好：
`--forget-memory --memory-file .local/memory.json --user-id chelsea`。

启用一次只读质量复核：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --review --output json
```

这会增加一次 `TripPlanReviewer` 模型调用，并通过 MAF `WorkflowBuilder` 的
`PlannerExecutor -> ReviewerExecutor` 边传递 typed plan。Reviewer 只能返回
`ReviewResult`（pass / needs_revision、问题、优点和检查项），不能改写航班、
价格、日期或工具事实；复核服务失败时，原计划仍然保留并在 notes 中说明。

保存和恢复本地 workflow 状态：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --checkpoint-file .local/checkpoint.json --output json

PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --checkpoint-file .local/checkpoint.json --resume --output markdown
```

checkpoint 保存 `intake`、`searched`、`complete` 三个应用层阶段。`complete` 可以
直接无模型重放；`searched` 会跳过重新解析和外部工具调用，只恢复 Planner（以及可选
Reviewer），并保留原始用户需求。它仍然不是服务端队列，也不是 MAF 的分布式持久化。
MAF WorkflowBuilder 当前用于 Planner→Reviewer 交接，应用层 checkpoint 继续保存完整旅行事实。

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

Use `--log-content` only when you explicitly need bounded LLM output previews
inside the trajectory. It stores at most 4,000 characters per model event;
prompts remain excluded. Set `LLM_INPUT_PRICE_USD_PER_MILLION` and
`LLM_OUTPUT_PRICE_USD_PER_MILLION` in `.env` to calculate an estimated USD cost.
The run-level `metrics` object reports token totals, per-stage and per-provider
P95 latency, success rates, provider error rate, and estimated cost. Leave
prices empty when the gateway's current price is unknown; cost then remains
`null` rather than pretending to be exact.

### Run without an LLM

Natural-language intake cannot be done by the CLI alone: it requires either a
hosted model or a local model. To demonstrate the rest of the workflow without
an LLM key, pass a normalized `TripRequest` JSON to `--offline`:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --offline \
  --output markdown \
  --debug \
  --request-json '{"requested_services":["flights","hotels","activities","guide"],"origin":"Shanghai","destination":"Tokyo","start_date":"2026-10-01","days":3,"preferences":["Tokyo Tower"],"preference_details":[{"label":"Tokyo Tower","search_query":"Tokyo Tower Tokyo","kind":"place"}]}'
```

Offline mode still calls the free geocoding, weather, place, attraction, and
routing providers. It does not call an LLM and therefore uses deterministic
day titles; it is a workflow/tool smoke test, not a natural-language agent.
Use `--request-json @path/to/request.json` when the JSON is too long for a
shell command.

## Clarification behavior

- A full plan requires origin, destination, and departure date.
- Flights-only requires origin, destination, and departure date; without return information it stays one-way.
- Hotels-only requires destination and check-in date.
- Guide/activities requires only destination.
- Explicit days, nights, and return dates always win. When duration is omitted,
  Intake recommends `days` from the destination, user preferences, requested
  services, and implied pace before tools run. `duration_profile` records the
  model's bounded rationale (`day_trip`, `weekend`, `short_break`, `standard`,
  or `extended`). The application only uses a small two-day complete-plan
  fallback when the model supplies neither a recommendation nor a profile.
- Dates such as `十月3号`, `10.3`, and `2026.10.3` are normalized to ISO dates. If the year is omitted, the nearest upcoming occurrence relative to the runtime date is used.
- The CLI asks at most three follow-up rounds. Every round merges earlier answers, so supplying `Shanghai` once cannot be lost later.
- If required fields are still missing, it returns `needs_clarification` and calls no travel-data tools.
- Required fields are computed by `TripRequest.required_missing_fields()`, not by
  planner prose: destination is always required; flights add origin and
  departure date; hotel-only adds check-in date. Duration, budget, return date,
  transport mode, and preferences are optional and are defaulted or left empty.
- Interactive CLI clarification keeps asking while a hard field is still
  missing, up to three rounds. `--offline` is different: it accepts an already
  structured `TripRequest` and deliberately cannot ask natural-language
  follow-up questions.

## Failure behavior

Each external adapter returns a typed `ToolResult` with `ok`, `unavailable`, or `skipped`. A timeout or rate limit only removes that provider's data; successful data from other providers remains. Mock data is never substituted for a failed real API.

Weather, attraction, and route facts are copied from `SearchResults` into `TripPlan` after the model responds. The model can write summaries and day titles, but cannot move an attraction to another day or replace tool evidence.

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

The current local result is `111 passed, 13 deselected`; the explicit public-API
smoke run is `8 passed`. Public APIs can still be throttled or temporarily
unavailable, so the default CI-style suite remains offline.

新增的本地 contract tests 还覆盖：JSON memory 的读写、损坏文件降级、偏好边界、
checkpoint 往返序列化，以及 reviewer 的 prompt 和 `ReviewResult` 解析。

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

`evals/evaluate.py` 只比较 Intake 阶段：同一组中文/英文 golden 和 held-out
输入分别交给 Qwen、DeepSeek，检查 `TripRequest` 的服务范围、日期、天数、预算、
偏好和 ready 状态。`evals/evaluate_end_to_end.py` 再使用固定的旅行工具证据，
检查完整的 `Intake → SearchPlan → SearchResults → Planner → TripPlan`。
因此 Qwen 不是“接上就算成功”：它必须在相同 case 上通过结构化字段和 workflow
invariants。模型输出的自然语言好不好另算；事实正确性由程序检查，不交给另一个
LLM 自说自话地判定。

课程内容和当前代码的对应关系：

| 主题 | 当前实现 | 还有什么可以做 |
| --- | --- | --- |
| Tool Use | typed adapters、`ToolResult`、调用记录、部分降级 | 接入正式航班/酒店供应商 adapter |
| Planning | `TripRequest → SearchPlan`，固定依赖顺序；质量路径使用 MAF `WorkflowBuilder` | 将更长的执行状态映射到 MAF durable workflow |
| Context engineering | 原始需求、typed state、证据包、有限 memory | 压缩长对话并做 context 预算监控 |
| Memory | 可选本地 JSON 偏好记忆 | 多账户数据库或 MAF/Mem0 等长期存储 |
| Metacognition | 可选只读 reviewer + deterministic checks | 线上反馈、回归集和人工抽检闭环 |
| Production/eval | JSONL trace、latency/status、golden/held-out/E2E | 成本、token、成功率 dashboard |
| Multi-agent | Intake、Planner、Formatter、Reviewer 是职责边界；Planner→Reviewer 有 MAF edge | 只有出现独立专业权限和交接需求时再拆 specialists |

目前没有必要再添加订票、支付、数据库、服务器或自由循环 multi-agent；这些会
扩大风险和成本，不能替代当前还需要打磨的评估与真实供应商边界。

### Local RAG

默认使用 `hybrid` RAG：Markdown 按标题和段落切块 → BM25 lexical 检索 +
`intfloat/multilingual-e5-small` embedding 检索 → RRF 合并 → 返回带 `source`、
`section`、`score` 的 `KnowledgeHit` → Planner 读取证据 → 程序把 citation 写入
`TripPlan.knowledge_sources`。知识库只用于稳定的背景说明，不代替天气、地点、
营业时间或价格工具，也不会被当作实时事实。向量依赖不可用时，`hybrid` 自动退回
BM25；`vector` 模式则明确报错。

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --knowledge-dir knowledge --debug --output markdown

PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --no-rag --output markdown
```

只使用 BM25 不需要额外安装。要启用持久化向量库：

```bash
uv sync --extra dev --extra rag-vector
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid \
  --rag-model intfloat/multilingual-e5-small \
  --rag-db-dir .local/chroma_rag --debug --output markdown
```

`sentence-transformers` 在本地把 `query:` / `passage:` 转成向量，Chroma
`PersistentClient` 把向量、原文和 metadata 保存在 `.local/chroma_rag`。首次运行会
下载模型，CPU 可运行；不需要 embedding API key 或 GPU。`tests/test_rag.py` 覆盖 BM25、
RRF 和降级，`tests/test_rag_vector.py` 是需要下载模型的可选 smoke test：

```bash
PYTHONPATH=src uv run pytest -q -m rag_vector
```

替换 embedding 或向量数据库时，只需实现同一个
`travel_agent.rag.retriever.KnowledgeRetriever.search()` 接口，workflow、Pydantic
证据、citation 和 Planner 不需要改动。完整设计见 [docs/rag.md](docs/rag.md)。

### Optional MCP read-only server

MCP 是协议入口，不是另一套旅行业务逻辑。服务器复用现有地理编码、天气和 RAG
实现，只暴露只读工具：

```bash
uv sync --extra mcp
PYTHONPATH=src TRAVEL_KNOWLEDGE_DIR=knowledge \
  uv run python -m travel_agent.mcp_server
```

默认 CLI 不依赖 MCP；这条命令用于 MCP Inspector 或其他支持 MCP 的 Agent 客户端。
当前没有暴露订票、支付或写入型工具。

可以用本地 MCP Client 做一次直调对照实验：

```bash
PYTHONPATH=src uv run python scripts/mcp_smoke.py \
  --destination Tokyo --query "Tokyo Tower"
```

脚本会先直接调用 Python 工具，再启动本地 STDIO MCP Server，调用同名的
`weather` 和 `search_knowledge`，比较工具发现结果、返回状态、知识 chunk ID 和
分阶段延迟。工具的诊断日志写到 stderr，JSON 报告写到 stdout，避免污染 MCP 的
JSON-RPC 通道。

## Important limits

- OSRM output is a driving-road estimate, not public-transit time.
- Open-Meteo forecasts only work inside its supported forecast window.
- Photon and Overpass public instances may throttle or temporarily fail.
- A generated Google Travel link is not evidence that inventory or prices were read.
- No booking or payment action is performed.
