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

The last section is the second channel. There is one scheduler and one send
loop, so those tests are mostly not about WhatsApp behaving — they are about
WhatsApp still going through the same code with its own, tighter numbers, which
is the property a forked copy would pass while quietly drifting. `WhatsAppSession`
is stubbed the way `SmtpSender` is, and no test here opens a browser.
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
from core import whatsapp as W  # noqa: E402

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
        # The host and port are taken and ignored rather than left out: the
        # worker passes whatever the account row carries, and a stub that
        # refused them would fail as a TypeError swallowed by `run()`, which
        # reads as "the campaign sent nothing" and names nothing.
        def __init__(self, email, app_password, display_name="", timeout=30.0,
                     host="", port=0):
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


# ── Send now ─────────────────────────────────────────────────────────────────

# Saturday 14 March 2026, 22:00. No send day, and hours outside every window,
# so anything that leaves here left because the clock was waived and not
# because the schedule happened to allow it.
SATURDAY_NIGHT = datetime(2026, 3, 14, 22, 0).timestamp()


def test_send_now_does_not_release_a_chaser_before_its_first_touch():
    """The measured symptom: five leads, fifteen messages, all in one second.

    `release_now` brought every queued row forward in `scheduled_at` order, and
    with follow-ups configured that is the whole sequence. Each stranger's
    first three emails arrived together — the cold pitch, "bumping my last
    email", and the closing note — which is the one thing the four-day gap in
    the plan exists to prevent.
    """
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_campaign(conn, 5, dry_run=False)
        assert len(_messages(conn, campaign_id)) == 15

        assert C.release_now(conn, campaign_id, 15) == 5, \
            "a chaser was brought forward past the touch it chases"
        now = time.time()
        released = [row for row in _messages(conn, campaign_id)
                    if row["scheduled_at"] <= now + 1]
        assert {row["step"] for row in released} == {0}, \
            sorted((row["step"], row["scheduled_at"]) for row in released)

        # Pressing it again once the first touches have gone releases the next
        # step, and only the next step: chasing now is then a decision the user
        # takes having watched the first touch leave.
        for row in released:
            DB.mark_message(conn, row["id"], "sent", sent_at=now,
                            message_id="<x%d@shop.test>" % row["id"])
        assert C.release_now(conn, campaign_id, 15) == 5
        again = [row for row in _messages(conn, campaign_id)
                 if row["status"] == "queued" and row["scheduled_at"] <= time.time() + 1]
        assert {row["step"] for row in again} == {1}, sorted(r["step"] for r in again)
        print("send now does not release a chaser before its first touch: OK")


def test_send_now_waives_the_clock_and_keeps_the_daily_cap():
    """The window and the gap are the user's to waive. The daily cap is not.

    The window and the random spacing exist to keep a mailbox looking like a
    person typing, and somebody who has decided to go now can waive that for
    themselves. The daily cap is what stands between them and a suspended
    Gmail account, so nothing on this path can lift it — including a queue
    that has been released past it, which is what this releases.
    """
    with temp_db() as conn:
        with fake_clock(SATURDAY_NIGHT) as clock:
            _plan, campaign_id, settings = _plan_campaign(
                conn, 12, dry_run=False, followup_enabled=False,
                daily_cap_per_account=4, send_min_gap_sec=600, send_max_gap_sec=900)
            settings["smtp_accounts"][0]["daily_cap"] = 4
            assert C.release_now(conn, campaign_id, 12) == 12

            with stub_smtp() as (opened, wire), stub_imap():
                worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                                          ignore_schedule=True)
                worker._nap = lambda seconds: worker.stop()
                worker.run()

            sent = [row for row in _messages(conn, campaign_id)
                    if row["status"] == "sent"]
            assert len(sent) == 4, \
                "a cap of 4 a day let %d messages through" % len(sent)
            assert len(wire) == 4 and opened, (len(wire), opened)

            stamps = sorted(row["sent_at"] for row in sent)
            assert stamps[-1] - stamps[0] < settings["send_min_gap_sec"], \
                "send now sat out the pacing gap it was told to waive"
            assert not any(C.in_send_window(ts, settings) for ts in stamps), \
                "send now waited for the sending window"
            # And the quota ledger the cap is read off agrees with the wire.
            assert DB.sent_today(conn, "s0@shop.test", settings.get("send_timezone"),
                                 now_ts=clock.now) == 4
        print("send now waives the clock and keeps the daily cap: OK")


def test_an_hourly_cap_does_not_send_a_send_now_run_into_next_week():
    """The measured symptom: 30 of 40 messages held 35 hours by a one-hour cap.

    An hourly cap holding the queue was treated exactly like a spent day: the
    backlog went through `_replan`, which places into the next *window* open,
    and on a Saturday evening that is Monday morning. A user who had just
    pressed Send now watched ten messages leave and the rest go quiet for a day
    and a half, under a line that said every account was at its cap for today
    while 35 of each account's 40 a day were still unspent.
    """
    with temp_db() as conn:
        with fake_clock(SATURDAY_NIGHT) as clock:
            _plan, campaign_id, settings = _plan_campaign(
                conn, 20, dry_run=False, followup_enabled=False,
                hourly_cap_per_account=5, daily_cap_per_account=40)
            assert C.release_now(conn, campaign_id, 20) == 20

            said: list = []
            with stub_smtp() as (_opened, wire), stub_imap():
                worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                                          ignore_schedule=True)
                worker.log_signal.connect(lambda text, level: said.append(text))
                worker._nap = lambda seconds: worker.stop()
                worker.run()

            rows = _messages(conn, campaign_id)
            assert len([row for row in rows if row["status"] == "sent"]) == 5
            held = [row for row in rows if row["status"] == "queued"]
            assert len(held) == 15
            assert all(row["scheduled_at"] <= SATURDAY_NIGHT + 1 for row in held), \
                "the hourly cap pushed the queue into the next window"

            hourly = [text for text in said if "hourly limit" in text]
            assert len(hourly) == 1, said
            assert C._clock(clock.now + C._HOUR_SEC) in hourly[0], hourly[0]
            assert not any("allowance for today" in text for text in said), \
                "an hourly hold was reported as the day's allowance being spent"
        print("an hourly cap does not send a send-now run into next week: OK")


# ── A held queue must never look like a dead button ──────────────────────────

def test_a_queue_held_by_the_window_says_so():
    """The commonest "glitch" report there is, and it was entirely silent.

    A campaign prepared on a Saturday sends on Monday, which is correct. Press
    Start and the button greys out, the counter sits at zero, and the log gets
    nothing at all: measured before this, twenty passes of the send loop —
    ten minutes of a user watching — wrote one line, and that line was
    "Stopped", when they gave up.
    """
    with temp_db() as conn:
        with fake_clock(SATURDAY_NIGHT) as clock:
            _plan, campaign_id, settings = _plan_campaign(
                conn, 8, dry_run=False, followup_enabled=False)

            said: list = []
            with stub_smtp() as (opened, wire), stub_imap():
                worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
                worker.log_signal.connect(lambda text, level: said.append(text))
                naps = [0]

                def nap(seconds: float) -> None:
                    naps[0] += 1
                    clock.now += max(1.0, float(seconds))
                    if naps[0] >= 20:
                        worker.stop()

                worker._nap = nap
                worker.run()

            assert wire == [] and opened == [], "the window did not hold the queue"
            held = [text for text in said if "Outside the sending window" in text]
            # Once, not once per nap: the hold lasts hours and the sends either
            # side of it have to stay readable.
            assert len(held) == 1, "%d hold lines in %d" % (len(held), len(said))
            assert "8 message(s)" in held[0], held[0]
            assert C._clock(C.next_window_open(SATURDAY_NIGHT, settings)) in held[0], \
                held[0]
        print("a queue held by the window says so: OK")


