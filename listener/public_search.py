"""Public web search for platforms that would otherwise need a Treg token.

Each selected platform becomes one Google News RSS query scoped to that site.
Results are tagged with the platform name so the run shows them as that source.
"""
import urllib.parse

from . import generic

# job name -> Google News query. {kw} is an OR of the listener's phrases.
PLATFORM_SCOPES = (
    ("reddit", "site:reddit.com ({kw})"),
    ("x", "(site:x.com OR site:twitter.com) ({kw})"),
    ("linkedin_posts", "site:linkedin.com ({kw})"),
    ("linkedin_jobs", "site:linkedin.com/jobs ({kw})"),
    ("facebook_posts", "site:facebook.com ({kw})"),
    ("facebook_groups", "site:facebook.com/groups ({kw})"),
    ("instagram_hashtags", "site:instagram.com ({kw})"),
    ("instagram_reels", "site:instagram.com/reel ({kw})"),
    ("funding", "({kw}) (raised OR funding OR \"series a\" OR \"seed round\")"),
)

# Runner job names that already cover a platform, so we don't search it twice.
COVERED_BY = {
    "hn": ("hn",),
    "reddit": ("reddit",),
    "x": ("x",),
    "linkedin": ("linkedin_posts",),
    "linkedin_posts": ("linkedin_posts",),
    "linkedin_jobs": ("linkedin_jobs",),
    "facebook": ("facebook_posts",),
    "facebook_posts": ("facebook_posts",),
    "facebook_group": ("facebook_groups",),
    "facebook_groups": ("facebook_groups",),
    "instagram": ("instagram_hashtags", "instagram_reels"),
    "instagram_hashtags": ("instagram_hashtags",),
    "instagram_reels": ("instagram_reels",),
    "funding": ("funding",),
}


def _phrases(src_cfg, cfg):
    seen = []
    for key, _scope in PLATFORM_SCOPES:
        for q in ((src_cfg.get(key) or {}).get("queries") or []):
            text = " ".join(str(q).split())
            if text and text not in seen:
                seen.append(text)
    simple = cfg.get("listener_simple") if isinstance(cfg.get("listener_simple"), dict) else {}
    for line in str((simple or {}).get("keywords_include") or "").splitlines():
        text = " ".join(line.split())
        if text and text not in seen:
            seen.append(text)
    for q in ((cfg.get("rss") or {}).get("keywords") or []):
        text = " ".join(str(q).split())
        if text and text not in seen:
            seen.append(text)
    return seen[:6]


def _or_query(phrases):
    parts = []
    for phrase in phrases:
        clean = phrase.replace('"', "")
        parts.append(f'"{clean}"' if " " in clean else clean)
    return " OR ".join(parts)


def google_news_url(query):
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def fetch_platform(url, platform):
    rows = generic.fetch_rss(url, [])
    for row in rows:
        row["source"] = platform
        row["platform"] = platform
    return rows


def public_search_jobs(src_cfg, cfg, covered_names):
    """Free jobs for enabled platforms that are not already fetched another way."""
    covered = set()
    for name in covered_names or []:
        covered.update(COVERED_BY.get(name, ()))
    phrases = _phrases(src_cfg, cfg)
    if not phrases:
        return []
    kw = _or_query(phrases)
    jobs = []

    def single(fn):
        return lambda cursor: (fn(), None)

    for name, scope in PLATFORM_SCOPES:
        block = src_cfg.get(name) or {}
        if not block.get("enabled") or name in covered:
            continue
        url = google_news_url(scope.format(kw=kw))
        jobs.append((
            name,
            url,
            single(lambda url=url, name=name: fetch_platform(url, name)),
            1,
            True,
        ))
    return jobs
