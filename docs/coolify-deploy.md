# Coolify Deploy

This repository can be deployed to Coolify as a Dockerfile-based application.
It is meant to host the MCP server only. Keep the worker on your local machine
or another remote desktop that has the logged-in browser session.

## What To Deploy

- Use the root `Dockerfile`
- Run the `hf_space.py` entrypoint
- Expose port `7860`
- Keep the database in Postgres
- Keep the worker separate from Coolify

## Coolify Settings

- Build pack: `Dockerfile`
- Port: `7860`
- Domain: your Coolify domain or subdomain
- Start command: leave default, or use the Dockerfile `CMD`

## Required Environment Variables

- `ADINTEL_DATABASE_URL`

For the legacy API-key deployment:

- `MCP_API_KEY` or `ADINTEL_MCP_API_KEY`

For Google OAuth deployment:

- `BASE_URL=https://adintel-mcp.3.15.29.33.sslip.io`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `ALLOWED_DOMAIN=feedmob.com`

When the Google OAuth variables are present, the MCP endpoint is protected at
`/mcp` by default and the static API-key gate is bypassed. Override the path
with `ADINTEL_MCP_PATH` only if a client already depends on a different URL.

## Recommended Environment Variables

- `ADINTEL_DATA_STALE_HOURS`
- `ADINTEL_WORKER_POLL_INTERVAL_S`
- `ADINTEL_ALERT_WEBHOOK_URL`
- `ADINTEL_APPFOLLOW_WORKSPACE`
- `OAUTH_ACCESS_TOKEN_TTL_SECONDS=3600`
- `OAUTH_REFRESH_TOKEN_TTL_SECONDS=2592000`

## Google OAuth Setup

Create a Google OAuth **Web application** client in Google Cloud Console and add
this authorized redirect URI:

```text
https://adintel-mcp.3.15.29.33.sslip.io/auth/google/callback
```

The OAuth consent screen only needs `openid` and `email`. Access is restricted
server-side to Google Workspace accounts whose `hd` claim and email domain both
match `ALLOWED_DOMAIN`, which should be `feedmob.com`.

## Notes

- The hosted MCP server is read-only except for enqueueing refresh jobs.
- If you want on-demand refresh to work, make sure the local worker can reach
  the same Postgres database and a live browser session.
- The worker should run locally with:

```bash
./.venv/bin/adintel worker run --use-cdp
```

## Quick Checklist

1. Connect the GitHub repo in Coolify.
2. Select the Dockerfile build pack.
3. Set port `7860`.
4. Add `ADINTEL_DATABASE_URL`.
5. Add either the legacy API-key variables or the Google OAuth variables.
6. Set the app domain to `https://adintel-mcp.3.15.29.33.sslip.io`.
7. Deploy.
8. Check `https://adintel-mcp.3.15.29.33.sslip.io/health`.
9. For OAuth clients, use `https://adintel-mcp.3.15.29.33.sslip.io/mcp`.
