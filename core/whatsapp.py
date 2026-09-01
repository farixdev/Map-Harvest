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

**The QR is drawn, not photographed.** It used to be an element screenshot of
the `<canvas>` the web client paints into, and a headless Chrome renders that
canvas blank or at the wrong scale — so the one login that most needs to work
without a window was the one that could not be scanned. WhatsApp puts the QR's
*payload* in the `data-ref` attribute of the login container, so the payload is
read and the QR is encoded here, from the `qrcode` package, and rasterised by a
few lines of `zlib` into a PNG. Black modules on white with a four-module quiet
zone whatever the app theme is, because a themed QR does not scan. That makes
the image byte-identical headless or not, which is the entire point. The
screenshot survives only as the fallback for a build that stops publishing
`data-ref`.

**Linking with a code is a first-class path, not a fallback.** A QR needs a
second device pointed at this screen; `request_pairing_code` asks the web client
for the eight characters that are typed on the phone instead, which is both the
answer to a QR that will not scan and quicker than fetching a phone. The code is
short-lived on WhatsApp's side, so `status()` gains `"pairing"`, the code is
readable only while it lasts, and asking for another is one call.

**Logging out is a different act from disconnecting.** `close()` quits the
browser and keeps the login, which is what a campaign wants between runs.
`log_out()` is for changing which number this is: it logs out inside the web
client where it can, so the phone stops listing this machine as a linked device,
and then deletes the profile session directory so the next connect starts clean.
It removes a tree, so it refuses to touch anything that is not exactly this
profile's own directory under the app's `wa-session` folder.

**An idle session does not sit there as a browser.** After
`idle_close_sec` with nothing sending, the poller quits Chrome and keeps the
login; the next `send()` reopens it and waits for the restore. A stored session
costs a page load, not a scan, and an idle campaign costs no browser at all.

Selenium and `undetected_chromedriver` are imported *lazily*, inside the driver
builder, and `qrcode` and `zlib` the same way inside the QR encoder. That keeps
this module stdlib-only at import time, which is what lets
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
PAIRING = "pairing"      # waiting for a pairing code to be typed on the phone
LOADING = "loading"      # authenticated, still syncing
READY = "ready"          # logged in; sends may go — the browser may be asleep
BANNED = "banned"        # the platform has restricted this number

WA_STATUSES = (OFFLINE, QR, PAIRING, LOADING, READY, BANNED)

# `READY` says "a send placed now will go", not "a window is open". After
# `idle_close_sec` of nothing sending the browser is quit and the login left on
# disk; the session stays `READY` and the next `send()` reopens Chrome and waits
# for the restore. Reporting `OFFLINE` there would be the more literal answer
# and the less true one — it would put "not connected, scan the QR" in front of
# a user whose login is fine and who has to do nothing at all.

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


# Where Chrome puts the two stores the web client keeps a login in, relative to
# a user-data-dir. `--user-data-dir=X` with no `--profile-directory` means the
# profile is `X/Default`.
_LOGIN_MARKERS = (
    os.path.join("Default", "IndexedDB",
                 "https_web.whatsapp.com_0.indexeddb.leveldb"),
    os.path.join("Default", "Local Storage", "leveldb"),
)


def has_login(profile: str = "default") -> bool:
    """Has this profile ever been linked. A heuristic, and named as one.

    It answers "is it worth starting hidden", not "is the login still valid" —
    only WhatsApp can answer the second, and the phone can revoke a linked
    device at any moment without this directory changing. That asymmetry is why
    the heuristic is safe in the direction it is used: a false *yes* costs a
    hidden browser that comes up on the QR screen, which `start()` sees and the
    UI reports; a false *no* costs a visible window nobody needed. Neither loses
    a login, and neither sends anything.

    Chrome writes `Default/` on its very first run, so the directory existing
    proves nothing; the markers are the two stores the web client actually keeps
    its credentials in, and they have to be non-empty.
    """
    root = state_dir(profile)
    for marker in _LOGIN_MARKERS:
        path = os.path.join(root, marker)
        try:
            if os.path.isdir(path) and os.listdir(path):
                return True
        except OSError:
            continue
    return False


# ── The login QR ─────────────────────────────────────────────────────────────

# The QR is encoded here from WhatsApp's own payload rather than screenshotted
# off the page. See the module docstring for why; these are the numbers.
#
# Ten pixels a module is deliberately generous. The image is scaled down by
# whatever widget shows it, and downscaling a QR loses module edges — starting
# large means the scaled copy still has whole modules in it. Four modules of
# quiet zone is the spec's own minimum and the thing most home-made QRs omit;
# without it a camera cannot find the finder patterns against a dark app theme.
QR_MODULE_PX = 10
QR_QUIET_MODULES = 4

# Black on white, always, and not from `ui/theme.py`. A QR drawn in the app's
# accent colour on the app's surface colour is a QR a phone will not lock onto:
# scanners threshold on luminance and expect dark-on-light. This is the one
# image in the app that is not allowed to follow the theme.
_QR_DARK = b"\x00"
_QR_LIGHT = b"\xff"


