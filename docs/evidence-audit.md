# 审计：哪些字段是"模型编了程序就信"，哪些检查"出错了没人管"

审计日期 2026-10-27。方法：对 `TripRequest` 的每个模型可写字段，问两个问题——
(1) 模型能不能凭空填？(2) 填了之后有没有任何东西会拦？
再对每个"会产生判断结果"的检查，问：这个结果被谁消费了？

---

> **2026-10-27 更新：下面"已修"一栏描述的 5 个正则/关键词守卫已经全部删除**，
> 换成了一个统一机制：模型为每个填入的字段给出用户原文的引文，程序验证引文确实
> 存在，站不住的值清空后交回 `required_missing_fields()` 追问。
> 设计与取舍见 [retry-and-evidence.md](retry-and-evidence.md)。
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
