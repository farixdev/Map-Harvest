"""Offline tests for the send scheduler in core.campaign.

Run:  venv/Scripts/python.exe -m tests.test_schedule
(or `python -m pytest tests/ -q` where pytest is installed).

No clock, no database, no network, no Qt event loop. Every test hands
`next_send_times` a fixed start instant — Monday 9 March 2026, 08:00 local —
so the assertions hold whatever day the suite happens to run on, and reads the
results back through the machine's own local time, so they hold in whatever
timezone it runs in too.

What is being guarded here is the one thing in this app that can cost the user
their Gmail account: the shape of the outgoing stream. Working hours, working
days, daily and hourly ceilings, a ramp on a fresh account, and gaps that vary.
The determinism tests exist so the rest of them can be written at all.

The second half of the file covers the two callers of that schedule — the
planner and the send worker — against a throwaway database. Those tests do
touch sqlite, because what they are asserting is that the rows written to
`messages` have the shape the pure function promised, and that the summary the
user approves describes the queue that was actually written. No clock is slept
on, no socket is opened: SMTP and IMAP are both stubbed.
"""

import contextlib
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audit as A  # noqa: E402
from core import campaign as C  # noqa: E402
from core import enrich as E  # noqa: E402
from core import mailer as M  # noqa: E402
from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402

# 9 March 2026 is a Monday. 08:00 is an hour before the window opens, so the
# first placed send has to be pulled forward to 09:00 rather than left at now.
START = datetime(2026, 3, 9, 8, 0)

BASE = {
    "send_days": [0, 1, 2, 3, 4],
    "send_start_hour": 9,
    "send_end_hour": 17,
    "send_timezone": "local",
    "send_min_gap_sec": 60,
    "send_max_gap_sec": 240,
    "daily_cap_per_account": 40,
    "hourly_cap_per_account": 12,
    "warmup_enabled": False,
    "warmup_start": 10,
    "warmup_step": 5,
    "warmup_max": 40,
}


def _settings(**overrides) -> dict:
    return dict(BASE, **overrides)


def _account(email: str = "sender@shop.test", **extra) -> dict:
    return {"email": email, "app_password": "x", "display_name": "", "daily_cap": 40,
            "enabled": True, "warmup_started": "", "imap_enabled": False, **extra}


def _at(days: int = 0, hour: int = 8, minute: int = 0) -> float:
    return (START + timedelta(days=days)).replace(hour=hour, minute=minute).timestamp()


def _plan(count: int, accounts=None, sent_today=None, seed: int = 7, **overrides):
    return C.next_send_times(
        count=count,
        accounts=accounts if accounts is not None else [_account()],
        settings=_settings(**overrides),
        start_ts=_at(),
        sent_today_by_account=sent_today or {},
        seed=seed,
    )


def _day(ts: float) -> date:
    return datetime.fromtimestamp(ts).date()


def _by_day(slots) -> dict:
    out: dict = {}
    for ts, _email in slots:
        out.setdefault(_day(ts), []).append(ts)
    return out


def _per_account(slots) -> dict:
    out: dict = {}
    for ts, email in slots:
        out.setdefault(email, []).append(ts)
    return out


# ── Determinism ──────────────────────────────────────────────────────────────

def test_same_seed_same_schedule():
    first = _plan(60, seed=11)
    second = _plan(60, seed=11)
    assert first == second, "a fixed seed must reproduce the schedule exactly"
    assert len(first) == 60

    other = _plan(60, seed=12)
    assert other != first, "a different seed must move the gaps"
    assert len(other) == 60
    print("deterministic for a fixed seed: OK")


def test_gaps_do_not_depend_on_the_other_accounts():
    # Adding a second account must not reshuffle the first account's own gaps,
    # or every test below would be asserting on an accident of iteration order.
    alone = _per_account(_plan(20, accounts=[_account("a@shop.test")]))["a@shop.test"]
    shared = _per_account(_plan(40, accounts=[_account("a@shop.test"),
                                              _account("b@shop.test")]))["a@shop.test"]
    assert shared[:20] == alone, (shared[:3], alone[:3])
    print("per-account gap streams are independent: OK")


# ── Window and days ──────────────────────────────────────────────────────────

def test_every_send_lands_inside_the_window():
    for ts, _email in _plan(300):
        when = datetime.fromtimestamp(ts)
        assert 9 <= when.hour < 17, when
        assert when.weekday() in (0, 1, 2, 3, 4), when
    print("window and send days: OK")


def test_output_is_ascending_and_capped_to_count():
    slots = _plan(137)
    assert len(slots) == 137
    stamps = [ts for ts, _ in slots]
    assert stamps == sorted(stamps), "callers zip this against their leads in order"
    assert stamps[0] == _at(hour=9), "a start before the window waits for it to open"
    print("ascending and capped to count: OK")


def test_custom_days_and_window_are_honoured():
    slots = _plan(80, send_days=[2, 5], send_start_hour=13, send_end_hour=15)
    for ts, _email in slots:
        when = datetime.fromtimestamp(ts)
        assert when.weekday() in (2, 5), when
        assert 13 <= when.hour < 15, when
    assert _day(slots[0][0]) == date(2026, 3, 11), _day(slots[0][0])
    print("custom days and window: OK")


