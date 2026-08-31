"""Offline tests for core.wa_templates. No network, no browser, no Qt.

Nothing here opens a WhatsApp session. This module renders strings; the only
thing it borrows from `core.whatsapp` is `matches_opt_out`, which is a regex
over text, and it borrows it to prove the one thing neither module can prove
alone: that the word the copy tells a stranger to reply is the same word the
reply watcher honours. A message that invites "STOP" and then keeps messaging
somebody who typed it is the fastest route there is to a reported number, and a
reported number is a banned number.

The rest of this file is copy rules. They read like style opinions and they are
not: every one of them is a way a sixty-word message from an unknown number
turns into a mail merge, and a mail merge on this channel does not cost a reply,
it costs the number.

The store is redirected out of the real profile at import, the way
`tests/test_templates.py` does, and then checked rather than trusted --
`test_the_store_can_never_be_the_users_own` asserts it. An earlier run of the
template editor wrote an override into a real user's profile once already.
"""

import atexit
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audit as A  # noqa: E402
from core import settings as ST  # noqa: E402
from core import templates as T  # noqa: E402
from core import wa_templates as W  # noqa: E402
from core import whatsapp as WA  # noqa: E402

# Redirected at import, which pytest does for every test module before it runs
# the first test in any of them. `store_path` derives from
# `core.templates.TEMPLATES_PATH`, which conftest also moves, so this is a belt
# on top of a brace -- and the belt is what a test run outside conftest gets.
_STORE_DIR = tempfile.mkdtemp(prefix="mapharvest-wa-templates-")
atexit.register(shutil.rmtree, _STORE_DIR, ignore_errors=True)
W.WA_TEMPLATES_PATH = os.path.join(_STORE_DIR, "wa_templates.json")

REAL_PROFILES = (os.path.join(os.path.expanduser("~"), ".leadforge"),
                 os.path.join(os.path.expanduser("~"), ".mapharvest"))


# ── Fixtures ──

LEAD = {
    "name": "Acme Plumbing & Heating",
    "email": "mike.reid@acmeplumbing.ca",
    "city": "Toronto",
    "category": "Plumber",
    "domain": "acmeplumbing.ca",
    "website": "https://www.acmeplumbing.ca",
    "phone": "+1 416-555-0142",
}

# Deliberately the widest everything: the longest business name in the email
# suite, a long domain, a long sender and a long company. A word budget that
# only holds for "Acme Plumbing" is not a word budget.
BIG_LEAD = {
    "name": "Coastal Fabrication & Welding Incorporated",
    "email": "frontdesk@coastalfab.ca",
    "domain": "coastal-fabrication-welding.example.co.uk",
    "phone": "+1 905-555-0188",
}

BOOKING_GAP = {"code": "no_online_booking", "title": "no online booking", "severity": 3,
               "subject_phrase": "online booking",
               "evidence": "contact form on /contact, no booking widget",
               "services": ["Appointment booking", "Lead Automation"]}

AUDIT = {"final_url": "https://acmeplumbing.ca", "gaps": [BOOKING_GAP]}

AI = {
    "ok": True,
    "subject": "booking on acmeplumbing.ca",
    "opener": ("Your emergency page lists four services and every one of them ends at "
               "the same contact form. Acme Plumbing has no way to take a booking "
               "after hours, which is when a burst pipe happens."),
    "ps": "Online booking could write straight into your calendar.",
}

PROFILE = {
    "company": "Auto Army",
    "sender_name": "Umar Farooq",
    "sender_title": "Automation lead",
    "website": "https://autoarmy.io",
    "reply_to": "umar@autoarmy.io",
    "calendar_link": "https://cal.com/autoarmy/15min",
    "postal_address": "12 King St W, Toronto ON M5H 1A1",
    "proof_points": ["A Mississauga HVAC firm went from replying in a day to four minutes."],
    "services": [],
}

BARE_PROFILE = dict(PROFILE, proof_points=[])

BIG_PROFILE = dict(PROFILE, company="Auto Army Automation Solutions",
                   sender_name="Umar Farooq Siddiqui")

SETTINGS = {"unsubscribe_mailto": "",
            "smtp_accounts": [{"email": "umar@autoarmy.io", "enabled": True}]}

LEAK_PATTERNS = ("{{", "}}", "\x00", "{business_name}", "{first_name}", "{gap_1}")


def _ctx(lead=LEAD, audit=AUDIT, ai=None, profile=PROFILE, settings=SETTINGS) -> dict:
    return T.build_context(lead, audit, ai or {}, profile, settings)


# Every shape a real campaign hands the renderer, including the three that have
# no site to have a finding on. Cold outreach is mostly these.
RENDER_CONTEXTS = (
    ("full context", lambda: _ctx(ai=AI)),
    ("no model output", lambda: _ctx()),
    ("no gaps", lambda: _ctx(audit={})),
    ("unreachable site, no proof points", lambda: _ctx(audit={}, profile=BARE_PROFILE)),
    ("no website at all", lambda: _ctx(
        {"name": "Beeston Joinery", "email": "quotes@beestonjoinery.co.uk"},
        {}, None, BARE_PROFILE, SETTINGS)),
    ("longest names, longest gap", lambda: _ctx(
        BIG_LEAD, {"gaps": [dict(A.GAP_CATALOGUE["stale_site"], code="stale_site")]},
        None, BIG_PROFILE, SETTINGS)),
    ("empty everything", lambda: T.build_context({}, {}, {}, {}, {})),
)


# `slow_site` and `no_mobile` are website work and the catalogue is not a web
# shop, so `core.templates.GAP_SERVICES` deliberately has no honest offer for
# either and `build_context` refuses to let one become the headline. They reach
# the copy as nothing at all, which is the correct answer rather than a hole: a
# sixty-word message has no room to name a finding its own offer cannot follow
# from.
ANSWERABLE_GAPS = {code: gap for code, gap in A.GAP_CATALOGUE.items()
                   if T.services_for_gaps([dict(gap, code=code)])}


