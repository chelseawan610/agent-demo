# 让 Agent 不"蠢"：多轮与灵活性改造方案

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
# 需要补充旅行信息

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
# schemas/trip.py
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
（重新规划，4 天 3 晚，没有重新调用天气以外的工具）
```
看点：说一句"重新规划时只有 SearchPlan 变了的工具才会重跑"，
这是老师会注意到的工程判断。

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
