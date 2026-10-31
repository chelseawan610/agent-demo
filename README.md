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

以下为中文详细说明。

一个基于 Microsoft Agent Framework（MAF）的单 Agent 旅行规划项目。模型负责理解自然语言和组织表达，Python workflow 负责确定工具调用、日期边界、失败降级和事实约束。

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

这个实现采用微软课程中的两类模式：

- [Tool Use Design Pattern](https://github.com/microsoft/ai-agents-for-beginners/blob/main/04-tool-use/README.md)：工具具有明确参数、类型和错误状态。
- [Planning Design](https://github.com/microsoft/ai-agents-for-beginners/blob/main/07-planning-design/README.md)：先得到结构化计划，再按依赖顺序执行。

Planner 没有直接工具权限。所有工具调用只能来自 `SearchPlan`，因此不会出现模型无限循环调用或擅自改变日期的问题。

这不是“只把 prompt 写得更长”的实现：Intake 和 Planner 都通过 MAF 的
`response_format` 进入 Pydantic 边界。正常 Planner 只输出很小的
`TripNarrative`（摘要和每日标题）；程序随后从 `TripRequest` 与 `SearchResults`
确定性组装完整 `TripPlan`。航班、酒店、天气、地点、路线、来源和活动日期不允许由
模型重写。模型负责语义理解和表达，程序负责边界、执行、失败状态和可审计记录。

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

1. Intake 区分具体地点与规划约束：`place_name`/`place_type` 用于地点，`planning_hint` 用于室内外搭配等偏好。Photon 只解析具体地点，并用目的地坐标、规范名称和 OSM 类型核验候选；同名餐厅不能替代海港。
2. 程序按候选数量和旅行天数计算每日容量，通常为 1--3 个地点，而不是固定每天三个。
3. 如果 OSM 提供简单的 `opening_hours` 规则，程序会把计划日期为 `closed` 的地点排除；缺少或暂时无法解析的规则保留为 `unknown`，并在输出中明确标注“开放时间未核验”。
4. 已解析的地点按天分配，并尽量让同一天的地点靠近；往返行程会给最后一天较小容量，因为返程航班时间尚未选定。

`venue_mix=balanced` 会让 Overpass 查询包含公园、花园等室外候选，并在截断结果前
交替保留已知室内/室外候选；每天分配也使用这个约束。它不会把“室内外结合”造为
景点。类型未知仍保持 unknown；候选不足时，最终计划标记 partial 并说明偏好尚未满足。
具体地点要求核验完整名称（Unicode/大小写/空白规范化后），不使用名称片段猜测。
遇到不支持的别名、类型不匹配或远距离同名候选会返回 unavailable，可能降低召回率，
但不会接受相似商家。海港等区域的中心点保留在 JSON 中，同时标记
`route_eligible=false`，不会被当作已核验入口传入路线计算。
完整示例及修改位置见 [docs/preference-resolution.md](docs/preference-resolution.md)。

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
输出的是候选餐厅，不会编造价格、评分、菜单或预订状态。用户明确给出菜系偏好时，
程序只保留 OSM `cuisine` 标签与 provider-friendly 查询词匹配的候选；没有标签或
没有匹配项就返回空列表并说明原因，不会把普通咖啡店冒充成寿司或拉面店。普通旅行
请求不会因为餐饮工具而增加网络调用。

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

最简单的 Markdown 运行方式（普通用户只需要这一条）：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli
```

CLI 启动时会把标准输入、输出和错误流统一配置为 UTF-8，并在宿主终端拒绝重配置
时安全降级，因此中文输入不会因为本地 locale 不一致直接触发编码异常。目的地也有
一层很小的实体保护：用户明确写出首尔、东京等已知城市时，模型不能把它静默改成另一个
城市；多个城市表达不清时会回到追问，而不会先调用错误的工具。

Intake 的每轮追问是无状态的：下一轮只重新读取用户原文和本轮补充，程序再合并已经确认
的字段，不把上一轮模型 JSON 当作新的用户事实。这样可以避免一次错误解析通过 MAF session
或追问上下文被重复强化。指令中的城市示例也使用占位词，避免示例城市成为模型的默认答案。

如果需要给程序或评测读取 JSON：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --output json
```

Example request:

```text
我从上海出发，2026年10月1日去东京玩三天，总预算10000元，
我想去东京塔，还喜欢动漫。
```

See [docs/demo.md](docs/demo.md) for the expected tool trace and JSON/Markdown shapes.

`--debug` shows `TripRequest`, `SearchPlan`, `ToolCallRecord`, and deterministic
workflow checks. It never prints the LLM API key:

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --debug --output json
```

### CLI 参数怎么选

普通运行不需要记住全部参数。当前参数按使用场景分组：

| 参数组 | 参数 | 用途 |
| --- | --- | --- |
| 常用 | `--output` | 选择 Markdown 或 JSON；默认 Markdown |
| 常用 | `--mode` | 模型结构化输出策略；默认 `auto`，通常不需要改 |
| 调试评测 | `--debug` | 查看解析后的请求、搜索计划、工具事件和检查结果 |
| 调试评测 | `--debug-prompts` | 查看发给模型的动态消息；只用于教学和排错 |
| 调试评测 | `--offline`、`--request-json` | 跳过 LLM，使用结构化请求测试工具 workflow |
| 调试评测 | `--review` | 计划完成后额外运行一次只读 MAF Reviewer，会增加一次模型调用 |
| 模型记录 | `--model` | 单次覆盖 `.env` 中的主模型，用于模型比较 |
| 模型记录 | `--fallback-model` | 主模型发生超时、限流或 5xx 时使用备用模型 |
| 模型记录 | `--log-file` | 把一次运行的脱敏记录追加到 JSONL |
| 模型记录 | `--log-input`、`--log-content` | 明确要求时保存原始输入或有限模型输出；都需要 `--log-file` |
| 本地状态 | `--memory-file` | 开启本地长期偏好 Memory；不传则不保存长期偏好 |
| 本地状态 | `--user-id` | Memory 的用户键，默认是 `local-user` |
| 本地状态 | `--forget-memory` | 删除当前用户的偏好；必须和 `--memory-file` 一起使用 |
| 本地状态 | `--checkpoint-file`、`--resume` | 保存或恢复本地 workflow 阶段 |
| RAG | `--knowledge-dir` | 指定 Markdown 知识库目录，默认 `knowledge/` |
| RAG | `--no-rag` | 关闭本次知识检索，用于对照实验 |
| RAG | `--rag-engine` | 选择 `hybrid`、`vector` 或 `bm25` |
| RAG | `--rag-model`、`--rag-db-dir` | 配置向量 embedding 模型和 Chroma 目录 |

这些参数都保留，是因为它们分别支持正常运行、故障定位、评测、Memory、
Checkpoint 和 RAG 实验；但只有 `--output` 是普通用户经常需要的参数。
`--mode`、模型、日志、review、checkpoint 和 RAG 引擎属于开发者参数，
不应该要求最终用户理解。

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

显式保存用户偏好（可选）。默认 `user_id` 是 `local-user`：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --output markdown
```

如果多个用户在同一台电脑、同一个 Memory 文件中运行，需要用 `--user-id`
覆盖默认值，避免偏好混在一起：

```bash
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --user-id chelsea

PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --memory-file .local/memory.json \
  --user-id alice
```

`--user-id` 只是本地偏好分区键，不是登录认证。终端没有账户系统；真正的
Web/API 应用应该由后端从登录会话得到用户 ID。`.env` 和 `.local/` 已加入
`.gitignore`，不要把 API key、Memory、运行日志或 checkpoint 一起压缩分享。

memory 只保存用户明确说出的旅行偏好，例如“喜欢动漫”；Intake 的本轮追问使用
一个 MAF `AgentSession` 保存短期上下文。它不把整段对话写入长期偏好文件，
也不会自动训练模型。当前请求和历史偏好冲突时，当前请求优先。删除本地偏好：
`--forget-memory --memory-file .local/memory.json --user-id local-user`。

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

完整字段说明、免费离线复现命令和可提交到 GitHub 的 JSONL 样例见
[Run logging and debugging](docs/run-logging.md)。

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
  the menu and write any combination such as “机票和酒店” directly.
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
- Dates such as `十月3号`, `10.3`, and `2026.10.3` are normalized to ISO dates. If the year is omitted, the nearest upcoming occurrence relative to the runtime date is used.
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

The current local result is `254 passed, 14 deselected`; the no-key public-API
smoke run is `7 passed, 2 skipped` when a provider is rate-limited or
temporarily unavailable. Public APIs have no SLA, so the default CI-style suite
remains offline.

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
BM25；`vector` 模式则明确报错。检索还会带上目的地 scope：通用章节可以命中，
其他章节只有在标题/正文与当前目的地匹配时才会进入上下文，避免“首尔请求命中东京
知识”的跨目的地污染。

#### RAG 参数和模型要求

RAG 的参数分工如下：

| 参数 | 作用 | 默认值 | 什么时候需要改 |
| --- | --- | --- | --- |
| `--knowledge-dir` | Markdown 知识库目录 | `knowledge` | 资料放在其他目录时 |
| `--rag-engine` | 检索方式：`bm25`、`vector` 或 `hybrid` | `hybrid` | 零额外依赖选 `bm25`；使用 embedding 时选 `vector`/`hybrid` |
| `--rag-model` | **embedding 模型**，把查询和文档转成向量 | `intfloat/multilingual-e5-small` | 想比较语义检索模型时 |
| `--rag-db-dir` | Chroma 本地向量索引目录 | `.local/chroma_rag` | 换 embedding 模型或隔离知识库时 |
| `--no-rag` | 完全关闭知识检索 | 关闭 | 调试模型/工具链路时 |

`--rag-model` 不是聊天模型参数。它必须是能被
`sentence-transformers.SentenceTransformer` 加载的 embedding 模型名称，或者本地模型目录；
不能填写 OpenRouter/聊天模型名，例如 `deepseek-chat`、`qwen-*`、`gpt-*`。聊天模型由
`--model` 或 `LLM_MODEL` 负责，embedding 模型由 `--rag-model` 或
`RAG_EMBEDDING_MODEL` 负责，两者是不同的模型用途。

默认模型适合这个小型中英文旅行知识库。第一次使用 `vector`/`hybrid` 时，
`sentence-transformers` 会从 Hugging Face 下载模型并缓存到本机；因此首次运行需要网络、
磁盘空间和一些等待时间，但不需要 API key，也不要求 GPU。任意模型名都不会自动可用：
模型必须公开可下载、与 Sentence Transformers 兼容，并且本机有足够资源。私有或 gated 模型
还需要相应的 Hugging Face 登录权限。

先安装向量检索依赖：

```bash
uv sync --extra dev --extra rag-vector
```

常用运行方式：

```bash
# 最稳妥的零额外依赖模式；--rag-model 在此模式不会参与检索
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine bm25

# 使用默认 embedding 模型的混合检索
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid \
  --rag-model intfloat/multilingual-e5-small \
  --rag-db-dir .local/chroma_rag

# 更换 embedding 模型时使用新的索引目录
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid \
  --rag-model BAAI/bge-m3 \
  --rag-db-dir .local/chroma_bge_m3
```

换模型时建议换 `--rag-db-dir`。因为不同 embedding 模型可能有不同的向量维度和空间，
复用旧 Chroma 目录可能导致维度冲突或混用旧索引。CLI 参数优先级高于环境变量；不想每次
输入长参数时，可以在 `.env` 中设置：

上面的 `BAAI/bge-m3` 只是对照实验示例，模型更大、首次下载和 CPU 推理更重；当前项目
默认的 `intfloat/multilingual-e5-small` 更适合先跑通和课堂演示。

```dotenv
RAG_ENGINE=hybrid
RAG_EMBEDDING_MODEL=intfloat/multilingual-e5-small
RAG_DB_DIR=.local/chroma_rag
```

第一次展示项目时推荐直接使用默认配置；只有要比较 embedding 模型时才显式传
`--rag-model` 和新的 `--rag-db-dir`。
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
