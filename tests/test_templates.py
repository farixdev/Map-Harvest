"""Offline tests for core.templates. No network, no Qt.

Run:  venv/Scripts/python.exe -m tests.test_templates
(or `python -m pytest tests/ -q` where pytest is installed).

The bulk of this file guards one failure mode: a merge token reaching a real
inbox. Everything else here is a copy rule the templates must keep obeying as
they get edited.
"""

import atexit
import contextlib
import hashlib
import html as _html
import itertools
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audit as A  # noqa: E402
from core import templates as T  # noqa: E402

# The store must never be the developer's own. `get_template` reads it now, so a
# real ~/.mapharvest/templates.json would rewrite the copy every assertion in
# this file is written against — and in `tests/test_audit.py`, which renders the
# same templates. Redirected at import, which pytest does for every test module
# before it runs the first test in any of them.
_STORE_DIR = tempfile.mkdtemp(prefix="mapharvest-templates-")
atexit.register(shutil.rmtree, _STORE_DIR, ignore_errors=True)
T.TEMPLATES_PATH = os.path.join(_STORE_DIR, "templates.json")

# The department side of the greeting, and the corpus the accept side is checked
# against so the two can never be extended into each other. Every trade, and
# every language a trade works in, invents its own front desk.
ROLE_LOCALS = frozenset("""
quotes quote quotations owner owners estimates estimate manager management director
accounts accounting invoices reservations appointments ops operations dispatch hiring
recruitment newsletter feedback helpdesk client clients info sales careers
frontdesk estimating scheduler surgery parts workshop maintenance lettings
conveyancing servicedesk fleet showroom spares tyres grooming boarding wartung
buchhaltung rezeption kundendienst atelier prenotazioni presupuestos
plumbatorium quotedesk
""".split())

LEAD = {
    "name": "Acme Plumbing & Heating",
    "email": "mike.reid@acmeplumbing.ca",
    "city": "Toronto",
    "category": "Plumber",
    "domain": "acmeplumbing.ca",
    "website": "https://www.acmeplumbing.ca",
}

AUDIT = {
    "final_url": "https://acmeplumbing.ca",
    "gaps": [
        {"code": "no_online_booking", "title": "no online booking", "severity": 3,
         "subject_phrase": "online booking",
         "evidence": "contact form on /contact, no booking widget",
         "services": ["Appointment booking", "Lead Automation"]},
        {"code": "no_crm_signals", "title": "no CRM behind the form", "severity": 3,
         "subject_phrase": "leads from the contact form",
         "evidence": "form posts to a mailbox", "services": ["CRM & Sales Automation"]},
        {"code": "careers_manual", "title": "careers handled manually", "severity": 2,
         "subject_phrase": "the careers page",
         "evidence": "/careers page with a PDF", "services": ["HR processes"]},
    ],
}

AI = {
    "ok": True,
    "subject": "booking on acmeplumbing.ca",
    "opener": "Your emergency page lists four services and every one of them ends at the same contact form.",
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
    "proof_points": [
        "A Mississauga HVAC firm went from replying in a day to replying in four minutes.",
        "One roofing client stopped rekeying quotes into two systems.",
    ],
    "services": [],
}

SETTINGS = {"unsubscribe_mailto": "", "smtp_accounts": [{"email": "umar@autoarmy.io", "enabled": True}]}

FULL_CTX = T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)

LEAK_PATTERNS = ("{{", "}}", "\x00", "{business_name}", "{first_name}", "{gap_1}")


def _core_body(body_text: str, ctx: dict) -> str:
    """The message minus the compliance footer."""
    for marker in (ctx.get("company", "") + " | ", ctx.get("unsubscribe_line", "")):
        if marker.strip() and marker in body_text:
            body_text = body_text.split(marker)[0]
    return body_text.strip()


# ── Catalogue ──


def test_catalogue():
    assert list(T.AUTO_ARMY_SERVICES) == [
        "Workflow Automation", "AI Automation", "Lead Generation", "Lead Automation",
        "CRM & Sales Automation", "Business Process Automation",
        "Web Scraping & Data Automation", "Document Automation", "Marketing Automation",
    ], list(T.AUTO_ARMY_SERVICES)

    counts = {k: len(v) for k, v in T.AUTO_ARMY_SERVICES.items()}
    assert counts == {
        "Workflow Automation": 6, "AI Automation": 10, "Lead Generation": 8,
        "Lead Automation": 10, "CRM & Sales Automation": 6,
        "Business Process Automation": 7, "Web Scraping & Data Automation": 6,
        "Document Automation": 5, "Marketing Automation": 6,
    }, counts

    # Spot-check the seller's own wording survives verbatim.
    assert "AI decision/triage systems" in T.AUTO_ARMY_SERVICES["AI Automation"]
    assert "Google Maps lead generation" in T.AUTO_ARMY_SERVICES["Lead Generation"]
    assert "automatically add leads to CRM" in T.AUTO_ARMY_SERVICES["Lead Automation"]
    assert "purchase/order workflows" in T.AUTO_ARMY_SERVICES["Business Process Automation"]
    assert "price monitoring" in T.AUTO_ARMY_SERVICES["Web Scraping & Data Automation"]

    assert T.PRIORITY_CATEGORIES == ("Business Process Automation", "Web Scraping & Data Automation")

    flat = T.DEFAULT_SERVICES
    assert len(flat) == len({s.lower() for s in flat}), "DEFAULT_SERVICES has duplicates"
    every = {s for names in T.AUTO_ARMY_SERVICES.values() for s in names}
    assert set(flat) == every, "DEFAULT_SERVICES is not the flat catalogue"
    assert flat[0] == "automate repetitive business processes", flat[0]

    ranked = T.top_services(["email campaigns", "attendance", "price monitoring"], limit=3)
    assert ranked[:2] == ["attendance", "price monitoring"], ranked  # priority lines first
    assert T.canonical_service("Appointment Booking") == "appointment booking"
    assert T.canonical_service("Order automation") == "Order automation"  # unknown passes through
    assert T.is_priority_service("payroll workflows") and not T.is_priority_service("email campaigns")
    print("catalogue: OK")


# The gap codes the catalogue has no honest answer for. Page speed and a missing
# mobile layout are front-end work; the seller automates the business behind the
# site and does not build sites. Mapping them anyway is what produced "a slow
# site, so we build approval systems", so they are absent from `GAP_SERVICES` on
# purpose and `core.audit` records them with no services of their own.
NO_OFFER_CODES = frozenset({"slow_site", "no_mobile"})


def test_gap_services():
    spec_codes = {
        "no_online_booking", "no_live_chat", "contact_form_only", "no_crm_signals",
        "no_lead_capture", "quote_by_form", "stale_blog", "no_analytics",
        "careers_manual", "ecommerce_manual", "pdf_forms", "multi_location",
        "no_social_presence", "stale_site", "slow_site", "no_mobile", "no_schema",
        "price_opaque",
    }
    assert NO_OFFER_CODES < spec_codes
    assert spec_codes - NO_OFFER_CODES == set(T.GAP_SERVICES), \
        set(T.GAP_SERVICES) ^ (spec_codes - NO_OFFER_CODES)
    assert {c for c, e in A.GAP_CATALOGUE.items() if not e["services"]} == NO_OFFER_CODES

    # Nothing is invented for them here either, so they contribute no offer and
    # `build_context` will not let them lead an email.
    for code in NO_OFFER_CODES:
        gap = dict(A.GAP_CATALOGUE[code], code=code, evidence="the page says so")
        assert T.services_for_gaps([gap]) == [], code

    # Entries only. A heading in this table reaches `service_1` and offers the
    # reader a section of the seller's catalogue instead of a piece of work.
    for code, names in T.GAP_SERVICES.items():
        for name in names:
            assert name not in T.AUTO_ARMY_SERVICES, (code, name)

    # The two flagged lines must still be reachable from the gap map, not just
    # from the catalogue — as the work under them, which is what is sold.
    for wanted in T.PRIORITY_CATEGORIES:
        hits = [code for code, names in T.GAP_SERVICES.items()
                if any(wanted in T._CATEGORIES_OF.get(n.lower(), ()) for n in names)]
        assert hits, f"{wanted} is not surfaced by any gap"

    services = T.services_for_gaps(AUDIT["gaps"])
    assert services[0] == "appointment booking", services  # audit order wins, catalogue spelling
    assert "automatic follow-ups" in services[:3], services
    assert len(services) == len({s.lower() for s in services}), services

    # A gap with no services of its own still pitches something.
    bare = T.services_for_gaps([{"code": "no_analytics", "title": "no analytics"}])
    assert bare[:3] == ["automated reports", "competitor monitoring",
                        "data collection and cleaning"], bare

    # An unknown code degrades to the top-up list rather than to nothing.
    assert T.services_for_gaps([{"code": "who_knows"}], extra=["invoice processing"]) == \
        ["invoice processing"]
    print("gap services: OK")


def test_no_catalogue_heading_is_offered_to_a_prospect():
    """The keys of `AUTO_ARMY_SERVICES` are section headings over the services,
    and the templates read `service_1..3` inside a sentence. "The fix is Lead
    Automation" offers the reader a line from the seller's own filing instead of
    something that could be built for them, so no heading may survive as far as
    the copy — including the ones `core.audit` legitimately records against a
    gap, and the ones a user can type into their own profile."""
    headings = set(T.AUTO_ARMY_SERVICES)
    entries = {s.lower() for s in T.DEFAULT_SERVICES}
    assert set(T.CATEGORY_SERVICE) == headings, headings ^ set(T.CATEGORY_SERVICE)
    for category, stand_in in T.CATEGORY_SERVICE.items():
        assert stand_in in T.AUTO_ARMY_SERVICES[category], (category, stand_in)
    cases = [(code, dict(entry, code=code, evidence="the page says so"))
             for code, entry in A.GAP_CATALOGUE.items()]
    cases.append(("<no gaps at all>", None))
    assert len(cases) == 19, len(cases)

    for code, gap in cases:
        ctx = T.build_context(LEAD, {"gaps": [gap]} if gap else {}, AI, PROFILE, SETTINGS)
        for slot in ("service_1", "service_2", "service_3"):
            value = ctx[slot]
            assert value not in headings, (code, slot, value)
            assert value.lower() in entries, (code, slot, value)
        for tpl in T.TEMPLATES:
            subject, text, _ = T.render(tpl, ctx)
            for heading in headings:
                assert heading not in subject and heading not in text, (code, tpl.id, heading)

    # A whole line resolves to the work under it that this gap already picked,
    # and to the line's stand-in when there is no gap to go on. A name the
    # catalogue has never heard of is the user's own and is left alone.
    assert T.spoken_service("lead automation", "no_online_booking") == "appointment booking"
    assert T.spoken_service("Web Scraping & Data Automation", "no_schema") == "competitor monitoring"
    assert T.spoken_service("Document Automation") == "automatic document generation"
    assert T.spoken_service("carrier pigeons") == "carrier pigeons"

    profile = dict(PROFILE, services=["Marketing Automation"])
    assert T.build_context(LEAD, {}, {}, profile, SETTINGS)["service_1"] == "email campaigns"
    print("no catalogue heading is offered: OK")


def test_every_value_that_reaches_a_service_slot_is_a_service():
    """The mechanical form of the rule the copy depends on.

    `service_1..3` are read inside a sentence the template already wrote — "the
    fix is ___", "we build ___ and ___" — so whatever lands there has to be a
    thing that gets built. A key of `AUTO_ARMY_SERVICES` is not: it is the
    seller's own filing, and "the fix is Business Process Automation" sells the
    reader a section heading.

    The earlier test walks one gap at a time through one profile. This one walks
    every source a value can come from — the three lookup tables, the audit's own
    per-gap lists, a full multi-gap audit, and five shapes of sender profile
    including one whose services are nothing *but* headings — because a heading
    that only surfaces on an unusual combination is still a heading in a live
    email, and the combination that surfaces it is the one nobody rendered."""
    headings = set(T.AUTO_ARMY_SERVICES)
    entries = {s.lower() for s in T.DEFAULT_SERVICES}

    # The tables first, so a bad row is caught where it was written rather than
    # only when some lead happens to reach it.
    tables = [("DEFAULT_PITCH_SERVICES", T.DEFAULT_PITCH_SERVICES),
              ("CATEGORY_SERVICE", list(T.CATEGORY_SERVICE.values()))]
    tables += [("GAP_SERVICES[%s]" % code, names) for code, names in T.GAP_SERVICES.items()]
    for where, names in tables:
        assert names, where
        for name in names:
            assert name not in headings, (where, name)
            assert name.lower() in entries, (where, name)

    # `core.audit` is allowed to record a whole line against a gap; nothing that
    # comes back out of `spoken_service` may still be one.
    for code, entry in A.GAP_CATALOGUE.items():
        for name in T.services_for_gaps([dict(entry, code=code, evidence="e")]):
            assert name not in headings, (code, name)
            assert name.lower() in entries, (code, name)

    every = [dict(entry, code=code, evidence="the page says so")
             for code, entry in A.GAP_CATALOGUE.items()]
    audits = [("no gaps", {})] + [(g["code"], {"gaps": [g]}) for g in every]
    audits.append(("all eighteen at once", {"gaps": every}))
    profiles = (
        ("no profile", {}),
        ("shipped", PROFILE),
        # The seeded list: every entry, so it is not a choice and the curated
        # pitch leads.
        ("whole catalogue", dict(PROFILE, services=list(T.DEFAULT_SERVICES))),
        # The worst case the GUI can produce: a narrowed list of nothing but
        # headings, which then leads.
        ("headings only", dict(PROFILE, services=list(T.AUTO_ARMY_SERVICES))),
        ("one heading", dict(PROFILE, services=["Business Process Automation"])),
    )
    for label, audit in audits:
        for shape, profile in profiles:
            ctx = T.build_context(LEAD, audit, AI, profile, SETTINGS)
            for slot in ("service_1", "service_2", "service_3"):
                value = ctx[slot]
                assert value, (label, shape, slot)
                assert value not in headings, (label, shape, slot, value)
                assert value.lower() in entries, (label, shape, slot, value)
            for tpl in T.TEMPLATES:
                subject, text, html = T.render(tpl, ctx)
                for heading in headings:
                    assert heading not in subject, (label, shape, tpl.id, heading)
                    assert heading not in text, (label, shape, tpl.id, heading)
                    assert heading not in html, (label, shape, tpl.id, heading)

    # A name the catalogue has never heard of is the user's own wording and is
    # left exactly as typed — it is still not a heading, which is the rule.
    ctx = T.build_context(LEAD, {}, {}, dict(PROFILE, services=["carrier pigeons"]),
                          SETTINGS)
    assert ctx["service_1"] == "carrier pigeons", ctx["service_1"]
    assert ctx["service_1"] not in headings
    print("every value that reaches a service slot is a service: OK")


