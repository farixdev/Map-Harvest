"""Offline tests for core.whatsapp.

**Nothing here opens a WhatsApp session, a browser, or a socket.** Every session
test drives a scripted stub through `driver_factory`, the way `SmtpSender` is
stubbed on the email side, and the clock is a counter rather than the wall so a
timeout is asserted at a fixed instant instead of slept through. Opening a real
session in a test would put a cold-outreach client on the user's own number,
which is the one thing this channel cannot take back.

The phone tests use the formats Google Maps actually returns, including the ones
that must be refused. The refusals are the point: a number with no country code
and no configured region is not guessed, because a wrong guess messages a
stranger in another country and there is no undo.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import settings as ST  # noqa: E402
from core import whatsapp as WA  # noqa: E402


# ── A scripted browser ───────────────────────────────────────────────────────

class _Element:
    """One DOM node, as much of one as this module ever touches."""

    def __init__(self, text: str = "", png: bytes = b"", on_click=None) -> None:
        self.text = text
        self.screenshot_as_png = png
        self._on_click = on_click
        self.clicks = 0
        self.keys: list = []

    def click(self) -> None:
        self.clicks += 1
        if self._on_click is not None:
            self._on_click()

    def send_keys(self, value) -> None:
        self.keys.append(value)
        if self._on_click is not None:
            self._on_click()


class _Driver:
    """A Selenium driver's surface, scripted. `page` maps selector → elements.

    `on_get` is how a scene reacts to navigation, which is the only thing the
    real client does that the stub has to imitate: `send` drives a deep link and
    then waits for what the page turned into.
    """

    def __init__(self, page=None, page_source: str = "WhatsApp", storage=None) -> None:
        self.page = dict(page or {})
        self.page_source = page_source
        self.storage = dict(storage or {})
        self.urls: list = []
        self.on_get = None
        self.quits = 0
        self.sizes: list = []
        self.timeouts: list = []

    # ── what core.whatsapp calls ──

    def get(self, url: str) -> None:
        self.urls.append(url)
        # Only a chat deep link runs the scene's hook. The initial load of
        # web.whatsapp.com must leave the page as the scene set it, or every
        # test would assert against the state its own `start()` provoked.
        if self.on_get is not None and "send?" in url:
            self.on_get(self, url)

    def find_elements(self, by, selector):
        assert by == "css selector", by
        return list(self.page.get(selector, []))

    def execute_script(self, script, *args):
        if "localStorage" in script:
            for key in args[0]:
                if self.storage.get(key):
                    return self.storage[key]
            return ""
        return ""

    def quit(self) -> None:
        self.quits += 1

    def set_window_size(self, width, height) -> None:
        self.sizes.append((width, height))

    def set_page_load_timeout(self, seconds) -> None:
        self.timeouts.append(seconds)


class _Clock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.01)


def _session(driver: _Driver, **kwargs) -> WA.WhatsAppSession:
    """A session wired to `driver`, with a fake clock and a dormant poller.

    `poll_sec` is a minute so the background thread sleeps through the test and
    the status snapshot only moves when a test asks it to; the thread is still
    really started and really joined, because `close()` deadlocking on it is a
    failure worth catching.
    """
    clock = _Clock()
    session = WA.WhatsAppSession(
        driver_factory=lambda directory, headless: driver,
        poll_sec=60.0, send_timeout=5.0, clock=clock, sleep=clock.sleep, **kwargs)
    session._clock_for_test = clock
    return session


def _qr_page():
    return {"div[data-ref]": [_Element(png=b"\x89PNG-qr")]}


def _ready_page():
    return {"#pane-side": [_Element(text="Chats")]}


def _dialog(text: str):
    return {"div[data-testid='popup-contents']": [_Element(text=text)]}


# ── Phone numbers ────────────────────────────────────────────────────────────

def test_to_wa_id_reads_the_formats_maps_returns():
    """The four shapes a scraped Canadian number actually arrives in."""
    assert WA.to_wa_id("+1 416-555-0142") == "14165550142"
    assert WA.to_wa_id("(416) 555-0142", "CA") == "14165550142"
    assert WA.to_wa_id("416.555.0142", "CA") == "14165550142"
    assert WA.to_wa_id("1 416 555 0142", "CA") == "14165550142"
    assert WA.to_wa_id("  +1 (416) 555-0142  ") == "14165550142"
    # A listing that labels the number still carries its plus, and the plus is
    # what says the country code is already there.
    assert WA.to_wa_id("Tel: +1 416-555-0142") == "14165550142"
    assert WA.to_wa_id("Phone (+61) 416 555 142") == "61416555142"

    # The "00" international prefix qualifies a number without any region at
    # all. NANP's "011" does not, and is not treated as though it did: 011 is
    # not part of E.164 and reading it as one would invent a country code 11.
    assert WA.to_wa_id("0061 416 555 142") == "61416555142"
    assert WA.to_wa_id("011 61 416 555 142") == "", "no region, nothing to apply"
    # With a NANP region, 011 is read as the international prefix it is rather
    # than being handed a second country code on top.
    assert WA.to_wa_id("011 61 416 555 142", "CA") == "61416555142"
    assert WA.to_wa_id("011 44 20 7946 0958", "US") == "442079460958"

    # The trunk zero outside NANP is not part of the number.
    assert WA.to_wa_id("0416 555 142", "AU") == "61416555142"
    assert WA.to_wa_id("020 7946 0958", "GB") == "442079460958"
    assert WA.to_wa_id("+44 20 7946 0958") == "442079460958"
    print("to_wa_id across real Maps formats: OK")


def test_to_wa_id_refuses_an_unqualified_number_rather_than_guessing():
    """The single most consequential refusal in the module.

    "(416) 555-0142" is a Toronto plumber to a Canadian user and a Dallas one to
    a Texan. Guessing would put a cold sales message on a stranger's phone in
    another country, from a number that then gets reported and, usually,
    permanently banned. So with no region there is no answer.
    """
    for number in ("(416) 555-0142", "416.555.0142", "0416 555 142",
                   "4165550142", "020 7946 0958"):
        assert WA.to_wa_id(number) == "", number
        assert WA.to_wa_id(number, "") == "", number
        assert WA.to_wa_id(number, "   ") == "", number

    # A region the table does not know is refused too — an unknown ISO code is
    # not a licence to fall back on some default country.
    assert WA.to_wa_id("(416) 555-0142", "ZZ") == ""
    assert WA.to_wa_id("(416) 555-0142", "XK") == ""

    # But the same numbers resolve the moment the user says where they are.
    assert WA.to_wa_id("(416) 555-0142", "CA") == "14165550142"
    assert WA.to_wa_id("0416 555 142", "AU") == "61416555142"
    print("unqualified numbers refused, never guessed: OK")


def test_to_wa_id_strips_extensions_before_reading_digits():
    """A scraped extension is not part of the number and never was."""
    assert WA.to_wa_id("+1 416-555-0142 x22") == "14165550142"
    assert WA.to_wa_id("+1 416-555-0142 ext. 305") == "14165550142"
    assert WA.to_wa_id("(416) 555-0142 ext 7", "CA") == "14165550142"
    assert WA.to_wa_id("+1 416-555-0142, 400") == "14165550142"
    assert WA.digits_of("416.555.0142 x22") == "4165550142"
    print("extensions stripped before the digits are read: OK")


def test_to_wa_id_refuses_what_cannot_be_a_number():
    assert WA.to_wa_id("") == ""
    assert WA.to_wa_id(None) == ""
    assert WA.to_wa_id("call us!", "CA") == ""
    assert WA.to_wa_id("555-0142", "CA") == "", "seven digits is not a mobile"
    assert WA.to_wa_id("+0 416 555 0142") == "", "no country code starts with 0"
    assert WA.to_wa_id("+" + "9" * 20) == "", "E.164 stops at fifteen digits"
    # No NANP area code opens with 0 or 1, so there is nothing to complete here
    # and a completed guess would dial somebody.
    assert WA.to_wa_id("0416 555 142", "CA") == ""
    print("unusable numbers refused: OK")


def test_is_plausible_is_the_weaker_question():
    # `is_plausible` asks "is this a phone number"; `to_wa_id` asks "may we
    # message it". A number can be the first and not the second.
    assert WA.is_plausible("(416) 555-0142") is True
    assert WA.to_wa_id("(416) 555-0142") == ""
    assert WA.is_plausible("0416 555 142") is True
    assert WA.is_plausible("555-0142") is False
    assert WA.is_plausible("") is False
    assert WA.is_plausible("no phone") is False
    print("is_plausible vs to_wa_id: OK")


def test_phone_key_matches_the_same_number_written_two_ways():
    """Suppression's whole job: Maps' format and a WhatsApp reply's must agree."""
    scraped = WA.phone_key("(416) 555-0142")
    replied = WA.phone_key("14165550142")
    assert scraped and scraped == replied, (scraped, replied)
    assert WA.phone_key("+1 416-555-0142") == scraped
    assert WA.phone_key("416.555.0142 x22") == scraped

    # And across the trunk-zero and country-code forms outside NANP.
    assert WA.phone_key("0416 555 142") == WA.phone_key("+61 416 555 142")
    assert WA.phone_key("020 7946 0958") == WA.phone_key("+44 20 7946 0958")
    # An eight-digit national number still matches its own E.164 form, which is
    # why the tail is eight digits and not nine.
    assert WA.phone_key("9123 4567") == WA.phone_key("+65 9123 4567")

    # Different numbers stay different.
    assert WA.phone_key("(416) 555-0142") != WA.phone_key("(416) 555-0143")
    assert WA.phone_key("(416) 555-0142") != WA.phone_key("(647) 555-0142")
    assert WA.phone_key("555-0142") == "", "too short to identify anyone"
    assert WA.phone_key("") == ""
    print("phone_key matches one number across formats: OK")


