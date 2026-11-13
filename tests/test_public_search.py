import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from listener.public_search import public_search_jobs  # noqa: E402


class PublicSearchTests(unittest.TestCase):
    def test_selected_platforms_get_a_public_query(self):
        src = {
            "reddit": {"enabled": True, "queries": ["performance marketing agency"]},
            "x": {"enabled": True, "queries": ["Meta ads agency"]},
            "linkedin_posts": {"enabled": True, "queries": ["CRM for leads"]},
            "linkedin_jobs": {"enabled": False, "queries": []},
            "facebook_posts": {"enabled": True, "queries": ["looking for marketing agency"]},
            "facebook_groups": {"enabled": False},
            "instagram_hashtags": {"enabled": True, "queries": ["WhatsApp marketing automation"]},
            "instagram_reels": {"enabled": False},
            "funding": {"enabled": True},
        }
        jobs = public_search_jobs(src, {"listener_simple": {"keywords_include": "cost per lead too high"}}, {"reddit"})
        names = [job[0] for job in jobs]
        self.assertNotIn("reddit", names)
        self.assertEqual(names, ["x", "linkedin_posts", "facebook_posts", "instagram_hashtags", "funding"])
        self.assertTrue(all(job[4] for job in jobs))
        self.assertIn("site%3Alinkedin.com", jobs[1][1])
        self.assertIn("site%3Ax.com", jobs[0][1])


if __name__ == "__main__":
    unittest.main()
