import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from listener.config import DEFAULT_CONFIG  # noqa: E402
from listener.lead_signals import merge_configs, slugify  # noqa: E402
from listener.store import Store  # noqa: E402


class LeadSignalTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "s.db"))

    def test_slugify_unique(self):
        self.assertEqual(slugify("Book Publishing", []), "book-publishing")
        self.assertEqual(slugify("Book Publishing", ["book-publishing"]), "book-publishing-2")

    def test_crud_and_scoped_leads(self):
        a = self.store.create_lead_signal("Software Development", config={"focus": "dev"})
        b = self.store.create_lead_signal("Book Publishing", config={"focus": "books"})
        self.store.upsert_signals([{
            "source": "hn", "kind": "pain", "external_id": "1", "title": "t", "text": "x",
            "score": 50, "persona": "saas", "matched": [], "query": "q",
        }], lead_signal_id=a["id"])
        self.store.upsert_signals([{
            "source": "hn", "kind": "pain", "external_id": "2", "title": "t", "text": "x",
            "score": 50, "persona": "saas", "matched": [], "query": "q",
        }], lead_signal_id=b["id"])
        self.assertEqual(self.store.count_signals(lead_signal_id=a["id"]), 1)
        self.assertEqual(self.store.delete_leads_for_signal(a["id"]), 1)
        self.assertEqual(self.store.count_signals(lead_signal_id=a["id"]), 0)
        dup = self.store.duplicate_lead_signal(b["id"])
        self.assertIn("(copy)", dup["name"])

    def test_merge_configs(self):
        global_cfg = dict(DEFAULT_CONFIG)
        global_cfg["focus"] = "global"
        merged = merge_configs(global_cfg, {"focus": "local", "lookback_days": 3})
        self.assertEqual(merged["focus"], "local")
        self.assertEqual(merged["lookback_days"], 3)
        self.assertIn("treg_token", merged)


if __name__ == "__main__":
    unittest.main()
