#!/usr/bin/env python3
"""Add Type and Tags fields to every record in data.json."""

import json
import re
from urllib.parse import urlparse

INPUT = "data.json"


def get_domain(link: str) -> str:
    """Extract domain from URL, stripping www. prefix."""
    try:
        host = urlparse(link).hostname or ""
        return host.lower().removeprefix("www.")
    except Exception:
        return ""


# --- Type classification by domain ---

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


def classify_type(domain: str) -> str:
    # Check exact domain first
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

    # Check if domain ends with any known domain (for subdomains)
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


# --- Tag classification ---

# Precompile regex patterns for word-boundary matches
RE_AI = re.compile(r"\bAI\b")
RE_VR = re.compile(r"\bVR\b")
RE_AR = re.compile(r"\bAR\b")
RE_XR = re.compile(r"\bXR\b")
RE_FREE = re.compile(r"\bfree\b", re.IGNORECASE)
RE_MAYA = re.compile(r"\bMaya\b")
RE_SWITCH = re.compile(r"\bSwitch\b")
RE_UE4 = re.compile(r"\bUE4\b")
RE_UE5 = re.compile(r"\bUE5\b")


def classify_tags(domain: str, text: str) -> list[str]:
    """Build tags list by checking domain + keyword rules against combined title+description."""
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

    # Maya (word boundary)
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

    # XR (VR/AR/XR)
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


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    print(f"Loaded {len(data)} records")

    for rec in data:
        domain = get_domain(rec.get("Link", ""))
        text = (rec.get("Title", "") + " " + rec.get("Description", ""))

        rec["Type"] = classify_type(domain)
        rec["Tags"] = classify_tags(domain, text)

    # Verify
    missing_type = sum(1 for r in data if not r.get("Type"))
    missing_tags = sum(1 for r in data if "Tags" not in r)
    print(f"Records missing Type: {missing_type}")
    print(f"Records missing Tags: {missing_tags}")

    # Stats
    from collections import Counter
    type_counts = Counter(r["Type"] for r in data)
    print("\nType distribution:")
    for t, c in type_counts.most_common():
        print(f"  {t}: {c}")

    tag_counts = Counter()
    for r in data:
        for tag in r["Tags"]:
            tag_counts[tag] += 1
    print(f"\nTag distribution:")
    for t, c in tag_counts.most_common():
        print(f"  {t}: {c}")

    tagged = sum(1 for r in data if r["Tags"])
    print(f"\nRecords with at least one tag: {tagged}/{len(data)}")

    with open(INPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(data)} records to {INPUT}")


if __name__ == "__main__":
    main()
