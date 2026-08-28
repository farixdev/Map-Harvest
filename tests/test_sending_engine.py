"""The sending engine end to end, and the record it keeps of what it sent.

Run:  venv/Scripts/python.exe -m pytest tests/test_sending_engine.py -q

Everything here is asserted against the database and the wire, never against
the log. A campaign that *says* it sent two hundred messages and a campaign
that sent them are the same log and different `messages` tables, and the second
is the one the user's Gmail account lives or dies by. So the whole of a 200-lead
campaign is planned, run to its last follow-up over a clock the test moves, and
then read back out of sqlite: one first touch per lead, nothing outside the
window, nothing over a cap, no two sends on an account inside the minimum gap,
every chaser threaded onto the first touch and leaving from the address that
first touch left from, and an opt-out cancelling everything still owed.

No socket is opened. `stub_smtp` replaces `SmtpSender` and keeps the built
`EmailMessage` objects, so the headers a recipient's client would read are
available to assert on directly; `stub_imap` stands in for the three IMAP round
trips. Nothing is slept on: `fake_clock` is the same `_Clock` the schedule tests
use, and `_run_to_the_end` steps it to the next scheduled message.

The second half covers the two things the store has to survive — a process that
dies between the SMTP hand-off and the write, and the question "what did that
message actually say?" — and the model client's cost behaviour.

`SETTINGS_DIR`, `TEMPLATES_PATH` and the AI cache are all redirected into a
temp directory. `core.outreach_db` and `core.ai` both resolve their paths
through `settings.SETTINGS_DIR` on every call, so one redirect covers the store,
the reply cache and the token counter.
"""

import contextlib
import os
import sys
import tempfile
import time
from datetime import date, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ai as AI  # noqa: E402
from core import campaign as C  # noqa: E402
from core import mailer as M  # noqa: E402
from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402

PROFILE = {"company": "Auto Army", "sender_name": "Umar", "sender_title": "",
           "website": "autoarmy.io", "reply_to": "umar@autoarmy.io", "phone": "",
           "postal_address": "1 King St W, Toronto ON", "calendar_link": "",
           "services": [], "proof_points": [], "tone": "direct"}

# The shipped defaults, near enough: a working week, an eight-hour window, forty
# a day and twelve an hour per account, and a gap drawn from a minute to four.
# Named here so the assertions can read the rule they are checking rather than
# repeating its value.
RULES = {
    "send_days": [0, 1, 2, 3, 4],
    "send_start_hour": 9,
    "send_end_hour": 17,
    "send_timezone": "local",
    "send_min_gap_sec": 60,
    "send_max_gap_sec": 240,
    "daily_cap_per_account": 40,
    "hourly_cap_per_account": 12,
    "warmup_enabled": False,
    "warmup_start": 10, "warmup_step": 5, "warmup_max": 40,
}


def _account(email: str, **extra) -> dict:
    return {"email": email, "app_password": "x", "display_name": "", "daily_cap": 40,
            "enabled": True, "warmup_started": "", "imap_enabled": False, **extra}


def _settings(accounts: int = 2, **overrides) -> dict:
    base = dict(RULES)
    base.update({
        "audit_enabled": False,          # no site crawl: these tests are offline
        "followup_enabled": True, "followup_gap_days": 4, "followup_max_steps": 2,
        "dry_run": False, "sender_profile": PROFILE,
        "smtp_accounts": [_account("s%d@shop.test" % index) for index in range(accounts)],
    })
    base.update(overrides)
    return base


