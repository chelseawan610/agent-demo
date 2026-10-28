# Design notes
Decisions behind the guard rails, kept because each one records a mistake
that was made first and corrected after.

> Historical note: several sections describe Chinese user-facing strings.
> Output is English now; the reasoning is unchanged.

---

## 两个问题的答案，以及一个该换掉正则的设计

---

## 问题一：本来就有"必要条件缺了就追问"的机制，为什么我去加正则？

有，而且它是对的：`TripRequest.required_missing_fields()`
（[request.py:143](../src/travel_agent/schemas/request.py:143)）。它是一张声明式的表：

- 目的地永远必填
- 要机票 → 加出发城市、出发日期
- 只要酒店 → 加入住日期
- 往返机票 / 含机票酒店的完整行程 → 再加一个时长边界

澄清循环完全由它驱动，`missing_fields` 一非空就继续追问。这个机制没有问题。

**问题在于它回答的是"字段在不在"，不是"这个值是不是真的"。**

```
模型填了 origin="Tokyo"  →  origin 非空  →  不在 missing_fields 里  →  不追问
```

一个编造的值和一个真实的值，在这张表眼里完全一样。所以追问机制不是失效了，
是**它根本看不到这种失败**。

我上一轮的做法是给 5 个字段各写一个正则守卫，判断"用户原话里提没提过这类事"。
你的批评是对的：

- 5 个函数、5 份关键词表，互相之间没有关系
- 每加一个字段就要再写一份
- 关键词表永远不全（中文表达太多，英文同理），漏一个就是一个洞
- 它是**挂在旁边的第二套机制**，没有复用已有的声明式契约

---

## 更好的做法：让模型给出处，程序验出处

把问题倒过来。与其让程序去猜"用户有没有提过日期"，不如**要求模型在提取每个字段时，
同时引用用户原文里支持它的那一小段**；程序只做一件极简单、完全确定性的事：
**这段引文在用户原文里真的存在吗？**

```python
## schemas/request.py
field_evidence: dict[str, str] = Field(default_factory=dict)
## 例如 {"start_date": "2026-10-29", "budget": "预算只有3万", "origin": "从上海出发"}
```

```python
## 一个函数替掉 5 个正则守卫
def drop_uncited_fields(request, source_text, *, exempt=frozenset()):
    """字段的引文必须真的出现在用户原文里，否则这个值不作数。"""
    text = _normalize(source_text)          # 大小写、空白、全角半角
    values = request.model_dump()
    for field in EVIDENCE_REQUIRED_FIELDS:  # origin/start_date/return_date/budget/flight_type
        if values.get(field) is None or field in exempt:
            continue
        quote = request.field_evidence.get(field, "")
        if len(_normalize(quote)) < 2 or _normalize(quote) not in text:
            values[field] = None            # 交还给 required_missing_fields 去追问
    return TripRequest.model_validate(values)
```

Intake prompt 只加一句（不是加规则，是加一个输出要求）：

> For every field you fill, add an entry to `field_evidence` quoting the exact
> span of the user's message that supports it. Copy the span verbatim. If no
> span of the user's message supports a field, leave the field null instead of
> inventing a citation.

### 为什么这个比正则好

| | 5 个正则守卫 | 引文验证 |
| --- | --- | --- |
| 机制数量 | 5 套各自的关键词表 | 1 套，所有字段共用 |
| 语言 | 中英文各维护一份，永远不全 | 与语言无关，用户怎么说都行 |
| 加新字段 | 再写一个函数 + 一份词表 | 加进一个集合，完 |
| 谁做语义判断 | **程序**（正则猜用户意思，容易误拦） | **模型**（它本来就在读这句话） |
| 谁做验证 | 程序 | **程序**，而且是精确子串匹配，不是模型自评 |
| 失败方向 | 词表漏词 → 漏拦；词表过宽 → 误拦 | 编不出引文 → 字段为空 → **走原有的追问机制** |

最后一行是关键：它**不新增追问机制，而是把问题还给
`required_missing_fields()`**——也就是你记得的那个本来就存在的机制。
守卫不再是平行的第二套逻辑，只是"把站不住的值清空"，剩下的交给原有契约。

代码量大致是 **−150 行正则/词表，+30 行**。

### 它的弱点（必须说清楚）

模型可以引一段**确实存在但其实不支持这个字段**的话——比如拿 `"玩3天"` 去当
`start_date` 的出处。引文验证挡不住这个。但这比"凭空编造"弱得多：

- 凭空编造：模型只要想填就能填
- 错误引用：模型必须从用户原文里挑一段，而且这段会被**记录在 run log 里**，
  可以事后审计"它拿什么当依据"

真要再收紧，可以加一条：同一段引文不能同时当两个字段的依据。但那是以后的事，
不是现在该堆的东西。

---

## 问题二：有没有"第二轮跑"？有，但缺的恰恰是最该重跑的那一类

我把所有重试路径都查了一遍：

| 失败类型 | 现在会不会重跑 | 在哪 |
| --- | --- | --- |
| 模型 5xx / 超时 / 连接错误 | ✅ 重试 3 次，再不行 `FallbackAgent` 换模型 | [trip_planning.py:105](../src/travel_agent/workflows/trip_planning.py:105) |
| 用户必填信息缺失 | ✅ 澄清循环，最多 3 轮 | `collect_trip_request` |
| 工具 provider 瞬时失败 | ✅ HTTP 层重试，之后标 unavailable | `tools/http.py` |
| 计划违反确定性不变量 | ⚠️ 重跑 1 次（上一轮加的 gate）——**但见下** | `workflows/gate.py` |
| **模型返回的 JSON 不符合 schema** | ❌ **一次都不重跑**，直接降级 | [trip_planning.py:588](../src/travel_agent/workflows/trip_planning.py:588) |