def test_window_helpers():
    assert C.in_send_window(_at(hour=10), BASE)
    assert not C.in_send_window(_at(hour=8), BASE)
    assert not C.in_send_window(_at(hour=17), BASE)
    assert not C.in_send_window(_at(days=5, hour=10), BASE)     # Saturday

    assert C.next_window_open(_at(hour=10), BASE) == _at(hour=10)
    assert C.next_window_open(_at(hour=7), BASE) == _at(hour=9)
    assert C.next_window_open(_at(hour=19), BASE) == _at(days=1, hour=9)
    assert C.next_window_open(_at(days=5, hour=12), BASE) == _at(days=7, hour=9)
    print("window helpers: OK")


# ── Caps ─────────────────────────────────────────────────────────────────────

def test_daily_cap_per_account():
    for ts_list in _by_day(_plan(200)).values():
        assert len(ts_list) <= 40, len(ts_list)
    assert len(_by_day(_plan(200))[date(2026, 3, 9)]) == 40
    print("daily cap: OK")


def test_the_smaller_of_the_two_daily_caps_wins():
    tight = _plan(100, accounts=[_account(daily_cap=7)])
    assert len(_by_day(tight)[date(2026, 3, 9)]) == 7

    tight = _plan(100, daily_cap_per_account=5)
    assert len(_by_day(tight)[date(2026, 3, 9)]) == 5

    # A blank per-account cap means "not configured", not "zero".
    loose = _plan(100, accounts=[_account(daily_cap=0)])
    assert len(_by_day(loose)[date(2026, 3, 9)]) == 40
    print("caps compose as a minimum: OK")


def test_sent_today_eats_into_the_first_day_only():
    slots = _plan(100, sent_today={"sender@shop.test": 36})
    days = _by_day(slots)
    assert len(days[date(2026, 3, 9)]) == 4, days[date(2026, 3, 9)]
    assert len(days[date(2026, 3, 10)]) == 40

    # An account already at its cap simply starts tomorrow.
    slots = _plan(50, sent_today={"SENDER@shop.test": 40})
    assert _day(slots[0][0]) == date(2026, 3, 10)
    print("sent_today applies to today only: OK")


def test_hourly_cap_holds_over_a_rolling_hour():
    slots = _plan(200, hourly_cap_per_account=6)
    stamps = [ts for ts, _ in slots]
    for index, start in enumerate(stamps):
        window = [t for t in stamps[index:] if t < start + 3600]
        assert len(window) <= 6, (index, len(window))
    print("hourly cap over a rolling hour: OK")


def test_hourly_cap_is_per_account():
    accounts = [_account("a@shop.test"), _account("b@shop.test")]
    slots = _plan(120, accounts=accounts, hourly_cap_per_account=4)
    for stamps in _per_account(slots).values():
        for index, start in enumerate(stamps):
            window = [t for t in stamps[index:] if t < start + 3600]
            assert len(window) <= 4, (index, len(window))
    # Two accounts at four an hour each is eight an hour overall.
    first_hour = [ts for ts, _ in slots if ts < slots[0][0] + 3600]
    assert len(first_hour) == 8, len(first_hour)
    print("hourly cap is per account: OK")


# ── Warm-up ──────────────────────────────────────────────────────────────────

def test_warmup_ramps_day_one_to_day_five():
    account = _account(warmup_started="2026-03-09", daily_cap=100)
    slots = _plan(200, accounts=[account], warmup_enabled=True,
                  daily_cap_per_account=100, hourly_cap_per_account=0)
    counts = [len(v) for _k, v in sorted(_by_day(slots).items())]
    # start 10, step 5: 10 on the first day, 30 on the fifth.
    assert counts[:5] == [10, 15, 20, 25, 30], counts[:5]
    print("warm-up ramp day 1 to day 5: OK")


def test_warmup_clamps_at_its_maximum():
    account = _account(warmup_started="2026-01-01", daily_cap=100)
    on_day = date(2026, 3, 9)
    settings = _settings(warmup_enabled=True, daily_cap_per_account=100, warmup_max=25)
    assert C.account_daily_cap(account, settings, on_day=on_day) == 25

    settings = _settings(warmup_enabled=True, daily_cap_per_account=100)
    assert C.account_daily_cap(account, settings, on_day=on_day) == 40   # warmup_max
    print("warm-up clamps at warmup_max: OK")


def test_warmup_is_a_minimum_not_an_override():
    account = _account(warmup_started="2026-01-01", daily_cap=6)
    settings = _settings(warmup_enabled=True, daily_cap_per_account=100)
    # Fully warmed to 40, but the account's own cap is 6.
    assert C.account_daily_cap(account, settings, on_day=date(2026, 3, 9)) == 6

    # And the other way round: caps of 40 do not lift a first-day ramp of 10.
    fresh = _account(warmup_started="2026-03-09", daily_cap=40)
    assert C.account_daily_cap(fresh, settings, on_day=date(2026, 3, 9)) == 10

    # Off means off.
    off = _settings(warmup_enabled=False, daily_cap_per_account=100)
    assert C.account_daily_cap(fresh, off, on_day=date(2026, 3, 9)) == 40
    print("warm-up is a minimum, not an override: OK")


def test_an_account_with_no_start_date_still_ramps():
    # A missing warmup_started is read as "starting with this plan" rather than
    # as "fully warmed" — the safe guess for an account of unknown age.
    slots = _plan(200, accounts=[_account(daily_cap=100)], warmup_enabled=True,
                  daily_cap_per_account=100, hourly_cap_per_account=0)
    counts = [len(v) for _k, v in sorted(_by_day(slots).items())]
    assert counts[:3] == [10, 15, 20], counts[:3]
    print("missing warm-up start date still ramps: OK")


