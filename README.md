# AdIntel

AdIntel is a Python-first competitive intelligence workspace. It scrapes ad and app intelligence platforms locally, writes normalized data to a shared PostgreSQL database (Neon), and exposes a read-only MCP server so Claude can query the data.

## Data Flow

```
Your Mac (local only)              Neon PostgreSQL             Vercel (MCP server)
──────────────────────────         ─────────────────           ────────────────────
Browser scrapes SensorTower
  └─ adintel collect …       ───→  st_downloads
  └─ run_local_to_server.sh        st_reviews          ←───   adintel MCP tools
                                   st_rankings                 served to Claude
                                   scrape_runs, …
```

**Collection runs locally. Vercel is read-only. Neon is the shared truth.**

---

## Where Data Goes — At a Glance

| Command / Script | Writes to Neon? | Notes |
|---|---|---|
| `adintel login sensortower` | ❌ Local only | Saves browser session to `state/browser/` |
| `adintel collect advertiser <name>` | ✅ Yes | Writes metric rows to Neon via `ADINTEL_DATABASE_URL` |
| `adintel collect stale` | ✅ Yes | Same as above, for all stale advertisers |
| `bash scripts/run_local_to_server.sh` | ✅ Yes | Applies schema + syncs catalog + runs collection |
| `adintel advertisers sync-catalog` | ✅ Yes | Syncs `config/advertisers.yaml` into the `advertisers` table |
| `adintel init-db` | ✅ Yes | Applies `sql/schema.sql` to the configured database |
| `adintel mcp` | ❌ Local only | Starts a local stdio MCP server (for Claude Desktop) |
| Vercel MCP server | ❌ Read only | Only queries Neon — never writes |

---

## Project Layout

```
src/adintel/         application package
  cli/               CLI entry point (adintel …)
  collectors/        orchestration layer (CollectorService)
  platforms/         SensorTower collector + parsers; AdClarity stub
  db/                SQLAlchemy models, repositories, session management
  mcp/               MCP server (FastMCP)
  core/              settings, models, browser manager, alerts

config/              YAML advertiser catalog (edit this to add advertisers)
sql/schema.sql       canonical PostgreSQL schema (source of truth)
scripts/             run_local_to_server.sh, migrate_server_db.sh
api/index.py         Vercel entry point for the MCP HTTP server
tests/               pytest test suite
```

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
playwright install chromium
```

Copy the example environment file and fill in your Neon credentials:

```bash
cp .env.example .env
# Edit .env and set ADINTEL_DATABASE_URL to your Neon pooler URL
```

Apply the schema and load your advertiser catalog:

```bash
adintel init-db
adintel catalog validate
adintel advertisers sync-catalog
```

---

## Collecting Data

**1. Log in to SensorTower** (one-time, saves a browser session locally):

```bash
adintel login sensortower
# A browser window opens — complete the login, then press Enter.
# Session is saved to state/browser/ and reused on future runs.
```

**2. Collect a single advertiser** (writes to Neon):

```bash
adintel collect advertiser Chime --platform sensortower --verbose
```

**3. Collect all stale advertisers** (writes to Neon):

```bash
adintel collect stale --platform sensortower --verbose
```

**4. One-command batch run** (schema + catalog sync + collection → Neon):

```bash
# Set your database URLs first (or export them in your shell profile):
export SERVER_DATABASE_URL='postgresql://user:pass@host/db?sslmode=require'
export ADINTEL_DATABASE_URL='postgresql+psycopg://user:pass@pooler-host/db?sslmode=require'

bash scripts/run_local_to_server.sh
```

**Run all advertisers from config:**
```bash
RUN_ALL_FROM_CONFIG=true bash scripts/run_local_to_server.sh
```

**Run a specific advertiser:**
```bash
ADVERTISER_NAME=Chime bash scripts/run_local_to_server.sh
```

**Dry-run (shows commands without executing):**
```bash
DRY_RUN=true bash scripts/run_local_to_server.sh
```

**Use an existing browser via CDP:**
```bash
USE_CDP=true bash scripts/run_local_to_server.sh
```

---

## Managing Advertisers

```bash
# List all advertisers in the database
adintel advertisers list

# Sync config/advertisers.yaml → database
adintel advertisers sync-catalog

# Add or update one advertiser manually
adintel advertisers upsert --name Chime --category Finance --domain chime.com \
  --sensortower-unified-app-id <uai>

# Batch-onboard from a YAML file (searches SensorTower for app IDs)
adintel advertisers onboard-batch --input config/onboarding.example.yaml
```

---

## MCP Server

AdIntel supports two MCP connection styles:

- local `stdio` if you want the MCP server to run on your machine
- remote HTTP if you want an always-on MCP endpoint on Vercel

Use local `stdio` when you are developing locally or want full control over the environment.
Use remote HTTP when you want Claude, Codex, or Antigravity to connect to the same shared Neon-backed data without starting a local process first.

### Current Hosted Endpoints

The current Vercel deployment exposes:

- root info page: `https://adintel-delta.vercel.app/`
- health check: `https://adintel-delta.vercel.app/health`
- MCP endpoint: `https://adintel-delta.vercel.app/api/mcp`

### Before You Install Any Client

Make sure the backend is healthy first:

1. Open `https://adintel-delta.vercel.app/health`
2. Confirm it returns:

```json
{"ok": true}
```

If `/health` is not working, fix the Vercel deployment first. Client setup will not work until that endpoint is healthy.

### Local MCP Server

Start the MCP server locally. It will read from whichever database `ADINTEL_DATABASE_URL` points to.

```bash
adintel mcp
# or
adintel-mcp
```

This is the right choice if:

