"""Gmail transport — building the message and getting it past the filters.

Everything here exists because of one asymmetry: a lead who never sees the email
costs nothing to fix, and a Gmail account that gets flagged costs the user their
inbox. So this module is biased hard towards looking like a person's mail client
and not like a bulk sender.

Concretely, that means the following, and they are decisions rather than gaps:

* **No tracking pixel, no open tracking, no click wrapping, no images.** A 1x1
  image from a domain nobody has heard of is one of the cheapest bulk-mail
  signals there is, and rewriting the one link in the body through a redirector
  costs the sending domain its reputation the first time that redirector hosts
  something else. Open rates are worth less than landing in the inbox. If you
  are reading this because you were asked to "just add analytics", the answer
  is a reply-rate report from `messages`, not a beacon in the body.
* Real `Message-ID`, `Date`, `Reply-To`, and `List-Unsubscribe` on every send.
  Gmail and Outlook both weight `List-Unsubscribe`, and the header costs a line.
* `multipart/alternative` with `text/plain` first — a plain-text part that is
  actually the same words as the HTML, not a stub.

Authentication is App Password only. Gmail has not accepted an ordinary account
password over SMTP since 2022, and "wrong password" is the single most common
support question this app will get, so `_auth_error` spells out the fix rather
than echoing the server's terse reply.

Errors come back as strings prefixed `AUTH:`, `QUOTA:`, `RECIPIENT:`, `CONN:`
or `OTHER:`. The prefix is a control signal for `core/campaign.py`, not a label:
AUTH and QUOTA mean stop the whole run for this account, RECIPIENT means skip
this one lead, CONN means it is worth another try. Nothing here raises.

`wire_form` and `read_wire` are the other half of `build_message`: the bytes as
the server received them, and those bytes back as headers and body. They exist
because the only honest answer to "what did you send?" is the message itself.
The templates this app builds from are editable, so a sent email rebuilt from
today's copy is a message that was never sent — presented with the authority of
a record. `core/campaign.py` stores the wire form before every hand-off, and
`ui/screen_outreach.py` reads it back.

The IMAP half is the other side of the same bargain. The footer asks people to
reply "unsubscribe" and the `List-Unsubscribe` header points at a mailto, so
somebody has to read that mailbox: `check_bounces`, `check_replies` and
`check_unsubscribes` are what `OutreachWorker` polls to honour it.
"""

from __future__ import annotations

import imaplib
import re
import smtplib
import ssl
import urllib.parse
import uuid
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, parseaddr

from core import templates as _templates

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993

_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_APP_PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{16}$")
# A colon is the only thing that delimits a reply prefix. Accepting a hyphen too
# read "Re-Max Realty" as a reply to "Max Realty" and put the amputated name on
# the wire — and the trades this app sells into are full of "Re-Roof" and
# "Re-Cover". No mail client has ever written "Re-" to mean a reply.
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fw|fwd)\s*:\s*", re.I)


# ── Small shared helpers ─────────────────────────────────────────────────────

def _header_safe(value) -> str:
    """Flatten a value so it cannot become a second header.

    A subject or display name reaches us from a template, and beyond that from a
    language model and a scraped business name. A stray CRLF in any of those is
    a free `Bcc:` for whoever put it there.
    """
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()


def _addr(name: str, email: str) -> str:
    email = _header_safe(email)
    name = _header_safe(name)
    return formataddr((name, email)) if name and email else email


def _plain_subject(value: str) -> str:
    """`value` with any `Re:`/`Fwd:` prefix taken off, however many it wears.

    `core.templates.render` already strips these and both queue paths go through
    it, so nothing reaches here wearing one today. The guarantee should not rest
    on every future caller remembering that route: a manufactured `Re:` on a
    first cold email is the misleading subject line CAN-SPAM prohibits, and a
    chaser carries its thread in `In-Reply-To` rather than in its wording. The
    loop is what stops "Re: Fwd: Re:" leaving one prefix standing.
    """
    subject = _header_safe(value)
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", subject, count=1)
        if stripped == subject:
            return subject.strip()
        subject = stripped


