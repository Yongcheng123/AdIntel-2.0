# AdIntel MCP — Advertiser Intelligence for AI Assistants

AdIntel MCP is a read-only advertiser intelligence interface backed by a shared PostgreSQL database populated from **Sensor Tower** data. It gives teammates a simple, natural-language way to explore advertiser metrics, collection health, and competitive insights — without touching the database directly.

**Hosted endpoint:** `https://adintel-delta.vercel.app/`

> **Note:** The MCP is read-only. It queries already-collected data. No data collection runs through this endpoint.

---

## What AdIntel Can Do

- **List advertisers** — see every app currently tracked in AdIntel
- **Summarize an advertiser** — get the latest Sensor Tower snapshot including downloads, DAU, retention, impression share, rankings, demographics, reviews, creatives, and ASO keywords
- **Compare advertisers** — side-by-side latest metric comparison across two or more apps
- **Explore historical trends** — daily time-series data for up to 90 days per metric
- **Check data health** — staleness, consecutive failures, and collection alerts for any advertiser
- **Inspect collection runs** — recent run history with status, timing, and records written
- **Request new advertisers** — log onboarding requests so the team can prioritize them
- **Read the schema** — retrieve the canonical SQL schema for reference

---

## Installation

---

### Claude

#### Claude.ai Chat and Cowork (Web)

No config files needed. Use the Connectors UI. Works in both **Chat** and **Cowork** tabs.

