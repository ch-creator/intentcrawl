"""Create SignalDesk clients, bank-transfer billing, and dashboard logins."""
import json
import os
import secrets

from listener.config import Config
from listener.desk import access_path, billing_path, password_hash, DEFAULT_BILLING
from listener.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def _signal(focus, keywords, feeds, reddit_feeds):
    platform_keys = [
        "hn", "reddit", "x", "linkedin_posts", "linkedin_jobs", "funding",
        "facebook_posts", "facebook_groups", "instagram_hashtags", "instagram_reels",
    ]
    free = {"hn", "reddit"}
    sources = {}
    for key in platform_keys:
        block = {"enabled": key in free, "max_pages": 1}
        if key != "funding":
            block["queries"] = list(keywords)
        if key == "funding":
            block["region"] = "US"
        sources[key] = block
    platforms = {key: key in free for key in platform_keys}
    platforms["rss"] = True
    return {
        "focus": focus,
        "lookback_days": 14,
        "min_score_to_keep": 8,
        "max_cost_per_run_usd": 1.0,
        "schedule_hours": 0,
        "sources": sources,
        "rss": {"enabled": True, "keywords": list(keywords), "feeds": list(feeds)},
        "rules": [{"type": "intent", "phrase": k.lower(), "score": 30} for k in keywords],
        "listener_simple": {
            "prompt": focus,
            "keywords_include": "\n".join(keywords),
            "keywords_ignore": "",
            "platforms": platforms,
        },
        "free_collectors": {
            "enabled": False,
            "replace_treg": True,
            "incremental": True,
            "reddit_rss": {
                "enabled": True,
                "feeds": list(reddit_feeds),
                "keywords": list(keywords),
                "delay_seconds": 4,
            },
        },
    }


def _iip_books():
    """Authors who want IIP-style publishing: print, ebook, editing, ghostwriting, ISBN."""
    focus = (
        "Indian authors, researchers, and first-time writers who want to publish a print book, "
        "ebook, or audiobook, or who need editing, ghostwriting, an ISBN, or book marketing."
    )
    keywords = [
        "publish my book",
        "looking for a publisher",
        "self publishing India",
        "need a ghostwriter",
        "book editing service",
        "ISBN for my book",
        "academic book publishing",
        "kindle publishing help",
    ]
    cfg = _signal(
        focus,
        keywords,
        [
            "https://news.google.com/rss/search?q=%22publish+my+book%22+OR+%22self+publishing%22+India&hl=en-IN&gl=IN&ceid=IN:en",
            "https://news.google.com/rss/search?q=ghostwriter+book+OR+%22book+publisher%22+India&hl=en-IN&gl=IN&ceid=IN:en",
        ],
        [
            "https://www.reddit.com/r/selfpublish/new/.rss",
            "https://www.reddit.com/r/writing/new/.rss",
        ],
    )
    cfg["sources"]["hn"]["enabled"] = False
    cfg["listener_simple"]["platforms"]["hn"] = False
    cfg["rss"]["keywords"] = []
    cfg["min_score_to_keep"] = 15
    cfg["rules"] = [
        {"type": "intent", "phrase": "publish my book", "score": 35},
        {"type": "intent", "phrase": "looking for a publisher", "score": 35},
        {"type": "intent", "phrase": "need a publisher", "score": 30},
        {"type": "intent", "phrase": "self publishing", "score": 25},
        {"type": "intent", "phrase": "self-publish", "score": 25},
        {"type": "intent", "phrase": "ghostwriter", "score": 30},
        {"type": "intent", "phrase": "ghost writer", "score": 30},
        {"type": "intent", "phrase": "book editing", "score": 20},
        {"type": "intent", "phrase": "isbn", "score": 20},
        {"type": "intent", "phrase": "kindle publishing", "score": 20},
        {"type": "intent", "phrase": "academic book", "score": 15},
        {"type": "pain", "phrase": "manuscript ready", "score": 25},
        {"type": "pain", "phrase": "finished my book", "score": 25},
        {"type": "pain", "phrase": "rejected by publishers", "score": 20},
        {"type": "negative", "phrase": "book review", "score": -20},
        {"type": "negative", "phrase": "book summary", "score": -20},
        {"type": "negative", "phrase": "free pdf", "score": -25},
        {"type": "negative", "phrase": "book recommendation", "score": -15},
        {"type": "negative", "phrase": "free ebook", "score": -20},
        {"type": "negative", "phrase": "book giveaway", "score": -25},
    ]
    cfg["listener_simple"]["keywords_ignore"] = "\n".join(
        row["phrase"] for row in cfg["rules"] if row["type"] == "negative"
    )
    cfg["free_collectors"]["rss_extra"] = {"enabled": False, "feeds": [], "keywords": []}
    cfg["free_collectors"]["funding_rss"] = {"enabled": False, "feeds": [], "keywords": []}
    cfg["free_collectors"]["hn"] = {"enabled": False, "queries": []}
    return cfg


def _tune(cfg, negatives, min_score=15, hn=False):
    cfg["sources"]["hn"]["enabled"] = hn
    cfg["listener_simple"]["platforms"]["hn"] = hn
    cfg["rss"]["keywords"] = []
    cfg["min_score_to_keep"] = min_score
    cfg["rules"] = list(cfg["rules"]) + [
        {"type": "negative", "phrase": phrase, "score": -25} for phrase in negatives
    ]
    cfg["listener_simple"]["keywords_ignore"] = "\n".join(negatives)
    cfg["free_collectors"]["rss_extra"] = {"enabled": False, "feeds": [], "keywords": []}
    cfg["free_collectors"]["funding_rss"] = {"enabled": False, "feeds": [], "keywords": []}
    cfg["free_collectors"]["hn"] = {"enabled": False, "queries": []}
    return cfg