@contextlib.contextmanager
def temp_db():
    """A throwaway store with every profile path pointed into it.

    `core.ai` reads `SETTINGS_DIR` for its reply cache and `core.settings` for
    the token counter, so this one redirect keeps a personalisation test out of
    the developer's real ~/.mapharvest as well as the store.
    """
    saved = (ST.SETTINGS_DIR, ST.SETTINGS_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        ST.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        assert not os.path.realpath(AI.cache_path()).startswith(
            os.path.realpath(os.path.join(os.path.expanduser("~"), ".leadforge")))
        try:
            yield DB.connect(os.path.join(tmp, "outreach.db"))
        finally:
            DB.close_all()
            ST.SETTINGS_DIR, ST.SETTINGS_PATH = saved


class _Clock:
    """A clock the test moves, standing in for `time` inside `core.campaign`."""

    def __init__(self, now: float):
        self.now = float(now)

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


@contextlib.contextmanager
def fake_clock(now: float):
    original = C.time
    C.time = clock = _Clock(now)
    try:
        yield clock
    finally:
        C.time = original


@contextlib.contextmanager
def stub_smtp(fail=None):
    """Stand in for SmtpSender. Yields (accounts opened, [(account, message)]).

    `fail(index, message)` may return an error string to refuse one delivery,
    which is how the failure paths are exercised without a server.
    """
    opened, wire = [], []

    class _Sender:
        def __init__(self, email, app_password, display_name=""):
            opened.append(email)
            self.email = email

        def send(self, message):
            error = fail(len(wire), message) if fail else ""
            wire.append((self.email, message))
            return (not error), error

        def close(self) -> None:
            pass

    original = M.SmtpSender
    M.SmtpSender = _Sender
    try:
        yield opened, wire
    finally:
        M.SmtpSender = original


@contextlib.contextmanager
def stub_imap(*, bounces=(), unsubscribes=(), replies=()):
    saved = (M.check_bounces, M.check_unsubscribes, M.check_replies)
    M.check_bounces = lambda *a, **k: list(bounces)
    M.check_unsubscribes = lambda *a, **k: list(unsubscribes)
    M.check_replies = lambda *a, **k: list(replies)
    try:
        yield
    finally:
        M.check_bounces, M.check_unsubscribes, M.check_replies = saved


def _messages(conn, campaign_id: int) -> list:
    return DB._query(conn, "SELECT * FROM messages WHERE campaign_id = ? "
                           "ORDER BY scheduled_at, id", (campaign_id,))


def _plan(conn, count: int, **overrides):
    """(plan, campaign_id, settings) for `count` fresh leads."""
    settings = _settings(**overrides)
    campaign_id = DB.create_campaign(conn, "run", "", PROFILE, settings)
    leads = [{"email": "lead%03d@x.test" % index, "name": "Biz %d" % index}
             for index in range(count)]
    plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads, template_id="",
                           profile=PROFILE, settings=settings, ai=None)
    assert not plan["error"], plan["error"]
    return plan, campaign_id, settings


def _run_to_the_end(worker, conn, campaign_id: int, clock, *, limit: int = 4000,
                    watch=None) -> list:
    """Run the worker's whole loop, stepping the clock instead of sleeping.

    `watch(order)` is called after each message so a test can interrupt the run
    part way — an unsubscribe landing mid-campaign, say — without reaching into
    the loop.
    """
    order: list = []

    def note(row):
        order.append((int(row["lead_id"]), int(row["step"])))
        if watch is not None:
            watch(order)

    worker.message_sent_signal.connect(note)
    naps = [0]

    def skip_ahead(seconds: float) -> None:
        naps[0] += 1
        if naps[0] > limit:                    # a loop that will not finish
            worker.stop()
            return
        clock.now += max(0.0, float(seconds))
        row = DB._one(conn, "SELECT MIN(scheduled_at) AS next FROM messages "
                            "WHERE campaign_id = ? AND status = 'queued'",
                      (campaign_id,))
        if row.get("next"):
            clock.now = max(clock.now, float(row["next"]))

    worker._nap = skip_ahead
    worker.run()
    return order


def _by_account(rows: list) -> dict:
    out: dict = {}
    for row in sorted(rows, key=lambda r: r["sent_at"]):
        out.setdefault(row["account_email"], []).append(row["sent_at"])
    return out


# ── A whole campaign, from plan to last follow-up ────────────────────────────

