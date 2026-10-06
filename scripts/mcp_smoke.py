"""Compare direct Python tools with the local MCP stdio server.

This is an integration smoke test, not part of the default unit-test suite:
it calls the public Open-Meteo service for a short forecast.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta
from time import perf_counter
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from travel_agent.rag.vector import DEFAULT_EMBEDDING_MODEL, create_knowledge_retriever
from travel_agent.tools.geocoding import resolve_destination
from travel_agent.tools.weather import get_weather_forecast


def _json_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _mcp_content_json(result: Any) -> Any:
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise RuntimeError(f"MCP tool returned no JSON text: {result!r}")


def _direct_weather(destination: str, start_date: date) -> tuple[Any, Any]:
    """Run direct tools while keeping their diagnostics off the JSON report."""

    with redirect_stdout(sys.stderr):
        location = resolve_destination(destination)
        weather = None
        if location.status == "ok" and location.data is not None:
            weather = get_weather_forecast(location.data, start_date, 2)
    return location, weather


async def run(destination: str, query: str, start_date: date) -> dict[str, Any]:
    direct_weather_started = perf_counter()
    direct_location, direct_weather = _direct_weather(destination, start_date)
    direct_weather_elapsed_ms = round(
        (perf_counter() - direct_weather_started) * 1000, 2
    )

    direct_knowledge_started = perf_counter()
    direct_retriever = create_knowledge_retriever(
        "knowledge",
        engine=os.getenv("RAG_ENGINE", "hybrid"),
        db_dir=os.getenv("RAG_DB_DIR", ".local/chroma_rag"),
        model_name=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
    )
    direct_knowledge = {
        "provider": direct_retriever.provider if direct_retriever else "none",
        "hits": [
            hit.model_dump(mode="json")
            for hit in (direct_retriever.search(query, top_k=2) if direct_retriever else [])
        ],
    }
    direct_knowledge_elapsed_ms = round(
        (perf_counter() - direct_knowledge_started) * 1000, 2
    )

    server_environment = os.environ.copy()
    server_environment.setdefault("TRAVEL_KNOWLEDGE_DIR", "knowledge")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "travel_agent.mcp_server"],
        env=server_environment,
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()

            mcp_weather_started = perf_counter()
            weather_result = await session.call_tool(
                "weather",
                arguments={
                    "destination": destination,
                    "start_date": start_date.isoformat(),
                    "days": 2,
                },
            )
            mcp_weather = _mcp_content_json(weather_result)
            mcp_weather_elapsed_ms = round(
                (perf_counter() - mcp_weather_started) * 1000, 2
            )
            mcp_knowledge_started = perf_counter()
            knowledge_result = await session.call_tool(
                "search_knowledge",
                arguments={"query": query, "top_k": 2},
            )
            mcp_knowledge = _mcp_content_json(knowledge_result)
            mcp_knowledge_elapsed_ms = round(
                (perf_counter() - mcp_knowledge_started) * 1000, 2
            )
    return {
        "tools_discovered": [tool.name for tool in listed.tools],
        "direct": {
            "weather_elapsed_ms": direct_weather_elapsed_ms,
            "knowledge_elapsed_ms": direct_knowledge_elapsed_ms,
            "weather": _json_dump(direct_weather),
            "knowledge": direct_knowledge,
        },
        "mcp": {
            "weather_elapsed_ms": mcp_weather_elapsed_ms,
            "knowledge_elapsed_ms": mcp_knowledge_elapsed_ms,
            "weather": mcp_weather,
            "knowledge": mcp_knowledge,
        },
        "comparison": {
            "weather_status_equal": (
                _json_dump(direct_weather).get("status") == mcp_weather.get("status")
                if direct_weather is not None
                else False
            ),
            "knowledge_provider_equal": (
                direct_knowledge["provider"] == mcp_knowledge.get("provider")
            ),
            "knowledge_chunk_ids_equal": (
                [hit["chunk_id"] for hit in direct_knowledge["hits"]]
                == [hit["chunk_id"] for hit in mcp_knowledge.get("hits", [])]
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare direct tools with MCP stdio tools")
    parser.add_argument("--destination", default="Tokyo")
    parser.add_argument("--query", default="Tokyo Tower")
    parser.add_argument(
        "--start-date",
        default=(date.today() + timedelta(days=2)).isoformat(),
        help="A date inside the Open-Meteo forecast window",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args.destination, args.query, date.fromisoformat(args.start_date)))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