def qr_matrix_of(payload: str) -> list:
    """`payload` as QR modules — rows of booleans, quiet zone included.

    `[]` for anything that cannot be encoded, so a caller never has to catch.
    Published rather than kept private because a widget that can paint modules
    directly gets a crisper QR at its own size than one rescaling a PNG.
    """
    text = str(payload or "").strip()
    if not text:
        return []
    try:
        import qrcode                       # pure Python; no image library
    except Exception:                       # noqa: BLE001 — a missing package
        return []
    try:
        code = qrcode.QRCode(border=QR_QUIET_MODULES,
                             error_correction=qrcode.constants.ERROR_CORRECT_L)
        code.add_data(text)
        code.make(fit=True)
        return [[bool(cell) for cell in row] for row in code.get_matrix()]
    except Exception:                       # noqa: BLE001 — an unencodable payload
        return []


def qr_png_of(payload: str, module_px: int = QR_MODULE_PX) -> bytes:
    """`payload` as a scannable PNG. `b""` when it cannot be encoded.

    The error-correction level is L, the same as the web client's own. It is not
    a compatibility question — any valid QR carrying the same string scans the
    same — it is a size one: L spends the fewest modules on the payload, so at a
    fixed image size each module is the largest, and module size is what decides
    whether a phone camera locks on across a room.
    """
    return _png_of_matrix(qr_matrix_of(payload), module_px)


def _png_of_matrix(matrix, module_px: int) -> bytes:
    """An 8-bit greyscale PNG of `matrix`, written by hand. `b""` if empty.

    Hand-written because the alternative is Pillow, and this app ships as one
    Windows `.exe` — a 3 MB imaging library to draw a grid of squares is not a
    trade worth making. `zlib` and `struct` are stdlib and a PNG of flat squares
    is four chunks. Greyscale rather than 1-bit for the sake of every loader
    that has ever mishandled a sub-byte depth; zlib flattens the difference.
    """
    if not matrix:
        return b""
    import struct
    import zlib

    scale = max(1, int(module_px or 1))
    side = len(matrix) * scale
    raw = bytearray()
    for row in matrix:
        line = bytearray()
        for cell in row:
            line += (_QR_DARK if cell else _QR_LIGHT) * scale
        for _ in range(scale):
            raw.append(0)                   # filter type 0: none
            raw += line

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


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

# Pairing. The entry point and the confirm are buttons identified by their
# words, not by a selector — WhatsApp renames test ids far more often than it
# renames a button in the UI, and a label the user can read on screen is a
# contract this code can be debugged against. `_SEL_CLICKABLE` narrows the scan;
# `_words_saying` picks out of it.
_SEL_CLICKABLE = ("div[role='button']", "button", "div[role='menuitem']")
_PAIR_ENTRY_WORDS = ("log in with phone number", "login with phone number",
                     "link with phone number")
_PAIR_NEXT_WORDS = ("next", "continue")
_SEL_PAIR_PHONE = ("input[type='tel']",
                   "input[aria-label*='phone number']",
                   "input[aria-label*='Phone number']",
                   "form input[type='text']")
_SEL_PAIR_CODE = ("div[aria-details*='link-device-phone-number-code']",
                  "div[data-testid='link-device-phone-number-code-screen-instructions']",
                  "div[aria-label*='pairing code']")

# Logging out. Same argument: the menu opens from an icon whose `data-icon` name
# is stable-ish, and the item inside it is found by its words.
_SEL_MENU = ("span[data-icon='menu']", "div[data-testid='menu']",
             "button[aria-label='Menu']", "div[title='Menu']",
             "span[data-icon='menu-alt']")
_LOG_OUT_WORDS = ("log out", "logout", "sign out")

# localStorage key the web client keeps the logged-in wid under. Read rather
# than scraped out of the profile menu, which needs a click.
_ME_KEYS = ("last-wid-md", "last-wid")

# Eight characters, and the client renders them spaced or hyphenated or one to a
# node, so everything that is not alphanumeric is squeezed out before the match.
_PAIR_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")

# How long a pairing code is treated as good for. WhatsApp does not publish the
# number and the DOM does not always carry a countdown, so this is deliberately
# *shorter* than the code's real life rather than a guess at it: erring short
# tells the user to ask for a fresh code slightly early, and erring long lets
# them walk to their phone and type eight characters that have already died —
# which reads as "this feature is broken" rather than "that code expired".
# `pairing_expires_in()` is what the UI counts down, and a new code is one call.
PAIRING_CODE_TTL_SEC = 60.0


# ── Session ──────────────────────────────────────────────────────────────────

