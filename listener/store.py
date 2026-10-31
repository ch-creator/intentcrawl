"""SQLite storage with de-duplication on (lead_signal_id, source, external_id)."""
import copy
import csv
import io
import json
import sqlite3
import threading
import time
import uuid

from .lead_signals import CONFIG_VERSION, extract_signal_config, slugify

STATUSES = ("new", "qualified", "contacted", "replied", "ignored")
RUN_STATUSES = ("running", "success", "partial", "failed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lead_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  config TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  schedule_hours REAL NOT NULL DEFAULT 0,
  archived_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  next_run_at INTEGER,
  config_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_lead_signals_slug ON lead_signals(slug);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_signal_id INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
  url TEXT, title TEXT, text TEXT, author TEXT, author_url TEXT, community TEXT,
  created_utc INTEGER, engagement INTEGER DEFAULT 0, query TEXT,
  score INTEGER DEFAULT 0, persona TEXT, matched TEXT,
  status TEXT DEFAULT 'new', note TEXT DEFAULT '',
  first_seen INTEGER NOT NULL,
  UNIQUE(lead_signal_id, source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(score DESC);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_signal_id INTEGER,
  started INTEGER, finished INTEGER, new_signals INTEGER DEFAULT 0,
  fetched INTEGER DEFAULT 0, cost_micro INTEGER DEFAULT 0, calls INTEGER DEFAULT 0, log TEXT,
  status TEXT DEFAULT 'success',
  error_summary TEXT DEFAULT '',
  run_uuid TEXT
);
CREATE TABLE IF NOT EXISTS fetch_state (
  collector TEXT NOT NULL,
  query_key TEXT NOT NULL,
  cursor TEXT,
  last_success INTEGER,
  PRIMARY KEY (collector, query_key)
);
"""

EXPORT_FIELDS = ["id", "lead_signal_id", "score", "persona", "status", "source", "kind", "community", "author",
                 "author_url", "title", "text", "url", "query", "matched", "engagement", "created_utc", "note"]


class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._migrate_legacy_signals_table()
        self._migrate_runs_columns()
        self._ensure_signal_indexes()
        self.reconcile_stale_runs()

    def _ensure_signal_indexes(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(signals)").fetchall()}
        if "lead_signal_id" in cols:
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_lead_signal ON signals(lead_signal_id, first_seen DESC)"
            )
            self.db.commit()

    def _table_sql(self, name):
        r = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return r[0] if r else ""

    def _signals_has_signal_id_unique(self):
        sql = self._table_sql("signals") or ""
        return "lead_signal_id" in sql and "UNIQUE(lead_signal_id" in sql

    def _migrate_legacy_signals_table(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(signals)").fetchall()}
        if self._signals_has_signal_id_unique():
            return
        if "lead_signal_id" not in cols:
            self.db.execute("ALTER TABLE signals ADD COLUMN lead_signal_id INTEGER NOT NULL DEFAULT 1")
            self.db.commit()
        if self._signals_has_signal_id_unique():
            return
        # Rebuild to replace UNIQUE(source, external_id) with per-signal dedupe.
        self.db.executescript("""
        CREATE TABLE signals_new (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          lead_signal_id INTEGER NOT NULL DEFAULT 1,
          source TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
          url TEXT, title TEXT, text TEXT, author TEXT, author_url TEXT, community TEXT,
          created_utc INTEGER, engagement INTEGER DEFAULT 0, query TEXT,
          score INTEGER DEFAULT 0, persona TEXT, matched TEXT,
          status TEXT DEFAULT 'new', note TEXT DEFAULT '',
          first_seen INTEGER NOT NULL,
          UNIQUE(lead_signal_id, source, external_id)
        );
        INSERT INTO signals_new
          SELECT id, COALESCE(lead_signal_id, 1), source, kind, external_id, url, title, text,
                 author, author_url, community, created_utc, engagement, query, score, persona,
                 matched, status, note, first_seen FROM signals;
        DROP TABLE signals;
        ALTER TABLE signals_new RENAME TO signals;
        CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(score DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
        CREATE INDEX IF NOT EXISTS idx_signals_lead_signal ON signals(lead_signal_id, first_seen DESC);
        """)
        self.db.commit()

    def _migrate_runs_columns(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(runs)").fetchall()}
        for col, ddl in (
            ("lead_signal_id", "ALTER TABLE runs ADD COLUMN lead_signal_id INTEGER"),
            ("status", "ALTER TABLE runs ADD COLUMN status TEXT DEFAULT 'success'"),
            ("error_summary", "ALTER TABLE runs ADD COLUMN error_summary TEXT DEFAULT ''"),
            ("run_uuid", "ALTER TABLE runs ADD COLUMN run_uuid TEXT"),
        ):
            if col not in cols:
                self.db.execute(ddl)
        self.db.execute("UPDATE runs SET lead_signal_id=1 WHERE lead_signal_id IS NULL")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_runs_signal ON runs(lead_signal_id, id DESC)")
        self.db.commit()

    def ensure_default_lead_signal(self, global_config_data, name="Default"):
        """Create default signal from legacy config if none exist."""
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM lead_signals WHERE archived_at IS NULL").fetchone()[0]
            if n:
                return self.db.execute(
                    "SELECT id FROM lead_signals WHERE archived_at IS NULL ORDER BY id LIMIT 1"
                ).fetchone()[0]
            cfg = extract_signal_config(global_config_data)
            prompt = (cfg.get("listener_simple") or {}).get("prompt") or cfg.get("focus") or ""
            if prompt and name == "Default":
                name = prompt[:80] if len(prompt) <= 80 else prompt[:77] + "..."
            now = int(time.time())
            slug = slugify(name, [])
            self.db.execute(
                """INSERT INTO lead_signals (name, slug, config, enabled, schedule_hours, created_at, updated_at, config_version)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (name, slug, json.dumps(cfg, ensure_ascii=False), 1,
                 float(global_config_data.get("schedule_hours") or 0), now, now, CONFIG_VERSION),
            )
            self.db.commit()
            return self.db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def reconcile_stale_runs(self, max_age=1800):
        cutoff = int(time.time()) - max_age
        with self.lock:
            self.db.execute(
                """UPDATE runs SET status='failed', finished=?, error_summary='stale run'
                   WHERE status='running' AND started < ?""",
                (int(time.time()), cutoff),
            )
            self.db.commit()

    def upsert_signals(self, signals, lead_signal_id=1):
        """Insert unseen signals; returns number of new rows."""
        new = 0
        now = int(time.time())
        lid = int(lead_signal_id)
        with self.lock:
            for s in signals:
                cur = self.db.execute(
                    """INSERT OR IGNORE INTO signals
                       (lead_signal_id, source, kind, external_id, url, title, text, author, author_url, community,
                        created_utc, engagement, query, score, persona, matched, first_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (lid, s["source"], s["kind"], s["external_id"], s.get("url"), s.get("title"), s.get("text"),
                     s.get("author"), s.get("author_url"), s.get("community"), s.get("created_utc"),
                     s.get("engagement", 0), s.get("query"), s.get("score", 0), s.get("persona"),
                     json.dumps(s.get("matched", []), ensure_ascii=False), now))
                new += cur.rowcount
            self.db.commit()
        return new

    @staticmethod
    def _where(status=None, source=None, persona=None, min_score=0, q=None, since=None, lead_signal_id=None,
               since_today=None):
        sql, args = " WHERE score >= ?", [int(min_score or 0)]
        if lead_signal_id is not None and str(lead_signal_id).strip() != "":
            sql += " AND lead_signal_id = ?"
            args.append(int(lead_signal_id))
        for col, val in (("status", status), ("source", source), ("persona", persona)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        if q:
            sql += " AND (title LIKE ? OR text LIKE ? OR author LIKE ?)"
            args += [f"%{q}%"] * 3
        if since:
            sql += " AND first_seen >= ?"
            args.append(int(since))
        if since_today is not None:
            sql += " AND first_seen >= ?"
            args.append(int(since_today))
        return sql, args

    def get_signal(self, sid):
        with self.lock:
            r = self.db.execute("SELECT * FROM signals WHERE id=?", (int(sid),)).fetchone()
        return _row(r) if r else None

    def list_signals(self, limit=200, offset=0, sort="score", **filters):
        where, args = self._where(**filters)
        order = " ORDER BY first_seen DESC, score DESC" if sort == "recent" else " ORDER BY score DESC, first_seen DESC"
        with self.lock:
            rows = self.db.execute("SELECT * FROM signals" + where + order + " LIMIT ? OFFSET ?",
                                   args + [int(limit), int(offset)]).fetchall()
        return [_row(r) for r in rows]

    def count_signals(self, **filters):
        where, args = self._where(**filters)
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM signals" + where, args).fetchone()[0]

    def count_by_status(self, **filters):
        filters = dict(filters)
        filters.pop("status", None)
        where, args = self._where(**filters)
        with self.lock:
            rows = self.db.execute("SELECT status, COUNT(*) FROM signals" + where + " GROUP BY status", args).fetchall()
        counts = {s: 0 for s in STATUSES}
        counts.update({r[0]: r[1] for r in rows if r[0] in counts})
        return counts

    def last_run(self, lead_signal_id=None):
        with self.lock:
            if lead_signal_id is not None:
                r = self.db.execute(
                    "SELECT started, new_signals FROM runs WHERE lead_signal_id=? AND status != 'running' "
                    "ORDER BY id DESC LIMIT 1",
                    (int(lead_signal_id),),
                ).fetchone()
            else:
                r = self.db.execute(
                    "SELECT started, new_signals FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"
                ).fetchone()
        return {"started": r[0], "new": r[1]} if r else None

    def update_signal(self, sid, status=None, note=None):
        if status is not None and status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self.lock:
            if status is not None:
                self.db.execute("UPDATE signals SET status=? WHERE id=?", (status, sid))
            if note is not None:
                self.db.execute("UPDATE signals SET note=? WHERE id=?", (note, sid))
            self.db.commit()

    def rescore_all(self, scorer, lead_signal_id=None):
        with self.lock:
            if lead_signal_id is not None:
                rows = self.db.execute(
                    "SELECT id, kind, title, text, community, engagement FROM signals WHERE lead_signal_id=?",
                    (int(lead_signal_id),),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT id, kind, title, text, community, engagement FROM signals"
                ).fetchall()
            for r in rows:
                score, persona, matched = scorer(dict(r))
                self.db.execute("UPDATE signals SET score=?, persona=?, matched=? WHERE id=?",
                                (score, persona, json.dumps(matched, ensure_ascii=False), r["id"]))
            self.db.commit()
        return len(rows)

    def stats(self, lead_signal_id=None):
        with self.lock:
            if lead_signal_id is not None:
                lid = int(lead_signal_id)
                by_status = dict(self.db.execute(
                    "SELECT status, COUNT(*) FROM signals WHERE lead_signal_id=? GROUP BY status", (lid,)
                ).fetchall())
                by_source = dict(self.db.execute(
                    "SELECT source, COUNT(*) FROM signals WHERE lead_signal_id=? GROUP BY source", (lid,)
                ).fetchall())
                spent = self.db.execute(
                    "SELECT COALESCE(SUM(cost_micro),0), COALESCE(SUM(calls),0) FROM runs WHERE lead_signal_id=?",
                    (lid,),
                ).fetchone()
                month_start = int(time.time()) - 30 * 86400
                spent30 = self.db.execute(
                    "SELECT COALESCE(SUM(cost_micro),0) FROM runs WHERE lead_signal_id=? AND started >= ?",
                    (lid, month_start),
                ).fetchone()[0]
            else:
                by_status = dict(self.db.execute("SELECT status, COUNT(*) FROM signals GROUP BY status").fetchall())
                by_source = dict(self.db.execute("SELECT source, COUNT(*) FROM signals GROUP BY source").fetchall())
                spent = self.db.execute("SELECT COALESCE(SUM(cost_micro),0), COALESCE(SUM(calls),0) FROM runs").fetchone()
                month_start = int(time.time()) - 30 * 86400
                spent30 = self.db.execute(
                    "SELECT COALESCE(SUM(cost_micro),0) FROM runs WHERE started >= ?", (month_start,)
                ).fetchone()[0]
        last = self.last_run(lead_signal_id)
        return {"by_status": by_status, "by_source": by_source, "total": sum(by_status.values()), "last_run": last,
                "spent_usd": spent[0] / 1e6, "spent_30d_usd": spent30 / 1e6, "calls": spent[1]}

    def begin_run(self, lead_signal_id, started):
        run_uuid = str(uuid.uuid4())
        with self.lock:
            cur = self.db.execute(
                """INSERT INTO runs (lead_signal_id, started, status, run_uuid, new_signals, fetched, cost_micro, calls)
                   VALUES (?,?,?,?,0,0,0,0)""",
                (int(lead_signal_id), started, "running", run_uuid),
            )
            self.db.commit()
            return cur.lastrowid, run_uuid

    def finish_run(self, run_id, finished, new_signals, fetched, cost_micro, calls, log, status="success",
                   error_summary=""):
        with self.lock:
            self.db.execute(
                """UPDATE runs SET finished=?, new_signals=?, fetched=?, cost_micro=?, calls=?, log=?,
                   status=?, error_summary=? WHERE id=?""",
                (finished, new_signals, fetched, cost_micro, calls, "\n".join(log), status, error_summary, run_id),
            )
            self.db.commit()

    def add_run(self, started, finished, new_signals, fetched, cost_micro, calls, log, lead_signal_id=1,
                status="success", error_summary=""):
        """Legacy one-shot insert (tests / imports)."""
        with self.lock:
            self.db.execute(
                """INSERT INTO runs (lead_signal_id, started, finished, new_signals, fetched, cost_micro, calls, log,
                   status, error_summary) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (lead_signal_id, started, finished, new_signals, fetched, cost_micro, calls, "\n".join(log),
                 status, error_summary),
            )
            self.db.commit()

    def recent_runs(self, limit=10, lead_signal_id=None):
        with self.lock:
            if lead_signal_id is not None:
                rows = self.db.execute(
                    "SELECT * FROM runs WHERE lead_signal_id=? ORDER BY id DESC LIMIT ?",
                    (int(lead_signal_id), limit),
                ).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def signal_is_running(self, lead_signal_id):
        with self.lock:
            r = self.db.execute(
                "SELECT 1 FROM runs WHERE lead_signal_id=? AND status='running' LIMIT 1",
                (int(lead_signal_id),),
            ).fetchone()
        return r is not None

    def get_fetch_state(self, collector, query_key):
        with self.lock:
            r = self.db.execute(
                "SELECT cursor, last_success FROM fetch_state WHERE collector=? AND query_key=?",
                (collector, query_key),
            ).fetchone()
        if not r:
            return None
        return {"cursor": r[0], "last_success": r[1]}

    def set_fetch_state(self, collector, query_key, cursor):
        now = int(time.time())
        with self.lock:
            self.db.execute(
                """INSERT INTO fetch_state (collector, query_key, cursor, last_success) VALUES (?,?,?,?)
                   ON CONFLICT(collector, query_key) DO UPDATE SET cursor=excluded.cursor, last_success=excluded.last_success""",
                (collector, query_key, str(cursor), now),
            )
            self.db.commit()

    def export(self, fmt="csv", **filters):
        rows = self.list_signals(limit=100000, **filters)
        if fmt == "json":
            return json.dumps(rows, ensure_ascii=False, indent=2)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["matched"] = ", ".join(r.get("matched") or [])
            w.writerow(r)
        return "\ufeff" + buf.getvalue()

    # ---- lead_signals CRUD ----

    def _lead_signal_row(self, r):
        if not r:
            return None
        d = dict(r)
        try:
            d["config"] = json.loads(d.get("config") or "{}")
        except json.JSONDecodeError:
            d["config"] = {}
        d["enabled"] = bool(d.get("enabled"))
        return d

    def list_lead_signals(self, include_archived=False):
        sql = "SELECT * FROM lead_signals"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        sql += " ORDER BY name COLLATE NOCASE"
        with self.lock:
            rows = self.db.execute(sql).fetchall()
        return [self._lead_signal_row(r) for r in rows]

    def get_lead_signal(self, ref):
        """ref: int id or slug string."""
        with self.lock:
            if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
                r = self.db.execute("SELECT * FROM lead_signals WHERE id=?", (int(ref),)).fetchone()
            else:
                r = self.db.execute("SELECT * FROM lead_signals WHERE slug=?", (str(ref),)).fetchone()
        return self._lead_signal_row(r)

    def create_lead_signal(self, name, config=None, enabled=True, schedule_hours=0):
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")
        config = config or {}
        now = int(time.time())
        slugs = [r[0] for r in self.db.execute("SELECT slug FROM lead_signals").fetchall()]
        slug = slugify(name, slugs)
        with self.lock:
            cur = self.db.execute(
                """INSERT INTO lead_signals (name, slug, config, enabled, schedule_hours, created_at, updated_at, config_version)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (name, slug, json.dumps(config, ensure_ascii=False), 1 if enabled else 0,
                 float(schedule_hours or 0), now, now, CONFIG_VERSION),
            )
            self.db.commit()
            sid = cur.lastrowid
        return self.get_lead_signal(sid)

    def update_lead_signal(self, ref, *, name=None, config=None, enabled=None, schedule_hours=None, next_run_at=None):
        row = self.get_lead_signal(ref)
        if not row:
            raise ValueError("lead signal not found")
        now = int(time.time())
        fields, args = [], []
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("name is required")
            fields.append("name=?")
            args.append(name)
        if config is not None:
            fields.append("config=?")
            args.append(json.dumps(config, ensure_ascii=False))
        if enabled is not None:
            fields.append("enabled=?")
            args.append(1 if enabled else 0)
        if schedule_hours is not None:
            fields.append("schedule_hours=?")
            args.append(float(schedule_hours))
        if next_run_at is not None:
            fields.append("next_run_at=?")
            args.append(next_run_at)
        fields.append("updated_at=?")
        args.append(now)
        args.append(row["id"])
        with self.lock:
            self.db.execute(f"UPDATE lead_signals SET {', '.join(fields)} WHERE id=?", args)
            self.db.commit()
        return self.get_lead_signal(row["id"])

    def duplicate_lead_signal(self, ref):
        row = self.get_lead_signal(ref)
        if not row:
            raise ValueError("lead signal not found")
        return self.create_lead_signal(
            f"{row['name']} (copy)",
            config=copy.deepcopy(row["config"]),
            enabled=row["enabled"],
            schedule_hours=row.get("schedule_hours") or 0,
        )

    def delete_leads_for_signal(self, ref):
        row = self.get_lead_signal(ref)
        if not row:
            raise ValueError("lead signal not found")
        with self.lock:
            cur = self.db.execute("DELETE FROM signals WHERE lead_signal_id=?", (row["id"],))
            self.db.commit()
            return cur.rowcount

    def delete_lead_signal(self, ref, *, hard=True):
        row = self.get_lead_signal(ref)
        if not row:
            raise ValueError("lead signal not found")
        if self.signal_is_running(row["id"]):
            raise ValueError("cannot delete while a run is in progress")
        with self.lock:
            n_leads = self.db.execute(
                "SELECT COUNT(*) FROM signals WHERE lead_signal_id=?", (row["id"],)
            ).fetchone()[0]
            if hard:
                self.db.execute("DELETE FROM signals WHERE lead_signal_id=?", (row["id"],))
                self.db.execute("DELETE FROM runs WHERE lead_signal_id=?", (row["id"],))
                self.db.execute("DELETE FROM lead_signals WHERE id=?", (row["id"],))
            else:
                self.db.execute(
                    "UPDATE lead_signals SET archived_at=?, enabled=0 WHERE id=?",
                    (int(time.time()), row["id"]),
                )
            self.db.commit()
        return {"deleted_leads": n_leads, "id": row["id"]}

    def lead_signal_stats(self, signal_id, today_start=None):
        """Counts and last run for dashboard row."""
        lid = int(signal_id)
        if today_start is None:
            # UTC midnight; UI may pass local offset later
            now = int(time.time())
            today_start = now - (now % 86400)
        with self.lock:
            total = self.db.execute(
                "SELECT COUNT(*) FROM signals WHERE lead_signal_id=?", (lid,)
            ).fetchone()[0]
            today = self.db.execute(
                "SELECT COUNT(*) FROM signals WHERE lead_signal_id=? AND first_seen >= ?",
                (lid, today_start),
            ).fetchone()[0]
            last = self.db.execute(
                """SELECT id, started, finished, new_signals, status, cost_micro FROM runs
                   WHERE lead_signal_id=? ORDER BY id DESC LIMIT 1""",
                (lid,),
            ).fetchone()
            running = self.db.execute(
                "SELECT 1 FROM runs WHERE lead_signal_id=? AND status='running' LIMIT 1", (lid,)
            ).fetchone()
            spent30 = self.db.execute(
                """SELECT COALESCE(SUM(cost_micro),0) FROM runs
                   WHERE lead_signal_id=? AND started >= ?""",
                (lid, int(time.time()) - 30 * 86400),
            ).fetchone()[0]
        return {
            "total_leads": total,
            "leads_today": today,
            "last_run": dict(last) if last else None,
            "running": running is not None,
            "spent_30d_usd": spent30 / 1e6,
        }


def _row(r):
    d = dict(r)
    try:
        d["matched"] = json.loads(d.get("matched") or "[]")
    except json.JSONDecodeError:
        d["matched"] = []
    return d
