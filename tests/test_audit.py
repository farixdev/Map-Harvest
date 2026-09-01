"""Offline tests for core.audit. No network, no Qt.

Run:  venv/Scripts/python.exe -m tests.test_audit
(or `python -m pytest tests/ -q` where pytest is installed).

Every fixture below is handwritten HTML built from markers real sites actually
ship. Two things are load-bearing and are asserted hardest: that a detected gap
names services that exist verbatim in `core.templates.AUTO_ARMY_SERVICES` (a gap
pitching a service the seller does not offer is a lie in a live email), and that
`digest` stays inside its character budget (it is the only thing billed per
lead).
"""

import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audit as A  # noqa: E402
from core import templates as T  # noqa: E402

TODAY = datetime.date.today()

# Every service name the catalogue knows, categories included.
CATALOGUE_NAMES = {c.lower() for c in T.AUTO_ARMY_SERVICES}
CATALOGUE_NAMES |= {n.lower() for names in T.AUTO_ARMY_SERVICES.values() for n in names}


# ── Fixtures ──

WORDPRESS_PLUMBER = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="WordPress 6.4.2">
<meta name="description" content="Emergency plumbers in Toronto since 1998.">
<title>Acme Plumbing &amp; Heating | Toronto Plumbers</title>
<link rel="stylesheet" href="/wp-content/themes/acme/style.css?ver=6.4.2">
<script src="/wp-includes/js/jquery/jquery.min.js"></script>
</head><body>
<nav>
  <a href="/">Home</a>
  <a href="/services/emergency-plumbing/">Emergency Plumbing</a>
  <a href="/services/boiler-installation/">Boiler Installation</a>
  <a href="/services/drain-clearing/">Drain Clearing</a>
  <a href="/about/">About</a>
  <a href="/contact/">Contact</a>
</nav>
<h1>Toronto plumbers, same day service</h1>
<h2>Bathroom fitting and repair</h2>
<p>Book an appointment by sending the form below and we will call you back.</p>
<form action="/contact/" method="post">
  <input type="text" name="your-name" placeholder="Name">
  <input type="email" name="your-email" placeholder="Email">
  <textarea name="your-message"></textarea>
  <button type="submit">Send</button>
</form>
<footer><p>&copy; 2024 Acme Plumbing &amp; Heating. 89 King St W, Toronto, ON M5H 1A1.</p></footer>
</body></html>"""

SHOPIFY_STORE = """<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width">
<title>Northside Supply</title>
<script>var Shopify = Shopify || {}; Shopify.theme = {"name":"Dawn","id":123};</script>
<link href="https://cdn.shopify.com/s/files/1/0001/theme.css" rel="stylesheet">
</head><body>
<a href="/collections/all">Shop</a>
<a href="/cart">Cart</a>
<h1>Northside Supply</h1>
<p>Workwear and tools. Orders ship from Hamilton.</p>
<form action="/cart/add" method="post"><input type="hidden" name="id" value="1"><button>Add to cart</button></form>
<p>Boots from $129.00, jackets $189.00, gloves $24.99.</p>
<footer>&copy; %d Northside Supply</footer>
</body></html>""" % TODAY.year

CAREERS_PAGE = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width"><title>Riverbend Dental</title></head>
<body>
<nav><a href="/">Home</a><a href="/careers">Careers</a><a href="/contact">Contact</a></nav>
<h1>Riverbend Dental</h1>
<h2>We are hiring hygienists</h2>
<p>Download the <a href="/files/employment-application-form.pdf">employment application form</a>
and email it back to us.</p>
<a href="https://calendly.com/riverbend/checkup">Book an appointment</a>
<script src="https://widget.intercom.io/widget/abc123"></script>
<script src="https://js.hs-scripts.com/1234567.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123"></script>
<footer>&copy; %d Riverbend Dental</footer>
</body></html>""" % TODAY.year

STALE_BLOG = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width"><title>Hillside Roofing</title></head>
<body>
<nav><a href="/">Home</a><a href="/blog/">Blog</a></nav>
<h1>Hillside Roofing</h1>
</body></html>"""

# The default shape for `no_online_booking`: the contact form is the home page's
# own, so the page it sits on is "/". Every brochure site in the list looks like
# this, which is why the gap's evidence may never name the page it found. The
# download is the other default — a file the site gives no readable name to.
HOME_PAGE_FORM = """<!doctype html>
<html lang="en"><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lakeshore Physio</title>
</head><body>
<h1>Lakeshore Physio</h1>
<h2>Sports injury treatment</h2>
<p>Emergency slots most days. Send the form and we will get back to you.</p>
<p>New here? <a href="/uploads/intake_2019_v2.pdf">Click here</a> before your visit.</p>
<form action="/enquiry" method="post">
  <input type="text" name="your-name">
  <input type="email" name="your-email">
  <textarea name="your-message"></textarea>
</form>
<footer>&copy; %d Lakeshore Physio</footer>
</body></html>""" % TODAY.year

STALE_BLOG_INDEX = """<!doctype html><html><body>
<article>
  <h2>Choosing shingles</h2>
  <time datetime="%s">%s</time>
</article>
</body></html>"""


# A site with nothing to sell against: booking, chat, CRM, analytics, schema,
# social, prices, a mobile layout and a footer from this year.
WELL_RUN = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harbourview Dental</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XYZ"></script>
<script src="https://embed.tawk.to/hv/default"></script>
<script src="https://js.hs-scripts.com/778899.js"></script>
<script type="application/ld+json">{"@type":"Dentist","name":"Harbourview Dental"}</script>
</head><body>
<nav>
  <a href="https://calendly.com/harbourview/checkup">Book online</a>
  <a href="https://www.facebook.com/harbourviewdental">Facebook</a>
</nav>
<h1>Harbourview Dental</h1>
<p>Check-up $95.00, cleaning $140.00, whitening $350.00.</p>
<form action="/enquiry"><input type="email" name="email"><textarea name="message"></textarea></form>
<footer>&copy; %d Harbourview Dental. 42 Quay St, Halifax, NS B3J 1P6.</footer>
</body></html>""" % TODAY.year


def _stale_blog_pages(days_old: int) -> dict:
    when = TODAY - datetime.timedelta(days=days_old)
    index = STALE_BLOG_INDEX % (when.isoformat(), when.strftime("%B %d, %Y"))
    return {"https://hillsideroofing.ca/": STALE_BLOG,
            "https://hillsideroofing.ca/blog/": index}


def _codes(result: dict) -> list[str]:
    return [g["code"] for g in result["gaps"]]


# ── Gap catalogue integrity ──


def test_catalogue_services_are_real():
    # `slow_site`, `no_mobile` and `no_ssl` are the three the catalogue has no
    # answer for — page speed, a mobile layout and a certificate are hosting and
    # front-end work and the seller does not sell any of it. They carry no
    # services here and no entry in `T.GAP_SERVICES`, and every other code has
    # to be in both tables.
    #
    # (Superseded: this set read `{"slow_site", "no_mobile"}` until `no_ssl` was
    # added. It is a third piece of evidence with no offer behind it, not a
    # weakening — `_gaps` still sorts every offerless gap behind every gap that
    # carries one, so none of the three can become the sentence an offer has to
    # follow.)
    no_offer = {code for code, entry in A.GAP_CATALOGUE.items() if not entry["services"]}
    assert no_offer == {"slow_site", "no_mobile", "no_ssl"}, no_offer
    assert list(A.GAP_CATALOGUE)
    assert set(A.GAP_CATALOGUE) - no_offer == set(T.GAP_SERVICES), (
        (set(A.GAP_CATALOGUE) - no_offer) ^ set(T.GAP_SERVICES))
    for code, entry in A.GAP_CATALOGUE.items():
        assert entry["severity"] in (1, 2, 3), code
        # Titles drop into the middle of a sentence, so they start lower-case
        # and stay short enough for a subject line.
        assert entry["title"] and entry["title"][0].islower(), code
        assert len(entry["title"]) <= 40, code
        assert entry["services"] or code in no_offer, code
        for name in entry["services"]:
            assert name.lower() in CATALOGUE_NAMES, (code, name)
    print("catalogue services are real: OK")


def test_subject_phrases_are_neutral():
    """`title` gets a sentence around it in the body. `subject_phrase` gets the
    subject line, where it is the first thing a stranger reads about themselves,
    so it names the topic instead of passing judgement on it."""
    # Verdicts. Fine mid-sentence with evidence attached, an insult in a subject.
    jabs = ("no ", "nobody", "nothing", "by hand", "manually", "gone quiet",
            "and nothing else", "opaque", "stale", "slow", "years")
    for code, entry in A.GAP_CATALOGUE.items():
        phrase = entry["subject_phrase"]
        assert phrase and phrase[0].islower(), code
        # The business name follows it inside a 55-character subject cap.
        assert 4 <= len(phrase) <= 30, (code, len(phrase))
        assert not phrase.endswith((".", "!", "?")), code
        assert phrase != entry["title"], code
        for jab in jabs:
            assert jab not in phrase.lower(), (code, jab)

    # It reaches the gap dicts the templates read, on the real detection path.
    result = A.audit_from_html({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER},
                               "https://acmeplumbing.ca/")
    assert result["gaps"]
    for gap in result["gaps"]:
        assert gap["subject_phrase"] == A.GAP_CATALOGUE[gap["code"]]["subject_phrase"], gap

    # And every one of the 18 renders inside the subject cap, blunt title absent.
    # The two the catalogue cannot answer never lead, so their phrase is not in
    # the subject either — the subject falls back to the business name, which is
    # what it does for a lead with no gaps at all.
    lead = {"name": "Acme Plumbing Ltd", "email": "mike@acmeplumbing.ca"}
    for code, entry in A.GAP_CATALOGUE.items():
        ctx = T.build_context(lead, {"gaps": [A._gap(code, "evidence")]}, {}, {}, {})
        for template_id in ("gap_direct", "followup_bump"):
            subject = T.render(T.get_template(template_id), ctx)[0]
            assert subject and len(subject) <= T.SUBJECT_MAX, (code, template_id, subject)
            assert entry["title"] not in subject, (code, template_id, subject)
            if entry["services"]:
                assert entry["subject_phrase"] in subject, (code, template_id, subject)
            else:
                assert entry["subject_phrase"] not in subject, (code, template_id, subject)
    print("subject phrases are neutral: OK")


# ── The three assigned fixtures ──


def test_wordpress_plumber():
    result = A.audit_from_html({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER},
                               "https://acmeplumbing.ca/")
    assert result["reachable"] and result["error"] == ""
    assert result["tech"]["cms"] == "wordpress", result["tech"]
    assert result["tech"]["chat"] == "" and result["tech"]["crm"] == ""
    assert result["tech"]["forms"] == 1
    assert result["signals"]["has_contact_form"] is True
    assert result["brand"] == "Acme Plumbing & Heating", result["brand"]
    assert result["h1"].startswith("Toronto plumbers")

    codes = _codes(result)
    assert "no_live_chat" in codes, codes
    assert "no_crm_signals" in codes, codes

    fired = {g["code"]: g for g in result["gaps"]}
    for code in ("no_live_chat", "no_crm_signals"):
        services = fired[code]["services"]
        assert services, code
        for name in services:
            assert name.lower() in CATALOGUE_NAMES, (code, name)
        assert fired[code]["evidence"]
    # The pitch for those two gaps is the one the seller actually makes.
    assert "AI customer-support agents" in fired["no_live_chat"]["services"]
    assert "CRM & Sales Automation" in fired["no_crm_signals"]["services"]
    print("wordpress plumber: OK")


def test_shopify_store():
    result = A.audit_from_html({"https://northsidesupply.ca/": SHOPIFY_STORE},
                               "https://northsidesupply.ca/")
    assert result["tech"]["ecommerce"] == "shopify", result["tech"]
    assert result["tech"]["cms"] == "shopify", result["tech"]
    codes = _codes(result)
    assert "ecommerce_manual" in codes, codes
    # Prices are on the page, so the opaque-pricing gap must stay silent.
    assert "price_opaque" not in codes, codes

    gap = next(g for g in result["gaps"] if g["code"] == "ecommerce_manual")
    assert gap["severity"] == 2
    # The fingerprint is what fires the gap; it is not what the owner is told.
    # "a woocommerce store on the site" is a vendor key read out of a script src.
    assert "shopify" not in gap["evidence"].lower(), gap["evidence"]
    assert "shop on the site" in gap["evidence"], gap["evidence"]
    for name in gap["services"]:
        assert name.lower() in CATALOGUE_NAMES, name
    print("shopify store: OK")


def test_careers_page():
    result = A.audit_from_html({"https://riverbenddental.ca/": CAREERS_PAGE},
                               "https://riverbenddental.ca/")
    codes = _codes(result)
    assert "careers_manual" in codes, codes
    assert "pdf_forms" in codes, codes

    # Not "HR processes": it is a catalogue entry, but the slot it reaches reads
    # "the fix is ___", and there it names a department instead of a deliverable.
    gap = next(g for g in result["gaps"] if g["code"] == "careers_manual")
    assert gap["services"] == ["employee onboarding", "AI document/data extraction"], \
        gap["services"]

    # The PDF is named the way the site names it, not the way the server files it.
    paperwork = next(g for g in result["gaps"] if g["code"] == "pdf_forms")
    assert paperwork["evidence"].startswith("the employment application form is a PDF"), (
        paperwork["evidence"])

    # The same page proves the positive detections, which is what keeps the
    # negative ones honest.
    assert result["tech"]["chat"] == "intercom", result["tech"]
    assert result["tech"]["crm"] == "hubspot", result["tech"]
    assert result["tech"]["booking"] == "calendly", result["tech"]
    # Was `== ["ga4"]`. This page loads js.hs-scripts.com, which is HubSpot's
    # page-view tracking as well as its CRM and its chat widget, so a site with
    # that one script on it is measured whether or not Google is also watching.
    # Reading the script as a CRM and nothing else is what put "no analytics on
    # any page, so last month's visits went uncounted" in front of businesses
    # that count them.
    assert result["tech"]["analytics"] == ["ga4", "hubspot"], result["tech"]
    assert result["signals"]["has_online_booking"] is True
    for absent in ("no_live_chat", "no_crm_signals", "no_online_booking", "no_analytics"):
        assert absent not in codes, (absent, codes)
    print("careers page: OK")


# ── Fingerprints ──


def test_cms_fingerprints():
    cases = {
        "wix": '<script src="https://static.wixstatic.com/bundler/main.js"></script>',
        "squarespace": '<script>Static.SQUARESPACE_CONTEXT = {};</script>'
                       '<img src="https://images.squarespace-cdn.com/x.jpg">',
        "webflow": '<html data-wf-page="abc"><body><script src="https://assets.website-files.com/x.js">'
                   '</script></body></html>',
        "duda": '<link href="https://irp.cdn-website.com/site.css" rel="stylesheet">',
        "godaddy": '<img src="https://img1.wsimg.com/isteam/logo.png">',
    }
    for expected, snippet in cases.items():
        page = "<html><head><title>X</title></head><body>%s</body></html>" % snippet
        result = A.audit_from_html({"https://x.test/": page}, "https://x.test/")
        assert result["tech"]["cms"] == expected, (expected, result["tech"]["cms"])

    plain = A.audit_from_html({"https://x.test/": "<html><body><p>hi</p></body></html>"},
                              "https://x.test/")
    assert plain["tech"]["cms"] == "custom", plain["tech"]
    print("cms fingerprints: OK")


def test_chat_and_booking_fingerprints():
    chat = {
        "tawk": '<script src="https://embed.tawk.to/abc/default"></script>',
        "crisp": "<script>window.$crisp=[];CRISP_WEBSITE_ID='abc';</script>",
        "drift": '<script src="https://js.driftt.com/include/x/abc.js"></script>',
        "tidio": '<script src="//code.tidio.co/abc.js"></script>',
        "livechat": '<script src="https://cdn.livechatinc.com/tracking.js"></script>',
        "messenger": '<div class="fb-customerchat" page_id="1"></div>',
    }
    for expected, snippet in chat.items():
        result = A.audit_from_html({"https://x.test/": "<html><body>%s</body></html>" % snippet},
                                   "https://x.test/")
        assert result["tech"]["chat"] == expected, (expected, result["tech"]["chat"])
        assert "no_live_chat" not in _codes(result), expected

    booking = {
        "acuity": '<a href="https://app.acuityscheduling.com/schedule.php?owner=1">Book</a>',
        "setmore": '<a href="https://my.setmore.com/x">Book</a>',
        "housecallpro": '<a href="https://book.housecallpro.com/book/x">Book</a>',
        "servicetitan": '<script src="https://cdn.servicetitan.com/booking.js"></script>',
        "mindbody": '<script src="https://widgets.mindbodyonline.com/x.js"></script>',
    }
    for expected, snippet in booking.items():
        result = A.audit_from_html({"https://x.test/": "<html><body>%s</body></html>" % snippet},
                                   "https://x.test/")
        assert result["tech"]["booking"] == expected, (expected, result["tech"]["booking"])
        assert "no_online_booking" not in _codes(result), expected
    print("chat and booking fingerprints: OK")


# ── Blog staleness ──


def test_blog_staleness():
    stale = A.audit_from_html(_stale_blog_pages(900), "https://hillsideroofing.ca/")
    assert stale["signals"]["has_blog"] is True
    assert stale["signals"]["blog_stale"] is True, stale["signals"]
    assert stale["signals"]["blog_year"] == (TODAY - datetime.timedelta(days=900)).year
    assert "stale_blog" in _codes(stale)

    fresh = A.audit_from_html(_stale_blog_pages(30), "https://hillsideroofing.ca/")
    assert fresh["signals"]["has_blog"] is True
    assert fresh["signals"]["blog_stale"] is False, fresh["signals"]
    assert "stale_blog" not in _codes(fresh)

    # A blog with no dates anywhere is never called stale: no date, no claim.
    undated = A.audit_from_html({"https://hillsideroofing.ca/": STALE_BLOG},
                                "https://hillsideroofing.ca/")
    assert undated["signals"]["has_blog"] is True
    assert undated["signals"]["blog_stale"] is False
    assert "stale_blog" not in _codes(undated)
    print("blog staleness: OK")


# ── Ordering and score ──


def test_ordering_and_score():
    for pages, base in (
        ({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER}, "https://acmeplumbing.ca/"),
        ({"https://northsidesupply.ca/": SHOPIFY_STORE}, "https://northsidesupply.ca/"),
        ({"https://riverbenddental.ca/": CAREERS_PAGE}, "https://riverbenddental.ca/"),
        (_stale_blog_pages(900), "https://hillsideroofing.ca/"),
    ):
        result = A.audit_from_html(pages, base)
        # An offer first, then severity, then catalogue order. `gaps[0]` is what
        # the email leads with and the paragraph under it is an offer, so a gap
        # the catalogue cannot answer sorts behind every gap it can, however bad
        # the finding is.
        order = list(A.GAP_CATALOGUE)
        keys = [(not g["services"], -g["severity"], order.index(g["code"]))
                for g in result["gaps"]]
        assert keys == sorted(keys), keys

        offered = [g for g in result["gaps"] if g["services"]]
        severities = [g["severity"] for g in offered]
        assert severities == sorted(severities, reverse=True), severities

        assert 0 <= result["opportunity_score"] <= 100, result["opportunity_score"]
        assert result["opportunity_score"] >= 10  # reachable

    empty = A.audit_from_html({}, "https://nothing.test/")
    assert empty["opportunity_score"] == 0 and empty["reachable"] is False
    assert empty["gaps"] == [] and empty["pages"] == []
    print("ordering and score: OK")


def test_score_separates_leads():
    """The score is the column the operator sorts leads by, so it has to rank.

    Adding severity*9 straight across the fired gaps put every site with a
    contact form past the cap — nine gaps on a one-page brochure total 162 — so
    the column read 100 all the way down and ordered nothing. Each fixture below
    is checked twice: once for what straight addition would have said about it,
    and once for what it says now.
    """
    graded = []
    for pages, base in (
        ({"https://harbourview.ca/": WELL_RUN}, "https://harbourview.ca/"),
        ({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER}, "https://acmeplumbing.ca/"),
        ({"https://lakeshorephysio.ca/": HOME_PAGE_FORM}, "https://lakeshorephysio.ca/"),
    ):
        result = A.audit_from_html(pages, base)
        flat = min(100, sum(g["severity"] for g in result["gaps"]) * 9 + 10
                   + (5 if result["signals"]["has_email"] else 0))
        graded.append((result["opportunity_score"], flat, len(result["gaps"])))

    # A site with nothing to fix has nothing to pitch, and the score says so.
    assert graded[0][2] == 0 and graded[0][0] < 20, graded[0]

    scores = [score for score, _flat, _gaps in graded]
    assert scores == sorted(scores), scores
    assert len(set(scores)) == 3, scores

    # The two below it are the reason this test exists: both pinned at the cap.
    for score, flat, count in graded[1:]:
        assert flat == 100, (score, flat, count)
        assert score < 100, (score, flat, count)
    print("score separates leads: OK")


# ── Digest ──


def test_digest_shape():
    result = A.audit_from_html({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER},
                               "https://acmeplumbing.ca/")
    text = A.digest(result)
    assert len(text) < 1200, len(text)

    lines = text.split("\n")
    assert len(lines) == 5, lines
    assert lines[0].startswith("SITE: acmeplumbing.ca"), lines[0]
    prefixes = [line.split(":")[0] for line in lines]
    assert prefixes == ["SITE", "WHAT", "STACK", "SIGNALS", "TOP GAPS"], prefixes

    assert "no chat" in text and "no crm" in text and "wordpress" in text
    # The headline gap has to survive into the only thing the model reads.
    assert result["gaps"][0]["title"] in text, text
    assert "<" not in text and "http" not in text
    print("digest shape: OK")


def test_digest_budget():
    """A pathological audit still fits the budget rather than overflowing it."""
    bloated = A.audit_from_html({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER},
                                "https://acmeplumbing.ca/")
    bloated["services"] = ["Extremely long service name number %d for a very wordy business" % i
                           for i in range(12)]
    bloated["brand"] = "B" * 300
    bloated["gaps"] = [dict(g, evidence="e" * 400) for g in bloated["gaps"]] * 4

    for cap in (1200, 600, 300, 120):
        text = A.digest(bloated, max_chars=cap)
        assert len(text) <= cap, (cap, len(text))
    assert len(A.digest(bloated)) <= 1200

    assert A.digest({}) and A.digest({}).startswith("SITE:")
    assert A.digest(None) == "" and A.digest("nonsense") == ""
    print("digest budget: OK")


# ── Wrapper behaviour ──


def test_audit_site_uses_prefetched():
    """`prefetched` must mean zero network, even for an unresolvable host."""
    calls = []
    original = A._fetch

    def _boom(url, timeout):
        calls.append(url)
        raise AssertionError("audit_site fetched %s despite prefetched HTML" % url)

    A._fetch = _boom
    try:
        result = A.audit_site("https://acmeplumbing.ca",
                              prefetched={"https://acmeplumbing.ca/": WORDPRESS_PLUMBER})
    finally:
        A._fetch = original

    assert calls == []
    assert result["reachable"] is True and result["tech"]["cms"] == "wordpress"
    assert "no_live_chat" in _codes(result)
    assert result["pages"] == ["https://acmeplumbing.ca/"]

    # max_pages caps how much of the enricher's harvest is scanned.
    many = {"https://acmeplumbing.ca/page%d" % i: WORDPRESS_PLUMBER for i in range(9)}
    A._fetch = _boom
    try:
        capped = A.audit_site("https://acmeplumbing.ca", max_pages=3, prefetched=many)
    finally:
        A._fetch = original
    assert capped["page_count"] == 3, capped["page_count"]
    print("audit_site uses prefetched: OK")


class _Headers:
    """Just enough of an HTTPMessage for `_decode`."""

    def __init__(self, content_type: str) -> None:
        self.content_type = content_type

    def get(self, name, default=None):
        return self.content_type if name.lower() == "content-type" else default


def test_decoding_never_settles_for_replacement_chars():
    """Brand and title are most of what `digest()` sends the model, so a page
    decoded with errors="replace" puts U+FFFD into a live email."""
    page = ("<html><head><title>Café André</title></head>"
            "<body><h1>Café André</h1></body></html>")
    plain = _Headers("text/html")

    # No declared charset: utf-8 is tried strictly, then cp1252, and latin-1
    # bytes come through as text rather than as question marks in a diamond.
    assert A._decode(page.encode("latin-1"), plain) == page
    assert A._decode(page.encode("utf-8"), plain) == page
    # A declared charset that decodes cleanly wins.
    assert A._decode(page.encode("cp1252"), _Headers("text/html; charset=iso-8859-1")) == page
    # A stale legacy label on genuinely utf-8 bytes must not produce mojibake.
    assert A._decode(page.encode("utf-8"), _Headers("text/html; charset=iso-8859-1")) == page
    # A charset nobody has heard of falls through instead of raising.
    assert A._decode(page.encode("utf-8"), _Headers("text/html; charset=x-nope")) == page
    # Bytes that decode as nothing still come back as a string.
    assert isinstance(A._decode(b"\xff\xfe\x00\x81\x8d", plain), str)

    assert A._charset(plain, b"") == ""
    assert A._charset(plain, b'<meta charset="iso-8859-1">') == "iso-8859-1"

    result = A.audit_from_html({"https://cafeandre.test/": page}, "https://cafeandre.test/")
    assert result["brand"] == "Café André", result["brand"]
    assert "�" not in A.digest(result), A.digest(result)
    print("decoding never settles for replacement chars: OK")


def test_never_raises():
    junk = [None, "", 0, [], {}, {"x": None}, {None: "<html>"}, {"u": ""},
            {"https://x.test/": "<html><body>" + "<a href=>" * 50}]
    for pages in junk:
        result = A.audit_from_html(pages, "https://x.test/")
        assert isinstance(result, dict) and isinstance(result["gaps"], list)
        assert isinstance(A.digest(result), str)

    for url in (None, "", "not a url", 12):
        result = A.audit_site(url, timeout=0.01)
        assert isinstance(result, dict) and result["reachable"] in (True, False)
        assert isinstance(result["error"], str)

    # Every documented key survives, on both the full and the degraded path.
    full = A.audit_from_html({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER},
                             "https://acmeplumbing.ca/")
    blank = A.audit_from_html({}, "")
    for key in ("url", "final_url", "reachable", "status", "load_ms", "pages", "page_count",
                "title", "description", "h1", "brand", "tech", "services", "signals",
                "gaps", "opportunity_score", "error"):
        assert key in full and key in blank, key
    for key in ("cms", "ecommerce", "analytics", "chat", "booking", "crm", "forms", "frameworks"):
        assert key in full["tech"] and key in blank["tech"], key
    for key in ("has_ssl", "mobile_viewport", "has_schema", "has_localbusiness_schema",
                "has_blog", "blog_stale", "has_online_booking", "has_live_chat",
                "has_contact_form", "has_phone", "has_email", "has_social", "has_pricing",
                "has_testimonials", "has_gallery", "has_careers", "has_newsletter",
                "has_pdf_forms", "has_multiple_locations", "has_quote_form",
                "copyright_year", "stale_copyright", "avg_page_kb", "slow"):
        assert key in full["signals"] and key in blank["signals"], key
    print("never raises: OK")


