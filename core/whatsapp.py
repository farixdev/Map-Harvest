"""WhatsApp transport — `web.whatsapp.com` driven by Selenium, not an API.

There is no cold-outreach API for WhatsApp, and the Business Cloud API only
delivers pre-approved template messages to numbers that have opted in — which is
the opposite of what this app does. The reference the user pointed at
(`D:\\Projects\\wa-bot`) solves it with `whatsapp-web.js`: Puppeteer drives the
web client, `LocalAuth` keeps the session in a data directory, login is a QR
scan. Right technique, wrong runtime — that is Node, and this app ships as one
Windows `.exe`. So the same thing is done here with Selenium and
`undetected-chromedriver`, which `core/scraper.py` already drives for Google
Maps, and a Chrome user-data-dir standing in for `LocalAuth`. No second runtime,
no new dependency.

**Why every limit here is tighter than the email side.** WhatsApp bans numbers
for bulk outreach far faster than Gmail suspends accounts, there is no CAN-SPAM
equivalent that permits cold contact, and a banned number is usually gone for
good. So `BANNED:` is sticky: once this session has seen a restriction it stops
answering sends at all, without touching the browser again, because continuing
after a restriction is how a temporary block becomes permanent.

Three shapes in here are deliberate and load-bearing:

* **`status()` never touches the browser.** The GUI polls it on a timer; a
  method that waited on Selenium would freeze the window for as long as a page
  load. A daemon thread reads the DOM and publishes a snapshot; `status()`,
  `qr_png()` and `me()` read that snapshot under a different lock than the one
  the driver is held by, so none of them can ever block behind a send.
* **`qr_png()` returns bytes.** The reference prints the QR to a terminal
  because it is a Node CLI; this is a desktop app and the login has to happen
  inside it.
* **Failures come back as strings with the same prefixes `core/mailer.py`
  uses** — `AUTH:`, `RECIPIENT:`, `RATE:`, `BANNED:`, `CONN:`, `OTHER:` — so the
  campaign loop reuses the logic it already has. Nothing here raises across the
  boundary and every wait has a timeout.

Selenium and `undetected_chromedriver` are imported *lazily*, inside the driver
builder. That keeps this module stdlib-only at import time, which is what lets
`core/outreach_db.py` import `phone_key` from here without dragging a browser
stack into a database open — and lets the phone helpers be tested on a machine
with no Chrome installed at all.

An unqualified number is **refused, never guessed**. See `to_wa_id`.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse

from core import settings as _settings

# ── Status vocabulary ────────────────────────────────────────────────────────

OFFLINE = "offline"      # no browser, or it has gone away
QR = "qr"                # waiting for the user to scan
LOADING = "loading"      # authenticated, still syncing
READY = "ready"          # the chat list is up; sends may go
BANNED = "banned"        # the platform has restricted this number

WA_STATUSES = (OFFLINE, QR, LOADING, READY, BANNED)

WA_URL = "https://web.whatsapp.com/"

# The user-data-dir that stands in for the reference's `LocalAuth`. Advertised as
# a constant because the spec names one; the *live* path is resolved by
# `state_dir()` on every call, because the test suite repoints
# `settings.SETTINGS_DIR` after this module has been imported and a path captured
# at import would write a session into a real user profile.
_WA_STATE_DIRNAME = "wa-session"
WA_STATE_DIR = os.path.join(_settings.SETTINGS_DIR, _WA_STATE_DIRNAME)


def state_dir(profile: str = "default") -> str:
    """Where this profile's Chrome user-data-dir lives, resolved now.

    One QR scan has to survive a restart, so this must be a stable directory
    under the app profile rather than a temp dir. The profile name is scrubbed
    of anything that could climb out of it — it reaches here from the UI.
    """
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(profile or "default")).strip("-.")
    return os.path.join(_settings.SETTINGS_DIR, _WA_STATE_DIRNAME, name or "default")


# ── Phone numbers ────────────────────────────────────────────────────────────

# Scraped Maps numbers arrive as "+1 416-555-0142", "(416) 555-0142",
# "0416 555 142", "416.555.0142", and a fair number carry an extension. The
# extension is cut before the digits are read: "416.555.0142 x22" reads as
# 416555014222 otherwise, which is a different number entirely.
#
# There is no closing `\b` after the marker, and that is not an oversight: "x"
# and "2" are both word characters, so `\bx\b22` never matches and "x22" — the
# commonest form of all — went through untouched. Longest alternative first, so
# "extension" is not eaten down to "ext" and left unmatched.
_EXTENSION_RE = re.compile(
    r"(?:[,;]|\b(?:extension|extn|ext|poste|durchwahl|x))\s*\.?\s*\d+\s*$", re.I)
_NON_DIGIT_RE = re.compile(r"\D+")

# E.164 allows 15 digits including the country code. Eight is the floor: no
# country WhatsApp operates in has a national mobile number shorter than that,
# and accepting less would let a room number or a price match a lead.
_MIN_DIGITS = 8
_MAX_DIGITS = 15

# How many trailing digits identify a number for suppression matching. See
# `phone_key` for why it is a tail and why it is exactly eight.
_PHONE_TAIL_DIGITS = 8

# ISO 3166 alpha-2 → country calling code. Deliberately a table and not a
# library: a wrong guess here messages a stranger in another country, which is
# unrecoverable, so a region this table does not know is refused rather than
# approximated.
CALLING_CODES = {
    # North America (NANP)
    "US": "1", "CA": "1", "BS": "1", "BB": "1", "DO": "1", "JM": "1",
    "PR": "1", "TT": "1",
    # Latin America
    "MX": "52", "BR": "55", "AR": "54", "CL": "56", "CO": "57", "PE": "51",
    "VE": "58", "EC": "593", "UY": "598", "PY": "595", "BO": "591",
    "CR": "506", "PA": "507", "GT": "502", "SV": "503", "HN": "504", "NI": "505",
    # Europe
    "GB": "44", "IE": "353", "FR": "33", "DE": "49", "ES": "34", "IT": "39",
    "PT": "351", "NL": "31", "BE": "32", "LU": "352", "CH": "41", "AT": "43",
    "DK": "45", "SE": "46", "NO": "47", "FI": "358", "IS": "354", "PL": "48",
    "CZ": "420", "SK": "421", "HU": "36", "RO": "40", "BG": "359", "GR": "30",
    "HR": "385", "SI": "386", "RS": "381", "UA": "380", "RU": "7", "TR": "90",
    # Middle East and Africa
    "IL": "972", "AE": "971", "SA": "966", "QA": "974", "KW": "965",
    "BH": "973", "OM": "968", "EG": "20", "ZA": "27", "NG": "234", "KE": "254",
    "GH": "233", "MA": "212",
    # Asia and Oceania
    "AU": "61", "NZ": "64", "SG": "65", "MY": "60", "ID": "62", "PH": "63",
    "TH": "66", "VN": "84", "IN": "91", "PK": "92", "BD": "880", "LK": "94",
    "CN": "86", "HK": "852", "JP": "81", "KR": "82", "TW": "886",
}

# NANP has no trunk prefix — the "1" people dial is the country code, so an
# 11-digit number starting with 1 is already qualified and a leading 0 is not
# something to strip.
_NANP = "1"

# Everywhere else a national number is written with a leading trunk 0 that E.164
# does not carry: "0416 555 142" is +61 416 555 142. Italy is the exception the
# rule always trips over — an Italian landline keeps its 0 (+39 06 …) — so it is
# named rather than assumed.
_KEEPS_TRUNK_ZERO = frozenset({"IT"})


def _no_extension(phone) -> str:
    """`phone` with a trailing extension removed. Applied before any digit read."""
    text = str(phone or "").strip()
    while True:
        trimmed = _EXTENSION_RE.sub("", text).strip()
        if trimmed == text:
            return text
        text = trimmed


def digits_of(phone) -> str:
    """Every digit in `phone`, extension dropped. "" for anything unusable."""
    return _NON_DIGIT_RE.sub("", _no_extension(phone))


def _region_code(default_region: str) -> str:
    return CALLING_CODES.get(str(default_region or "").strip().upper(), "")


def to_wa_id(phone: str, default_region: str = "") -> str:
    """`phone` as the bare E.164 digits WhatsApp addresses a chat by. "" refuses.

    An **unqualified number with no `default_region` is refused, never guessed.**
    That is the single most important line in this module. "(416) 555-0142" is a
    Toronto plumber to a Canadian user and a Dallas plumber to a Texan one, and
    the cost of getting it wrong is not a bounce — it is a cold sales message
    landing on a stranger's phone in another country, from a number that then
    gets reported. There is no undo for that, so the app makes the user say
    which region it may assume and tells them which one it is applying.

    Qualification is taken from the number itself first: a leading "+" or the
    "00" international prefix means the country code is already there, and the
    region is not consulted at all. Anything else is read as a *national* number
    in `default_region` — deliberately, and even when its digits happen to open
    with that region's calling code. "9198765432" is a real ten-digit Indian
    mobile and also looks like +91 98765432, and there is no evidence in the
    string that says which; the rule "a number written without a plus is a local
    number" is one the user can be told and can act on, where a length heuristic
    per country is a guess wearing a table.
    """
    raw = _no_extension(phone)
    digits = _NON_DIGIT_RE.sub("", raw)
    if not digits:
        return ""

    # A "+" anywhere ahead of the first digit qualifies the number, not only one
    # at the very start: a scraped listing says "Tel: +1 416-555-0142" as often
    # as it says the number alone, and reading that as a local number would hand
    # it a second country code.
    head = raw[:raw.index(next(c for c in raw if c.isdigit()))]
    if "+" in head:
        return _e164(digits)
    if digits.startswith("00"):
        return _e164(digits[2:])

    region = str(default_region or "").strip().upper()
    code = _region_code(region)
    if not code:
        return ""

    national = digits
    if code == _NANP:
        # 1-416-555-0142 written without a plus is still qualified; there is no
        # trunk 0 to strip here, and stripping one would corrupt the number.
        if len(national) == 11 and national.startswith(_NANP):
            return _e164(national)
        # 011 is NANP's international prefix, the local spelling of "00", and a
        # North American business site writes an overseas number with it.
        # Prefixing a country code to that would invent one.
        if national.startswith("011"):
            return _e164(national[3:])
        # No NANP area code begins with 0 or 1, so this is not a number this
        # region can complete — and completing it anyway would dial a stranger.
        if national[:1] in ("0", "1"):
            return ""
    elif national.startswith("0") and region not in _KEEPS_TRUNK_ZERO:
        # Exactly one digit, not `lstrip("0")`: the trunk prefix is a single 0,
        # and eating a second would silently renumber a subscriber whose own
        # number begins with one.
        national = national[1:]

    # The floor is checked on the national part, not only on the finished
    # string. "555-0142" is a local number nobody can be reached on, and adding
    # a country code to it makes eight digits — long enough to pass a check on
    # the result, and still a number that belongs to nobody.
    if len(national) < _MIN_DIGITS:
        return ""
    return _e164(code + national)


def _e164(digits: str) -> str:
    """Accept `digits` as a wa_id, or refuse it. No country code starts with 0."""
    if not digits or digits.startswith("0"):
        return ""
    if not (_MIN_DIGITS <= len(digits) <= _MAX_DIGITS):
        return ""
    return digits


def is_plausible(phone: str) -> bool:
    """Could this be a phone number at all, before any region is applied.

    The weaker of the two questions. `is_plausible` answers "is this a phone
    number" — what the Leads table filters on — and `to_wa_id` answers "may we
    message it", which needs the region. A number can be perfectly plausible and
    still be unsendable because the user has not said which country it is in.
    """
    return _MIN_DIGITS <= len(digits_of(phone)) <= _MAX_DIGITS


def phone_key(phone: str) -> str:
    """The trailing digits that identify a number across formats. "" if unusable.

    Suppression has to work across the two ways the same number reaches this
    app: scraped off Maps as "(416) 555-0142", and read back off a WhatsApp
    reply as "14165550142". Comparing whole strings misses that pair, and so
    does comparing full digit runs, because one carries a country code and the
    other does not. A fixed-length tail makes it an equality match SQLite can
    index.

    Eight digits is chosen against the asymmetry rather than for elegance. Too
    long and an 8-digit national number (Singapore, Denmark, Hong Kong) never
    matches its own E.164 form, and someone who said stop gets the email
    sequence anyway — the unrecoverable direction. Too short and two different
    numbers collide, and one lead goes uncontacted. Eight clears every national
    format the region table covers, and its worst case is a NANP pair whose area
    codes differ only in the first digit sharing a subscriber number.
    """
    digits = digits_of(phone)
    if len(digits) < _PHONE_TAIL_DIGITS:
        return ""
    return digits[-_PHONE_TAIL_DIGITS:]


def matches_opt_out(text: str, words) -> bool:
    """Does this reply ask to be left alone.

    Matched on word boundaries, not with `in`: "stop" inside "one-stop shop" or
    "stopover" is a business describing itself, and suppressing that lead throws
    away a reply that was probably interest.

    The boundary counts a hyphen and an apostrophe as part of the word, which
    `\\b` does not. "one-stop shop" is the trade this app sells into writing its
    own tagline, and plain `\\b` reads the "stop" in it as an opt-out.
    """
    body = str(text or "").strip().lower()
    if not body:
        return False
    for word in words or ():
        phrase = str(word or "").strip().lower()
        if not phrase:
            continue
        if re.search(r"(?<![\w'-])%s(?![\w'-])" % re.escape(phrase), body):
            return True
    return False


# ── Failure classification ───────────────────────────────────────────────────

# The same prefix vocabulary as core/mailer.py, so core/campaign.py reuses its
# reaction logic. BANNED is the one this module adds and the only one that is
# never retryable: AUTH means stop until the user scans again, RATE means back
# off, RECIPIENT means skip this lead, CONN means it is worth another try, and
# BANNED means stop everything, now and until the user acknowledges it.

_BANNED_PHRASES = (
    "your phone number is banned",
    "phone number is banned",
    "account has been banned",
    "your account has been banned",
    "temporarily banned",
    "you can't use whatsapp",
    "you cannot use whatsapp",
    "violating our terms",
    "violated our terms",
    "account restricted",
    "this account is not allowed",
)

_RATE_PHRASES = (
    "too many messages",
    "sending too fast",
    "try again later",
    "try again in",
    "rate limit",
    "slow down",
    "temporarily unable to send",
)

_RECIPIENT_PHRASES = (
    "phone number shared via url is invalid",
    "invalid phone number",
    "is not on whatsapp",
    "not a valid whatsapp",
    "no whatsapp account",
    "url is invalid",
)

_AUTH_PHRASES = (
    "scan the qr",
    "scan this qr",
    "log in to whatsapp",
    "logged out",
    "session expired",
    "use whatsapp on your phone",
)

_CONN_PHRASES = (
    "computer not connected",
    "phone not connected",
    "no internet",
    "trying to reach phone",
    "reconnecting",
    "timeout",
    "timed out",
    "connection refused",
    "chrome not reachable",
    "session deleted",
    "invalid session id",
    "disconnected",
    "err_",
)


def classify(detail: str, *, default: str = "OTHER") -> str:
    """One failure to one prefixed, human-readable string. Never raises.

    Order matters and is the same argument as `mailer._classify`: a restriction
    notice can also mention trying again later, and reading it as `RATE:` would
    keep the run going straight into a permanent ban.
    """
    text = re.sub(r"[\r\n\t]+", " ", str(detail or "")).strip() or "no detail from WhatsApp"
    low = text.lower()
    if any(p in low for p in _BANNED_PHRASES):
        return "BANNED: %s" % text
    if any(p in low for p in _RECIPIENT_PHRASES):
        return "RECIPIENT: %s" % text
    if any(p in low for p in _AUTH_PHRASES):
        return "AUTH: %s" % text
    if any(p in low for p in _RATE_PHRASES):
        return "RATE: %s" % text
    if any(p in low for p in _CONN_PHRASES):
        return "CONN: %s" % text
    return "%s: %s" % (default, text)


_BANNED_STICKY = (
    "BANNED: WhatsApp has restricted this number. Sending is stopped — every "
    "further attempt makes a temporary block more likely to become permanent. "
    "Open WhatsApp on the phone, check the account status, and reconnect only "
    "once it is clear."
)


# ── The DOM contract ─────────────────────────────────────────────────────────

# Selectors, not a page object model. WhatsApp Web ships a new build most weeks
# and these will drift; each is a tuple of candidates so a rename breaks one
# entry rather than the transport, and every read degrades to "I could not tell"
# instead of raising. Nothing above this line depends on any of it.

_SEL_QR = ("div[data-ref]", "canvas[aria-label*='Scan']", "div[data-testid='qrcode']")
_SEL_READY = ("#pane-side", "div[data-testid='chat-list']", "#side")
_SEL_COMPOSER = ("div[contenteditable='true'][data-tab='10']",
                 "footer div[contenteditable='true']",
                 "div[data-testid='conversation-compose-box-input']")
_SEL_SEND = ("button[aria-label='Send']", "span[data-icon='send']",
             "button[data-testid='compose-btn-send']")
_SEL_DIALOG = ("div[data-testid='popup-contents']", "div[role='dialog']",
               "div[data-animate-modal-body='true']")
_SEL_UNREAD_ROW = ("div[aria-label*='unread'] div[role='listitem']",
                   "#pane-side div[role='listitem']")

# localStorage key the web client keeps the logged-in wid under. Read rather
# than scraped out of the profile menu, which needs a click.
_ME_KEYS = ("last-wid-md", "last-wid")


# ── Session ──────────────────────────────────────────────────────────────────

class WhatsAppSession:
    """One logged-in WhatsApp Web session, reused for every send in a campaign.

    Construct, `start()`, then poll `status()` from the GUI until it reads
    `"ready"`. `start()` does block — it launches Chrome — so the caller runs it
    off the GUI thread; everything the GUI polls afterwards is served from a
    cached snapshot and returns immediately.

    `driver_factory`, `clock` and `sleep` exist so the tests can drive the whole
    state machine against a stub, the way `SmtpSender` is stubbed today. No test
    in this suite opens a real session.
    """

    def __init__(self, profile: str = "default", headless: bool = False, *,
                 default_region: str = "", driver_factory=None,
                 poll_sec: float = 2.0, send_timeout: float = 25.0,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self.profile = str(profile or "default")
        self.headless = bool(headless)
        self.default_region = str(default_region or "").strip().upper()
        self.send_timeout = float(send_timeout) or 25.0
        self.poll_sec = max(0.2, float(poll_sec) or 2.0)

        self._new_driver = driver_factory or _build_driver
        self._clock = clock
        self._sleep = sleep

        # Two locks, and which one covers what is the reason `status()` cannot
        # freeze the GUI. `_driver_lock` is held across every Selenium call —
        # one webdriver session is not thread-safe and a send takes seconds.
        # `_state_lock` is held only across a dict assignment, so a reader never
        # queues behind the browser.
        self._driver_lock = threading.RLock()
        self._state_lock = threading.Lock()

        self._driver = None
        self._status = OFFLINE
        self._qr = b""
        self._me = ""
        self._banned = False
        self._stop = threading.Event()
        self._poller = None

    # ── lifecycle ──

    def start(self) -> tuple[bool, str]:
        """Launch Chrome on the persisted profile and begin polling. (ok, error).

        Idempotent while the session is alive. Blocks; the GUI calls it from a
        worker. A previously scanned QR lives in the user-data-dir, so the usual
        outcome of a restart is `status()` going straight to `"ready"` with no
        scan at all.
        """
        if self._banned:
            return False, _BANNED_STICKY
        with self._driver_lock:
            if self._driver is not None:
                return True, ""
            directory = state_dir(self.profile)
            try:
                os.makedirs(directory, exist_ok=True)
                driver = self._new_driver(directory, self.headless)
                driver.get(WA_URL)
            except Exception as exc:                      # noqa: BLE001 — returned
                self._set_state(status=OFFLINE)
                return False, classify(str(exc) or "Chrome would not start",
                                       default="CONN")
            self._driver = driver
        self._stop.clear()
        self._poll_once()
        self._start_poller()
        return True, ""

    def close(self) -> None:
        """Stop polling and quit the browser. Safe to call twice, never raises."""
        self._stop.set()
        poller, self._poller = self._poller, None
        if poller is not None and poller is not threading.current_thread():
            poller.join(timeout=5.0)
        with self._driver_lock:
            driver, self._driver = self._driver, None
            if driver is not None:
                try:
                    driver.quit()
                except Exception:                          # noqa: BLE001 — shutdown
                    pass
        self._set_state(status=BANNED if self._banned else OFFLINE, qr=b"")

    def __enter__(self) -> "WhatsAppSession":
        # Deliberately does not start, for the reason `SmtpSender.__enter__`
        # does not connect: a start failure has to reach the caller as a return
        # value and `__enter__` has nowhere to put one.
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── what the GUI polls ──

    def status(self) -> str:
        """One of `WA_STATUSES`, from the cached snapshot. Never blocks."""
        with self._state_lock:
            return self._status

    def qr_png(self) -> bytes:
        """The login QR as PNG bytes; `b""` unless `status()` is `"qr"`.

        Bytes rather than a file or a terminal draw: the QR has to render inside
        the app. Captured by the poller so this call, like `status()`, is a
        dictionary read.
        """
        with self._state_lock:
            return self._qr if self._status == QR else b""

    def me(self) -> str:
        """The logged-in number, "" while unknown. Never blocks."""
        with self._state_lock:
            return self._me

    @property
    def banned(self) -> bool:
        """Sticky. Once true this session refuses to send, browser or no browser."""
        return self._banned

    # ── sending ──

    def send(self, phone: str, text: str) -> tuple[bool, str]:
        """Deliver one message. Returns (ok, error); never raises.

        The error prefixes are `core/mailer.py`'s, so the campaign loop reacts
        without knowing which transport it is driving. `BANNED:` latches: the
        next call returns it without opening the browser at all.
        """
        body = str(text or "").strip()
        if self._banned:
            return False, _BANNED_STICKY
        if not body:
            return False, "OTHER: there is no message to send"

        wa_id = to_wa_id(phone, self.default_region)
        if not wa_id:
            return False, self._unsendable(phone)

        state = self.status()
        if state == BANNED:
            return False, self._latch(_BANNED_STICKY)
        if state == QR:
            return False, ("AUTH: WhatsApp is not logged in — scan the QR in "
                           "Settings before sending.")
        if state != READY:
            return False, ("CONN: the WhatsApp session is %s, not ready to send."
                           % (state or OFFLINE))

        with self._driver_lock:
            if self._driver is None:
                return False, "CONN: the WhatsApp browser session is not open."
            try:
                ok, error = self._deliver(wa_id, body)
            except Exception as exc:                       # noqa: BLE001 — returned
                ok, error = False, classify(str(exc), default="CONN")

        if error.startswith("BANNED:"):
            return False, self._latch(error)
        if error.startswith("AUTH:"):
            self._set_state(status=QR)
        return ok, error

    def _unsendable(self, phone: str) -> str:
        """Why this number cannot be addressed, in the words the user needs."""
        if not digits_of(phone):
            return "RECIPIENT: this lead has no phone number."
        if not is_plausible(phone):
            return "RECIPIENT: %r is too short to be a phone number." % str(phone)
        if not self.default_region:
            return ("RECIPIENT: %r has no country code and no default region is "
                    "set, so the country would have to be guessed. Set the "
                    "WhatsApp region in Settings, or store the number with its "
                    "+ prefix." % str(phone))
        return ("RECIPIENT: %r could not be read as a number in %s."
                % (str(phone), self.default_region))

    def _latch(self, error: str) -> str:
        self._banned = True
        self._set_state(status=BANNED, qr=b"")
        return error

    def _deliver(self, wa_id: str, body: str) -> tuple[bool, str]:
        """Drive one send. Caller holds `_driver_lock`.

        The message is handed over in the deep link rather than typed into the
        composer: typing loses newlines and emoji to keystroke translation, and
        every character typed is another chance for the DOM to move underneath
        the send.
        """
        driver = self._driver
        url = "%ssend?phone=%s&text=%s&type=phone_number&app_absent=0" % (
            WA_URL, wa_id, urllib.parse.quote(body))
        driver.get(url)

        composer = self._await(lambda: self._first(_SEL_COMPOSER))
        if composer is None:
            blocked = self._blocking_message()
            if blocked:
                return False, classify(blocked, default="OTHER")
            if self._first(_SEL_QR) is not None:
                return False, "AUTH: WhatsApp logged out mid-run; scan the QR again."
            return False, ("CONN: WhatsApp did not open a chat for %s within %.0fs."
                           % (wa_id, self.send_timeout))

        button = self._first(_SEL_SEND)
        try:
            if button is not None:
                button.click()
            else:
                composer.send_keys("\n")
        except Exception as exc:                           # noqa: BLE001 — returned
            return False, classify(str(exc), default="CONN")

        # A cleared composer is the web client's own acknowledgement that it
        # accepted the message. Waiting for a delivery tick instead would wait
        # on the recipient's phone, which is not this app's business and is a
        # minute away on a cold contact.
        if not self._await(self._composer_cleared):
            blocked = self._blocking_message()
            if blocked:
                return False, classify(blocked, default="RATE")
            return False, ("RATE: WhatsApp accepted no confirmation for %s within "
                           "%.0fs — treating it as throttling rather than "
                           "re-sending." % (wa_id, self.send_timeout))
        return True, ""

    def _composer_cleared(self) -> bool:
        composer = self._first(_SEL_COMPOSER)
        if composer is None:
            return False
        try:
            return not (composer.text or "").strip()
        except Exception:                                  # noqa: BLE001 — a read
            return False

    def _blocking_message(self) -> str:
        """The text of whatever dialog is standing in the way, "" if none."""
        for element in self._all(_SEL_DIALOG):
            try:
                text = (element.text or "").strip()
            except Exception:                              # noqa: BLE001 — a read
                continue
            if text:
                return text
        return ""

    def _await(self, probe):
        """Poll `probe` until it returns something truthy or the timeout runs out.

        Every wait in this module goes through here so that none of them can be
        written without one.
        """
        deadline = self._clock() + self.send_timeout
        while True:
            try:
                found = probe()
            except Exception:                              # noqa: BLE001 — a read
                found = None
            if found:
                return found
            if self._clock() >= deadline:
                return None
            self._sleep(min(self.poll_sec, 1.0))

    # ── replies ──

    def unread_replies(self, since_ts: float) -> list[dict]:
        """Unread chats as `{"wa_id", "phone", "text", "ts"}`. [] when not ready.

        Read off the chat-list previews rather than by opening every thread:
        opening one marks it read, which destroys the only record that a reply
        arrived if the app closes before the reply is acted on, and it costs a
        page load per chat.

        `ts` is when this app observed the reply, not when WhatsApp received it.
        The list shows a wall-clock label ("11:04", "Yesterday") with no epoch
        behind it, and inventing one would put a fabricated timestamp into the
        event log. `since_ts` therefore filters on observation time, and the
        caller dedupes on `wa_id` plus `text` — which is what the opt-out check
        needs anyway.
        """
        floor = float(since_ts or 0.0)
        now = time.time()
        if now < floor or self.status() != READY:
            return []

        out: list[dict] = []
        with self._driver_lock:
            if self._driver is None:
                return []
            for row in self._all(_SEL_UNREAD_ROW):
                try:
                    text = (row.text or "").strip()
                except Exception:                          # noqa: BLE001 — a read
                    continue
                if not text:
                    continue
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                if len(lines) < 2:
                    continue
                wa_id = to_wa_id(lines[0], self.default_region) or digits_of(lines[0])
                out.append({"wa_id": wa_id, "phone": lines[0],
                            "text": lines[-1], "ts": now})
        return out

    # ── the poller ──

    def _start_poller(self) -> None:
        if self._poller is not None and self._poller.is_alive():
            return
        thread = threading.Thread(target=self._poll_loop, name="wa-status",
                                  daemon=True)
        self._poller = thread
        thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.poll_sec):
            self._poll_once()

    def _poll_once(self) -> None:
        """Read the DOM once and publish a snapshot. Never raises, never blocks
        a reader: the driver lock is dropped before the snapshot is written."""
        if self._banned:
            self._set_state(status=BANNED, qr=b"")
            return
        status, qr, me = OFFLINE, b"", None
        with self._driver_lock:
            driver = self._driver
            if driver is not None:
                status, qr, me = self._read_state(driver)
        if status == BANNED:
            self._banned = True
            qr = b""
        self._set_state(status=status, qr=qr, me=me)

    def _read_state(self, driver) -> tuple[str, bytes, str | None]:
        try:
            banner = self._blocking_message() or (driver.page_source or "")[:4000]
        except Exception:                                  # noqa: BLE001 — a read
            return OFFLINE, b"", None
        if classify(banner, default="OTHER").startswith("BANNED:"):
            return BANNED, b"", None

        qr_node = self._first(_SEL_QR)
        if qr_node is not None:
            # A QR means logged out, so the remembered number is stale — "" is
            # the instruction to clear it, where None below means "unchanged".
            return QR, self._qr_bytes(qr_node), ""
        if self._first(_SEL_READY) is not None:
            return READY, b"", self._read_me(driver) or None
        return LOADING, b"", None

    def _qr_bytes(self, node) -> bytes:
        """PNG bytes for the QR element, `b""` if it cannot be captured.

        An element screenshot works on the `<canvas>` the web client draws into;
        `toDataURL` is the fallback for the builds that render an `<svg>`.
        """
        try:
            data = node.screenshot_as_png
            if data:
                return bytes(data)
        except Exception:                                  # noqa: BLE001 — a read
            pass
        try:
            import base64
            encoded = self._driver.execute_script(
                "var c = arguments[0].querySelector('canvas') || arguments[0];"
                "return c.toDataURL ? c.toDataURL('image/png') : '';", node)
            head, _, payload = str(encoded or "").partition(",")
            if payload and "base64" in head:
                return base64.b64decode(payload)
        except Exception:                                  # noqa: BLE001 — a read
            pass
        return b""

    def _read_me(self, driver) -> str:
        try:
            raw = driver.execute_script(
                "for (var i = 0; i < arguments[0].length; i++) {"
                "  var v = window.localStorage.getItem(arguments[0][i]);"
                "  if (v) return v; } return '';", list(_ME_KEYS))
        except Exception:                                  # noqa: BLE001 — a read
            return ""
        return digits_of(str(raw or "").strip('"').split("@")[0].split(":")[0])

    # ── DOM plumbing ──

    def _all(self, selectors) -> list:
        """The first selector in `selectors` that matches anything. [] if none.

        `"css selector"` is spelled out rather than imported as
        `By.CSS_SELECTOR`, which is the same string: importing it would put
        selenium back at module scope and undo the lazy-import contract above.
        """
        driver = self._driver
        if driver is None:
            return []
        for selector in selectors:
            try:
                found = driver.find_elements("css selector", selector)
            except Exception:                              # noqa: BLE001 — a read
                continue
            if found:
                return list(found)
        return []

    def _first(self, selectors):
        found = self._all(selectors)
        return found[0] if found else None

    def _set_state(self, *, status: str, qr: bytes | None = None,
                   me: str | None = None) -> None:
        with self._state_lock:
            self._status = status if status in WA_STATUSES else OFFLINE
            if qr is not None:
                self._qr = qr
            if me is not None:
                self._me = me


# ── Chrome ───────────────────────────────────────────────────────────────────

def _build_driver(user_data_dir: str, headless: bool):
    """A Chrome bound to `user_data_dir`, so one QR scan survives a restart.

    Imported here and not at module scope on purpose. It keeps this module
    stdlib-only to import, which is what lets `core/outreach_db.py` take
    `phone_key` from it without pulling a browser stack into a database open,
    and lets the phone helpers be tested where no Chrome exists.

    The import order mirrors `core/scraper.py`: `distutils_compat` installs its
    `distutils.version` shim on import and has to land before
    `undetected_chromedriver` reads it. A plain import is enough — the shim is a
    side effect of loading the module, and `import *` is a syntax error inside a
    function.
    """
    import core.distutils_compat  # noqa: F401 — installs the shim uc needs

    import undetected_chromedriver as uc

    # Reused rather than copied: matching the installed Chrome's major version
    # is the difference between a driver that starts and one that does not, and
    # two copies of that lookup would drift.
    from core.scraper import _chrome_major_version

    options = uc.ChromeOptions()
    # The session directory *is* the login. Without it every start is a new QR
    # scan, which is the one thing the user must not have to repeat.
    options.add_argument("--user-data-dir=%s" % user_data_dir)
    if headless:
        # Off by default and warned about in the UI: the first scan needs a
        # window, and WhatsApp Web treats a headless client more suspiciously
        # than a visible one.
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_experimental_option("prefs", {"intl.accept_languages": "en-US,en"})

    kwargs = {"options": options, "use_subprocess": True}
    major = _chrome_major_version()
    if major:
        kwargs["version_main"] = major

    driver = uc.Chrome(**kwargs)
    driver.set_window_size(1180, 860)
    driver.set_page_load_timeout(45)
    return driver
