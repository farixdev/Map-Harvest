"""Runtime correctness: the shared connection, the hot queries, and shutdown.

Four defects are pinned here, each with the measurement that found it.

The first is a data race. `core.outreach_db` shares one `sqlite3.Connection`
across the GUI thread and every worker, and a connection carries an LRU cache of
prepared statements keyed on the SQL text. Two threads running the same query
therefore stepped the *same* `sqlite3_stmt` past each other. Six readers against
two writers for five seconds produced 252 `IndexError: tuple index out of range`
and three `SystemError`s out of the row factory — neither is a `sqlite3.Error`,
so the module that documents "Nothing here raises" was raising out of a worker
thread once in every 75 reads, and the rows it did hand back were built from a
cursor description belonging to somebody else's query.

The second is the index set. A `messages` row holds a whole rendered email, so
an index that only narrows the search still costs a page read per row. Against
20,000 queued messages the stats counters took 63ms each on the GUI thread, the
reply matcher scanned the whole table, and the follow-up thread lookup ran once
per candidate inside the dispatch loop.

The third is shutdown. Nothing closed the database. `close_all` existed for the
test suite alone, so the app exited with the handle open and SQLite never got to
fold the write-ahead log back in. Opening and closing the real window over a
seeded store left 3.5MB of `outreach.db-wal` against a 4KB `outreach.db` — every
row of it replayed from the log on the next start — and the user profile this
was found in carries 263KB of log against a 57KB database for the same reason.

The fourth is the shell's second row, which was torn down and rebuilt on every
call to `set_context`, and a running campaign calls it once per message.
"""
import contextlib
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QThread  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from core import campaign as CAMPAIGN  # noqa: E402
from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402
from ui import app as APP  # noqa: E402

# Held at module scope on purpose. `QApplication.instance() or QApplication([])`
# builds one and drops the only reference to it in the same expression, and the
# next Qt call then runs against a deleted application and takes the process
# down with no traceback at all.
_APP = QApplication.instance() or QApplication([])


class _Ticker(QThread):
    """A worker that stays running until it is asked not to."""

    def run(self) -> None:
        while not self.isInterruptionRequested():
            time.sleep(0.01)


def _running(worker: QThread) -> bool:
    deadline = time.monotonic() + 5
    while not worker.isRunning() and time.monotonic() < deadline:
        time.sleep(0.01)
    return worker.isRunning()


def _retire(worker: QThread) -> None:
    worker.requestInterruption()
    worker.wait(5000)


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


def _seed(conn, leads: int = 40, messages: int = 200) -> int:
    campaign_id = DB.create_campaign(conn, "Bench", "gap_direct", {}, {})
    ids = [DB.upsert_lead(conn, {"email": "owner%d@biz%d.example.com" % (i, i),
                                 "name": "Business %d" % i})
           for i in range(leads)]
    body = "body " * 200
    for i in range(messages):
        DB.queue_message(conn, {
            "campaign_id": campaign_id, "lead_id": ids[i % leads], "step": i % 3,
            "subject": "Subject %d" % i, "body_text": body, "body_html": body,
            "account_email": "sender@gmail.com", "scheduled_at": time.time() + 9000,
        })
    return campaign_id


def _plan(conn, sql: str, params: tuple = ()) -> str:
    return " | ".join(row["detail"] for row in
                      DB._query(conn, "EXPLAIN QUERY PLAN " + sql, params))


# ── The shared connection ────────────────────────────────────────────────────

