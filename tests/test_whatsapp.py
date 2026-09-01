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

    def __init__(self, text: str = "", png: bytes = b"", on_click=None,
                 attrs=None) -> None:
        self.text = text
        self.screenshot_as_png = png
        self.attrs = dict(attrs or {})
        self._on_click = on_click
        self.clicks = 0
        self.keys: list = []
        self.clears = 0

    def click(self) -> None:
        self.clicks += 1
        if self._on_click is not None:
            self._on_click()

    def send_keys(self, value) -> None:
        self.keys.append(value)
        if self._on_click is not None:
            self._on_click()

    def clear(self) -> None:
        self.clears += 1

    def get_attribute(self, name):
        """Selenium returns None for an attribute a node does not carry, and the
        transport has to read that as "this build moved it" rather than as text."""
        return self.attrs.get(name)


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


def _qr_page(ref: str = ""):
    """The login screen. `ref` is WhatsApp's own QR payload in `data-ref`.

    Blank by default so the tests written before the QR was encoded here still
    exercise the screenshot fallback, which is exactly what it is for.
    """
    attrs = {"data-ref": ref} if ref else {}
    return {"div[data-ref]": [_Element(png=b"\x89PNG-qr", attrs=attrs)]}


def _ready_page():
    return {"#pane-side": [_Element(text="Chats")]}


def _dialog(text: str):
    return {"div[data-testid='popup-contents']": [_Element(text=text)]}


# A payload shaped like the real thing: WhatsApp's `data-ref` is four
# comma-separated base64 fields. Nothing here talks to WhatsApp, so it only has
# to be the right *shape* — the encoder does not care what the string says.
_REF = ("2@nHkPuVdG5hK1qYbTz3RwLcXmJfSoAeIu9NpQrWvB0dCyEgZlMhTaKjXn4FsOiU7,"
        "kL9mQpR2sT4uV6wX8yZ0aB1cD3eF5gH7iJ9kL1mN3o=,"
        "pQ2rS4tU6vW8xY0zA1bC3dE5fG7hI9jK1lM3nO5pQ7r=,1")


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
    # Inverted, deliberately, and the spec line it came from is superseded.
    # `docs/WHATSAPP_SPEC.md` §2 shipped this False with the note "the QR needs
    # a visible window the first time" — and the first half of that is still
    # true and is now enforced somewhere better than a default: `start()` opens
    # a window whenever `has_login` says there is nothing to restore, whatever
    # this setting says. What the False was really paying for was a QR that only
    # rendered in a visible browser, and that is fixed — the QR is encoded from
    # `data-ref` now and is identical either way. So the remaining question is
    # what a *restored* session should do, and the answer is not "put a Chrome
    # window over whatever the user is doing, once per campaign".
    "wa_headless": True,
    "wa_idle_close_sec": 600,
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
    # The one relationship the idle close depends on. An interval at or below
    # the longest gap the pacer can pick would quit the browser between two
    # ordinary sends and make a page load a per-message cost instead of a
    # per-campaign one — which is the opposite of what closing it is for.
    assert settings["wa_idle_close_sec"] > settings["wa_max_gap_sec"], (
        "the browser would be torn down between two ordinary sends")
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


# ── The login QR ─────────────────────────────────────────────────────────────

def _png_pixels(png: bytes):
    """(width, height, rows of greyscale bytes), parsed back out of the PNG.

    Read here rather than trusted, and with the stdlib rather than with the
    writer's own helpers: a rasteriser checked by calling itself proves nothing.
    A QR that is off by a row, inverted, or missing its quiet zone is still a
    perfectly valid PNG, and the only way to catch that is to look at pixels.
    """
    import struct
    import zlib

    assert png[:8] == b"\x89PNG\r\n\x1a\n", png[:8]
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        tag, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            assert (depth, colour) == (8, 0), "8-bit greyscale, not %r" % ((depth, colour),)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width + 1
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0, "only filter type 0 is written"
        rows.append(list(line[1:]))
    assert len(rows) == height, (len(rows), height)
    return width, height, rows


