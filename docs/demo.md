# Demo walkthrough

## Input

```text
我从上海出发，2026年10月1日去东京玩三天，总预算10000元，
我想去东京塔，还喜欢动漫。
```

## Expected tool trace

```text
search_flights(Shanghai, Tokyo, 2026-10-01)          # local demo
search_flights(Tokyo, Shanghai, 2026-10-03)          # local demo
search_hotels(Tokyo, 2 nights)                       # local demo
build_flight_search_link(...)                        # no scraping
build_hotel_search_link(...)                         # no scraping
resolve_destination(Tokyo)                           # Open-Meteo
get_weather_forecast(2026-10-01, 3 days)             # Open-Meteo
resolve_preference_place(Tokyo Tower, ...)            # Photon / OSM
resolve_preference_place(Anime, ...)                  # Photon / OSM
search_attractions(Tokyo, ...)                        # Overpass / OSM
optimize_day_route(day=1..3)                         # OSRM / OSM
```

`search_attractions` 返回的地点如果有 OSM 标签，还会携带营业时间、官网、地址、电话和
无障碍字段。程序随后执行 `assign_activities`：它根据候选数量动态计算每天容量，过滤
明确闭馆日期，并把 `opening_status` 和预计停留时间写入结构化活动对象。Planner 不会
重新调用这些工具，也不能把工具返回的地点改成另一天。

如果用户明确要求餐厅或美食，额外的工具轨迹会是：

```text
search_food(Tokyo, ...)                             # Overpass / OSM
```

如果用户明确要求公共交通，路线工具会同时调用 `optimize_day_route` 和
`optimize_transit_route`。当前公共交通结果会记录为 `unavailable / not_configured`，
但驾车路线仍然保留；系统不会复用 OSRM 的驾车分钟数冒充公交时间。

## Markdown shape

Live names and weather values may change, but the validated shape is stable:

```markdown
# Tokyo 3日旅行计划

## 预算
10000 CNY（total）

## 航班选项
- Demo Air：Shanghai → Tokyo ...（本地演示）
- Demo Air：Tokyo → Shanghai ...（本地演示）

## 实时搜索入口
- 航班：Google Travel — 仅生成搜索入口，未自动读取实时价格或库存。
- 酒店：Google Travel — 仅生成搜索入口，未自动读取实时价格或库存。

## 天气参考
- 2026-10-01：...°C

## 每日行程
### Day 1：...
- Tokyo Tower（Photon / OpenStreetMap）

## 路线估算
- Day 1：...；约 ... km，驾车估算 ... 分钟
```

## JSON shape

```json
{
  "status": "complete",
  "destination": "Tokyo",
  "days": 3,
  "budget": {
    "amount": 10000,
    "currency": "CNY",
    "level": null,
    "period": "total"
  },
  "preferences": ["Tokyo Tower", "Anime"],
  "flights": [{"source_type": "demo", "is_live": false}],
  "hotels": [{"source_type": "demo", "is_live": false}],
  "food": [{"name": "Demo Sushi", "category": "restaurant", "venue_type": "unknown"}],
  "weather": [{"date": "2026-10-01", "temperature_max_c": 0}],
  "itinerary": [
    {
      "day": 1,
      "activities": [
        {
          "name": "Tokyo Tower",
          "source": "Photon / OpenStreetMap",
          "verified": true,
          "opening_hours": "Mo-Su 09:00-22:00",
          "opening_status": "open",
          "estimated_visit_minutes": 90
        }
      ]
    }
  ],
  "routes": [{"day": 1, "profile": "driving"}],
  "search_links": [{"provider": "Google Travel"}],
  "tool_status": [{"tool_name": "resolve_destination", "status": "ok"}],
  "data_sources": ["Open-Meteo Geocoding", "© OpenStreetMap contributors"]
}
```

The abbreviated JSON above demonstrates the important fields rather than a complete `TripPlan`. Use `--output json` to produce the complete validated object.
