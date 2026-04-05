---
name: parse
description: Parse digest article to grab site info. Use this skill when user asks to parse digest.
---

When user asks to parse a digest, run the unified processing script:

```bash
python3 raw/process_digest.py <digest_url>
```

This script handles everything for one digest URL:
1. Fetches the digest page
2. Extracts all resource links (h3 blocks) with titles and descriptions
3. Visits each resource to detect language, author, and publication date
4. Classifies type (article/video/social/store/repository) and tags
5. Downloads and processes thumbnail images (300x120 JPEG)
6. Appends records to `raw/data.json`
7. Updates `raw/processed_digests.json`

## Page types

1. **Listing page** (e.g. `https://suvitruf.ru/page/28/`) — contains links to digest pages. Look for `<article>` blocks with `<h1>` headers starting with "Недельный геймдев".
2. **Digest page** (e.g. `https://suvitruf.ru/2021/01/18/8324/weekly-gamedev-1-17-january-2021/`) — the page to pass to the script.

If user provides a listing page, extract the digest URLs first, then run the script for each one.

## HTML structure of a digest page

- Content is inside an `<article>` block
- `<h1>` header format: `Недельный геймдев: #<number> — <day> <month>, <year>`
- `<h2>` headers define sections (news, articles/videos)
- `<h3>` headers are individual resource entries with title and link
- Images are `<img>` tags with `wp-content/uploads` in src

## Record format in data.json

```json
{
  "Link": "https://...",
  "Title": "...",
  "Author": "...",
  "Language": "en",
  "Description": "...",
  "DigestNumber": 7,
  "DigestDate": "2021-02-28",
  "Type": "article",
  "Tags": ["blender"],
  "Date": "01.01.1970",
  "Image": "assets/images/7/filename.jpg"
}
```

## Type classification

- **video**: youtube.com, youtu.be, vimeo.com, twitch.tv
- **social**: twitter.com, x.com, reddit.com
- **repository**: github.com, gitlab.com
- **store**: store.steampowered.com, store.epicgames.com, assetstore.unity.com, fab.com, itch.io, gumroad.com
- **article**: 80.lv, habr.com, gamedeveloper.com, and default

## Tag classification

Tags are assigned by domain and keyword matching: unreal engine, unity, godot, blender, houdini, substance, maya, zbrush, opensource, free, steam, playstation, xbox, nintendo, ai, xr, shaders, animation, procedural.
