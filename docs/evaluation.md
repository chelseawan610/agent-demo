# Evaluation and Memory Contract

这个项目把评估分成三层，避免“让另一个模型看一段漂亮文字”成为唯一标准。

## 1. Deterministic contract checks

`travel_agent.quality.check_workflow_invariants()` 检查程序必须保证的事实：

- 需求中的目的地、天数、预算和用户偏好没有被 Planner 改写。
- SearchPlan 中声明的航班、酒店和餐饮查询各执行一次，用户没有请求的服务不调用。
- 最终航班、酒店、餐饮、天气和路线来自 `SearchResults`，而不是模型自行编造。
- 活动日期和路线日期在旅行天数内；已确认关闭的地点不能进入日程。
- 公共交通模式必须保留为 `transit`，即使 OpenTripPlanner adapter 返回
  `unavailable`，也不能退回成 OSRM 驾车时间。
- 当请求公共交通时，必须逐天分别记录 `optimize_day_route` 和
  `optimize_transit_route`；只调用两次驾车工具不能算通过。

这类检查不需要 LLM，失败时应直接阻止发布或标记为失败。

## 2. Golden and held-out cases

`evals/evaluate.py` 测试 Intake 的结构化字段；`evals/evaluate_end_to_end.py` 使用
固定工具证据测试 Intake 到 TripPlan 的完整模型路径；`evals/evaluate_workflow.py`
完全不调用模型和网络，测试 SearchPlan、adapter 边界和降级。每个报告现在包含：

- `pass_rate` / `failure_rate`
- `p95_elapsed_ms`
- 按失败前缀统计的 `failure_categories`
- 每个 case 的失败原因和阶段

LLM judge 可以作为开放式“可读性、相关性、个性化”补充指标，但不能决定日期、价格、
数据来源或工具是否真的执行。那些必须由 typed evidence 和 deterministic checks 决定。

交互式追问和离线评估是两个不同场景：CLI 在 Intake 返回硬缺失字段时继续追问，最多
三轮；`--offline` 接收已经结构化的 `TripRequest`，没有自然语言输入，因此只验证
“缺字段时不进入工具执行”。当前 workflow eval 还包含一个 RAG evidence case，
用于验证检索结果被保留为 typed evidence。项目的 LLM judge 是可选的 `--review` 阶段，使用
MAF 的 `PlannerExecutor -> ReviewerExecutor`，不会自动修改计划，也没有纳入离线
10-case 的 pass/fail。

时长也在工具执行前固定：明确的 `days`/`nights` 优先，其次使用起止日期推导；如果
用户没有说明，Intake Agent 结合目的地、偏好、服务范围和旅行节奏直接推荐 `days`，
同时输出受限的 `duration_profile` 作为可审计解释。因此改变时长不会导致工具执行完
以后重新搜索；只有模型没有给出任何推荐时，才使用按服务范围区分的最小保护性窗口。

## 3. Memory contract

`JsonMemoryStore` 是本地、可选的长期偏好存储，不是对话数据库。它只保存用户明确表达
的偏好，带 schema version、更新时间和来源标记，不保存原始 prompt、模型推理、工具结果、
日期或预算。`--forget-memory` 删除一个用户的全部偏好，`forget_preferences()` 可删除
其中一部分。当前请求优先于历史偏好，memory 只作为 Planner 的建议上下文。

这对应微软课程里的 context engineering 和 memory 原则：保存少量有用信息、能够更新和
删除，并保留足够的 trace 说明本次调用实际用了哪些上下文。生产环境可以把这个接口替换
成数据库或 Mem0/Cognee，但 workflow 不需要改变。

## Commands

```bash
PYTHONPATH=src uv run python -m evals.evaluate_workflow
PYTHONPATH=src uv run pytest -q
PYTHONPATH=src uv run pytest -q -m live
```