最后一行就是你问的那种情况，而且它还**把上一行也废掉了**。实测：

```
模型返回 schema 不合法的 narrative
→ 模型调用次数      : 1        ← 没有重试
→ gate attempts    : 1   regenerated: False
→ generation_source: deterministic_fallback
→ invariants       : 全过
```

链路是这样断的：

1. narrative 校验失败 → `plan_trip_structured` **立刻**返回确定性计划
2. 确定性计划是**合法**的，而且天然满足所有不变量
3. gate 一看"不变量全过"，认为成功，`attempts=1`，不重跑

所以 gate 的重试永远看不到 schema 失败。用户拿到的是模板化的每日标题，
而不是模型写的叙述——**而这恰恰是最可能重跑一次就好的失败**。

### 但有个前提得先说清楚

`planner` 的 `temperature = 0.0`（[travel.py:73](../src/travel_agent/agents/travel.py:73)）。
**同样的 prompt 重跑一次，大概率得到同样的错误输出。** 所以单纯"再调一次"没用。

真正能让第二轮有意义的是**把失败原因写回 prompt**：

```
上一次输出未通过校验：summary 不能为空（min_length=1）。
请修正这一点后重新输出，只返回 TripNarrative JSON。
```

`validation_errors` 这个列表**已经存在**（trip_planning.py:522），现在只被写进
日志和最终异常文本，从来没有回传给模型。这是现成的材料。

### 建议的改法

一次**带错误反馈的重试**，然后才降级：

```
narrative 校验失败
  → 把 validation_errors[-1] 追加到 prompt，重调一次      ← 新增
  → 还失败 → 降级到确定性计划（现有行为）
```

这样三层重试各司其职、互不重叠：

| 层 | 负责的失败 | 重跑有没有意义 |
| --- | --- | --- |
| `plan_trip` | 传输层（5xx/超时） | 有，网络是随机的 |
| **schema 重试**（待加） | 输出格式不合法 | 有，**前提是把错误写回 prompt** |
| `plan_with_invariant_gate` | 格式合法但内容违反契约 | 有，同样该把失败的检查名写回 prompt |

顺带一提，gate 现在的重试也有同一个毛病：它重调的 prompt 和第一次一模一样，
temperature 还是 0。**它也应该把失败的检查名写进 prompt**，否则第二次大概率
重复第一次的错误。这是我上一轮没想透的地方。

---

## 建议的顺序

你那次真实模型的 transcript，跑之前最好先决定要不要换设计：

- **先换再跑**（推荐）：现在跑出来的"误拦率"是在测 5 个正则，而这些正则按上面的
  方案是要被删掉的，测了也用不上。换完只跑一次，拿到的是最终形态的数据。
- **先跑再换**：能拿到正则方案的真实误拦数据，作为"正则确实不够用"的证据。
  如果你想要这个对照，也有价值——但要多花一轮模型调用。

两个改动（引文验证、带反馈的重试）都不新增机制，都是把已有的东西接起来：
一个把判断还给 `required_missing_fields()`，一个把已经收集好的
`validation_errors` 真正用上。

---

## 审计：哪些字段是"模型编了程序就信"，哪些检查"出错了没人管"

审计日期 2026-10-27。方法：对 `TripRequest` 的每个模型可写字段，问两个问题——
(1) 模型能不能凭空填？(2) 填了之后有没有任何东西会拦？
再对每个"会产生判断结果"的检查，问：这个结果被谁消费了？

---

> **2026-10-27 更新：下面"已修"一栏描述的 5 个正则/关键词守卫已经全部删除**，
> 换成了一个统一机制：模型为每个填入的字段给出用户原文的引文，程序验证引文确实
> 存在，站不住的值清空后交回 `required_missing_fields()` 追问。
> 设计与取舍见 the citation section above。
> 结果相同，机制从 5 套关键词表变成 1 套，且与语言无关。

## 一、模型编了程序就信（Category A）

### 已修

| 字段 | 编造后的后果 | 原本有没有拦 | 现在怎么拦 |
| --- | --- | --- | --- |
| `origin` | 状态直接 ready，**不追问**，拿错城市搜航班 | ❌ 完全没有 | `protect_origin_evidence`：用户原话里没有任何"出发"表述 → 丢弃并追问 |
| `start_date` | 航班、酒店、天气、营业时间**全部按一个用户没给过的日期**执行 | ⚠️ 只拦过去的日期（`infer_status`），未来的日期放行 | `protect_date_evidence`：原话里没有任何日历表述 → 丢弃并追问 |
| `flight_type="one_way"` | `required_missing_fields` 对单程**跳过时长边界**→ 少问一轮 | ❌ 完全没有 | `protect_flight_type_evidence`：没说单程 → 回落 `round_trip`（文档里的默认值） |
| `budget` | 把编造的金额当作用户自己的预算展示并写进 planner prompt | ❌ 完全没有 | `protect_budget_evidence`：原话里没有任何钱的表述 → 丢弃 |
| `requested_services` 扩大 | "给我东京攻略" 变成四项全开，**多搜机票酒店**并展示 | ⚠️ 只有 `food` 被 `protect_service_scope` 守着 | `protect_service_expansion`：用户把请求框定为攻略类且没提机票/酒店 → 只保留 `activities`/`guide` |

五个守卫统一由 `apply_evidence_guards()` 调用，设计原则一致：

