# Free lead collectors (no Treg)

Self-hosted, $0 API cost collectors plug into the same SQLite store and `score_signal()` pipeline as Lead Signal Listener.

## Folder structure

```
listener/free/
  defaults.py          # Default queries, feeds, hashtags
  registry.py            # Builds run jobs from config
  normalize.py           # Signal shape helpers
  connectors/
    hn.py                # Algolia HN (stdlib)
    rss.py               # Reddit subreddits, hnrss, TechCrunch, etc.
    twscrape_x.py        # X via twscrape CLI
    instaloader_ig.py    # Instagram hashtags
    playwright_social.py # LinkedIn / Facebook (Playwright + saved session)
```

## Enable

In `data/config.json`:

```json
{
  "free_collectors": {
    "enabled": true,
    "replace_treg": true,
    "incremental": true
  }
}
```

Or merge `config/free_collectors.example.json` into your config (includes scoring rules for agency/ERP intent).

When `replace_treg` is true, paid Treg sources are skipped entirely. Scheduler (`schedule_hours`) and deduplication (`UNIQUE(source, external_id)`) behave as before.

## Incremental fetch

Table `fetch_state` stores the latest `created_utc` cursor per collector + query. The next run only keeps rows at or after that timestamp (within `lookback_days`).

## Normalized signal (storage)

Stored in `signals` as:

| Field | Notes |
|-------|--------|
| source | e.g. `reddit`, `x`, `hn`, `funding` |
| kind | `pain`, `hiring`, `funding` |
| external_id | stable id for dedupe |
| url, title, text, author, author_url | |
| community | subreddit, hashtag, etc. |
| created_utc, engagement, query | |

Extra `platform` may appear in exports from collectors but is not persisted in SQLite.

## Platform setup

| Platform | Connector | Setup |
|----------|-----------|--------|
| Hacker News | Algolia API | None |
| Reddit | Subreddit `.rss` | None (respect rate limits) |
| RSS / funding | stdlib RSS | Feed URLs in config |
| X | [twscrape](https://github.com/vladkens/twscrape) | `pip install twscrape`, add accounts to `data/twscrape.db` |
| Instagram | [Instaloader](https://github.com/instaloader/instaloader) | `pip install instaloader`, login session |
| LinkedIn jobs/posts | Playwright | Save auth storage to `data/playwright-state.json`; extend scraper or drop JSON via `data/playwright_custom.json` |
| Facebook | Playwright | Same as LinkedIn |

Redlib (self-hosted Reddit) can be used by pointing `reddit_rss.feeds` at your Redlib RSS URLs.

## Docker

```bash
docker compose up --build
```

Data persists in `./data`.

## Cron / daily run

```bash
0 8 * * * cd /path/to/IntentCrawl && python3 app.py --run-once --no-browser >> data/cron.log 2>&1
```

Or set `"schedule_hours": 24` in config and keep the web app running.

## Legal / ToS

Scraping LinkedIn, Facebook, and Instagram may violate platform terms. Use official APIs where required. This project provides hooks; you are responsible for compliance.

## Optional deps

```bash
pip install -r requirements-free.txt
pip install twscrape instaloader playwright
playwright install chromium
```