def test_a_queue_that_can_never_go_out_ends_the_run():
    """A crash inside `suppress` leaves a campaign that reads as busy for ever.

    `suppress` writes the address and then cancels that lead's queued rows, and
    the two are separate statements. Die in between and `campaign_stats` counts
    a queue while `due_messages` — which refuses a suppressed lead — hands back
    nothing, for ever. That ran here as a thirty-second nap repeated until the
    app was closed, with nothing in the log and the screen saying "Sending".
    """
    with temp_db() as conn:
        worker, _settings, _row = _queued_run(conn, 3)
        campaign_id = worker.campaign_id
        for lead in DB.list_leads(conn):
            DB._write(conn, "INSERT INTO suppression (email, reason, added_at) "
                            "VALUES (?, ?, ?)", (lead["email"], "unsubscribed",
                                                 time.time()))

        said: list = []
        worker.log_signal.connect(lambda text, level: said.append(text))
        naps = [0]

        def nap(seconds: float) -> None:
            naps[0] += 1
            if naps[0] > 5:
                worker.stop()

        worker._nap = nap
        with stub_smtp() as (opened, wire), stub_imap():
            worker.run()

        assert wire == [] and opened == [], "a message went to a suppressed address"
        assert naps[0] == 0, "the loop napped %d times over a queue that can never go" \
                             % naps[0]
        assert any("can ever go out" in text for text in said), said
        assert DB.get_campaign(conn, campaign_id)["status"] == "stopped"
        assert {row["status"] for row in _messages(conn, campaign_id)} == {"queued"}, \
            "the run rewrote a queue it had decided it could not send"
        print("a queue that can never go out ends the run: OK")


# ── Pause, resume, stop ──────────────────────────────────────────────────────

def test_pause_holds_the_queue_and_resume_finishes_it():
    """Pause has to land between messages and give the queue back untouched."""
    with temp_db() as conn:
        worker, _settings, _row = _queued_run(conn, 8, send_min_gap_sec=0,
                                              send_max_gap_sec=0)
        campaign_id = worker.campaign_id
        sent: list = []
        state = {"naps": 0, "at_pause": 0, "at_resume": 0, "while_paused": 0}

        def note(row: dict) -> None:
            sent.append(int(row["id"]))
            if worker._paused:
                state["while_paused"] += 1
            elif len(sent) == 3 and not state["at_pause"]:
                worker.pause()
                state["at_pause"] = len(sent)

        worker.message_sent_signal.connect(note)

        def nap(seconds: float) -> None:
            state["naps"] += 1
            if worker._paused and state["naps"] >= 4:
                state["at_resume"] = len(sent)
                worker.resume()
            if state["naps"] > 200:
                worker.stop()

        worker._nap = nap
        with stub_smtp() as (_opened, wire), stub_imap():
            worker.run()

        assert (state["at_pause"], state["at_resume"]) == (3, 3), state
        assert state["while_paused"] == 0, "a message left while the run was paused"
        assert len(wire) == 8, "%d of 8 went out" % len(wire)
        assert {row["status"] for row in _messages(conn, campaign_id)} == {"sent"}
        assert DB.get_campaign(conn, campaign_id)["status"] == "done"
        print("pause holds the queue and resume finishes it: OK")


def test_stop_lands_promptly_and_leaves_the_queue_coherent():
    """Stop finishes the message in flight; everything else stays queued.

    Nothing may be left in `sending`: that status means "handed to a server,
    outcome unknown", and the next run writes every one of those off as sent
    rather than risk a second copy. A stop that produced them would cost the
    campaign a message per press.
    """
    with temp_db() as conn:
        worker, _settings, _row = _queued_run(conn, 9, send_min_gap_sec=0,
                                              send_max_gap_sec=0)
        campaign_id = worker.campaign_id
        sent: list = []

        def note(row: dict) -> None:
            sent.append(int(row["id"]))
            if len(sent) == 3:
                worker.stop()

        worker.message_sent_signal.connect(note)
        worker._nap = lambda seconds: None
        with stub_smtp() as (_opened, wire), stub_imap():
            worker.run()

        counts: dict = {}
        for row in _messages(conn, campaign_id):
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        assert len(wire) == 3, "%d messages left after Stop" % len(wire)
        assert counts == {"sent": 3, "queued": 6}, counts
        assert DB.get_campaign(conn, campaign_id)["status"] == "stopped"
        print("stop lands promptly and leaves the queue coherent: OK")


# ── Every SMTP error class ───────────────────────────────────────────────────

def test_every_smtp_error_class_lands_on_the_right_outcome():
    """AUTH and QUOTA bench the account, RECIPIENT drops the lead, CONN retries.

    One table rather than five tests, because what matters is that no two of
    them are handled the same way: benching an account over one dead address
    stalls a campaign for a day, and skipping a lead over a quota error loses
    it for good. `core.mailer` classifies; this is the half that acts on it.
    """
    outcomes = {
        # error -> (message status, the account is benched, the lead is suppressed)
        "AUTH: Gmail rejected the sign-in": ("queued", True, False),
        "QUOTA: 550 5.4.5 Daily user sending limit exceeded": ("queued", True, False),
        "RECIPIENT: 550 5.1.1 does not exist": ("failed", False, True),
        "CONN: 421 4.7.0 try again later": ("queued", False, False),
        "OTHER: 554 5.6.0 message rejected": ("failed", False, False),
    }
    for error, expected in outcomes.items():
        with temp_db() as conn:
            worker, settings, row = _queued_run(conn, 2)
            worker._senders["s0@shop.test"] = _DeadSender(error)
            now = time.time()

            worker._send(conn, row, ST.smtp_accounts(settings)[0], now)

            after = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row["id"],))
            lead = DB.get_lead(conn, row["lead_id"])
            got = (after["status"], bool(worker._stopped),
                   DB.is_suppressed(conn, lead["email"]))
            assert got == expected, (error, got, expected)
            # Pacing and quota belong to the attempt, not to its outcome: a
            # provider reads a refusal exactly as plainly as a delivery.
            assert worker._next_ok.get("s0@shop.test", 0.0) > now, error
            assert DB.sent_today(conn, "s0@shop.test", settings.get("send_timezone"),
                                 now_ts=now) == 1, error
    print("every SMTP error class lands on the right outcome: OK")


