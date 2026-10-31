import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from listener.store import Store  # noqa: E402


def sig(i, score):
    return {"source": "reddit", "kind": "pain", "external_id": str(i), "title": f"t{i}", "text": "x",
            "score": score, "persona": "saas", "matched": [], "query": "q"}


class StoreQueryTests(unittest.TestCase):
    def setUp(self):
        self.st = Store(os.path.join(tempfile.mkdtemp(), "s.db"))
        self.st.upsert_signals([sig(1, 80), sig(2, 10)])
        # Second batch, as if found by a later run.
        self.run_started = int(time.time()) + 5
        self.st.db.execute("UPDATE signals SET first_seen = first_seen - 100")
        self.st.add_run(self.run_started, self.run_started + 1, 2, 10, 0, 1, [])
        self.st.upsert_signals([sig(3, 50), sig(4, 30)])
        self.st.db.execute("UPDATE signals SET first_seen = ? WHERE external_id IN ('3','4')", (self.run_started + 1,))
        self.st.db.commit()

    def test_sort_by_score_and_recent(self):
        self.assertEqual([r["external_id"] for r in self.st.list_signals()], ["1", "3", "4", "2"])
        self.assertEqual([r["external_id"] for r in self.st.list_signals(sort="recent")], ["3", "4", "1", "2"])

    def test_since_and_count(self):
        last = self.st.last_run()
        self.assertEqual(last, {"started": self.run_started, "new": 2})
        rows = self.st.list_signals(since=last["started"])
        self.assertEqual(sorted(r["external_id"] for r in rows), ["3", "4"])
        self.assertEqual(self.st.count_signals(), 4)
        self.assertEqual(self.st.count_signals(min_score=20), 3)
        self.assertEqual(self.st.count_signals(since=last["started"], min_score=40), 1)

    def test_count_by_status_ignores_status_filter(self):
        ids = {r["external_id"]: r["id"] for r in self.st.list_signals()}
        self.st.update_signal(ids["1"], status="qualified")
        self.st.update_signal(ids["3"], status="replied")
        counts = self.st.count_by_status(status="new", min_score=20)
        self.assertEqual(counts, {"new": 1, "qualified": 1, "contacted": 0, "replied": 1, "ignored": 0})

    def test_paging(self):
        page1 = self.st.list_signals(limit=2, offset=0)
        page2 = self.st.list_signals(limit=2, offset=2)
        self.assertEqual([r["external_id"] for r in page1 + page2], ["1", "3", "4", "2"])
        self.assertEqual(self.st.stats()["last_run"]["new"], 2)

    def test_get_signal(self):
        row = self.st.get_signal(9999)
        self.assertIsNone(row)
        one = self.st.list_signals(limit=1)[0]
        got = self.st.get_signal(one["id"])
        self.assertEqual(got["external_id"], one["external_id"])


if __name__ == "__main__":
    unittest.main()
