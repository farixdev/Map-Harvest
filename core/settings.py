"""User settings — a single JSON file at ~/.mapharvest/settings.json.

`DEFAULT_SETTINGS` is the schema. Load and save are deliberately lossy: keys not
in the schema are dropped on the next save, so a settings file cannot accumulate
junk from experiments and dead releases. The merge is *deep* — a file written by
an older build gains the sub-keys added since (a new `sender_profile` field, a
new per-account flag) instead of being flattened back to the old shape, and a
user's existing values survive the upgrade.

Credentials are never stored as typed. `get_secret`/`set_secret` run them through
`core.secrets`; nothing else in the app should read `groq_api_key` or an
account's `app_password` directly.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime

from core import secrets

DEFAULT_SETTINGS = {
    "headless": False,
    "max_limit_cap": 100,
    "default_max_results": 50,
    "export_dir": "",
    "saved_searches": [],

    # ── Appearance ──
    # Both palettes and both densities exist in `ui/theme.py` and neither could
    # be reached: `theme.from_settings` reads these two keys, the merge below
    # drops every key that is not in this schema, so a settings file asking for
    # the light theme was silently answered with the dark one. An unknown value
    # is not validated here — `theme.theme()` falls back rather than raising, so
    # a hand-edited file cannot stop the app from starting.
    "theme": "dark",                  # dark | light
    "density": "comfortable",         # comfortable | compact

    # ── AI ──
    "ai_provider": "auto",            # auto | groq | openrouter | off
    "groq_api_key": "",               # stored via core.secrets
    "groq_model": "llama-3.3-70b-versatile",
    "openrouter_api_key": "",         # stored via core.secrets
    "openrouter_model": "meta-llama/llama-3.3-70b-instruct",
    "ai_max_tokens_per_lead": 220,
    "ai_monthly_token_cap": 2000000,
    "ai_tokens_used": 0,
    "ai_tokens_month": "",            # "YYYY-MM"; resets ai_tokens_used on rollover

    # ── Enrichment ──
    "enrich_max_pages": 4,
    "enrich_workers": 6,
    "enrich_timeout": 8.0,
    "enrich_verify_dns": True,
    "enrich_accept_free_mail": True,  # keep gmail.com/yahoo.com addresses

    # ── Audit ──
    "audit_enabled": True,
    "audit_max_pages": 6,
    "audit_timeout": 8.0,

    # ── Sender profile (drives all copy) ──
    "sender_profile": {
        "company": "Auto Army",
        "sender_name": "",
        "sender_title": "",
        "website": "",
        "reply_to": "",
        "phone": "",
        "postal_address": "",         # required by CAN-SPAM, rendered in footer
        "calendar_link": "",
        "services": [],               # seeded from core.templates.DEFAULT_SERVICES
        "proof_points": [],
        "tone": "direct",             # direct | friendly | consultative
    },

    # ── Gmail accounts ──
    "smtp_accounts": [],              # entries shaped like SMTP_ACCOUNT_DEFAULTS

    # ── Sending schedule ──
    "send_days": [0, 1, 2, 3, 4],     # Mon=0 .. Sun=6
    "send_start_hour": 9,
    "send_end_hour": 17,
    "send_timezone": "local",         # "local" or an IANA name
    "send_min_gap_sec": 60,
    "send_max_gap_sec": 240,
    "daily_cap_per_account": 40,
    "hourly_cap_per_account": 12,
    "warmup_enabled": True,
    "warmup_start": 10,
    "warmup_step": 5,
    "warmup_max": 40,

    # ── Follow-ups ──
    "followup_enabled": True,
    "followup_gap_days": 4,
    "followup_max_steps": 2,

    # ── Compliance ──
    "unsubscribe_mailto": "",         # blank = use the sending account address
    # On by default because this is what keeps cold mail out of the spam folder,
    # and switchable because the copy belongs to the user. Off, the footer loses
    # its opt-out line; a reader with no visible way out marks the mail as spam,
    # which is the one signal a provider never forgets. The invisible
    # `List-Unsubscribe` header is deliberately not covered by this and ships on
    # every send — see `core.mailer.build_message`.
    "append_unsubscribe": True,
    # Off, the footer loses the postal address. CAN-SPAM requires one, and a
    # filter reads its absence as bulk.
    "append_postal_address": True,
    # Off, an unfinished sender profile warns instead of stopping the campaign:
    # the same problems are still listed, and Prepare and Start go ahead.
    "require_profile_complete": True,
    "dry_run": True,                  # fresh installs never surprise-send
}

# One Gmail account row. Also the schema each entry of `smtp_accounts` is merged
# against, so the settings screen can build a new row from it.
SMTP_ACCOUNT_DEFAULTS = {
    "email": "",
    "app_password": "",               # stored via core.secrets
    "display_name": "",
    "daily_cap": 40,
    "enabled": True,
    "warmup_started": "",             # "YYYY-MM-DD"
    "imap_enabled": False,
}

MAX_SAVED_SEARCHES = 12
OLD_SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".mapharvest")
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".leadforge")

if os.path.exists(OLD_SETTINGS_DIR) and not os.path.exists(SETTINGS_DIR):
    import shutil
    try:
        shutil.copytree(OLD_SETTINGS_DIR, SETTINGS_DIR)
    except Exception:
        pass

SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

_SECRET_FIELDS = ("groq_api_key", "openrouter_api_key")
_ACCOUNT_SECRET_PREFIX = "smtp_accounts."
_ACCOUNT_SECRET_SUFFIX = ".app_password"


def _ensure_dir() -> None:
    os.makedirs(SETTINGS_DIR, exist_ok=True)


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ── Merge ────────────────────────────────────────────────────────────────────

def _merge(defaults: dict, data) -> dict:
    """Overlay `data` onto a copy of `defaults`, keeping only schema keys.

    Nested dicts recurse rather than being replaced wholesale, which is what
    lets a new sub-key appear in an existing user's file. `smtp_accounts` is a
    list of dicts, so each entry gets the same treatment against
    SMTP_ACCOUNT_DEFAULTS.
    """
    out = deepcopy(defaults)
    if not isinstance(data, dict):
        return out

    for key, default in defaults.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, dict):
            out[key] = _merge(default, value)
        elif key == "smtp_accounts":
            entries = value if isinstance(value, list) else []
            out[key] = [_merge(SMTP_ACCOUNT_DEFAULTS, a) for a in entries if isinstance(a, dict)]
        elif isinstance(default, list):
            out[key] = list(value) if isinstance(value, list) else deepcopy(default)
        else:
            out[key] = value
    return out


def _default_services() -> list:
    """Seed list for `sender_profile["services"]`.

    Imported lazily: the catalogue lives with the email copy and churns far more
    than this schema does, and settings have to load even when that module is
    missing or broken mid-refactor — the app is unusable without settings.
    """
    try:
        from core.templates import DEFAULT_SERVICES
    except Exception:
        return []
    return [str(s) for s in DEFAULT_SERVICES]


# ── Load / save ──────────────────────────────────────────────────────────────

def load_settings() -> dict:
    _ensure_dir()
    data = {}
    if os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}

    merged = _merge(DEFAULT_SETTINGS, data)
    merged["saved_searches"] = merged["saved_searches"][:MAX_SAVED_SEARCHES]

    # Seed services only when the file never carried the key — an empty list the
    # user saved deliberately must stay empty.
    stored_profile = data.get("sender_profile")
    stored_services = stored_profile.get("services") if isinstance(stored_profile, dict) else None
    if not isinstance(stored_services, list):
        merged["sender_profile"]["services"] = _default_services()

    return merged


def save_settings(settings: dict) -> None:
    _ensure_dir()
    payload = _merge(DEFAULT_SETTINGS, settings)
    payload["saved_searches"] = list(payload.get("saved_searches") or [])[:MAX_SAVED_SEARCHES]

    # Write-then-rename: a crash mid-write would otherwise cost the user every
    # saved search and every Gmail app password in one go.
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def add_saved_search(settings: dict, domains: list[str], area: str, max_results: int) -> dict:
    entry = {
        "domains": list(domains),
        "area": area.strip(),
        "max_results": max_results,
    }
    saved = [s for s in settings.get("saved_searches", []) if s != entry]
    saved.insert(0, entry)
    settings["saved_searches"] = saved[:MAX_SAVED_SEARCHES]
    save_settings(settings)
    return settings


# ── Secrets ──────────────────────────────────────────────────────────────────

def _secret_slot(settings: dict, key: str, create: bool = False):
    """Resolve a secret key to the (dict, field) that holds it, or None.

    Accepts the two top-level API keys and the dotted form
    `smtp_accounts.<email>.app_password`. The email is taken as everything
    between the fixed prefix and suffix — splitting on "." would mangle
    `first.last@example.com`.
    """
    if key in _SECRET_FIELDS:
        return settings, key
    if not (key.startswith(_ACCOUNT_SECRET_PREFIX) and key.endswith(_ACCOUNT_SECRET_SUFFIX)):
        return None

    email = key[len(_ACCOUNT_SECRET_PREFIX):-len(_ACCOUNT_SECRET_SUFFIX)].strip().lower()
    if not email:
        return None
    accounts = settings.setdefault("smtp_accounts", []) if create else (settings.get("smtp_accounts") or [])
    for account in accounts:
        if isinstance(account, dict) and (account.get("email") or "").strip().lower() == email:
            return account, "app_password"
    if not create:
        return None
    account = deepcopy(SMTP_ACCOUNT_DEFAULTS)
    account["email"] = email
    accounts.append(account)
    return account, "app_password"


def get_secret(settings: dict, key: str) -> str:
    """Plaintext value of a secret field. "" when it is unset or unreadable."""
    slot = _secret_slot(settings, key)
    if slot is None:
        return ""
    holder, field = slot
    return secrets.decrypt(holder.get(field) or "")


def set_secret(settings: dict, key: str, value: str) -> None:
    """Store a secret encrypted. Does not persist — call `save_settings` after.

    Naming an account that does not exist yet creates the row, so the settings
    screen can write a password and the account details in either order.
    """
    slot = _secret_slot(settings, key, create=True)
    if slot is None:
        return
    holder, field = slot
    # A UI that round-trips the stored value back through here must not seal it
    # twice — the second unseal would return the first ciphertext.
    holder[field] = value if secrets.is_encrypted(value) else secrets.encrypt(value or "")


def smtp_accounts(settings: dict) -> list[dict]:
    """Usable sending accounts, app passwords decrypted.

    Returns copies: the plaintext password must never find its way back into the
    dict that `save_settings` writes.
    """
    out = []
    for account in settings.get("smtp_accounts") or []:
        if not isinstance(account, dict):
            continue
        email = (account.get("email") or "").strip()
        if not email or not account.get("enabled", True):
            continue
        row = _merge(SMTP_ACCOUNT_DEFAULTS, account)
        row["email"] = email
        row["app_password"] = secrets.decrypt(account.get("app_password") or "")
        out.append(row)
    return out


# ── AI token budget ──────────────────────────────────────────────────────────

def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def note_ai_tokens(settings: dict, tokens: int) -> None:
    """Add `tokens` to this month's usage and persist it.

    The counter is monthly because the cap is monthly; crossing into a new
    "YYYY-MM" zeroes it before adding.
    """
    month = _current_month()
    if settings.get("ai_tokens_month") != month:
        settings["ai_tokens_month"] = month
        settings["ai_tokens_used"] = 0
    settings["ai_tokens_used"] = _to_int(settings.get("ai_tokens_used")) + max(0, _to_int(tokens))
    save_settings(settings)


def ai_budget_left(settings: dict) -> int:
    """Tokens left this month. 0 means stop calling the model."""
    cap = _to_int(settings.get("ai_monthly_token_cap"))
    if settings.get("ai_tokens_month") != _current_month():
        return max(0, cap)  # a stale month is a spent month; usage resets on next call
    return max(0, cap - _to_int(settings.get("ai_tokens_used")))
