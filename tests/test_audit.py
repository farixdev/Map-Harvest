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
    # `slow_site` and `no_mobile` are the two the catalogue has no answer for —
    # page speed and a mobile layout are front-end work and the seller does not
    # sell it. They carry no services here and no entry in `T.GAP_SERVICES`, and
    # every other code has to be in both tables.
    no_offer = {code for code, entry in A.GAP_CATALOGUE.items() if not entry["services"]}
    assert no_offer == {"slow_site", "no_mobile"}, no_offer
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
    assert result["tech"]["analytics"] == ["ga4"], result["tech"]
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
    both be true, and reading all eighteen means firing both halves.
    """
    tech = {"cms": "custom", "ecommerce": "shopify", "analytics": [], "chat": "",
            "booking": "", "crm": "", "forms": 3, "frameworks": []}
    signals = dict(A._blank("https://x.test/")["signals"],
                   has_contact_form=True, has_quote_form=True, has_careers=True,
                   has_pdf_forms=True, has_multiple_locations=True, location_count=4,
                   has_blog=True, blog_stale=True, stale_copyright=True,
                   copyright_year=2019, slow=True)
    facts = {"quote_phrase": "request a quote", "latest_date": "March 2022",
             "pdf_form": "the employment application form",
             "appointment_shaped": True, "call_cta": False}
    fired = A._gaps(tech, signals, facts)
    fired += A._gaps(dict(tech, forms=0, ecommerce=""),
                     dict(signals, has_contact_form=False, has_quote_form=False), facts)
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
    _labelled(
        "Northwind Logistics Software", """
<h1>Northwind Logistics Software</h1>
<h2>What we do</h2><ul><li>Fleet dispatch software</li><li>Route optimisation</li>
<li>Integration support</li></ul>
""" + _LEAD_FORM,
        _BASE_GAPS,
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


def _corpus_rates() -> dict:
    """Per-rule (true positives, false positives, false negatives) over CORPUS."""
    rates = {code: [0, 0, 0] for code in A.GAP_CATALOGUE}
    for name, html, expected in CORPUS:
        url = "https://%s.test/" % re.sub(r"[^a-z]+", "-", name.lower())
        fired = set(_codes(A.audit_from_html({url: html}, url)))
        for code in A.GAP_CATALOGUE:
            hit, want = code in fired, code in expected
            if hit and want:
                rates[code][0] += 1
            elif hit:
                rates[code][1] += 1
            elif want:
                rates[code][2] += 1
    return rates


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
    rates = _corpus_rates()
    for code in ("no_online_booking", "no_crm_signals", "no_lead_capture",
                 "careers_manual", "pdf_forms", "no_social_presence"):
        true_positives = rates[code][0]
        assert true_positives >= 2, (code, rates[code])
        assert true_positives < len(CORPUS), (code, rates[code])
    print("no rule stands a substring in for the fact it claims: OK")


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
    print("\nALL AUDIT TESTS PASSED")