def _normalize_app_password(value: str) -> str:
    """Drop the spaces Google prints inside an App Password.

    The generator shows "abcd efgh ijkl mnop" and people paste it verbatim; the
    spaces are presentation only. Anything that is not exactly sixteen
    alphanumerics once the whitespace is gone is left alone apart from trimming
    — it might be a real password that contains spaces, and quietly rewriting
    that would be worse than a failed login.
    """
    raw = str(value or "")
    squeezed = re.sub(r"\s+", "", raw)
    return squeezed if _APP_PASSWORD_RE.match(squeezed) else raw.strip()


# ── Message construction ─────────────────────────────────────────────────────

def _message_id(from_email: str) -> str:
    """`<uuid@sender-domain>`.

    The domain has to be one that exists, or spam filters read the Message-ID as
    forged; the sender's own domain is the only one we can honestly claim.
    """
    domain = re.sub(r"[^A-Za-z0-9.\-]", "", from_email.rpartition("@")[2].lower())
    return "<%s@%s>" % (uuid.uuid4().hex, domain or "localhost")


def _normalize_message_id(value: str) -> str:
    """Put a caller-supplied id into the bracketed form headers use."""
    value = _header_safe(value).strip("<>").strip()
    return "<%s>" % value if value else ""


def _cte(text: str) -> str | None:
    """Content-transfer-encoding override, or None to let the stdlib choose.

    The stdlib's heuristic picks `8bit` for short non-ASCII lines, which we then
    hand to SMTP without ever negotiating 8BITMIME. Quoted-printable keeps the
    body 7-bit clean and, unlike base64, leaves it readable in a log or a raw
    source view — base64 over an entire text part is itself a mild bulk signal.
    """
    return None if text.isascii() else "quoted-printable"


def build_message(*, to_email: str, to_name: str, from_email: str,
                  from_name: str, reply_to: str, subject: str,
                  body_text: str, body_html: str,
                  unsubscribe_mailto: str, message_id: str = "",
                  in_reply_to: str = "") -> tuple[EmailMessage, str]:
    """Returns (message, message_id).

    `message_id` is returned in the bracketed `<id@host>` form so the caller can
    store it in `messages.message_id` and compare it directly against what
    `check_replies` pulls out of `In-Reply-To`. Pass one in to rebuild an
    identical message after a crash; leave it blank for a fresh one.

    `in_reply_to` is the first touch's Message-ID. Setting it, and `References`
    with it, is what makes a chaser land in the same conversation instead of
    arriving as a second unrelated cold email — which is how a reader tells a
    follow-up from a stranger writing twice. There is deliberately no `Re:` on
    the subject: threading is carried by the headers, and a manufactured `Re:`
    on a message that is not a reply is the misleading-subject line CAN-SPAM
    prohibits.

    An empty `unsubscribe_mailto` falls back to the sending address, which is
    the settings default and always a working unsubscribe route.

    The footer already in `body_text` and `body_html` is re-pointed at whichever
    address that resolves to, so the sentence the reader is offered and the
    `List-Unsubscribe` their client acts on are one mailbox. They are resolved
    apart nowhere: this is the only place either is decided.
    """
    from_email = _header_safe(from_email)
    to_email = _header_safe(to_email)

    msg = EmailMessage()
    msg["From"] = _addr(from_name, from_email)
    msg["To"] = _addr(to_name, to_email)
    msg["Subject"] = _plain_subject(subject)
    msg["Date"] = formatdate(localtime=True)

    mid = _normalize_message_id(message_id) or _message_id(from_email)
    msg["Message-ID"] = mid
    msg["Reply-To"] = _addr(from_name, _header_safe(reply_to) or from_email)

    parent = _normalize_message_id(in_reply_to)
    if parent:
        msg["In-Reply-To"] = parent
        msg["References"] = parent

    # Through the same resolver the footer was written with, so "the setting, or
    # else the account this is leaving from" cannot mean two things. It is also
    # what unpacks a setting typed as a whole `mailto:` URL, which reaching
    # straight for the raw value used to nest inside a second one.
    unsubscribe = _header_safe(_templates.unsubscribe_address(
        {}, {"unsubscribe_mailto": unsubscribe_mailto}, from_email))
    if unsubscribe:
        msg["List-Unsubscribe"] = "<mailto:%s?subject=unsubscribe>" % urllib.parse.quote(
            unsubscribe, safe="@")
        # RFC 8058 one-click is defined over HTTPS; a mailto-only list pairing is
        # a stretch of the spec. Gmail and Outlook both accept it and surface the
        # unsubscribe button for it, and hosting an HTTPS endpoint would mean
        # running a server this desktop app does not have.
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["Auto-Submitted"] = "auto-generated"

    text = _templates.retarget_unsubscribe(
        str(body_text or "").replace("\r\n", "\n"), unsubscribe)
    msg.set_content(text, subtype="plain", charset="utf-8", cte=_cte(text))

    html = _templates.retarget_unsubscribe(str(body_html or "").strip(), unsubscribe)
    if html:
        # Order matters: set_content lays down the plain part, add_alternative
        # promotes the message to multipart/alternative and appends the HTML
        # after it. Clients render the last part they understand.
        msg.add_alternative(html, subtype="html", charset="utf-8", cte=_cte(html))
        # set_content stamps MIME-Version onto the part it builds. The header is
        # only meaningful on the outermost message; no real client emits it on a
        # subpart, and looking hand-rolled is the thing we are avoiding.
        del list(msg.iter_parts())[-1]["MIME-Version"]
        # Same reasoning for the boundary: the stdlib's "===============N==" is a
        # legible Python fingerprint, and mailer fingerprints feed reputation
        # scoring. A random token is what every real client writes.
        msg.set_boundary("=_%s" % uuid.uuid4().hex)

    return msg, mid