def test_a_backlog_left_by_a_closed_app_replans_inside_the_caps():
    """A week shut. Everything is overdue at once and must not go out at once."""
    monday = datetime(2026, 3, 9, 9, 30).timestamp()
    week_back = monday - 7 * 86400
    with temp_db() as conn:
        with fake_clock(week_back):
            _plan, campaign_id, settings = _plan_campaign(
                conn, 24, dry_run=False, followup_enabled=False,
                daily_cap_per_account=5)
        settings["smtp_accounts"][0]["daily_cap"] = 5
        DB._write(conn, "UPDATE messages SET scheduled_at = ? WHERE campaign_id = ?",
                  (week_back, campaign_id))

        with fake_clock(monday) as clock, stub_smtp() as (_opened, wire), stub_imap():
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False)
            _run_to_the_end(worker, conn, campaign_id, clock)

        rows = _messages(conn, campaign_id)
        assert {row["status"] for row in rows} == {"sent"}, \
            sorted({row["status"] for row in rows})
        assert len(wire) == 24, len(wire)

        per_day: dict = {}
        for row in rows:
            assert C.in_send_window(row["sent_at"], settings), row["sent_at"]
            day = datetime.fromtimestamp(row["sent_at"]).date()
            per_day[day] = per_day.get(day, 0) + 1
        assert max(per_day.values()) <= 5, sorted(per_day.items())

        stamps = sorted(row["sent_at"] for row in rows)
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])
                if _day(earlier) == _day(later)]
        assert min(gaps) >= settings["send_min_gap_sec"], min(gaps)
        print("a backlog left by a closed app replans inside the caps: OK")


# ── WhatsApp: one scheduler, two channels ────────────────────────────────────
#
# Nothing below opens a browser, a WhatsApp session or a socket. `_StubSession`
# stands in for `core.whatsapp.WhatsAppSession` exactly as `stub_smtp` stands in
# for `SmtpSender`, and every test that could have opened one asserts that it
# did not.
#
# What is being guarded is narrower than it looks. The scheduler, the caps, the
# warm-up, the pacing, the replan and the crash-resume are one implementation
# shared by both channels, so most of these tests are not asking "does WhatsApp
# work" — they are asking "is WhatsApp still going through the same code, with
# its own numbers". A fork would pass a test that only checked the output.


class _StubSession:
    """A WhatsAppSession that never opens anything, and counts what it was asked.

    `results` is the queue of (ok, error) answers `send` hands back, one per
    call, falling through to success once it runs out — so a test names the
    failure it is about and nothing else.
    """

    def __init__(self, results=None, status="ready"):
        self.sent: list = []          # (phone, text) in order
        self.started = 0
        self.closed = 0
        self.replies: list = []
        self._results = list(results or [])
        self._status = status

    def start(self):
        self.started += 1
        return True, ""

    def status(self):
        return self._status

    @property
    def banned(self) -> bool:
        return False

    def send(self, phone: str, text: str):
        self.sent.append((phone, text))
        return self._results.pop(0) if self._results else (True, "")

    def unread_replies(self, since_ts: float) -> list:
        return list(self.replies)

    def close(self) -> None:
        self.closed += 1


@contextlib.contextmanager
def stub_wa_session():
    """Stand in for the `WhatsAppSession` constructor itself.

    Yields the list of sessions built through it, so a test can prove that a
    run opened one — or, far more often, that it opened none at all. Building
    one is the only way this app reaches WhatsApp, exactly as constructing an
    `SmtpSender` is the only way it reaches Gmail.
    """
    built: list = []

    def _build(*args, **kwargs):
        session = _StubSession()
        built.append(session)
        return session

    original = W.WhatsAppSession
    W.WhatsAppSession = _build
    try:
        yield built
    finally:
        W.WhatsAppSession = original


def _wa_settings(**overrides) -> dict:
    """Settings for a WhatsApp campaign — built on top of the email ones.

    Deliberately not a WhatsApp-only dict. The app holds one settings file with
    two sets of numbers in it, and the mistake worth catching is the WhatsApp
    path reading an email number: every email key here is left at a value that
    would be visibly wrong if it leaked through (40 a day, a 60-second floor,
    two chasers, a 09:00 window, dry run on).
    """
    base = dict(_campaign_settings(),
                wa_enabled=True, wa_default_region="CA", wa_dry_run=False,
                wa_send_days=[0, 1, 2, 3, 4], wa_send_start_hour=10,
                wa_send_end_hour=19, wa_daily_cap=30, wa_hourly_cap=8,
                wa_min_gap_sec=90, wa_max_gap_sec=300,
                wa_warmup_enabled=False, wa_warmup_start=5, wa_warmup_step=3,
                wa_warmup_max=30, wa_followup_enabled=True,
                wa_followup_gap_days=3, wa_followup_max_steps=1,
                wa_opt_out_words=["stop", "unsubscribe", "remove me",
                                  "do not message"])
    base.update(overrides)
    return base


def _wa_leads(count: int, prefix: str = "wa", first: int = 100) -> list:
    """Leads carrying a number this app can address."""
    return [{"email": "%s%03d@x.test" % (prefix, i), "name": "Biz %d" % i,
             "phone": "+1 416-555-%04d" % (first + i)} for i in range(count)]


def _plan_wa(conn, count: int, leads=None, allow_cross_channel: bool = False,
             **overrides):
    """(plan, campaign_id, settings) for a WhatsApp campaign."""
    settings = _wa_settings(**overrides)
    campaign_id = DB.create_campaign(conn, "wa", "", PROFILE, settings)
    plan = C.plan_campaign(conn, campaign_id=campaign_id,
                           leads=leads if leads is not None else _wa_leads(count),
                           template_id="", profile=PROFILE, settings=settings,
                           ai=None, channel=C.WHATSAPP,
                           allow_cross_channel=allow_cross_channel)
    return plan, campaign_id, settings


def _wa_account(settings: dict) -> dict:
    return C.channel_accounts(settings, C.WHATSAPP)[0]


def _queued_wa_run(conn, count: int, session=None, **overrides):
    """(worker, settings, first queued row) for `count` WhatsApp messages.

    The window is opened right round so a send never waits on the clock, which
    leaves whatever the test is about as the only thing that can hold it.
    """
    settings = _wa_settings(**overrides)
    settings.update(wa_send_days=[0, 1, 2, 3, 4, 5, 6], wa_send_start_hour=0,
                    wa_send_end_hour=24)
    campaign_id = DB.create_campaign(conn, "wa-send", "", PROFILE, settings)
    for index in range(count):
        lead_id = DB.upsert_lead(conn, {"email": "was%03d@x.test" % index,
                                        "name": "Biz %d" % index,
                                        "phone": "+1 416-555-%04d" % (200 + index)})
        DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id,
                                "step": 0, "subject": "", "body_text": "hello there",
                                "body_html": "", "account_email": C.WA_ACCOUNT,
                                "channel": DB.WHATSAPP,
                                "scheduled_at": time.time() - 10})
    worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                              channel=C.WHATSAPP,
                              session=session if session is not None else _StubSession())
    worker._ramp_start = date.today()
    return worker, settings, _messages(conn, campaign_id)[0]


