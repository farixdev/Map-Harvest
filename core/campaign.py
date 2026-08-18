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

Nothing here raises across the module boundary. A campaign that cannot be
planned comes back as a plan dict carrying `error`, and a worker that hits
something unexpected emits `error_signal` and finishes cleanly.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
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
            audit = _audit.audit_from_html({}, site.get("final_url") or website)
            audit["error"] = _text(site.get("error")) or "unreachable"
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


# ── Planning a campaign ──────────────────────────────────────────────────────

def _blank_plan(campaign_id: int) -> dict:
    return {"campaign_id": _int(campaign_id), "leads": 0, "queued": 0, "followups": 0,
            "skipped": 0, "skip_reasons": {}, "generic": 0, "generic_reasons": {},
            "accounts": [], "days": 0, "per_day": {}, "first_send": 0.0,
            "last_send": 0.0, "daily_cap": 0, "error": "", "cancelled": False}


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
                  progress=None, should_stop=None) -> dict:
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
    """
    plan = _blank_plan(campaign_id)
    try:
        return _plan(conn, plan, leads or [], _text(template_id), profile or {},
                     settings or {}, ai, progress, should_stop)
    except Exception as exc:
        plan["error"] = f"{type(exc).__name__}: {exc}"[:200]
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
          settings: dict, ai, progress, should_stop) -> dict:
    conn = conn if conn is not None else _db.connect()
    campaign_id = plan["campaign_id"]

    first_touch = _templates.get_template(template_id)
    if first_touch is None or first_touch.step != 0:
        candidates = _templates.templates_for_step(0)
        first_touch = candidates[0] if candidates else None
    if first_touch is None:
        plan["error"] = "there is no first-touch template to write from"
        return plan

    # Every other refusal in this file is a lead counted out with a reason, and
    # this one cannot be: a message needs somewhere to leave from, and no
    # setting makes that optional. It says where the fix is instead, because the
    # user reaches it having already chosen to go ahead without one.
    accounts = [a for a in _settings.smtp_accounts(settings) if _text(a.get("email")).strip()]
    if not accounts:
        plan["error"] = "no Gmail account is set up to send from — add one in Settings"
        return plan
    plan["accounts"] = [a["email"] for a in accounts]

    if not profile:
        profile = settings.get("sender_profile") or {}

    rows = _eligible_leads(conn, leads, plan)
    plan["leads"] = len(rows)
    if not rows:
        plan["error"] = plan["error"] or (
            "no leads left to contact — every one here has had a first touch "
            "already, or is suppressed")
        return plan

    total = len(rows)
    rows = _audit_pass(conn, rows, settings, profile, ai, first_touch.id, plan,
                       total, progress, should_stop)
    if plan["cancelled"]:
        return plan
    prepared = _render_pass(rows, first_touch, profile, settings, plan, total,
                            progress, should_stop)
    if plan["cancelled"]:
        return plan
    if not prepared:
        plan["error"] = plan["error"] or (
            "no usable copy came out of any lead — audit them and try again")
        return plan

    now = time.time()
    ramp = campaign_start_day(conn, campaign_id, settings, now)
    sent_today = _sent_today(conn, accounts, settings, now)
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
    _queue_pass(conn, plan, prepared, slots, settings, total, progress, schedule,
                should_stop)
    plan["days"] = len(plan["per_day"])
    if plan["queued"]:
        _db.set_campaign_status(conn, campaign_id, "scheduled")
        _db.log_event(conn, "planned",
                      "campaign %d: %d queued, %d follow-ups, %d skipped, "
                      "%d not personalised"
                      % (campaign_id, plan["queued"], plan["followups"],
                         plan["skipped"], plan["generic"]))
    return plan


def _contacted_lead_ids(conn) -> set[int]:
    """Leads that already have a first touch sent or pending.

    Read off `messages` rather than off `leads.status`, because the rule is
    about messages: a lead whose status was reset by a re-import is still a
    person who has had one cold email, and a second one is the compliance
    failure this guards against.
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT lead_id FROM messages WHERE step = 0 "
            "AND status IN ('queued', 'sending', 'sent', 'replied', 'bounced')"
        ).fetchall()
    except sqlite3.Error:
        return set()
    return {_int(row[0]) for row in rows}