def test_evidence_is_quotable():
    """Evidence lands inside a sentence in a live email, so keep it clean."""
    for pages, base in (
        ({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER}, "https://acmeplumbing.ca/"),
        ({"https://riverbenddental.ca/": CAREERS_PAGE}, "https://riverbenddental.ca/"),
        (_stale_blog_pages(900), "https://hillsideroofing.ca/"),
    ):
        for gap in A.audit_from_html(pages, base)["gaps"]:
            evidence = gap["evidence"]
            assert 0 < len(evidence) <= 120, gap
            assert "<" not in evidence and "http" not in evidence, gap
            assert "\n" not in evidence and not re.search(r"\{\{|\}\}", evidence), gap
    print("evidence is quotable: OK")


def _fire_every_gap() -> dict:
    """Every code in the catalogue, fired once, with the facts it quotes.

    Two passes: on a real site `no_lead_capture` and the three form gaps cannot
    both be true, and reading all of them means firing both halves. The second
    pass also drops the price document, because a business that publishes a
    price list is the one business `price_opaque` may never be said about.
    """
    tech = {"cms": "custom", "ecommerce": "shopify", "analytics": [], "chat": "",
            "booking": "", "crm": "", "forms": 3, "frameworks": []}
    signals = dict(A._blank("https://x.test/")["signals"],
                   has_contact_form=True, has_quote_form=True, has_careers=True,
                   has_pdf_forms=True, has_multiple_locations=True, location_count=4,
                   has_blog=True, blog_stale=True, stale_copyright=True,
                   copyright_year=2019, slow=True, has_whatsapp=True,
                   has_testimonials=True)
    facts = {"quote_phrase": "request a quote", "latest_date": "March 2022",
             "pdf_form": "the employment application form",
             "appointment_shaped": True, "call_cta": False, "plain_http": True,
             # The five facts only a crawl of more than one page can produce.
             "form_fields": 14, "listed_services": 12, "services_routed": False,
             "price_document": True, "price_doc_year": 2019,
             "shop_email_route": False}
    fired = A._gaps(tech, signals, facts)
    # `email_only_intake` needs the second pass too: it is what happens to the
    # enquiries a site with no form still receives, so it cannot be true while
    # the form gaps are.
    fired += A._gaps(dict(tech, forms=0, ecommerce=""),
                     dict(signals, has_contact_form=False, has_quote_form=False,
                          has_email=True),
                     dict(facts, price_document=False, price_doc_year=0))
    return {gap["code"]: gap for gap in fired}


def test_evidence_is_written_for_the_owner():
    """The parenthetical is read by the person who owns the site, inside a
    sentence offering to help. It says what *they* would find if they looked: a
    page to open, a phone to try it on, a line in the footer. What the crawler
    did is nobody's business but ours, and "across 6 page(s) checked" tells a
    small-business owner he has been surveyed by a robot."""
    fired = _fire_every_gap()
    assert set(fired) == set(A.GAP_CATALOGUE), set(A.GAP_CATALOGUE) - set(fired)

    crawler = ("(s)", "checked", "crawl", "fetch", "scan", "in the source", "markup",
               "schema.org", "viewport", "widget", "storefront", "http", "tag ", " tag")
    for code, gap in fired.items():
        evidence = gap["evidence"]
        # It has to reach the reader whole: `build_context` cuts at 90 characters.
        assert 20 < len(evidence) <= 90, (code, len(evidence), evidence)
        assert T._clean_snippet(evidence, 90) == evidence, (code, evidence)
        assert not evidence.endswith((".", "!", "?")), (code, evidence)
        assert "  " not in evidence, (code, evidence)
        for phrase in crawler:
            assert phrase not in evidence.lower(), (code, phrase, evidence)

        # And it has to say something the title did not, or the brackets are noise.
        def _words(text):
            return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2}

        fresh = _words(evidence) - _words(A.GAP_CATALOGUE[code]["title"])
        assert len(fresh) >= 3, (code, sorted(fresh), evidence)
    print("evidence is written for the owner: OK")


# Crawler state, in the shapes it reaches a sentence in. A four-digit year is the
# one number allowed through: the footer year and the month on a stale post are
# things the reader can walk over and check.
_MACHINERY: tuple[tuple[str, re.Pattern], ...] = (
    ("a url path", re.compile(r"(?<![A-Za-z0-9])/[A-Za-z0-9_.\-]*")),
    ("a file name", re.compile(r"(?i)\b[a-z0-9_\-]+\.(?:pdf|html?|php|aspx?|js|css|xml|json)\b")),
    ("a page count", re.compile(r"(?i)\b\d+\s+(?:\w+\s+){0,2}pages?\b")),
    ("a tally", re.compile(r"(?<!\d)(?!(?:19|20)\d{2}(?!\d))\d+")),
    ("an html tag or attribute",
     re.compile(r"(?i)</?[a-z]|\b(?:href|src|alt|rel|itemtype|itemprop|datetime|viewport|"
                r"iframe|noscript|textarea|ld\+json|og:[a-z]+|data-[a-z\-]+)\b")),
    ("a selector",
     re.compile(r"(?<![A-Za-z0-9])[.#][A-Za-z_\-][\w\-]*|\[[a-z\-]+[=\]]|:{1,2}[a-z\-]+\(")),
)


def test_evidence_never_hands_back_crawler_state():
    """Read the bracket as the person who owns the site.

    Round three rewrote all eighteen of these for the owner's eyes, and one of
    them still went out saying "the form on / is the only way to ask for a time"
    — because it was assembled from `_Page.path` rather than written. A form on
    the home page is the *default* for a brochure site, so "/" was the common
    case, in the one sentence the email stakes its credibility on.

    A sentence built by interpolation is always one rename away from doing that
    again, so this reads every gap back out of a finished body and refuses the
    whole class: paths, file names, tallies, tag names, selectors.
    """
    lead = {"name": "Coastal Fabrication Ltd", "city": "Windsor",
            "email": "mike.reid@coastalfab.ca", "category": "Metal fabricator",
            "domain": "coastalfab.ca"}
    profile = {"company": "Auto Army", "sender_name": "Umar Farooq",
               "sender_title": "Automation lead", "website": "https://autoarmy.io",
               "calendar_link": "https://cal.com/autoarmy/15min", "proof_points": [],
               "services": []}
    template = T.get_template("gap_direct")

    def _read(code, gap):
        ctx = T.build_context(lead, {"gaps": [gap]}, {}, profile, {})
        if ctx["gap_1_evidence"]:
            body = T.render(template, ctx)[1]
            line = next(ln for ln in body.splitlines()
                        if ln.startswith("One thing stands out"))
            assert "(" in line and line.endswith(")."), (code, line)
            seen = line.split("(", 1)[1][:-2]
        else:
            # `slow_site` and `no_mobile` have no offer behind them, so they
            # never become the headline and this bracket is never rendered for
            # them. The sentence still exists — in the operator's table and in
            # the model's brief — and the rule it has to keep is the same one.
            assert not gap["services"], (code, gap)
            seen = gap["evidence"]
        # It arrived whole, which is what makes the scan mean anything: evidence
        # cut at the 90-character cap could drop the machinery off the end.
        assert seen == gap["evidence"], (code, seen, gap["evidence"])
        for name, pattern in _MACHINERY:
            found = pattern.search(seen)
            assert not found, (code, name, found.group(0), seen)

    fired = _fire_every_gap()
    assert set(fired) == set(A.GAP_CATALOGUE), set(A.GAP_CATALOGUE) - set(fired)
    for code, gap in fired.items():
        _read(code, gap)

    # Again with the values a crawl really produces. The dict above is handed its
    # own `pdf_form`, so on its own it can never catch a path arriving where a
    # sentence was meant to be — which is exactly what shipped.
    covered = set()
    for pages, base in (
        ({"https://lakeshorephysio.ca/": HOME_PAGE_FORM}, "https://lakeshorephysio.ca/"),
        ({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER}, "https://acmeplumbing.ca/"),
        ({"https://northsidesupply.ca/": SHOPIFY_STORE}, "https://northsidesupply.ca/"),
        ({"https://riverbenddental.ca/": CAREERS_PAGE}, "https://riverbenddental.ca/"),
        (_stale_blog_pages(900), "https://hillsideroofing.ca/"),
    ):
        for gap in A.audit_from_html(pages, base)["gaps"]:
            covered.add(gap["code"])
            _read(gap["code"], gap)

    # The gaps that used to interpolate have to be inside that sweep, or it
    # proves nothing about the ones that mattered.
    assert {"no_online_booking", "no_crm_signals", "ecommerce_manual", "pdf_forms",
            "stale_blog", "stale_site"} <= covered, sorted(covered)

    # A download the site never gave a name to degrades to a noun. "Click here"
    # pointing at intake_2019_v2.pdf is the common case, and the file name the
    # server happens to store it under is not an answer.
    unnamed = A.audit_from_html({"https://lakeshorephysio.ca/": HOME_PAGE_FORM},
                                "https://lakeshorephysio.ca/")
    paperwork = next(g for g in unnamed["gaps"] if g["code"] == "pdf_forms")
    assert paperwork["evidence"].startswith("the paperwork is a PDF"), paperwork["evidence"]
    print("evidence never hands back crawler state: OK")


def test_every_gap_reads_in_every_template():
    """All eighteen, through all five templates. A title is a noun phrase of
    unknown number ("no live chat", "quotes handled by hand"), so anything that
    agrees with it is wrong for half the catalogue."""
    fired = _fire_every_gap()
    profile = {"company": "Auto Army", "sender_name": "Umar Farooq",
               "sender_title": "Automation lead", "website": "https://autoarmy.io",
               "calendar_link": "https://cal.com/autoarmy/15min", "proof_points": [],
               "services": []}
    lead = {"name": "Coastal Fabrication & Welding Incorporated", "city": "Windsor",
            "email": "mike.reid@coastalfab.ca", "category": "Metal fabricator",
            "domain": "coastalfab.ca"}
    agrees = re.compile(r"(?i)^\s*(?:is|was|are|were|has|have|does|do|sits|means|costs|goes)\b")

    for code, gap in fired.items():
        ctx = T.build_context(lead, {"gaps": [gap]}, {}, profile, {})
        for tpl in T.TEMPLATES:
            subject, text, html = T.render(tpl, ctx)
            for blob in (subject, text, html):
                assert "{{" not in blob and "(s)" not in blob, (code, tpl.id, blob)
            assert subject and len(subject) <= T.SUBJECT_MAX, (code, tpl.id, subject)
            assert re.search(r"[A-Za-z0-9?]$", subject), (code, tpl.id, subject)

            for tail in text.split(gap["title"])[1:]:
                assert not agrees.match(tail), (code, tpl.id, tail[:60])
            if not gap["services"]:
                # No offer behind it, so it never leads: the sentence that would
                # have named it is dropped whole rather than left standing in
                # front of an offer that answers a different question.
                assert gap["title"] not in text, (code, tpl.id, text)
                assert gap["evidence"] not in text, (code, tpl.id, text)
                assert "\n\n\n" not in text and "()" not in text, (code, tpl.id, text)
                continue
            if "{{gap_1}}" in tpl.body:
                # The title arrived whole, inside a sentence that finished.
                assert gap["title"] in text, (code, tpl.id, text)
                sentence = text.split(gap["title"])[1].split("\n")[0]
                assert sentence.strip().endswith("."), (code, tpl.id, sentence)
            if "{{gap_1_evidence}}" in tpl.body:
                assert "(%s)" % gap["evidence"] in text, (code, tpl.id, text)
    print("every gap reads in every template: OK")


# ── Accuracy of what the audit claims ──

# Each fixture below is one sentence the audit used to get wrong in front of the
# person who owns the site. They are grouped here because the failure is always
# the same shape: a detector that only speaks North American English, reading a
# page that does not, and reporting the absence of something the page shows.

_FOOTER_PHONES = {
    "france": "04 78 55 44 33",
    "germany": "0941 5550120",
    "spain": "920 55 10 40",
    "uk": "0161 555 0166",
    "new zealand": "06 555 0122",
    "north america": "(905) 555-1234",
    "international": "+353 91 555 021",
}

# Runs of digits that share a shape with a phone number and are not one. A
# spurious `has_phone` only holds a gap back, but these are what the loose
# patterns would swallow if they were not bounded, and a page of dated posts
# would then report a phone the site does not have.
_NOT_PHONES = ("2026-04-02", "2026 2025 2024", "1.850,00", "05001", "L8L 2W7",
               "Suite 12", "1961", "10-class pack", "$59.00", "12,50")


def test_a_printed_phone_number_is_seen_in_every_market():
    """`contact_form_only` says "no chat, no booking, no number to tap" out loud.

    The claim is about a number being on the page, so the only question that
    matters is whether one is printed — and the pattern behind it read 3-3-4 and
    nothing else, so a French, German, Spanish, British or New Zealand footer
    came back as a site with no phone. That put a checkably false sentence at the
    top of a cold email, and it put "no phone" in the model's brief besides.
    """
    for market, number in _FOOTER_PHONES.items():
        assert A._phone_present("Call us on " + number), (market, number)
    for text in _NOT_PHONES:
        assert not A._phone_present("published " + text + " here"), text

    page = ("<html><head><meta name='viewport' content='width=device-width'></head>"
            "<body><h2>Kontakt</h2>"
            "<form method='post'><input type='email' name='email'>"
            "<textarea name='nachricht'></textarea></form>"
            "<footer>Telefon 0941 5550120</footer></body></html>")
    result = A.audit_from_html({"https://beispiel.de/": page}, "https://beispiel.de/")
    assert result["signals"]["has_phone"] is True, result["signals"]
    assert "contact_form_only" not in _codes(result), _codes(result)
    print("a printed phone number is seen in every market: OK")


def test_a_call_to_action_is_not_what_makes_a_number_real():
    """The gate used to be the marketing phrase, not the number.

    A footer that prints a number without saying "call now" is still a number to
    tap, and asking whether the business *promotes* the phone answered a
    different question than the one the sentence makes a claim about.
    """
    quiet = ("<html><body><form><textarea name='message'></textarea></form>"
             "<footer>216-555-0940</footer></body></html>")
    loud = quiet.replace("216-555-0940", "Call now: 216-555-0940")
    for page in (quiet, loud):
        result = A.audit_from_html({"https://x.test/": page}, "https://x.test/")
        assert "contact_form_only" not in _codes(result), page
    print("a call to action is not what makes a number real: OK")


def test_prices_are_read_with_the_symbol_on_either_side():
    """"not a rate, a range or a starting figure on any page", to a price list.

    Most of Europe writes `89,00 €` and the pattern demanded `€89.00`, so a page
    whose whole purpose is publishing prices fired `price_opaque`.
    """
    for written in ("89,00 €", "1.850,00 €", "8,50 €", "£42", "$59.00", "€1,200.00"):
        assert A._MONEY_RE.search(written), written

    page = ("<html><body><h1>Zahnarztpraxis</h1><h2>Preise</h2>"
            "<p>Zahnreinigung ab 89,00 € · Bleaching ab 349,00 € · "
            "Implantat ab 1.850,00 €</p></body></html>")
    result = A.audit_from_html({"https://beispiel.de/": page}, "https://beispiel.de/")
    assert result["signals"]["has_pricing"] is True, result["signals"]
    assert "price_opaque" not in _codes(result), _codes(result)
    print("prices are read with the symbol on either side: OK")


def test_booking_is_a_system_and_not_a_sentence():
    """"Request an appointment" is the heading over the form on sites with none.

    Reading it as proof of a booking system deleted `no_online_booking` from
    exactly the leads it was written for, and left `has_online_booking: True` in
    the audit of a clinic that answers the phone to make every appointment.
    """
    prose = ("<html><head><meta name='viewport' content='width=device-width'></head>"
             "<body><h1>Riverbend Dental</h1>"
             "<h2>Our Services</h2><ul><li>Dental cleaning and checkups</li>"
             "<li>Root canal treatment</li><li>Teeth whitening</li></ul>"
             "<h2>Request an appointment</h2>"
             "<form method='post'><input type='email' name='email'>"
             "<textarea name='message'></textarea></form></body></html>")
    result = A.audit_from_html({"https://riverbend.ca/": prose}, "https://riverbend.ca/")
    assert result["signals"]["has_online_booking"] is False, result["signals"]
    assert "no_online_booking" in _codes(result), _codes(result)

    # A control the visitor clicks is still evidence, in any of the languages
    # this tool meets. A verb — `buchen` — and never the bare noun `Termin`,
    # which titles the phone-only appointments page as often as a calendar.
    booked = prose.replace("<h2>Request an appointment</h2>",
                           "<a href='/termin-buchen/'>Termin online buchen</a>")
    result = A.audit_from_html({"https://riverbend.ca/": booked}, "https://riverbend.ca/")
    assert result["signals"]["has_online_booking"] is True, result["signals"]
    assert "no_online_booking" not in _codes(result), _codes(result)
    print("booking is a system and not a sentence: OK")


def test_a_site_is_hiring_in_more_than_one_language():
    """A German vacancies page was read as a business that is not hiring."""
    for markup, market in (
            ("<a href='/stellenangebote/'>Stellenangebote</a>"
             "<p>Wir stellen ein: Elektroniker (m/w/d)</p>", "de"),
            ("<a href='/recrutement/'>Recrutement</a>"
             "<p>Nous recrutons un collaborateur</p>", "fr"),
            ("<a href='/empleo'>Empleo</a><p>Trabaja con nosotros</p>", "es"),
            ("<a href='/careers/'>Careers</a><p>We are hiring</p>", "en")):
        page = "<html><body><h1>Firma</h1>" + markup + "</body></html>"
        result = A.audit_from_html({"https://x.test/": page}, "https://x.test/")
        assert result["signals"]["has_careers"] is True, market
        assert "careers_manual" in _codes(result), market
    print("a site is hiring in more than one language: OK")


def test_a_date_in_the_markup_is_not_a_blog():
    """`blog 2019` reached the model's brief for a site with no blog at all.

    `datePublished` in a home page's JSON-LD is the page's own date, and every
    CMS emits one. Read as a publishing date it described a machine shop with no
    news section anywhere as having one, four years stale — and the brief is the
    only thing the model sees, so it had no way to know better.
    """
    page = ('<html><head><meta name="viewport" content="width=device-width">'
            '<script type="application/ld+json">{"@context":"https://schema.org",'
            '"@type":"WebPage","datePublished":"2019-03-04"}</script></head>'
            '<body><h1>Ferro Machine and Tool</h1>'
            '<p>Precision machining since 1961.</p><p>216-555-0940</p></body></html>')
    result = A.audit_from_html({"https://ferro.test/": page}, "https://ferro.test/")
    assert result["signals"]["has_blog"] is False, result["signals"]
    assert result["signals"]["blog_year"] == 0, result["signals"]
    assert "blog 2019" not in A.digest(result), A.digest(result)
    assert "no blog" in A.digest(result), A.digest(result)

    # With a blog to date, the same markup is read exactly as before.
    with_blog = page.replace("<h1>", '<a href="/blog/">Blog</a><h1>')
    result = A.audit_from_html({"https://ferro.test/": with_blog}, "https://ferro.test/")
    assert result["signals"]["has_blog"] is True, result["signals"]
    assert result["signals"]["blog_year"] == 2019, result["signals"]
    print("a date in the markup is not a blog: OK")


def test_the_score_ranks_by_how_much_there_is_to_fix():
    """The column the operator sorts by has to be monotone in the findings.

    The per-gap taper that replaced straight addition stopped the pinning and
    broke the ordering doing it: it made the score depend on where a gap landed
    in the list rather than on what was found, so eighteen small findings scored
    below three large ones and the fifth gap onward moved the number by less
    than a point. Measured over a thirty-site corpus the tapered score ranked
    those leads worse than counting the gaps and ignoring severity entirely.
    """
    def score(severities):
        gaps = [{"severity": s} for s in severities]
        return A._score(gaps, True, True)

    # Order is a copywriting decision — which finding the email leads with — and
    # it has no business moving an opportunity score.
    assert score([3, 2, 1]) == score([1, 2, 3]) == score([2, 1, 3])

    # More to fix always reads higher, however the severities are distributed.
    ladder = [score([1] * n) for n in range(0, 25)]
    assert ladder == sorted(ladder), ladder
    assert score([1] * 18) > score([3, 3, 3]), (score([1] * 18), score([3, 3, 3]))
    for small, large in (([3], [3, 1]), ([3, 3], [3, 3, 2]), ([2] * 6, [2] * 7)):
        assert score(large) > score(small), (small, large)

    # The cap is enforced by the shape of the curve, so no list of findings can
    # reach it and pin two different leads at the same number.
    assert score([3] * 40) < 100, score([3] * 40)

    # And the floor the rest of the module relies on is unchanged: a reachable
    # site with an address and nothing to fix scores something, but not much.
    assert A._score([], True, True) == 15
    assert 10 <= A._score([], True, False) < 20
    print("the score ranks by how much there is to fix: OK")


def test_what_a_business_sells_is_read_from_the_list_it_writes_it_in():
    """A small business writes "Services" once and lists them underneath.

    Nothing read that list, so the `WHAT:` line — the most concrete thing in the
    brief the model is given, and the only part written in the prospect's own
    words — was simply absent from most audits.
    """
    page = ("<html><body><h1>Elektro Baumgartner GmbH</h1>"
            "<h2>Unsere Leistungen</h2>"
            "<ul><li>Elektroinstallation Neubau</li><li>Photovoltaikanlagen</li>"
            "<li>Wallbox Installation</li></ul>"
            "<h2>Kontakt</h2><ul><li>Telefon</li><li>Anfahrt</li></ul>"
            "</body></html>")
    result = A.audit_from_html({"https://beispiel.de/": page}, "https://beispiel.de/")
    services = result["services"]
    assert "Elektroinstallation Neubau" in services, services
    assert "Photovoltaikanlagen" in services, services
    # The list belongs to the heading that introduced it. A second list under an
    # unrelated heading is not a service, and scooping up every <li> on the page
    # would fill the brief with nav and footer.
    assert "Anfahrt" not in services, services
    assert "WHAT: " in A.digest(result), A.digest(result)
    print("what a business sells is read from the list it writes it in: OK")


# ── One page shape, for the tests below and the corpus after them ──

