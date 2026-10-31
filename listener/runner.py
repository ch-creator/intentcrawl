"""One listening run: fetch every enabled source/query, score, de-dupe, store."""
import queue
import threading
import time

from . import generic, sources
from .free import build_free_jobs
from .i18n import http_hint, t
from .lead_signals import infer_run_status, merge_configs
from .scoring import score_signal
from .treg_client import TregClient, TregError

DEFAULT_PAGES = 3

TREG_SOURCES = {
    "reddit": sources.fetch_reddit,
    "x": sources.fetch_x,
    "linkedin_posts": sources.fetch_linkedin_posts,
    "facebook_posts": sources.fetch_facebook_posts,
    "facebook_groups": sources.fetch_facebook_group,
    "instagram_hashtags": sources.fetch_instagram_hashtag,
    "instagram_reels": sources.fetch_instagram_reels,
}


PLATFORM_LABELS = {
    "hn": "Hacker News",
    "reddit": "Reddit",
    "x": "X (Twitter)",
    "linkedin_posts": "LinkedIn posts",
    "linkedin": "LinkedIn posts",
    "linkedin_jobs": "LinkedIn hiring",
    "facebook_posts": "Facebook posts",
    "facebook": "Facebook",
    "facebook_groups": "Facebook groups",
    "facebook_group": "Facebook groups",
    "instagram_hashtags": "Instagram hashtags",
    "instagram_reels": "Instagram reels",
    "instagram": "Instagram",
    "funding": "Funding",
    "rss": "RSS",
}