def test_ramp_start_anchors_an_undated_account():
    # Replanning a running campaign must not drop an undated account back to
    # its first-day rate: the caller passes the day the campaign began, and the
    # ramp carries on from there.
    settings = dict(BASE, warmup_enabled=True, daily_cap_per_account=100,
                    hourly_cap_per_account=0)
    slots = C.next_send_times(count=200, accounts=[_account(daily_cap=100)],
                              settings=settings, start_ts=_at(),
                              sent_today_by_account={}, seed=7,
                              ramp_start=date(2026, 3, 5))
    counts = [len(v) for _k, v in sorted(_by_day(slots).items())]
    # Four days on from 5 March, so the ramp is already at 10 + 4 * 5.
    assert counts[:2] == [30, 35], counts[:2]
    print("ramp_start anchors an undated account: OK")


# ── Gaps ─────────────────────────────────────────────────────────────────────

def test_gaps_stay_inside_the_configured_range():
    slots = _plan(30, hourly_cap_per_account=0, send_min_gap_sec=45,
                  send_max_gap_sec=300)
    stamps = _per_account(slots)["sender@shop.test"]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps, stamps
    assert all(45 <= gap <= 300 for gap in gaps), sorted(gaps)[:5]
    print("gaps inside the configured range: OK")


def test_gaps_are_never_a_fixed_cadence():
    # A metronome is the clearest automation fingerprint a mail provider reads,
    # so this is a product requirement and not a statistical nicety.
    slots = _plan(40, hourly_cap_per_account=0)
    stamps = _per_account(slots)["sender@shop.test"]
    gaps = {b - a for a, b in zip(stamps, stamps[1:])}
    assert len(gaps) > 5, sorted(gaps)
    print("gaps are not a fixed cadence: OK")


# ── Several accounts ─────────────────────────────────────────────────────────

def test_round_robin_shares_the_load():
    accounts = [_account("a@shop.test"), _account("b@shop.test"), _account("c@shop.test")]
    counts = {email: len(stamps) for email, stamps in _per_account(_plan(60, accounts=accounts)).items()}
    assert set(counts) == {"a@shop.test", "b@shop.test", "c@shop.test"}, counts
    assert max(counts.values()) - min(counts.values()) <= 2, counts
    print("round robin across accounts: OK")


def test_disabled_and_capped_accounts_drop_out():
    accounts = [_account("a@shop.test", enabled=False), _account("b@shop.test")]
    assert {email for _ts, email in _plan(20, accounts=accounts)} == {"b@shop.test"}

    accounts = [_account("a@shop.test", daily_cap=3), _account("b@shop.test")]
    slots = _plan(40, accounts=accounts)
    first_day = [email for ts, email in slots if _day(ts) == date(2026, 3, 9)]
    assert first_day.count("a@shop.test") == 3, first_day.count("a@shop.test")
    print("disabled and capped accounts drop out: OK")


# ── Spill ────────────────────────────────────────────────────────────────────

def test_five_hundred_messages_spill_across_working_days():
    slots = _plan(500)
    assert len(slots) == 500
    days = _by_day(slots)
    assert len(days) == 13, sorted(days)          # 500 at 40 a day
    counts = [len(v) for _k, v in sorted(days.items())]
    assert counts[:12] == [40] * 12, counts
    assert counts[-1] == 20, counts

    for day in days:
        assert day.weekday() < 5, day
    # Thirteen working days from a Monday is two weekends away.
    assert min(days) == date(2026, 3, 9) and max(days) == date(2026, 3, 25), sorted(days)
    print("500 messages spill across 13 working days: OK")


def test_spill_respects_two_accounts_together():
    accounts = [_account("a@shop.test"), _account("b@shop.test")]
    days = _by_day(_plan(500, accounts=accounts))
    assert len(days) == 7, sorted(days)            # 80 a day between them
    for stamps in days.values():
        assert len(stamps) <= 80, len(stamps)
    print("two accounts halve the calendar: OK")


# ── Degrading ────────────────────────────────────────────────────────────────

def test_returns_empty_rather_than_raising():
    assert _plan(0) == []
    assert _plan(-5) == []
    assert _plan(10, accounts=[]) == []
    assert _plan(10, accounts=[{"email": "  "}]) == []
    assert C.next_send_times(count=5, accounts=None, settings=None, start_ts=0.0,
                             sent_today_by_account=None) == []
    assert C.next_send_times(count=5, accounts=[{"email": "a@b.c"}], settings={"send_days": "nonsense"},
                             start_ts=_at(), sent_today_by_account={}) != []
    print("degrades to empty instead of raising: OK")


def test_a_broken_window_still_sends_something():
    # An end hour at or before the start hour would otherwise mean silence for
    # ever, which reads as a broken app rather than as a bad setting.
    slots = _plan(5, send_start_hour=10, send_end_hour=10)
    assert len(slots) == 5
    assert all(datetime.fromtimestamp(ts).hour == 10 for ts, _ in slots), slots
    print("a broken window still sends: OK")


# ── Planning a whole campaign ────────────────────────────────────────────────

@contextlib.contextmanager
def temp_db():
    """A throwaway outreach.db, with the settings dir pointed at it as well.

    The redirect matters: a stray default `connect()` would otherwise land in
    the developer's real ~/.mapharvest/outreach.db and queue test mail there.
    """
    original = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            yield DB.connect(os.path.join(tmp, "outreach.db"))
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original


