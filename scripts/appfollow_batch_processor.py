#!/usr/bin/env python3
"""
Process remaining AppFollow advertisers in batches of 5.

Automatically cycles through batches:
1. Shows which 5 advertisers to add to workspace
2. Waits for user confirmation
3. Runs discovery + collection
4. Repeats until all advertisers are collected
"""
import asyncio
import sys
import subprocess
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

from adintel.core.settings import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "appfollow_groups.yaml"
BATCH_SIZE = 5


def get_collected_advertisers() -> set[str]:
    """Get advertisers already collected from database."""
    db_url = get_settings().database_url
    engine = create_engine(db_url)

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT DISTINCT advertiser_name
            FROM appfollow_reviews
        """)).fetchall()
        return {row[0] for row in result}


def get_all_advertisers() -> list[dict]:
    """Get all advertisers from config."""
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    advertisers = []
    for group in config.get("groups", []):
        advertiser = group.get("advertiser")
        if advertiser:
            advertisers.append({
                "name": advertiser,
                "role": "advertiser",
                "competitors": [c.get("name") for c in group.get("competitors", [])]
            })

    return advertisers


def main():
    collected = get_collected_advertisers()
    all_advertisers = get_all_advertisers()

    # Find missing advertisers
    missing_roots = [a for a in all_advertisers if a["name"] not in collected]

    if not missing_roots:
        print("✓ All advertisers already collected!")
        return

    print(f"\n{len(missing_roots)} advertiser groups remaining ({len(missing_roots) * 4} apps total)")
    print("=" * 70)

    for batch_num, i in enumerate(range(0, len(missing_roots), BATCH_SIZE), start=2):
        batch = missing_roots[i:i+BATCH_SIZE]
        batch_apps = []

        for advertiser in batch:
            batch_apps.append(advertiser["name"])
            batch_apps.extend(advertiser["competitors"])

        print(f"\n🔹 BATCH {batch_num}: Add these {len(batch_apps)} apps to AppFollow workspace:\n")

        for advertiser in batch:
            print(f"  📱 {advertiser['name']} (primary)")
            for comp in advertiser["competitors"]:
                print(f"     └─ {comp} (competitor)")

        print(f"\n📍 Workspace URL: https://watch.appfollow.io")
        print("\n⚠️  Please add all these apps to your workspace, then run:")
        print(f"    python scripts/appfollow_batch_processor_continue.py {batch_num}")
        print("\nWaiting for batch to be added...")

        # For automated runs, skip the interactive prompt
        # Uncomment below to run automatically (only if apps are already in workspace)
        # input("\nPress Enter once you've added all these apps...")
        return

    print("\n" + "=" * 70)
    print("✅ All advertisers collected!")
    print("=" * 70)


if __name__ == "__main__":
    main()