1. **不验证值，只验证"用户提没提过这类事"。** 别名表只有 8 个城市，验不了任意城市；
   但"这句话里有没有出发表述/日期表述/钱的表述"是可以从用户自己的文字判定的。
2. **一律 fail-open。** 只要文本里有相应表述，就信模型。误判的代价是多问一句，
   而不是把错事实送进工具。
3. **我们自己问过的字段豁免。** 用户回答"上海"、"十一月一号"这种光秃秃的值时
   没有任何标记词，但那是对我们自己问题的回答，必须接受
   （`apply_evidence_guards(requested_fields=...)`）。

### 未修，但是有意为之（不是洞）

| 字段 | 为什么不拦 |
| --- | --- |
| `destination`（别名表外的城市） | 拦不了。别名表只有 8 个城市，任意城市的正确性只能靠 geocoder 下游验证。已修的是另一个相关 bug：出发城市在表里、目的地不在时，目的地会被改写成出发城市 |
| `preference_details[].place_name` / `place_type` | **下游已验证**。Photon 解析时会用目的地坐标、规范名和 OSM 类型核验，对不上返回 `unavailable` |
| 攻略-only 请求的 `days` | **设计允许**。README 明确写了 guide/activities-only 可以由 Intake 给出 bounded 推荐 |
| `transport_modes` | 危害低。编造 `transit` 只会让 transit adapter 诚实返回 `not_configured` |

### 未修，已知残留（按危害排序）

| 字段 | 问题 | 为什么这次没动 |
| --- | --- | --- |
| `nights_explicit` | 这是一个**模型控制的信任开关**：设成 true，程序就不再从 days 重新推导 nights。模型错误地设 true，晚数就会卡住 | 需要和 `_resolve_duration` 一起改，影响面比今天其他改动大。目前 `merge_trip_requests` 里已经处理了"改天数时丢弃派生晚数"的情况 |
| `preference_details[].planning_hint` | 纯模型字段，决定 `venue_mix`。模型乱标 `venue_mix` 会让用户拿到没要求的室内外交替排序 | 危害低（只是软排序），且"偏好未满足"现在会标 partial 并说明 |
| `stated_fields`（本次新增） | 模型多报字段，就能让漂移值覆盖已确认事实——等于削弱了原来的保护 | 这是刻意的取舍：空列表时行为等同于改动前，不会更差。真要收紧，需要和 `protect_*` 交叉验证 |
| `duration_profile` | 模型给的时长档位，只在 guide-only 时影响推荐天数 | 危害低 |

---

## 二、检查算出来了但没人管（Category B）

| 检查 | 原本的处理 | 现在 |
| --- | --- | --- |
| `check_workflow_invariants`（23 条不变量） | **只在 `--debug` 下 print**。FAIL 不重试、不降级、不告知普通用户、exit code 不变 | ✅ `plan_with_invariant_gate`：失败 → 重新生成一次 → 仍失败 → 降级到确定性组装（抄工具证据，不变量天然成立）→ 并且**对普通用户打印** |
| `ReviewResult.status == "needs_revision"` | **只渲染成 Markdown 末尾的一行文字**。不影响 `plan.status`、不重试、不改 exit code | ✅ `report_review()`：在正常输出路径上明确提示。**刻意不做硬门禁**——按项目既定立场，release gate 是确定性检查，不是 LLM 判断 |
| `maf.py` 把确定性失败强制改成 `needs_revision` | 改完之后那个字段也只是被打印 | ✅ 随上面两条一起生效；而且现在确定性失败在 reviewer 之前就被 gate 处理了 |
| `validate_trip_plan`（天数/行程日范围） | 抛异常，被 `plan_trip_structured` 捕获并降级 | 原本就是对的，未改动 |
| `ToolResult.status`（unavailable/skipped） | 标记 `partial` + notes + 不可用数据源列表 | 原本就是对的，未改动 |
| `evals/workflow.py` 的不变量 | 当门禁用 | 原本就是对的，未改动 |

---

## 三、验证

```
pytest              209 → 236 passed
evaluate_workflow   10 / 10
```

端到端（`scripts/replay_multi_turn.py`，模型同时编造 origin 和 flight_type）：

```
REPLAY_FAKE_ORIGIN=Tokyo REPLAY_FAKE_FLIGHT_TYPE=one_way

从哪个城市出发？                                      ← origin 守卫生效
> 从上海出发
[tool] search_flights(origin='Shanghai', ... 2026-10-29)   ← 不是 Tokyo
[tool] search_flights(origin='Maldives',  ... 2026-10-31)   ← 回程存在 = 已回落 round_trip
```

整份 transcript 里 `Tokyo` 出现 **0 次**。

不变量 gate（让模型编造一条工具从没返回过的航班）：

```
一直正常            attempts=1  不重跑  不降级
第一次编造、第二次正常  attempts=2  重跑    幽灵航班消失
两次都编造          attempts=2  重跑    降级   幽灵航班消失
```

---

## 四、仍未验证

- **真实模型没跑过**。所有守卫都是对"模型可能编造"的模拟，不是观测。
  真实模型实际多久编一次、编什么，要用 `scripts/demo_multi_turn.py` 跑了才知道。
- 守卫的**误判率没有测量**。每条正则都是 fail-open，但到底多少正常句子会被
  误拦（多问一轮），只有真实输入跑过才知道。
- 英文输入仍然得到中文输出。

---

## 让 Agent 不"蠢"：多轮与灵活性改造方案

目标：今天之内让这个 agent 在**真人乱说话**的时候表现得像个 agent，然后发给老师。
不做实验、不接新 API、不改架构。

