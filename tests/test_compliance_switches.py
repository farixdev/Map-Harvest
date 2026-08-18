"""Offline tests for the three compliance switches and the end of hard blocks.

The user asked for a system that never stands in their way, and for mail that
does not land in spam. Those two pull against each other exactly once — at the
unsubscribe line, the postal address and the unfinished sender profile — so
each of them became a setting that is on by default and can be turned off, and
none of them is a wall any more.

What is asserted here is that both halves of that bargain are kept:

* a switch turned off actually removes the line, and removes it *cleanly* — no
  dangling "|", no empty grey block under a rule, no orphaned separator where
  the address used to be;
* the `List-Unsubscribe` header goes out either way. It is invisible, it costs
  one line, and it is the opt-out signal Gmail and Outlook actually weight, so
  it is the one thing the visible switch deliberately does not govern;
* an incomplete sender profile is stated plainly and can always be overridden.
  With `require_profile_complete` on, Prepare and Start ask and offer to go
  ahead; with it off they do not ask at all. Neither one refuses.

Qt runs offscreen. `SETTINGS_DIR` and the database are redirected into a temp
directory before the screen is built, so nothing here can read or write a
developer's real ~/.mapharvest.
"""
import contextlib
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QSize  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core import campaign as C  # noqa: E402
from core import mailer as M  # noqa: E402
from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402
from core import templates as T  # noqa: E402
from ui import app as APP  # noqa: E402
from ui import screen_outreach as SO  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="mapharvest-compliance-")
_APP = None
_SCREEN = None

PROFILE = {"company": "Auto Army", "sender_name": "Umar", "sender_title": "Automation",
           "website": "autoarmy.io", "reply_to": "umar@autoarmy.io", "phone": "",
           "postal_address": "1 King St W, Toronto ON", "calendar_link": "",
           "services": [], "proof_points": [], "tone": "direct"}

ACCOUNT = {"email": "sender@shop.test", "app_password": "x", "enabled": True,
           "display_name": "Umar", "daily_cap": 40, "warmup_started": "",
           "imap_enabled": False}

LEAD = {"email": "rob@harbourvale.co.uk", "name": "Harbourvale Joinery",
        "website": "https://harbourvale.co.uk"}

AUDIT = {"final_url": "https://harbourvale.co.uk", "reachable": True, "gaps": [
    {"code": "no_online_booking", "title": "no way to book online",
     "subject_phrase": "booking by phone only", "evidence": "no booking link found",
     "services": ["appointment booking"]}]}


def _settings(**overrides) -> dict:
    base = {
        "sender_profile": dict(PROFILE), "smtp_accounts": [dict(ACCOUNT)],
        "unsubscribe_mailto": "", "audit_enabled": False, "dry_run": True,
        "send_days": [0, 1, 2, 3, 4, 5, 6], "send_start_hour": 0, "send_end_hour": 24,
        "send_timezone": "local", "send_min_gap_sec": 60, "send_max_gap_sec": 240,
        "daily_cap_per_account": 100, "hourly_cap_per_account": 0,
        "warmup_enabled": False, "followup_enabled": False, "followup_max_steps": 0,
    }
    base.update(overrides)
    return base


@contextlib.contextmanager
def temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        original = (ST.SETTINGS_DIR, ST.SETTINGS_PATH)
        ST.SETTINGS_DIR = tmp
        ST.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        try:
            yield DB.connect(os.path.join(tmp, "outreach.db"))
        finally:
            DB.close_all()
            ST.SETTINGS_DIR, ST.SETTINGS_PATH = original


# A first touch of this file's own. The shipped copy is somebody else's to
# write and rewrite; what is under test here is the footer beneath it, and a
# fixed body is what keeps these assertions about the switches.
PROBE = T.Template(id="switch_probe", name="Switch probe", step=0,
                   subject="{{gap_1_subject}} at {{business_name}}",
                   body=("Hi {{first_name}},\n"
                         "\n"
                         "One thing stands out on {{website_domain}}: {{gap_1}}.\n"
                         "\n"
                         "{{sender_name}}\n"
                         "{{sender_title}}, {{company}}"))


def _rendered(**switches):
    """(text, html) for one lead, with the switches applied as the send loop does."""
    settings = _settings(**switches)
    ctx = C.apply_compliance(
        T.build_context(LEAD, AUDIT, {}, PROFILE, settings), settings)
    _subject, text, html = T.render(PROBE, ctx)
    return text, html


@contextlib.contextmanager
def _probe_template():
    """Plan against PROBE rather than against whatever the catalogue holds."""
    original = T.get_template
    T.get_template = lambda template_id: PROBE
    try:
        yield
    finally:
        T.get_template = original


