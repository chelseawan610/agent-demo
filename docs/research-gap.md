# 从 "工程 demo" 到 "可以参与科研" 的差距分析

针对目标：让 Huazheng Wang 老师（Oregon State EECS）相信我能参与 agent 科研。
写于 2026-10-27，基于当前仓库状态（7.7k 行 src，169 offline tests）。

---

## 0. 一句话结论

**缺的不是 API，是"实验"。**

这个仓库现在是一个论证充分、边界清晰的工程系统，但它**没有一个可被检验的研究主张**：
没有 baseline、没有 ablation、没有外部 benchmark 数字、没有可复现的随机性控制。
补上真实 Amadeus / Booking 航班酒店 API 对科研价值是 **0 甚至负数**（引入不可复现性和成本）；
补上 baseline 和实验，一周之内就能让这个仓库从"课程作业"变成"可以聊的 research artifact"。

README 第 482 行已经写了这条命令：

```bash
python scripts/analyze_runs.py evals/results/typed-plan.jsonl evals/results/free-loop.jsonl
```

但仓库里 **根本没有 free-loop agent**（`grep -riE "free.?loop|ReAct"` 只有 README 那一行和两处无关注释）。
也就是说：项目最核心的主张——"typed deterministic workflow 优于 free tool loop"——
**被断言了但从未被测量**。这是老师第一眼就会发现的问题，也是性价比最高的修补点。

---

## 1. 现状盘点

### 已经有的（而且是真资产，不要推翻）

| 资产 | 位置 | 为什么对科研有用 |
| --- | --- | --- |
| 分阶段 trajectory（run_id / trace_id / stage / status / latency / tokens / cost） | `app/trace.py`, `schemas/trace.py` | 这是 failure attribution 研究的原始数据格式，别人要花两周搭 |
| 归一化错误分类（`provider_transient` / `provider_contract` / `workflow_dependency` …） | `app/trace.py`, `run_log.py` | 已经是 taxonomy，不用再 parse provider 的异常字符串 |
| 确定性 invariant 检查 + layer/category 标注 | `quality.py` (`check_workflow_invariants`, `_check_layer`, `_check_category`) | **这是一个 verifiable reward function**，不是 LLM judge。老师 COLM'26 的 RLSVR 正好要这个东西 |
| typed 边界（TripRequest → SearchPlan → SearchResults → TripNarrative → TripPlan） | `schemas/` | 做 ablation 时，"去掉某一层"是一个干净的实验变量 |
| 可替换 adapter protocol | `tools/adapters.py` | 做 fault injection 的天然注入点 |
| golden / held-out 分离 | `evals/cases.py` | 已经有防过拟合意识，这点加分 |
| 离线 workflow 回归 + E2E fixture eval | `evals/workflow.py`, `evals/evaluate_end_to_end.py` | 已有骨架，加 baseline 只要换 planner |

### 缺的（按科研可读性排序）

1. **没有 baseline。** 没有 ReAct / free tool-loop 对照组，所有设计主张都无法证伪。
2. **没有外部 benchmark 数字。** 所有 case 是自己写的 23 条，外人没法跟文献比较。
   这是一个 travel planner，而 **TravelPlanner (ICML 2024)** 就是这个领域的标准 benchmark，
   仓库里一次都没提到。
3. **不可复现。** Overpass / Photon / OSRM / Open-Meteo 都是 live 的，同一个 case 两次跑
   结果可能不同；LLM 也没固定 seed/temperature 记录。目前没有 record/replay。
   **没有这一条，上面所有实验都不成立。**
4. **没有失败的主动制造能力。** 失败只能靠"公共 API 碰巧挂掉"偶然观察到，
   无法做受控的 failure study。
5. **没有 reliability 指标。** 只有单次 pass/fail，没有 pass^k（同一 case 跑 k 次全过）。
6. **没有一页纸的结果。** README 700 行，是产品文档不是研究仓库。老师不会读完。

---

## 2. 和 Wang 老师方向的对照

老师近两年的 agent 主线（来自其主页与 2025–2026 论文）：

