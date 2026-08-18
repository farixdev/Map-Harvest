"""Offline tests for core.mailer. No network, no Qt, no SMTP server.

Run:  venv/Scripts/python.exe -m tests.test_mailer
(or `python -m pytest tests/ -q` where pytest is installed).

Two things are guarded here. First, the shape of the wire format: header
injection, the plain-before-HTML ordering, and the deliberate absence of any
tracking. Second, the error prefixes — `core/campaign.py` branches on them to
decide between stopping a run and skipping one lead, so a misclassified Gmail
reply either burns an account or drops a campaign on the floor.

To eyeball the MIME by hand:

    venv/Scripts/python.exe -c "from core.mailer import build_message; print(build_message(to_email='owner@acmeplumbing.ca', to_name='Mike Reid', from_email='umar@autoarmy.io', from_name='Umar Farooq', reply_to='umar@autoarmy.io', subject='booking on acmeplumbing.ca', body_text='Hi Mike,\\n\\nOne line.\\n\\nUmar', body_html='<p>Hi Mike,</p><p>One line.</p>', unsubscribe_mailto='')[0].as_string())"
"""

import email
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import mailer as M  # noqa: E402

BASE = {
    "to_email": "owner@acmeplumbing.ca",
    "to_name": "Mike Reid",
    "from_email": "umar@autoarmy.io",
    "from_name": "Umar Farooq",
    "reply_to": "hello@autoarmy.io",
    "subject": "booking on acmeplumbing.ca",
    "body_text": "Hi Mike,\n\nYour emergency page ends at one contact form.\n\nUmar",
    "body_html": "<p>Hi Mike,</p><p>Your emergency page ends at one contact form.</p>",
    "unsubscribe_mailto": "unsubscribe@autoarmy.io",
}


def _build(**overrides):
    return M.build_message(**{**BASE, **overrides})


def _recipients_of(message) -> list[str]:
    """Every address an MTA would actually deliver to."""
    found = []
    for header in ("To", "Cc", "Bcc"):
        for raw in message.get_all(header, []):
            found.extend(M._ADDRESS_RE.findall(str(raw)))
    return found


# ── Headers ──

def test_headers():
    msg, mid = _build()

    assert msg["From"] == "Umar Farooq <umar@autoarmy.io>"
    assert msg["To"] == "Mike Reid <owner@acmeplumbing.ca>"
    assert msg["Subject"] == BASE["subject"]
    assert msg["Reply-To"] == "Umar Farooq <hello@autoarmy.io>"
    assert msg["Auto-Submitted"] == "auto-generated"

    # A real Date with an offset, not a bare timestamp.
    assert re.search(r"[+-]\d{4}$", msg["Date"]), msg["Date"]

    # <uuid@sender-domain>, and the same value handed back to the caller.
    assert msg["Message-ID"] == mid
    assert re.fullmatch(r"<[0-9a-f]{32}@autoarmy\.io>", mid), mid
    assert _build()[1] != mid  # fresh id per message

    assert msg["List-Unsubscribe"] == "<mailto:unsubscribe@autoarmy.io?subject=unsubscribe>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    print("headers: OK")


def test_unsubscribe_falls_back_to_sender():
    msg, _ = _build(unsubscribe_mailto="")
    assert msg["List-Unsubscribe"] == "<mailto:umar@autoarmy.io?subject=unsubscribe>"

    msg, _ = _build(unsubscribe_mailto="", from_email="")
    assert msg["List-Unsubscribe"] is None      # nothing to unsubscribe to
    assert msg["List-Unsubscribe-Post"] is None
    print("unsubscribe fallback: OK")


def test_supplied_message_id():
    msg, mid = _build(message_id="abc123@autoarmy.io")
    assert mid == "<abc123@autoarmy.io>" and msg["Message-ID"] == mid

    msg, mid = _build(message_id="<abc123@autoarmy.io>")
    assert mid == "<abc123@autoarmy.io>"
    print("supplied message id: OK")