# Every pass below walks leads built out of scraped third-party text, and a
# value no amount of guarding upstream anticipated will eventually arrive. Each
# one therefore carries the same per-lead guard: the bad record takes itself out
# of the campaign, with a reason the Campaign screen can print, and the other
# 499 go on. Round 5 gave `_render_pass` that guard and left the three passes
# around it able to return a campaign of zero from one frame earlier.


def _eligible_leads(conn, leads: list, plan: dict) -> list[dict]:
    """Stored lead rows for everything in `leads` that may still be contacted."""
    contacted = _contacted_lead_ids(conn)
    seen: set[int] = set()
    rows = []
    for lead in leads:
        try:
            if not isinstance(lead, dict):
                _skip(plan, "not a lead record")
                continue
            email = _text(lead.get("email")).strip().lower()
            if "@" not in email or _db.is_suppressed(conn, email):
                _skip(plan, "no usable address, or suppressed")
                continue
            lead_id = _int(lead.get("id")) or _db.upsert_lead(conn, lead)
            if not lead_id or lead_id in contacted or lead_id in seen:
                _skip(plan, "already contacted")
                continue
            seen.add(lead_id)
            rows.append(_db.get_lead(conn, lead_id) or dict(lead, id=lead_id))
        except Exception as exc:
            _skip(plan, "could not be read (%s)" % type(exc).__name__)
    return rows


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
                 total: int, progress, should_stop) -> list[tuple]:
    """(lead, ctx, subject, text, html) per lead whose copy came out usable.

    Also where a form letter is counted. `core.templates` keeps a model sentence
    only when it refers to this recipient, so a lead whose site never answered
    renders three paragraphs that could have been written before the crawl —
    correct, and invisible until it is counted here.
    """
    prepared = []
    for index, row in enumerate(rows, start=1):
        if _stopped(should_stop, plan):
            return prepared
        try:
            audit = _loads(row.get("audit_json"))
            fields = _loads(row.get("ai_json"))
            ctx = apply_compliance(
                _templates.build_context(row, audit, fields, profile, settings), settings)
            subject, text, html = _templates.render(template, ctx)
        except Exception as exc:
            _skip(plan, "could not be rendered (%s)" % type(exc).__name__)
            continue
        if not subject or not text:
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


