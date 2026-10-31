"""X (Twitter) via twscrape CLI — https://github.com/vladkens/twscrape"""
import json
import os
import shutil
import subprocess


def available():
    return shutil.which("twscrape") is not None


def fetch(query, limit=40, db_path="data/twscrape.db"):
    if not available():
        raise RuntimeError("twscrape not installed (pip install twscrape; add accounts to twscrape.db)")
    cmd = [
        "twscrape", "search", query,
        "--limit", str(int(limit)),
        "--json",
    ]
    run_env = os.environ.copy()
    if db_path:
        run_env["TWSCrape_DB"] = db_path
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=run_env,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("twscrape search timed out") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        raise RuntimeError(err or f"twscrape exit {proc.returncode}")
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    out = []
    for ln in lines:
        try:
            item = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(item, list):
            items = item
        else:
            items = [item]
        for p in items:
            if not isinstance(p, dict):
                continue
            pid = p.get("id_str") or p.get("id")
            text = p.get("rawContent") or p.get("content") or p.get("text") or ""
            user = p.get("user") or {}
            handle = user.get("username") or p.get("username") or ""
            if not pid or not text:
                continue
            url = p.get("url") or f"https://x.com/{handle or 'i'}/status/{pid}"
            out.append({
                "source": "x",
                "platform": "twitter",
                "kind": "pain",
                "external_id": str(pid),
                "url": url,
                "title": "",
                "text": text[:4000],
                "author": handle,
                "author_url": f"https://x.com/{handle}" if handle else "",
                "community": "",
                "created_utc": p.get("date") or p.get("created_at"),
                "engagement": int(p.get("likeCount") or 0) + int(p.get("replyCount") or 0),
                "query": query,
            })
    return out
