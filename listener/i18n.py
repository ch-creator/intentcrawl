"""Server-side messages (run log, token check, errors). UI strings live in static/index.html."""

MESSAGES = {
    "busy": "A run is already in progress",
    "skip_no_token": "[skip] {name} · {q}: Treg token not set",
    "reddit_rss_fallback": "[info] Reddit enabled without Treg token — using subreddit RSS feeds",
    "free_platform_fallback": "[info] No Treg token — selected platforms are using free feeds and collectors",
    "skip_disabled": "[skip] {name} · {q}: Treg disabled for this run",
    "skip_budget": "[skip] {name} · {q}: run budget ${budget:.2f} reached",
    "ok": "[ok] {name} · {q}: {pages} page(s), fetched {got}, kept {kept}, new {new}",
    "error": "[error] {name} · {q}: {err}",
    "stop": "[stop] Invalid token or insufficient balance; remaining Treg calls cancelled",
    "done": "[done] fetched {fetched}, new leads {new}, Treg calls {calls}, cost ${cost:.4f}",
    "token_ok": "Token is valid",
    "token_bad": "Token check failed (HTTP {code})",
    "token_net": "Cannot reach treg.to: {reason}",
    "token_missing": "Token not set",
    "http_401": "Invalid or missing token",
    "http_402": "Insufficient balance or per-call cost cap exceeded",
    "http_429": "Upstream rate limit, try again later",
    "http_503": "Upstream unavailable, try again later",
    "http_other": "Request failed",
}


def t(lang, key, **kw):
    del lang  # English only
    return MESSAGES.get(key, key).format(**kw)


def http_hint(lang, status):
    del lang
    key = f"http_{status}" if f"http_{status}" in MESSAGES else "http_other"
    return t("en", key)