def _queue_pass(conn, plan: dict, prepared: list, slots: list, settings: dict,
                total: int, progress, schedule: dict, should_stop) -> None:
    zone = _zone(settings)
    placed = []

    for index, ((row, ctx, subject, text, html), (when, account_email)) in enumerate(
            zip(prepared, slots), start=1):
        if _stopped(should_stop, plan):
            return
        try:
            lead_id = _int(row.get("id"))
            message_id = _db.queue_message(conn, {
                "campaign_id": plan["campaign_id"], "lead_id": lead_id, "step": 0,
                "subject": subject, "body_text": text, "body_html": html,
                "account_email": account_email, "scheduled_at": when,
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
        _queue_followups(conn, plan, placed, settings, schedule)


def _queue_followups(conn, plan: dict, placed: list, settings: dict,
                     schedule: dict) -> None:
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
    """
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
        if not _templates.templates_for_step(step):
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
                if not _queue_followup(conn, plan, ctx, lead_id, step, email, when):
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
                    account_email: str, when: float) -> bool:
    options = _templates.templates_for_step(step)
    if not options:
        return False
    subject, text, html = _templates.render(options[0], ctx)
    if not subject or not text:
        return False
    return bool(_db.queue_message(conn, {
        "campaign_id": plan["campaign_id"], "lead_id": lead_id, "step": step,
        "subject": subject, "body_text": text, "body_html": html,
        "account_email": account_email, "scheduled_at": when,
    }))


def _sent_today(conn, accounts: list, settings: dict, now: float) -> dict[str, int]:
    zone = settings.get("send_timezone")
    return {_text(a["email"]).strip().lower():
            _db.sent_today(conn, a["email"], zone, now_ts=now) for a in accounts}


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

    def __init__(self, campaign_id: int, settings: dict, dry_run: bool = False):
        super().__init__()
        self.campaign_id = _int(campaign_id)
        self._settings = settings if isinstance(settings, dict) else {}
        # Or-ed with the setting, never replaced by the argument: a caller that
        # forgets to pass the flag must not turn a rehearsal into a live send.
        self.dry_run = bool(dry_run) or bool(self._settings.get("dry_run", True))
        self._running = True
        self._paused = False
        self._senders: dict[str, _mailer.SmtpSender] = {}
        self._stopped: dict[str, date] = {}    # account -> the day it hit AUTH/QUOTA
        self._next_ok: dict[str, float] = {}   # per-account gap between sends
        self._rehearsed: dict[str, list] = {}  # dry-run sends, for the cap checks
        self._conn_failures = 0
        self._replanned_at = 0.0
        self._polled_at = 0.0                  # last inbox read
        self._ramp_start: date | None = None   # resolved once the DB is open

    # ── control ──

    def stop(self) -> None:
        self._running = False
        self._paused = False

    def abort(self) -> None:
        """Stop and force the SMTP sockets closed (used when the app is closing).

        A thread parked inside `smtplib` cannot read the stop flag; closing the
        session underneath it makes that call fail fast so `run()` can unwind,
        the same trick `ScrapeWorker.abort()` uses on the browser.
        """
        self.stop()
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
            self._close_senders()
            self.done_signal.emit()

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
                "retried — check the account's Sent folder if you need to be sure."
                % (len(rows), "" if len(rows) == 1 else "s"), "info")

    def _loop(self) -> None:
        conn = _db.connect()
        self._ramp_start = campaign_start_day(conn, self.campaign_id, self._settings)
        self._recover_claimed(conn)
        _db.set_campaign_status(conn, self.campaign_id, "running")
        if self.dry_run:
            self.log_signal.emit(
                "DRY RUN — messages are marked sent and nothing leaves this machine", "info")
        self._emit_progress(conn)

        while self._running:
            if self._paused:
                self._nap(0.5)
                continue

            self._poll_inboxes(conn, time.time())

            stats = _db.campaign_stats(conn, self.campaign_id)
            if _int(stats.get("queued")) <= 0:
                self.stats_signal.emit(stats)
                self.log_signal.emit("Campaign finished — nothing left in the queue", "done")
                _db.set_campaign_status(conn, self.campaign_id, "done")
                return

            now = time.time()
            due = self._due(conn, now)
            if not due:
                self._nap(min(30.0, max(1.0, self._seconds_until_next(conn, now))))
                continue
            self._dispatch(conn, due, now)

        _db.set_campaign_status(conn, self.campaign_id, "stopped")
        self.log_signal.emit("Stopped", "done")

    def _due(self, conn, now: float) -> list[dict]:
        return [row for row in _db.due_messages(conn, now, limit=200)
                if _int(row.get("campaign_id")) == self.campaign_id]

    def _seconds_until_next(self, conn, now: float) -> float:
        """How long until this campaign's next message is due. 30 s if unknown."""
        horizon = now + _MAX_PLAN_DAYS * _DAY_SEC
        for row in _db.due_messages(conn, horizon, limit=400):
            if _int(row.get("campaign_id")) == self.campaign_id:
                return max(1.0, _float(row.get("scheduled_at")) - now)
        return 30.0

    # ── one message ──

    def _dispatch(self, conn, due: list, now: float) -> None:
        if not in_send_window(now, self._settings):
            self._replan(conn, due, now, "Outside the sending window")
            return

        account, wait = self._pick_account(conn, due[0], now)
        if account is None:
            if wait > 0:
                self._nap(min(wait, 30.0))       # only the pacing gap is holding us
                return
            self._replan(conn, due, now, "Every account is at its cap for today")
            return

        self._send(conn, due[0], account, now)

    def _live_accounts(self, now: float = 0.0) -> list[dict]:
        """Enabled accounts that are not out of action for today.

        A quota or auth failure retires an account for the calendar day it
        happened on, not for the run: a campaign that spans a fortnight would
        otherwise finish with every account permanently benched over one bad
        afternoon.
        """
        today = _local_date(now or time.time(), _zone(self._settings))
        return [a for a in _settings.smtp_accounts(self._settings)
                if _text(a.get("email")).strip()
                and self._stopped.get(_text(a.get("email")).strip().lower()) != today]

    def _pick_account(self, conn, row: dict, now: float) -> tuple[dict | None, float]:
        """(account to send from, seconds to wait). Both empty means all capped."""
        accounts = self._live_accounts(now)
        preferred = _text(row.get("account_email")).strip().lower()
        accounts.sort(key=lambda a: _text(a.get("email")).strip().lower() != preferred)

        waits = []
        for account in accounts:
            email = _text(account["email"]).strip().lower()
            if not self._has_headroom(conn, account, now):
                continue
            ready = self._next_ok.get(email, 0.0)
            if ready > now:
                waits.append(ready - now)
                continue
            return account, 0.0
        return None, min(waits) if waits else 0.0

    def _has_headroom(self, conn, account: dict, now: float) -> bool:
        zone = _zone(self._settings)
        email = _text(account["email"]).strip().lower()
        rehearsed = self._rehearsed.get(email) or []
        midnight = _at(_local_date(now, zone), 0, zone)

        cap = account_daily_cap(account, self._settings, on_day=_local_date(now, zone),
                                ramp_start=self._ramp_start)
        today = _db.sent_today(conn, email, self._settings.get("send_timezone"), now_ts=now)
        # Rehearsed sends count towards the cap here but are never written to
        # `sends`: pacing a dry run realistically is worth having, spending a
        # live account's real daily quota on messages nobody received is not.
        today += sum(1 for ts in rehearsed if ts >= midnight)
        if today >= cap:
            return False

        hourly = _hourly_cap(self._settings)
        if hourly >= _NO_CAP:
            return True
        hour = _db.sent_last_hour(conn, email, now_ts=now)
        hour += sum(1 for ts in rehearsed if ts > now - _HOUR_SEC)
        return hour < hourly

    def _thread_parent(self, conn, row: dict) -> str:
        """The first touch's Message-ID, for a chaser to reply into.

        Returns "" for a first touch, and for a chaser whose parent never got a
        Message-ID because it has not been sent yet — in which case the chaser
        stands on its own rather than pointing at a conversation that does not
        exist.
        """
        if _int(row.get("step")) <= 0:
            return ""
        return _db.first_touch_message_id(conn, _int(row.get("campaign_id")),
                                          _int(row.get("lead_id")))

    def _send(self, conn, row: dict, account: dict, now: float) -> None:
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
            message_id=_text(row.get("message_id")),
            in_reply_to=self._thread_parent(conn, row),
        )

        if self.dry_run:
            ok, error = True, "DRY-RUN"
        else:
            # Claim it before the hand-off. A crash between the server taking
            # the message and this process writing the result would otherwise
            # leave the row `queued`, and the restart would send it a second
            # time; `sending` is recoverable evidence that it may already be
            # gone, which is the safer side to be wrong on.
            _db.mark_message(conn, message_id, "sending", account_email=account_email)
            ok, error = self._sender(account).send(message)

        key = account_email.lower()
        # The pacing slot belongs to the attempt, not to its outcome. A run of
        # dead addresses is ordinary in a scraped list, and a failure that
        # leaves the gap unspent puts the next transaction on the wire
        # immediately — twenty-five refusals a second is a far louder signal to
        # Gmail than twenty-five deliveries would have been.
        self._next_ok[key] = now + _RUNTIME_RNG.randint(*_gap_bounds(self._settings))
        # Caps count transactions, not deliveries. A provider sees a refusal as
        # plainly as an acceptance, and a stretch of dead addresses in a scraped
        # list is the ordinary case — counting only successes let one account
        # put sixty rejected transactions on the wire against a cap of ten,
        # which is the reputation damage the caps exist to prevent.
        if self.dry_run:
            self._rehearsed.setdefault(key, []).append(now)
        else:
            _db.record_send(conn, account_email, now)

        if ok:
            self._conn_failures = 0
            _db.mark_message(conn, message_id, "sent", sent_at=now, message_id=header_id,
                             account_email=account_email, error=error if self.dry_run else "")
            _db.upsert_lead(conn, {"email": to_email, "status": "sent"})
            _db.log_event(conn, "sent", "%s via %s" % (to_email, account_email),
                          _int(row.get("lead_id")))
            self.message_sent_signal.emit(dict(row, status="sent", sent_at=now,
                                               message_id=header_id,
                                               account_email=account_email,
                                               error=error if self.dry_run else ""))
            self.log_signal.emit("%s to %s%s" % ("Would send" if self.dry_run else "Sent",
                                                 to_email,
                                                 "" if self.dry_run else " via " + account_email),
                                 "active")
            self._emit_progress(conn)
            return

        self._handle_failure(conn, row, account_email, error, now)

    def _handle_failure(self, conn, row: dict, account_email: str, error: str,
                        now: float) -> None:
        message_id = _int(row.get("id"))
        kind = error.split(":", 1)[0].strip().upper()

        if kind in ("AUTH", "QUOTA"):
            # The account is finished for today; the message itself is fine and
            # goes back on the queue for whichever account is still standing.
            self._stopped[account_email.lower()] = _local_date(now, _zone(self._settings))
            _db.mark_message(conn, message_id, "queued", scheduled_at=now + 60.0)
            _db.log_event(conn, "account_stopped", "%s: %s" % (account_email, error))
            self.log_signal.emit("%s stopped for today — %s" % (account_email, error), "error")
            if not self._live_accounts(now):
                self.log_signal.emit(
                    "No sending accounts left — stopping the run. Fix the account in "
                    "Settings, then start again.", "error")
                _db.set_campaign_status(conn, self.campaign_id, "stopped")
                self._running = False
            return

        if kind == "RECIPIENT":
            _db.mark_message(conn, message_id, "failed", error=error)
            lead = _db.get_lead(conn, _int(row.get("lead_id")))
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

    # ── reading the inbox back ──

    def _imap_accounts(self) -> list[dict]:
        return [a for a in _settings.smtp_accounts(self._settings)
                if a.get("imap_enabled") and _text(a.get("email")).strip()
                and _text(a.get("app_password"))]

    def _poll_inboxes(self, conn, now: float) -> None:
        """Fold bounces, unsubscribes and replies back into the queue.

        Throttled to `IMAP_POLL_SEC` because each call is a login, a search and
        a fetch against Gmail, and skipped entirely in a dry run — a rehearsal
        that promised nothing would leave the machine has no business opening a
        mailbox. `_running` is re-read between accounts so Stop is not held up
        behind a mailbox that has stopped answering.
        """
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

    def _suppress(self, conn, address: str, reason: str) -> None:
        """Do-not-contact an address and unwind everything queued to it."""
        address = _text(address).strip().lower()
        if "@" not in address or _db.is_suppressed(conn, address):
            return
        # `suppress` cancels every queued and sending message for the lead, not
        # only the next one — the +4 day and +8 day chasers are already on the
        # queue by the time anybody opts out.
        _db.suppress(conn, address, reason)
        self.log_signal.emit("%s — %s; remaining messages cancelled" % (address, reason),
                             "info")
        self._emit_progress(conn)

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
        try:
            rows = conn.execute(
                "SELECT id, lead_id, status FROM messages WHERE message_id = ? "
                "ORDER BY id DESC LIMIT 1", (header_id,)).fetchall()
        except sqlite3.Error:
            return {}
        return {"id": rows[0][0], "lead_id": rows[0][1], "status": rows[0][2]} if rows else {}

    def _cancel_queued(self, conn, lead_id: int, reason: str) -> int:
        """Drop everything still queued for one lead. Returns how many."""
        lead_id = _int(lead_id)
        if not lead_id:
            return 0
        try:
            rows = conn.execute(
                "SELECT id FROM messages WHERE lead_id = ? "
                "AND status IN ('queued', 'sending')", (lead_id,)).fetchall()
        except sqlite3.Error:
            return 0
        for row in rows:
            _db.mark_message(conn, _int(row[0]), "skipped", error=reason)
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
            self.log_signal.emit("No sending accounts available — stopping", "error")
            self._running = False
            return

        slots = next_send_times(count=len(due), accounts=accounts, settings=self._settings,
                                start_ts=opens,
                                sent_today_by_account=self._sent_today_map(conn, accounts, opens),
                                seed=self.campaign_id, ramp_start=self._ramp_start)
        if not slots:
            self._nap(30.0)
            return

        for row, (when, email) in zip(due, slots):
            _db.mark_message(conn, _int(row.get("id")), "queued",
                             scheduled_at=when, account_email=email)
        self.log_signal.emit("%s — %d message(s) held until %s"
                             % (reason, len(slots), _clock(slots[0][0])), "info")

    def _sent_today_map(self, conn, accounts: list, when: float) -> dict[str, int]:
        """Today's send counts per account, rehearsals included.

        A dry run writes nothing to `sends`, so without this it would replan
        the same messages into the same full day for ever instead of showing
        the user the real shape of the campaign.
        """
        counts = _sent_today(conn, accounts, self._settings, when)
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
            sender = _mailer.SmtpSender(email, _text(account.get("app_password")),
                                        display_name=_text(account.get("display_name")))
            self._senders[email.lower()] = sender
        return sender

    def _close_senders(self) -> None:
        for sender in list(self._senders.values()):
            try:
                sender.close()
            except Exception:
                pass
        self._senders.clear()

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