# ── The settings themselves ──────────────────────────────────────────────────

def test_the_three_switches_ship_on():
    for key in ("append_unsubscribe", "append_postal_address", "require_profile_complete"):
        assert ST.DEFAULT_SETTINGS[key] is True, key


def test_an_older_settings_file_gains_them_switched_on():
    """The deep merge is what makes a new guardrail safe to add.

    A file written before these existed must come back with them on, and must
    not lose a single thing the user had already chosen.
    """
    original = (ST.SETTINGS_DIR, ST.SETTINGS_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        ST.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        try:
            with open(ST.SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({"dry_run": False, "daily_cap_per_account": 7,
                           "sender_profile": {"company": "Auto Army"}}, f)
            loaded = ST.load_settings()
            assert loaded["append_unsubscribe"] is True
            assert loaded["append_postal_address"] is True
            assert loaded["require_profile_complete"] is True
            assert loaded["daily_cap_per_account"] == 7, "an existing choice was lost"
            assert loaded["dry_run"] is False, "an existing choice was lost"

            # And a file that says off stays off across a save/load round trip.
            loaded["append_unsubscribe"] = False
            ST.save_settings(loaded)
            assert ST.load_settings()["append_unsubscribe"] is False
        finally:
            ST.SETTINGS_DIR, ST.SETTINGS_PATH = original


def test_apply_compliance_never_raises_and_defaults_to_on():
    assert C.apply_compliance(None, None) is None
    assert C.apply_compliance({"unsubscribe_line": "x"}, None)["unsubscribe_line"] == "x"
    ctx = {"unsubscribe_line": "x", "unsubscribe_email": "a@b.test", "postal_address": "1 King"}
    assert C.apply_compliance(dict(ctx), {})["unsubscribe_line"] == "x", "silence must mean on"
    off = C.apply_compliance(dict(ctx), {"append_unsubscribe": False,
                                         "append_postal_address": False})
    assert off == {"unsubscribe_line": "", "unsubscribe_email": "", "postal_address": ""}


# ── The footer degrades, it does not break ───────────────────────────────────

def test_the_footer_carries_both_lines_by_default():
    text, html = _rendered()
    assert "1 King St W, Toronto ON" in text and "1 King St W, Toronto ON" in html
    assert "unsubscribe" in text.lower() and "unsubscribe" in html.lower()
    assert "Auto Army | 1 King St W, Toronto ON" in text


def test_the_address_off_leaves_no_dangling_separator():
    text, html = _rendered(append_postal_address=False)
    assert "1 King St W" not in text and "1 King St W" not in html
    assert "unsubscribe" in text.lower(), "the opt-out line went with the address"
    assert "|" not in text.split("\n")[-2:][0] or "Auto Army |" not in text, text
    assert "Auto Army |" not in text and "Auto Army |" not in html, text
    assert "| <br>" not in html and "|</p>" not in html, html


def test_the_unsubscribe_line_off_leaves_the_rest_intact():
    text, html = _rendered(append_unsubscribe=False)
    assert "unsubscribe" not in text.lower(), text
    assert "unsubscribe" not in html.lower(), html
    assert "1 King St W, Toronto ON" in text, "the address went with the opt-out line"
    assert "Auto Army | 1 King St W, Toronto ON" in text


def test_both_off_leaves_no_empty_footer_block():
    """What is left of the footer is the sender's own name, on its own line.

    Neither switch governs that: it is who the mail is from, it is in the body
    already, and a rule with a company under it is a footer. What must not
    survive is the joinery — the separator that held the address on, and the
    grey block with nothing in it.
    """
    text, html = _rendered(append_unsubscribe=False, append_postal_address=False)
    assert "unsubscribe" not in text.lower() and "1 King St W" not in text
    assert "Auto Army |" not in text and "Auto Army |" not in html, text[-120:]
    assert not text.rstrip().endswith("|"), text[-80:]
    assert text.strip(), "the whole message vanished with the footer"
    assert "Harbourvale" in text or "harbourvale" in text

    # With no company either there is nothing to put under the rule, and then
    # the rule itself must go rather than draw an empty grey box.
    settings = _settings(append_unsubscribe=False, append_postal_address=False)
    blank = dict(PROFILE, company="", postal_address="")
    ctx = C.apply_compliance(T.build_context(LEAD, AUDIT, {}, blank, settings), settings)
    _subject, bare_text, bare_html = T.render(T.get_template("gap_direct"), ctx)
    assert "<hr" not in bare_html, bare_html[-200:]
    assert not bare_text.rstrip().endswith(("|", ",", ";")), bare_text[-80:]


def test_no_switch_can_put_a_merge_token_in_the_copy():
    for switches in ({}, {"append_unsubscribe": False}, {"append_postal_address": False},
                     {"append_unsubscribe": False, "append_postal_address": False}):
        text, html = _rendered(**switches)
        assert "{{" not in text and "}}" not in text, (switches, text)
        assert "{{" not in html and "}}" not in html, (switches, html)


# ── The header stays on the wire ─────────────────────────────────────────────

def test_list_unsubscribe_ships_even_with_the_visible_line_off():
    """The point of the switch is the copy, not the deliverability signal."""
    message, _mid = M.build_message(
        to_email="rob@harbourvale.co.uk", to_name="Harbourvale Joinery",
        from_email="sender@shop.test", from_name="Umar", reply_to="umar@autoarmy.io",
        subject="booking by phone only", body_text="Hi Rob,\n\nOne thing stands out.",
        body_html="", unsubscribe_mailto="")
    assert message["List-Unsubscribe"] == "<mailto:sender@shop.test?subject=unsubscribe>"
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_the_send_path_still_sets_the_header_with_both_switches_off():
    """Read off the message the worker actually hands to SMTP."""
    with temp_db() as conn:
        settings = _settings(append_unsubscribe=False, append_postal_address=False,
                             dry_run=True)
        campaign_id = DB.create_campaign(conn, "switched off", "", PROFILE, settings)
        lead_id = DB.upsert_lead(conn, dict(LEAD))
        DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id, "step": 0,
                                "subject": "booking by phone only",
                                "body_text": "Hi Rob,\n\nOne thing stands out.",
                                "body_html": "", "account_email": ACCOUNT["email"],
                                "scheduled_at": time.time() - 10})
        worker = C.OutreachWorker(campaign_id, settings, dry_run=True)
        row = DB.due_messages(conn, time.time())[0]

        built = []
        original = C._mailer.build_message

        def spy(**kwargs):
            message, mid = original(**kwargs)
            built.append(message)
            return message, mid

        C._mailer.build_message = spy
        try:
            worker._send(conn, row, dict(ACCOUNT), time.time())
        finally:
            C._mailer.build_message = original

        assert built, "the worker never built a message"
        assert built[0]["List-Unsubscribe"], "the header was tidied away with the line"


