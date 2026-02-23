# Gamedev Links

A curated collection of links to videos, articles, and other materials for game developers. Based on the [Weekly Gamedev Digest](https://suvitruf.ru/2026/02/22/19690/weekly-gamedev-266-22-february-2026/) newsletter.

![Table screenshot](docs/Assets/table.png)

## Features

- Searchable and filterable table of gamedev resources
- Filter by type (article, video, site), language, and tags
- Edit mode for managing entries (add, update, delete)
- Export edited data as a JSON file

## Built With

- [Jekyll](https://jekyllrb.com/) — static site generator
- [GitHub Pages](https://pages.github.com/) — hosting
- Mostly all code writtent via Claude code

## Local Development

```bash
bundle install
bundle exec jekyll serve
```

The site will be available at `http://localhost:4000/gamedev-links/`.

## Data Format

Resource entries are stored in `raw/` as JSON with the following schema:

```json
{
  "Link": "https://example.com",
  "Title": "Resource title",
  "Author": "Author name",
  "Type": "article | video | site",
  "Language": "en",
  "Tags": ["tutorial", "showcase"]
}
```

## Links

- [Gamedev Suffering (Telegram)](https://t.me/gamedev_suffering)
- [Newsletter (Substack)](https://gamedevsuffering.substack.com/)
- [Twitter](https://x.com/Suvitruf)
- [Patreon](https://www.patreon.com/suvitruf)
