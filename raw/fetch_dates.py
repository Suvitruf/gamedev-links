#!/usr/bin/env python3
"""
Fetch publication dates for all resources in data.json.
Resumable: tracks progress in date_progress.json.
"""

import json
import re
import os
import sys
import urllib.request
import urllib.error
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "date_progress.json")
RESOURCE_TIMEOUT = 10
BATCH_SIZE = 50
MAX_WORKERS = 5
DEFAULT_DATE = "01.01.1970"

# SSL context that doesn't verify (some old resource links have expired certs)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def fetch_url(url, timeout=RESOURCE_TIMEOUT):
    """Fetch a URL and return its HTML content."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as e:
        return None


def load_json(path, default):
    """Load JSON file or return default."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    """Save data to JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_date_string(raw):
    """Parse a date string into dd.mm.YYYY format. Returns None on failure."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()

    # ISO 8601: 2023-03-15T10:00:00+00:00 or 2023-03-15
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    # US format: 03/15/2023
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', raw)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    # European format: 15.03.2023
    m = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', raw)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    return None


def extract_date_from_url(url):
    """Extract date from URL path patterns. Returns dd.mm.YYYY or None."""
    # /2023/03/15/
    m = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', url)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    # /2023-03-15/
    m = re.search(r'/(\d{4})-(\d{2})-(\d{2})/', url)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    return None


def extract_date_from_html(html, url):
    """Extract publication date from HTML content. Returns dd.mm.YYYY or None."""

    # Strategy 1: Open Graph article:published_time
    m = re.search(
        r'<meta\s+(?:property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']'
        r'|content=["\']([^"\']+)["\']\s+property=["\']article:published_time["\'])',
        html, re.IGNORECASE
    )
    if m:
        raw = m.group(1) or m.group(2)
        parsed = parse_date_string(raw)
        if parsed:
            return parsed

    # Strategy 2: Various meta name date tags
    for name in ["date", "pubdate", "DC.date.issued", "publish_date", "article:published"]:
        m = re.search(
            r'<meta\s+(?:name=["\']' + re.escape(name) + r'["\']\s+content=["\']([^"\']+)["\']'
            r'|content=["\']([^"\']+)["\']\s+name=["\']' + re.escape(name) + r'["\'])',
            html, re.IGNORECASE
        )
        if m:
            raw = m.group(1) or m.group(2)
            parsed = parse_date_string(raw)
            if parsed:
                return parsed

    # Strategy 3: JSON-LD datePublished
    ld_blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    for block in ld_blocks:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', block)
        if m:
            parsed = parse_date_string(m.group(1))
            if parsed:
                return parsed

    # Strategy 4: <time datetime="..."> elements
    time_matches = re.findall(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for raw in time_matches:
        parsed = parse_date_string(raw)
        if parsed:
            return parsed

    # Strategy 5: URL path patterns
    url_date = extract_date_from_url(url)
    if url_date:
        return url_date

    return None


def process_single_url(url):
    """Fetch URL and extract date. Returns (url, date_string)."""
    html = fetch_url(url)

    if html:
        date = extract_date_from_html(html, url)
        if date:
            return (url, date)

    # Fallback: try URL pattern even without HTML
    date = extract_date_from_url(url)
    if date:
        return (url, date)

    return (url, DEFAULT_DATE)


def apply_dates(data, progress):
    """Apply dates from progress to all records in data.json."""
    found = 0
    not_found = 0
    for record in data:
        url = record["Link"]
        date = progress.get(url, DEFAULT_DATE)
        record["Date"] = date
        if date != DEFAULT_DATE:
            found += 1
        else:
            not_found += 1

    save_json(DATA_FILE, data)
    print(f"Updated {len(data)} records: {found} with dates, {not_found} defaulted.")


def main():
    data = load_json(DATA_FILE, [])
    progress = load_json(PROGRESS_FILE, {})

    # Collect unique URLs that need processing
    urls_to_process = []
    seen = set()
    for record in data:
        url = record["Link"]
        if url not in progress and url not in seen:
            urls_to_process.append(url)
            seen.add(url)

    print(f"Total records: {len(data)}")
    print(f"Unique URLs already processed: {len(progress)}")
    print(f"URLs to process: {len(urls_to_process)}")

    if not urls_to_process:
        print("All URLs already processed. Applying dates to data.json...")
        apply_dates(data, progress)
        return

    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_single_url, url): url
                for url in urls_to_process
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    _, date = future.result()
                    progress[url] = date
                except Exception as e:
                    print(f"  [ERROR] {url}: {e}")
                    progress[url] = DEFAULT_DATE

                completed += 1
                if completed % BATCH_SIZE == 0:
                    save_json(PROGRESS_FILE, progress)
                    print(f"  Progress: {completed}/{len(urls_to_process)} "
                          f"({completed * 100 // len(urls_to_process)}%)")

    except KeyboardInterrupt:
        print("\nInterrupted! Saving progress...")
        save_json(PROGRESS_FILE, progress)
        print(f"Saved progress for {len(progress)} URLs.")
        sys.exit(1)

    # Final save of progress
    save_json(PROGRESS_FILE, progress)
    print(f"\nAll URLs processed. Applying dates to data.json...")

    apply_dates(data, progress)


if __name__ == "__main__":
    main()
