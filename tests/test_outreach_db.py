"""Offline tests for core.outreach_db.

Every test runs against a throwaway database in a temp directory, and
`core.settings.SETTINGS_DIR` is redirected there too so that a stray default
`connect()` can never touch a developer's real ~/.leadforge/outreach.db.

Time is never slept on and never read from the clock where it matters: the
quota tests pass explicit epoch timestamps, so the local-midnight boundary is
asserted at a fixed instant rather than whenever the suite happens to run.
"""
import contextlib
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402
from core import whatsapp as WA  # noqa: E402


@contextlib.contextmanager
def temp_db():
    """A fresh outreach.db in a temp dir, closed so Windows can delete it."""
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            yield DB.connect(os.path.join(tmp, "outreach.db"))
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original


UTC = timezone.utc


def _utc(hour: int, minute: int = 0, day: int = 10) -> float:
    """An epoch timestamp on 2026-03-10, chosen so no test depends on today."""
    return datetime(2026, 3, day, hour, minute, tzinfo=UTC).timestamp()


def _lead(conn, email: str, **extra) -> int:
    row = {"email": email, "name": extra.pop("name", "Acme"), **extra}
    return DB.upsert_lead(conn, row)


# ── Schema ───────────────────────────────────────────────────────────────────

def test_init_db_is_idempotent_and_indexed():
    """`idx_messages_campaign` is asserted gone, which is the opposite of before.

    It indexed `messages(campaign_id)` alone, so every query that used it — the
    stats counters, the per-day rollup, the follow-up's thread lookup — narrowed
    to the campaign and then read the table for the columns it actually wanted,
    and a `messages` row carries a whole rendered email. Measured against 20,000
    queued messages that cost 63ms per counter on the GUI thread.
    `idx_messages_campaign_status` opens with the same column and carries those
    columns, which answers all three out of the index; keeping the narrower one
    alongside it left a second btree to maintain on every insert and gave the
    planner a worse option it sometimes took.
    """
    with temp_db() as conn:
        DB.init_db(conn)
        DB.init_db(conn)
        tables = {r["name"] for r in DB._query(
            conn, "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"leads", "campaigns", "messages",
                "suppression", "sends", "events"} <= tables, tables
        indexes = {r["name"] for r in DB._query(
            conn, "SELECT name FROM sqlite_master WHERE type = 'index'")}
        for expected in ("idx_messages_due", "idx_messages_campaign_status",
                         "idx_messages_lead", "idx_messages_step",
                         "idx_messages_header", "idx_sends_account_ts",
                         "idx_leads_status", "idx_leads_domain"):
            assert expected in indexes, expected
        assert "idx_messages_campaign" not in indexes, (
            "superseded by idx_messages_campaign_status, which opens with the "
            "same column and covers the queries that used it")
        assert DB._one(conn, "PRAGMA journal_mode")["journal_mode"].lower() == "wal"
    print("init_db idempotent + indexes + WAL: OK")


def test_rows_are_plain_dicts():
    # The Qt signal layer json-dumps these; a sqlite3.Row would blow up there.
    with temp_db() as conn:
        lead_id = _lead(conn, "a@acme.com")
        lead = DB.get_lead(conn, lead_id)
        assert type(lead) is dict, type(lead)
        assert type(DB.list_leads(conn)[0]) is dict
        assert DB.get_lead(conn, 999) == {}
    print("rows are plain dicts: OK")


# ── Leads ────────────────────────────────────────────────────────────────────

def test_upsert_dedupes_on_lowercased_email():
    with temp_db() as conn:
        first = _lead(conn, "Info@Acme.COM", website="https://www.acme.com/contact")
        second = _lead(conn, "  info@acme.com  ", name="Acme Ltd")
        assert first and first == second, (first, second)
        assert DB.count_leads(conn) == 1
        lead = DB.get_lead(conn, first)
        assert lead["email"] == "info@acme.com", lead["email"]
        assert lead["name"] == "Acme Ltd"
        assert lead["domain"] == "acme.com", lead["domain"]   # www stripped
        assert lead["status"] == "new"
        assert lead["created_at"] and lead["updated_at"]
    print("upsert dedupe on lowercased email: OK")


def test_upsert_update_is_non_destructive():
    with temp_db() as conn:
        lead_id = _lead(conn, "info@acme.com", name="Acme Plumbing",
                        website="https://acme.com", phone="555-0100",
                        city="Toronto", audit={"opportunity_score": 61},
                        ai={"subject": "acme booking"}, opportunity_score=61)

        # A second sighting that knows almost nothing must not blank the rest.
        again = DB.upsert_lead(conn, {
            "email": "INFO@acme.com", "name": "", "website": None,
            "phone": "555-0199", "audit_json": None, "ai_json": "",
            "opportunity_score": 0,
        })
        assert again == lead_id

        lead = DB.get_lead(conn, lead_id)
        assert lead["name"] == "Acme Plumbing"
        assert lead["website"] == "https://acme.com"
        assert lead["city"] == "Toronto"
        assert lead["phone"] == "555-0199"                  # the one real update
        assert lead["opportunity_score"] == 61
        assert "opportunity_score" in lead["audit_json"], lead["audit_json"]
        assert "acme booking" in lead["ai_json"], lead["ai_json"]
    print("upsert non-destructive update: OK")


def test_upsert_rejects_unusable_rows():
    with temp_db() as conn:
        assert DB.upsert_lead(conn, {}) == 0
        assert DB.upsert_lead(conn, {"email": ""}) == 0
        assert DB.upsert_lead(conn, {"email": "not-an-address"}) == 0
        assert DB.upsert_lead(conn, {"name": "no email"}) == 0
        assert DB.count_leads(conn) == 0
    print("upsert rejects rows without an email: OK")


def test_set_lead_audit_never_nulls_and_promotes_status():
    with temp_db() as conn:
        lead_id = _lead(conn, "info@acme.com")
        DB.set_lead_audit(conn, lead_id, {"opportunity_score": 74, "gaps": [{"code": "no_crm_signals"}]},
                          {"subject": "one line"})
        lead = DB.get_lead(conn, lead_id)
        assert lead["status"] == "audited"
        assert lead["opportunity_score"] == 74
        assert "no_crm_signals" in lead["audit_json"]

        # A failed re-audit (unreachable site, no AI budget) keeps what we have.
        DB.set_lead_audit(conn, lead_id, {}, {})
        DB.set_lead_audit(conn, lead_id, None, None)
        lead = DB.get_lead(conn, lead_id)
        assert "no_crm_signals" in lead["audit_json"]
        assert lead["ai_json"] and lead["opportunity_score"] == 74

        # A lead already mailed must not be dragged back to 'audited'.
        DB._write(conn, "UPDATE leads SET status = 'sent' WHERE id = ?", (lead_id,))
        DB.set_lead_audit(conn, lead_id, {"opportunity_score": 80}, {})
        assert DB.get_lead(conn, lead_id)["status"] == "sent"
    print("set_lead_audit non-destructive + status promotion: OK")


def test_list_and_count_leads_filters():
    with temp_db() as conn:
        ids = [_lead(conn, f"lead{i}@acme.com") for i in range(5)]
        DB._write(conn, "UPDATE leads SET status = 'sent' WHERE id IN (?, ?)", (ids[0], ids[1]))

        assert DB.count_leads(conn) == 5
        assert DB.count_leads(conn, status="sent") == 2
        assert DB.count_leads(conn, status="new") == 3
        assert [r["id"] for r in DB.list_leads(conn, status="sent")] == ids[:2]
        assert [r["id"] for r in DB.list_leads(conn, limit=2)] == ids[:2]
        assert [r["id"] for r in DB.list_leads(conn, limit=2, offset=2)] == ids[2:4]
        assert [r["id"] for r in DB.list_leads(conn, offset=3)] == ids[3:]

        campaign_id = DB.create_campaign(conn, "March", "gap_direct", {}, {})
        for lead_id in ids[:2]:
            DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                    "scheduled_at": _utc(9)})
        assert DB.count_leads(conn, campaign_id=campaign_id) == 2
        assert [r["id"] for r in DB.list_leads(conn, campaign_id=campaign_id)] == ids[:2]
    print("list/count lead filters: OK")