def test_matches_opt_out_is_word_boundaried():
    words = ST.DEFAULT_SETTINGS["wa_opt_out_words"]
    assert WA.matches_opt_out("STOP", words) is True
    assert WA.matches_opt_out("please stop messaging me", words) is True
    assert WA.matches_opt_out("Unsubscribe.", words) is True
    assert WA.matches_opt_out("remove me from this list", words) is True
    # A business describing itself is not an opt-out, and treating it as one
    # throws away a reply that was probably interest.
    assert WA.matches_opt_out("we're a one-stop shop for tyres", words) is False
    assert WA.matches_opt_out("we are by the bus stopover", words) is False
    assert WA.matches_opt_out("", words) is False
    assert WA.matches_opt_out("stop", []) is False
    print("opt-out matching is word-boundaried: OK")


# ── Settings ─────────────────────────────────────────────────────────────────

# The contract from docs/WHATSAPP_SPEC.md §2, quoted rather than read out of the
# module it is checking. A default that drifts here is not a style change: every
# one of these numbers is a decision about how fast a number gets banned.
_SPEC_DEFAULTS = {
    "wa_enabled": False,
    "wa_default_region": "",
    "wa_headless": False,
    "wa_daily_cap": 30,
    "wa_hourly_cap": 8,
    "wa_min_gap_sec": 90,
    "wa_max_gap_sec": 300,
    "wa_warmup_enabled": True,
    "wa_warmup_start": 5,
    "wa_warmup_step": 3,
    "wa_warmup_max": 30,
    "wa_send_days": [0, 1, 2, 3, 4],
    "wa_send_start_hour": 10,
    "wa_send_end_hour": 19,
    "wa_followup_enabled": True,
    "wa_followup_gap_days": 3,
    "wa_followup_max_steps": 1,
    "wa_dry_run": True,
    "wa_opt_out_words": ["stop", "unsubscribe", "remove me", "do not message"],
}


