"""Send scheduling, and the two worker threads that run a campaign.

Two very different things live here on purpose.

`next_send_times` is a pure function. Given a count, the accounts, the settings
and a start instant it returns the exact second each message leaves, with no
clock, no database and no Qt anywhere near it. That matters because this
function is the only thing standing between the user and a suspended Gmail
account, and the only way to know it is right is to be able to test it: a fixed
seed produces a fixed schedule, so every rule below is asserted offline in
tests/test_schedule.py.

The rules are all one idea — do not look like software. Sends stay inside
working hours on working days, no account passes its daily or hourly cap, a new
account ramps up over its first fortnight instead of opening at full rate, and
the gap between two sends is drawn from a range rather than being a constant.
That last one carries more weight than it looks: a message every 180 seconds on
the dot is the clearest automation fingerprint a mail provider can read, and it
costs nothing at all to avoid.

`OutreachWorker` then does as little thinking as possible. It re-checks the caps
and the window at send time, because a plan made on Friday afternoon is still in
the queue on Monday and the user will have changed the settings twice in
between. It waits in quarter-second slices so Stop lands immediately, and
`abort()` closes the SMTP socket from the outside for exactly the reason
`ScrapeWorker.abort()` quits the browser: a thread blocked in a network call
cannot check a flag.

**Two channels, one scheduler.** WhatsApp is a second transport, not a second
send loop. `next_send_times` already takes every number it enforces as an
argument, so `channel_settings` hands it the WhatsApp numbers under the names it
already reads and the whole of the window, the caps, the warm-up, the jitter and
the spill across days apply to WhatsApp for free. A second copy of that function
would have drifted from this one inside a month, and the drift would show up as
a banned number rather than as a failing test.

`OutreachWorker` gains a `channel` and picks its transport from it. Everything
the email path earned holds unchanged on WhatsApp — pacing consumed on every
attempt whatever the outcome, an account benched rather than the run, resume
without double-sending, a dry run that opens nothing and hands the queue back,
and a held queue that says what is holding it — plus one rule email has no need
of: `BANNED:` stops this run and every future run until the user acknowledges
it, because continuing after a restriction is how a temporary block becomes
permanent.

A campaign is single-channel, and a lead reached on one channel is not reached
on the other unless the user asks for it by name. Being contacted twice in a
week by the same stranger on two channels is what gets a sender reported.

Nothing here raises across the module boundary. A campaign that cannot be
planned comes back as a plan dict carrying `error`, and a worker that hits
something unexpected emits `error_signal` and finishes cleanly.
"""

from __future__ import annotations

import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core import audit as _audit
from core import enrich as _enrich
from core import mailer as _mailer
from core import outreach_db as _db
from core import settings as _settings
from core import templates as _templates
from core import whatsapp as _wa
from core.ai import AIClient

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except ImportError:  # the scheduler and the planner must import without a GUI
    class _DeadSignal:
        def emit(self, *args) -> None:
            pass

        def connect(self, *args, **kwargs) -> None:
            pass

    def pyqtSignal(*args, **kwargs):
        return _DeadSignal()

    class QThread:
        def __init__(self) -> None:
            pass

        def start(self) -> None:
            self.run()

        def run(self) -> None:
            pass

        def isRunning(self) -> bool:
            return False

        def wait(self, msecs: int = 0) -> bool:
            return True

        def terminate(self) -> None:
            pass


_DAY_SEC = 86400.0
_HOUR_SEC = 3600.0

# Stop resolution. Every wait in a worker is this long, repeated.
_SLICE = 0.25

# A schedule that cannot place its messages inside a year of working days is a
# misconfiguration (no send days, an inverted window), not a long campaign.
_MAX_PLAN_DAYS = 366

# Stands in for "no cap configured" so the three caps can be composed with
# min() without special cases. Larger than any plausible plan.
_NO_CAP = 1_000_000

# How many times a follow-up pass may be nudged later and drawn again before it
# is accepted as it stands. Each try costs one pure-function call and no I/O.
_PLACEMENT_TRIES = 4

# How far down an overdue queue one dispatch looks for a message that can go
# now. A cap rather than the whole backlog: each candidate past the first costs
# a lookup, and a head that nothing in twenty-five rows can get past is a queue
# that wants replanning rather than scanning.
_DISPATCH_SCAN = 25

# draft (created) -> scheduled (planned) -> running -> paused -> stopped | done
CAMPAIGN_STATUSES = ("draft", "scheduled", "running", "paused", "stopped", "done")

_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")

# Runtime spacing between sends. Unseeded on purpose: the plan is what tests
# assert on, and a live run has nothing to gain from being reproducible.
_RUNTIME_RNG = random.Random()


# ── Small helpers ────────────────────────────────────────────────────────────

def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    return "" if value is None else str(value)


def _loads(blob) -> dict:
    """Decode a stored JSON blob. Anything unusable reads as an empty dict."""
    if isinstance(blob, dict):
        return blob
    if not isinstance(blob, str) or not blob.strip():
        return {}
    try:
        data = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_day(value) -> date | None:
    match = _DATE_RE.search(_text(value))
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _report(progress, done: int, total: int, message: str) -> None:
    """Call a caller-supplied progress hook without letting it break the plan."""
    if progress is None:
        return
    try:
        progress(done, total, message)
    except Exception:
        pass


# ── Channels ─────────────────────────────────────────────────────────────────

EMAIL = _db.EMAIL
WHATSAPP = _db.WHATSAPP
CHANNELS = _db.CHANNELS

# The WhatsApp channel's identity in `sends` and on `messages.account_email`.
#
# One key, deliberately, where email has one per Gmail account. There is one
# WhatsApp session per profile and the caps belong to the number behind it, and
# that number cannot be read without opening a browser — which planning must
# never do. Keying the ledger on the number instead would mean a plan drawn
# before the session came up and the sends charged after it landed in two
# different buckets, and the day's allowance would be spent twice. A number
# swapped mid-campaign therefore inherits the ledger, which sends more slowly
# than it strictly must; the mistake in the other direction hands a fresh number
# a full day's allowance on the strength of a DOM read, and that one costs the
# number.
WA_ACCOUNT = "whatsapp"


def _channel(value, default: str = EMAIL) -> str:
    """A channel name. Anything unrecognised reads as email, as in the store."""
    name = _text(value).strip().lower()
    return name if name in CHANNELS else default


def other_channel(channel: str) -> str:
    return EMAIL if _channel(channel) == WHATSAPP else WHATSAPP


# scheduler key → (the WhatsApp key that supplies it, the value to fall back on).
#
# This is the whole of "one scheduler, two channels": every number
# `next_send_times` and the send loop read arrives under one of these names, so
# renaming them for the WhatsApp channel schedules WhatsApp correctly with no
# second copy of anything.
#
# The fallbacks repeat `core.settings.DEFAULT_SETTINGS` on purpose, and
# `tests/test_schedule.py` asserts they still match it. A missing key must not
# fall through to the *email* number sitting under the same name: it would hand
# WhatsApp email's 40 a day and 60-second floor, which is precisely the mistake
# every limit in the spec exists to prevent, and nothing would look wrong until
# the number was gone.
_WA_KEYS: dict[str, tuple[str, object]] = {
    "send_days": ("wa_send_days", [0, 1, 2, 3, 4]),
    "send_start_hour": ("wa_send_start_hour", 10),
    "send_end_hour": ("wa_send_end_hour", 19),
    "send_min_gap_sec": ("wa_min_gap_sec", 90),
    "send_max_gap_sec": ("wa_max_gap_sec", 300),
    "daily_cap_per_account": ("wa_daily_cap", 30),
    "hourly_cap_per_account": ("wa_hourly_cap", 8),
    "warmup_enabled": ("wa_warmup_enabled", True),
    "warmup_start": ("wa_warmup_start", 5),
    "warmup_step": ("wa_warmup_step", 3),
    "warmup_max": ("wa_warmup_max", 30),
    "followup_enabled": ("wa_followup_enabled", True),
    "followup_gap_days": ("wa_followup_gap_days", 3),
    "followup_max_steps": ("wa_followup_max_steps", 1),
    "dry_run": ("wa_dry_run", True),
}

# `send_timezone` is deliberately absent: the user's working hours are in one
# timezone whichever channel they are sending on.


def _wa_value(settings: dict, key: str, fallback):
    """One WhatsApp setting, from the user's file, then the defaults, then here."""
    settings = settings if isinstance(settings, dict) else {}
    if key in settings and settings[key] is not None:
        return settings[key]
    default = getattr(_settings, "DEFAULT_SETTINGS", {}).get(key)
    return fallback if default is None else default


def channel_settings(settings: dict, channel: str = EMAIL) -> dict:
    """`settings` with this channel's numbers under the scheduler's own names.

    Email gets the caller's own dict back, unchanged and un-copied, so nothing
    about the email path can shift by having been routed through here.
    """
    if not isinstance(settings, dict):
        return {}
    if _channel(channel) != WHATSAPP:
        return settings
    out = dict(settings)
    for key, (wa_key, fallback) in _WA_KEYS.items():
        out[key] = _wa_value(settings, wa_key, fallback)
    return out


def channel_accounts(settings: dict, channel: str = EMAIL) -> list[dict]:
    """Who may send on this channel, in the shape `next_send_times` reads.

    WhatsApp has exactly one: the connected number, described here as an account
    row so that the scheduler, the cap checks and the benching logic need no
    idea which channel they are running. An empty list when the channel is
    switched off is what stops a plan or a run rather than a comment asking
    nobody to.
    """
    settings = settings if isinstance(settings, dict) else {}
    if _channel(channel) != WHATSAPP:
        return _settings.smtp_accounts(settings)
    if not settings.get("wa_enabled", False):
        return []
    return [{"email": WA_ACCOUNT, "display_name": "", "app_password": "",
             "enabled": True, "imap_enabled": False,
             # No `wa_warmup_started` exists, so an undated account ramps from
             # the day the campaign began — the same guess-safe rule
             # `_warmup_cap` applies to a Gmail account nobody dated.
             "warmup_started": "",
             "daily_cap": _int(_wa_value(settings, "wa_daily_cap", 30), 30)}]


def wa_region(settings: dict) -> str:
    """The ISO region an unqualified number may be completed in. "" refuses."""
    return _text((settings or {}).get("wa_default_region")).strip().upper()


def wa_warnings(settings: dict) -> list[str]:
    """What the user should know before committing a WhatsApp campaign.

    The counterpart of `optout_warnings` on the email side, and said for the
    same reason: the thing that will bite is invisible at the moment of
    deciding. A blank region is not an error — it is the safe setting — but it
    silently drops every lead whose number was scraped without a `+`, which on a
    Maps list is most of them.
    """
    settings = settings if isinstance(settings, dict) else {}
    out = []
    if not wa_region(settings):
        out.append(
            "No default WhatsApp region is set, so any lead whose number was "
            "scraped without a + country code will be left out rather than "
            "guessed at. Set the region in Settings to include them.")
    return out


# ── A restriction outlives the run that hit it ───────────────────────────────

# `BANNED:` has to stop the next run too, and the next one after that, until the
# user says they have looked. Kept in `events` rather than in settings because a
# worker thread writing settings.json mid-run is a file the GUI is also holding,
# and because this is a fact about what happened, which is what that table is.
WA_BAN_EVENT = "wa_banned"
WA_BAN_ACK_EVENT = "wa_ban_acknowledged"

_WA_BAN_DEFAULT = "WhatsApp has restricted this number."


def wa_ban_notice(conn) -> str:
    """The unacknowledged restriction, "" when there is none.

    The newest of the two events wins, so acknowledging is a write and never a
    delete: what happened stays in the log, which is the only record the user
    has of why a fortnight of sending stopped.

    `events` carries no index on `kind`, so with no restriction ever recorded
    this walks the table backwards: measured at 1.8ms against 20,000 events,
    and 0.02ms once there is a ban to find. It is asked twice — at the start of
    a run and at the start of a plan — so that is where it stays. Capping the
    scan would be the obvious fix and is the wrong one: an old unacknowledged
    ban with a fortnight of sends written after it is exactly the case that must
    not be missed, and missing it means sending through a restriction.
    """
    try:
        rows = _db.rows(conn,
                        "SELECT kind, detail FROM events WHERE kind IN (?, ?) "
                        "ORDER BY id DESC LIMIT 1",
                        (WA_BAN_EVENT, WA_BAN_ACK_EVENT))
    except Exception:
        return ""
    if not rows or _text(rows[0].get("kind")) != WA_BAN_EVENT:
        return ""
    return _text(rows[0].get("detail")) or _WA_BAN_DEFAULT


def record_wa_ban(conn, detail: str = "") -> None:
    _db.log_event(conn, WA_BAN_EVENT, _text(detail) or _WA_BAN_DEFAULT)


def acknowledge_wa_ban(conn) -> bool:
    """The user has seen the restriction. True when there was one to clear."""
    if not wa_ban_notice(conn):
        return False
    _db.log_event(conn, WA_BAN_ACK_EVENT,
                  "the user acknowledged the WhatsApp restriction")
    return True


# ── Where a channel's copy comes from ────────────────────────────────────────

# The planner renders through one of these rather than reaching for
# `core.templates` directly, because a WhatsApp message has no subject, no HTML
# and its own register — and because that is the whole of the difference. One
# planner, one adapter per channel.


class _EmailCopy:
    """The email templates, as the planner wants them."""

    channel = EMAIL

    def for_step(self, step: int) -> list:
        return _templates.templates_for_step(_int(step))

    def get(self, template_id: str):
        return _templates.get_template(_text(template_id))

    def render(self, template, ctx: dict) -> tuple[str, str, str]:
        return _templates.render(template, ctx)

    def usable(self, subject: str, text: str) -> bool:
        return bool(subject and text)


class _WhatsAppCopy:
    """`core.wa_templates`, behind the same three questions.

    Held as a module rather than imported at the top of this file so that a
    build without it still plans and sends email — and so the tests can drive a
    stub, the way they stub the session and the SMTP sender.
    """

    channel = WHATSAPP

    def __init__(self, module) -> None:
        self._module = module

    def _all(self) -> list:
        return list(getattr(self._module, "WA_TEMPLATES", None) or [])

    def for_step(self, step: int) -> list:
        return [t for t in self._all() if _int(getattr(t, "step", 0)) == _int(step)]

    def get(self, template_id: str):
        wanted = _text(template_id)
        return next((t for t in self._all() if _text(getattr(t, "id", "")) == wanted), None)

    def render(self, template, ctx: dict) -> tuple[str, str, str]:
        # One string, and no subject or HTML to go with it: a WhatsApp message
        # is a chat bubble. The empty pair keeps the planner's tuple the same
        # shape on both channels rather than teaching it two.
        return "", _text(self._module.render_wa(template, ctx)).strip(), ""

    def usable(self, subject: str, text: str) -> bool:
        return bool(text)