def test_a_gap_only_leads_when_there_is_an_offer_behind_it():
    """The email names one finding and then names what would be built. A reader
    who cannot draw the line between the two does not read a weak pitch, they
    read a broken mail merge — "a slow site, so we build approval systems" — and
    that is worse than an email that said nothing about their site.

    Page speed and a mobile layout are front-end work and the catalogue does not
    sell it, so those two gaps carry no services, sort behind every gap that
    does, and never reach `gap_1`. Everything the reader is shown as a finding
    has an offer standing behind it, and the two that do not still count towards
    the score and still reach the model's brief."""
    for code in NO_OFFER_CODES:
        assert A.GAP_CATALOGUE[code]["services"] == [], code
        assert code not in T.GAP_SERVICES, code

    for code, entry in A.GAP_CATALOGUE.items():
        gap = dict(entry, code=code, evidence="the page says so")
        ctx = T.build_context(LEAD, {"gaps": [gap]}, {}, PROFILE, SETTINGS)
        leads = code not in NO_OFFER_CODES
        assert bool(ctx["gap_1"]) is leads, (code, ctx["gap_1"])
        assert bool(ctx["gap_1_evidence"]) is leads, code
        assert bool(ctx["gap_1_subject"]) is leads, code
        for tpl in T.TEMPLATES:
            subject, text, _ = T.render(tpl, ctx)
            assert subject and re.search(r"[A-Za-z0-9?]$", subject), (code, tpl.id, subject)
            if leads:
                continue
            # Dropped whole, with the punctuation that belonged to it.
            assert entry["title"] not in text, (code, tpl.id, text)
            assert entry["subject_phrase"] not in subject, (code, tpl.id, subject)
            assert "()" not in text and "\n\n\n" not in text, (code, tpl.id, text)

    # Behind a gap that does map, an unmappable one is supporting detail, not the
    # headline — whichever order it arrives in, because a CSV import or a
    # hand-built lead does not go through `core.audit`'s sort.
    slow = dict(A.GAP_CATALOGUE["slow_site"], code="slow_site", evidence="it takes a while")
    booking = dict(A.GAP_CATALOGUE["no_online_booking"], code="no_online_booking",
                   evidence="the form is the only way to ask for a time")
    for order in ([slow, booking], [booking, slow]):
        ctx = T.build_context(LEAD, {"gaps": order}, {}, PROFILE, SETTINGS)
        assert ctx["gap_1"] == "no online booking", (order, ctx["gap_1"])
        assert ctx["gap_2"] == "a slow site", ctx["gap_2"]
        assert ctx["service_1"] == "appointment booking", ctx["service_1"]

    # And `core.audit` sorts them the same way, so the operator's table and the
    # email agree about which finding is the headline.
    fired = A._gaps(
        {"cms": "custom", "ecommerce": "", "analytics": ["ga4"], "chat": "tawk",
         "booking": "calendly", "crm": "hubspot", "forms": 1, "frameworks": []},
        dict(A._blank("https://x.test/")["signals"], has_contact_form=True,
             has_online_booking=True, has_live_chat=True, has_schema=True,
             has_pricing=True, has_social=True, mobile_viewport=False, slow=True),
        {"call_cta": True, "appointment_shaped": True})
    codes = [g["code"] for g in fired]
    assert set(codes) >= {"no_mobile", "slow_site"}, codes
    assert [c for c in codes if c in NO_OFFER_CODES] == codes[-2:], codes
    print("a gap only leads when there is an offer behind it: OK")


# ── The token-leak guarantee ──


def test_no_tokens_survive():
    hostile = {
        "business_name": "Bad {{first_name}} Co",
        "first_name": "{business_name}",
        "gap_1": "no booking }} widget",
        "ai_opener": "We saw {{website_domain}} and [insert city here].",
        "unsubscribe_line": 'Reply "unsubscribe".',
    }
    contexts = [
        {},
        {"business_name": "Bloor Dental"},
        T.build_context({}, {}, {}, {}, {}),
        T.build_context(LEAD, {}, {}, PROFILE, SETTINGS),
        T.build_context(LEAD, AUDIT, {}, {}, {}),
        FULL_CTX,
        hostile,
    ]
    for ctx in contexts:
        for tpl in T.TEMPLATES:
            subject, text, html = T.render(tpl, ctx)
            for blob, label in ((subject, "subject"), (text, "text"), (html, "html")):
                for bad in LEAK_PATTERNS:
                    assert bad not in blob, f"{tpl.id} leaked {bad!r} in {label}: {blob!r}"
                if label != "html":
                    assert not any("  " in ln for ln in blob.splitlines()), \
                        f"{tpl.id} double space in {label}: {blob!r}"
                assert " ." not in blob and " ," not in blob and " ?" not in blob, \
                    f"{tpl.id} orphan punctuation in {label}: {blob!r}"
                assert "()" not in blob, f"{tpl.id} empty parenthetical in {label}"

    # A template referring to a field nobody defines must not print it either.
    invented = T.Template(id="x", name="x", step=0,
                          subject="hi {{nope}}", body="Hi {{first_name}},\n\n{{not_a_field}} tail.")
    subject, text, html = T.render(invented, FULL_CTX)
    for blob in (subject, text, html):
        for bad in LEAK_PATTERNS:
            assert bad not in blob, blob
    print("no tokens survive: OK")


def test_render_degrades_cleanly():
    ctx = T.build_context(LEAD, {"gaps": [dict(AUDIT["gaps"][0], evidence="")]}, {}, PROFILE, SETTINGS)
    _, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert "no online booking." in text, text          # the empty parenthetical vanished with its space
    assert "()" not in text and " ." not in text

    # Evidence that ends in a full stop must not double it inside the parenthesis,
    # and a verbose audit string is trimmed rather than blowing the word budget.
    long_evidence = "contact form posts to a mailbox with no CRM tag on any of the nine service pages we fetched."
    ctx = T.build_context(LEAD, {"gaps": [dict(AUDIT["gaps"][0], evidence=long_evidence)]},
                          {}, PROFILE, SETTINGS)
    _, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert ".)." not in text and ").." not in text, text
    assert len(ctx["gap_1_evidence"]) <= 95, ctx["gap_1_evidence"]

    # No audit at all: the sentence that needed a gap is gone, not left with a hole.
    # The reason for asking is no longer one of them — it never was a fact about
    # this reader — so it survives the lead that has no audit behind it.
    ctx = T.build_context(LEAD, {}, {}, PROFILE, SETTINGS)
    _, text, _ = T.render(T.get_template("question"), ctx)
    assert "one line stood out" not in text and "before writing" not in text, text
    assert "who picks it up" in text and "I ask because" in text, text

    # No calendar and no website: the CTA survives as a question with no link.
    ctx = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, calendar_link="", website=""), SETTINGS)
    _, text, html = T.render(T.get_template("gap_direct"), ctx)
    assert "Worth fifteen minutes to see if it fits?" in text, text
    assert "http" not in text.split(ctx["unsubscribe_line"])[0], text
    assert html.count("<a ") == 1, html   # the unsubscribe only

    # Missing sender title must not orphan the comma in the sign-off.
    ctx = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, sender_title=""), SETTINGS)
    _, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert "\nAuto Army\n" in text + "\n", text
    print("render degrades cleanly: OK")


# ── Reading the rendered email as the recipient ──

# The shapes every template has to survive. Only the first is the happy path; a
# site that would not load, a lead with no website, a switchboard address and a
# lead whose worst finding is cosmetic are all the common case in cold outreach,
# not edge cases. `BARE_PROFILE` is a seller who has not written a single proof
# point, which is how the profile ships.
BARE_PROFILE = dict(PROFILE, proof_points=[])
SEVERITY_1 = {"code": "no_schema", "title": "nothing search engines can read", "severity": 1,
              "subject_phrase": "search engine markup",
              "evidence": "Google gets no hours, no prices and no reviews from the pages",
              "services": ["SEO automation"]}

RENDER_CONTEXTS = (
    ("full context", lambda: T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)),
    ("no gaps", lambda: T.build_context(LEAD, {}, AI, PROFILE, SETTINGS)),
    ("unreachable site, no proof points",
     lambda: T.build_context(LEAD, {}, {}, BARE_PROFILE, SETTINGS)),
    ("no website at all", lambda: T.build_context(
        {"name": "Beeston Joinery", "email": "quotes@beestonjoinery.co.uk"},
        {}, {}, BARE_PROFILE, SETTINGS)),
    ("role address, no category or city", lambda: T.build_context(
        {"name": "Coastal Fabrication & Welding Incorporated", "email": "frontdesk@coastalfab.ca"},
        {}, {}, BARE_PROFILE, SETTINGS)),
    ("severity-1 headline gap", lambda: T.build_context(
        LEAD, {"gaps": [SEVERITY_1]}, AI, PROFILE, SETTINGS)),
)


def test_no_orphan_back_references():
    """A sentence whose merge value vanished is deleted whole, so nothing may
    point back at it. "Right now that depends on someone remembering" opening an
    email is the reader's first line, and "that" has no antecedent."""
    pronoun_opener = re.compile(r"(?i)^(?:that|this|those|these|they|it|so)\b")
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in T.TEMPLATES:
            _, text, _ = T.render(tpl, ctx)
            body = _core_body(text, ctx)
            for sentence in re.split(r"(?<=[.!?])\s+", body):
                sentence = sentence.strip()
                assert not pronoun_opener.match(sentence), (label, tpl.id, sentence)
            low = body.lower()
            for phrase in ("that step", "that depends", "the above", "as mentioned",
                           "as i said"):
                assert phrase not in low, (label, tpl.id, phrase)


def _optional_fields() -> set:
    """Merge fields `build_context` can hand back empty.

    Discovered, not listed: an empty lead against an empty profile is the widest
    hole the renderer ever sees, and whatever survives it is a field carrying a
    fallback. A fallback deleted from `build_context` therefore turns its field
    optional here, and the rule below starts guarding the templates that use it.
    """
    shapes = [make() for _, make in RENDER_CONTEXTS] + [T.build_context({}, {}, {}, {}, {})]
    return {f for f in T.MERGE_FIELDS
            if any(not str(ctx.get(f) or "").strip() for ctx in shapes)}


def test_a_sentence_that_can_vanish_is_the_last_on_its_line():
    """`_drop_holed_sentences` deletes a sentence that lost a value, and it takes
    no notice of what was written after it. "I ask because {{gap_1}} is usually
    what sits behind a slow reply. If yours is already covered, say so" loses the
    first half on any lead with no gaps and sends the second half on its own,
    where "yours" points at nothing. So a sentence holding a field that can go
    empty is written last on its line, and two sentences that need each other are
    written as one."""
    optional = _optional_fields()
    assert "gap_1" in optional and "proof_point" in optional, sorted(optional)
    assert not optional & {"business_name", "first_name", "city", "category",
                           "website_domain", "service_1", "unsubscribe_line"}, sorted(optional)

    for tpl in T.TEMPLATES:
        for line in tpl.body.splitlines():
            sentences = re.split(r"(?<=[.!?])\s+", line.strip())
            for sentence in sentences[:-1]:
                used = set(re.findall(r"\{\{([a-z0-9_]+)\}\}", sentence)) & optional
                assert not used, (tpl.id, sorted(used), sentence)

    # The R5 case end to end: no gap, so the sentence goes, and nothing it was
    # holding up is left standing.
    ctx = T.build_context(LEAD, {}, {}, BARE_PROFILE, SETTINGS)
    _, text, _ = T.render(T.get_template("question"), ctx)
    assert "one line stood out" not in text, text
    assert "If yours" not in text, text
    assert "where does it land, and who picks it up?" in text, text
    assert "say so and I will leave it there." in text, text


def test_gap_titles_never_govern_a_verb():
    """A gap title is a noun phrase of unknown number: "no online booking" and
    "quotes handled by hand" both land in `gap_1`. Nothing may agree with it, so
    nothing may follow it — the templates put a full stop or a bracket there."""
    for tpl in T.TEMPLATES:
        for match in re.finditer(r"\{\{gap_[12]\}\}", tpl.subject + "\n" + tpl.body):
            tail = (tpl.subject + "\n" + tpl.body)[match.end():].lstrip()
            assert not tail or tail[0] in ".,;:!?()", (tpl.id, tail[:40])

    plural = ["quotes handled by hand", "careers handled manually", "orders handled by hand",
              "no social profiles linked", "several locations, one inbox"]
    singular = ["no online booking", "a slow site", "nothing search engines can read"]
    verb = re.compile(r"(?i)\b(?:is|was|are|were|has|have|does|do|sits|means|costs)\b")
    for title in plural + singular:
        ctx = dict(FULL_CTX, gap_1=title)
        for tpl in T.TEMPLATES:
            _, text, _ = T.render(tpl, ctx)
            after = text.split(title)
            for tail in after[1:]:
                head = tail.split(".")[0]
                assert not verb.search(head), (tpl.id, title, head)


def test_category_only_ever_qualifies_a_noun():
    """`category` falls back to "local", which is an adjective. A template that
    puts it in a noun slot renders "the local that answers first"."""
    for tpl in T.TEMPLATES:
        for match in re.finditer(r"\{\{category\}\}\s*(\S*)", tpl.subject + "\n" + tpl.body):
            assert re.match(r"[a-z]", match.group(1)), (tpl.id, match.group(0))

    ctx = T.build_context({"name": "Beeston Joinery", "email": "a@b.ca"}, {}, {}, PROFILE, SETTINGS)
    assert ctx["category"] == "local"
    for tpl in T.TEMPLATES:
        _, text, _ = T.render(tpl, ctx)
        stray = re.search(r"\blocal\b(?!\s+(?:business|businesses|firm|firms|area))", text)
        assert stray is None, (tpl.id, text)


def test_service_slots_are_number_neutral():
    """`service_1` is plural for a third of the gap codes and for the no-gap
    default, so no template may agree a verb with it."""
    singular = re.compile(r"\{\{service_\d\}\}\s+(?:is|was|has|does|moves|gets|takes|needs|works)\b")
    for tpl in T.TEMPLATES:
        assert not singular.search(tpl.body), tpl.id

    plurals = ["HR processes", "AI customer-support agents", "purchase/order workflows",
               "automatic follow-ups", "sales pipelines"]
    singulars = ["appointment booking", "email finding", "invoice processing"]
    for name in plurals + singulars:
        ctx = dict(FULL_CTX, service_1=name)
        for tpl in T.TEMPLATES:
            _, text, _ = T.render(tpl, ctx)
            assert not re.search(r"%s\s+(?:is|was|has|does|moves)\b" % re.escape(name), text), \
                (tpl.id, name, text)


def test_templates_invent_no_figures():
    """The sender knows their own ask. They do not know the reader's inbound
    volume, and an unhedged number says they guessed."""
    quantity = re.compile(
        r"(?i)\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|"
        r"thirty|forty|fifty|sixty|hundred)\b")
    allowed = {"one", "fifteen"}          # "One thing stands out", "Worth fifteen minutes"
    for tpl in T.TEMPLATES:
        found = {m.lower() for m in quantity.findall(tpl.subject + "\n" + tpl.body)}
        assert found <= allowed, (tpl.id, found - allowed)

    # It must not claim a contact form on a site the crawler never loaded.
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        _, text, _ = T.render(T.get_template("time_saved"), ctx)
        for claim in ("through the site", "gets through", "a month is"):
            assert claim not in text, (label, claim, text)


def test_no_credential_is_built_out_of_the_lead():
    """`category` and `city` come off a Google Maps record the sender has never
    seen. "We build this for nail salon businesses in Windsor" and "across the
    metal fabricator firms we have done this for" both read as a track record,
    and both were assembled from the reader's own listing. What the sender has
    actually done lives in `proof_points`, in the sender's words, and when the
    profile has none the claim is not made at all."""
    lead_fields = re.compile(r"\{\{(?:category|city)\}\}")
    ourselves = re.compile(r"(?i)\b(?:we|our|us)\b|\bI (?:have|had|did|built|helped|worked)\b")
    for tpl in T.TEMPLATES:
        for sentence in re.split(r"(?<=[.!?])\s+", tpl.body):
            if lead_fields.search(sentence):
                assert not ourselves.search(sentence), (tpl.id, sentence)

    # No template carries a track record of its own, in any rendering.
    record = re.compile(
        r"(?i)\b(?:we|our|us|I)\b[^.?!]*\b(?:have done|done this|clients?|customers?|"
        r"firms we|businesses we|worked with|track record|results|case study)\b")
    for label, make in RENDER_CONTEXTS:
        ctx = make()
        for tpl in T.TEMPLATES:
            _, text, _ = T.render(tpl, ctx)
            body = _core_body(text, ctx)
            claimed = record.search(body)
            assert not claimed or claimed.group(0) in (ctx.get("proof_point") or "x"), \
                (label, tpl.id, claimed.group(0) if claimed else "")

    # With proof points, the sender's own sentence is what carries the credit.
    ctx = T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)
    for template_id in ("gap_direct", "time_saved"):
        _, text, _ = T.render(T.get_template(template_id), ctx)
        assert ctx["proof_point"] in text, (template_id, text)
        assert ctx["proof_point"] in PROFILE["proof_points"]

    # Without them, the line is gone rather than replaced by something invented.
    bare = T.build_context(LEAD, AUDIT, AI, BARE_PROFILE, SETTINGS)
    assert bare["proof_point"] == ""
    for template_id in ("gap_direct", "time_saved"):
        _, text, _ = T.render(T.get_template(template_id), bare)
        assert "\n\n\n" not in text, (template_id, text)
        for invented in ("we have done", "firms we", "clients", "on average", "typically saves"):
            assert invented not in text.lower(), (template_id, invented)


