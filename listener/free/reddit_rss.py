"""Reddit RSS: rate limits + rotate feeds across runs."""


def select_feeds(feeds, feeds_per_run, store):
    """Return a subset of feeds; rotate start index in fetch_state when store is set."""
    feeds = list(feeds or [])
    if not feeds:
        return []
    n = int(feeds_per_run or 0)
    if n <= 0 or n >= len(feeds):
        return feeds
    start = 0
    if store is not None:
        st = store.get_fetch_state("reddit_rss_rotate", "index")
        if st and st.get("cursor"):
            try:
                start = int(st["cursor"]) % len(feeds)
            except ValueError:
                start = 0
    chunk = [feeds[(start + i) % len(feeds)] for i in range(n)]
    if store is not None:
        store.set_fetch_state("reddit_rss_rotate", "index", str((start + n) % len(feeds)))
    return chunk