def test_the_whatsapp_numbers_are_the_tight_ones_and_stay_tight():
    """Two things, and the second is the one that will fail one day.

    `core.campaign` carries its own fallback for every WhatsApp setting so that
    a settings dict missing one cannot quietly fall through to the *email* value
    sitting under the same name — 40 a day and a 60-second floor for a channel
    that bans numbers for exactly that. Repeating the numbers means they can
    drift from `core.settings`, so the drift is asserted here rather than
    discovered on a banned account.

    And every WhatsApp limit is checked against its email counterpart, because
    "deliberately tighter" is a promise the defaults have to keep. Softening one
    fails this test, which is the point of it.
    """
    for key, (wa_key, fallback) in C._WA_KEYS.items():
        assert wa_key in ST.DEFAULT_SETTINGS, "%s is not a real setting" % wa_key
        assert ST.DEFAULT_SETTINGS[wa_key] == fallback, (
            "%s ships as %r and this module falls back to %r"
            % (wa_key, ST.DEFAULT_SETTINGS[wa_key], fallback))
        assert key in ST.DEFAULT_SETTINGS, "%s is not the email key it stands in for" % key

    d = ST.DEFAULT_SETTINGS
    assert d["wa_daily_cap"] < d["daily_cap_per_account"]
    assert d["wa_hourly_cap"] < d["hourly_cap_per_account"]
    assert d["wa_min_gap_sec"] > d["send_min_gap_sec"]
    assert d["wa_max_gap_sec"] > d["send_max_gap_sec"]
    assert d["wa_send_start_hour"] > d["send_start_hour"], \
        "a WhatsApp message at 09:00 reads worse than an email does"
    assert d["wa_followup_max_steps"] < d["followup_max_steps"]
    assert d["wa_warmup_start"] < d["warmup_start"]
    assert d["wa_dry_run"] is True, "a fresh install must never surprise-send"

    # A dict that knows only the email numbers must come back with none of them.
    thin = {"daily_cap_per_account": 40, "hourly_cap_per_account": 12,
            "send_min_gap_sec": 60, "send_max_gap_sec": 240, "send_start_hour": 9,
            "followup_max_steps": 2, "dry_run": False}
    translated = C.channel_settings(thin, C.WHATSAPP)
    for key, (wa_key, _fallback) in C._WA_KEYS.items():
        assert translated[key] == d[wa_key], (key, translated[key], d[wa_key])
    assert translated["dry_run"] is True, "a missing wa_dry_run read as email's False"
    # And email is handed back exactly what it gave, not a copy of it.
    assert C.channel_settings(thin, C.EMAIL) is thin
    print("the WhatsApp numbers are the tight ones: OK")


def test_one_scheduler_places_whatsapp_inside_whatsapp_limits():
    """The whole of section 5 in one assertion set: no fork, and no leakage.

    `next_send_times` is handed the WhatsApp settings and nothing else changes.
    Every rule it enforces for email — the window, the days, the daily cap, the
    minimum gap, the spill across days — therefore holds for WhatsApp with its
    own numbers, and each one below would fail if the email number had leaked.
    """
    settings = _wa_settings()
    slots = C.next_send_times(count=100, accounts=C.channel_accounts(settings, C.WHATSAPP),
                              settings=C.channel_settings(settings, C.WHATSAPP),
                              start_ts=_at(), sent_today_by_account={}, seed=3)
    assert len(slots) == 100

    for ts, account in slots:
        when = datetime.fromtimestamp(ts)
        assert 10 <= when.hour < 19, "%s is outside WhatsApp's own window" % when
        assert when.weekday() < 5, when
        assert account == C.WA_ACCOUNT, account

    by_day = _by_day(slots)
    assert max(len(stamps) for stamps in by_day.values()) <= 30, \
        "email's 40 a day leaked into the WhatsApp cap"
    assert len(by_day) == 4, sorted(by_day)          # 100 at 30 a day, spilled
    assert datetime.fromtimestamp(slots[0][0]).hour == 10, \
        "the first send opened at email's 09:00"

    for stamps in by_day.values():
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert not gaps or min(gaps) >= 90, "email's 60-second floor leaked"
        # Eight an hour, not email's twelve. Read as a rolling window rather
        # than a clock hour, which is what the cap means.
        for index, stamp in enumerate(stamps):
            hour = [t for t in stamps[:index + 1] if t > stamp - 3600]
            assert len(hour) <= 8, (len(hour), stamp)
    print("one scheduler places WhatsApp inside WhatsApp's limits: OK")


def test_a_fresh_whatsapp_number_ramps_from_five():
    """The warm-up is the same function, counting from the WhatsApp start."""
    settings = _wa_settings(wa_warmup_enabled=True)
    slots = C.next_send_times(count=60, accounts=C.channel_accounts(settings, C.WHATSAPP),
                              settings=C.channel_settings(settings, C.WHATSAPP),
                              start_ts=_at(), sent_today_by_account={}, seed=5)
    counts = [len(stamps) for _day_, stamps in sorted(_by_day(slots).items())]
    assert counts[:5] == [5, 8, 11, 14, 17], counts
    print("a fresh WhatsApp number ramps from five: OK")


def test_whatsapp_volume_does_not_spend_the_email_allowance():
    """Two ledgers, or one channel silently retires the other for the day."""
    with temp_db() as conn:
        now = time.time()
        for _ in range(40):
            DB.record_send(conn, "s0@shop.test", now)
        for _ in range(5):
            DB.record_send(conn, C.WA_ACCOUNT, now, channel=DB.WHATSAPP)

        settings = _wa_settings()
        assert C._sent_today(conn, [{"email": "s0@shop.test"}], settings, now,
                             C.EMAIL) == {"s0@shop.test": 40}
        assert C._sent_today(conn, C.channel_accounts(settings, C.WHATSAPP), settings,
                             now, C.WHATSAPP) == {C.WA_ACCOUNT: 5}

        worker = C.OutreachWorker(0, settings, dry_run=False, channel=C.WHATSAPP,
                                  session=_StubSession())
        worker._ramp_start = date.today()
        assert worker._daily_room(conn, _wa_account(settings), now) == 25, \
            "the WhatsApp cap was spent by the email run"

        # And the reverse: forty WhatsApp messages must not close the mailbox.
        for _ in range(40):
            DB.record_send(conn, C.WA_ACCOUNT, now, channel=DB.WHATSAPP)
        email_worker = C.OutreachWorker(0, settings, dry_run=False)
        email_worker._ramp_start = date.today()
        assert email_worker._daily_room(conn, ST.smtp_accounts(settings)[0], now) == 0
        assert DB.sent_today(conn, "s0@shop.test", "local", now_ts=now) == 40
    print("WhatsApp volume does not spend the email allowance: OK")


def test_the_whatsapp_copy_comes_from_wa_templates():
    """The planner renders through an adapter, and says so when there is none."""
    copy = C.copy_for(C.WHATSAPP)
    assert copy is not None and copy.channel == C.WHATSAPP
    assert copy.for_step(0), "no first-touch WhatsApp template to write from"

    ctx = {"business_name": "Biz", "sender_name": "Umar", "company": "Auto Army"}
    subject, text, html = copy.render(copy.for_step(0)[0], ctx)
    assert (subject, html) == ("", ""), "a chat bubble grew a subject line"
    assert copy.usable(subject, text) and not copy.usable("", "")
    # Email keeps its own rule: a body without a subject is not a usable email.
    assert not C.copy_for(C.EMAIL).usable("", "body")

    with temp_db() as conn:
        original = C.copy_for
        C.copy_for = lambda channel=C.EMAIL: (
            None if channel == C.WHATSAPP else original(channel))
        try:
            plan, campaign_id, _s = _plan_wa(conn, 2)
            email_plan, _cid, _s2 = _plan_campaign(conn, 1)
        finally:
            C.copy_for = original
        assert plan["queued"] == 0 and "missing" in plan["error"], plan["error"]
        assert _messages(conn, campaign_id) == []
        assert email_plan["queued"] == 1, "email stopped working without wa_templates"
    print("the WhatsApp copy comes from wa_templates: OK")