def test_a_two_hundred_lead_campaign_lands_in_the_store_as_planned():
    """Every rule the send loop exists to keep, asserted against sqlite.

    Two hundred leads, two accounts, two follow-up steps: six hundred messages
    over eight working days. What is being guarded is not that the run finishes
    — it is that the six hundred rows it leaves behind describe a stream a mail
    provider will not flag and a stranger will not be written to twice.
    """
    with temp_db() as conn:
        plan, campaign_id, settings = _plan(conn, 200)
        assert (plan["queued"], plan["followups"]) == (200, 400), plan

        with fake_clock(time.time()) as clock, stub_smtp() as (opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
            order = _run_to_the_end(worker, conn, campaign_id, clock)

        rows = _messages(conn, campaign_id)
        assert len(rows) == 600 and len(order) == 600 and len(wire) == 600
        assert {row["status"] for row in rows} == {"sent"}, \
            sorted({row["status"] for row in rows})
        assert opened, "nothing ever opened an SMTP session"

        # One first touch each, and one only: a second cold email to the same
        # stranger is the compliance failure the whole planner guards against.
        firsts = [row for row in rows if row["step"] == 0]
        assert len(firsts) == 200
        assert len({row["lead_id"] for row in firsts}) == 200

        # Inside the window, on a working day.
        for row in rows:
            when = datetime.fromtimestamp(row["sent_at"])
            assert C.in_send_window(row["sent_at"], settings), when
            assert when.weekday() in settings["send_days"], when
            assert settings["send_start_hour"] <= when.hour < settings["send_end_hour"], when

        per_account = _by_account(rows)
        assert len(per_account) == 2, "one account carried the whole campaign"

        for email, stamps in per_account.items():
            # Daily cap, per account, per local day.
            by_day: dict = {}
            for ts in stamps:
                day = datetime.fromtimestamp(ts).date()
                by_day[day] = by_day.get(day, 0) + 1
            assert max(by_day.values()) <= settings["daily_cap_per_account"], \
                (email, sorted(by_day.items()))

            # Hourly cap, as a rolling window rather than a clock hour.
            for index, ts in enumerate(stamps):
                inside = sum(1 for other in stamps[:index + 1] if other > ts - 3600.0)
                assert inside <= settings["hourly_cap_per_account"], (email, index, inside)

            # The pacing gap. A message every N seconds on the dot is the
            # loudest automation fingerprint there is, so this is a floor and
            # not an equality.
            gaps = [b - a for a, b in zip(stamps, stamps[1:])]
            assert min(gaps) >= settings["send_min_gap_sec"], (email, min(gaps))
            assert len(set(round(gap) for gap in gaps)) > 20, \
                "the stream became a metronome"

        # Threading: a chaser answers the first touch, and leaves from the
        # address that first touch left from.
        parent = {row["lead_id"]: row for row in firsts}
        for row in rows:
            if not row["step"]:
                continue
            first = parent[row["lead_id"]]
            assert first["message_id"], "a first touch was sent without a Message-ID"
            assert row["account_email"] == first["account_email"], (
                "follow-up %d for lead %d left from %s, its thread from %s"
                % (row["step"], row["lead_id"], row["account_email"],
                   first["account_email"]))

        by_id = {message["Message-ID"]: (account, message) for account, message in wire}
        threaded = 0
        for account, message in wire:
            parent_id = message["In-Reply-To"]
            if not parent_id:
                continue
            threaded += 1
            assert parent_id in by_id, "a chaser answered a message never sent"
            from_account, first = by_id[parent_id]
            assert from_account == account and first["From"] == message["From"], (
                "%s answered a conversation belonging to %s"
                % (account, from_account))
            assert message["References"] == parent_id
        assert threaded == 400, threaded
        print("a 200-lead campaign lands in the store as planned: OK")


def test_a_follow_up_never_leaves_from_a_different_account():
    """The measured symptom: 186 of 400 chasers went out from the wrong address.

    `_pick_account` handed each message to whichever account was out of its
    pacing gap first, and a follow-up carries `In-Reply-To` for the first touch.
    So nearly half the chasers arrived from a second address, threaded into a
    conversation that address had never been part of — which the recipient sees
    as a stranger answering somebody else's mail to them.
    """
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 40, followup_max_steps=1, followup_gap_days=1,
            send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0, send_end_hour=24)

        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
            _run_to_the_end(worker, conn, campaign_id, clock)

        rows = _messages(conn, campaign_id)
        firsts = {row["lead_id"]: row for row in rows if row["step"] == 0}
        chasers = [row for row in rows if row["step"] == 1]
        assert len(chasers) == 40 and len(firsts) == 40

        strayed = [row for row in chasers
                   if row["account_email"] != firsts[row["lead_id"]]["account_email"]]
        assert not strayed, "%d of %d chasers left from the wrong account" % (
            len(strayed), len(chasers))

        # And on the wire, which is what the recipient actually sees.
        sent_from = {message["Message-ID"]: message["From"] for _a, message in wire}
        for _account, message in wire:
            if message["In-Reply-To"]:
                assert sent_from[message["In-Reply-To"]] == message["From"]
        print("a follow-up never leaves from a different account: OK")


def test_an_unsubscribe_mid_run_cancels_everything_owed_to_that_lead():
    """An opt-out has to reach the chasers already scheduled days out.

    By the time somebody replies "unsubscribe", their +4 and +8 day messages
    are rows in the queue. A suppression that only stopped the next one would
    keep mailing the person who asked to be left alone.
    """
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 12, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_gap_days=1)
        target = DB._query(conn, "SELECT id, email FROM leads ORDER BY id LIMIT 1")[0]

        opted_out = []

        def watch(order):
            if len(order) == 3 and not opted_out:
                opted_out.append(True)
                DB.suppress(conn, target["email"], "unsubscribed")

        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
            _run_to_the_end(worker, conn, campaign_id, clock, watch=watch)

        assert opted_out, "the run finished before the opt-out could land"
        theirs = [row for row in _messages(conn, campaign_id)
                  if row["lead_id"] == target["id"]]
        assert theirs, "the suppressed lead had no messages at all"
        assert not [row for row in theirs if row["status"] == "queued"], \
            "a message stayed queued for a suppressed lead"
        assert all(row["status"] in ("sent", "skipped") for row in theirs), theirs
        assert DB.get_lead(conn, target["id"])["status"] == "suppressed"

        # Nothing addressed to them left after the opt-out.
        after = [message for _a, message in wire[3:]
                 if target["email"] in str(message["To"])]
        assert not after, "%d message(s) went to an address that had opted out" % len(after)
        print("an unsubscribe cancels everything owed to that lead: OK")


# ── One campaign must not be hidden behind another ───────────────────────────