def test_header_injection():
    msg, _ = _build(subject="hello\r\nBcc: victim@example.com",
                    to_name="Mike\nX-Evil: 1")
    assert msg["Bcc"] is None and msg["X-Evil"] is None
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]

    # The payload must survive only as inert text: re-parse the wire form and
    # confirm the injected names never became headers of their own.
    reparsed = email.message_from_string(msg.as_string())
    assert set(reparsed.keys()).isdisjoint({"Bcc", "X-Evil"}), reparsed.keys()
    assert _recipients_of(reparsed) == ["owner@acmeplumbing.ca"]
    print("header injection: OK")


# ── Body ──

def test_alternative_ordering():
    msg, _ = _build()
    assert msg.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in msg.iter_parts()]
    assert parts == ["text/plain", "text/html"], parts
    print("alternative ordering: OK")


def test_plain_only_when_no_html():
    msg, _ = _build(body_html="")
    assert msg.get_content_type() == "text/plain"
    assert BASE["body_text"].splitlines()[0] in msg.get_content()
    print("plain only: OK")


def test_no_tracking():
    """The deliberate absence — see the module docstring before 'fixing' this."""
    msg, _ = _build(body_html="<p>Hi</p><p><a href='https://cal.com/autoarmy/15min'>15 minutes</a></p>")
    raw = msg.as_string().lower()
    for banned in ("<img", "1x1", "pixel", "track", "beacon", "utm_", "open.gif"):
        assert banned not in raw, banned
    print("no tracking: OK")


def test_unicode_body_and_subject():
    msg, _ = _build(subject="café booking", to_name="Mikaël Reid",
                    body_text="Hi Mikaël,\n\nBonne journée.\n", body_html="<p>Bonne journée</p>")
    raw = msg.as_bytes()                      # must serialise for smtplib
    assert b"caf\xc3\xa9" not in raw          # header is RFC 2047 encoded, not raw utf-8
    assert "café" in msg["Subject"]
    plain = list(msg.iter_parts())[0]
    assert plain["Content-Transfer-Encoding"] == "quoted-printable"
    assert "Bonne journée" in plain.get_content()
    print("unicode: OK")


def test_never_raises():
    for kwargs in ({}, {"to_email": None}, {"body_text": None, "body_html": None},
                   {"subject": None, "from_email": None, "reply_to": None},
                   {"to_name": None, "from_name": None, "unsubscribe_mailto": None}):
        msg, mid = _build(**kwargs)
        assert msg.as_string() and isinstance(mid, str) and mid.startswith("<")
    print("never raises: OK")


# ── Error classification ──

def test_quota_classification():
    for reply in ("550 5.4.5 Daily user sending limit exceeded. Learn more at ...",
                  "452 4.2.2 The email account that you tried to reach is over quota",
                  "421 4.7.0 Our system has detected an unusual rate of unsolicited mail",
                  "454 4.7.0 Too many login attempts, please try again later"):
        assert M._classify(reply, code=int(reply[:3])).startswith("QUOTA:"), reply
    print("quota classification: OK")


def test_auth_classification():
    err = M._classify("535 5.7.8 Username and Password not accepted", code=535,
                      account="umar@autoarmy.io")
    assert err.startswith("AUTH:")
    low = err.lower()
    # The whole point of this string: it answers the question the user is about to ask.
    assert "app password" in low and "2-step verification" in low and "2fa" in low
    assert "umar@autoarmy.io" in err and "5.7.8" in err

    assert M._classify("534 5.7.9 Application-specific password required", code=534).startswith("AUTH:")

    # 5.7.1 is a reputation block and 5.7.14 is a login problem; one is a prefix
    # of the other, and substring matching used to fold them together.
    assert M._classify("534 5.7.14 Please log in via your web browser", code=534).startswith("AUTH:")
    assert M._classify("550 5.7.1 Our system has detected that this message is likely unsolicited",
                       code=550).startswith("QUOTA:")
    print("auth classification: OK")


def test_recipient_classification():
    for reply in ("550 5.1.1 The email account that you tried to reach does not exist",
                  "553 5.1.2 We weren't able to find the recipient domain",
                  "550 5.1.10 Recipient address rejected: address not found"):
        assert M._classify(reply, code=int(reply[:3])).startswith("RECIPIENT:"), reply
    print("recipient classification: OK")