def copy_for(channel: str = EMAIL):
    """The copy source for a channel, or None when there is not one.

    None rather than a raise: a plan that cannot be written comes back as a plan
    carrying `error`, and "the WhatsApp templates are missing from this build"
    is something to print rather than a traceback out of a worker thread.
    """
    if _channel(channel) != WHATSAPP:
        return _EmailCopy()
    try:
        from core import wa_templates as module
    except Exception:
        return None
    if not callable(getattr(module, "render_wa", None)):
        return None
    if not getattr(module, "WA_TEMPLATES", None):
        return None
    return _WhatsAppCopy(module)


# ── The sending window ───────────────────────────────────────────────────────

def _zone(settings: dict):
    """tzinfo for `send_timezone`; None means the machine's local time.

    Windows ships no tz database, so an IANA name only resolves when `tzdata`
    happens to be installed. An unresolvable zone degrades to local rather than
    taking the schedule down with it.
    """
    name = _text((settings or {}).get("send_timezone") or "local").strip()
    if name and name.lower() != "local":
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return None


def _hours(settings: dict) -> tuple[int, int]:
    start = min(23, max(0, _int((settings or {}).get("send_start_hour"), 9)))
    end = min(24, max(0, _int((settings or {}).get("send_end_hour"), 17)))
    if end <= start:
        # An inverted window would silently send nothing for ever. One hour is
        # a visible mistake; zero hours looks like the app is broken.
        end = min(24, start + 1)
    return start, end


def _send_days(settings: dict) -> set[int]:
    raw = (settings or {}).get("send_days") or []
    days = {_int(d) % 7 for d in raw if isinstance(d, (int, float))}
    return days or {0, 1, 2, 3, 4}


def _local_date(ts: float, zone) -> date:
    return datetime.fromtimestamp(ts, zone).date()


def _at(day: date, hour: int, zone) -> float:
    """Epoch seconds of `hour:00` on `day`. Hour 24 is the following midnight.

    Built by adding hours to local midnight rather than by constructing the
    hour directly, so a window that spans a DST change keeps its wall-clock
    meaning: nine in the morning is nine in the morning either side of it.
    """
    base = datetime(day.year, day.month, day.day, tzinfo=zone)
    return (base + timedelta(hours=hour)).timestamp()


def in_send_window(ts: float, settings: dict) -> bool:
    """Is `ts` a moment this campaign is allowed to send at?"""
    try:
        zone = _zone(settings)
        day = _local_date(ts, zone)
        if day.weekday() not in _send_days(settings):
            return False
        start, end = _hours(settings)
        return _at(day, start, zone) <= ts < _at(day, end, zone)
    except Exception:
        return False


def next_window_open(ts: float, settings: dict) -> float:
    """The first sendable instant at or after `ts`. `ts` itself when it is one."""
    try:
        zone = _zone(settings)
        start, end = _hours(settings)
        days = _send_days(settings)
        day = _local_date(ts, zone)
        for _ in range(_MAX_PLAN_DAYS):
            if day.weekday() in days:
                opens, closes = _at(day, start, zone), _at(day, end, zone)
                if ts < closes:
                    return max(ts, opens)
            day += timedelta(days=1)
        return ts
    except Exception:
        return ts


# ── Caps ─────────────────────────────────────────────────────────────────────

def _cap(value) -> int:
    """A configured cap, where missing or non-positive means "not configured".

    Reading a blank per-account cap as zero would silently retire the account
    the first time somebody cleared the field.
    """
    number = _int(value)
    return number if number > 0 else _NO_CAP


def _warmup_cap(account: dict, settings: dict, on_day: date, ramp_start: date | None) -> int:
    """The warm-up ceiling for `on_day`: start + step per elapsed day, capped.

    An account with no recorded `warmup_started` is treated as beginning its
    ramp on the first day of this plan, not as fully warmed. A brand-new Gmail
    account sending forty cold emails on its first morning is the precise
    failure the ramp exists to prevent, and guessing safe costs a few days.
    """
    started = _parse_day(account.get("warmup_started")) or ramp_start or on_day
    elapsed = max(0, (on_day - started).days)
    first = max(0, _int(settings.get("warmup_start"), 10))
    step = max(0, _int(settings.get("warmup_step"), 5))
    ceiling = max(0, _int(settings.get("warmup_max"), 40))
    return min(ceiling, first + step * elapsed)


def account_daily_cap(account: dict, settings: dict, *, on_day: date | None = None,
                      ramp_start: date | None = None) -> int:
    """How many messages `account` may send on `on_day`.

    The three limits compose as a minimum and never as an override: the global
    cap, the per-account cap and the warm-up ramp each have to allow the send.
    """
    account = account or {}
    settings = settings or {}
    caps = [_cap(settings.get("daily_cap_per_account")), _cap(account.get("daily_cap"))]
    if settings.get("warmup_enabled", True):
        caps.append(_warmup_cap(account, settings, on_day or date.today(), ramp_start))
    return max(0, min(caps))


def _hourly_cap(settings: dict) -> int:
    return _cap((settings or {}).get("hourly_cap_per_account"))


def _gap_bounds(settings: dict) -> tuple[int, int]:
    low = max(0, _int((settings or {}).get("send_min_gap_sec"), 60))
    high = max(0, _int((settings or {}).get("send_max_gap_sec"), 240))
    return (high, low) if high < low else (low, high)


# ── The scheduler ────────────────────────────────────────────────────────────

def next_send_times(*, count: int, accounts: list[dict], settings: dict,
                    start_ts: float, sent_today_by_account: dict[str, int],
                    seed: int = 0, ramp_start: date | None = None) -> list[tuple[float, str]]:
    """Returns [(timestamp, account_email)] of length <= count, ascending.

    Pure: no clock, no database, no network. Everything it needs arrives as an
    argument, which is what makes the send-rate rules testable offline.

    `ramp_start` is the day the warm-up counts from for accounts that have no
    `warmup_started` of their own, and defaults to the first day of the plan.
    Callers that replan a running campaign should pass the day the campaign
    began, or a replan on day three would drop those accounts back to their
    first-day rate.

    Shorter than `count` only when the plan cannot fit inside a year of
    sendable days — with no enabled account, or a settings file whose window
    and caps leave no room at all.
    """
    try:
        return _schedule(count, accounts, settings or {}, _float(start_ts),
                         sent_today_by_account or {}, _int(seed), ramp_start)
    except Exception:
        return []


def _schedule(count: int, accounts, settings: dict, start_ts: float,
              sent_today: dict, seed: int, ramp_start: date | None) -> list[tuple[float, str]]:
    count = _int(count)
    usable = [a for a in accounts or []
              if isinstance(a, dict) and _text(a.get("email")).strip()
              and a.get("enabled", True)]
    if count <= 0 or not usable:
        return []

    zone = _zone(settings)
    opens_at, closes_at = _hours(settings)
    weekdays = _send_days(settings)
    hourly = _hourly_cap(settings)
    low, high = _gap_bounds(settings)

    emails = [_text(a["email"]).strip().lower() for a in usable]
    already = {_text(k).strip().lower(): max(0, _int(v)) for k, v in (sent_today or {}).items()}

    # One generator per account, seeded from the account address as well as the
    # seed, so an account's gaps do not shift when a *different* account is
    # added, enabled or capped out. Determinism has to survive that.
    rngs = {email: random.Random("%d|%s" % (seed, email)) for email in emails}
    ready = {email: start_ts for email in emails}     # earliest next send
    history = {email: [] for email in emails}         # placed sends, for the hourly window

    out: list[tuple[float, str]] = []
    start_day = _local_date(start_ts, zone)
    ramp_from = ramp_start or start_day
    day = start_day

    for _ in range(_MAX_PLAN_DAYS):
        if len(out) >= count:
            break
        if day.weekday() in weekdays:
            window_start = max(start_ts, _at(day, opens_at, zone))
            window_end = _at(day, closes_at, zone)
            if window_start < window_end:
                left = {}
                for account, email in zip(usable, emails):
                    cap = account_daily_cap(account, settings, on_day=day, ramp_start=ramp_from)
                    spent = already.get(email, 0) if day == start_day else 0
                    left[email] = max(0, cap - spent)
                _fill_day(out, count, emails, left, ready, history, rngs,
                          window_start, window_end, hourly, low, high)
        day += timedelta(days=1)

    return out


def _fill_day(out, count, emails, left, ready, history, rngs,
              window_start, window_end, hourly, low, high) -> None:
    """Place as many sends as today's window and caps allow, ascending."""
    for email in emails:
        if ready[email] < window_start:
            ready[email] = window_start

    while len(out) < count:
        chosen, when, rank = "", 0.0, ()
        for index, email in enumerate(emails):
            if left[email] <= 0:
                continue
            candidate = _after_hourly(max(ready[email], window_start), history[email], hourly)
            if candidate >= window_end:
                continue
            # Earliest first; then the account that has sent least, which is
            # what makes the rotation hold when two accounts are free at the
            # same instant. The index last so the whole thing stays reproducible.
            key = (candidate, len(history[email]), index)
            if not chosen or key < rank:
                chosen, when, rank = email, candidate, key
        if not chosen:
            return

        out.append((when, chosen))
        left[chosen] -= 1
        history[chosen].append(when)
        ready[chosen] = when + rngs[chosen].randint(low, high)


def _after_hourly(ts: float, placed: list, cap: int) -> float:
    """`ts`, pushed later if it would be the (cap+1)th send in a rolling hour."""
    if cap >= _NO_CAP or not placed:
        return ts
    recent = [t for t in placed if t > ts - _HOUR_SEC]
    if len(recent) < cap:
        return ts
    # Moving to exactly one hour after the cap-th most recent send drops that
    # send out of the trailing window and leaves room for this one.
    return recent[-cap] + _HOUR_SEC


# ── Auditing one lead ────────────────────────────────────────────────────────

def audit_lead(lead: dict, *, settings: dict, ai=None, profile: dict | None = None,
               template_id: str = "") -> tuple[dict, dict]:
    """(audit, ai_fields) for one lead. Network-bound, never raises, no DB.

    The site is downloaded exactly once: `harvest_site` crawls it and its HTML
    is handed to `audit_site` as `prefetched`, so the audit costs no extra
    requests. A host that would not answer at all is downloaded zero more times
    — the audit is built offline from the nothing that came back, because
    `audit_site` with no prefetched HTML would go and time out against the same
    dead host again. DNS verification is switched off here because this path
    already has the address it is going to mail — the check only reorders
    *candidate* emails, which nothing downstream looks at.
    """
    lead = lead or {}
    settings = settings or {}
    profile = profile if isinstance(profile, dict) else (settings.get("sender_profile") or {})

    website = _text(lead.get("website")).strip()
    if not website:
        domain = _text(lead.get("domain")).strip() or _text(lead.get("email")).rpartition("@")[2]
        website = domain.strip()
    if not website:
        return {}, {}

    try:
        site = _enrich.harvest_site(
            website,
            max_pages=max(1, _int(settings.get("enrich_max_pages"), 4)),
            timeout=max(1.0, _float(settings.get("enrich_timeout"), 8.0)),
            workers=max(1, _int(settings.get("enrich_workers"), 6)),
            verify_dns=False,
            accept_free_mail=bool(settings.get("enrich_accept_free_mail", True)),
        )
        if site.get("reachable"):
            audit = _audit.audit_site(
                site.get("final_url") or website,
                max_pages=max(1, _int(settings.get("audit_max_pages"), 6)),
                timeout=max(1.0, _float(settings.get("audit_timeout"), 8.0)),
                prefetched=site.get("html") or {},
            )
        else:
            # The enricher has already paid the connection timeout — twice, for
            # an https host it retried over plain http. Across five hundred
            # leads a third dead wait each is minutes of nothing.
            #
            # Built through `unreachable_audit` rather than from an empty page
            # dict, because the reason is the whole point: it is what the Leads
            # table shows the operator, and `audit_from_html({}, url)` had
            # nowhere to put it.
            landed = _text(site.get("final_url")) or website
            audit = _audit.unreachable_audit(
                landed, _text(site.get("error")) or "unreachable", final_url=landed)
    except Exception:
        return {}, {}

    fields: dict = {}
    if ai is not None:
        try:
            fields = ai.personalize(
                business_name=_text(lead.get("name")),
                digest=_audit.digest(audit),
                profile=profile,
                tone=_text(profile.get("tone") or "direct"),
                template_id=template_id,
            )
        except Exception:
            fields = {}
    return audit, fields


# ── Compliance switches ──────────────────────────────────────────────────────

# Which context fields each switch owns. `unsubscribe_email` travels with the
# line it is the mailto for: on its own it renders nothing, and left behind it
# would hand a later footer an address the reader was never offered.
_COMPLIANCE_FIELDS: dict[str, tuple[str, ...]] = {
    "append_unsubscribe": ("unsubscribe_line", "unsubscribe_email"),
    "append_postal_address": ("postal_address",),
}


def apply_compliance(ctx: dict, settings: dict) -> dict:
    """Blank the footer fields the user has switched off. Mutates and returns `ctx`.

    `core.templates` builds both footers out of whatever is non-empty, so a
    field emptied here takes its separator with it: no dangling "|", no grey
    block under a rule with nothing in it. Both switches default to on.

    What this does not touch is `List-Unsubscribe`. That header is invisible to
    the reader, costs one line, and is the opt-out signal Gmail and Outlook
    actually weight; a user turning the visible line off has decided something
    about their copy, not about what the wire carries. `core.mailer` therefore
    keeps setting it on every send, and tidying that up to match this would cost
    the campaign its inbox placement to gain consistency nobody can see.

    Never raises: a context it cannot read comes back as it arrived.
    """
    if not isinstance(ctx, dict):
        return ctx
    settings = settings if isinstance(settings, dict) else {}
    for key, fields in _COMPLIANCE_FIELDS.items():
        if settings.get(key, True):
            continue
        for field in fields:
            if field in ctx:
                ctx[field] = ""
    return ctx


# ── Opt-out routes, and whether anything reads them ──────────────────────────

def reads_inbox(account: dict) -> bool:
    """Whether `_poll_inboxes` will ever open this account's mailbox.

    IMAP has to be switched on for the account and the App Password has to be
    stored, because the poll is a login. One predicate rather than two so the
    set the worker actually polls and the set `optout_warnings` measures against
    cannot drift apart — a warning that named a mailbox the app does read, or
    stayed quiet about one it does not, would be worse than no warning.
    """
    return bool(account.get("imap_enabled") and _text(account.get("email")).strip()
                and _text(account.get("app_password")))


def polled_mailboxes(settings: dict) -> set[str]:
    """Lowercased addresses of every mailbox this app reads opt-outs out of."""
    accounts = _settings.smtp_accounts(settings if isinstance(settings, dict) else {})
    return {_text(a.get("email")).strip().lower() for a in accounts if reads_inbox(a)}


def _optout_routes(settings: dict, profile: dict) -> list[str]:
    """Every mailbox a reader could reasonably send an opt-out to, in order.

    Three of them, and they need not be the same address: the footer sentence
    and `List-Unsubscribe` share one (see `core.templates.unsubscribe_address`),
    the footer also says to reply — which lands on `Reply-To` — and `Reply-To`
    falls back to the account the message left from.
    """
    routes = []
    reply_to = _text(profile.get("reply_to")).strip()
    for account in _settings.smtp_accounts(settings):
        sender = _text(account.get("email")).strip()
        routes.append(_templates.unsubscribe_address(profile, settings, sender))
        routes.append(reply_to or sender)
    seen, out = set(), []
    for route in routes:
        key = route.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(route.strip())
    return out


