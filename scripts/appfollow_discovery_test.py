#!/usr/bin/env python3
"""
AppFollow discovery test.

Opens the browser, navigates to AppFollow, waits for you to log in if needed,
then navigates to the reviews section and captures all API responses.
"""
import asyncio, json, sys
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

WORKSPACE  = "my-first-workspace"
ITEM_ID    = "634040"
FROM_DATE  = "2026-03-19"
TO_DATE    = "2026-04-16"
STATE_DIR  = Path(__file__).resolve().parents[1] / "state" / "browser" / "appfollow"
DEBUG_DIR  = Path(__file__).resolve().parents[1] / "state" / "debug" / "appfollow"

HOME_URL = (
    f"https://watch.appfollow.io/apps/{WORKSPACE}/home"
    f"?from={FROM_DATE}&to={TO_DATE}&country=us&itemId={ITEM_ID}"
)

# Try these URLs in sequence — AppFollow may load reviews on a dedicated sub-path
REVIEW_URLS = [
    f"https://watch.appfollow.io/apps/{WORKSPACE}/reviews"
    f"?from={FROM_DATE}&to={TO_DATE}&country=us&itemId={ITEM_ID}",
    f"https://watch.appfollow.io/apps/{WORKSPACE}/reviews"
    f"?from={FROM_DATE}&to={TO_DATE}&itemId={ITEM_ID}",
    HOME_URL,
]


async def capture_responses(page, url: str, label: str, wait_ms: int = 12_000) -> list[dict]:
    captured = []

    async def on_response(response):
        ct = response.headers.get("content-type", "")
        u = response.url
        if "json" not in ct and "/api/" not in u and "client-api" not in u:
            return
        try:
            data = await response.json()
            captured.append({"url": u, "data": data})
        except Exception:
            pass

    page.on("response", on_response)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        print(f"  note: {e}")
    print(f"  [{label}] landed: {page.url[:80]}")
    await page.wait_for_timeout(wait_ms)
    page.remove_listener("response", on_response)
    return captured


async def run():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(STATE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Step 1: load home page and check auth
        print(f"\nStep 1: Navigating to AppFollow home...")
        init_captured = await capture_responses(page, HOME_URL, "home", wait_ms=5_000)

        # Check auth from API responses
        auth_ok = False
        for item in init_captured:
            if "client-api/v1/client" in item["url"]:
                if item["data"].get("success") is True:
                    auth_ok = True
                    break
            if "client-api/v1/auth" in item["url"]:
                if item["data"].get("success") is True:
                    auth_ok = True

        if not auth_ok:
            print("\n⚠  Session is not logged in. A browser window should be open.")
            print("   Please log in to AppFollow in that browser window.")
            print("   Waiting 60 seconds for you to complete login...")
            for i in range(60, 0, -10):
                print(f"   {i}s remaining...", flush=True)
                await page.wait_for_timeout(10_000)
            print("   Continuing after wait...")
            # Re-navigate to home to refresh auth state
            init_captured = await capture_responses(page, HOME_URL, "home-after-login", wait_ms=5_000)

        # Step 2: try review-specific URLs
        print(f"\nStep 2: Navigating to reviews section...")
        all_captured: list[dict] = list(init_captured)

        for url in REVIEW_URLS:
            print(f"\n  Trying: {url[:90]}")
            captured = await capture_responses(page, url, "reviews", wait_ms=10_000)
            all_captured.extend(captured)
            new_api = [c for c in captured if "client-api" in c["url"] or "/api/" in c["url"]]
            if new_api:
                print(f"  Captured {len(new_api)} client-api calls:")
                for c in new_api:
                    print(f"    {c['url']}")
                break  # Found API calls, stop trying other URLs

        await context.close()

    # Analysis
    print(f"\n{'='*60}")
    print(f"Total responses captured: {len(all_captured)}")
    client_api_calls = [c for c in all_captured if "client-api" in c["url"]]
    print(f"AppFollow client-api calls: {len(client_api_calls)}")

    review_candidates = []
    for item in all_captured:
        url = item["url"]
        data = item["data"]
        if not isinstance(data, dict):
            continue
        for k in ("reviews", "data", "items", "results", "feedback", "list"):
            candidate = data.get(k)
            if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
                first = candidate[0]
                # Score: does it look like a review?
                review_signals = sum([
                    "rating" in first or "stars" in first or "score" in first,
                    "body" in first or "text" in first or "content" in first or "review" in first,
                    "date" in first or "created_at" in first or "published_at" in first,
                    "sentiment" in first or "tone" in first,
                    "id" in first,
                ])
                if review_signals >= 2:
                    review_candidates.append({
                        "url": url, "key": k,
                        "count": len(candidate),
                        "sample_keys": list(first.keys()),
                        "score": review_signals,
                    })

    # Save dump
    dump: list[dict] = []
    for c in all_captured:
        data = c["data"]
        if isinstance(data, dict):
            keys = list(data.keys())
            sample = json.dumps(data)[:800]
        else:
            keys = [f"list[{len(data)}]"]
            sample = json.dumps(data[:1] if data else [])[:800]
        dump.append({"url": c["url"], "keys": keys, "sample": sample})

    ts = datetime.now().strftime("%H%M%S")
    dump_path = DEBUG_DIR / f"discovery-{ts}.json"
    dump_path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    print(f"\nFull dump: {dump_path}")

    print(f"\n{'='*60}")
    if review_candidates:
        print(f"✅ Found {len(review_candidates)} review endpoint(s):")
        for rc in sorted(review_candidates, key=lambda x: -x["score"]):
            print(f"\n  URL: {rc['url']}")
            print(f"  key: '{rc['key']}' | items: {rc['count']} | sample fields: {rc['sample_keys']}")
        print("\n→ Update appfollow_parsers.py if field names differ from: id, date, body, rating, sentiment, tags")
    else:
        print("⚠ No review data found in API responses.")
        print("\nAll client-api calls captured:")
        for c in client_api_calls:
            print(f"  {c['url']}")
            if isinstance(c["data"], dict):
                print(f"    keys: {list(c['data'].keys())}")
        print(f"\nInspect {dump_path} for the full raw data.")


asyncio.run(run())