def test_the_qr_is_encoded_from_whatsapps_payload_not_photographed():
    """The headless QR did not scan, and this is why.

    The QR used to be an element screenshot of the `<canvas>` the web client
    paints into, and a headless Chrome renders that canvas blank or at the wrong
    scale — so the one login that most needs no window was the one that could
    not be completed. WhatsApp publishes the payload in `data-ref`; encoded from
    that, the image does not depend on a renderer at all.
    """
    driver = _Driver(page=_qr_page(_REF))
    with _session(driver) as session:
        session.start()
        assert session.status() == "qr"
        png = session.qr_png()
        assert png.startswith(b"\x89PNG\r\n\x1a\n"), png[:8]
        assert png != b"\x89PNG-qr", "the screenshot was taken after all"
        assert session.qr_payload() == _REF
        # And it is the QR *for that payload*, not merely an image.
        assert png == WA.qr_png_of(_REF)
        modules = session.qr_modules()
        assert modules and len(modules) == len(modules[0]), "a QR is square"
    print("the QR is encoded from data-ref, not screenshotted: OK")


def test_the_qr_image_is_black_on_white_with_a_real_quiet_zone():
    """A themed or crowded QR is a QR a phone will not lock onto.

    Three things a camera needs and a hand-rolled rasteriser gets wrong: two
    tones and not an anti-aliased gradient, dark modules on light and not the
    inverse, and the four-module quiet zone that lets the finder patterns be
    found at all. Measured off the pixels, module by module.
    """
    png = WA.qr_png_of(_REF)
    modules = WA.qr_matrix_of(_REF)
    side, scale = len(modules), WA.QR_MODULE_PX
    width, height, rows = _png_pixels(png)

    assert width == height == side * scale, (width, height, side, scale)
    assert {value for row in rows for value in row} == {0, 255}, (
        "a QR is two tones; anything between them is a blurred module edge")

    quiet = WA.QR_QUIET_MODULES * scale
    assert quiet > 0
    for y in range(quiet):
        assert set(rows[y]) == {255}, "the top quiet zone has ink in it"
        assert set(rows[height - 1 - y]) == {255}, "the bottom quiet zone has ink"
    for row in rows:
        assert set(row[:quiet]) == {255} and set(row[-quiet:]) == {255}

    # Every module, at its own centre, is the colour the matrix says it is.
    for my, module_row in enumerate(modules):
        for mx, dark in enumerate(module_row):
            pixel = rows[my * scale + scale // 2][mx * scale + scale // 2]
            assert pixel == (0 if dark else 255), (mx, my, dark, pixel)

    # The top-left finder pattern, which is what a scanner hunts for first: a
    # 7×7 dark square with a light ring and a 3×3 dark core, at the quiet zone.
    q = WA.QR_QUIET_MODULES
    assert all(modules[q][q + i] for i in range(7)), "no finder pattern"
    assert modules[q + 2][q + 2] and not modules[q + 1][q + 1]
    print("the QR is %dx%d px, %d modules, black-on-white, %d-module quiet zone: OK"
          % (width, height, side, WA.QR_QUIET_MODULES))


def test_the_qr_is_the_same_image_hidden_or_visible():
    """Which is the entire point of not screenshotting it.

    Two sessions on the same payload: one whose element screenshot comes back as
    a capture, one whose comes back empty the way a headless canvas does. The
    bytes the phone is asked to read have to be identical, because the payload
    is identical.
    """
    def scene(png_bytes):
        return {"div[data-ref]": [_Element(png=png_bytes,
                                           attrs={"data-ref": _REF})]}

    visible, hidden = _Driver(page=scene(b"\x89PNG-a-real-capture")), _Driver(page=scene(b""))
    with _session(visible) as one, _session(hidden) as two:
        one.start()
        two.start()
        assert one.qr_png() == two.qr_png() != b""
        assert one.qr_payload() == two.qr_payload() == _REF
    print("the QR is byte-identical hidden or visible: OK")


def test_the_qr_falls_back_to_the_screenshot_when_data_ref_is_gone():
    """WhatsApp renames things. A build without `data-ref` still has to log in.

    The fallback is the old path, blur and all — worse than the encoded QR and
    much better than no QR, which is what a hard dependency on one attribute
    would leave behind on the week they rename it.
    """
    driver = _Driver(page=_qr_page())            # no data-ref anywhere
    with _session(driver) as session:
        session.start()
        assert session.status() == "qr"
        assert session.qr_png() == b"\x89PNG-qr", "the screenshot fallback is gone"
        assert session.qr_payload() == ""
        assert session.qr_modules() == []
    print("a missing data-ref falls back to the screenshot: OK")


def test_the_raster_agrees_with_the_qr_librarys_own_renderer():
    """The check the pixel test above cannot make: is it the right way round.

    A transposed or mirrored QR is still square, still two-tone, still has three
    finder patterns and still has its quiet zone — and no phone will read it.
    Nothing in the shape of the image gives that away, so the module positions
    are compared against `qrcode`'s own SVG output, which is a renderer this
    module does not share a line of code with and whose QRs are known to scan.
    `qrcode.image.svg` is pure Python, so this stays an offline test.
    """
    import io
    import re

    import qrcode
    import qrcode.image.svg as svg_factory

    drawn = qrcode.make(_REF, image_factory=svg_factory.SvgImage,
                        border=WA.QR_QUIET_MODULES,
                        error_correction=qrcode.constants.ERROR_CORRECT_L)
    buffer = io.BytesIO()
    drawn.save(buffer)
    theirs = {(int(x), int(y)) for x, y in
              re.findall(r'x="(\d+)mm" y="(\d+)mm"', buffer.getvalue().decode("utf-8"))}
    assert theirs, "the reference renderer drew nothing; the check is vacuous"

    modules = WA.qr_matrix_of(_REF)
    ours = {(x, y) for y, row in enumerate(modules)
            for x, dark in enumerate(row) if dark}
    assert ours == theirs, "the matrix is transposed or mirrored"

    scale = WA.QR_MODULE_PX
    _width, height, rows = _png_pixels(WA.qr_png_of(_REF))
    painted = {(x // scale, y // scale) for y in range(height)
               for x, value in enumerate(rows[y]) if value == 0}
    assert painted == theirs, "the raster does not match the modules it drew"
    print("%d modules agree with qrcode's own renderer: OK" % len(theirs))


def test_an_unencodable_payload_is_a_blank_qr_and_not_a_raise():
    assert WA.qr_png_of("") == b""
    assert WA.qr_matrix_of("") == []
    assert WA.qr_png_of(None) == b""
    assert WA.qr_png_of("x" * 5000) == b"", "past QR capacity, refused not raised"
    print("an unencodable QR payload returns empty: OK")


# ── Linking with a code ──────────────────────────────────────────────────────

def _pairing_driver(code: str = "ABCD1234"):
    """The login screen, with WhatsApp's "Log in with phone number" behind it.

    Rendered the way the client does: the code arrives one character to a node,
    which is why the reader squeezes out everything that is not alphanumeric
    before it decides whether it has eight characters.
    """
    driver = _Driver(page=_qr_page(_REF))
    field = _Element()

    def show_code():
        driver.page["div[aria-details*='link-device-phone-number-code']"] = [
            _Element(text="\n".join(code))]

    next_button = _Element(text="Next", on_click=show_code)

    def offer_field():
        driver.page["input[type='tel']"] = [field]
        driver.page["div[role='button']"] = [next_button]

    entry = _Element(text="Log in with phone number", on_click=offer_field)
    driver.page["div[role='button']"] = [entry]
    return driver, field, entry, next_button


def test_a_pairing_code_is_the_other_way_in():
    """The answer to a QR that will not scan, and quicker than fetching a phone."""
    driver, field, entry, next_button = _pairing_driver()
    with _session(driver, default_region="CA") as session:
        session.start()
        assert session.status() == "qr"

        code, error = session.request_pairing_code("(416) 555-0142")
        assert (code, error) == ("ABCD1234", ""), (code, error)
        assert entry.clicks == 1 and next_button.clicks == 1
        # The number goes in as the full international form the client parses,
        # and it went through `to_wa_id` first like every other number here.
        assert field.keys == ["+14165550142"], field.keys

        assert session.status() == "pairing"
        assert session.pairing_code() == "ABCD1234"
        assert session.pairing_phone() == "14165550142"
        assert 0 < session.pairing_expires_in() <= WA.PAIRING_CODE_TTL_SEC

        # A code on screen is not a login. Sending has to keep saying so.
        ok, why = session.send("(416) 555-0143", "hello")
        assert ok is False and why.startswith("AUTH:"), why
    print("a pairing code is asked for and read back: OK")


def test_a_pairing_code_expires_and_another_can_be_asked_for():
    """It dies on this app's clock, deliberately early — see PAIRING_CODE_TTL_SEC.

    A code that has quietly expired is worse than no code: the user walks to
    their phone, types eight characters, and is told they are wrong.
    """
    driver, _field, entry, next_button = _pairing_driver()
    with _session(driver, default_region="CA") as session:
        session.start()
        assert session.request_pairing_code("+14165550142")[0] == "ABCD1234"
        clock = session._clock_for_test

        clock.sleep(WA.PAIRING_CODE_TTL_SEC / 2)
        assert session.pairing_code() == "ABCD1234", "not expired yet"
        clock.sleep(WA.PAIRING_CODE_TTL_SEC)
        assert session.pairing_code() == "", "an expired code must not be shown"
        assert session.pairing_expires_in() == 0.0
        assert session.pairing_phone() == ""

        # And the state underneath comes back, so the card offers the QR again.
        session._poll_once()
        assert session.status() == "qr"

        # Another is one call, and does not walk the entry screen a second time
        # because the client is already sitting on the number field.
        again, error = session.request_pairing_code("+14165550142")
        assert (again, error) == ("ABCD1234", ""), error
        assert entry.clicks == 1, "the flow was re-entered from the start"
        assert next_button.clicks == 2
        assert session.status() == "pairing"
    print("a pairing code expires and another can be asked for: OK")


def test_the_pairing_code_reader_takes_the_code_and_not_the_word_beside_it():
    """"WhatsApp" is eight letters, and squashed it is the shape of a code.

    The reader squeezes out spacing and hyphens to survive three different
    renderings of the same eight characters, and that squeeze is what makes a
    word on the screen indistinguishable from an answer. A candidate with a
    digit in it wins; an all-letter one is still taken when it is all there is,
    because an all-letter code is rare rather than impossible.
    """
    for rendering in ("ABCD1234", "ABCD-1234", "ABCD 1234", "\n".join("ABCD1234"),
                      "Enter this code on your phone\nABCD 1234"):
        driver, _field, _entry, _next = _pairing_driver()
        with _session(driver, default_region="CA") as session:
            session.start()
            driver.page["div[aria-details*='link-device-phone-number-code']"] = [
                _Element(text=rendering)]
            assert session._read_pairing_code() == "ABCD1234", rendering

    driver = _Driver(page=_qr_page(_REF))
    with _session(driver) as session:
        session.start()
        driver.page["div[aria-details*='link-device-phone-number-code']"] = [
            _Element(text="WhatsApp\nAB7D1234")]
        assert session._read_pairing_code() == "AB7D1234", "the word won"
        driver.page["div[aria-details*='link-device-phone-number-code']"] = [
            _Element(text="ABCDEFGH")]
        assert session._read_pairing_code() == "ABCDEFGH", (
            "an all-letter code is rare, not impossible")
        driver.page["div[aria-details*='link-device-phone-number-code']"] = [
            _Element(text="not a code at all")]
        assert session._read_pairing_code() == ""
    print("the pairing code reader prefers the code over the word: OK")


def test_a_pairing_code_refuses_a_number_it_would_have_to_guess():
    """Same refusal as `send`, and for a weaker reason than `send`'s.

    A misread number here cannot message anybody — the code has to be typed on
    the phone that owns the number, so the worst case is a pairing that never
    happens. It is refused anyway, because a user who is told "no country code"
    once learns it for the send path too.
    """
    driver, _field, entry, _next = _pairing_driver()
    with _session(driver) as session:               # no default_region
        session.start()
        code, error = session.request_pairing_code("(416) 555-0142")
        assert code == "" and error.startswith("RECIPIENT:"), error
        assert "region" in error.lower(), error
        assert entry.clicks == 0, "the client was driven before the number was read"

        assert session.request_pairing_code("")[1].startswith("RECIPIENT:")
    print("a pairing code refuses an unqualified number: OK")


def test_a_pairing_code_is_refused_when_there_is_nothing_to_pair():
    driver = _Driver(page=_ready_page())
    with _session(driver, default_region="CA") as session:
        # Already connected: asking would link a second number onto a profile
        # that Chrome only has one login slot for.
        session.start()
        code, error = session.request_pairing_code("+14165550142")
        assert code == "" and "already connected" in error, error

    cold = _session(_Driver(page=_qr_page(_REF)), default_region="CA")
    code, error = cold.request_pairing_code("+14165550142")
    assert code == "" and error.startswith("CONN:"), error
    cold.close()
    print("a pairing code is refused when there is nothing to pair: OK")


def test_a_pairing_screen_that_never_appears_is_a_sentence_not_a_raise():
    driver = _Driver(page=_qr_page(_REF))           # no pairing entry at all
    with _session(driver, default_region="CA") as session:
        session.start()
        code, error = session.request_pairing_code("+14165550142")
        assert code == "" and error.startswith("OTHER:"), error
        assert "QR" in error, "the user is left without the other option named"
    print("a missing pairing flow degrades to a sentence: OK")


# ── Logging out ──────────────────────────────────────────────────────────────

def _linked_driver():
    """A logged-in client whose menu can actually be driven to Log out."""
    driver = _Driver(page=_ready_page())
    confirm = _Element(text="Log out")

    def confirmed():
        driver.page = _qr_page(_REF)

    confirm._on_click = confirmed

    def chose_log_out():
        driver.page.pop("div[role='menuitem']", None)
        driver.page["div[role='button']"] = [confirm]

    item = _Element(text="Log out", on_click=chose_log_out)

    def opened():
        driver.page["div[role='menuitem']"] = [item]

    menu = _Element(text="", on_click=opened)
    driver.page["span[data-icon='menu']"] = [menu]
    return driver, menu, item, confirm


def _plant_login(profile: str = "default") -> str:
    """Write the marker that makes `has_login` true, and return the directory."""
    directory = WA.state_dir(profile)
    leveldb = os.path.join(directory, "Default", "Local Storage", "leveldb")
    os.makedirs(leveldb, exist_ok=True)
    with open(os.path.join(leveldb, "000003.log"), "wb") as handle:
        handle.write(b"not really a leveldb")
    return directory


def test_log_out_unlinks_the_client_and_then_wipes_the_saved_session():
    """Disconnect keeps the login. This is the one that lets a number change.

    Both halves are checked because they answer different questions: driving the
    client is what stops the phone listing this machine under Linked Devices,
    and clearing the directory is what makes the next connect a fresh QR.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            driver, menu, item, confirm = _linked_driver()
            session = _session(driver)
            session.start()
            directory = _plant_login()
            assert WA.has_login("default") is True

            ok, message = session.log_out()
            assert ok is True, message
            assert (menu.clicks, item.clicks, confirm.clicks) == (1, 1, 1)
            assert "linked device" in message, message
            assert driver.quits == 1, "the browser is quit as well"
            assert not os.path.exists(directory), directory
            assert WA.has_login("default") is False
            assert session.status() == "offline"
            assert session.me() == ""
        finally:
            ST.SETTINGS_DIR = original
    print("log out unlinks the client and wipes the session: OK")


def test_log_out_still_clears_the_login_when_the_client_cannot_be_driven():
    """The directory is what decides, so a renamed menu cannot strand a user.

    WhatsApp renames its menu; a log out that could only work by driving the UI
    would be a log out that stopped working on their schedule, leaving the only
    way to switch numbers a directory deleted by hand.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            driver = _Driver(page=_ready_page())     # ready, and no menu at all
            session = _session(driver)
            session.start()
            directory = _plant_login()

            ok, message = session.log_out()
            assert ok is True, message
            assert not os.path.exists(directory)
            # And it says so, rather than implying the phone was told.
            assert "Linked Devices" in message, message
            assert "could not be logged out" in message, message
        finally:
            ST.SETTINGS_DIR = original
    print("log out clears the login with no client to drive: OK")


def test_log_out_never_removes_anything_but_its_own_session_directory():
    """This function deletes a tree. The refusals are the whole of the test.

    Two layers, and both are checked. The profile name is scrubbed on the way in
    so a hostile one cannot name a path outside; and the resolved path is then
    required to be a direct child of the app's own `wa-session` folder, so a
    junction, a symlink, or a bug upstream in `state_dir` still cannot reach a
    user's files.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            keep = os.path.join(tmp, "settings.json")
            with open(keep, "w", encoding="utf-8") as handle:
                handle.write("{}")
            neighbour = os.path.join(tmp, "wa-session-elsewhere")
            os.makedirs(neighbour, exist_ok=True)

            for hostile in ("../../escape", r"..\..\escape", "a/b", "", ".", ".."):
                WA._clear_state_dir(hostile, sleep=lambda _s: None)
                assert os.path.exists(keep), hostile
                assert os.path.exists(neighbour), hostile

            # And the guard itself, with `state_dir` made to lie the way a bug
            # or a symlink would.
            outside = os.path.join(tmp, "not-a-session")
            os.makedirs(outside, exist_ok=True)
            os.makedirs(os.path.join(tmp, "wa-session"), exist_ok=True)
            saved = WA.state_dir
            try:
                for lie in (outside, os.path.join(tmp, "wa-session"), tmp):
                    WA.state_dir = lambda profile="default", _p=lie: _p
                    ok, why = WA._clear_state_dir("default", sleep=lambda _s: None)
                    assert ok is False, lie
                    assert "refused" in why, why
                    assert os.path.exists(lie), lie
            finally:
                WA.state_dir = saved

            # A link where the session directory should be is refused rather
            # than followed, whatever it points at. Windows only lets some
            # accounts create one, so where it cannot the case is reported as
            # unexercised instead of quietly passing.
            # Pointed at a directory that *would* have passed every other check,
            # so the refusal can only be coming from it being a link.
            real = os.path.join(tmp, "wa-session", "real")
            os.makedirs(real, exist_ok=True)
            link = os.path.join(tmp, "wa-session", "linked")
            try:
                os.symlink(real, link, target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError) as why:
                print("  (symlink case not exercised: %s)" % why)
            else:
                WA.state_dir = lambda profile="default", _p=link: _p
                try:
                    ok, refusal = WA._clear_state_dir("default", sleep=lambda _s: None)
                    assert ok is False and "refused" in refusal, refusal
                    assert os.path.exists(real), "a link was followed and deleted"
                finally:
                    WA.state_dir = saved
                    os.unlink(link)

            # A directory that is not there is success, because success means
            # "no old login is left", not "something was deleted".
            assert WA._clear_state_dir("default", sleep=lambda _s: None) == (True, "")
        finally:
            ST.SETTINGS_DIR = original
    print("log out refuses every path but its own session directory: OK")


def test_log_out_is_how_a_restricted_number_gets_swapped_out():
    """The one place the ban latch is cleared, and the reason it may be.

    `BANNED:` is sticky because continuing on a restricted number is how a
    temporary block becomes permanent. But the latch is a fact about a *number*,
    and this call is the user throwing that number's login away — leaving it set
    would mean a restricted account could never be replaced without restarting
    the app, which is exactly the corner this whole function exists to open up.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            driver = _Driver(page=_ready_page())
            driver.on_get = lambda drv, url: drv.page.update(
                _dialog("Your phone number is banned from using WhatsApp."))
            session = _session(driver, default_region="CA")
            session.start()
            assert session.send("(416) 555-0142", "hello")[1].startswith("BANNED:")
            assert session.banned is True
            assert session.start()[0] is False, "still latched"

            _plant_login()
            ok, message = session.log_out()
            assert ok is True, message
            assert session.banned is False
            assert session.status() == "offline"
            # And the session is usable again, on whatever number is linked next.
            fresh = _Driver(page=_qr_page(_REF))
            session._new_driver = lambda directory, headless: fresh
            assert session.start() == (True, "")
            assert session.status() == "qr"
            session.close()
        finally:
            ST.SETTINGS_DIR = original
    print("log out is how a restricted number is swapped out: OK")


# ── Headless, and not sitting there as a browser ─────────────────────────────

def test_headless_is_the_default_once_there_is_a_login_to_restore():
    """The old default was False on folklore. This is the shape that replaces it.

    `headless=True` means "hidden whenever it can be", not "hidden always": a
    profile with nothing to restore is opened with a window whatever the setting
    says, because a first link needs one. What changed is the *restore*, which
    is every start after the first, and which used to put a Chrome window over
    whatever the user was doing once per campaign.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            seen = []

            def build(profile, **kwargs):
                driver = _Driver(page=_ready_page())
                return WA.WhatsAppSession(
                    profile=profile, poll_sec=60.0,
                    driver_factory=lambda directory, headless: (
                        seen.append(headless) or driver), **kwargs)

            assert WA.WhatsAppSession().headless is True, "the constructor default"

            first = build("default")
            assert first.start() == (True, "")
            assert seen == [False], "a first link must be watchable"
            assert first.running_headless() is False
            first.close()

            _plant_login("default")
            assert WA.has_login("default") is True
            restored = build("default")
            assert restored.start() == (True, "")
            assert seen == [False, True], "a restored session must not need a window"
            assert restored.running_headless() is True
            restored.close()
            assert restored.running_headless() is False, "no browser, nothing hidden"

            # And the switch still pins a window open for watching the client.
            pinned = build("default", headless=False)
            assert pinned.start() == (True, "")
            assert seen == [False, True, False]
            pinned.close()
        finally:
            ST.SETTINGS_DIR = original
    print("headless is the default once a login exists: OK")


def test_has_login_needs_more_than_chrome_having_run_once():
    """Chrome writes `Default/` on its first run, so its existence proves nothing.

    A false yes costs a hidden browser that comes up on the QR screen, which the
    UI then reports; a false no costs a window nobody needed. Neither loses a
    login — but a `Default/` that means "logged in" would make the first ever
    connect headless, which is the one that cannot be.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            assert WA.has_login("default") is False, "nothing on disk"
            os.makedirs(os.path.join(WA.state_dir("default"), "Default"),
                        exist_ok=True)
            assert WA.has_login("default") is False, "Chrome ran; nobody logged in"
            empty = os.path.join(WA.state_dir("default"), "Default",
                                 "Local Storage", "leveldb")
            os.makedirs(empty, exist_ok=True)
            assert WA.has_login("default") is False, "an empty store is not a login"
            _plant_login("default")
            assert WA.has_login("default") is True
            # Per profile, so one number's login says nothing about another's.
            assert WA.has_login("second") is False
        finally:
            ST.SETTINGS_DIR = original
    print("has_login needs a non-empty store, not just a Default folder: OK")


def test_an_idle_session_puts_the_browser_down_and_the_next_send_picks_it_up():
    """A campaign that is not sending must not be a Chrome sitting open.

    The login is on disk, so the cost of being wrong about the interval is a
    page load and never a scan — which is what makes this safe to do by default.
    `status()` stays `"ready"` throughout, because from the user's side nothing
    has happened and there is nothing for them to do.
    """
    warm = _Driver(page=_ready_page())
    reopened = _Driver(page=_ready_page())
    composer = _Element(text="typed")
    button = _Element(on_click=lambda: setattr(composer, "text", ""))

    def open_chat(drv, url):
        drv.page = dict(_ready_page())
        drv.page["div[contenteditable='true'][data-tab='10']"] = [composer]
        drv.page["button[aria-label='Send']"] = [button]

    reopened.on_get = open_chat
    drivers = [warm, reopened]

    session = WA.WhatsAppSession(
        default_region="CA", poll_sec=60.0, send_timeout=5.0, idle_close_sec=30.0,
        driver_factory=lambda directory, headless: drivers.pop(0))
    clock = _Clock()
    session._clock, session._sleep = clock, clock.sleep
    try:
        assert session.start() == (True, "")
        assert session.browser_running() is True
        assert session._should_idle_close() is False, "nothing has gone idle yet"

        clock.sleep(31.0)
        assert session.idle_for() >= 30.0
        assert session._should_idle_close() is True
        session._idle_close()

        assert warm.quits == 1, "the idle browser was left running"
        assert session.browser_running() is False
        assert session.status() == "ready", (
            "an asleep browser is not a logged-out one, and telling the user to "
            "go and scan a QR would be a lie")

        ok, error = session.send("(416) 555-0142", "hello")
        assert (ok, error) == (True, ""), error
        assert drivers == [], "the send did not reopen the browser"
        assert session.browser_running() is True
        assert "phone=14165550142" in reopened.urls[-1]
    finally:
        session.close()
    print("an idle session closes its browser and a send reopens it: OK")


def test_a_session_that_was_never_open_is_not_opened_by_a_send():
    """Only a session that went idle is woken. A cold one stays a `CONN:`.

    Opening a browser on the strength of a queued message would turn a campaign
    started against no connection into one that silently launches Chrome behind
    the user — and on this channel, launching a browser is the act that puts a
    cold-outreach client on their own number.
    """
    opened = []
    session = WA.WhatsAppSession(
        default_region="CA", poll_sec=60.0,
        driver_factory=lambda directory, headless: opened.append(1))
    session._set_state(status="ready")               # as if it were connected
    ok, error = session.send("(416) 555-0142", "hello")
    assert ok is False and error.startswith("CONN:"), error
    assert opened == [], "a send opened a browser nobody asked for"
    session.close()
    print("a never-opened session is not opened by a send: OK")


def test_the_poller_is_what_actually_puts_an_idle_browser_down():
    """The decision above, made by the thread that really makes it.

    Driven on the wall clock at a tenth of a second rather than on the fake one,
    because the bug this catches is a poll loop that computes the right answer
    and never runs — and a stubbed clock cannot tell those apart.
    """
    import time as _time

    driver = _Driver(page=_ready_page())
    session = WA.WhatsAppSession(
        poll_sec=0.02, idle_close_sec=0.05,
        driver_factory=lambda directory, headless: driver)
    try:
        assert session.start() == (True, "")
        started = _time.perf_counter()
        while session.browser_running() and _time.perf_counter() - started < 5.0:
            _time.sleep(0.02)
        elapsed = _time.perf_counter() - started
        assert session.browser_running() is False, (
            "the poller never put the idle browser down")
        assert driver.quits == 1
        assert session.status() == "ready"
    finally:
        session.close()
    print("the poller closed an idle browser after %.2fs: OK" % elapsed)


def test_the_idle_close_is_off_while_anything_is_in_flight():
    """Ready is the only state it may fire from.

    Closing on a QR throws away the code the user is walking back to scan;
    closing on `"loading"` interrupts a restore; closing on `"pairing"` kills a
    code mid-type. Each of those reads to the user as the feature being broken.
    """
    driver, _field, _entry, _next = _pairing_driver()
    session = WA.WhatsAppSession(
        default_region="CA", poll_sec=60.0, send_timeout=5.0, idle_close_sec=10.0,
        driver_factory=lambda directory, headless: driver)
    clock = _Clock()
    session._clock, session._sleep = clock, clock.sleep
    try:
        session.start()
        clock.sleep(60.0)
        assert session.status() == "qr"
        assert session._should_idle_close() is False, "closed on a QR"

        # Asking for a code counts as use, so the clock is walked past the
        # interval again — otherwise every assertion below would pass for the
        # uninteresting reason that nothing had gone idle yet.
        session.request_pairing_code("+14165550142")
        clock.sleep(20.0)
        assert session.status() == "pairing"
        assert session.idle_for() >= 10.0
        assert session._should_idle_close() is False, "closed on a pairing code"

        driver.page = {"div[data-testid='chat-list']": []}   # neither QR nor list
        session._poll_once()
        assert session.status() in ("loading", "pairing")
        session._set_state(status="loading", pair=("", 0.0))
        assert session._should_idle_close() is False, "closed mid-restore"

        driver.page = _ready_page()
        session._poll_once()
        assert session.status() == "ready"
        assert session._should_idle_close() is True, "ready and idle is the case"
    finally:
        session.close()
    print("the idle close only fires from ready: OK")


# ── What all of this costs ───────────────────────────────────────────────────

def test_what_a_session_costs_is_measured_rather_than_assumed():
    """The four numbers the change was asked for, and the two it cannot have.

    Everything here is measured against the stub, because no test in this suite
    may open a real WhatsApp session — the user is logged in on their own
    machine and a stray session could log them out. So these are this module's
    own costs with Chrome taken out of the picture, which is exactly the half
    the change is responsible for; the browser's half is named below and left
    unmeasured rather than guessed at.
    """
    import time as _time
    import tracemalloc

    def timed(call, runs=20):
        best = None
        for _ in range(runs):
            started = _time.perf_counter()
            call()
            spent = _time.perf_counter() - started
            best = spent if best is None else min(best, spent)
        return best * 1000.0

    # 1. Time to first QR — the part that changed. From WhatsApp's payload to
    #    PNG bytes, which is now the whole of the work between the DOM read and
    #    the image on screen.
    encode_ms = timed(lambda: WA.qr_png_of(_REF))

    # 2. Time from a stored session to ready, minus Chrome: the disk check that
    #    decides headless, plus start() plus the wait for the restore.
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            _plant_login("default")
            has_login_ms = timed(lambda: WA.has_login("default"))

            def restore():
                driver = _Driver(page=_ready_page())
                session = WA.WhatsAppSession(
                    poll_sec=60.0,
                    driver_factory=lambda directory, headless: driver)
                session.start()
                session._await_ready()
                session.close()

            restore_ms = timed(restore, runs=5)
        finally:
            ST.SETTINGS_DIR = original

    # 3. Time to send one message on a warm session.
    def warm_send():
        composer = _Element(text="typed")
        button = _Element(on_click=lambda: setattr(composer, "text", ""))
        driver = _Driver(page=_ready_page())
        driver.on_get = lambda drv, url: drv.page.update(
            {"div[contenteditable='true'][data-tab='10']": [composer],
             "button[aria-label='Send']": [button]})
        session = _session(driver, default_region="CA")
        session.start()
        ok, error = session.send("(416) 555-0142", "hello there")
        assert ok, error
        session.close()

    send_ms = timed(warm_send, runs=5)

    # 4. What an idle session holds. The browser is the answer that matters and
    #    after the idle close there is no browser — so what is left is the
    #    Python object, measured rather than described.
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    idle = [WA.WhatsAppSession(profile="p%d" % i, poll_sec=60.0)
            for i in range(50)]
    after = tracemalloc.take_snapshot()
    grew = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    per_session = grew / float(len(idle))
    for session in idle:
        session.close()

    print("\n  time to first QR (payload → PNG)   %8.3f ms" % encode_ms)
    print("  has_login, the headless decision   %8.3f ms" % has_login_ms)
    print("  stored session -> ready, no Chrome %8.3f ms" % restore_ms)
    print("  one send on a warm session         %8.3f ms" % send_ms)
    print("  an idle session, browser closed    %8.0f bytes" % per_session)
    print("  NOT measured here: Chrome's own launch, the page load behind a")
    print("  restore, and an idle browser's RSS — no test may open a real")
    print("  WhatsApp session, so those numbers are the user's to take.")

    # Loose, and only against a regression into something a user would feel.
    assert encode_ms < 200.0, encode_ms
    assert send_ms < 200.0, send_ms
    assert per_session < 200_000, per_session


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL WHATSAPP TESTS PASSED")