def test_a_campaign_is_not_starved_by_another_campaigns_backlog():
    """The measured symptom: a fresh campaign sent nothing, for ever.

    `due_messages` applies its LIMIT in SQLite, and the worker filtered the
    result in Python. A stale campaign with two hundred overdue messages
    therefore filled the whole window, this campaign read its own queue as
    empty, and the run napped in a circle while the screen said "Sending".
    """
    with temp_db() as conn:
        settings = _settings(1, send_days=[0, 1, 2, 3, 4, 5, 6],
                             send_start_hour=0, send_end_hour=24,
                             send_min_gap_sec=0, send_max_gap_sec=0)
        stale = DB.create_campaign(conn, "stale", "", PROFILE, settings)
        for index in range(250):
            lead_id = DB.upsert_lead(conn, {"email": "old%04d@x.test" % index,
                                            "name": "Old"})
            DB.queue_message(conn, {"campaign_id": stale, "lead_id": lead_id, "step": 0,
                                    "subject": "hi", "body_text": "hi", "body_html": "",
                                    "account_email": "s0@shop.test",
                                    "scheduled_at": time.time() - 600})

        mine = DB.create_campaign(conn, "mine", "", PROFILE, settings)
        for index in range(5):
            lead_id = DB.upsert_lead(conn, {"email": "new%04d@x.test" % index,
                                            "name": "New"})
            DB.queue_message(conn, {"campaign_id": mine, "lead_id": lead_id, "step": 0,
                                    "subject": "hi", "body_text": "hi", "body_html": "",
                                    "account_email": "s0@shop.test",
                                    "scheduled_at": time.time() - 60})

        worker = C.OutreachWorker(mine, settings, dry_run=False)
        worker._ramp_start = date.today()
        assert len(worker._due(conn, time.time())) == 5, \
            "the worker cannot see its own queue"

        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, wire), stub_imap():
            _run_to_the_end(worker, conn, campaign_id=mine, clock=clock)

        assert len(wire) == 5, "the campaign sent %d of its 5 messages" % len(wire)
        assert {row["status"] for row in _messages(conn, mine)} == {"sent"}
        # And it left the other campaign alone.
        assert {row["status"] for row in _messages(conn, stale)} == {"queued"}
        print("a campaign is not starved by another campaign's backlog: OK")


# ── Crash and resume ─────────────────────────────────────────────────────────

class _CrashingSender:
    """Takes the message and then the process dies, as a power cut would."""

    def __init__(self, wire: list):
        self.wire = wire

    def send(self, message):
        self.wire.append(message)
        raise KeyboardInterrupt("the process died after the server took it")

    def close(self) -> None:
        pass


def _crash_one_send(conn, campaign_id: int, settings: dict, row: dict) -> list:
    wire: list = []
    worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
    worker._ramp_start = date.today()
    worker._senders["s0@shop.test"] = _CrashingSender(wire)
    try:
        worker._send(conn, row, ST.smtp_accounts(settings)[0], time.time())
    except KeyboardInterrupt:
        pass
    return wire


def test_a_crash_after_the_hand_off_keeps_the_thread_and_the_quota():
    """The at-least-once window, closed on the three things it used to lose.

    A message the process died in the middle of comes back as 'sent' and is
    never retried — that part was already right. What it lost was everything
    else about the message: the `Message-ID` was minted in memory and written
    only after the send, so the recovered row had none. Its lead's chaser then
    threaded onto nothing, a reply to it was never matched against any row, and
    the sequence kept chasing somebody who had already answered. The quota went
    the same way: the send was never charged, so the restart put one message per
    crash over the account's daily ceiling.
    """
    with temp_db() as conn:
        settings = _settings(1, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
                             send_end_hour=24)
        campaign_id = DB.create_campaign(conn, "crash", "", PROFILE, settings)
        lead_id = DB.upsert_lead(conn, {"email": "one@x.test", "name": "One"})
        first = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                        "step": 0, "subject": "hi", "body_text": "hi",
                                        "body_html": "", "account_email": "s0@shop.test",
                                        "scheduled_at": time.time() - 60})
        chaser = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                         "step": 1, "subject": "again",
                                         "body_text": "again", "body_html": "",
                                         "account_email": "s0@shop.test",
                                         "scheduled_at": time.time() - 30})

        row = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (first,))
        wire = _crash_one_send(conn, campaign_id, settings, row)
        assert len(wire) == 1, "the server never saw the message"
        header = wire[0]["Message-ID"]

        claimed = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (first,))
        assert claimed["status"] == "sending", claimed["status"]
        assert claimed["message_id"] == header, (claimed["message_id"], header)
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends") == 1, \
            "a message Gmail has already seen was not charged to the account"

        # ── restart ──
        worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
        worker._recover_claimed(conn)
        recovered = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (first,))
        assert recovered["status"] == "sent"
        assert recovered["message_id"] == header
        assert DB.first_touch_message_id(conn, campaign_id, lead_id) == header

        chaser_row = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (chaser,))
        assert worker._thread_parent(conn, chaser_row, "s0@shop.test") == header, \
            "the chaser lost the thread its first touch started"
        assert worker._message_by_header(conn, header).get("id") == first, \
            "a reply to that message would never have been recognised"
        print("a crash after the hand-off keeps the thread and the quota: OK")