def test_a_planned_campaign_queues_the_switched_off_footer():
    """End to end: what the switches do is what lands in `messages`."""
    for switches, wanted, unwanted in (
            ({}, ("1 King St W", "unsubscribe"), ()),
            ({"append_postal_address": False}, ("unsubscribe",), ("1 King St W",)),
            ({"append_unsubscribe": False}, ("1 King St W",), ("unsubscribe",)),
            ({"append_unsubscribe": False, "append_postal_address": False},
             (), ("1 King St W", "unsubscribe"))):
        with temp_db() as conn, _probe_template():
            settings = _settings(**switches)
            campaign_id = DB.create_campaign(conn, "switches", "", PROFILE, settings)
            plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=[dict(LEAD)],
                                   template_id=PROBE.id, profile=PROFILE,
                                   settings=settings, ai=None)
            assert not plan["error"], (switches, plan["error"])
            assert plan["queued"] == 1, (switches, plan)
            row = DB.due_messages(conn, time.time() + 10 * 86400)[0]
            body = (row["body_text"] + row["body_html"]).lower()
            for needle in wanted:
                assert needle.lower() in body, (switches, needle)
            for needle in unwanted:
                assert needle.lower() not in body, (switches, needle)


# ── Nothing hard-blocks ──────────────────────────────────────────────────────

def _app() -> QApplication:
    global _APP
    if _APP is None:
        ST.SETTINGS_DIR = _TMP
        ST.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyle("Fusion")
        _APP.setStyleSheet(APP.QSS)
    return _APP


def _screen():
    """A built OutreachScreen with one lead and a deliberately empty profile."""
    global _SCREEN
    if _SCREEN is None:
        app = _app()
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        DB.upsert_lead(conn, dict(LEAD, source="test"))
        _SCREEN = SO.OutreachScreen()
        _SCREEN.resize(QSize(1080, 760))
        _SCREEN.show()
        app.processEvents()
    return _SCREEN


class _Asked:
    """Stands in for the modal question, and records that it was asked."""

    def __init__(self, go: bool, stop_asking: bool = False):
        self.go = go
        self.stop_asking = stop_asking
        self.calls = []

    def __call__(self, title, body, proceed):
        self.calls.append((title, body, proceed))
        return self.go, self.stop_asking


