"""LinkedIn / Facebook via Playwright (optional). Requires login state on disk.

Set FREE_PLAYWRIGHT_STATE=data/playwright-state.json and implement flows in your fork,
or use the stub which returns no rows until configured.
"""
import json
import os


def _state_path(cfg):
    return cfg.get("state_file") or os.environ.get("FREE_PLAYWRIGHT_STATE") or "data/playwright-state.json"


def linkedin_jobs(query, location="United States", cfg=None):
    if not os.path.isfile(_state_path(cfg or {})):
        raise RuntimeError(
            "Playwright LinkedIn jobs: save authenticated storage to data/playwright-state.json "
            "(see docs/FREE_SOURCES_DEPLOY.md)"
        )
    return _stub_search("linkedin_jobs", query, kind="hiring")


def linkedin_posts(query, cfg=None):
    if not os.path.isfile(_state_path(cfg or {})):
        raise RuntimeError("Playwright LinkedIn posts: configure playwright state (see docs/FREE_SOURCES_DEPLOY.md)")
    return _stub_search("linkedin", query, kind="pain")


def facebook_search(term, cfg=None):
    if not os.path.isfile(_state_path(cfg or {})):
        raise RuntimeError("Playwright Facebook: configure playwright state (see docs/FREE_SOURCES_DEPLOY.md)")
    return _stub_search("facebook", term, kind="pain")


def facebook_group(group_name, cfg=None):
    if not os.path.isfile(_state_path(cfg or {})):
        raise RuntimeError("Playwright Facebook groups: configure playwright state (see docs/FREE_SOURCES_DEPLOY.md)")
    return _stub_search("facebook_group", group_name, kind="pain", community=group_name)


def _stub_search(source, query, kind="pain", community=""):
    """Placeholder when state exists but scraper not wired — extend in production."""
    meta_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "playwright_custom.json")
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(source, {}).get(query, [])
    return []