# ── Campaigns ────────────────────────────────────────────────────────────────

def test_campaign_roundtrip():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "March plumbers", "gap_direct",
                                         {"company": "Auto Army"}, {"dry_run": True})
        assert campaign_id
        campaign = DB.get_campaign(conn, campaign_id)
        assert campaign["name"] == "March plumbers"
        assert campaign["template_id"] == "gap_direct"
        assert campaign["status"] == "draft"
        assert "Auto Army" in campaign["profile_json"]
        assert "dry_run" in campaign["settings_json"]

        DB.set_campaign_status(conn, campaign_id, "running")
        assert DB.get_campaign(conn, campaign_id)["status"] == "running"
        assert [c["id"] for c in DB.list_campaigns(conn)] == [campaign_id]
        assert DB.get_campaign(conn, 404) == {}
    print("campaign round-trip: OK")


# ── Messages ─────────────────────────────────────────────────────────────────

def test_due_messages_ordering_and_horizon():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        wanted = []
        for hour in (11, 9, 10, 15):                       # deliberately unsorted
            lead_id = _lead(conn, f"lead{hour}@acme.com")
            message_id = DB.queue_message(conn, {
                "campaign_id": campaign_id, "lead_id": lead_id,
                "subject": f"at {hour}", "scheduled_at": _utc(hour),
            })
            wanted.append((hour, message_id))

        due = DB.due_messages(conn, _utc(12))
        assert [m["subject"] for m in due] == ["at 9", "at 10", "at 11"], due
        assert all(m["status"] == "queued" for m in due)
        assert [m["scheduled_at"] for m in due] == sorted(m["scheduled_at"] for m in due)

        assert len(DB.due_messages(conn, _utc(12), limit=2)) == 2
        assert DB.due_messages(conn, _utc(8)) == []
        assert len(DB.due_messages(conn, _utc(23))) == 4

        # A message that is no longer queued is not due, whatever its schedule.
        DB.mark_message(conn, wanted[1][1], "sent")
        assert [m["subject"] for m in DB.due_messages(conn, _utc(12))] == ["at 10", "at 11"]
    print("due_messages ordering + horizon: OK")