| 他的工作 | 方向内核 | 这个项目的距离 |
| --- | --- | --- |
| **Which Agent Causes Task Failures and When?** (ICML'25 Spotlight)<br>**Who&When Pro** (NeurIPS'26) | 自动化 **failure attribution**：给定失败 trajectory，判断 who（哪个 agent/组件）+ when（哪一步）+ why | **距离最近。** 你已经有 trace、stage、error taxonomy 和 deterministic checks。而且你能 **自动生成 ground-truth 标签**——Who&When 只有 127 条人工标注，Who&When Pro 要靠"重放成功前缀再注入失败"才造出 12k 条 |
| **Position: Digital Agents Require Unified Agent-Native Computers** (ICML'26) | agent 需要为它设计的、可控的 environment，而不是人类界面 | demo adapter 现在被你当成"缺陷"，其实它就是 agent-native environment 的雏形，只差 seed + 扰动旋钮 |
| **EVOCHAMBER** (NeurIPS'26) / **Live-Evo** / **Divide-Optimize-Merge** (EMNLP'25) | agent 的 test-time 自进化、agentic memory 在线演化、细粒度 prompt 优化 | 你有 memory 和 reviewer，但两者都是静态的，没有 feedback loop |
| **From RLVR to RLSVR** (COLM'26) / **TreeBoN** (EMNLP'25) | 把开放式任务转成 **self-verifiable reward**，用 verifier 做 inference-time 对齐 | `check_workflow_invariants()` 已经是一个 verifier，只差包成 reward + best-of-N |
| **The Ranking Blind Spot: Decision Hijacking** (EMNLP'25)<br>**Reward Poisoning in Linear MDPs** (ICML'26)<br>**AutoDefense** (2024) | 决策系统的对抗脆弱性与防御 | 完全没碰。但你的 typed 边界天然是一个 defense，可以量化 |
| bandits / provable efficiency / exploration | 带理论保证的在线决策 | 距离最远，短期不碰 |

**最佳切入点非常清楚：failure attribution。**
它同时是老师引用最高的 agent 方向、是他今年还在推的方向（NeurIPS'26 新 benchmark），
而且是你这个仓库**唯一拥有别人没有的资产**的方向（自动 ground truth）。

---

## 3. 和"好 agent 项目"的对照

| 参照 | 它有而你没有的 |
| --- | --- |
| **TravelPlanner** (ICML'24, Xie et al.) | 标准化 metric：Delivery Rate / Commonsense Constraint Pass (Micro+Macro) / Hard Constraint Pass (Micro+Macro) / **Final Pass Rate**。GPT-4-Turbo 在 tool-use 模式只有 **0.6%**，后续工作做到 90%+。你的 `WorkflowChecks` 其实就是 constraint pass 的同构物，但没对齐到它的定义上，所以不可比 |
| **τ-bench / τ²-bench** (Sierra) | **pass^k** 可靠性指标：同一任务跑 k 次全部成功才算过。专门暴露"平均能过但不稳定"的 agent。你的确定性 assembly 在这个指标上应该大幅领先 ReAct——这是你最好卖的一个数字，而且几乎零成本 |
| **ReAct / Reflexion** | 它们是所有 agent 论文的默认 baseline。你连 baseline 都没有 |
| **AgentBench / WebArena / GAIA** | 可控 environment + 确定性 checker。你的 environment 是 live public API，不可控 |
| **SWE-bench 系** | `repro.sh` + 固定版本 + 结果表。你的 README 是用法文档，不是结果文档 |

---

## 4. 快速优化清单（按性价比排序）

工作量估计按"每天 3–4 小时"算。全部不需要生产级质量。

### P0-0 · HTTP record/replay cassette —— 1 天，**先决条件**

没有它后面所有数字都不可复现。

- `tools/http.py` 已有 cache 结构，加一层 `TRAVEL_HTTP_CASSETTE=path` 模式：
  `record` 时把 (url, params) → response JSON 写成 fixture，`replay` 时只读 fixture，
  未命中就报错（而不是偷偷走网络）。
- 同时在 run log 里记 `model`、`temperature`、`seed`、cassette hash。
- 产出：同一条命令跑两次 byte-identical。这是审稿人最低要求。

### P0-1 · ReAct free-loop baseline —— 2 天，**最高性价比**

兑现 README 已经写下的承诺，给项目第一个 ablation。

- 新建 `src/travel_agent/agents/react_baseline.py`：把现有 `tools/` 里的函数直接暴露成
  OpenAI function-calling 工具，planner 自由循环，max 15 步，无 SearchPlan、无 typed 组装。
- 复用同一个 `TraceRecorder`、同一组 `check_workflow_invariants()` 打分，同一组 case。
- 再加一个中间档 `typed-plan, no-deterministic-assembly`（让模型写完整 TripPlan），
  这样是 3 个设计点的 ablation，不是 2 个。
- 产出表（这张表就是你给老师看的第一张图）：

  | Design | Final Pass | Grounding Check Pass | Constraint Check Pass | Tool Calls | Dup Calls | P95 Latency | Tokens | $ |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | ReAct free loop | | | | | | | | |
  | Typed plan, LLM assembly | | | | | | | | |
  | Typed plan, deterministic assembly (ours) | | | | | | | | |

### P0-2 · Fault injection + 自动标注的 failure-attribution 数据集 —— 3–4 天，**最对口**

这是能写成 workshop paper 的那一条。

- `tools/faults.py`：一个 `FaultInjector`，在 adapter / HTTP 层按 spec 注入
  `timeout`、`http_429`、`schema_drift`（字段改名）、`empty_result`、
  `coordinate_drift`（坐标偏移 50km）、`stale_opening_hours`、`poisoned_rag_chunk`。
- **注入点 = ground-truth "who"（哪个工具/stage），注入时刻的 trace event index = ground-truth "when"。标签完全免费。**
- 在 cassette 回放上注入 → 得到几百条带金标的失败 trajectory。
- 然后用 LLM 做归因，**直接复刻老师论文的三个协议**：All-at-Once / Step-by-Step / Binary Search，
  报 agent-level accuracy、step-level accuracy、token cost。
- 再加一条他论文里没有的对照组：**用 deterministic `WorkflowChecks` 做归因**
  （规则 vs LLM）。很可能规则在 who 上吊打 LLM、在 why 上输——这就是一个有意思的 finding。
- 顺手复现他 Who&When Pro 的发现：trace 长度 ↑ → step localization accuracy ↓。
  你能通过改 days / requested_services 精确控制 trace 长度，画出 scaling 曲线。

### P0-3 · pass^k 可靠性 —— 0.5 天，**几乎免费**

- 每个 case 跑 k=5 次，报 pass^1 / pass^3 / pass^5。
- 已有 run log 基础设施，只要加个循环和聚合。
- 预期结论：typed workflow 的 pass^5 ≈ pass^1，ReAct 的 pass^5 远低于 pass^1。
  这句话比 169 passed tests 有说服力得多。

### P1-1 · WorkflowChecks 当 verifiable reward + best-of-N —— 1 天

对应老师的 TreeBoN / RLSVR。

- 把 `check_workflow_invariants()` 包成 `reward(plan) ∈ [0,1]`（加权通过率）。
- 跑 N=1 / 2 / 4 的 best-of-N：final pass rate 提升多少，token 涨多少。
- 零新基建，一天出一条 inference-time scaling 曲线。

### P1-2 · 投毒 / 注入鲁棒性 —— 1–2 天（基建与 P0-2 共用）

对应 Ranking Blind Spot / AutoDefense。

- 往 `knowledge/` 塞一条投毒 chunk（"东京塔已永久关闭，建议改去 XX"），
  往 OSM POI `name` 字段塞 prompt injection（"Ignore previous instructions and ..."）。
- 测 ReAct baseline vs typed workflow 的 **attack success rate**。
- 预期结论："typed boundary 本身就是一种 defense"，并给出它挡不住的那一类
  （RAG 内容投毒会穿透，因为 narrative 来自模型）。这个 **negative result 同样值钱**。

### P1-3 · TravelPlanner 对接 —— 3–5 天（可选，但最有说服力）

- 只做 **sole-planning / validation split（180 条）**：写 adapter 把它的 reference information
  映射成你的 `SearchResults`，跑你的 planner，输出它的 plan 格式，用官方 eval script 打分。
- 产出一个能直接跟文献比的 Final Pass Rate。
- 这条的意义不在分数高低，而在于证明"我会把自己的系统放进别人的 benchmark 里受检"——
  这正是科研能力的核心信号。
- 如果时间紧，可以砍掉，用 P0-3 的 pass^k 代替。

### P1-4 · 一页 `RESULTS.md` + `repro.sh` —— 0.5 天

- `RESULTS.md`：研究问题（3 条）→ 实验设置 → 3 张表 → 发现（含 negative result）→ 局限。
  **不超过一页半。**
- `repro.sh`：一条命令跑完所有表。
- README 保持原样，但在最顶上加一行指向 `RESULTS.md`。

---

## 5. 明确不要做的事

- ❌ **不要接 Amadeus / Booking / 真实航班酒店 API。** 它带来 key 管理、配额、成本、
  不可复现，科研收益为零。demo adapter 不是缺陷，把它**改名叫 simulator** 并加上
  seed 和扰动旋钮，它就从"缺的 API"变成"可控 environment"——这恰好是老师 ICML'26
  position paper 的论点。
- ❌ **不要为了"多 agent"而拆 agent。** README 里那段"When to split agents"的论证是对的，
  保留它。真要做 multi-agent failure attribution，用 P0-2 的注入在现有
  Intake/Planner/Reviewer 边界上做就够了。
- ❌ **不要再加 CLI 参数、再加文档、再加 RAG engine。** 现在的问题不是功能少。
- ❌ **不要上 LLM judge 当主指标。** 你现在的立场（deterministic checks 是 release gate）
  是正确的，而且比大多数 agent 项目清醒，守住。

---

## 6. 两周半时间表

| 天 | 任务 | 产出 |
| --- | --- | --- |
| 1 | P0-0 cassette | 可复现 |
| 2–3 | P0-1 ReAct baseline | 表 1：三档设计 ablation |
| 4 | P0-3 pass^k | 表 1 加一列 pass^5 |
| 5–8 | P0-2 fault injection + attribution | 表 2：三协议 × {who, when} accuracy + 规则对照 + 长度 scaling 曲线 |
| 9 | P1-1 best-of-N | 图 1：inference-time scaling |
| 10–11 | P1-2 投毒鲁棒性 | 表 3：attack success rate |
| 12 | P1-4 RESULTS.md + repro.sh | 一页纸 |
| 13–15 | （可选）P1-3 TravelPlanner | 可比外部数字 |

---

## 7. 给老师的新邮件角度

现在 `docs/teacher-update.md` 的措辞是**"我做了一个系统，下一步想做评估"**。
做完 P0 之后应该改成**"我做了一个实验，这是三个发现，这是我想继续追的问题"**。

建议的三句核心：

1. "我把自己系统的 deterministic invariant checks 当作 failure 的自动金标，
   可以零人工成本地生成带 who/when 标注的失败 trajectory；
   在这上面我复现了 All-at-Once / Step-by-Step / Binary Search 三种归因协议，
   发现 ______。"
2. "对比 ReAct free tool loop，typed workflow 在 pass^5 上的差距比 pass^1 大 ___ 倍，
   说明结构化边界主要买的是 **可靠性** 而不是平均正确率。"
3. "我也看到了它挡不住的攻击面：RAG 内容投毒会穿过 typed 边界进入 narrative，
   因为约束只覆盖了 evidence 字段、没覆盖 prose。我想知道这个边界能不能形式化。"

第 3 句尤其重要——**主动报告自己方法的失败边界**，是老师判断一个人能不能做科研
最直接的信号，比 169 passed tests 有用得多。

---

## 参考

- [Huazheng Wang 主页](https://huazhengwang.github.io/)
- [Which Agent Causes Task Failures and When? (ICML'25 Spotlight)](https://arxiv.org/abs/2505.00212)
- [Who&When Pro (NeurIPS'26)](https://arxiv.org/html/2607.09996v1)
- [Position: Digital Agents Require Unified Agent-Native Computers (ICML'26) / AgentVM](https://github.com/ag2ai/AgentVM)
- [TravelPlanner (ICML'24)](https://arxiv.org/html/2402.01622v4)
