"""Build runner jobs from free_collectors config."""
import time

from .connectors import hn, instaloader_ig, playwright_social, rss, twscrape_x
from .normalize import filter_since
from .reddit_rss import select_feeds


def build_free_jobs(fcfg, days, store=None):
    """Return list of (name, query, fetch_fn, max_pages, is_free). fetch_fn(cursor) -> (signals, next_cursor)."""
    if not fcfg or not fcfg.get("enabled"):
        return []

    incremental = fcfg.get("incremental", True)
    jobs = []

    def single(fn):
        return lambda cursor: (fn(), None)

    def since_for(collector, query_key):
        if not incremental or store is None:
            return None
        st = store.get_fetch_state(collector, query_key)
        return int(st["cursor"]) if st and st.get("cursor") else None

    def wrap_incremental(collector, query_key, fn):
        def run(cursor=None):
            since = since_for(collector, query_key)
            if since and cursor is None:
                since = max(since, int(time.time()) - days * 86400)
            rows = fn()
            if since:
                rows = filter_since(rows, since)
            if store is not None and rows:
                latest = max((r.get("created_utc") or 0) for r in rows)
                if latest:
                    store.set_fetch_state(collector, query_key, str(latest))
            return rows, None
        return run

    hc = fcfg.get("hn") or {}
    if hc.get("enabled"):
        for q in hc.get("queries") or []:
            key = f"hn:{q}"
            jobs.append(("hn", q, wrap_incremental("hn", key, lambda q=q: hn.fetch(q, days=days)), 1, True))

    rc = fcfg.get("reddit_rss") or {}
    if rc.get("enabled"):
        kws = rc.get("keywords") or []
        reddit_opts = {
            "delay_seconds": rc.get("delay_seconds", 4),
            "max_retries": rc.get("max_retries", 4),
        }
        feed_list = select_feeds(rc.get("feeds") or [], rc.get("feeds_per_run"), store)
        for url in feed_list:
            key = f"reddit_rss:{url}"
            jobs.append((
                "reddit",
                url,
                wrap_incremental(
                    "reddit_rss", key,
                    lambda url=url, kws=kws, reddit_opts=reddit_opts: rss.fetch_feed(
                        url, kws, reddit_opts=reddit_opts),
                ),
                1,
                True,
            ))

    for block_name, source_label in (("rss_extra", "rss"), ("funding_rss", "funding")):
        block = fcfg.get(block_name) or {}
        if not block.get("enabled"):
            continue
        kws = block.get("keywords") or []
        for url in block.get("feeds") or []:
            key = f"{block_name}:{url}"
            if block_name == "funding_rss":
                fn = lambda url=url, kws=kws: rss.fetch_funding_feed(url, kws)
                name = "funding"
            else:
                fn = lambda url=url, kws=kws: rss.fetch_feed(url, kws)
                name = source_label
            jobs.append((name, url, wrap_incremental(block_name, key, fn), 1, True))

    tc = fcfg.get("twitter") or {}
    if tc.get("enabled"):
        limit = int(tc.get("limit") or 40)
        db = tc.get("twscrape_db") or "data/twscrape.db"
        for q in tc.get("queries") or []:
            key = f"x:{q}"
            jobs.append((
                "x",
                q,
                wrap_incremental("twitter", key, lambda q=q: twscrape_x.fetch(q, limit=limit, db_path=db)),
                1,
                True,
            ))

    lj = fcfg.get("linkedin_jobs") or {}
    if lj.get("enabled"):
        loc = lj.get("location") or "United States"
        pw = lj.get("playwright") or {}
        for q in lj.get("queries") or []:
            key = f"linkedin_jobs:{q}"
            jobs.append((
                "linkedin_jobs",
                q,
                wrap_incremental("linkedin_jobs", key, lambda q=q: playwright_social.linkedin_jobs(q, loc, pw)),
                1,
                True,
            ))

    lp = fcfg.get("linkedin_posts") or {}
    if lp.get("enabled"):
        pw = lp.get("playwright") or {}
        for q in lp.get("queries") or []:
            key = f"linkedin_posts:{q}"
            jobs.append((
                "linkedin",
                q,
                wrap_incremental("linkedin_posts", key, lambda q=q: playwright_social.linkedin_posts(q, pw)),
                1,
                True,
            ))

    ig = fcfg.get("instagram") or {}
    if ig.get("enabled"):
        n = int(ig.get("posts_per_tag") or 15)
        for tag in ig.get("hashtags") or []:
            key = f"instagram:{tag}"
            jobs.append((
                "instagram",
                tag,
                wrap_incremental("instagram", key, lambda tag=tag: instaloader_ig.fetch_hashtag(tag, n)),
                1,
                True,
            ))

    fp = fcfg.get("facebook_pages") or {}
    if fp.get("enabled"):
        pw = fp.get("playwright") or {}
        for term in fp.get("searches") or []:
            key = f"facebook:{term}"
            jobs.append((
                "facebook",
                term,
                wrap_incremental("facebook", key, lambda term=term: playwright_social.facebook_search(term, pw)),
                1,
                True,
            ))

    fg = fcfg.get("facebook_groups") or {}
    if fg.get("enabled"):
        pw = fg.get("playwright") or {}
        for g in fg.get("groups") or []:
            key = f"facebook_group:{g}"
            jobs.append((
                "facebook_group",
                g,
                wrap_incremental("facebook_group", key, lambda g=g: playwright_social.facebook_group(g, pw)),
                1,
                True,
            ))

    return jobs
