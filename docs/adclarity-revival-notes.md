# AdClarity Revival Notes

AdClarity was intentionally removed from the active AdIntel product surface on April 15, 2026 because there was no real collection pipeline behind it yet. If we want to add it back later, this note is the shortest path to do it cleanly.

## Current Product Position

- Active data platforms are `sensortower` and `otterlyai`.
- GEO in MCP means Otterly-backed GEO data.
- AdClarity should stay out of MCP summaries and health checks until we have a real collector and normalized storage.

## What Was Removed

These areas were simplified to remove dormant AdClarity references:

- runtime platform enum in [src/adintel/core/models.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/core/models.py:1)
- collector wiring in [src/adintel/collectors/service.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/collectors/service.py:1)
- CLI advertiser upsert / platform selection in [src/adintel/cli/main.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/cli/main.py:1)
- onboarding payload shape in [src/adintel/onboarding.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/onboarding.py:1)
- advertiser ORM fields in [src/adintel/db/models.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/db/models.py:1)
- advertiser repository mapping and health-platform defaults in [src/adintel/db/repositories.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/db/repositories.py:1)
- schema columns in [sql/schema.sql](/Users/yongcheng/Desktop/projects/AdIntel/sql/schema.sql:1)
- old collector stub file `src/adintel/platforms/adclarity.py`

## Recommended Re-Introduction Plan

Bring AdClarity back only after deciding the real storage model.

1. Define the normalized tables first.
   Decide what AdClarity data we actually want to store, for example advertiser metadata, creatives, impression share, networks, or spend-like proxy metrics.

2. Re-introduce platform identity in the core model.
   Add `PlatformName.ADCLARITY` and an `adclarity` block under `AdvertiserPlatforms` in [src/adintel/core/models.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/core/models.py:1).

3. Restore advertiser catalog support.
   Add AdClarity identifiers back into:
   - [config/advertisers.yaml](/Users/yongcheng/Desktop/projects/AdIntel/config/advertisers.yaml:1)
   - [config/advertisers.example.yaml](/Users/yongcheng/Desktop/projects/AdIntel/config/advertisers.example.yaml:1)
   - [src/adintel/onboarding.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/onboarding.py:1) only if onboarding can actually source the IDs
   - [src/adintel/cli/main.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/cli/main.py:1) for manual upsert support

4. Add schema fields and migrations deliberately.
   If identifiers belong on `advertisers`, restore the columns in [sql/schema.sql](/Users/yongcheng/Desktop/projects/AdIntel/sql/schema.sql:1) and add additive / reversible migration steps instead of relying only on ORM shape.

5. Implement a real collector, not a placeholder.
   Recreate `src/adintel/platforms/adclarity.py` only once it can:
   - authenticate
   - fetch real records
   - upsert normalized rows
   - write `scrape_runs` and, if useful, `scrape_run_metrics`

6. Wire the collector into the service layer.
   Re-add the collector in [src/adintel/collectors/service.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/collectors/service.py:1) only when step 5 is done. Avoid reintroducing a stub that only creates noisy health alerts.

7. Re-enable MCP exposure last.
   Only after real data exists should we restore AdClarity into:
   - `get_collection_status` platform summaries
   - any data availability matrix
   - docs and user-facing guidance

## Important Rule

Do not re-add AdClarity to health checks just because IDs exist in the catalog. That was the source of the earlier confusion: users saw "never collected" alerts for a platform that had no meaningful pipeline yet.

The bar for bringing it back should be:

- real collector
- real data tables
- real runs in `scrape_runs`
- MCP output that users can act on

## Nice Future Improvement

If AdClarity returns very different data than SensorTower and Otterly, consider introducing an explicit `platform_availability` builder per platform in [src/adintel/mcp/server.py](/Users/yongcheng/Desktop/projects/AdIntel/src/adintel/mcp/server.py:1) instead of hardcoding assumptions into collection health. That will make future platform additions much cheaper and cleaner.
