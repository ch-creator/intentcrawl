"""Local config stored at data/config.json. The Treg token can also come from env TREG_TOKEN
(env wins and is never written to disk)."""
import copy
import json
import os
import threading

from .free.defaults import FREE_COLLECTORS_DEFAULT
from .scoring import DEFAULT_RULES

# Example strategy (finding companies that struggle with AI search visibility). Replace with your own.
# Short 2-4 word phrases in the buyer's words match far more posts than long exact sentences.
PAIN_QUERIES = [
    "AI Overviews traffic",
    "traffic dropped AI",
    "show up in ChatGPT",
    "ChatGPT recommend brand",
    "AI search visibility",
    "GEO agency",
    "LLM SEO",
    "Perplexity ranking",
]

DEFAULT_CONFIG = {
    "treg_token": "",
    "treg_org": "",
    "lang": "en",
    # One sentence describing who you're looking for; shown on the Leads page.
    "focus": "",
    "lookback_days": 7,
    "max_cost_per_call_usd": 0.05,
    "max_cost_per_run_usd": 1.0,
    "schedule_hours": 0,
    "min_score_to_keep": 10,
    "sources": {
        "hn": {"enabled": True, "queries": ["AI Overviews", "ChatGPT SEO", "LLM visibility", "AI search traffic"]},
        # max_pages: pages per keyword per run (each page is one paid call). Reddit pages hold ~7 posts, X ~20.
        "reddit": {"enabled": True, "max_pages": 3, "queries": PAIN_QUERIES},
        "x": {"enabled": True, "max_pages": 3, "queries": PAIN_QUERIES[:5]},
        "linkedin_posts": {"enabled": False, "max_pages": 2, "queries": PAIN_QUERIES[:4]},
        "linkedin_jobs": {"enabled": True, "max_pages": 2, "location": "United States",
                          "queries": ["Head of Growth", "SEO Manager", "Growth Marketing Manager",
                                      "Content Marketing Manager"]},
        "funding": {"enabled": False, "region": "US"},
        # Meta platforms (off by default). Facebook post search is the priciest source (~$0.03/page).
        "facebook_posts": {"enabled": False, "max_pages": 1, "queries": ["AI search visibility", "GEO agency"]},
        # One public group URL per line; posts older than the lookback window are dropped.
        "facebook_groups": {"enabled": False, "max_pages": 2, "queries": []},
        # Hashtags without "#".
        "instagram_hashtags": {"enabled": False, "max_pages": 2, "queries": ["seotips", "aisearch"]},
        "instagram_reels": {"enabled": False, "max_pages": 2, "queries": ["ChatGPT SEO", "AI search visibility"]},
    },
    # Free: any RSS/Atom feed. Optional keyword filter (match any; empty = keep everything).
    "rss": {"enabled": False, "keywords": [],
            "feeds": ["https://hnrss.org/newest?q=%22AI+Overviews%22",
                      "https://www.reddit.com/r/SaaS/new/.rss"]},
    # Any Treg catalog endpoint. Find ids with: python app.py --catalog "what you want".
    # params: GET -> query string, POST -> JSON body; "{q}" is replaced by each query, "{days}" by lookback.
    # fields (optional): map signal fields to response paths, "a.b|c" = alternatives. items_path optional.
    "custom_sources": [
        {"name": "google_news", "enabled": False, "kind": "mention",
         "endpoint": "treg.google.serp.news", "method": "POST",
         "params": {"q": "{q}", "limit": 20}, "queries": ["AI search visibility agency"]},
        {"name": "youtube", "enabled": False, "kind": "pain",
         "endpoint": "treg.youtube.search.videos", "method": "POST",
         "params": {"q": "{q}"}, "queries": ["ChatGPT not recommending my brand"],
         "url_template": "https://www.youtube.com/watch?v={id}",
         "fields": {"id": "video_id", "author": "author", "created": "published_time"}},
    ],
    "rules": DEFAULT_RULES,
    # Optional snapshot from the simplified Listeners UI (prompt, include/ignore keywords, platform toggles).
    "listener_simple": None,
    "free_collectors": copy.deepcopy(FREE_COLLECTORS_DEFAULT),
    # Active lead signal for Listeners tab and default Run (integer id in DB).
    "active_lead_signal_id": None,
}


class Config:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _merge(self.data, json.load(f))
        else:
            self.save()

    def token(self):
        return os.environ.get("TREG_TOKEN") or self.data.get("treg_token") or ""

    def token_source(self):
        if os.environ.get("TREG_TOKEN"):
            return "env"
        return "file" if self.data.get("treg_token") else "none"

    def public(self):
        """Config safe to send to the browser: token masked."""
        d = copy.deepcopy(self.data)
        t = self.token()
        d["treg_token"] = (t[:9] + "…" + t[-4:]) if len(t) > 16 else ("set" if t else "")
        d["token_source"] = self.token_source()
        return d

    def update(self, patch):
        with self.lock:
            patch = dict(patch)
            token = patch.pop("treg_token", None)
            patch.pop("token_source", None)
            # Only overwrite the token when a real new value is submitted (not the masked echo).
            if token is not None and "…" not in token and token not in ("set", "已设置"):
                self.data["treg_token"] = token.strip()
            _merge(self.data, patch)
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and k not in ("rules", "listener_simple"):
            _merge(dst[k], v)
        else:
            dst[k] = v