def _clear_store():
    W._forget_store()
    with contextlib.suppress(OSError):
        os.remove(W.store_path())
    W._forget_store()


@contextlib.contextmanager
def _empty_store():
    """An empty store for one test, and an empty store for the next one."""
    _clear_store()
    try:
        yield
    finally:
        _clear_store()


# ── The store ──


def test_the_store_can_never_be_the_users_own():
    """The one failure this suite is not allowed to have.

    `store_path` is resolved on every call from `core.templates.TEMPLATES_PATH`
    rather than captured at import, which is what makes the single redirect
    `tests/conftest.py` already performs carry this store with it. A second
    global captured at import would have to be redirected a second time, by
    every test module that remembered, and the one that forgot would be writing
    into a real user's profile.
    """
    resolved = os.path.abspath(W.store_path()).lower()
    for real in REAL_PROFILES:
        assert not resolved.startswith(os.path.abspath(real).lower()), resolved

    # And it follows the email store, wherever that is put.
    saved_email, saved_wa = T.TEMPLATES_PATH, W.WA_TEMPLATES_PATH
    try:
        W.WA_TEMPLATES_PATH = ""
        T.TEMPLATES_PATH = os.path.join(_STORE_DIR, "elsewhere", "templates.json")
        assert W.store_path() == os.path.join(_STORE_DIR, "elsewhere",
                                              W.WA_STORE_NAME), W.store_path()
    finally:
        T.TEMPLATES_PATH, W.WA_TEMPLATES_PATH = saved_email, saved_wa
    assert W.store_path() == W.WA_TEMPLATES_PATH


def test_the_shape_is_the_email_stores_shape():
    """The settings editor edits both channels with the component it has.

    Same `Template` class, same fields, same ids-are-unique rule, same
    `TEMPLATES`/`BUILTIN_TEMPLATES` relationship. What differs is the copy and
    the fact that `subject` is dead weight here.
    """
    assert W.Template is T.Template
    assert W.WA_TEMPLATES is W.WA_BUILTIN_TEMPLATES
    assert len({t.id for t in W.WA_TEMPLATES}) == len(W.WA_TEMPLATES)
    assert all(isinstance(t, T.Template) for t in W.WA_TEMPLATES)

    # A small set of genuinely different first touches, and exactly one chaser:
    # `wa_followup_max_steps` is 1, and a second chaser to a number that has not
    # replied is what gets a sender reported.
    first = W.templates_for_step(0)
    assert len(first) >= 3, [t.id for t in first]
    assert [t.id for t in W.WA_TEMPLATES if t.step > 0] == ["wa_followup"]
    assert len(W.templates_for_step(1)) == 1

    # No subject anywhere: WhatsApp has none, and the editor is told so.
    for tpl in W.WA_TEMPLATES:
        assert tpl.subject == "", (tpl.id, tpl.subject)
        assert tpl.name.strip(), tpl.id

    assert W.get_template("wa_gap") is not None
    assert W.get_template("gap_direct") is None      # an email id is not a WA id
    assert W.is_builtin("wa_gap") and not W.is_builtin("gap_direct")


def test_the_two_channels_never_share_a_store():
    """One store for two channels means a sixty-word body with no subject can be
    picked for an email campaign, and an id can collide across the two so that
    editing one channel's copy silently overrides the other's."""
    with _empty_store():
        assert W.store_path() != T.TEMPLATES_PATH

        mine = T.Template(id="wa_gap", name="My WhatsApp angle", step=0, subject="",
                          body="Hi {{first_name}},\n\nMy own words about {{gap_1}}.")
        W.save_user_template(mine)

        assert W.get_template("wa_gap") == mine
        assert T.get_template("wa_gap") is None
        assert mine not in T.all_templates()
        assert {t.id for t in T.all_templates()} >= {"gap_direct", "followup_close"}
        assert not any(t.id.startswith("wa_") for t in T.all_templates())
        assert not any(t.id in ("gap_direct", "question") for t in W.all_templates())


def test_an_edited_builtin_takes_effect_everywhere_and_reset_undoes_it():
    """An edit that only the editing screen can see is not an edit. An override
    is keyed on the shipped id, so every caller sees it at once, and the shipped
    copy is untouched underneath so no edit is a one-way door."""
    with _empty_store():
        shipped = W.get_template("wa_gap")
        assert shipped == W.WA_BUILTIN_TEMPLATES[0]
        assert not W.is_overridden("wa_gap")

        edited = T.Template(
            id="wa_gap", name="Headline gap, my words", step=0, subject="",
            body="Hi {{first_name}},\n{{sender_name}} at {{company}}.\n\n"
                 "One thing on {{website_domain}}: {{gap_1}}.\n\nAny use?")
        W.save_user_template(edited)

        assert W.get_template("wa_gap") == edited
        assert edited in W.templates_for_step(0)
        assert shipped not in W.templates_for_step(0)
        assert W.is_overridden("wa_gap") and W.is_builtin("wa_gap")
        assert W.WA_BUILTIN_TEMPLATES[0] == shipped
        assert W.WA_TEMPLATES is W.WA_BUILTIN_TEMPLATES

        # It renders, which is the only thing an override is for -- and it still
        # cannot leave without an opt-out line, because that is not the user's
        # to delete.
        text = W.render_wa(W.get_template("wa_gap"), _ctx())
        assert "One thing on acmeplumbing.ca: no online booking." in text, text
        assert W.WA_OPT_OUT_LINE in text, text
        for bad in LEAK_PATTERNS:
            assert bad not in text

        assert W.reset_template("wa_gap") == shipped
        assert not W.is_overridden("wa_gap")
        assert W.load_user_templates() == []
        assert W.delete_user_template("wa_gap") is False
        assert W.reset_template("nothing_by_that_name") is None


