"""Map collector rows to lead-signal-listener signal dicts (store + score_signal)."""


def to_signal(
    *,
    source,
    platform,
    external_id,
    url="",
    title="",
    text="",
    author="",
    profile_url="",
    community="",
    created_at=None,
    engagement=0,
    query="",
    kind="pain",
):
    created_utc = created_at
    if isinstance(created_utc, str) and created_utc.isdigit():
        created_utc = int(created_utc)
    return {
        "source": source,
        "platform": platform,
        "kind": kind,
        "external_id": str(external_id),
        "url": url or "",
        "title": title or "",
        "text": text or "",
        "author": author or "",
        "author_url": profile_url or "",
        "community": community or "",
        "created_utc": created_utc,
        "engagement": int(engagement or 0),
        "query": query or "",
    }


def filter_since(signals, since_ts):
    if not since_ts:
        return signals
    out = []
    for s in signals:
        ts = s.get("created_utc") or 0
        if ts >= since_ts:
            out.append(s)
    return out
