"""SQLite store for the outreach pipeline — leads, campaigns, the send queue.

Why a database and not another JSON file: a campaign is a *schedule*, not a
document. Five hundred leads sent at forty a day is a fortnight of wall-clock
time, so the queue has to survive the app being closed and reopened, and two
threads (the Qt worker doing the sending, the GUI redrawing counters) read and
write it at the same time. That is what SQLite is for.

The concurrency rules are baked in here so no caller has to think about them:

* one connection is opened lazily per path and shared — `connect()` with no
  argument always hands back the same object, so the worker thread and the GUI
  thread see each other's writes the moment they land;
* WAL journalling, so a reader is never blocked behind the writer;
* `check_same_thread=False` plus a module-level re-entrant lock around every
  write, which is what actually serialises those two threads;
* rows come back as plain dicts, never `sqlite3.Row` — they cross a pyqtSignal
  boundary, land in table models and get `json.dumps`ed, and a Row does none of
  those things.

Nothing here raises. A locked, corrupt or missing database degrades to an empty
list, a zero id or a False, because the alternative is a traceback out of a
worker thread and a dead GUI. The blobs stay as text: `audit_json` and
`ai_json` are handed back exactly as stored, and the caller decodes them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

from core import settings as _settings

DB_FILENAME = "outreach.db"

LEAD_STATUSES = ("new", "audited", "queued", "sent", "replied",
                 "bounced", "skipped", "suppressed")
MESSAGE_STATUSES = ("queued", "sending", "sent", "failed",
                    "skipped", "bounced", "replied")

_HOUR_SEC = 3600.0

# Re-entrant because the compound writes (`upsert_lead`, `suppress`) hold it
# across several statements that each take it again.
_LOCK = threading.RLock()
_CONNS: dict[str, sqlite3.Connection] = {}


# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_messages_due ON messages(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_messages_campaign ON messages(campaign_id);
CREATE INDEX IF NOT EXISTS idx_sends_account_ts ON sends(account_email, ts);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
"""


# ── Connection ───────────────────────────────────────────────────────────────

def _default_path() -> str:
    """Resolved late — the test suite repoints `settings.SETTINGS_DIR`."""
    return os.path.join(_settings.SETTINGS_DIR, DB_FILENAME)


def connect(path: str = "") -> sqlite3.Connection:
    """Open (or reuse) the outreach database, schema already applied.

    Handles are cached per absolute path, so the whole process shares one
    connection and therefore one write lock. A file that cannot be opened at
    all falls back to an in-memory database: the outreach screen then shows an
    empty, useless-but-alive store with the reason in the event log, which is a
    better failure than the GUI refusing to start.
    """
    target = os.path.abspath(path or _default_path())
    with _LOCK:
        cached = _CONNS.get(target)
        if cached is not None:
            return cached
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            conn = _open(target)
            failure = ""
        except (sqlite3.Error, OSError) as exc:
            conn = _open(":memory:")
            failure = f"{target}: {exc}"
        _CONNS[target] = conn
        if failure:
            log_event(conn, "db_error", failure)
        return conn


def _open(target: str) -> sqlite3.Connection:
    conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # busy_timeout matters more than it looks: WAL still serialises writers, and
    # the worker thread must wait for the GUI's write rather than fail the send.
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    init_db(conn)
    return conn


def init_db(conn) -> None:
    """Create the tables and indexes. Idempotent; safe on every open."""
    conn = _resolve(conn)
    with _LOCK:
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        except sqlite3.Error:
            pass


def close_all() -> None:
    """Close every cached connection — app shutdown, and temp dirs in tests.

    Windows will not delete an open database file, so a test that skips this
    leaves its temp directory behind.
    """
    with _LOCK:
        for conn in _CONNS.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _CONNS.clear()


def _resolve(conn) -> sqlite3.Connection:
    """Callers may pass the shared connection implicitly by passing None."""
    return conn if conn is not None else connect()


# ── Row / value helpers ──────────────────────────────────────────────────────