def test_a_whatsapp_campaign_is_queued_on_its_own_channel():
    with temp_db() as conn:
        plan, campaign_id, _s = _plan_wa(conn, 20)
        assert plan["channel"] == C.WHATSAPP
        # One chaser, not email's two.
        assert (plan["queued"], plan["followups"]) == (20, 20), plan

        rows = _messages(conn, campaign_id)
        assert len(rows) == 40
        by_day: dict = {}
        for row in rows:
            assert row["channel"] == C.WHATSAPP, row
            assert row["account_email"] == C.WA_ACCOUNT, row
            assert row["subject"] == "" and row["body_html"] == "", row
            assert row["body_text"], row
            assert "STOP" in row["body_text"], \
                "a queued WhatsApp message with no opt-out line: %r" % row["body_text"]
            when = datetime.fromtimestamp(row["scheduled_at"])
            assert 10 <= when.hour < 19 and when.weekday() < 5, when
            by_day[when.date()] = by_day.get(when.date(), 0) + 1
        assert max(by_day.values()) <= 30, sorted(by_day.items())

        first = {row["lead_id"]: row["scheduled_at"] for row in rows if not row["step"]}
        for row in rows:
            if row["step"]:
                # Three days, which is WhatsApp's gap — email's is four.
                due = first[row["lead_id"]] + 3 * 86400
                assert row["scheduled_at"] >= due - 900, (row["step"], row["lead_id"])
        assert plan["queued"] + plan["followups"] == len(rows)
        assert plan["per_day"] and plan["days"] == len(plan["per_day"])

        # The channel being switched off is a refusal that says so, not a
        # campaign that quietly queues and then cannot send.
        off, off_id, _s2 = _plan_wa(conn, 0, leads=_wa_leads(2, "off", 700),
                                    wa_enabled=False)
        assert off["queued"] == 0 and "switched off" in off["error"], off["error"]
        assert _messages(conn, off_id) == []
    print("a WhatsApp campaign is queued on its own channel: OK")


def test_a_lead_with_no_usable_number_is_counted_before_the_user_commits():
    """An unqualified number is refused, never guessed — and the plan says so."""
    leads = [{"email": "good@x.test", "name": "Good", "phone": "+1 416-555-0142"},
             {"email": "local@x.test", "name": "Local", "phone": "(416) 555-0143"},
             {"email": "none@x.test", "name": "None", "phone": ""},
             {"email": "short@x.test", "name": "Short", "phone": "555-01"}]

    with temp_db() as conn:
        plan, campaign_id, _s = _plan_wa(conn, 0, leads=leads, wa_default_region="")
        assert plan["queued"] == 1, plan["skip_reasons"]
        assert plan["no_phone"] == 3, plan["skip_reasons"]
        assert any("country code" in reason for reason in plan["skip_reasons"]), \
            plan["skip_reasons"]
        assert plan["warnings"] and "region" in plan["warnings"][0]
        queued = {DB.get_lead(conn, row["lead_id"])["email"]
                  for row in _messages(conn, campaign_id)}
        assert queued == {"good@x.test"}, queued

    # The same list with a region set completes the local number instead.
    with temp_db() as conn:
        plan, campaign_id, _s = _plan_wa(conn, 0, leads=leads, wa_default_region="CA")
        assert plan["queued"] == 2 and plan["no_phone"] == 2, plan["skip_reasons"]
        queued = {DB.get_lead(conn, row["lead_id"])["email"]
                  for row in _messages(conn, campaign_id)}
        assert queued == {"good@x.test", "local@x.test"}, queued
        assert not plan["warnings"], plan["warnings"]
    print("a lead with no usable number is counted before the user commits: OK")


def test_one_first_touch_per_number_and_not_merely_per_lead():
    """Two branches of one business share a switchboard, and are two leads.

    The lead pool cannot dedupe them — they have different addresses and are
    genuinely two records — so on WhatsApp the rule has to be asked of the
    number. Without this the same phone gets two cold pitches from the same
    sender, which is the report that ends a number.
    """
    with temp_db() as conn:
        shared = "+1 416-555-0900"
        leads = [{"email": "north@x.test", "name": "North branch", "phone": shared},
                 {"email": "south@x.test", "name": "South branch",
                  "phone": "(416) 555-0900"},          # the same number, written locally
                 {"email": "other@x.test", "name": "Elsewhere",
                  "phone": "+1 416-555-0901"}]

        plan, campaign_id, _s = _plan_wa(conn, 0, leads=leads)
        assert plan["queued"] == 2, plan["skip_reasons"]
        assert "another lead at this number has already been messaged" in plan["skip_reasons"]
        queued = {DB.get_lead(conn, row["lead_id"])["email"]
                  for row in _messages(conn, campaign_id)}
        assert queued == {"north@x.test", "other@x.test"}, queued

        # And across campaigns, not only inside one batch.
        second, _cid, _s2 = _plan_wa(conn, 0, leads=[leads[1]])
        assert second["queued"] == 0, second["skip_reasons"]

        # Email is untouched by this: two branches at one switchboard are two
        # mailboxes, and one email each is one email each.
        settings = _campaign_settings()
        mail_id = DB.create_campaign(conn, "mail", "", PROFILE, settings)
        mail = C.plan_campaign(conn, campaign_id=mail_id, leads=leads,
                               template_id="", profile=PROFILE, settings=settings,
                               ai=None, allow_cross_channel=True)
        assert mail["queued"] == 3, mail["skip_reasons"]
    print("one first touch per number, not merely per lead: OK")


def test_a_lead_reached_on_one_channel_is_not_reached_on_the_other():
    """Being emailed on Tuesday and WhatsApped on Thursday is what gets reported.

    Both directions, because the rule is about the person and not about which
    channel happened to go first — and the override, because "unless the user
    explicitly starts one for it" has to be reachable or the rule is a wall.
    """
    with temp_db() as conn:
        leads = _wa_leads(3)
        settings = _campaign_settings()
        campaign_id = DB.create_campaign(conn, "mail", "", PROFILE, settings)
        first = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads,
                                template_id="", profile=PROFILE, settings=settings,
                                ai=None)
        assert first["queued"] == 3

        blocked, wa_id, _s = _plan_wa(conn, 0, leads=leads)
        assert blocked["queued"] == 0 and blocked["other_channel"] == 3, blocked
        assert "already contacted on the other channel" in blocked["skip_reasons"]
        assert _messages(conn, wa_id) == []

        allowed, wa_id, _s = _plan_wa(conn, 0, leads=leads, allow_cross_channel=True)
        assert allowed["queued"] == 3, allowed["skip_reasons"]

    with temp_db() as conn:                    # and the other way round
        leads = _wa_leads(2)
        _plan, _wa_id, _s = _plan_wa(conn, 0, leads=leads)
        settings = _campaign_settings()
        campaign_id = DB.create_campaign(conn, "mail", "", PROFILE, settings)
        second = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads,
                                 template_id="", profile=PROFILE, settings=settings,
                                 ai=None)
        assert second["queued"] == 0 and second["other_channel"] == 2, second
        assert _messages(conn, campaign_id) == []
    print("a lead reached on one channel is not reached on the other: OK")


