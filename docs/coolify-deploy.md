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
- `MCP_API_KEY` or `ADINTEL_MCP_API_KEY`

## Recommended Environment Variables

- `ADINTEL_DATA_STALE_HOURS`
- `ADINTEL_WORKER_POLL_INTERVAL_S`
- `ADINTEL_ALERT_WEBHOOK_URL`
- `ADINTEL_APPFOLLOW_WORKSPACE`

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
5. Add `MCP_API_KEY` or `ADINTEL_MCP_API_KEY`.
6. Deploy.
