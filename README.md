---
title: AdIntel MCP
sdk: docker
app_port: 7860
short_description: Public AdIntel MCP server with key-based access
---

# AdIntel

AdIntel is a local-first scraping and intelligence workspace.

It does three things:

1. runs SensorTower collection locally with your browser session
2. stores normalized data in Neon Postgres
3. exposes a read-only MCP server so clients like Claude, Codex, and Antigravity can query that data

## Architecture

```text
Local machine
  -> SensorTower login/session
  -> Playwright collection
  -> writes to Neon

Neon Postgres
  -> shared source of truth
  -> advertiser catalog
  -> SensorTower metric tables
  -> scrape run history

Vercel MCP
  -> reads from Neon
  -> exposes tools over MCP HTTP
  -> does not scrape
```

The key separation is:

- collection runs locally
- the database is shared
- the hosted MCP server is read-only

## What This MCP Can Do

The AdIntel MCP server can:

- list advertisers stored in the database
- summarize the latest SensorTower data for an advertiser
- show collection health and alerts
- show recent collection runs and saved metadata
- return metric time series
- compare advertisers side by side
- log advertiser requests for later onboarding
- return the canonical schema text

## MCP Tools

The current hosted MCP server exposes these tools:

- `list_advertisers`
  - list advertisers currently stored in AdIntel
- `get_advertiser_summary`
  - latest SensorTower summary for an advertiser
- `request_advertiser`
  - log a missing advertiser for later onboarding
- `list_requested_advertisers`
  - view requested advertisers not yet onboarded
- `read_schema_text`
  - return the canonical SQL schema text
- `get_collection_health`
  - view freshness, failures, and recent success state
- `get_collection_alerts`
  - surface stale data and repeated collection failures
- `get_recent_collection_runs`
  - inspect recent saved scrape runs and metadata
- `get_metric_timeseries`
  - return historical metric rows for a metric/advertiser/country
- `compare_advertisers`
  - compare latest values across advertisers

## Data Structures

### Shared Operational Tables

These tables are cross-platform and should stay generic:

- `advertisers`
- `scrape_runs`
- `scrape_run_metrics`
- `requested_advertisers`

### SensorTower Tables

SensorTower data uses provider-prefixed table names:

- `sensortower_downloads`
  - downloads and revenue by date, country, OS, granularity
- `sensortower_usage`
  - DAU, time spent, sessions per day
- `sensortower_retention`
  - cohort retention values such as `d1`, `d7`, `d30`, `d60`
- `sensortower_impression_share`
  - share of voice by ad network
- `sensortower_demographics`
  - age bracket and gender split
- `sensortower_rankings`
  - app chart rankings by category and chart type
- `sensortower_reviews`
  - daily average rating and star-count breakdown
- `sensortower_review_texts`
  - individual review text, sentiment, tags, version
- `sensortower_creatives`
  - creative metadata such as creative type, network, thumbnail, duration, first seen
- `sensortower_aso_keywords`
  - keyword rank plus traffic and opportunity scores

### OtterlyAI Tables

OtterlyAI data also uses provider-prefixed table names:

- `otterlyai_prompts`
  - prompt-level AI visibility rows by brand/domain, country, engine, and query window
- `otterlyai_citations`
  - cited URL rows by brand/domain, country, engine, and query window

### Naming Convention

Use this naming convention going forward:

- shared workflow tables stay generic
- platform/provider data tables use provider prefixes

Examples:

- `sensortower_downloads`
- `adclarity_creatives`
- `otterlyai_prompts`
- `otterlyai_citations`

This is the recommended structure if you plan to add more providers later.

### SensorTower Identifier Overrides

Most advertisers only need one global App Store ID and one global Android
package:

```yaml
platforms:
  sensortower:
    unified_app_id: "..."
    publisher_id: "..."
    ios_app_id: "..."
    android_package: "..."
```

Some apps reuse the same `unified_app_id` but have country-specific store IDs.
For those cases, keep the global default and add overrides only where needed:

