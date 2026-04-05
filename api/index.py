"""Vercel entry point for the AdIntel MCP server.

Exposes the MCP server over HTTP using FastMCP's streamable-HTTP transport
(Starlette ASGI app). All database reads go to the Neon PostgreSQL instance
configured via ADINTEL_DATABASE_URL.

No browser automation or collection code is loaded here — this is read-only.
"""

from __future__ import annotations

from adintel.mcp.server import create_mcp_server

# Vercel's Python runtime looks for a module-level `app` callable.
# FastMCP's streamable_http_app() returns a Starlette ASGI app with
# the MCP endpoint mounted at /mcp (POST + SSE upgrade).
app = create_mcp_server().streamable_http_app()
