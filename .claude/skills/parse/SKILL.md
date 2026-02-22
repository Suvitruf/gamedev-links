---
name: parse
description: Parse digest article to grab site info. Use this skill when user asks to parse digest.
---

When parsing, you need to understand each type of page you are parsing:
1. Page like https://suvitruf.ru/page/28/ (see "page" here?), it contains links to other pages.
2. Page like https://suvitruf.ru/2021/01/18/8324/weekly-gamedev-1-17-january-2021/. A specific digest page.

When parsing a listing page:
1. Each block is inside an <article> block.
2. Inside the <h1> header you can grab info. Grab the link when the title starts with "Недельный геймдев".
3. Parse these links separately.

When parsing a specific digest page, the needed content is inside an <article> block:
1. **Collect digest basic info**: Inside the <h1> header you can grab info. Header format: `Недельный геймдев: #<number> — <day> <month>, <year>`.
2. Chech <h2> header. If it's something like "Обновления/релизы/новости", add type "news" for record. If it's something like "Интересные статьи/видео" and "site"/"article"/"video" depending on type.
3. **Collect links**: Each record starts with an <h3> header. It's a material title. Inside there is usually an image or video, with some text. Grab the title, detect language by opening the link (try to take the author from here), get description (summarize it to be no more than 200 symbols).
4. **Write to base**: Add new records into raw/data.json with fields: link, date, digest number, title.
5. **Update parsed digest list**: Add to the parsed digests list in raw/processed_digests.json to track progress.
6. Add type to records:
- If it is site 80.lv, newsletter.gamediscover.co, habr.com, then add "article"
- If it is video from youtube, add "video"
7. Add tag to records:
- If it is unrealengine.com or about Unreal Engine, add "unreal engine"
- If it is about Unity, add "unity"
- If it is about Godot, add "godot"
- If it is something free or opensource, add tags "free" and "opensource"