```yaml
platforms:
  sensortower:
    unified_app_id: "..."
    ios_app_id: "global-default-ios-id"
    ios_app_ids_by_country:
      TR: "country-specific-ios-id"
    android_package: "global.default.package"
    android_packages_by_country:
      BR: "country.specific.package"
```

AdIntel will use the country-specific override when present and automatically
fall back to the global `ios_app_id` / `android_package` for every other
country.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
playwright install chromium
```

Create your env file:

```bash
cp .env.example .env
```

Set at least:

- `ADINTEL_DATABASE_URL`

For Neon, use the SQLAlchemy-style URL:

```text
postgresql+psycopg://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

## Monitoring Hugging Face MCP

The repository includes a small adaptive monitor for the hosted Hugging Face MCP:

- [scripts/monitor_hf_mcp.py](/Users/yongcheng/Desktop/projects/AdIntel/scripts/monitor_hf_mcp.py)

It checks the Space health endpoint, then:

- waits `4 hours` after a success
- waits `30 minutes` after a failure
- can alert to Slack through an incoming webhook

Basic setup:

```bash
export HF_MCP_URL="https://yongchengmu-adintel-mcp.hf.space/"
export HF_MCP_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
./scripts/monitor_hf_mcp.py
```

One-shot probe:

```bash
./scripts/monitor_hf_mcp.py --single-run
```

Recommended env vars:

- `HF_MCP_SLACK_WEBHOOK_URL`
- `HF_MCP_ALERT_AFTER_FAILURES=2`
- `HF_MCP_ALERT_ON_RECOVERY=true`
- `HF_MCP_SUCCESS_HOURS=4`
- `HF_MCP_FAILURE_MINUTES=30`

Example `cron` entry:

```cron
0 * * * * /Users/yongcheng/Desktop/projects/AdIntel/.venv/bin/python /Users/yongcheng/Desktop/projects/AdIntel/scripts/monitor_hf_mcp.py >> /Users/yongcheng/Desktop/projects/AdIntel/state/hf_mcp_monitor.log 2>&1
```

## Database Workflow

### Canonical Schema

The source of truth is:

- [schema.sql](/Users/yongcheng/Desktop/projects/AdIntel/sql/schema.sql)

### Migration Files

Use `sql/migrations/` for structural or destructive changes such as:

- renaming tables
- renaming constraints
- data backfills
- one-time cleanup steps

Current migration example:

- [20260405_rename_st_tables_to_sensortower.sql](/Users/yongcheng/Desktop/projects/AdIntel/sql/migrations/20260405_rename_st_tables_to_sensortower.sql)

### Apply Schema And Migrations

```bash
export SERVER_DATABASE_URL='postgresql://user:pass@host/db?sslmode=require'
bash scripts/migrate_server_db.sh
```

The migration script:

1. applies `sql/schema.sql`
2. applies each SQL file in `sql/migrations/` once
3. records applied files in `adintel_migration_state`

### Testing Reset

If this is a testing database and you want a clean reset:

```bash
psql "$SERVER_DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
bash scripts/migrate_server_db.sh
./.venv/bin/adintel advertisers sync-catalog
```

## Advertiser Workflow

### Sync Advertiser Catalog

```bash
./.venv/bin/adintel advertisers sync-catalog
```

Why this matters:

- runtime collection reads advertiser identifiers from the `advertisers` table in the database
- if IDs in DB are stale or wrong, collection can run but return `empty`/`partial`
- syncing copies current identifiers from `config/advertisers.yaml` into DB before collection

`scripts/run_local_to_server.sh` runs this sync step by default (`SYNC_CATALOG=true`) for that reason.

If you prefer DB-first operations, you can disable sync and manage IDs directly in DB:

```bash
SYNC_CATALOG=false bash scripts/run_local_to_server.sh
```

### One-Command Run Order

When you run the main helper script, it does these steps in order:

1. sync catalog to the database
2. validate catalog vs DB
3. run collection for the selected advertisers

So you usually do **not** need to run `adintel advertisers sync-catalog` separately before `bash scripts/run_local_to_server.sh`.

Typical daily run:

```bash
bash scripts/run_local_to_server.sh
```

### Validate Catalog Vs DB