def test_a_failed_whatsapp_send_still_spends_its_pacing_gap():
    """The email path's rule, on the channel that punishes breaking it harder.

    A refusal leaves the browser free instantly, so without this a run of
    numbers that are not on WhatsApp is a burst of chat-opens in one second —
    which is a far clearer bot signature than the messages would have been.
    """
    errors = ("RECIPIENT: is not on WhatsApp",
              "CONN: chrome not reachable",
              "AUTH: scan the QR code to log in",
              "OTHER: something went wrong",
              "BANNED: your phone number is banned")
    for error in errors:
        with temp_db() as conn:
            worker, settings, row = _queued_wa_run(
                conn, 1, _StubSession([(False, error)]))
            now = time.time()
            worker._send(conn, row, _wa_account(settings), now)

            ready = worker._next_ok.get(C.WA_ACCOUNT, 0.0)
            assert now + 90 <= ready <= now + 300, (error, ready - now)
            assert worker._pick_account(conn, row, now)[0] is None, error
            # The cap counts transactions, not deliveries: WhatsApp saw this one.
            assert DB.sent_today(conn, C.WA_ACCOUNT, "local", now_ts=now,
                                 channel=DB.WHATSAPP) == 1, error

    # RATE is the one that buys more than a pacing gap, and must.
    with temp_db() as conn:
        worker, settings, row = _queued_wa_run(
            conn, 1, _StubSession([(False, "RATE: too many messages")]))
        now = time.time()
        worker._send(conn, row, _wa_account(settings), now)
        assert worker._next_ok[C.WA_ACCOUNT] >= now + worker.RATE_BACKOFF_SEC
    print("a failed WhatsApp send still spends its pacing gap: OK")


def test_every_whatsapp_error_class_lands_on_the_right_outcome():
    """The email table's counterpart, plus the two failures email has not got."""
    outcomes = {
        # error -> (message status, the number is benched, the run stops)
        "AUTH: scan the QR code to log in": ("queued", True, True),
        "RATE: too many messages": ("queued", False, False),
        "RECIPIENT: is not on WhatsApp": ("failed", False, False),
        "CONN: chrome not reachable": ("queued", False, False),
        "OTHER: something went wrong": ("failed", False, False),
        "BANNED: your phone number is banned": ("queued", False, True),
    }
    for error, expected in outcomes.items():
        with temp_db() as conn:
            worker, settings, row = _queued_wa_run(
                conn, 2, _StubSession([(False, error)]))
            worker._send(conn, row, _wa_account(settings), time.time())

            after = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row["id"],))
            got = (after["status"], bool(worker._stopped), not worker._running)
            assert got == expected, (error, got, expected)
    print("every WhatsApp error class lands on the right outcome: OK")


def test_an_auth_failure_benches_the_number_and_keeps_the_message():
    """AUTH benches the sender, not the message — and with one number that ends
    the run, which is the honest answer rather than a special case."""
    with temp_db() as conn:
        worker, settings, row = _queued_wa_run(
            conn, 3, _StubSession([(False, "AUTH: log in to WhatsApp again")]))
        said: list = []
        worker.log_signal.connect(lambda text, level: said.append(text))
        now = time.time()
        worker._send(conn, row, _wa_account(settings), now)

        after = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row["id"],))
        assert after["status"] == "queued" and after["scheduled_at"] >= now, after
        assert worker._live_accounts(now) == [], "the number was not benched"
        assert worker._running is False
        assert any("Reconnect the number" in text for text in said), said
        assert DB.get_campaign(conn, worker.campaign_id)["status"] == "stopped"
        assert {r["status"] for r in _messages(conn, worker.campaign_id)} == {"queued"}
    print("an auth failure benches the number and keeps the message: OK")


def test_a_banned_number_halts_this_run_and_every_future_run():
    """The rule email has no equivalent of, and the reason WhatsApp is different.

    A suspended Gmail account can be argued back. A banned WhatsApp number is
    usually gone, and the surest way to turn a temporary restriction into a
    permanent one is to keep sending through it — so the restriction has to
    outlive the run that hit it, and be cleared by the user rather than by time.
    """
    with temp_db() as conn:
        plan, campaign_id, settings = _plan_wa(conn, 4, **_fits_in_a_test())
        assert plan["queued"] == 4

        session = _StubSession([(False, "BANNED: your phone number is banned")])
        said: list = []
        with fake_clock(time.time()) as clock:
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                                      channel=C.WHATSAPP, session=session)
            worker.log_signal.connect(lambda text, level: said.append(text))
            _run_to_the_end(worker, conn, campaign_id, clock)

        assert len(session.sent) == 1, \
            "the run sent %d messages after a restriction" % len(session.sent)
        assert any("acknowledge" in text for text in said), said
        assert C.wa_ban_notice(conn), "the restriction did not outlive the run"
        assert DB.get_campaign(conn, campaign_id)["status"] == "stopped"
        # The message it died on is still owed, not lost and not sent again.
        assert {row["status"] for row in _messages(conn, campaign_id)} == {"queued"}

        # The next run does not even open the session.
        again = _StubSession()
        with fake_clock(time.time()) as clock:
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                                      channel=C.WHATSAPP, session=again)
            _run_to_the_end(worker, conn, campaign_id, clock)
        assert (again.sent, again.started) == ([], 0), (again.sent, again.started)

        # Nor can a fresh campaign be planned around it.
        blocked, _cid, _s = _plan_wa(conn, 2, leads=_wa_leads(2, "ban", 300))
        assert "restricted" in blocked["error"] and blocked["queued"] == 0

        # An email campaign is untouched: this is one number's restriction.
        mail = _plan_campaign(conn, 2)[0]
        assert mail["queued"] == 2, mail["error"]

        # Until the user says they have looked.
        assert C.acknowledge_wa_ban(conn) is True
        assert C.acknowledge_wa_ban(conn) is False, "acknowledged twice"
        assert C.wa_ban_notice(conn) == ""

        after = _StubSession()
        with fake_clock(time.time()) as clock:
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                                      channel=C.WHATSAPP, session=after)
            _run_to_the_end(worker, conn, campaign_id, clock)
        assert after.sent, "acknowledging the restriction did not release the queue"
    print("a banned number halts this run and every future run: OK")


