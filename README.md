---
title: AdIntel MCP
sdk: docker
app_port: 7860
short_description: Public AdIntel MCP server with key-based access
---

# AdIntel

AdIntel is a Python-first scraping and intelligence workspace for mobile advertisers.

It does four things:

1. runs SensorTower, SocialPeta, AppFollow, and Otterly collection on a remote desktop with a logged-in browser session
2. stores normalized data in Postgres (Neon)
3. exposes an MCP server so clients like Claude can query that data
4. supports on-demand advertiser refresh via a job queue — MCP enqueues a job when data is missing or stale, and the worker on the remote desktop picks it up and runs the existing scraper

## Architecture

```text
Remote desktop
  -> logged-in Playwright browser (SensorTower, SocialPeta, AppFollow)
  -> adintel worker run --use-cdp
  -> polls jobs table, runs existing scraper
  -> writes to Postgres

Postgres (Neon)
  -> shared source of truth
  -> advertisers table + per-platform metric tables
  -> scrape_runs / scrape_run_metrics (run health)
  -> jobs table (on-demand refresh queue)
  -> requested_advertisers (onboarding backlog)

Hugging Face MCP (hosted)
  -> reads from Postgres
  -> exposes MCP tools over HTTP with API key auth
  -> enqueues jobs when data is stale or missing
  -> does not scrape directly
```

The key separation is:

- collection runs on the remote desktop (browser session stays there)
- the database is shared
- the hosted MCP server is read-only except for enqueuing jobs

## What This MCP Can Do

The AdIntel MCP server can:

- list advertisers and their data freshness
- return full advertiser summaries (downloads, DAU, retention, SOV, demographics, reviews, creatives, ASO keywords)
- compare advertisers side by side with gap analysis
- show metric time series
- show SocialPeta display-ad creative analysis
- show GEO (AI search visibility) data via Otterly
- show AppFollow review sentiment trends and keyword analysis
- show market-wide top app rankings
- show collection status, health, and stale-data alerts
- log advertiser requests for later onboarding
- trigger on-demand advertiser refresh (creates a job if data is missing or stale)
- return job status and partial scrape progress
- run read-only SQL queries against the database
- return the canonical schema text

## MCP Tools

### Advertiser Data

- `list_advertisers` — list all tracked advertisers with data freshness metadata
- `get_advertiser_summary` — full SensorTower dashboard for one advertiser
- `get_socialpeta_summary` — SocialPeta display-ad creative analysis
- `get_socialpeta_comparison` — multi-advertiser creative comparison
- `get_full_comparison` — side-by-side downloads/DAU timeseries, SOV, gap analysis
- `get_metric_timeseries` — historical rows for a specific metric/advertiser/country
- `get_market_top_apps` — market-wide top app rankings by category

### GEO (Generative Engine Optimization)

- `get_geo_summary` — AI search visibility by engine, sentiment, cited domains
- `compare_geo_visibility` — competitive GEO comparison
- `get_geo_data_availability` — coverage report for GEO tables

### AppFollow Reviews

- `get_appfollow_reviews` — individual reviews filtered by sentiment/rating/country
- `get_appfollow_sentiment_trend` — daily sentiment distribution
- `get_appfollow_keyword_analysis` — top review keywords by sentiment
- `compare_appfollow_reviews` — cross-advertiser review sentiment comparison

### Collection Management

- `get_collection_status` — data availability matrix and stale-data alerts
- `request_advertiser` — log a missing advertiser for later onboarding
- `list_requested_advertisers` — view the onboarding backlog

### On-Demand Refresh (Job Queue)

- `request_advertiser_refresh` — check freshness; if stale or missing, enqueue a job for the remote worker. Returns immediately with job ID. Duplicate jobs for the same advertiser/platform are collapsed.
- `get_job_status` — status for a specific job, including linked `scrape_run_metrics` for partial progress
- `list_jobs` — recent job history, filterable by advertiser and status

### Database

- `run_query` — read-only SQL (SELECT only) against the AdIntel database
- `read_schema_text` — return the canonical SQL schema

## Data Structures

### Shared Operational Tables

These tables are cross-platform and should stay generic:

- `advertisers`
- `scrape_runs`
- `scrape_run_metrics`
- `requested_advertisers`
- `jobs` — on-demand refresh queue; workers poll this and link back to `scrape_runs`

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

### SocialPeta Tables

For competitor creative strategy work, SocialPeta should be stored in three provider-prefixed tables:

- `socialpeta_creatives`
  - one row per creative from SocialPeta `creative/list`, including verified fields like `ad_key`, `advertiser_name`, `ecom_advertiser_id`, `page_name`, `title`, `body`, `message`, `call_to_action`, `platform`, `first_seen`, `last_seen`, `days_count`, `impression`, `heat`, `all_exposure_value`, `preview_img_url`, `resource_urls`, and `raw_payload`
