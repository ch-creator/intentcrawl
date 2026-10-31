"""Generic listening methods:

- custom: any Treg catalog endpoint, described by a params template + field mapping
- rss:    any RSS/Atom feed (free) — Google Alerts, subreddit .rss, hnrss.org, blogs, job boards
- import: signals pushed from a file / another agent (POST /api/import or --import)
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .sources import clean_text, pick, to_epoch, unwrap
from .treg_client import BASE_URL, USER_AGENT, TregClient

KINDS = ("pain", "hiring", "funding", "mention", "other")


# ---------- custom Treg endpoints ----------

def _fill(template, q, days):
    """Substitute {q} and {days} in every string of a params template (recursively)."""
    if isinstance(template, str):
        if template == "{days}":
            return days
        return template.replace("{q}", q).replace("{days}", str(days))
    if isinstance(template, dict):
        return {k: _fill(v, q, days) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, q, days) for v in template]
    return template


def _get_path(data, path):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def find_items(data, path=None):
    """Return the list of result rows: explicit items_path, else the first list of objects found (breadth-first)."""
    if path:
        v = _get_path(data, path)
        return v if isinstance(v, list) else []
    queue = [unwrap(data)]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, list) and cur and all(isinstance(x, dict) for x in cur[:3]):
            return cur
        if isinstance(cur, dict):
            queue.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return []


def _field(item, spec, default=None):
    """spec: "a.b" or alternatives "a.b|c|d"."""
    if not spec:
        return default
    return pick(item, *[s.strip() for s in spec.split("|")], default=default)


DEFAULT_FIELDS = {
    "id": "id|_id|uuid|video_id|post_id|url|link",
    "title": "title|name|headline",
    "text": "text|description|content|body|snippet|summary|selftext",
    "url": "url|link|permalink|href",
    "author": "author|authorName|authorUsername|username|user.name|channel|company",
    "author_url": "authorUrl|author_url|profileUrl|channelUrl|companyUrl",
    "community": "subreddit|community|source|channel|category|location",
    "created": "createdUtc|created_utc|created_at|createdAt|published|publishedAt|date|timestamp",
    "engagement": "score|likeCount|likes|reactionCount|views|viewCount|number_of_views|points",
}

REL_RE = re.compile(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
REL_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def _created(v):
    """Absolute timestamps/ISO dates, or relative strings like "2 weeks ago"."""
    if isinstance(v, str):
        m = REL_RE.search(v)
        if m:
            return int(time.time()) - int(m.group(1)) * REL_SECONDS[m.group(2).lower()]
    return to_epoch(v)


def _text(v):
    if isinstance(v, dict):
        return v.get("name") or v.get("title") or v.get("username") or ""
    return "" if v is None else str(v)


def map_items(items, src, query):
    fields = {**DEFAULT_FIELDS, **(src.get("fields") or {})}
    name = src.get("name") or "custom"
    kind = src.get("kind") if src.get("kind") in KINDS else "pain"
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ext = _field(it, fields["id"])
        title = clean_text(_field(it, fields["title"], ""), 300)
        text = clean_text(_field(it, fields["text"], ""))
        if ext in (None, "") or not (title or text):
            continue
        author = _text(_field(it, fields["author"], ""))
        url = _text(_field(it, fields["url"], ""))
        if not url and src.get("url_template"):
            # e.g. "https://www.youtube.com/watch?v={id}" for APIs that only return an id
            url = src["url_template"].replace("{id}", urllib.parse.quote(str(ext)))
        try:
            eng = int(_field(it, fields["engagement"], 0) or 0)
        except (TypeError, ValueError):
            eng = 0
        out.append({
            "source": name, "kind": kind, "external_id": str(ext)[:300],
            "url": url, "title": title, "text": text,
            "author": author, "author_url": _text(_field(it, fields["author_url"], "")),
            "community": _text(_field(it, fields["community"], "")),
            "created_utc": _created(_field(it, fields["created"])), "engagement": eng, "query": query,
        })
    return out


def fetch_custom(client: TregClient, src, query, days=7):
    params = _fill(src.get("params") or {}, query, days)
    method = (src.get("method") or "GET").upper()
    seed = f"custom|{src.get('endpoint')}|{json.dumps(params, sort_keys=True)}|{int(time.time() // 3600)}"
    if method == "GET":
        resp, _ = client.call(src["endpoint"], query=params, method="GET", idempotency_seed=seed)
    else:
        resp, _ = client.call(src["endpoint"], body=params, method=method, idempotency_seed=seed)
    return map_items(find_items(resp, src.get("items_path")), src, query)


# ---------- RSS / Atom ----------

ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml_bytes, feed_url, keywords=None):
    root = ET.fromstring(xml_bytes)
    kws = [k.lower() for k in (keywords or []) if k.strip()]
    title_el = root.find("channel/title") if root.find("channel") is not None else root.find(f"{ATOM}title")
    feed_title = clean_text(title_el.text if title_el is not None else urllib.parse.urlparse(feed_url).netloc, 120)
    entries = []
    for it in root.iter("item"):
        entries.append({
            "id": (it.findtext("guid") or it.findtext("link") or "").strip(),
            "title": it.findtext("title") or "", "text": it.findtext("description") or "",
            "url": (it.findtext("link") or "").strip(), "author": it.findtext("author") or it.findtext("{http://purl.org/dc/elements/1.1/}creator") or "",
            "created": it.findtext("pubDate") or "",
        })
    for e in root.iter(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link")
        entries.append({
            "id": (e.findtext(f"{ATOM}id") or "").strip(),
            "title": e.findtext(f"{ATOM}title") or "",
            "text": e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary") or "",
            "url": link.get("href", "") if link is not None else "",
            "author": e.findtext(f"{ATOM}author/{ATOM}name") or "",
            "created": e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated") or "",
        })
    out = []
    for e in entries:
        title, text = clean_text(e["title"], 300), clean_text(e["text"])
        hay = f"{title} {text}".lower()
        if kws and not any(k in hay for k in kws):
            continue
        ext = e["id"] or e["url"]
        if not ext or not (title or text):
            continue
        out.append({
            "source": "rss", "kind": "pain", "external_id": ext[:300], "url": e["url"], "title": title, "text": text,
            "author": clean_text(e["author"], 120), "author_url": "", "community": feed_title,
            "created_utc": _rss_time(e["created"]), "engagement": 0, "query": feed_url,
        })
    return out


def _rss_time(s):
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError, IndexError):
        return to_epoch(s)


_reddit_rss_lock = threading.Lock()
_last_reddit_rss_at = 0.0


def fetch_rss(feed_url, keywords=None, timeout=30, min_interval=0, max_retries=3):
    """Fetch RSS/Atom. For reddit.com feeds use min_interval (seconds) between requests and 429 retries."""
    is_reddit = "reddit.com" in (feed_url or "")
    ua = f"{USER_AGENT} (+rss)"
    if is_reddit:
        ua = (
            "Mozilla/5.0 (compatible; LeadSignalListener/0.1; +https://github.com/yangyixxxx/lead-signal-listener) "
            "RSS reader"
        )
    last_err = None
    for attempt in range(max(1, max_retries)):
        if is_reddit and min_interval:
            global _last_reddit_rss_at
            with _reddit_rss_lock:
                wait = min_interval - (time.time() - _last_reddit_rss_at)
                if wait > 0:
                    time.sleep(wait)
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": ua, "Accept": "application/rss+xml, application/atom+xml, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if is_reddit:
                with _reddit_rss_lock:
                    _last_reddit_rss_at = time.time()
            return parse_feed(body, feed_url, keywords)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    pause = max(2, int(retry_after or 0))
                except (TypeError, ValueError):
                    pause = 2 ** (attempt + 2)
                time.sleep(min(pause, 120))
                continue
            raise
    if last_err:
        raise last_err
    return []


# ---------- import ----------

def normalize_imported(rows, default_source="import"):
    """Accept a list of loosely shaped dicts (from an agent, a CSV->JSON export, etc.)."""
    if isinstance(rows, dict):
        rows = rows.get("signals") or rows.get("items") or [rows]
    src = {"name": default_source, "kind": "pain"}
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        mapped = map_items([r], {**src, "name": r.get("source") or default_source,
                                 "kind": r.get("kind") or "pain"}, r.get("query") or "import")
        out.extend(mapped)
    return out


# ---------- catalog search (no token needed) ----------

def search_catalog(query, limit=10, timeout=30):
    url = f"{BASE_URL}/catalog/search?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    rows = []
    for e in data.get("results", [])[:limit]:
        inp = e.get("input") or {}
        params = list((inp.get("queryParams") or inp.get("body") or {}).keys())
        rows.append({"id": e["id"], "method": e.get("method"), "usd": (e.get("cost") or {}).get("usd"),
                     "summary": e.get("summary", ""), "params": params,
                     "body": "body" in inp, "platform": e.get("platform_label", "")})
    return rows