---

## 0. 先说结论

你的直觉是对的，而且问题比"单轮"更具体。我在代码里找到了 **5 个可复现的失败**，
其中 2 个是真 bug（不是设计取舍）：

| # | 现象 | 严重度 | 根因 |
| --- | --- | --- | --- |
| 1 | **用户改主意会被静默忽略**。"改成4天" → 还是 3 天 | 🔴 真 bug | `merge_trip_requests` 里老值无条件覆盖新值 |
| 2 | **出完计划直接退出**，没有任何后续轮次 | 🔴 最伤 demo | `cli.py` 打印完 `return 0` |
| 3 | 澄清轮里新增的服务被丢掉。第二轮说"顺便推荐餐厅" → 没有餐厅 | 🟠 真 bug | `merge` 硬覆盖 `requested_services`，把 `protect_service_scope` 的结果冲掉 |
| 4 | 第三轮会忘记第二轮说过的话 | 🟠 | 提取时只拼 `initial_text + 最后一轮回答` |
| 5 | 澄清时甩一张表单，不是问问题；回车就放弃 | 🟡 体验 | `render_trip_request` 打印字段名列表 |

**好消息：多轮能力你其实已经写了 80%，只是锁在一个 CLI flag 里。**

`WorkflowCheckpoint(phase="searched")` 保存的正好是 `(request, search_plan, search_results)`，
而 `--resume` 做的事就是"跳过工具、只重跑 Planner"
（[cli.py:499-503](src/travel_agent/app/cli.py:499)）。
这就是多轮修改所需要的全部原语。你要做的不是发明机制，
**是把这个文件级的 flag 变成一个内存里的循环。**

这句话也是你给老师讲这个项目时最好的一句话。

---

## 1. 每个问题的根因、改法、理由

### P0-1 · 让用户能改主意（🔴 必须今天修）

#### 现象

```
> 我10月1号从上海去东京玩三天
（澄清轮：问预算）
> 预算一万，哦对，改成4天
→ 计划还是 3 天
```

#### 根因

[workflows/intake.py:327-362](src/travel_agent/workflows/intake.py:327)：

```python
def merge_trip_requests(old: TripRequest, new: TripRequest) -> TripRequest:
    values = new.model_dump()
    for field in ("origin", "destination", "start_date", "return_date",
                  "flight_type", "days", "nights", "duration_profile", "budget"):
        old_value = getattr(old, field)
        ...
        if old_value is not None:
            values[field] = old_value        # ← 老值无条件赢
    ...
    values["requested_services"] = old.requested_services   # ← 同样无条件
```

注释写的是 "Clarification turns fill gaps; they do not silently rewrite facts"。
**意图是对的**——防止模型在第二轮把第一轮的城市幻觉成别的。
但实现把两件事混为一谈了：

- "模型自己漂移了" → 应该拒绝
- "用户明确改口了" → 应该接受

现在代码无法区分，于是一刀切拒绝，代价是用户永远改不了主意。

#### 改法

让 Intake **自报哪些字段是本轮用户亲口说的**，用它来区分上面两种情况。

**(a) `schemas/request.py`，在 `TripRequest` 加一个字段：**

```python
stated_fields: list[str] = Field(
    default_factory=list,
    description=(
        "Field names the user explicitly stated in THIS message, including "
        "corrections. Used to decide whether a new value may override a "
        "previously confirmed one."
    ),
)
```

**(b) `agents/intake.py`，instructions 末尾加一段：**

```python
"Also return stated_fields: the field names that THIS user message explicitly "
"states, using only these values: origin, destination, start_date, return_date, "
"days, nights, budget, flight_type, requested_services, preferences, "
"transport_modes. Include a field only when the current message actually "
"mentions it, including when the user corrects an earlier value such as "
"'改成4天', '其实是去大阪', or '不要酒店了'. Never include a field you only "
"inferred, defaulted, or carried over from earlier context."
```

**(c) `workflows/intake.py`，改 `merge_trip_requests`：**

```python
def merge_trip_requests(old: TripRequest, new: TripRequest) -> TripRequest:
    """Keep confirmed facts, but let the user's newest turn correct them.

    A field named in ``new.stated_fields`` was spoken by the user in this turn
    (including a correction), so it wins.  Everything else falls back to the
    value the user already confirmed, which is what stops a model drift from
    rewriting an earlier city or date.
    """

    values = new.model_dump()
    stated = set(new.stated_fields)
    for field in (
        "origin", "destination", "start_date", "return_date",
        "flight_type", "days", "nights", "duration_profile", "budget",
    ):
        if field in stated and values.get(field) is not None:
            continue                                   # 用户本轮亲口说的，保留新值
        old_value = getattr(old, field)
        if field == "duration_profile" and old_value == "unknown":
            continue
        if old_value is not None:
            values[field] = old_value

    # 改了天数但没提晚数时，必须丢掉旧的派生晚数，否则 4 天会配 2 晚。
    if "days" in stated and "nights" not in stated and not old.nights_explicit:
        values["nights"] = None

    values["nights_explicit"] = bool(
        (old.nights_explicit and "nights" not in stated)
        or values.get("nights_explicit", False)
    )
    values["preferences"] = list(dict.fromkeys([*old.preferences, *values["preferences"]]))
    details = {d.label: d for d in [*old.preference_details, *new.preference_details]}
    values["preference_details"] = list(details.values())
    if "requested_services" not in stated:
        values["requested_services"] = old.requested_services
    return TripRequest(**values)
```

#### 为什么这么改（而不是别的改法）