def test_a_crash_never_sends_the_same_message_twice():
    """Restart the whole run after a crash and count what reaches the server."""
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 6, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_enabled=False)
        rows = _messages(conn, campaign_id)
        assert len(rows) == 6

        wire = _crash_one_send(conn, campaign_id, settings, rows[0])
        assert len(wire) == 1
        crashed = str(wire[0]["To"])

        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, again), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
            _run_to_the_end(worker, conn, campaign_id, clock)

        addressed = [str(message["To"]) for _a, message in again]
        assert crashed not in addressed, \
            "the interrupted message went to the same stranger a second time"
        assert len(addressed) == 5 and len(set(addressed)) == 5, addressed

        after = _messages(conn, campaign_id)
        assert {row["status"] for row in after} == {"sent"}, \
            "a message was silently dropped"
        assert len(after) == 6
        errors = [row["error"] for row in after if row["error"]]
        assert len(errors) == 1 and "interrupted" in errors[0], errors
        print("a crash never sends the same message twice: OK")


# ── What was actually sent ───────────────────────────────────────────────────

def test_what_left_the_machine_can_be_read_back_exactly():
    """The store keeps the bytes, not a recipe for rebuilding them.

    Before this there was no way inside the app to see the subject, body,
    recipient, account or time of anything already sent: every surface counted
    messages and none of them showed one, so "did that go out with the right
    footer?" could only be answered from the Gmail account's Sent folder.
    """
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 2, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_enabled=False)

        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
            _run_to_the_end(worker, conn, campaign_id, clock)
        assert len(wire) == 2

        listed = DB.sent_messages(conn, campaign_id)
        assert len(listed) == 2 and all(row["has_transcript"] for row in listed)
        assert all(row["to_email"] for row in listed), "the recipient was not joined in"

        for row in listed:
            raw = DB.transcript(conn, row["id"])
            assert raw, "nothing was kept for a message that was sent"
            wired = next(message for _a, message in wire
                         if message["Message-ID"] == row["message_id"])
            assert raw == wired.as_string(), "the record is not what left"

            read = M.read_wire(raw)
            headers = dict(read["headers"])
            assert headers["Subject"] == str(wired["Subject"])
            assert row["to_email"] in headers["To"]
            assert headers["From"].endswith("<%s>" % row["account_email"]) or \
                headers["From"] == row["account_email"]
            assert headers["Message-ID"] == row["message_id"]
            assert "List-Unsubscribe" in headers, "the header that matters is missing"
            assert read["text"].strip(), "the body came back empty"
            assert "unsubscribe" in read["text"].lower(), "the footer was not kept"
            assert read["html"], "the HTML part was not kept"
        print("what left the machine can be read back exactly: OK")


def test_the_record_survives_the_template_being_edited_afterwards():
    """Why the bytes are stored rather than re-rendered on demand.

    The template store is editable and the sender profile changes mid-campaign,
    so rebuilding a sent message from today's copy would show the user a
    message that was never sent, with the authority of a record.
    """
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 1, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_enabled=False)

        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, wire), stub_imap():
            _run_to_the_end(C.OutreachWorker(campaign_id, settings, dry_run=False),
                            conn, campaign_id, clock)

        row = DB.sent_messages(conn, campaign_id)[0]
        before = DB.transcript(conn, row["id"])
        assert before

        # The row the queue was built from is rewritten, as an edited template
        # would rewrite the next one. The record must not move with it.
        DB.mark_message(conn, row["id"], "sent", subject="something else entirely",
                        body_text="different copy")
        assert DB.transcript(conn, row["id"]) == before
        assert "something else entirely" not in DB.transcript(conn, row["id"])
        print("the record survives the template being edited: OK")


def test_a_rehearsal_leaves_no_record_because_nothing_left():
    """A dry run must not fill the sent list with mail nobody received."""
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 3, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_enabled=False, dry_run=True)

        with fake_clock(time.time()) as clock, stub_smtp() as (opened, wire), stub_imap():
            _run_to_the_end(C.OutreachWorker(campaign_id, settings, dry_run=True),
                            conn, campaign_id, clock)

        assert opened == [] and wire == [], "a rehearsal opened a socket"
        assert DB.sent_messages(conn, campaign_id) == []
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sent_mail") == 0
        print("a rehearsal leaves no record because nothing left: OK")


# ── The model client: cost, fallback and safety ──────────────────────────────