def optout_warnings(settings: dict, profile: dict | None = None) -> list[str]:
    """One sentence per opt-out route the reader is offered that nothing reads.

    Which mailboxes get opened is the user's own Gmail setup, so this cannot be
    fixed from in here. What can be fixed is the silence: a recipient who does
    everything right and keeps getting the chasers is the compliance failure
    that matters most, and from inside the app it looks exactly like a campaign
    nobody objected to.

    Addresses, not counts. "An opt-out route is unread" is not something anyone
    can act on, and the fix is per mailbox.
    """
    settings = settings if isinstance(settings, dict) else {}
    profile = profile if isinstance(profile, dict) else (settings.get("sender_profile") or {})
    polled = polled_mailboxes(settings)
    known = {_text(a.get("email")).strip().lower()
             for a in _settings.smtp_accounts(settings)}

    out = []
    for route in _optout_routes(settings, profile):
        key = route.lower()
        if key in polled:
            continue
        if key in known:
            out.append(
                "Opt-outs sent to %s will not be read: that account is set up to "
                "send but not to receive. Switch IMAP on for it in Settings." % route)
        else:
            out.append(
                "Opt-outs sent to %s will not be read: it is not one of the Gmail "
                "accounts this app can open, so a lead who asks to be removed there "
                "stays in the campaign and keeps getting the follow-ups." % route)
    return out


# ── Planning a campaign ──────────────────────────────────────────────────────

def _blank_plan(campaign_id: int, channel: str = EMAIL) -> dict:
    return {"campaign_id": _int(campaign_id), "channel": _channel(channel),
            "leads": 0, "queued": 0, "followups": 0,
            "skipped": 0, "skip_reasons": {}, "generic": 0, "generic_reasons": {},
            "accounts": [], "days": 0, "per_day": {}, "first_send": 0.0,
            "last_send": 0.0, "daily_cap": 0, "error": "", "cancelled": False,
            # Counted out of `skip_reasons` as well as into it, because the
            # summary has to answer "how many of my leads cannot be messaged at
            # all" before the user commits — the same way it reports how many
            # emails went out unpersonalised.
            "no_phone": 0, "other_channel": 0,
            "warnings": []}


def _skip(plan: dict, reason: str, count: int = 1) -> None:
    """Count a lead out of the campaign, and say why.

    "12 skipped" with no reason is a number the user cannot act on. The counts
    are per reason so the Campaign screen can name the one that matters — a
    record the renderer choked on is a bug report, a suppressed address is not.
    """
    if count <= 0:
        return
    plan["skipped"] += count
    reasons = plan.setdefault("skip_reasons", {})
    reasons[reason] = reasons.get(reason, 0) + count


def _generic(plan: dict, reason: str) -> None:
    """Count one email that went out as a form letter, and say why.

    `core.templates` discards every model sentence it cannot tie to the
    recipient, so a lead whose site never answered gets three paragraphs that
    could have been written before the crawl. That is the right trade — it
    claims nothing the sender does not know — but it is only the right trade if
    the user is told how much of their campaign it happened to.
    """
    plan["generic"] += 1
    reasons = plan.setdefault("generic_reasons", {})
    reasons[reason] = reasons.get(reason, 0) + 1


def plan_campaign(conn, *, campaign_id: int, leads: list[dict], template_id: str,
                  profile: dict, settings: dict, ai: AIClient | None,
                  progress=None, should_stop=None, channel: str = EMAIL,
                  allow_cross_channel: bool = False, copy=None) -> dict:
    """Audit, personalise, render and queue a whole campaign.

    Returns the plan summary the Campaign screen shows before the first send:
    how many messages, over how many days, from which accounts, starting when.
    `progress(done, total, message)` is called throughout if given.

    Skipped leads are counted, never queued: a suppressed address, an address
    that has already had a first touch, a site that produced no usable copy.

    `should_stop()` is polled between leads and must keep answering the same way
    until the call returns — it is a flag, not an event. A true answer stops the
    pass where it stands and comes back with `cancelled` set; whatever was
    already written to the queue stays written, so closing the app mid-plan
    costs the leads not yet reached and nothing else. Planning a five-hundred
    lead list is minutes of crawling, and without this the thread can only be
    killed.

    `channel` picks both the transport the queue is written for and the settings
    the schedule is drawn from — see `channel_settings`. A campaign is
    single-channel: every row it writes carries that one channel, and a lead
    that has already had a first touch on the *other* one is left out unless
    `allow_cross_channel` says the user asked for it by name. Being emailed and
    WhatsApped by the same stranger inside a week is what gets a sender
    reported, so the default is no and the override is explicit.

    `copy` is the template source and defaults to the one that belongs to the
    channel — a seam for a caller that has its own, and for the test that asks
    what a build with no `core.wa_templates` in it does.
    """
    conn = conn if conn is not None else _db.connect()
    channel = _channel(channel)
    plan = _blank_plan(campaign_id, channel)
    try:
        res = _plan(conn, plan, leads or [], _text(template_id), profile or {},
                    channel_settings(settings or {}, channel), ai, progress,
                    should_stop, channel, bool(allow_cross_channel),
                    copy if copy is not None else copy_for(channel))
        if res.get("cancelled"):
            _db.set_campaign_status(conn, campaign_id, "cancelled")
        elif res.get("error") and not res.get("queued"):
            _db.delete_campaign_messages(conn, campaign_id)
            _db.set_campaign_status(conn, campaign_id, "failed")
        return res
    except Exception as exc:
        plan["error"] = f"{type(exc).__name__}: {exc}"[:200]
        _db.delete_campaign_messages(conn, campaign_id)
        _db.set_campaign_status(conn, campaign_id, "failed")
        return plan


def _stopped(should_stop, plan: dict) -> bool:
    """Poll the caller's cancel flag. One that raises is read as "keep going"."""
    if should_stop is None:
        return False
    try:
        stop = bool(should_stop())
    except Exception:
        return False
    if stop:
        plan["cancelled"] = True
    return stop


def _plan(conn, plan: dict, leads: list, template_id: str, profile: dict,
          settings: dict, ai, progress, should_stop, channel: str = EMAIL,
          allow_cross_channel: bool = False, copy=None) -> dict:
    conn = conn if conn is not None else _db.connect()
    campaign_id = plan["campaign_id"]
    channel = _channel(channel)

    if copy is None:
        plan["error"] = ("the WhatsApp message templates are missing from this "
                         "build, so there is nothing to write from")
        return plan

    first_touch = copy.get(template_id)
    if first_touch is None or _int(getattr(first_touch, "step", 0)) != 0:
        candidates = copy.for_step(0)
        first_touch = candidates[0] if candidates else None
    if first_touch is None:
        plan["error"] = "there is no first-touch template to write from"
        return plan

    # Every other refusal in this file is a lead counted out with a reason, and
    # this one cannot be: a message needs somewhere to leave from, and no
    # setting makes that optional. It says where the fix is instead, because the
    # user reaches it having already chosen to go ahead without one.
    accounts = [a for a in channel_accounts(settings, channel)
                if _text(a.get("email")).strip()]
    if not accounts:
        plan["error"] = (
            "WhatsApp is switched off in Settings, so there is nothing to send "
            "from — switch it on and connect a number"
            if channel == WHATSAPP else
            "no Gmail account is set up to send from — add one in Settings")
        return plan
    plan["accounts"] = [a["email"] for a in accounts]

    if not profile:
        profile = settings.get("sender_profile") or {}

    # Said here because this is where the user decides to go ahead, and written
    # to the event log as well because it is a fact about their setup rather
    # than about this campaign — it will be just as true of the next one.
    if channel == WHATSAPP:
        plan["warnings"] = wa_warnings(settings)
        notice = wa_ban_notice(conn)
        if notice:
            plan["error"] = (
                "WhatsApp has restricted this number and the restriction has "
                "not been acknowledged — %s" % notice)
            return plan
    else:
        plan["warnings"] = optout_warnings(settings, profile)
        for warning in plan["warnings"]:
            _db.log_event(conn, "unread_optout", warning)

    rows = _eligible_leads(conn, leads, plan, settings, channel, allow_cross_channel)
    plan["leads"] = len(rows)
    if not rows:
        plan["error"] = plan["error"] or (
            "no leads left to contact — every one here has had a first touch "
            "already, or is suppressed")
        return plan

    total = len(rows)
    rows = _audit_pass(conn, rows, settings, profile, ai,
                       _text(getattr(first_touch, "id", "")), plan,
                       total, progress, should_stop)
    if plan["cancelled"]:
        return plan
    prepared = _render_pass(rows, first_touch, profile, settings, plan, total,
                            progress, should_stop, copy)
    if plan["cancelled"]:
        return plan
    if not prepared:
        plan["error"] = plan["error"] or (
            "no usable copy came out of any lead — audit them and try again")
        return plan

    now = time.time()
    ramp = campaign_start_day(conn, campaign_id, settings, now)
    sent_today = _sent_today(conn, accounts, settings, now, channel)
    slots = next_send_times(
        count=len(prepared), accounts=accounts, settings=settings, start_ts=now,
        sent_today_by_account=sent_today,
        # Seeding on the campaign keeps one campaign's minute-by-minute pattern
        # from being an exact repeat of the last one's.
        seed=campaign_id, ramp_start=ramp,
    )
    _skip(plan, "no room left in the sending window", len(prepared) - len(slots))
    plan["daily_cap"] = sum(
        account_daily_cap(a, settings, on_day=_local_date(now, _zone(settings)), ramp_start=ramp)
        for a in accounts)

    # What the follow-up passes need to keep placing inside the same caps the
    # first touches were placed inside: who may send, where the warm-up counts
    # from, and how much of the opening day is already spent.
    schedule = {"accounts": accounts, "ramp_start": ramp, "sent_today": sent_today,
                "start_day": _local_date(now, _zone(settings))}
    _queue_pass(conn, plan, prepared, slots, settings, profile, total, progress,
                schedule, should_stop, channel, copy)
    plan["days"] = len(plan["per_day"])
    if plan["queued"]:
        _db.set_campaign_status(conn, campaign_id, "scheduled")
        _db.log_event(conn, "planned",
                      "campaign %d on %s: %d queued, %d follow-ups, %d skipped, "
                      "%d not personalised"
                      % (campaign_id, channel, plan["queued"], plan["followups"],
                         plan["skipped"], plan["generic"]))
    return plan


def _first_touches(conn) -> dict[str, set[int]]:
    """Every lead that has had a first touch, by the channel it went out on.

    Read off `messages` rather than off `leads.status`, because the rule is
    about messages: a lead whose status was reset by a re-import is still a
    person who has had one cold email, and a second one is the compliance
    failure this guards against.

    'rehearsed' counts as pending, not as sent. Nobody has heard from that lead
    yet, but a first touch addressed to them is sitting in the store waiting to
    be put back on the queue, and queueing a second one behind it would mail
    the same stranger twice.

    Both channels come back from the one query. Not for the round trip — this
    runs once per plan — but because the two answers are read together and a
    second query is a second chance for them to disagree about a row written
    between them.

    Through `core.outreach_db` rather than off the connection, because the
    planner runs on its own thread beside the GUI and one shared connection is
    one shared statement cache — see `core.outreach_db._query`.
    """
    out: dict[str, set[int]] = {channel: set() for channel in CHANNELS}
    for row in _db.rows(
            conn,
            "SELECT DISTINCT lead_id, COALESCE(channel, 'email') AS channel "
            "FROM messages WHERE step = 0 "
            "AND status IN ('queued', 'sending', 'rehearsed', 'sent', "
            "               'replied', 'bounced')"):
        out[_channel(row.get("channel"))].add(_int(row.get("lead_id")))
    return out


def _contacted_lead_ids(conn, channel: str = EMAIL) -> set[int]:
    """Leads that already have a first touch on `channel`, sent or pending."""
    return _first_touches(conn)[_channel(channel)]


# Every pass below walks leads built out of scraped third-party text, and a
# value no amount of guarding upstream anticipated will eventually arrive. Each
# one therefore carries the same per-lead guard: the bad record takes itself out
# of the campaign, with a reason the Campaign screen can print, and the other
# 499 go on. Round 5 gave `_render_pass` that guard and left the three passes
# around it able to return a campaign of zero from one frame earlier.


def _eligible_leads(conn, leads: list, plan: dict, settings: dict = None,
                    channel: str = EMAIL,
                    allow_cross_channel: bool = False) -> list[dict]:
    """Stored lead rows for everything in `leads` that may still be contacted.

    The second channel adds three refusals to the two email always had, and each
    of them is a rule about the person rather than about the transport.

    A WhatsApp campaign needs a number this app can address, which is a stricter
    question than "has a phone": an unqualified number with no default region is
    refused rather than completed, because the country would have to be guessed
    and a wrong guess is a cold sales message on a stranger's phone abroad. Both
    counts reach the summary before the user commits.

    And a lead already touched on the *other* channel is left out unless the
    user has said otherwise. One person, one first approach: the same stranger
    getting an email on Tuesday and a WhatsApp message on Thursday is what gets
    a sender reported, and no recency window softens it here — a rule that
    quietly starts allowing it after seven days is one the user cannot see and
    cannot check before pressing Start.

    On WhatsApp the "already contacted" rule is asked of the *number* as well as
    of the lead, which on email has no counterpart worth having. Two branches of
    one business scraped as two listings share a switchboard, and one first
    touch each is one stranger receiving two cold pitches on the same phone from
    the same sender — the exact thing that gets a number reported. The lead pool
    cannot dedupe them, because they are genuinely two leads with two addresses.

    The lead pool is keyed on the email address (see `core.outreach_db`), so a
    business with a number and no address cannot be a lead on either channel.
    """
    settings = settings if isinstance(settings, dict) else {}
    channel = _channel(channel)
    touches = _first_touches(conn)
    contacted = touches[channel]
    elsewhere = set() if allow_cross_channel else touches[other_channel(channel)]
    region = wa_region(settings)
    numbers = _messaged_numbers(conn) if channel == WHATSAPP else set()

    seen: set[int] = set()
    rows = []
    for lead in leads:
        try:
            if not isinstance(lead, dict):
                _skip(plan, "not a lead record")
                continue
            email = _text(lead.get("email")).strip().lower()
            phone = _text(lead.get("phone")).strip()
            if "@" not in email or _db.is_suppressed(conn, email, phone=phone):
                _skip(plan, "no usable address, or suppressed")
                continue
            if channel == WHATSAPP and not _usable_number(plan, phone, region):
                continue
            lead_id = _int(lead.get("id")) or _db.upsert_lead(conn, lead)
            if not lead_id or lead_id in contacted or lead_id in seen:
                _skip(plan, "already contacted")
                continue
            if lead_id in elsewhere:
                plan["other_channel"] += 1
                _skip(plan, "already contacted on the other channel")
                continue
            tail = _wa.phone_key(phone) if channel == WHATSAPP else ""
            if tail and tail in numbers:
                _skip(plan, "another lead at this number has already been messaged")
                continue
            seen.add(lead_id)
            if tail:
                numbers.add(tail)
            rows.append(_db.get_lead(conn, lead_id) or dict(lead, id=lead_id))
        except Exception as exc:
            _skip(plan, "could not be read (%s)" % type(exc).__name__)
    return rows