PROFILE = {"company": "Auto Army", "sender_name": "Umar", "sender_title": "",
           "website": "autoarmy.io", "reply_to": "umar@autoarmy.io", "phone": "",
           "postal_address": "1 King St W, Toronto ON", "calendar_link": "",
           "services": [], "proof_points": [], "tone": "direct"}


def _campaign_settings(accounts: int = 1, **overrides) -> dict:
    base = {
        "audit_enabled": False,          # no site crawl: these tests are offline
        "followup_enabled": True, "followup_gap_days": 4, "followup_max_steps": 2,
        "hourly_cap_per_account": 0, "dry_run": True, "sender_profile": PROFILE,
        "smtp_accounts": [_account("s%d@shop.test" % i, imap_enabled=True)
                          for i in range(accounts)],
    }
    base.update(overrides)
    return _settings(**base)


def _plan_campaign(conn, count: int, **overrides):
    """(plan, campaign_id, settings) for `count` fresh leads."""
    settings = _campaign_settings(**overrides)
    campaign_id = DB.create_campaign(conn, "test", "", PROFILE, settings)
    leads = [{"email": "lead%03d@x.test" % i, "name": "Biz %d" % i} for i in range(count)]
    plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads, template_id="",
                           profile=PROFILE, settings=settings, ai=None)
    assert not plan["error"], plan["error"]
    return plan, campaign_id, settings


def _messages(conn, campaign_id: int) -> list:
    return DB._query(conn, "SELECT * FROM messages WHERE campaign_id = ? "
                           "ORDER BY scheduled_at, id", (campaign_id,))


def _lead_id(conn, email: str) -> int:
    return DB._query(conn, "SELECT id FROM leads WHERE email = ?", (email,))[0]["id"]


def test_followups_are_placed_inside_the_daily_cap():
    """A follow-up is another cold email, so it spends the same day's capacity.

    Scheduling it as `first touch + n days` ignored every cap in the app: 200
    leads at 40 a day landed 160 messages on one Monday.
    """
    with temp_db() as conn:
        plan, campaign_id, _s = _plan_campaign(conn, 200)
        rows = _messages(conn, campaign_id)
        assert (plan["queued"], plan["followups"]) == (200, 400)
        assert len(rows) == 600

        by_day: dict = {}
        for row in rows:
            when = datetime.fromtimestamp(row["scheduled_at"])
            assert 9 <= when.hour < 17, when
            assert when.weekday() < 5, when
            by_day.setdefault(when.date(), 0)
            by_day[when.date()] += 1
        assert max(by_day.values()) <= 40, sorted(by_day.items())
        assert len(by_day) == 15, sorted(by_day)     # 600 at 40 a day
        print("follow-ups respect the daily cap: OK")


def test_followups_are_spaced_like_first_touches():
    with temp_db() as conn:
        _plan, campaign_id, _s = _plan_campaign(conn, 200)
        stamps = sorted(row["scheduled_at"] for row in _messages(conn, campaign_id))

        # next_window_open used to collapse every weekend-jittered follow-up onto
        # the same instant — 84 messages on one second, from one account.
        assert len(set(stamps)) == len(stamps), "no two messages may share an instant"

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert min(gaps) >= 60, min(gaps)            # send_min_gap_sec
        assert len(set(gaps)) > 20, "the stream must not become a metronome"
        print("follow-ups are spaced like first touches: OK")


def test_a_followup_never_arrives_before_it_is_due():
    with temp_db() as conn:
        _plan, campaign_id, _s = _plan_campaign(conn, 200)
        rows = _messages(conn, campaign_id)
        first = {row["lead_id"]: row["scheduled_at"] for row in rows if not row["step"]}

        for row in rows:
            if not row["step"]:
                continue
            due = first[row["lead_id"]] + row["step"] * 4 * 86400
            # Minutes of tolerance, not seconds: a pass spanning several days can
            # only be nudged at its start, and past day one the placement is
            # pinned to the window. See `_queue_followups`.
            assert row["scheduled_at"] >= due - 900, (
                row["lead_id"], row["step"], (due - row["scheduled_at"]) / 60.0)

        offsets = {round((row["scheduled_at"] - first[row["lead_id"]]) / 60.0)
                   for row in rows if row["step"] == 1}
        assert len(offsets) > 5, "every chaser landing at the same offset is a pattern"
        print("follow-ups wait their gap: OK")


def test_the_plan_summary_describes_the_whole_queue():
    """The Campaign screen shows this dict as the thing the user approves.

    It used to be built from the step-0 messages alone: five days ending on the
    24th, for a queue that ran eleven days and ended on the 1st.
    """
    with temp_db() as conn:
        plan, campaign_id, _s = _plan_campaign(conn, 200)
        stamps = [row["scheduled_at"] for row in _messages(conn, campaign_id)]

        assert plan["queued"] + plan["followups"] == len(stamps)
        assert plan["first_send"] == min(stamps)
        assert plan["last_send"] == max(stamps)
        assert plan["days"] == len(plan["per_day"])

        real: dict = {}
        for ts in stamps:
            day = datetime.fromtimestamp(ts).date().isoformat()
            real[day] = real.get(day, 0) + 1
        assert plan["per_day"] == real, (sorted(plan["per_day"].items()), sorted(real.items()))
        print("the plan summary matches the queue: OK")


def test_follow_ups_stay_on_the_account_that_opened_the_thread():
    with temp_db() as conn:
        _plan, campaign_id, _s = _plan_campaign(conn, 120, accounts=3)
        rows = _messages(conn, campaign_id)
        opened = {row["lead_id"]: row["account_email"] for row in rows if not row["step"]}
        for row in rows:
            assert row["account_email"] == opened[row["lead_id"]], row
        assert len(set(opened.values())) == 3, "all three accounts should be in play"
        print("follow-ups keep the first touch's account: OK")


