# AdIntel Architecture

AdIntel is organized around explicit boundaries:

## Layers

- `adintel.core`
  Shared settings, typed models, catalog loading, browser services
- `adintel.db`
  SQLAlchemy models, session helpers, repositories
- `adintel.collectors`
  Collection orchestration and collector registry
- `adintel.platforms`
  Platform-specific browser and extraction logic
- `adintel.cli`
  Operator-facing commands
- `adintel.mcp`
  Query-facing surface over the shared repository layer

## Design Rules

- Advertiser metadata lives in YAML and the database, never in executable source
- Browser automation does not write directly to SQL tables
- Collectors return normalized records or explicit placeholder results
- CLI commands call services; services call repositories
- Stealth is applied centrally in the browser manager

## Rewrite Scope

This initial rewrite establishes the Python foundation and operator workflows first:

- project packaging
- catalog validation
- advertiser persistence
- browser session management with stealth
- platform plugin contract

Deep platform extraction can now be ported module by module without reintroducing the previous structural coupling.