def test_due_messages_excludes_suppressed_leads():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        good = _lead(conn, "keep@acme.com")
        gone = _lead(conn, "Opted.Out@acme.com")
        for lead_id in (gone, good):
            DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                    "subject": str(lead_id), "scheduled_at": _utc(9)})

        DB.suppress(conn, "opted.out@ACME.com", "unsubscribed")
        assert [m["subject"] for m in DB.due_messages(conn, _utc(12))] == [str(good)]

        # Even if something re-queues that row, the join keeps it out of the loop.
        stale = DB._one(conn, "SELECT id FROM messages WHERE lead_id = ?", (gone,))
        DB.mark_message(conn, stale["id"], "queued", error="")
        assert [m["subject"] for m in DB.due_messages(conn, _utc(12))] == [str(good)]
    print("due_messages excludes suppressed leads: OK")


def test_mark_message_writes_header_message_id():
    # The row id and the RFC 5322 header share a name; positional-only params
    # are what keep `message_id=` free for the header.
    with temp_db() as conn:
        lead_id = _lead(conn, "info@acme.com")
        row_id = DB.queue_message(conn, {"lead_id": lead_id, "scheduled_at": _utc(9)})
        DB.mark_message(conn, row_id, "sent", sent_at=_utc(9, 14),
                        message_id="<abc@acme.com>", error="")
        row = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row_id,))
        assert row["status"] == "sent"
        assert row["message_id"] == "<abc@acme.com>"
        assert row["sent_at"] == _utc(9, 14)
        assert row["error"] == ""

        DB.mark_message(conn, row_id, "", error="DRY-RUN")     # status left alone
        row = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row_id,))
        assert row["status"] == "sent" and row["error"] == "DRY-RUN"
    print("mark_message header message_id + partial update: OK")


def test_campaign_stats_counts():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        other = DB.create_campaign(conn, "other", "question", {}, {})
        rows = []
        for i in range(6):
            lead_id = _lead(conn, f"lead{i}@acme.com")
            rows.append(DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                                "scheduled_at": _utc(9)}))
        DB.queue_message(conn, {"campaign_id": other, "lead_id": rows[0], "scheduled_at": _utc(9)})

        DB.mark_message(conn, rows[0], "sent", sent_at=_utc(9))
        DB.mark_message(conn, rows[1], "sent", sent_at=_utc(10))
        DB.mark_message(conn, rows[2], "failed", error="CONN: timeout")
        DB.mark_message(conn, rows[3], "replied")

        stats = DB.campaign_stats(conn, campaign_id)
        assert stats["sent"] == 2
        assert stats["failed"] == 1
        assert stats["replied"] == 1
        assert stats["queued"] == 2
        assert stats["bounced"] == 0 and stats["skipped"] == 0 and stats["sending"] == 0
        assert stats["total"] == 6, stats            # the other campaign is excluded
        assert stats["leads"] == 6
        assert stats["campaign_id"] == campaign_id

        empty = DB.campaign_stats(conn, 999)
        assert empty["total"] == 0
        assert all(empty[status] == 0 for status in DB.MESSAGE_STATUSES)
    print("campaign_stats counts: OK")


# ── Suppression ──────────────────────────────────────────────────────────────

def test_suppression_blocks_queueing_and_cancels_followups():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        lead_id = _lead(conn, "Opted.Out@acme.com")
        first = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                        "step": 0, "scheduled_at": _utc(9)})
        followup = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                           "step": 1, "scheduled_at": _utc(9, day=14)})
        assert first and followup

        DB.suppress(conn, "  OPTED.OUT@acme.com ", "unsubscribe link")

        assert DB.is_suppressed(conn, "opted.out@acme.com") is True
        assert DB.is_suppressed(conn, "Opted.Out@ACME.com") is True
        assert DB.is_suppressed(conn, "someone@else.com") is False

        # Everything already scheduled, follow-ups included, is cancelled.
        statuses = [r["status"] for r in DB._query(
            conn, "SELECT status FROM messages WHERE lead_id = ? ORDER BY id", (lead_id,))]
        assert statuses == ["skipped", "skipped"], statuses
        assert DB.get_lead(conn, lead_id)["status"] == "suppressed"

        # And nothing new can be queued for them afterwards.
        assert DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                       "step": 1, "scheduled_at": _utc(9, day=20)}) == 0
        assert DB._scalar(conn, "SELECT COUNT(*) FROM messages WHERE lead_id = ?", (lead_id,)) == 2

        entries = DB.suppression_list(conn)
        assert [e["email"] for e in entries] == ["opted.out@acme.com"]
        assert entries[0]["reason"] == "unsubscribe link"
        assert entries[0]["added_at"] > 0
        assert any(e["kind"] == "suppressed" for e in DB.recent_events(conn))

        DB.suppress(conn, "", "blank")
        DB.suppress(conn, "junk", "not an address")
        assert len(DB.suppression_list(conn)) == 1
    print("suppression blocks queueing + cancels follow-ups: OK")


# ── Quotas ───────────────────────────────────────────────────────────────────

