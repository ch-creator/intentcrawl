"""SignalDesk — mention tracking for client workspaces.

Usage:
  python app.py                 # http://127.0.0.1:8787
  python app.py --port 9000
  python app.py --run-once      # run one listening pass headless, print the log, exit
  python app.py --catalog "youtube search videos"   # find Treg endpoint ids for custom sources
  python app.py --import leads.json                 # import signals found by another agent/script
"""
import argparse
import json
import os
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from listener import generic  # noqa: E402
from listener.config import Config  # noqa: E402
from listener.runner import Runner  # noqa: E402
from listener.scoring import score_signal  # noqa: E402
from listener.server import App  # noqa: E402
from listener.store import Store  # noqa: E402


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="SignalDesk")
    ap.add_argument("--host", default="127.0.0.1", help="bind address; use 0.0.0.0 on the dashboard server")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--data", default=os.path.join(ROOT, "data"), help="directory for config.json and signals.db")
    ap.add_argument("--run-once", action="store_true", help="run one pass without the web UI")
    ap.add_argument("--signal", type=int, default=None, help="lead signal id for --run-once")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--catalog", metavar="QUERY", help="search the Treg catalog (no token needed) and exit")
    ap.add_argument("--import", dest="import_file", metavar="FILE", help="import signals from a JSON file and exit")
    args = ap.parse_args()

    if args.catalog:
        for r in generic.search_catalog(args.catalog, limit=15):
            kind = "body" if r["body"] else "query"
            print(r["id"])
            print(f"    {r['method']}  ${r['usd']}/call  {r['platform']}  {kind} params: {', '.join(r['params'])}")
            print(f"    {r['summary'][:110]}")
        return 0

    os.makedirs(args.data, exist_ok=True)
    config = Config(os.path.join(args.data, "config.json"))
    store = Store(os.path.join(args.data, "signals.db"))
    store.ensure_default_lead_signal(config.data)
    runner = Runner(config, store)

    if args.import_file:
        with open(args.import_file, encoding="utf-8") as f:
            rows = generic.normalize_imported(json.load(f))
        rules = config.data.get("rules")
        for s in rows:
            s["score"], s["persona"], s["matched"] = score_signal(s, rules)
        lid = config.data.get("active_lead_signal_id") or store.ensure_default_lead_signal(config.data)
        print(f"received {len(rows)}, new {store.upsert_signals(rows, lead_signal_id=int(lid))}")
        return 0

    if args.run_once:
        lid = args.signal or config.data.get("active_lead_signal_id") or store.ensure_default_lead_signal(config.data)
        result = runner.run(int(lid))
        for line in result.get("log", [result.get("error", "")]):
            print(line)
        return 0 if result.get("ok") else 1

    app = App(config, store, runner)
    httpd = app.serve(host=args.host, port=args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"SignalDesk running at {url}  (Ctrl+C to quit)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