@contextlib.contextmanager
def _incomplete(screen, **overrides):
    """The screen with an unfinished profile and no account to send from."""
    original_settings = screen.settings
    original_ask = screen._ask
    screen.settings = _settings(sender_profile=dict(PROFILE, sender_name="",
                                                    postal_address=""),
                                smtp_accounts=[], **overrides)
    try:
        yield
    finally:
        screen.settings = original_settings
        screen._ask = original_ask


def _quiet_plan(plan=None):
    """Replace the planner so pressing Prepare costs no crawl and no database."""
    original = SO.plan_campaign
    SO.plan_campaign = lambda conn, **kwargs: dict(plan or {"queued": 0, "error": "stubbed"})
    return original


def test_the_profile_problems_still_say_what_is_missing():
    screen = _screen()
    with _incomplete(screen):
        problems = screen._profile_problems()
        listed = [problem for problem, _cost in problems]
        assert len(problems) == 3, problems
        assert any("Gmail account" in p for p in listed), listed
        assert any("sign-off" in p for p in listed), listed
        assert any("postal address" in p for p in listed), listed
        assert all(cost for _p, cost in problems), "a warning with no consequence"

        screen._refresh_profile()
        shown = screen.profile_summary.text()
        for problem in listed:
            assert problem in shown, "%r is not on the Sender profile card" % problem


def test_prepare_offers_a_way_through_and_takes_no_for_an_answer():
    screen = _screen()
    app = _app()
    original_plan = _quiet_plan()
    try:
        with _incomplete(screen):
            refuse = _Asked(go=False)
            screen._ask = refuse
            screen._on_prepare_clicked()
            assert refuse.calls, "an unfinished profile was not questioned at all"
            assert "anyway" in refuse.calls[0][2].lower(), refuse.calls[0][2]
            assert not screen._planning, "'Open Settings' still started a campaign"

            accept = _Asked(go=True)
            screen._ask = accept
            screen._on_prepare_clicked()
            assert accept.calls, "the question was not asked the second time"
            assert screen._planning, "'Prepare anyway' did not prepare anything"
    finally:
        SO.plan_campaign = original_plan
        if screen.plan_worker is not None:
            screen.plan_worker.wait(4000)
        app.processEvents()
        screen._planning = False


def test_the_check_switched_off_asks_nothing_at_all():
    screen = _screen()
    app = _app()
    original_plan = _quiet_plan()
    try:
        with _incomplete(screen, require_profile_complete=False):
            never = _Asked(go=False)
            screen._ask = never
            screen._on_prepare_clicked()
            assert not never.calls, "the switch is off and it still asked"
            assert screen._planning, "the switch is off and it still refused"
            assert "unfinished profile" in screen.toast_label.text().lower(), \
                "nothing said the profile is still unfinished: %r" % screen.toast_label.text()
    finally:
        SO.plan_campaign = original_plan
        if screen.plan_worker is not None:
            screen.plan_worker.wait(4000)
        app.processEvents()
        screen._planning = False


def test_start_sending_offers_send_anyway():
    screen = _screen()
    app = _app()
    conn = screen.conn
    campaign_id = DB.create_campaign(conn, "gate", "gap_direct", PROFILE, {})
    lead_id = DB.upsert_lead(conn, dict(LEAD))
    DB.queue_message(conn, {"campaign_id": campaign_id, "lead_id": lead_id, "step": 0,
                            "subject": "s", "body_text": "b", "body_html": "",
                            "account_email": ACCOUNT["email"],
                            "scheduled_at": time.time() + 3600})
    try:
        with _incomplete(screen, dry_run=True):
            screen._campaign_id = campaign_id
            refuse = _Asked(go=False)
            screen._ask = refuse
            screen._on_start_clicked()
            assert refuse.calls, "Start sending never asked"
            assert refuse.calls[0][2] == "Send anyway", refuse.calls[0][2]
            assert not screen._sending, "'Open Settings' started the run anyway"

            accept = _Asked(go=True)
            screen._ask = accept
            screen._on_start_clicked()
            assert screen._sending, "'Send anyway' did not send"
    finally:
        if screen.send_worker is not None:
            screen.send_worker.stop()
            screen.send_worker.wait(4000)
        app.processEvents()
        screen._sending = False
        screen._campaign_id = 0


