"""Optional read-only MCP server for the travel evidence tools.

The main CLI does not require MCP. This module is a protocol boundary for
clients such as an MCP inspector or another Agent; it reuses the same typed
adapters and never adds booking or payment capabilities.
"""

from __future__ import annotations

import os
import sys
from contextlib import redirect_stdout
from datetime import date
from typing import Any

from travel_agent.rag.vector import DEFAULT_EMBEDDING_MODEL, create_knowledge_retriever
from travel_agent.tools.geocoding import resolve_destination
from travel_agent.tools.weather import get_weather_forecast


def _dump(value: Any) -> dict[str, Any] | list[dict[str, Any]]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _call_quietly(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Keep tool diagnostics off stdout, which is the MCP stdio channel."""

    with redirect_stdout(sys.stderr):
        return function(*args, **kwargs)


def create_mcp_server():
    """Build an MCP server lazily so the normal CLI has no MCP dependency."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "MCP server requires the optional dependency; run `uv sync --extra mcp`."
        ) from error

    server = FastMCP("travel-readonly-tools")

    @server.tool(description="Resolve a city to a public geocoding result.")
    def resolve_city(destination: str) -> dict[str, Any]:
        return _dump(_call_quietly(resolve_destination, destination))

    @server.tool(description="Get weather evidence for a city and date range.")
    def weather(destination: str, start_date: str, days: int = 1) -> dict[str, Any]:
        location = _call_quietly(resolve_destination, destination)
        if location.status != "ok" or location.data is None:
            return _dump(location)
        result = _call_quietly(
            get_weather_forecast,
            location.data,
            date.fromisoformat(start_date),
            days,
        )
        return _dump(result)

    @server.tool(description="Search the local travel Markdown knowledge base.")
    def search_knowledge(query: str, top_k: int = 5) -> dict[str, Any]:
        root = os.getenv("TRAVEL_KNOWLEDGE_DIR", "knowledge")
        knowledge = create_knowledge_retriever(
            root,
            engine=os.getenv("RAG_ENGINE", "hybrid"),
            db_dir=os.getenv("RAG_DB_DIR", ".local/chroma_rag"),
            model_name=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        )
        if knowledge is None:
            return {"provider": "none", "query": query, "hits": []}
        return {
            "provider": knowledge.provider,
            "query": query,
            "hits": [hit.model_dump(mode="json") for hit in knowledge.search(query, top_k=top_k)],
        }

    return server


def main() -> None:
    create_mcp_server().run("stdio")


if __name__ == "__main__":
    main()