Use this check to catch drift before collection:

```bash
bash scripts/validate_catalog_vs_db.sh
```

The script fails non-zero when it finds:

- duplicate advertiser names after normalization
- advertisers missing in DB
- extra advertisers in DB not present in catalog
- field mismatches for category, countries, and SensorTower IDs

`scripts/run_local_to_server.sh` runs this validation by default (`VALIDATE_CATALOG_DB=true`).
Disable only if you intentionally allow temporary drift:

```bash
VALIDATE_CATALOG_DB=false bash scripts/run_local_to_server.sh
```

### List Advertisers

```bash
./.venv/bin/adintel advertisers list
```

### Upsert One Advertiser

```bash
./.venv/bin/adintel advertisers upsert \
  --name Chime \
  --category Finance \
  --domain chime.com \
  --sensortower-unified-app-id <uai>
```

### Batch Onboard Advertisers

```bash
./.venv/bin/adintel advertisers onboard-batch --input config/onboarding.example.yaml
```

Important:

- batch onboarding can still produce ambiguous or bad matches
- review results before treating them as trusted

## Collection Workflow

### Login To SensorTower

```bash
./.venv/bin/adintel login sensortower
```

This saves local browser state under `state/browser/`.

### Collect One Advertiser

```bash
./.venv/bin/adintel collect advertiser Chime --platform sensortower --verbose
```

### Collect Stale Advertisers

```bash
./.venv/bin/adintel collect stale --platform sensortower --verbose
```

### One-Command Local To Neon Run

```bash
bash scripts/run_local_to_server.sh
```

Useful variants:

```bash
RUN_ALL_FROM_CONFIG=true bash scripts/run_local_to_server.sh
ADVERTISER_NAME=Chime bash scripts/run_local_to_server.sh
USE_CDP=true bash scripts/run_local_to_server.sh
DRY_RUN=true bash scripts/run_local_to_server.sh
```

## Hosted MCP

Current hosted endpoint:

- `https://adintel-delta.vercel.app/`

This endpoint is a real MCP HTTP transport endpoint.

A plain browser or `curl` may return a `406 Not Acceptable` response because the server expects a client that accepts `text/event-stream`. That is normal.

## Install The MCP

### Claude Desktop

Config file on macOS:

- `~/Library/Application Support/Claude/claude_desktop_config.json`

Recommended config:

```json
{
  "mcpServers": {
    "adintel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://adintel-delta.vercel.app/",
        "--transport",
        "http-only"
      ]
    }
  }
}
```

Then fully restart Claude Desktop.

### Claude Code

```bash
claude mcp add --transport http adintel https://adintel-delta.vercel.app/
```

### Codex

Config file:

- `~/.codex/config.toml`

```toml
[mcp_servers.adintel]
url = "https://adintel-delta.vercel.app/"
```

### Antigravity

Native HTTP config:

```json
{
  "mcpServers": {
    "adintel": {
      "serverUrl": "https://adintel-delta.vercel.app/"
    }
  }
}
```

Or use `mcp-remote` if needed:

```json
{
  "mcpServers": {
    "adintel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://adintel-delta.vercel.app/"
      ]
    }
  }
}
```

## Example Questions

Ask the MCP things like:

- `List all advertisers in the database.`
- `Show the most recent collection runs.`
- `Give me a summary for Chime.`
- `Which advertisers have stale or missing data?`
- `Show collection health for the last 7 days.`
- `What SensorTower metrics do we have for Coinbase?`
- `Compare recent scrape status for Chime and Binance.`

## Project Layout

```text
src/adintel/         application package
  cli/               CLI commands
  collectors/        collection orchestration
  platforms/         SensorTower logic and parsers
  db/                SQLAlchemy models and repositories
  mcp/               MCP tool layer
  core/              settings and shared models

config/              advertiser YAML catalogs
sql/schema.sql       canonical schema
sql/migrations/      one-time SQL migrations
scripts/             helper scripts for migration and collection
api/index.py         Vercel MCP entry point
tests/               pytest suite
```

## Tests

```bash
./.venv/bin/pytest tests/ -v
```