- `socialpeta_creative_channels`
  - one row per creative-channel pair so you can measure distribution across TikTok, Meta, Unity, and other channels from fields like `fb_merge_channel`
- `socialpeta_creative_tags`
  - reserved for future per-creative tag enrichment after tag assignment fields are verified

### SocialPeta Competitor Groups

Use `config/advertisers.yaml` as the source of truth for individual advertisers and a second file such as `config/socialpeta_groups.yaml` to define comparison sets.

Example:

```yaml
groups:
  - advertiser: Chime
    country: US
    competitors:
      - Current
      - Dave
      - MoneyLion
```

This lets collection stay brand-by-brand while analysis can automatically look up the competitor set for a target advertiser.

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
- can alert to Slack or email

Basic setup:

```bash
export HF_MCP_URL="https://yongchengmu-adintel-mcp.hf.space/"
export HF_MCP_EMAIL_TO="you@example.com"
export HF_MCP_EMAIL_FROM="you@example.com"
export HF_MCP_SMTP_HOST="smtp.gmail.com"
export HF_MCP_SMTP_PORT="587"
export HF_MCP_SMTP_USERNAME="you@example.com"
export HF_MCP_SMTP_PASSWORD="your-app-password"
./scripts/monitor_hf_mcp.py
```

One-shot probe:

```bash
./scripts/monitor_hf_mcp.py --single-run
```

Recommended env vars:

- `HF_MCP_SLACK_WEBHOOK_URL`
- `HF_MCP_EMAIL_TO`
- `HF_MCP_EMAIL_FROM`
- `HF_MCP_SMTP_HOST`
- `HF_MCP_SMTP_PORT`
- `HF_MCP_SMTP_USERNAME`
- `HF_MCP_SMTP_PASSWORD`
- `HF_MCP_ALERT_AFTER_FAILURES=2`
- `HF_MCP_ALERT_ON_RECOVERY=true`
- `HF_MCP_SUCCESS_HOURS=4`
- `HF_MCP_FAILURE_MINUTES=30`

Example `cron` entry:

```cron
0 * * * * /Users/yongcheng/Desktop/projects/AdIntel/.venv/bin/python /Users/yongcheng/Desktop/projects/AdIntel/scripts/monitor_hf_mcp.py >> /Users/yongcheng/Desktop/projects/AdIntel/state/hf_mcp_monitor.log 2>&1
```

## Safe Pushes

If your local `main` has diverged from GitHub or Hugging Face, use:

```bash
scripts/push_safe.sh --remote both
```

That script starts from each remote's current `main`, cherry-picks your chosen
commit, and pushes only that change. By default it uses `HEAD`. For Hugging
Face, set `HF_TOKEN` first if the `hf` remote does not already have credentials.

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

### Platform Runbook (Recommended Commands)

Use this section as the quick reference for future runs.

#### SensorTower

Login (once per session):

```bash
./.venv/bin/adintel login sensortower
```

Run all advertisers from `config/advertisers.yaml`:

```bash
bash scripts/run_local_to_server.sh
```

Run one advertiser:

```bash
RUN_ALL_FROM_CONFIG=false ADVERTISER_NAME=Chime bash scripts/run_local_to_server.sh
```

#### Otterly

Run batch from `config/otterly_batch.yaml`:

```bash
bash scripts/run_otterly_batch.sh
```

Use a different batch config:

```bash
CONFIG_FILE=config/otterly_batch.yaml bash scripts/run_otterly_batch.sh
```

#### SocialPeta

Login (once per session):

```bash
./.venv/bin/adintel login socialpeta
```

Run only missing advertisers (default, recommended for daily use):

```bash
bash scripts/run_socialpeta_to_server.sh
```

Force full refresh for all configured advertisers:

```bash
MODE=all bash scripts/run_socialpeta_to_server.sh
```

Run one advertiser only:

```bash
RUN_ALL_FROM_CONFIG=false ADVERTISER_NAME=Chime bash scripts/run_socialpeta_to_server.sh
```

##### AppFollow

Login (once per session):

```bash
./.venv/bin/adintel login appfollow
```

Run only missing advertisers (default, recommended for daily use):

```bash
bash scripts/run_appfollow_to_server.sh
```

Force full refresh for all configured advertisers:

```bash
MODE=all bash scripts/run_appfollow_to_server.sh
```

Test one batch:

```bash
TEST=true bash scripts/run_appfollow_to_server.sh
```

#### On-Demand Worker

Start the worker on the remote desktop. It polls the `jobs` table and runs the existing scraper for each claimed job:

```bash
./.venv/bin/adintel worker run --use-cdp
```

Options:

