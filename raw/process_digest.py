#!/usr/bin/env python3
"""
Process a single weekly gamedev digest from suvitruf.ru.
Extracts resources, detects language/author/date, classifies type/tags,
downloads images, and appends results to data.json.

Usage: python process_digest.py <digest_url>
Requires: Pillow (pip install Pillow)
"""

import argparse
import json
import re
import os
import sys
import io
import time
import urllib.request
import urllib.error
import ssl
from html import unescape
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from PIL import Image
except ImportError:
    print("Pillow is required. Install it with: pip install Pillow")
    sys.exit(1)

# --- Paths ---
RAW_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(RAW_DIR)
DATA_FILE = os.path.join(RAW_DIR, "data.json")
PROGRESS_FILE = os.path.join(RAW_DIR, "processed_digests.json")
IMAGES_DIR = os.path.join(PROJECT_DIR, "assets", "images")

# --- Network ---
RESOURCE_TIMEOUT = 10
DIGEST_TIMEOUT = 15
MAX_WORKERS = 5
IMAGE_WORKERS = 3
DEFAULT_DATE = "01.01.1970"
CDX_TIMEOUT = 20

# --- Wayback Machine archival ---
ARCHIVE_WORKERS = 2
ARCHIVE_TIMEOUT = 180
ARCHIVE_RETRY_BACKOFF = 30
ARCHIVE_RETRIES = 2
ARCHIVE_RETRY_SLEEP = 10
SPN_ENDPOINT = "https://web.archive.org/save/"

# --- Image ---
TARGET_W = 300
TARGET_H = 120
JPEG_QUALITY = 85
WP_SIZE_RE = re.compile(r'-\d+x\d+(?=\.\w+$)')

# --- Russian months ---
MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# --- English months (3-letter prefixes) ---
MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# --- SSL (some old resource links have expired certs) ---
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# --- Type classification domains ---
VIDEO_DOMAINS = {"youtube.com", "youtu.be", "vimeo.com", "twitch.tv"}
SOCIAL_DOMAINS = {"twitter.com", "x.com", "reddit.com"}
ARTICLE_DOMAINS = {
    "80.lv", "habr.com", "dtf.ru", "gamedeveloper.com",
    "newsletter.gamediscover.co", "kotaku.com", "venturebeat.com",
    "wccftech.com", "nplus1.ru", "3dnews.ru", "vc.ru",
}
REPO_DOMAINS = {"github.com", "gitlab.com"}
STORE_DOMAINS = {
    "store.steampowered.com", "store.epicgames.com",
    "assetstore.unity.com", "fab.com", "itch.io", "gumroad.com",
}

# --- Tag classification regexes ---
RE_AI = re.compile(r"\bAI\b")
RE_VR = re.compile(r"\bVR\b")
RE_AR = re.compile(r"\bAR\b")
RE_XR = re.compile(r"\bXR\b")
RE_FREE = re.compile(r"\bfree\b", re.IGNORECASE)
RE_MAYA = re.compile(r"\bMaya\b")
RE_SWITCH = re.compile(r"\bSwitch\b")
RE_UE4 = re.compile(r"\bUE4\b")
RE_UE5 = re.compile(r"\bUE5\b")


# ============================================================
# Shared utilities
# ============================================================