class WhatsAppSession:
    """One logged-in WhatsApp Web session, reused for every send in a campaign.

    Construct, `start()`, then poll `status()` from the GUI until it reads
    `"ready"`. `start()` does block — it launches Chrome — so the caller runs it
    off the GUI thread; everything the GUI polls afterwards is served from a
    cached snapshot and returns immediately.

    Three ways out of `"qr"`: scan the QR (`qr_png()`), type a code
    (`request_pairing_code`), or already have a login on disk, which is the
    usual one and costs a page load. One way back: `log_out()`.

    The browser is not the session. `close()` quits Chrome and keeps the login;
    after `idle_close_sec` with nothing sending the poller does the same by
    itself and `send()` brings it back. `status()` stays `"ready"` throughout,
    because from the user's side nothing has happened.

    `driver_factory`, `clock` and `sleep` exist so the tests can drive the whole
    state machine against a stub, the way `SmtpSender` is stubbed today. No test
    in this suite opens a real session.
    """

    def __init__(self, profile: str = "default", headless: bool = True, *,
                 default_region: str = "", driver_factory=None,
                 poll_sec: float = 2.0, send_timeout: float = 25.0,
                 idle_close_sec: float = 600.0, restore_timeout: float = 60.0,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self.profile = str(profile or "default")
        # `headless` means "hidden whenever it can be". A profile with no stored
        # login is opened visible whatever this says, because the first link
        # needs a window; `start()` resolves it and `running_headless()` reports
        # which of the two actually happened.
        self.headless = bool(headless)
        self.default_region = str(default_region or "").strip().upper()
        self.send_timeout = float(send_timeout) or 25.0
        self.poll_sec = max(0.2, float(poll_sec) or 2.0)
        # 0 disables. The browser is quit after this long with nothing sending;
        # the login stays on disk, so the cost of being wrong about the interval
        # is a page load on the next send and never a scan. The default is twice
        # `wa_max_gap_sec` deliberately — an interval shorter than the longest
        # gap the pacer can pick would tear the browser down between two
        # ordinary sends and make the page load a per-message cost.
        self.idle_close_sec = max(0.0, float(idle_close_sec or 0.0))
        # A restored session has to finish loading before a deep link means
        # anything, and that is a slower wait than a send's — it is a cold
        # browser reading a synced account, not a chat opening in a warm one.
        self.restore_timeout = float(restore_timeout) or 60.0

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
        self._qr_payload = ""
        self._me = ""
        self._banned = False
        self._pair_code = ""
        self._pair_until = 0.0
        self._pair_phone = ""
        self._stop = threading.Event()
        self._poller = None
        # Each poll loop carries the generation it was started under and exits
        # when a newer one exists. An idle close ends the loop and a reopen
        # starts another, and without this the two could overlap for a tick and
        # both publish snapshots of a browser only one of them owns.
        self._poll_gen = 0
        self._last_used = 0.0
        self._idle_closed = False
        self._ever_started = False
        self._headless_now = False

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
            # Hidden only when there is something to restore. A first link has
            # to be watchable — the QR, the pairing screen, and whatever the
            # client puts up when it does not like the look of the browser —
            # and a window nobody can see is a login nobody can finish.
            headless = self.headless and has_login(self.profile)
            try:
                os.makedirs(directory, exist_ok=True)
                driver = self._new_driver(directory, headless)
                driver.get(WA_URL)
            except Exception as exc:                      # noqa: BLE001 — returned
                self._set_state(status=OFFLINE)
                return False, classify(str(exc) or "Chrome would not start",
                                       default="CONN")
            self._driver = driver
            self._headless_now = bool(headless)
            self._idle_closed = False
            self._ever_started = True
        self._touch()
        self._stop.clear()
        self._poll_once()
        self._start_poller()
        return True, ""

    def close(self) -> None:
        """Stop polling and quit the browser. Safe to call twice, never raises.

        The *login* survives this — it lives in the user-data-dir, not in the
        browser — which is why closing between runs is free and why `log_out()`
        has to exist separately for the case where the user wants it gone.
        """
        self._stop.set()
        self._poll_gen += 1
        poller, self._poller = self._poller, None
        if poller is not None and poller is not threading.current_thread():
            poller.join(timeout=5.0)
        with self._driver_lock:
            driver, self._driver = self._driver, None
            self._idle_closed = False
            if driver is not None:
                try:
                    driver.quit()
                except Exception:                          # noqa: BLE001 — shutdown
                    pass
        self._set_state(status=BANNED if self._banned else OFFLINE, qr=b"",
                        pair=("", 0.0))

    def __enter__(self) -> "WhatsAppSession":
        # Deliberately does not start, for the reason `SmtpSender.__enter__`
        # does not connect: a start failure has to reach the caller as a return
        # value and `__enter__` has nowhere to put one.
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── logging out, which is not the same as closing ──

    def log_out(self) -> tuple[bool, str]:
        """Unlink this machine and wipe the profile. (ok, what happened).

        `close()` keeps the login so the next run costs a page load. This throws
        it away, which is the only way to connect a *different* number — before
        it existed the answer was "delete a directory by hand".

        Two halves, and the second is the one that decides the answer. The
        client-side log out is best effort: driven through the menu so the phone
        stops listing this machine under Linked Devices, and a build that has
        renamed its menu simply does not get driven. Clearing the user-data-dir
        is what actually guarantees the next connect starts clean, and it works
        with no browser at all — so `ok` reflects the directory, and the message
        says whether the phone was told.

        The ban latch is cleared here, and only here. It is a fact about a
        *number*, and this call is the user saying they are done with that
        number; leaving it set would mean a restricted account could never be
        swapped out without restarting the app. Every other path keeps it.
        """
        told_phone, why = self._client_log_out()
        self.close()
        wiped, trouble = _clear_state_dir(self.profile, sleep=self._sleep)

        self._banned = False
        self._ever_started = False
        self._idle_closed = False
        self._set_state(status=OFFLINE, qr=b"", me="", pair=("", 0.0))
        self._pair_phone = ""

        if not wiped:
            return False, ("The WhatsApp session directory could not be "
                           "removed, so the old login is still on this machine "
                           "(%s). Close any other Chrome running on this "
                           "profile and try again." % trouble)
        if told_phone:
            return True, ("Logged out. The phone no longer lists this machine "
                          "as a linked device, and the saved session has been "
                          "deleted — the next connect starts from a fresh QR.")
        return True, ("The saved session has been deleted, so the next connect "
                      "starts from a fresh QR. WhatsApp itself could not be "
                      "logged out from here (%s), so the phone may still show "
                      "this machine under Linked Devices until it is removed "
                      "there." % (why or "the browser was not open"))

    def _client_log_out(self) -> tuple[bool, str]:
        """Drive Log out inside the web client. (done, why not). Never raises."""
        with self._driver_lock:
            if self._driver is None:
                return False, "the browser was not open"
            if self.status() not in (READY, LOADING):
                return False, "nothing was logged in"
            try:
                menu = self._first(_SEL_MENU)
                if menu is None:
                    return False, "the client's menu was not where it was"
                menu.click()
                item = self._await(lambda: self._saying(_LOG_OUT_WORDS))
                if item is None:
                    return False, "no Log out item appeared in the menu"
                item.click()
                # The confirm dialog offers Log out again; where a build does
                # not ask, this finds nothing and the log out has already gone
                # through, which the QR check below is what actually decides.
                confirm = self._await(lambda: self._saying(_LOG_OUT_WORDS))
                if confirm is not None:
                    confirm.click()
                if self._await(lambda: self._first(_SEL_QR)) is None:
                    return False, "the login screen did not come back"
            except Exception as exc:                       # noqa: BLE001 — reported
                return False, str(exc) or "the client would not be driven"
        return True, ""

    # ── the browser's own lifetime ──

    def running_headless(self) -> bool:
        """Whether the browser that is open now is hidden. False when none is."""
        return bool(self._headless_now and self._driver is not None)

    def browser_running(self) -> bool:
        """Is Chrome actually up. False while idle-closed, and `status()` is
        still `"ready"` — the login is fine, the browser is just asleep."""
        return self._driver is not None

    def idle_for(self) -> float:
        """Seconds since anything used this session. 0.0 if nothing has."""
        if not self._last_used:
            return 0.0
        return max(0.0, self._clock() - self._last_used)

    def _touch(self) -> None:
        self._last_used = self._clock()

    def _idle_close(self) -> None:
        """Quit the browser but stay connected. Called from the poller only.

        Not `close()`: that publishes `OFFLINE` and disarms the reopen, which is
        what the user pressing Disconnect means. This one leaves the published
        status alone — the login has not gone anywhere and the user has nothing
        to do — and arms `send()` to bring the browser back.
        """
        self._poll_gen += 1
        self._poller = None
        with self._driver_lock:
            driver, self._driver = self._driver, None
            self._idle_closed = True
            if driver is not None:
                try:
                    driver.quit()
                except Exception:                          # noqa: BLE001 — shutdown
                    pass
        self._set_state(status=READY, qr=b"", pair=("", 0.0))

    def _ensure_browser(self) -> str:
        """Bring an idle-closed browser back and wait for it. "" when ready.

        Only ever reopens a session that was open and went idle. A session that
        was never started stays a `CONN:` — opening a browser on the strength of
        a queued message would turn a campaign started against no connection
        into one that silently launches Chrome behind the user.
        """
        if self._driver is not None:
            return ""
        if not (self._idle_closed and self._ever_started):
            return "CONN: the WhatsApp browser session is not open."
        ok, error = self.start()
        if not ok:
            return error or "CONN: the WhatsApp browser would not reopen."
        if self._await_ready():
            return ""
        return ("CONN: the saved WhatsApp session did not finish loading within "
                "%.0fs of reopening — nothing was sent." % self.restore_timeout)

    def _await_ready(self) -> bool:
        """Poll until the restored session reports ready, or give up. Never raises."""
        deadline = self._clock() + self.restore_timeout
        while True:
            self._poll_once()
            state = self.status()
            if state == READY:
                return True
            if state == BANNED or self._clock() >= deadline:
                return False
            self._sleep(min(self.poll_sec, 1.0))

    # ── what the GUI polls ──

    def status(self) -> str:
        """One of `WA_STATUSES`, from the cached snapshot. Never blocks."""
        with self._state_lock:
            return self._status

    def qr_png(self) -> bytes:
        """The login QR as PNG bytes; `b""` unless `status()` is `"qr"`.

        Bytes rather than a file or a terminal draw: the QR has to render inside
        the app. Encoded by the poller from WhatsApp's own `data-ref` payload so
        this call, like `status()`, is a dictionary read — and so that the image
        is the same whether the browser is visible or hidden.
        """
        with self._state_lock:
            return self._qr if self._status == QR else b""

    def qr_payload(self) -> str:
        """The string the QR encodes, "" when there is no QR up.

        Published because it is the difference between a QR that scans and one
        that does not: a caller with the payload can re-encode at any size, and
        a bug report with it in can be reproduced without a browser.
        """
        with self._state_lock:
            return self._qr_payload if self._status == QR else ""

    def qr_modules(self) -> list:
        """The QR as rows of booleans, `[]` when there is no QR up.

        For a widget that would rather paint modules than rescale a PNG —
        rescaling is what blurs a module edge, and a blurred edge is what a
        phone camera fails to lock onto.
        """
        return qr_matrix_of(self.qr_payload())

    def me(self) -> str:
        """The logged-in number, "" while unknown. Never blocks."""
        with self._state_lock:
            return self._me

    @property
    def banned(self) -> bool:
        """Sticky. Once true this session refuses to send, browser or no browser."""
        return self._banned

    # ── linking with a code instead of a QR ──

    def request_pairing_code(self, phone: str) -> tuple[str, str]:
        """Ask WhatsApp for eight characters to type on `phone`. (code, error).

        The other half of the login, and on a machine whose QR will not scan it
        is the *only* half. WhatsApp's own "Link with phone number" flow: give
        the client a number, it shows a code, the code is typed on that phone
        under Linked Devices.

        This is the one place in the module that drives the client's UI by the
        words on its buttons, and it is allowed to be best-effort in a way
        `send()` is not — because of what a mistake costs. Misread the number
        here and the worst case is a code that never works, since the code has
        to be typed on the phone that owns the number; misread a number in
        `send()` and a cold sales message lands on a stranger. So the number is
        still put through `to_wa_id` and still refused rather than guessed, but
        the DOM walk below degrades to a sentence instead of pretending.

        The code is short-lived — `pairing_expires_in()` counts it down and
        calling this again asks for another.
        """
        if self._banned:
            return "", _BANNED_STICKY

        wa_id = to_wa_id(phone, self.default_region)
        if not wa_id:
            return "", self._unsendable(
                phone, empty="no phone number was given to link with.")

        state = self.status()
        if state == READY:
            return "", ("OTHER: WhatsApp is already connected on this profile. "
                        "Log out first to link a different number.")
        if state == BANNED:
            return "", _BANNED_STICKY
        if state == OFFLINE:
            return "", ("CONN: the WhatsApp browser session is not open — "
                        "connect first, then ask for a code.")

        with self._driver_lock:
            if self._driver is None:
                return "", "CONN: the WhatsApp browser session is not open."
            try:
                code, error = self._drive_pairing(wa_id)
            except Exception as exc:                       # noqa: BLE001 — returned
                code, error = "", classify(str(exc), default="CONN")

        if not code:
            return "", error
        self._touch()
        self._pair_phone = wa_id
        self._set_state(status=PAIRING, qr=b"",
                        pair=(code, self._clock() + PAIRING_CODE_TTL_SEC))
        return code, ""

    def pairing_code(self) -> str:
        """The live pairing code, "" once it has expired. Never blocks."""
        with self._state_lock:
            if self._pair_code and self._clock() < self._pair_until:
                return self._pair_code
        return ""

    def pairing_expires_in(self) -> float:
        """Seconds the current code has left, 0.0 when there is none.

        What the card counts down. It runs out on this app's clock rather than
        WhatsApp's, deliberately early — see `PAIRING_CODE_TTL_SEC`.
        """
        with self._state_lock:
            if not self._pair_code:
                return 0.0
            return max(0.0, self._pair_until - self._clock())

    def pairing_phone(self) -> str:
        """The number the live code was asked for, so the card can say it back."""
        return self._pair_phone if self.pairing_code() else ""

    def _drive_pairing(self, wa_id: str) -> tuple[str, str]:
        """Walk the client's link-with-a-number flow. Caller holds the lock."""
        field = self._first(_SEL_PAIR_PHONE)
        if field is None:
            entry = self._saying(_PAIR_ENTRY_WORDS)
            if entry is None:
                return "", ("OTHER: this build of WhatsApp Web is not offering "
                            "'Log in with phone number'. Scan the QR instead.")
            entry.click()
            field = self._await(lambda: self._first(_SEL_PAIR_PHONE))
            if field is None:
                return "", ("OTHER: WhatsApp did not ask for a phone number "
                            "after 'Log in with phone number' was pressed.")

        try:
            field.clear()
            # The full international form, `+` and all. Where the client shows
            # one combined field it parses the country out of the prefix; where
            # it shows a separate country selector the `+` is dropped and the
            # digits land in the national field, which is wrong — and which
            # fails as a code that never pairs, not as a message to a stranger.
            field.send_keys("+" + wa_id)
        except Exception as exc:                           # noqa: BLE001 — returned
            return "", classify(str(exc), default="CONN")

        confirm = self._saying(_PAIR_NEXT_WORDS)
        try:
            if confirm is not None:
                confirm.click()
            else:
                field.send_keys("\n")
        except Exception as exc:                           # noqa: BLE001 — returned
            return "", classify(str(exc), default="CONN")

        code = self._await(self._read_pairing_code)
        if code:
            return str(code), ""
        blocked = self._blocking_message()
        if blocked:
            return "", classify(blocked, default="OTHER")
        return "", ("CONN: WhatsApp showed no pairing code for +%s within "
                    "%.0fs." % (wa_id, self.send_timeout))

    def _read_pairing_code(self) -> str:
        """The eight characters off the pairing screen, "" if not up yet.

        Everything non-alphanumeric is squeezed out first, because the client
        renders the code spaced, hyphenated, or one character to a node
        depending on the build — so the whole element's text is tried as well as
        each of its lines.

        A candidate carrying a digit wins over one that does not, and that is
        not a style preference: "WhatsApp" is eight letters, and squashed it
        matches the shape of a code exactly. A real code is overwhelmingly
        likely to contain a digit, so preferring one costs nothing and stops the
        word on the screen being read as the answer. An all-letter candidate is
        still returned when it is all there is, because an all-letter code is
        rare rather than impossible and refusing it would be worse.
        """
        fallback = ""
        for element in self._all(_SEL_PAIR_CODE):
            try:
                text = element.text or ""
            except Exception:                              # noqa: BLE001 — a read
                continue
            for candidate in list(text.splitlines()) + [text]:
                squashed = re.sub(r"[^A-Za-z0-9]+", "", candidate).upper()
                if not _PAIR_CODE_RE.match(squashed):
                    continue
                if any(character.isdigit() for character in squashed):
                    return squashed
                fallback = fallback or squashed
        return fallback

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
        if state in (QR, PAIRING):
            return False, ("AUTH: WhatsApp is not logged in — scan the QR or "
                           "finish linking in Settings before sending.")
        if state != READY:
            return False, ("CONN: the WhatsApp session is %s, not ready to send."
                           % (state or OFFLINE))

        with self._driver_lock:
            # Reopen a browser the poller closed for being idle, and do it
            # holding the lock: the poller takes the same one to close, so a
            # reopen outside it could be undone between the check and the send
            # and cost a message its pacing slot for nothing. It is a reentrant
            # lock and `start()` takes it again from here.
            trouble = self._ensure_browser()
            if trouble:
                return False, trouble
            self._touch()
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

    def _unsendable(self, phone: str, *, empty: str = "this lead has no phone "
                    "number.") -> str:
        """Why this number cannot be addressed, in the words the user needs.

        `empty` is the one line that changes with the caller: a send is talking
        about a lead, and a pairing request is talking about what the user just
        typed into a box.
        """
        if not digits_of(phone):
            return "RECIPIENT: %s" % empty
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
        self._set_state(status=BANNED, qr=b"", pair=("", 0.0))
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
            # An asleep browser is left asleep, and deliberately does not count
            # as use. The campaign polls for replies on a timer; waking Chrome
            # for each poll would mean the idle close never once fired, and
            # touching the clock here would mean the same. Replies are read
            # while a run is sending, which is exactly when the browser is up.
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
        self._poll_gen += 1
        generation = self._poll_gen
        thread = threading.Thread(target=self._poll_loop, args=(generation,),
                                  name="wa-status", daemon=True)
        self._poller = thread
        thread.start()

    def _poll_loop(self, generation: int) -> None:
        while not self._stop.wait(self.poll_sec):
            if generation != self._poll_gen:
                return
            self._poll_once()
            if self._should_idle_close():
                self._idle_close()
                return

    def _should_idle_close(self) -> bool:
        """Has this session earned being put to sleep.

        Only from `"ready"`, and that is the whole of the care this needs.
        Closing on a QR throws away the code the user is walking back to their
        desk to scan; closing on `"loading"` interrupts a restore; closing on
        `"pairing"` kills a code mid-type. Ready is the only state where nothing
        is in flight and the login is safely on disk.
        """
        if self.idle_close_sec <= 0 or self._driver is None:
            return False
        if self.status() != READY:
            return False
        return self.idle_for() >= self.idle_close_sec

    def _poll_once(self) -> None:
        """Read the DOM once and publish a snapshot. Never raises, never blocks
        a reader: the driver lock is dropped before the snapshot is written."""
        if self._banned:
            self._set_state(status=BANNED, qr=b"", pair=("", 0.0))
            return
        status, qr, me, payload = OFFLINE, b"", None, ""
        with self._driver_lock:
            driver = self._driver
            if driver is None and self._idle_closed:
                # Asleep, not gone. Publishing OFFLINE here would tell a user
                # whose login is perfectly good to go and scan a QR.
                return
            if driver is not None:
                status, qr, me, payload = self._read_state(driver)
        if status == BANNED:
            self._banned = True
            qr = b""
        # A live pairing code outranks the login screen behind it. The client
        # shows the code in place of the QR, so the DOM reads as `loading` or
        # `qr` throughout — and reporting either would hide the eight characters
        # the user is in the middle of typing.
        if status in (QR, LOADING) and self.pairing_code():
            status = PAIRING
        self._set_state(status=status, qr=qr, me=me, payload=payload)

    def _read_state(self, driver) -> tuple[str, bytes, str | None, str]:
        try:
            banner = self._blocking_message() or (driver.page_source or "")[:4000]
        except Exception:                                  # noqa: BLE001 — a read
            return OFFLINE, b"", None, ""
        if classify(banner, default="OTHER").startswith("BANNED:"):
            return BANNED, b"", None, ""

        qr_node = self._first(_SEL_QR)
        if qr_node is not None:
            # A QR means logged out, so the remembered number is stale — "" is
            # the instruction to clear it, where None below means "unchanged".
            png, payload = self._qr_image(qr_node)
            return QR, png, "", payload
        if self._first(_SEL_READY) is not None:
            return READY, b"", self._read_me(driver) or None, ""
        return LOADING, b"", None, ""

    def _qr_image(self, node) -> tuple[bytes, str]:
        """(PNG bytes, payload) for the login QR. `(b"", "")` if neither reads.

        The payload comes first and the screenshot is only a fallback, which is
        the fix for a headless login that could not be scanned: an element
        screenshot of the `<canvas>` WhatsApp paints into comes back blank or at
        the wrong scale with no window, so the image the user was shown depended
        on whether the browser was visible. Encoded from `data-ref` it does not.
        """
        payload = self._qr_ref(node)
        if payload:
            png = qr_png_of(payload)
            if png:
                return png, payload
        return self._qr_screenshot(node), payload

    def _qr_ref(self, node) -> str:
        """WhatsApp's own QR payload out of `data-ref`. "" if the build moved it."""
        try:
            ref = node.get_attribute("data-ref")
        except Exception:                                  # noqa: BLE001 — a read
            ref = ""
        if ref:
            return str(ref).strip()
        # The attribute can sit on a wrapper rather than on the node the
        # selector matched, so ask the document rather than give up on it.
        try:
            ref = self._driver.execute_script(
                "var n = document.querySelector('[data-ref]');"
                "return n ? (n.getAttribute('data-ref') || '') : '';")
        except Exception:                                  # noqa: BLE001 — a read
            ref = ""
        return str(ref or "").strip()

    def _qr_screenshot(self, node) -> bytes:
        """The old path, kept only for a build that stops publishing `data-ref`.

        An element screenshot works on the `<canvas>` the web client draws into
        *when there is a window*; `toDataURL` is the fallback for the builds
        that render an `<svg>`. Both are why headless could not be scanned.
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

    def _saying(self, words) -> object:
        """The first clickable whose visible text is one of `words`. None if none.

        By its words rather than by a `data-testid`, for the login and log-out
        flows only. WhatsApp renames test ids far more often than it renames a
        button a user reads, and a selector nobody can see is a selector nobody
        can debug when it breaks. Matched on the whole trimmed text so that
        "Log out" is not found inside "Log out of all devices?" — a heading is
        not a button, and clicking a heading does nothing while the real item
        goes unpressed.
        """
        wanted = {str(word or "").strip().lower() for word in (words or ())}
        wanted.discard("")
        if not wanted:
            return None
        for selector in _SEL_CLICKABLE:
            for element in self._all((selector,)):
                try:
                    text = (element.text or "").strip().lower()
                except Exception:                          # noqa: BLE001 — a read
                    continue
                if text in wanted:
                    return element
        return None

    def _set_state(self, *, status: str, qr: bytes | None = None,
                   me: str | None = None, payload: str | None = None,
                   pair: tuple | None = None) -> None:
        with self._state_lock:
            self._status = status if status in WA_STATUSES else OFFLINE
            if qr is not None:
                self._qr = qr
            if me is not None:
                self._me = me
            if payload is not None:
                self._qr_payload = payload
            if pair is not None:
                self._pair_code, self._pair_until = str(pair[0]), float(pair[1])


# ── Wiping a profile ─────────────────────────────────────────────────────────

# How many goes at the tree, and how long between them. Chrome does not release
# every handle in its user-data-dir the instant it exits on Windows, and the
# first `rmtree` after a `quit()` fails on a lock often enough that giving up on
# it would leave the user with the old login and a message saying otherwise.
_WIPE_ATTEMPTS = 5
_WIPE_PAUSE_SEC = 0.4


def _clear_state_dir(profile: str, sleep=time.sleep) -> tuple[bool, str]:
    """Delete one profile's session directory. (gone, why not).

    **This function removes a tree**, so it is written as a series of refusals
    rather than as a delete. The path is not taken on trust from anywhere: it is
    recomputed from `state_dir`, resolved through `realpath` so a junction or a
    symlink cannot stand in for it, and then required to be a direct child of
    the app's own `wa-session` folder. Anything else — the folder itself, a path
    on another drive, a link pointing out of the profile, a file — is refused
    with a sentence and nothing is touched. A missing directory is success,
    because success here means "there is no old login left", not "I deleted
    something".
    """
    import inspect
    import shutil
    import stat

    try:
        named = state_dir(profile)
        target = os.path.realpath(named)
        root = os.path.realpath(
            os.path.join(_settings.SETTINGS_DIR, _WA_STATE_DIRNAME))
    except Exception as exc:                               # noqa: BLE001 — reported
        return False, "the session path could not be resolved (%s)" % exc

    # Checked on the *resolved* path, so a junction or a symlink standing where
    # the session directory should be is measured by where it points rather than
    # by what it is called.
    if os.path.dirname(target) != root or os.path.basename(target) in ("", ".", ".."):
        return False, ("refused: %s is not one of this app's WhatsApp session "
                       "directories" % target)
    if not os.path.exists(target):
        return True, ""
    # And a link is refused outright rather than followed, even one pointing
    # somewhere legitimate. Nothing this app writes puts a link here, so one
    # being here at all means something is going on that a delete should not be
    # the first thing to find out about.
    if os.path.islink(named) or not os.path.isdir(target):
        return False, "refused: %s is not a plain directory" % named

    def force(func, path, _exc):
        """A read-only file is a permission bit, not a reason to stop."""
        try:
            os.chmod(path, stat.S_IWRITE)
        except Exception:                                  # noqa: BLE001 — retried
            return
        func(path)

    # `onerror` was deprecated in 3.12 in favour of `onexc`, and this app runs
    # on 3.14. Picked by inspecting the function rather than the interpreter
    # version, so a build that still has only the old name keeps working.
    name = ("onexc" if "onexc" in inspect.signature(shutil.rmtree).parameters
            else "onerror")
    kwargs = {name: force}
    last = ""
    for attempt in range(_WIPE_ATTEMPTS):
        try:
            shutil.rmtree(target, **kwargs)
        except Exception as exc:                           # noqa: BLE001 — retried
            last = str(exc)
        if not os.path.exists(target):
            return True, ""
        if attempt + 1 < _WIPE_ATTEMPTS:
            sleep(_WIPE_PAUSE_SEC)
    return False, last or "the directory is still there"


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

    major = _chrome_major_version()
    if headless:
        # Hidden is now the default once a login exists — see `has_login` and
        # `WhatsAppSession.start`. The two arguments underneath it are what make
        # that a fair thing to do rather than a hopeful one.
        #
        # The old comment here said WhatsApp treats a headless client more
        # suspiciously than a visible one. That is folklore this code was
        # repeating rather than something it had established, and it was doing
        # real damage: it justified defaulting to a window, and it hid the
        # actual reason a hidden login failed, which was the QR being
        # screenshotted off a canvas that headless does not paint. That is
        # fixed above and the QR is now identical either way.
        #
        # What is *known*: `--headless=new` runs the same renderer as a windowed
        # Chrome, it reports a `HeadlessChrome/` user agent unless one is set,
        # and with no window it has no window size, so a client that lays itself
        # out on the viewport gets a viewport of nothing. Both are given real
        # values below. What is *not* known, and cannot be established from this
        # repository — no test here may open a real WhatsApp session — is
        # whether WhatsApp's own risk scoring still treats the result
        # differently. If a hidden restore ever comes back to the QR screen
        # where a visible one does not, that is the evidence, and the switch to
        # turn it off is `wa_headless` in Settings.
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1180,860")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/%d.0.0.0 "
            "Safari/537.36" % (major or 120))

    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_experimental_option("prefs", {"intl.accept_languages": "en-US,en"})

    kwargs = {"options": options, "use_subprocess": True}
    if major:
        kwargs["version_main"] = major

    driver = uc.Chrome(**kwargs)
    driver.set_window_size(1180, 860)
    driver.set_page_load_timeout(45)
    return driver