def test_conn_and_other_classification():
    assert M._classify("421 4.7.0 Try again later, closing connection", code=421).startswith("CONN:")
    assert M._classify("[Errno 11001] getaddrinfo failed", default="CONN").startswith("CONN:")
    assert M._classify("554 5.6.0 Message exceeds fixed maximum size", code=554).startswith("OTHER:")
    assert M._classify("").startswith("OTHER:")
    print("conn/other classification: OK")


def test_app_password_normalisation():
    assert M._normalize_app_password("abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert M._normalize_app_password("  abcdefghijklmnop  ") == "abcdefghijklmnop"
    # Not App-Password shaped: left alone apart from trimming.
    assert M._normalize_app_password(" my real pass ") == "my real pass"
    assert M._normalize_app_password("") == ""
    assert M._normalize_app_password(None) == ""
    print("app password normalisation: OK")


# ── Degradation without a server ──

def test_missing_credentials_never_touch_the_network():
    ok, err = M.verify_account("", "")
    assert not ok and err.startswith("AUTH:") and "app password" in err.lower()

    ok, err = M.verify_account("umar@autoarmy.io", "")
    assert not ok and err.startswith("AUTH:")

    sender = M.SmtpSender("umar@autoarmy.io", "")
    with sender as s:
        ok, err = s.send(_build()[0])
    assert not ok and err.startswith("AUTH:")

    ok, err = M.SmtpSender("umar@autoarmy.io", "abcdefghijklmnop").send(None)
    assert not ok and err.startswith("OTHER:")

    msg, _ = _build(to_email="")
    ok, err = M.SmtpSender("umar@autoarmy.io", "abcdefghijklmnop").send(msg)
    assert not ok and err.startswith("RECIPIENT:")
    print("missing credentials: OK")


def test_imap_disabled_returns_empty():
    assert M.check_replies("", "") == []
    assert M.check_bounces("", "") == []
    assert M.check_unsubscribes("", "") == []
    assert M.check_replies("umar@autoarmy.io", "") == []
    assert M.check_bounces("umar@autoarmy.io", "", since_days=0) == []
    assert M.check_unsubscribes("umar@autoarmy.io", "") == []
    print("imap disabled: OK")


def test_bounce_parsing():
    report = (
        "From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
        "X-Failed-Recipients: owner@acmeplumbing.ca\r\n"
        "Subject: Delivery Status Notification (Failure)\r\n"
        "\r\n"
        "Your message wasn't delivered to owner@acmeplumbing.ca.\r\n"
        "\r\n"
        "Final-Recipient: rfc822; owner@acmeplumbing.ca\r\n"
        "Action: failed\r\n"
        "Status: 5.1.1\r\n"
    ).encode()

    addresses = []
    for pattern in M._FAILED_RECIPIENT_RES:
        for fragment in pattern.findall(report.decode()):
            addresses.extend(M._ADDRESS_RE.findall(fragment))
    assert "owner@acmeplumbing.ca" in addresses
    assert not any(a.startswith("mailer-daemon") for a in addresses)

    ids = M._MSGID_RE.findall("In-Reply-To: <abc123@autoarmy.io>\r\n"
                              "References: <abc123@autoarmy.io> <CAF=x+y@mail.gmail.com>\r\n")
    assert ids == ["<abc123@autoarmy.io>", "<abc123@autoarmy.io>", "<CAF=x+y@mail.gmail.com>"]
    print("bounce parsing: OK")


# ── Unsubscribe detection ──

# What our own footer looks like coming back inside a reply. Every assertion
# about a false positive below hangs on this text being present and ignored.
FOOTER = ('Not the right person? Reply "unsubscribe" or write to '
          'unsubscribe@autoarmy.io and I will stop.')


def _inbound(body: str, *, sender: str = "Mike Reid <owner@acmeplumbing.ca>",
             subject: str = "Re: booking on acmeplumbing.ca",
             content_type: str = "text/plain") -> bytes:
    """One IMAP-fetched message, in the truncated wire form `_imap_collect` yields."""
    return ("From: %s\r\nTo: umar@autoarmy.io\r\nSubject: %s\r\n"
            "Content-Type: %s; charset=utf-8\r\n\r\n%s"
            % (sender, subject, content_type, body)).encode()


def test_unsubscribe_by_subject():
    """Gmail's own unsubscribe button sends the List-Unsubscribe mailto verbatim."""
    blob = _inbound("", subject="unsubscribe")
    assert M._unsubscribe_sender(blob) == "owner@acmeplumbing.ca"

    # RFC 2047 in the subject must still be read as the words it stands for.
    encoded = _inbound("", subject="=?utf-8?q?Unsubscribe_please?=")
    assert M._unsubscribe_sender(encoded) == "owner@acmeplumbing.ca"
    print("unsubscribe by subject: OK")


def test_unsubscribe_by_reply_body():
    """The footer asks for a plain reply, so the word arrives in the body."""
    for body in ("unsubscribe", "Unsubscribe please.", "Please take me off your list.",
                 "opt out", "remove me", "stop emailing me"):
        blob = _inbound("%s\n\nOn Mon, 9 Mar 2026 at 09:14, Umar wrote:\n> %s" % (body, FOOTER))
        assert M._unsubscribe_sender(blob) == "owner@acmeplumbing.ca", body
    print("unsubscribe by reply body: OK")


def test_a_quoted_footer_is_not_an_unsubscribe():
    """The expensive false positive: our own footer comes back in every reply.

    Reading the whole body would suppress everyone who answers, including the
    ones saying yes — and suppression cancels their remaining follow-ups.
    """
    for quote in ("On Mon, 9 Mar 2026 at 09:14, Umar Farooq <umar@autoarmy.io> wrote:\n> %s",
                  "-----Original Message-----\nFrom: Umar\n%s",
                  "________________________________\n%s",
                  "> %s"):
        blob = _inbound("Yes, interested — can you do Thursday?\n\n" + quote % FOOTER)
        assert M._unsubscribe_sender(blob) == "", quote[:30]

    # And a reply that says nothing at all is not an opt-out either.
    assert M._unsubscribe_sender(_inbound("Thanks, will read this week.\n\n> " + FOOTER)) == ""
    print("a quoted footer is not an unsubscribe: OK")


def test_unsubscribe_from_an_html_only_reply():
    blob = _inbound("<p>Please <b>unsubscribe</b> me.</p>", content_type="text/html")
    assert M._unsubscribe_sender(blob) == "owner@acmeplumbing.ca"
    print("html-only unsubscribe: OK")


def test_unsubscribe_parsing_never_raises():
    for blob in (b"", b"not a message at all", b"From: \r\n\r\nunsubscribe",
                 b"Subject: unsubscribe\r\nFrom: <>\r\n\r\n", None):
        assert isinstance(M._unsubscribe_sender(blob or b""), str)
    # Truncated mid-body — every fetch is capped, so this is the normal case.
    truncated = _inbound("unsubscribe\n\nOn Mon, 9 Mar 2026 at 09:1")[:120]
    assert isinstance(M._unsubscribe_sender(truncated), str)
    print("unsubscribe parsing never raises: OK")


if __name__ == "__main__":
    test_headers()
    test_unsubscribe_falls_back_to_sender()
    test_supplied_message_id()
    test_header_injection()
    test_alternative_ordering()
    test_plain_only_when_no_html()
    test_no_tracking()
    test_unicode_body_and_subject()
    test_never_raises()
    test_quota_classification()
    test_auth_classification()
    test_recipient_classification()
    test_conn_and_other_classification()
    test_app_password_normalisation()
    test_missing_credentials_never_touch_the_network()
    test_imap_disabled_returns_empty()
    test_bounce_parsing()
    test_unsubscribe_by_subject()
    test_unsubscribe_by_reply_body()
    test_a_quoted_footer_is_not_an_unsubscribe()
    test_unsubscribe_from_an_html_only_reply()
    test_unsubscribe_parsing_never_raises()
    print("\nALL MAILER TESTS PASSED")