def test_sent_today_counts_from_local_midnight():
    """The boundary is a calendar day in the account's zone, not a 24h window."""
    west = timezone(timedelta(hours=-5))      # midnight lands at 05:00 UTC
    far_east = timezone(timedelta(hours=14))  # already the 11th; midnight at 10:00 UTC
    noon = _utc(12)

    with temp_db() as conn:
        for hour in (4, 6, 11):
            DB.record_send(conn, "sender@acme.com", _utc(hour))
        DB.record_send(conn, "other@acme.com", _utc(11))

        # 04:00 UTC is 8 hours ago — inside a rolling 24h window, but yesterday.
        assert DB.sent_today(conn, "sender@acme.com", west, now_ts=noon) == 2
        assert DB.sent_today(conn, "sender@acme.com", far_east, now_ts=noon) == 1
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends WHERE ts >= ?",
                          (noon - 86400,)) == 4, "rolling window would over-count"

        # Addresses are normalised, and one account's quota is its own.
        assert DB.sent_today(conn, "  SENDER@Acme.com ", west, now_ts=noon) == 2
        assert DB.sent_today(conn, "other@acme.com", west, now_ts=noon) == 1
        assert DB.sent_today(conn, "nobody@acme.com", west, now_ts=noon) == 0

        # A send at exactly local midnight belongs to the new day.
        DB.record_send(conn, "edge@acme.com", _utc(5))
        DB.record_send(conn, "edge@acme.com", _utc(4, 59))
        assert DB.sent_today(conn, "edge@acme.com", west, now_ts=noon) == 1

        # Just after midnight the counter is empty again, though the previous
        # day's sends are minutes old.
        just_after = _utc(5, 1, day=11)
        assert DB.sent_today(conn, "sender@acme.com", west, now_ts=just_after) == 0
    print("sent_today local-midnight boundary: OK")


def test_sent_today_accepts_named_and_local_zones():
    with temp_db() as conn:
        DB.record_send(conn, "sender@acme.com", _utc(12))
        # "local" and an IANA name must both answer with a number, never raise —
        # Windows has no tz database unless `tzdata` happens to be installed.
        for zone in ("local", "", None, "America/Toronto", "Not/AZone"):
            count = DB.sent_today(conn, "sender@acme.com", zone, now_ts=_utc(12, 30))
            assert isinstance(count, int) and count in (0, 1), (zone, count)
    print("sent_today zone handling: OK")


def test_sent_last_hour_is_a_rolling_window():
    now = _utc(12)
    with temp_db() as conn:
        DB.record_send(conn, "sender@acme.com", now - 60)
        DB.record_send(conn, "sender@acme.com", now - 3599)
        DB.record_send(conn, "sender@acme.com", now - 3601)
        DB.record_send(conn, "other@acme.com", now - 10)
        assert DB.sent_last_hour(conn, "sender@acme.com", now_ts=now) == 2
        assert DB.sent_last_hour(conn, "other@acme.com", now_ts=now) == 1
        assert DB.sent_last_hour(conn, "nobody@acme.com", now_ts=now) == 0
    print("sent_last_hour rolling window: OK")


def test_record_send_defaults_to_now():
    with temp_db() as conn:
        DB.record_send(conn, "sender@acme.com", 0)
        row = DB._one(conn, "SELECT * FROM sends")
        assert row["ts"] > _utc(12), row      # a real clock reading, not zero
        assert row["account_email"] == "sender@acme.com"
    print("record_send default timestamp: OK")


# ── Events ───────────────────────────────────────────────────────────────────

def test_events_newest_first():
    with temp_db() as conn:
        for i in range(5):
            DB.log_event(conn, "send", f"event {i}", lead_id=i)
        events = DB.recent_events(conn, limit=3)
        assert [e["detail"] for e in events] == ["event 4", "event 3", "event 2"]
        assert events[0]["kind"] == "send" and events[0]["lead_id"] == 4
        assert events[0]["ts"] > 0
        assert len(DB.recent_events(conn)) == 5
    print("events newest first: OK")


# ── Concurrency ──────────────────────────────────────────────────────────────

def test_concurrent_writers_do_not_collide():
    """The worker thread and the GUI thread share one connection by design."""
    with temp_db() as conn:
        errors = []

        def hammer(worker: int):
            try:
                for i in range(25):
                    DB.upsert_lead(conn, {"email": f"lead{i}@acme.com", "phone": str(worker)})
                    DB.record_send(conn, f"sender{worker}@acme.com", _utc(12))
                    DB.log_event(conn, "send", f"{worker}:{i}")
            except Exception as exc:               # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(w,)) for w in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, errors
        assert DB.count_leads(conn) == 25, DB.count_leads(conn)
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends") == 100
        assert len(DB.recent_events(conn, limit=500)) == 100
    print("concurrent writers: OK")


# ── Channels ─────────────────────────────────────────────────────────────────

def test_channel_defaults_to_email_and_is_stored():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        lead_id = _lead(conn, "info@acme.com", phone="+1 416-555-0142")

        plain = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                        "scheduled_at": _utc(9)})
        wa = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                     "channel": "whatsapp", "scheduled_at": _utc(9)})
        junk = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                       "channel": "carrier pigeon",
                                       "scheduled_at": _utc(9)})

        stored = {r["id"]: r["channel"] for r in DB._query(conn, "SELECT id, channel FROM messages")}
        assert stored[plain] == "email", "a caller that says nothing means email"
        assert stored[wa] == "whatsapp"
        assert stored[junk] == "email", "an unrecognised channel reads as email"

        DB.mark_message(conn, plain, "", channel="whatsapp")
        assert DB._one(conn, "SELECT channel FROM messages WHERE id = ?",
                       (plain,))["channel"] == "whatsapp"
    print("message channel stored, defaulted and validated: OK")