# ── What went out ────────────────────────────────────────────────────────────

# The headers a person needs to answer "what did this look like in their
# inbox?", in the order they answer it. Everything else an EmailMessage carries
# is MIME plumbing, and a reader hunting for the subject through nine lines of
# boundary declarations is being shown less, not more.
WIRE_HEADERS = ("From", "To", "Reply-To", "Subject", "Date", "Message-ID",
                "In-Reply-To", "References", "List-Unsubscribe",
                "List-Unsubscribe-Post", "Auto-Submitted")


def wire_form(message) -> str:
    """The message serialised exactly as the server is handed it.

    This is the only honest answer to "what did you send?". Re-rendering the
    template would answer a different question — what the template says *now* —
    and the template store is editable, so the two drift the first time the user
    fixes a typo. Returns "" for anything that cannot be serialised; the caller
    stores nothing rather than storing a lie.
    """
    try:
        return message.as_string()
    except Exception:
        return ""


def read_wire(raw) -> dict:
    """A stored wire form as {headers, text, html} for display.

    Headers come back RFC 2047-decoded and in `WIRE_HEADERS` order, so the
    reader sees the words the recipient's client shows rather than
    `=?utf-8?q?...?=`. A blob that will not parse yields empty parts and no
    headers — never an exception, because this runs on the GUI thread.
    """
    blank = {"headers": [], "text": "", "html": ""}
    raw = str(raw or "")
    if not raw.strip():
        return blank
    try:
        message = message_from_bytes(raw.encode("utf-8", "replace"))
    except Exception:
        return blank

    headers = []
    for name in WIRE_HEADERS:
        for value in message.get_all(name, []):
            headers.append((name, _decoded(value)))
    text, html = _wire_parts(message)
    return {"headers": headers, "text": text, "html": html}