def test_reads_and_writes_at_once_never_raise_out_of_the_module():
    """The reproduction, bounded: readers and writers on the one connection.

    As shipped this raised `IndexError` out of `_query` within a second or two,
    because the two threads were handed the same cached prepared statement.
    Anything escaping here is that race, not a database error: a database error
    is a `sqlite3.Error` and `_query` already answers those with an empty list.
    """
    with temp_db() as conn:
        campaign_id = _seed(conn)
        escaped: list = []
        reads = [0]
        stop = threading.Event()

        def reader(kind):
            while not stop.is_set():
                try:
                    if kind == 0:
                        DB.campaign_stats(conn, campaign_id)
                    elif kind == 1:
                        DB.due_messages(conn, time.time() + 99999, limit=50)
                    else:
                        DB.list_leads(conn)
                    reads[0] += 1
                except BaseException as exc:      # noqa: BLE001 - that is the point
                    escaped.append("%s: %s" % (type(exc).__name__, exc))

        def writer():
            while not stop.is_set():
                try:
                    DB.log_event(conn, "probe", "x" * 80)
                    DB.record_send(conn, "sender@gmail.com", time.time())
                except BaseException as exc:      # noqa: BLE001
                    escaped.append("%s: %s" % (type(exc).__name__, exc))

        threads = [threading.Thread(target=reader, args=(k,), daemon=True)
                   for k in (0, 1, 2, 0, 1, 2)]
        threads += [threading.Thread(target=writer, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        time.sleep(2.0)
        stop.set()
        for thread in threads:
            thread.join(10)

        assert reads[0] > 100, "the probe did not read enough to be evidence"
        assert not escaped, "%d exceptions escaped core.outreach_db: %s" % (
            len(escaped), escaped[:3])
    print("concurrent reads and writes stay inside the module: OK")


def test_a_read_waits_for_the_lock_the_writes_take():
    """Reads are serialised too, which is what the docstring now promises.

    Held only across the `execute`, this would still race: a statement stays
    live until its rows are pulled, so the fetch has to be inside the lock as
    well.
    """
    with temp_db() as conn:
        DB.upsert_lead(conn, {"email": "a@acme.com", "name": "Acme"})
        started = threading.Event()
        finished: list = []

        def read():
            started.set()
            DB.list_leads(conn)
            finished.append(time.monotonic())

        with DB._LOCK:
            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            started.wait(2)
            time.sleep(0.3)
            assert not finished, "the read went through while the lock was held"
            released = time.monotonic()
        thread.join(5)

        assert finished, "the read never completed"
        assert finished[0] >= released, "the read finished before the lock was free"
    print("reads wait on the write lock: OK")


def test_nothing_outside_this_module_reaches_past_the_lock():
    """`core.campaign` reads through `outreach_db.rows`, not off the connection.

    Its three raw `conn.execute` calls ran on the plan thread and the send
    thread, beside a GUI thread reading the same connection — which is exactly
    the pair the race above needs.
    """
    source = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "core", "campaign.py"), encoding="utf-8").read()
    assert "conn.execute(" not in source, (
        "core.campaign reaches the connection directly; use outreach_db.rows")


def test_rows_is_the_public_door_and_answers_like_the_private_one():
    with temp_db() as conn:
        lead_id = DB.upsert_lead(conn, {"email": "a@acme.com", "name": "Acme"})
        rows = DB.rows(conn, "SELECT id, email FROM leads WHERE id = ?", (lead_id,))
        assert rows == [{"id": lead_id, "email": "a@acme.com"}], rows
        assert DB.rows(conn, "SELECT nope FROM nowhere") == []
    print("rows() reads and degrades like _query: OK")


def test_the_reply_matcher_and_the_canceller_still_answer_correctly():
    """Routing them through `rows` must not change what they return.

    Deliberately reads through `_query` rather than through the new door, so
    this is a regression guard that holds either side of the change rather than
    a second copy of the test above.
    """
    with temp_db() as conn:
        campaign_id = _seed(conn, leads=4, messages=8)
        rows = DB._query(conn, "SELECT id, lead_id FROM messages ORDER BY id")
        DB.mark_message(conn, rows[0]["id"], "sent", message_id="<one@example.com>")

        worker = CAMPAIGN.OutreachWorker(campaign_id, {}, dry_run=True)
        found = worker._message_by_header(conn, "<one@example.com>")
        assert found.get("id") == rows[0]["id"], found
        assert found.get("status") == "sent", found
        assert worker._message_by_header(conn, "") == {}
        assert worker._message_by_header(conn, "<nobody@example.com>") == {}

        lead_id = rows[0]["lead_id"]
        owed = [r for r in rows if r["lead_id"] == lead_id and r["id"] != rows[0]["id"]]
        assert worker._cancel_queued(conn, lead_id, "probe") == len(owed)
        assert worker._cancel_queued(conn, 0, "probe") == 0

        contacted = CAMPAIGN._contacted_lead_ids(conn)
        assert contacted, "a first touch was queued and should count as contacted"
        assert all(isinstance(i, int) for i in contacted), contacted
    print("the routed reads answer as they did: OK")