def test_planning_can_be_cancelled_between_leads():
    """Quitting mid-plan must not need the thread killed under it."""
    with temp_db() as conn:
        settings = _campaign_settings()

        def _plan_with(name: str, after) -> tuple[dict, int]:
            """Plan 50 leads of its own, stopping after `after` polls. -1 never stops."""
            polls = []

            def should_stop() -> bool:
                polls.append(1)
                return after >= 0 and len(polls) > after

            campaign_id = DB.create_campaign(conn, name, "", PROFILE, settings)
            leads = [{"email": "%s%03d@x.test" % (name, i), "name": "Biz %d" % i}
                     for i in range(50)]
            return C.plan_campaign(conn, campaign_id=campaign_id, leads=leads,
                                   template_id="", profile=PROFILE, settings=settings,
                                   ai=None, should_stop=should_stop), campaign_id

        # Cancelled while rendering: nothing was queued, and nothing half-was.
        plan, campaign_id = _plan_with("early", 10)
        assert plan["cancelled"] is True and plan["queued"] == 0
        assert _messages(conn, campaign_id) == []

        # Cancelled while queueing: what reached the queue stays, and the plan
        # says so rather than claiming the whole list.
        plan, campaign_id = _plan_with("late", 60)
        assert plan["cancelled"] is True
        assert 0 < plan["queued"] < 50, plan["queued"]
        assert len(_messages(conn, campaign_id)) == plan["queued"] + plan["followups"]

        # A hook that never fires changes nothing, and neither does no hook.
        plan, _cid = _plan_with("running", -1)
        assert plan["cancelled"] is False and plan["queued"] == 50

        campaign_id = DB.create_campaign(conn, "whole", "", PROFILE, settings)
        leads = [{"email": "whole%03d@x.test" % i, "name": "Biz %d" % i} for i in range(50)]
        plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads, template_id="",
                               profile=PROFILE, settings=settings, ai=None)
        assert plan["cancelled"] is False and plan["queued"] == 50
        print("planning can be cancelled: OK")


def test_a_dead_host_is_not_fetched_again_by_the_audit():
    """`harvest_site` already proved the host unreachable, and paid for it twice."""
    fetched = []

    def _enrich_fetch(url, timeout, *args, **kwargs):
        fetched.append(("enrich", url))
        return "", "", "unreachable"

    def _audit_fetch(url, timeout, *args, **kwargs):
        fetched.append(("audit", url))
        return "", url, 0, "unreachable"

    original = (E._fetch_page, A._fetch)
    E._fetch_page, A._fetch = _enrich_fetch, _audit_fetch
    try:
        audit, fields = C.audit_lead({"name": "Dead Co", "website": "https://dead.test"},
                                     settings={"enrich_timeout": 8.0, "audit_timeout": 8.0})
    finally:
        E._fetch_page, A._fetch = original

    # Two: the https attempt and the plain-http retry the enricher documents.
    assert fetched == [("enrich", "https://dead.test"), ("enrich", "http://dead.test")], fetched
    assert audit["reachable"] is False and audit["error"] == "unreachable"
    assert fields == {}
    print("a dead host is fetched once, not three times: OK")


# ── The send worker ──────────────────────────────────────────────────────────

class _DeadSender:
    """An SmtpSender that never gets a message through, and counts the tries."""

    def __init__(self, error: str = "RECIPIENT: 550 5.1.1 does not exist"):
        self.error = error
        self.attempts = 0

    def send(self, message):
        self.attempts += 1
        return False, self.error

    def close(self) -> None:
        pass


def _live_settings(**overrides) -> dict:
    """Settings whose window is always open, so a send never waits on the clock."""
    return _campaign_settings(dry_run=False, send_days=[0, 1, 2, 3, 4, 5, 6],
                              send_start_hour=0, send_end_hour=24, **overrides)


def _queued_run(conn, count: int, **overrides):
    """(worker, settings, first queued row) against `count` dead addresses."""
    settings = _live_settings(**overrides)
    campaign_id = DB.create_campaign(conn, "send", "", PROFILE, settings)
    for index in range(count):
        lead_id = DB.upsert_lead(conn, {"email": "dead%03d@x.test" % index,
                                        "name": "Dead %d" % index})
        DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id, "step": 0,
                                "subject": "hello", "body_text": "hello", "body_html": "",
                                "account_email": "s0@shop.test",
                                "scheduled_at": time.time() - 10})
    worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
    worker._ramp_start = date.today()
    return worker, settings, _messages(conn, campaign_id)[0]


def test_a_failed_send_still_spends_its_pacing_gap():
    """The gap belongs to the attempt, not to the outcome.

    Every failure path used to fall through without touching `_next_ok`, so a
    stretch of dead addresses — ordinary in any scraped list — went out as
    back-to-back SMTP transactions against Gmail.
    """
    for error in ("RECIPIENT: 550 5.1.1 does not exist",
                  "OTHER: 554 5.6.0 message rejected",
                  "CONN: 421 4.7.0 try again later",
                  "QUOTA: 550 5.4.5 Daily user sending limit exceeded",
                  "AUTH: Gmail rejected the sign-in"):
        with temp_db() as conn:
            worker, settings, row = _queued_run(conn, 1)
            worker._senders["s0@shop.test"] = _DeadSender(error)
            account = ST.smtp_accounts(settings)[0]

            now = time.time()
            worker._send(conn, row, account, now)

            ready = worker._next_ok.get("s0@shop.test", 0.0)
            assert now + 60 <= ready <= now + 240, (error, ready - now)
            assert worker._pick_account(conn, row, now)[0] is None, error
    print("a failed send spends its pacing gap: OK")


