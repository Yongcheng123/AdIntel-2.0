"""Vercel entry point for the AdIntel MCP server.

Exposes the MCP server over HTTP using FastMCP's streamable-HTTP transport
(Starlette ASGI app). All database reads go to the Neon PostgreSQL instance
configured via ADINTEL_DATABASE_URL.

No browser automation or collection code is loaded here — this is read-only.
"""

from __future__ import annotations

import os
import sys

# Vercel installs deps from requirements-vercel.txt but does NOT install the
# adintel package itself (which lives in src/).  Add src/ to the path so
# imports resolve correctly on the serverless runtime.
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from adintel.mcp.server import create_mcp_server

# Vercel's Python runtime looks for a module-level `app` callable.
# FastMCP's streamable_http_app() returns a Starlette ASGI app with
# the MCP endpoint mounted at /mcp (POST + SSE upgrade).
app = create_mcp_server().streamable_http_app()
