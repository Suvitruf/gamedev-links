#!/usr/bin/env python3
"""
Fetch thumbnail images from weekly gamedev digest pages, resize/crop to 300x120,
and store locally. Updates data.json with Image field.
Resumable: tracks progress in image_progress.json.

Requires: Pillow (pip install Pillow)
"""

import json
import re
import time
import os
import sys
import io
import urllib.request
import urllib.error
import ssl
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from PIL import Image
except ImportError:
    print("Pillow is required. Install it with: pip install Pillow")
    sys.exit(1)

RAW_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(RAW_DIR)
DATA_FILE = os.path.join(RAW_DIR, "data.json")
PROGRESS_FILE = os.path.join(RAW_DIR, "image_progress.json")
PROCESSED_DIGESTS_FILE = os.path.join(RAW_DIR, "processed_digests.json")
IMAGES_DIR = os.path.join(PROJECT_DIR, "assets", "images")

TARGET_W = 300
TARGET_H = 120
JPEG_QUALITY = 85
IMAGE_WORKERS = 3
DIGEST_DELAY = 0.5

# SSL context that doesn't verify (some old resource links have expired certs)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# WordPress size suffix pattern: -1024x420, -300x200, etc.
WP_SIZE_RE = re.compile(r'-\d+x\d+(?=\.\w+$)')


def fetch_url(url, timeout=15):
    """Fetch a URL and return its content as bytes."""
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


def fetch_html(url, timeout=15):
    """Fetch a URL and return its HTML as string."""
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
    """Extract a clean filename from an image URL, removing WP size suffixes."""
    # Get the filename from URL path
    path = url.split("?")[0]
    filename = os.path.basename(path)
    # Remove WordPress size suffix (e.g., -1024x420)
    filename = WP_SIZE_RE.sub("", filename)
    # Change extension to .jpg
    name, _ = os.path.splitext(filename)
    if not name:
        name = "image"
    return name + ".jpg"


def download_and_process_image(img_url, save_path):
    """Download an image, resize/crop it, and save as JPEG."""
    raw = fetch_url(img_url, timeout=10)
    if raw is None:
        return False

    try:
        img = Image.open(io.BytesIO(raw))

        # Handle animated GIFs: extract first frame
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)

        # Convert RGBA/P/LA to RGB for JPEG
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


def extract_image_map(html, digest_number):
    """
    Extract a mapping of resource_link -> image_url from a digest page.

    Uses greedy forward pass: for each h3, search after_zone first, then before_zone.
    Tracks claimed images to avoid double-counting.
    """
    # Get article content
    article_match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if not article_match:
        return {}

    article_html = article_match.group(1)

    # Find all h3 matches (same regex as parse_digests.py)
    h3_pattern = r'<h3[^>]*>(.*?)</h3>'
    h3_matches = list(re.finditer(h3_pattern, article_html, re.DOTALL))

    if not h3_matches:
        return {}

    # Image pattern: <img> tags with wp-content/uploads in src
    img_pattern = re.compile(
        r'<img[^>]+src=["\']([^"\']*wp-content/uploads[^"\']*)["\']',
        re.IGNORECASE
    )

    claimed_images = set()
    image_map = {}  # resource_link -> image_url

    for i, match in enumerate(h3_matches):
        h3_inner = match.group(1).strip()

        # Extract resource link from h3 (same logic as parse_digests.py)
        link_in_h3 = re.search(r'<a\s+href="([^"]+)"[^>]*>', h3_inner, re.DOTALL)
        if link_in_h3:
            resource_link = link_in_h3.group(1).strip()
        else:
            # Link is in the content block after h3
            start = match.end()
            end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(article_html)
            block_html = article_html[start:end]
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
            continue

        # Skip suvitruf self-links
        if 'suvitruf.ru' in resource_link and 'wp-content' not in resource_link:
            continue

        # Define zones
        after_start = match.end()
        after_end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(article_html)
        after_zone = article_html[after_start:after_end]

        before_start = h3_matches[i - 1].end() if i > 0 else 0
        before_end = match.start()
        before_zone = article_html[before_start:before_end]

        # Search after_zone first
        found_img = None
        for img_match in img_pattern.finditer(after_zone):
            img_url = img_match.group(1)
            if img_url not in claimed_images:
                found_img = img_url
                break

        # If not found, search before_zone
        if not found_img:
            for img_match in img_pattern.finditer(before_zone):
                img_url = img_match.group(1)
                if img_url not in claimed_images:
                    found_img = img_url
                    break

        if found_img:
            claimed_images.add(found_img)
            image_map[resource_link] = found_img

    return image_map