# A real page carries a viewport, a phone number and an address whatever else it
# is missing, so every page built here does too: it keeps `no_mobile`,
# `contact_form_only` and `multi_location` out of the way of the rule under test.
_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
%s</head><body>
%s
<footer><p>&copy; %d %s. 12 Mill St, Guelph, ON N1H 2A9. Call (905) 555-0134</p></footer>
</body></html>"""

# A form a stranger writes to, the site's own search box, and the same search
# box marked up the other way — by input type rather than by role.
_LEAD_FORM = ('<form action="/contact" method="post">'
              '<input type="text" name="name" placeholder="Name">'
              '<input type="email" name="email" placeholder="Email">'
              '<textarea name="message"></textarea>'
              '<button type="submit">Send</button></form>')
_SEARCH_BOX = ('<form role="search" action="/search">'
               '<input type="text" name="q" placeholder="Search the site">'
               '<button type="submit">Search</button></form>')
_SEARCH_INPUT = ('<form action="/results">'
                 '<input type="search" name="s" placeholder="Find a plant">'
                 '<button type="submit">Go</button></form>')


def _site(name: str, body: str, head: str = "") -> str:
    return _PAGE % (name, head, body, TODAY.year, name)


def _built(body: str, head: str = "") -> dict:
    return A.audit_from_html({"https://x.test/": _site("A business", body, head)},
                             "https://x.test/")


def test_a_list_of_services_is_not_a_thing_to_book():
    """The headline gap fired on any business that sells anything.

    `no_online_booking` is the highest severity in the catalogue and usually the
    sentence the email opens with, and the rule behind it was "has a contact
    form and sells something" — so a fabricator, a wholesaler and a print shop
    were all told that asking them for a time means filling in a form and
    waiting, about work no one books a time for. The worst of them was a barber
    shop whose page reads "walk-ins only, we do not take appointments": the word
    the rule matched on was inside the sentence denying it.
    """
    nothing_to_book = ("<h2>Our Services</h2><ul><li>CNC machining</li>"
                       "<li>Powder coating</li><li>Sheet metal fabrication</li></ul>")
    walk_in = ("<p>Walk-ins only. We do not take appointments.</p>"
               "<h2>Services</h2><ul><li>Haircut</li><li>Beard trim</li></ul>")
    # No appointment word anywhere on the page: what says this business books
    # times is the work it lists, and losing that arm would cost the rule the
    # leads it exists for.
    books_times = ("<h2>Treatments</h2><ul><li>Sports injury treatment</li>"
                   "<li>Manual therapy</li></ul>")
    for body, expected in ((nothing_to_book, False), (walk_in, False), (books_times, True)):
        codes = _codes(_built(body + _LEAD_FORM))
        assert ("no_online_booking" in codes) is expected, (body, codes)

    # A site that calls itself a dentist in its own JSON-LD books times too, and
    # says so in the one place on the page that cannot be a turn of phrase.
    declared = _built(_LEAD_FORM, head='<script type="application/ld+json">'
                                       '{"@type":"Dentist","name":"A business"}</script>')
    assert "no_online_booking" in _codes(declared), _codes(declared)
    print("a list of services is not a thing to book: OK")


def test_a_search_box_is_not_a_lead_form():
    """One filter, or two rules read the same box two ways.

    `_is_contact_form` rejected `role="search"` while the `tech["forms"]` tally
    counted raw <form> tags, so a brochure site whose only form is the site
    search was told nothing was hooked up behind its contact form — and the
    finding that was true of it, that nothing on the site asks a visitor for a
    name, was suppressed by that same box. One wrong claim, one real one hidden,
    both out of a single disagreement.
    """
    only_search = _built(_SEARCH_BOX)
    assert only_search["tech"]["forms"] == 0, only_search["tech"]
    assert only_search["signals"]["has_contact_form"] is False, only_search["signals"]
    assert "no_crm_signals" not in _codes(only_search), _codes(only_search)
    assert "no_lead_capture" in _codes(only_search), _codes(only_search)

    both = _built(_SEARCH_BOX + _LEAD_FORM)
    assert both["tech"]["forms"] == 1, both["tech"]
    assert "no_crm_signals" in _codes(both), _codes(both)
    assert "no_lead_capture" not in _codes(both), _codes(both)

    # The other half of the same rule: a newsletter a visitor can join is a lead
    # route, and a newsletter mentioned in a paragraph is a promise.
    promised = _built("<p>Our newsletter goes out every spring, ask us to add you.</p>")
    assert "no_lead_capture" in _codes(promised), _codes(promised)
    joinable = _built('<h2>Newsletter</h2><form action="/subscribe">'
                      '<input type="email" name="EMAIL"><button>Subscribe</button></form>')
    assert "no_lead_capture" not in _codes(joinable), _codes(joinable)

    # And the rejection itself was a substring standing in for a structure:
    # "search" is inside "research", so a form posting to /research-request was
    # discarded as the site's search box — and a site whose only form has been
    # discarded is told nothing on it asks a visitor for a name.
    enquiry = _built('<form action="/research-request" method="post">'
                     '<input type="text" name="name"><input type="email" name="email">'
                     '<textarea name="message"></textarea>'
                     "<button type=\"submit\">Send</button></form>")
    assert enquiry["tech"]["forms"] == 1, enquiry["tech"]
    assert "no_lead_capture" not in _codes(enquiry), _codes(enquiry)
    assert "no_crm_signals" in _codes(enquiry), _codes(enquiry)
    # The box it was meant to reject is still rejected, however it is marked up.
    for box in (_SEARCH_BOX, _SEARCH_INPUT,
                '<form class="searchform" action="/results">'
                '<input type="text" name="s"><button>Go</button></form>'):
        assert A._is_contact_form(box) is False, box
    print("a search box is not a lead form: OK")


def test_careers_has_to_head_a_job_listing():
    """"Careers" is a section heading and an email label both.

    The rule matched the bare words anywhere in the visible text, so a contact
    page reading "Careers: jobs@clearviewhvac.ca" and a financing page with
    "Apply now" over a credit application were both told they must be sifting
    CVs by hand. The path arm had the same shape: "/careers" sits inside
    "/blog/careers-in-the-trades/", which is an article about apprentice wages.
    """
    not_hiring = (
        '<p>Careers: <a href="mailto:jobs@clearviewhvac.ca">jobs@clearviewhvac.ca</a></p>',
        '<h2>Financing</h2><p>Good credit, bad credit. Apply now and drive today.</p>'
        '<p><a href="/finance-application/">Apply Now</a></p>',
        '<h2><a href="/blog/careers-in-the-trades/">Careers in the trades: what an '
        'apprenticeship really pays</a></h2>',
    )
    hiring = (
        '<nav><a href="/careers/">Careers</a></nav>',
        '<h2>Careers</h2><ul><li>Journeyman Electrician, full time</li></ul>',
        '<a href="/stellenangebote/">Stellenangebote</a><p>Wir stellen ein.</p>',
    )
    for body in not_hiring:
        codes = _codes(_built(body + _LEAD_FORM))
        assert "careers_manual" not in codes, (body, codes)
    for body in hiring:
        codes = _codes(_built(body + _LEAD_FORM))
        assert "careers_manual" in codes, (body, codes)
    print("careers has to head a job listing: OK")


def test_a_marker_stops_where_the_word_stops():
    """Three more of the same shape, found while the three above were being fixed.

    "form" is inside "uniform", and the sentence it produced went out as "the
    uniform catalogue is a PDF to print, fill in and send back". "x.com" is
    inside "simplex.com", so a link to a supplier counted as a social profile
    and buried a finding the business really did have. "formidable" is a plugin
    directory and an English adjective, and the embed list was matched against
    the copy rather than against the URLs that load an embed.
    """
    catalogue = _built('<p>Browse the <a href="/downloads/uniform-catalogue-2026.pdf">'
                       "uniform catalogue</a>.</p>" + _LEAD_FORM)
    assert "pdf_forms" not in _codes(catalogue), _codes(catalogue)
    paperwork = _built('<p>Please <a href="/forms/new-patient-form.pdf">download the new '
                       "patient form</a>.</p>" + _LEAD_FORM)
    assert "pdf_forms" in _codes(paperwork), _codes(paperwork)
    named = next(g for g in paperwork["gaps"] if g["code"] == "pdf_forms")
    assert named["evidence"].startswith("the new patient form is a PDF"), named["evidence"]

    supplier = _built('<p>We fit <a href="https://www.simplex.com/flooring">Simplex</a>'
                      " products.</p>" + _LEAD_FORM)
    assert supplier["signals"]["has_social"] is False, supplier["signals"]
    assert "no_social_presence" in _codes(supplier), _codes(supplier)
    profile = _built('<a href="https://www.facebook.com/abusiness/">Facebook</a>' + _LEAD_FORM)
    assert profile["signals"]["has_social"] is True, profile["signals"]
    assert "no_social_presence" not in _codes(profile), _codes(profile)

    prose = _built("<p>Four decades and a formidable record in boundary disputes.</p>")
    assert prose["signals"]["has_contact_form"] is False, prose["signals"]
    assert "no_lead_capture" in _codes(prose), _codes(prose)
    embedded = _built('<iframe src="https://form.jotform.com/2411234567890"></iframe>')
    assert embedded["signals"]["has_contact_form"] is True, embedded["signals"]
    assert "no_lead_capture" not in _codes(embedded), _codes(embedded)
    print("a marker stops where the word stops: OK")


# ── The labelled corpus ──

# Twenty-nine pages, each labelled with the gaps a person who read the page
# would say are true of it. Fixtures alone cannot hold these rules honest: a
# rule that matches a substring where it means a structural fact still passes
# every example written to prove it works, and only shows itself as a rate over
# pages that merely *read* like the thing. So the corpus carries both — the
# shapes each rule exists to catch, and the shapes that resemble them: a
# services list with nothing bookable on it, a site-search box, "Careers:"
# beside an email address, "Apply Now" over a credit application, a uniform
# catalogue, a supplier called Simplex.
#
# Every label is a fact about the page rather than a restatement of a rule —
# whether the business takes appointments, whether anything asks a visitor for
# a name, whether the site is advertising a job. Every page is built from the
# shape above, so `no_mobile`, `contact_form_only` and `multi_location` are
# silent throughout and are measured by the fixtures earlier in the file.

# What every page in the corpus has in common: no chat, no analytics, no
# markup, no social profile and no price on the page.
_BASE_GAPS = frozenset({"no_live_chat", "no_analytics", "no_schema",
                        "no_social_presence", "price_opaque"})

_FRESH_POST = TODAY - datetime.timedelta(days=30)


def _labelled(name: str, body: str, gaps, head: str = "") -> tuple:
    return name, _site(name, body, head), frozenset(gaps)


CORPUS: tuple[tuple[str, str, frozenset], ...] = (
    # ── A services list is not a thing to book ──
    _labelled(
        "Coastal Fabrication Ltd", """
