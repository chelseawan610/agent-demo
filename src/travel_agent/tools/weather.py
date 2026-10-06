from datetime import date, timedelta

from agent_framework import tool

from travel_agent.schemas.evidence import GeoPoint, ToolResult, WeatherDay
from travel_agent.tools.http import ExternalToolError, HTTP_CLIENT


OPEN_METEO_ATTRIBUTION = "Weather data: Open-Meteo (CC BY 4.0)"


@tool(approval_mode="never_require")
def get_weather_forecast(
    point: GeoPoint,
    start_date: date | str,
    days: int,
) -> ToolResult[list[WeatherDay]]:
    """Get daily weather for a resolved destination and date range."""

    print(
        f"[tool] get_weather_forecast(start_date={start_date!r}, days={days!r}, "
        f"latitude={point.latitude!r}, longitude={point.longitude!r})"
    )
    try:
        first_day = (
            date.fromisoformat(start_date)
            if isinstance(start_date, str)
            else start_date
        )
        end_date = first_day + timedelta(days=max(days - 1, 0))
        today = date.today()
        last_forecast_day = today + timedelta(days=15)
        if first_day < today:
            return ToolResult[list[WeatherDay]].skipped(
                "Open-Meteo Forecast",
                "出发日期已经过去，未请求天气预报。",
            )
        if end_date > last_forecast_day:
            return ToolResult[list[WeatherDay]].skipped(
                "Open-Meteo Forecast",
                "出发日期尚未进入 16 天预报窗口，请临近出行时再查询。",
            )
        payload = HTTP_CLIENT.get_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "timezone": "auto",
                "start_date": first_day.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        daily = payload["daily"]
        weather = [
            WeatherDay(
                date=day,
                weather_code=daily["weather_code"][index],
                temperature_max_c=daily["temperature_2m_max"][index],
                temperature_min_c=daily["temperature_2m_min"][index],
                precipitation_probability_max=(
                    daily.get("precipitation_probability_max") or [None] * len(daily["time"])
                )[index],
            )
            for index, day in enumerate(daily["time"])
        ]
        return ToolResult[list[WeatherDay]].ok(
            "Open-Meteo Forecast", weather, attribution=OPEN_METEO_ATTRIBUTION
        )
    except (ExternalToolError, KeyError, IndexError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[list[WeatherDay]].unavailable(
            "Open-Meteo Forecast", code, str(error), attribution=OPEN_METEO_ATTRIBUTION
        )
