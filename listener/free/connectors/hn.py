"""Hacker News via Algolia (free)."""
from listener import sources


def fetch(query, days=7):
    rows = sources.fetch_hn(query, days=days)
    for r in rows:
        r["platform"] = "hackernews"
    return rows