1. Open [claude.ai](https://claude.ai)
2. Go to **Profile → Settings → Connectors**
3. Click **"+"** → **"Add custom connector"**
4. Enter the URL and click **Add**:

<button onclick="copyCommand('url-endpoint')">Copy</button>

```
https://adintel-delta.vercel.app/
```

<pre id="url-endpoint" style="display:none">https://adintel-delta.vercel.app/</pre>

**To enable in a conversation:** Click **"+"** at the bottom of chat → **Connectors** → toggle **AdIntel** on.

---

#### Claude Desktop

Edit your config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

> Claude Desktop requires the `mcp-remote` wrapper — it cannot connect to HTTP servers directly.

<button onclick="copyCommand('claude-desktop-json')">Copy</button>

```json
{
  "mcpServers": {
    "AdIntel": {
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

<pre id="claude-desktop-json" style="display:none">{
  "mcpServers": {
    "AdIntel": {
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
}</pre>

Save the file and **fully restart Claude Desktop**.

---

#### Claude Code (CLI)

Run once to register the server:

<button onclick="copyCommand('claude-code-cmd')">Copy</button>

```bash
claude mcp add --transport http AdIntel https://adintel-delta.vercel.app/
```

<pre id="claude-code-cmd" style="display:none">claude mcp add --transport http AdIntel https://adintel-delta.vercel.app/</pre>

Verify it was added:

<button onclick="copyCommand('claude-code-list')">Copy</button>

```bash
claude mcp list
```

<pre id="claude-code-list" style="display:none">claude mcp list</pre>

AdIntel tools are available in all Claude Code sessions immediately.

---

### Codex

#### Option 1 — CLI (quickest)

<button onclick="copyCommand('codex-cli')">Copy</button>

```bash
codex mcp add AdIntel --url https://adintel-delta.vercel.app/
```

<pre id="codex-cli" style="display:none">codex mcp add AdIntel --url https://adintel-delta.vercel.app/</pre>

#### Option 2 — Config file

Edit (or create) `~/.codex/config.toml`:

<button onclick="copyCommand('codex-toml')">Copy</button>

```toml
[mcp_servers.AdIntel]
url = "https://adintel-delta.vercel.app/"
```

<pre id="codex-toml" style="display:none">[mcp_servers.AdIntel]
url = "https://adintel-delta.vercel.app/"</pre>

**Restart Codex** after either method to reload MCP server definitions.

---

### Antigravity

Antigravity stores MCP server config in a JSON file. You can edit it through the UI or directly on disk.

#### Step 1 — Open the config file

**Via the UI:**

1. Launch Antigravity
2. Click the **Agent** icon in the Activity Bar (left sidebar)
3. In the Agent panel, click the **⚙️ gear icon** → **Manage MCP Servers**
4. Click **Edit configuration** — this opens `mcp_config.json` in the editor

**On disk directly:**

- **macOS / Linux:** `~/.gemini/antigravity/mcp_config.json`
- **Windows:** `%USERPROFILE%\.gemini\antigravity\mcp_config.json`

#### Step 2 — Add the server config

**Option A — Native HTTP (preferred, no extra dependencies):**

<button onclick="copyCommand('antigravity-native')">Copy</button>

```json
{
  "mcpServers": {
    "AdIntel": {
      "serverUrl": "https://adintel-delta.vercel.app/"
    }
  }
}
```

<pre id="antigravity-native" style="display:none">{
  "mcpServers": {
    "AdIntel": {
      "serverUrl": "https://adintel-delta.vercel.app/"
    }
  }
}</pre>

**Option B — Via `mcp-remote` bridge (if native HTTP is not supported in your version):**

<button onclick="copyCommand('antigravity-remote')">Copy</button>

```json
{
  "mcpServers": {
    "AdIntel": {
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

<pre id="antigravity-remote" style="display:none">{
  "mcpServers": {
    "AdIntel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://adintel-delta.vercel.app/"
      ]
    }
  }
}</pre>

#### Step 3 — Activate

1. Save `mcp_config.json`
2. In the Agent panel, click the **↺ refresh icon**, or fully restart Antigravity
3. The `AdIntel` server should appear in the MCP server list with a green indicator

#### Step 4 — Verify

Ask Antigravity: *"What tools do you have access to?"*

You should see all AdIntel tools listed — `list_advertisers`, `get_advertiser_summary`, `compare_advertisers`, and more.

---

<script>
function copyCommand(id) {
  const el = document.getElementById(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent.trim());
}
</script>

---

## Available Tools

### `list_advertisers`

Lists all advertisers currently stored in AdIntel, including catalog metadata and platform identifiers.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| *(none)* | — | — | — |

**Returns:** Array of advertiser objects with name, category, countries, and platform IDs.

---

### `get_advertiser_summary`

Returns the latest Sensor Tower snapshot for one advertiser: downloads, DAU, retention, impression share, rankings, demographics, reviews, review texts, creatives, and ASO keywords.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `advertiser_name` | string | required | Exact advertiser name from catalog |
| `country` | string | `null` | ISO country code to filter results (e.g. `"US"`) |

**Returns:** Full latest snapshot across all available Sensor Tower data domains.

---

### `compare_advertisers`

Returns the latest value for a single metric across two or more advertisers, side by side.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `advertiser_names` | string | required | Comma-separated advertiser names (e.g. `"Binance, Coinbase"`) |
| `metric` | string | `"downloads"` | One of: `downloads`, `usage`, `retention`, `impression_share`, `rankings`, `reviews` |
| `country` | string | `"US"` | ISO country code |

**Returns:** Latest metric snapshot per advertiser with date and values.

---

### `get_metric_timeseries`

Returns daily historical data for a supported metric, for one advertiser, up to 90 days.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `advertiser_name` | string | required | Exact advertiser name |
| `metric` | string | required | One of: `downloads`, `usage`, `retention`, `impression_share`, `rankings`, `reviews` |
| `country` | string | `"US"` | ISO country code |
| `start_date` | string | `null` | ISO date string `YYYY-MM-DD` |
| `end_date` | string | `null` | ISO date string `YYYY-MM-DD` |
| `limit` | integer | `90` | Maximum number of rows (max 90) |

**Returns:** Chronological array of daily data points with date, country, and metric values.

---

### `get_collection_health`

Shows data freshness, last successful run, consecutive failures, and recent error messages for one advertiser or all advertisers.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `advertiser_name` | string | `null` | Specific advertiser, or omit for all |

**Returns:** Health status per advertiser including `hours_since_success`, `consecutive_failures`, and last error.

---

### `get_collection_alerts`

Surfaces active alerts: stale data, repeated failures, and advertisers that have never successfully collected.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stale_hours` | number | `48` | Threshold in hours to flag data as stale |
| `max_consecutive_failures` | integer | `3` | Threshold to flag repeated failures |

**Returns:** List of active alerts with severity (`critical` / `warning`) and alert type.

---

### `get_recent_collection_runs`

Returns recent collection job history including run status, timestamps, and per-metric records written.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `advertiser_name` | string | `null` | Filter by advertiser, or omit for all |
| `platform` | string | `null` | Filter by platform (e.g. `"sensortower"`) |
| `limit` | integer | `20` | Number of runs to return (max 100) |

**Returns:** Array of run records with `status`, `started_at`, `finished_at`, `message`, and metadata.

---

### `request_advertiser`

Logs a missing advertiser request so the team can prioritize onboarding.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | required | Advertiser name to request |
| `requested_by` | string | `null` | Your name or identifier |
| `context` | string | `null` | Why you need this advertiser |

**Returns:** Confirmation that the request was logged.

---

### `list_requested_advertisers`

Lists advertisers that have been requested but are not yet onboarded.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| *(none)* | — | — | — |

**Returns:** Array of pending requests with name, requester, context, status, and date.

---

### `read_schema_text`

Returns the full canonical SQL schema for the AdIntel database. Useful for understanding table structures when writing custom queries.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| *(none)* | — | — | — |

**Returns:** Raw SQL schema text.

---

## Sample Questions

### Discovery

> "List all advertisers in AdIntel and group them by category."

> "Give me the full latest Sensor Tower snapshot for Chime — downloads, DAU, retention, and top ASO keywords."

### Competitive Analysis

> "Compare MonopolyGo and ScrabbleGo. Pull 30-day download trends, DAU, revenue, and ad impression share for each. Which ad networks does MonopolyGo run on that ScrabbleGo doesn't? Where is the biggest gap in coverage?"

> "For Binance, Coinbase, and Kraken in the US, compare the latest downloads and usage metrics. Tell me which looks strongest, show the 30-day trend, and flag any data health issues or stale data."

> "Which finance apps in AdIntel have the highest impression share on TikTok? Which ones have zero presence there?"

### Operations & Health

> "Which advertisers have stale data or repeated collection failures? Show severity."

> "Show me the last 5 collection runs for MonopolyGo and tell me if any failed or had partial results."

---

## Appendix A: Data Availability

AdIntel exposes the following Sensor Tower data domains:

| Domain | Key Metrics | Dimensions |
|--------|-------------|------------|
| **Downloads** | `downloads`, `revenue` | date, country, OS (iOS / Android / unified) |
| **Usage** | `avg_dau`, `time_spent_min`, `sessions_per_day` | date, country |
| **Retention** | `d1`, `d3`, `d7`, `d14`, `d30`, `d60` | cohort date, country |
| **Impression Share** | `sov_pct` | date, ad network, country |
| **Demographics** | `male_pct`, `female_pct` | age bracket, country |
| **Rankings** | `rank`, `is_featured` | date, category, chart type, country |
| **Reviews** | `avg_rating`, `rating_count`, star distribution | date, country |
| **Review Texts** | `body`, `sentiment`, `tags` | review date, country |
| **Creatives** | `creative_type`, `network`, `thumbnail_url` | first seen date |
| **ASO Keywords** | `rank`, `traffic_score`, `opportunity_score` | country, device |

**Notes:**
- `get_metric_timeseries` and `compare_advertisers` support: `downloads`, `usage`, `retention`, `impression_share`, `rankings`, `reviews`
- `get_advertiser_summary` surfaces all domains for one advertiser at once
- Most advertisers are tracked for **US** only; Binance is also tracked for TR, BR, and NG
- Data coverage varies by advertiser — check `get_collection_health` if results look sparse

---

## Appendix B: Advertiser Availability

**24 active advertisers** as of April 2026.

### Finance (14)

| Advertiser | Countries |
|------------|-----------|
| Binance | US, TR, BR, NG |
| Chime | US |
| Coinbase | US |
| Current | US |
| Dave | US |
| eToro | US |
| Koho | US |
| Kraken | US |
| MoneyLion | US |
| Possible Finance | US |
| Stash | US |
| Tilt | US |
| swagbucks | US |
| testerup | US |

### Games & Lifestyle (10)

| Advertiser | Category | Countries |
|------------|----------|-----------|
| Albert | Games | US |
| Mistplay | Games | US |
| MonopolyGo | Games | US |
| Pokemon GO | Games | US |
| Royal Match | Games | US |
| ScrabbleGo | Games | US |
| Travel Town | Games | US |
| Realtor | Lifestyle / Business | US |
| Shopback | Shopping / Lifestyle | US |
| Upside | Travel / Shopping | US |

> To request a new advertiser, use the `request_advertiser` tool or just ask your AI assistant: *"Log a request for [name] in AdIntel."*

---

## Notes for Teammates

- Use advertiser names **exactly as they appear** in the catalog (case-sensitive matching)
- If an advertiser is missing, use `request_advertiser` to keep the onboarding queue visible to the whole team
- If results look empty or sparse, check `get_collection_health` before assuming no data exists — the advertiser may have a recent collection failure
- The MCP is read-only — it reflects what is already in the shared database, nothing more
