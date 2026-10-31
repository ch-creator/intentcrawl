"""Local web server (127.0.0.1 only) exposing a small JSON API + single-page UI."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .i18n import t
from . import generic
from .lead_signals import extract_signal_config, merge_configs, validate_signal_config
from .scoring import score_signal
from .treg_client import BASE_URL, auth_headers

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml"}
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
LS_API = re.compile(r"^/api/lead-signals(?:/([^/]+))?(?:/(run|duplicate|leads|runs))?$")


def check_token(token, org=None, lang="en"):
    req = urllib.request.Request(f"{BASE_URL}/calls?limit=1", headers=auth_headers(token, org))
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, t(lang, "token_ok")
    except urllib.error.HTTPError as e:
        return False, t(lang, "token_bad", code=e.code)
    except urllib.error.URLError as e:
        return False, t(lang, "token_net", reason=e.reason)


def _resolve_signal_id(store, ref):
    if ref is None or ref == "":
        return None
    row = store.get_lead_signal(ref)
    return row["id"] if row else None


def _today_start_local(offset_minutes=0):
    now = int(time.time())
    local = now + int(offset_minutes) * 60
    midnight = local - (local % 86400)
    return midnight - int(offset_minutes) * 60


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LeadSignalListener/0.2"

        def log_message(self, fmt, *args):
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ALLOWED_HOSTS

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if not isinstance(body, (bytes, bytearray)):
                body = (json.dumps(body, ensure_ascii=False) if ctype.startswith("application/json") else body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            extra = extra or {}
            if "Cache-Control" not in extra:
                self.send_header("Cache-Control", "no-store")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("Content-Type must be application/json")
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

        def _signal_ref_from_qs(self, qs):
            g = lambda k: (qs.get(k) or [None])[0]
            ref = g("lead_signal_id") or g("signal")
            if ref:
                return _resolve_signal_id(app.store, ref)
            aid = app.config.data.get("active_lead_signal_id")
            if aid:
                return int(aid)
            rows = app.store.list_lead_signals()
            return rows[0]["id"] if rows else None

        def _filters(self, qs):
            g = lambda k: (qs.get(k) or [None])[0]
            since = None
            lid = g("lead_signal_id") or g("signal")
            signal_id = _resolve_signal_id(app.store, lid) if lid else None
            if g("scope") == "last_run":
                last = app.store.last_run(signal_id)
                since = last["started"] if last else int(time.time()) + 1
            since_today = None
            if g("scope") == "today":
                tz = g("tz_offset")
                offset = int(tz) if tz is not None and str(tz).lstrip("-").isdigit() else 0
                since_today = _today_start_local(offset)
            return {
                "status": g("status"), "source": g("source"), "persona": g("persona"),
                "min_score": g("min_score") or 0, "q": g("q"), "since": since,
                "lead_signal_id": signal_id, "since_today": since_today,
            }

        def _lead_signals_list_payload(self, qs):
            g = lambda k: (qs.get(k) or [None])[0]
            tz = g("tz_offset")
            offset = int(tz) if tz is not None and str(tz).lstrip("-").isdigit() else 0
            today = _today_start_local(offset)
            out = []
            for row in app.store.list_lead_signals():
                st = app.store.lead_signal_stats(row["id"], today_start=today)
                if app.runner.running and app.runner.running_signal_id == row["id"]:
                    st = {**st, "running": True}
                merged = merge_configs(app.config.data, row["config"])
                out.append({**row, "stats": st, "effective_schedule_hours": float(
                    row.get("schedule_hours") or merged.get("schedule_hours") or 0)})
            return out

        def _start_run(self, lead_signal_id):
            lang = app.config.data.get("lang", "en")
            if app.store.signal_is_running(lead_signal_id):
                return self._send(409, {"ok": False, "error": t(lang, "busy")})
            app.runner.enqueue(lead_signal_id)
            return self._send(202, {"ok": True, "started": True, "lead_signal_id": lead_signal_id})

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/index.html") or (
                u.path.startswith("/leads/") and u.path[7:].strip("/").isdigit()
            ):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/soft-ui-theme.css":
                with open(os.path.join(STATIC_DIR, "soft-ui-theme.css"), "rb") as f:
                    return self._send(200, f.read(), "text/css; charset=utf-8", {"Cache-Control": "max-age=86400"})
            if u.path == "/fonts.css" or u.path.startswith("/fonts/"):
                rel = os.path.normpath(u.path.lstrip("/"))
                full = os.path.join(STATIC_DIR, rel)
                ext = os.path.splitext(full)[1]
                if ".." in rel.split(os.sep) or ext not in STATIC_TYPES or not os.path.isfile(full):
                    return self._send(404, {"error": "not found"})
                with open(full, "rb") as f:
                    return self._send(200, f.read(), STATIC_TYPES[ext], {"Cache-Control": "max-age=86400"})
            if u.path == "/api/config":
                cfg = app.config.public()
                cfg["project_dir"] = PROJECT_DIR
                return self._send(200, cfg)
            if u.path == "/api/lead-signals":
                return self._send(200, self._lead_signals_list_payload(qs))
            m = LS_API.match(u.path)
            if m and m.group(1):
                ref, action = m.group(1), m.group(2)
                row = app.store.get_lead_signal(ref)
                if not row:
                    return self._send(404, {"error": "not found"})
                if action == "runs":
                    limit = max(1, min(50, int((qs.get("limit") or [20])[0])))
                    return self._send(200, app.store.recent_runs(limit, lead_signal_id=row["id"]))
                if action:
                    return self._send(404, {"error": "not found"})
                tz = (qs.get("tz_offset") or ["0"])[0]
                offset = int(tz) if str(tz).lstrip("-").isdigit() else 0
                st = app.store.lead_signal_stats(row["id"], today_start=_today_start_local(offset))
                return self._send(200, {**row, "stats": st})
            if u.path == "/api/status":
                g = lambda k: (qs.get(k) or [None])[0]
                ref = g("lead_signal_id") or g("signal")
                scoped_id = _resolve_signal_id(app.store, ref) if ref else None
                sid = app.runner.running_signal_id
                stats_id = scoped_id if scoped_id else sid
                sig_stats = app.store.stats(stats_id) if stats_id else app.store.stats()
                runs = app.store.recent_runs(10, lead_signal_id=scoped_id) if scoped_id else app.store.recent_runs(10)
                running = app.runner.running and (
                    not scoped_id or app.runner.running_signal_id == scoped_id
                )
                snap = app.runner.progress_snapshot()
                return self._send(200, {
                    "running": running,
                    "running_any": bool(app.runner.running),
                    "running_signal_id": sid,
                    "running_signal_name": app.runner.running_signal_name,
                    "scoped_signal_id": scoped_id,
                    "progress": snap["current"],
                    "progress_events": snap["events"],
                    "last_log": app.runner.last_log if (not scoped_id or sid == scoped_id) else [],
                    "stats": sig_stats,
                    "runs": runs,
                    "next_run": app.next_run_summary(),
                })
            if u.path == "/api/signals":
                f = self._filters(qs)
                limit = max(1, min(500, int((qs.get("limit") or [200])[0])))
                offset = max(0, int((qs.get("offset") or [0])[0]))
                sort = (qs.get("sort") or ["score"])[0]
                rows = app.store.list_signals(limit=limit, offset=offset, sort=sort, **f)
                return self._send(200, rows, extra={"X-Total-Count": str(app.store.count_signals(**f))})
            if u.path == "/api/signals/counts":
                counts = app.store.count_by_status(**self._filters(qs))
                return self._send(200, {"by_status": counts, "total": sum(counts.values())})
            if u.path.startswith("/api/signals/"):
                tail = u.path.rsplit("/", 1)[-1]
                if tail.isdigit():
                    row = app.store.get_signal(int(tail))
                    if not row:
                        return self._send(404, {"error": "not found"})
                    return self._send(200, row)
            if u.path == "/api/catalog":
                q = (qs.get("q") or [""])[0]
                if not q:
                    return self._send(400, {"error": "q is required"})
                try:
                    return self._send(200, generic.search_catalog(q, limit=int((qs.get("limit") or [10])[0])))
                except (urllib.error.URLError, ValueError) as e:
                    return self._send(502, {"error": f"catalog search failed: {e}"})
            if u.path == "/api/export":
                fmt = (qs.get("fmt") or ["csv"])[0]
                body = app.store.export(fmt, sort=(qs.get("sort") or ["score"])[0], **self._filters(qs))
                ctype = "application/json; charset=utf-8" if fmt == "json" else "text/csv; charset=utf-8"
                name = time.strftime("signals-%Y%m%d-%H%M") + (".json" if fmt == "json" else ".csv")
                return self._send(200, body.encode("utf-8"), ctype,
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            u = urllib.parse.urlparse(self.path)
            try:
                body = self._json_body()
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            try:
                if u.path == "/api/config":
                    app.config.update(body)
                    app.reschedule()
                    return self._send(200, app.config.public())
                if u.path == "/api/test-token":
                    token = body.get("token") or app.config.token()
                    if not token or "…" in token:
                        token = app.config.token()
                    if not token:
                        return self._send(400, {"ok": False, "message": t(app.config.data.get("lang", "en"), "token_missing")})
                    ok, msg = check_token(token, app.config.data.get("treg_org") or None, app.config.data.get("lang", "en"))
                    return self._send(200, {"ok": ok, "message": msg})
                if u.path == "/api/run":
                    lid = body.get("lead_signal_id") or app.config.data.get("active_lead_signal_id")
                    if not lid:
                        rows = app.store.list_lead_signals()
                        if not rows:
                            return self._send(400, {"ok": False, "error": "no lead signals"})
                        lid = rows[0]["id"]
                    return self._start_run(int(lid))
                if u.path == "/api/lead-signals":
                    name = body.get("name") or "New lead signal"
                    cfg = body.get("config")
                    if cfg is None:
                        cfg = extract_signal_config(app.config.data)
                    err = validate_signal_config(cfg)
                    if err:
                        return self._send(400, {"error": err})
                    row = app.store.create_lead_signal(
                        name, config=cfg, enabled=body.get("enabled", True),
                        schedule_hours=body.get("schedule_hours", 0),
                    )
                    return self._send(201, row)
                m = LS_API.match(u.path)
                if m and m.group(1):
                    ref, action = m.group(1), m.group(2)
                    row = app.store.get_lead_signal(ref)
                    if not row:
                        return self._send(404, {"error": "not found"})
                    if action == "run":
                        return self._start_run(row["id"])
                    if action == "duplicate":
                        dup = app.store.duplicate_lead_signal(ref)
                        return self._send(201, dup)
                if u.path == "/api/import":
                    rules = app.config.data.get("rules")
                    default_src = body.get("source", "import") if isinstance(body, dict) else "import"
                    lid = body.get("lead_signal_id") or app.config.data.get("active_lead_signal_id") or 1
                    rows = generic.normalize_imported(body, default_src)
                    for s in rows:
                        s["score"], s["persona"], s["matched"] = score_signal(s, rules)
                    return self._send(200, {"ok": True, "received": len(rows),
                                            "new": app.store.upsert_signals(rows, lead_signal_id=int(lid))})
                if u.path == "/api/rescore":
                    lid = body.get("lead_signal_id")
                    rules = app.config.data.get("rules")
                    if lid:
                        sig = app.store.get_lead_signal(lid)
                        if sig:
                            rules = merge_configs(app.config.data, sig["config"]).get("rules", rules)
                    n = app.store.rescore_all(lambda s: score_signal(s, rules),
                                              lead_signal_id=int(lid) if lid else None)
                    return self._send(200, {"ok": True, "rescored": n})
                if u.path.startswith("/api/signals/"):
                    sid = int(u.path.rsplit("/", 1)[1])
                    app.store.update_signal(sid, status=body.get("status"), note=body.get("note"))
                    return self._send(200, {"ok": True})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})

        def do_PUT(self):
            return self._mutate_lead_signal()

        def do_PATCH(self):
            return self._mutate_lead_signal()

        def _mutate_lead_signal(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            u = urllib.parse.urlparse(self.path)
            m = LS_API.match(u.path)
            if not m or not m.group(1) or m.group(2):
                return self._send(404, {"error": "not found"})
            ref = m.group(1)
            try:
                body = self._json_body()
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            try:
                if "config" in body:
                    err = validate_signal_config(body["config"])
                    if err:
                        return self._send(400, {"error": err})
                row = app.store.update_lead_signal(
                    ref,
                    name=body.get("name"),
                    config=body.get("config"),
                    enabled=body.get("enabled") if "enabled" in body else None,
                    schedule_hours=body.get("schedule_hours") if "schedule_hours" in body else None,
                )
                if body.get("set_active"):
                    app.config.update({"active_lead_signal_id": row["id"]})
                return self._send(200, row)
            except ValueError as e:
                return self._send(400, {"error": str(e)})

        def do_DELETE(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            u = urllib.parse.urlparse(self.path)
            m = LS_API.match(u.path)
            if not m or not m.group(1):
                return self._send(404, {"error": "not found"})
            ref, action = m.group(1), m.group(2)
            try:
                if action == "leads":
                    n = app.store.delete_leads_for_signal(ref)
                    return self._send(200, {"ok": True, "deleted": n})
                if action:
                    return self._send(404, {"error": "not found"})
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                hard = (qs.get("hard") or ["1"])[0] != "0"
                result = app.store.delete_lead_signal(ref, hard=hard)
                aid = app.config.data.get("active_lead_signal_id")
                if aid and int(aid) == result["id"]:
                    rows = app.store.list_lead_signals()
                    app.config.update({"active_lead_signal_id": rows[0]["id"] if rows else None})
                return self._send(200, {"ok": True, **result})
            except ValueError as e:
                return self._send(400, {"error": str(e)})

    return Handler


class App:
    def __init__(self, config, store, runner):
        self.config, self.store, self.runner = config, store, runner
        default_id = store.ensure_default_lead_signal(config.data)
        if not config.data.get("active_lead_signal_id"):
            config.update({"active_lead_signal_id": default_id})
        self._timer_lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._scheduler, daemon=True).start()

    def next_run_summary(self):
        """Earliest scheduled run among enabled signals."""
        now = int(time.time())
        due = []
        for row in self.store.list_lead_signals():
            if not row.get("enabled"):
                continue
            merged = merge_configs(self.config.data, row["config"])
            hours = float(row.get("schedule_hours") or merged.get("schedule_hours") or 0)
            if hours <= 0:
                continue
            nxt = row.get("next_run_at")
            if nxt is None:
                nxt = now + int(hours * 3600)
            due.append((nxt, row["id"], row["name"]))
        if not due:
            return None
        due.sort()
        return {"at": due[0][0], "lead_signal_id": due[0][1], "name": due[0][2]}

    def reschedule(self):
        pass  # per-signal next_run_at updated after each run

    def _scheduler(self):
        while not self._stop.wait(20):
            now = time.time()
            for row in self.store.list_lead_signals():
                if not row.get("enabled"):
                    continue
                merged = merge_configs(self.config.data, row["config"])
                hours = float(row.get("schedule_hours") or merged.get("schedule_hours") or 0)
                if hours <= 0:
                    continue
                nxt = row.get("next_run_at")
                if nxt is None:
                    self.store.update_lead_signal(row["id"], next_run_at=int(now + hours * 3600))
                    continue
                if now >= nxt and not self.store.signal_is_running(row["id"]):
                    self.runner.enqueue(row["id"])

    def serve(self, host="127.0.0.1", port=8787):
        httpd = ThreadingHTTPServer((host, port), make_handler(self))
        return httpd
