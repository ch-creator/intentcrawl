import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from listener.free.defaults import FREE_COLLECTORS_DEFAULT  # noqa: E402
from listener.free.registry import build_free_jobs  # noqa: E402
from listener.store import Store  # noqa: E402


class FreeCollectorTests(unittest.TestCase):
    def test_build_jobs_hn_only(self):
        fcfg = {"enabled": True, "incremental": False, "hn": {"enabled": True, "queries": ["shopify"]}}
        jobs = build_free_jobs(fcfg, days=7, store=None)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0][0], "hn")
        self.assertEqual(jobs[0][1], "shopify")
        self.assertTrue(jobs[0][4])

    def test_incremental_state(self):
        d = tempfile.mkdtemp()
        store = Store(os.path.join(d, "s.db"))
        store.set_fetch_state("hn", "hn:test", "1000")
        st = store.get_fetch_state("hn", "hn:test")
        self.assertEqual(st["cursor"], "1000")

    def test_default_has_platforms(self):
        self.assertIn("reddit_rss", FREE_COLLECTORS_DEFAULT)
        self.assertTrue(FREE_COLLECTORS_DEFAULT["hn"]["queries"])


if __name__ == "__main__":
    unittest.main()
