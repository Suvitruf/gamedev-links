#!/usr/bin/env python3
"""
Backfill publication dates for records in data.json that still have the
1970 placeholder, using the date-extraction pipeline from process_digest.py:

1. Tweet snowflake IDs (offline, exact)
2. Re-fetch the page and run generic HTML extraction (meta/JSON-LD/datetime/URL)
3. Domain fallbacks: steam release date, t.me embed, github API
4. Wayback CDX earliest snapshot (approximate; clamped to the digest date)

Only the "Date" field is touched. Re-running skips already-resolved records,
and progress is saved incrementally, so the script is safe to interrupt.

Usage: python backfill_dates.py [--limit N] [--workers N] [--dry-run]
"""

import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import process_digest as pd


def date_key(d):
    """dd.mm.YYYY -> sortable YYYYMMDD string."""
    day, month, year = d.split(".")
    return f"{year}{month}{day}"


def digest_date_as_record_date(digest_date):
    """YYYY-MM-DD -> dd.mm.YYYY."""
    year, month, day = digest_date.split("-")
    return f"{day}.{month}.{year}"


def resolve_date(link):
    """Return (date, method) or (None, None)."""
    # 1. Offline: tweet snowflake (skip fetching twitter/x entirely)
    date = pd.date_from_tweet_url(link)
    if date:
        return date, "tweet-id"

    # 2. Page fetch + generic extraction
    html = pd.fetch_html(link, timeout=pd.RESOURCE_TIMEOUT)
    if html:
        date = pd.extract_date_from_html(html, link)
        if date:
            return date, "html"
    else:
        date = pd.extract_date_from_url(link)
        if date:
            return date, "url"

    # 3. Domain-specific fallbacks
    if html and "store.steampowered.com" in link:
        date = pd.date_from_steam_page(html)
        if date:
            return date, "steam"

    date = pd.date_from_telegram_embed(link)
    if date:
        return date, "telegram"

    date = pd.date_from_github_api(link)
    if date:
        return date, "github-api"

    # 4. Wayback CDX earliest snapshot
    date = pd.date_from_wayback_cdx(link)
    if date:
        return date, "cdx"

    return None, None


def main():
    parser = argparse.ArgumentParser(description="Backfill dates for 1970 records.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N distinct links")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true", help="Resolve but do not save")
    args = parser.parse_args()

    data = pd.load_json(pd.DATA_FILE, [])
    targets = [r for r in data if r.get("Date") == pd.DEFAULT_DATE and r.get("Link")]
    print(f"{len(targets)} records with placeholder date")

    # Resolve each distinct link once; records sharing a link share the result
    by_link = {}
    for r in targets:
        by_link.setdefault(r["Link"], []).append(r)
    links = list(by_link)
    if args.limit:
        links = links[: args.limit]
    print(f"{len(links)} distinct links to resolve")

    stats = {}
    lock = threading.Lock()
    done = [0]
    resolved = [0]

    def work(link):
        date, method = resolve_date(link)
        # CDX gives first-archived date; the digest date is a tighter upper
        # bound when the page was only archived after being shared
        if date and method == "cdx":
            digest_dates = [r.get("DigestDate") for r in by_link[link] if r.get("DigestDate")]
            if digest_dates:
                digest_date = digest_date_as_record_date(min(digest_dates))
                if date_key(date) > date_key(digest_date):
                    date, method = digest_date, "cdx-clamped"
        return link, date, method

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(work, link) for link in links]
        for future in as_completed(futures):
            try:
                link, date, method = future.result()
            except Exception as e:
                print(f"  [WARN] worker error: {e}")
                continue
            with lock:
                done[0] += 1
                if date:
                    resolved[0] += 1
                    stats[method] = stats.get(method, 0) + 1
                    for r in by_link[link]:
                        r["Date"] = date
                if done[0] % 25 == 0:
                    print(f"  {done[0]}/{len(links)} links, {resolved[0]} resolved...")
                if not args.dry_run and done[0] % 200 == 0:
                    pd.save_json(pd.DATA_FILE, data)
                    print(f"  [saved after {done[0]}]")

    if not args.dry_run:
        pd.save_json(pd.DATA_FILE, data)

    print(f"\nResolved {resolved[0]}/{len(links)} links. By method:")
    for method, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {method:12} {count}")
    remaining = sum(1 for r in data if r.get("Date") == pd.DEFAULT_DATE)
    print(f"Records still at {pd.DEFAULT_DATE}: {remaining}")


if __name__ == "__main__":
    main()