def test_role_addresses_are_not_greeted_as_people():
    """The greeting is a positive test, not a list of departments to avoid: a
    mailbox is read as a person only when it opens on a name we recognise. A
    denylist has to guess what the next trade calls its front desk, in every
    language it trades in, and "Hi Surgery," goes out the day it guesses wrong."""
    # `ROLE_LOCALS` carries the ones a denylist did not think of — a UK dental
    # practice, a body shop, a letting agent, a German workshop, a French studio
    # — and one department nobody has invented yet.
    for local in sorted(ROLE_LOCALS):
        ctx = T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})
        assert ctx["first_name"] == "there", (local, ctx["first_name"])
        _, text, _ = T.render(T.get_template("gap_direct"), ctx)
        assert text.startswith("Hi there,"), (local, text[:24])

    # The local part is split on separators first, so a role word in front still
    # decides it, and a role word behind a name cannot rescue itself either.
    for local in ("quote-requests", "owner.direct", "estimates+web", "front.desk",
                  "sales.team", "service.desk", "j.smith", "info.uk"):
        assert T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})["first_name"] == "there", local

    # And a real mailbox is still greeted by name, wherever the name is from.
    people = (("sarah", "Sarah"), ("mike.reid", "Mike"), ("dev_patel", "Dev"),
              ("priya", "Priya"), ("yusuf", "Yusuf"), ("siobhan", "Siobhan"),
              ("bjorn", "Bjorn"), ("wei.zhang", "Wei"), ("juan.perez", "Juan"),
              ("jean-luc", "Jean"), ("magdalena", "Magdalena"), ("mohammed", "Mohammed"),
              ("Sarah.OBrien", "Sarah"))
    for local, expected in people:
        assert T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})["first_name"] == expected, local

    # A name somebody typed into the record is taken at its word either way.
    assert T.build_context({"contact_name": "Nkechi Okafor"}, {}, {}, {}, {})["first_name"] == "Nkechi"


def test_no_template_asserts_a_site_feature_the_audit_did_not_find():
    """The `question` template opened "When someone sends the form on
    {{website_domain}}" and closed "If the form already lands somewhere that
    chases it". Nothing had checked. On a lead whose headline gap is
    `no_lead_capture` the email asked about a form twice and reported there is no
    way to capture a lead, in the same message; on a site that would not load and
    on a lead with no website — both the common case in cold outreach — it
    invented the form outright.

    What the audit found reaches the copy as `gap_1` and nowhere else. Everything
    else the copy wants to know about their site it has to ask for, so no
    template may name an audited site feature as a thing the reader has."""
    # Exactly the features the audit exists to decide, and only with a determiner
    # that makes naming one a claim. "a form" in a question is asking.
    feature = re.compile(
        r"(?i)\b(?:the|your|their|its)\s+(?:\w+\s+){0,2}"
        r"(?:forms?|live\s+chat|chat\s+widget|booking|bookings|blog|newsletter|CRM|"
        r"careers\s+page|pricing|price\s+list|analytics|social\s+profiles|"
        r"PDFs?|schema|checkout|shopping\s+cart)\b")
    for tpl in T.TEMPLATES:
        claim = feature.search(tpl.subject + "\n" + tpl.body)
        assert claim is None, (tpl.id, claim.group(0) if claim else "")

    # End to end on the lead that made it plain, and on the two shapes that have
    # no site to have a feature on.
    no_capture = dict(A.GAP_CATALOGUE["no_lead_capture"], code="no_lead_capture",
                      evidence="no form, no newsletter, no phone link on any page")
    shapes = (
        ("no way to capture a lead", LEAD, {"gaps": [no_capture]}),
        ("site would not load", LEAD, {}),
        ("no website at all", {"name": "Beeston Joinery", "email": "sam@beeston.co.uk"}, {}),
    )
    for label, lead, audit in shapes:
        ctx = T.build_context(lead, audit, {}, BARE_PROFILE, SETTINGS)
        for tpl in T.TEMPLATES:
            _, text, _ = T.render(tpl, ctx)
            written = _core_body(text, ctx)
            # What the audit established may name a form; the copy around it is
            # what invented one, so the audit's own strings come out first.
            for found in (ctx.get("gap_1"), ctx.get("gap_1_evidence")):
                if found:
                    written = written.replace(found, " ")
            claim = feature.search(written)
            assert claim is None, (label, tpl.id, claim.group(0) if claim else "")
            assert "form" not in written.lower(), (label, tpl.id, written)

    # The audit's own finding still reaches the reader, whatever it was.
    assert "no way to capture a lead" in _core_body(
        T.render(T.get_template("question"),
                 T.build_context(LEAD, {"gaps": [no_capture]}, {}, PROFILE, SETTINGS))[1],
        FULL_CTX)


def test_every_gap_reads_as_a_reason_in_the_question_template():
    """"I ask because {{category}} businesses that answer first tend to get the
    job, and because of what I noticed on the way through: {{gap_1}}" made the
    headline gap the reason to ask how fast a contact form is answered. Six of
    the eighteen gap codes are about the lead route. The rest rendered
    "...answer first tend to get the job, and because of what I noticed on the
    way through: a blog that has gone quiet."

    Why the question is worth asking is not a finding about this reader, and it
    no longer claims to be one. All eighteen are rendered here."""
    assert len(A.GAP_CATALOGUE) == 18, len(A.GAP_CATALOGUE)
    seen = 0
    for code, spec in A.GAP_CATALOGUE.items():
        gap = dict(spec, code=code, evidence="what the crawl saw")
        ctx = T.build_context(LEAD, {"gaps": [gap]}, {}, PROFILE, SETTINGS)
        _, text, _ = T.render(T.get_template("question"), ctx)
        body = _core_body(text, ctx)
        seen += 1

        reason = [ln for ln in body.splitlines() if "I ask because" in ln]
        assert len(reason) == 1, (code, body)
        # The reason stands on its own: it does not carry the gap, and it does
        # not become a non-sequitur when the gap it used to carry is unrelated.
        assert reason[0].endswith("."), (code, reason[0])

        if code in NO_OFFER_CODES:
            # Nothing to offer against it, so it is never the finding the email
            # opens on — and the sentence that would have carried it goes with
            # it whole, rather than standing there naming a problem the next
            # paragraph does not answer.
            assert ctx["gap_1"] == "", code
            assert spec["title"] not in body, (code, body)
            continue

        assert ctx["gap_1"] == spec["title"], code
        assert ctx["gap_1"] not in reason[0], (code, reason[0])

        # The gap is reported, in a sentence of its own, ending on a full stop.
        finding = [ln for ln in body.splitlines() if ctx["gap_1"] in ln]
        assert len(finding) == 1, (code, body)
        assert finding[0].endswith(ctx["gap_1"] + "."), (code, finding[0])
        assert finding[0] != reason[0], code
    assert seen == 18, seen


def test_a_doubled_greeting_is_stripped_however_many_deep():
    """`_clean_snippet` matched the greeting once. The opener is pasted straight
    under "Hi Rob,", so a model that wrote "Hi Jane Smith, hi again," had one
    greeting taken and shipped the other under the real one. Keeping the second
    is worse than keeping none: the name in it is whichever name the model
    invented, and it is the first word the reader reads."""
    for opener, expected in (
        ("Hi Jane Smith, hi again, your form is manual.", "Your form is manual."),
        ("Hi Jane, hello, your form is manual.", "Your form is manual."),
        ("Hey there, hi Rob, the quote page is a PDF.", "The quote page is a PDF."),
        ("Hello, dear Ms Reid: good morning, your form has nine fields.",
         "Your form has nine fields."),
        ("Hi, hi, hi, the booking page is buried.", "The booking page is buried."),
    ):
        assert T._clean_snippet(opener) == expected, opener

    # One greeting still goes, and a line that only looks like one is left alone.
    assert T._clean_snippet("Hi Rob, your site loads fast.") == "Your site loads fast."
    assert T._clean_snippet("Hidden costs are what the page leads with.") == \
        "Hidden costs are what the page leads with."
    assert T._clean_snippet("Hire us in March for the rebuild.") == \
        "Hire us in March for the rebuild."

    # End to end: exactly one greeting reaches the reader, and it is the one
    # `build_context` resolved from the lead.
    ctx = T.build_context(LEAD, AUDIT,
                          {"opener": "Hi Jane Smith, hi again, your booking page is manual."},
                          PROFILE, SETTINGS)
    _, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert text.startswith("Hi Mike,\n\nYour booking page is manual."), text[:64]
    assert not re.search(r"(?im)^(?:hi|hey|hello|dear|greetings)\b", text[len("Hi Mike,"):]), text


def test_short_owner_mailboxes_are_greeted_by_name():
    """`_GIVEN_NAMES` carried robert, roberta and roberto, and not rob. The
    clipped form is how a one-to-five-person business spells the owner's own
    mailbox, and that owner is the best lead in the file, so twenty-two of forty
    sampled short names opened "Hi there,".

    A full name is easy to think of and its clipping is not, which is why the
    short forms are a list of their own with rules of their own: short, a person,
    and never a job."""
    sampled = ("rob bob joe pete ray ed al pat jo li gus vic abe moe sid nat gil hal "
               "wes kip cy jed dan tom tim jim ken sam max ron leo ian jay lee kim amy "
               "eva ava ida ivy").split()
    assert len(sampled) == 40 and len(set(sampled)) == 40, len(sampled)
    for local in sampled:
        ctx = T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})
        assert ctx["first_name"] == local.capitalize(), (local, ctx["first_name"])
        _, text, _ = T.render(T.get_template("gap_direct"), ctx)
        assert text.startswith("Hi %s,\n" % local.capitalize()), (local, text[:20])

    # The same mailbox with a surname behind it, which is the other spelling.
    for local, expected in (("rob.feldman", "Rob"), ("joe_oshea", "Joe"),
                            ("pat-nolan", "Pat"), ("ed.tan", "Ed"), ("gus.moreau", "Gus")):
        assert T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})["first_name"] \
            == expected, local

    # The structural half. The short list stays short, stays people, and cannot
    # be extended into the department side, which is the only thing that makes
    # extending it safe: a wrong name here greets a filing cabinet.
    assert T._SHORT_FORMS <= T._GIVEN_NAMES
    assert not (T._SHORT_FORMS & ROLE_LOCALS), sorted(T._SHORT_FORMS & ROLE_LOCALS)
    for name in sorted(T._SHORT_FORMS):
        assert name == name.lower() and 2 <= len(name) <= 5, name
        assert T._NAME_RE.match(name), name
        assert T.build_context({"email": name + "@x.ca"}, {}, {}, {}, {})["first_name"] \
            == name.capitalize(), name
    # And the department side is unharmed by every name just added.
    for local in sorted(ROLE_LOCALS):
        assert T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})["first_name"] \
            == "there", local


def test_east_asian_mailboxes_are_greeted_by_name():
    """The main list was assembled out of European and South Asian names, so
    hiroshi.tanaka@ was greeted "Hi there,". It fails safe, which is why it waited
    this long, but the safe failure is still a worse email.

    The rule that makes the addition safe is the same one that governs the short
    forms: nothing here may be a function a business hands an address to. Two- and
    three-letter romanisations are where a name and some other language's word for
    a front desk collide, so they are not on the list at all."""
    for local, expected in (("hiroshi.tanaka", "Hiroshi"), ("yuki", "Yuki"),
                            ("takeshi", "Takeshi"), ("seoyeon.kim", "Seoyeon"),
                            ("minjun", "Minjun"), ("xiaoming.li", "Xiaoming"),
                            ("thanh.nguyen", "Thanh"), ("kazuko", "Kazuko")):
        assert T.build_context({"email": local + "@x.jp"}, {}, {}, {}, {})["first_name"] \
            == expected, local

    for name in sorted(T._EAST_ASIAN_NAMES):
        assert name == name.lower() and 4 <= len(name) <= 9, name
        assert T._NAME_RE.match(name), name
        assert T.build_context({"email": name + "@x.jp"}, {}, {}, {}, {})["first_name"] \
            == name.capitalize(), name

    # The department side is where this list would do damage, and it does none.
    assert not (T._EAST_ASIAN_NAMES & ROLE_LOCALS), sorted(T._EAST_ASIAN_NAMES & ROLE_LOCALS)
    for local in sorted(ROLE_LOCALS):
        assert T.build_context({"email": local + "@x.ca"}, {}, {}, {}, {})["first_name"] \
            == "there", local