- you want to run everything on your machine
- you want to connect a desktop client to a local process
- you want to point the MCP server at a local database or a custom database URL

### Hosted MCP Server On Vercel

The Vercel deployment serves the same MCP tools over HTTP. Collection still runs locally and writes to Neon. Vercel only reads from Neon.

Deploy flow:

1. Push this repo to GitHub
2. Import the repo in [vercel.com](https://vercel.com)
3. Add Vercel environment variables:
   - `ADINTEL_DATABASE_URL`
     Set this to your Neon pooled SQLAlchemy URL, for example `postgresql+psycopg://...`
   - `ADINTEL_AUTO_APPLY_SCHEMA`
     Set this to `false`
4. Deploy
5. Verify:
   - `https://adintel-delta.vercel.app/health`
   - `https://adintel-delta.vercel.app/api/mcp`

Important:

- Vercel is read-only for this project
- browser automation and Playwright collection still run on your machine
- the hosted MCP server is for querying Neon-backed data, not scraping SensorTower

### Install In Claude Desktop

Claude Desktop usually works best with the `mcp-remote` bridge for remote HTTP MCP servers.

Config file on macOS:

`~/Library/Application Support/Claude/claude_desktop_config.json`

Add:

```json
{
  "mcpServers": {
    "adintel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://adintel-delta.vercel.app/api/mcp",
        "--transport",
        "http-only"
      ]
    }
  }
}
```

Requirements:

- Node.js installed locally
- `npx` available in your shell

After saving the file:

1. Fully quit Claude Desktop
2. Reopen it
3. Confirm the `adintel` MCP server appears

If you want to use the local MCP server instead of Vercel, use:

```json
{
  "mcpServers": {
    "adintel": {
      "command": "/Users/yongcheng/Desktop/projects/AdIntel/.venv/bin/adintel-mcp",
      "env": {
        "ADINTEL_DATABASE_URL": "postgresql+psycopg://user:pass@pooler-host/db?sslmode=require"
      }
    }
  }
}
```

### Install In Claude Code

Claude Code supports remote HTTP MCP directly.

Add the hosted MCP server:

```bash
claude mcp add --transport http adintel https://adintel-delta.vercel.app/api/mcp
```

Verify it is registered:

```bash
claude mcp list
```

If you want to use the local stdio server instead, start `adintel mcp` locally and configure Claude Code to use that local command-based server instead of the hosted URL.

### Install In Codex

Codex uses `~/.codex/config.toml`.

Remote hosted MCP:

```toml
[mcp_servers.adintel]
url = "https://adintel-delta.vercel.app/api/mcp"
```

Local stdio MCP:

```toml
[mcp_servers.adintel]
command = "/Users/yongcheng/Desktop/projects/AdIntel/.venv/bin/adintel-mcp"

[mcp_servers.adintel.env]
ADINTEL_DATABASE_URL = "postgresql+psycopg://user:pass@pooler-host/db?sslmode=require"
```

After editing the file, restart Codex.

### Install In Antigravity

Open AntiGravity and configure the MCP server from the MCP settings panel:

1. Open the agent panel
2. Click `...`
3. Open `MCP Servers`
4. Choose `Manage MCP Servers`
5. Open the raw config file

Typical config file path:

- macOS/Linux: `~/.gemini/antigravity/mcp_config.json`
- Windows: `C:\\Users\\<USERNAME>\\.gemini\\antigravity\\mcp_config.json`

Native HTTP config:

```json
{
  "mcpServers": {
    "adintel": {
      "serverUrl": "https://adintel-delta.vercel.app/api/mcp"
    }
  }
}
```

If native HTTP is unreliable, use `mcp-remote` instead:

```json
{
  "mcpServers": {
    "adintel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://adintel-delta.vercel.app/api/mcp"
      ]
    }
  }
}
```

### What You Can Ask The MCP

Once connected, try questions like:

- `List all advertisers in the database.`
- `Show the most recent collection runs.`
- `Give me a summary for Chime.`
- `Which advertisers have stale or missing data?`
- `Show collection health for the last 7 days.`
- `What SensorTower metrics do we have for Coinbase?`
- `Compare recent scrape status for Chime and Binance.`
- `Which advertisers were collected successfully today?`
- `Show me the latest scrape run metadata for Travel Town.`

### Troubleshooting

- `The URL returns 404 in the browser`
  `https://adintel-delta.vercel.app/api/mcp` is an MCP endpoint, not a normal web page. Check `/health` instead.

- `Claude Desktop does not show the MCP server`
  Confirm your JSON is valid, then fully quit and reopen Claude Desktop.

- `mcp-remote command not found`
  Install Node.js so `npx` is available.

- `The server appears but tools do not work`
  Check the Vercel deployment logs and confirm `ADINTEL_DATABASE_URL` is set correctly.

- `Collection is not happening on Vercel`
  That is expected. Vercel only serves the read-only MCP layer. Run collection locally with `adintel collect ...` or `bash scripts/run_local_to_server.sh`.

---

## Schema Changes

`sql/schema.sql` is the canonical schema. It is idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) and safe to re-apply.

**Apply to Neon manually:**

```bash
export SERVER_DATABASE_URL='postgresql://user:pass@host/db?sslmode=require'
bash scripts/migrate_server_db.sh
```

**When you add a column to an existing table**, also add an `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` statement at the bottom of `sql/schema.sql` so the migration is non-destructive on existing data.

---

## Platform Status

| Platform | Status |
|---|---|
| SensorTower | ✅ Active — downloads, usage, retention, impression share, demographics, rankings, reviews, creatives, ASO keywords |
| AdClarity | ⏳ Deferred — login works, data extraction pending account access |

---

## Tests

```bash
.venv/bin/pytest tests/ -v
```