- **不能靠关键词判断"改口"**（"改成"/"其实"/"不是"）。中文表达太多，
  而且"不是周末去"和"改成4天"的句式完全不同，关键词表会一直漏。
- **不能让新值无条件赢**。那就退回到了模型幻觉能覆盖用户已确认事实的状态，
  README 里专门论证过这个风险，是真实存在的。
- `stated_fields` 把判断交给模型，但**只让它回答一个极窄的问题**（"这句话提到了哪些字段"），
  不让它决定最终值。这跟项目既有的哲学一致：模型做语义，程序做边界。
- **降级是安全的**：模型不填 `stated_fields` → 空集合 → 行为完全等于现在。
  不会比现状更差。

#### 对系统的好处

- 这一个改动同时修好了 P0-1 和 P0-3（服务范围）两个 bug。
- 它是 P0-2（出完计划后继续改）能工作的前提——后续轮次复用同一个 merge。
- `stated_fields` 本身会进 run log，以后看日志能知道"用户第几轮改了什么"。

#### 工作量

约 40 行，含 3 个新测试（改天数 / 改目的地 / 加服务）。**1 小时。**

---

### P0-2 · 出完计划之后能接着聊（🔴 对 demo 影响最大）

#### 现象

[cli.py:696-700](src/travel_agent/app/cli.py:696)：

```python
        if args.output == "json":
            print(json.dumps(...))
        else:
            print(render_trip_plan(result))
    return 0          # ← 进程结束
```

老师看完计划想说"第二天换成室内的"——没有地方可以说。
只能整个重跑，重新打一遍需求，**重新调用一遍所有外部 API**。

这是"显得蠢"最大的来源：它不是 agent，是一个一次性的表单报告生成器。

#### 改法

**(a) 先把 `main()` 里出计划的那段抽成函数。** 现在 `main()` 有 460 行，
什么都塞在里面，没法复用。抽出：

```python
async def produce_plan(
    trip_request: TripRequest,
    *,
    agent, formatter, reviewer, settings, args, trace, memory,
    original_text: str,
    reuse: tuple[SearchPlan, SearchResults] | None = None,
) -> tuple[SearchPlan, SearchResults, TripPlan]:
    """Run SearchPlan -> tools -> Planner once, optionally reusing tool evidence."""

    if reuse is not None:
        search_plan, tool_results = reuse
    else:
        search_plan = build_search_plan(trip_request)
        tool_results = execute_search_plan(
            search_plan,
            knowledge_retriever=_build_knowledge_retriever(args),
            run_id=args.run_id,
            trace_id=trace.trace_id,
        )
    planner_request = build_trip_narrative_prompt(
        original_text, trip_request, search_plan, tool_results, memory=memory
    )
    result = await plan_trip_structured(...)
    return search_plan, tool_results, result
```

**(b) 加一个意图分类 schema，`schemas/followup.py`：**

```python
from typing import Literal
from pydantic import BaseModel


class FollowUpIntent(BaseModel):
    """How one user turn after a delivered plan should be handled."""

    action: Literal[
        "modify_request",    # 旅行事实变了：天数/日期/城市/预算/服务范围
        "refine_narrative",  # 事实没变，只改表达：更详细、换英文、语气
        "ask_about_plan",    # 针对已有计划提问，不需要改计划
        "end",               # 用户表示结束
        "out_of_scope",      # 订票、付款、与旅行无关
    ]
    reason: str = ""
```

**(c) 在 `cli.py` 打印完计划后加循环：**

```python
    while True:
        answer = (await asyncio.to_thread(
            input,
            "\n还要调整吗？（例如：改成4天 / 第二天换成室内 / 为什么没安排东京塔；"
            "直接回车结束）\n> ",
        )).strip()
        if not answer:
            break

        intent = await classify_follow_up(intent_agent, answer, on_trace=trace.record_llm)

        if intent.action == "end":
            break

        if intent.action == "out_of_scope":
            print("这个 demo 不做预订和支付。可以调整天数、日期、城市、预算、"
                  "服务范围，或者让我把某一天写得更详细。")
            continue

        if intent.action == "ask_about_plan":
            # 只读：拿已有的 TripPlan + SearchResults 回答，不碰工具，不改计划。
            print(await answer_plan_question(
                agent, answer, trip_request, tool_results, result
            ))
            continue

        if intent.action == "refine_narrative":
            # 事实没变 -> 零网络调用，只重跑 Planner。通常 2-3 秒。
            search_plan, tool_results, result = await produce_plan(
                trip_request, reuse=(search_plan, tool_results),
                original_text=f"{original_text}\n\n用户追加要求：{answer}", ...
            )
            print(render_trip_plan(result))
            continue

        # modify_request：重新提取需求，用 stated_fields 合并（复用 P0-1 的机制）
        revised = await extract_trip_request(intake_agent, _accumulated_text(...))
        revised = merge_trip_requests(trip_request, revised)
        if revised.status != "ready":
            print(render_trip_request(revised))
            continue
        next_plan = build_search_plan(revised)
        # SearchPlan 是 Pydantic 模型，按值比较：查询没变就不重新调用外部 API。
        reuse = (search_plan, tool_results) if next_plan == search_plan else None
        trip_request = revised
        search_plan, tool_results, result = await produce_plan(
            trip_request, reuse=reuse, original_text=original_text, ...
        )
        print(render_trip_plan(result))
```

#### 为什么这么改

