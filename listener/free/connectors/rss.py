"""RSS / Atom feeds (Reddit subreddits, hnrss, TechCrunch, etc.)."""
from listener import generic


def fetch_feed(feed_url, keywords=None, keyword_filter=None, reddit_opts=None):
    kws = keyword_filter if keyword_filter is not None else keywords
    opts = reddit_opts or {}
    min_interval = 0
    max_retries = 3
    if "reddit.com" in (feed_url or ""):
        min_interval = float(opts.get("delay_seconds") or 4)
        max_retries = int(opts.get("max_retries") or 4)
    rows = generic.fetch_rss(
        feed_url, kws, min_interval=min_interval, max_retries=max_retries,
    )
    for r in rows:
        r["platform"] = r.get("platform") or "rss"
        if r["source"] == "rss" and "reddit.com" in feed_url:
            r["source"] = "reddit"
            r["platform"] = "reddit"
    return rows


def fetch_funding_feed(feed_url, keywords=None):
    rows = fetch_feed(feed_url, keywords)
    for r in rows:
        r["kind"] = "funding"
        r["platform"] = "funding_rss"
        r["source"] = "funding"
    return rows
