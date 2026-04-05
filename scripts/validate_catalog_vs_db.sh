#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}"

# Resolve DB URL from settings (.env-aware) unless explicitly overridden.
DEFAULT_ADINTEL_DATABASE_URL="$(./.venv/bin/python - <<'PY'
from adintel.core.settings import get_settings
print(get_settings().database_url)
PY
 2>/dev/null || true)"

if [[ -n "${DEFAULT_ADINTEL_DATABASE_URL}" ]]; then
  export ADINTEL_DATABASE_URL="${ADINTEL_DATABASE_URL:-${DEFAULT_ADINTEL_DATABASE_URL}}"
fi

./.venv/bin/python - <<'PY'
from __future__ import annotations

import unicodedata
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

from adintel.core.settings import get_settings


def normalize_name(value: str) -> str:
    text_value = unicodedata.normalize("NFKD", value or "")
    text_value = "".join(ch for ch in text_value if not unicodedata.combining(ch))
    text_value = " ".join(text_value.strip().split())
    return text_value.casefold()


def csv_countries(value: list[str] | None) -> str:
    countries = [c.strip().upper() for c in (value or ["US"]) if c and c.strip()]
    return ",".join(countries or ["US"])


settings = get_settings()
config_path = Path(settings.config_file)
config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
config_advertisers = config_data.get("advertisers", [])

catalog_by_key: dict[str, dict] = {}
catalog_duplicates: list[str] = []
for entry in config_advertisers:
    name = str(entry.get("name") or "").strip()
    if not name:
        continue
    key = normalize_name(name)
    if key in catalog_by_key:
        catalog_duplicates.append(name)
        continue

    platforms = entry.get("platforms") or {}
    st = platforms.get("sensortower") or {}
    catalog_by_key[key] = {
        "name": name,
        "category": entry.get("category"),
        "countries_csv": csv_countries(entry.get("countries")),
        "uai": st.get("unified_app_id"),
        "ios": st.get("ios_app_id"),
        "android": st.get("android_package"),
    }

engine = create_engine(settings.database_url)
with engine.connect() as conn:
    db_rows = conn.execute(text("""
        SELECT name, category, countries_csv,
               sensortower_unified_app_id, sensortower_ios_app_id, sensortower_android_package
        FROM advertisers
        ORDER BY name
    """)).all()

db_by_key: dict[str, dict] = {}
db_duplicates: list[str] = []
for row in db_rows:
    key = normalize_name(row[0])
    if key in db_by_key:
        db_duplicates.append(row[0])
        continue
    db_by_key[key] = {
        "name": row[0],
        "category": row[1],
        "countries_csv": row[2] or "US",
        "uai": row[3],
        "ios": row[4],
        "android": row[5],
    }

missing_in_db = [v["name"] for k, v in catalog_by_key.items() if k not in db_by_key]
extra_in_db = [v["name"] for k, v in db_by_key.items() if k not in catalog_by_key]

mismatches: list[tuple[str, list[str]]] = []
for key, cfg in catalog_by_key.items():
    db = db_by_key.get(key)
    if db is None:
        continue

    diffs: list[str] = []
    if (cfg["category"] or None) != (db["category"] or None):
        diffs.append(f"category: cfg={cfg['category']} db={db['category']}")
    if (cfg["countries_csv"] or "US") != (db["countries_csv"] or "US"):
        diffs.append(f"countries_csv: cfg={cfg['countries_csv']} db={db['countries_csv']}")
    if (cfg["uai"] or None) != (db["uai"] or None):
        diffs.append(f"uai: cfg={cfg['uai']} db={db['uai']}")
    if (cfg["ios"] or None) != (db["ios"] or None):
        diffs.append(f"ios_app_id: cfg={cfg['ios']} db={db['ios']}")
    if (cfg["android"] or None) != (db["android"] or None):
        diffs.append(f"android_package: cfg={cfg['android']} db={db['android']}")

    if diffs:
        mismatches.append((cfg["name"], diffs))

print("Catalog/DB validation summary")
print(f"- catalog advertisers: {len(catalog_by_key)}")
print(f"- db advertisers: {len(db_by_key)}")

has_error = False
if catalog_duplicates:
    has_error = True
    print("- ERROR: duplicate normalized advertiser names in catalog:")
    for name in catalog_duplicates:
        print(f"  - {name}")

if db_duplicates:
    has_error = True
    print("- ERROR: duplicate normalized advertiser names in db:")
    for name in db_duplicates:
        print(f"  - {name}")

if missing_in_db:
    has_error = True
    print("- ERROR: missing in db:")
    for name in missing_in_db:
        print(f"  - {name}")

if extra_in_db:
    has_error = True
    print("- ERROR: extra in db (not in catalog):")
    for name in extra_in_db:
        print(f"  - {name}")

if mismatches:
    has_error = True
    print("- ERROR: field mismatches:")
    for name, diffs in mismatches:
        print(f"  - {name}")
        for diff in diffs:
            print(f"    - {diff}")

if has_error:
    raise SystemExit(1)

print("- OK: catalog and db are aligned")
PY