REPLY = ('{"subject": "booking on acme.test", "opener": "Your acme.test booking '
         'page ends at one contact form.", "ps": "Happy to wire acme.test a '
         'booking bot."}')
DIGEST = ("SITE: acme.test | Acme Plumbing\nGAP: no online booking\n"
          "GAP: the emergency page ends at a contact form")


def _ai_settings() -> dict:
    settings = {"ai_provider": "auto", "groq_model": "llama-3.1-8b",
                "openrouter_model": "meta/llama-3.1-8b",
                "ai_monthly_token_cap": 100_000, "ai_tokens_used": 0,
                "ai_max_tokens_per_lead": 220}
    ST.set_secret(settings, "groq_api_key", "gsk_test")
    ST.set_secret(settings, "openrouter_api_key", "or_test")
    return settings


@contextlib.contextmanager
def stub_model(answers):
    """Stand in for `_chat`. `answers` is a list of (payload, error) per call."""
    calls: list = []
    queue = list(answers)

    def _chat(provider, api_key, model, messages, **kwargs):
        calls.append((provider, messages))
        return queue.pop(0) if queue else (None, "no answer configured")

    original, AI._chat = AI._chat, _chat
    AI._cache = None                       # a cold cache, like a fresh process
    try:
        yield calls
    finally:
        AI._chat = original
        AI._cache = None


def _payload(content: str, tokens: int = 0) -> dict:
    out = {"choices": [{"message": {"content": content}}]}
    if tokens:
        out["usage"] = {"total_tokens": tokens}
    return out


def test_the_disk_cache_prevents_a_second_spend_on_a_re_run():
    """Re-running a five-hundred lead campaign after a copy tweak costs nothing.

    The cache is the only thing standing between an edited subject line and a
    second bill for the whole list, so this asserts on the provider call count
    and the token counter, not on the returned strings.
    """
    with temp_db():
        settings = _ai_settings()
        with stub_model([(_payload(REPLY, 640), "")]) as calls:
            first = AI.AIClient(settings).personalize(
                business_name="Acme Plumbing", digest=DIGEST, profile=PROFILE,
                template_id="gap_direct")
            assert first["ok"] and not first["cached"] and first["tokens"] == 640
            assert len(calls) == 1

            spent = ST._to_int(settings.get("ai_tokens_used"))
            assert spent == 640, spent

            AI._cache = None               # the app is closed and reopened
            second = AI.AIClient(settings).personalize(
                business_name="Acme Plumbing", digest=DIGEST, profile=PROFILE,
                template_id="gap_direct")
            assert second["cached"] and second["ok"], second
            assert (second["subject"], second["opener"]) == \
                   (first["subject"], first["opener"])
            assert len(calls) == 1, "the re-run called the provider again"
            assert ST._to_int(settings.get("ai_tokens_used")) == spent

        # And it is on disk, under the redirected profile — never the real one.
        assert os.path.exists(AI.cache_path())
        print("the disk cache prevents a second spend: OK")


def test_a_spent_budget_still_answers_from_the_cache():
    """A cached lead is free, so the budget must not gate the cache read."""
    with temp_db():
        settings = _ai_settings()
        with stub_model([(_payload(REPLY, 640), "")]):
            AI.AIClient(settings).personalize(business_name="Acme Plumbing",
                                              digest=DIGEST, profile=PROFILE,
                                              template_id="gap_direct")
        settings["ai_tokens_used"] = settings["ai_monthly_token_cap"]
        with stub_model([]) as calls:
            AI._cache = None
            out = AI.AIClient(settings).personalize(
                business_name="Acme Plumbing", digest=DIGEST, profile=PROFILE,
                template_id="gap_direct")
        assert out["ok"] and out["cached"] and calls == []
        print("a spent budget still answers from the cache: OK")


def test_a_dead_provider_falls_back_without_saying_so_to_the_lead():
    with temp_db():
        settings = _ai_settings()
        with stub_model([(None, "503 upstream unavailable"),
                         (_payload(REPLY, 600), "")]) as calls:
            client = AI.AIClient(settings)
            out = client.personalize(business_name="Acme Plumbing", digest=DIGEST,
                                     profile=PROFILE, template_id="gap_direct")
        assert out["ok"] and out["provider"] == "openrouter", out
        assert [name for name, _messages in calls] == ["groq", "openrouter"]
        assert client.last_error == "", "a recovered failure was reported as one"
        print("a dead provider falls back silently: OK")


