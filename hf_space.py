"""Standalone Hugging Face Space entrypoint for the AdIntel MCP server.

This file is intentionally separate from `api/index.py` so Vercel can keep
using the existing entrypoint unchanged.

Configure the Space with:
  - `sdk: docker`
  - `app_port: 7860`
  - an env secret named `MCP_API_KEY` or `ADINTEL_MCP_API_KEY`

Then run:
  uvicorn hf_space:app --host 0.0.0.0 --port 7860
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from adintel.mcp.server import create_mcp_server  # noqa: E402


class APIKeyGate:
    def __init__(self, app):
        self.app = app
        self.expected_key = os.getenv("MCP_API_KEY") or os.getenv("ADINTEL_MCP_API_KEY")
        self.oauth_enabled = bool(
            os.getenv("BASE_URL")
            and os.getenv("GOOGLE_CLIENT_ID")
            and os.getenv("GOOGLE_CLIENT_SECRET")
        )

    @staticmethod
    def _extract_key(scope) -> str | None:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        auth = headers.get(b"authorization")
        if auth:
            value = auth.decode("utf-8", errors="ignore").strip()
            if value.lower().startswith("bearer "):
                return value[7:].strip()
            return value or None

        api_key = headers.get(b"x-api-key")
        if api_key:
            value = api_key.decode("utf-8", errors="ignore").strip()
            return value or None

        return None

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("path") == "/health"
            or not self.expected_key
            or self.oauth_enabled
        ):
            await self.app(scope, receive, send)
            return

        provided_key = self._extract_key(scope)
        if provided_key != self.expected_key:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"www-authenticate", b'Bearer realm="AdIntel MCP"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized\n"})
            return

        await self.app(scope, receive, send)


app = APIKeyGate(create_mcp_server().streamable_http_app())