def _messaged_numbers(conn) -> set[str]:
    """Every phone this app has already opened a WhatsApp chat to.

    Matched on `phone_key` rather than on the number as written, for the reason
    suppression is: the same phone reaches this app as "(416) 555-0142" from
    Maps and as "14165550142" from a reply, and a comparison that missed that
    pair would be no rule at all.
    """
    return {_text(row.get("tail")) for row in _db.rows(
        conn,
        "SELECT DISTINCT COALESCE(leads.phone_key, '') AS tail FROM messages "
        "JOIN leads ON leads.id = messages.lead_id "
        "WHERE messages.step = 0 AND COALESCE(messages.channel, 'email') = ? "
        "AND messages.status IN ('queued', 'sending', 'rehearsed', 'sent', "
        "                        'replied', 'bounced') "
        "AND COALESCE(leads.phone_key, '') != ''", (WHATSAPP,)) if row.get("tail")}


def _usable_number(plan: dict, phone: str, region: str) -> bool:
    """Can this number be messaged. Counts the refusal, with a reason, when not.

    Two refusals rather than one, because they are two different things for the
    user to do: a lead with no number at all is not a WhatsApp lead, and a lead
    whose number was scraped without a country code becomes one the moment a
    default region is set.
    """
    if not _wa.is_plausible(phone):
        plan["no_phone"] += 1
        _skip(plan, "no usable phone number")
        return False
    if not _wa.to_wa_id(phone, region):
        plan["no_phone"] += 1
        _skip(plan, "phone number has no country code, and no default WhatsApp "
                    "region is set to complete it")
        return False
    return True


def _audit_pass(conn, rows: list, settings: dict, profile: dict, ai,
                template_id: str, plan: dict, total: int, progress,
                should_stop) -> list[dict]:
    """Fill in the audit and AI blobs for leads that do not have them yet.

    Returns the rows still in the campaign. Results are collected as they land
    rather than in submission order, so a cancel can clear the backlog with
    `cancel_futures` instead of waiting for a queue of site crawls it is never
    going to use.
    """
    pending = [row for row in rows if not _loads(row.get("audit_json"))]
    if not pending or not settings.get("audit_enabled", True):
        return rows

    workers = max(1, min(12, _int(settings.get("enrich_workers"), 6)))
    done = 0
    dropped: set[int] = set()
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(audit_lead, row, settings=settings, ai=ai,
                               profile=profile, template_id=template_id): row
                   for row in pending}
        for future in as_completed(futures):
            if _stopped(should_stop, plan):
                break
            row = futures[future]
            try:
                audit, fields = future.result()
                _db.set_lead_audit(conn, _int(row.get("id")), audit, fields)
            except Exception as exc:
                _skip(plan, "could not be audited (%s)" % type(exc).__name__)
                dropped.add(id(row))
                continue
            row["audit_json"] = audit
            row["ai_json"] = fields
            done += 1
            _report(progress, done, total, "audited %s" % (row.get("name") or row.get("email")))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return [row for row in rows if id(row) not in dropped]


def _render_pass(rows: list, template, profile: dict, settings: dict, plan: dict,
                 total: int, progress, should_stop, copy=None) -> list[tuple]:
    """(lead, ctx, subject, text, html) per lead whose copy came out usable.

    Also where a form letter is counted. `core.templates` keeps a model sentence
    only when it refers to this recipient, so a lead whose site never answered
    renders three paragraphs that could have been written before the crawl —
    correct, and invisible until it is counted here.

    The merge fields are the same on both channels — same audit, same gap, same
    services — and only the copy that spends them differs. The compliance
    footers do not travel: they are an email's unsubscribe sentence and postal
    address, and a WhatsApp message carries its opt-out in the body where the
    reader will actually see it.
    """
    copy = copy if copy is not None else _EmailCopy()
    prepared = []
    for index, row in enumerate(rows, start=1):
        if _stopped(should_stop, plan):
            return prepared
        try:
            audit = _loads(row.get("audit_json"))
            fields = _loads(row.get("ai_json"))
            ctx = _templates.build_context(row, audit, fields, profile, settings)
            if copy.channel == EMAIL:
                ctx = apply_compliance(ctx, settings)
            subject, text, html = copy.render(template, ctx)
        except Exception as exc:
            _skip(plan, "could not be rendered (%s)" % type(exc).__name__)
            continue
        if not copy.usable(subject, text):
            _skip(plan, "no usable copy")
            continue
        if not ctx.get("personalised"):
            _generic(plan, _text(ctx.get("generic_reason")))
        prepared.append((row, ctx, subject, text, html))
        _report(progress, index, total, "prepared %s" % (row.get("name") or row.get("email")))
    return prepared


def _note_send(plan: dict, zone, when: float) -> None:
    """Fold one queued message into the summary the Campaign screen shows.

    Every message counts, follow-ups included. The user approves a multi-day
    send off this dict, so a summary that describes only the first touches
    understates the campaign by however many steps are configured.
    """
    day = _local_date(when, zone).isoformat()
    plan["per_day"][day] = plan["per_day"].get(day, 0) + 1
    plan["first_send"] = min(plan["first_send"] or when, when)
    plan["last_send"] = max(plan["last_send"], when)


def _for_account(text: str, html: str, profile: dict, settings: dict,
                 account_email: str) -> tuple[str, str]:
    """The rendered body with its footer pointed at `account_email`'s opt-out route.

    The copy is written before the schedule is drawn, so the footer arrives here
    naming whichever account came first. The account this message is placed on
    is known now, and `core.mailer.build_message` re-points the footer once more
    at whatever account it finally leaves from — so the queue reads honestly and
    the wire is right even when a capped account hands the message on.
    """
    address = _templates.unsubscribe_address(profile, settings, account_email)
    return (_templates.retarget_unsubscribe(text, address),
            _templates.retarget_unsubscribe(html, address))


def _queue_pass(conn, plan: dict, prepared: list, slots: list, settings: dict,
                profile: dict, total: int, progress, schedule: dict,
                should_stop, channel: str = EMAIL, copy=None) -> None:
    zone = _zone(settings)
    channel = _channel(channel)
    placed = []

    for index, ((row, ctx, subject, text, html), (when, account_email)) in enumerate(
            zip(prepared, slots), start=1):
        if _stopped(should_stop, plan):
            return
        try:
            lead_id = _int(row.get("id"))
            if channel == EMAIL:
                text, html = _for_account(text, html, profile, settings, account_email)
            message_id = _db.queue_message(conn, {
                "campaign_id": plan["campaign_id"], "lead_id": lead_id, "step": 0,
                "subject": subject, "body_text": text, "body_html": html,
                "account_email": account_email, "channel": channel,
                "scheduled_at": when,
            })
            if not message_id:
                _skip(plan, "could not be queued")
                continue

            plan["queued"] += 1
            _note_send(plan, zone, when)
            placed.append((lead_id, ctx, account_email, when))

            if _text(row.get("status")) in ("", "new", "audited"):
                _db.upsert_lead(conn, {"email": row.get("email"), "status": "queued"})
            _report(progress, index, total, "queued %s" % (row.get("name") or row.get("email")))
        except Exception as exc:
            _skip(plan, "could not be queued (%s)" % type(exc).__name__)

    if placed and settings.get("followup_enabled", True):
        _queue_followups(conn, plan, placed, settings, profile, schedule,
                         channel, copy)


def _queue_followups(conn, plan: dict, placed: list, settings: dict, profile: dict,
                     schedule: dict, channel: str = EMAIL, copy=None) -> None:
    """Place every follow-up step through `next_send_times`, like a first touch.

    A follow-up is another cold email to the same stranger, so it has to obey
    the same window, the same daily and hourly caps and the same spacing.
    Working it out as `first touch + n days` and snapping that into the next
    open window does none of those things: it doubles a day's volume the moment
    the steps overlap, and every weekend-jittered follow-up lands on the same
    window-open instant.

    One pass per step and per account. Per step because a chaser cannot be
    placed before the touch it chases; per account because a chaser from a
    different address reads as a different sender and loses the thread — and
    because the caps are per account anyway, so the passes do not compete.

    The gap a lead sees is `followup_gap_days` to within a few minutes, not to
    the second: a pass that spans days can only be nudged at its start, and past
    the first day the placement is pinned to the window. Buying exactness would
    mean teaching `next_send_times` a per-message earliest instant, which is not
    worth it for a drift nobody can perceive on a four-day gap.

    All of that is as true of a WhatsApp chaser, which is why this pass is not
    channel-aware beyond which copy it renders and which channel it stamps: the
    settings it reads have already been translated, so the tighter gap and the
    single chaser arrive as `followup_gap_days` and `followup_max_steps`.
    """
    copy = copy if copy is not None else _EmailCopy()
    channel = _channel(channel)
    max_steps = max(0, _int(settings.get("followup_max_steps"), 2))
    gap = max(1, _int(settings.get("followup_gap_days"), 4)) * _DAY_SEC
    hourly = _hourly_cap(settings)
    low, _high = _gap_bounds(settings)
    zone = _zone(settings)
    by_email = {_text(a.get("email")).strip().lower(): a
                for a in schedule.get("accounts") or []}

    # Carried across every pass: how much of each local day an account has
    # already spent, and every instant it has been given. Without them a later
    # step re-fills days an earlier one has spent, and the two passes each get a
    # clean hourly window across the seam between them.
    used: dict = {(email, schedule.get("start_day")): _int(count)
                  for email, count in (schedule.get("sent_today") or {}).items() if count}
    history: dict = {}

    groups: dict = {}
    for lead_id, ctx, email, when in placed:
        groups.setdefault(email, []).append((lead_id, ctx, when))
        _spend(used, history, zone, email, when)

    for step in range(1, max_steps + 1):
        if not copy.for_step(step):
            continue
        for email, items in groups.items():
            account = by_email.get(email)
            if account is None:
                continue
            due = [first + step * gap for _lead_id, _ctx, first in items]
            before = history.setdefault(email, [])
            slots = []
            start = max(due[0], (before[-1] if before else 0.0) + low)
            if before and hourly < _NO_CAP:
                # Each pass counts its own rolling hour from nothing, so the
                # only way the seam between two of them cannot exceed the cap is
                # for their hours not to overlap. The passes are days apart; the
                # hour costs nothing.
                start = max(start, before[-1] + _HOUR_SEC)
            for _ in range(_PLACEMENT_TRIES):
                slots = next_send_times(
                    count=len(items), accounts=[account], settings=settings,
                    start_ts=start,
                    sent_today_by_account={email: used.get((email, _local_date(start, zone)), 0)},
                    seed=plan["campaign_id"] * 1000 + step,
                    ramp_start=schedule.get("ramp_start"),
                )
                # `next_send_times` has no notion of a per-message earliest
                # instant, and its spacing is random, so a pass can run a few
                # minutes ahead of the touch a message is chasing. Nudge by the
                # overrun and draw again; one retry normally settles it.
                overrun = max((need - when for need, (when, _e) in zip(due, slots)),
                              default=0.0)
                if overrun <= 0:
                    break
                start += overrun
            for (lead_id, ctx, _first), (when, _email) in zip(items, slots):
                if not _queue_followup(conn, plan, ctx, lead_id, step, email, when,
                                       profile, settings, channel, copy):
                    # The first touch is already queued, so this is one chaser
                    # missing from an otherwise healthy campaign — invisible
                    # until the day it does not arrive.
                    _skip(plan, "follow-up %d could not be queued" % step)
                    continue
                plan["followups"] += 1
                _note_send(plan, zone, when)
                _spend(used, history, zone, email, when)


def _spend(used: dict, history: dict, zone, email: str, when: float) -> None:
    """Record that `email` has had a message placed at `when`."""
    key = (email, _local_date(when, zone))
    used[key] = used.get(key, 0) + 1
    history.setdefault(email, []).append(when)


def _queue_followup(conn, plan: dict, ctx: dict, lead_id: int, step: int,
                    account_email: str, when: float, profile: dict,
                    settings: dict, channel: str = EMAIL, copy=None) -> bool:
    copy = copy if copy is not None else _EmailCopy()
    options = copy.for_step(step)
    if not options:
        return False
    subject, text, html = copy.render(options[0], ctx)
    if not copy.usable(subject, text):
        return False
    if _channel(channel) == EMAIL:
        text, html = _for_account(text, html, profile, settings, account_email)
    return bool(_db.queue_message(conn, {
        "campaign_id": plan["campaign_id"], "lead_id": lead_id, "step": step,
        "subject": subject, "body_text": text, "body_html": html,
        "account_email": account_email, "channel": _channel(channel),
        "scheduled_at": when,
    }))


def _sent_today(conn, accounts: list, settings: dict, now: float,
                channel: str = EMAIL) -> dict[str, int]:
    """Per-account counts for the day, on one channel's ledger only.

    Per channel because the allowances are: WhatsApp's thirty is not thirty of
    email's forty, and a pooled count would let a morning of email leave the
    number looking spent when its real exposure was nil.
    """
    zone = settings.get("send_timezone")
    return {_text(a["email"]).strip().lower():
            _db.sent_today(conn, a["email"], zone, now_ts=now,
                           channel=_channel(channel)) for a in accounts}


def campaign_start_day(conn, campaign_id: int, settings: dict, now: float = 0.0) -> date:
    """The day a campaign began, as the warm-up origin for undated accounts.

    Anchoring the ramp to the campaign rather than to "today" is what keeps the
    planner and the send loop agreeing about an account whose `warmup_started`
    was never filled in. Re-deriving it from the current date on every replan
    would reset such an account to its first-day rate over and over, and the
    campaign would never finish.
    """
    now = now or time.time()
    created = _float(_db.get_campaign(conn, campaign_id).get("created_at")) or now
    return _local_date(created, _zone(settings))


# ── The send worker ──────────────────────────────────────────────────────────

def release_now(conn, campaign_id: int, limit: int = 0) -> int:
    """Bring queued messages forward so they are due immediately.

    Only the ones whose turn it actually is. A follow-up chases a message its
    lead has not received yet, so releasing it alongside the touch it chases is
    not "send now" — it is the whole sequence in one second. Measured on five
    leads with two follow-up steps configured: `release_now` moved all fifteen
    rows and every one of them left inside the same second, so each stranger's
    first three emails were a cold pitch, "bumping my last email" and a closing
    note, in that order, with no gap between them at all.

    So a step waits while any earlier step for that lead is still owed. A
    second press then releases the next step, which is the user asking to chase
    now having watched the first touch go — a decision, rather than an
    accident of ordering by `scheduled_at`.

    Separate from the worker because "go now" is a decision about the plan, not
    about the loop: the rows move, the loop then finds them overdue like any
    other backlog, and a stop half way through leaves the rest exactly where it
    put them rather than in some third state.
    """
    if limit <= 0:
        return 0
    rows = _db.rows(
        conn,
        "SELECT id FROM messages AS m WHERE m.campaign_id = ? AND m.status = 'queued' "
        "AND NOT EXISTS (SELECT 1 FROM messages AS owed "
        "                WHERE owed.campaign_id = m.campaign_id "
        "                AND owed.lead_id = m.lead_id AND owed.step < m.step "
        "                AND owed.status IN ('queued', 'sending', 'rehearsed')) "
        "ORDER BY m.scheduled_at, m.id LIMIT ?",
        (_int(campaign_id), int(limit)),
    )
    now = time.time()
    for row in rows:
        _db.mark_message(conn, _int(row["id"]), "queued", scheduled_at=now)
    return len(rows)