def fetch_url(url, timeout=DIGEST_TIMEOUT):
    """Fetch a URL and return raw bytes."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            return resp.read()
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None


def fetch_html(url, timeout=DIGEST_TIMEOUT):
    """Fetch a URL and return decoded HTML string."""
    raw = fetch_url(url, timeout)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


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


# ============================================================
# Digest parsing
# ============================================================

def parse_digest_header(html):
    """Extract digest number and date from the h1 header."""
    h1_match = re.search(
        r'<h1[^>]*>[^<]*Недельный геймдев[^<]*?#(\d+)\s*[—–-]\s*(\d+)\s+(\w+),?\s*(\d{4})',
        html, re.DOTALL
    )
    if not h1_match:
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

    article_match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if not article_match:
        return resources
    article_html = article_match.group(1)

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
            # Format 2: <h3>Title</h3> — link in content block after
            title = re.sub(r'<[^>]+>', '', h3_inner).strip()
            link = None

        title = unescape(title)
        if not title:
            continue

        # Content block between this h3 and the next
        start = match.end()
        end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(article_html)
        block_html = article_html[start:end]

        # Find link in block if not in h3
        if not link:
            block_links = re.findall(r'<a\s+href="([^"]+)"', block_html)
            for candidate in block_links:
                if 'wp-content/uploads' in candidate:
                    continue
                if candidate.startswith('#'):
                    continue
                if not candidate.startswith('http'):
                    continue
                link = candidate
                break

        # Check for YouTube iframe embeds
        if not link:
            iframe_match = re.search(
                r'<iframe[^>]+src=["\'](?:https?:)?//(?:www\.)?youtube\.com/embed/([^"\'?]+)',
                block_html, re.IGNORECASE
            )
            if iframe_match:
                link = f"https://www.youtube.com/watch?v={iframe_match.group(1)}"

        if not link:
            continue

        # Skip suvitruf self-links
        if 'suvitruf.ru' in link and 'wp-content' not in link:
            continue

        # Description from block text
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


# ============================================================
# Image map extraction
# ============================================================

def extract_image_map(html):
    """Extract resource_link -> image_url mapping from digest HTML."""
    article_match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if not article_match:
        return {}

    article_html = article_match.group(1)
    h3_pattern = r'<h3[^>]*>(.*?)</h3>'
    h3_matches = list(re.finditer(h3_pattern, article_html, re.DOTALL))
    if not h3_matches:
        return {}

    img_pattern = re.compile(
        r'<img[^>]+src=["\']([^"\']*wp-content/uploads[^"\']*)["\']',
        re.IGNORECASE
    )

    claimed_images = set()
    image_map = {}

    for i, match in enumerate(h3_matches):
        h3_inner = match.group(1).strip()

        start = match.end()
        end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(article_html)
        block_html = article_html[start:end]

        # Extract resource link (same logic as extract_resources_from_digest)
        link_in_h3 = re.search(r'<a\s+href="([^"]+)"[^>]*>', h3_inner, re.DOTALL)
        if link_in_h3:
            resource_link = link_in_h3.group(1).strip()
        else:
            block_links = re.findall(r'<a\s+href="([^"]+)"', block_html)
            resource_link = None
            for candidate in block_links:
                if 'wp-content/uploads' in candidate:
                    continue
                if candidate.startswith('#'):
                    continue
                if not candidate.startswith('http'):
                    continue
                resource_link = candidate
                break

        if not resource_link:
            iframe_match = re.search(
                r'<iframe[^>]+src=["\'](?:https?:)?//(?:www\.)?youtube\.com/embed/([^"\'?]+)',
                block_html, re.IGNORECASE
            )
            if iframe_match:
                resource_link = f"https://www.youtube.com/watch?v={iframe_match.group(1)}"

        if not resource_link:
            continue
        if 'suvitruf.ru' in resource_link and 'wp-content' not in resource_link:
            continue

        # Zones for image search
        after_zone = block_html
        before_start = h3_matches[i - 1].end() if i > 0 else 0
        before_zone = article_html[before_start:match.start()]

        # Search after_zone first, then before_zone
        found_img = None
        for img_match in img_pattern.finditer(after_zone):
            img_url = img_match.group(1)
            if img_url not in claimed_images:
                found_img = img_url
                break

        if not found_img:
            for img_match in img_pattern.finditer(before_zone):
                img_url = img_match.group(1)
                if img_url not in claimed_images:
                    found_img = img_url
                    break

        # YouTube thumbnail fallback
        if not found_img:
            yt_match = re.match(
                r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([^&?]+)',
                resource_link
            )
            if yt_match:
                found_img = f"https://img.youtube.com/vi/{yt_match.group(1)}/maxresdefault.jpg"

        if found_img:
            claimed_images.add(found_img)
            image_map[resource_link] = found_img

    return image_map


# ============================================================
# Date extraction
# ============================================================

def parse_date_string(raw):
    """Parse a date string into dd.mm.YYYY format."""
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

    # Textual English: "15 Mar, 2022" / "15 March 2022" (Steam release dates etc.)
    m = re.search(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b', raw)
    if m:
        day, year = int(m.group(1)), int(m.group(3))
        month = MONTHS_EN.get(m.group(2)[:3].lower())
        if month and 1990 <= year <= 2030 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    # Textual English: "Mar 15, 2022" / "March 15 2022"
    m = re.search(r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b', raw)
    if m:
        day, year = int(m.group(2)), int(m.group(3))
        month = MONTHS_EN.get(m.group(1)[:3].lower())
        if month and 1990 <= year <= 2030 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    return None


def extract_date_from_url(url):
    """Extract date from URL path patterns."""
    m = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', url)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    m = re.search(r'/(\d{4})-(\d{2})-(\d{2})/', url)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{day:02d}.{month:02d}.{year}"

    return None


META_TAG_RE = re.compile(r'<meta\b[^>]*>', re.IGNORECASE)
META_ATTR_RE = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(["\'])(.*?)\2', re.DOTALL)

# (attribute, value) pairs of <meta> tags that carry a publication date,
# in priority order. Values are compared lowercase.
META_DATE_KEYS = [
    ("property", "article:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datepublished"),      # youtube
    ("itemprop", "uploaddate"),         # youtube, vimeo
    ("itemprop", "datecreated"),
    ("property", "og:published_time"),
    ("property", "video:release_date"),
    ("name", "date"),
    ("name", "pubdate"),
    ("name", "publishdate"),
    ("name", "publish_date"),
    ("name", "publish-date"),
    ("name", "publication_date"),
    ("name", "publication-date"),
    ("name", "dc.date"),
    ("name", "dc.date.issued"),
    ("name", "dcterms.date"),
    ("name", "article:published"),
    ("name", "parsely-pub-date"),
    ("name", "sailthru.date"),
]


def extract_meta_dates(html):
    """Parse all <meta> tags, return {(attr, lowercase value): content}.

    Attribute-order independent, tolerant of extra attributes (nonce,
    data-rh, etc.) that break naive adjacency regexes.
    """
    found = {}
    for tag in META_TAG_RE.findall(html):
        attrs = {name.lower(): val for name, _, val in META_ATTR_RE.findall(tag)}
        content = attrs.get("content", "").strip()
        if not content:
            continue
        for key_attr in ("property", "name", "itemprop"):
            val = attrs.get(key_attr)
            if val:
                found.setdefault((key_attr, val.strip().lower()), content)
    return found


def extract_date_from_html(html, url):
    """Extract publication date from HTML content."""
    # Strategy 1: <meta> tags (Open Graph, itemprop microdata, name= variants)
    meta_dates = extract_meta_dates(html)
    for key in META_DATE_KEYS:
        raw = meta_dates.get(key)
        if raw:
            parsed = parse_date_string(raw)
            if parsed:
                return parsed

    # Strategy 2: JSON-LD datePublished / uploadDate / dateCreated
    ld_blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    for ld_key in ("datePublished", "uploadDate", "dateCreated"):
        for block in ld_blocks:
            m = re.search(r'"' + ld_key + r'"\s*:\s*"([^"]+)"', block)
            if m:
                parsed = parse_date_string(m.group(1))
                if parsed:
                    return parsed

    # Strategy 3: datetime="..." on any element (<time>, github <relative-time>, ...)
    time_matches = re.findall(r'<[a-zA-Z][^>]*\sdatetime=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for raw in time_matches:
        parsed = parse_date_string(raw)
        if parsed:
            return parsed

    # Strategy 4: data-*date* attributes (e.g. godotengine data-post-date)
    data_matches = re.findall(r'\sdata-[a-zA-Z-]*date[a-zA-Z-]*=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for raw in data_matches:
        parsed = parse_date_string(raw)
        if parsed:
            return parsed

    # Strategy 5: URL path patterns
    return extract_date_from_url(url)


# ============================================================
# Domain-specific date fallbacks
# ============================================================

TWEET_STATUS_RE = re.compile(r'(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)', re.IGNORECASE)
TWITTER_EPOCH_MS = 1288834974657
GITHUB_REPO_RE = re.compile(r'https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)/?(?:[?#].*)?$')
TELEGRAM_POST_RE = re.compile(r'https?://t\.me/[^/?#]+/\d+', re.IGNORECASE)


def date_from_tweet_url(url):
    """Decode timestamp from tweet snowflake ID. Works offline, even when
    twitter/x.com blocks the fetch."""
    m = TWEET_STATUS_RE.search(url)
    if not m:
        return None
    tweet_id = int(m.group(1))
    if tweet_id < (1 << 22):  # pre-snowflake IDs (before Nov 2010)
        return None
    ts = ((tweet_id >> 22) + TWITTER_EPOCH_MS) / 1000.0
    t = time.gmtime(ts)
    if 2010 <= t.tm_year <= 2030:
        return f"{t.tm_mday:02d}.{t.tm_mon:02d}.{t.tm_year}"
    return None


def date_from_steam_page(html):
    """Steam store pages carry the release date as text:
    <div class="release_date">...<div class="date">15 Mar, 2022</div>"""
    m = re.search(r'class="release_date".{0,500}?class="date">\s*([^<]+)<', html, re.DOTALL)
    if m:
        return parse_date_string(m.group(1))
    return None


def date_from_telegram_embed(url):
    """t.me post pages hide the date; the ?embed=1 variant has <time datetime>."""
    if not TELEGRAM_POST_RE.match(url):
        return None
    html = fetch_html(url.split("?")[0] + "?embed=1", timeout=RESOURCE_TIMEOUT)
    if not html:
        return None
    m = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return parse_date_string(m.group(1))
    return None


def date_from_github_api(url):
    """Repo root pages render no dates; the API exposes created_at."""
    m = GITHUB_REPO_RE.match(url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner.lower() in ("topics", "orgs", "sponsors", "collections", "features", "about"):
        return None
    raw = fetch_url(f"https://api.github.com/repos/{owner}/{repo}", timeout=RESOURCE_TIMEOUT)
    if not raw:
        return None
    try:
        created = json.loads(raw).get("created_at", "")
    except Exception:
        return None
    return parse_date_string(created)


WAYBACK_TOOLBAR_RE = re.compile(
    r'<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->',
    re.DOTALL,
)


def date_from_wayback_cdx(url):
    """Last resort: the Wayback Machine. Finds the earliest snapshot, tries to
    extract the real publication date from the archived page content, and
    falls back to the snapshot timestamp (an upper bound on publication).

    Note: no server-side statuscode filter — it makes CDX queries drastically
    slower and timeout-prone; fetch a few rows and pick client-side instead."""
    api = ("https://web.archive.org/cdx/search/cdx?url=" + quote(url, safe="") +
           "&output=json&fl=timestamp,statuscode&limit=5")
    raw = fetch_url(api, timeout=CDX_TIMEOUT)
    if not raw:
        # CDX gets slow under concurrent queries; one retry catches most misses
        time.sleep(5)
        raw = fetch_url(api, timeout=CDX_TIMEOUT)
    if not raw:
        return None
    try:
        rows = json.loads(raw)[1:]  # first row is the header
    except Exception:
        return None
    ts = None
    for row in rows:
        status = row[1] if len(row) > 1 else ""
        if status[:1] in ("2", "3"):
            ts = row[0]
            break
    if ts is None and rows:
        ts = rows[0][0]
    if not ts or not re.match(r'\d{8}', ts):
        return None

    snap_date = parse_date_string(f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}")

    # The archived page usually preserves the original date markup — a real
    # publication date beats the snapshot-time approximation
    snap_url = f"https://web.archive.org/web/{ts}/{url}"
    snap_html = fetch_html(snap_url, timeout=30)
    if not snap_html:
        time.sleep(5)
        snap_html = fetch_html(snap_url, timeout=30)
    if snap_html:
        content_date = extract_date_from_html(WAYBACK_TOOLBAR_RE.sub("", snap_html), url)
        if content_date:
            day, month, year = content_date.split(".")
            # sanity: publication cannot postdate the capture
            if year + month + day <= ts[:8]:
                return content_date

    return snap_date


def extract_date_fallbacks(url, html):
    """Domain-specific fallbacks, tried when generic HTML extraction fails."""
    date = date_from_tweet_url(url)
    if date:
        return date

    if html and "store.steampowered.com" in url:
        date = date_from_steam_page(html)
        if date:
            return date

    date = date_from_telegram_embed(url)
    if date:
        return date

    date = date_from_github_api(url)
    if date:
        return date

    return date_from_wayback_cdx(url)


# ============================================================
# Resource processing (merged: language + author + date)
# ============================================================

def process_single_resource(res):
    """Fetch resource URL once, extract language, author, and date."""
    url = res["Link"]
    language = "en"
    author = ""
    date = DEFAULT_DATE

    html = fetch_html(url, timeout=RESOURCE_TIMEOUT)

    if html:
        # --- Language detection ---
        lang_match = re.search(r'<html[^>]+lang=["\']([a-zA-Z]{2})', html, re.IGNORECASE)
        if lang_match:
            language = lang_match.group(1).lower()
        elif re.search(r'\.(ru|by|ua|kz)(/|$)', url):
            language = "ru"

        # --- Author extraction ---
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
                if author.startswith("http"):
                    author = ""
                break

        if not author:
            ld_match = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
            if ld_match:
                author = unescape(ld_match.group(1).strip())

        if not author:
            rel_author = re.search(r'<a[^>]+rel=["\']author["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
            if rel_author:
                author = unescape(rel_author.group(1).strip())

        # --- Date extraction ---
        extracted_date = extract_date_from_html(html, url)
        if extracted_date:
            date = extracted_date
    else:
        # URL-based fallbacks when fetch fails
        if re.search(r'\.(ru|by|ua|kz)(/|$)', url):
            language = "ru"
        url_date = extract_date_from_url(url)
        if url_date:
            date = url_date

    # Domain-specific fallbacks (tweet IDs, steam, telegram, github API, wayback)
    if date == DEFAULT_DATE:
        fallback_date = extract_date_fallbacks(url, html)
        if fallback_date:
            date = fallback_date

    res["Language"] = language
    res["Author"] = author
    res["Date"] = date
    return res


# ============================================================
# Wayback Machine archival
# ============================================================

def archive_to_wayback(url):
    """Submit URL to Save Page Now, return snapshot URL or ''.

    Retries on transient failures: timeouts and 5xx responses (SPN often
    returns 520/523 from its Cloudflare layer when under load). 429 triggers
    a longer backoff. Other HTTP errors are treated as permanent.
    """
    req = urllib.request.Request(
        SPN_ENDPOINT + url,
        headers={
            "User-Agent": "Mozilla/5.0 gamedev-links-archiver",
            "Accept": "text/html,*/*",
        },
        method="GET",
    )
    last_err = ""
    for attempt in range(ARCHIVE_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=ARCHIVE_TIMEOUT, context=ssl_ctx) as resp:
                content_location = resp.headers.get("Content-Location") or ""
                if content_location.startswith("/web/"):
                    return "https://web.archive.org" + content_location

                final_url = resp.geturl()
                if "/web/" in final_url:
                    return final_url

                body = resp.read(4096).decode("utf-8", "ignore")
                m = re.search(r'/web/(\d{14})/', body)
                if m:
                    return f"https://web.archive.org/web/{m.group(1)}/{url}"
                return ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTPError {e.code}"
            if e.code == 429:
                time.sleep(ARCHIVE_RETRY_BACKOFF)
                continue
            if 500 <= e.code < 600 and attempt < ARCHIVE_RETRIES:
                time.sleep(ARCHIVE_RETRY_SLEEP)
                continue
            break
        except Exception as e:
            last_err = str(e)
            if attempt < ARCHIVE_RETRIES:
                time.sleep(ARCHIVE_RETRY_SLEEP)
                continue
            break
    print(f"  [WARN] SPN failed for {url}: {last_err}")
    return ""


# ============================================================
# Classification
# ============================================================

def get_domain(link):
    """Extract domain from URL, stripping www. prefix."""
    try:
        host = urlparse(link).hostname or ""
        return host.lower().removeprefix("www.")
    except Exception:
        return ""


def classify_type(domain):
    if domain in VIDEO_DOMAINS:
        return "video"
    if domain in SOCIAL_DOMAINS:
        return "social"
    if domain in REPO_DOMAINS:
        return "repository"
    if domain in STORE_DOMAINS:
        return "store"
    if domain in ARTICLE_DOMAINS:
        return "article"
    for d in VIDEO_DOMAINS:
        if domain.endswith("." + d):
            return "video"
    for d in SOCIAL_DOMAINS:
        if domain.endswith("." + d):
            return "social"
    for d in REPO_DOMAINS:
        if domain.endswith("." + d):
            return "repository"
    for d in STORE_DOMAINS:
        if domain.endswith("." + d):
            return "store"
    return "article"


def classify_tags(domain, text):
    tags = []

    # Unreal Engine
    if domain.endswith("unrealengine.com") or domain == "unrealengine.com":
        tags.append("unreal engine")
    elif "unreal engine" in text.lower() or "unreal" in text.lower() or RE_UE4.search(text) or RE_UE5.search(text):
        tags.append("unreal engine")

    # Unity
    if domain in ("blog.unity.com", "unity.com") or domain.endswith(".unity.com"):
        tags.append("unity")
    elif "unity" in text.lower():
        tags.append("unity")

    # Godot
    if domain.endswith("godotengine.org") or domain == "godotengine.org":
        tags.append("godot")
    elif "godot" in text.lower():
        tags.append("godot")

    # Blender
    if "blender" in text.lower():
        tags.append("blender")

    # Houdini
    if "houdini" in text.lower():
        tags.append("houdini")

    # Substance
    if "substance" in text.lower():
        tags.append("substance")

    # Maya
    if RE_MAYA.search(text):
        tags.append("maya")

    # ZBrush
    if "zbrush" in text.lower():
        tags.append("zbrush")

    # Opensource
    if domain in ("github.com", "gitlab.com") or domain.endswith(".github.com") or domain.endswith(".gitlab.com"):
        tags.append("opensource")
    elif any(kw in text.lower() for kw in ("open source", "opensource", "открытый код", "open-source")):
        tags.append("opensource")

    # Free
    if "бесплатн" in text.lower() or RE_FREE.search(text) or domain == "itch.io" or domain.endswith(".itch.io"):
        tags.append("free")

    # Steam
    if domain.endswith("steampowered.com") or domain == "steampowered.com":
        tags.append("steam")
    elif "steam" in text.lower():
        tags.append("steam")

    # PlayStation
    if any(kw in text.lower() for kw in ("playstation",)) or any(kw in text for kw in ("PS4", "PS5")):
        tags.append("playstation")

    # Xbox
    if "xbox" in text.lower():
        tags.append("xbox")

    # Nintendo
    if "nintendo" in text.lower() or RE_SWITCH.search(text):
        tags.append("nintendo")

    # AI
    if RE_AI.search(text) or "machine learning" in text.lower() or "нейросет" in text.lower() or "искусственн" in text.lower():
        tags.append("ai")

    # XR
    if RE_VR.search(text) or RE_AR.search(text) or RE_XR.search(text) or "virtual reality" in text.lower() or "виртуальн" in text.lower():
        tags.append("xr")

    # Shaders
    if "shader" in text.lower() or "шейдер" in text.lower():
        tags.append("shaders")

    # Animation
    if "animation" in text.lower() or "анимаци" in text.lower():
        tags.append("animation")

    # Procedural
    if "procedural" in text.lower() or "процедурн" in text.lower():
        tags.append("procedural")

    return tags


# ============================================================
# Image processing
# ============================================================

def resize_and_crop(img, target_w=TARGET_W, target_h=TARGET_H):
    """Resize and center-crop image to target dimensions."""
    scale = max(target_w / img.width, target_h / img.height)
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (img.width - target_w) // 2
    top = (img.height - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def clean_filename(url):
    """Extract clean filename from image URL, removing WP size suffixes."""
    path = url.split("?")[0]
    filename = os.path.basename(path)
    filename = WP_SIZE_RE.sub("", filename)
    name, _ = os.path.splitext(filename)
    if not name:
        name = "image"
    return name + ".jpg"


def download_and_process_image(img_url, save_path):
    """Download an image, resize/crop to 300x120, save as JPEG."""
    raw = fetch_url(img_url, timeout=10)
    if raw is None:
        return False

    try:
        img = Image.open(io.BytesIO(raw))

        # Handle animated GIFs
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)

        # Convert to RGB for JPEG
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Skip very small images (likely icons/buttons)
        if img.width < 50 or img.height < 50:
            return False

        img = resize_and_crop(img)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img.save(save_path, "JPEG", quality=JPEG_QUALITY)
        return True
    except Exception as e:
        print(f"    [WARN] Failed to process image {img_url}: {e}")
        return False


# ============================================================
# Main orchestrator
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Process a single weekly gamedev digest.")
    parser.add_argument("digest_url", help="URL of the digest page")
    parser.add_argument("--skip-archive", action="store_true",
                        help="Skip Wayback Machine archival step")
    args = parser.parse_args()

    digest_url = args.digest_url

    # Load existing state
    data = load_json(DATA_FILE, [])
    progress = load_json(PROGRESS_FILE, {})

    # Fetch digest page once
    print(f"Fetching digest: {digest_url}")
    html = fetch_html(digest_url)
    if not html:
        print("[ERROR] Could not fetch digest page")
        sys.exit(1)

    # Parse header
    number, date = parse_digest_header(html)
    if number is None:
        print("[ERROR] Could not parse digest header")
        sys.exit(1)

    print(f"Digest #{number}, date: {date}")

    # Check if already processed
    if str(number) in progress:
        print(f"Digest #{number} already processed, skipping")
        sys.exit(0)

    # Extract resources from digest HTML
    resources = extract_resources_from_digest(html)
    print(f"Found {len(resources)} resources")

    if not resources:
        progress[str(number)] = digest_url
        save_json(PROGRESS_FILE, progress)
        print("No resources found, marked as processed")
        return

    # Extract image map from the same digest HTML
    image_map = extract_image_map(html)
    print(f"Found {len(image_map)} images in digest")

    # Process resources in parallel (language + author + date in one fetch)
    print("Fetching resource pages for language/author/date...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_resource, res): res for res in resources}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 10 == 0:
                print(f"  Processed {done_count}/{len(resources)} links...")
            try:
                future.result()
            except Exception as e:
                res = futures[future]
                res.setdefault("Language", "en")
                res.setdefault("Author", "")
                res.setdefault("Date", DEFAULT_DATE)
                print(f"  [WARN] Error processing {res['Link']}: {e}")

    dated = sum(1 for res in resources if res.get("Date", DEFAULT_DATE) != DEFAULT_DATE)
    print(f"Dates resolved for {dated}/{len(resources)} resources")

    # Build final records with classification
    new_records = []
    for res in resources:
        domain = get_domain(res["Link"])
        text = res.get("Title", "") + " " + res.get("Description", "")
        record = {
            "Link": res["Link"],
            "Title": res["Title"],
            "Author": res.get("Author", ""),
            "Language": res.get("Language", "en"),
            "Description": res.get("Description", ""),
            "DigestNumber": number,
            "DigestDate": date,
            "Type": classify_type(domain),
            "Tags": classify_tags(domain, text),
            "Date": res.get("Date", DEFAULT_DATE),
            "Image": "",
            "WaybackURL": "",
        }
        new_records.append(record)

    # Archive to Wayback Machine
    if args.skip_archive:
        print("Skipping Wayback Machine archival (--skip-archive)")
    else:
        print(f"Archiving {len(new_records)} URLs to Wayback Machine (this may take several minutes)...")
        with ThreadPoolExecutor(max_workers=ARCHIVE_WORKERS) as executor:
            futures = {executor.submit(archive_to_wayback, r["Link"]): r for r in new_records}
            done = 0
            for future in as_completed(futures):
                done += 1
                record = futures[future]
                try:
                    record["WaybackURL"] = future.result() or ""
                except Exception as e:
                    print(f"  [WARN] Archive task error for {record['Link']}: {e}")
                    record["WaybackURL"] = ""
                if done % 5 == 0:
                    print(f"  Archived {done}/{len(new_records)}...")
        archived = sum(1 for r in new_records if r["WaybackURL"])
        print(f"Archived {archived}/{len(new_records)} URLs")

    # Download and process images
    print("Downloading images...")
    used_filenames = set()
    image_tasks = []

    for record in new_records:
        img_url = image_map.get(record["Link"])
        if not img_url:
            continue
        filename = clean_filename(img_url)
        base_name, ext = os.path.splitext(filename)
        final_name = filename
        counter = 2
        while final_name in used_filenames:
            final_name = f"{base_name}_{counter}{ext}"
            counter += 1
        used_filenames.add(final_name)
        local_path = os.path.join("assets", "images", str(number), final_name)
        full_path = os.path.join(PROJECT_DIR, local_path)
        image_tasks.append((record, img_url, full_path, local_path))

    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as executor:
        futures = {}
        for record, img_url, full_path, local_path in image_tasks:
            future = executor.submit(download_and_process_image, img_url, full_path)
            futures[future] = (record, local_path)
        for future in as_completed(futures):
            record, local_path = futures[future]
            try:
                if future.result():
                    record["Image"] = local_path
            except Exception:
                pass

    img_count = sum(1 for r in new_records if r["Image"])
    print(f"Downloaded {img_count}/{len(image_tasks)} images")

    # Append to data.json
    data.extend(new_records)
    save_json(DATA_FILE, data)

    # Mark as processed
    progress[str(number)] = digest_url
    save_json(PROGRESS_FILE, progress)

    print(f"Done! Added {len(new_records)} records for digest #{number}")


if __name__ == "__main__":
    main()
