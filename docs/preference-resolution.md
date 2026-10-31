# 自然语言偏好如何变成工具参数

更新日期：2026-10-23。

## 两类语义，两条路径

工具只执行参数，不直接理解一句自然语言。Intake 在已有的那次 LLM 调用里把用户需求
转换为 Pydantic `PreferenceSpec`，没有为这次改动增加第二次理解模型调用。

```json
[
  {
    "label": "维多利亚港",
    "search_query": "Victoria Harbour Hong Kong",
    "kind": "place",
    "place_name": "Victoria Harbour",
    "place_type": "waterfront",
    "planning_hint": "none"
  },
  {
    "label": "室内外结合",
    "search_query": "indoor and outdoor activities Hong Kong",
    "kind": "interest",
    "place_name": null,
    "place_type": "other",
    "planning_hint": "venue_mix"
  }
]
```

`label` 保存用户原话；`search_query` 是供应商查询；`place_name` 是不带城市后缀的规范
地点名；`place_type` 约束候选类别。`planning_hint` 是稳定枚举，程序读取枚举而不匹配
中文/英文偏好关键词。仍然存在模型误分类的风险，Pydantic 只能保证结构与枚举合法。

## 地点路径

1. `schemas/request.py::PreferenceSpec` 定义 LLM 与程序之间的数据契约。
2. `agents/intake.py::create_intake_agent` 在已有结构化提取中填写新字段。
3. `workflows/search.py::execute_search_plan` 仅把 kind=place 传入 Photon。
4. `tools/places.py::resolve_preference_place` 给 Photon 传 `q/lat/lon`，最多检索
   原查询和规范名称两种查询。目的地中心只影响检索排序，程序另外检查 100 km 范围。
5. 对每个候选独立校验坐标、供应商名称/别名和 OSM 类型。名称先做 Unicode、大小写和
   标点/空白规范化；精确名称或供应商别名优先，只有满足“至少两个词、覆盖期望名称
   至少三分之二、且没有额外词”的保守缩写才降级接受。错误候选不会遮住后续有效候选。
6. `waterfront` 对应结构化 OSM 类型，例如 natural=bay/strait、waterway=river。
   不包含 amenity=restaurant 或 landuse=residential。远距离同名候选不能仅靠“离城市近”
   消歧，返回 ambiguous_place。
7. 明确点名的餐厅、酒店仍可使用相应 place_type；并未全面禁用这些地点类别。

这次实测的旧问题：Photon 同时返回 `Victoria Harbour` 海港、同名住宅和
`Victoria Harbour Supreme` 餐厅。旧的名称片段比较把餐厅也视为匹配，随后距离排序选中了
更近的餐厅。现在成功选中规范化后的 `way:851848400`，类型为 `natural=strait`。

验证了地理实体，不代表验证了开放时间、可进入性或可到访入口。
海港的中心坐标是 `22.2903682, 114.1798246`，不是道路入口。PlaceCandidate 和
ActivityItem 用 `route_eligible=false` 保留这个区别；OSRM 和 transit adapter 都排除它。
JSON 保留地理实体，Markdown 明确展示入口未核验。未来可再查询海滨步道或观景点作为
独立、带来源的 access point；当前不凭空替换。

## 室内外搭配路径

1. `planning_hint=venue_mix` 变成 ActivityQuery 和 AttractionQuery 的 `venue_mix=balanced`。
2. Overpass 在原景点、博物馆和画廊查询外增加公园、花园等有明确室外类型的候选。
3. 对已知室内和室外候选交替排序，再按 limit 截断；不能先全截成博物馆再要求程序平衡。
4. `assign_activities` 在开放日、天气和每日容量约束下，优先安排互补的室内外地点。
   类型只来自 OSM 标签/类别，unknown 不伪装成已知。
5. 非 place 的偏好不生成地图点，也不生成假活动。偏好原文仍保存在最终 preferences。
6. 若用户要求 indoor_only/outdoor_only，通用景点池必须有相应类型的正面证据。
   用户明确点名的地点仍保留，不在这层静默删除。
7. 最终缺少任一类已核验候选时，计划标记 partial，notes 说明搭配尚未满足。

这是受约束的启发式分配，不是全局路线最优解；也不保证每一天都有足够候选。

## 验证与复盘

```bash
PYTHONPATH=src uv run pytest -q
PYTHONPATH=src uv run pytest -q -m live -k photon
```

新增 `tests/test_place_intent.py` 覆盖：同名错误类型、Unicode 名称、明确命名的餐厅、
完整规范名称重试、同名歧义、坏候选、坐标范围、海域路线排除、候选池搭配、严格室内偏好、
typed request 到最终计划的传递，以及公共 API 超时后的偏好保留。另修正了一条旧的离线
路线测试，避免它意外调用真实 OSRM。

中文真实 CLI 输入：

```text
我从北京出发，2026年10月28日去香港玩3天，想去维多利亚港，喜欢室内外结合，请同时给我驾车和公共交通路线。
```

本地日志：`.local/preference-fix-20261023.jsonl`，run_id 为
`990feec7cc444528b750e913d0da2cae`。这次 Intake 和 Planner 成功，没有模型替换或确定性
叙事降级。目的地、天气和维多利亚港成功；Overpass 超时，结果为 partial，后两天没有
已核验活动。系统没有声称室内外搭配已完成，也没有编造路线。该案例关闭了 RAG，专门
核对本次修改涉及的模型到工具链路。

本轮实际验证：离线测试 `254 passed, 14 deselected`；固定证据的 workflow 回归
`10/10`；真实 Photon 测试（Tokyo Tower、Victoria Harbour）`2 passed`。
另用真实 Intake 模型解析英文 Vienna/Danube 请求，得到 activities+guide、2 天、
waterfront 地点和 venue_mix 约束，日志保存在 `.local/preference-intake-20261023.jsonl`。
这些是结构/回归与有限 live 验证结果，不是开放用户分布上的成功率。

## 仍有的限制

- 先比较供应商名称及其返回的多语言名称/别名，再允许保守的名称缩写；
  未提供的同义词或不同转写仍可能被拒绝，precision 与 recall 存在取舍。
- place_type 仍取决于 Intake 语义理解；枚举通过不代表理解一定正确。
- 100 km 范围和 1 km 同名歧义阈值是演示项目启发式边界，不是地理事实。
- Overpass 返回的原始池受上限和公共实例负载影响；即使正确查询也可能不全。
- 公共交通未配置真实 provider；航班酒店仍为非实时 demo adapter。

依据：[Photon API](https://github.com/komoot/photon/blob/master/docs/api-v1.md)
明确提供 q、lat/lon、OSM 元数据；[OSM natural=bay](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dbay)
说明水域地理类型。偏好枚举、完整名称核验、路线资格和失败规则是本项目的设计选择。
