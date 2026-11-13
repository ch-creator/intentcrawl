"""SignalDesk accounts, packages, and bank-transfer billing."""
import hashlib
import hmac
import json
import os
import time

PACKAGES = [
    {
        "id": "signal",
        "name": "Signal",
        "price_inr": 4999,
        "period": "month",
        "blurb": "One brand. Enough to prove the listener in a client meeting.",
        "keywords": 3,
        "mentions": 2000,
        "projects": 1,
        "updates": "A few times a day",
        "includes": ["1 client workspace", "3 keywords", "2,000 mentions / month", "News, RSS, and Hacker News"],
    },
    {
        "id": "desk",
        "name": "Desk",
        "price_inr": 9999,
        "period": "month",
        "blurb": "The working plan for a brand that needs daily mention tracking.",
        "keywords": 7,
        "mentions": 10000,
        "projects": 3,
        "updates": "About hourly",
        "popular": True,
        "includes": ["3 client workspaces", "7 keywords", "10,000 mentions / month", "Reddit, news, and RSS", "Export CSV and JSON"],
    },
    {
        "id": "agency",
        "name": "Agency",
        "price_inr": 19999,
        "period": "month",
        "blurb": "Several brands on one desk, for an agency or a publisher group.",
        "keywords": 15,
        "mentions": 40000,
        "projects": 0,
        "updates": "On demand",
        "includes": ["Unlimited client workspaces", "15 keywords", "40,000 mentions / month", "Priority setup for new clients"],
    },
]

DEFAULT_BILLING = {
    "payment_method": {
        "id": "bank_transfer",
        "label": "Bank transfer",
        "enabled": True,
        "currency": "INR",
        "beneficiary_name": "Shizzy",
        "bank_name": "",
        "account_number": "",
        "ifsc": "",
        "instructions": "Bank transfer is the only payment method right now. Card and UPI gateways are not connected. Transfer the plan amount and send the UTR with the client name. The workspace stays active after the transfer is confirmed.",
    }
}


def packages():
    return PACKAGES


def billing_path(data_dir):
    return os.path.join(data_dir, "billing.json")


def load_billing(data_dir):
    path = billing_path(data_dir)
    if not os.path.isfile(path):
        return json.loads(json.dumps(DEFAULT_BILLING))
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_BILLING))
    method = data.get("payment_method") or {}
    merged["payment_method"].update(method)
    return merged


def access_path(data_dir):
    return os.path.join(data_dir, "desk_access.json")


def load_access(data_dir):
    path = access_path(data_dir)
    if not os.path.isfile(path):
        return {"secret": "", "users": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def password_hash(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def check_password(access, username, password):
    username = (username or "").strip().lower()
    for user in access.get("users") or []:
        if user.get("username", "").lower() != username:
            continue
        digest = user.get("password_sha256") or ""
        if digest and hmac.compare_digest(digest, password_hash(password or "")):
            return {k: v for k, v in user.items() if k != "password_sha256"}
    return None


def make_session(access, username, days=14):
    exp = int(time.time()) + days * 86400
    payload = f"{username}|{exp}"
    sig = hmac.new(access["secret"].encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def read_session(access, token):
    secret = access.get("secret") or ""
    if not token or not secret or token.count("|") != 2:
        return None
    username, exp, sig = token.split("|", 2)
    payload = f"{username}|{exp}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    for user in access.get("users") or []:
        if user.get("username", "").lower() == username.lower():
            return {k: v for k, v in user.items() if k != "password_sha256"}
    return None