def test_a_model_track_record_never_reaches_the_reader():
    """`core.ai`'s prompt asks for "a concrete thing you noticed on their site".
    Asking is not enforcement — the same distinction this module already makes
    for greetings and exclamation marks, which it does enforce. A model with
    nothing to say invents the credit the sender has not earned, and it ships
    under the sender's own name and signature.

    Rounds 1-4 filtered this with a denylist of phrasings, and each round it was
    beaten by the next way of saying the same thing. The last version matched
    first-person forms only, so fifteen third-person claims walked through
    verbatim, including ones naming the sender's own company.

    A denylist has to describe every way of writing a lie. The accept side only
    has to describe what the sender actually knows about the reader: the name on
    the record, the host that was crawled, and the gap the crawl found. A
    sentence referring to none of those is not an observation, whatever else it
    is, and every field this guards is optional copy."""
    fabricated = (
        # The fifteen that survived the first-person filter verbatim.
        "Auto Army has automated intake for eleven joiners in Leeds already.",
        "Twelve other joiners in Leeds already run this exact setup.",
        "The last four joinery clients saw quotes go out same day.",
        "Trusted by joiners across the north of England.",
        "Rated 5 stars by 60 small businesses.",
        "auto army has automated intake for eleven joiners in leeds already.",
        "Joinery firms across Yorkshire have switched to this.",
        "Over 200 tradespeople rely on this every week.",
        "The team has delivered this for dozens of workshops.",
        "This has saved joiners in Leeds four hours a week.",
        "Several fabricators in the north saw quotes go out same day.",
        "Auto Army built the same intake for a cabinetmaker nearby.",
        "Every client who switched cut their reply time in half.",
        "It is the setup most joinery firms in Leeds now use.",
        "Case studies from the trade back this up.",
        # Fifteen more, several of which name no company at all.
        "Nine out of ten workshops see replies inside a minute.",
        "Businesses like this one typically save six hours a week.",
        "A neighbouring workshop went from a day to four minutes.",
        "Recognised as the leading automation partner for the trade.",
        "Thousands of enquiries are handled this way every month.",
        "Our track record in joinery speaks for itself.",
        "We have automated intake for eleven plumbers in Leeds already.",
        "I set this up for a joinery firm last month.",
        "Independent reviews put this at the top of the category.",
        "The last three installs paid for themselves in a fortnight.",
        "Word of mouth in Leeds has been strong.",
        "It has been proven across the north of England.",
        "Awarded best automation supplier two years running.",
        "Most workshops that try it never go back.",
        "There is a waiting list of joiners for this.",
    )
    assert len(fabricated) == 30 and len(set(fabricated)) == 30, len(fabricated)

    lead = {"name": "Harbourvale Joinery", "email": "rob@harbourvale.co.uk",
            "website": "https://harbourvale.co.uk", "category": "Joiner", "city": "Leeds"}
    audit = {"final_url": "https://harbourvale.co.uk", "gaps": AUDIT["gaps"]}

    # End to end, through every field the model fills and every template.
    for claim in fabricated:
        ctx = T.build_context(lead, audit,
                              {"subject": claim, "opener": claim, "ps": claim},
                              PROFILE, SETTINGS)
        assert ctx["ai_opener"] == "" and ctx["ai_ps"] == "" and ctx["ai_subject"] == "", claim
        for tpl in T.TEMPLATES:
            subject, text, _ = T.render(tpl, ctx)
            body = _core_body(text, ctx).lower()
            assert subject, (tpl.id, claim)          # the template subject takes over
            for fragment in ("joiners", "clients", "trusted by", "rated ", "case stud",
                             "dozens", "track record", "awarded", "recognised",
                             "automated intake", "workshop", "tradespeople"):
                assert fragment not in body, (tpl.id, claim, fragment)

    # What the prompt actually asked for survives, first person and all: each of
    # these names the business, the host that was crawled, or the gap found on it.
    anchors = T._recipient_anchors("Harbourvale Joinery", "harbourvale.co.uk",
                                   audit["gaps"])
    for kept in (
        "Your booking page still ends at a plain contact form.",
        "harbourvale.co.uk has no way to take an online booking.",
        "Harbourvale Joinery lists four services and every one ends at the same form.",
        "I noticed the online booking link goes to a dead calendar.",
        "The contact form on harbourvale.co.uk asks for nine fields.",
    ):
        assert T._observed(kept, anchors) == kept, (kept, T._observed(kept, anchors))

    # A mixed reply loses the invented half and keeps the observed one.
    mixed = ("Your contact form posts to a mailbox. We have automated intake for "
             "eleven joiners in Leeds already.")
    assert T._observed(mixed, anchors) == "Your contact form posts to a mailbox.", \
        T._observed(mixed, anchors)

    # The sender's own company is not a reference to the reader. That is the hole
    # the first-person filter left, and it is the first claim in the list above.
    assert not any("army" in anchor for anchor in anchors), sorted(anchors)

    # With nothing known about the recipient there is nothing to observe, so the
    # whole field goes rather than a plausible sentence being let through.
    assert T._recipient_anchors("", "", []) == frozenset()
    assert T._observed("Your booking page is buried.", frozenset()) == ""
    bare = T.build_context({"email": "a@b.ca"}, {},
                           {"subject": "x", "opener": "Your booking page is buried.",
                            "ps": "y"}, PROFILE, SETTINGS)
    assert bare["ai_opener"] == "" and bare["ai_ps"] == "" and bare["ai_subject"] == ""

    # A name is not an anchor word by word: "Acme Plumbing" must not make
    # "plumbing" evidence, or "I set this up for a plumbing firm last month"
    # reads as an observation about Acme.
    acme = T._recipient_anchors("Acme Plumbing & Heating", "", [])
    assert acme == frozenset({"acme plumbing heating"}), sorted(acme)
    assert T._observed("I set this up for a plumbing firm last month.", acme) == ""


# Twenty more, and every one of them anchors: each names the business on the
# record, the host that was crawled, or the gap found on it, so the accept-list
# alone keeps all twenty. First person and third, numeric and testimonial, in
# the three languages the copy is written in.
ANCHORED_FABRICATIONS = (
    # First person, English.
    "Harbourvale Joinery would be the eleventh joinery firm in Leeds we have set this up for.",
    "Your booking page is the same one we fixed for four other Leeds joiners.",
    "I rebuilt the same online booking page for four other joiners last month.",
    "We have already automated the contact form on sites like harbourvale.co.uk twelve times.",
    "Your online booking gap is one our clients in Leeds all had before we solved it.",
    # Third person, English, numeric.
    "Harbourvale Joinery joins a long list of joinery firms that fixed their "
    "online booking with us.",
    "The contact form on harbourvale.co.uk is the same one twelve other firms "
    "replaced last quarter.",
    "Auto Army has rebuilt online booking for dozens of firms like Harbourvale Joinery.",
    "Nine out of ten firms with the contact form harbourvale.co.uk uses switched "
    "within a week.",
    "Most workshops running the contact form on harbourvale.co.uk never go back "
    "after switching.",
    # Third person, English, testimonial.
    "Harbourvale Joinery would join the five-star reviews already left for this booking fix.",
    "The online booking problem at Harbourvale Joinery has a proven fix with a "
    "two-year track record.",
    "Harbourvale Joinery is on a waiting list of joinery firms wanting online booking.",
    "Harbourvale Joinery deserves the award-winning booking setup used elsewhere in Leeds.",
    # French.
    "Nous avons déjà mis en place la réservation en ligne pour onze entreprises "
    "comme Harbourvale Joinery.",
    "Harbourvale Joinery serait la douzième entreprise de Leeds à nous confier "
    "son formulaire de contact.",
    "Le formulaire de contact de harbourvale.co.uk est le même que celui que nous "
    "avons corrigé pour quatre autres ateliers.",
    # German.
    "Wir haben die Online-Buchung für elf andere Tischlereien wie Harbourvale "
    "Joinery bereits eingerichtet.",
    "Harbourvale Joinery wäre der zwölfte Betrieb in Leeds, dem wir das "
    "Kontaktformular abgenommen haben.",
    "Von über 200 Handwerksbetrieben wie Harbourvale Joinery wird diese "
    "Online-Buchung empfohlen.",
)

def test_a_fabricated_record_that_also_anchors_never_reaches_the_reader():
    """The accept-list asks whether a sentence refers to the reader. It never
    asked whether the same sentence also claims something about the sender, and
    one doing both walked through: "Harbourvale Joinery is the eleventh joinery
    firm in Leeds we have set this up for" names the reader in three words.

    Both halves are required now. The first assertion below is the point of the
    corpus — every case anchors, so round 5's rule kept all twenty verbatim, and
    they are only stopped by the second half."""
    assert len(ANCHORED_FABRICATIONS) == 20 and len(set(ANCHORED_FABRICATIONS)) == 20

    lead = {"name": "Harbourvale Joinery", "email": "rob@harbourvale.co.uk",
            "website": "https://harbourvale.co.uk", "category": "Joiner", "city": "Leeds"}
    audit = {"final_url": "https://harbourvale.co.uk", "reachable": True,
             "gaps": AUDIT["gaps"]}
    anchors = T._recipient_anchors("Harbourvale Joinery", "harbourvale.co.uk",
                                   audit["gaps"])
    sender = T._sender_words(PROFILE, anchors)

    for claim in ANCHORED_FABRICATIONS:
        flat = T._flatten(claim)
        assert any(" %s " % anchor in flat for anchor in anchors), \
            ("does not anchor, so it proves nothing", claim)
        assert T._sender_claim(claim, anchors, sender), claim
        assert T._observed(claim, anchors, sender) == "", claim

        ctx = T.build_context(lead, audit,
                              {"subject": claim, "opener": claim, "ps": claim},
                              PROFILE, SETTINGS)
        assert ctx["ai_opener"] == "" and ctx["ai_ps"] == "" and ctx["ai_subject"] == "", claim
        for tpl in T.TEMPLATES:
            subject, text, _ = T.render(tpl, ctx)
            body = _core_body(text, ctx).lower()
            assert subject, (tpl.id, claim)
            # "Auto Army" is not here: the sender's company signs every one of
            # these, which is the whole reason a claim about it reads as true.
            for fragment in ("we have", "we fixed", "other", "dozens", "firms",
                             "reviews", "track record", "waiting list", "nous",
                             "wir haben", "empfohlen", "joiners"):
                assert fragment not in body, (tpl.id, claim, fragment)

    # A sentence that anchors and then sells past it keeps the observed half only.
    mixed = ("Your contact form posts to a mailbox. Harbourvale Joinery would be "
             "the eleventh joinery firm in Leeds we have set this up for.")
    assert T._observed(mixed, anchors, sender) == "Your contact form posts to a mailbox."

    # The sender may say what they looked at — that is a statement about the
    # reader's site — and nothing else about themselves.
    for kept in ("I noticed the online booking link goes to a dead calendar.",
                 "We saw the contact form on harbourvale.co.uk asks for nine fields."):
        assert T._observed(kept, anchors, sender) == kept, kept
    assert T._observed("I noticed your booking page, and we have built eleven of "
                       "these.", anchors, sender) == ""

    # The sender's own company in a sentence is a sentence about the sender, and
    # the check is skipped for the seller who mails their own trade.
    assert T._sender_words(PROFILE, anchors) == frozenset({"auto army", "umar farooq"})
    assert T._sender_words(PROFILE, frozenset({"auto army pty"})) == frozenset({"umar farooq"})

    # A business whose own name is a word on one of these lists keeps its
    # personalisation: what the recipient's record supplied is struck out before
    # the sentence is judged. A misjudged sentence costs one field once, and a
    # name that trips a rule costs that lead every sentence it will ever get.
    for name, observation in (
        ("Sterne Fabrication", "Sterne Fabrication buries its booking page three clicks down."),
        ("Referenz Bau GmbH", "Referenz Bau GmbH has no online booking at all."),
        ("Voisine Interiors", "Voisine Interiors lists four services and no way to book any."),
        ("Andere Werkstatt", "Andere Werkstatt has no online booking."),
        ("Meinhardt Joinery", "Meinhardt Joinery still takes bookings by phone only."),
    ):
        own = T._recipient_anchors(name, "", audit["gaps"])
        assert T._observed(observation, own, sender) == observation, (name, observation)
        assert T._sender_claim("%s would be the eleventh workshop we set this "
                               "up for." % name, own, sender), name


def test_subject_uses_the_neutral_gap_phrase():
    """`title` is written for the middle of a sentence. Alone in a subject line
    it reads as a verdict on the reader's business."""
    blunt = {"code": "stale_site", "title": "a site nobody has touched in years",
             "subject_phrase": "keeping the site current", "severity": 1,
             "evidence": "copyright still reads 2019",
             "services": ["Business Process Automation"]}
    ctx = T.build_context(LEAD, {"gaps": [blunt]}, {}, PROFILE, SETTINGS)
    assert ctx["gap_1"] == blunt["title"]
    assert ctx["gap_1_subject"] == blunt["subject_phrase"]

    subject, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert subject == "keeping the site current at Acme Plumbing & Heating", subject
    assert blunt["title"] not in subject
    assert blunt["title"] in text, text          # the body has the sentence to carry it
    assert T.render(T.get_template("followup_bump"), ctx)[0] == \
        "one more note on keeping the site current"

    for tpl in T.TEMPLATES:
        assert "{{gap_1}}" not in tpl.subject, (tpl.id, "blunt title in a subject")

    # A gap dict written before `subject_phrase` existed still renders a subject.
    legacy = dict(blunt)
    legacy.pop("subject_phrase")
    ctx = T.build_context(LEAD, {"gaps": [legacy]}, {}, PROFILE, SETTINGS)
    assert ctx["gap_1_subject"] == blunt["title"]
    assert T.render(T.get_template("followup_bump"), ctx)[0].startswith("one more note on")


def test_model_output_is_cleaned_before_it_is_trusted():
    """`core.ai` asks for no greeting and no exclamation marks. Asking is not
    enforcement, and the opener is pasted straight under "Hi Rob,": a model that
    opens with its own "Hi Rob," ships two greetings, and "it looks great!!"
    ships as a stranger shouting in the second line of a cold email."""
    ai = {"ok": True,
          "subject": "your online booking!!",
          "opener": "Hi {{first_name}}, I saw the booking page -- it looks great!!",
          "ps": "Hey Rob! online booking would take an afternoon!"}
    ctx = T.build_context(LEAD, AUDIT, ai, PROFILE, SETTINGS)
    assert ctx["ai_opener"] == "I saw the booking page, it looks great.", ctx["ai_opener"]
    assert ctx["ai_ps"] == "P.S. Online booking would take an afternoon.", ctx["ai_ps"]

    subject, text, html = T.render(T.get_template("gap_direct"), ctx)
    assert "!" not in subject and "!" not in text and "!" not in html
    assert text.startswith("Hi Mike,\n\nI saw the booking page"), text[:60]
    assert text.count("Hi ") == 1, text

    for opener, expected in (
        ("Hello there! Your booking page loads slowly.", "Your booking page loads slowly."),
        ("Hey Sarah, your booking page is buried.", "Your booking page is buried."),
        ("Dear Mr Reid: your contact form has nine fields.",
         "Your contact form has nine fields."),
        ("Good morning, the online booking link is dead.", "The online booking link is dead."),
        ("Your booking page loads slowly.", "Your booking page loads slowly."),
        ("Hidden costs are what the booking page leads with.",
         "Hidden costs are what the booking page leads with."),
    ):
        cleaned = T.build_context(LEAD, AUDIT, {"opener": opener}, PROFILE, SETTINGS)["ai_opener"]
        assert cleaned == expected, (opener, cleaned)


# ── Copy rules ──


def test_subject_rules():
    for tpl in T.TEMPLATES:
        subject, _, _ = T.render(tpl, FULL_CTX)
        assert subject, tpl.id
        assert len(subject) <= T.SUBJECT_MAX, (tpl.id, len(subject), subject)
        assert "!" not in subject, tpl.id
        assert not re.match(r"^\s*(re|fw|fwd)\s*[:\-]", subject, re.I), subject
        assert subject != subject.upper() or not subject.strip("? "), subject

    # The model's subject takes the first touch; follow-ups keep their own.
    ctx = dict(FULL_CTX, ai_subject="RE: booking!! at ACME PLUMBING")
    # "PLUMBING" stops shouting; "ACME" is short enough to be an acronym and is left alone.
    assert T.render(T.get_template("gap_direct"), ctx)[0] == "booking at ACME Plumbing"
    assert T.render(T.get_template("followup_close"), ctx)[0] == "last one from me"

    # A subject that resolved fully keeps every word it was written with.
    assert T.render(T.get_template("time_saved"), dict(FULL_CTX, ai_subject=""))[0] \
        == "the same few minutes, over and over"

    # A subject whose value is missing loses the word that pointed at it, and
    # falls back to the business name when nothing is left.
    nogap = T.build_context(LEAD, {}, {}, PROFILE, SETTINGS)
    assert T.render(T.get_template("followup_bump"), nogap)[0] == "one more note"
    assert T.render(T.get_template("gap_direct"), nogap)[0] == "Acme Plumbing & Heating"

    # Over-long model subjects are cut at a word boundary, not mid-word.
    ctx = dict(FULL_CTX, ai_subject="booking and follow up and reporting and onboarding and quotes")
    subject = T.render(T.get_template("time_saved"), ctx)[0]
    assert len(subject) <= T.SUBJECT_MAX and not subject.endswith(" "), subject
    assert subject in ctx["ai_subject"], subject
    print("subject rules: OK")