def _wire_parts(message) -> tuple[str, str]:
    """(plain, html) out of a parsed message. Either may be empty."""
    text = html = ""
    try:
        parts = list(message.walk())
    except Exception:
        parts = [message]
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not payload:
            continue
        body = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if content_type == "text/plain":
            text = text or body
        else:
            html = html or body
    return text, html


# ── Error classification ─────────────────────────────────────────────────────

# Enhanced status codes are matched as whole tokens, never as substrings: "5.7.1"
# is a reputation block and "5.7.14" is a login problem, and `in` cannot tell
# them apart.
_ENHANCED_RE = re.compile(r"\b[245]\.\d{1,3}\.\d{1,3}\b")

# QUOTA is "stop this account for today". It covers the daily send limit and
# Gmail's reputation blocks alike, because the right response to either is the
# same and pushing through either is how an account gets suspended.
_QUOTA_STATUSES = frozenset({
    "5.4.5",
    "4.2.2",   # RFC-wise the *recipient* is over quota, but Gmail also emits this
               # while throttling a sender. Pausing on a false positive costs
               # minutes; missing a true one costs the account.
    "5.7.1",   # policy / reputation refusal
})
_QUOTA_PHRASES = (
    "daily user sending limit exceeded",
    "daily sending quota exceeded",
    "you have reached a limit for sending mail",
    "sending limit",
    "message rate exceeded",
    "rate limit",
    "over quota",
    "too many login attempts",
    "unusual sending activity",
    "unsolicited mail",
)

_AUTH_STATUSES = frozenset({"5.7.8", "5.7.9", "5.7.14"})
_AUTH_PHRASES = (
    "username and password not accepted",
    "application-specific password required",
    "please log in via your web browser",
    "web login required",
    "invalid credentials",
    "bad credentials",
    "authentication failed",
    "authentication required",
)
_AUTH_CODES = frozenset({432, 454, 530, 534, 535, 538})

_RECIPIENT_STATUSES = frozenset({"5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.1.10"})
_RECIPIENT_PHRASES = (
    "does not exist",
    "user unknown",
    "no such user",
    "no such address",
    "recipient address rejected",
    "unrouteable address",
    "mailbox unavailable",
    "invalid recipient",
    "address not found",
    "domain not found",
)

_CONN_CODES = frozenset({421, 450, 451})

_APP_PASSWORD_HELP = (
    "Gmail does not accept a normal account password over SMTP. Turn on 2-Step "
    "Verification (2FA) at myaccount.google.com/security, create a 16-character "
    "App Password at myaccount.google.com/apppasswords, and paste that here "
    "instead of your Google password."
)


def _auth_error(account: str, detail: str) -> str:
    return "AUTH: Gmail rejected the sign-in for %s. %s Server said: %s" % (
        account or "this account", _APP_PASSWORD_HELP, detail or "no detail")


def _classify(detail: str, *, code: int = 0, account: str = "",
              default: str = "OTHER") -> str:
    """One SMTP failure to one prefixed, human-readable error string.

    Order is deliberate. A `550` can be a daily limit (`5.4.5`) or a dead mailbox
    (`5.1.1`), and the two want opposite reactions, so the enhanced status code
    decides before the bare reply code gets a vote.
    """
    text = _header_safe(detail) or "no detail from the server"
    low = text.lower()
    statuses = set(_ENHANCED_RE.findall(low))

    if statuses & _QUOTA_STATUSES or any(p in low for p in _QUOTA_PHRASES):
        return "QUOTA: %s" % text
    if statuses & _AUTH_STATUSES or code in _AUTH_CODES or any(p in low for p in _AUTH_PHRASES):
        return _auth_error(account, text)
    if statuses & _RECIPIENT_STATUSES or any(p in low for p in _RECIPIENT_PHRASES):
        return "RECIPIENT: %s" % text
    if code in _CONN_CODES:
        return "CONN: %s" % text
    return "%s: %s" % (default, text)