def test_due_messages_can_be_read_per_channel_and_defaults_to_both():
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        for channel in ("email", "whatsapp"):
            lead_id = _lead(conn, "%s@acme.com" % channel)
            DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                    "channel": channel, "subject": channel,
                                    "scheduled_at": _utc(9)})

        both = DB.due_messages(conn, _utc(12))
        assert sorted(m["subject"] for m in both) == ["email", "whatsapp"], (
            "no channel asked for means every channel — a campaign is "
            "single-channel and campaign_id already narrows it")
        assert [m["subject"] for m in DB.due_messages(conn, _utc(12), channel="email")] == ["email"]
        assert [m["subject"] for m in DB.due_messages(conn, _utc(12),
                                                     channel="whatsapp")] == ["whatsapp"]
    print("due_messages per channel: OK")


def test_caps_are_counted_per_channel():
    """WhatsApp volume must never spend the email allowance, or the reverse.

    The caps are not the same number — thirty a day against forty, eight an hour
    against twelve — so a shared ledger does not merely blur the two, it hands
    the smaller allowance to whichever channel ran first.
    """
    noon = _utc(12)
    zone = timezone(timedelta(hours=-5))
    with temp_db() as conn:
        for _ in range(9):
            DB.record_send(conn, "sender@acme.com", noon - 600)
        for _ in range(4):
            DB.record_send(conn, "sender@acme.com", noon - 600, channel="whatsapp")

        assert DB.sent_today(conn, "sender@acme.com", zone, now_ts=noon) == 9
        assert DB.sent_today(conn, "sender@acme.com", zone, now_ts=noon,
                             channel="whatsapp") == 4
        assert DB.sent_last_hour(conn, "sender@acme.com", now_ts=noon) == 9
        assert DB.sent_last_hour(conn, "sender@acme.com", now_ts=noon,
                                 channel="whatsapp") == 4
        assert len(DB.recent_sends(conn, "sender@acme.com", since_ts=noon - 3600,
                                   channel="whatsapp")) == 4

        # The default is email, so every caller written before the second
        # channel existed still counts exactly what it always counted.
        assert DB.sent_today(conn, "sender@acme.com", zone, now_ts=noon,
                             channel="email") == 9
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends") == 13, (
            "both ledgers are in one table; only the counting is separate")
    print("per-channel caps and ledger: OK")


# ── Suppression across channels ──────────────────────────────────────────────

def test_a_whatsapp_opt_out_stops_the_email_sequence():
    """STOP on WhatsApp must cancel the mail, not only the messages.

    This is the finding the user would be most embarrassed by. One lead pool
    means one opt-out; a person who has said stop on a phone and then receives
    the four-step email sequence has been told the "unsubscribe" did nothing.
    """
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        lead_id = _lead(conn, "info@acme.com", phone="(416) 555-0142")
        first = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                        "step": 0, "scheduled_at": _utc(9)})
        chaser = DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                         "step": 1, "scheduled_at": _utc(9, day=14)})
        assert first and chaser

        # The reply arrives from the number in the form WhatsApp reports it,
        # which is not the form Google Maps supplied.
        DB.suppress(conn, phone="14165550142", reason="replied STOP on WhatsApp")

        assert DB.is_suppressed(conn, phone="14165550142") is True
        assert DB.is_suppressed(conn, "info@acme.com") is True, (
            "the email address is the same person")
        assert DB.is_suppressed(conn, phone="(416) 555-0142") is True
        assert DB.due_messages(conn, _utc(12, day=30)) == []
        statuses = [r["status"] for r in DB._query(
            conn, "SELECT status FROM messages WHERE lead_id = ? ORDER BY id", (lead_id,))]
        assert statuses == ["skipped", "skipped"], statuses
        assert DB.get_lead(conn, lead_id)["status"] == "suppressed"
        assert DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                       "step": 1, "scheduled_at": _utc(9, day=20)}) == 0

        listed = DB.suppressed_phones(conn)
        assert [r["tail"] for r in listed] == [WA.phone_key("14165550142")]
        assert listed[0]["phone"] == "14165550142", (
            "the list shows a number, not the eight digits it matches on")
        assert [r["email"] for r in DB.suppression_list(conn)] == ["info@acme.com"]
    print("a WhatsApp opt-out stops the email sequence: OK")