def test_a_run_of_dead_addresses_does_not_burst():
    """The measured symptom: 25 refusals in 0.04 s against a 60 s floor."""
    with temp_db() as conn:
        worker, _s, _row = _queued_run(conn, 25)
        sender = _DeadSender()
        worker._senders["s0@shop.test"] = sender
        naps = []
        worker._nap = naps.append

        now = time.time()
        for _ in range(25):
            due = worker._due(conn, now)
            if not due:
                break
            worker._dispatch(conn, due, now)

        assert sender.attempts == 1, sender.attempts
        assert naps and all(nap > 0 for nap in naps), naps
        print("a run of dead addresses does not burst: OK")


# ── Replies, bounces and unsubscribes ────────────────────────────────────────

@contextlib.contextmanager
def stub_imap(*, bounces=(), unsubscribes=(), replies=()):
    """Stand in for the three IMAP round trips. Yields the log of calls made."""
    calls = []

    def _stub(answer, name):
        def call(email, app_password, since_days=14):
            calls.append((name, email, since_days))
            return list(answer)
        return call

    original = (M.check_bounces, M.check_unsubscribes, M.check_replies)
    M.check_bounces = _stub(bounces, "bounces")
    M.check_unsubscribes = _stub(unsubscribes, "unsubscribes")
    M.check_replies = _stub(replies, "replies")
    try:
        yield calls
    finally:
        M.check_bounces, M.check_unsubscribes, M.check_replies = original


def test_an_unsubscribe_cancels_the_whole_queued_thread():
    """Spec section 13: the +4 day and +8 day chasers are already on the queue."""
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 3, dry_run=False)
        assert {row["step"] for row in _messages(conn, campaign_id)} == {0, 1, 2}

        target = "lead001@x.test"
        worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
        with stub_imap(unsubscribes=[target.upper()]) as calls:
            worker._poll_inboxes(conn, time.time())
        assert calls, "an account with imap_enabled must be read"

        assert DB.is_suppressed(conn, target)
        lead_id = _lead_id(conn, target)
        theirs = [row for row in _messages(conn, campaign_id) if row["lead_id"] == lead_id]
        assert {row["step"] for row in theirs} == {0, 1, 2}
        assert all(row["status"] == "skipped" for row in theirs), theirs
        assert DB.get_lead(conn, lead_id)["status"] == "suppressed"

        others = [row for row in _messages(conn, campaign_id) if row["lead_id"] != lead_id]
        assert all(row["status"] == "queued" for row in others), "only that lead stops"
        print("an unsubscribe cancels the whole thread: OK")


def test_a_hard_bounce_suppresses_the_address():
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 3, dry_run=False)
        target = "lead002@x.test"
        worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
        with stub_imap(bounces=[target]):
            worker._poll_inboxes(conn, time.time())

        assert DB.is_suppressed(conn, target)
        reasons = {row["email"]: row["reason"] for row in DB.suppression_list(conn)}
        assert reasons[target] == "hard bounce", reasons
        theirs = [row for row in _messages(conn, campaign_id)
                  if row["lead_id"] == _lead_id(conn, target)]
        assert all(row["status"] == "skipped" for row in theirs), theirs
        print("a hard bounce suppresses the address: OK")


def test_a_reply_stops_the_sequence_without_suppressing():
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 3, dry_run=False)
        first = next(row for row in _messages(conn, campaign_id) if not row["step"])
        header_id = "<abc123@shop.test>"
        DB.mark_message(conn, first["id"], "sent", sent_at=time.time(), message_id=header_id)

        worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
        with stub_imap(replies=[header_id]):
            worker._poll_inboxes(conn, time.time())

        theirs = {row["step"]: row["status"] for row in _messages(conn, campaign_id)
                  if row["lead_id"] == first["lead_id"]}
        assert theirs == {0: "replied", 1: "skipped", 2: "skipped"}, theirs

        lead = DB.get_lead(conn, first["lead_id"])
        assert lead["status"] == "replied"
        # Somebody who answered is a warm lead, not an opt-out.
        assert not DB.is_suppressed(conn, lead["email"])
        print("a reply stops the sequence: OK")


def test_the_inbox_poll_is_throttled_and_skipped_in_a_dry_run():
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 2, dry_run=False)
        worker = C.OutreachWorker(campaign_id, settings, dry_run=False)

        with stub_imap() as calls:
            now = time.time()
            worker._poll_inboxes(conn, now)
            assert [name for name, _e, _d in calls] == ["bounces", "unsubscribes", "replies"]
            # The first poll of a run covers the app having been shut for a while.
            assert calls[0][2] == worker.IMAP_FIRST_DAYS

            worker._poll_inboxes(conn, now + 1.0)
            worker._poll_inboxes(conn, now + worker.IMAP_POLL_SEC - 1.0)
            assert len(calls) == 3, "three IMAP round trips are not a loop-rate poll"

            worker._poll_inboxes(conn, now + worker.IMAP_POLL_SEC)
            assert len(calls) == 6 and calls[3][2] == worker.IMAP_DAYS, calls

        # A rehearsal promised nothing would leave the machine, mailbox included.
        with stub_imap() as calls:
            C.OutreachWorker(campaign_id, settings, dry_run=True)._poll_inboxes(conn, time.time())
            assert calls == []

        # And an account with IMAP switched off is never opened.
        off = _live_settings(smtp_accounts=[_account("s0@shop.test", imap_enabled=False)])
        with stub_imap() as calls:
            C.OutreachWorker(campaign_id, off, dry_run=False)._poll_inboxes(conn, time.time())
            assert calls == []
        print("the inbox poll is throttled: OK")