def test_subject_never_ends_on_a_joiner():
    """A subject cut at the cap has to stop on a word that says something.
    "leads from the contact form at Coastal Fabrication &" is fifty characters,
    well inside the cap, and it still shows the reader the machinery."""
    gap = AUDIT["gaps"][1]
    names = ("Coastal Fabrication & Welding Incorporated",
             "Northwood Heating & Cooling Solutions Ltd",
             "Smith & Sons Roofing and Restoration Company",
             "Bright + Bold Interiors of Greater Sudbury",
             "Miller / Vance Automotive Group of Windsor")
    for name in names:
        ctx = T.build_context(dict(LEAD, name=name), {"gaps": [gap]}, {}, PROFILE, SETTINGS)
        for tpl in T.TEMPLATES:
            subject, _, _ = T.render(tpl, ctx)
            assert subject, (name, tpl.id)
            assert re.search(r"[A-Za-z0-9?]$", subject), (name, tpl.id, subject)
            assert not re.search(r"(?i)\s(?:at|on|for|to|with|and|or|of|the|in|by)$", subject), \
                (name, tpl.id, subject)

    # The same rule wherever the tail came from: a model subject, a hole where a
    # value should have been, a cut at the cap.
    for raw, expected in (
        ("booking at Smith &", "booking at Smith"),
        ("the form and", "the form"),
        ("a note on your site -", "a note on your site"),
        ("quotes at Miller /", "quotes at Miller"),
        ("booking and follow up and reporting and onboarding and quotes",
         "booking and follow up and reporting and onboarding"),
    ):
        assert T.render(T.get_template("gap_direct"), dict(FULL_CTX, ai_subject=raw))[0] == expected, raw


def test_the_subject_fallback_obeys_the_subject_rules():
    """The '!' strip, the Re:/Fwd: strip and the de-shouting all ran before the
    fallback assigned the business name raw, so the one path that reaches for a
    value nobody wrote for a subject line was the one path with no rules on it.
    A business named "{{name}}" put the machinery into the subject of a live
    email, which is the single thing `render` promises never to do, and a name
    that is all punctuation produced "" and a lead skipped with no reason."""
    for name, expected in (
        ("{{name}}", ""),                        # a token, swept to nothing
        ("{{business_name}}", ""),               # including one pointing at itself
        ("- -", ""),
        (". . .", ""),
        ("RE: Bloor Dental", "Bloor Dental"),
        ("Fwd: Re: Bloor Dental", "Bloor Dental"),
        ("Bloor Dental!!", "Bloor Dental"),
        ("BLOOR DENTAL GROUP", "Bloor Dental Group"),
        ("Bloor Dental &", "Bloor Dental"),
    ):
        got = T._clean_subject("", {"business_name": name})
        assert got == expected, (name, got)
        for bad in LEAK_PATTERNS:
            assert bad not in got, (name, got)

    # A name that cleans away falls through to the company rather than to "".
    for name in ("{{name}}", "- -", "your business", ""):
        assert T._clean_subject("", {"business_name": name, "company": "Auto Army"}) \
            == "Auto Army", name

    # End to end: no gap, so the template subject comes to nothing and the
    # fallback is what the prospect actually receives.
    for name, expected in (("{{name}}", "Auto Army"), ("- -", "Auto Army")):
        ctx = T.build_context(dict(LEAD, name=name), {}, {}, PROFILE, SETTINGS)
        subject, text, html = T.render(T.get_template("gap_direct"), ctx)
        assert subject == expected, (name, subject)
        for blob in (subject, text, html):
            for bad in LEAK_PATTERNS:
                assert bad not in blob, (name, bad, blob)


def test_the_sign_off_never_ends_on_a_dangling_comma():
    """Every template signs off "{{sender_title}}, {{company}}". The company is
    editable and not required, and a profile with it cleared signed the email
    "Rob Feldman / Automation lead,". `_trim_tail` does this for subjects and
    nothing did it for a body line. The comma is only there because the value
    behind it vanished, so the rule is anchored to the hole and a sign-off that
    resolved keeps its punctuation."""
    shapes = (
        ("no company", dict(PROFILE, company="")),
        ("no title", dict(PROFILE, sender_title="")),
        ("neither", dict(PROFILE, company="", sender_title="")),
        ("both", PROFILE),
    )
    for label, profile in shapes:
        ctx = T.build_context(LEAD, AUDIT, AI, profile, SETTINGS)
        for tpl in T.TEMPLATES:
            _, text, _ = T.render(tpl, ctx)
            # The greeting's comma is punctuation; everything after it is a line
            # that has to stop on something that says something.
            for line in _core_body(text, ctx).splitlines()[1:]:
                assert not line.rstrip().endswith((",", ";", ":", "&", "+", "/", "-")), \
                    (label, tpl.id, repr(line))

    ctx = T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)
    assert "Automation lead, Auto Army" in T.render(T.get_template("gap_direct"), ctx)[1]

    ctx = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, company=""), SETTINGS)
    _, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert "Automation lead," not in text, text
    assert "\nAutomation lead\n" in text + "\n", text


def test_the_proof_point_reads_as_its_own_paragraph():
    """`proof_point` sits alone between two blank lines and `_pick` returned the
    stored line exactly as typed. The settings placeholder actively teaches a
    mid-sentence fragment, so the shipped preview put a lowercase unterminated
    paragraph between two correct ones and the whole email read as generated."""
    fragments = ["cut a roofing client's quote turnaround from 2 days to 20 minutes",
                 "took a Mississauga HVAC firm from a day to four minutes"]
    ctx = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, proof_points=fragments), SETTINGS)
    proof = ctx["proof_point"]
    assert proof[:1].isupper() and proof.endswith("."), proof
    assert proof[:1].lower() + proof[1:-1] in fragments, proof

    for template_id in ("gap_direct", "time_saved"):
        _, text, _ = T.render(T.get_template(template_id), ctx)
        assert [p for p in text.split("\n\n") if proof in p] == [proof], (template_id, text)

    # A line the seller already wrote as a sentence is left exactly as it was.
    assert T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)["proof_point"] \
        in PROFILE["proof_points"]

    # A stop already there is never doubled, whatever closes the line.
    for stored, expected in (
        ("It halved their quote time.", "It halved their quote time."),
        ("it halved their quote time", "It halved their quote time."),
        ('They called it "instant."', 'They called it "instant."'),
        ("Quote time fell (2 days to 20 minutes)", "Quote time fell (2 days to 20 minutes)."),
        ("Was it worth it?", "Was it worth it?"),
        ("", ""),
    ):
        got = T.build_context(LEAD, {}, {}, dict(PROFILE, proof_points=[stored]),
                              SETTINGS)["proof_point"]
        assert got == expected, (stored, got)


def test_the_proof_point_obeys_the_no_exclamation_rule():
    """The rule was enforced on subjects and on model output and nowhere else, so
    the one line that comes off the user's own profile carried it into the body.
    That line is its own paragraph, which makes a shouted one the most prominent
    thing in the email — and a punctuation mark on its own rendered a paragraph
    containing one character."""
    for stored, expected in (
        ("we ship fast!", "We ship fast."),
        ("We ship fast!!!", "We ship fast."),
        ("Quotes go out same day! Every time!", "Quotes go out same day. Every time."),
        ("!", ""),
        (".", ""),
        ("...", ""),
        ("?", ""),
        ("- -", ""),
        ("   ", ""),
    ):
        got = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, proof_points=[stored]),
                              SETTINGS)["proof_point"]
        assert got == expected, (stored, got)

    for stored in ("we ship fast!", "!", "."):
        ctx = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, proof_points=[stored]),
                              SETTINGS)
        for template_id in ("gap_direct", "time_saved"):
            _, text, html = T.render(T.get_template(template_id), ctx)
            assert "!" not in text and "!" not in html, (stored, template_id)
            paragraphs = [p.strip() for p in _core_body(text, ctx).split("\n\n")]
            assert all(re.search(r"[A-Za-z0-9]", p) for p in paragraphs), \
                (stored, template_id, paragraphs)


def test_a_snippet_that_loses_a_fragment_is_dropped_not_shipped_with_a_hole():
    """`_clean_snippet` cut URLs, {{tokens}} and [brackets] out in place, so the
    sentence around them shipped with the hole still in it. The body path has
    `_drop_holed_sentences` for exactly this; the snippet path did not."""
    for snippet, expected in (
        ("I saw {{business_name}} still uses a [contact form] on {{website_domain}}.", ""),
        ("I saw https://harbourvale.co.uk/contact still ends in a plain form.", ""),
        ("Your [booking page] is buried.", ""),
        # A hole in front of the sentence took nothing out of it, so the
        # sentence stands — and it stands capitalised, as the paragraph it opens.
        ("{{first_name}}, your quote page is a PDF.", "Your quote page is a PDF."),
        # A whole sentence survives when the excision is not inside it.
        ("Your booking page is buried. https://harbourvale.co.uk/book",
         "Your booking page is buried."),
        ("Your booking page is buried. It sits under [Services].",
         "Your booking page is buried."),
        # Nothing to excise, nothing to drop.
        ("Your booking page is buried.", "Your booking page is buried."),
        ("contact form on /contact, no booking widget",
         "contact form on /contact, no booking widget"),
    ):
        assert T._clean_snippet(snippet) == expected, (snippet, T._clean_snippet(snippet))

    # An unterminated remainder cannot be dropped as a sentence, so it is trimmed
    # back to the last word that still points at something.
    assert T._clean_snippet("form posts to https://formspree.io/x") == "form posts"

    # End to end: the hole never reaches the body, and the template stands.
    ctx = T.build_context(
        LEAD, AUDIT,
        {"opener": "I saw {{business_name}} still uses a [contact form] on {{website_domain}}."},
        PROFILE, SETTINGS)
    assert ctx["ai_opener"] == ""
    _, text, _ = T.render(T.get_template("gap_direct"), ctx)
    assert "I saw still" not in text and "[" not in text, text
    for bad in LEAK_PATTERNS:
        assert bad not in text, bad


def test_a_business_name_with_nothing_in_it_falls_back():
    """`_clean_subject` rescues a punctuation-only name; `build_context` had no
    such rescue, so a name that was present but meaningless never reached the
    "your business" fallback and rendered raw into the middle of a sentence."""
    for name in ("!!!", "- -", ". . .", "???", "  *  ", "/"):
        ctx = T.build_context(dict(LEAD, name=name), AUDIT, {}, PROFILE, SETTINGS)
        assert ctx["business_name"] == "your business", (name, ctx["business_name"])
        for template_id in ("gap_direct", "time_saved", "question", "followup_bump"):
            subject, text, html = T.render(T.get_template(template_id), ctx)
            assert subject and re.search(r"[A-Za-z0-9]", subject), (name, template_id, subject)
            assert "!" not in text and "!" not in html, (name, template_id)
        assert T.render(T.get_template("question"), ctx)[0] == \
            "how do messages reach your business?", name
        assert "reaches your business, where does it land" in \
            T.render(T.get_template("question"), ctx)[1], name

    # And the name never anchors a model sentence when there is no name.
    assert T._recipient_anchors("!!!", "", []) == frozenset()

    # A name is meaningless only when there is nothing in it to read. One letter
    # or one digit is a name, and it is left exactly as the record has it.
    for name in ("Q", "3M", "Ács Kft", "北新工房"):
        ctx = T.build_context(dict(LEAD, name=name), AUDIT, {}, PROFILE, SETTINGS)
        assert ctx["business_name"] == name, (name, ctx["business_name"])


def test_a_business_name_arrives_as_text_not_as_markup():
    """A CSV export and a scraped page both hand over names with entities and
    tags in them, and `business_name` was never decoded or stripped: a lead
    named "Acme &amp; Sons" shipped a subject reading "online booking at Acme
    &amp; Sons" and a body reading "before anyone at Acme &amp; Sons opens the
    inbox".

    Decoding is a loop, not a step: "&amp;lt;b&amp;gt;" decodes to "&lt;b&gt;"
    and then to "<b>", so one pass leaves markup behind. The sanitisers run
    after the decode for the same reason — a decode can put back the em dash and
    the control characters the strip had just taken out."""
    for raw, expected in (
        ("Acme &amp; Sons", "Acme & Sons"),
        ("Fern &amp;amp; Co", "Fern & Co"),
        ("Ratcliffe &AMP; Sons", "Ratcliffe & Sons"),
        ("<b>Harbourvale</b> Joinery", "Harbourvale Joinery"),
        ("&lt;b&gt;Harbourvale&lt;/b&gt; Joinery", "Harbourvale Joinery"),
        ("&amp;lt;b&amp;gt;Harbourvale&amp;lt;/b&amp;gt;", "Harbourvale"),
        ("<span class=\"x\">Coastal</span> Fabrication", "Coastal Fabrication"),
        ("Acme &#8212; Sons", "Acme , Sons"),          # the em dash rule still runs
        ("Acme\nLtd", "Acme Ltd"),
        ("Acme\x00Ltd", "Acme Ltd"),                   # never the gap marker itself
        ("Caf&eacute; Rouge", "Café Rouge"),
        ("Acme Plumbing & Heating", "Acme Plumbing & Heating"),
        ("&nbsp;", ""),
        ("<p></p>", ""),
    ):
        assert T._plain_name(raw) == expected, (raw, T._plain_name(raw))

    ctx = T.build_context(dict(LEAD, name="Acme &amp; Sons"), AUDIT, {}, PROFILE, SETTINGS)
    assert ctx["business_name"] == "Acme & Sons"
    subject, text, html = T.render(T.get_template("time_saved"), ctx)
    assert "on the tools Acme & Sons already has" in text, text
    assert T.render(T.get_template("gap_direct"), ctx)[0] == "online booking at Acme & Sons"
    # And the HTML part escapes it exactly once, as it does any other name.
    assert "Acme &amp; Sons" in html and "&amp;amp;" not in html

    # A name with nothing left in it after the strip reaches the fallback, and a
    # decode that puts a merge token back is swept before it can render.
    for raw in ("<p></p>", "&nbsp;", "&#160;"):
        assert T.build_context(dict(LEAD, name=raw), AUDIT, {}, PROFILE,
                               SETTINGS)["business_name"] == "your business", raw
    for raw in ("&#123;&#123;gap_1&#125;&#125; Ltd", "&lt;script&gt;alert(1)&lt;/script&gt;"):
        ctx = T.build_context(dict(LEAD, name=raw), AUDIT, {}, PROFILE, SETTINGS)
        for tpl in T.TEMPLATES:
            subject, text, html = T.render(tpl, ctx)
            assert subject and text, (raw, tpl.id)
            for leak in LEAK_PATTERNS + ("<script", "<b>", "&lt;", "&amp;a"):
                assert leak not in subject and leak not in text and leak not in html, \
                    (raw, tpl.id, leak)

    # The anchors are built from the decoded name, so a model sentence naming the
    # business is kept for a lead whose record spells it with an entity.
    anchors = T._recipient_anchors(T._plain_name("Acme &amp; Sons"), "", [])
    assert anchors == frozenset({"acme sons"}), sorted(anchors)


def test_no_merge_field_is_resolved_but_unused():
    """`city` was in `MERGE_FIELDS`, `build_context` gave it a "your area"
    fallback, and a Google Maps scrape stood behind it. It appeared in none of
    the five templates, so the reader never saw it once — local relevance the
    pipeline paid for and the copy threw away.

    It is not coming back in a sentence: every phrasing reads as the mail merge
    it is, or builds a credential out of the reader's own listing, which
    `test_no_credential_is_built_out_of_the_lead` refuses outright. The pinned
    set below is the guard — a new field resolved into every context and used by
    nobody fails here rather than shipping quietly."""
    used = {f for tpl in T.TEMPLATES
            for f in re.findall(r"\{\{([a-z0-9_]+)\}\}", tpl.subject + "\n" + tpl.body)}
    assert used <= set(T.MERGE_FIELDS), sorted(used - set(T.MERGE_FIELDS))

    # Read by `render` and the footer by name rather than through a token.
    by_name = {"ai_subject", "postal_address", "unsubscribe_line"}
    for field in by_name:
        assert field in FULL_CTX and field not in used, field

    unused = set(T.MERGE_FIELDS) - used - by_name
    # `gap_2` and `service_3` are slot two and slot three of data slot one
    # already shows, and `phone` is the sender's own. None of them is a signal
    # gathered about the lead and then dropped, which is what `city` was.
    assert unused == {"gap_2", "service_3", "phone"}, sorted(unused)

    assert "city" not in T.MERGE_FIELDS
    for _label, make in RENDER_CONTEXTS:
        assert "city" not in make()
    assert "city" not in T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)


