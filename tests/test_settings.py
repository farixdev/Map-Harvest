"""Offline tests for core.secrets and core.settings.

Every test redirects SETTINGS_DIR/SETTINGS_PATH into a temp directory, so the
suite never reads, writes or destroys a developer's real ~/.mapharvest.
"""
import contextlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import secrets as SEC  # noqa: E402
from core import settings as ST  # noqa: E402


@contextlib.contextmanager
def temp_settings(initial=None):
    """Point core.settings at a throwaway directory, optionally pre-seeded."""
    original = (ST.SETTINGS_DIR, ST.SETTINGS_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        ST.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        if initial is not None:
            with open(ST.SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(initial, f)
        try:
            yield ST.SETTINGS_PATH
        finally:
            ST.SETTINGS_DIR, ST.SETTINGS_PATH = original


# ── core.secrets ─────────────────────────────────────────────────────────────

def test_secrets_roundtrip():
    for value in ("hunter2", "a-very-long-gmail-app-password", "pässwörd 日本 🔐"):
        token = SEC.encrypt(value)
        assert SEC.is_encrypted(token), token
        assert token != value
        assert value not in token, "plaintext leaked into the stored form"
        assert SEC.decrypt(token) == value, token
    assert SEC.encrypt("") == ""
    print("secrets round-trip: OK")


def test_secrets_xor_fallback_roundtrip():
    """The non-Windows path must round-trip too, so force DPAPI to fail."""
    original = SEC._dpapi
    SEC._dpapi = lambda protect, data: b""
    try:
        token = SEC.encrypt("fallback-secret")
        assert SEC.is_encrypted(token)
        assert SEC.decrypt(token) == "fallback-secret"
    finally:
        SEC._dpapi = original
    print("secrets XOR fallback: OK")


def test_secrets_plaintext_passthrough():
    # Migration path: settings written before encryption existed.
    assert not SEC.is_encrypted("abcd1234")
    assert not SEC.is_encrypted("")
    assert SEC.decrypt("abcd1234") == "abcd1234"
    assert SEC.decrypt("") == ""
    # Unopenable tokens degrade to "" rather than returning binary garbage.
    assert SEC.decrypt("enc:v1:not-base64!!") == ""
    print("secrets plaintext passthrough: OK")


# ── merge behaviour ──────────────────────────────────────────────────────────

def test_deep_merge_partial_nested_dict():
    stored = {
        "headless": True,
        "sender_profile": {"company": "Bob's Bots", "phone": "555-0100"},
        "smtp_accounts": [{"email": "a@b.com", "daily_cap": 12}],
    }
    with temp_settings(stored):
        s = ST.load_settings()

    profile = s["sender_profile"]
    assert profile["company"] == "Bob's Bots"          # kept
    assert profile["phone"] == "555-0100"              # kept
    assert profile["tone"] == "direct"                 # gained from defaults
    assert "calendar_link" in profile and "proof_points" in profile

    account = s["smtp_accounts"][0]
    assert account["email"] == "a@b.com"
    assert account["daily_cap"] == 12                  # kept
    assert account["enabled"] is True                  # gained from defaults
    assert account["imap_enabled"] is False
    assert account["warmup_started"] == ""
    assert s["headless"] is True
    print("deep merge of partial nested dict: OK")


def test_old_format_settings_load():
    old = {
        "headless": True,
        "max_limit_cap": 250,
        "default_max_results": 75,
        "export_dir": "C:/exports",
        "saved_searches": [{"domains": ["roofing"], "area": "Toronto", "max_results": 50}],
    }
    with temp_settings(old):
        s = ST.load_settings()

    for key, value in old.items():
        assert s[key] == value, key
    missing = [k for k in ST.DEFAULT_SETTINGS if k not in s]
    assert not missing, missing
    assert s["dry_run"] is True                        # fresh install never sends
    assert s["ai_provider"] == "auto"
    assert s["send_days"] == [0, 1, 2, 3, 4]
    assert isinstance(s["sender_profile"], dict) and s["smtp_accounts"] == []
    print("old-format settings.json upgrade: OK")


def test_unknown_keys_dropped_and_saved_search_cap():
    with temp_settings({"headless": True, "legacy_junk": 1}):
        s = ST.load_settings()
        assert "legacy_junk" not in s
        s["also_junk"] = "x"
        s["saved_searches"] = [{"domains": [str(i)], "area": "a", "max_results": 1}
                               for i in range(ST.MAX_SAVED_SEARCHES + 5)]
        ST.save_settings(s)
        with open(ST.SETTINGS_PATH, encoding="utf-8") as f:
            written = json.load(f)
    assert "also_junk" not in written
    assert len(written["saved_searches"]) == ST.MAX_SAVED_SEARCHES
    print("unknown keys dropped + saved-search cap: OK")


def test_add_saved_search_roundtrip():
    with temp_settings({}):
        s = ST.load_settings()
        ST.add_saved_search(s, ["roofing", "plumbing"], " Toronto ", 40)
        reloaded = ST.load_settings()
    assert reloaded["saved_searches"][0] == {
        "domains": ["roofing", "plumbing"], "area": "Toronto", "max_results": 40,
    }
    print("add_saved_search round-trip: OK")


# ── secrets through settings ─────────────────────────────────────────────────

def test_get_set_secret():
    with temp_settings({}):
        s = ST.load_settings()
        ST.set_secret(s, "groq_api_key", "gsk_live_123")
        ST.save_settings(s)
        with open(ST.SETTINGS_PATH, encoding="utf-8") as f:
            written = json.load(f)
        assert "gsk_live_123" not in json.dumps(written), "API key stored in plaintext"
        assert SEC.is_encrypted(written["groq_api_key"])
        reloaded = ST.load_settings()
        assert ST.get_secret(reloaded, "groq_api_key") == "gsk_live_123"
        assert ST.get_secret(reloaded, "openrouter_api_key") == ""

        # Re-writing an already-encrypted value must not double-seal it.
        ST.set_secret(reloaded, "groq_api_key", reloaded["groq_api_key"])
        assert ST.get_secret(reloaded, "groq_api_key") == "gsk_live_123"
    print("get_secret/set_secret: OK")


def test_account_secret_dotted_key():
    with temp_settings({}):
        s = ST.load_settings()
        # A dotted local part is the case a naive key.split(".") gets wrong.
        key = "smtp_accounts.first.last@example.com.app_password"
        ST.set_secret(s, key, "abcd efgh ijkl mnop")
        assert s["smtp_accounts"][0]["email"] == "first.last@example.com"
        assert SEC.is_encrypted(s["smtp_accounts"][0]["app_password"])
        assert ST.get_secret(s, key) == "abcd efgh ijkl mnop"
        ST.save_settings(s)
        reloaded = ST.load_settings()
        assert ST.get_secret(reloaded, key) == "abcd efgh ijkl mnop"
    print("dotted smtp_accounts secret key: OK")


def test_smtp_accounts_decrypted_and_filtered():
    with temp_settings({}):
        s = ST.load_settings()
        ST.set_secret(s, "smtp_accounts.on@x.com.app_password", "pw-on")
        ST.set_secret(s, "smtp_accounts.off@x.com.app_password", "pw-off")
        s["smtp_accounts"][1]["enabled"] = False
        s["smtp_accounts"].append({"email": "", "enabled": True})

        rows = ST.smtp_accounts(s)
        assert [r["email"] for r in rows] == ["on@x.com"]
        assert rows[0]["app_password"] == "pw-on"
        assert rows[0]["daily_cap"] == ST.SMTP_ACCOUNT_DEFAULTS["daily_cap"]
        # The stored dict must still hold ciphertext after handing out plaintext.
        assert SEC.is_encrypted(s["smtp_accounts"][0]["app_password"])
    print("smtp_accounts decrypt + filter: OK")


# ── AI token budget ──────────────────────────────────────────────────────────

def test_note_ai_tokens_month_rollover():
    with temp_settings({}):
        s = ST.load_settings()
        s["ai_tokens_month"] = "2000-01"
        s["ai_tokens_used"] = 500_000

        ST.note_ai_tokens(s, 120)
        assert s["ai_tokens_month"] == ST._current_month()
        assert s["ai_tokens_used"] == 120, "stale month was not reset"

        ST.note_ai_tokens(s, 80)
        assert s["ai_tokens_used"] == 200

        reloaded = ST.load_settings()
        assert reloaded["ai_tokens_used"] == 200      # persisted
        assert reloaded["ai_tokens_month"] == ST._current_month()
    print("note_ai_tokens month rollover: OK")


def test_ai_budget_left():
    with temp_settings({}):
        s = ST.load_settings()
        s["ai_monthly_token_cap"] = 1000
        s["ai_tokens_month"] = ST._current_month()
        s["ai_tokens_used"] = 400
        assert ST.ai_budget_left(s) == 600

        s["ai_tokens_used"] = 5000
        assert ST.ai_budget_left(s) == 0               # never negative

        # A stale month means the counter is about to reset — full budget.
        s["ai_tokens_month"] = "2000-01"
        assert ST.ai_budget_left(s) == 1000
    print("ai_budget_left: OK")


def test_corrupt_settings_file_falls_back_to_defaults():
    with temp_settings():
        with open(ST.SETTINGS_PATH, "w", encoding="utf-8") as f:
            f.write("{not json at all")
        s = ST.load_settings()
    assert s["max_limit_cap"] == ST.DEFAULT_SETTINGS["max_limit_cap"]
    assert s["dry_run"] is True
    print("corrupt settings.json: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL SETTINGS/SECRETS TESTS PASSED")