def _reply(exc) -> str:
    """The server's own words, with the reply code kept in front of them.

    The enhanced status (`5.4.5`, `5.1.1`) lives in `smtp_error`, and `_classify`
    matches on it, so this must not throw the code or the text away.
    """
    err = getattr(exc, "smtp_error", None)
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    err = _header_safe(err) if err else str(exc)
    code = getattr(exc, "smtp_code", 0) or 0
    return ("%d %s" % (code, err)).strip() if code else err


def _refused_detail(recipients: dict) -> str:
    parts = []
    for address, reply in (recipients or {}).items():
        code, message = reply if isinstance(reply, tuple) else (0, reply)
        if isinstance(message, bytes):
            message = message.decode("utf-8", "replace")
        parts.append("%s: %s %s" % (address, code, _header_safe(message)))
    return "; ".join(parts) or "the server refused every recipient"


def _first_code(recipients: dict) -> int:
    for reply in (recipients or {}).values():
        if isinstance(reply, tuple) and isinstance(reply[0], int):
            return reply[0]
    return 0


# ── SMTP ─────────────────────────────────────────────────────────────────────

def _quit(smtp) -> None:
    if smtp is None:
        return
    try:
        smtp.quit()
    except (smtplib.SMTPException, ssl.SSLError, OSError):
        try:
            smtp.close()
        except (smtplib.SMTPException, ssl.SSLError, OSError):
            pass


class SmtpSender:
    """One authenticated Gmail session, reused for every send in a campaign.

    Sends are minutes apart on purpose, and Gmail drops an idle submission
    connection well inside that gap, so a dead socket is the normal case rather
    than an error: `send` reconnects and tries once more. It never retries an
    auth or quota failure — the second attempt fails identically, and repeating
    either is exactly the pattern that gets an account locked.
    """

    def __init__(self, email: str, app_password: str, display_name: str = "",
                 timeout: float = 30.0, host: str = "", port: int = 0) -> None:
        self.email = str(email or "").strip()
        self.display_name = str(display_name or "").strip()
        self.timeout = float(timeout) if timeout else 30.0
        # Blank falls back to Gmail, which is what every existing account has
        # stored, so nothing that works today changes.
        self.host = str(host or "").strip() or GMAIL_SMTP_HOST
        self.port = int(port) if port else GMAIL_SMTP_PORT
        self._password = _normalize_app_password(app_password)
        self._smtp = None

    # ── lifecycle ──

    def connect(self) -> tuple[bool, str]:
        """Open and authenticate. Idempotent while the session is alive."""
        if self._smtp is not None:
            return True, ""
        if not self.email or not self._password:
            return False, _auth_error(self.email, "no App Password is stored for this account")

        smtp = None
        try:
            smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()  # the extension list is renegotiated after STARTTLS
            smtp.login(self.email, self._password)
        except smtplib.SMTPAuthenticationError as exc:
            _quit(smtp)
            return False, _auth_error(self.email, _reply(exc))
        except smtplib.SMTPResponseException as exc:
            _quit(smtp)
            return False, _classify(_reply(exc), code=exc.smtp_code,
                                    account=self.email, default="CONN")
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            _quit(smtp)
            return False, _classify(str(exc), account=self.email, default="CONN")

        self._smtp = smtp
        return True, ""

    def close(self) -> None:
        _quit(self._smtp)
        self._smtp = None

    def __enter__(self) -> "SmtpSender":
        # Deliberately does not connect: a connect error has to reach the caller
        # as a return value, and __enter__ has nowhere to put one. The first
        # send() opens the session and reports the failure properly.
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── sending ──

    def send(self, message) -> tuple[bool, str]:
        """Deliver one message. Returns (ok, error); never raises."""
        if message is None:
            return False, "OTHER: no message to send"
        if not _recipients(message):
            return False, "RECIPIENT: the message carries no deliverable address"

        ok, error = self.connect()
        if not ok:
            return False, error

        ok, error, retryable = self._attempt(message)
        if ok or not retryable:
            return ok, error

        self.close()
        ok, error = self.connect()
        if not ok:
            return False, error
        ok, error, _ = self._attempt(message)
        return ok, error

    def _attempt(self, message) -> tuple[bool, str, bool]:
        """(ok, error, worth_retrying) for a single delivery attempt."""
        try:
            self._smtp.send_message(message, from_addr=self.email)
        except smtplib.SMTPServerDisconnected as exc:
            self._smtp = None  # the socket is already gone; quit() would only hang
            return False, _classify(str(exc) or "the server closed the connection",
                                    account=self.email, default="CONN"), True
        except smtplib.SMTPConnectError as exc:
            self._smtp = None
            return False, _classify(_reply(exc), code=exc.smtp_code,
                                    account=self.email, default="CONN"), True
        except smtplib.SMTPRecipientsRefused as exc:
            # smtplib RSETs before raising this, so the session stays usable and
            # the campaign can move straight on to the next lead.
            return False, _classify(_refused_detail(exc.recipients),
                                    code=_first_code(exc.recipients),
                                    account=self.email, default="RECIPIENT"), False
        except smtplib.SMTPResponseException as exc:
            return False, _classify(_reply(exc), code=exc.smtp_code,
                                    account=self.email, default="OTHER"), False
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            self._smtp = None
            return False, _classify(str(exc), account=self.email, default="CONN"), True
        return True, "", False


