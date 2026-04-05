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

### Option A — Local stdio (Claude Desktop / Codex)

Start the server locally. It reads from whichever database `ADINTEL_DATABASE_URL` points to.

```bash
adintel mcp
# or: adintel-mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "adintel": {
      "command": "/path/to/AdIntel/.venv/bin/adintel-mcp",
      "env": {
        "ADINTEL_DATABASE_URL": "postgresql+psycopg://user:pass@pooler-host/db?sslmode=require"
      }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.adintel]
command = "/path/to/AdIntel/.venv/bin/adintel-mcp"

[mcp_servers.adintel.env]
ADINTEL_DATABASE_URL = "postgresql+psycopg://user:pass@pooler-host/db?sslmode=require"
```

### Option B — Vercel HTTP (always-on, no local process)

The Vercel deployment serves the same MCP tools over HTTP. Collection still runs locally and writes to Neon — Vercel only reads.

**Deploy:**
1. Push the repo to GitHub
2. Connect the repo in [vercel.com](https://vercel.com) → New Project
3. Add environment variable in Vercel dashboard:
   - `ADINTEL_DATABASE_URL` = your Neon pooler URL (`postgresql+psycopg://…`)
4. Deploy — the entry point is `api/index.py`, config is `vercel.json`

**Claude Desktop with Vercel:**

```json
{
  "mcpServers": {
    "adintel": {
      "transport": {
        "type": "http",
        "url": "https://your-project.vercel.app/mcp"
      }
    }
  }
}
```

Restart Claude Desktop. The same tools (`list_advertisers`, `get_advertiser_summary`, `get_collection_health`, etc.) are available — now served from Vercel reading live Neon data.

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