# ── The indexes the real queries need ────────────────────────────────────────

def test_no_hot_query_on_messages_scans_the_table():
    """Every query the GUI and the send loop run, checked against its plan.

    A `SCAN messages` here is a page read for every message in the store on a
    thread the user is watching. The reply matcher was one.
    """
    with temp_db() as conn:
        campaign_id = _seed(conn)
        hot = {
            "campaign_stats counters":
                ("SELECT status, COUNT(*) AS n FROM messages WHERE campaign_id = ? "
                 "GROUP BY status", (campaign_id,)),
            "campaign_stats leads":
                ("SELECT COUNT(DISTINCT lead_id) FROM messages WHERE campaign_id = ?",
                 (campaign_id,)),
            "first_touch_sent":
                ("SELECT message_id, account_email FROM messages WHERE campaign_id = ? "
                 "AND lead_id = ? AND step = 0 AND status = 'sent' AND message_id != '' "
                 "ORDER BY id LIMIT 1", (campaign_id, 1)),
            "contacted lead ids":
                ("SELECT DISTINCT lead_id FROM messages WHERE step = 0 AND status IN "
                 "('queued', 'sending', 'rehearsed', 'sent', 'replied', 'bounced')", ()),
            "reply matcher":
                ("SELECT id, lead_id, status FROM messages WHERE message_id = ? "
                 "ORDER BY id DESC LIMIT 1", ("<x@y.z>",)),
            "cancel what is owed":
                ("SELECT id FROM messages WHERE lead_id = ? AND status IN "
                 "('queued', 'sending', 'rehearsed')", (1,)),
            "claimed messages":
                ("SELECT * FROM messages WHERE status = 'sending' AND campaign_id = ? "
                 "ORDER BY id", (campaign_id,)),
            "per-day rollup":
                ("SELECT status, scheduled_at, sent_at FROM messages "
                 "WHERE campaign_id = ?", (campaign_id,)),
        }
        for label, (sql, params) in hot.items():
            plan = _plan(conn, sql, params)
            assert "SCAN messages" not in plan, "%s: %s" % (label, plan)
    print("no hot query scans messages: OK")


def test_the_counters_behind_the_stats_tiles_are_answered_out_of_an_index():
    """Covering, not merely narrowing — the table is what costs.

    `campaign_stats` runs three times per screen refresh and twice per second
    while the Sending tab is open. Reading `messages` for it cost 253ms of every
    one-second tick.
    """
    with temp_db() as conn:
        campaign_id = _seed(conn)
        for sql in ("SELECT status, COUNT(*) AS n FROM messages "
                    "WHERE campaign_id = ? GROUP BY status",
                    "SELECT COUNT(DISTINCT lead_id) FROM messages WHERE campaign_id = ?"):
            plan = _plan(conn, sql, (campaign_id,))
            assert "COVERING INDEX idx_messages_campaign_status" in plan, plan
    print("stats counters read only the index: OK")


def test_campaign_stats_still_counts_what_it_counted():
    """The indexes changed the plan, and must not have changed the answer."""
    with temp_db() as conn:
        campaign_id = _seed(conn, leads=5, messages=12)
        rows = DB._query(conn, "SELECT id FROM messages ORDER BY id LIMIT 3")
        for row in rows:
            DB.mark_message(conn, row["id"], "sent", sent_at=time.time())
        stats = DB.campaign_stats(conn, campaign_id)
        assert stats["total"] == 12, stats
        assert stats["sent"] == 3, stats
        assert stats["queued"] == 9, stats
        assert stats["leads"] == 5, stats
        for status in DB.MESSAGE_STATUSES:
            assert status in stats, status
    print("campaign_stats unchanged by the new indexes: OK")


# ── Shutdown ─────────────────────────────────────────────────────────────────