def test_rate_limiting_buys_time_instead_of_retrying():
    """WhatsApp said "too fast". The only useful reply is not to send."""
    with temp_db() as conn:
        worker, settings, _row = _queued_wa_run(
            conn, 3, _StubSession([(False, "RATE: too many messages"),
                                   (False, "RATE: sending too fast")]))
        account = _wa_account(settings)
        rows = _messages(conn, worker.campaign_id)

        now = time.time()
        worker._send(conn, rows[0], account, now)
        first = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (rows[0]["id"],))
        assert first["status"] == "queued"
        assert first["scheduled_at"] >= now + worker.RATE_BACKOFF_SEC, first
        assert worker._rate_hits == 1

        worker._send(conn, rows[1], account, now)
        second = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (rows[1]["id"],))
        assert second["scheduled_at"] >= now + 2 * worker.RATE_BACKOFF_SEC, second
        assert worker._rate_hits == 2
        assert worker._running is True, "throttling is not a reason to stop"

        # Anything getting through means the backoff is over.
        worker._send(conn, rows[2], account, now)
        assert worker._rate_hits == 0
    print("rate limiting buys time instead of retrying: OK")


def test_a_whatsapp_dry_run_opens_nothing_and_hands_the_queue_back():
    """`wa_dry_run` ships True, so this is what a first press of Start does."""
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_wa(
            conn, 3, wa_dry_run=True, wa_send_days=[0, 1, 2, 3, 4, 5, 6],
            wa_send_start_hour=0, wa_send_end_hour=24, wa_min_gap_sec=0,
            wa_max_gap_sec=0, wa_followup_gap_days=1)
        columns = ("step", "lead_id", "scheduled_at", "subject", "body_text", "channel")
        before = {row["id"]: tuple(row[key] for key in columns)
                  for row in _messages(conn, campaign_id)}
        assert len(before) == 6

        with stub_wa_session() as built, fake_clock(time.time()) as clock:
            # No session handed in, and none may be built.
            worker = C.OutreachWorker(campaign_id, settings, channel=C.WHATSAPP)
            assert worker.dry_run is True, "wa_dry_run did not reach the worker"
            rehearsed = _run_to_the_end(worker, conn, campaign_id, clock)

        assert built == [], "a rehearsal opened a WhatsApp session"
        assert len(rehearsed) == 6, rehearsed
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends") == 0, "real quota spent"

        rows = _messages(conn, campaign_id)
        assert {row["id"]: tuple(row[key] for key in columns) for row in rows} == before
        assert {row["status"] for row in rows} == {"queued"}
        assert all(not row["sent_at"] and not row["error"] for row in rows), rows
        assert DB.get_campaign(conn, campaign_id)["status"] == "scheduled"

        # And then for real, on that same campaign, through a stubbed session.
        session = _StubSession()
        with fake_clock(time.time()) as clock:
            worker = C.OutreachWorker(campaign_id, dict(settings, wa_dry_run=False),
                                      channel=C.WHATSAPP, session=session)
            sent = _run_to_the_end(worker, conn, campaign_id, clock)
        assert len(sent) == 6 and len(session.sent) == 6, (sent, session.sent)
        assert all(phone.startswith("+1416555") for phone, _text_ in session.sent), \
            session.sent
        assert session.closed == 0, "the run closed a session it was lent"
        assert {row["status"] for row in _messages(conn, campaign_id)} == {"sent"}
        assert DB._scalar(conn, "SELECT COUNT(*) FROM sends WHERE channel = 'whatsapp'") == 6
    print("a WhatsApp dry run opens nothing and hands the queue back: OK")


def test_a_dry_run_says_which_numbers_it_could_not_send_to():
    """A rehearsal must not consume the queue, and must not be quiet either.

    The region can change between planning and rehearsing, so a queued row can
    turn unsendable. Marking it failed would spend a message the dry run
    promised to leave alone; saying nothing would hide it until the live run.
    """
    with temp_db() as conn:
        _plan, campaign_id, settings = _plan_wa(conn, 2, wa_dry_run=True)
        row = _messages(conn, campaign_id)[0]
        worker = C.OutreachWorker(campaign_id, dict(settings, wa_default_region=""),
                                  channel=C.WHATSAPP)
        said: list = []
        worker.log_signal.connect(lambda text, level: said.append(text))
        DB._write(conn, "UPDATE leads SET phone = '416-555-0199' WHERE id = ?",
                  (row["lead_id"],))

        worker._send(conn, row, _wa_account(settings), time.time())

        after = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row["id"],))
        assert after["status"] == "rehearsed", after
        assert after["error"] == "", "a rehearsal wrote a failure onto the queue"
        assert not DB.is_suppressed(conn, DB.get_lead(conn, row["lead_id"])["email"]), \
            "a rehearsal suppressed a lead"
        assert any("Would skip" in text and "country code" in text for text in said), said

        # A row that lost its body goes the same way and for the same reason:
        # 'skipped' is a status `requeue_rehearsed` does not bring back.
        other = _messages(conn, campaign_id)[1]
        DB.mark_message(conn, other["id"], "queued", body_text="")
        worker._send(conn, dict(other, body_text=""), _wa_account(settings), time.time())
        assert DB._one(conn, "SELECT status FROM messages WHERE id = ?",
                       (other["id"],))["status"] == "rehearsed"

        assert worker._restore_rehearsal(conn) == 2
    print("a dry run says which numbers it could not send to: OK")


def test_a_crashed_whatsapp_send_is_not_sent_twice():
    """The browser may have delivered it a moment before the process died."""
    with temp_db() as conn:
        session = _StubSession()
        worker, settings, row = _queued_wa_run(conn, 3, session)
        campaign_id = worker.campaign_id
        # What a crash between the claim and the outcome leaves behind.
        DB.mark_message(conn, row["id"], "sending", account_email=C.WA_ACCOUNT)
        DB.record_send(conn, C.WA_ACCOUNT, time.time(), channel=DB.WHATSAPP)

        said: list = []
        worker.log_signal.connect(lambda text, level: said.append(text))
        with fake_clock(time.time()) as clock:
            _run_to_the_end(worker, conn, campaign_id, clock)

        after = DB._one(conn, "SELECT * FROM messages WHERE id = ?", (row["id"],))
        assert after["status"] == "sent" and "not retried" in after["error"], after
        assert len(session.sent) == 2, \
            "the interrupted message was sent again (%d handed over)" % len(session.sent)
        assert any("chat on your phone" in text for text in said), said
    print("a crashed WhatsApp send is not sent twice: OK")


def test_a_held_whatsapp_queue_says_what_is_holding_it_and_when_it_lifts():
    """The same silence bug, and it has to name WhatsApp's window, not email's."""
    with temp_db() as conn:
        with fake_clock(SATURDAY_NIGHT) as clock:
            _plan, campaign_id, settings = _plan_wa(
                conn, 6, wa_followup_enabled=False)

            session = _StubSession()
            said: list = []
            worker = C.OutreachWorker(campaign_id, settings, dry_run=False,
                                      channel=C.WHATSAPP, session=session)
            worker.log_signal.connect(lambda text, level: said.append(text))
            naps = [0]

            def nap(seconds: float) -> None:
                naps[0] += 1
                clock.now += max(1.0, float(seconds))
                if naps[0] >= 20:
                    worker.stop()

            worker._nap = nap
            worker.run()

        assert session.sent == [], "the window did not hold the queue"
        held = [text for text in said if "Outside the sending window" in text]
        assert len(held) == 1, "%d hold lines in %d" % (len(held), len(said))
        assert "6 message(s)" in held[0], held[0]
        opens = C.next_window_open(SATURDAY_NIGHT,
                                   C.channel_settings(settings, C.WHATSAPP))
        assert C._clock(opens) in held[0], held[0]
        assert datetime.fromtimestamp(opens).hour == 10, \
            "the hold named email's window rather than WhatsApp's"
    print("a held WhatsApp queue says what is holding it: OK")