<nav><a href="/">Home</a> <a href="/services/">Services</a> <a href="/contact/">Contact</a></nav>
<h1>Coastal Fabrication Ltd</h1>
<p>Structural steel and custom metalwork for contractors across the region.</p>
<h2>Our Services</h2>
<ul><li>CNC machining</li><li>Structural steel welding</li><li>Powder coating</li>
<li>Sheet metal fabrication</li></ul>
<h2>Request a quote</h2>
<p>Send us your drawings and we will price the job.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "quote_by_form"}),
    _labelled(
        "Northline Janitorial Supply", """
<h1>Northline Janitorial Supply</h1>
<p>Trade supplier to schools, offices and property managers.</p>
<h2>What we do</h2>
<ul><li>Next day delivery across the province</li><li>Bulk paper and chemical supply</li>
<li>Account management for facilities teams</li><li>Dispenser stocking programmes</li></ul>
<p>Open a trade account by sending the form below.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Riverside Print Co", """
<h1>Riverside Print Co</h1>
<h2>Services</h2>
<ul><li>Business card printing</li><li>Large format banners</li><li>Vehicle wraps</li>
<li>Brochure design and layout</li></ul>
<h2>Get a quote</h2>
<p>Tell us what you need printed and we will come back with a price.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "quote_by_form"}),
    # The page says outright that it does not book times. Reading the word
    # "appointments" out of that sentence as proof it does is the worst of the
    # false positives: the email opens by contradicting the page it cites.
    _labelled(
        "Dundas Street Barbers", """
<h1>Dundas Street Barbers</h1>
<p>Walk-ins only. We do not take appointments &mdash; first come, first served,
six days a week.</p>
<h2>Services</h2><ul><li>Haircut</li><li>Beard trim</li><li>Hot towel shave</li></ul>
<h2>Questions?</h2>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),

    # ── And the businesses the rule exists for ──
    _labelled(
        "Riverbend Dental", """
<h1>Riverbend Dental</h1>
<h2>Request an appointment</h2>
<p>Fill in the form and the front desk will call you back to confirm a time.</p>
""" + _LEAD_FORM + """
<h2>Our Services</h2><ul><li>Dental cleaning and check-ups</li>
<li>Root canal treatment</li><li>Teeth whitening</li></ul>
""",
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),
    # No appointment word anywhere: what says this business books times is the
    # work it lists. Lose this one and the rule stops paying for itself.
    _labelled(
        "Lakeshore Physiotherapy", """
<h1>Lakeshore Physiotherapy</h1>
<h2>Treatments</h2><ul><li>Sports injury treatment</li><li>Manual therapy</li>
<li>Post-surgical rehabilitation</li></ul>
<p>Send us a message and we will find you a slot this week.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Trattoria Bella", """
<h1>Trattoria Bella</h1>
<p>Reservations by telephone only &mdash; call the restaurant and we will hold a table.</p>
<h2>Menu</h2><p>Antipasti from $12.00, pasta $22.00, secondi $31.00.</p>
<h2>Private events</h2>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Maple Ridge Veterinary", """
<h1>Maple Ridge Veterinary</h1>
<h2>Our Services</h2><ul><li>Vaccinations and wellness exams</li>
<li>Dental cleaning</li><li>Pet grooming</li></ul>
<p>New clients are welcome. Send us a note and we will be in touch.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Bloom Hair Studio", """
<h1>Bloom Hair Studio</h1>
<p><a href="https://my.setmore.com/bloomhair">Book an appointment</a></p>
<h2>Services</h2><ul><li>Cut and blow dry</li><li>Balayage</li>
<li>Keratin treatment</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Clearview Heating and Cooling", """
<nav><a href="/">Home</a> <a href="/book-online/">Book online</a></nav>
<h1>Clearview Heating and Cooling</h1>
<h2>Services</h2><ul><li>Furnace repair</li><li>Air conditioning installation</li>
<li>Annual maintenance plans</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),

    # ── A search box is not lead capture ──
    _labelled(
        "Grand River Hardware", """
<h1>Grand River Hardware</h1>
""" + _SEARCH_BOX + """
<p>Four aisles of trade supplies, open seven days. Come and see us at the counter.</p>
<h2>Departments</h2><ul><li>Plumbing supplies</li><li>Paint and stain</li>
<li>Garden tools</li></ul>
""",
        _BASE_GAPS | {"no_lead_capture"}),
    _labelled(
        "Elmwood Garden Centre", """
<h1>Elmwood Garden Centre</h1>
""" + _SEARCH_INPUT + """
<p>Ten acres of trees, shrubs and perennials just off the highway.</p>
""",
        _BASE_GAPS | {"no_lead_capture"}),
    _labelled(
        "Ferro Machine and Tool", """
<h1>Ferro Machine and Tool</h1>
""" + _SEARCH_BOX + """
<h2>What we do</h2><ul><li>Precision turning</li><li>Surface grinding</li>
<li>Short-run production</li></ul>
<h2>Contact</h2>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Hilltop Masonry", """
<h1>Hilltop Masonry</h1>
<p>Brick, block and stone work. Call the yard and we will come and look at the job.</p>
<h2>Our Work</h2><ul><li>Chimney repointing</li><li>Stone veneer</li>
<li>Retaining walls</li></ul>
""",
        _BASE_GAPS | {"no_lead_capture"}),
    # A newsletter nobody can sign up to is a promise in a paragraph, not a
    # route in, and reading it as one hides the finding on a site with no form
    # at all.
    _labelled(
        "Rideau Upholstery", """
<h1>Rideau Upholstery</h1>
<p>Repairs and re-covering for antique and modern furniture. Drop in or call the workshop.</p>
<p>Our newsletter goes out every spring &mdash; ask us to add you to the mailing
list when you call.</p>
""",
        _BASE_GAPS | {"no_lead_capture"}),
    # "formidable" is a plugin directory and an English adjective, and the
    # embed list was matched against the whole page rather than against the
    # URLs that load an embed.
    _labelled(
        "Ashworth Chartered Surveyors", """
<h1>Ashworth Chartered Surveyors</h1>
<p>Four decades and a formidable record in party wall and boundary disputes.</p>
<p>Telephone the practice and ask for Mr Ashworth.</p>
""",
        _BASE_GAPS | {"no_lead_capture"}),
    # And the case the embed list exists for: a form that renders no <form> of
    # its own, loaded from the URL that proves it is there.
    _labelled(
        "Halton Fencing Supplies", """
<h1>Halton Fencing Supplies</h1>
<p>Panels, posts and gravel boards, collected from the yard or delivered on a flatbed.</p>
<iframe src="https://form.jotform.com/2411234567890" title="Enquiry"></iframe>
""",
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Cedar Lane Bakery", """
<h1>Cedar Lane Bakery</h1>
<p>Sourdough baked every morning. Call ahead for large orders.</p>
<h2>Join our mailing list</h2>
<form action="https://cedarlane.us1.list-manage.com/subscribe/post">
<input type="email" name="EMAIL" placeholder="Email"><button>Subscribe</button></form>
""",
        _BASE_GAPS),
    # Relabelled. This page was labelled `no_live_chat` and `no_analytics` when
    # HubSpot was held in the CRM table only, and both were wrong about it:
    # js.hs-scripts.com is the loader for HubSpot Conversations and for HubSpot's
    # page-view tracking as well as for the CRM, so the site this markup
    # describes has a chat box in the corner and a record of last month's
    # visits. Two false sentences out of one script the tables half knew.
    _labelled(
        "Northwind Logistics Software", """
<h1>Northwind Logistics Software</h1>
<h2>What we do</h2><ul><li>Fleet dispatch software</li><li>Route optimisation</li>
<li>Integration support</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS - {"no_live_chat", "no_analytics"},
        head='<script src="https://js.hs-scripts.com/1234567.js"></script>\n'),

    # ── "Careers" has to head a job listing ──
    _labelled(
        "Clearview HVAC", """
<h1>Clearview HVAC</h1>
<h2>Contact</h2>
<p>Service: <a href="mailto:service@clearviewhvac.ca">service@clearviewhvac.ca</a><br>
Careers: <a href="mailto:jobs@clearviewhvac.ca">jobs@clearviewhvac.ca</a></p>
<h2>Services</h2><ul><li>Furnace repair</li><li>Air conditioning installation</li>
<li>Duct cleaning</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Southgate Auto Sales", """
<h1>Southgate Auto Sales</h1>
<h2>Financing</h2>
<p>Good credit, bad credit, no credit. Apply now and drive today.</p>
<p><a href="/finance-application/">Apply Now</a> or print the
<a href="/docs/credit-application.pdf">credit application</a> and bring it in.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "pdf_forms"}),
    _labelled(
        "Clearview HVAC Blog", """
<h1>Clearview HVAC Blog</h1>
<article>
<h2><a href="/blog/careers-in-the-trades/">Careers in the trades: what an apprenticeship
really pays</a></h2>
<time datetime="%s">%s</time>
<p>Every winter somebody asks us what a first-year apprentice earns.</p>
</article>
""" % (_FRESH_POST.isoformat(), _FRESH_POST.strftime("%B %d, %Y")) + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Bridgeport Electric", """
<nav><a href="/">Home</a> <a href="/careers/">Careers</a></nav>
<h1>Bridgeport Electric</h1>
<h2>Current openings</h2>
<ul><li>Journeyman Electrician, full time</li><li>Apprentice Electrician, first year</li></ul>
<p>Send your resume to the office and we will call you in for a chat.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "careers_manual"}),
    _labelled(
        "Dominion Roofing", """
<h1>Dominion Roofing</h1>
<h2>Careers</h2>
<ul><li>Roofing labourer, full time, year round</li><li>Crew lead, five years on the tools</li></ul>
<p>Applications can be dropped off at the office any weekday.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "careers_manual"}),
    _labelled(
        "Elektro Baumgartner GmbH", """
<h1>Elektro Baumgartner GmbH</h1>
<h2>Unsere Leistungen</h2><ul><li>Elektroinstallation Neubau</li>
<li>Photovoltaikanlagen</li><li>Wallbox Installation</li></ul>
<p><a href="/stellenangebote/">Stellenangebote</a></p>
<p>Wir stellen ein: Elektroniker (m/w/d) für Photovoltaik.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "careers_manual", "no_online_booking"}),

    # ── Paperwork, and a word that only contains one ──
    _labelled(
        "Trailside Workwear", """
<h1>Trailside Workwear</h1>
<p>Browse the <a href="/downloads/uniform-catalogue-2026.pdf">uniform catalogue</a>
or ask about bulk pricing for crews.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Bayview Family Chiropractic", """
<h1>Bayview Family Chiropractic</h1>
<p>New here? Please <a href="/forms/new-patient-form.pdf">download the new patient form</a>
and bring it with you.</p>
<h2>Our Services</h2><ul><li>Chiropractic adjustment</li><li>Massage therapy</li>
<li>Custom orthotics</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "pdf_forms", "no_online_booking"}),

    # ── A supplier whose name ends in the same three characters as a social host ──
    _labelled(
        "Kettle Creek Flooring", """
<h1>Kettle Creek Flooring</h1>
<p>We fit products from <a href="https://www.simplex.com/flooring">Simplex</a>
and three other mills.</p>
<h2>Services</h2><ul><li>Hardwood installation</li><li>Carpet supply and fitting</li>
<li>Floor sanding and refinishing</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),

    # ── And a site with nothing to sell against ──
    _labelled(
        "Harbourview Dental", """
<nav><a href="https://calendly.com/harbourview/checkup">Book online</a>
<a href="https://www.facebook.com/harbourviewdental/">Facebook</a></nav>
<h1>Harbourview Dental</h1>
<p>Check-up $95.00, cleaning $140.00, whitening $350.00.</p>
""" + _LEAD_FORM,
        frozenset(),
        head='<script async src="https://www.googletagmanager.com/gtag/js?id=G-XYZ"></script>\n'
             '<script src="https://embed.tawk.to/hv/default"></script>\n'
             '<script src="https://js.hs-scripts.com/778899.js"></script>\n'
             '<script type="application/ld+json">{"@type":"Dentist",'
             '"name":"Harbourview Dental"}</script>\n'),
)


# ── A second corpus: the fact behind a threshold, and behind a vendor list ──

# Thirty-one more pages, every label written down before a line of the rules was
# touched. The corpus above catches a rule that matched a *word* where it meant a
# structure. These are the two remaining shapes of the same mistake.
#
# A threshold standing in for the fact. `price_opaque` asked for three money
# matches anywhere in the whole crawl, so a page that publishes one or two prices
# read as a page that publishes none — and the email opened by telling a business
# with its rates on the home page that it has none. The same threshold read three
# discounts as a price list.
#
# A vendor list standing in for the fact. `no_live_chat`, `no_online_booking`,
# `no_analytics`, `no_crm_signals` and `ecommerce_manual` each ask "is one of
# these seven scripts on the page" and answer "no such thing exists here" when
# the site runs the eighth. Every page in that half carries a real product the
# tables did not hold, and a person looking at the page can see the chat box.
#
# And the lookalikes, for the same reason they are up there: a discount is not a
# price, "Booking terms" is not a booking system, "let's have a chat" is not a
# chat box, and a page that says its prices are competitive has published none.

PRICE_AND_VENDOR_CORPUS: tuple[tuple[str, str, frozenset], ...] = (
    # ── One or two prices is not "no pricing anywhere" ──
    _labelled(
        "Kestrel Dog Grooming", """
<h1>Kestrel Dog Grooming</h1>
<h2>Grooming</h2>
<ul><li>Full groom &mdash; $65</li><li>Nail trim &mdash; $15</li></ul>
<p>Call the shop and we will find you a slot this week.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Tempo Driving School", """
<h1>Tempo Driving School</h1>
<h2>Driving lessons</h2>
<p>Lessons are $60 per hour, with a ten-lesson package at $550.</p>
<p>Tell us where you are and we will meet you at your door.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Ridgeway Self Storage", """
<h1>Ridgeway Self Storage</h1>
<h2>Unit sizes</h2>
<table><tr><td>5x10</td><td>$89 per month</td></tr>
<tr><td>10x10</td><td>$145 per month</td></tr>
<tr><td>10x20</td><td>$210 per month</td></tr></table>
<p>Drive-up access seven days a week.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"no_crm_signals"}),
    _labelled(
        "Fen Lane Physiotherapy", """
<h1>Fen Lane Physiotherapy</h1>
<h2>Treatments</h2><ul><li>Manual therapy</li><li>Post-surgical rehabilitation</li></ul>
<h2>Fees</h2><p>Initial assessment $110. Follow-up visits are thirty minutes.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"no_crm_signals", "no_online_booking"}),
    _labelled(
        "Stonebridge Cabinetry", """
<h1>Stonebridge Cabinetry</h1>
<h2>What we do</h2><ul><li>Bespoke kitchen cabinetry</li><li>Fitted wardrobes</li>
<li>Solid surface worktops</li></ul>
<p>Kitchens start from &pound;12,000.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"no_crm_signals"}),
    # Three money matches and not a price among them: the page is advertising
    # what it takes off, which is the shape the old threshold could not tell
    # apart from a rate card.
    _labelled(
        "Argyle Auto Detailing", """
<h1>Argyle Auto Detailing</h1>
<h2>Detailing packages</h2>
<p>Save $50 on your first service, $25 off referrals and $0 down on the monthly plan.</p>
<ul><li>Interior detailing</li><li>Paint correction</li><li>Ceramic coating</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),
    # And a page that talks about price without publishing one. "our prices" was
    # read as a price list, so the finding that is true here never fired.
    _labelled(
        "Halden Chartered Accountants", """
<h1>Halden Chartered Accountants</h1>
<h2>Accounting services</h2><ul><li>Year end accounts</li><li>Payroll</li>
<li>VAT returns</li></ul>
<p>Our prices are competitive and every engagement is quoted individually.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Marchmont Wine Merchants", """
<h1>Marchmont Wine Merchants</h1>
<div id="my-store-12345678"></div>
<h2>This month</h2>
<ul><li>Barbera d'Asti &pound;14.50</li><li>Muscadet &pound;11.00</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"ecommerce_manual", "no_crm_signals"},
        head='<script src="https://app.ecwid.com/script.js?12345678"></script>\n'),

    # ── A chat box the seven-vendor list could not see ──
    _labelled(
        "Ellesmere Windows", """
<h1>Ellesmere Windows</h1>
<h2>Services</h2><ul><li>Double glazing replacement</li><li>Conservatory repairs</li>
<li>Door installation</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_live_chat"}) | {"no_crm_signals", "no_online_booking"},
        head='<script id="ze-snippet" src="https://static.zdassets.com/ekr/snippet.js?key=ab12">'
             "</script>\n"),
    _labelled(
        "Copperfield Clinic", """
<h1>Copperfield Clinic</h1>
<h2>Treatments</h2><ul><li>Physiotherapy</li><li>Sports massage</li>
<li>Acupuncture</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_live_chat"}) | {"no_crm_signals", "no_online_booking"},
        head='<script src="//fw-cdn.com/1234567/2345678.js" chat="true"></script>\n'),
    _labelled(
        "Rowan and Hale Solicitors", """
<h1>Rowan and Hale Solicitors</h1>
<h2>Our services</h2><ul><li>Conveyancing</li><li>Family law</li>
<li>Wills and probate</li></ul>
<p>Telephone the office or send the form and a partner will come back to you.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_live_chat"}) | {"no_crm_signals"},
        head='<script async src="https://static.olark.com/jsclient/loader0.js"></script>\n'),
    _labelled(
        "Nordvik Interiors", """
<h1>Nordvik Interiors</h1>
<h2>What we do</h2><ul><li>Curtains and blinds</li><li>Upholstery</li>
<li>Colour schemes</li></ul>
<p>Visit the showroom on the Mill Road any weekday.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_live_chat"}) | {"no_crm_signals"},
        head='<script src="https://www.smartsuppchat.com/loader.js?"></script>\n'),
    _labelled(
        "Sandpiper Marine", """
<h1>Sandpiper Marine</h1>
<h2>Services</h2><ul><li>Engine servicing</li><li>Hull cleaning</li>
<li>Winter storage</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_live_chat"}) | {"no_crm_signals", "no_online_booking"},
        head='<script src="//code.jivosite.com/widget/ab12cd34" async></script>\n'),
    # One script, three facts: HubSpot's loader is the chat box, the CRM behind
    # the form and the thing counting last month's visits, and the tables held
    # it as a CRM only.
    _labelled(
        "Kingsway Orthodontics", """
<h1>Kingsway Orthodontics</h1>
<h2>Treatments</h2><ul><li>Clear aligners</li><li>Fixed braces</li><li>Retainers</li></ul>
<p>Your first appointment takes about an hour.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_live_chat", "no_analytics"}) | {"no_online_booking"},
        head='<script id="hs-script-loader" async defer src="//js.hs-scripts.com/7654321.js">'
             "</script>\n"),
    # The lookalike: the word in a sentence, and nothing on the page to click.
    _labelled(
        "Ivybridge Consulting", """
<h1>Ivybridge Consulting</h1>
<p>Get in touch and let’s have a chat about your project.</p>
<h2>What we do</h2><ul><li>Operations reviews</li><li>Process mapping</li>
<li>Interim management</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),

    # ── A booking system the link rule could not read ──
    # "/online-booking/" and "Book Appointment" are the two plainest ways a site
    # writes it, and the rule wanted the segment to be exactly "/booking".
    _labelled(
        "Wren Street Dental", """
<h1>Wren Street Dental</h1>
<p><a href="/online-booking/">Book Appointment</a></p>
<h2>Treatments</h2><ul><li>Dental check-ups</li><li>Hygienist visits</li>
<li>Teeth whitening</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Halcyon Day Spa", """
<h1>Halcyon Day Spa</h1>
<iframe src="https://halcyon.janeapp.com/" title="Book"></iframe>
<h2>Treatments</h2><ul><li>Swedish massage</li><li>Facial and skin care</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Nether Green Veterinary", """
<h1>Nether Green Veterinary</h1>
<p><a href="https://booksy.com/en-gb/12345_nether-green-veterinary">Make a booking</a></p>
<h2>Our Services</h2><ul><li>Vaccinations</li><li>Wellness exams</li>
<li>Dental cleaning</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    _labelled(
        "Whitfield Osteopathy", """
<h1>Whitfield Osteopathy</h1>
<p><a href="https://calendar.app.google/aBcD1234">Choose a time</a></p>
<h2>Treatments</h2><ul><li>Osteopathic treatment</li><li>Sports massage</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    # The trade says it books times and the page never uses the word.
    _labelled(
        "Larkspur Tattoo Studio", """
<h1>Larkspur Tattoo Studio</h1>
<h2>What we do</h2><ul><li>Custom tattoo design</li><li>Cover-up work</li>
<li>Fine line and script</li></ul>
<p>Send us your idea and we will find you a chair.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "no_online_booking"}),
    # The page says it books nothing, in the wording most shops use for it.
    # Without the comma and the hyphens the phrase list had never seen it.
    _labelled(
        "Pinewood Barbers", """
<h1>Pinewood Barbers</h1>
<p>Walk ins welcome &mdash; first come first served, six days a week.</p>
<h2>Services</h2><ul><li>Haircut</li><li>Beard trim</li><li>Hot towel shave</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),
    # And a booking word that is not a booking system: the small print.
    _labelled(
        "Kilbride Print", """
<nav><a href="/">Home</a> <a href="/booking-terms/">Booking terms</a></nav>
<h1>Kilbride Print</h1>
<h2>Services</h2><ul><li>Litho printing</li><li>Digital printing</li>
<li>Finishing and binding</li></ul>
<p>Booking terms and cancellation policy apply to every order.</p>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),

    # ── Something is measuring the site, and something is filing the lead ──
    _labelled(
        "Auchen Timber", """
<h1>Auchen Timber</h1>
<h2>What we do</h2><ul><li>Sawn timber and sheet materials</li><li>Fencing supplies</li>
<li>Decking kits</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_analytics"}) | {"no_crm_signals"},
        head='<script>window.uetq = window.uetq || [];</script>\n'
             '<script src="//bat.bing.com/bat.js"></script>\n'),
    _labelled(
        "Crawford and Sons Ironmongers", """
<h1>Crawford and Sons Ironmongers</h1>
<h2>What we do</h2><ul><li>Key cutting</li><li>Tool hire</li><li>Paint mixing</li></ul>
<p>Key cutting from &pound;4.50.</p>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_analytics", "price_opaque"}) | {"no_crm_signals"},
        head='<script src="https://cdn.segment.com/analytics.js/v1/ab12/analytics.min.js">'
             "</script>\n"),
    _labelled(
        "Bellevue Aesthetics", """
<h1>Bellevue Aesthetics</h1>
<h2>Treatments</h2><ul><li>Anti-wrinkle injections</li><li>Dermal fillers</li>
<li>Skin consultation</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_online_booking"},
        head='<script src="https://bellevue.infusionsoft.com/app/webTracking/getTrackingCode">'
             "</script>\n"),
    # A mailing list a visitor can join, rendered by the tool rather than by a
    # <form>: nothing on this site is a contact form, and something on it does
    # ask for an email.
    _labelled(
        "Ravenscroft Legal", """
<h1>Ravenscroft Legal</h1>
<h2>Our services</h2><ul><li>Conveyancing</li><li>Wills and probate</li>
<li>Employment law</li></ul>
<h2>Newsletter</h2>
<div class="ctct-inline-form" data-form-id="ab12cd34"></div>
""",
        _BASE_GAPS,
        head='<script src="https://static.ctctcdn.com/js/signup-form-widget/current/'
             'signup-form-widget.min.js"></script>\n'),
    _labelled(
        "Foxglove Florists", """
<h1>Foxglove Florists</h1>
<div class="sqs-add-to-cart-button" data-item-id="ab12cd34">Add to Cart</div>
<h2>Bouquets</h2>
<ul><li>Seasonal hand-tied &pound;35.00</li><li>Letterbox flowers &pound;22.00</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"price_opaque"}) | {"ecommerce_manual", "no_crm_signals"},
        head='<script src="https://static1.squarespace.com/static/commerce/scripts/shop.js">'
             "</script>\n"),

    # ── A quote is handled by hand when the site hands you the control ──
    _labelled(
        "Tamar Glazing", """
<h1>Tamar Glazing</h1>
<p><a href="/quote/">Request a quote</a></p>
<h2>Services</h2><ul><li>Double glazing installation</li><li>Window repair</li>
<li>Misted unit replacement</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "quote_by_form", "no_online_booking"}),
    # The same words in a paragraph, on a site whose whole pitch is that you do
    # not have to ask anybody for a price.
    _labelled(
        "Fairfield Insurance Brokers", """
<h1>Fairfield Insurance Brokers</h1>
<h2>Instant quotes</h2>
<p>Compare policies online and see your premium instantly &mdash; no need to
request a quote by email and wait.</p>
<h2>Cover we arrange</h2><ul><li>Commercial combined</li><li>Fleet insurance</li>
<li>Professional indemnity</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals"}),

    # ── A social profile the host list did not hold, and a vacancy typeset
    #    with the apostrophe a word processor produces ──
    _labelled(
        "Quayside Framing", """
<h1>Quayside Framing</h1>
<p><a href="https://www.pinterest.com/quaysideframing/">Pinterest</a></p>
<h2>What we do</h2><ul><li>Bespoke frames</li><li>Glass cutting</li>
<li>Mount cutting</li></ul>
""" + _LEAD_FORM,
        (_BASE_GAPS - {"no_social_presence"}) | {"no_crm_signals"}),
    _labelled(
        "Bramhall Motors", """
<h1>Bramhall Motors</h1>
<p>We’re hiring a qualified technician — bring your CV to the workshop.</p>
<h2>Services</h2><ul><li>MOT testing</li><li>Servicing and repairs</li>
<li>Diagnostics</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS | {"no_crm_signals", "careers_manual", "no_online_booking"}),
)


def _corpus_rates(corpus) -> dict:
    """Per-rule (true positives, false positives, false negatives) over `corpus`.

    A row is `(name, html, gaps)` or `(name, url, html, gaps)`. The four-part
    form exists because the address a page was served from is itself a fact the
    audit reads — a site on plain http is one `no_ssl` fires on — and a
    synthesised https URL would have graded that rule against a page it never
    saw.
    """
    rates = {code: [0, 0, 0] for code in A.GAP_CATALOGUE}
    for row in corpus:
        if len(row) == 4:
            name, url, html, expected = row
        else:
            name, html, expected = row
            url = "https://%s.test/" % re.sub(r"[^a-z]+", "-", name.lower())
        # The third slot may also be a whole crawl -- the `{url: html}` dict
        # `harvest_site` hands over -- which is the only shape the findings
        # that live across a site can be graded in at all.
        fired = set(_codes(A.audit_from_html(
            html if isinstance(html, dict) else {url: html}, url)))
        for code in A.GAP_CATALOGUE:
            hit, want = code in fired, code in expected
            if hit and want:
                rates[code][0] += 1
            elif hit:
                rates[code][1] += 1
            elif want:
                rates[code][2] += 1
    return rates



# ── A third corpus: the platforms this tool actually meets ──

# Thirty-six pages built from the markers real site builders ship, every label
# written down from reading the page before a rule was run against it. The two
# corpora above are made of one page shape on purpose, which is what makes them
# sharp about a single rule and blind to everything a real crawl brings with it:
# a site builder that ships its own chat, a page that answers 200 with a bot
# check on it, a consent wall, a framework shell, a business whose whole site is
# in German.
#
# `reason` is the other half of the label. On six of these pages the honest
# answer is not a list of faults but "nobody read this site, and here is why" —
# and until this corpus existed, all six produced a full slate of confident
# absences instead. See `test_a_page_nobody_could_read_is_not_a_list_of_faults`.

_PLATFORM_CORPUS: list = []


def _platform(name, url, html, gaps, reason=""):
    _PLATFORM_CORPUS.append((name, url, html, frozenset(gaps), reason))


OLD_POST = (TODAY - datetime.timedelta(days=1100)).isoformat()

_FOOT = ('<footer><p>&copy; %d %%s. %%s</p></footer></body></html>' % TODAY.year)


# ── 1. WordPress + Elementor, a dentist ──
_platform("wp-elementor-dentist", "https://bloorwestdental.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="WordPress 6.5.2">
<title>Bloor West Dental | Family Dentist in Toronto</title>
<link rel="stylesheet" href="/wp-content/plugins/elementor/assets/css/frontend.min.css?ver=3.21.0">
<link rel="stylesheet" href="/wp-content/uploads/elementor/css/post-14.css">
<script src="/wp-includes/js/jquery/jquery.min.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-7QK2L9"></script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Dentist",
"name":"Bloor West Dental","telephone":"(416) 555-0142"}</script>
</head><body class="elementor-page elementor-page-14">
<nav><a href="/">Home</a> <a href="/services/">Services</a> <a href="/contact/">Contact</a></nav>
<h1>Bloor West Dental</h1>
<h2>Our Services</h2>
<ul><li>Dental cleaning and check-ups</li><li>Emergency dental repair</li>
<li>Teeth whitening</li></ul>
<p>Call (416) 555-0142 or send the form and the front desk will call you back.</p>
<form class="elementor-form" action="/contact/" method="post">
<input type="text" name="form_fields[name]"><input type="email" name="form_fields[email]">
<textarea name="form_fields[message]"></textarea><button type="submit">Send</button></form>
<p><a href="https://www.facebook.com/bloorwestdental/">Facebook</a></p>
""" + _FOOT % ("Bloor West Dental", "2180 Bloor St W, Toronto, ON M6S 1N3."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "price_opaque"})

# ── 2. WordPress + Divi, a roofer that quotes by form and is hiring ──
_platform("wp-divi-roofer", "https://summitroofing.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Summit Roofing | Mississauga Roofers</title>
<link rel="stylesheet" href="/wp-content/themes/Divi/style.css?ver=4.24">
<script src="/wp-content/themes/Divi/js/custom.unified.js"></script>
</head><body class="et_pb_pagebuilder_layout">
<nav><a href="/">Home</a> <a href="/services/">Services</a> <a href="/careers/">Careers</a></nav>
<h1>Summit Roofing</h1>
<h2>Our Services</h2>
<ul><li>Roof repair</li><li>Roof inspection</li><li>Eavestrough installation</li></ul>
<h2>What our customers say</h2>
<blockquote>They were on the roof the same afternoon. &mdash; Dana R.</blockquote>
<p><a class="et_pb_button" href="/contact/">Request a free quote</a></p>
<form action="/contact/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (905) 555-0188.</p>
""" + _FOOT % ("Summit Roofing", "77 Dundas St E, Mississauga, ON L4W 5N5."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "no_analytics", "no_schema",
      "no_social_presence", "price_opaque", "quote_by_form", "careers_manual",
      "no_review_capture"})

# ── 3. Wix, a salon running Wix Bookings and Wix Chat ──
_platform("wix-salon", "https://gildedshears.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gilded Shears Salon</title>
<link rel="stylesheet" href="https://static.parastorage.com/services/wix-thunderbolt/dist/main.css">
<script src="https://static.parastorage.com/services/wix-thunderbolt/dist/main.bundle.min.js"></script>
<script src="https://static.parastorage.com/services/chat-widget/1.2.0/wix-visitor-chat.bundle.min.js"></script>
</head><body>
<nav><a href="/">Home</a> <a href="/book-online">Book Online</a>
<a href="https://www.instagram.com/gildedshears/">Instagram</a></nav>
<h1>Gilded Shears</h1>
<h2>Services</h2>
<ul><li>Cut and blow dry &mdash; $70</li><li>Balayage &mdash; $210</li>
<li>Keratin treatment &mdash; $180</li></ul>
<div data-hook="bookings-widget" id="comp-bookings"></div>
<script>window.wixBookingsConfig={"instance":"abc"};</script>
<form action="/_functions/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (647) 555-0110.</p>
""" + _FOOT % ("Gilded Shears", "14 Ossington Ave, Toronto, ON M6J 2Y7."),
     {"no_crm_signals", "no_analytics", "no_schema"})

# ── 4. Squarespace, a yoga studio with Acuity, Mailchimp and analytics ──
_platform("squarespace-yoga", "https://stillpointyoga.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stillpoint Yoga</title>
<link rel="stylesheet" href="https://static1.squarespace-cdn.com/static/site.css">
<script src="https://static.squarespace.com/universal/scripts-compressed/common.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-BB88CC"></script>
<script src="https://chimpstatic.com/mcjs-connected/js/users/abc/1.js"></script>
<script type="application/ld+json">{"@context":"https://schema.org",
"@type":"HealthAndBeautyBusiness","name":"Stillpoint Yoga"}</script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://stillpoint.squarespacescheduling.com/schedule.php">Book a class</a>
<a href="https://www.instagram.com/stillpointyoga/">Instagram</a></nav>
<h1>Stillpoint Yoga</h1>
<h2>Classes</h2><ul><li>Drop-in class &mdash; $24</li><li>Ten-class pass &mdash; $200</li></ul>
<form action="/api/form" method="post"><input type="email" name="email">
<textarea name="message"></textarea><button type="submit">Send</button></form>
<p>Call (416) 555-0177.</p>
""" + _FOOT % ("Stillpoint Yoga", "500 Queen St W, Toronto, ON M5V 2B3."),
     {"no_live_chat"})

# ── 5. Shopify with Shopify Inbox and Klaviyo ──
_platform("shopify-inbox-store", "https://northsidesupply.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Northside Supply</title>
<script>var Shopify = Shopify || {}; Shopify.theme = {"name":"Dawn","id":1};</script>
<link href="https://cdn.shopify.com/s/files/1/0001/theme.css" rel="stylesheet">
<script src="https://cdn.shopify.com/shopifycloud/chat/assets/chat.js" async></script>
<script src="https://static.klaviyo.com/onsite/js/klaviyo.js?company_id=ABC"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-SHOP1"></script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
"name":"Steel toe boot","offers":{"@type":"Offer","price":"129.00"}}</script>
</head><body>
<nav><a href="/collections/all">Shop</a> <a href="/cart">Cart</a>
<a href="https://www.instagram.com/northsidesupply/">Instagram</a></nav>
<h1>Northside Supply</h1>
<p>Boots from $129.00, jackets $189.00, gloves $24.99.</p>
<form action="/cart/add" method="post"><input type="hidden" name="id" value="1">
<button>Add to cart</button></form>
<p>Call (905) 555-0123.</p>
""" + _FOOT % ("Northside Supply", "9 Barton St, Hamilton, ON L8L 2X1."),
     {"ecommerce_manual"})

# ── 6. Webflow agency on HubSpot ──
_platform("webflow-agency", "https://foldstudio.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fold Studio | Brand and Product Design</title>
<link rel="stylesheet" href="https://assets-global.website-files.com/6011/fold.css">
<script src="https://assets-global.website-files.com/6011/webflow.js"></script>
<script src="https://js.hs-scripts.com/4455667.js" async defer></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-FOLD1"></script>
</head><body data-wf-page="6011a" data-wf-site="6011">
<nav><a href="/">Home</a> <a href="/work">Work</a>
<a href="https://www.linkedin.com/company/foldstudio/">LinkedIn</a></nav>
<h1>Fold Studio</h1>
<h2>What we do</h2><ul><li>Brand strategy</li><li>Product design</li>
<li>Design systems</li></ul>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call 020 7946 0102.</p>
""" + _FOOT % ("Fold Studio", "18 Hoxton Sq, London N1 6NU."),
     {"no_schema", "price_opaque"})

# ── 7. GoDaddy one-page brochure, an email address and nothing else ──
_platform("godaddy-brochure", "https://verdantlawns.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verdant Lawns</title>
<link rel="stylesheet" href="https://img1.wsimg.com/blobby/go/site/styles.css">
<script src="https://img1.wsimg.com/blobby/go/bundle.js"></script>
</head><body>
<h1>Verdant Lawns</h1>
<p>Lawn care and garden maintenance across Barrie and Innisfil.</p>
<h2>Services</h2><ul><li>Lawn mowing</li><li>Garden maintenance</li>
<li>Spring and fall cleanup</li></ul>
<p>Email <a href="mailto:hello@verdantlawns.test">hello@verdantlawns.test</a>
or call (705) 555-0166.</p>
""" + _FOOT % ("Verdant Lawns", "31 Dunlop St E, Barrie, ON L4M 1A2."),
     {"no_lead_capture", "email_only_intake", "no_live_chat", "no_analytics", "no_schema",
      "no_social_presence", "price_opaque"})

# ── 8. Duda, a three-branch physiotherapy chain ──
_platform("duda-physio-chain", "https://tricityphysio.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tri-City Physiotherapy</title>
<link rel="stylesheet" href="https://irp.cdn-website.com/tricity/styles.css">
<script src="https://static.cdn-website.com/libs/runtime.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TRI1"></script>
<script type="application/ld+json">{"@context":"https://schema.org",
"@type":"MedicalBusiness","name":"Tri-City Physiotherapy"}</script>
</head><body>
<nav><a href="/">Home</a> <a href="/locations">Locations</a>
<a href="https://www.facebook.com/tricityphysio/">Facebook</a></nav>
<h1>Tri-City Physiotherapy</h1>
<h2>Treatments</h2><ul><li>Manual therapy</li><li>Sports injury treatment</li>
<li>Post-surgical rehabilitation</li></ul>
<address>120 King St W, Kitchener, ON N2G 1A7</address>
<address>55 Erb St E, Waterloo, ON N2J 1L7</address>
<address>10 Queen St S, Cambridge, ON N3C 1G2</address>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (519) 555-0144.</p>
""" + _FOOT % ("Tri-City Physiotherapy", "Three clinics across Waterloo Region."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "multi_location",
      "price_opaque"})

# ── 9. Weebly restaurant, OpenTable, no form ──
_platform("weebly-restaurant", "https://ferrymanstable.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Ferryman's Table</title>
<link rel="stylesheet" href="https://www.editmysite.com/editor/site.css">
<script src="https://www.weebly.com/uploads/site.js"></script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://www.opentable.com/r/the-ferrymans-table">Reserve a table</a>
<a href="https://www.facebook.com/ferrymanstable/">Facebook</a>
<a href="https://www.instagram.com/ferrymanstable/">Instagram</a></nav>
<h1>The Ferryman's Table</h1>
<h2>Menu</h2><p>Starters from $14, mains $32, desserts $11.</p>
<p>Questions? <a href="mailto:eat@ferrymanstable.test">eat@ferrymanstable.test</a>
or call (250) 555-0195.</p>
""" + _FOOT % ("The Ferryman's Table", "42 Wharf St, Victoria, BC V8W 1T2."),
     {"no_lead_capture", "no_live_chat", "no_analytics", "no_schema"})

# ── 10. Joomla contractor on plain http, no viewport, a 2019 footer ──
_platform("joomla-http-contractor", "http://cornerstoneconcrete.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Cornerstone Concrete</title>
<link rel="stylesheet" href="/media/jui/css/bootstrap.min.css">
<script src="/media/system/js/core.js"></script>
</head><body>
<h1>Cornerstone Concrete</h1>
<h2>What we do</h2><ul><li>Concrete forming</li><li>Excavation</li>
<li>Site servicing</li></ul>
<form action="/index.php?option=com_contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (613) 555-0121.</p>
<footer><p>&copy; 2019 Cornerstone Concrete. 8 Bank St, Ottawa, ON K1P 5N2.</p></footer>
</body></html>""",
     {"no_ssl", "no_mobile", "stale_site", "no_crm_signals", "no_live_chat",
      "no_analytics", "no_schema", "no_social_presence", "price_opaque"})

# ── 11. Drupal clinic with a blog nobody has posted to in three years ──
_platform("drupal-stale-blog", "https://harbourhealth.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harbour Health Clinic</title>
<script src="/sites/default/files/js/drupal-settings.js"></script>
<script type="application/json" data-drupal-selector="drupal-settings-json">{}</script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-HARB1"></script>
</head><body>
<nav><a href="/">Home</a> <a href="/blog">Blog</a>
<a href="https://www.facebook.com/harbourhealth/">Facebook</a></nav>
<h1>Harbour Health Clinic</h1>
<h2>Our Services</h2><ul><li>Physiotherapy treatment</li><li>Massage therapy</li>
<li>Custom orthotics</li></ul>
<article><h2><a href="/blog/winter-injuries">Winter injuries and how to avoid them</a></h2>
<time datetime="%s">a while ago</time></article>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (902) 555-0133.</p>
""" % OLD_POST + _FOOT % ("Harbour Health Clinic", "5 Water St, Halifax, NS B3J 1A1."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "stale_blog", "no_schema",
      "price_opaque"})

# ── 12. A Cloudflare bot check ──
_platform("cloudflare-challenge", "https://guardedplumbing.test/", """<!doctype html>
<html lang="en-US"><head><meta charset="utf-8">
<title>Just a moment...</title>
<meta http-equiv="X-UA-Compatible" content="IE=Edge">
<meta name="robots" content="noindex,nofollow">
</head><body class="no-js">
<div class="main-wrapper" role="main"><div class="main-content">
<h1><span>guardedplumbing.test</span></h1>
<h2 class="h2"><span id="challenge-error-text">Verifying you are human. This may take a
few seconds.</span></h2>
<p>guardedplumbing.test needs to review the security of your connection before proceeding.</p>
</div></div>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=8a1"></script>
</body></html>""", set(), "challenge")

# ── 13. A Dutch cookie wall and nothing behind it ──
_platform("dutch-cookie-wall", "https://dakwerkenvermeer.test/", """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dakwerken Vermeer</title>
</head><body>
<div id="cookie-consent-wall">
<h1>Deze website gebruikt cookies</h1>
<p>Wij en onze partners plaatsen cookies om de website te laten werken, het gebruik te
meten en advertenties te personaliseren. U kunt uw keuze later altijd wijzigen.</p>
<button id="accept-all">Alles accepteren</button>
<button id="reject-all">Alleen noodzakelijke</button>
<a href="/cookiebeleid">Cookiebeleid</a>
</div>
</body></html>""", set(), "cookie_wall")

# ── 14. A Next.js shell that renders everything client side ──
_platform("nextjs-shell", "https://orbitdental.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbit Dental</title>
<link rel="preload" href="/_next/static/css/a1b2.css" as="style">
<link rel="stylesheet" href="/_next/static/css/a1b2.css">
</head><body>
<div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{}},
"page":"/","query":{},"buildId":"x9"}</script>
<script src="/_next/static/chunks/main-9f2.js" defer></script>
<script src="/_next/static/chunks/pages/index-3c1.js" defer></script>
</body></html>""", set(), "js_only")

# ── 15. A parked domain ──
_platform("parked-domain", "https://claymoreelectric.test/", """<!doctype html>
<html><head><meta charset="utf-8">
<title>claymoreelectric.test</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body>
<h1>claymoreelectric.test</h1>
<p>This domain is for sale. Buy this domain today.</p>
<p><a href="https://www.hugedomains.com/domain_profile.cfm?d=claymoreelectric">
Make an offer</a></p>
<script src="https://parkingcrew.net/js/park.js"></script>
</body></html>""", set(), "parked")

# ── 16. A response with a shell and no body at all ──
_platform("empty-body", "https://kettlevalleymasonry.test/",
     "<!doctype html><html><head><title></title></head><body></body></html>",
     set(), "empty")

# ── 17. A WordPress maintenance-mode splash ──
_platform("under-construction", "https://tidewaterhvac.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tidewater HVAC &#8212; Coming Soon</title>
<link rel="stylesheet" href="/wp-content/plugins/coming-soon/style.css">
</head><body>
<h1>Our new site is coming soon</h1>
<p>We are working on something great. Check back shortly.</p>
</body></html>""", set(), "under_construction")

# ── 18. French plumber, appointments by telephone ──
_platform("french-plumber", "https://plomberiedurand.test/", """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="WordPress 6.4">
<title>Plomberie Durand | Plombier &agrave; Lyon</title>
<link rel="stylesheet" href="/wp-content/themes/durand/style.css">
</head><body>
<nav><a href="/">Accueil</a> <a href="/nos-prestations/">Nos prestations</a>
<a href="/contact/">Contact</a>
<a href="https://www.facebook.com/plomberiedurand/">Facebook</a></nav>
<h1>Plomberie Durand</h1>
<h2>Nos prestations</h2><ul><li>D&eacute;pannage plomberie</li>
<li>Installation de chaudi&egrave;re</li><li>Recherche de fuite</li></ul>
<p>Pour prendre rendez-vous, appelez le 04 78 55 44 33.</p>
<form action="/contact/" method="post"><input type="text" name="nom">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Envoyer</button></form>
""" + _FOOT % ("Plomberie Durand", "12 rue de la R&eacute;publique, 69002 Lyon."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "no_analytics", "no_schema",
      "price_opaque"})

# ── 19. German dentist booking through Doctolib ──
_platform("german-doctolib", "https://zahnarztpraxis-berg.test/", """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zahnarztpraxis Berg | Z&auml;hne in M&uuml;nchen</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Dentist",
"name":"Zahnarztpraxis Berg"}</script>
</head><body>
<nav><a href="/">Start</a> <a href="/leistungen/">Leistungen</a>
<a href="https://www.doctolib.de/zahnarzt/muenchen/praxis-berg">Termin buchen</a>
<a href="https://www.facebook.com/zahnarztpraxisberg/">Facebook</a></nav>
<h1>Zahnarztpraxis Berg</h1>
<h2>Unsere Leistungen</h2><ul><li>Professionelle Zahnreinigung</li>
<li>Implantologie</li><li>Kinderzahnheilkunde</li></ul>
<form action="/kontakt/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="nachricht"></textarea>
<button type="submit">Senden</button></form>
<p>Telefon 089 55 44 33 22.</p>
""" + _FOOT % ("Zahnarztpraxis Berg", "Leopoldstra&szlig;e 21, 80802 M&uuml;nchen."),
     {"no_crm_signals", "no_live_chat", "no_analytics", "price_opaque"})

