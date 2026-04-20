#!/usr/bin/env python3
"""Retry Wayback archival for records missing WaybackURL.

Usage: python3 raw/backfill_wayback.py [digest_number]

With no argument, scans all records. With a digest number, only that digest.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from process_digest import ARCHIVE_WORKERS, archive_to_wayback

DATA_FILE = Path(__file__).parent / "data.json"


def main():
    digest_filter = int(sys.argv[1]) if len(sys.argv) > 1 else None

    with DATA_FILE.open(encoding="utf-8") as f:
        data = json.load(f)

    targets = [
        r for r in data
        if not r.get("WaybackURL")
        and (digest_filter is None or r.get("DigestNumber") == digest_filter)
    ]

    if not targets:
        print("Nothing to backfill.")
        return

    label = f"digest #{digest_filter}" if digest_filter else "all digests"
    print(f"Retrying Wayback archival for {len(targets)} records ({label})...")

    done = 0
    with ThreadPoolExecutor(max_workers=ARCHIVE_WORKERS) as executor:
        futures = {executor.submit(archive_to_wayback, r["Link"]): r for r in targets}
        for future in as_completed(futures):
            record = futures[future]
            try:
                url = future.result() or ""
            except Exception as e:
                print(f"  [WARN] unexpected error for {record['Link']}: {e}")
                url = ""
            if url:
                record["WaybackURL"] = url
            done += 1
            if done % 5 == 0:
                print(f"  Processed {done}/{len(targets)}...")

    archived = sum(1 for r in targets if r.get("WaybackURL"))
    print(f"Archived {archived}/{len(targets)} this run.")

    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved {DATA_FILE}")


if __name__ == "__main__":
    main()