def _recipients(message) -> list[str]:
    """Every address an MTA would deliver to; empty for anything that is not a message."""
    get_all = getattr(message, "get_all", None)
    if get_all is None:
        return []
    values = []
    for header in ("To", "Cc", "Bcc"):
        for raw in get_all(header, []):
            values.extend(_ADDRESS_RE.findall(str(raw)))
    return values


def smtp_host_for(email: str) -> tuple[str, int, str]:
    """A sensible (smtp host, port, imap host) guess for an address.

    Only a starting point the user can overwrite: the common providers are worth
    filling in so nobody has to look up a hostname, and a domain nobody knows
    falls back to Gmail because Workspace is the usual answer for a business
    domain. Guessing wrong costs one edit; not offering anything cost the user
    an account they could not add at all.
    """
    domain = str(email or "").rsplit("@", 1)[-1].strip().lower()
    known = {
        "gmail.com": (GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, GMAIL_IMAP_HOST),
        "googlemail.com": (GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, GMAIL_IMAP_HOST),
        "outlook.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
        "hotmail.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
        "live.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
        "office365.com": ("smtp.office365.com", 587, "outlook.office365.com"),
        "zoho.com": ("smtp.zoho.com", 587, "imap.zoho.com"),
        "yahoo.com": ("smtp.mail.yahoo.com", 587, "imap.mail.yahoo.com"),
        "icloud.com": ("smtp.mail.me.com", 587, "imap.mail.me.com"),
        "fastmail.com": ("smtp.fastmail.com", 587, "imap.fastmail.com"),
    }
    return known.get(domain, (GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, GMAIL_IMAP_HOST))


def verify_account(email: str, app_password: str, host: str = "",
                   port: int = 0) -> tuple[bool, str]:
    """Prove the credentials work without sending anything.

    Used by the settings screen's per-account Verify button, so the error string
    is the one the user reads — hence the App Password explanation in `AUTH:`.
    """
    sender = SmtpSender(email, app_password, timeout=20.0, host=host, port=port)
    try:
        return sender.connect()
    finally:
        sender.close()


# ── IMAP (replies and bounces) ───────────────────────────────────────────────

_IMAP_TIMEOUT = 20.0
_IMAP_MAX_MESSAGES = 400      # newest N in the window; a busy inbox is not our problem
_IMAP_FETCH_BYTES = 32768     # enough for a bounce report, far short of a quoted thread
_IMAP_BATCH = 50

_MSGID_RE = re.compile(r"<[^<>\s@]+@[^<>\s]+>")

_BOUNCE_LOCALS = ("mailer-daemon", "mailer_daemon", "postmaster")