- **四个分支是有必要的，不是过度设计。** 它们的代价差了一个数量级：
  `ask_about_plan` 和 `refine_narrative` 是 1 次模型调用、0 次网络；
  `modify_request` 可能要重跑 Overpass。用一个分类器挡在前面，
  意味着"把第二天写详细点"不会白白重打一遍 OSM。
- **`next_plan == search_plan` 这个判断很关键。** 很多修改（口味、详略、
  室内外偏好的表达）根本不改 SearchPlan。Pydantic 的值比较让你免费拿到
  "需不需要重新取数据"的答案，不需要写缓存逻辑。
- **复用 `produce_plan` 而不是递归调 main()**，保证多轮和单轮走的是同一条
  代码路径，`WorkflowChecks` 对每一轮都仍然成立。

#### 对系统的好处

- demo 从"看一份报告"变成"跟它聊"。这是老师唯一会直接感知到的差别。
- 把 `--resume`/checkpoint 的价值真正用起来了。现在 checkpoint 只能跨进程用，
  改完之后它是同一套状态模型在进程内的应用。你可以在 README 里把这段
  "application-owned state"的论证直接接上。
- 每一轮都写进同一个 `trajectory`，run log 会自然变成多轮轨迹——
  以后真要做任何分析，数据格式已经对了。

#### 工作量

抽 `produce_plan` 约 60 行搬运，循环约 90 行，`classify_follow_up` 和
`answer_plan_question` 各约 30 行。**2.5 小时。**

---

### P0-3 · 第二轮加的服务会丢（🟠 跟 P0-1 一起修）

#### 现象

```
> 去东京玩三天
（澄清轮）
> 从上海出发，顺便推荐几家餐厅
→ 没有餐厅
```

#### 根因

[intake.py:284-287](src/travel_agent/workflows/intake.py:284) 的执行顺序：

```python
request = protect_explicit_destination(request, source_turns)
request = protect_service_scope(request, source_turns)      # 这里算出了 food
if previous_request is not None:
    request = merge_trip_requests(previous_request, request)  # 这里又被冲掉
```

`merge` 最后一行 `values["requested_services"] = old.requested_services`
把上一行刚算出来的 food 直接丢了。

#### 改法

P0-1 的 `if "requested_services" not in stated:` 已经修好一半。
再加一道保险，把顺序倒过来——**先合并，再跑保护**：

```python
if previous_request is not None:
    request = merge_trip_requests(previous_request, request)
request = protect_explicit_destination(request, source_turns)
request = protect_service_scope(request, source_turns)
```

`protect_service_scope` 本来就是对**全部用户原文**（`source_turns`）做判断的，
放在最后才是它该在的位置：它是最终守门人，不应该被后续步骤覆盖。

#### 为什么

两道独立的防线比一道好：就算模型没正确填 `stated_fields`，
`protect_service_scope` 扫全部用户原文仍然能把 food 捞回来。

#### 对系统的好处

明确了一个不变量：**所有 `protect_*` 函数都是流水线的最后一环**。
以后再加保护函数时不会重蹈覆辙。值得在函数 docstring 里写一句。

#### 工作量

3 行 + 1 个测试。**15 分钟。**

---

### P0-4 · 第三轮忘记第二轮（🟠）

#### 根因

[intake.py:264-271](src/travel_agent/workflows/intake.py:264)：

```python
extraction_text = (
    "用户原始旅行需求（只读取用户原文）：\n"
    f"{initial_text}\n\n"
    "本轮用户补充信息：\n"
    f"{latest_answer}\n\n"        # ← 只有最后一轮
    ...
)
```

`latest_answer` 只有最近一次。第 3 轮看不到第 2 轮的原话。
目前靠 `merge_trip_requests` 兜住已解析的字段，但凡是没进字段的信息
（偏好的细微表述、否定、条件）就永久丢失了。

#### 改法

```python
    answers: list[str] = []
    for round_number in range(max_rounds + 1):
        extraction_text = current_text
        if answers:
            numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(answers, 1))
            extraction_text = (
                "用户原始旅行需求（只读取用户原文）：\n"
                f"{initial_text}\n\n"
                "用户后续补充，按时间顺序（只读取用户原文）：\n"
                f"{numbered}\n\n"
                "请只从以上用户文字提取字段；不要复制任何上一轮模型输出。"
                "若后面的补充与前面冲突，以最后一次用户说法为准。"
            )
        ...
        source_turns = "\n".join([initial_text, *answers])
        ...
        answers.append(answer.strip())
```

#### 为什么

关键是：**这不违反原来的设计意图**。原来的注释说得很清楚——
不能把上一轮的**模型 JSON** 喂回去，否则幻觉会自我强化。
累积的是**用户原话**，不是模型输出，所以那个保护仍然成立。
现在这个实现是"为了防模型输出回流，连用户原话一起扔了"，扔过头了。

#### 对系统的好处

- 多轮上限从"实际 2 轮有效"变成真正的 3 轮。
- 配合 P0-1，"以最后一次说法为准"这条规则在 prompt 和 merge 两个层面一致，
  不会打架。

#### 工作量

约 15 行。**30 分钟。**

---

### P0-5 · 澄清时问人话，不甩表单（🟡 但最容易看出来）

#### 现象

现在触发澄清时打印：

```
## 需要补充旅行信息

请求范围：flights, hotels, activities, guide

在调用旅行工具前，请补充：
- 出发城市
- 游玩天数或回程/退房日期

可选信息：预算、交通方式和兴趣偏好；时长边界请按上面的请求类型补充。
```

然后 `请补充以上信息（直接回车结束）：`。
回车 → [intake.py:302-303](src/travel_agent/workflows/intake.py:302) 直接放弃退出。

