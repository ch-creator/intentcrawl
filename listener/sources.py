"""Signal sources. Each fetcher returns a list of normalized signal dicts:

  source, kind, external_id, url, title, text, author, author_url,
  community, created_utc, engagement, query

kind: "pain" (someone talking about a problem) | "hiring" | "funding"
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .treg_client import TregClient

HN_API = "https://hn.algolia.com/api/v1/search_by_date"

# Catalog endpoint ids (verified against https://treg.to/catalog/endpoints/<id>).
ENDPOINTS = {
    "reddit": "scrapecreators.reddit.search.posts",
    "x": "treg.x.search.posts",
    "linkedin_posts": "anyapi.linkedin.search.posts",
    "linkedin_jobs": "anyapi.linkedin.search.jobs",
    "funding": "getleadsio.companies.funding.feed",
    "facebook_posts": "justoneapi.x.facebook-search-post-v1",
    "facebook_groups": "anyapi.facebook.group.posts",
    "instagram_hashtags": "anyapi.instagram.hashtag.posts",
    "instagram_reels": "anyapi.instagram.search.reels",
}

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s, limit=4000):
    if not s:
        return ""
    s = html.unescape(TAG_RE.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def pick(d, *paths, default=None):
    """Return the first non-empty value among dotted paths, e.g. pick(d, "user.screen_name", "username")."""
    for p in paths:
        cur = d
        for part in p.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, "", [], {}):
            return cur
    return default


def to_epoch(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 10_000_000_000 else int(v)
    s = str(v).strip()
    if s.isdigit():
        return to_epoch(int(s))
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def unwrap(resp):
    """Treg responses come either raw from the provider or wrapped as {output, raw, _treg}."""
    if isinstance(resp, dict) and "output" in resp:
        out = resp["output"]
        if isinstance(out, dict) and isinstance(out.get("data"), (dict, list)):
            return out["data"]
        return out
    return resp


# ---------- parsers (pure functions, unit-tested offline) ----------

def parse_reddit(resp, query):
    data = unwrap(resp)
    posts = data.get("posts") if isinstance(data, dict) else data
    out = []
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or p.get("name")
        if not pid:
            continue
        url = p.get("url") or ""
        if p.get("permalink"):
            url = "https://www.reddit.com" + p["permalink"]
        author = p.get("author") or ""
        out.append({
            "source": "reddit", "kind": "pain", "external_id": str(pid), "url": url,
            "title": clean_text(p.get("title"), 300), "text": clean_text(p.get("selftext")),
            "author": author, "author_url": f"https://www.reddit.com/user/{author}" if author else "",
            "community": "r/" + p["subreddit"] if p.get("subreddit") else "",
            "created_utc": to_epoch(p.get("created_utc") or p.get("created")),
            "engagement": int(p.get("score") or 0) + int(p.get("num_comments") or 0),
            "query": query,
        })
    return out


def parse_x(resp, query):
    data = unwrap(resp)
    posts = (data.get("posts") or data.get("items")) if isinstance(data, dict) else data
    out = []
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        pid = pick(p, "id_str", "rest_id", "tweet_id", "id", "legacy.id_str")
        text = pick(p, "full_text", "text", "content", "legacy.full_text", "note_tweet.text")
        if not pid or not text:
            continue
        handle = pick(p, "authorUsername", "author.username", "author.screen_name", "user.screen_name", "user.username",
                      "username", "screen_name", "author_handle", "core.user_results.result.legacy.screen_name",
                      default="")
        if isinstance(handle, dict):
            handle = ""
        url = pick(p, "url", "tweet_url", "link", default="")
        if not url or "/status/" not in str(url) or ("/i/web/" in str(url) and handle):
            url = f"https://x.com/{handle or 'i'}/status/{pid}"
        likes = pick(p, "likeCount", "favorite_count", "like_count", "likes", "public_metrics.like_count", "legacy.favorite_count", default=0)
        replies = pick(p, "replyCount", "reply_count", "replies", "public_metrics.reply_count", "legacy.reply_count", default=0)
        out.append({
            "source": "x", "kind": "pain", "external_id": str(pid), "url": url,
            "title": "", "text": clean_text(text),
            "author": handle, "author_url": f"https://x.com/{handle}" if handle else "",
            "community": "",
            "created_utc": to_epoch(pick(p, "createdUtc", "created_at", "createdAt", "legacy.created_at", "date")),
            "engagement": _int(likes) + _int(replies), "query": query,
        })
    return out


def parse_linkedin_posts(resp, query):
    data = unwrap(resp)
    posts = data.get("posts") if isinstance(data, dict) else data
    out = []
    for p in posts or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        out.append({
            "source": "linkedin", "kind": "pain", "external_id": str(p["id"]), "url": p.get("url", ""),
            "title": "", "text": clean_text(p.get("text")),
            "author": p.get("authorName", ""), "author_url": p.get("authorUrl", ""), "community": "",
            "created_utc": to_epoch(p.get("createdUtc")),
            "engagement": _int(p.get("reactionCount")) + _int(p.get("commentCount")), "query": query,
        })
    return out


def parse_linkedin_jobs(resp, query):
    data = unwrap(resp)
    items = data.get("items") if isinstance(data, dict) else data
    out = []
    for j in items or []:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        company = j.get("company", "")
        out.append({
            "source": "linkedin_jobs", "kind": "hiring", "external_id": str(j["id"]), "url": j.get("url", ""),
            "title": clean_text(j.get("title"), 300),
            "text": clean_text(f"{company} is hiring: {j.get('title', '')} ({j.get('location', '')})"),
            "author": company, "author_url": j.get("companyUrl", ""), "community": j.get("location", ""),
            "created_utc": to_epoch(j.get("createdUtc")), "engagement": 0, "query": query,
        })
    return out


def parse_hn(resp, query):
    out = []
    if isinstance(resp, dict):
        hits = resp.get("hits") or []
    elif isinstance(resp, list):
        hits = resp
    else:
        hits = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        oid = h.get("objectID")
        if not oid:
            continue
        is_comment = bool(h.get("comment_text"))
        # Comments carry their thread's title; keep it as context so rows don't look like duplicates.
        title = "" if is_comment else (h.get("title") or "")
        text = h.get("comment_text") or h.get("story_text") or ""
        if not (title or text):
            continue
        thread = clean_text(h.get("story_title"), 120)
        out.append({
            "source": "hn", "kind": "pain", "external_id": str(oid),
            "url": f"https://news.ycombinator.com/item?id={oid}",
            "title": clean_text(title, 300), "text": clean_text(text),
            "author": h.get("author", ""),
            "author_url": f"https://news.ycombinator.com/user?id={h.get('author', '')}" if h.get("author") else "",
            "community": f"HN comment · {thread}" if is_comment and thread else "Hacker News", "created_utc": to_epoch(h.get("created_at_i")),
            "engagement": _int(h.get("points")) + _int(h.get("num_comments")), "query": query,
        })
    return out


def parse_funding(resp, query=""):
    data = resp.get("data") if isinstance(resp, dict) else resp
    out = []
    for f in data or []:
        if not isinstance(f, dict) or not f.get("company_name"):
            continue
        key = f"{f['company_name']}|{f.get('round', '')}|{f.get('announced_date', '')}"
        amount = f.get("amount")
        amount_s = f"{f.get('currency', 'USD')} {amount:,.0f}" if isinstance(amount, (int, float)) else ""
        out.append({
            "source": "funding", "kind": "funding", "external_id": key, "url": f.get("url") or f.get("source_url") or "",
            "title": clean_text(f"{f['company_name']} raised {f.get('round', '')} {amount_s}", 300),
            "text": clean_text(json.dumps(f, ensure_ascii=False)),
            "author": f["company_name"], "author_url": "", "community": f.get("region", ""),
            "created_utc": to_epoch(f.get("announced_date")), "engagement": 0, "query": query,
        })
    return out


def parse_facebook_posts(resp, query):
    data = resp.get("data") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else unwrap(resp)
    out = []
    for p in (data or {}).get("results") or []:
        if not isinstance(p, dict) or not p.get("post_id"):
            continue
        text = clean_text(p.get("message") or p.get("message_rich"))
        if not text:
            continue
        author = p.get("author") or {}
        out.append({
            "source": "facebook", "kind": "pain", "external_id": str(p["post_id"]), "url": p.get("url", ""),
            "title": "", "text": text, "author": author.get("name", ""), "author_url": author.get("url", ""),
            "community": clean_text((p.get("associated_group") or {}).get("name") if isinstance(p.get("associated_group"), dict) else "", 120),
            "created_utc": to_epoch(p.get("timestamp")),
            "engagement": _int(p.get("reactions_count")) + _int(p.get("comments_count")), "query": query,
        })
    return out


def parse_facebook_group(resp, group_url):
    data = unwrap(resp)
    out = []
    for p in (data or {}).get("posts") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        text = clean_text(p.get("text"))
        if not text:  # image-only posts carry no signal for keyword scoring
            continue
        out.append({
            "source": "facebook_group", "kind": "pain", "external_id": str(p["id"]), "url": p.get("url", ""),
            "title": "", "text": text, "author": p.get("authorName", ""),
            "author_url": f"https://www.facebook.com/{p['authorId']}" if p.get("authorId") else "",
            "community": "FB group · " + group_url.rstrip("/").rsplit("/", 1)[-1],
            "created_utc": to_epoch(p.get("publishTime")),
            "engagement": _int(p.get("reactionCount")) + _int(p.get("commentCount")), "query": group_url,
        })
    return out


def _parse_instagram(items, source, query, community):
    out = []
    for p in items or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or p.get("shortcode")
        text = clean_text(p.get("caption"))
        if not pid or not text:
            continue
        user = p.get("username", "")
        out.append({
            "source": source, "kind": "pain", "external_id": str(pid), "url": p.get("url", ""),
            "title": "", "text": text, "author": user,
            "author_url": f"https://www.instagram.com/{user}/" if user else "", "community": community,
            "created_utc": to_epoch(p.get("createdUtc")),
            "engagement": _int(p.get("likes")) // 10 + _int(p.get("comments")), "query": query,
        })
    return out


def parse_instagram_hashtag(resp, hashtag):
    data = unwrap(resp)
    return _parse_instagram((data or {}).get("posts"), "instagram", hashtag, "#" + hashtag)


def parse_instagram_reels(resp, query):
    data = unwrap(resp)
    return _parse_instagram((data or {}).get("reels"), "instagram_reels", query, "Reels")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------- fetchers ----------

def fetch_hn(query, days=7, timeout=30):
    since = int(time.time()) - days * 86400
    params = urllib.parse.urlencode({
        "query": query, "tags": "(story,comment)",
        "numericFilters": f"created_at_i>{since}", "hitsPerPage": 50,
    })
    req = urllib.request.Request(f"{HN_API}?{params}", headers={"User-Agent": "lead-signal-listener/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_hn(json.loads(r.read().decode("utf-8")), query)


def _bucket(days):
    # Idempotency seed bucket: identical query within the same hour is replayed for free.
    return f"{days}:{int(time.time() // 3600)}"


def next_cursor(resp):
    """Pagination token from a provider response, if any."""
    for src in (resp, unwrap(resp)):
        if isinstance(src, dict):
            for k in ("after", "next_cursor", "nextCursor", "cursor"):
                v = src.get(k)
                if isinstance(v, str) and v:
                    return v
    return None


# Paged fetchers: (client, query, days, cursor) -> (signals, next_cursor_or_None).
# The cursor is opaque to the runner; it just passes back whatever it got.

def fetch_reddit(client: TregClient, query, days=7, cursor=None):
    timeframe = "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year"
    params = {"query": query, "sort": "relevance", "timeframe": timeframe, "filter": "posts", "trim": "true"}
    if cursor:
        params["after"] = cursor
    resp, _ = client.call(ENDPOINTS["reddit"], query=params,
                          idempotency_seed=f"reddit|{query}|{cursor or ''}|{_bucket(days)}")
    return parse_reddit(resp, query), next_cursor(resp)


def fetch_x(client: TregClient, query, days=7, cursor=None):
    # Page 1 goes through treg's router; a cursor only works with the provider that issued it,
    # so later pages call that provider's endpoint directly. cursor = (endpoint_id, token).
    if cursor:
        endpoint, token = cursor
        resp, _ = client.call(endpoint, body={"query": query, "cursor": token},
                              idempotency_seed=f"x|{query}|{token}|{_bucket(days)}")
    else:
        endpoint = None
        resp, _ = client.call(ENDPOINTS["x"], body={"q": query}, idempotency_seed=f"x|{query}|{_bucket(days)}")
        served = (resp.get("_treg") or {}).get("served_by") if isinstance(resp, dict) else None
        # Only providers known to accept {"query", "cursor"} are followed.
        endpoint = served if served in X_PAGEABLE else None
    token = next_cursor(resp)
    return parse_x(resp, query), ((endpoint, token) if endpoint and token else None)


X_PAGEABLE = {"anyapi.x.search.posts"}


def fetch_linkedin_posts(client: TregClient, query, days=7, cursor=None):
    date_posted = "last-day" if days <= 1 else "last-week" if days <= 7 else "last-month" if days <= 31 else "last-year"
    body = {"query": query, "datePosted": date_posted}
    if cursor:
        body["cursor"] = cursor
    resp, _ = client.call(ENDPOINTS["linkedin_posts"], body=body,
                          idempotency_seed=f"lip|{query}|{cursor or ''}|{_bucket(days)}")
    return parse_linkedin_posts(resp, query), next_cursor(resp)


def fetch_linkedin_jobs(client: TregClient, query, days=7, cursor=None, location="United States"):
    posted = "24h" if days <= 1 else "week" if days <= 7 else "month"
    body = {"query": query, "location": location, "postedLimit": posted}
    if cursor:
        body["cursor"] = cursor
    resp, _ = client.call(ENDPOINTS["linkedin_jobs"], body=body,
                          idempotency_seed=f"lij|{query}|{location}|{cursor or ''}|{_bucket(days)}")
    return parse_linkedin_jobs(resp, query), next_cursor(resp)


def _date_posted(days):
    return "last-day" if days <= 1 else "last-week" if days <= 7 else "last-month" if days <= 31 else "last-year"


def fetch_facebook_posts(client: TregClient, query, days=7, cursor=None):
    start = datetime.fromtimestamp(time.time() - days * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    params = {"keyword": query, "startDate": start}
    if cursor:
        params["cursor"] = cursor
    resp, _ = client.call(ENDPOINTS["facebook_posts"], query=params,
                          idempotency_seed=f"fbp|{query}|{cursor or ''}|{_bucket(days)}")
    data = resp.get("data") if isinstance(resp, dict) else None
    nxt = data.get("cursor") if isinstance(data, dict) else None
    return parse_facebook_posts(resp, query), (nxt if isinstance(nxt, str) and nxt else None)


def fetch_facebook_group(client: TregClient, group_url, days=7, cursor=None):
    body = {"url": group_url, "sort": "CHRONOLOGICAL"}
    if cursor:
        body["cursor"] = cursor
    resp, _ = client.call(ENDPOINTS["facebook_groups"], body=body,
                          idempotency_seed=f"fbg|{group_url}|{cursor or ''}|{_bucket(days)}")
    rows = parse_facebook_group(resp, group_url)
    since = time.time() - days * 86400
    fresh = [r for r in rows if not r["created_utc"] or r["created_utc"] >= since]
    # Chronological feed: once a page is entirely older than the lookback, stop paging.
    return fresh, (next_cursor(resp) if fresh else None)


def fetch_instagram_hashtag(client: TregClient, hashtag, days=7, cursor=None):
    tag = hashtag.strip().lstrip("#").replace(" ", "")
    body = {"hashtag": tag, "datePosted": _date_posted(days)}
    if cursor:
        body["cursor"] = cursor
    resp, _ = client.call(ENDPOINTS["instagram_hashtags"], body=body,
                          idempotency_seed=f"igh|{tag}|{cursor or ''}|{_bucket(days)}")
    return parse_instagram_hashtag(resp, tag), next_cursor(resp)


def fetch_instagram_reels(client: TregClient, query, days=7, cursor=None):
    # This endpoint rejects date filters (despite the catalog listing one), so filter locally.
    body = {"query": query}
    if cursor:
        body["cursor"] = cursor
    resp, _ = client.call(ENDPOINTS["instagram_reels"], body=body,
                          idempotency_seed=f"igr|{query}|{cursor or ''}|{_bucket(days)}")
    since = time.time() - days * 86400
    rows = [r for r in parse_instagram_reels(resp, query) if not r["created_utc"] or r["created_utc"] >= since]
    return rows, next_cursor(resp)


def fetch_funding(client: TregClient, days=7, region="US", limit=50):
    since = datetime.fromtimestamp(time.time() - days * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    resp, _ = client.call(ENDPOINTS["funding"], query={"since": since, "region": region, "limit": limit},
                          idempotency_seed=f"fund|{region}|{since}|{limit}")
    return parse_funding(resp, f"funding:{region}")