# Ordered best-evidence-first. Gmail sets X-Failed-Recipients on its own bounces;
# every RFC 3464 report carries Final-Recipient; the prose match is the last
# resort for the handful of MTAs that send neither.
_FAILED_RECIPIENT_RES = (
    re.compile(r"^X-Failed-Recipients:\s*(.+)$", re.I | re.M),
    re.compile(r"^Final-Recipient:\s*rfc822;\s*(\S+)", re.I | re.M),
    re.compile(r"^Original-Recipient:\s*rfc822;\s*(\S+)", re.I | re.M),
    re.compile(r"(?:wasn't|was not|couldn't be|could not be) delivered to[:\s]+(\S+@\S+)", re.I),
)


# What an opt-out looks like when a person types it. Gmail's own unsubscribe
# button follows the `List-Unsubscribe` mailto and sends the word as the
# subject; the footer asks for a plain reply, which arrives with it in the body.
_UNSUBSCRIBE_RE = re.compile(
    r"\bunsubscribe\b|\bunsub\b|\bopt[\s\-_]?out\b|\bremove me\b|"
    r"\btake me off\b|\bstop (?:emailing|contacting)\b|"
    r"\bdo not (?:email|contact) me\b", re.I)

# Where a reply stops being the sender's own words. Our footer contains the word
# "unsubscribe" and comes back quoted inside every reply, so without this cut
# everybody who answers at all — including the ones saying yes — reads as an
# opt-out and gets suppressed.
_QUOTE_START_RE = re.compile(
    r"^\s*(?:>|-{2,}\s*original message|_{10,}|from:\s|on\b.{0,200}?\bwrote:)",
    re.I | re.M)

_OWN_WORDS_CHARS = 800
_TAG_RE = re.compile(r"<[^>]+>")


def _imap_since(days: int) -> str:
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 14
    return (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")


def _imap_logout(conn) -> None:
    if conn is None:
        return
    for step in (conn.close, conn.logout):
        try:
            step()
        except (imaplib.IMAP4.error, ssl.SSLError, OSError):
            pass


def _imap_collect(email: str, app_password: str, criteria: str, fetch_spec: str) -> list[bytes]:
    """Run one read-only INBOX search and fetch. Returns [] on any failure.

    Read-only plus BODY.PEEK, because marking a prospect's reply as read in the
    user's own inbox before they have seen it would be unforgivable.
    """
    if not email or not app_password:
        return []

    conn = None
    try:
        conn = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT,
                                 ssl_context=ssl.create_default_context(),
                                 timeout=_IMAP_TIMEOUT)
        conn.login(email, _normalize_app_password(app_password))
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            return []

        status, data = conn.search(None, criteria)
        if status != "OK" or not data or not data[0]:
            return []

        ids = data[0].split()[-_IMAP_MAX_MESSAGES:]
        out = []
        for start in range(0, len(ids), _IMAP_BATCH):
            batch = b",".join(ids[start:start + _IMAP_BATCH])
            status, parts = conn.fetch(batch, fetch_spec)
            if status != "OK":
                continue
            for part in parts or []:
                if isinstance(part, tuple) and len(part) > 1 and part[1]:
                    out.append(bytes(part[1])[:_IMAP_FETCH_BYTES])
        return out
    except (imaplib.IMAP4.error, ssl.SSLError, OSError, ValueError):
        return []
    finally:
        _imap_logout(conn)


def check_replies(email: str, app_password: str, since_days: int = 14) -> list[str]:
    """Message-IDs that recent inbox mail is replying to.

    This deliberately does not know which ids we sent — the campaign holds those
    in `messages.message_id` and intersects. Ids come back bracketed, the same
    form `build_message` hands out, so the two sets compare without munging.

    Caveat worth knowing before you debug a silent result: Gmail has been known
    to substitute its own Message-ID on submission, in which case nothing here
    will ever match. That loses a reply signal; it never causes a wrong send.
    """
    blobs = _imap_collect(email, app_password,
                          '(SINCE "%s")' % _imap_since(since_days),
                          "(BODY.PEEK[HEADER.FIELDS (IN-REPLY-TO REFERENCES)])")
    seen, out = set(), []
    for blob in blobs:
        for mid in _MSGID_RE.findall(blob.decode("utf-8", "replace")):
            if mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