def campaign_channel(conn, campaign_id: int, default: str = EMAIL) -> str:
    """Which channel a campaign's queue belongs to, read off its own rows.

    A campaign is single-channel, so one row answers for all of them. Read from
    `messages` rather than stored on the campaign because that is where the
    channel actually is: the rows are what gets sent, and a campaign column
    could disagree with them after an edit. A campaign with nothing queued has
    no channel yet and comes back as `default`.
    """
    rows = _db.rows(conn, "SELECT COALESCE(channel, 'email') AS channel FROM messages "
                          "WHERE campaign_id = ? LIMIT 1", (_int(campaign_id),))
    return _channel(rows[0].get("channel"), default) if rows else _channel(default)


class OutreachWorker(QThread):
    """Sends one campaign's queue, re-deciding everything at send time.

    The plan in the database is a proposal. By the time a row comes due the
    user may have paused for a day, changed the window, disabled an account or
    unsubscribed the lead, so every send re-checks the window, the caps and the
    suppression list against the settings as they are now.

    It also reads the inbox back. The footer asks people to reply "unsubscribe"
    and the `List-Unsubscribe` header points at a mailbox, so unless something
    polls that mailbox the promise is a lie: the person who opted out keeps
    getting the +4 day and +8 day chasers, and a dead address gets re-queued by
    the next campaign. Every hit lands in `suppression`, which cancels the whole
    rest of that lead's thread.

    `channel` picks the transport and, through `channel_settings`, every number
    the loop below re-checks. Nothing else about the loop knows which channel it
    is running: the window, the caps, the pacing, the benching and the replan
    are one implementation because there is one right answer to each of them.

    A WhatsApp run may be handed the `session` the Settings screen already has
    logged in, and then it does not own it and will not close it — the user's
    connection card must still be live when the run finishes. Given none, it
    opens its own on the first live send and closes it at the end, and a dry run
    never asks for one at all.
    """

    log_signal = pyqtSignal(str, str)          # message, level (info|active|done|error)
    progress_signal = pyqtSignal(int, int)     # done, total
    message_sent_signal = pyqtSignal(dict)     # the message row
    stats_signal = pyqtSignal(dict)            # campaign_stats()
    done_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    # Consecutive connection failures before the run gives up. Enough to ride
    # out a laptop lid or a flaky hotel network, short of hammering Gmail.
    MAX_CONN_FAILURES = 5

    # How often the sending accounts' inboxes are read. Each poll is three IMAP
    # round trips per account, so it is deliberately nowhere near the loop rate;
    # five minutes is far inside the four days before the first chaser is due.
    IMAP_POLL_SEC = 300.0
    # The first poll of a run reaches back far enough to cover the app having
    # been closed for a fortnight. After that the loop has been watching.
    IMAP_FIRST_DAYS = 14
    IMAP_DAYS = 2

    # How often the WhatsApp chat list is read for replies. Far more often than
    # the mailbox, because it is a DOM read on a browser that is already open
    # rather than three logins, and because a WhatsApp opt-out has to stop the
    # next message rather than the next hour's.
    WA_POLL_SEC = 60.0

    # What `RATE:` costs, doubling per consecutive hit up to the ceiling. The
    # platform has just said "too fast" in its own words, and the one thing that
    # must not happen next is another message: this is the only place in the app
    # where a failure buys time rather than a retry.
    RATE_BACKOFF_SEC = 900.0
    RATE_BACKOFF_MAX = 3600.0

    def __init__(self, campaign_id: int, settings: dict, dry_run: bool = False,
                 ignore_schedule: bool = False, *, channel: str = EMAIL,
                 session=None):
        super().__init__()
        self.campaign_id = _int(campaign_id)
        self.channel = _channel(channel)
        # Send now: the clock stops holding the queue, the caps do not. The
        # window and the random gap exist to keep a mailbox looking human, and
        # a user who has decided to go now can waive that for themselves. The
        # daily and hourly caps protect the account from Google rather than the
        # recipient from us, so they still apply and nothing here can lift them.
        self.ignore_schedule = bool(ignore_schedule)
        # Translated once, here, and never again: every cap, hour, gap and
        # warm-up number the loop below reads arrives under the name it already
        # used, so there is no second send loop and no channel test in one.
        self._settings = channel_settings(
            settings if isinstance(settings, dict) else {}, self.channel)
        # Or-ed with the setting, never replaced by the argument: a caller that
        # forgets to pass the flag must not turn a rehearsal into a live send.
        # On WhatsApp the setting behind this is `wa_dry_run`, which ships True.
        self.dry_run = bool(dry_run) or bool(self._settings.get("dry_run", True))
        self._running = True
        self._paused = False
        self._senders: dict[str, _mailer.SmtpSender] = {}
        # Given one, this run borrows it; opening its own makes it responsible
        # for closing it. Closing the Settings screen's live session at the end
        # of a run would take the connection card down with it.
        self._wa_session = session
        self._owns_session = session is None
        self._stopped: dict[str, date] = {}    # account -> the day it hit AUTH/QUOTA
        self._next_ok: dict[str, float] = {}   # per-account gap between sends
        self._rehearsed: dict[str, list] = {}  # dry-run sends, for the cap checks
        self._conn_failures = 0
        self._rate_hits = 0                    # consecutive RATE: refusals
        self._holding = ""                     # the hold reason already announced
        self._replanned_at = 0.0
        self._polled_at = 0.0                  # last inbox or chat-list read
        self._seen_replies: set = set()        # (wa_id, text) already acted on
        self._ramp_start: date | None = None   # resolved once the DB is open

    # ── control ──

    def stop(self) -> None:
        self._running = False
        self._paused = False

    def abort(self) -> None:
        """Stop and force the transport closed (used when the app is closing).

        A thread parked inside `smtplib` cannot read the stop flag; closing the
        session underneath it makes that call fail fast so `run()` can unwind,
        the same trick `ScrapeWorker.abort()` uses on the browser. A thread
        parked inside Selenium waiting on WhatsApp Web is the same problem with
        the same answer, and there it is literally the same trick.

        This is the one place a borrowed session is closed as well. Everywhere
        else it is left alone because the Settings screen is still showing it;
        here the application is going away and there is no connection card left
        to keep alive — and a thread blocked on a browser nobody is going to
        close is what stops the process from exiting at all.
        """
        self.stop()
        self._owns_session = True
        self._close_senders()

    def pause(self) -> None:
        self._paused = True
        self.log_signal.emit("Paused", "info")

    def resume(self) -> None:
        self._paused = False
        self.log_signal.emit("Resumed", "active")

    def _nap(self, seconds: float) -> None:
        """Wait in 0.25 s slices so Stop lands within a quarter of a second.

        One long sleep until the next scheduled send is the whole bug: the
        button stops responding for minutes and the app looks hung.
        """
        deadline = time.monotonic() + max(0.0, _float(seconds))
        while self._running and time.monotonic() < deadline:
            time.sleep(_SLICE)

    # ── run ──

    def run(self) -> None:
        try:
            self._loop()
        except Exception as exc:
            self.error_signal.emit(str(exc))
        finally:
            # Every way out of a rehearsal comes through here — finished,
            # stopped, or thrown out of — and every one of them owes the
            # campaign its queue back.
            self._restore_rehearsal(None)
            self._close_senders()
            self.done_signal.emit()

    def _restore_rehearsal(self, conn) -> int:
        """Hand back everything a dry run marked. Returns how many rows moved.

        Nothing rehearsed was ever sent, so the messages are still owed to their
        leads. Leaving them consumed is what made the safety feature dangerous:
        the next real run found every first touch already spent and opened with
        "Bumping my last email" to somebody who had heard nothing.
        """
        return _db.requeue_rehearsed(conn, self.campaign_id)

    def _recover_rehearsed(self, conn) -> None:
        """Put back what a dry run that never finished left behind.

        A rehearsal killed by the app closing cannot run its own restore, so
        every run does it on the way in as well — including a live run, which
        is the one that would otherwise send the follow-ups on their own.
        """
        restored = self._restore_rehearsal(conn)
        if restored:
            self.log_signal.emit(
                "%d message%s left over from a dry run that did not finish are back "
                "on the queue — none of them was sent."
                % (restored, "" if restored == 1 else "s"), "info")

    def _recover_claimed(self, conn) -> None:
        """Resolve messages claimed by a previous run that never came back.

        A `sending` row means the server was handed the message and this
        process died before it could record the outcome. Nothing can tell us
        which side of the hand-off it fell on, so the choice is which way to be
        wrong: re-sending puts a second copy of a cold email in a stranger's
        inbox, while leaving it counts as sent and the lead simply hears once.
        The second is recoverable and the first is not, so a claimed message is
        closed out rather than re-queued, and says so in its error column.
        """
        rows = _db.claimed_messages(conn, self.campaign_id)
        for row in rows:
            _db.mark_message(conn, _int(row.get("id")), "sent",
                             sent_at=_float(row.get("scheduled_at")) or time.time(),
                             error="interrupted — outcome unknown, not retried")
        if rows:
            self.log_signal.emit(
                "%d message%s were interrupted by a previous run and will not be "
                "retried — %s if you need to be sure."
                % (len(rows), "" if len(rows) == 1 else "s",
                   "check the chat on your phone" if self.channel == WHATSAPP
                   else "check the account's Sent folder"), "info")

    def _refuse(self, conn, message: str) -> None:
        """Stop before the first send, saying why. The queue is left as it is."""
        self.log_signal.emit(message, "error")
        _db.set_campaign_status(conn, self.campaign_id, "stopped")
        self._emit_progress(conn)

    def _wrong_channel(self, conn) -> bool:
        """Is this worker about to send one channel's copy down the other's wire.

        A campaign is single-channel and the rows say which, so the two can only
        disagree through a caller's mistake — and the failure it would produce is
        the worst one available: WhatsApp copy, written for a chat bubble with
        an opt-out line and no subject, handed to Gmail as a cold email.
        """
        queued = campaign_channel(conn, self.campaign_id, self.channel)
        if queued == self.channel:
            return False
        self._refuse(conn, "This campaign's messages were written for %s and this "
                           "run is set up to send %s — nothing has been sent. "
                           "Start the campaign from its own channel."
                     % (queued, self.channel))
        return True

    def _ban_held(self, conn) -> bool:
        """Is a WhatsApp restriction still standing over this run.

        Checked before anything else happens, on every run and not only on the
        one that hit it. A restriction that stopped Tuesday's run and let
        Wednesday's straight back out would be no protection at all: the second
        run is exactly how a temporary block becomes a permanent one.
        """
        if self.channel != WHATSAPP:
            return False
        notice = wa_ban_notice(conn)
        if not notice:
            return False
        self._refuse(conn,
                     "WhatsApp has restricted this number and nothing will be sent "
                     "until you acknowledge it in Settings. %s" % notice)
        return True

    def _loop(self) -> None:
        conn = _db.connect()
        if self._wrong_channel(conn) or self._ban_held(conn):
            return
        self._ramp_start = campaign_start_day(conn, self.campaign_id, self._settings)
        self._recover_rehearsed(conn)
        self._recover_claimed(conn)
        _db.set_campaign_status(conn, self.campaign_id, "running")
        if self.dry_run:
            self.log_signal.emit(
                "DRY RUN — every message is built and logged, nothing leaves this "
                "machine, and the queue is handed back untouched at the end", "info")
        self._warn_unread_optouts()
        self._emit_progress(conn)

        while self._running:
            if self._paused:
                self._nap(0.5)
                continue

            self._poll_replies(conn, time.time())

            if _int(_db.campaign_stats(conn, self.campaign_id).get("queued")) <= 0:
                self._finish(conn)
                return

            now = time.time()
            due = self._due(conn, now)
            if not due:
                if not self._announce_wait(conn, now):
                    break
                self._nap(min(30.0, max(1.0, self._seconds_until_next(conn, now))))
                continue
            self._dispatch(conn, due, now)

        # Stopped half way through a rehearsal is still a rehearsal: the part
        # of the queue it walked goes back before the campaign is left alone.
        self._restore_rehearsal(conn)
        _db.set_campaign_status(conn, self.campaign_id, "stopped")
        self.log_signal.emit("Stopped", "done")
        self._emit_progress(conn)

    def _finish(self, conn) -> None:
        """Close out a run that has emptied the queue.

        A live run leaves the campaign 'done'. A rehearsal must not: the user
        pressed Start on a dry run to see the whole thing happen and then send
        it for real, so the queue goes back exactly as it was and the campaign
        returns to 'scheduled', ready for that second press.
        """
        restored = self._restore_rehearsal(conn)
        self.stats_signal.emit(_db.campaign_stats(conn, self.campaign_id))
        if restored:
            _db.set_campaign_status(conn, self.campaign_id, "scheduled")
            self.log_signal.emit(
                "Dry run finished — %d message%s rehearsed and none sent. The queue is "
                "back as it was; turn dry run off in Settings to send it for real."
                % (restored, "" if restored == 1 else "s"), "done")
            return
        self.log_signal.emit("Campaign finished — nothing left in the queue", "done")
        _db.set_campaign_status(conn, self.campaign_id, "done")

    def _due(self, conn, now: float) -> list[dict]:
        """This campaign's overdue queue, narrowed by SQLite and not by us.

        The filter belongs in the query because `limit` is applied before any
        row reaches Python. Filtering afterwards meant a second campaign
        prepared behind a stale one saw a window of two hundred rows that were
        all somebody else's, read its own queue as empty, and sat there.
        """
        return _db.due_messages(conn, now, limit=200, campaign_id=self.campaign_id)

    def _next_due_at(self, conn, now: float) -> float:
        """When this campaign's next sendable message is due. 0.0 when none is.

        Zero is a real answer and not a missing one: every queued row left is
        either addressed to an address that has since been suppressed, or
        scheduled past the year the planner works in. Both mean the loop will
        wait for ever, so the caller has something to say rather than a nap to
        repeat.
        """
        horizon = now + _MAX_PLAN_DAYS * _DAY_SEC
        rows = _db.due_messages(conn, horizon, limit=1, campaign_id=self.campaign_id)
        return _float(rows[0].get("scheduled_at")) if rows else 0.0

    def _seconds_until_next(self, conn, now: float) -> float:
        """How long until this campaign's next message is due. 30 s if unknown."""
        due = self._next_due_at(conn, now)
        return max(1.0, due - now) if due else 30.0

    def _hold(self, message: str, level: str = "info") -> None:
        """Say why the queue is parked — once per reason, not once per nap.

        Deduped on the text because the loop comes back around every thirty
        seconds and a hold lasts hours; repeated verbatim it would bury the
        sends either side of it. `_send` clears it, so the *next* hold is
        announced again.
        """
        message = _text(message)
        if message and message != self._holding:
            self._holding = message
            self.log_signal.emit(message, level)

    def _announce_wait(self, conn, now: float) -> bool:
        """Name the thing holding a queue that has nothing due. False to give up.

        This is the commonest "glitch" there is and it was entirely silent. A
        campaign planned on a Saturday for Monday morning, started straight
        away: the button greys out, the counter sits at zero, and twenty passes
        of this loop wrote exactly one line to the log — "Stopped", when the
        user gave up. Nothing is wrong in that run, and nothing said so.

        The false answer covers the one wait that never ends. `campaign_stats`
        counts a row by its status and `due_messages` refuses one whose lead
        has since been suppressed, so a crash part way through `suppress` — the
        address written, the rows not yet cancelled — leaves a campaign that
        reads as having a queue and can never send from it. That ran here as a
        thirty-second nap repeated for ever, with nothing in the log.
        """
        waiting = _int(_db.campaign_stats(conn, self.campaign_id).get("queued"))
        due = self._next_due_at(conn, now)
        if not due:
            self.log_signal.emit(
                "Stopping: %d message(s) are queued and none of them can ever go "
                "out — each is either addressed to a suppressed address or "
                "scheduled beyond the next year. Prepare a fresh campaign from "
                "the leads you can still contact." % waiting, "error")
            return False
        if not self.ignore_schedule and not in_send_window(now, self._settings):
            self._hold("Outside the sending window — %d message(s) waiting. The "
                       "window reopens %s, and the first of them goes %s."
                       % (waiting, _clock(next_window_open(now, self._settings)),
                          _clock(due)))
            return True
        if self._holding:
            # A cap or a replan has already said something more specific, and
            # nothing has left since. Repeating it as "waiting" adds no fact.
            return True
        self._hold("Waiting — %d message(s) queued and none due yet. The next one "
                   "goes %s." % (waiting, _clock(due)))
        return True

    # ── one message ──

    def _dispatch(self, conn, due: list, now: float) -> None:
        """Send the first overdue message that has an account free to take it.

        It walks the head of the queue rather than only looking at `due[0]`
        because a follow-up is pinned to the account its thread belongs to. One
        chaser whose account is inside its pacing gap used to hold the whole
        run behind it, and every unbound message behind that chaser could have
        gone from the other account at once.
        """
        if not self.ignore_schedule and not in_send_window(now, self._settings):
            self._replan(conn, due, now, "Outside the sending window")
            return

        waits = []
        for row in due[:_DISPATCH_SCAN]:
            account, wait = self._pick_account(conn, row, now)
            if account is not None:
                self._send(conn, row, account, now)
                return
            if wait > 0:
                waits.append(wait)
        if waits:
            self._nap(min(min(waits), 30.0))     # only the pacing gap is holding us
            return

        hourly = self._hourly_hold(conn, now)
        if hourly > 0:
            # An hourly cap flattens a burst and clears inside the hour, so the
            # queue does not want re-spacing over it. Replanning here sent the
            # backlog to the next *window* open instead, which under Send now
            # meant a run the user had just told to go immediately went quiet
            # until Monday morning — measured: 30 of 40 messages held 35 hours
            # by a cap that would have cleared in one — under a line that said
            # the accounts were at their cap for the day when 35 of the 40 a
            # day were still unspent.
            self._hold("Holding for the hourly limit of %d per %s — %d message(s) "
                       "waiting, and the next can go %s."
                       % (_hourly_cap(self._settings), self._sender_noun(),
                          len(due), _clock(now + hourly)))
            self._nap(min(hourly, 30.0))
            return
        self._replan(conn, due, now,
                     "This number has sent its allowance for today"
                     if self.channel == WHATSAPP else
                     "Every account has sent its allowance for today")

    def _sender_noun(self) -> str:
        """What the thing sending is called, for a line the user reads."""
        return "number" if self.channel == WHATSAPP else "account"

    def _live_accounts(self, now: float = 0.0) -> list[dict]:
        """Enabled accounts that are not out of action for today.

        A quota or auth failure retires an account for the calendar day it
        happened on, not for the run: a campaign that spans a fortnight would
        otherwise finish with every account permanently benched over one bad
        afternoon.

        On WhatsApp there is one, so benching it does end the run — which is the
        right answer there and needs no special case: a number that has been
        logged out cannot send, and the caller below already says so.
        """
        today = _local_date(now or time.time(), _zone(self._settings))
        return [a for a in channel_accounts(self._settings, self.channel)
                if _text(a.get("email")).strip()
                and self._stopped.get(_text(a.get("email")).strip().lower()) != today]

    def _thread_account(self, conn, row: dict) -> str:
        """The address a chaser has to leave from, or "" when it may move.

        A follow-up carries `In-Reply-To` for the first touch, so its account is
        not a preference. Before this was enforced, `_pick_account` handed a
        chaser to whichever account was out of its pacing gap first: 186 of 400
        follow-ups in a 200-lead campaign went out from the other address,
        threaded into a conversation that address had never been part of, which
        the reader sees as a second stranger answering their mail.

        WhatsApp has no threading to protect and one number to send from, so
        there is nothing here to bind and no query worth running for it.
        """
        if self.channel == WHATSAPP or _int(row.get("step")) <= 0:
            return ""
        parent = _db.first_touch_sent(conn, _int(row.get("campaign_id")),
                                      _int(row.get("lead_id")))
        return _text(parent.get("account_email")).strip().lower()

    def _waives_pacing(self) -> bool:
        """May Send now drop the gap between two messages on this channel.

        On email, yes, and it is documented in `__init__`: the gap is there to
        keep a mailbox looking human and a user who has decided to go now can
        waive that for themselves.

        On WhatsApp, no. The spec's rule is that Send now may waive the *clock*
        and may not waive the number's own limits, and the gap is one of them
        rather than part of the clock: eight messages inside a minute is the
        shape the platform reads as a bot, and it is reachable under Send now
        even with the hourly cap holding — the cap bounds the count and says
        nothing about how tightly they are packed. The window is still waived,
        so Send now means what it says; what it cannot do is put the number in
        front of WhatsApp at machine speed.
        """
        return self.ignore_schedule and self.channel != WHATSAPP

    def _pick_account(self, conn, row: dict, now: float) -> tuple[dict | None, float]:
        """(account to send from, seconds to wait). Both empty means all capped."""
        accounts = self._live_accounts(now)
        # A benched or deleted account cannot hold its own thread hostage: the
        # chaser falls back to whatever is standing, and `_thread_parent` then
        # drops the headers so it goes as its own message rather than as a
        # forged reply. Holding it instead would stall the campaign until
        # midnight for a message that has somewhere perfectly good to go.
        bound = self._thread_account(conn, row)
        if bound and any(_text(a.get("email")).strip().lower() == bound for a in accounts):
            accounts = [a for a in accounts
                        if _text(a.get("email")).strip().lower() == bound]
        preferred = _text(row.get("account_email")).strip().lower()
        accounts.sort(key=lambda a: _text(a.get("email")).strip().lower() != preferred)

        waits = []
        for account in accounts:
            email = _text(account["email"]).strip().lower()
            if not self._has_headroom(conn, account, now):
                continue
            ready = 0.0 if self._waives_pacing() else self._next_ok.get(email, 0.0)
            if ready > now:
                waits.append(ready - now)
                continue
            return account, 0.0
        return None, min(waits) if waits else 0.0

    def _daily_room(self, conn, account: dict, now: float) -> int:
        """How many more this account may send today. Zero means tomorrow.

        Split out from the hourly check below because the two holds mean
        different things and want different handling: a spent day is over until
        midnight and its backlog wants re-spacing into tomorrow's window, while
        an hourly cap clears inside the hour and the queue should stay where it
        is.
        """
        zone = _zone(self._settings)
        email = _text(account["email"]).strip().lower()
        midnight = _at(_local_date(now, zone), 0, zone)

        cap = account_daily_cap(account, self._settings, on_day=_local_date(now, zone),
                                ramp_start=self._ramp_start)
        today = _db.sent_today(conn, email, self._settings.get("send_timezone"),
                               now_ts=now, channel=self.channel)
        # Rehearsed sends count towards the cap here but are never written to
        # `sends`: pacing a dry run realistically is worth having, spending a
        # live account's real daily quota on messages nobody received is not.
        today += sum(1 for ts in (self._rehearsed.get(email) or []) if ts >= midnight)
        return max(0, cap - today)

    def _hour_stamps(self, conn, account_email: str, now: float) -> list[float]:
        """Every send this account has made in the trailing hour, ascending.

        On this channel's ledger only: an hour of email must not read as an hour
        the WhatsApp number has spent, or the other way about.
        """
        stamps = _db.recent_sends(conn, account_email, since_ts=now - _HOUR_SEC,
                                  channel=self.channel)
        stamps += [ts for ts in (self._rehearsed.get(account_email) or [])
                   if ts > now - _HOUR_SEC]
        stamps.sort()
        return stamps

    def _has_headroom(self, conn, account: dict, now: float) -> bool:
        if self._daily_room(conn, account, now) <= 0:
            return False
        hourly = _hourly_cap(self._settings)
        if hourly >= _NO_CAP:
            return True
        email = _text(account["email"]).strip().lower()
        return len(self._hour_stamps(conn, email, now)) < hourly

    def _hourly_hold(self, conn, now: float) -> float:
        """Seconds until an hourly window frees, or 0 when the day is what holds.

        Zero is also the answer when something is free, so a caller can read a
        positive number as "the hourly cap, and only the hourly cap, is holding
        this queue" and park for exactly that long.
        """
        cap = _hourly_cap(self._settings)
        if cap >= _NO_CAP:
            return 0.0
        waits = []
        for account in self._live_accounts(now):
            if self._daily_room(conn, account, now) <= 0:
                continue
            stamps = self._hour_stamps(conn, _text(account["email"]).strip().lower(), now)
            if len(stamps) < cap:
                return 0.0
            # The cap-th most recent send drops out of the trailing hour then,
            # which is the instant this account may send again.
            waits.append(max(1.0, stamps[-cap] + _HOUR_SEC - now))
        return min(waits) if waits else 0.0

    def _thread_parent(self, conn, row: dict, account_email: str = "") -> str:
        """The first touch's Message-ID, for a chaser to reply into.

        Returns "" for a first touch, and for a chaser whose parent has not
        been sent — rehearsed included — in which case the chaser stands on its
        own rather than pointing at a conversation that does not exist.

        Also "" when this message is leaving from a different address than the
        first touch did. `_pick_account` keeps a chaser on its thread's account
        while that account is standing, so this is the benched-account case; a
        fresh message from the new address is honest where a reply from it
        would not be.
        """
        if _int(row.get("step")) <= 0:
            return ""
        parent = _db.first_touch_sent(conn, _int(row.get("campaign_id")),
                                      _int(row.get("lead_id")))
        from_account = _text(parent.get("account_email")).strip().lower()
        if account_email and from_account and from_account != account_email.strip().lower():
            return ""
        return _text(parent.get("message_id"))

    def _reusable_header_id(self, row: dict, account_email: str) -> str:
        """The `Message-ID` this row already carries, when it may be reused.

        A retry from the same account keeps its id: the id was claimed before
        the hand-off precisely so a message the process died in the middle of
        can be rebuilt as the same message rather than as a second one.

        A message handed to a *different* account may not. The id names the
        sender's own domain — that is the only domain we can honestly claim —
        so one minted for another address reads as forged.
        """
        stored = _text(row.get("message_id")).strip()
        if not stored:
            return ""
        same = _text(row.get("account_email")).strip().lower() == account_email.strip().lower()
        return stored if same else ""

    def _send(self, conn, row: dict, account: dict, now: float) -> None:
        """Hand one message to this channel's transport.

        Two builders, one outcome. What differs between the channels is what is
        built and what is claimed before the hand-off; what happens afterwards —
        the pacing slot, the cap, the status, the log, the failure — is one
        implementation, because every one of those rules was earned by the email
        path and none of them is about email.
        """
        if self.channel == WHATSAPP:
            self._send_whatsapp(conn, row, account, now)
        else:
            self._send_email(conn, row, account, now)

    def _send_email(self, conn, row: dict, account: dict, now: float) -> None:
        message_id = _int(row.get("id"))
        lead = _db.get_lead(conn, _int(row.get("lead_id")))
        to_email = _text(lead.get("email")).strip()
        if not to_email:
            _db.mark_message(conn, message_id, "skipped", error="no recipient address")
            return

        profile = self._settings.get("sender_profile") or {}
        account_email = _text(account["email"]).strip()
        message, header_id = _mailer.build_message(
            to_email=to_email,
            to_name=_text(lead.get("name")),
            from_email=account_email,
            from_name=_text(account.get("display_name")) or _text(profile.get("sender_name")),
            reply_to=_text(profile.get("reply_to")),
            subject=_text(row.get("subject")),
            body_text=_text(row.get("body_text")),
            body_html=_text(row.get("body_html")),
            unsubscribe_mailto=_text(self._settings.get("unsubscribe_mailto")),
            message_id=self._reusable_header_id(row, account_email),
            in_reply_to=self._thread_parent(conn, row, account_email),
        )

        if self.dry_run:
            ok, error = True, ""
        else:
            # Claim it before the hand-off, and claim the whole of it: the
            # status, the `Message-ID` it goes out under, and the bytes
            # themselves. A crash between the server taking the message and
            # this process writing the result would otherwise leave the row
            # `queued`, and the restart would send it a second time; `sending`
            # is recoverable evidence that it may already be gone, which is the
            # safer side to be wrong on.
            #
            # The header id has to be written here rather than after the send
            # for the same reason the status is. It was minted in memory a line
            # ago, and a crash lost it — so the recovered row read as sent with
            # no id at all, its lead's chaser threaded onto nothing, and a reply
            # to it was never matched, which meant the sequence kept chasing
            # somebody who had already answered.
            _db.mark_message(conn, message_id, "sending",
                             account_email=account_email, message_id=header_id)
            _db.record_transcript(conn, message_id, _mailer.wire_form(message))
            # The quota is charged here too, and for the same reason the status
            # is. Caps count transactions, and a transaction the process died
            # in the middle of is one Gmail has already seen — charged after
            # the fact, it was not counted at all, and the restart sent one
            # message per crash over the account's daily ceiling.
            _db.record_send(conn, account_email, now, channel=self.channel)
            ok, error = self._sender(account).send(message)

        self._after_attempt(conn, row, account_email, lead, to_email, ok, error,
                            header_id, now)

    def _send_whatsapp(self, conn, row: dict, account: dict, now: float) -> None:
        """One WhatsApp message. The email builder's counterpart, and no more.

        No MIME, no `Message-ID`, no `In-Reply-To`: a WhatsApp follow-up is
        another message in the same chat, which the platform threads by itself,
        and there is no header to forge or reuse.
        """
        message_id = _int(row.get("id"))
        lead = _db.get_lead(conn, _int(row.get("lead_id")))
        phone = _text(lead.get("phone")).strip()
        body = _text(row.get("body_text")).strip()
        account_email = _text(account.get("email")).strip() or WA_ACCOUNT
        if not body:
            # Through the failure path rather than straight to 'skipped', for
            # the reason an unsendable number goes that way: a rehearsal must
            # hand the queue back, and a row marked skipped never comes back.
            self._after_attempt(conn, row, account_email, lead, phone or "lead",
                                False, "OTHER: there is no message to send", "", now)
            return

        # Resolved here rather than left to the session, so that a dry run —
        # which has no session — refuses exactly the numbers a live run would,
        # and so the number is written the one way both ends agree on. Handing
        # the session `+<E.164>` rather than the scraped text means its own
        # `to_wa_id` cannot reach a different answer than this one did.
        wa_id = _wa.to_wa_id(phone, wa_region(self._settings))
        shown = "+%s" % wa_id if wa_id else (phone or "lead")

        if not wa_id:
            ok, error = False, self._unsendable(phone)
        elif self.dry_run:
            ok, error = True, ""
        else:
            session, error = self._session()
            if session is None:
                ok = False
            else:
                # Claimed before the hand-off for the reason the email path is:
                # the browser may have delivered the message a moment before
                # this process died, and a row left `queued` would send a
                # stranger the same cold pitch twice. The transcript is what was
                # handed over, which on this channel is the number and the text.
                _db.mark_message(conn, message_id, "sending",
                                 account_email=account_email)
                _db.record_transcript(conn, message_id,
                                      "To: %s\n\n%s" % (shown, body))
                # Charged before the hand-off, again for the email path's
                # reason: a transaction WhatsApp has seen counts against the
                # number whether or not this process lived to write it down.
                _db.record_send(conn, account_email, now, channel=self.channel)
                ok, error = session.send(shown, body)

        self._after_attempt(conn, row, account_email, lead, shown, ok, error, "", now)

    def _unsendable(self, phone: str) -> str:
        """Why this number cannot be addressed, in words the user can act on.

        Written here rather than taken from the session so that the dry run and
        the live run give the same answer for the same lead — a rehearsal whose
        whole purpose is to show what will happen must not be quieter than the
        thing it is rehearsing.
        """
        if not _wa.digits_of(phone):
            return "RECIPIENT: this lead has no usable phone number."
        if not _wa.is_plausible(phone):
            return "RECIPIENT: %s is too short to be a phone number." % phone
        if not wa_region(self._settings):
            return ("RECIPIENT: %s has no country code, and no default WhatsApp "
                    "region is set to complete it — set one in Settings, or store "
                    "the number with its + prefix." % phone)
        return ("RECIPIENT: %s could not be read as a number in %s."
                % (phone, wa_region(self._settings)))

    def _after_attempt(self, conn, row: dict, account_email: str, lead: dict,
                       shown: str, ok: bool, error: str, header_id: str,
                       now: float) -> None:
        """Everything that follows a hand-off, whichever transport made it."""
        message_id = _int(row.get("id"))
        key = account_email.lower()
        # The pacing slot belongs to the attempt, not to its outcome. A run of
        # dead addresses is ordinary in a scraped list, and a failure that
        # leaves the gap unspent puts the next transaction on the wire
        # immediately — twenty-five refusals a second is a far louder signal to
        # Gmail than twenty-five deliveries would have been. WhatsApp reads it
        # louder still, which is why the gap it draws from starts at 90 seconds.
        self._next_ok[key] = now + _RUNTIME_RNG.randint(*_gap_bounds(self._settings))
        # Caps count transactions, not deliveries. A provider sees a refusal as
        # plainly as an acceptance, and a stretch of dead addresses in a scraped
        # list is the ordinary case — counting only successes let one account
        # put sixty rejected transactions on the wire against a cap of ten,
        # which is the reputation damage the caps exist to prevent. A live send
        # charged its slot before the hand-off; a rehearsal has none to charge
        # and keeps its own tally instead, so a dry run paces like the real
        # thing without spending the account's real quota.
        if self.dry_run:
            self._rehearsed.setdefault(key, []).append(now)

        noted = ""
        if self.dry_run and not ok:
            # A rehearsal hands the queue back untouched, whatever it finds
            # wrong with a message. Marking this one failed — or suppressing its
            # lead, which is what a live `RECIPIENT:` does — would consume the
            # very row the dry run promised to leave alone, so the problem is
            # said out loud and the message is rehearsed with the rest.
            noted, ok, error = error, True, ""

        if ok:
            self._conn_failures = 0
            self._rate_hits = 0
            # The queue is moving, so whatever was holding it is over and the
            # next hold is news again rather than a repeat.
            self._holding = ""
            # A rehearsal writes its own status and no Message-ID. Both matter:
            # the status is what every "has this gone?" query reads, and a
            # header id on an unsent row would hand this lead's chaser an
            # `In-Reply-To` pointing at a message that does not exist. The lead
            # stays 'queued' for the same reason — nobody has heard from them.
            status = "rehearsed" if self.dry_run else "sent"
            header_id = "" if self.dry_run else header_id
            _db.mark_message(conn, message_id, status, sent_at=now, message_id=header_id,
                             account_email=account_email, error="")
            to_email = _text(lead.get("email")).strip()
            if not self.dry_run and to_email:
                _db.upsert_lead(conn, {"email": to_email, "status": "sent"})
            _db.log_event(conn, status, "%s%s" % (shown, self._via(account_email)),
                          _int(row.get("lead_id")))
            self.message_sent_signal.emit(dict(row, status=status, sent_at=now,
                                               message_id=header_id,
                                               account_email=account_email,
                                               error=noted))
            if noted:
                self.log_signal.emit("Would skip %s — %s" % (shown, noted), "info")
            else:
                self.log_signal.emit(
                    "%s to %s%s" % ("Would send" if self.dry_run else "Sent", shown,
                                    "" if self.dry_run else self._via(account_email)),
                    "active")
            self._emit_progress(conn)
            return

        self._handle_failure(conn, row, account_email, error, now)

    def _via(self, account_email: str) -> str:
        """" via <account>" for email; " on WhatsApp" for the one number."""
        if self.channel == WHATSAPP:
            return " on WhatsApp"
        return " via %s" % account_email

    def _handle_failure(self, conn, row: dict, account_email: str, error: str,
                        now: float) -> None:
        message_id = _int(row.get("id"))
        kind = error.split(":", 1)[0].strip().upper()

        if kind == "BANNED":
            # First, and unconditionally. A restriction notice can also mention
            # trying again later, and reading it as a rate limit is how a
            # temporary block becomes a permanent one — see
            # `core.whatsapp.classify`, which orders its own tests the same way.
            self._banned(conn, row, error, now)
            return

        if kind == "RATE":
            self._back_off(conn, row, account_email, error, now)
            return

        if kind in ("AUTH", "QUOTA"):
            # The account is finished for today; the message itself is fine and
            # goes back on the queue for whichever account is still standing.
            self._stopped[account_email.lower()] = _local_date(now, _zone(self._settings))
            _db.mark_message(conn, message_id, "queued", scheduled_at=now + 60.0)
            _db.log_event(conn, "account_stopped", "%s: %s" % (account_email, error))
            self.log_signal.emit("%s stopped for today — %s"
                                 % (self._sender_name(account_email), error), "error")
            if not self._live_accounts(now):
                self.log_signal.emit(
                    "WhatsApp is no longer logged in — stopping the run. Reconnect "
                    "the number in Settings, then start again."
                    if self.channel == WHATSAPP else
                    "No sending accounts left — stopping the run. Fix the account in "
                    "Settings, then start again.", "error")
                _db.set_campaign_status(conn, self.campaign_id, "stopped")
                self._running = False
            return

        if kind == "RECIPIENT":
            _db.mark_message(conn, message_id, "failed", error=error)
            lead = _db.get_lead(conn, _int(row.get("lead_id")))
            if self.channel == WHATSAPP:
                # "Not on WhatsApp" is a fact about the transport, not about the
                # person, so it must not go through the suppression door the
                # bounce handler uses. Suppression is shared across channels by
                # design — that is what makes an opt-out mean something — and
                # putting a number nobody answers on it would quietly cancel the
                # email sequence for a lead who reads their mail perfectly well.
                # What is owed on *this* channel goes, and nothing else does.
                cancelled = self._cancel_queued(conn, _int(row.get("lead_id")),
                                                "not reachable on WhatsApp",
                                                channel=WHATSAPP)
                self.log_signal.emit(
                    "Skipped %s — %s%s"
                    % (_text(lead.get("phone")).strip() or "lead", error,
                       "; %d queued message(s) cancelled" % cancelled if cancelled else ""),
                    "info")
                return
            if lead.get("email"):
                # A hard rejection is the address telling us it does not exist,
                # which is exactly what the IMAP bounce handler acts on — so it
                # goes through the same door. Suppressing rather than only
                # marking the lead is what cancels the two chasers already
                # scheduled behind this message, and what keeps the next
                # campaign from queueing a fresh first touch to a dead mailbox.
                _db.upsert_lead(conn, {"email": lead["email"], "status": "bounced"})
                self._suppress(conn, lead["email"], "hard bounce")
            self.log_signal.emit("Skipped %s — %s" % (lead.get("email") or "lead", error), "info")
            return

        if kind == "CONN":
            self._conn_failures += 1
            _db.mark_message(conn, message_id, "queued", scheduled_at=now + 120.0)
            self.log_signal.emit("Connection problem — retrying in two minutes (%s)" % error,
                                 "error")
            if self._conn_failures >= self.MAX_CONN_FAILURES:
                self.error_signal.emit("Gave up after %d connection failures: %s"
                                       % (self._conn_failures, error))
                self._running = False
            return

        _db.mark_message(conn, message_id, "failed", error=error)
        self.log_signal.emit("Failed — %s" % error, "error")
        self._emit_progress(conn)

    def _sender_name(self, account_email: str) -> str:
        """What to call the thing that just failed, in a line the user reads."""
        return "WhatsApp" if self.channel == WHATSAPP else account_email

    def _banned(self, conn, row: dict, error: str, now: float) -> None:
        """A restriction ends this run and holds every future one.

        The message goes back on the queue because it did not go: WhatsApp said
        no. Everything else here is about the next run rather than this one —
        `record_wa_ban` is what `_ban_held` reads on the way in, and until the
        user acknowledges it in Settings no campaign on this channel will send
        another message. That is the point. A number that keeps trying after a
        restriction is a number that stops being temporarily restricted.
        """
        _db.mark_message(conn, _int(row.get("id")), "queued", scheduled_at=now)
        record_wa_ban(conn, error)
        _db.set_campaign_status(conn, self.campaign_id, "stopped")
        self.log_signal.emit(
            "STOPPED — WhatsApp has restricted this number. %s Nothing further "
            "will be sent on WhatsApp, in this run or any other, until you open "
            "Settings and acknowledge it. Check the account on the phone first: "
            "sending again while a block is in place is what makes it permanent."
            % error, "error")
        self._running = False

    def _back_off(self, conn, row: dict, account_email: str, error: str,
                  now: float) -> None:
        """The platform said "too fast". The only useful reply is to wait.

        Doubling per consecutive refusal, because the first backoff being
        enough is a guess and being wrong about it twice is how throttling turns
        into a restriction. The message keeps its place and comes back when the
        wait is over; the counter resets the moment anything gets through.
        """
        self._rate_hits += 1
        wait = min(self.RATE_BACKOFF_MAX,
                   self.RATE_BACKOFF_SEC * (2 ** (self._rate_hits - 1)))
        # Through the pacing gate as well as the row, so nothing else in the
        # queue slips past this message and straight back into the throttle.
        self._next_ok[account_email.lower()] = max(
            self._next_ok.get(account_email.lower(), 0.0), now + wait)
        _db.mark_message(conn, _int(row.get("id")), "queued", scheduled_at=now + wait)
        _db.log_event(conn, "rate_limited", "%s: %s" % (account_email, error))
        self._hold("%s is throttling this %s — holding %d minute(s) before the "
                   "next message. %s"
                   % ("WhatsApp" if self.channel == WHATSAPP else "The server",
                      self._sender_noun(), int(wait // 60), error), "error")

    # ── reading the inbox back ──

    def _warn_unread_optouts(self) -> None:
        """Name every opt-out route this run offers and will never read.

        Said at the start of the run as well as in the plan, and in red, because
        the plan summary is minutes old by the time Start is pressed and this is
        the last moment before the promise in the footer is made to a stranger.

        Email only, and not because WhatsApp has no opt-out to honour — it has a
        stricter one. It is that the route is a reply in the chat the run is
        already holding open, so there is no mailbox that might not be read and
        nothing here to warn about; `wa_warnings` says the thing that channel
        does need said.
        """
        if self.channel != EMAIL:
            return
        for warning in optout_warnings(self._settings):
            self.log_signal.emit(warning, "error")

    def _imap_accounts(self) -> list[dict]:
        return [a for a in _settings.smtp_accounts(self._settings) if reads_inbox(a)]

    def _poll_replies(self, conn, now: float) -> None:
        """Read this channel's replies back. The loop's one door to both."""
        if self.channel == WHATSAPP:
            self._poll_whatsapp(conn, now)
        else:
            self._poll_inboxes(conn, now)

    def _poll_inboxes(self, conn, now: float) -> None:
        """Fold bounces, unsubscribes and replies back into the queue.

        Throttled to `IMAP_POLL_SEC` because each call is a login, a search and
        a fetch against Gmail, and skipped entirely in a dry run — a rehearsal
        that promised nothing would leave the machine has no business opening a
        mailbox. `_running` is re-read between accounts so Stop is not held up
        behind a mailbox that has stopped answering.
        """
        if self.channel != EMAIL:
            return
        if self.dry_run or now - self._polled_at < self.IMAP_POLL_SEC:
            return
        since = self.IMAP_DAYS if self._polled_at else self.IMAP_FIRST_DAYS
        self._polled_at = now

        for account in self._imap_accounts():
            email = _text(account.get("email")).strip()
            password = _text(account.get("app_password"))
            for address in _mailer.check_bounces(email, password, since_days=since):
                self._suppress(conn, address, "hard bounce")
            if not self._running:
                return
            for address in _mailer.check_unsubscribes(email, password, since_days=since):
                self._suppress(conn, address, "unsubscribed")
            if not self._running:
                return
            for header_id in _mailer.check_replies(email, password, since_days=since):
                self._note_reply(conn, header_id)
            if not self._running:
                return

    def _poll_whatsapp(self, conn, now: float) -> None:
        """Fold WhatsApp replies back into the queue, opt-outs first.

        Reads the chat list of the session this run already has open, and never
        opens one to look: a run that has not sent anything has nothing to be
        replied to, and launching a browser to find that out would be a window
        appearing on the user's desktop for no reason. Skipped in a dry run, for
        the reason the mailbox poll is — a rehearsal touches nothing.

        The opt-out check comes before anything else is done with a reply.
        `wa_opt_out_words` matched in a reply suppresses that lead **on both
        channels at once** — `core.outreach_db.suppress` walks the person and
        not the transport — and cancels everything still queued for them,
        including the chaser due tomorrow. That is what makes the STOP line the
        first message promises true.
        """
        if self.dry_run or now - self._polled_at < self.WA_POLL_SEC:
            return
        session = self._live_session()
        if session is None:
            return
        self._polled_at = now

        words = self._settings.get("wa_opt_out_words") or []
        try:
            replies = session.unread_replies(0.0)
        except Exception:                                  # noqa: BLE001 — a read
            return
        for reply in replies or []:
            if not self._running:
                return
            if not isinstance(reply, dict):
                continue
            phone = _text(reply.get("phone")) or _text(reply.get("wa_id"))
            body = _text(reply.get("text"))
            # Deduped on the pair rather than on a timestamp, because the chat
            # list carries a wall-clock label and no epoch — see
            # `WhatsAppSession.unread_replies`. Acting twice on one reply would
            # log the same opt-out twice; missing one is not recoverable.
            key = (_wa.phone_key(phone) or phone, body)
            if not phone or key in self._seen_replies:
                continue
            self._seen_replies.add(key)
            if _wa.matches_opt_out(body, words):
                self._suppress(conn, reason="asked to stop on WhatsApp", phone=phone)
            else:
                self._note_wa_reply(conn, phone)

    def _suppress(self, conn, address: str = "", reason: str = "", *,
                  phone: str = "") -> None:
        """Do-not-contact this person and unwind everything queued to them.

        Either handle suppresses the person and not the channel they used:
        somebody who says stop on WhatsApp must not then receive the email
        sequence, and an unsubscribe by mail has to stop the WhatsApp messages
        already scheduled. `core.outreach_db.suppress` writes both handles and
        cancels every queued row on both channels; all this adds is the guard
        and the line the user reads.
        """
        address = _text(address).strip().lower()
        if "@" not in address:
            address = ""
        phone = _text(phone).strip()
        if not address and not _wa.phone_key(phone):
            return
        if _db.is_suppressed(conn, address, phone=phone):
            return
        # `suppress` cancels every queued and sending message for the lead, not
        # only the next one — the +4 day and +8 day chasers are already on the
        # queue by the time anybody opts out.
        _db.suppress(conn, address, reason, phone=phone)
        self.log_signal.emit("%s — %s; remaining messages cancelled"
                             % (address or phone, reason), "info")
        self._emit_progress(conn)

    def _note_wa_reply(self, conn, phone: str) -> None:
        """A real answer ends the sequence here too: nobody chases a reply.

        Matched on the number rather than on a header id, which WhatsApp has no
        equivalent of. Scoped to no campaign, for the reason `_message_by_header`
        is not: an answer to March's message is still an answer.
        """
        lead = self._lead_by_phone(conn, phone)
        lead_id = _int(lead.get("id"))
        if not lead_id or _text(lead.get("status")) == "replied":
            return
        cancelled = self._cancel_queued(conn, lead_id, "lead replied")
        address = _text(lead.get("email")).strip()
        if address:
            _db.upsert_lead(conn, {"email": address, "status": "replied"})
        _db.log_event(conn, "replied", "%s — %d follow-up(s) cancelled"
                      % (_text(lead.get("phone")) or phone, cancelled), lead_id)
        self.log_signal.emit("%s replied on WhatsApp — sequence stopped"
                             % (_text(lead.get("phone")) or phone), "done")
        self._emit_progress(conn)

    def _lead_by_phone(self, conn, phone: str) -> dict:
        """The lead a number belongs to, matched the way suppression matches."""
        tail = _wa.phone_key(phone)
        if not tail:
            return {}
        rows = _db.rows(conn, "SELECT * FROM leads WHERE phone_key = ? "
                              "ORDER BY id LIMIT 1", (tail,))
        return rows[0] if rows else {}

    def _note_reply(self, conn, header_id: str) -> None:
        """A real answer ends the sequence: nobody chases somebody who replied."""
        row = self._message_by_header(conn, header_id)
        if not row or _text(row.get("status")) == "replied":
            return
        lead_id = _int(row.get("lead_id"))
        _db.mark_message(conn, _int(row.get("id")), "replied")
        cancelled = self._cancel_queued(conn, lead_id, "lead replied")

        lead = _db.get_lead(conn, lead_id)
        address = _text(lead.get("email")).strip()
        if address:
            _db.upsert_lead(conn, {"email": address, "status": "replied"})
        _db.log_event(conn, "replied", "%s — %d follow-up(s) cancelled"
                      % (address or header_id, cancelled), lead_id)
        self.log_signal.emit("%s replied — sequence stopped" % (address or "a lead"), "done")
        self._emit_progress(conn)

    def _message_by_header(self, conn, header_id: str) -> dict:
        """The message we sent under this RFC 5322 id, across every campaign.

        Not scoped to this campaign on purpose: a reply to March's mail is still
        a reply, and it has to stop whatever this campaign has queued for them.
        """
        header_id = _text(header_id).strip()
        if not header_id:
            return {}
        rows = _db.rows(conn,
                        "SELECT id, lead_id, status FROM messages "
                        "WHERE message_id = ? ORDER BY id DESC LIMIT 1",
                        (header_id,))
        return rows[0] if rows else {}

    def _cancel_queued(self, conn, lead_id: int, reason: str,
                       channel: str = "") -> int:
        """Drop everything still owed to one lead. Returns how many.

        A rehearsed row is one of them: it was never sent, so it is on its way
        back to the queue, and a sequence stopped without it would restart on
        the next dry run.

        Every channel by default, because the two reasons this is called for —
        the lead answered, the lead opted out — are facts about the person. The
        `channel` argument is for the one case that is not: a number that turns
        out not to be on WhatsApp says nothing about the address.
        """
        lead_id = _int(lead_id)
        if not lead_id:
            return 0
        scope, params = "", [lead_id]
        if _text(channel).strip():
            scope = " AND COALESCE(channel, 'email') = ?"
            params.append(_channel(channel))
        rows = _db.rows(conn,
                        "SELECT id FROM messages WHERE lead_id = ? "
                        "AND status IN ('queued', 'sending', 'rehearsed')" + scope,
                        tuple(params))
        for row in rows:
            _db.mark_message(conn, _int(row.get("id")), "skipped", error=reason)
        return len(rows)

    # ── rescheduling ──

    def _replan(self, conn, due: list, now: float, reason: str) -> None:
        """Re-space an overdue backlog into the next window that has room.

        Reached when the app was shut for a day, when the user narrowed the
        window under a live plan, or when today's caps are spent. Everything
        due goes back through `next_send_times` rather than being released as
        it comes free, because a hundred messages leaving in one burst at nine
        in the morning is a worse signal than the stale plan ever was.
        """
        if now - self._replanned_at < 30.0:
            self._nap(30.0)
            return
        self._replanned_at = now

        opens = next_window_open(now, self._settings)
        # Asked about the window we are replanning *into*: an account benched
        # by a quota error this afternoon is available again tomorrow morning.
        accounts = self._live_accounts(opens)
        if not accounts:
            self.log_signal.emit("No WhatsApp number available — stopping"
                                 if self.channel == WHATSAPP else
                                 "No sending accounts available — stopping", "error")
            self._running = False
            return

        slots = next_send_times(count=len(due), accounts=accounts, settings=self._settings,
                                start_ts=opens,
                                sent_today_by_account=self._sent_today_map(conn, accounts, opens),
                                seed=self.campaign_id, ramp_start=self._ramp_start)
        if not slots:
            # The settings leave no sendable instant inside a year: no send day
            # survives, or every cap is zero. Nothing the loop does will shift
            # it, so the one useful thing left is to say where the fix is.
            self._hold("%s, and there is nowhere to move %d message(s) to — your "
                       "sending days, hours or caps in Settings leave no room at "
                       "all." % (reason, len(due)), "error")
            self._nap(30.0)
            return

        for row, (when, email) in zip(due, slots):
            # The slot's account is a suggestion; a chaser's is not. Rewriting
            # a follow-up onto whichever account the replan drew would leave the
            # row disagreeing with the account `_pick_account` will actually
            # send it from, and the countdown reads that column.
            bound = self._thread_account(conn, row)
            _db.mark_message(conn, _int(row.get("id")), "queued",
                             scheduled_at=when, account_email=bound or email)
        # Through `_hold` so that the wait this replan creates does not then get
        # announced a second time, in vaguer words, by `_announce_wait`.
        self._hold("%s — %d message(s) held until %s"
                   % (reason, len(slots), _clock(slots[0][0])))

    def _sent_today_map(self, conn, accounts: list, when: float) -> dict[str, int]:
        """Today's send counts per account, rehearsals included.

        A dry run writes nothing to `sends`, so without this it would replan
        the same messages into the same full day for ever instead of showing
        the user the real shape of the campaign.
        """
        counts = _sent_today(conn, accounts, self._settings, when, self.channel)
        if not self.dry_run:
            return counts
        zone = _zone(self._settings)
        midnight = _at(_local_date(when, zone), 0, zone)
        for email, stamps in self._rehearsed.items():
            if email in counts:
                counts[email] += sum(1 for ts in stamps if ts >= midnight)
        return counts

    # ── plumbing ──

    def _sender(self, account: dict):
        email = _text(account["email"]).strip()
        sender = self._senders.get(email.lower())
        if sender is None:
            sender = _mailer.SmtpSender(
                email, _text(account.get("app_password")),
                display_name=_text(account.get("display_name")),
                host=_text(account.get("smtp_host")),
                port=_int(account.get("smtp_port")))
            self._senders[email.lower()] = sender
        return sender

    def _session(self) -> tuple[object, str]:
        """(the WhatsApp session, error). Never called by a dry run.

        `start()` is idempotent while the session is alive, so a borrowed
        session the Settings screen already brought up costs a dictionary read
        here, and a run that owns its own opens Chrome exactly once. Whether the
        session is *ready* is `send`'s question, not this one — it answers it
        without blocking, and this method would have to wait to.
        """
        session = self._wa_session
        if session is None:
            try:
                session = _wa.WhatsAppSession(
                    profile=_text(self._settings.get("wa_profile") or "default"),
                    headless=bool(self._settings.get("wa_headless", False)),
                    default_region=wa_region(self._settings))
            except Exception as exc:                       # noqa: BLE001 — returned
                return None, "CONN: the WhatsApp browser could not be started (%s)" % exc
            self._wa_session = session
        try:
            ok, error = session.start()
        except Exception as exc:                           # noqa: BLE001 — returned
            return None, "CONN: WhatsApp would not start (%s)" % exc
        if not ok:
            return None, error or "CONN: WhatsApp would not start."
        return session, ""

    def _live_session(self):
        """The session already open, or None. Never opens one.

        The poll uses this rather than `_session`: reading the chat list is
        worth doing when a browser is already up and is never worth launching
        one for, least of all in a run that has not sent anything yet.
        """
        return self._wa_session

    def _close_senders(self) -> None:
        """Close every transport this run is responsible for.

        A borrowed WhatsApp session is deliberately left open — it belongs to
        the Settings screen, which is still showing its status.
        """
        for sender in list(self._senders.values()):
            try:
                sender.close()
            except Exception:
                pass
        self._senders.clear()
        if self._owns_session and self._wa_session is not None:
            session, self._wa_session = self._wa_session, None
            try:
                session.close()
            except Exception:
                pass

    def _emit_progress(self, conn) -> None:
        stats = _db.campaign_stats(conn, self.campaign_id)
        self.stats_signal.emit(stats)
        total = _int(stats.get("total"))
        self.progress_signal.emit(max(0, total - _int(stats.get("queued"))), total)


def _clock(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%a %H:%M")
    except (OSError, OverflowError, ValueError):
        return "later"


# ── The audit worker ─────────────────────────────────────────────────────────

class AuditWorker(QThread):
    """Enrich, audit and personalise a batch of leads without sending anything.

    Every lead is one site crawl plus at most one model call, so the batch is
    network-bound and runs on a thread pool. Stopping cancels whatever has not
    started and stops collecting results; work already in flight is bounded by
    the fetch timeouts rather than by anything this class does.
    """

    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int, int)
    lead_signal = pyqtSignal(dict)
    done_signal = pyqtSignal()

    def __init__(self, leads: list[dict], settings: dict, template_id: str = ""):
        super().__init__()
        self.leads = [lead for lead in (leads or []) if isinstance(lead, dict)]
        self._settings = settings if isinstance(settings, dict) else {}
        self.template_id = _text(template_id)
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            self._loop()
        except Exception as exc:
            self.log_signal.emit(str(exc), "error")
        finally:
            self.done_signal.emit()

    def _loop(self) -> None:
        if not self.leads:
            return

        conn = _db.connect()
        profile = self._settings.get("sender_profile") or {}
        ai = AIClient(self._settings)
        if not ai.available():
            self.log_signal.emit(
                "No AI provider available — using the plain templates", "info")

        # Every database call in this class happens on this thread, and the
        # pool only ever touches the network. `core.outreach_db` shares one
        # sqlite3 connection across the whole process and guards its writes;
        # six pool threads reading and writing through that at once is outside
        # what it promises, and the failure is silent wrong rows rather than an
        # error.
        pending = []
        for lead in self.leads:
            if not self._running:
                break
            lead_id = _int(lead.get("id")) or _db.upsert_lead(conn, lead)
            if lead_id:
                pending.append((lead_id, lead))

        total = len(pending)
        if total < len(self.leads):
            self.log_signal.emit("Skipped %d lead(s) with no usable email address"
                                 % (len(self.leads) - total), "info")
        self.progress_signal.emit(0, total)

        done = 0
        workers = max(1, min(12, _int(self._settings.get("enrich_workers"), 6)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(self._audit, lead, profile, ai): lead_id
                       for lead_id, lead in pending}
            for future in as_completed(futures):
                if not self._running:
                    break
                lead_id = futures[future]
                audit, fields = future.result()
                _db.set_lead_audit(conn, lead_id, audit, fields)
                row = _db.get_lead(conn, lead_id)
                done += 1
                self.progress_signal.emit(done, total)
                if row:
                    self.lead_signal.emit(row)
                    self.log_signal.emit(
                        "%s — score %s" % (row.get("name") or row.get("email"),
                                           row.get("opportunity_score")), "active")
        finally:
            # cancel_futures clears the backlog at once; the shutdown then waits
            # only on the handful of crawls already in flight, each of which is
            # bounded by its own fetch timeout.
            pool.shutdown(wait=True, cancel_futures=True)

        self.log_signal.emit("Audited %d of %d lead(s)" % (done, total),
                             "done" if self._running else "info")

    def _audit(self, lead: dict, profile: dict, ai) -> tuple[dict, dict]:
        """Runs on the pool: network only, no database, never raises."""
        if not self._running:
            return {}, {}
        try:
            return audit_lead(lead, settings=self._settings, ai=ai,
                              profile=profile, template_id=self.template_id)
        except Exception:
            return {}, {}