# ── A dry run is a rehearsal, not a send ─────────────────────────────────────


class _Clock:
    """A clock the test moves, standing in for `time` inside `core.campaign`.

    A campaign is days of wall-clock time by design, so a rehearsal cannot be
    walked to its end any other way. Only the waiting is taken away: the loop,
    the window checks, the caps and the queue are the shipped ones.
    """

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
def stub_smtp():
    """Stand in for SmtpSender. Yields (accounts opened, messages handed over).

    Constructing one is the only way this app reaches Gmail, so an empty
    `opened` is what proves a rehearsal stayed on the machine.
    """
    opened, sent = [], []

    class _Sender:
        def __init__(self, email, app_password, display_name=""):
            opened.append(email)

        def send(self, message):
            sent.append(message)
            return True, ""

        def close(self) -> None:
            pass

    original = M.SmtpSender
    M.SmtpSender = _Sender
    try:
        yield opened, sent
    finally:
        M.SmtpSender = original


def _run_to_the_end(worker, conn, campaign_id: int, clock) -> list:
    """Run `worker.run()` through the whole queue. Returns (lead_id, step) in order.

    `_nap` steps the clock to the next scheduled message instead of sleeping
    through the days in between; nothing else about the run is touched.
    """
    order: list = []
    worker.message_sent_signal.connect(
        lambda row: order.append((int(row["lead_id"]), int(row["step"]))))
    naps = [0]

    def skip_ahead(seconds: float) -> None:
        naps[0] += 1
        if naps[0] > 500:                      # a loop that will not finish
            worker.stop()
            return
        clock.now += max(0.0, float(seconds))
        pending = [row["scheduled_at"] for row in _messages(conn, campaign_id)
                   if row["status"] == "queued"]
        if pending:
            clock.now = max(clock.now, min(pending))

    worker._nap = skip_ahead
    worker.run()
    return order


def _fits_in_a_test() -> dict:
    """Plan overrides that put a three-step campaign inside a couple of days."""
    return {"send_days": [0, 1, 2, 3, 4, 5, 6], "send_start_hour": 0,
            "send_end_hour": 24, "send_min_gap_sec": 0, "send_max_gap_sec": 0,
            "followup_gap_days": 1, "followup_max_steps": 2}


def test_a_rehearsal_leaves_the_campaign_ready_to_send_for_real():
    """Prepare, rehearse the whole thing, then start it for real.

    A dry run used to mark every message 'sent' with DRY-RUN in the error
    column, and nothing in the app read that column. The next real run found
    all three first touches spent and opened with "Bumping my last email in
    case it landed in a bad week" to three strangers who had heard nothing —
    the safety feature producing the worst output in the product.
    """
    with temp_db() as conn:
        plan, campaign_id, settings = _plan_campaign(conn, 3, **_fits_in_a_test())
        assert (plan["queued"], plan["followups"]) == (3, 6), plan
        columns = ("step", "lead_id", "scheduled_at", "subject", "body_text")
        before = {row["id"]: tuple(row[key] for key in columns)
                  for row in _messages(conn, campaign_id)}
        assert len(before) == 9

        with fake_clock(time.time()) as clock, stub_smtp() as (opened, wire), \
                stub_imap() as polled:
            worker = C.OutreachWorker(campaign_id, settings, dry_run=True)
            rehearsed = _run_to_the_end(worker, conn, campaign_id, clock)

        assert len(rehearsed) == 9, rehearsed
        assert opened == [] and wire == [], "a rehearsal opened a socket"
        assert polled == [], "a rehearsal read a mailbox"
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends") == 0, "real quota was spent"
        assert DB.sent_today(conn, "s0@shop.test", settings.get("send_timezone")) == 0

        rows = _messages(conn, campaign_id)
        assert {row["id"]: tuple(row[key] for key in columns) for row in rows} == before, \
            "the rehearsal did not hand the queue back as it found it"
        assert {row["status"] for row in rows} == {"queued"}, \
            [(row["step"], row["status"]) for row in rows]
        assert all(not row["message_id"] and not row["error"] and not row["sent_at"]
                   for row in rows), rows
        assert DB.get_campaign(conn, campaign_id)["status"] == "scheduled"
        leads = {row["lead_id"] for row in rows}
        assert {DB.get_lead(conn, lead)["status"] for lead in leads} == {"queued"}, \
            "a rehearsal moved a lead to sent"

        # ── and now for real, on that same campaign ──
        with fake_clock(time.time()) as clock, stub_smtp() as (opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, dict(settings, dry_run=False),
                                      dry_run=False)
            sent = _run_to_the_end(worker, conn, campaign_id, clock)

        assert len(sent) == 9 and len(wire) == 9, (sent, len(wire))
        assert sum(1 for _lead, step in sent if step == 0) == 3, sent

        opening: dict = {}
        for index, (lead_id, step) in enumerate(sent):
            opening.setdefault(lead_id, (index, step))
        assert set(opening) == leads, "a lead was left out of the real run"
        for lead_id, (index, step) in opening.items():
            address = DB.get_lead(conn, lead_id)["email"]
            assert step == 0, ("%s was opened with follow-up %d — the first email "
                               "this stranger ever gets is a chaser" % (address, step))
            assert address in wire[index]["To"], (address, wire[index]["To"])
            assert not wire[index]["In-Reply-To"], \
                "a first touch claimed to answer a message that never existed"

        assert {row["status"] for row in _messages(conn, campaign_id)} == {"sent"}
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends") == 9
        assert DB.get_campaign(conn, campaign_id)["status"] == "done"
        print("a rehearsal leaves the campaign ready to send for real: OK")


