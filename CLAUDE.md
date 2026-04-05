# AdIntel

AdIntel is now a Python-first collection and MCP project.

## Architecture

```text
SensorTower ──→ Playwright collection ──→ PostgreSQL ──→ Python MCP server
```

## Primary Paths

- `src/adintel/` — application package
- `config/` — advertiser catalog
- `sql/schema.sql` — canonical schema
- `tests/` — parser and MCP tests

## Working Rules

- Browser automation uses stealth by default
- Advertiser metadata lives in YAML and the database, not executable source
- Platform parsing should be validated against captured API responses before expanding coverage
- SensorTower is the active platform implementation
- AdClarity is deferred until account access exists
