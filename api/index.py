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


def _make_app():
    """Build the ASGI app, returning a diagnostic app on import errors."""
    try:
        from adintel.mcp.server import create_mcp_server
        mcp_app = create_mcp_server().streamable_http_app()
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        async def _root(request: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "ok": True,
                    "name": "AdIntel MCP",
                    "mcp_endpoint": "/api/mcp",
                    "health": "/health",
                }
            )

        async def _health(request: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        return Starlette(
            routes=[
                Route("/", _root),
                Route("/health", _health),
                Mount("/api/mcp", app=mcp_app),
            ]
        )
    except Exception:
        import traceback
        _tb = traceback.format_exc()
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def _err(request: Request) -> JSONResponse:
            return JSONResponse(
                {"boot_error": _tb, "sys_path": sys.path},
                status_code=500,
            )

        return Starlette(routes=[Route("/", _err), Route("/{path:path}", _err)])


# app must be a module-level name so Vercel's checker can find it.
app = _make_app()