# ── 20. Spanish dental clinic: online booking and a WhatsApp button ──
_platform("spanish-whatsapp-clinic", "https://clinicadentalsol.test/", """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cl&iacute;nica Dental Sol | Valencia</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-SOL22"></script>
</head><body>
<nav><a href="/">Inicio</a> <a href="/pedir-cita/">Pedir cita online</a>
<a href="https://www.instagram.com/clinicadentalsol/">Instagram</a></nav>
<h1>Cl&iacute;nica Dental Sol</h1>
<h2>Nuestros servicios</h2><ul><li>Limpieza dental</li><li>Ortodoncia invisible</li>
<li>Implantes dentales</li></ul>
<p><a href="https://wa.me/34600555044">Escr&iacute;benos por WhatsApp</a></p>
<form action="/contacto/" method="post"><input type="text" name="nombre">
<input type="email" name="email"><textarea name="mensaje"></textarea>
<button type="submit">Enviar</button></form>
<p>Tel&eacute;fono 960 55 50 44.</p>
""" + _FOOT % ("Cl&iacute;nica Dental Sol", "Carrer de Colon 8, 46004 Valencia."),
     {"no_crm_signals", "no_live_chat", "no_schema", "price_opaque", "whatsapp_manual"})

# ── 21. Dutch dentist, appointment link, no form ──
_platform("dutch-tandarts", "https://tandartsdekade.test/", """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tandartspraktijk De Kade</title>
</head><body>
<nav><a href="/">Home</a> <a href="/afspraak-maken/">Afspraak maken</a>
<a href="/behandelingen/">Behandelingen</a></nav>
<h1>Tandartspraktijk De Kade</h1>
<h2>Onze behandelingen</h2><ul><li>Gebitsreiniging</li><li>Vullingen</li>
<li>Kronen en bruggen</li></ul>
<p>Bel 010 555 44 33 of mail
<a href="mailto:info@tandartsdekade.test">info@tandartsdekade.test</a>.</p>
""" + _FOOT % ("Tandartspraktijk De Kade", "Westzeedijk 100, 3016 AE Rotterdam."),
     {"no_lead_capture", "no_live_chat", "no_analytics", "no_schema",
      "no_social_presence", "price_opaque"})

# ── 22. A real WooCommerce bakery ──
_platform("woocommerce-bakery", "https://proofbakehouse.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proof Bakehouse</title>
<link rel="stylesheet" href="/wp-content/plugins/woocommerce/assets/css/woocommerce.css">
<script src="/wp-content/plugins/woocommerce/assets/js/frontend/add-to-cart.min.js"></script>
<script>var wc_add_to_cart_params = {"ajax_url":"/?wc-ajax=%%%%endpoint%%%%"};</script>
<script src="https://chimpstatic.com/mcjs-connected/js/users/proof/1.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-PROOF"></script>
</head><body class="woocommerce-page">
<nav><a href="/shop/">Shop</a> <a href="/cart/">Cart</a>
<a href="https://www.facebook.com/proofbakehouse/">Facebook</a></nav>
<h1>Proof Bakehouse</h1>
<ul class="products"><li>Sourdough loaf &mdash; $9.00</li><li>Cinnamon buns &mdash; $18.00</li></ul>
<form class="cart" method="post"><button type="submit" name="add-to-cart" value="41">
Add to cart</button></form>
<p>Call (416) 555-0109.</p>
""" + _FOOT % ("Proof Bakehouse", "88 Ossington Ave, Toronto, ON M6J 2Z1."),
     {"ecommerce_manual", "no_live_chat", "no_schema"})

# ── 23. An agency that sells WooCommerce builds and runs no shop ──
_platform("agency-mentions-woocommerce", "https://reddeerdigital.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Red Deer Digital</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-RDD1"></script>
</head><body>
<nav><a href="/">Home</a> <a href="/services/">Services</a>
<a href="https://www.linkedin.com/company/reddeerdigital/">LinkedIn</a></nav>
<h1>Red Deer Digital</h1>
<h2>Our Services</h2><ul><li>WooCommerce development</li><li>Shopify theme builds</li>
<li>Magento migrations</li></ul>
<p>We build online stores for other people. We do not sell anything here.</p>
<form action="/contact/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (403) 555-0187.</p>
""" + _FOOT % ("Red Deer Digital", "44 Gaetz Ave, Red Deer, AB T4N 4A1."),
     {"no_crm_signals", "no_live_chat", "no_schema", "price_opaque"})

# ── 24. An HR consultancy that links to workforce.com ──
_platform("hr-links-workforce", "https://northgatepeople.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Northgate People</title>
</head><body>
<nav><a href="/">Home</a> <a href="/services/">Services</a>
<a href="https://www.linkedin.com/company/northgatepeople/">LinkedIn</a></nav>
<h1>Northgate People</h1>
<h2>What we do</h2><ul><li>HR policy reviews</li><li>Payroll setup</li>
<li>Employment contracts</li></ul>
<p>We implement rota tools such as
<a href="https://www.workforce.com/features">Workforce.com</a> for our clients.</p>
<form action="/contact/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call 0161 555 0134.</p>
""" + _FOOT % ("Northgate People", "3 Deansgate, Manchester M3 2BW."),
     {"no_crm_signals", "no_live_chat", "no_analytics", "no_schema", "price_opaque"})

# ── 25. Tag Manager only, Intercom, Calendly ──
_platform("gtm-intercom-calendly", "https://arborfinance.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arbor Finance</title>
<script>(function(w,d,s,l,i){w[l]=w[l]||[];})(window,document,'script','dataLayer','GTM-ABC123');</script>
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>
<script>window.intercomSettings={app_id:"ab12cd"};</script>
<script src="https://widget.intercom.io/widget/ab12cd"></script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://calendly.com/arborfinance/intro">Book a consultation</a>
<a href="https://www.linkedin.com/company/arborfinance/">LinkedIn</a></nav>
<h1>Arbor Finance</h1>
<h2>What we do</h2><ul><li>Cashflow forecasting</li><li>Bookkeeping</li>
<li>Year-end accounts</li></ul>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call 0117 555 0192.</p>
""" + _FOOT % ("Arbor Finance", "9 Queen Sq, Bristol BS1 4JQ."),
     {"no_crm_signals", "no_schema", "price_opaque"})

# ── 26. Testimonials with a live Google review link ──
_platform("movers-with-review-link", "https://ironhorsemoving.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Iron Horse Moving</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-IRON1"></script>
<script type="application/ld+json">{"@context":"https://schema.org",
"@type":"MovingCompany","name":"Iron Horse Moving"}</script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://www.facebook.com/ironhorsemoving/">Facebook</a></nav>
<h1>Iron Horse Moving</h1>
<h2>What we do</h2><ul><li>Local moving</li><li>Packing and unpacking</li>
<li>Storage</li></ul>
<h2>What our customers say</h2>
<blockquote>Nothing broken, nothing late. &mdash; Priya S.</blockquote>
<p><a href="https://g.page/r/CQabcdEFGhIJ/review">Leave us a Google review</a></p>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (204) 555-0155.</p>
""" + _FOOT % ("Iron Horse Moving", "300 Portage Ave, Winnipeg, MB R3C 0C4."),
     {"no_crm_signals", "no_live_chat", "price_opaque"})

# ── 27. A German optician chain whose branches live in JSON-LD ──
_platform("german-optician-chain", "https://optikwendt.test/", """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Optik Wendt | Vier Filialen</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-WENDT"></script>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"Optician","name":"Optik Wendt Mitte","address":{"@type":"PostalAddress",
"streetAddress":"Friedrichstra&szlig;e 12","postalCode":"10117","addressLocality":"Berlin"}},
{"@type":"Optician","name":"Optik Wendt Charlottenburg","address":{"@type":"PostalAddress",
"streetAddress":"Kantstra&szlig;e 55","postalCode":"10627","addressLocality":"Berlin"}},
{"@type":"Optician","name":"Optik Wendt Potsdam","address":{"@type":"PostalAddress",
"streetAddress":"Brandenburger Str. 3","postalCode":"14467","addressLocality":"Potsdam"}},
{"@type":"Optician","name":"Optik Wendt Spandau","address":{"@type":"PostalAddress",
"streetAddress":"Carl-Schurz-Str. 9","postalCode":"13597","addressLocality":"Berlin"}}]}</script>
</head><body>
<nav><a href="/">Start</a> <a href="/filialen/">Filialen</a>
<a href="https://www.facebook.com/optikwendt/">Facebook</a></nav>
<h1>Optik Wendt</h1>
<h2>Unsere Leistungen</h2><ul><li>Sehtest</li><li>Brillenanpassung</li>
<li>Kontaktlinsen</li></ul>
<form action="/kontakt/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="nachricht"></textarea>
<button type="submit">Senden</button></form>
<p>Telefon 030 55 44 33 22.</p>
""" + _FOOT % ("Optik Wendt", "Vier Filialen in Berlin und Potsdam."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "multi_location",
      "price_opaque"})

# ── 28. Behind Cloudflare, but serving the real site ──
_platform("cloudflare-real-site", "https://ashgrovevets.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ashgrove Veterinary Surgery</title>
<script src="/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js"></script>
<script defer src="https://static.cloudflareinsights.com/beacon.min.js"
data-cf-beacon='{"token":"abc123"}'></script>
</head><body>
<nav><a href="/">Home</a> <a href="/services/">Services</a>
<a href="https://www.facebook.com/ashgrovevets/">Facebook</a></nav>
<h1>Ashgrove Veterinary Surgery</h1>
<h2>Our Services</h2><ul><li>Vaccinations and wellness exams</li>
<li>Dental cleaning</li><li>Pet grooming</li></ul>
<form action="/contact/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (07) 3555 0177.</p>
""" + _FOOT % ("Ashgrove Veterinary Surgery", "12 Waterworks Rd, Ashgrove QLD 4060."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "no_schema", "price_opaque"})

# ── 29. A React site that still ships its content in the HTML ──
_platform("react-but-rendered", "https://lakelinedental.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lakeline Dental</title>
<script src="/static/js/react-dom.production.min.js" defer></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-LAKE1"></script>
</head><body>
<div id="root" data-reactroot="">
<nav><a href="/">Home</a> <a href="/services">Services</a>
<a href="https://www.instagram.com/lakelinedental/">Instagram</a></nav>
<h1>Lakeline Dental</h1>
<h2>Our Services</h2><ul><li>Dental cleaning</li><li>Emergency repair</li>
<li>Whitening</li></ul>
<p><a href="https://code.tidio.co/abc.js">chat</a></p>
<script src="//code.tidio.co/abc123.js" async></script>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (512) 555-0143.</p>
</div>
""" + _FOOT % ("Lakeline Dental", "1400 Ranch Rd, Austin, TX 78717."),
     {"no_online_booking", "no_crm_signals", "no_schema", "price_opaque"})

# ── 30. A shop whose only route in is WhatsApp ──
_platform("whatsapp-only-shop", "https://muebleslaparra.test/", """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Muebles La Parra</title>
</head><body>
<h1>Muebles La Parra</h1>
<h2>Nuestros productos</h2><ul><li>Sof&aacute;s a medida</li><li>Mesas de comedor</li>
<li>Armarios empotrados</li></ul>
<p>Pregunta por
<a href="https://api.whatsapp.com/send?phone=34611555099">WhatsApp</a>
o llama al 961 55 50 99.</p>
""" + _FOOT % ("Muebles La Parra", "Avinguda del Port 44, 46023 Val&egrave;ncia."),
     {"no_lead_capture", "whatsapp_manual", "no_live_chat", "no_analytics", "no_schema",
      "no_social_presence", "price_opaque"})

# ── 31. Squarespace with Squarespace Scheduling and a Trustpilot widget ──
_platform("squarespace-trustpilot", "https://sableaccounting.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sable Accounting</title>
<link rel="stylesheet" href="https://static1.squarespace-cdn.com/static/sable.css">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-SABLE"></script>
<script src="https://widget.trustpilot.com/bootstrap/v5/tp.widget.bootstrap.min.js"></script>
<script type="application/ld+json">{"@context":"https://schema.org",
"@type":"AccountingService","name":"Sable Accounting"}</script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://sable.squarespacescheduling.com/schedule.php">Book a consultation</a>
<a href="https://www.linkedin.com/company/sableaccounting/">LinkedIn</a></nav>
<h1>Sable Accounting</h1>
<h2>Fees</h2><ul><li>Self-assessment return &mdash; &pound;180</li>
<li>Limited company accounts &mdash; from &pound;950</li></ul>
<h2>What our clients say</h2>
<blockquote>Straight answers, no jargon. &mdash; Tom H.</blockquote>
<div class="trustpilot-widget" data-businessunit-id="abc"></div>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call 0131 555 0121.</p>
""" + _FOOT % ("Sable Accounting", "22 George St, Edinburgh EH2 2PF."),
     {"no_crm_signals", "no_live_chat"})

# ── 32. A French Ecwid shop running Crisp ──
_platform("french-ecwid-crisp", "https://cavesaintjulien.test/", """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cave Saint-Julien</title>
<script src="https://app.ecwid.com/script.js?store=12345" charset="utf-8"></script>
<script>window.$crisp=[];window.CRISP_WEBSITE_ID="ab-12-cd";</script>
<script src="https://client.crisp.chat/l.js" async></script>
</head><body>
<nav><a href="/">Accueil</a> <a href="/boutique/">Boutique</a>
<a href="https://www.facebook.com/cavesaintjulien/">Facebook</a></nav>
<h1>Cave Saint-Julien</h1>
<div id="my-store-12345"></div>
<p>Bordeaux 2019 &mdash; 24,00 &euro;. Sancerre 2021 &mdash; 19,50 &euro;.</p>
<p>T&eacute;l&eacute;phone 05 56 55 44 33.</p>
""" + _FOOT % ("Cave Saint-Julien", "8 cours du Chapeau Rouge, 33000 Bordeaux."),
     # `cart_no_recovery` was added to this label rather than to a rule: the
     # page carries a storefront, no mailing tool and no field asking for an
     # address anywhere, header to footer. That is the gap, and it was true of
     # this page before there was a code for it. Nothing here changed except
     # that the audit can now see it.
     {"ecommerce_manual", "cart_no_recovery", "no_analytics", "no_schema"})

# ── 33. A brochure whose only form is a Typeform embed ──
_platform("typeform-brochure", "https://calderstonelaw.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calderstone Law</title>
</head><body>
<h1>Calderstone Law</h1>
<h2>What we do</h2><ul><li>Wills and probate</li><li>Conveyancing</li>
<li>Employment advice</li></ul>
<iframe src="https://form.typeform.com/to/AbCdEf" width="100%" height="500"></iframe>
<p>Call 0151 555 0166.</p>
""" + _FOOT % ("Calderstone Law", "5 Castle St, Liverpool L2 4SW."),
     {"no_crm_signals", "no_live_chat", "no_analytics", "no_schema",
      "no_social_presence", "price_opaque"})

# ── 34. GoHighLevel: CRM, calendar and chat in one loader ──
_platform("gohighlevel-hvac", "https://polarisheating.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polaris Heating &amp; Cooling</title>
<script src="https://widgets.leadconnectorhq.com/loader.js"
data-resources-url="https://widgets.leadconnectorhq.com/chat-widget/loader.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-POLAR"></script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://api.leadconnectorhq.com/widget/booking/polaris">Book an appointment</a>
<a href="https://www.facebook.com/polarisheating/">Facebook</a></nav>
<h1>Polaris Heating &amp; Cooling</h1>
<h2>Our Services</h2><ul><li>Furnace repair</li><li>Air conditioning installation</li>
<li>Annual maintenance</li></ul>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (780) 555-0198.</p>
""" + _FOOT % ("Polaris Heating &amp; Cooling", "112 Jasper Ave, Edmonton, AB T5J 1W8."),
     {"no_schema", "price_opaque"})

