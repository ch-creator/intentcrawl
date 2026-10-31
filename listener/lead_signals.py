"""Named lead signals: merge per-signal config with global settings."""
import copy
import json
import re
import time

# Stored inside lead_signals.config (JSON); global config keeps token, lang, per-call cap.
SIGNAL_CONFIG_KEYS = (
    "focus",
    "sources",
    "rss",
    "rules",
    "listener_simple",
    "free_collectors",
    "custom_sources",
    "lookback_days",
    "max_cost_per_run_usd",
    "min_score_to_keep",
    "schedule_hours",
)

CONFIG_VERSION = 1


def slugify(name, existing_slugs=None):
    base = re.sub(r"[^a-z0-9]+", "-", (name or "signal").lower()).strip("-") or "signal"
    existing = set(existing_slugs or [])
    if base not in existing:
        return base
    for i in range(2, 1000):
        cand = f"{base}-{i}"
        if cand not in existing:
            return cand
    return f"{base}-{int(time.time())}"


def extract_signal_config(global_data):
    """Snapshot listener-related fields from the legacy single config file."""
    out = {}
    for k in SIGNAL_CONFIG_KEYS:
        if k in global_data:
            out[k] = copy.deepcopy(global_data[k])
    return out


def merge_configs(global_data, signal_config_json):
    """Build effective run config: global token/lang + signal overrides."""
    merged = copy.deepcopy(global_data)
    if not signal_config_json:
        return merged
    if isinstance(signal_config_json, str):
        patch = json.loads(signal_config_json or "{}")
    else:
        patch = copy.deepcopy(signal_config_json)
    for k, v in patch.items():
        if k not in SIGNAL_CONFIG_KEYS:
            continue
        if isinstance(v, dict) and isinstance(merged.get(k), dict) and k not in ("rules", "listener_simple"):
            _deep_merge(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def validate_signal_config(patch):
    """Return error message or None."""
    simple = patch.get("listener_simple") if isinstance(patch.get("listener_simple"), dict) else None
    if simple:
        inc = (simple.get("keywords_include") or "").strip()
        if not inc and not patch.get("sources"):
            return "Add at least one keyword or enable sources."
    sources = patch.get("sources") or {}
    if isinstance(sources, dict):
        any_on = any((s or {}).get("enabled") for s in sources.values())
        rss_on = (patch.get("rss") or {}).get("enabled")
        fc = (patch.get("free_collectors") or {}).get("enabled")
        if not any_on and not rss_on and not fc:
            pass  # allow save while drafting
    return None


def infer_run_status(log_lines, new_count, fetched):
    text = "\n".join(log_lines or [])
    if "[stop]" in text or "[停止]" in text:
        return "partial" if new_count or fetched else "failed"
    if "[error]" in text or "[错误]" in text:
        if new_count or fetched:
            return "partial"
        return "failed"
    return "success"