def test_first_touch_length():
    for tpl in T.templates_for_step(0):
        _, text, _ = T.render(tpl, FULL_CTX)
        words = len(_core_body(text, FULL_CTX).split())
        assert words < T.FIRST_TOUCH_MAX_WORDS, (tpl.id, words)
        assert words > 40, (tpl.id, words)
    print("first touch length: OK")


def test_copy_conventions():
    assert len(T.templates_for_step(0)) >= 3
    assert [t.id for t in T.TEMPLATES if t.step > 0] == ["followup_bump", "followup_close"]
    assert len({t.id for t in T.TEMPLATES}) == len(T.TEMPLATES)
    assert T.get_template("gap_direct") is not None and T.get_template("nope") is None

    banned = (
        "i hope this email finds you well", "i came across your website",
        "quick question", "just following up", "reaching out", "circle back",
        "touch base", "act now", "limited time", "dear sir", "to whom it may concern",
        "synergy", "game changer", "revolutionary",
    )
    for tpl in T.TEMPLATES:
        blob = (tpl.subject + "\n" + tpl.body)
        for dash in ("—", "–", "‒", "―"):
            assert dash not in blob, (tpl.id, "em dash")
        low = blob.lower()
        for phrase in banned:
            assert phrase not in low, (tpl.id, phrase)
        assert "!" not in tpl.subject, tpl.id
        assert len(tpl.subject) <= T.SUBJECT_MAX, tpl.id
        assert tpl.body.startswith("Hi {{first_name}},"), tpl.id
        assert "{{sender_name}}" in tpl.body, (tpl.id, "no sign-off")
        # One link, at most: the calendar or the company site, never both.
        links = tpl.body.count("{{calendar_link}}") + tpl.body.count("{{company_website}}")
        assert links <= 1, (tpl.id, links)

    # The three first touches must be three arguments, not one email three times.
    # Compare the written copy only: the merge fields and the footer are shared
    # by design and would drown the signal.
    first = T.templates_for_step(0)
    written = [set(re.sub(r"\{\{[^}]*\}\}", " ", t.body).lower().split()) for t in first]
    for i, a in enumerate(written):
        for j, b in enumerate(written[i + 1:], i + 1):
            overlap = len(a & b) / max(len(a), 1)
            assert overlap < 0.35, (first[i].id, first[j].id, round(overlap, 2))
    print("copy conventions: OK")


# ── HTML ──


def test_html_shape():
    _, text, html = T.render(T.get_template("gap_direct"), FULL_CTX)

    anchors = re.findall(r"<a\s+href=\"([^\"]+)\"", html)
    assert len(anchors) == 2, anchors
    assert anchors[0] == PROFILE["calendar_link"], anchors
    assert anchors[1].startswith("mailto:umar@autoarmy.io?subject=unsubscribe"), anchors

    for banned in ("<img", "<table", "<script", "<style", "@font-face", "<link", "background-image"):
        assert banned not in html.lower(), banned
    assert "<hr" in html and html.count("<hr") == 1
    assert PROFILE["postal_address"] in html, "no postal address in the footer"

    # A business name carrying HTML-significant characters must arrive escaped.
    escaped = T.render(T.get_template("time_saved"), FULL_CTX)[2]
    assert "Acme Plumbing &amp; Heating" in escaped, escaped
    assert "&" not in escaped.replace("&amp;", "").replace("&quot;", "").replace("&#x27;", ""), escaped

    # Compliance lives in the plain-text part too, not only the HTML.
    assert PROFILE["postal_address"] in text
    assert FULL_CTX["unsubscribe_line"] in text

    # Handing render's own body_text back to to_html must not double the footer.
    again = T.to_html(text, FULL_CTX)
    assert again.count("<hr") == 1 and again.count(PROFILE["postal_address"]) == 1
    assert again == html, "to_html is not idempotent over its own footer"
    print("html shape: OK")


# What `campaign.apply_compliance` empties when a switch is turned off. Written
# out rather than imported so this stays a test of core.templates alone.
_SWITCHED_OFF = ("unsubscribe_line", "unsubscribe_email", "postal_address")