def test_do_not_ask_again_writes_the_setting():
    screen = _screen()
    app = _app()
    original_plan = _quiet_plan()
    stored = dict(screen.settings)
    try:
        with _incomplete(screen):
            screen._ask = _Asked(go=True, stop_asking=True)
            screen._on_prepare_clicked()
            assert screen.settings["require_profile_complete"] is False
            with open(ST.SETTINGS_PATH, encoding="utf-8") as f:
                assert json.load(f)["require_profile_complete"] is False, \
                    "the choice would be gone by the next launch"
    finally:
        SO.plan_campaign = original_plan
        if screen.plan_worker is not None:
            screen.plan_worker.wait(4000)
        app.processEvents()
        screen._planning = False
        screen.settings = stored
        ST.save_settings(dict(stored, require_profile_complete=True))


def test_no_control_is_disabled_without_saying_why():
    screen = _screen()
    screen._campaign_id = 0
    screen._sending = False
    screen._refresh_send_controls()
    assert not screen.start_btn.isEnabled()
    assert "Campaign tab" in screen.start_btn.toolTip(), screen.start_btn.toolTip()
    for button in (screen.pause_btn, screen.stop_btn):
        assert button.toolTip(), "a disabled control with nothing to read"

    screen.lead_table.clearSelection()
    screen._refresh_audit_button()
    assert screen.audit_btn.toolTip(), "the audit button says nothing about itself"


def test_a_template_written_elsewhere_is_pickable_without_a_restart():
    """The list is rebuilt, not remembered, and the choice survives the rebuild."""
    screen = _screen()
    original = T.templates_for_step
    T.templates_for_step = lambda step: ([PROBE] + list(original(0))) if step == 0 else original(step)
    try:
        screen._refresh_templates()
        assert screen.template_combo.findData(PROBE.id) >= 0, \
            "a template written a minute ago is not on the list"
        screen.template_combo.setCurrentIndex(screen.template_combo.findData(PROBE.id))
        screen._refresh_templates()
        assert screen._template_id() == PROBE.id, "the rebuild lost the chosen template"
        assert screen._first_touch_template().id == PROBE.id
    finally:
        T.templates_for_step = original
        screen._refresh_templates()

    # A chosen id that no longer resolves falls back to a real first touch
    # instead of leaving the screen with nothing it can prepare.
    screen.template_combo.blockSignals(True)
    screen.template_combo.addItem("gone", "no_such_template")
    screen.template_combo.setCurrentIndex(screen.template_combo.count() - 1)
    screen.template_combo.blockSignals(False)
    try:
        fallback = screen._first_touch_template()
        assert fallback is not None and fallback.step == 0, fallback
    finally:
        screen._refresh_templates()


def test_the_preview_says_what_the_footer_is_missing():
    screen = _screen()
    original = screen.settings
    try:
        screen.settings = _settings()
        screen._refresh_footer_hint()
        assert "unsubscribe line included" in screen.preview_hint.text()

        screen.settings = _settings(append_unsubscribe=False, append_postal_address=False)
        screen._refresh_footer_hint()
        note = screen.preview_hint.text()
        assert "unsubscribe line" in note and "postal address" in note, note
        assert "spam" in note, "the cost of the choice is not stated: %r" % note
        assert "List-Unsubscribe" in note, "the header still ships and nobody said so"
    finally:
        screen.settings = original
        screen._refresh_footer_hint()


def test_the_sending_tab_shows_the_rules_it_will_obey():
    """Caps, window, days, warm-up and follow-ups all live two screens away."""
    screen = _screen()
    original = screen.settings
    try:
        screen.settings = _settings(send_days=[0, 4], send_start_hour=9,
                                    send_end_hour=17, daily_cap_per_account=25,
                                    hourly_cap_per_account=6, warmup_enabled=True,
                                    warmup_start=8, followup_enabled=True,
                                    followup_max_steps=2, followup_gap_days=3,
                                    ai_provider="groq")
        summary = screen._rules_summary()
        for fragment in ("Mon", "Fri", "9:00", "17:00", "25 a day", "6 an hour",
                         "ramping from 8", "2 follow-ups", "3 days apart"):
            assert fragment in summary, (fragment, summary)
        screen._refresh_mode()
        assert "25 a day" in screen.send_note.text(), screen.send_note.text()
        assert "Settings" in screen.send_note.text()
        assert screen._ai_summary() == "AI: Groq", screen._ai_summary()

        screen.settings = _settings(ai_provider="off", followup_enabled=False,
                                    warmup_enabled=False)
        assert "no follow-ups" in screen._rules_summary()
        assert screen._ai_summary() == "AI off — plain templates"
    finally:
        screen.settings = original
        screen._refresh_mode()


def test_close_all_at_exit():
    """Not a test — Windows will not delete the temp dir with the db open."""
    DB.close_all()