def _query(conn, sql: str, params: tuple = ()) -> list[dict]:
    """Read rows as plain dicts. Never raises; an unusable DB reads as empty."""
    try:
        return [dict(row) for row in _resolve(conn).execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _one(conn, sql: str, params: tuple = ()) -> dict:
    rows = _query(conn, sql, params)
    return rows[0] if rows else {}


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    row = _one(conn, sql, params)
    values = list(row.values())
    return _int(values[0]) if values else 0


def _write(conn, sql: str, params: tuple = ()) -> int:
    """Run one statement under the write lock. Returns the new rowid, else 0."""
    with _LOCK:
        try:
            conn = _resolve(conn)
            cursor = conn.execute(sql, params)
            conn.commit()
            return _int(cursor.lastrowid)
        except sqlite3.Error:
            return 0


def _assign(changes: dict) -> str:
    return ", ".join(f"{column} = ?" for column in changes)


def _text(value) -> str:
    return "" if value is None else str(value)


def _email(value) -> str:
    """Addresses are normalised on the way in so every lookup can compare raw."""
    return _text(value).strip().lower()


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json(value) -> str:
    """Serialise a blob column. Already-encoded text passes straight through."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return ""


def _has_payload(blob: str) -> bool:
    return blob.strip() not in ("", "{}", "[]", "null")


def _domain_of(url) -> str:
    host = _text(url).strip().lower()
    if not host:
        return ""
    host = host.split("//")[-1].split("/")[0].split("?")[0].split("#")[0]
    host = host.rpartition("@")[2].split(":")[0]
    return host[4:] if host.startswith("www.") else host


# ── Leads ────────────────────────────────────────────────────────────────────

_LEAD_TEXT_FIELDS = ("name", "website", "phone", "city", "category",
                     "rating", "maps_link", "source", "status")


def _lead_columns(lead: dict, email: str) -> dict:
    """The non-empty subset of `lead` that maps onto real columns.

    Missing keys, blank strings and a zero score are dropped rather than
    written. That is what makes a re-scrape additive: a second sighting of the
    same business that only knows its phone number cannot blank out the website
    and the audit that the first pass paid for.
    """
    out: dict = {}
    for key in _LEAD_TEXT_FIELDS:
        value = _text(lead.get(key)).strip()
        if value:
            out[key] = value

    domain = _text(lead.get("domain")).strip().lower()
    if not domain:
        domain = _domain_of(lead.get("website")) or email.rpartition("@")[2]
    if domain:
        out["domain"] = domain

    for column, alias in (("audit_json", "audit"), ("ai_json", "ai")):
        raw = lead.get(column)
        blob = _json(raw if raw is not None else lead.get(alias))
        if _has_payload(blob):
            out[column] = blob

    score = _int(lead.get("opportunity_score"))
    if score > 0:
        out["opportunity_score"] = score
    return out


def upsert_lead(conn, lead: dict) -> int:
    """Insert or merge a lead, deduped on the lowercased email. 0 if unusable.

    The read and the write share the lock: two enrichment threads finding the
    same address at the same moment must not both see "no row" and race into a
    UNIQUE violation.
    """
    lead = lead or {}
    email = _email(lead.get("email"))
    if "@" not in email:
        return 0

    incoming = _lead_columns(lead, email)
    now = time.time()
    with _LOCK:
        existing = _one(conn, "SELECT * FROM leads WHERE email = ?", (email,))
        if not existing:
            columns = ["email", "created_at", "updated_at", *incoming]
            values = [email, now, now, *incoming.values()]
            placeholders = ", ".join("?" * len(columns))
            return _write(
                conn,
                f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(values),
            )

        changes = {k: v for k, v in incoming.items() if v != existing.get(k)}
        if changes:
            changes["updated_at"] = now
            _write(conn, f"UPDATE leads SET {_assign(changes)} WHERE id = ?",
                   (*changes.values(), existing["id"]))
        return _int(existing.get("id"))


def get_lead(conn, lead_id: int) -> dict:
    return _one(conn, "SELECT * FROM leads WHERE id = ?", (_int(lead_id),))


def _lead_scope(head: str, status: str, campaign_id: int) -> tuple[str, list]:
    where, params = [], []
    if campaign_id:
        head += " JOIN messages ON messages.lead_id = leads.id"
        where.append("messages.campaign_id = ?")
        params.append(campaign_id)
    if status:
        where.append("leads.status = ?")
        params.append(status)
    if where:
        head += " WHERE " + " AND ".join(where)
    return head, params


def list_leads(conn, *, status: str = "", campaign_id: int = 0,
               limit: int = 0, offset: int = 0) -> list[dict]:
    """Leads, oldest first. `limit=0` means all of them.

    Ordered by id rather than by score so that paging with `offset` is stable
    while the audit pass is still rewriting scores underneath the table.
    """
    sql, params = _lead_scope("SELECT DISTINCT leads.* FROM leads",
                              _text(status).strip(), _int(campaign_id))
    sql += " ORDER BY leads.id"
    limit, offset = _int(limit), _int(offset)
    if limit > 0 or offset > 0:
        sql += " LIMIT ? OFFSET ?"
        params += [limit if limit > 0 else -1, max(0, offset)]
    return _query(conn, sql, tuple(params))


def count_leads(conn, **filters) -> int:
    sql, params = _lead_scope("SELECT COUNT(DISTINCT leads.id) FROM leads",
                              _text(filters.get("status")).strip(),
                              _int(filters.get("campaign_id")))
    return _scalar(conn, sql, tuple(params))


def set_lead_audit(conn, lead_id: int, audit: dict, ai: dict) -> None:
    """Store the audit and AI blobs against a lead.

    An empty audit (the site was unreachable, the model was out of budget)
    leaves whatever is already stored alone — a later successful pass is the
    only thing that overwrites it. The status only advances out of 'new', so
    re-auditing a lead that has already been mailed does not reset its state.
    """
    changes: dict = {}
    audit_blob = _json(audit)
    if _has_payload(audit_blob):
        changes["audit_json"] = audit_blob
        changes["opportunity_score"] = _int((audit or {}).get("opportunity_score"))
    ai_blob = _json(ai)
    if _has_payload(ai_blob):
        changes["ai_json"] = ai_blob
    if not changes:
        return

    changes["updated_at"] = time.time()
    _write(
        conn,
        f"UPDATE leads SET {_assign(changes)}, "
        "status = CASE WHEN status = 'new' THEN 'audited' ELSE status END "
        "WHERE id = ?",
        (*changes.values(), _int(lead_id)),
    )


# ── Campaigns ────────────────────────────────────────────────────────────────

def create_campaign(conn, name: str, template_id: str, profile: dict,
                    settings_snapshot: dict) -> int:
    """Start a campaign, freezing the profile and settings that shaped it.

    The snapshot is not redundant with `settings.json`: a campaign runs for days
    and the user will change the sender profile or the caps mid-flight, and the
    already-queued copy has to stay explainable afterwards.
    """
    return _write(
        conn,
        "INSERT INTO campaigns (name, template_id, profile_json, settings_json, "
        "status, created_at) VALUES (?, ?, ?, ?, 'draft', ?)",
        (_text(name), _text(template_id), _json(profile or {}),
         _json(settings_snapshot or {}), time.time()),
    )


def get_campaign(conn, campaign_id: int) -> dict:
    return _one(conn, "SELECT * FROM campaigns WHERE id = ?", (_int(campaign_id),))


def list_campaigns(conn) -> list[dict]:
    return _query(conn, "SELECT * FROM campaigns ORDER BY id DESC")


def set_campaign_status(conn, campaign_id: int, status: str) -> None:
    _write(conn, "UPDATE campaigns SET status = ? WHERE id = ?",
           (_text(status).strip(), _int(campaign_id)))


# ── Messages ─────────────────────────────────────────────────────────────────

def queue_message(conn, msg: dict) -> int:
    """Queue one rendered message. Returns 0 when the lead is suppressed.

    Refusing here rather than at send time is deliberate: an unsubscribe must be
    honoured by every later planning pass too, not only by the send loop.
    """
    msg = msg or {}
    lead_id = _int(msg.get("lead_id"))
    if lead_id and _lead_suppressed(conn, lead_id):
        return 0

    row = {
        "campaign_id": _int(msg.get("campaign_id")),
        "lead_id": lead_id,
        "step": _int(msg.get("step")),
        "subject": _text(msg.get("subject")),
        "body_text": _text(msg.get("body_text")),
        "body_html": _text(msg.get("body_html")),
        "account_email": _email(msg.get("account_email")),
        "status": _text(msg.get("status")).strip() or "queued",
        "scheduled_at": _float(msg.get("scheduled_at")),
        "message_id": _text(msg.get("message_id")),
        "created_at": time.time(),
    }
    placeholders = ", ".join("?" * len(row))
    return _write(conn,
                  f"INSERT INTO messages ({', '.join(row)}) VALUES ({placeholders})",
                  tuple(row.values()))


def due_messages(conn, now_ts: float, limit: int = 50) -> list[dict]:
    """Queued messages that are ready to send, earliest first.

    Suppressed leads are filtered out in the query rather than by the caller:
    the plan can be days old, and an unsubscribe that arrived in between must
    take effect even for a message whose row `suppress()` never saw.
    """
    return _query(
        conn,
        "SELECT messages.* FROM messages "
        "LEFT JOIN leads ON leads.id = messages.lead_id "
        "WHERE messages.status = 'queued' AND messages.scheduled_at <= ? "
        "AND NOT EXISTS (SELECT 1 FROM suppression "
        "                WHERE suppression.email = LOWER(COALESCE(leads.email, ''))) "
        "ORDER BY messages.scheduled_at ASC, messages.id ASC LIMIT ?",
        (_float(now_ts), _int(limit) if _int(limit) > 0 else -1),
    )


_MESSAGE_NUMERIC = {"scheduled_at": _float, "sent_at": _float,
                    "step": _int, "campaign_id": _int, "lead_id": _int}
_MESSAGE_UPDATABLE = ("sent_at", "error", "message_id", "account_email",
                      "scheduled_at", "subject", "body_text", "body_html",
                      "step", "campaign_id", "lead_id")


def first_touch_message_id(conn, campaign_id: int, lead_id: int) -> str:
    """The Message-ID of this lead's step-0 message, once it has been sent."""
    rows = _query(conn,
                  "SELECT message_id FROM messages WHERE campaign_id = ? AND lead_id = ? "
                  "AND step = 0 AND message_id != '' ORDER BY id LIMIT 1",
                  (_int(campaign_id), _int(lead_id)))
    return _text(rows[0].get("message_id")) if rows else ""


def claimed_messages(conn, campaign_id: int = 0) -> list[dict]:
    """Messages left in `sending` by a run that did not finish."""
    if campaign_id:
        return _query(conn, "SELECT * FROM messages WHERE status = 'sending' "
                            "AND campaign_id = ? ORDER BY id", (_int(campaign_id),))
    return _query(conn, "SELECT * FROM messages WHERE status = 'sending' ORDER BY id")


def mark_message(conn, message_id, /, status: str, **fields) -> None:
    """Move a message to `status`, writing any of its other columns alongside.

    The connection and the row id are positional-only on purpose. The row id and
    the RFC 5322 header share the name `message_id`, and the slash is what lets
    a caller write `mark_message(conn, row_id, "sent", message_id="<x@y>")`
    without the two colliding into a TypeError.
    """
    changes = {}
    for key in _MESSAGE_UPDATABLE:
        if key in fields:
            coerce = _MESSAGE_NUMERIC.get(key, _text)
            changes[key] = coerce(fields[key])
    status = _text(status).strip()
    if status:
        changes["status"] = status
    if not changes:
        return
    _write(conn, f"UPDATE messages SET {_assign(changes)} WHERE id = ?",
           (*changes.values(), _int(message_id)))


def campaign_stats(conn, campaign_id: int) -> dict:
    """Per-status message counts for the stats tiles. Every key always present."""
    campaign_id = _int(campaign_id)
    stats = {"campaign_id": campaign_id, "total": 0, "leads": 0}
    stats.update({status: 0 for status in MESSAGE_STATUSES})

    for row in _query(conn, "SELECT status, COUNT(*) AS n FROM messages "
                            "WHERE campaign_id = ? GROUP BY status", (campaign_id,)):
        count = _int(row.get("n"))
        stats[_text(row.get("status")).strip() or "queued"] = count
        stats["total"] += count
    stats["leads"] = _scalar(conn, "SELECT COUNT(DISTINCT lead_id) FROM messages "
                                   "WHERE campaign_id = ?", (campaign_id,))
    return stats


# ── Suppression ──────────────────────────────────────────────────────────────

def suppress(conn, email: str, reason: str) -> None:
    """Add an address to the do-not-contact list and unwind what is queued.

    Cancelling the queued messages is the whole point: by the time somebody
    unsubscribes, their follow-ups are already scheduled days out, and leaving
    them queued would send mail to a person who has explicitly opted out.
    """
    address = _email(email)
    if "@" not in address:
        return

    now = time.time()
    with _LOCK:
        _write(conn,
               "INSERT INTO suppression (email, reason, added_at) VALUES (?, ?, ?) "
               "ON CONFLICT(email) DO UPDATE SET reason = excluded.reason",
               (address, _text(reason), now))
        _write(conn,
               "UPDATE messages SET status = 'skipped', error = 'suppressed' "
               "WHERE status IN ('queued', 'sending') "
               "AND lead_id IN (SELECT id FROM leads WHERE LOWER(email) = ?)",
               (address,))
        _write(conn, "UPDATE leads SET status = 'suppressed', updated_at = ? "
                     "WHERE LOWER(email) = ?", (now, address))
        detail = f"{address}: {reason}" if reason else address
        log_event(conn, "suppressed", detail)


def is_suppressed(conn, email: str) -> bool:
    return bool(_scalar(conn, "SELECT COUNT(*) FROM suppression WHERE email = ?",
                        (_email(email),)))


def _lead_suppressed(conn, lead_id: int) -> bool:
    return bool(_scalar(
        conn,
        "SELECT COUNT(*) FROM leads JOIN suppression "
        "ON suppression.email = LOWER(leads.email) WHERE leads.id = ?",
        (_int(lead_id),),
    ))


def suppression_list(conn) -> list[dict]:
    return _query(conn, "SELECT * FROM suppression ORDER BY added_at DESC, email")


# ── Send log and quotas ──────────────────────────────────────────────────────

def record_send(conn, account_email: str, ts: float) -> None:
    """Log one delivered message against the sending account's quota."""
    _write(conn, "INSERT INTO sends (account_email, ts) VALUES (?, ?)",
           (_email(account_email), _float(ts) or time.time()))


def _zone(tz):
    """A tzinfo from a tzinfo, an IANA name, or "local" (None = machine local).

    zoneinfo needs a tz database and Windows ships none, so an IANA name is only
    resolvable when the `tzdata` package happens to be installed. An
    unresolvable zone degrades to the machine's local time rather than taking
    the send loop down with it.
    """
    if isinstance(tz, tzinfo):
        return tz
    name = _text(tz).strip()
    if name and name.lower() != "local":
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return None


def _day_start(now_ts: float, tz) -> float:
    """Epoch seconds of the most recent local midnight in `tz`."""
    zone = _zone(tz)
    local = datetime.fromtimestamp(now_ts, zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def sent_today(conn, account_email: str, tz, *, now_ts: float = 0.0) -> int:
    """Sends by this account since local midnight in `tz`.

    Deliberately *not* a rolling 24 hours. Gmail's daily limit resets on the
    calendar-day boundary, so a rolling window either strands most of a day's
    quota unused or — far worse — lets the run keep going straight past midnight
    on yesterday's budget, which is how an account gets suspended.

    `now_ts` exists so the scheduler and the tests can ask about a fixed
    instant instead of "now".
    """
    start = _day_start(_float(now_ts) or time.time(), tz)
    return _scalar(conn, "SELECT COUNT(*) FROM sends WHERE account_email = ? AND ts >= ?",
                   (_email(account_email), start))


def sent_last_hour(conn, account_email: str, *, now_ts: float = 0.0) -> int:
    """Sends in the trailing 60 minutes.

    A rolling window is correct here where it is wrong for the daily count: the
    hourly cap exists to flatten bursts, and nothing about it resets on a clock
    hour.
    """
    now = _float(now_ts) or time.time()
    return _scalar(conn, "SELECT COUNT(*) FROM sends WHERE account_email = ? AND ts >= ?",
                   (_email(account_email), now - _HOUR_SEC))


# ── Events ───────────────────────────────────────────────────────────────────

def log_event(conn, kind: str, detail: str, lead_id: int = 0) -> None:
    _write(conn, "INSERT INTO events (ts, kind, detail, lead_id) VALUES (?, ?, ?, ?)",
           (time.time(), _text(kind).strip(), _text(detail), _int(lead_id)))


def recent_events(conn, limit: int = 200) -> list[dict]:
    """Newest first — the log pane appends at the top."""
    return _query(conn, "SELECT * FROM events ORDER BY id DESC LIMIT ?",
                  (_int(limit) if _int(limit) > 0 else -1,))