def test_unsuppress_clears_both_handles():
    """Releasing only the address would leave the number blocking, invisibly.

    The lead would go on refusing to queue with nothing on screen to explain it,
    which is the failure the UI's own inverse had before this existed.
    """
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        lead_id = _lead(conn, "info@acme.com", phone="(416) 555-0142")
        DB.suppress(conn, phone="14165550142", reason="replied STOP")
        assert DB.is_suppressed(conn, "info@acme.com") is True

        assert DB.unsuppress(conn, "info@acme.com") is True
        assert DB.is_suppressed(conn, "info@acme.com") is False
        assert DB.is_suppressed(conn, phone="(416) 555-0142") is False
        assert DB.suppression_list(conn) == []
        assert DB.suppressed_phones(conn) == []
        assert any(e["kind"] == "unsuppressed" for e in DB.recent_events(conn))

        # Contactable again, and nothing was resurrected: the cancelled rows
        # carry send times that have long passed.
        DB.upsert_lead(conn, {"email": "info@acme.com", "status": "audited"})
        assert DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                       "scheduled_at": _utc(9)}) > 0

        assert DB.unsuppress(conn, "nobody@acme.com") is False
        assert DB.unsuppress(conn) is False
    print("unsuppress clears both handles: OK")


def test_an_email_unsubscribe_stops_the_whatsapp_messages():
    """And the same in the other direction, which is the half easily forgotten."""
    with temp_db() as conn:
        campaign_id = DB.create_campaign(conn, "wa", "gap_direct", {}, {})
        lead_id = _lead(conn, "info@acme.com", phone="+1 416-555-0142")
        DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                "channel": "whatsapp", "scheduled_at": _utc(9)})

        DB.suppress(conn, "info@acme.com", "unsubscribe link")

        assert DB.is_suppressed(conn, phone="(416) 555-0142") is True
        assert DB.is_suppressed(conn, phone="14165550142") is True
        assert DB.due_messages(conn, _utc(12), channel="whatsapp") == []
        assert DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                       "channel": "whatsapp",
                                       "scheduled_at": _utc(9)}) == 0
        assert [r["tail"] for r in DB.suppressed_phones(conn)] == [WA.phone_key("14165550142")]
    print("an email unsubscribe stops the WhatsApp messages: OK")


def test_a_lead_scraped_after_the_opt_out_is_still_suppressed():
    """The eager write cannot have seen a row that did not exist yet.

    Which is why every read joins as well as looking up. The lead pool keeps
    growing after somebody has said stop, and a re-scrape that produced a fresh
    row the opt-out had never touched is exactly how a suppressed number gets
    messaged again.
    """
    with temp_db() as conn:
        DB.suppress(conn, phone="+1 416-555-0142", reason="replied STOP")

        # A later scrape finds the same business under a different address.
        campaign_id = DB.create_campaign(conn, "c", "gap_direct", {}, {})
        lead_id = _lead(conn, "bookings@acme.com", phone="(416) 555-0142")
        assert DB.is_suppressed(conn, "bookings@acme.com") is True
        assert DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                       "scheduled_at": _utc(9)}) == 0

        # And a message forced past the queue guard is still refused by the read.
        DB._write(conn, "INSERT INTO messages (campaign_id, lead_id, status, "
                        "scheduled_at) VALUES (?, ?, 'queued', ?)",
                  (campaign_id, lead_id, _utc(9)))
        assert DB.due_messages(conn, _utc(12)) == []

        # An unrelated number is untouched.
        other = _lead(conn, "hello@other.com", phone="(416) 555-0199")
        assert DB.is_suppressed(conn, "hello@other.com") is False
        assert DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": other,
                                       "scheduled_at": _utc(9)}) > 0
    print("a lead scraped after the opt-out is still suppressed: OK")


def test_suppress_ignores_handles_it_cannot_use():
    with temp_db() as conn:
        DB.suppress(conn, "", "blank")
        DB.suppress(conn, "junk", "not an address")
        DB.suppress(conn, phone="555-0142", reason="too short to identify anyone")
        DB.suppress(conn, phone="", reason="nothing at all")
        assert DB.suppression_list(conn) == []
        assert DB.suppressed_phones(conn) == []
        assert DB.is_suppressed(conn) is False
        assert DB.is_suppressed(conn, "junk") is False
    print("suppress ignores unusable handles: OK")


def test_phone_key_is_derived_and_tracks_the_stored_number():
    with temp_db() as conn:
        lead_id = _lead(conn, "info@acme.com", phone="(416) 555-0142",
                        phone_key="not-mine")
        stored = DB.get_lead(conn, lead_id)
        assert stored["phone_key"] == WA.phone_key("(416) 555-0142"), (
            "the key is derived here, never taken from the caller")

        # A re-scrape that corrects the number moves the key with it, or the
        # do-not-contact list would keep matching the number it replaced.
        DB.upsert_lead(conn, {"email": "info@acme.com", "phone": "+1 416-555-0199"})
        assert DB.get_lead(conn, lead_id)["phone_key"] == WA.phone_key("4165550199")

        # A sighting that knows no number leaves the key alone, the same way it
        # leaves the number alone.
        DB.upsert_lead(conn, {"email": "info@acme.com", "name": "Acme Ltd"})
        assert DB.get_lead(conn, lead_id)["phone_key"] == WA.phone_key("4165550199")
    print("phone_key derived and kept in step with the number: OK")


