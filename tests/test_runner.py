import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from listener import runner as runner_mod  # noqa: E402
from listener.config import Config  # noqa: E402
from listener.lead_signals import extract_signal_config  # noqa: E402
from listener.store import Store  # noqa: E402


def fake_source(calls, cost_micro=1000):
    """A paged source that always has another page; records each call."""
    def fetch(client, q, days, cursor=None):
        page = (cursor or 0) + 1
        calls.append(page)
        client.spent_micro += cost_micro
        client.calls += 1
        rows = [{"source": "fake", "kind": "pain", "external_id": f"{q}-{page}-{i}", "title": "need help",
                 "text": "looking for an agency", "url": "", "author": "", "author_url": "", "community": "",
                 "created_utc": 0, "engagement": 0, "query": q} for i in range(3)]
        return rows, page
    return fetch


class RunnerPagingTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TREG_TOKEN", None)
        d = tempfile.mkdtemp()
        self.cfg = Config(os.path.join(d, "config.json"))
        self.cfg.update({"treg_token": "FAKE-TEST-TOKEN-not-a-real-key-0000", "min_score_to_keep": 0,
                         "sources": {k: {"enabled": False} for k in ("hn", "reddit", "x", "linkedin_posts",
                                                                    "linkedin_jobs", "funding")}})
        self.store = Store(os.path.join(d, "s.db"))
        self.signal_id = self.store.ensure_default_lead_signal(self.cfg.data)
        self.saved = dict(runner_mod.TREG_SOURCES)

    def tearDown(self):
        runner_mod.TREG_SOURCES.clear()
        runner_mod.TREG_SOURCES.update(self.saved)

    def run_with(self, max_pages, budget=1.0):
        calls = []
        runner_mod.TREG_SOURCES.clear()
        runner_mod.TREG_SOURCES["reddit"] = fake_source(calls)
        self.cfg.update({"max_cost_per_run_usd": budget,
                         "sources": {"reddit": {"enabled": True, "queries": ["q1"], "max_pages": max_pages}}})
        self.store.update_lead_signal(self.signal_id, config=extract_signal_config(self.cfg.data))
        result = runner_mod.Runner(self.cfg, self.store).run(self.signal_id)
        return calls, result

    def test_stops_at_max_pages(self):
        calls, result = self.run_with(3)
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(result["new"], 9)

    def test_budget_stops_paging(self):
        calls, _ = self.run_with(5, budget=0.002)  # 2 pages * $0.001 reaches the budget
        self.assertEqual(calls, [1, 2])


if __name__ == "__main__":
    unittest.main()