class Runner:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.lock = threading.Lock()
        self.running = False
        self.running_signal_id = None
        self.running_signal_name = None
        self.last_log = []
        self.progress = None
        self.progress_events = []
        self._progress_seq = 0
        self._state_lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker_started = False

    def progress_snapshot(self):
        with self._state_lock:
            return {
                "current": dict(self.progress) if self.progress else None,
                "events": [dict(e) for e in self.progress_events],
            }

    def _emit_progress(self, signal_id, signal_name, platform, phase, query=""):
        label = PLATFORM_LABELS.get(platform, platform)
        with self._state_lock:
            self._progress_seq += 1
            event = {
                "seq": self._progress_seq,
                "signal_id": signal_id,
                "signal_name": signal_name,
                "platform": platform,
                "platform_label": label,
                "phase": phase,
                "query": (query or "")[:180],
            }
            self.progress = event
            self.progress_events.append(event)
            if len(self.progress_events) > 80:
                self.progress_events = self.progress_events[-80:]

    def _clear_progress(self):
        with self._state_lock:
            self.progress = None

    def enqueue(self, lead_signal_id):
        self._queue.put(int(lead_signal_id))
        self._ensure_worker()

    def _ensure_worker(self):
        if self._worker_started:
            return
        with self.lock:
            if self._worker_started:
                return
            self._worker_started = True
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            lead_signal_id = self._queue.get()
            try:
                self.run(lead_signal_id)
            finally:
                self._queue.task_done()

    def run(self, lead_signal_id=None):
        if lead_signal_id is None:
            row = self.store.list_lead_signals()
            if not row:
                return {"ok": False, "error": "no lead signals configured"}
            lead_signal_id = row[0]["id"]
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "error": t(self.config.data.get("lang", "en"), "busy")}
        self.running = True
        self.running_signal_id = int(lead_signal_id)
        try:
            return self._run(int(lead_signal_id))
        finally:
            self.running = False
            self.running_signal_id = None
            self.running_signal_name = None
            self._clear_progress()
            self.lock.release()

    def _free_fallbacks_for_selected(self, src_cfg, cfg, fcfg, days):
        """When Treg is unavailable, run free collectors for platforms the listener has turned on."""
        mini = {"enabled": True, "incremental": fcfg.get("incremental", True)}

        def take(block_name, source_on, key="queries"):
            block = fcfg.get(block_name) or {}
            items = block.get(key) or []
            if source_on and items:
                mini[block_name] = {**block, "enabled": True}

        take("reddit_rss", (src_cfg.get("reddit") or {}).get("enabled"), "feeds")
        take("twitter", (src_cfg.get("x") or {}).get("enabled"))
        take("linkedin_posts", (src_cfg.get("linkedin_posts") or {}).get("enabled"))
        take("linkedin_jobs", (src_cfg.get("linkedin_jobs") or {}).get("enabled"))
        take("facebook_pages", (src_cfg.get("facebook_posts") or {}).get("enabled"), "searches")
        take("facebook_groups", (src_cfg.get("facebook_groups") or {}).get("enabled"), "groups")
        ig_on = (src_cfg.get("instagram_hashtags") or {}).get("enabled") or (src_cfg.get("instagram_reels") or {}).get("enabled")
        take("instagram", ig_on, "hashtags")
        take("funding_rss", (src_cfg.get("funding") or {}).get("enabled"), "feeds")
        if (cfg.get("rss") or {}).get("enabled"):
            take("rss_extra", True, "feeds")
        if len(mini) <= 2:
            return []
        return build_free_jobs(mini, days, self.store)

    def _legacy_jobs(self, cfg, days, src_cfg, client):
        jobs = []

        def single(fn):
            return lambda cursor: (fn(), None)

        def pages_for(cfg_block):
            try:
                return max(1, min(10, int(cfg_block.get("max_pages", DEFAULT_PAGES))))
            except (TypeError, ValueError):
                return DEFAULT_PAGES

        if src_cfg.get("hn", {}).get("enabled"):
            for q in src_cfg["hn"].get("queries", []):
                jobs.append(("hn", q, single(lambda q=q: sources.fetch_hn(q, days)), 1, True))
        for name, fn in TREG_SOURCES.items():
            sc = src_cfg.get(name, {}) or {}
            if sc.get("enabled"):
                for q in sc.get("queries", []):
                    jobs.append((
                        name, q,
                        lambda cursor, fn=fn, q=q, sc=sc: fn(client, q, days, cursor),
                        pages_for(sc),
                        False,
                    ))
        jc = src_cfg.get("linkedin_jobs", {}) or {}
        if jc.get("enabled"):
            for q in jc.get("queries", []):
                jobs.append((
                    "linkedin_jobs", q,
                    lambda cursor, q=q: sources.fetch_linkedin_jobs(
                        client, q, days, cursor, jc.get("location") or "United States"),
                    pages_for(jc),
                    False,
                ))
        rc = cfg.get("rss") or {}
        if rc.get("enabled"):
            for url in rc.get("feeds", []):
                jobs.append(("rss", url, single(lambda url=url: generic.fetch_rss(url, rc.get("keywords"))), 1, True))
        for cs in cfg.get("custom_sources") or []:
            if not cs.get("enabled") or not cs.get("endpoint"):
                continue
            for q in cs.get("queries") or [""]:
                jobs.append((
                    cs.get("name") or cs["endpoint"], q,
                    single(lambda cs=cs, q=q: generic.fetch_custom(client, cs, q, days)),
                    1,
                    False,
                ))
        fc = src_cfg.get("funding", {}) or {}
        if fc.get("enabled"):
            jobs.append((
                "funding", fc.get("region", "US"),
                single(lambda: sources.fetch_funding(client, days, fc.get("region", "US"))),
                1,
                False,
            ))
        return jobs

    def _run(self, lead_signal_id):
        signal = self.store.get_lead_signal(lead_signal_id)
        if not signal:
            return {"ok": False, "error": "lead signal not found"}
        if self.store.signal_is_running(lead_signal_id):
            return {"ok": False, "error": t(self.config.data.get("lang", "en"), "busy")}

        self.running_signal_name = signal.get("name") or ""
        cfg = merge_configs(self.config.data, signal["config"])
        lang = cfg.get("lang", self.config.data.get("lang", "en"))
        days = int(cfg.get("lookback_days", 7))
        budget_micro = int(float(cfg.get("max_cost_per_run_usd", 1.0)) * 1e6)
        min_keep = int(cfg.get("min_score_to_keep", 0))
        rules = cfg.get("rules")
        src_cfg = cfg.get("sources", {})
        fcfg = cfg.get("free_collectors") or {}
        started = int(time.time())
        run_id, _run_uuid = self.store.begin_run(lead_signal_id, started)
        log, fetched, new = [], 0, 0
        token = self.config.token()
        client = TregClient(token, max_cost_usd=float(cfg.get("max_cost_per_call_usd", 0.05)),
                            org=cfg.get("treg_org") or None) if token else None

        def keep(batch):
            nonlocal fetched, new
            fetched += len(batch)
            kept = []
            for s in batch:
                s["score"], s["persona"], s["matched"] = score_signal(s, rules)
                if s["score"] >= min_keep:
                    kept.append(s)
            n = self.store.upsert_signals(kept, lead_signal_id=lead_signal_id)
            new += n
            return len(batch), len(kept), n

        disabled = client is None

        def over_budget():
            return client is not None and client.spent_micro >= budget_micro

        replace_treg = fcfg.get("enabled") and fcfg.get("replace_treg", True)
        if replace_treg:
            jobs = build_free_jobs(fcfg, days, self.store)
            if fcfg.get("enabled"):
                log.append(f"[info] free collectors · signal {signal['name']}")
        else:
            jobs = self._legacy_jobs(cfg, days, src_cfg, client)
            if fcfg.get("enabled"):
                jobs = jobs + build_free_jobs(fcfg, days, self.store)
            elif disabled:
                extra = self._free_fallbacks_for_selected(src_cfg, cfg, fcfg, days)
                if extra:
                    log.append(t(lang, "free_platform_fallback"))
                    jobs = jobs + extra

        seen_platform = None
        for job in jobs:
            name, q, fetch, max_pages, is_free = job
            will_skip = (not is_free and disabled) or (not is_free and over_budget())
            marker = (name, bool(is_free), bool(will_skip))
            if marker != seen_platform:
                seen_platform = marker
                self._emit_progress(
                    lead_signal_id,
                    signal.get("name") or "",
                    name,
                    "skipped" if will_skip else "running",
                    q,
                )
            if not is_free and disabled:
                log.append(t(lang, "skip_no_token" if client is None else "skip_disabled", name=name, q=q))
                continue
            if not is_free and over_budget():
                log.append(t(lang, "skip_budget", name=name, q=q, budget=budget_micro / 1e6))
                continue
            got = kept = n = pages = 0
            cursor = None
            try:
                while pages < max_pages:
                    if pages and not is_free and over_budget():
                        break
                    rows, cursor = fetch(cursor)
                    pages += 1
                    g, k, nn = keep(rows)
                    got, kept, n = got + g, kept + k, n + nn
                    if not cursor or not rows:
                        break
                log.append(t(lang, "ok", name=name, q=q, got=got, kept=kept, new=n, pages=pages))
            except TregError as e:
                if pages:
                    log.append(t(lang, "ok", name=name, q=q, got=got, kept=kept, new=n, pages=pages))
                err = f"HTTP {e.status} {http_hint(lang, e.status)} {e.detail}".strip()
                log.append(t(lang, "error", name=name, q=q, err=err))
                if e.status in (401, 402):
                    log.append(t(lang, "stop"))
                    disabled = True
            except Exception as e:
                log.append(t(lang, "error", name=name, q=q, err=f"{type(e).__name__}: {e}"))

        cost = client.spent_micro if client else 0
        calls = client.calls if client else 0
        log.append(t(lang, "done", fetched=fetched, new=new, calls=calls, cost=cost / 1e6))
        finished = int(time.time())
        status = infer_run_status(log, new, fetched)
        err_sum = "" if status == "success" else status
        self.store.finish_run(run_id, finished, new, fetched, cost, calls, log, status=status, error_summary=err_sum)
        sched = float(signal.get("schedule_hours") or cfg.get("schedule_hours") or 0)
        next_at = int(finished + sched * 3600) if sched > 0 else None
        self.store.update_lead_signal(lead_signal_id, next_run_at=next_at)
        self.last_log = log
        return {"ok": True, "new": new, "fetched": fetched, "cost_usd": cost / 1e6, "calls": calls,
                "log": log, "lead_signal_id": lead_signal_id, "status": status}