def test_a_stop_on_whatsapp_stops_the_email_sequence_too():
    """One lead pool, one opt-out. Anything less is why people get reported."""
    with temp_db() as conn:
        leads = _wa_leads(2, "both", 400)
        mail_settings = _campaign_settings()
        mail_id = DB.create_campaign(conn, "mail", "", PROFILE, mail_settings)
        C.plan_campaign(conn, campaign_id=mail_id, leads=leads, template_id="",
                        profile=PROFILE, settings=mail_settings, ai=None)
        _plan, wa_id, settings = _plan_wa(conn, 0, leads=leads,
                                          allow_cross_channel=True)

        target, other = leads[0], leads[1]
        session = _StubSession()
        session.replies = [
            {"wa_id": "", "phone": target["phone"], "text": "STOP", "ts": 0.0},
            {"wa_id": "", "phone": other["phone"],
             "text": "we are a one-stop shop, tell me more", "ts": 0.0},
        ]
        worker = C.OutreachWorker(wa_id, settings, dry_run=False,
                                  channel=C.WHATSAPP, session=session)
        worker._poll_whatsapp(conn, time.time())

        assert DB.is_suppressed(conn, target["email"]), "the address escaped the STOP"
        assert DB.is_suppressed(conn, phone=target["phone"])
        stopped = _lead_id(conn, target["email"])
        for campaign_id in (mail_id, wa_id):
            theirs = [row for row in _messages(conn, campaign_id)
                      if row["lead_id"] == stopped]
            assert theirs and all(row["status"] == "skipped" for row in theirs), \
                (campaign_id, theirs)

        # "one-stop shop" is a business describing itself, not an opt-out. It is
        # a reply, so the sequence stops — and the lead stays contactable.
        warm = _lead_id(conn, other["email"])
        assert not DB.is_suppressed(conn, other["email"]), \
            "a lead who answered was put on the do-not-contact list"
        assert DB.get_lead(conn, warm)["status"] == "replied"
        # Both channels, because an answer is a fact about the person: chasing
        # by email somebody who has just written back on WhatsApp is the same
        # mistake as chasing them on WhatsApp would be.
        for campaign_id in (mail_id, wa_id):
            theirs = [row for row in _messages(conn, campaign_id)
                      if row["lead_id"] == warm]
            assert theirs and all(row["status"] == "skipped" for row in theirs), \
                (campaign_id, theirs)

        # Acted on once: the same unread chat is read again on the next poll.
        worker._polled_at = 0.0
        worker._poll_whatsapp(conn, time.time())
        replied = [e for e in DB.recent_events(conn) if e["kind"] == "replied"]
        assert len(replied) == 1, replied
    print("a stop on WhatsApp stops the email sequence too: OK")


def test_a_number_not_on_whatsapp_does_not_kill_the_email_sequence():
    """`RECIPIENT:` means the transport cannot reach them, not that they said no.

    Suppression is shared across channels by design, so putting a number nobody
    answers on that list would quietly cancel a perfectly good email sequence.
    """
    with temp_db() as conn:
        leads = _wa_leads(1, "gone", 500)
        mail_settings = _campaign_settings()
        mail_id = DB.create_campaign(conn, "mail", "", PROFILE, mail_settings)
        C.plan_campaign(conn, campaign_id=mail_id, leads=leads, template_id="",
                        profile=PROFILE, settings=mail_settings, ai=None)
        _plan, wa_id, settings = _plan_wa(conn, 0, leads=leads,
                                          allow_cross_channel=True)

        row = _messages(conn, wa_id)[0]
        worker = C.OutreachWorker(
            wa_id, settings, dry_run=False, channel=C.WHATSAPP,
            session=_StubSession([(False, "RECIPIENT: is not on WhatsApp")]))
        worker._send(conn, row, _wa_account(settings), time.time())

        assert not DB.is_suppressed(conn, leads[0]["email"]), \
            "a number that is not on WhatsApp suppressed the email address"
        assert not DB.is_suppressed(conn, phone=leads[0]["phone"])
        assert {r["status"] for r in _messages(conn, mail_id)} == {"queued"}, \
            "the email sequence was cancelled by a WhatsApp refusal"
        theirs = _messages(conn, wa_id)
        assert {r["status"] for r in theirs} == {"failed", "skipped"}, theirs
    print("a number not on WhatsApp does not kill the email sequence: OK")


def test_a_worker_refuses_a_queue_written_for_the_other_channel():
    """The one mistake with no recoverable failure mode: WhatsApp copy on SMTP.

    A chat bubble has no subject and carries a plain "reply STOP" line; handed
    to Gmail it is a subject-less cold email with no footer and no unsubscribe
    header, from an account whose reputation the whole rest of this file exists
    to protect.
    """
    with temp_db() as conn:
        _plan, mail_id, _s = _plan_campaign(conn, 2, dry_run=False)
        session = _StubSession()
        said: list = []
        worker = C.OutreachWorker(mail_id, _wa_settings(), dry_run=False,
                                  channel=C.WHATSAPP, session=session)
        worker.log_signal.connect(lambda text, level: said.append(text))
        worker.run()

        assert (session.sent, session.started) == ([], 0)
        assert any("written for email" in text for text in said), said
        assert {row["status"] for row in _messages(conn, mail_id)} == {"queued"}

        # And the mirror: an email worker refuses a WhatsApp queue.
        _plan, wa_id, settings = _plan_wa(conn, 2, leads=_wa_leads(2, "x", 600))
        said = []
        with stub_smtp() as (opened, wire), stub_imap():
            worker = C.OutreachWorker(wa_id, settings, dry_run=False)
            worker.log_signal.connect(lambda text, level: said.append(text))
            worker.run()
        assert (opened, wire) == ([], []), "WhatsApp copy reached an SMTP socket"
        assert any("written for whatsapp" in text for text in said), said
    print("a worker refuses a queue written for the other channel: OK")


def test_a_whatsapp_run_owns_only_the_session_it_opened():
    """A borrowed session belongs to the Settings screen and stays open."""
    with temp_db() as conn:
        lent = _StubSession()
        worker, _s, _row = _queued_wa_run(conn, 1, lent)
        worker._close_senders()
        assert lent.closed == 0, "the run closed the connection card's session"

        # Except when the app is closing, where there is no card left to keep
        # alive and a thread parked in Selenium is what stops the process from
        # exiting at all.
        worker.abort()
        assert lent.closed == 1, "abort left a browser open on the way out"

        with stub_wa_session() as built:
            worker, settings, row = _queued_wa_run(conn, 1)
            # `_queued_wa_run` lends one by default; take it away so this run
            # has to open its own.
            worker._wa_session, worker._owns_session = None, True
            worker._send(conn, row, _wa_account(settings), time.time())

            assert len(built) == 1, "the run never opened a session"
            mine = built[0]
            assert worker._wa_session is mine
            worker._close_senders()
            assert mine.closed == 1, "the run left its own browser open"
            assert worker._wa_session is None
    print("a WhatsApp run owns only the session it opened: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL SCHEDULE TESTS PASSED")
