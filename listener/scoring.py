"""Rule-based pain/intent scoring. Transparent and free; an LLM pass can be layered on later
via the JSON export (e.g. a newmax skill that re-scores `new` leads)."""
import copy
import re

DEFAULT_RULES = {
    "pain": {
        "traffic dropped": 12, "traffic drop": 12, "lost traffic": 12, "organic traffic down": 12,
        "ai overviews": 8, "ai overview": 8, "zero-click": 8, "zero click": 8,
        "doesn't mention": 12, "doesnt mention": 12, "not mentioned": 10, "not showing up": 10,
        "show up in chatgpt": 12, "appear in chatgpt": 12, "recommend us": 8, "ai search": 6,
        "struggling": 8, "frustrated": 6, "can't get": 6, "no customers": 10, "no users": 8,
        "cac": 6, "customer acquisition": 6, "lead gen": 5, "pipeline": 4, "churn": 3,
    },
    "intent": {
        "looking for": 10, "any recommendations": 10, "recommend an agency": 15, "agency": 6,
        "consultant": 8, "freelancer": 6, "how do i": 6, "how do you": 5, "what worked": 6,
        "anyone tried": 6, "need help": 10, "hire": 4, "budget": 6, "willing to pay": 12,
    },
    "persona": {
        "saas": ["saas", "b2b", "arr", "mrr", "founder", "startup", "head of growth", "demand gen"],
        "dtc": ["shopify", "dtc", "d2c", "ecommerce", "e-commerce", "amazon seller", "store", "brand"],
    },
    "negative": {
        "i built": -8, "check out my": -10, "launching my": -6, "giveaway": -15, "crypto": -15,
        "airdrop": -20, "nsfw": -30, "job seeker": -10, "open to work": -15,
        "who is hiring": -30, "who wants to be hired": -30, "seeking work": -20,
    },
}


def _hits(text, table):
    found, total = [], 0
    for phrase, weight in table.items():
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text):
            found.append(phrase)
            total += weight
    return found, total


def normalize_rules(rules):
    """Accept legacy list rules [{type, phrase, score}] or dict buckets used by score_signal."""
    if not rules:
        return DEFAULT_RULES
    if isinstance(rules, list):
        out = {"pain": {}, "intent": {}, "negative": {}, "persona": dict(DEFAULT_RULES["persona"])}
        for row in rules:
            if not isinstance(row, dict):
                continue
            phrase = (row.get("phrase") or "").strip().lower()
            if not phrase:
                continue
            kind = (row.get("type") or "pain").strip().lower()
            try:
                weight = int(row.get("score", 0))
            except (TypeError, ValueError):
                weight = 0
            if kind == "persona":
                persona_name = (row.get("persona") or row.get("name") or "other").strip().lower()
                words = row.get("words") or row.get("keywords") or [phrase]
                if isinstance(words, str):
                    words = [w.strip() for w in words.split(",") if w.strip()]
                out["persona"][persona_name] = [w.lower() for w in words if w]
            elif kind in out:
                out[kind][phrase] = weight
        return out
    if isinstance(rules, dict):
        if any(isinstance(rules.get(k), dict) for k in ("pain", "intent", "negative", "persona")):
            merged = copy.deepcopy(DEFAULT_RULES)
            for bucket in ("pain", "intent", "negative", "persona"):
                if isinstance(rules.get(bucket), dict):
                    merged[bucket] = rules[bucket]
            return merged
    return DEFAULT_RULES


def score_signal(sig, rules=None):
    """Return (score 0-100, persona, matched_terms)."""
    rules = normalize_rules(rules)
    # community carries subreddit / HN thread title, useful for persona and for filtering hiring threads.
    text = f"{sig.get('title', '')} {sig.get('text', '')} {sig.get('community', '')}".lower()
    pain_f, pain = _hits(text, rules.get("pain", {}))
    intent_f, intent = _hits(text, rules.get("intent", {}))
    neg_f, neg = _hits(text, rules.get("negative", {}))

    persona, best = "other", 0
    for name, words in rules.get("persona", {}).items():
        n = sum(1 for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text))
        if n > best:
            persona, best = name, n

    base = {"hiring": 30, "funding": 35}.get(sig.get("kind"), 0)
    eng = min(10, (sig.get("engagement") or 0) // 10)
    # Questions are a strong "asking for help" signal on forums.
    question = 5 if "?" in text else 0
    score = base + min(45, pain) + min(30, intent) + min(10, best * 3) + eng + question + neg
    return max(0, min(100, score)), persona, pain_f + intent_f + neg_f