def test_the_whatsapp_defaults_are_the_spec_and_are_tighter_than_email():
    for key, value in _SPEC_DEFAULTS.items():
        assert key in ST.DEFAULT_SETTINGS, key
        assert ST.DEFAULT_SETTINGS[key] == value, (key, ST.DEFAULT_SETTINGS[key], value)

    # Not decoration. WhatsApp bans a number for bulk outreach far faster than
    # Gmail suspends an account and a banned number is usually gone for good, so
    # every limit that has an email counterpart has to be the stricter of the two.
    settings = ST.DEFAULT_SETTINGS
    assert settings["wa_daily_cap"] < settings["daily_cap_per_account"]
    assert settings["wa_hourly_cap"] < settings["hourly_cap_per_account"]
    assert settings["wa_min_gap_sec"] > settings["send_min_gap_sec"]
    assert settings["wa_max_gap_sec"] > settings["send_max_gap_sec"]
    assert settings["wa_warmup_start"] < settings["warmup_start"]
    assert settings["wa_warmup_step"] < settings["warmup_step"]
    assert settings["wa_warmup_max"] < settings["warmup_max"]
    assert settings["wa_followup_max_steps"] < settings["followup_max_steps"]
    assert settings["wa_send_start_hour"] > settings["send_start_hour"], (
        "a message at 08:00 reads worse than an email does")
    assert settings["wa_dry_run"] is True, "a fresh install never surprise-sends"
    assert settings["wa_default_region"] == "", (
        "blank is what makes an unqualified number a refusal, not a guess")
    print("WhatsApp defaults match the spec and are tighter than email: OK")