def process_digest_images(digest_number, digest_url, progress):
    """Process images for a single digest."""
    print(f"\n  Fetching digest #{digest_number}: {digest_url}")
    html = fetch_html(digest_url)
    if not html:
        print(f"  [ERROR] Could not fetch digest page")
        return {}

    image_map = extract_image_map(html, digest_number)
    print(f"  Found {len(image_map)} images to download")

    if not image_map:
        return {}

    # Download and process images in parallel
    digest_dir = os.path.join(IMAGES_DIR, str(digest_number))
    results = {}  # resource_link -> local_path
    used_filenames = set()

    # Prepare download tasks
    tasks = []
    for resource_link, img_url in image_map.items():
        filename = clean_filename(img_url)

        # Handle duplicate filenames within the same digest
        base_name, ext = os.path.splitext(filename)
        final_name = filename
        counter = 2
        while final_name in used_filenames:
            final_name = f"{base_name}_{counter}{ext}"
            counter += 1
        used_filenames.add(final_name)

        local_path = os.path.join("assets", "images", str(digest_number), final_name)
        full_path = os.path.join(PROJECT_DIR, local_path)
        tasks.append((resource_link, img_url, full_path, local_path))

    # Download in parallel
    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as executor:
        futures = {}
        for resource_link, img_url, full_path, local_path in tasks:
            future = executor.submit(download_and_process_image, img_url, full_path)
            futures[future] = (resource_link, local_path)

        for future in as_completed(futures):
            resource_link, local_path = futures[future]
            try:
                success = future.result()
                if success:
                    results[resource_link] = local_path
            except Exception as e:
                print(f"    [WARN] Error downloading image for {resource_link}: {e}")

    print(f"  Successfully downloaded {len(results)}/{len(image_map)} images")
    return results


def main():
    # Load data
    data = load_json(DATA_FILE, [])
    progress = load_json(PROGRESS_FILE, {})
    processed_digests = load_json(PROCESSED_DIGESTS_FILE, {})

    print(f"Total records in data.json: {len(data)}")
    print(f"Processed digests with images: {len(progress)}")
    print(f"Total digests available: {len(processed_digests)}")

    # Process each digest
    digests_to_process = []
    for digest_num, digest_url in processed_digests.items():
        if digest_num not in progress:
            digests_to_process.append((digest_num, digest_url))

    print(f"Digests to process: {len(digests_to_process)}")

    if not digests_to_process:
        print("All digests already processed for images.")
    else:
        # Sort by digest number for consistent ordering
        digests_to_process.sort(key=lambda x: int(x[0]))

        for i, (digest_num, digest_url) in enumerate(digests_to_process):
            print(f"\n{'='*60}")
            print(f"Processing digest {i+1}/{len(digests_to_process)}")
            print(f"{'='*60}")

            results = process_digest_images(digest_num, digest_url, progress)

            # Save progress after each digest
            progress[digest_num] = results
            save_json(PROGRESS_FILE, progress)

            # Delay between digests
            if i < len(digests_to_process) - 1:
                time.sleep(DIGEST_DELAY)

    # Apply images to data.json
    print(f"\n{'='*60}")
    print("Applying image paths to data.json...")
    print(f"{'='*60}")

    updated = 0
    no_image = 0
    for record in data:
        link = record["Link"]
        digest_num = str(record.get("DigestNumber", ""))

        digest_images = progress.get(digest_num, {})
        if isinstance(digest_images, dict) and link in digest_images:
            record["Image"] = digest_images[link]
            updated += 1
        else:
            record["Image"] = ""
            no_image += 1

    save_json(DATA_FILE, data)
    print(f"Updated {len(data)} records: {updated} with images, {no_image} without.")
    print("Done!")


if __name__ == "__main__":
    main()