def test_a_worker_held_only_by_its_qt_parent_is_found_at_shutdown():
    """`ui.app._screen_threads` used to look at attributes and nothing else.

    `ui.screen_settings._FetchModelsProbe` is built with `parent=self` and
    stored in no attribute, so shutdown never saw it and never stopped it, and
    closing the window destroyed a QThread still inside its HTTP call — which Qt
    answers by aborting the process.
    """
    screen = QWidget()
    parented = _Ticker(parent=screen)
    parented.start()
    try:
        assert _running(parented), "the probe never started"
        found = APP._screen_threads(screen)
        assert parented in found, (
            "a running QThread held only by its Qt parent is invisible to "
            "shutdown; found %r" % (found,))
        assert found.count(parented) == 1, "reported twice: %r" % (found,)
    finally:
        _retire(parented)

    assert APP._screen_threads(screen) == [], "a finished worker is not running"
    print("shutdown finds Qt-parented workers: OK")


def test_a_worker_held_in_an_attribute_is_still_found():
    """The other half: the screens' own workers have no Qt parent at all."""
    screen = QWidget()
    screen.worker = _Ticker()
    screen.workers = [_Ticker()]
    screen.worker.start()
    screen.workers[0].start()
    try:
        assert _running(screen.worker) and _running(screen.workers[0])
        found = APP._screen_threads(screen)
        assert screen.worker in found, found
        assert screen.workers[0] in found, found
    finally:
        _retire(screen.worker)
        _retire(screen.workers[0])
    print("shutdown still finds detached workers: OK")


def test_the_second_row_survives_a_context_line_that_only_counts_up():
    """A running campaign publishes a new line per message; the tabs must stay.

    `AppShell._sync` rebuilt the whole row on every call, so each message
    deleted and recreated the four sub-tab buttons — under the user's pointer,
    at 2.2ms a time, and 2.2ms again when the line had not changed at all.

    Read through `AppShell._sections()` rather than off `_sub_tabs`, because
    there are two rows now and the shell picks between them: a screen's sections
    are indented rows in the navigation rail when it is open and a row under the
    page header when it is collapsed. The contract is the one it always was —
    whichever row they are in, a context line may not rebuild them.
    """
    shell = APP.AppShell()
    shell.register("outreach", "Outreach", QWidget)
    shell.go("outreach")
    shell.set_subtabs("outreach", ("Leads", "Campaign", "Sending", "Stats"),
                      lambda _index: None, 0)

    tabs = list(shell._sections())
    assert [tab.text() for tab in tabs] == ["Leads", "Campaign", "Sending", "Stats"]

    for sent in range(1, 6):
        shell.set_context("outreach", "Sending — %d of 200" % sent, tone="warning")
        assert list(shell._sections()) == tabs, (
            "the sub-tab buttons were rebuilt by message %d" % sent)

    shell.set_context("outreach", "Sending — 5 of 200", tone="warning")
    assert list(shell._sections()) == tabs, "an unchanged line rebuilt the row"
    shell.set_subtabs("outreach", ("Leads", "Campaign", "Sending", "Stats"),
                      lambda _index: None, 2)
    assert list(shell._sections()) == tabs, "unchanged labels rebuilt the row"
    assert [tab.text() for tab in shell._sections() if tab.isChecked()] == ["Sending"]

    shell.set_subtabs("outreach", ("One", "Two"), lambda _index: None, 0)
    assert [tab.text() for tab in shell._sections()] == ["One", "Two"], \
        "labels that did change were not applied"
    print("the second row is left alone when it has not changed: OK")


def test_the_window_closes_the_store_on_the_way_out():
    """Nothing did, so the write-ahead log was never folded back in.

    Asserted through `MainWindow.shutdown_store` rather than by opening a window,
    because the point is that the store is closed *after* the threads and by
    something the close path actually calls.
    """
    assert hasattr(APP.MainWindow, "shutdown_store"), (
        "the window has no way to close the store")

    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            conn = DB.connect(os.path.join(tmp, "outreach.db"))
            for _ in range(200):
                DB.log_event(conn, "probe", "x" * 200)
            wal = os.path.join(tmp, "outreach.db-wal")
            assert os.path.exists(wal) and os.path.getsize(wal) > 0, (
                "expected a write-ahead log to have something in it")

            APP.MainWindow.shutdown_store()

            assert not DB._CONNS, "the connection cache still holds a handle"
            assert not os.path.exists(wal) or os.path.getsize(wal) == 0, (
                "the log was not checkpointed: %d bytes left" % os.path.getsize(wal))
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original
    print("the window closes the store: OK")


def test_close_all_at_exit():
    """Not a test — Windows will not delete the temp dir with the db open."""
    DB.close_all()