def test_a_user_template_survives_a_round_trip():
    """The user's own copy is the one thing here that cannot be regenerated, so
    it goes to disk whole and comes back whole."""
    with _empty_store():
        template_id = W.new_template_id("Site visit angle", 0)
        assert template_id == "site_visit_angle", template_id
        assert not W.is_builtin(template_id)

        written = T.Template(
            id=template_id, name="Site visit angle", step=0, subject="",
            body="Hi {{first_name}},\nIch bin {{sender_name}} von {{company}}.\n\n"
                 "Auf \"{{website_domain}}\" ist mir das aufgefallen: {{gap_1}}.\n\n"
                 "Passt das?")
        W.save_user_template(written)

        W._forget_store()
        assert W.load_user_templates() == [written]
        assert W.get_template(template_id) == written
        assert not W.is_overridden(template_id)

        text = W.render_wa(written, _ctx())
        assert "Auf \"acmeplumbing.ca\" ist mir das aufgefallen" in text, text

        with open(W.store_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
        assert stored["templates"] == [
            {"id": template_id, "name": "Site visit angle", "step": 0,
             "subject": "", "body": written.body}], stored
        assert not os.path.exists(W.store_path() + ".tmp")

        assert W.new_template_id("Site visit angle", 0) == "site_visit_angle_2"
        assert W.new_template_id("Headline gap", 0) == "headline_gap"
        assert W.new_template_id("!!!", 0) == "wa_custom"
        assert W.new_template_id("", 1) == "wa_custom_step1"

        assert W.delete_user_template(template_id) is True
        assert W.get_template(template_id) is None


CORRUPT_STORES = (
    '{"templates": [{"id"', "", "   ", "null", "[]", "not json at all",
    '{"templates": "nope"}', '{"version": 1}', '[1, 2, "three", null]',
    '{"templates": [{"name": "no id at all"}]}', "\x00\x01\x02",
    '{"templates": [{"id": "x", "step": "later", "subject": null, "body": 42}]}',
)


def test_a_corrupt_store_costs_the_edits_and_nothing_else():
    """A file truncated by a crash or opened in an editor loses the user their
    edits. It does not lose them the channel."""
    with _empty_store():
        for raw in CORRUPT_STORES:
            with open(W.store_path(), "w", encoding="utf-8") as handle:
                handle.write(raw)
            W._forget_store()
            assert isinstance(W.load_user_templates(), list), raw[:20]
            assert W.get_template("wa_gap") is not None, raw[:20]
            text = W.render_wa(W.get_template("wa_gap"), _ctx())
            assert text and W.WA_OPT_OUT_LINE in text, raw[:20]


# ── The shape of a message ──


def test_a_message_fits_a_phone():
    """Under sixty words, on every lead in the audit's own gap catalogue and
    with the longest names this app has ever rendered.

    Measured on the message as it arrives, opt-out line included, because that
    is what the reader's phone decides whether to collapse behind Read more. A
    budget that only holds for "Acme Plumbing" and "no online booking" is not a
    budget: `service_1` runs to five words and a gap title to seven.
    """
    worst = (0, "")
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in W.all_templates():
            words = W.word_count(W.render_wa(tpl, ctx))
            assert words < W.WA_MAX_WORDS, (label, tpl.id, words)
            worst = max(worst, (words, "%s / %s" % (label, tpl.id)))

    # Every gap the audit can answer, against the widest lead and profile.
    for code, gap in ANSWERABLE_GAPS.items():
        ctx = _ctx(BIG_LEAD, {"gaps": [dict(gap, code=code)]}, None, BIG_PROFILE, SETTINGS)
        for tpl in W.WA_TEMPLATES:
            words = W.word_count(W.render_wa(tpl, ctx))
            assert words < W.WA_MAX_WORDS, (code, tpl.id, words, ctx["gap_1"])
            worst = max(worst, (words, "%s / %s" % (code, tpl.id)))

    # Margin, not a coincidence. A budget met exactly is a budget the next edit
    # breaks, and the next edit is the user's.
    assert worst[0] <= W.WA_MAX_WORDS - 4, worst
    print("longest WhatsApp message: %d words (%s)" % worst)


def test_there_is_no_subject_no_signature_no_footer_and_no_html():
    """`render_wa` returns one string. Everything `core.templates.render`
    returns beside the body -- the subject, the HTML alternative, the compliance
    footer with its postal address and mailto -- is email machinery, and pasted
    into a chat bubble it is what tells the reader a machine sent this."""
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in W.WA_TEMPLATES:
            text = W.render_wa(tpl, ctx)
            assert isinstance(text, str), (label, tpl.id)
            for markup in ("<", ">", "&nbsp;", "&amp;", "style=", "href="):
                assert markup not in text, (label, tpl.id, markup)
            # The email footer, in all three of its parts.
            for banned in (str(ctx.get("postal_address") or "x"),
                           str(ctx.get("unsubscribe_email") or "x@x"),
                           "unsubscribe", "@", "http://", "https://"):
                assert banned not in text, (label, tpl.id, banned)
            # No signature block: the title and the company-on-its-own line are
            # how an email signs off, and a chat message does not sign off.
            assert str(ctx.get("sender_title") or "\x01") not in text, (label, tpl.id)
            assert not text.endswith(str(ctx.get("company") or "\x01")), (label, tpl.id)


def test_one_question_and_no_link_in_a_first_message():
    """A stranger who asks two things is running an interview, and a stranger
    who opens with a booking link is selling. Neither gets a reply, and on this
    channel a message that gets no reply is a message that gets reported."""
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in W.WA_TEMPLATES:
            text = W.render_wa(tpl, ctx)
            assert text.count("?") <= W.WA_MAX_QUESTIONS, (label, tpl.id, text)
            assert not T._URL_RE.search(text), (label, tpl.id, text)
            if tpl.step == 0:
                assert text.count("?") == 1, (label, tpl.id, text)

    # No template reaches for a calendar link at all, on any step, and the
    # profile that has one does not put it in a message.
    for tpl in W.WA_TEMPLATES:
        assert "{{calendar_link}}" not in tpl.body, tpl.id
        assert "{{company_website}}" not in tpl.body, tpl.id
    ctx = _ctx()
    assert ctx["calendar_link"] == PROFILE["calendar_link"]
    for tpl in W.WA_TEMPLATES:
        assert PROFILE["calendar_link"] not in W.render_wa(tpl, ctx), tpl.id


# ── The opt-out ──


def test_every_message_says_how_to_stop_it():
    """Not a convention the shipped copy happens to keep. `render_wa` appends
    the line to any message that does not already carry one, whatever the user
    wrote, because a cold message with no way out is the one that gets reported
    and a report is what bans a number for good.

    The chaser too, though the spec asks only for the first touch. It is the
    last message in the thread and therefore the one being read by somebody
    choosing between replying and reporting, and eight words is a cheap place to
    put the way out."""
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in W.all_templates():
            text = W.render_wa(tpl, ctx)
            assert W.has_opt_out(text), (label, tpl.id, text)
            assert text.rstrip().endswith(W.WA_OPT_OUT_LINE), (label, tpl.id, text)

    # No shipped template spells the word into its own body: the one the reader
    # is told to send has to be one `wa_opt_out_words` is watching, and a body
    # that hard-codes STOP keeps saying it after the user edits that list.
    for tpl in W.WA_TEMPLATES:
        assert not W.has_opt_out(tpl.body), tpl.id
        assert "STOP" not in tpl.body, tpl.id

    with _empty_store():
        # A user who deletes it still sends one.
        stripped = T.Template(id="wa_gap", name="Mine", step=0, subject="",
                              body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?")
        W.save_user_template(stripped)
        text = W.render_wa(W.get_template("wa_gap"), _ctx())
        assert text.endswith(W.WA_OPT_OUT_LINE), text
        assert text.count("Reply STOP") == 1, text

        # A user who writes their own is not given a second, near-identical one.
        own = T.Template(id="wa_gap", name="Mine", step=0, subject="",
                         body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?\n"
                              "Reply STOP if you would rather I did not.")
        W.save_user_template(own)
        text = W.render_wa(W.get_template("wa_gap"), _ctx())
        assert W.WA_OPT_OUT_LINE not in text, text
        assert text.lower().count("stop") == 1, text


def test_the_word_the_copy_teaches_is_the_word_the_app_honours():
    """The whole point of the line. A message that invites STOP and then keeps
    messaging the person who typed it is worse than one that never offered:
    that reader reports the number, and the report is what ends it.

    So the vocabulary this module writes into the copy is checked against the
    vocabulary `core.whatsapp.matches_opt_out` reads a reply with, and against
    the default `core.settings` ships.
    """
    shipped = ST.DEFAULT_SETTINGS["wa_opt_out_words"]
    for word in W.WA_OPT_OUT_WORDS:
        assert word in shipped, (word, shipped)
        assert WA.matches_opt_out(word.upper(), shipped), word
        assert WA.matches_opt_out("  %s  " % word.title(), shipped), word

    # The reply the shipped line actually asks for, typed the way a person types
    # it on a phone.
    assert "STOP" in W.WA_OPT_OUT_LINE
    for reply in ("STOP", "stop", "Stop", "STOP.", "stop please", "Stop!"):
        assert WA.matches_opt_out(reply, shipped), reply

    # And the guard is an instruction, not the bare word: a message that merely
    # contains "stop" has not told anybody how to leave.
    assert not W.has_opt_out("The van stops outside at nine.")
    assert not W.has_opt_out("We are a one-stop shop for approvals.")
    assert W.has_opt_out("Reply STOP and I will leave it there.")
    assert W.has_opt_out("text stop to end these")
    assert not W.has_opt_out("")

    # A line teaching a word the watcher does not read is not an opt-out line,
    # which is the honest answer: that reader would type it and be messaged
    # again anyway.
    assert not W.has_opt_out("Reply GO AWAY and I will stop.", shipped)

    # And a user who edits the watch list out from under the shipped wording
    # gets a line naming a word their own app will honour, not one it will not.
    assert W.opt_out_line() == W.WA_OPT_OUT_LINE
    assert W.opt_out_line(shipped) == W.WA_OPT_OUT_LINE
    assert W.opt_out_line([]) == W.WA_OPT_OUT_LINE
    narrowed = ["remove me", "do not message"]
    line = W.opt_out_line(narrowed)
    assert "STOP" not in line and "REMOVE ME" in line, line
    assert WA.matches_opt_out("remove me", narrowed)

    tpl = T.Template(id="n", name="N", step=0, subject="",
                     body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?")
    sent = W.render_wa(tpl, _ctx(), narrowed)
    assert sent.endswith(line), sent
    assert "STOP" not in sent, sent
    assert W.has_opt_out(sent, narrowed), sent


# ── Merge fields ──


def test_no_tokens_survive():
    """A live cold message reading "Hi {{first_name}}" burns the number and the
    prospect. Renders through the same `_resolve`/`_tidy` pair the email side
    does, so this is the same guarantee -- asserted here because a second
    renderer that stopped calling them would fail nowhere else."""
    hostile = {
        "business_name": "Bad {{first_name}} Co",
        "first_name": "{business_name}",
        "gap_1": "no booking }} widget",
        "service_1": "{{service_2}}",
        "ai_opener": "We saw {{website_domain}} and [insert city here].",
    }
    contexts = [dict(make()) for _, make in RENDER_CONTEXTS] + [{}, hostile]
    for index, ctx in enumerate(contexts):
        for tpl in W.WA_TEMPLATES:
            text = W.render_wa(tpl, ctx)
            for bad in LEAK_PATTERNS:
                assert bad not in text, (index, tpl.id, bad, text)
            assert "  " not in text, (index, tpl.id, text)
            for orphan in (" .", " ,", " ?", " :", "()", "[]"):
                assert orphan not in text, (index, tpl.id, orphan, text)

    invented = T.Template(id="x", name="x", step=0, subject="",
                          body="Hi {{first_name}},\n\n{{not_a_field}} tail. Any use?")
    text = W.render_wa(invented, _ctx())
    for bad in LEAK_PATTERNS:
        assert bad not in text, text


def test_a_sentence_that_can_vanish_is_the_last_on_its_line():
    """`_tidy` deletes a sentence that lost a merge value and takes no notice of
    what was written after it, so anything standing behind one is left pointing
    at nothing. In sixty words there is no slack to hide that in."""
    shapes = [make() for _, make in RENDER_CONTEXTS] + [T.build_context({}, {}, {}, {}, {})]
    optional = {f for f in T.MERGE_FIELDS
                if any(not str(ctx.get(f) or "").strip() for ctx in shapes)}
    assert {"gap_1", "sender_name", "company"} <= optional, sorted(optional)
    assert not optional & {"first_name", "business_name", "website_domain", "service_1"}

    for tpl in W.WA_TEMPLATES:
        for line in tpl.body.splitlines():
            sentences = re.split(r"(?<=[.!?])\s+", line.strip())
            for sentence in sentences[:-1]:
                used = set(re.findall(r"\{\{([a-z0-9_]+)\}\}", sentence)) & optional
                assert not used, (tpl.id, sorted(used), sentence)

    # End to end: no audit, so the sentence that named the finding is gone and
    # nothing it was holding up is left standing.
    text = W.render_wa(W.get_template("wa_gap"), _ctx(audit={}))
    assert "stood out" not in text, text
    assert "We build automatic follow-ups" in text, text
    assert "Worth a look?" in text, text

    # No sender and no company: the identity line goes whole rather than
    # rendering "This is from ." at the top of a cold message.
    text = W.render_wa(W.get_template("wa_gap"), _ctx(profile={}))
    assert text.startswith("Hi Mike,\n\nI read"), text
    assert "This is" not in text, text


def test_no_orphan_back_references():
    """A sentence that survives alone has to read alone. "That is usually the
    problem" as the first line of a message from an unknown number points at
    nothing, and the reader has no earlier message to look at."""
    pronoun_opener = re.compile(r"(?i)^(?:that|this|those|these|they|it|so)\b")
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in W.WA_TEMPLATES:
            text = W.render_wa(tpl, ctx)
            for block in text.split("\n\n"):
                first = re.split(r"(?<=[.!?])\s+", block.strip())[0].strip()
                assert not pronoun_opener.match(first), (label, tpl.id, first)
            low = text.lower()
            for phrase in ("that step", "the above", "as mentioned", "as i said",
                           "as per my"):
                assert phrase not in low, (label, tpl.id, phrase)


def test_gap_titles_never_govern_a_verb():
    """A gap title is a noun phrase of unknown number: "no online booking" and
    "quotes handled by hand" both land in `gap_1`. Nothing may agree with it, so
    nothing follows it but punctuation."""
    for tpl in W.WA_TEMPLATES:
        for match in re.finditer(r"\{\{gap_[12]\}\}", tpl.body):
            tail = tpl.body[match.end():].lstrip()
            assert not tail or tail[0] in ".,;:!?()", (tpl.id, tail[:40])

    verb = re.compile(r"(?i)\b(?:is|was|are|were|has|have|does|do|sits|means|costs)\b")
    for code, gap in ANSWERABLE_GAPS.items():
        ctx = _ctx(LEAD, {"gaps": [dict(gap, code=code)]})
        title = ctx["gap_1"]
        for tpl in W.WA_TEMPLATES:
            for tail in W.render_wa(tpl, ctx).split(title)[1:]:
                head = tail.split(".")[0]
                assert not verb.search(head), (tpl.id, code, title, head)


def test_service_slots_are_number_neutral():
    """`service_1` is "appointment booking" for one gap code and "HR processes"
    for another, so no copy may agree a verb with it."""
    singular = re.compile(
        r"\{\{service_\d\}\}\s+(?:is|was|has|does|moves|gets|takes|needs|works)\b")
    for tpl in W.WA_TEMPLATES:
        assert not singular.search(tpl.body), tpl.id

    for name in ("HR processes", "AI customer-support agents", "automatic follow-ups",
                 "appointment booking", "invoice processing", "automatically add leads to CRM"):
        ctx = dict(_ctx(), service_1=name)
        for tpl in W.WA_TEMPLATES:
            text = W.render_wa(tpl, ctx)
            assert not re.search(r"%s\s+(?:is|was|has|does|moves)\b" % re.escape(name),
                                 text), (tpl.id, name, text)


# ── What the copy may claim ──


def test_the_offer_comes_from_the_users_own_catalogue_and_does_not_drift():
    """The services are the user's, and they are theirs to edit. This module
    ships copy, not a second gap-to-service table: it reads `service_1..3` off
    the context `core.templates.build_context` built, so renaming a service or
    remapping a gap changes both channels at once and neither can drift."""
    assert not hasattr(W, "GAP_SERVICES"), "a second mapping is a mapping that drifts"
    assert not hasattr(W, "AUTO_ARMY_SERVICES")

    catalogue = {s.lower() for s in T.DEFAULT_SERVICES}
    for code, gap in ANSWERABLE_GAPS.items():
        ctx = _ctx(LEAD, {"gaps": [dict(gap, code=code)]})
        assert ctx["service_1"].lower() in catalogue, (code, ctx["service_1"])

        wa = W.render_wa(W.get_template("wa_gap"), ctx)
        _, email, _ = T.render(T.get_template("gap_direct"), ctx)
        # The same lead is offered the same work on both channels.
        assert ctx["service_1"] in wa, (code, wa)
        assert ctx["service_1"] in email, (code, email)

    # A user who narrows their catalogue narrows it for WhatsApp too.
    narrowed = dict(PROFILE, services=["invoice processing"])
    ctx = _ctx(LEAD, {}, None, narrowed)
    assert "invoice processing" in W.render_wa(W.get_template("wa_gap"), ctx)


def test_no_fabricated_track_record_reaches_the_reader():
    """The `_observed` guard lives in `build_context` and this channel inherits
    it by using the same context rather than re-deriving one. A model sentence
    that claims a rating, a client list or a count of businesses is discarded
    before a merge field ever sees it, and it has to stay discarded here."""
    fabricated = {
        "ok": True,
        "opener": ("Acme Plumbing is the fourth Toronto plumber we have fixed this "
                   "for. We are rated five stars by nine hundred clients and our "
                   "case studies speak for themselves."),
        "ps": "Businesses like yours see results in a week.",
        "subject": "Acme Plumbing joins the firms we have done this for",
    }
    ctx = _ctx(ai=fabricated)
    assert ctx["ai_opener"] == "", ctx["ai_opener"]
    assert ctx["ai_ps"] == "", ctx["ai_ps"]

    invented = re.compile(
        r"(?i)\b(?:rated|stars?|reviews?|case stud|track record|clients?|customers?|"
        r"firms we|businesses we|have done|results)\b")
    holder = T.Template(id="x", name="x", step=0, subject="",
                        body="Hi {{first_name}},\n\n{{ai_opener}} {{gap_1}}.\n\n"
                             "{{ai_ps}} Any use?")
    for tpl in list(W.WA_TEMPLATES) + [holder]:
        text = W.render_wa(tpl, ctx)
        claim = invented.search(text)
        assert claim is None, (tpl.id, claim.group(0) if claim else "", text)

    # And no shipped template carries a record of its own, on any lead.
    record = re.compile(r"(?i)\b(?:we|our|us|I)\b[^.?!]*"
                        r"\b(?:clients?|customers?|worked with|track record|results)\b")
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in W.WA_TEMPLATES:
            claim = record.search(W.render_wa(tpl, ctx))
            assert claim is None, (label, tpl.id, claim.group(0) if claim else "")


def test_no_credential_is_built_out_of_the_lead():
    """`category` comes off a Google Maps record the sender has never seen. "We
    build this for nail salons" is a track record assembled out of the reader's
    own listing, and in sixty words there is no room to use the field safely, so
    the shipped copy does not use it at all."""
    for tpl in W.WA_TEMPLATES:
        assert "{{category}}" not in tpl.body, tpl.id
        assert "{{city}}" not in tpl.body, tpl.id

    # If a user does reach for it, nothing shipped teaches them to put it in a
    # first-person claim -- the rule the email side enforces, held here too.
    lead_fields = re.compile(r"\{\{(?:category|city)\}\}")
    ourselves = re.compile(r"(?i)\b(?:we|our|us)\b|\bI (?:have|had|did|built|helped)\b")
    for tpl in W.WA_TEMPLATES:
        for sentence in re.split(r"(?<=[.!?])\s+", tpl.body):
            if lead_fields.search(sentence):
                assert not ourselves.search(sentence), (tpl.id, sentence)


def test_no_template_asserts_a_site_feature_the_audit_did_not_find():
    """What the crawl established arrives as `gap_1` and nowhere else. A message
    that names the reader's contact form goes to leads whose finding is that
    there is no way to capture a lead at all, to sites that never loaded, and to
    leads with no website -- and cold outreach is mostly those three."""
    feature = re.compile(
        r"(?i)\b(?:the|your|their|its)\s+(?:\w+\s+){0,2}"
        r"(?:forms?|live\s+chat|chat\s+widget|booking|bookings|blog|newsletter|CRM|"
        r"careers\s+page|pricing|price\s+list|analytics|social\s+profiles|"
        r"PDFs?|schema|checkout|shopping\s+cart)\b")
    for tpl in W.WA_TEMPLATES:
        claim = feature.search(tpl.body)
        assert claim is None, (tpl.id, claim.group(0) if claim else "")

    no_capture = dict(A.GAP_CATALOGUE["no_lead_capture"], code="no_lead_capture")
    shapes = (
        ("no way to capture a lead", LEAD, {"gaps": [no_capture]}),
        ("site would not load", LEAD, {}),
        ("no website at all", {"name": "Beeston Joinery", "email": "sam@beeston.co.uk"}, {}),
    )
    for label, lead, audit in shapes:
        ctx = _ctx(lead, audit, None, BARE_PROFILE)
        for tpl in W.WA_TEMPLATES:
            text = W.render_wa(tpl, ctx)
            for found in (ctx.get("gap_1"), ctx.get("gap_1_evidence")):
                if found:
                    text = text.replace(found, " ")
            claim = feature.search(text)
            assert claim is None, (label, tpl.id, claim.group(0) if claim else "")
            assert "form" not in text.lower(), (label, tpl.id, text)


def test_the_audit_gap_is_why_the_message_exists():
    """The specificity is the entire value. Strip the finding out and this is
    indistinguishable from every other message that number has had this week,
    and indistinguishable-from-spam is what gets it reported."""
    for tpl in W.templates_for_step(0):
        assert "{{gap_1}}" in tpl.body, tpl.id
        # And it is named in the opening, not buried: the reader decides in the
        # first two lines, so the finding is in the first block after the name.
        blocks = [b for b in tpl.body.split("\n\n") if b.strip()]
        assert any("{{gap_1}}" in b for b in blocks[:3]), (tpl.id, blocks)

    # An unanswerable gap is not smuggled in as a headline the offer cannot
    # follow from. That is `build_context`'s rule, and this channel inherits it
    # by using the same context rather than choosing a headline of its own.
    #
    # Named rather than derived, on purpose: all three are website work and the
    # catalogue is not a web shop, so a fourth one appearing here is a copy
    # decision somebody has to make deliberately rather than inherit in silence.
    unanswerable = set(A.GAP_CATALOGUE) - set(ANSWERABLE_GAPS)
    assert unanswerable == {"slow_site", "no_mobile", "no_ssl"}, sorted(unanswerable)
    for code in sorted(unanswerable):
        ctx = _ctx(LEAD, {"gaps": [dict(A.GAP_CATALOGUE[code], code=code)]})
        assert ctx["gap_1"] == "", (code, ctx["gap_1"])
        for tpl in W.templates_for_step(0):
            text = W.render_wa(tpl, ctx)
            assert text and W.WA_OPT_OUT_LINE in text, (code, tpl.id)

    # Every finding the audit can answer reaches the reader in the audit's own
    # words, on every first touch.
    for code, gap in ANSWERABLE_GAPS.items():
        ctx = _ctx(LEAD, {"gaps": [dict(gap, code=code)]})
        assert ctx["gap_1"], code
        for tpl in W.templates_for_step(0):
            assert ctx["gap_1"] in W.render_wa(tpl, ctx), (code, tpl.id)


# ── Register ──


# Words a message can share without being the same message. Compared on content
# words only: two forty-word messages that both say "hi", "this", "from" and
# "you" are not the same argument, and a raw overlap on a body this short is
# mostly the greeting.
_FUNCTION_WORDS = frozenset("""
that this from what when with your you the and for but not are was were have has
had will would could should about into onto over under they them their there here
""".split())


def test_the_copy_does_not_read_as_a_mail_merge():
    """Read at 11am on a Tuesday, from a number with no name against it. Every
    phrase below is one a reader has had from six other senders this month, and
    the one that lands is the one that could only have been written to them."""
    banned = (
        "i hope this message finds you well", "i hope this email finds you well",
        "i came across your website", "quick question", "just following up",
        "reaching out", "circle back", "touch base", "act now", "limited time",
        "dear sir", "to whom it may concern", "synergy", "game changer",
        "revolutionary", "kindly", "as per", "hope you are well", "trust this finds",
    )
    for tpl in W.WA_TEMPLATES:
        blob = tpl.body
        for dash in ("—", "–", "‒", "―"):
            assert dash not in blob, (tpl.id, "em dash")
        assert "!" not in blob, tpl.id
        low = blob.lower()
        for phrase in banned:
            assert phrase not in low, (tpl.id, phrase)
        # No shouting. STOP is the one word this channel needs in capitals.
        shouted = {w for w in re.findall(r"\b[A-Z]{4,}\b", blob)
                   if w.lower() not in W.WA_OPT_OUT_WORDS}
        assert not shouted, (tpl.id, shouted)
        # No email sign-off machinery.
        for field in ("{{sender_title}}", "{{postal_address}}", "{{unsubscribe_line}}",
                      "{{ai_subject}}", "{{gap_1_subject}}", "{{ai_ps}}"):
            assert field not in blob, (tpl.id, field)
        # Every field it does reach for is one that belongs on a phone.
        for field in re.findall(r"\{\{([a-z0-9_]+)\}\}", blob):
            assert field in W.WA_MERGE_FIELDS, (tpl.id, field)

    # No figure the sender cannot know. "one" is the only number here: an
    # unhedged count of the reader's own enquiries says the sender guessed.
    quantity = re.compile(
        r"(?i)\b(\d+|one|two|three|four|five|ten|fifteen|twenty|thirty|fifty|hundred)\b")
    for tpl in W.WA_TEMPLATES:
        found = {m.lower() for m in quantity.findall(tpl.body)}
        assert found <= {"one"}, (tpl.id, found - {"one"})

    # The first touches are different arguments, not one message four times.
    first = W.templates_for_step(0)
    written = []
    for tpl in first:
        words = set(re.sub(r"\{\{[^}]*\}\}", " ", tpl.body).lower().split())
        written.append({re.sub(r"[^a-z]", "", w) for w in words
                        if len(w) >= 4 and w not in _FUNCTION_WORDS} - {""})
    for i, a in enumerate(written):
        for j, b in enumerate(written[i + 1:], i + 1):
            overlap = len(a & b) / max(len(a), len(b), 1)
            assert overlap < 0.30, (first[i].id, first[j].id, round(overlap, 2), a & b)


def test_the_message_opens_by_saying_who_is_writing():
    """From an unknown number, an unsigned message is a scam until proven
    otherwise. The name is in the second line, where a person puts it, and it is
    the name and the company rather than a job title and a footer."""
    for tpl in W.templates_for_step(0):
        head = "\n".join(tpl.body.splitlines()[:2])
        assert "{{first_name}}" in head, tpl.id
        assert "{{sender_name}}" in head, tpl.id
        assert "{{company}}" in tpl.body, tpl.id

    ctx = _ctx()
    for tpl in W.templates_for_step(0):
        text = W.render_wa(tpl, ctx)
        assert text.startswith("Hi Mike,\n"), (tpl.id, text[:30])
        assert "Umar Farooq" in text.split("\n\n")[0], (tpl.id, text)

    # A mailbox that is a department is greeted as one, not christened.
    ctx = _ctx({"name": "Beeston Joinery", "email": "frontdesk@beeston.co.uk"}, {})
    for tpl in W.WA_TEMPLATES:
        assert W.render_wa(tpl, ctx).startswith("Hi there,"), tpl.id


# ── Context ──


def test_the_model_line_is_cut_to_phone_length():
    """`ai_opener` is budgeted for an email at 260 characters, which on this
    channel is half the message. It is cut to whole sentences: an observation
    truncated mid-clause reads as the machine it is, and a claim cut in half is
    a claim that got past the guard by losing its second half."""
    long_opener = ("Your emergency page lists four services and every one of them ends "
                   "at the same contact form. Acme Plumbing has no way to take a "
                   "booking after hours, which is when a burst pipe happens. The "
                   "quotes page asks for a phone number and nothing else.")
    ctx = dict(_ctx(), ai_opener=long_opener, ai_ps="P.S. worth a look",
               ai_subject="booking on acmeplumbing.ca",
               unsubscribe_line=T.UNSUBSCRIBE_LINE % "umar@autoarmy.io")
    tight = W.wa_context(ctx)

    assert len(tight["ai_opener"]) <= W.WA_AI_OPENER_MAX, tight["ai_opener"]
    assert tight["ai_opener"].endswith((".", "?")), tight["ai_opener"]
    assert long_opener.startswith(tight["ai_opener"]), tight["ai_opener"]
    assert tight["ai_ps"] == "" and tight["ai_subject"] == ""
    assert tight["unsubscribe_line"] == W.WA_OPT_OUT_LINE
    # Nothing else is touched: the gap, the services and the names are the ones
    # the email path is using for the same lead.
    for key in ("business_name", "first_name", "gap_1", "service_1", "website_domain"):
        assert tight[key] == ctx[key], key

    # A single sentence already over budget is clipped rather than dropped, and
    # closed rather than left hanging.
    one = "x" * 40 + " " + "y" * 200
    clipped = W.wa_context({"ai_opener": one})["ai_opener"]
    assert len(clipped) <= W.WA_AI_OPENER_MAX and clipped.endswith("."), clipped

    # A short opener is left exactly as the guard produced it.
    short = "Your booking page ends at a contact form."
    assert W.wa_context({"ai_opener": short})["ai_opener"] == short


# ── Validation ──


def test_validate_wa_template_warns_and_never_blocks():
    """The editor says what a template will cost before the reply rate does. It
    never refuses: "error" means the reader sees something broken, "warning"
    means it costs a reply or invites a report, and both are the user's call."""
    for tpl in W.WA_TEMPLATES:
        assert W.validate_wa_template(tpl) == [], (tpl.id,
                                                  W.validate_wa_template(tpl))
        assert W.validate_wa_template(tpl, _ctx()) == [], tpl.id

    def _messages(tpl, ctx=None):
        return " | ".join(f["message"] for f in W.validate_wa_template(tpl, ctx))

    def _levels(tpl, ctx=None):
        return {f["level"] for f in W.validate_wa_template(tpl, ctx)}

    # A subject nobody sends.
    tpl = T.Template(id="s", name="S", step=0, subject="a question about you",
                     body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?\n"
                          "Reply STOP and I will leave it.")
    assert "no subject line" in _messages(tpl)

    # A wall.
    tpl = T.Template(id="w", name="W", step=0, subject="",
                     body="Hi {{first_name}},\n\n" + "word " * 60 + "Any use?")
    assert "Read more" in _messages(tpl)
    assert "Read more" in _messages(tpl, _ctx())

    # An interview, and a message with nothing to answer.
    tpl = T.Template(id="q", name="Q", step=0, subject="",
                     body="Hi {{first_name}},\n\nWho picks it up? And how fast? "
                          "Reply STOP to end it.")
    assert "interview" in _messages(tpl)
    tpl = T.Template(id="q2", name="Q2", step=0, subject="",
                     body="Hi {{first_name}},\n\nWe build things. Reply STOP to end it.")
    assert "read and closed" in _messages(tpl)

    # Links, and the calendar link in particular.
    tpl = T.Template(id="l", name="L", step=0, subject="",
                     body="Hi {{first_name}},\n\nSee {{calendar_link}} and "
                          "https://autoarmy.io. Any use? Reply STOP to end it.")
    said = _messages(tpl)
    assert "One at most" in said and "automated blast" in said

    # Email machinery.
    tpl = T.Template(id="e", name="E", step=0, subject="",
                     body="Hi {{first_name}},\n\n{{ai_ps}} {{sender_title}} "
                          "{{postal_address}} Any use? Reply STOP to end it.")
    said = _messages(tpl)
    for field in ("ai_ps", "sender_title", "postal_address"):
        assert field in said, field

    # Markup, and a typo'd merge field.
    tpl = T.Template(id="m", name="M", step=0, subject="",
                     body="Hi {{first_name}},\n\n<b>{{buisness_name}}</b>. Any use? "
                          "Reply STOP to end it.")
    said = _messages(tpl)
    assert "markup" in said and "business_name" in said
    assert "error" in _levels(tpl)

    # A missing opt-out is not a finding: `render_wa` adds one, and a warning on
    # every correct template is a panel the user stops reading.
    tpl = T.Template(id="o", name="O", step=0, subject="",
                     body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?")
    assert W.validate_wa_template(tpl) == [], W.validate_wa_template(tpl)

    # An opt-out naming a word the watcher does not read *is* the finding, and it
    # is the failure the line exists to prevent: the reader types what they were
    # told to, gets the follow-up anyway, and reports the number.
    tpl = T.Template(id="o2", name="O2", step=0, subject="",
                     body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?\n"
                          "Reply REMOVE and I will take you off.")
    said = _messages(tpl)
    assert "REMOVE" in said and "not honoured" in said, said
    assert "error" not in _levels(tpl)

    # STOP is not shouting, and a template that says it is not scolded for it.
    tpl = T.Template(id="c", name="C", step=0, subject="",
                     body="Hi {{first_name}},\n\nNoticed {{gap_1}}. Any use?\n"
                          "Reply STOP and I will leave it.")
    assert W.validate_wa_template(tpl) == [], W.validate_wa_template(tpl)

    # An empty body is the one thing that is an error, and nothing raises.
    assert "error" in _levels(T.Template(id="b", name="B", step=0, subject="", body=""))
    assert W.validate_wa_template({"name": "no id"})[0]["level"] == "error"


def test_nothing_here_raises_whatever_it_is_handed():
    """Nothing on a send path may fail over a copy table. A template that cannot
    be rendered renders as nothing, and the campaign reports a lead it could not
    write for -- it does not lose the run."""
    junk = (None, 0, "", [], {}, {"id": None}, object(),
            T.Template(id="x", name="x", step=0, subject="", body=None or ""),
            T.Template(id="y", name="y", step=-3, subject="x" * 500, body="{{" * 40))
    contexts = (None, {}, {"first_name": None}, {"gap_1": ["a", "list"]},
                {"service_1": {"nested": 1}}, _ctx())
    for tpl in junk:
        for ctx in contexts:
            out = W.render_wa(tpl, ctx)
            assert isinstance(out, str), (tpl, ctx)
            for bad in LEAK_PATTERNS:
                assert bad not in out, (tpl, bad, out)
            assert isinstance(W.validate_wa_template(tpl, ctx), list)
    assert W.word_count(None) == 0
    assert W.has_opt_out(None) is False
    assert isinstance(W.wa_context(None), dict)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as why:
                failures += 1
                print("FAIL", name, why)
    sys.exit(1 if failures else 0)