def test_an_older_settings_file_gains_the_whatsapp_keys():
    """The deep merge is what upgrades a user in place.

    A settings.json written before this channel existed has to come back with
    every WhatsApp key at its default and every value the user chose untouched.
    """
    import json

    original = (ST.SETTINGS_DIR, ST.SETTINGS_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        ST.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        try:
            with open(ST.SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump({"theme": "light", "daily_cap_per_account": 25,
                           "send_days": [0, 1, 2]}, handle)

            loaded = ST.load_settings()
            for key, value in _SPEC_DEFAULTS.items():
                assert loaded[key] == value, (key, loaded.get(key))
            assert loaded["theme"] == "light"
            assert loaded["daily_cap_per_account"] == 25
            assert loaded["send_days"] == [0, 1, 2]

            # And they survive a round trip, so the upgrade is not re-applied
            # on every load.
            loaded["wa_default_region"] = "CA"
            loaded["wa_daily_cap"] = 12
            ST.save_settings(loaded)
            again = ST.load_settings()
            assert again["wa_default_region"] == "CA"
            assert again["wa_daily_cap"] == 12
            assert again["wa_opt_out_words"] == _SPEC_DEFAULTS["wa_opt_out_words"]
        finally:
            ST.SETTINGS_DIR, ST.SETTINGS_PATH = original
    print("an older settings file gains the WhatsApp keys: OK")


# ── Failure classification ───────────────────────────────────────────────────

def test_classify_uses_the_mailer_prefixes():
    assert WA.classify("Your phone number is banned from using WhatsApp."
                       ).startswith("BANNED:")
    assert WA.classify("Phone number shared via url is invalid."
                       ).startswith("RECIPIENT:")
    assert WA.classify("Too many messages, try again later").startswith("RATE:")
    assert WA.classify("Scan the QR code to log in").startswith("AUTH:")
    assert WA.classify("chrome not reachable").startswith("CONN:")
    assert WA.classify("something new").startswith("OTHER:")
    assert WA.classify("something new", default="CONN").startswith("CONN:")
    assert WA.classify("") == "OTHER: no detail from WhatsApp"
    print("classify prefixes match core.mailer: OK")


def test_a_ban_notice_that_also_says_try_again_later_reads_as_banned():
    """Order matters, for the same reason it does in `mailer._classify`.

    A restriction notice frequently invites the user to try again later. Read as
    `RATE:` the campaign backs off and resumes, which is exactly how a temporary
    block becomes the permanent one that costs the number.
    """
    notice = ("Your account has been banned for violating our terms. "
              "Try again later or contact support.")
    assert WA.classify(notice).startswith("BANNED:"), WA.classify(notice)
    print("BANNED outranks RATE: OK")


# ── Session state ────────────────────────────────────────────────────────────

def test_status_reports_the_login_states_without_blocking():
    driver = _Driver(page=_qr_page())
    with _session(driver) as session:
        assert session.status() == "offline", "nothing is open yet"
        assert session.qr_png() == b""

        ok, error = session.start()
        assert (ok, error) == (True, ""), error
        assert driver.urls == [WA.WA_URL]
        assert session.status() == "qr"
        assert session.qr_png() == b"\x89PNG-qr", "the QR renders inside the app"

        # Scanned: the chat list replaces the QR, and the number comes off the
        # web client's own storage rather than a click through the profile menu.
        driver.page = _ready_page()
        driver.storage = {"last-wid-md": '"14165550142@c.us"'}
        session._poll_once()
        assert session.status() == "ready"
        assert session.me() == "14165550142"
        assert session.qr_png() == b"", "no QR to show once it is scanned"
    assert driver.quits == 1, "close() quits the browser"
    print("status/qr_png/me across the login states: OK")


def test_status_never_waits_on_the_browser():
    """The GUI polls `status()` on a timer; a blocking read freezes the window.

    Measured rather than asserted from the design: the driver is held for a
    quarter of a second and `status()` is called throughout. It is served from
    the snapshot under a different lock, so it must not queue behind that.
    """
    import threading
    import time as _time

    driver = _Driver(page=_ready_page())
    with _session(driver) as session:
        session.start()
        held = threading.Event()
        release = threading.Event()

        def hog():
            with session._driver_lock:
                held.set()
                release.wait(2.0)

        thread = threading.Thread(target=hog, daemon=True)
        thread.start()
        assert held.wait(2.0), "the lock was never taken"

        started = _time.perf_counter()
        for _ in range(200):
            session.status()
            session.qr_png()
            session.me()
        elapsed = _time.perf_counter() - started
        release.set()
        thread.join(2.0)

    assert elapsed < 0.25, (
        "600 snapshot reads took %.3fs while the driver was held; status() is "
        "queueing behind the browser and would freeze the GUI" % elapsed)
    print("600 status reads under a held driver lock: %.4fs — OK" % elapsed)


def test_a_ban_notice_latches_and_stops_everything():
    """`BANNED:` is not retryable and must halt the run and every later one.

    Continuing after a restriction is how a temporary block becomes permanent,
    so the session refuses without touching the browser again — the assertion
    that `driver.urls` does not grow is the whole test.
    """
    driver = _Driver(page=_ready_page())

    def banned_on_send(drv, url):
        drv.page = _dialog("Your phone number is banned from using WhatsApp.")

    driver.on_get = banned_on_send
    with _session(driver, default_region="CA") as session:
        session.start()
        assert session.status() == "ready"

        ok, error = session.send("(416) 555-0142", "hello")
        assert ok is False
        assert error.startswith("BANNED:"), error
        assert session.status() == "banned"
        assert session.banned is True

        urls = len(driver.urls)
        again = session.send("(416) 555-0143", "hello")
        assert again[0] is False and again[1].startswith("BANNED:"), again
        assert len(driver.urls) == urls, "a banned session must not open a chat"

        # And it survives a restart attempt: start() refuses too.
        assert session.start()[0] is False
        assert session.status() == "banned"
    print("BANNED latches and halts everything: OK")


def test_send_classifies_an_unreachable_recipient_without_stopping_the_run():
    driver = _Driver(page=_ready_page())
    driver.on_get = lambda drv, url: drv.page.update(
        _dialog("Phone number shared via url is invalid."))
    with _session(driver, default_region="CA") as session:
        session.start()
        ok, error = session.send("(416) 555-0142", "hello")
        assert ok is False
        assert error.startswith("RECIPIENT:"), error
        # RECIPIENT skips one lead; the session stays usable.
        assert session.status() == "ready"
        assert session.banned is False
    print("RECIPIENT skips one lead: OK")


def test_send_delivers_through_the_deep_link():
    driver = _Driver(page=_ready_page())
    composer = _Element(text="Hi Acme — noticed your site has no booking form.")

    def cleared():
        composer.text = ""

    button = _Element(on_click=cleared)

    def open_chat(drv, url):
        drv.page = dict(_ready_page())
        drv.page["div[contenteditable='true'][data-tab='10']"] = [composer]
        drv.page["button[aria-label='Send']"] = [button]

    driver.on_get = open_chat
    with _session(driver, default_region="CA") as session:
        session.start()
        ok, error = session.send("(416) 555-0142", "Hi Acme — no booking form?")
        assert (ok, error) == (True, ""), error
        assert button.clicks == 1

        sent_url = driver.urls[-1]
        assert "phone=14165550142" in sent_url, sent_url
        # The body travels in the link rather than being typed: keystrokes lose
        # newlines and emoji, and every one is another chance for the DOM to move.
        assert "Hi%20Acme" in sent_url, sent_url
        assert composer.keys == []
    print("send delivers through the deep link: OK")


def test_send_treats_an_unconfirmed_message_as_throttling():
    """No confirmation is `RATE:`, never a silent retry.

    Re-sending a message WhatsApp may already have accepted double-messages a
    cold contact, which is the complaint that gets a number reported.
    """
    driver = _Driver(page=_ready_page())
    stuck = _Element(text="still here")
    driver.on_get = lambda drv, url: drv.page.update(
        {"div[contenteditable='true'][data-tab='10']": [stuck],
         "button[aria-label='Send']": [_Element()]})
    with _session(driver, default_region="CA") as session:
        session.start()
        ok, error = session.send("(416) 555-0142", "hello")
        assert ok is False
        assert error.startswith("RATE:"), error
        assert len(driver.urls) == 2, "one navigation to WhatsApp, one to the chat"
    print("an unconfirmed send reads as RATE, not a retry: OK")


def test_send_refuses_before_it_opens_anything_when_it_cannot():
    driver = _Driver(page=_qr_page())
    with _session(driver) as session:
        # No session at all.
        ok, error = session.send("+14165550142", "hello")
        assert ok is False and error.startswith("CONN:"), error

        session.start()
        assert session.status() == "qr"
        ok, error = session.send("+14165550142", "hello")
        assert ok is False and error.startswith("AUTH:"), error

        # An unqualified number with no region never reaches the browser, and
        # the error says what the user has to do about it.
        driver.page = _ready_page()
        session._poll_once()
        ok, error = session.send("(416) 555-0142", "hello")
        assert ok is False and error.startswith("RECIPIENT:"), error
        assert "region" in error.lower(), error
        assert len(driver.urls) == 1, "only the initial load"

        assert session.send("+14165550142", "   ")[1].startswith("OTHER:")
    print("send refuses early rather than opening a chat: OK")


def test_a_logout_mid_run_reads_as_auth_not_a_dead_session():
    driver = _Driver(page=_ready_page())
    driver.on_get = lambda drv, url: drv.page.update(_qr_page())
    with _session(driver, default_region="CA") as session:
        session.start()
        ok, error = session.send("(416) 555-0142", "hello")
        assert ok is False and error.startswith("AUTH:"), error
        assert session.status() == "qr", "the UI must offer the QR again"
    print("a mid-run logout reads as AUTH: OK")


def test_a_driver_that_raises_never_escapes_the_boundary():
    """Nothing here may raise into a worker thread; every failure is a string."""

    class _Exploding(_Driver):
        def find_elements(self, by, selector):
            raise RuntimeError("chrome not reachable: session deleted")

        def get(self, url):
            super().get(url)
            raise RuntimeError("chrome not reachable")

    driver = _Exploding()
    session = _session(driver, default_region="CA")
    ok, error = session.start()
    assert ok is False and error.startswith("CONN:"), error
    assert session.status() == "offline"
    assert session.send("+14165550142", "hello")[1].startswith("CONN:")
    assert session.unread_replies(0.0) == []
    session.close()
    session.close()          # idempotent, and still does not raise
    print("a failing driver never raises across the boundary: OK")


def test_start_is_idempotent_and_uses_a_persisted_profile_directory():
    """One QR scan has to survive a restart, which is what the directory is for."""
    seen = []
    driver = _Driver(page=_ready_page())
    session = WA.WhatsAppSession(
        profile="default",
        driver_factory=lambda directory, headless: (
            seen.append((directory, headless)) or driver),
        poll_sec=60.0)
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            assert session.start() == (True, "")
            assert session.start() == (True, ""), "starting twice reuses the browser"
            assert len(seen) == 1, seen
            directory, headless = seen[0]
            assert os.path.isdir(directory), directory
            assert os.path.abspath(directory).startswith(os.path.abspath(tmp))
            assert headless is False, "the first scan needs a visible window"
        finally:
            session.close()
            ST.SETTINGS_DIR = original
    print("start() is idempotent and persists the session directory: OK")


def test_state_dir_follows_a_redirected_profile_and_cannot_escape_it():
    """Resolved late, and scrubbed.

    Late, because the test suite repoints `settings.SETTINGS_DIR` after import
    and a path captured at import would write a live WhatsApp session into a
    real user profile. Scrubbed, because the profile name reaches this from the
    UI and a path separator in it would climb out of the app directory.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            here = os.path.abspath(WA.state_dir("default"))
            assert here.startswith(os.path.abspath(tmp)), here
            for hostile in ("../../escape", r"..\..\escape", "a/b", ""):
                resolved = os.path.abspath(WA.state_dir(hostile))
                assert resolved.startswith(os.path.abspath(tmp)), (hostile, resolved)
        finally:
            ST.SETTINGS_DIR = original
    print("state_dir is late-resolved and cannot escape the profile: OK")


def test_unread_replies_reads_the_list_without_opening_threads():
    driver = _Driver(page=_ready_page())
    with _session(driver, default_region="CA") as session:
        session.start()
        driver.page["#pane-side div[role='listitem']"] = [
            _Element(text="+1 416-555-0142\n11:04\nSTOP"),
            _Element(text="+1 416-555-0143\n11:07\nsounds interesting, call me"),
            _Element(text="no preview"),
        ]
        replies = session.unread_replies(0.0)
        assert [r["text"] for r in replies] == ["STOP", "sounds interesting, call me"]
        assert replies[0]["wa_id"] == "14165550142"
        assert all(r["ts"] > 0 for r in replies)
        assert driver.urls == [WA.WA_URL], "no thread was opened"

        words = ST.DEFAULT_SETTINGS["wa_opt_out_words"]
        assert WA.matches_opt_out(replies[0]["text"], words) is True
        assert WA.matches_opt_out(replies[1]["text"], words) is False
    print("unread_replies reads previews only: OK")


def test_importing_the_transport_pulls_in_no_browser_stack():
    """The phone helpers have to work where no Chrome and no selenium exist.

    `core.outreach_db` imports `phone_key` from here on every database open. If
    this module reached for selenium at import time, opening the outreach store
    would drag a browser stack in with it — and the phone tests above could not
    run on a machine without Chrome.
    """
    import importlib

    module = importlib.import_module("core.whatsapp")
    with open(module.__file__, encoding="utf-8") as handle:
        source = handle.read()
    # Column zero only: an import inside a function is exactly the lazy one
    # this is checking for, and must not be counted as a violation of it.
    top_level = [line for line in source.splitlines()
                 if line.startswith(("import ", "from "))]
    assert top_level, "no imports found; the check would pass vacuously"
    for line in top_level:
        assert "selenium" not in line, line
        assert "undetected" not in line, line
        assert "PyQt5" not in line, line
    print("core.whatsapp imports no browser stack at module scope: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL WHATSAPP TESTS PASSED")