def test_leads_can_be_filtered_to_those_carrying_a_usable_number():
    """What the plan summary counts before the user commits to a campaign.

    A lead with no number cannot be in a WhatsApp campaign, and the summary has
    to say how many of those there are up front rather than discovering it
    while sending.
    """
    with temp_db() as conn:
        with_number = _lead(conn, "a@acme.com", phone="(416) 555-0142")
        also = _lead(conn, "b@acme.com", phone="+1 647-555-0188")
        _lead(conn, "c@acme.com")                      # no number at all
        _lead(conn, "d@acme.com", phone="555-0142")    # too short to identify

        assert DB.count_leads(conn) == 4
        assert DB.count_leads(conn, has_phone=True) == 2
        assert [r["id"] for r in DB.list_leads(conn, has_phone=True)] == [with_number, also]
        assert [r["id"] for r in DB.list_leads(conn)][:4] != []
        assert DB.count_leads(conn, has_phone=True, status="sent") == 0
    print("leads filtered to those carrying a usable number: OK")


# ── Migration ────────────────────────────────────────────────────────────────

# The schema exactly as it stood before the WhatsApp channel, quoted rather than
# generated. A migration test that builds its fixture from today's code proves
# nothing: the file it has to open is the one shipped builds have been writing
# for months, and this is that file.
_PRE_CHANNEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, domain TEXT,
  website TEXT, phone TEXT, city TEXT, category TEXT, rating TEXT,
  maps_link TEXT, source TEXT, audit_json TEXT, ai_json TEXT,
  opportunity_score INTEGER DEFAULT 0, status TEXT DEFAULT 'new',
  created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY, name TEXT, template_id TEXT, profile_json TEXT,
  settings_json TEXT, status TEXT DEFAULT 'draft', created_at REAL);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, campaign_id INTEGER, lead_id INTEGER, step INTEGER DEFAULT 0,
  subject TEXT, body_text TEXT, body_html TEXT, account_email TEXT,
  status TEXT DEFAULT 'queued', scheduled_at REAL, sent_at REAL,
  error TEXT, message_id TEXT, created_at REAL);

CREATE TABLE IF NOT EXISTS suppression (
  email TEXT PRIMARY KEY, reason TEXT, added_at REAL);

CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY, account_email TEXT, ts REAL);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, detail TEXT, lead_id INTEGER);

CREATE TABLE IF NOT EXISTS sent_mail (
  message_id INTEGER PRIMARY KEY, raw TEXT, wrote_at REAL);

