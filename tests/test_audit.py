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
    assert list(A.GAP_CATALOGUE) and set(A.GAP_CATALOGUE) == set(T.GAP_SERVICES), (
        set(A.GAP_CATALOGUE) ^ set(T.GAP_SERVICES))
    for code, entry in A.GAP_CATALOGUE.items():
        assert entry["severity"] in (1, 2, 3), code
        # Titles drop into the middle of a sentence, so they start lower-case
        # and stay short enough for a subject line.
        assert entry["title"] and entry["title"][0].islower(), code
        assert len(entry["title"]) <= 40, code
        assert entry["services"], code
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
    lead = {"name": "Acme Plumbing Ltd", "email": "mike@acmeplumbing.ca"}
    for code, entry in A.GAP_CATALOGUE.items():
        ctx = T.build_context(lead, {"gaps": [A._gap(code, "evidence")]}, {}, {}, {})
        for template_id in ("gap_direct", "followup_bump"):
            subject = T.render(T.get_template(template_id), ctx)[0]
            assert subject and len(subject) <= T.SUBJECT_MAX, (code, template_id, subject)
            assert entry["title"] not in subject, (code, template_id, subject)
            assert entry["subject_phrase"] in subject, (code, template_id, subject)
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

    gap = next(g for g in result["gaps"] if g["code"] == "careers_manual")
    assert set(gap["services"]) == {"HR processes", "employee onboarding"}, gap["services"]

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
        severities = [g["severity"] for g in result["gaps"]]
        assert severities == sorted(severities, reverse=True), severities

        order = list(A.GAP_CATALOGUE)
        keys = [(-g["severity"], order.index(g["code"])) for g in result["gaps"]]
        assert keys == sorted(keys), keys

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
        body = T.render(template, ctx)[1]
        line = next(ln for ln in body.splitlines() if ln.startswith("One thing stands out"))
        assert "(" in line and line.endswith(")."), (code, line)
        seen = line.split("(", 1)[1][:-2]
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
            if "{{gap_1}}" in tpl.body:
                # The title arrived whole, inside a sentence that finished.
                assert gap["title"] in text, (code, tpl.id, text)
                sentence = text.split(gap["title"])[1].split("\n")[0]
                assert sentence.strip().endswith("."), (code, tpl.id, sentence)
            if "{{gap_1_evidence}}" in tpl.body:
                assert "(%s)" % gap["evidence"] in text, (code, tpl.id, text)
    print("every gap reads in every template: OK")


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
    print("\nALL AUDIT TESTS PASSED")