def test_a_malformed_model_reply_can_never_reach_a_rendered_email():
    """Every shape of bad answer, and none of them may leave the client."""
    bad = (
        "Sure! Here is {{business}} for you.",            # a template token
        '{"subject": "hi", "opener": "Call [business] today."}',  # a placeholder
        '{"subject": "hi", "opener": "one line"}',        # a missing key
        '{"subject": "", "opener": "x", "ps": "y"}',      # an empty field
        "not json at all",
        '{"subject": 4, "opener": "x", "ps": "y"}',       # a non-string
    )
    with temp_db():
        for content in bad:
            settings = _ai_settings()
            with stub_model([(_payload(content, 300), ""),
                             (_payload(content, 300), "")]):
                client = AI.AIClient(settings)
                out = client.personalize(business_name="Nowhere Ltd",
                                         digest="SITE: nowhere%d.test | Nowhere Ltd"
                                                % bad.index(content),
                                         profile=PROFILE, template_id="gap_direct")
            assert not out["ok"], content
            assert (out["subject"], out["opener"], out["ps"]) == ("", "", ""), out
            assert client.last_error, "a refusal with no reason for the log"
            # Charged, because the provider billed for it either way — a broken
            # model must not be able to spend the month for free.
            assert ST._to_int(settings.get("ai_tokens_used")) == 600, content
        print("a malformed model reply never reaches an email: OK")


def test_one_lead_costs_between_five_hundred_and_nine_hundred_tokens():
    """The per-lead budget the module is built around, measured not assumed.

    The prompt is fixed wording plus `core.audit.digest`, and both are capped,
    so this is arithmetic rather than a guess — but it is arithmetic that a
    longer system prompt or a wider digest cap would quietly break, and the
    cost is paid on every lead of every campaign.
    """
    with temp_db():
        settings = _ai_settings()
        captured: list = []
        with stub_model([(_payload(REPLY, 0), "")]):
            AI._chat = lambda provider, key, model, messages, **kw: (
                captured.append(messages) or (_payload(REPLY, 0), ""))
            out = AI.AIClient(settings).personalize(
                business_name="Acme Plumbing", digest=DIGEST, profile=dict(
                    PROFILE, services=["AI voice agents", "lead follow-up automation",
                                       "CRM automation", "booking bots"]),
                template_id="gap_direct")

        assert out["ok"] and captured, out
        prompt = "".join(part["content"] for part in captured[0])
        # A worst case rather than this digest's: `core.audit.digest` may hand
        # over the full 1200 characters, and the reply is capped by the setting.
        widest = len(prompt) - len(DIGEST) + AI._DIGEST_MAX_CHARS
        floor = len(prompt) // 4
        ceiling = widest // 4 + min(AI._MAX_MAX_TOKENS, settings["ai_max_tokens_per_lead"])
        assert 500 <= ceiling <= 900, (floor, ceiling, len(prompt))
        assert floor < ceiling
        print("one lead costs %d-%d tokens: OK" % (floor, ceiling))


def test_the_reply_cache_never_resolves_under_the_real_profile():
    """Profile safety, as an assertion rather than as a convention.

    `CACHE_PATH` used to be `os.path.join(SETTINGS_DIR, ...)` evaluated at
    import, so it froze to whatever the profile directory was on first import
    and nothing could redirect it afterwards. One personalised lead in the test
    suite then read and rewrote the real user's cache file.
    """
    real = os.path.realpath(os.path.join(os.path.expanduser("~"), ".leadforge"))
    with temp_db() as _conn:
        redirected = os.path.realpath(AI.cache_path())
        assert not redirected.startswith(real), redirected
        assert redirected.startswith(os.path.realpath(ST.SETTINGS_DIR))
    print("the reply cache follows the redirect: OK")


# ── The screen, over a queue it did not plan ─────────────────────────────────
# Qt is imported here rather than at the top so the whole first half of this
# file can run without a display server; the screen tests build one offscreen.


@contextlib.contextmanager
def screen_over(conn):
    """An OutreachScreen reading the store this test built.

    Built inside the redirect and driven through its own handlers rather than
    through the event loop: nothing here is about painting, and a screen that
    needs `processEvents` to answer a question is a screen whose answer depends
    on timing.
    """
    from PyQt5.QtWidgets import QApplication
    from ui import components as CO
    from ui import screen_outreach as SO
    from ui import theme as TH

    app = QApplication.instance() or QApplication([])
    CO.use_theme(TH.theme())
    screen = SO.OutreachScreen()
    screen.conn = conn
    try:
        yield screen
    finally:
        screen._tick.stop()
        screen.deleteLater()
        app.processEvents()


