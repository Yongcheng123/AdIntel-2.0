"""Vercel entry point for the AdIntel MCP server.

This serves the MCP app directly at the function root. Vercel routing is much
more reliable in this shape than mounting the MCP app under a nested path.
"""

from __future__ import annotations

import os
import sys

_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from adintel.mcp.server import create_mcp_server

app = create_mcp_server().streamable_http_app()