# ── 35. Podium webchat and Housecall Pro booking ──
_platform("podium-housecall", "https://brightpathelectric.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brightpath Electric</title>
<script src="https://connect.podium.com/widget.js#ORG_TOKEN=abc" async></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-BRIGHT"></script>
</head><body>
<nav><a href="/">Home</a>
<a href="https://book.housecallpro.com/book/Brightpath-Electric/abc">Book online</a>
<a href="https://www.facebook.com/brightpathelectric/">Facebook</a></nav>
<h1>Brightpath Electric</h1>
<h2>Our Services</h2><ul><li>Panel upgrades</li><li>EV charger installation</li>
<li>Electrical inspection</li></ul>
<form action="/contact" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call (403) 555-0164.</p>
""" + _FOOT % ("Brightpath Electric", "700 4 Ave SW, Calgary, AB T2P 3J4."),
     {"no_crm_signals", "no_schema", "price_opaque"})

# ── 36. A cookie banner sitting on top of a real page ──
_platform("cookie-banner-real-page", "https://harlowdentalcare.test/", """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harlow Dental Care</title>
</head><body>
<div id="cookie-banner"><p>This website uses cookies to make sure you get the best
experience. <button>Accept all cookies</button> <button>Cookie settings</button></p></div>
<nav><a href="/">Home</a> <a href="/treatments/">Treatments</a>
<a href="/contact/">Contact</a></nav>
<h1>Harlow Dental Care</h1>
<h2>Our Treatments</h2><ul><li>Hygienist appointment</li><li>White fillings</li>
<li>Teeth whitening</li></ul>
<p>We have looked after families in Harlow for thirty years. New patients are welcome
and there is parking behind the practice.</p>
<form action="/contact/" method="post"><input type="text" name="name">
<input type="email" name="email"><textarea name="message"></textarea>
<button type="submit">Send</button></form>
<p>Call 01279 555 012.</p>
""" + _FOOT % ("Harlow Dental Care", "18 The High, Harlow CM20 1LN."),
     {"no_online_booking", "no_crm_signals", "no_live_chat", "no_analytics", "no_schema",
      "no_social_presence", "price_opaque"})


PLATFORM_CORPUS: tuple = tuple(_PLATFORM_CORPUS)

# The four codes added in the same pass as this corpus. Reported separately in
# the docstring below, because a rule that did not exist cannot have had a rate.
PLATFORM_NEW_CODES = frozenset({"no_ssl", "whatsapp_manual", "email_only_intake",
                                "no_review_capture"})


# ── A fourth corpus: whole crawls, not single pages ──

# Sixty-three pages across twenty-four sites, every label written down from
# reading the pages before a rule was run against them. The three corpora above
# are one page each, which is the shape that can only ever grade what a home
# page says: everything a business does badly *across* a site is invisible to
# them. The page headed Book Online that turns out to be a contact form, the
# quote form fourteen fields deep two clicks in, the services page listing
# twelve things with one form under all of them, the price list that is a PDF
# from 2019, the shop with nowhere at all to leave an email — five findings a
# crawl already had in memory and no rule had ever opened.
#
# A row is (name, base url, {url: html}, gaps, reason). `reason` is "" unless
# the honest answer is that nobody could read the site.
SITE_CORPUS: list = []


def _crawl(name, base, pages, gaps, reason=""):
    SITE_CORPUS.append((name, base, pages, frozenset(gaps), reason))


def _shead(title, extra=""):
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<title>%s</title>\n%s</head><body>\n' % (title, extra))


def _sfoot(name, addr, phone="(905) 555-0134"):
    return ('\n<footer><p>&copy; %d %s. %s Call %s</p></footer>\n</body></html>'
            % (TODAY.year, name, addr, phone))


def _asks(*names):
    """A form body of `names`, each rendered the way a builder renders it."""
    out = []
    for spec in names:
        kind, label = spec.split(":", 1)
        key = label.lower().replace(" ", "_")
        if kind == "t":
            out.append('<label>%s<input type="text" name="%s"></label>' % (label, key))
        elif kind == "e":
            out.append('<label>%s<input type="email" name="%s"></label>' % (label, key))
        elif kind == "p":
            out.append('<label>%s<input type="tel" name="%s"></label>' % (label, key))
        elif kind == "d":
            out.append('<label>%s<input type="date" name="%s"></label>' % (label, key))
        elif kind == "s":
            out.append('<label>%s<select name="%s"><option>Choose</option>'
                       '<option>Other</option></select></label>' % (label, key))
        elif kind == "a":
            out.append('<label>%s<textarea name="%s"></textarea></label>' % (label, key))
    return "".join(out)


# ── 1. WordPress + Elementor: the page headed Book Online is a contact form ──

_D = "https://bloorwestdental.test"
_dent_head = (
    '<meta name="generator" content="WordPress 6.5.2">\n'
    '<link rel="stylesheet" href="/wp-content/plugins/elementor/assets/css/frontend.min.css">\n'
    '<script src="/wp-includes/js/jquery/jquery.min.js"></script>\n'
    '<script async src="https://www.googletagmanager.com/gtag/js?id=G-7QK2L9"></script>\n'
    '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Dentist",'
    '"name":"Bloor West Dental"}</script>\n')
_dent_nav = ('<nav><a href="/">Home</a> <a href="/services/">Services</a> '
             '<a href="/book-online/">Book Online</a> <a href="/contact/">Contact</a></nav>\n')
_dent_addr = "2180 Bloor St W, Toronto, ON M6S 1N3."

_crawl("wp-elementor-dentist", _D + "/", {
    _D + "/": _shead("Bloor West Dental", _dent_head) + _dent_nav + """
<h1>Bloor West Dental</h1>
<p>A family practice on Bloor Street since 1996. New patients are welcome and there
is parking behind the building.</p>
<p><a href="https://www.facebook.com/bloorwestdental/">Facebook</a></p>
""" + _sfoot("Bloor West Dental", _dent_addr),
    _D + "/services/": _shead("Services", _dent_head) + _dent_nav + """
<h1>What we look after</h1>
<h2>Our Services</h2>
<ul><li>Dental cleaning and check-ups</li><li>White fillings</li>
<li>Root canal treatment</li><li>Teeth whitening</li>
<li>Emergency dental repair</li><li>Dentures and repairs</li></ul>
""" + _sfoot("Bloor West Dental", _dent_addr),
    _D + "/book-online/": _shead("Book Online", _dent_head) + _dent_nav + """
<h1>Book Online</h1>
<p>Tell us when suits you and the front desk will call you back to confirm the time.</p>
<form action="/book-online/" method="post">
<input type="text" name="name"><input type="email" name="email">
<input type="tel" name="phone"><textarea name="message"></textarea>
<button type="submit">Request a time</button></form>
""" + _sfoot("Bloor West Dental", _dent_addr),
    _D + "/contact/": _shead("Contact", _dent_head) + _dent_nav + """
<h1>Contact the practice</h1>
<p>2180 Bloor St W, Toronto. Open Monday to Friday.</p>
""" + _LEAD_FORM + _sfoot("Bloor West Dental", _dent_addr),
}, {"no_online_booking", "no_crm_signals", "no_live_chat", "price_opaque"})


# ── 2. WordPress + Divi: a thirteen-field quote form and a careers mailto ──

_R = "https://summitroofing.test"
_roof_head = ('<link rel="stylesheet" href="/wp-content/themes/Divi/style.css">\n'
              '<script src="/wp-content/themes/Divi/js/custom.js"></script>\n')
_roof_nav = ('<nav><a href="/">Home</a> <a href="/quote/">Get a free quote</a> '
             '<a href="/careers/">Careers</a></nav>\n')
_roof_addr = "44 Mill Street, Guelph, ON N1H 2A9."

_crawl("wp-divi-roofer", _R + "/", {
    _R + "/": _shead("Summit Roofing", _roof_head) + '<div class="et_pb_section">' + _roof_nav + """
<h1>Summit Roofing</h1>
<h2>What we do</h2>
<ul><li>Roof replacement</li><li>Flat roofing</li><li>Emergency repairs</li>
<li>Gutter cleaning</li></ul>
<p>Twenty-two years on roofs across the county.</p></div>
""" + _sfoot("Summit Roofing", _roof_addr),
    _R + "/quote/": _shead("Get a free quote", _roof_head) + _roof_nav + """
<h1>Get a free quote</h1>
<p>Fill this in and we will price the job.</p>
<form action="/quote/" method="post">""" + _asks(
        "t:Your name", "e:Email", "p:Telephone", "t:Street address", "t:Town",
        "t:Postcode", "s:Roof type", "s:Roof age", "t:Approximate area",
        "d:Preferred date", "s:Budget", "s:How did you hear about us",
        "a:Tell us about the job") + """
<button type="submit">Send</button></form>
""" + _sfoot("Summit Roofing", _roof_addr),
    _R + "/careers/": _shead("Careers", _roof_head) + _roof_nav + """
<h1>Careers</h1>
<h2>Current openings</h2>
<ul><li>Roofer, full time, year round</li><li>Crew lead, five years on the tools</li></ul>
<p>Send your CV to <a href="mailto:jobs@summitroofing.test">jobs@summitroofing.test</a>
and we will call you in for a chat.</p>
""" + _sfoot("Summit Roofing", _roof_addr),
}, {"no_online_booking", "no_crm_signals", "quote_by_form", "long_intake_form",
    "careers_manual", "no_live_chat", "no_analytics", "no_schema",
    "no_social_presence", "price_opaque"})


# ── 3. Wix, with Wix Bookings, Wix Chat and a price beside every service ──

_W = "https://bloomhair.test"
_wix_head = ('<meta name="generator" content="Wix.com Website Builder">\n'
             '<script src="https://static.parastorage.com/services/wix-thunderbolt/dist/main.js">'
             '</script>\n'
             '<script>window.wixVisitorChat={};/* wix-visitor-chat */</script>\n'
             '<script src="https://static.wixstatic.com/tag-manager-client.js"></script>\n')
_wix_nav = ('<nav><a href="/">Home</a> <a href="/services">Services</a> '
            '<a href="https://bookings.wixapps.net/bloomhair">Book now</a> '
            '<a href="/contact">Contact</a></nav>\n')
_wix_addr = "18 Queen Street West, Toronto, ON M5H 3S5."

_crawl("wix-salon", _W + "/", {
    _W + "/": _shead("Bloom Hair Studio", _wix_head) + _wix_nav + """
<h1>Bloom Hair Studio</h1>
<p>A six-chair studio off Queen Street. Colour, cutting and bridal work.</p>
<p><a href="https://www.instagram.com/bloomhairstudio/">Instagram</a></p>
""" + _sfoot("Bloom Hair Studio", _wix_addr),
    _W + "/services": _shead("Services", _wix_head) + _wix_nav + """
<h1>Price list</h1>
<h2>Our Services</h2>
<ul><li>Cut and blow dry &mdash; $65</li><li>Balayage &mdash; from $180</li>
<li>Keratin treatment &mdash; $220</li><li>Colour correction &mdash; from $250</li>
<li>Bridal styling &mdash; $150</li></ul>
""" + _sfoot("Bloom Hair Studio", _wix_addr),
    _W + "/contact": _shead("Contact", _wix_head) + _wix_nav + """
<h1>Find us</h1>
<p>Two minutes from Osgoode station.</p>
""" + _LEAD_FORM + _sfoot("Bloom Hair Studio", _wix_addr),
}, {"no_crm_signals", "no_schema"})


# ── 4. Squarespace with Acuity, Mailchimp and Google Analytics ──

_Y = "https://riverbendyoga.test"
_yoga_head = ('<link rel="stylesheet" href="https://static1.squarespace.com/static/site.css">\n'
              '<script src="https://assets.squarespace.com/universal/scripts.js"></script>\n'
              '<script async src="https://www.googletagmanager.com/gtag/js?id=G-88AA1"></script>\n'
              '<script src="//riverbend.us18.list-manage.com/subscribe/post.js"></script>\n')
_yoga_nav = ('<nav><a href="/">Home</a> <a href="/schedule">Schedule</a> '
             '<a href="/blog">Blog</a></nav>\n')
_yoga_addr = "301 Water Street, Guelph, ON N1G 1A7."
_RECENT_POST = (TODAY - datetime.timedelta(days=20))
_MAILCHIMP_FORM = ('<form action="https://riverbend.us18.list-manage.com/subscribe/post" '
              'method="post"><input type="email" name="EMAIL" placeholder="Your email">'
              '<button type="submit">Join the list</button></form>')

_crawl("squarespace-yoga", _Y + "/", {
    _Y + "/": _shead("Riverbend Yoga", _yoga_head) + _yoga_nav + """
<h1>Riverbend Yoga</h1>
<p>Drop-in class $22, ten-class pass $180, unlimited month $145.</p>
<p><a href="https://app.acuityscheduling.com/schedule.php?owner=8871">Reserve a mat</a></p>
<p><a href="https://www.instagram.com/riverbendyoga/">Instagram</a></p>
""" + _MAILCHIMP_FORM + _sfoot("Riverbend Yoga", _yoga_addr),
    _Y + "/schedule": _shead("Schedule", _yoga_head) + _yoga_nav + """
<h1>This week</h1>
<p>Morning flow, lunchtime restore and an evening beginners class.</p>
<iframe src="https://app.acuityscheduling.com/schedule.php?owner=8871"></iframe>
""" + _sfoot("Riverbend Yoga", _yoga_addr),
    _Y + "/blog": _shead("Blog", _yoga_head) + _yoga_nav + """
<h1>Blog</h1>
<article><h2>What to bring to your first class</h2>
<time datetime="%s">%s</time>
<p>A mat, water and nothing else. We keep spare blocks at the studio.</p></article>
""" % (_RECENT_POST.isoformat(), _RECENT_POST.strftime("%B %d, %Y")) + _sfoot("Riverbend Yoga", _yoga_addr),
}, {"no_live_chat", "no_schema"})


# ── 5. Shopify: a checkout, a chat box and nowhere at all to leave an email ──

_S = "https://northsidesupply.test"
_shop_head = ('<script>var Shopify=Shopify||{};Shopify.theme={"name":"Dawn","id":9};</script>\n'
              '<link href="https://cdn.shopify.com/s/files/1/0001/theme.css" rel="stylesheet">\n'
              '<script src="https://cdn.shopify.com/shopifycloud/web-pixels-manager/v1.js">'
              '</script>\n'
              '<script src="https://cdn.shopify.com/shopifycloud/chat/assets/chat.js"></script>\n')
_shop_nav = ('<nav><a href="/">Home</a> <a href="/collections/all">Shop</a> '
             '<a href="/pages/contact">Contact</a></nav>\n')
_shop_addr = "77 Barton Street East, Hamilton, ON L8L 2W9."
_CART_FORM = ('<form action="/cart/add" method="post"><input type="hidden" name="id" value="1">'
         '<button type="submit">Add to cart</button></form>')

_crawl("shopify-workwear", _S + "/", {
    _S + "/": _shead("Northside Supply", _shop_head) + _shop_nav + """
<h1>Northside Supply</h1>
<p>Workwear and hand tools, shipped from Hamilton.</p>
<p>Boots from $129.00, jackets $189.00, gloves $24.99.</p>
""" + _CART_FORM + _sfoot("Northside Supply", _shop_addr),
    _S + "/collections/all": _shead("Shop", _shop_head) + _shop_nav + """
<h1>Everything</h1>
<ul><li>Steel toe boots &mdash; $129.00</li><li>Insulated jacket &mdash; $189.00</li>
<li>Rigger gloves &mdash; $24.99</li></ul>
""" + _CART_FORM + _sfoot("Northside Supply", _shop_addr),
    _S + "/pages/contact": _shead("Contact", _shop_head) + _shop_nav + """
<h1>Talk to us</h1>
<p>The counter is open weekdays. Write to
<a href="mailto:orders@northsidesupply.test">orders@northsidesupply.test</a>
and we will come back the same day.</p>
""" + _sfoot("Northside Supply", _shop_addr),
}, {"ecommerce_manual", "cart_no_recovery", "email_only_intake", "no_schema",
    "no_social_presence"})


# ── 6. Webflow agency: twelve services on one page and one form under all of them ──

_A = "https://northfieldstudio.test"
_agy_head = ('<script src="https://assets.website-files.com/webflow.js" data-wf-site="abc">'
             '</script>\n'
             '<script src="https://js.hs-scripts.com/4455661.js"></script>\n'
             '<script type="application/ld+json">{"@context":"https://schema.org",'
             '"@type":"Organization","name":"Northfield Studio"}</script>\n')
_agy_nav = ('<nav><a href="/">Home</a> <a href="/what-we-do/">What we do</a> '
            '<a href="/work/">Work</a> <a href="/contact/">Contact</a></nav>\n')
_agy_addr = "5 Camden Row, Toronto, ON M5V 2K4."

_crawl("webflow-agency", _A + "/", {
    _A + "/": _shead("Northfield Studio", _agy_head) + _agy_nav + """
<h1>Northfield Studio</h1>
<p>A studio of nine working with challenger brands.</p>
<p><a href="https://www.linkedin.com/company/northfieldstudio/">LinkedIn</a></p>
""" + _sfoot("Northfield Studio", _agy_addr),
    _A + "/what-we-do/": _shead("What we do", _agy_head) + _agy_nav + """
<h1>How we can help</h1>
<h2>What we do</h2>
<ul><li>Brand strategy</li><li>Visual identity</li><li>Web design</li>
<li>Web development</li><li>Motion graphics</li><li>Content production</li>
<li>Copywriting</li><li>Photography</li><li>Paid social</li>
<li>Search marketing</li><li>Email marketing</li><li>Analytics and reporting</li></ul>
<p>Tell us what you have in mind and we will come back with a plan.</p>
""" + _sfoot("Northfield Studio", _agy_addr),
    _A + "/work/": _shead("Work", _agy_head) + _agy_nav + """
<h1>Selected work</h1>
<p>Nine years of brand and product work for food, finance and fitness.</p>
""" + _sfoot("Northfield Studio", _agy_addr),
    _A + "/contact/": _shead("Contact", _agy_head) + _agy_nav + """
<h1>Start something</h1>
""" + _LEAD_FORM + _sfoot("Northfield Studio", _agy_addr),
}, {"services_no_route", "price_opaque"})


# ── 7. GoDaddy one-page brochure: an address and a telephone, nothing else ──

_G = "https://ashworthsurveyors.test"
_crawl("godaddy-brochure", _G + "/", {
    _G + "/": _shead("Ashworth Chartered Surveyors",
                    '<script src="https://img1.wsimg.com/site/builder.js"></script>\n') + """
<h1>Ashworth Chartered Surveyors</h1>
<p>Four decades of party wall, boundary and dilapidations work across the county.</p>
<p>Telephone the practice and ask for Mr Ashworth, or write to
<a href="mailto:office@ashworthsurveyors.test">office@ashworthsurveyors.test</a>.</p>
""" + _sfoot("Ashworth Chartered Surveyors", "9 Bridge Street, Chester CH1 1NN.",
            "01244 555 012"),
}, {"no_lead_capture", "email_only_intake", "no_live_chat", "no_analytics",
    "no_schema", "no_social_presence", "price_opaque"})


# ── 8. Duda: a three-branch physiotherapy chain ──

_P = "https://lakeshorephysio.test"
_phys_head = ('<script src="https://irp.cdn-website.com/runtime.js"></script>\n'
              '<script async src="https://www.googletagmanager.com/gtag/js?id=G-4455"></script>\n')
_phys_nav = ('<nav><a href="/">Home</a> <a href="/locations/">Locations</a> '
             '<a href="/contact/">Contact</a></nav>\n')
_phys_addr = "120 Lakeshore Road, Oakville, ON L6K 1E3."

_crawl("duda-physio-chain", _P + "/", {
    _P + "/": _shead("Lakeshore Physiotherapy", _phys_head) + _phys_nav + """
<h1>Lakeshore Physiotherapy</h1>
<h2>Treatments</h2>
<ul><li>Sports injury treatment</li><li>Manual therapy</li>
<li>Post-surgical rehabilitation</li></ul>
""" + _sfoot("Lakeshore Physiotherapy", _phys_addr),
    _P + "/locations/": _shead("Locations", _phys_head) + _phys_nav + """
<h1>Three clinics</h1>
<address>120 Lakeshore Road, Oakville, ON L6K 1E3</address>
<address>88 Dundas Street, Burlington, ON L7R 3N4</address>
<address>15 Main Street North, Milton, ON L9T 1N1</address>
""" + _sfoot("Lakeshore Physiotherapy", _phys_addr),
    _P + "/contact/": _shead("Contact", _phys_head) + _phys_nav + """
<h1>Get in touch</h1>
<p>Send us a note and the clinic nearest you will answer.</p>
""" + _LEAD_FORM + _sfoot("Lakeshore Physiotherapy", _phys_addr),
}, {"no_online_booking", "no_crm_signals", "multi_location", "no_live_chat",
    "no_schema", "no_social_presence", "price_opaque"})


# ── 9. Framer ──

_F = "https://meridiandesign.test"
_fr_head = ('<meta name="generator" content="Framer 2f8a">\n'
            '<script src="https://framerusercontent.com/sites/9aB/script_main.js"></script>\n')
_fr_nav = ('<nav><a href="/">Home</a> <a href="/work/">Work</a> '
           '<a href="/contact/">Contact</a></nav>\n')
_fr_addr = "22 Ossington Avenue, Toronto, ON M6J 2Y7."

_crawl("framer-design-studio", _F + "/", {
    _F + "/": _shead("Meridian Design", _fr_head) + '<div data-framer-name="Hero">' + _fr_nav + """
<h1>Meridian Design</h1>
<h2>What we do</h2>
<ul><li>Product design</li><li>Design systems</li><li>Prototyping</li></ul></div>
""" + _sfoot("Meridian Design", _fr_addr),
    _F + "/work/": _shead("Work", _fr_head) + _fr_nav + """
<h1>Work</h1>
<p>Six years of interface work for software teams in Toronto and Berlin.</p>
""" + _sfoot("Meridian Design", _fr_addr),
    _F + "/contact/": _shead("Contact", _fr_head) + _fr_nav + """
<h1>Say hello</h1>
""" + _LEAD_FORM + _sfoot("Meridian Design", _fr_addr),
}, {"no_crm_signals", "no_live_chat", "no_analytics", "no_schema",
    "no_social_presence", "price_opaque"})


# ── 10. An AMP page ──

_M = "https://trattoriabella.test"
_amp_head = ('<script async src="https://cdn.ampproject.org/v0.js"></script>\n'
             '<script async custom-element="amp-analytics" '
             'src="https://cdn.ampproject.org/v0/amp-analytics-0.1.js"></script>\n'
             '<script type="application/ld+json">{"@context":"https://schema.org",'
             '"@type":"Restaurant","name":"Trattoria Bella"}</script>\n')
_amp_nav = '<nav><a href="/">Home</a> <a href="/menu/">Menu</a></nav>\n'
_amp_tag = ('<amp-analytics type="gtag" data-credentials="include">'
            '<script type="application/json">{"vars":{"gtag_id":"G-9911"}}</script>'
            '</amp-analytics>\n')
_amp_addr = "410 College Street, Toronto, ON M5T 1T3."

_crawl("amp-restaurant", _M + "/", {
    _M + "/": _shead("Trattoria Bella", _amp_head) + _amp_tag + _amp_nav + """
<h1>Trattoria Bella</h1>
<p>Reservations by telephone only. Call the restaurant and we will hold a table.</p>
<form method="post" action-xhr="/enquiry"><input type="email" name="email">
<textarea name="message"></textarea><input type="submit" value="Send"></form>
""" + _sfoot("Trattoria Bella", _amp_addr),
    _M + "/menu/": _shead("Menu", _amp_head) + _amp_tag + _amp_nav + """
<h1>Menu</h1>
<ul><li>Antipasti &mdash; $12.00</li><li>Pasta &mdash; $22.00</li>
<li>Secondi &mdash; $31.00</li></ul>
""" + _sfoot("Trattoria Bella", _amp_addr),
}, {"no_online_booking", "no_crm_signals", "no_live_chat", "no_social_presence"})


# ── 11-13. Three crawls nobody could read ──

_C = "https://claremontjoinery.test"
_crawl("cloudflare-challenge", _C + "/", {
    _C + "/": """<!doctype html><html><head><title>Just a moment...</title>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"></script></head>
<body class="no-js"><div id="cf-wrapper"><h1>Checking your browser before accessing
claremontjoinery.test</h1><p>Please enable JavaScript and cookies to continue.</p>
<p>Ray ID: 8a1f2c</p></div></body></html>""",
}, set(), "challenge")

_N = "https://vantageclinic.test"
_crawl("nextjs-shell", _N + "/", {
    _N + "/": """<!doctype html><html><head><title>Vantage Clinic</title>
<link rel="preload" href="/_next/static/chunks/main.js" as="script"></head>
<body><div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>
<script src="/_next/static/chunks/main.js"></script></body></html>""",
}, set(), "js_only")

_K = "https://dekruidenier.test"
_crawl("dutch-cookie-wall", _K + "/", {
    _K + "/": """<!doctype html><html lang="nl"><head><title>De Kruidenier</title></head>
<body><div class="consent"><h1>Wij gebruiken cookies</h1>
<p>Wij en onze partners plaatsen cookies om de website te verbeteren. U kunt uw
toestemming altijd intrekken.</p><button>Accepteer alle cookies</button>
<button>Instellingen</button></div></body></html>""",
}, set(), "cookie_wall")


# ── 14. A French plumber whose price list is a PDF from 2019 ──

_FR = "https://plomberiemartin.test"
_fr2_head = '<link rel="stylesheet" href="/wp-content/themes/artisan/style.css">\n'
_fr2_nav = ('<nav><a href="/">Accueil</a> <a href="/nos-services/">Nos services</a> '
            '<a href="/contact/">Contact</a></nav>\n')
_fr2_addr = "14 rue des Lilas, Lyon."

_crawl("fr-plombier", _FR + "/", {
    _FR + "/": _shead("Plomberie Martin", _fr2_head) + _fr2_nav + """
<h1>Plomberie Martin</h1>
<p>Artisan plombier à Lyon depuis 1998. Intervention rapide sur toute la métropole.</p>
<p><a href="/media/tarifs-2019.pdf">Nos tarifs</a></p>
""" + _sfoot("Plomberie Martin", _fr2_addr, "04 78 55 01 34"),
    _FR + "/nos-services/": _shead("Nos services", _fr2_head) + _fr2_nav + """
<h1>Ce que nous faisons</h1>
<h2>Nos services</h2>
<ul><li>Dépannage plomberie</li><li>Installation chauffe-eau</li>
<li>Recherche de fuite</li><li>Rénovation salle de bain</li></ul>
""" + _sfoot("Plomberie Martin", _fr2_addr, "04 78 55 01 34"),
    _FR + "/contact/": _shead("Contact", _fr2_head) + _fr2_nav + """
<h1>Nous écrire</h1>
""" + _LEAD_FORM + _sfoot("Plomberie Martin", _fr2_addr, "04 78 55 01 34"),
}, {"no_online_booking", "no_crm_signals", "dated_document", "no_live_chat",
    "no_analytics", "no_schema", "no_social_presence"})


# ── 15. A German dentist on plain http with no mobile layout ──

_DE = "http://zahnarzt-baumgartner.test"
_de_head = ('<script src="/media/system/js/core.js"></script>\n'
            '<meta name="generator" content="Joomla! - Open Source Content Management">\n')
_de_nav = ('<nav><a href="/">Startseite</a> <a href="/leistungen/">Leistungen</a> '
           '<a href="/kontakt/">Kontakt</a></nav>\n')
_de_addr = "Hauptstrasse 14, Berlin."


def _de_page(title, body):
    return ('<!doctype html>\n<html lang="de"><head><meta charset="utf-8">\n'
            '<title>%s</title>\n%s</head><body>\n%s%s' %
            (title, _de_head, _de_nav + body,
             _sfoot("Zahnarztpraxis Baumgartner", _de_addr, "030 555 0134")))


_crawl("de-zahnarzt-http", _DE + "/", {
    _DE + "/": _de_page("Zahnarztpraxis Baumgartner", """
<h1>Zahnarztpraxis Baumgartner</h1>
<p>Ihre Praxis in Berlin Mitte. Wir freuen uns auf neue Patienten.</p>
<p><a href="https://www.doctolib.de/zahnarzt/berlin/baumgartner">Termin online buchen</a></p>
"""),
    _DE + "/leistungen/": _de_page("Leistungen", """
<h1>Was wir anbieten</h1>
<h2>Unsere Leistungen</h2>
<ul><li>Professionelle Zahnreinigung</li><li>Implantologie</li>
<li>Bleaching</li><li>Kinderzahnheilkunde</li></ul>
"""),
    _DE + "/kontakt/": _de_page("Kontakt", """
<h1>Kontakt</h1>
""" + _LEAD_FORM),
}, {"no_ssl", "no_mobile", "no_crm_signals", "no_live_chat", "no_analytics",
    "no_schema", "no_social_presence", "price_opaque"})


# ── 16. A Spanish clinic with a WhatsApp button ──

_ES = "https://clinicasanmarcos.test"
_es_head = ('<link rel="stylesheet" href="/wp-content/themes/clinica/style.css">\n'
            '<script async src="https://www.googletagmanager.com/gtag/js?id=G-2211"></script>\n')
_es_nav = ('<nav><a href="/">Inicio</a> <a href="/servicios/">Servicios</a> '
           '<a href="/contacto/">Contacto</a></nav>\n')
_es_addr = "Calle Mayor 8, Valencia."

_crawl("es-clinica-whatsapp", _ES + "/", {
    _ES + "/": _shead("Clínica San Marcos", _es_head) + _es_nav + """
<h1>Clínica San Marcos</h1>
<p>Odontología familiar en el centro de Valencia.</p>
<p><a href="https://wa.me/34600111222">Escríbenos por WhatsApp</a></p>
<p><a href="https://www.pedircitaonline.test/sanmarcos">Pedir cita online</a></p>
""" + _sfoot("Clínica San Marcos", _es_addr, "961 55 01 34"),
    _ES + "/servicios/": _shead("Servicios", _es_head) + _es_nav + """
<h1>Tratamientos</h1>
<h2>Nuestros servicios</h2>
<ul><li>Limpieza dental</li><li>Ortodoncia invisible</li>
<li>Implantes dentales</li><li>Blanqueamiento</li></ul>
""" + _sfoot("Clínica San Marcos", _es_addr, "961 55 01 34"),
    _ES + "/contacto/": _shead("Contacto", _es_head) + _es_nav + """
<h1>Escríbenos</h1>
""" + _LEAD_FORM + _sfoot("Clínica San Marcos", _es_addr, "961 55 01 34"),
}, {"whatsapp_manual", "no_crm_signals", "no_live_chat", "no_schema",
    "no_social_presence", "price_opaque"})


# ── 17. A Dutch dentist whose Afspraak maken page is a telephone number ──

_NL = "https://tandartsdeboer.test"
_nl_head = ('<link rel="stylesheet" href="/wp-content/themes/praktijk/style.css">\n'
            '<script type="application/ld+json">{"@context":"https://schema.org",'
            '"@type":"Dentist","name":"Tandartspraktijk De Boer"}</script>\n')
_nl_nav = ('<nav><a href="/">Home</a> <a href="/afspraak-maken/">Afspraak maken</a> '
           '<a href="/contact/">Contact</a></nav>\n')
_nl_addr = "Keizersgracht 210, Amsterdam."

_crawl("nl-tandarts", _NL + "/", {
    _NL + "/": _shead("Tandartspraktijk De Boer", _nl_head) + _nl_nav + """
<h1>Tandartspraktijk De Boer</h1>
<p>Een kleine praktijk aan de gracht. Nieuwe patiënten zijn welkom.</p>
""" + _sfoot("Tandartspraktijk De Boer", _nl_addr, "020 555 0134"),
    _NL + "/afspraak-maken/": _shead("Afspraak maken", _nl_head) + _nl_nav + """
<h1>Afspraak maken</h1>
<p>Bel ons op 020 555 0134 en wij plannen uw afspraak in. Tussen negen en vijf staat
er altijd iemand aan de balie.</p>
""" + _sfoot("Tandartspraktijk De Boer", _nl_addr, "020 555 0134"),
    _NL + "/contact/": _shead("Contact", _nl_head) + _nl_nav + """
<h1>Contact</h1>
""" + _LEAD_FORM + _sfoot("Tandartspraktijk De Boer", _nl_addr, "020 555 0134"),
}, {"no_online_booking", "no_crm_signals", "no_live_chat", "no_analytics",
    "no_social_presence", "price_opaque"})


# ── 18. A four-branch German optician whose addresses live in JSON-LD ──

_OP = "https://optikwerner.test"
_op_schema = ('<script type="application/ld+json">{"@context":"https://schema.org",'
              '"@type":"Optician","name":"Optik Werner","location":['
              '{"@type":"PostalAddress","streetAddress":"Marktplatz 3",'
              '"postalCode":"70173","addressLocality":"Stuttgart"},'
              '{"@type":"PostalAddress","streetAddress":"Bahnhofstrasse 9",'
              '"postalCode":"71032","addressLocality":"Boeblingen"},'
              '{"@type":"PostalAddress","streetAddress":"Hauptstrasse 44",'
              '"postalCode":"73728","addressLocality":"Esslingen"},'
              '{"@type":"PostalAddress","streetAddress":"Koenigstrasse 12",'
              '"postalCode":"70563","addressLocality":"Vaihingen"}]}</script>\n')
_op_nav = ('<nav><a href="/">Startseite</a> <a href="/filialen/">Filialen</a> '
           '<a href="/kontakt/">Kontakt</a></nav>\n')
_op_addr = "Marktplatz 3, Stuttgart."

_crawl("de-optiker-chain", _OP + "/", {
    _OP + "/": _shead("Optik Werner", _op_schema) + _op_nav + """
<h1>Optik Werner</h1>
<p>Vier Filialen in der Region Stuttgart. Meisterbetrieb seit 1974.</p>
""" + _sfoot("Optik Werner", _op_addr, "0711 555 0134"),
    _OP + "/filialen/": _shead("Filialen", _op_schema) + _op_nav + """
<h1>Unsere Filialen</h1>
<p>Stuttgart, Böblingen, Esslingen und Vaihingen.</p>
""" + _sfoot("Optik Werner", _op_addr, "0711 555 0134"),
    _OP + "/kontakt/": _shead("Kontakt", _op_schema) + _op_nav + """
<h1>Kontakt</h1>
""" + _LEAD_FORM + _sfoot("Optik Werner", _op_addr, "0711 555 0134"),
}, {"no_online_booking", "no_crm_signals", "multi_location", "no_live_chat",
    "no_analytics", "no_social_presence", "price_opaque"})


# ── 19. A WooCommerce bakery that does collect email addresses ──

_B = "https://mapleleafbakery.test"
_bk_head = ('<link rel="stylesheet" href="/wp-content/plugins/woocommerce/assets/css/woo.css">\n'
            '<script>var wc_add_to_cart_params={"ajax_url":"/?wc-ajax=x"};</script>\n'
            '<script src="//maple.us4.list-manage.com/subscribe/post.js"></script>\n'
            '<script async src="https://www.googletagmanager.com/gtag/js?id=G-3311"></script>\n')
_bk_nav = ('<nav><a href="/">Home</a> <a href="/shop/">Shop</a> '
           '<a href="/contact/">Contact</a></nav>\n')
_bk_addr = "62 Locke Street South, Hamilton, ON L8P 4A8."

_crawl("woocommerce-bakery", _B + "/", {
    _B + "/": _shead("Maple Leaf Bakery", _bk_head) + _bk_nav + """
<h1>Maple Leaf Bakery</h1>
<p>Sourdough, rye and pastry, baked overnight on Locke Street.</p>
<p>Sourdough loaf $8.50, rye $9.00, croissant $4.25.</p>
<form action="/?wc-ajax=add_to_cart"><button type="submit">Add to cart</button></form>
""" + _sfoot("Maple Leaf Bakery", _bk_addr),
    _B + "/shop/": _shead("Shop", _bk_head) + _bk_nav + """
<h1>Order online</h1>
<ul><li>Sourdough loaf &mdash; $8.50</li><li>Rye &mdash; $9.00</li>
<li>Croissant &mdash; $4.25</li></ul>
""" + _sfoot("Maple Leaf Bakery", _bk_addr),
    _B + "/contact/": _shead("Contact", _bk_head) + _bk_nav + """
<h1>Say hello</h1>
""" + _LEAD_FORM + _sfoot("Maple Leaf Bakery", _bk_addr),
}, {"ecommerce_manual", "no_live_chat", "no_schema", "no_social_presence"})


# ── 20. A chiropractor with a fourteen-field intake form and a PDF of the same ──

_CH = "https://bayviewchiro.test"
_ch_head = '<link rel="stylesheet" href="/wp-content/themes/clinic/style.css">\n'
_ch_nav = ('<nav><a href="/">Home</a> <a href="/services/">Services</a> '
           '<a href="/new-patient/">New patients</a></nav>\n')
_ch_addr = "31 Bayview Avenue, Toronto, ON M4W 2G8."

_crawl("chiro-long-intake", _CH + "/", {
    _CH + "/": _shead("Bayview Family Chiropractic", _ch_head) + _ch_nav + """
<h1>Bayview Family Chiropractic</h1>
<p>Three practitioners, open six days.</p>
""" + _sfoot("Bayview Family Chiropractic", _ch_addr),
    _CH + "/services/": _shead("Services", _ch_head) + _ch_nav + """
<h1>What we treat</h1>
<h2>Our Services</h2>
<ul><li>Chiropractic adjustment</li><li>Massage therapy</li>
<li>Custom orthotics</li><li>Sports rehabilitation</li></ul>
""" + _sfoot("Bayview Family Chiropractic", _ch_addr),
    _CH + "/new-patient/": _shead("New patients", _ch_head) + _ch_nav + """
<h1>Before your first visit</h1>
<p>Please <a href="/forms/new-patient-form.pdf">download the new patient form</a>
and bring it with you, or fill it in here.</p>
<form action="/new-patient/" method="post">""" + _asks(
        "t:First name", "t:Last name", "e:Email", "p:Telephone", "d:Date of birth",
        "t:Street address", "t:City", "t:Postal code", "s:How did you hear about us",
        "t:Family doctor", "t:Insurance provider", "t:Policy number",
        "a:Reason for your visit", "a:Previous injuries") + """
<button type="submit">Send</button></form>
""" + _sfoot("Bayview Family Chiropractic", _ch_addr),
}, {"no_online_booking", "no_crm_signals", "long_intake_form", "pdf_forms",
    "no_live_chat", "no_analytics", "no_schema", "no_social_presence", "price_opaque"})


# ── 21. A law firm listing twelve practice areas with one form under them ──

_L = "https://hendersonlaw.test"
_law_head = '<link rel="stylesheet" href="/wp-content/themes/firm/style.css">\n'
_law_nav = ('<nav><a href="/">Home</a> <a href="/practice-areas/">Practice areas</a> '
            '<a href="/contact/">Contact</a></nav>\n')
_law_addr = "200 King Street West, Kitchener, ON N2G 1B2."

_crawl("law-firm-services-list", _L + "/", {
    _L + "/": _shead("Henderson Law", _law_head) + _law_nav + """
<h1>Henderson Law</h1>
<p>A four-partner firm in Kitchener, in practice since 1988.</p>
""" + _sfoot("Henderson Law", _law_addr),
    _L + "/practice-areas/": _shead("Practice areas", _law_head) + _law_nav + """
<h1>Practice areas</h1>
<h2>Our services</h2>
<ul><li>Wills and estates</li><li>Estate administration</li><li>Family law</li>
<li>Separation agreements</li><li>Residential real estate</li>
<li>Commercial real estate</li><li>Corporate formation</li>
<li>Shareholder agreements</li><li>Employment law</li><li>Civil litigation</li>
<li>Landlord and tenant</li><li>Notary services</li></ul>
<p>Write to us and one of the partners will come back to you.</p>
""" + _sfoot("Henderson Law", _law_addr),
    _L + "/contact/": _shead("Contact", _law_head) + _law_nav + """
<h1>Contact</h1>
""" + _LEAD_FORM + _sfoot("Henderson Law", _law_addr),
}, {"services_no_route", "no_crm_signals", "no_live_chat", "no_analytics",
    "no_schema", "no_social_presence", "price_opaque"})


# ── 22. The same shape with a page behind every service ──

_H = "https://clearviewhvac.test"
_hv_head = '<link rel="stylesheet" href="/wp-content/themes/trade/style.css">\n'
_hv_nav = ('<nav><a href="/">Home</a> <a href="/services/">Services</a> '
           '<a href="/contact/">Contact</a></nav>\n')
_hv_addr = "8 Industrial Road, Barrie, ON L4N 8Z4."

_crawl("hvac-linked-services", _H + "/", {
    _H + "/": _shead("Clearview Heating and Cooling", _hv_head) + _hv_nav + """
<h1>Clearview Heating and Cooling</h1>
<p>Furnaces, air conditioning and ductwork across Simcoe County.</p>
""" + _sfoot("Clearview Heating and Cooling", _hv_addr),
    _H + "/services/": _shead("Services", _hv_head) + _hv_nav + """
<h1>What we do</h1>
<h2>Our Services</h2>
<ul><li><a href="/services/furnace-repair/">Furnace repair</a></li>
<li><a href="/services/furnace-installation/">Furnace installation</a></li>
<li><a href="/services/air-conditioning/">Air conditioning</a></li>
<li><a href="/services/heat-pumps/">Heat pumps</a></li>
<li><a href="/services/duct-cleaning/">Duct cleaning</a></li>
<li><a href="/services/water-heaters/">Water heaters</a></li>
<li><a href="/services/maintenance-plans/">Maintenance plans</a></li>
<li><a href="/services/indoor-air-quality/">Indoor air quality</a></li>
<li><a href="/services/emergency-service/">Emergency service</a></li></ul>
""" + _sfoot("Clearview Heating and Cooling", _hv_addr),
    _H + "/contact/": _shead("Contact", _hv_head) + _hv_nav + """
<h1>Book a visit with us</h1>
""" + _LEAD_FORM + _sfoot("Clearview Heating and Cooling", _hv_addr),
}, {"no_online_booking", "no_crm_signals", "no_live_chat", "no_analytics",
    "no_schema", "no_social_presence", "price_opaque"})


# ── 23. A quote form one field under the line ──

_LS = "https://meadowlandscape.test"
_ls_head = '<link rel="stylesheet" href="/wp-content/themes/garden/style.css">\n'
_ls_nav = ('<nav><a href="/">Home</a> <a href="/quote/">Request a quote</a></nav>\n')
_ls_addr = "77 Concession Road, Ancaster, ON L9G 3K9."

_crawl("landscaper-short-quote", _LS + "/", {
    _LS + "/": _shead("Meadow Landscape", _ls_head) + _ls_nav + """
<h1>Meadow Landscape</h1>
<h2>What we do</h2>
<ul><li>Garden design</li><li>Lawn maintenance</li><li>Patio and paving</li>
<li>Seasonal clean-ups</li></ul>
""" + _sfoot("Meadow Landscape", _ls_addr),
    _LS + "/quote/": _shead("Request a quote", _ls_head) + _ls_nav + """
<h1>Request a quote</h1>
<form action="/quote/" method="post">""" + _asks(
        "t:Name", "e:Email", "p:Telephone", "t:Address", "s:Type of work",
        "s:Approximate size", "d:When would you like it done", "s:Budget",
        "a:Anything else") + """
<button type="submit">Send</button></form>
""" + _sfoot("Meadow Landscape", _ls_addr),
}, {"no_online_booking", "no_crm_signals", "quote_by_form", "no_live_chat",
    "no_analytics", "no_schema", "no_social_presence", "price_opaque"})


# ── 24. A price list that is a PDF, and a report that only looks like one ──

_T = "https://trailsideworkwear.test"
_tw_head = '<link rel="stylesheet" href="/wp-content/themes/trade/style.css">\n'
_tw_addr = "5 Front Street, Sarnia, ON N7T 5S5."

_crawl("dated-document-negatives", _T + "/", {
    _T + "/": _shead("Trailside Workwear", _tw_head) + """
<nav><a href="/">Home</a> <a href="/contact/">Contact</a></nav>
<h1>Trailside Workwear</h1>
<p>Crew outfitting and embroidery for contractors.</p>
<p>Download the <a href="/downloads/price-list.pdf">price list</a>, or read our
<a href="/docs/annual-report-2019.pdf">annual report 2019</a>.</p>
""" + _LEAD_FORM + _sfoot("Trailside Workwear", _tw_addr),
    _T + "/contact/": _shead("Contact", _tw_head) + """
<nav><a href="/">Home</a> <a href="/contact/">Contact</a></nav>
<h1>Contact</h1>
<p>The counter is open weekdays from seven.</p>
""" + _LEAD_FORM + _sfoot("Trailside Workwear", _tw_addr),
}, {"no_crm_signals", "no_live_chat", "no_analytics", "no_schema",
    "no_social_presence"})


SITE_PAGE_COUNT = sum(len(pages) for _n, _b, pages, _g, _r in SITE_CORPUS)


def test_no_rule_stands_a_substring_in_for_the_fact_it_claims():
    """Precision on the corpus, rule by rule, because a wrong headline is dear.

    `gaps[0]` becomes the first sentence of a cold email, so a false positive
    tells a stranger something untrue about their own website. Several rules
    were built on a word appearing somewhere in the page rather than on a fact
    about its structure, and over the pages above they scored, before -> after:

        rule                 precision            recall
        no_online_booking    0.571 -> 1.000       1.000 -> 1.000
        no_crm_signals       0.875 -> 1.000       1.000 -> 1.000
        no_lead_capture      1.000 -> 1.000       0.200 -> 1.000
        careers_manual       0.500 -> 1.000       1.000 -> 1.000
        pdf_forms            0.667 -> 1.000       1.000 -> 1.000
        no_social_presence   1.000 -> 1.000       0.964 -> 1.000
        every rule together  0.931 -> 1.000       0.972 -> 1.000

    Recall is the other half of what this measures. Precision is cheap to buy
    on its own — the rules could simply fire less — and the four pages that
    exist only to be caught (a physiotherapist whose page never says
    "appointment", a section headed Careers with no link under it, a new
    patient form, a jotform embed) are what stops that being the fix.

    One label here has since been corrected rather than one rule: the page
    running HubSpot was labelled as having no chat box and nothing measuring it,
    and the script it loads is both. See the comment above it.
    """
    wrong = {}
    for name, html, expected in CORPUS:
        url = "https://%s.test/" % re.sub(r"[^a-z]+", "-", name.lower())
        fired = set(_codes(A.audit_from_html({url: html}, url)))
        if fired != expected:
            wrong[name] = {"claimed but false": sorted(fired - expected),
                           "true but missed": sorted(expected - fired)}
    assert not wrong, wrong

    # The corpus only means something if every rule it grades is exercised by
    # it, positives and negatives both.
    rates = _corpus_rates(CORPUS)
    for code in ("no_online_booking", "no_crm_signals", "no_lead_capture",
                 "careers_manual", "pdf_forms", "no_social_presence"):
        true_positives = rates[code][0]
        assert true_positives >= 2, (code, rates[code])
        assert true_positives < len(CORPUS), (code, rates[code])
    print("no rule stands a substring in for the fact it claims: OK")


def test_a_price_on_the_page_is_not_no_pricing_anywhere():
    """"not a rate, a range or a starting figure on any page", to a page with one.

    The rule was `pricing_link or money_hits >= 3 or "our prices" in text`, and a
    threshold across the whole crawl is not a fact about anything. Two prices
    read as none, so a groomer with a price list under its own Grooming heading
    was told it publishes nothing; three discounts read as a rate card, so a
    detailer advertising $50 off was credited with prices it does not print; and
    "our prices are competitive" — a sentence that publishes no figure at all —
    silenced the finding that was true.

    What replaces it is the figure's own surroundings: a table cell, a list
    item, a "from", a "per hour", a section headed Fees. The old three-match arm
    survives underneath, on distinct figures, so nothing that used to read as a
    price list stops reading as one.
    """
    published = (
        "<ul><li>Full groom &mdash; $65</li><li>Nail trim &mdash; $15</li></ul>",
        "<table><tr><td>5x10</td><td>$89 per month</td></tr></table>",
        "<p>Kitchens start from &pound;12,000.</p>",
        "<p>Lessons are $60 per hour.</p>",
        "<h2>Fees</h2><p>Initial assessment $110.</p>",
        # The arm that was already there, on the shape it was already right about.
        "<p>Check-up $95.00, cleaning $140.00, whitening $350.00.</p>",
    )
    silent = (
        "<p>Save $50 on your first service, $25 off referrals and $0 down.</p>",
        "<p>Our prices are competitive and every engagement is quoted individually.</p>",
        "<p>Ask us for a figure and we will come back the same day.</p>",
        # "fee" is inside "coffee", so the heading arm reads whole words.
        "<h2>Coffee and cake</h2><p>Save $20 on a bean subscription.</p>",
    )
    for body in published:
        result = _built(body + _LEAD_FORM)
        assert result["signals"]["has_pricing"] is True, body
        assert "price_opaque" not in _codes(result), (body, _codes(result))
    for body in silent:
        result = _built(body + _LEAD_FORM)
        assert result["signals"]["has_pricing"] is False, body
        assert "price_opaque" in _codes(result), (body, _codes(result))
    print("a price on the page is not no pricing anywhere: OK")


def test_a_chat_box_is_a_chat_box_whoever_sells_it():
    """`_CHAT_MARKERS` held seven vendors and the sentence claims there are none.

    "no chat box on the site" is checkable in one second by the person reading
    it, which makes it the cheapest sentence in the catalogue to be caught out
    on — and eleven products that draw a box in the corner of the page were not
    in the table. HubSpot is the shape of the whole problem: one script is the
    chat widget, the CRM behind the form and the page-view counter, and it was
    filed under CRM alone, so two of the three sentences it disproves went out.
    """
    vendors = {
        "zendesk": '<script id="ze-snippet" src="https://static.zdassets.com/ekr/snippet.js">'
                   "</script>",
        "freshchat": '<script src="//fw-cdn.com/12345/67890.js" chat="true"></script>',
        "olark": '<script src="https://static.olark.com/jsclient/loader0.js"></script>',
        "smartsupp": '<script src="https://www.smartsuppchat.com/loader.js?"></script>',
        "zoho_salesiq": '<script src="https://salesiq.zoho.com/widget"></script>',
        "userlike": '<script src="https://userlike-cdn-widgets.s3.amazonaws.com/ab.js"></script>',
        "chatra": "<script>ChatraID='ab12';</script>"
                  '<script src="//call.chatra.io/chatra.js"></script>',
        "jivosite": '<script src="//code.jivosite.com/widget/ab12"></script>',
        "purechat": '<script src="https://app.purechat.com/VisitorWidget/WidgetScript">'
                    "</script>",
        "helpcrunch": '<script src="https://widget.helpcrunch.com/"></script>',
        "salesforce_chat": '<script src="https://x.my.site.com/embeddedservice_bootstrap.js">'
                           "</script>",
        "hubspot": '<script id="hs-script-loader" src="//js.hs-scripts.com/7654321.js">'
                   "</script>",
    }
    for expected, snippet in vendors.items():
        result = _built(_LEAD_FORM, head=snippet + "\n")
        assert result["tech"]["chat"] == expected, (expected, result["tech"]["chat"])
        assert result["signals"]["has_live_chat"] is True, expected
        assert "no_live_chat" not in _codes(result), (expected, _codes(result))

    # One script, three facts. The CRM and the analytics claims are the other two
    # the table was making wrongly about the same page.
    hub = _built(_LEAD_FORM,
                 head='<script src="//js.hs-scripts.com/7654321.js"></script>\n')
    assert hub["tech"]["crm"] == "hubspot", hub["tech"]
    assert hub["tech"]["analytics"] == ["hubspot"], hub["tech"]
    for absent in ("no_live_chat", "no_crm_signals", "no_analytics"):
        assert absent not in _codes(hub), (absent, _codes(hub))

    # And the word in a sentence is still not a chat box.
    prose = _built("<p>Get in touch and let’s have a chat about your project.</p>" + _LEAD_FORM)
    assert prose["signals"]["has_live_chat"] is False, prose["signals"]
    assert "no_live_chat" in _codes(prose), _codes(prose)
    print("a chat box is a chat box whoever sells it: OK")


def test_a_booking_page_is_read_as_words_and_not_as_spellings():
    """`/online-booking/` was not a booking system, and `/booking-terms/` was.

    The path rule was a list of twenty-two whole segments, so it wanted exactly
    `/booking` and the two plainest ways a site writes it — `/online-booking/`
    and `/book-a-table/` — fell straight through, putting the catalogue's
    highest-severity gap on businesses with a Book button on the home page. The
    label rule had the same shape: it knew "book an appointment" and not "Book
    Appointment".

    Reading the segment as the words in it fixes both directions at once, which
    is the point — "booking-terms" and "booking" differ by a noun, and only one
    of them is a calendar.
    """
    books = ('<a href="/online-booking/">Book Appointment</a>',
             '<a href="/book-a-table/">Reserve</a>',
             '<a href="/request-appointment/">Enquire</a>',
             '<a href="/x/">Make a booking</a>',
             '<a href="/x/">Schedule your visit</a>',
             '<a href="/termin-online-buchen/">Jetzt buchen</a>')
    small_print = ('<a href="/booking-terms/">Booking terms</a>',
                   '<a href="/cancellation-policy/">Booking and cancellation fees</a>',
                   '<a href="/blog/booking-tips/">Ten booking tips for tradespeople</a>')
    bookable = "<h2>Treatments</h2><ul><li>Sports massage</li><li>Manual therapy</li></ul>"
    for markup in books:
        result = _built(bookable + markup + _LEAD_FORM)
        assert result["signals"]["has_online_booking"] is True, markup
        assert "no_online_booking" not in _codes(result), (markup, _codes(result))
    for markup in small_print:
        result = _built(bookable + markup + _LEAD_FORM)
        assert result["signals"]["has_online_booking"] is False, markup
        assert "no_online_booking" in _codes(result), (markup, _codes(result))

    # A vendor that renders its calendar in an iframe and prints no label at all.
    for snippet in ('<iframe src="https://x.janeapp.com/"></iframe>',
                    '<iframe src="https://booking.vagaro.com/x"></iframe>',
                    '<a href="https://booksy.com/en-gb/12345_x">Online</a>',
                    '<a href="https://calendar.app.google/aBcD1234">Choose a time</a>',
                    '<a href="https://outlook.office365.com/owa/calendar/x/bookings/">Times</a>'):
        result = _built(bookable + snippet + _LEAD_FORM)
        assert result["signals"]["has_online_booking"] is True, snippet
        assert "no_online_booking" not in _codes(result), (snippet, _codes(result))
    print("a booking page is read as words and not as spellings: OK")


def test_a_trade_books_times_whether_or_not_it_says_so():
    """The other half of the same gap: the businesses it was silent about.

    `_bookable` reads `services`, which is capped at twelve labels and filtered
    down to what fits a brief, so a trade whose words never made that cut — a
    driving school heading its page "Driving lessons", a tattoo studio listing
    "Custom tattoo design" — looked like a business that arranges no times.
    Precision is cheap to buy by firing less, and these are the pages that stop
    that being the fix.
    """
    trades = ("<h2>Driving lessons</h2><p>We will meet you at your door.</p>",
              "<h2>What we do</h2><ul><li>Custom tattoo design</li><li>Cover-up work</li></ul>",
              "<h2>What we do</h2><ul><li>Osteopathic treatment</li></ul>",
              "<h2>Services</h2><ul><li>Sports massage</li><li>Acupuncture</li></ul>")
    for body in trades:
        codes = _codes(_built(body + _LEAD_FORM))
        assert "no_online_booking" in codes, (body, codes)

    # And the businesses it must stay silent about, which is why the list of
    # bookable work is a list and not "sells anything".
    counters = ("<h2>What we do</h2><ul><li>Litho printing</li><li>Finishing and binding</li>"
                "</ul>",
                "<h2>What we do</h2><ul><li>Key cutting</li><li>Tool hire</li></ul>",
                "<h2>What we do</h2><ul><li>Next day delivery</li><li>Bulk paper supply</li>"
                "</ul>")
    for body in counters:
        codes = _codes(_built(body + _LEAD_FORM))
        assert "no_online_booking" not in codes, (body, codes)
    print("a trade books times whether or not it says so: OK")


def test_a_phrase_is_matched_on_its_words_and_not_on_its_punctuation():
    """A word processor's apostrophe made a site stop advertising its own job.

    `_HIRING_PHRASES` holds "we're hiring" with a typewriter apostrophe, and the
    page ships U+2019 because that is what every editor produces. Same shape one
    rule over: a barber writing "walk-ins welcome — first-come, first-served"
    matched the phrase list, and one writing it flat did not, so the second shop
    was told to buy a booking system on a page that says it takes none.
    """
    for markup in ("<p>We’re hiring a qualified technician.</p>",
                   "<p>We're hiring a qualified technician.</p>",
                   "<p>Nous recrutons un collaborateur.</p>"):
        codes = _codes(_built(markup + _LEAD_FORM))
        assert "careers_manual" in codes, (markup, codes)

    haircuts = "<h2>Services</h2><ul><li>Haircut</li><li>Beard trim</li></ul>"
    for markup in ("<p>Walk-ins only. We do not take appointments.</p>",
                   "<p>Walk ins welcome &mdash; first come first served.</p>",
                   "<p>Walk‑ins welcome, first‑come, first‑served.</p>"):
        codes = _codes(_built(markup + haircuts + _LEAD_FORM))
        assert "no_online_booking" not in codes, (markup, codes)
    # The same shop with nothing said either way still books haircuts.
    assert "no_online_booking" in _codes(_built(haircuts + _LEAD_FORM))
    print("a phrase is matched on its words and not on its punctuation: OK")


def test_a_threshold_and_a_vendor_list_are_not_the_fact_they_stand_in_for():
    """The second corpus, rule by rule, for the same reason as the first.

    Thirty-one pages, every label written down before a rule was touched. What
    they measure is the two shapes the first corpus does not: a threshold
    counting matches instead of asking what a figure is attached to, and a
    vendor table that answers "there is no such thing on this site" whenever the
    site runs the eighth product rather than one of its seven.

    Over these thirty-one pages, before -> after:

        rule                 precision            recall
        no_online_booking    0.625 -> 1.000       0.833 -> 1.000
        price_opaque         0.750 -> 1.000       0.913 -> 1.000
        no_live_chat         0.806 -> 1.000       1.000 -> 1.000
        no_analytics         0.903 -> 1.000       1.000 -> 1.000
        no_crm_signals       0.966 -> 1.000       1.000 -> 1.000
        no_social_presence   0.968 -> 1.000       1.000 -> 1.000
        quote_by_form        0.500 -> 1.000       1.000 -> 1.000
        no_lead_capture      0.000 -> 1.000          --
        careers_manual          --   -> 1.000       0.000 -> 1.000
        ecommerce_manual        --   -> 1.000       0.000 -> 1.000
        no_schema            1.000 -> 1.000       1.000 -> 1.000
        every rule together  0.870 -> 1.000       0.961 -> 1.000

    A dash is a rate with no denominator: nothing fired for `careers_manual` or
    `ecommerce_manual` on any of these pages before, so there was no precision
    to have, and no page here is labelled `no_lead_capture`, so there is no
    recall to have. Both are measured over the corpus above instead.

    And over both corpora, sixty pages: 0.926 -> 1.000 precision, 0.981 ->
    1.000 recall. Nothing above was bought by firing less — six of the eleven
    rules gained recall in the same pass, and the pages that hold that honest
    are the driving school, the tattoo studio, the Ecwid wine merchant and the
    Constant Contact newsletter with no <form> anywhere on the page.
    """
    wrong = {}
    for name, html, expected in PRICE_AND_VENDOR_CORPUS:
        url = "https://%s.test/" % re.sub(r"[^a-z]+", "-", name.lower())
        fired = set(_codes(A.audit_from_html({url: html}, url)))
        if fired != expected:
            wrong[name] = {"claimed but false": sorted(fired - expected),
                           "true but missed": sorted(expected - fired)}
    assert not wrong, wrong

    assert len(PRICE_AND_VENDOR_CORPUS) >= 25, len(PRICE_AND_VENDOR_CORPUS)
    # Every rule the corpus grades has to be exercised both ways by it, or the
    # rate it reports is a rate over one answer.
    rates = _corpus_rates(PRICE_AND_VENDOR_CORPUS)
    for code in ("price_opaque", "no_live_chat", "no_online_booking", "no_analytics",
                 "no_crm_signals", "ecommerce_manual"):
        true_positives = rates[code][0]
        assert true_positives >= 1, (code, rates[code])
        assert true_positives < len(PRICE_AND_VENDOR_CORPUS), (code, rates[code])
    print("a threshold and a vendor list are not the fact they stand in for: OK")



# ── The platforms, the unreadable pages, and the reason a site is not reachable ──


def test_the_platforms_this_tool_actually_meets():
    """Precision and recall over thirty-six real page shapes, rule by rule.

    The two corpora above are one page shape each, which makes them sharp about
    a single rule and blind to what a real crawl drags in with it. These
    thirty-six carry the platforms this tool actually meets — WordPress under
    Elementor and under Divi, Wix, Squarespace, Shopify, Webflow, GoDaddy, Duda,
    Weebly, Joomla, Drupal, WooCommerce, Ecwid, GoHighLevel — plus a one-page
    brochure, a three-branch chain, a four-branch chain whose addresses live
    only in JSON-LD, a site behind Cloudflare, a React site that does ship its
    content, and pages in French, German, Spanish and Dutch.

    Over these pages, before -> after:

        rule                 precision            recall
        no_lead_capture      0.364 -> 1.000       1.000 -> 1.000
        no_social_presence   0.538 -> 1.000       1.000 -> 1.000
        no_analytics         0.650 -> 1.000       1.000 -> 1.000
        no_live_chat         0.688 -> 1.000       1.000 -> 1.000
        no_schema            0.786 -> 1.000       1.000 -> 1.000
        price_opaque         0.786 -> 1.000       0.957 -> 1.000
        ecommerce_manual     0.750 -> 1.000       1.000 -> 1.000
        no_online_booking    0.889 -> 1.000       0.889 -> 1.000
        no_mobile            0.333 -> 1.000       1.000 -> 1.000
        contact_form_only    0.000 -> 1.000          --
        multi_location       1.000 -> 1.000       0.500 -> 1.000
        no_crm_signals       1.000 -> 1.000       0.950 -> 1.000
        every rule together  0.728 -> 1.000       0.969 -> 1.000

    Forty-seven false claims became none. Thirty-eight of the forty-seven came
    from six pages nobody could read — see the test below — and the other nine
    were a vendor table that had never heard of the product on the page (Wix's
    own chat, Shopify Inbox, Podium, GoHighLevel, Cloudflare's own page-view
    counter), a bare "woocommerce" matching an agency's services list, a
    services list reading "Site servicing" as a thing somebody books, and an
    Australian phone number printed inside brackets that no pattern here could
    see. The four recall misses were a German chain whose branches are only in
    its JSON-LD, an optician whose bookable trade is only in its schema type, a
    shop excused from `price_opaque` by a false storefront, and an HR firm
    linking to workforce.com, which `force.com` read as Salesforce.

    `contact_form_only` has a dash for recall because no page here is labelled
    with it while one page used to claim it.
    """
    wrong = {}
    for name, url, html, expected, _reason in PLATFORM_CORPUS:
        fired = set(_codes(A.audit_from_html({url: html}, url)))
        if fired != expected:
            wrong[name] = {"claimed but false": sorted(fired - expected),
                           "true but missed": sorted(expected - fired)}
    assert not wrong, wrong

    assert len(PLATFORM_CORPUS) >= 30, len(PLATFORM_CORPUS)
    # Graded both ways, or the rate is a rate over one answer. The codes below
    # are the ones this corpus is built to exercise; the rest are measured by
    # the two corpora above.
    rates = _corpus_rates([(n, u, h, g) for n, u, h, g, _r in PLATFORM_CORPUS])
    for code in ("no_live_chat", "no_analytics", "no_schema", "price_opaque",
                 "no_crm_signals", "no_online_booking", "ecommerce_manual",
                 "no_lead_capture", "multi_location"):
        true_positives = rates[code][0]
        assert true_positives >= 1, (code, rates[code])
        assert true_positives < len(PLATFORM_CORPUS), (code, rates[code])
    # And every code added in this pass earns its row the same way.
    for code in PLATFORM_NEW_CODES:
        assert rates[code][0] >= 1, (code, rates[code])
        assert rates[code][1] == 0 and rates[code][2] == 0, (code, rates[code])
    print("the platforms this tool actually meets: OK")


def test_a_page_nobody_could_read_is_not_a_list_of_faults():
    """Six pages answered 200, told the crawler nothing, and it said plenty.

    Every rule in the catalogue is an absence — no chat, no markup, nothing asks
    a visitor for a name — and an absence is only a fact when there was
    somewhere to look. A Cloudflare bot check, a Dutch consent wall, a Next.js
    shell, a parked domain, an empty body and a coming-soon splash all present
    the same way to the rules: no markers anywhere, so all of them fire. Between
    them those six pages produced thirty-eight sentences about businesses whose
    home page had never actually been seen, and every one of them was headed for
    a live email.

    What comes back instead is the reason, in the two keys the Leads table
    reads. `tech` survives the verdict and the signals do not, and that is the
    whole distinction: a marker on the page is a positive fact and stays true
    however little rendered, while every signal is the other kind.
    """
    for name, url, html, _gaps, reason in PLATFORM_CORPUS:
        result = A.audit_from_html({url: html}, url)
        if not reason:
            assert result["reachable"] is True, name
            assert result["unreachable_reason"] == "", (name, result["unreachable_reason"])
            assert result["unreachable_detail"] == "", name
            continue
        assert result["unreachable_reason"] == reason, (name, result["unreachable_reason"])
        assert result["reachable"] is False, name
        assert result["gaps"] == [], (name, _codes(result))
        assert result["opportunity_score"] == 0, name
        # The sentence the operator reads, and it has to be one.
        detail = result["unreachable_detail"]
        assert detail == A.UNREACHABLE_REASONS[reason], (name, detail)
        assert detail and detail[0].islower() and not detail.endswith("."), (name, detail)

    # A shell is still a Next.js shell, and saying so is not an absence claim.
    shell = next(h for n, _u, h, _g, _r in PLATFORM_CORPUS if n == "nextjs-shell")
    read = A.audit_from_html({"https://orbitdental.test/": shell},
                             "https://orbitdental.test/")
    assert "nextjs" in read["tech"]["frameworks"], read["tech"]

    # And the model is told what is known rather than what was not found. The
    # digest used to hand it "no chat | no booking | no crm | no analytics"
    # about a host that never answered, and the opener was written around that.
    brief = A.digest(read)
    assert "UNREADABLE:" in brief, brief
    for invented in ("no chat", "no booking", "no crm", "no analytics", "TOP GAPS"):
        assert invented not in brief, (invented, brief)
    print("a page nobody could read is not a list of faults: OK")


def test_an_unreachable_site_says_why():
    """The operator asked which site is not reachable, and got back "that one".

    `reachable=False` with the reason thrown away is the same answer for a lead
    that moved to a new domain, one whose certificate expired on Sunday and one
    that is simply slow, and those three want three different things done about
    them. The reason is now a pair of keys on the audit — a code to branch on
    and a sentence to print — and it rides into `audit_json` on the lead, which
    is where the UI reads it.
    """
    cases = {
        "": "",
        "dns": "dns",
        "URLError: <urlopen error [Errno 11001] getaddrinfo failed>": "dns",
        "timeout": "timeout",
        "TimeoutError: timed out": "timeout",
        "ssl": "tls",
        "SSLCertVerificationError: certificate verify failed": "tls",
        "refused": "refused",
        "ConnectionRefusedError: [Errno 111]": "refused",
        "reset": "reset",
        "redirect loop": "redirect_loop",
        "http 404": "http_404",
        "HTTP 403": "http_403",
        "http 500": "http_500",
        "http 503": "http_503",
        "http 418": "http_error",
        "content-type": "not_html",
        "no url": "no_url",
        "empty response": "empty",
        "something nobody has a word for": "unreachable",
    }
    for error, expected in cases.items():
        assert A.unreachable_reason(error) == expected, (error, A.unreachable_reason(error))
    # A status with no error text answers too, which is the shape an HTTPError
    # reaches `_blank` in.
    assert A.unreachable_reason("", 404) == "http_404"
    assert A.unreachable_reason("", 200) == ""

    # Every code in the vocabulary has a sentence, and every sentence is one.
    for code, sentence in A.UNREACHABLE_REASONS.items():
        assert sentence and sentence[0].islower(), code
        assert not sentence.endswith((".", "!", "?")), code
        assert len(sentence) <= 60, (code, len(sentence))
        assert A.unreachable_detail(code) == sentence, code
    assert A.unreachable_detail("nothing anybody wrote down") == ""

    # The whole result, built the way `core.campaign` builds it for a host it
    # has already paid the connection timeout on twice.
    dead = A.unreachable_audit("https://gone.test/", "dns")
    assert dead["reachable"] is False and dead["error"] == "dns"
    assert dead["unreachable_reason"] == "dns"
    assert dead["unreachable_detail"] == "the domain name does not resolve"
    assert dead["gaps"] == [] and dead["opportunity_score"] == 0
    assert dead["final_url"] == "https://gone.test/"
    # It has to survive the round trip to the lead, because that is where the
    # UI reads it from.
    assert json.loads(json.dumps(dead))["unreachable_reason"] == "dns"

    # And the audit of a live site says nothing of the kind.
    alive = A.audit_from_html({"https://acmeplumbing.ca/": WORDPRESS_PLUMBER},
                              "https://acmeplumbing.ca/")
    assert alive["reachable"] is True
    assert alive["unreachable_reason"] == "" and alive["unreachable_detail"] == ""
    print("an unreachable site says why: OK")


def test_a_vendor_name_inside_a_longer_host_is_not_that_vendor():
    """The substring-for-a-structure shape, in the tables rather than the rules.

    Every vendor table answers "there is no such thing on this site" when it
    finds nothing, so a marker landing inside a longer host is a true finding
    deleted: `force.com` is the tail of `workforce.com`, and an HR consultancy
    linking to a rota product was credited with Salesforce, which silenced the
    one thing actually wrong with its site. `_present` now requires a
    host-shaped marker to start where a host starts.
    """
    assert A._present("https://www.workforce.com/features", "workforce.com") is True
    assert A._present("https://www.workforce.com/features", "force.com") is False
    assert A._present("https://acme.force.com/apex/form", ".force.com") is True
    assert A._present("https://static.wixstatic.com/x", "wixstatic.com") is True
    assert A._present("https://www.pressy.com/wine", "resy.com") is False
    assert A._present("https://resy.com/cities/ny", "resy.com") is True
    # A path or a JavaScript identifier is matched plainly, as it always was.
    assert A._present("var x=$crisp||[];", "$crisp") is True
    assert A._present("/wp-content/themes/x", "/wp-content/") is True

    hr = next(h for n, _u, h, _g, _r in PLATFORM_CORPUS if n == "hr-links-workforce")
    read = A.audit_from_html({"https://northgatepeople.test/": hr},
                             "https://northgatepeople.test/")
    assert read["tech"]["crm"] == "", read["tech"]
    assert "no_crm_signals" in _codes(read), _codes(read)

    # And the other half of the same lesson: a bare vendor name in a services
    # list is not a storefront. It cost two claims at once, because a shop is
    # excused from publishing prices.
    agency = next(h for n, _u, h, _g, _r in PLATFORM_CORPUS
                  if n == "agency-mentions-woocommerce")
    built = A.audit_from_html({"https://reddeerdigital.test/": agency},
                              "https://reddeerdigital.test/")
    assert built["tech"]["ecommerce"] == "", built["tech"]
    assert "ecommerce_manual" not in _codes(built), _codes(built)
    assert "price_opaque" in _codes(built), _codes(built)

    shop = next(h for n, _u, h, _g, _r in PLATFORM_CORPUS if n == "woocommerce-bakery")
    real = A.audit_from_html({"https://proofbakehouse.test/": shop},
                             "https://proofbakehouse.test/")
    assert real["tech"]["ecommerce"] == "woocommerce", real["tech"]
    print("a vendor name inside a longer host is not that vendor: OK")


def test_a_printed_number_survives_its_own_brackets():
    """The email says out loud that there is no number to tap. There was one.

    `contact_form_only` claims the form is the only way in, and all four phone
    patterns died on a bracketed trunk code — `(07) 3555 0177`, which is how
    Australia and most of Europe print an area code. The first pattern wants
    three digits inside the bracket, and the two loose ones run into the `)`
    and stop.
    """
    printed = ("(07) 3555 0177", "(0161) 555 0134", "(905) 555-0134",
               "+44 20 7946 0102", "04 78 55 44 33", "920 55 10 40",
               "(+61) 2 9374 4000")
    for number in printed:
        assert A._phone_present("Call us on %s today" % number), number
    for not_a_number in ("2026 2025 2024", "Suite 200", "(12) 34"):
        assert not A._phone_present(not_a_number), not_a_number

    vet = next(h for n, _u, h, _g, _r in PLATFORM_CORPUS if n == "cloudflare-real-site")
    read = A.audit_from_html({"https://ashgrovevets.test/": vet},
                             "https://ashgrovevets.test/")
    assert read["signals"]["has_phone"] is True, read["signals"]
    assert "contact_form_only" not in _codes(read), _codes(read)
    print("a printed number survives its own brackets: OK")


# ── What a crawl knows and a home page cannot ──


def test_a_site_is_more_than_its_home_page():
    """Precision and recall over twenty-four whole crawls, rule by rule.

    The corpora above are one page each, so every rule in the catalogue was a
    rule about a home page. Five findings that are worth more than most of them
    are two clicks in, and this is the corpus that grades those: sixty-three
    pages, labelled before a line was written, across WordPress under Elementor
    and under Divi, Wix, Squarespace, Shopify, Webflow, GoDaddy, Duda, Framer,
    an AMP page, WooCommerce, Joomla, a Cloudflare challenge, a Next.js shell, a
    Dutch cookie wall, and sites in French, German, Spanish and Dutch.

    Over these crawls, before -> after:

        rule                 precision            recall
        no_online_booking    1.000 -> 1.000       0.800 -> 1.000
        price_opaque         0.875 -> 1.000       1.000 -> 1.000
        no_analytics         0.923 -> 1.000       1.000 -> 1.000
        long_intake_form        --  -> 1.000       0.000 -> 1.000
        services_no_route       --  -> 1.000       0.000 -> 1.000
        dated_document          --  -> 1.000       0.000 -> 1.000
        cart_no_recovery        --  -> 1.000       0.000 -> 1.000
        every rule together  0.975 -> 1.000       0.935 -> 1.000

    The three false claims were a restaurant on AMP told nobody counts its
    visits (an `amp-analytics` component writes no gtag call, so every marker in
    the table missed it) and two businesses told they publish no prices at all,
    both of whom link a price list from their own home page. The eight misses
    were a dentist and a Dutch practice whose Book Online pages are contact
    forms, and the six the new codes are for. A dash means the rule did not
    exist to have a rate.
    """
    wrong = {}
    for name, base, pages, expected, _reason in SITE_CORPUS:
        fired = set(_codes(A.audit_from_html(pages, base)))
        if fired != expected:
            wrong[name] = {"claimed but false": sorted(fired - expected),
                           "true but missed": sorted(expected - fired)}
    assert not wrong, wrong

    assert len(SITE_CORPUS) >= 20, len(SITE_CORPUS)
    assert SITE_PAGE_COUNT >= 40, SITE_PAGE_COUNT
    # More than one page per site, or this corpus is the one above with extra
    # steps and grades nothing it was built for.
    assert sum(1 for _n, _b, p, _g, _r in SITE_CORPUS if len(p) > 1) >= 18

    # Every code this corpus exists to grade, graded both ways.
    rates = _corpus_rates([(n, b, p, g) for n, b, p, g, _r in SITE_CORPUS])
    for code in ("long_intake_form", "services_no_route", "dated_document",
                 "cart_no_recovery", "no_online_booking", "price_opaque",
                 "no_analytics"):
        tp, fp, fn = rates[code]
        assert tp >= 1 and fp == 0 and fn == 0, (code, rates[code])
        assert tp < len(SITE_CORPUS), (code, rates[code])
    print("a site is more than its home page: OK")


def test_a_booking_page_that_is_a_form_is_not_a_booking_system():
    """A Book Online button is a promise. The page behind it is the answer.

    `no_online_booking` is the catalogue's headline gap and it was suppressed by
    a link: any anchor whose path or label said booking counted as a calendar,
    on the reasoning that a business with a Book button probably has one. Over
    a crawl that reasoning is no longer needed, and it was wrong twice in
    twenty-four sites -- a dentist and a Dutch practice whose booking pages hold
    a contact form and a telephone number and nothing else. Those are not sites
    that need no calendar; they are the sites the gap was written for.

    It stays a promise wherever the crawl cannot check it: a link out to
    somebody else's calendar, a vendor script anywhere on the site, or an
    iframe, a date field or the word calendar on the page itself. All three
    cost a finding rather than buying a false one.
    """
    booking = "https://practice.test"
    home = _shead("Practice") + """
<nav><a href="/">Home</a> <a href="/book-online/">Book Online</a></nav>
<h1>Practice</h1><h2>Our Services</h2><ul><li>Dental cleaning</li>
<li>Teeth whitening</li></ul>
""" + _LEAD_FORM + _sfoot("Practice", "1 Mill St, Guelph, ON N1H 2A9.")

    # The page behind the button is a form, and the crawl has it.
    a_form = A.audit_from_html({
        booking + "/": home,
        booking + "/book-online/": _shead("Book Online") + "<h1>Book Online</h1>"
        "<p>Tell us when suits and we will call you back.</p>" + _LEAD_FORM
        + _sfoot("Practice", "1 Mill St, Guelph, ON N1H 2A9."),
    }, booking + "/")
    assert a_form["signals"]["has_online_booking"] is False, a_form["signals"]
    assert "no_online_booking" in _codes(a_form), _codes(a_form)
    headline = next(g for g in a_form["gaps"] if g["code"] == "no_online_booking")
    assert headline["evidence"].startswith("clicking through to book"), headline

    # And the three shapes that keep the benefit of the doubt.
    kept = {
        "a calendar of its own":
            "<h1>Book Online</h1><div class=\"datepicker\"></div>",
        "an embed this table has never heard of":
            "<h1>Book Online</h1><iframe src=\"https://booking.example/widget\"></iframe>",
        "a vendor":
            "<h1>Book Online</h1><a href=\"https://calendly.com/practice\">Pick a time</a>",
    }
    for why, body in kept.items():
        result = A.audit_from_html({
            booking + "/": home,
            booking + "/book-online/": _shead("Book Online") + body
            + _sfoot("Practice", "1 Mill St, Guelph, ON N1H 2A9."),
        }, booking + "/")
        assert result["signals"]["has_online_booking"] is True, (why, result["signals"])
        assert "no_online_booking" not in _codes(result), (why, _codes(result))

    # The page was never fetched, so nothing here has an opinion about it.
    unseen = A.audit_from_html({booking + "/": home}, booking + "/")
    assert unseen["signals"]["has_online_booking"] is True, unseen["signals"]
    print("a booking page that is a form is not a booking system: OK")


def test_a_form_is_measured_in_questions_and_not_in_input_tags():
    """"the form asks for fourteen separate things" has to be countable by hand.

    A builder ships a nonce, two hidden ids and a honeypot with every form, and
    a row of radio buttons is one question asked once. Counting `<input>` tags
    reads a four-question contact form as a nine-part interrogation, and the
    sentence this gap sends is about how much the reader is asking of a
    stranger -- a number they will check against their own page.
    """
    dressed = ('<form action="/contact" method="post">'
               '<input type="hidden" name="_wpnonce" value="a1b2c3">'
               '<input type="hidden" name="form_id" value="14">'
               '<input type="text" name="honeypot" style="display:none">'
               '<input type="text" name="name"><input type="email" name="email">'
               '<input type="radio" name="contact_by" value="phone">'
               '<input type="radio" name="contact_by" value="email">'
               '<textarea name="message"></textarea>'
               '<input type="submit" value="Send"></form>')
    assert A._form_fields(dressed) == 4, A._form_fields(dressed)

    fourteen = ('<form action="/intake" method="post">'
                + _asks("t:First name", "t:Last name", "e:Email", "p:Telephone",
                        "d:Date of birth", "t:Street", "t:City", "t:Postal code",
                        "s:How did you hear", "t:Doctor", "t:Insurer", "t:Policy",
                        "a:Reason", "a:History")
                + '<button type="submit">Send</button></form>')
    assert A._form_fields(fourteen) == 14, A._form_fields(fourteen)

    # The threshold, from either side, through the whole audit.
    for count, fires in ((A.LONG_FORM_FIELDS - 1, False), (A.LONG_FORM_FIELDS, True)):
        body = ('<h1>Enquiries</h1><form action="/enquiry" method="post">'
                + _asks(*(["t:Question %d" % i for i in range(count - 1)] + ["a:Message"]))
                + '<button type="submit">Send</button></form>')
        result = _built(body)
        assert result["signals"]["has_contact_form"] is True, count
        assert ("long_intake_form" in _codes(result)) is fires, (count, _codes(result))

    # And a search box is not an enquiry however many fields it has.
    assert "long_intake_form" not in _codes(_built(_SEARCH_BOX))
    print("a form is measured in questions and not in input tags: OK")


def test_a_price_list_on_the_site_is_not_no_pricing_anywhere():
    """Two sentences that cannot both be sent, and one of them was.

    `price_opaque` says "not a rate, a range or a starting figure on any page".
    To a business whose home page links its price list that is a sentence the
    reader disproves in one click, and it went out to two of the twenty-four
    sites in the corpus above. What is true about that business is the other
    sentence: the file was last put together in 2019.

    Only the nouns that mean what it costs, and only a year two behind. A
    uniform catalogue is a list of what a supplier stocks and a 2019 annual
    report is a report, and reading either as a rate card would silence
    `price_opaque` on the sites it was written for.
    """
    dated = _built('<p>Download our <a href="/files/price-list-2019.pdf">price list</a>.</p>'
                   + _LEAD_FORM)
    assert "dated_document" in _codes(dated), _codes(dated)
    assert "price_opaque" not in _codes(dated), _codes(dated)
    evidence = next(g for g in dated["gaps"] if g["code"] == "dated_document")["evidence"]
    assert evidence.endswith("2019"), evidence

    # Published, current: no claim either way about the price of anything.
    current = _built('<p>Our <a href="/files/price-list-%d.pdf">price list</a>.</p>'
                     % TODAY.year + _LEAD_FORM)
    assert "dated_document" not in _codes(current), _codes(current)
    assert "price_opaque" not in _codes(current), _codes(current)

    # Not a price list, so `price_opaque` is still the true finding.
    for body in ('<p>Read the <a href="/docs/annual-report-2019.pdf">annual report</a>.</p>',
                 '<p>Browse the <a href="/dl/uniform-catalogue-2019.pdf">uniform '
                 'catalogue</a>.</p>',
                 '<p>Please <a href="/forms/intake-2019.pdf">print the intake form</a>.</p>'):
        result = _built(body + _LEAD_FORM)
        assert "dated_document" not in _codes(result), (body, _codes(result))
        assert "price_opaque" in _codes(result), (body, _codes(result))
    print("a price list on the site is not no pricing anywhere: OK")


def test_a_service_with_a_page_behind_it_has_a_route():
    """Twelve services and one form is a finding. Twelve services and twelve
    pages is a website working properly, and the two look identical in a list of
    words -- the difference is in the markup of the `<li>`, which is why the
    count of linked items is taken on the pass that reads the list.
    """
    def _listing(count, linked):
        items = "".join(
            ('<li><a href="/services/item-%d/">Service number %d</a></li>' % (i, i))
            if linked else ("<li>Service number %d</li>" % i)
            for i in range(count))
        return "<h2>Our Services</h2><ul>%s</ul>" % items

    bare = _built(_listing(12, False) + _LEAD_FORM)
    assert "services_no_route" in _codes(bare), _codes(bare)
    routed = _built(_listing(12, True) + _LEAD_FORM)
    assert "services_no_route" not in _codes(routed), _codes(routed)

    # The threshold from either side, and a site with no way in at all, where
    # the finding is that nothing asks for a name rather than how it is routed.
    for count, fires in ((A.MANY_SERVICES - 1, False), (A.MANY_SERVICES, True)):
        result = _built(_listing(count, False) + _LEAD_FORM)
        assert ("services_no_route" in _codes(result)) is fires, (count, _codes(result))
    assert "services_no_route" not in _codes(_built(_listing(12, False)))
    print("a service with a page behind it has a route: OK")


def test_a_shop_that_asks_for_an_address_is_not_told_it_asks_for_none():
    """"nowhere at all for a shopper to leave an email" is a claim about the
    whole crawl, and a shop that has a signup in its footer disproves it on
    every page. Both halves are measured: the vendor script that renders the box
    in JavaScript, and a plain email field wherever it sits."""
    shop = ('<h1>Northside Supply</h1><p>Boots from $129.00.</p>'
            '<form action="/cart/add"><input type="hidden" name="id" value="1">'
            '<button>Add to cart</button></form>'
            '<script src="https://cdn.shopify.com/s/files/1/0001/theme.css"></script>')
    alone = _built(shop)
    assert alone["tech"]["ecommerce"] == "shopify", alone["tech"]
    assert "cart_no_recovery" in _codes(alone), _codes(alone)

    for why, extra in (
        ("a mailing tool", '<script src="//x.us4.list-manage.com/subscribe/post.js"></script>'),
        ("a signup field", '<form action="/subscribe"><input type="email" name="EMAIL">'
                           '<button>Join</button></form>'),
        ("a contact form", _LEAD_FORM),
    ):
        result = _built(shop + extra)
        assert result["tech"]["ecommerce"] == "shopify", (why, result["tech"])
        assert "cart_no_recovery" not in _codes(result), (why, _codes(result))

    # No shop, no claim: a brochure site with no newsletter is a different gap.
    assert "cart_no_recovery" not in _codes(_built("<h1>A business</h1>" + _LEAD_FORM))
    print("a shop that asks for an address is not told it asks for none: OK")


def test_a_crawl_costs_no_more_than_the_page_it_already_had():
    """Depth has to be free, or it is paid on every lead in a five-hundred lead
    run. Every rule added here reads pages `harvest_site` already fetched and
    `_audit` already parsed: the form fields come out of the blocks
    `_lead_forms` was walking anyway, the linked-item count off the pass
    `_listed_services` already makes, the price document out of the loop that
    was already looking at every PDF link, and the booking page out of the list
    of pages `_order_pages` had already built.

    So the guard is structural rather than a stopwatch: no new pass over the
    crawl, and `audit_site` still touches the network zero times when it is
    handed HTML.
    """
    calls = []
    original = A._fetch
    A._fetch = lambda url, timeout: (calls.append(url), ("", "", 0, "blocked"))[1]
    try:
        name, base, pages, _gaps, _reason = next(
            row for row in SITE_CORPUS if len(row[2]) >= 4)
        result = A.audit_site(base, prefetched=pages)
    finally:
        A._fetch = original
    assert not calls, calls
    assert result["reachable"] is True, result
    assert result["page_count"] == len(pages), (name, result["page_count"])
    # Every page it was handed, read once and only once.
    assert sorted(result["pages"]) == sorted(pages), name
    print("a crawl costs no more than the page it already had: OK")


if __name__ == "__main__":
    test_catalogue_services_are_real()
    test_subject_phrases_are_neutral()
    test_wordpress_plumber()
    test_shopify_store()
    test_careers_page()
    test_cms_fingerprints()
    test_chat_and_booking_fingerprints()
    test_blog_staleness()
    test_ordering_and_score()
    test_score_separates_leads()
    test_digest_shape()
    test_digest_budget()
    test_audit_site_uses_prefetched()
    test_decoding_never_settles_for_replacement_chars()
    test_never_raises()
    test_evidence_is_quotable()
    test_evidence_is_written_for_the_owner()
    test_evidence_never_hands_back_crawler_state()
    test_every_gap_reads_in_every_template()
    test_a_printed_phone_number_is_seen_in_every_market()
    test_a_call_to_action_is_not_what_makes_a_number_real()
    test_prices_are_read_with_the_symbol_on_either_side()
    test_booking_is_a_system_and_not_a_sentence()
    test_a_site_is_hiring_in_more_than_one_language()
    test_a_date_in_the_markup_is_not_a_blog()
    test_the_score_ranks_by_how_much_there_is_to_fix()
    test_what_a_business_sells_is_read_from_the_list_it_writes_it_in()
    test_a_list_of_services_is_not_a_thing_to_book()
    test_a_search_box_is_not_a_lead_form()
    test_careers_has_to_head_a_job_listing()
    test_a_marker_stops_where_the_word_stops()
    test_no_rule_stands_a_substring_in_for_the_fact_it_claims()
    test_a_price_on_the_page_is_not_no_pricing_anywhere()
    test_a_chat_box_is_a_chat_box_whoever_sells_it()
    test_a_booking_page_is_read_as_words_and_not_as_spellings()
    test_a_trade_books_times_whether_or_not_it_says_so()
    test_a_phrase_is_matched_on_its_words_and_not_on_its_punctuation()
    test_a_threshold_and_a_vendor_list_are_not_the_fact_they_stand_in_for()
    test_the_platforms_this_tool_actually_meets()
    test_a_page_nobody_could_read_is_not_a_list_of_faults()
    test_an_unreachable_site_says_why()
    test_a_vendor_name_inside_a_longer_host_is_not_that_vendor()
    test_a_printed_number_survives_its_own_brackets()
    test_a_site_is_more_than_its_home_page()
    test_a_booking_page_that_is_a_form_is_not_a_booking_system()
    test_a_form_is_measured_in_questions_and_not_in_input_tags()
    test_a_price_list_on_the_site_is_not_no_pricing_anywhere()
    test_a_service_with_a_page_behind_it_has_a_route()
    test_a_shop_that_asks_for_an_address_is_not_told_it_asks_for_none()
    test_a_crawl_costs_no_more_than_the_page_it_already_had()
    print("\nALL AUDIT TESTS PASSED")