def test_the_screen_shows_what_was_actually_sent():
    """The thing the user asked for by name, from the tab they watch it on."""
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 2, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_enabled=False)
        with fake_clock(time.time()) as clock, stub_smtp() as (_opened, wire), stub_imap():
            _run_to_the_end(C.OutreachWorker(campaign_id, settings, dry_run=False),
                            conn, campaign_id, clock)
        assert len(wire) == 2

        from PyQt5.QtCore import Qt
        from ui.screen_outreach import _SentMailDialog

        with screen_over(conn) as screen:
            screen.settings = settings
            screen._campaign_id = campaign_id
            screen._refresh_sent_mail()

            assert screen.sent_list.count() == 2, screen.sent_list.count()
            item = screen.sent_list.item(0)
            message_id = int(item.data(Qt.UserRole))
            assert message_id, "the list row does not know which message it is"
            assert item.data(Qt.UserRole + 1) is True

            row = DB._one(conn, "SELECT messages.*, leads.email AS to_email "
                                "FROM messages LEFT JOIN leads "
                                "ON leads.id = messages.lead_id "
                                "WHERE messages.id = ?", (message_id,))
            dialog = _SentMailDialog(row, DB.transcript(conn, message_id), screen)
            try:
                shown = dialog._message_html()
                sent = next(message for _a, message in wire
                            if message["Message-ID"] == row["message_id"])
                assert str(sent["Subject"]) in dialog._subject()
                assert row["to_email"] in shown
                assert row["account_email"] in shown
                assert "List-Unsubscribe" in shown, "the headers that matter are missing"
                assert "unsubscribe" in shown.lower(), "the footer is missing"
                assert row["to_email"] in dialog._provenance()
                assert row["account_email"] in dialog._provenance()

                dialog._view.button(1).setChecked(True)
                dialog._paint()          # the Source view must not throw either
            finally:
                dialog.deleteLater()
            print("the screen shows what was actually sent: OK")


def test_the_screen_counts_a_rehearsal_instead_of_reporting_zero():
    """The counter the user watches, through the operation it reports on.

    A dry run writes 'rehearsed' and never 'sent', and the headline read
    `stats["sent"]`: five hundred messages could be built and the screen said
    "Sending — 0 of 500 done" from the first to the last, with every tile on
    zero behind it.
    """
    with temp_db() as conn:
        _plan_out, campaign_id, settings = _plan(
            conn, 5, send_days=[0, 1, 2, 3, 4, 5, 6], send_start_hour=0,
            send_end_hour=24, followup_enabled=False, dry_run=True)
        for row in _messages(conn, campaign_id)[:4]:
            DB.mark_message(conn, row["id"], "rehearsed", sent_at=time.time())

        with screen_over(conn) as screen:
            screen.settings = settings
            screen._campaign_id = campaign_id
            screen._sending = True
            screen.send_worker = C.OutreachWorker(campaign_id, settings, dry_run=True)

            headline, _why = screen._send_health()
            assert "0 of 5" not in headline, headline
            assert "4 of 5" in headline and "Rehears" in headline, headline

            screen._paint_stats(screen._stats())
            assert screen.tiles["rehearsed"].value_label.text() == "4"
            # `isHidden`, not `isVisibleTo`: the Stats page is a card in a stack
            # and every page but the current one has its parent explicitly
            # hidden, so only the tile's own flag says anything about the tile.
            assert not screen.tiles["rehearsed"].isHidden()

            # And a campaign that has never been rehearsed does not carry the tile.
            screen._paint_stats({"queued": 3, "sent": 3})
            assert screen.tiles["rehearsed"].isHidden()
            print("the screen counts a rehearsal: OK")


def test_the_countdown_finds_this_campaign_behind_another_ones_backlog():
    """`_send_health` read an empty answer as "every address is suppressed".

    The countdown asked `due_messages` for a fixed window of rows and then
    filtered by campaign, so a campaign queued behind four hundred of somebody
    else's messages had no next send — and the screen's explanation for that is
    the confident, wrong sentence about suppression.
    """
    with temp_db() as conn:
        settings = _settings(1, send_days=[0, 1, 2, 3, 4, 5, 6],
                             send_start_hour=0, send_end_hour=24)
        stale = DB.create_campaign(conn, "stale", "", PROFILE, settings)
        for index in range(450):
            lead_id = DB.upsert_lead(conn, {"email": "old%04d@x.test" % index,
                                            "name": "Old"})
            DB.queue_message(conn, {"campaign_id": stale, "lead_id": lead_id, "step": 0,
                                    "subject": "hi", "body_text": "hi", "body_html": "",
                                    "account_email": "s0@shop.test",
                                    "scheduled_at": time.time() - 600})
        mine = DB.create_campaign(conn, "mine", "", PROFILE, settings)
        lead_id = DB.upsert_lead(conn, {"email": "new@x.test", "name": "New"})
        due_at = time.time() - 60
        DB.queue_message(conn, {"campaign_id": mine, "lead_id": lead_id, "step": 0,
                                "subject": "hi", "body_text": "hi", "body_html": "",
                                "account_email": "s0@shop.test", "scheduled_at": due_at})

        with screen_over(conn) as screen:
            screen.settings = settings
            screen._campaign_id = mine
            assert abs(screen._next_due_ts() - due_at) < 1.0, screen._next_due_ts()
            headline, why = screen._send_health()
            assert "suppressed" not in why, (headline, why)
            assert "Stalled" not in headline, headline
            print("the countdown finds this campaign behind a backlog: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL SENDING ENGINE TESTS PASSED")