这看起来像一个报错的表单校验，不像一个在问问题的助理。

#### 改法

**(a) `cli.py` 加一张问题表，一次只问最关键的一个：**

```python
CLARIFICATION_QUESTIONS = {
    "目的地": "你想去哪个城市？",
    "出发城市": "从哪个城市出发？",
    "出发日期": "哪天出发？（例如 10月1号 / 2026-10-01）",
    "入住日期": "哪天入住？（例如 10月1号）",
    "游玩天数或回程/退房日期": "打算玩几天，或者哪天回来？",
    "回程日期或游玩天数": "打算玩几天，或者哪天回来？",
    "游玩天数或入住晚数/退房日期": "住几晚，或者哪天退房？",
    "入住晚数或退房日期": "住几晚，或者哪天退房？",
}


async def ask_request_clarification(request: TripRequest) -> str:
    missing = request.missing_fields
    head = missing[0] if missing else None
    question = CLARIFICATION_QUESTIONS.get(head, f"请补充{head}。") if head else "还需要补充一点信息。"
    if len(missing) > 1:
        question += f"（顺便的话还需要：{'、'.join(missing[1:])}）"
    print(f"\n{question}")
    if args_debug:              # 表单只在 --debug 下保留，给评测和教学看
        print(render_trip_request(request))
    return await asyncio.to_thread(input, "> ")
```

**(b) 回车不再等于放弃，而是"你看着办"：**

```python
DEFERRAL_HINTS = ("随便", "你看着办", "都行", "你决定", "无所谓",
                  "anything", "you decide", "up to you")
```

空回车或命中以上词 → 对缺失的**非身份字段**（天数/晚数）套用文档化的默认值，
并把它写进计划：

```python
## schemas/trip.py
assumptions: list[str] = Field(default_factory=list)
```

渲染时在计划末尾加一节：

```markdown
## 本次使用的假设
- 未指定天数，按 3 天安排；如需调整请说"改成N天"。
```

**身份字段（目的地、出发城市、出发日期）永远不套默认值**——猜城市是幻觉，
猜天数是合理默认。这条界线要在 docstring 里写明。

#### 为什么这么改

- **成本是零。** `missing_fields` 本来就是 `required_missing_fields()`
  确定性算出来的，把它映射成一句话不需要任何额外模型调用。
  "更像 agent" 不等于"多调几次模型"——这点恰恰是这个项目想证明的东西。
- **`assumptions` 让灵活性不变成撒谎。** 项目整体立场是"不编造事实"，
  那么套默认值就必须是**声明过的**。有了这一节，你既可以在用户含糊时继续往下走，
  又没有破坏"所有事实可追溯"的主张。这比单纯放弃要强，也比偷偷填值要诚实。

#### 对系统的好处

- 把内部字段名从用户界面上拿掉了（表单退到 `--debug` 里，评测仍然能用）。
- `assumptions` 之后可以被 `check_workflow_invariants` 检查：
  "计划里出现的每一个默认值都必须在 assumptions 里有一条"。免费多一条不变量。

#### 工作量

约 50 行 + 渲染 10 行。**1 小时。**

---

### P1-1 · 不是所有输入都是订行程（有余力再做）

老师打开第一句很可能是"你能做什么？"或者"hello"。
现在这会直接进 intake，吐一张"需要补充目的地/出发城市/出发日期"的表单。很傻。

最省事的做法（不加模型调用）：

```python
def _looks_like_small_talk(text: str, request: TripRequest) -> bool:
    """A short message with no destination and no date is not a trip request."""
    return (
        len(text.strip()) <= 20
        and request.destination is None
        and request.start_date is None
        and not request.preferences
    )
```

命中就打印一段能力说明 + 一个可直接复制的例子，然后重新读一行输入。
不消耗额外模型调用，也不需要新 schema。

更正式的做法是把 `FollowUpIntent` 扩一个 `capability_question`，
第一轮也过一次分类器——但那是每次运行多一次模型调用，今天不值得。

**工作量：20 行，20 分钟。**

---

### P1-2 · 报错说人话（有余力再做）

[cli.py:634-635](src/travel_agent/app/cli.py:634) 现在直接打印
`模型请求失败：ValidationError: 12 validation errors for TripRequest ...`。
演示时这一屏很难看。

```python
def friendly_error(error: Exception) -> str:
    name, text = type(error).__name__, str(error)
    if "Timeout" in name or "timeout" in text.lower():
        return "模型响应超时了。可以直接重试，或用 --model 换一个更快的模型。"
    if "401" in text or "Authentication" in name:
        return "模型密钥无效，请检查 .env 里的 LLM_API_KEY。"
    if "429" in text or "RateLimit" in name:
        return "模型被限流了，稍后重试，或配置 --fallback-model。"
    if "Validation" in name:
        return "模型返回的结构不符合 schema，本次已记录到日志。重试或换模型通常能解决。"
    return f"运行失败（{name}）。加 --debug 可以看完整信息。"
```

原始异常保留在 `--debug` 和 run log 里，不丢诊断能力。

**工作量：30 行，20 分钟。**

---

## 2. 今天的执行顺序

| 顺序 | 任务 | 时间 | 为什么这个顺序 |
| --- | --- | --- | --- |
| 1 | P0-1 `stated_fields` + merge | 1h | 是 P0-2 和 P0-3 的基础 |
| 2 | P0-3 调换 protect/merge 顺序 | 15m | 顺手，和 1 共用测试 |
| 3 | P0-4 累积用户原话 | 30m | 独立，低风险 |
| 4 | P0-5 问人话 + assumptions | 1h | 独立，demo 肉眼可见 |
| 5 | P0-2 计划后 REPL | 2.5h | 最大，放在地基稳了之后 |
| 6 | 跑 `pytest -q`，补 4 个新测试 | 45m | 169 passed 不能掉 |
| 7 | P1-1 / P1-2 | 40m | 有时间才做 |