def check_bounces(email: str, app_password: str, since_days: int = 14) -> list[str]:
    """Addresses that bounced, for the caller to add to `suppression`.

    Searching by sender rather than by subject keeps this working in whatever
    language the user's Gmail is set to.
    """
    blobs = _imap_collect(
        email, app_password,
        '(SINCE "%s" (OR FROM "mailer-daemon" FROM "postmaster"))' % _imap_since(since_days),
        "(BODY.PEEK[]<0.%d>)" % _IMAP_FETCH_BYTES)

    own = str(email or "").strip().lower()
    seen, out = set(), []
    for blob in blobs:
        text = blob.decode("utf-8", "replace")
        for pattern in _FAILED_RECIPIENT_RES:
            for fragment in pattern.findall(text):
                for address in _ADDRESS_RE.findall(fragment):
                    address = address.lower()
                    if address == own or address.partition("@")[0] in _BOUNCE_LOCALS:
                        continue
                    if address not in seen:
                        seen.add(address)
                        out.append(address)
    return out


def check_unsubscribes(email: str, app_password: str, since_days: int = 14) -> list[str]:
    """Addresses that have asked to be taken off the list, for `suppression`.

    The search is deliberately wider than the match: IMAP narrows the mailbox to
    plausible messages, and `_unsubscribe_sender` then decides on the subject
    plus only the part of the body the sender typed. Reading the whole body
    would opt out every person who replies, because the quoted original carries
    our own footer.
    """
    blobs = _imap_collect(
        email, app_password,
        '(SINCE "%s" (OR SUBJECT "unsubscribe" (OR SUBJECT "opt out" '
        '(OR SUBJECT "remove me" TEXT "unsubscribe"))))' % _imap_since(since_days),
        "(BODY.PEEK[]<0.%d>)" % _IMAP_FETCH_BYTES)

    own = str(email or "").strip().lower()
    seen, out = set(), []
    for blob in blobs:
        address = _unsubscribe_sender(blob)
        if not address or address == own or address.partition("@")[0] in _BOUNCE_LOCALS:
            continue
        if address not in seen:
            seen.add(address)
            out.append(address)
    return out


def _unsubscribe_sender(blob: bytes) -> str:
    """The From address of one fetched message, but only if it is opting out."""
    try:
        message = message_from_bytes(bytes(blob or b""))
    except Exception:
        return ""
    intent = "%s\n%s" % (_decoded(message.get("Subject")),
                         _own_words(_body_text(message)))
    if not _UNSUBSCRIBE_RE.search(intent):
        return ""
    address = parseaddr(_decoded(message.get("From")))[1].strip().lower()
    return address if _ADDRESS_RE.fullmatch(address) else ""


def _decoded(value) -> str:
    """An RFC 2047 header as the words it stands for."""
    try:
        return _header_safe(make_header(decode_header(str(value or ""))))
    except Exception:
        return _header_safe(value)


def _body_text(message) -> str:
    """The readable body of a message that may have been truncated mid-fetch.

    Plain text first; an HTML-only reply (Outlook sends them) is stripped of its
    tags rather than skipped, because a missed unsubscribe is the expensive
    failure here and a false positive only costs one lead.
    """
    html = ""
    try:
        parts = list(message.walk())
    except Exception:
        parts = [message]
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if content_type == "text/plain":
            return text
        html = html or text
    return _TAG_RE.sub(" ", html)


def _own_words(text: str) -> str:
    """The part of a reply the sender wrote, before the original comes back."""
    match = _QUOTE_START_RE.search(text)
    if match:
        text = text[:match.start()]
    return text[:_OWN_WORDS_CHARS]