def _ivermectin():
    """Buyers looking for the tablets, lotion, and related products sold on ivermectintablet.com."""
    cfg = _signal(
        "People looking to buy ivermectin tablets, Iverpro, Ivrea lotion or shampoo, or albendazole and fenbendazole combinations, including shipping and authenticity.",
        [
            "buy ivermectin tablets",
            "ivermectin tablets online",
            "Iverpro",
            "Ivrea lotion",
            "ivermectin shipping",
            "albendazole ivermectin",
            "fenbendazole tablets",
        ],
        [
            "https://news.google.com/rss/search?q=%22buy+ivermectin%22+OR+%22ivermectin+tablets%22+OR+Iverpro&hl=en-IN&gl=IN&ceid=IN:en",
        ],
        [],
    )
    cfg["sources"]["reddit"]["enabled"] = False
    cfg["listener_simple"]["platforms"]["reddit"] = False
    return _tune(cfg, ["horse paste", "free sample", "crypto", "airdrop"], min_score=20, hn=False)


def _iip_series():
    """Researchers submitting chapters to an edited book or conference proceedings series."""
    cfg = _signal(
        "Researchers and faculty who want to publish a book chapter, edited volume, conference proceedings, or academic monograph in a peer-reviewed series.",
        [
            "call for book chapters",
            "publish a book chapter",
            "edited book submission",
            "conference proceedings publisher",
            "academic monograph",
            "call for chapters",
        ],
        [
            "https://news.google.com/rss/search?q=%22call+for+book+chapters%22+OR+%22edited+book%22+OR+%22conference+proceedings%22&hl=en-IN&gl=IN&ceid=IN:en",
        ],
        [
            "https://www.reddit.com/r/AskAcademia/new/.rss",
            "https://www.reddit.com/r/PhD/new/.rss",
        ],
    )
    return _tune(cfg, [
        "assignment solution", "homework help", "free pdf", "question paper", "exam notes",
    ], min_score=15, hn=False)


def _shizzy():
    """Businesses that need the performance marketing, WhatsApp, and CRM work Shizzy sells."""
    cfg = _signal(
        "Founders and marketing leads who need a performance marketing agency, Meta or Google ads, WhatsApp follow-up, or a CRM that turns leads into sales.",
        [
            "performance marketing agency",
            "Meta ads agency",
            "Google ads agency",
            "WhatsApp marketing automation",
            "CRM for leads",
            "cost per lead too high",
            "looking for marketing agency",
        ],
        [
            "https://news.google.com/rss/search?q=%22performance+marketing%22+OR+%22WhatsApp+marketing%22+OR+%22cost+per+lead%22+India&hl=en-IN&gl=IN&ceid=IN:en",
        ],
        [
            "https://www.reddit.com/r/PPC/new/.rss",
            "https://www.reddit.com/r/marketing/new/.rss",
            "https://www.reddit.com/r/smallbusiness/new/.rss",
        ],
    )
    return _tune(cfg, [
        "hiring", "internship", "job seeker", "open to work", "free course", "certification course",
    ], min_score=15, hn=False)


CLIENTS = [
    {
        "name": "Ivermectin",
        "enabled": True,
        "package": "desk",
        "config": _ivermectin(),
    },
    {
        "name": "IIP Series",
        "enabled": True,
        "package": "signal",
        "config": _iip_series(),
    },
    {
        "name": "Shizzy",
        "enabled": True,
        "package": "signal",
        "config": _shizzy(),
    },
    {
        "name": "IIP Books",
        "enabled": True,
        "package": "signal",
        "config": _iip_books(),
    },
]


def ensure_clients(store):
    by_name = {row["name"]: row for row in store.list_lead_signals(include_archived=True)}
    ids = {}
    for client in CLIENTS:
        row = by_name.get(client["name"])
        if row:
            store.update_lead_signal(row["id"], config=client["config"], enabled=client["enabled"])
            ids[client["name"]] = row["id"]
        else:
            created = store.create_lead_signal(client["name"], config=client["config"], enabled=client["enabled"])
            ids[client["name"]] = created["id"]
    old = by_name.get("Sofware Development")
    if old:
        store.update_lead_signal(old["id"], enabled=False)
    return ids


def ensure_billing():
    path = billing_path(DATA)
    if os.path.isfile(path):
        return path
    os.makedirs(DATA, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_BILLING, f, indent=2)
        f.write("\n")
    return path


def ensure_access():
    path = access_path(DATA)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    admin_pw = secrets.token_urlsafe(9)
    client_pw = secrets.token_urlsafe(9)
    access = {
        "secret": secrets.token_hex(24),
        "users": [
            {"username": "signaldesk", "role": "admin", "client": "", "password_sha256": password_hash(admin_pw)},
            {"username": "ivermectin", "role": "client", "client": "Ivermectin", "password_sha256": password_hash(client_pw)},
        ],
    }
    os.makedirs(DATA, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(access, f, indent=2)
        f.write("\n")
    return access, {"signaldesk": admin_pw, "ivermectin": client_pw}


def main():
    os.makedirs(DATA, exist_ok=True)
    config = Config(os.path.join(DATA, "config.json"))
    store = Store(os.path.join(DATA, "signals.db"))
    ids = ensure_clients(store)
    config.update({"active_lead_signal_id": ids["Ivermectin"]})
    ensure_billing()
    _access, passwords = ensure_access()
    print(json.dumps({"clients": ids, "passwords": passwords}, indent=2))


if __name__ == "__main__":
    main()
