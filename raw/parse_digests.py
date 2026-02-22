#!/usr/bin/env python3
"""
Parse weekly gamedev digest pages from suvitruf.ru and populate data.json.
Resumable: tracks processed digests in processed_digests.json.
"""

import json
import re
import time
import os
import sys
import urllib.request
import urllib.error
import ssl
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "processed_digests.json")
BASE_URL = "https://suvitruf.ru"
MAX_LISTING_PAGES = 28
RESOURCE_TIMEOUT = 8
LISTING_TIMEOUT = 15

# Month name mapping (Russian to number)
MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# SSL context that doesn't verify (some old resource links have expired certs)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def fetch_url(url, timeout=LISTING_TIMEOUT):
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
        print(f"  [WARN] Failed to fetch {url}: {e}")
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


def get_digest_urls_from_listing(html):
    """Extract digest URLs from a listing page."""
    digests = []
    # Find article blocks with h1 headers containing "Недельный геймдев"
    # Pattern: <article ...> ... <h1 ...><a href="URL">Недельный геймдев...</a></h1>
    articles = re.findall(r'<article[^>]*>.*?</article>', html, re.DOTALL)
    for article in articles:
        h1_match = re.search(r'<h1[^>]*>\s*<a\s+href="([^"]+)"[^>]*>([^<]*Недельный геймдев[^<]*)</a>', article, re.DOTALL)
        if h1_match:
            url = h1_match.group(1)
            title = unescape(h1_match.group(2).strip())
            digests.append((url, title))
    return digests


def parse_digest_header(html):
    """Extract digest number and date from the h1 header."""
    # Pattern: Недельный геймдев: #<number> — <day> <month>, <year>
    h1_match = re.search(
        r'<h1[^>]*>[^<]*Недельный геймдев[^<]*?#(\d+)\s*[—–-]\s*(\d+)\s+(\w+),?\s*(\d{4})',
        html, re.DOTALL
    )
    if not h1_match:
        # Try alternative format without comma
        h1_match = re.search(
            r'Недельный геймдев[^<]*?#(\d+)\s*[—–-]\s*(\d+)\s+(\w+)\s*,?\s*(\d{4})',
            html, re.DOTALL
        )
    if h1_match:
        number = int(h1_match.group(1))
        day = int(h1_match.group(2))
        month_name = h1_match.group(3).lower()
        year = int(h1_match.group(4))
        month = MONTHS_RU.get(month_name, 1)
        date = f"{year}-{month:02d}-{day:02d}"
        return number, date
    return None, None