合计 **约 6 小时**（不含 P1）。如果只有半天，砍掉第 4 步和 P1，
保留 1/2/3/5/6——**P0-2 是绝对不能砍的那个**。

---

## 3. 改完之后照着演的 demo 脚本

录一段 3 分钟的终端操作，比任何文档都有用。按这个顺序演：

**第一段——它会追问，而且问的是人话**

```
> 我想去东京
哪天出发？（顺便的话还需要：出发城市、打算玩几天）
> 从上海，十月一号，玩三天
（出计划）
```
看点：日期归一化、服务范围自动推断、工具调用全部有来源。

**第二段——它接受改口（这是现在做不到的）**

```
> 改成4天，预算一万
（重新规划，4 天 3 晚；SearchPlan 改变后重新执行整份计划）
```
当前实现只做整份 SearchPlan 的复用：计划完全相同时复用工具结果；
计划改变时重新执行整份计划，HTTP 层仍可能命中短期缓存。
逐工具差分执行尚未实现，不能在演示中声称已经具备。

**第三段——它能回答关于计划本身的问题**

```
> 为什么第二天没有安排东京塔？
东京塔被安排在第一天，因为……（引用 SearchResults 里的 opening_hours / 距离）
```
看点：这一轮**没有调用任何工具、没有重新规划**，
答案完全来自已有的 typed evidence。这是"结构化状态"的直接收益。

**最后 10 秒——加 `--debug` 再跑一次**，让他看到 `SearchPlan`、
`ToolCallRecord`、`WorkflowChecks` 全 PASS。
一句话："模型只写了摘要和每日标题，航班、天气、坐标、营业时间都是程序从工具结果里装配的。"

---

## 4. 怎么给老师

### 别做的事

- ❌ 别发 700 行的 README 链接让他自己看。
- ❌ 别在邮件里解释架构细节（Pydantic、MAF、RRF 这些他不需要在邮件里读到）。
- ❌ 别问"我可以加入您的实验室吗"这种没法回答的问题。

### 发什么

1. **一段 2-3 分钟的终端录屏**（`asciinema rec` 或直接 QuickTime 录屏）。
   这是主件。他最多花 3 分钟。
2. **一个 transcript 文本**（你 `.local/manual-cases-*/` 里已经有这种格式了），
   包含上面第 3 节的三段对话。方便他跳着看。
3. **邮件正文不超过 200 词。**

### 邮件怎么写

现在 `docs/teacher-update.md` 的版本问题在于：它在**汇报功能清单**
（"支持中英文、澄清、typed adapters、RAG、memory、checkpoint、reviewer"），
但功能清单不传达判断力。老师看不出哪些是你想清楚了才做的，哪些是堆上去的。

换成讲**一个你做过的设计取舍**。比如：

> Dear Professor Wang,
>
> I built a travel-planning agent on Microsoft Agent Framework. Short demo attached (3 min).
>
> The design question I spent most time on was where to put the model. The planner
> has no direct tool access — it only emits a small narrative object, and the
> application assembles flights, weather, coordinates, and opening hours from typed
> tool results. That makes the failure modes legible: when a tool degrades, the plan
> is marked partial instead of being quietly filled in by the model.
>
> It took me a while to get multi-turn right. My first version refused to let a user
> change their mind, because I was blocking the model from overwriting confirmed
> facts — which also blocked the user. I now have intake report which fields the
> user actually stated in each turn, so a correction is accepted and a model drift
> still is not.
>
> Every run writes a redacted trajectory with per-stage tool status, latency, and
> normalized error categories. I noticed that overlaps with your work on failure
> attribution in multi-agent systems, and I would like to understand whether that
> is a direction your students are actively working on.
>
> Would you have 20 minutes for a short demo? I would also be glad to just send
> more traces if that is easier.
>
> Best,
> Chelsea

为什么这封比现在的好：

- **第二段讲一个取舍**，不是一个功能。取舍才显示判断力。
- **第三段主动讲了一个自己做错又改对的地方。** 这是最强的信号——
  能说清楚"我原来的设计为什么是错的"的人，才是能做研究的人。
  比"169 passed tests"有用得多。
- **最后只问一个具体问题**（"这个方向您的学生在做吗"），
  他可以一句话回答，不用承诺任何事。比"能不能加入"容易回复十倍。
- 全文没有要求他读代码。

### 关于实验

你说得对，老师没要求做实验，现在花时间设计实验是本末倒置。
**上面第 3 节的三段 transcript 本身就是证据**——它展示的是你能让 agent
处理真实的、不配合的用户输入，这正是"理解 agent"的内容。

真要有一个数字的话，最便宜的是这个：改完之后把同一组 case 跑 5 遍，
看多少次结果一致。但这个也等老师回信、知道他想看什么之后再说，
别现在猜他想要什么。

---

## 5. 今天明确不要碰

- ❌ 接真实航班/酒店 API（demo adapter 已经如实标注 `is_live=false`，
  这是诚实不是缺陷）。
- ❌ 拆 multi-agent（README 里"When to split agents"那段论证是对的，留着）。
- ❌ 新的 RAG engine、新的 CLI 参数、新文档。
- ❌ 任何会让 `pytest -q` 掉下 169 的改动。改完一定重跑。