def _visible(html: str) -> str:
    """The copy a reader sees, with the markup taken back off."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    return _blocks(_html.unescape(re.sub(r"<[^>]+>", "", text)))


def _blocks(text: str) -> str:
    return re.sub(r"\n{2,}", "\n\n", str(text)).strip()


def test_the_two_mime_parts_carry_the_same_copy():
    """One message goes out as plain text and HTML together and a filter scores
    the pair, so the two have to say the same thing on every profile the app
    will send from — including the incomplete ones it now accepts. With no
    sender name and no sender title the sign-off tidies down to the company on
    its own, which with no postal address is the footer's identity line word for
    word, and a de-dup that recognised the footer by its wording deleted the
    sign-off from the HTML part only."""
    axes = ("sender_name", "sender_title", "postal_address", "company", "switches")
    for bits in itertools.product((False, True), repeat=len(axes)):
        blank = [name for name, off in zip(axes, bits) if off]
        profile = dict(PROFILE, **{k: "" for k in blank if k != "switches"})
        ctx = T.build_context(LEAD, AUDIT, AI, profile, SETTINGS)
        if "switches" in blank:
            ctx.update({k: "" for k in _SWITCHED_OFF})
        for tpl in T.TEMPLATES:
            _, text, html = T.render(tpl, ctx)
            assert _visible(html) == _blocks(text), (tpl.id, blank or ["full profile"])

    # The profile that used to split them, named so the failure reads straight
    # off the assertion: the company appears twice in the message, and the HTML
    # part is the one that used to be short of it.
    ctx = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, sender_name="",
                                                sender_title="", postal_address=""), SETTINGS)
    _, text, html = T.render(T.get_template("gap_direct"), ctx)
    assert text.count(PROFILE["company"]) == 2, text
    assert _visible(html).count(PROFILE["company"]) == 2, html
    assert T.to_html(text, ctx) == html, "to_html is not idempotent over its own footer"

    # And the de-dup takes the footer off the end, once. The same words standing
    # anywhere else are body copy and stay there.
    bare = {"company": "Auto Army", "postal_address": "", "unsubscribe_line": ""}
    assert T.to_html("Auto Army\n\nOne line.\n\nAuto Army", bare).count("Auto Army") == 2
    print("mime parts agree: OK")


# ── Context ──


def test_build_context():
    for field in T.MERGE_FIELDS:
        assert field in FULL_CTX, field

    assert FULL_CTX["business_name"] == "Acme Plumbing & Heating"
    assert FULL_CTX["first_name"] == "Mike"
    assert FULL_CTX["category"] == "plumber"
    assert FULL_CTX["website_domain"] == "acmeplumbing.ca"
    assert FULL_CTX["gap_1"] == "no online booking"
    assert FULL_CTX["gap_2"] == "no CRM behind the form"
    assert FULL_CTX["gap_1_evidence"].startswith("contact form")
    assert FULL_CTX["gap_1_subject"] == "online booking"
    assert FULL_CTX["service_1"] == "appointment booking"
    assert FULL_CTX["ai_ps"].startswith("P.S. ")
    assert FULL_CTX["calendar_link"] == PROFILE["calendar_link"]
    assert "umar@autoarmy.io" in FULL_CTX["unsubscribe_line"]

    # Names: a person's mailbox greets them, a department does not.
    assert T.build_context({"email": "info@x.ca"}, {}, {}, {}, {})["first_name"] == "there"
    assert T.build_context({"email": "noreply@x.ca"}, {}, {}, {}, {})["first_name"] == "there"
    assert T.build_context({"email": "j.smith@x.ca"}, {}, {}, {}, {})["first_name"] == "there"
    assert T.build_context({"email": "sarah@x.ca"}, {}, {}, {}, {})["first_name"] == "Sarah"
    assert T.build_context({"contact_name": "Dev Patel"}, {}, {}, {}, {})["first_name"] == "Dev"

    # Acronyms keep their case when the category is spoken mid-sentence.
    assert T.build_context({"category": "HVAC Contractor"}, {}, {}, {}, {})["category"] == "HVAC contractor"

    # Domain falls back through audit and website when the lead has none.
    assert T.build_context({"website": "www.Foo.CA/contact"}, {}, {}, {}, {})["website_domain"] == "foo.ca"
    assert T.build_context({}, {"final_url": "https://bar.ca/x"}, {}, {}, {})["website_domain"] == "bar.ca"
    assert T.build_context({}, {}, {}, {}, {})["website_domain"] == "your site"

    # Calendar falls back to the company site so the one-link rule still holds.
    assert T.build_context({}, {}, {}, {"website": "https://autoarmy.io"}, {})["calendar_link"] \
        == "https://autoarmy.io"

    # Unsubscribe address: explicit setting, then reply-to, then the sending account.
    assert "a@b.co" in T.build_context({}, {}, {}, {}, {"unsubscribe_mailto": "mailto:a@b.co"})["unsubscribe_line"]
    assert "r@b.co" in T.build_context({}, {}, {}, {"reply_to": "r@b.co"}, {})["unsubscribe_line"]
    assert "s@b.co" in T.build_context(
        {}, {}, {}, {}, {"smtp_accounts": [{"email": "s@b.co", "enabled": True}]})["unsubscribe_line"]
    assert "unsubscribe" in T.build_context({}, {}, {}, {}, {})["unsubscribe_line"].lower()

    # Model output is untrusted copy: no dashes, no links, no placeholders.
    dirty = T.build_context(LEAD, AUDIT, {
        "ok": True,
        "opener": "We saw your site — see https://spam.example/x — and [city] looks busy.",
        "ps": "Ping me.",
    }, PROFILE, SETTINGS)
    assert "—" not in dirty["ai_opener"] and "http" not in dirty["ai_opener"], dirty["ai_opener"]
    assert "[city]" not in dirty["ai_opener"]

    # The same lead always sees the same proof point.
    again = T.build_context(LEAD, AUDIT, AI, PROFILE, SETTINGS)
    assert again["proof_point"] == FULL_CTX["proof_point"] and FULL_CTX["proof_point"] in PROFILE["proof_points"]
    print("build context: OK")


def test_never_raises():
    junk = [None, {}, {"business_name": None}, {"gap_1": 12}, {"first_name": ["x"]}]
    for ctx in junk:
        for tpl in T.TEMPLATES:
            subject, text, html = T.render(tpl, ctx)
            assert isinstance(subject, str) and isinstance(text, str) and isinstance(html, str)
    assert T.build_context(None, None, None, None, None)["first_name"] == "there"
    assert T.to_html("", {}) and T.to_html(None, None) is not None
    print("never raises: OK")


# A website field as it actually arrives off a scraped page. `urlsplit` raises
# ValueError on the first of these under Python 3.14, and hands back a netloc
# that is not a hostname for most of the rest.
MALFORMED_WEBSITES = (
    "https://[example].com",
    "https://[not:an:ipv6",
    "http://exa mple.com",
    "http://ctrl\x01char.com",
    "http://new\nline.com",
    "https://" + "a" * 4000 + ".com",
    "javascript:alert(1)",
    "data:text/html,<h1>hi</h1>",
    "",
    "   ",
    "http://",
    "//",
    "https://@:99999",
    "[::1]",
    "{{website}}",
)


def test_a_malformed_website_never_leaves_the_lead_it_arrived_on():
    """`_domain` built "//" + raw and handed it to `urlsplit`, which raises on a
    bracketed netloc that is not a valid IPv6 literal. Nothing caught it, so one
    scraped lead carrying "https://[example].com" came back queued 0, skipped 0,
    error "ValueError: Invalid IPv6 URL" — the whole campaign discarded by one
    record out of six.

    Both layers are asserted here: `_domain` is total, and `_render_pass` skips
    a lead it cannot render rather than taking the pass down with it."""
    for raw in MALFORMED_WEBSITES:
        assert T._domain({"website": raw}, {}) == "", raw
        assert T._domain({}, {"final_url": raw}) == "", raw
        ctx = T.build_context({"name": "Biz", "email": "a@b.ca", "website": raw},
                              {"gaps": AUDIT["gaps"]}, AI, PROFILE, SETTINGS)
        assert ctx["website_domain"] == "your site", raw
        for tpl in T.TEMPLATES:
            subject, text, html = T.render(tpl, ctx)
            assert subject and text and html, (raw, tpl.id)

    # A real host still resolves, punycode and IDN and deep paths included.
    for raw, expected in (
        ("https://www.acmeplumbing.ca/contact?x=1", "acmeplumbing.ca"),
        ("acmeplumbing.ca", "acmeplumbing.ca"),
        ("HTTP://User@Acme-Plumbing.CO.UK:8080/x", "acme-plumbing.co.uk"),
        ("https://xn--mnchen-3ya.de", "xn--mnchen-3ya.de"),
        ("https://münchen.de", "münchen.de"),
    ):
        assert T._domain({"website": raw}, {}) == expected, raw


def _plan_settings(profile: dict, **overrides) -> dict:
    """Settings that plan a campaign offline: no audit, no follow-ups, no send."""
    settings = {
        "audit_enabled": False, "followup_enabled": False, "dry_run": True,
        "send_days": [0, 1, 2, 3, 4], "send_start_hour": 9, "send_end_hour": 17,
        "send_timezone": "local", "send_min_gap_sec": 60, "send_max_gap_sec": 240,
        "daily_cap_per_account": 500, "hourly_cap_per_account": 0,
        "warmup_enabled": False, "sender_profile": profile,
        "smtp_accounts": [{"email": "s@shop.test", "app_password": "x", "enabled": True,
                           "daily_cap": 500, "warmup_started": ""}],
    }
    settings.update(overrides)
    return settings


def test_one_bad_record_cannot_discard_the_whole_campaign():
    """The other half of the same failure. `_render_pass` had no guard of its
    own, so anything that escaped `build_context` unwound four frames to
    `plan_campaign`'s handler and returned a plan of zero. Six leads, one bad,
    and nothing was queued for the other five."""
    from core import campaign as C  # noqa: PLC0415
    from core import outreach_db as DB  # noqa: PLC0415
    from core import settings as ST  # noqa: PLC0415

    profile = dict(PROFILE, services=[], proof_points=[])
    settings = _plan_settings(profile)

    original_dir = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            conn = DB.connect(os.path.join(tmp, "outreach.db"))
            leads = [{"email": "bad%02d@x.test" % i, "name": "Bad %d" % i, "website": raw}
                     for i, raw in enumerate(MALFORMED_WEBSITES)]
            leads += [{"email": "good%d@x.test" % i, "name": "Good %d" % i,
                       "website": "https://good%d.test" % i} for i in range(6)]

            campaign_id = DB.create_campaign(conn, "malformed", "", profile, settings)
            plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads,
                                   template_id="", profile=profile, settings=settings,
                                   ai=None)
            assert plan["error"] == "", plan["error"]
            assert plan["queued"] == len(leads), (plan["queued"], len(leads))
            assert plan["skipped"] == 0, plan["skip_reasons"]
            assert DB.campaign_stats(conn, campaign_id)["total"] == len(leads)

            # And a record that blows up somewhere no guard anticipated takes
            # itself out of the campaign, with a reason, and nothing else.
            original = C._templates.build_context

            def exploding(lead, *args, **kwargs):
                if str((lead or {}).get("email")) == "boom@x.test":
                    raise ValueError("Invalid IPv6 URL")
                return original(lead, *args, **kwargs)

            C._templates.build_context = exploding
            try:
                second = DB.create_campaign(conn, "exploding", "", profile, settings)
                plan = C.plan_campaign(
                    conn, campaign_id=second,
                    leads=[{"email": "boom@x.test", "name": "Boom"}]
                          + [{"email": "ok%d@x.test" % i, "name": "Ok %d" % i}
                             for i in range(5)],
                    template_id="", profile=profile, settings=settings, ai=None)
            finally:
                C._templates.build_context = original

            assert plan["error"] == "", plan["error"]
            assert (plan["queued"], plan["skipped"]) == (5, 1), plan
            assert plan["skip_reasons"] == {"could not be rendered (ValueError)": 1}, \
                plan["skip_reasons"]
            assert DB.campaign_stats(conn, second)["total"] == 5
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original_dir


def test_every_pass_survives_one_exploding_lead():
    """`_render_pass` was the only pass with a guard, and it is the last of four.

    `_eligible_leads`, `_audit_pass` and `_queue_pass` all touch the same
    arbitrary third-party text one frame earlier, so an exception in any of them
    still unwound to `plan_campaign`'s handler and returned a campaign of zero.
    Each pass is driven into the ground here in turn, and each has to lose one
    lead, name a reason for it, and queue the other five."""
    from core import campaign as C  # noqa: PLC0415
    from core import outreach_db as DB  # noqa: PLC0415
    from core import settings as ST  # noqa: PLC0415

    profile = dict(PROFILE, services=[], proof_points=[])
    leads = [{"email": "boom@x.test", "name": "Boom"}] + \
            [{"email": "ok%d@x.test" % i, "name": "Ok %d" % i} for i in range(5)]

    original_dir = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            conn = DB.connect(os.path.join(tmp, "outreach.db"))
            # `audit_lead` is replaced outright rather than wrapped: the pass
            # runs for real otherwise, and every lead in this file would be a
            # site crawl.
            for pass_name, target, patch, audit_enabled, reason, stub in (
                ("_eligible_leads", DB, "is_suppressed",
                 False, "could not be read (ValueError)", None),
                ("_audit_pass", C, "audit_lead",
                 True, "could not be audited (ValueError)", lambda *a, **k: ({}, {})),
                ("_render_pass", C._templates, "build_context",
                 False, "could not be rendered (ValueError)", None),
                ("_queue_pass", DB, "queue_message",
                 False, "could not be queued (ValueError)", None),
            ):
                original = getattr(target, patch)
                healthy = stub or original

                def once(*args, _healthy=healthy, **kwargs):
                    # By name, not by call order: the audit pass answers out of
                    # a thread pool and "the first one" is whichever landed.
                    if "boom" in repr(args).lower():
                        raise ValueError("Invalid IPv6 URL")
                    return _healthy(*args, **kwargs)

                setattr(target, patch, once)
                try:
                    settings = _plan_settings(profile, audit_enabled=audit_enabled)
                    campaign_id = DB.create_campaign(conn, pass_name, "", profile, settings)
                    plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads,
                                           template_id="", profile=profile,
                                           settings=settings, ai=None)
                finally:
                    setattr(target, patch, original)

                assert plan["error"] == "", (pass_name, plan["error"])
                assert (plan["queued"], plan["skipped"]) == (5, 1), (pass_name, plan)
                assert plan["skip_reasons"] == {reason: 1}, (pass_name, plan["skip_reasons"])
                assert DB.campaign_stats(conn, campaign_id)["total"] == 5, pass_name
                # Each lead is only contactable once, so the next pass gets its
                # own five: leads already queued above are skipped, not requeued.
                leads = [{"email": "boom@x.test", "name": "Boom"}] + \
                        [{"email": "%s%d@x.test" % (pass_name.strip("_"), i),
                          "name": "Ok %d" % i} for i in range(5)]
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original_dir


def test_a_campaign_says_how_much_of_it_could_not_be_personalised():
    """The accept-list in `_observed` discards every model sentence it cannot
    tie to the recipient, so a lead whose site never answered gets three generic
    paragraphs with its own name merged in. That is the honest trade — the email
    claims nothing the sender does not know — but nobody was told it had
    happened, and two hundred form letters sent in the belief that they are
    personal is the failure the trade exists to prevent."""
    from core import campaign as C  # noqa: PLC0415
    from core import outreach_db as DB  # noqa: PLC0415
    from core import settings as ST  # noqa: PLC0415

    reachable = {"final_url": "https://good.test", "reachable": True, "gaps": AUDIT["gaps"]}
    empty = {"final_url": "https://quiet.test", "reachable": True, "gaps": []}
    dead = {"final_url": "https://dead.test", "reachable": False, "gaps": [],
            "error": "unreachable"}

    # The flag reads the copy, not the crawl: a crawl that found nothing still
    # personalises the email when the model wrote something about the business.
    named = {"opener": "Harbourvale Joinery still takes bookings by phone only."}
    lead = {"email": "rob@harbourvale.co.uk", "name": "Harbourvale Joinery",
            "website": "https://harbourvale.co.uk"}
    assert T.personalisation(lead, reachable, {}, PROFILE, SETTINGS) == (True, "")
    assert T.personalisation(lead, dead, named, PROFILE, SETTINGS) == (True, "")
    assert T.personalisation(lead, dead, {}, PROFILE, SETTINGS) == (False, "unreachable")
    assert T.personalisation(lead, empty, {}, PROFILE, SETTINGS) == (False, "nothing_found")
    assert T.personalisation({"email": "a@b.ca", "name": "Nowhere Ltd"}, {}, {},
                             PROFILE, SETTINGS) == (False, "no_website")
    assert T.personalisation(lead, {}, {}, PROFILE, SETTINGS) == (False, "not_audited")
    # And a fabricated track record does not buy a lead its personalisation back.
    assert T.personalisation(lead, dead, {"opener": ANCHORED_FABRICATIONS[0]},
                             PROFILE, SETTINGS) == (False, "unreachable")

    for code in T.GENERIC_REASONS:
        assert T.generic_reason(code) and T.generic_reason(code, plural=True), code
    assert T.generic_reason("nonsense") == ""

    profile = dict(PROFILE, services=[], proof_points=[])
    settings = _plan_settings(profile)
    original_dir = ST.SETTINGS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ST.SETTINGS_DIR = tmp
        try:
            conn = DB.connect(os.path.join(tmp, "outreach.db"))
            leads = []
            for index, (audit, ai) in enumerate(
                    [(reachable, {})] * 3 + [(dead, {})] * 2 + [(empty, {})]
                    + [(dead, named)]):
                lead = {"email": "lead%d@x.test" % index, "name": "Harbourvale Joinery",
                        "website": "https://harbourvale.co.uk"}
                lead_id = DB.upsert_lead(conn, lead)
                DB.set_lead_audit(conn, lead_id, audit, ai)
                leads.append(DB.get_lead(conn, lead_id))

            campaign_id = DB.create_campaign(conn, "personalisation", "", profile, settings)
            plan = C.plan_campaign(conn, campaign_id=campaign_id, leads=leads,
                                   template_id="", profile=profile, settings=settings,
                                   ai=None)
            assert plan["error"] == "" and plan["queued"] == 7, plan
            assert plan["generic"] == 3, plan["generic_reasons"]
            assert plan["generic_reasons"] == {"unreachable": 2, "nothing_found": 1}, \
                plan["generic_reasons"]
        finally:
            DB.close_all()
            ST.SETTINGS_DIR = original_dir


# ── The user template store ──


def _clear_store() -> None:
    try:
        os.remove(T.TEMPLATES_PATH)
    except OSError:
        pass
    T._forget_store()


@contextlib.contextmanager
def _empty_store():
    """An empty store for one test, and an empty store for the next one.

    Every assertion elsewhere in this file reads `get_template`, so a test that
    leaves an override behind rewrites the copy the rest of the file is checking.
    """
    _clear_store()
    try:
        yield
    finally:
        _clear_store()


MINE = T.Template(
    id="mine", name="My angle", step=0,
    subject="a question about {{business_name}}",
    body="Hi {{first_name}},\n\nMy own words about {{gap_1}}.\n\n{{sender_name}}")


def test_an_edited_builtin_takes_effect_everywhere_and_reset_undoes_it():
    """The user asked for templates they can actually change, and an edit that
    only the editing screen can see is not that. An override is keyed on the
    built-in's own id, so `get_template` and `templates_for_step` — which is
    every caller, the campaign planner and the preview pane included — see the
    edit with no other change anywhere.

    It is never a one-way door: `BUILTIN_TEMPLATES` is not touched, so the
    shipped copy is still underneath and `reset_template` hands it back."""
    with _empty_store():
        shipped = T.get_template("gap_direct")
        assert shipped == T.BUILTIN_TEMPLATES[0], shipped
        assert T.is_builtin("gap_direct") and not T.is_overridden("gap_direct")

        edited = T.Template(id="gap_direct", name="Headline gap, my words", step=0,
                            subject="{{gap_1_subject}} at {{business_name}}",
                            body="Hi {{first_name}},\n\nI read {{website_domain}} and "
                                 "one thing stood out: {{gap_1}}.\n\n{{sender_name}}")
        T.save_user_template(edited)

        assert T.get_template("gap_direct") == edited
        assert edited in T.templates_for_step(0)
        assert shipped not in T.templates_for_step(0)
        assert T.is_overridden("gap_direct") and T.is_builtin("gap_direct")
        assert T.load_user_templates() == [edited]

        # The shipped copy is untouched underneath, by both its names.
        assert T.BUILTIN_TEMPLATES[0] == shipped
        assert T.TEMPLATES is T.BUILTIN_TEMPLATES
        assert shipped not in T.load_user_templates()

        # And it renders, which is the only thing an override is for.
        subject, text, html = T.render(T.get_template("gap_direct"), FULL_CTX)
        assert subject and text and html
        assert "one thing stood out: no online booking." in text, text
        for bad in LEAK_PATTERNS:
            assert bad not in subject and bad not in text and bad not in html

        assert T.reset_template("gap_direct") == shipped
        assert T.get_template("gap_direct") == shipped
        assert not T.is_overridden("gap_direct")
        assert T.load_user_templates() == []

        # Resetting something that was never overridden is not an error.
        assert T.reset_template("gap_direct") == shipped
        assert T.delete_user_template("gap_direct") is False
        assert T.reset_template("nothing_by_that_name") is None

        # An override may move a built-in to another step, and it moves whole.
        moved = T.Template(id="followup_close", name="Close", step=3,
                           subject="last one", body="Hi {{first_name}},\n\n{{sender_name}}")
        T.save_user_template(moved)
        assert moved not in T.templates_for_step(2)
        assert T.templates_for_step(3) == [moved]
        assert T.reset_template("followup_close").step == 2


def test_a_user_template_survives_a_round_trip():
    """A template the user wrote is the one thing in this app that cannot be
    regenerated, so it goes to disk whole: the exact subject, the exact body,
    the accents and the quotes and the blank lines they typed."""
    with _empty_store():
        template_id = T.new_template_id("Second follow-up", 1)
        assert template_id == "second_follow_up", template_id
        assert not T.is_builtin(template_id)

        written = T.Template(
            id=template_id, name="Second follow-up", step=1,
            subject="une question sur {{business_name}}",
            body="Hi {{first_name}},\n\n"
                 "Ich habe \"{{website_domain}}\" gelesen: {{gap_1}}.\n\n"
                 "{{sender_name}}\n{{sender_title}}, {{company}}")
        T.save_user_template(written)

        T._forget_store()
        assert T.load_user_templates() == [written]
        assert T.get_template(template_id) == written
        assert T.templates_for_step(1)[-1] == written
        assert not T.is_overridden(template_id)   # nothing shipped underneath it

        subject, text, html = T.render(written, FULL_CTX)
        assert subject and text and html
        assert "Ich habe \"acmeplumbing.ca\" gelesen" in text, text

        # The file it went into is one valid JSON document with no debris beside it.
        with open(T.TEMPLATES_PATH, encoding="utf-8") as handle:
            stored = json.load(handle)
        assert stored["templates"] == [
            {"id": template_id, "name": "Second follow-up", "step": 1,
             "subject": written.subject, "body": written.body}], stored
        assert not os.path.exists(T.TEMPLATES_PATH + ".tmp")

        # A second id off the same name does not collide with the first.
        assert T.new_template_id("Second follow-up", 1) == "second_follow_up_2"
        # Nor can a new template be born as an override of a shipped one.
        assert T.new_template_id("Headline gap", 0) == "headline_gap"
        assert T.new_template_id("!!!", 0) == "custom"
        assert T.new_template_id("", 2) == "custom_step2"

        assert T.delete_user_template(template_id) is True
        assert T.get_template(template_id) is None
        assert T.load_user_templates() == []
        assert T.delete_user_template(template_id) is False


CORRUPT_STORES = (
    '{"templates": [{"id"',                    # truncated mid-write
    "",
    "   ",
    "null",
    "[]",
    "not json at all",
    '{"templates": "nope"}',
    '{"templates": {"id": "x"}}',
    '{"version": 1}',
    '[1, 2, "three", null]',
    '{"templates": [{"name": "no id at all"}]}',
    "\x00\x01\x02",
    '{"templates": [{"id": "x", "step": "later", "subject": null, "body": 42}]}',
)


def test_a_corrupt_store_costs_the_edits_and_nothing_else():
    """The store is one file the user is invited to hand-edit, so it will one day
    be half-written, saved from a text editor with a trailing comma, or truncated
    by a crash. None of that may take the app down with it: the built-ins are
    still there, the campaign still plans, and the next save writes a whole valid
    file over the top."""
    with _empty_store():
        for raw in CORRUPT_STORES:
            _clear_store()
            with open(T.TEMPLATES_PATH, "w", encoding="utf-8") as handle:
                handle.write(raw)
            loaded = T.load_user_templates()
            assert isinstance(loaded, list), raw
            # The last shape is readable, just badly typed; every other one is not.
            if "later" not in raw:
                assert loaded == [], (raw, loaded)
                assert T.all_templates() == T.BUILTIN_TEMPLATES, raw
                assert T.get_template("gap_direct") == T.BUILTIN_TEMPLATES[0], raw
            assert T.render(T.get_template("gap_direct"), FULL_CTX)[0], raw
            assert T.validate_template(MINE) is not None, raw

        # A hand-edited entry with the wrong types is read, not refused: a
        # plainer template beats a load that raises.
        _clear_store()
        with open(T.TEMPLATES_PATH, "w", encoding="utf-8") as handle:
            handle.write('{"templates": [{"id": "x", "step": "later", '
                         '"subject": null, "body": 42}]}')
        loose = T.load_user_templates()
        assert loose == [T.Template(id="x", name="x", step=0, subject="", body="42")], loose

        # A bare list is what a hand-written file usually is, and it loads.
        _clear_store()
        with open(T.TEMPLATES_PATH, "w", encoding="utf-8") as handle:
            json.dump([{"id": "hand", "name": "Hand written", "step": 0,
                        "subject": "s", "body": "b"}], handle)
        assert [t.id for t in T.load_user_templates()] == ["hand"]

        # And the next save repairs the file rather than appending to the wreck.
        _clear_store()
        with open(T.TEMPLATES_PATH, "w", encoding="utf-8") as handle:
            handle.write('{"templates": [{"id"')
        T.save_user_template(MINE)
        assert T.load_user_templates() == [MINE]
        with open(T.TEMPLATES_PATH, encoding="utf-8") as handle:
            assert json.load(handle)["version"] == 1

        # A store that cannot be written leaves the one on disk exactly as it was.
        original = T.TEMPLATES_PATH
        try:
            T.TEMPLATES_PATH = os.path.join(_STORE_DIR, "no", "such", "\0", "x.json")
            T.save_user_template(MINE)
            assert T.delete_user_template("mine") is False
        finally:
            T.TEMPLATES_PATH = original
            T._forget_store()
        assert T.load_user_templates() == [MINE]


def test_all_templates_ordering_is_stable():
    """A picker is rebuilt from this on every refresh. If the order moved when a
    template was renamed or edited, the row under the user's cursor would move
    with it."""
    with _empty_store():
        assert T.all_templates() == T.BUILTIN_TEMPLATES
        assert [t.id for t in T.all_templates()] == [
            "gap_direct", "time_saved", "question", "followup_bump", "followup_close"]

        # Saved in an order chosen to be wrong: the result is step, then the
        # shipped order inside a step, then name.
        for tpl in (
            T.Template(id="zulu", name="Zulu", step=0, subject="s", body="b"),
            T.Template(id="another", name="Another bump", step=1, subject="s", body="b"),
            T.Template(id="alpha", name="Alpha", step=0, subject="s", body="b"),
            T.Template(id="question", name="zzz renamed", step=0,
                       subject="s", body="b"),
        ):
            T.save_user_template(tpl)

        expected = ["gap_direct", "time_saved", "question", "alpha", "zulu",
                    "followup_bump", "another", "followup_close"]
        assert [t.id for t in T.all_templates()] == expected
        # An override keeps the slot its built-in had, whatever it is renamed to.
        assert T.get_template("question").name == "zzz renamed"

        for _ in range(3):
            assert [t.id for t in T.all_templates()] == expected
        assert [t.id for t in T.templates_for_step(0)] == expected[:5]
        assert [t.id for t in T.templates_for_step(1)] == ["followup_bump", "another"]
        assert [t.id for t in T.templates_for_step(2)] == ["followup_close"]

        # Two entries under one id is a file somebody edited by hand; the last
        # one wins and the picker still shows one row per id.
        _clear_store()
        with open(T.TEMPLATES_PATH, "w", encoding="utf-8") as handle:
            json.dump({"templates": [
                {"id": "dupe", "name": "First", "step": 0, "subject": "s", "body": "b"},
                {"id": "dupe", "name": "Second", "step": 0, "subject": "s", "body": "b"},
            ]}, handle)
        ids = [t.id for t in T.all_templates()]
        assert ids.count("dupe") == 1, ids
        assert T.get_template("dupe").name == "Second"


# `render`'s output for the shipped five, pinned. Everything else in this file
# says what the copy must not do; this says what it does, so a store, a merge or
# a refactor that quietly changes a live email fails here first.
BUILTIN_RENDERS = (
    ("gap_direct", "online booking at Acme Plumbing & Heating",
     "750d8a495a225cee456ae05a516e23e6afe5df94",
     "7be49db9f2328114d225cb6bb00aa27cf54d8bf5"),
    ("time_saved", "the same few minutes, over and over",
     "86216f218f47eb3bf0fa49250128dd8e9e930c52",
     "4ca0250ea61cf7fcaa8a06686cb11dc86ba0529d"),
    ("question", "how do messages reach Acme Plumbing & Heating?",
     "5fa378135442251f7f4321ac0df29036aadf7ed5",
     "3dbdbcd6d570b0ebffd91f4db07ce3fa9ef92c9c"),
    ("followup_bump", "one more note on online booking",
     "042022c97382a622778818f34d59ac363d1fd93a",
     "4398345c323ae9ad52ab88644596026b931dd4a3"),
    ("followup_close", "last one from me",
     "f3cd7143b742a188aafa64322366240c1233191e",
     "1bac17ee419da4ee95fca5477264330f8fa01628"),
)


def test_the_five_builtins_still_render_exactly_as_they_do_today():
    with _empty_store():
        ctx = dict(FULL_CTX, ai_subject="")
        assert len(BUILTIN_RENDERS) == len(T.BUILTIN_TEMPLATES) == 5
        for (template_id, subject, text_hash, html_hash), tpl in zip(
                BUILTIN_RENDERS, T.BUILTIN_TEMPLATES):
            assert tpl.id == template_id, tpl.id
            got_subject, text, html = T.render(tpl, ctx)
            assert got_subject == subject, (template_id, got_subject)
            assert hashlib.sha1(text.encode("utf-8")).hexdigest() == text_hash, \
                (template_id, text)
            assert hashlib.sha1(html.encode("utf-8")).hexdigest() == html_hash, \
                (template_id, html)
            assert T.get_template(template_id) is tpl

        # One template overridden changes that one template and nothing else.
        T.save_user_template(MINE)
        T.save_user_template(T.Template(id="time_saved", name="Mine", step=0,
                                        subject="s", body="Hi {{first_name}},\n\nb"))
        for template_id, subject, text_hash, _html_hash in BUILTIN_RENDERS:
            if template_id == "time_saved":
                continue
            got_subject, text, _html = T.render(T.get_template(template_id), ctx)
            assert got_subject == subject, (template_id, got_subject)
            assert hashlib.sha1(text.encode("utf-8")).hexdigest() == text_hash, template_id


# ── Template validation ──


def test_validate_template_warns_and_never_blocks():
    """The user asked not to be blocked, and this is what replaces the blocking:
    every rule is a sentence saying what the choice costs, next to the field it
    is about, and the send goes ahead either way. The shipped five have to come
    back clean or the panel is noise and gets ignored."""
    with _empty_store():
        for tpl in T.BUILTIN_TEMPLATES:
            assert T.validate_template(tpl) == [], (tpl.id, T.validate_template(tpl))

        # A misspelt merge field renders as nothing and takes its sentence with
        # it, which is the one failure here that is completely silent.
        typo = T.Template(id="typo", name="Typo", step=0,
                          subject="hello", body="Hi {{first_name}},\n\n"
                                                "A note for {{buisness_name}}.")
        found = T.validate_template(typo)
        merge = [f for f in found if "buisness_name" in f["message"]]
        assert len(merge) == 1, found
        assert merge[0]["level"] == "error" and merge[0]["field"] == "body", merge
        assert "{{business_name}}" in merge[0]["message"], merge[0]["message"]

        # A subject over the cap, a subject that claims a thread that never
        # existed, and a body with two links.
        loud = T.Template(
            id="loud", name="Loud", step=0,
            subject="Re: a subject line that is comfortably past the fifty-five "
                    "character cap",
            body="Hi {{first_name}},\n\nSee {{calendar_link}} and "
                 "https://autoarmy.io/pricing.\n\n{{sender_name}}")
        found = T.validate_template(loud)
        assert [f["field"] for f in found] == ["subject", "subject", "body"], found
        assert all(f["level"] == "warning" for f in found), found
        assert "55" in found[0]["message"] and str(len(loud.subject)) in found[0]["message"]
        assert "Re:" in found[1]["message"]
        assert "2 links" in found[2]["message"], found[2]["message"]

        # Every finding is the shape the editor is going to render.
        for tpl in (typo, loud, MINE, T.Template(id="e", name="", step=0,
                                                 subject="", body="")):
            for finding in T.validate_template(tpl):
                assert set(finding) == {"level", "field", "message"}, finding
                assert finding["level"] in ("error", "warning"), finding
                assert finding["field"] in ("subject", "body", "name"), finding
                assert isinstance(finding["message"], str) and finding["message"].strip()

        # And nothing it finds stops anything: the worst template in the file
        # still renders a subject and a body.
        for tpl in (typo, loud):
            subject, text, html = T.render(tpl, FULL_CTX)
            assert subject and text and html, tpl.id
            for bad in LEAK_PATTERNS:
                assert bad not in subject and bad not in text and bad not in html


def test_validate_template_names_the_deliverability_cost_rather_than_refusing():
    """The guardrails the user asked to be rid of are the two lines that keep
    this mail out of the spam folder, so they survive as sentences instead: what
    is missing, and what it costs. `render` writes both into the footer from the
    sender profile, so an empty profile is what the warning is actually about,
    and a body that carries them itself is warned about for the opposite reason
    — it would send them twice."""
    with _empty_store():
        plain = T.Template(id="plain", name="Plain", step=0, subject="a note",
                           body="Hi {{first_name}},\n\nOne line.\n\n{{sender_name}}")
        # The footer carries both, so with a full context there is nothing to say.
        assert T.validate_template(plain, FULL_CTX) == []
        assert T.validate_template(plain) == []

        bare = T.build_context(LEAD, AUDIT, AI, dict(PROFILE, postal_address=""),
                               {"smtp_accounts": []})
        found = T.validate_template(plain, bare)
        assert [f["field"] for f in found] == ["body"], found
        assert found[0]["level"] == "warning"
        assert "postal address" in found[0]["message"], found[0]["message"]
        assert "spam" in found[0]["message"].lower(), found[0]["message"]

        doubled = T.Template(id="doubled", name="Doubled", step=0, subject="a note",
                             body="Hi {{first_name}},\n\n{{sender_name}}\n"
                                  "{{postal_address}}\n{{unsubscribe_line}}")
        found = T.validate_template(doubled, FULL_CTX)
        assert len(found) == 1 and found[0]["field"] == "body", found
        assert "twice" in found[0]["message"], found[0]["message"]

        # Both word lists are advisory and neither is a filter: a template made
        # of nothing but trigger words is described, and then it sends.
        for advisory in (T.SPAM_TRIGGER_WORDS, T.LINK_SHORTENERS):
            assert isinstance(advisory, tuple) and len(advisory) > 10, advisory
            assert all(term == term.lower() and term.strip() for term in advisory), advisory
            assert len(advisory) == len(set(advisory)), advisory
        shouty = T.Template(
            id="shouty", name="Shouty", step=0,
            subject="ACT NOW - LIMITED TIME!!",
            body="Hi {{first_name}},\n\nCLICK HERE for a FREE TRIAL, 100% free, no "
                 "obligation, satisfaction guaranteed. https://bit.ly/x\n\n"
                 "{{sender_name}}")
        found = T.validate_template(shouty)
        assert {f["field"] for f in found} == {"subject", "body"}, found
        assert all(f["level"] == "warning" for f in found), found
        assert any("bit.ly" in f["message"] for f in found), found
        assert any("shouting" in f["message"] for f in found), found
        subject, text, html = T.render(shouty, FULL_CTX)
        assert subject and text and html

        # The shortener list is bounded, so a host that merely contains one is
        # not one: "t.co" sits inside "client.com".
        clean = T.Template(id="clean", name="Clean", step=0, subject="a note",
                           body="Hi {{first_name}},\n\nSee https://client.com/book "
                                "and orbit.lythe.\n\n{{sender_name}}")
        assert not any("shortener" in f["message"] for f in T.validate_template(clean)), \
            T.validate_template(clean)

        # A name nobody typed, and a name two templates share.
        T.save_user_template(MINE)
        assert [f["field"] for f in T.validate_template(
            T.Template(id="noname", name="", step=0, subject="a note",
                       body="Hi {{first_name}},\n\n{{sender_name}}"))] == ["name"]
        clash = T.validate_template(
            T.Template(id="other", name="My angle", step=0, subject="a note",
                       body="Hi {{first_name}},\n\n{{sender_name}}"))
        assert [f["field"] for f in clash] == ["name"], clash
        assert "My angle" in clash[0]["message"]
        # The template that already owns the name does not clash with itself.
        assert T.validate_template(MINE) == []


def test_validate_template_never_raises_whatever_it_is_handed():
    """It runs on every keystroke in an editor, on whatever is in the box at the
    time. Half a merge field is the normal state of a template being typed."""
    with _empty_store():
        for junk in (None, {}, "", 0, [], object(), {"id": None},
                     {"id": "x", "subject": None, "body": None, "step": None},
                     T.Template(id="", name="", step=0, subject="", body=""),
                     {"id": "x", "name": "x", "step": 0,
                      "subject": "{{business_name", "body": "{first_name} {{"},
                     {"id": "x", "name": "x", "step": 0, "subject": "}}{{",
                      "body": "{{" * 400}):
            found = T.validate_template(junk)
            assert isinstance(found, list), junk
            for finding in found:
                assert set(finding) == {"level", "field", "message"}, finding
            assert T.validate_template(junk, {}) is not None
            assert T.validate_template(junk, FULL_CTX) is not None

        # The half-typed shapes are named, not swallowed.
        unbalanced = T.validate_template({"id": "x", "name": "x", "step": 0,
                                          "subject": "{{business_name",
                                          "body": "Hi {{first_name}},"})
        assert any("Unbalanced" in f["message"] for f in unbalanced), unbalanced
        single = T.validate_template({"id": "x", "name": "x", "step": 0,
                                      "subject": "a note",
                                      "body": "Hi {first_name},"})
        assert any("single brace" in f["message"].lower() for f in single), single

        # A template with no id cannot be stored, and says so once.
        empty = T.validate_template({})
        assert len(empty) == 1 and empty[0]["field"] == "name", empty
        assert T.save_user_template({}) is None and T.load_user_templates() == []
        assert T.save_user_template(None) is None and T.load_user_templates() == []


if __name__ == "__main__":
    test_catalogue()
    test_gap_services()
    test_no_catalogue_heading_is_offered_to_a_prospect()
    test_every_value_that_reaches_a_service_slot_is_a_service()
    test_a_gap_only_leads_when_there_is_an_offer_behind_it()
    test_no_tokens_survive()
    test_render_degrades_cleanly()
    test_no_orphan_back_references()
    test_a_sentence_that_can_vanish_is_the_last_on_its_line()
    test_gap_titles_never_govern_a_verb()
    test_category_only_ever_qualifies_a_noun()
    test_service_slots_are_number_neutral()
    test_templates_invent_no_figures()
    test_no_credential_is_built_out_of_the_lead()
    test_role_addresses_are_not_greeted_as_people()
    test_model_output_is_cleaned_before_it_is_trusted()
    test_no_template_asserts_a_site_feature_the_audit_did_not_find()
    test_every_gap_reads_as_a_reason_in_the_question_template()
    test_a_doubled_greeting_is_stripped_however_many_deep()
    test_short_owner_mailboxes_are_greeted_by_name()
    test_a_model_track_record_never_reaches_the_reader()
    test_subject_uses_the_neutral_gap_phrase()
    test_subject_rules()
    test_subject_never_ends_on_a_joiner()
    test_the_subject_fallback_obeys_the_subject_rules()
    test_the_sign_off_never_ends_on_a_dangling_comma()
    test_the_proof_point_reads_as_its_own_paragraph()
    test_no_merge_field_is_resolved_but_unused()
    test_first_touch_length()
    test_copy_conventions()
    test_html_shape()
    test_the_two_mime_parts_carry_the_same_copy()
    test_build_context()
    test_never_raises()
    test_the_proof_point_obeys_the_no_exclamation_rule()
    test_a_snippet_that_loses_a_fragment_is_dropped_not_shipped_with_a_hole()
    test_a_business_name_with_nothing_in_it_falls_back()
    test_a_malformed_website_never_leaves_the_lead_it_arrived_on()
    test_one_bad_record_cannot_discard_the_whole_campaign()
    test_a_fabricated_record_that_also_anchors_never_reaches_the_reader()
    test_a_business_name_arrives_as_text_not_as_markup()
    test_every_pass_survives_one_exploding_lead()
    test_a_campaign_says_how_much_of_it_could_not_be_personalised()
    test_east_asian_mailboxes_are_greeted_by_name()
    test_an_edited_builtin_takes_effect_everywhere_and_reset_undoes_it()
    test_a_user_template_survives_a_round_trip()
    test_a_corrupt_store_costs_the_edits_and_nothing_else()
    test_all_templates_ordering_is_stable()
    test_the_five_builtins_still_render_exactly_as_they_do_today()
    test_validate_template_warns_and_never_blocks()
    test_validate_template_names_the_deliverability_cost_rather_than_refusing()
    test_validate_template_never_raises_whatever_it_is_handed()
    print("\nALL TEMPLATE TESTS PASSED")