CREATE INDEX IF NOT EXISTS idx_messages_due ON messages(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sends_account_ts ON sends(account_email, ts);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_messages_campaign_status
  ON messages(campaign_id, status, lead_id, scheduled_at, sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_lead
  ON messages(lead_id, step, status, campaign_id);
CREATE INDEX IF NOT EXISTS idx_messages_step
  ON messages(step, status, lead_id);
CREATE INDEX IF NOT EXISTS idx_messages_header ON messages(message_id);
"""


def _build_pre_channel_db(path: str) -> dict:
    """A real campaign in the old shape: leads, a queue, sends, an unsubscribe."""
    old = sqlite3.connect(path)
    old.executescript(_PRE_CHANNEL_SCHEMA)
    ids = {}
    for email, name, phone in (
        ("info@acme.com", "Acme Plumbing", "(416) 555-0142"),
        ("hello@brightsigns.ca", "Bright Signs", "+1 647-555-0188"),
        ("owner@nophone.com", "No Phone Co", ""),
    ):
        cursor = old.execute(
            "INSERT INTO leads (email, name, phone, domain, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'sent', ?, ?)",
            (email, name, phone, email.rpartition("@")[2], _utc(8), _utc(8)))
        ids[email] = cursor.lastrowid

    cursor = old.execute(
        "INSERT INTO campaigns (name, template_id, profile_json, settings_json, "
        "status, created_at) VALUES ('February', 'gap_direct', '{}', '{}', "
        "'running', ?)", (_utc(8),))
    ids["campaign"] = cursor.lastrowid

    cursor = old.execute(
        "INSERT INTO messages (campaign_id, lead_id, step, subject, body_text, "
        "account_email, status, scheduled_at, sent_at, message_id, created_at) "
        "VALUES (?, ?, 0, 'Your booking form', 'body', 'sender@acme.com', "
        "'sent', ?, ?, '<abc@acme.com>', ?)",
        (ids["campaign"], ids["info@acme.com"], _utc(9), _utc(9), _utc(8)))
    ids["sent_message"] = cursor.lastrowid
    cursor = old.execute(
        "INSERT INTO messages (campaign_id, lead_id, step, subject, status, "
        "scheduled_at, created_at) VALUES (?, ?, 1, 'Following up', 'queued', ?, ?)",
        (ids["campaign"], ids["info@acme.com"], _utc(9, day=14), _utc(8)))
    ids["queued_message"] = cursor.lastrowid

    for hour in (9, 10, 11):
        old.execute("INSERT INTO sends (account_email, ts) VALUES (?, ?)",
                    ("sender@acme.com", _utc(hour)))
    old.execute("INSERT INTO suppression (email, reason, added_at) VALUES "
                "('gone@away.com', 'unsubscribed', ?)", (_utc(9),))
    old.execute("INSERT INTO sent_mail (message_id, raw, wrote_at) VALUES "
                "(?, 'From: sender@acme.com', ?)", (ids["sent_message"], _utc(9)))
    old.commit()
    old.close()
    return ids


def test_a_pre_channel_database_opens_and_its_rows_read_as_email():
    """The promise of migrating in place rather than recreating.

    Everything a shipped build wrote has to still be there, still be queryable,
    and answer "which channel" with the only answer that was ever true for it.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        path = os.path.join(tmp, "outreach.db")
        ids = _build_pre_channel_db(path)
        try:
            conn = DB.connect(path)

            # Nothing was lost.
            assert DB.count_leads(conn) == 3
            assert DB.get_lead(conn, ids["info@acme.com"])["name"] == "Acme Plumbing"
            assert DB.get_campaign(conn, ids["campaign"])["name"] == "February"
            assert DB.transcript(conn, ids["sent_message"]) == "From: sender@acme.com"
            assert DB.first_touch_message_id(conn, ids["campaign"],
                                             ids["info@acme.com"]) == "<abc@acme.com>"
            assert DB.is_suppressed(conn, "gone@away.com") is True

            # Every legacy row reads as email, and none of them was rewritten to
            # say so — the column default is what answers.
            channels = {r["channel"] for r in DB._query(conn, "SELECT channel FROM messages")}
            assert channels == {"email"}, channels
            assert {r["channel"] for r in DB._query(conn, "SELECT channel FROM sends")} == {"email"}

            # The old queue still runs, and its history still counts — against
            # email's allowance, not WhatsApp's.
            due = DB.due_messages(conn, _utc(12, day=20))
            assert [m["subject"] for m in due] == ["Following up"], due
            assert [m["subject"] for m in DB.due_messages(conn, _utc(12, day=20),
                                                          channel="email")] == ["Following up"]
            zone = timezone(timedelta(hours=-5))
            assert DB.sent_today(conn, "sender@acme.com", zone, now_ts=_utc(12)) == 3
            assert DB.sent_today(conn, "sender@acme.com", zone, now_ts=_utc(12),
                                 channel="whatsapp") == 0, (
                "a year of email must not arrive as a spent WhatsApp allowance")

            # The phone numbers already in the file gained their match key.
            acme = DB.get_lead(conn, ids["info@acme.com"])
            assert acme["phone_key"] == WA.phone_key("(416) 555-0142")
            assert DB.get_lead(conn, ids["owner@nophone.com"])["phone_key"] == ""

            # Which is what makes the shared do-not-contact list work on rows
            # that predate it: a STOP from that number cancels the queued mail.
            DB.suppress(conn, phone="14165550142", reason="replied STOP")
            assert DB.is_suppressed(conn, "info@acme.com") is True
            assert DB.due_messages(conn, _utc(12, day=20)) == []
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original
    print("a pre-channel database opens and reads as email: OK")


def test_an_interrupted_backfill_is_finished_on_the_next_open():
    """The column existing is not proof the keys were written.

    Adding the column commits on its own, and the backfill that follows is a
    second transaction. Kill the app in between — a laptop lid, a crash, a user
    who waited long enough — and the column is there with every key still empty.
    A migration gated on "does the column exist" would answer yes forever after,
    and every lead that predated the upgrade would be invisible to the shared
    do-not-contact list. Nobody would notice until somebody who said stop on
    WhatsApp got the email sequence, so it is gated on the schema version, which
    is only stamped once the keys are in.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        path = os.path.join(tmp, "outreach.db")
        ids = _build_pre_channel_db(path)
        try:
            conn = DB.connect(path)
            assert DB._schema_version(conn) == DB._SCHEMA_VERSION
            assert DB.get_lead(conn, ids["info@acme.com"])["phone_key"] != ""

            # Rewind to exactly the half-applied state: the column is there,
            # the keys are not, and the version was never stamped.
            DB._write(conn, "UPDATE leads SET phone_key = ''")
            DB._write(conn, "PRAGMA user_version = 0")
            assert DB.is_suppressed(conn, phone="14165550142") is False
            DB.close_all()

            conn = DB.connect(path)
            assert DB._schema_version(conn) == DB._SCHEMA_VERSION, "stamped now"
            assert DB.get_lead(conn, ids["info@acme.com"])["phone_key"] == \
                WA.phone_key("(416) 555-0142"), "the interrupted pass was redone"

            DB.suppress(conn, phone="14165550142", reason="replied STOP")
            assert DB.is_suppressed(conn, "info@acme.com") is True
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original
    print("an interrupted backfill is finished on the next open: OK")


def test_a_migrated_database_matches_a_fresh_one_and_migrating_twice_is_safe():
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        migrated_path = os.path.join(tmp, "migrated.db")
        _build_pre_channel_db(migrated_path)
        try:
            migrated = DB.connect(migrated_path)
            fresh = DB.connect(os.path.join(tmp, "fresh.db"))

            for table in ("leads", "messages", "sends", "suppression",
                          "suppression_phone", "campaigns", "events", "sent_mail"):
                assert DB._columns(migrated, table) == DB._columns(fresh, table), table

            # Idempotent: init_db runs on every open, and a second pass must not
            # re-add a column, wipe a key or fail the open.
            before = DB.get_lead(migrated, 1)
            DB.init_db(migrated)
            DB.init_db(migrated)
            assert DB.get_lead(migrated, 1) == before
            assert DB.count_leads(migrated) == 3
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original
    print("migrated schema matches a fresh one; migrating twice is safe: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL OUTREACH DB TESTS PASSED")
