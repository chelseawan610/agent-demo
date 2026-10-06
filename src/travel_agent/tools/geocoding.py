from agent_framework import tool

from travel_agent.schemas.evidence import GeoPoint, ToolResult
from travel_agent.tools.http import ExternalToolError, HTTP_CLIENT


OPEN_METEO_ATTRIBUTION = "Geocoding data: Open-Meteo"


@tool(approval_mode="never_require")
def resolve_destination(destination: str) -> ToolResult[GeoPoint]:
    """Resolve a destination city to one latitude/longitude pair."""

    print(f"[tool] resolve_destination(destination={destination!r})")
    try:
        payload = HTTP_CLIENT.get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": destination, "count": 1, "language": "en", "format": "json"},
        )
        results = payload.get("results", [])
        if not results:
            return ToolResult[GeoPoint].unavailable(
                "Open-Meteo Geocoding",
                "not_found",
                f"未找到目的地：{destination}",
                attribution=OPEN_METEO_ATTRIBUTION,
            )
        result = results[0]
        return ToolResult[GeoPoint].ok(
            "Open-Meteo Geocoding",
            GeoPoint(latitude=result["latitude"], longitude=result["longitude"]),
            attribution=OPEN_METEO_ATTRIBUTION,
        )
    except (ExternalToolError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[GeoPoint].unavailable(
            "Open-Meteo Geocoding", code, str(error), attribution=OPEN_METEO_ATTRIBUTION
        )