def test_a_rehearsed_row_is_never_read_as_a_send():
    """Every query that asks "has this gone?" has to answer no."""
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 1)
        rows = _messages(conn, campaign_id)
        first = next(row for row in rows if row["step"] == 0)
        chaser = next(row for row in rows if row["step"] == 1)
        lead_id = first["lead_id"]

        worker = C.OutreachWorker(campaign_id, settings, dry_run=True)
        with stub_smtp() as (opened, wire):
            worker._send(conn, first, ST.smtp_accounts(settings)[0], time.time())
        assert opened == [] and wire == [], "a rehearsal opened a socket"

        row = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (first["id"],))
        assert row["status"] == "rehearsed", row["status"]
        assert row["message_id"] == "" and row["error"] == "", row
        assert DB.campaign_stats(conn, campaign_id)["sent"] == 0
        assert DB.get_lead(conn, lead_id)["status"] == "queued", "the lead reads as mailed"
        assert DB.sent_today(conn, "s0@shop.test", settings.get("send_timezone")) == 0
        assert [e for e in DB.recent_events(conn) if e["kind"] == "sent"] == []
        assert DB.first_touch_message_id(conn, campaign_id, lead_id) == ""
        assert worker._thread_parent(conn, chaser) == "", \
            "a chaser threaded onto a message that was never sent"
        # Pending, not free: a first touch addressed to this lead is still owed,
        # so a second campaign must not queue another one behind it.
        assert lead_id in C._contacted_lead_ids(conn)

        assert worker._restore_rehearsal(conn) == 1
        row = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (first["id"],))
        assert (row["status"], row["sent_at"]) == ("queued", 0.0), row
        due = DB.due_messages(conn, first["scheduled_at"])
        assert first["id"] in [row["id"] for row in due], "the message never came back"
        print("a rehearsed row is never read as a send: OK")


def test_a_rehearsal_that_never_finished_is_put_back_by_the_next_run():
    """The app is closed mid-rehearsal, and the next run is the live one."""
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 2)
        for row in _messages(conn, campaign_id)[:3]:
            DB.mark_message(conn, row["id"], "rehearsed", sent_at=time.time())

        worker = C.OutreachWorker(campaign_id, dict(settings, dry_run=False),
                                  dry_run=False)
        said = []
        worker.log_signal.connect(lambda text, level: said.append(text))
        worker._recover_rehearsed(conn)

        rows = _messages(conn, campaign_id)
        assert {row["status"] for row in rows} == {"queued"}, rows
        assert not any(row["sent_at"] for row in rows), rows
        assert said and "did not finish" in said[0], said

        # An opt-out that lands while a message is rehearsed cancels it, and the
        # restore must not bring it back.
        target = _lead_id(conn, "lead000@x.test")
        theirs = [row for row in rows if row["lead_id"] == target]
        DB.mark_message(conn, theirs[0]["id"], "rehearsed")
        DB.suppress(conn, "lead000@x.test", "unsubscribed")
        assert DB.requeue_rehearsed(conn, campaign_id) == 0
        after = [row for row in _messages(conn, campaign_id) if row["lead_id"] == target]
        assert all(row["status"] == "skipped" for row in after), after
        print("an unfinished rehearsal is put back by the next run: OK")


def test_a_stopped_rehearsal_does_not_open_with_a_follow_up():
    """The measured symptom, end to end, on the ordinary way a dry run is used.

    Nobody watches a rehearsal for four days. The user sees today's first
    touches go by, stops, turns dry run off and starts again — and the run
    before this fix put six messages on the wire, every one of them a chaser:

        lead000@x.test first hears: step 1 —
            'Bumping my last email in case it landed in a bad week.'
    """
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 3, **_fits_in_a_test())

        with fake_clock(time.time()), stub_smtp() as (opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=True)
            rehearsed: list = []
            worker.message_sent_signal.connect(
                lambda row: rehearsed.append((int(row["lead_id"]), int(row["step"]))))
            # Nothing more is due today, so the user stops there.
            worker._nap = lambda seconds: worker.stop()
            worker.run()

        assert [step for _lead, step in rehearsed] == [0, 0, 0], rehearsed
        assert opened == [] and wire == []
        assert {row["status"] for row in _messages(conn, campaign_id)} == {"queued"}, \
            "a stopped rehearsal kept the first touches it walked"

        with fake_clock(time.time()) as clock, stub_smtp() as (opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, dict(settings, dry_run=False),
                                      dry_run=False)
            sent = _run_to_the_end(worker, conn, campaign_id, clock)

        assert len(wire) == 9, "the real run sent %d of 9 messages" % len(wire)
        opening: dict = {}
        for lead_id, step in sent:
            opening.setdefault(lead_id, step)
        assert len(opening) == 3 and set(opening.values()) == {0}, [
            (DB.get_lead(conn, lead)["email"], step) for lead, step in opening.items()]
        print("a stopped rehearsal does not open with a follow-up: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL SCHEDULE TESTS PASSED")
