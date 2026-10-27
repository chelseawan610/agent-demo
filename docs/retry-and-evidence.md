# 两个问题的答案，以及一个该换掉正则的设计

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
# schemas/request.py
field_evidence: dict[str, str] = Field(default_factory=dict)
# 例如 {"start_date": "2026-10-29", "budget": "预算只有3万", "origin": "从上海出发"}
```

```python
# 一个函数替掉 5 个正则守卫
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
