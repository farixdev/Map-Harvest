"""Offline tests for core.outreach_db.

Every test runs against a throwaway database in a temp directory, and
`core.settings.SETTINGS_DIR` is redirected there too so that a stray default
`connect()` can never touch a developer's real ~/.mapharvest/outreach.db.

Time is never slept on and never read from the clock where it matters: the
quota tests pass explicit epoch timestamps, so the local-midnight boundary is
asserted at a fixed instant rather than whenever the suite happens to run.
"""
import contextlib
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL OUTREACH DB TESTS PASSED")
