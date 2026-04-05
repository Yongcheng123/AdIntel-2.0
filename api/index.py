"""Vercel entry point for the AdIntel MCP server.

Exposes the MCP server over HTTP using FastMCP's streamable-HTTP transport
(Starlette ASGI app). All database reads go to the Neon PostgreSQL instance
configured via ADINTEL_DATABASE_URL.

No browser automation or collection code is loaded here — this is read-only.
"""

from __future__ import annotations

import os
import sys
import traceback

# Vercel installs deps from requirements-vercel.txt but does NOT install the
# adintel package itself (which lives in src/).  Add src/ to the path so
# imports resolve correctly on the serverless runtime.
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

try:
    from adintel.mcp.server import create_mcp_server
    app = create_mcp_server().streamable_http_app()
    _boot_error: str | None = None
except Exception:
    _boot_error = traceback.format_exc()
    app = None  # defined below


# If boot failed, surface the full traceback via HTTP so it is visible
# without needing Vercel log access.
if app is None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def _error(request: Request) -> JSONResponse:
        return JSONResponse(
            {"boot_error": _boot_error, "sys_path": sys.path},
            status_code=500,
        )

    app = Starlette(routes=[Route("/{path:path}", _error), Route("/", _error)])