def extract_resources_from_digest(html):
    """Extract resource entries (h3 blocks) from a digest page."""
    resources = []

    # Get only the article content
    article_match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if not article_match:
        return resources
    article_html = article_match.group(1)

    # Find ALL h3 tags (both formats)
    h3_pattern = r'<h3[^>]*>(.*?)</h3>'
    h3_matches = list(re.finditer(h3_pattern, article_html, re.DOTALL))

    for i, match in enumerate(h3_matches):
        h3_inner = match.group(1).strip()

        # Format 1: <h3><a href="URL">Title</a></h3>
        link_in_h3 = re.search(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', h3_inner, re.DOTALL)

        if link_in_h3:
            link = link_in_h3.group(1).strip()
            title = re.sub(r'<[^>]+>', '', link_in_h3.group(2)).strip()
        else:
            # Format 2: <h3>Title</h3> — link is in the content block after
            title = re.sub(r'<[^>]+>', '', h3_inner).strip()
            link = None

        title = unescape(title)

        # Skip empty titles, section headers (e.g. h3 used for categories)
        if not title:
            continue

        # Get content block: text between this h3 and the next h3 (or end)
        start = match.end()
        end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(article_html)
        block_html = article_html[start:end]

        # If no link in h3, find the first non-image link in the block
        if not link:
            # Find all links in the block
            block_links = re.findall(r'<a\s+href="([^"]+)"', block_html)
            for candidate in block_links:
                # Skip image links (suvitruf uploads), anchor links, empty
                if 'wp-content/uploads' in candidate:
                    continue
                if candidate.startswith('#'):
                    continue
                if not candidate.startswith('http'):
                    continue
                link = candidate
                break

        # Skip if we still have no link
        if not link:
            continue

        # Skip suvitruf self-links (not resource links)
        if 'suvitruf.ru' in link and 'wp-content' not in link:
            continue

        # Get description from block text
        desc_text = re.sub(r'<[^>]+>', ' ', block_html)
        desc_text = re.sub(r'\s+', ' ', desc_text).strip()
        if len(desc_text) > 200:
            desc_text = desc_text[:197] + "..."

        resources.append({
            "Link": link,
            "Title": title,
            "Description": desc_text,
        })

    return resources


def detect_language_and_author(url):
    """Visit a resource URL to detect language and extract author."""
    language = "en"  # default
    author = ""

    html = fetch_url(url, timeout=RESOURCE_TIMEOUT)
    if not html:
        # Try to guess from URL
        if re.search(r'\.(ru|by|ua|kz)(/|$)', url):
            language = "ru"
        return language, author

    # Detect language from <html lang="...">
    lang_match = re.search(r'<html[^>]+lang=["\']([a-zA-Z]{2})', html, re.IGNORECASE)
    if lang_match:
        language = lang_match.group(1).lower()
    elif re.search(r'\.(ru|by|ua|kz)(/|$)', url):
        language = "ru"

    # Try to extract author from meta tags
    author_patterns = [
        r'<meta\s+name=["\']author["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']author["\']',
        r'<meta\s+property=["\']article:author["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']article:author["\']',
    ]
    for pattern in author_patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            author = unescape(m.group(1).strip())
            # Skip if it looks like a URL
            if author.startswith("http"):
                author = ""
            break

    # If no meta author, try JSON-LD
    if not author:
        ld_match = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
        if ld_match:
            author = unescape(ld_match.group(1).strip())

    # Try <a rel="author">
    if not author:
        rel_author = re.search(r'<a[^>]+rel=["\']author["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
        if rel_author:
            author = unescape(rel_author.group(1).strip())

    return language, author


def process_single_resource(res):
    """Process a single resource: detect language and author."""
    link = res["Link"]
    language, author = detect_language_and_author(link)
    res["Language"] = language
    res["Author"] = author
    return res


def process_digest(digest_url, data, progress):
    """Process a single digest page."""
    print(f"\n  Fetching digest: {digest_url}")
    html = fetch_url(digest_url)
    if not html:
        print(f"  [ERROR] Could not fetch digest page")
        return False

    number, date = parse_digest_header(html)
    if number is None:
        print(f"  [ERROR] Could not parse digest header")
        return False

    print(f"  Digest #{number}, date: {date}")

    # Check if already processed
    if str(number) in progress:
        print(f"  Already processed, skipping")
        return True

    resources = extract_resources_from_digest(html)
    print(f"  Found {len(resources)} resources")

    if not resources:
        # Still mark as processed even if empty
        progress[str(number)] = digest_url
        save_json(PROGRESS_FILE, progress)
        return True

    # Visit resource links in parallel (up to 5 concurrent)
    print(f"  Visiting resource links for language/author detection...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_single_resource, res): res for res in resources}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 10 == 0:
                print(f"    Processed {done_count}/{len(resources)} links...")
            try:
                future.result()
            except Exception as e:
                res = futures[future]
                res["Language"] = "en"
                res["Author"] = ""
                print(f"    [WARN] Error processing {res['Link']}: {e}")

    # Add digest info to each resource
    new_records = []
    for res in resources:
        record = {
            "Link": res["Link"],
            "Title": res["Title"],
            "Author": res.get("Author", ""),
            "Language": res.get("Language", "en"),
            "Description": res.get("Description", ""),
            "DigestNumber": number,
            "DigestDate": date,
        }
        new_records.append(record)

    # Append to data
    data.extend(new_records)
    save_json(DATA_FILE, data)

    # Mark as processed
    progress[str(number)] = digest_url
    save_json(PROGRESS_FILE, progress)

    print(f"  Saved {len(new_records)} records for digest #{number}")
    return True


def main():
    # Load existing data
    data = load_json(DATA_FILE, [])
    progress = load_json(PROGRESS_FILE, {})

    print(f"Starting. Already processed: {len(progress)} digests, {len(data)} records.")

    total_new = 0

    # Process listing pages from 28 down to 1
    for page_num in range(MAX_LISTING_PAGES, 0, -1):
        if page_num == 1:
            listing_url = f"{BASE_URL}/"
        else:
            listing_url = f"{BASE_URL}/page/{page_num}/"

        print(f"\n{'='*60}")
        print(f"Listing page {page_num}: {listing_url}")
        print(f"{'='*60}")

        html = fetch_url(listing_url)
        if not html:
            print(f"[ERROR] Could not fetch listing page {page_num}")
            continue

        digests = get_digest_urls_from_listing(html)
        print(f"Found {len(digests)} digest(s) on this page")

        for digest_url, digest_title in digests:
            before = len(data)
            success = process_digest(digest_url, data, progress)
            after = len(data)
            new = after - before
            total_new += new
            if success and new > 0:
                print(f"  +{new} records (total: {len(data)})")

            # Small delay between digests
            time.sleep(0.5)

        # Small delay between listing pages
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"DONE! Total records: {len(data)}, New records: {total_new}")
    print(f"Processed digests: {len(progress)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