```bash
# Set polling interval (seconds between polls when queue is empty)
./.venv/bin/adintel worker run --use-cdp --poll-interval 15

# Limit to specific platforms
./.venv/bin/adintel worker run --use-cdp --platforms sensortower

# Process a fixed number of jobs then exit (useful for cron)
./.venv/bin/adintel worker run --use-cdp --max-jobs 5

# Verbose logging
./.venv/bin/adintel worker run --use-cdp --verbose
```

Multiple worker processes can run concurrently — the queue uses `SELECT ... FOR UPDATE SKIP LOCKED` so each job is claimed by exactly one worker.

Once the worker is running, you can trigger collection from any MCP client:

```
request_advertiser_refresh("Chime", force=True)
# → {status: "queued", job: {id: 42, status: "queued", ...}}

get_job_status(42)
# → {job: {...}, scrape_run: {...}, metrics: [{metric_name: "downloads", status: "success", records_written: 90}, ...]}
```

### Check Latest Run Status

After any collection run, check recent statuses:

```bash
./.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
from adintel.core.settings import get_settings

engine = create_engine(get_settings().database_url)
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT id, advertiser_name, platform, status, started_at, finished_at
        FROM scrape_runs
        ORDER BY id DESC
        LIMIT 20
    """)).fetchall()
    for row in rows:
        print(tuple(row))
PY
```

### Login To SensorTower

```bash
./.venv/bin/adintel login sensortower
```

This saves local browser state under `state/browser/`.

### Login To Otterly

```bash
./.venv/bin/adintel login otterly
```

This saves local browser state under `state/browser/otterly`.

### Login To SocialPeta

```bash
./.venv/bin/adintel login socialpeta
```

This saves local browser state under `state/browser/socialpeta`.

### Collect One Advertiser

```bash
./.venv/bin/adintel collect advertiser Chime --platform sensortower --verbose
```

### Collect SocialPeta Display Ads

```bash
./.venv/bin/adintel collect socialpeta-display-ads --query "temu" --pages 3 --verbose
```

This uses the verified `display-ads` JSON API behind the logged-in page and saves rows into `socialpeta_creatives` and `socialpeta_creative_channels`.

### Run SocialPeta Batch Collection

```bash
./scripts/run_socialpeta_to_server.sh
```

This reads advertisers from `config/advertisers.yaml`, looks up competitor sets from `config/socialpeta_groups.yaml`, dedupes overlapping targets, and saves SocialPeta display-ad data into the database.

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

For Coolify deployment of the MCP host only, see
[docs/coolify-deploy.md](/Users/yongcheng/Desktop/projects/AdIntel-2.0/docs/coolify-deploy.md).

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

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `ADINTEL_DATABASE_URL` | SQLAlchemy Postgres URL | `postgresql+psycopg://postgres:postgres@localhost:5432/adintel` |
| `ADINTEL_DATA_STALE_HOURS` | Hours before MCP considers data stale | `24` |
| `ADINTEL_WORKER_POLL_INTERVAL_S` | Worker idle sleep seconds | `10` |
| `ADINTEL_CDP_URL` | Chrome DevTools Protocol URL for remote browser | `http://127.0.0.1:9222` |
| `ADINTEL_ALERT_WEBHOOK_URL` | Slack/webhook URL for collection alerts | — |
| `ADINTEL_APPFOLLOW_WORKSPACE` | AppFollow workspace ID | — |
| `MCP_API_KEY` / `ADINTEL_MCP_API_KEY` | API key for Hugging Face hosted MCP | — |

## Example Questions

Ask the MCP things like:

- `List all advertisers in the database.`
- `Give me a full summary for Chime.`
- `Compare Chime vs Dave vs Current on downloads and SOV.`
- `Which advertisers have stale or missing SensorTower data?`
- `Show GEO visibility for Chime across all AI engines.`
- `What are the top review keywords for Chime in the last 30 days?`
- `Trigger a refresh for Coinbase — data looks old.`
- `What's the status of job 42?`
- `Show me the top 20 Finance apps in the US this month.`

## Project Layout

```text
src/adintel/         application package
  cli/               CLI commands (includes `worker run`)
  collectors/        collection orchestration (CollectorService)
  platforms/         SensorTower, SocialPeta, AppFollow, Otterly logic and parsers
  db/                SQLAlchemy models and repositories (incl. JobRepository)
  mcp/               MCP tool layer (22 tools)
  core/              settings and shared models
  worker.py          on-demand job worker (polls jobs table, runs existing scraper)

config/              advertiser YAML catalogs
  advertisers.yaml   main advertiser catalog
  socialpeta_groups.yaml  competitor group definitions
  appfollow_groups.yaml   AppFollow group definitions

sql/schema.sql       canonical schema (idempotent, applied on startup)
sql/migrations/      one-time structural migrations
scripts/             helper scripts for collection and migration
hf_space.py          Hugging Face MCP entry point (API key auth)
api/index.py         Vercel MCP entry point
tests/               pytest suite
```

## Tests

```bash
./.venv/bin/pytest tests/ -v
```
