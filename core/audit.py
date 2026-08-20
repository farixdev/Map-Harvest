"""Local, deterministic, zero-token website audit — the evidence a cold email stands on.

Why this module exists: the model must never see raw HTML. A single business page
is 50-200k tokens of markup that buys nothing a pattern match cannot find, and a
500-lead campaign at that price is unsendable. So every fact in an outreach email
is discovered here, offline and for free, and the model only ever receives
`digest()` — five lines, under 1200 characters, roughly 300 tokens.

The output that earns its keep is `gaps`. Each detected gap carries the Auto Army
services that close it, resolved through `core.templates.AUTO_ARMY_SERVICES` so
the pitch can never name a capability the business does not actually sell. A gap
the catalogue has no answer for carries none, and that is what orders the list:
gaps with an offer behind them first, then severity, then catalogue order, so
`gaps[0]` is always a headline the email can follow with an offer.

Detection is deliberately conservative. A wrong "no online booking" in a live
cold email is worse than a missed gap: it tells the reader you did not look. So a
signal fires on a concrete marker — a script host, a link, a schema type, a
printed date — and staleness is never claimed without a date to point at.

`audit_from_html(pages, base_url)` is the pure core and holds every rule, which
keeps the whole catalogue testable from handwritten fixtures. `audit_site()` is a
thin network wrapper that reuses the HTML `core.enrich.harvest_site` already
fetched whenever it is handed any, because downloading the same six pages twice
per lead is the difference between a scrape that finishes and one that does not.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import gzip
import html as _html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from core.templates import AUTO_ARMY_SERVICES

# ── Service resolution ──

# lower() -> the catalogue's own spelling. Category names count here: a gap is
# sometimes closed by a whole line of work rather than by one item, and
# "CRM & Sales Automation" is how the seller names that line. It is a heading,
# though, so it is a fact about the gap and never copy — `core.templates`
# resolves it to an entry beneath it before a prospect reads the sentence.
_CATALOGUE_INDEX: dict[str, str] = {}
for _category, _names in AUTO_ARMY_SERVICES.items():
    _CATALOGUE_INDEX.setdefault(_category.lower(), _category)
    for _name in _names:
        _CATALOGUE_INDEX.setdefault(_name.lower(), _name)
del _category, _names, _name


def _svc(*names: str) -> list[str]:
    """Catalogue spellings for `names`; anything not in the catalogue is dropped.

    The gap table below *is* the sales pitch, so it resolves every service
    through the catalogue rather than restating it. A service renamed in
    core.templates either follows here or vanishes from the pitch — it can never
    quietly become an offer that is not on the list.
    """
    return [_CATALOGUE_INDEX[n.lower()] for n in names if n.lower() in _CATALOGUE_INDEX]


# ── Gap catalogue ──

# Insertion order is the tie-break for equal severities, so the gaps that convert
# best sit at the top. Titles are lower-case noun phrases because they are dropped
# straight into a sentence ("One thing stands out on acme.ca: no online booking").
#
# `subject_phrase` is the same gap named neutrally, and it is what the subject
# line gets. A title works in the body because the sentence around it does the
# explaining; alone at the top of a stranger's inbox, "a site nobody has touched
# in years at Acme Plumbing Ltd" is a jab and the email is dead before it opens.
# Phrases stay short — the business name follows them inside a 55-character cap.
GAP_CATALOGUE: dict[str, dict] = {
    "no_online_booking": {
        "title": "no online booking", "severity": 3,
        "subject_phrase": "online booking",
        "services": _svc("appointment booking", "Lead Automation"),
    },
    "no_crm_signals": {
        "title": "no CRM behind the form", "severity": 3,
        "subject_phrase": "leads from the contact form",
        "services": _svc("CRM & Sales Automation", "automatically add leads to CRM"),
    },
    "contact_form_only": {
        "title": "a contact form and nothing else", "severity": 3,
        "subject_phrase": "the contact form",
        "services": _svc("AI lead qualification", "automatic follow-ups"),
    },
    "no_lead_capture": {
        "title": "no way to capture a lead", "severity": 3,
        "subject_phrase": "capturing leads from the site",
        "services": _svc("Lead Generation", "Lead Automation"),
    },
    "no_live_chat": {
        "title": "no live chat", "severity": 2,
        "subject_phrase": "live chat",
        "services": _svc("AI customer-support agents", "AI lead qualification"),
    },
    "quote_by_form": {
        "title": "quotes handled by hand", "severity": 2,
        "subject_phrase": "how quotes get handled",
        "services": _svc("AI lead scoring", "AI decision/triage systems"),
    },
    "no_analytics": {
        "title": "nothing measuring the site", "severity": 2,
        "subject_phrase": "measuring the site",
        # Not "reporting" beside "automated reports": the pair is one offer said
        # twice, and the second word alone reads as a heading in a list.
        "services": _svc("automated reports", "competitor monitoring"),
    },
    "careers_manual": {
        "title": "careers handled manually", "severity": 2,
        "subject_phrase": "the careers page",
        # Not "HR processes" in front: it is a real catalogue entry, but in the
        # slot that reads "the fix is ___" it names a department rather than a
        # thing that gets built. What the gap is about is CVs arriving to be
        # read and sorted, and a hire that then has to be walked through a week
        # of paperwork by hand.
        "services": _svc("employee onboarding", "AI document/data extraction"),
    },
    "ecommerce_manual": {
        "title": "orders handled by hand", "severity": 2,
        "subject_phrase": "order handling",
        # The spec's "Order automation" is not a catalogue service; the closest
        # real ones are the order workflow item and its parent line.
        "services": _svc("purchase/order workflows", "Business Process Automation"),
    },
    "pdf_forms": {
        "title": "paperwork handed out as PDFs", "severity": 2,
        "subject_phrase": "the PDF forms",
        "services": _svc("Document Automation", "PDF/document data extraction"),
    },
    "multi_location": {
        "title": "several locations, one inbox", "severity": 2,
        "subject_phrase": "running several locations",
        # Same rule as no_analytics, and the same reason: "reporting" beside
        # "automated reports" is one offer said twice. The second slot goes to
        # what the gap is actually about — a message arriving for one branch and
        # having to find its way to that branch.
        "services": _svc("automated reports", "lead assignment"),
    },
    "no_mobile": {
        "title": "no mobile layout", "severity": 2,
        "subject_phrase": "the site on phones",
        # Empty on purpose. The catalogue automates the work behind a business;
        # it does not build websites, and nothing in it makes a desktop layout
        # fit a phone. An offer here would have to be borrowed from an unrelated
        # line, and the reader parses "no mobile layout, so we build approval
        # systems" as a broken mail merge. `_gaps` sorts a gap carrying no
        # services behind every gap that carries one, so this is evidence and a
        # score, and it never becomes the sentence an offer has to follow.
        "services": [],
    },
    "stale_blog": {
        "title": "a blog that has gone quiet", "severity": 1,
        "subject_phrase": "the blog",
        "services": _svc("AI content generation", "content pipelines"),
    },
    "no_social_presence": {
        "title": "no social profiles linked", "severity": 1,
        "subject_phrase": "social profiles",
        "services": _svc("social media workflows", "Marketing Automation"),
    },
    "stale_site": {
        "title": "a site nobody has touched in years", "severity": 1,
        "subject_phrase": "keeping the site current",
        # This one does map, and to the same work as `stale_blog`: the finding is
        # that nobody remembers to put anything new on the site, and what the
        # catalogue sells against that is content that arrives without anybody
        # remembering. The subject phrase already says it — "keeping the site
        # current".
        "services": _svc("AI content generation", "content pipelines"),
    },
    "slow_site": {
        "title": "a slow site", "severity": 1,
        "subject_phrase": "the site speed",
        # Empty for the reason `no_mobile` is empty: page speed is hosting,
        # images and front-end work, and the catalogue sells none of it.
        "services": [],
    },
    "no_schema": {
        "title": "nothing search engines can read", "severity": 1,
        "subject_phrase": "search engine markup",
        "services": _svc("SEO automation"),
    },
    "price_opaque": {
        "title": "no pricing anywhere", "severity": 1,
        "subject_phrase": "pricing on the site",
        "services": _svc("AI lead qualification"),
    },
}

# A blog is only worth mentioning as stale once it is well past "we have been
# busy". Fourteen months clears an annual post and a slow winter both.
STALE_BLOG_DAYS = 425

# ── Fingerprints ──

# Structural markers only: a script host, a build path, a generator tag. The bare
# vendor name is deliberately absent from most lists — "proudly built with
# WordPress" in a footer is not the same as running it.
_CMS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wordpress", ("/wp-content/", "/wp-includes/", "/wp-json", "wp-emoji-release",
                   'content="wordpress', "content='wordpress")),
    ("wix", ("static.wixstatic.com", "wix-code", "wixstatic.com", "_wixcssimports",
             "static.parastorage.com", 'content="wix.com')),
    ("squarespace", ("squarespace-cdn.com", "static.squarespace.com",
                     "static.squarespace_context", "squarespace.com/universal",
                     'content="squarespace')),
    ("shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com",
                 "shopify-features", "/cdn/shop/")),
    ("webflow", ("assets.website-files.com", "assets-global.website-files.com",
                 "data-wf-page", "data-wf-site", "webflow.js")),
    ("duda", ("irp.cdn-website.com", "static.cdn-website.com", "dudamobile.com",
              "d1yrv3vfvibqsm.cloudfront.net")),
    ("godaddy", ("img1.wsimg.com", "wsimg.com/blobby", "godaddy website builder")),
    ("weebly", ("editmysite.com", "weeblycloud", "weebly.com/uploads",
                'content="weebly')),
    ("joomla", ("/media/jui/", "/media/system/js/", "joomla-script-options",
                'content="joomla')),
    ("drupal", ("drupal.settings", "/sites/default/files", "drupal-settings-json",
                'content="drupal')),
)

_ECOMMERCE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com", "/cdn/shop/")),
    ("woocommerce", ("woocommerce", "wc-ajax", "/plugins/woocommerce", "wc-add-to-cart")),
    ("bigcommerce", ("cdn11.bigcommerce.com", "bigcommerce.com/stencil", "bigcommerce.js")),
    ("magento", ("/static/version", "mage/cookies", "magento_", "/pub/static/frontend")),
)

_ANALYTICS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ga4", ("googletagmanager.com/gtag/js", "gtag/js?id=g-", "gtag('config'", 'gtag("config"')),
    ("gtm", ("googletagmanager.com/gtm.js", "googletagmanager.com/ns.html", "gtm-")),
    ("universal_analytics", ("google-analytics.com/analytics.js", "ga('create'", 'ga("create"')),
    ("meta_pixel", ("connect.facebook.net", "fbevents.js", "fbq('init'", 'fbq("init"')),
    ("hotjar", ("static.hotjar.com", "hotjar.com/c/hotjar", "_hjsettings")),
    ("clarity", ("clarity.ms",)),
    ("linkedin_insight", ("snap.licdn.com", "_linkedin_partner_id")),
    ("tiktok_pixel", ("analytics.tiktok.com",)),
    ("plausible", ("plausible.io/js",)),
    ("matomo", ("matomo.js", "piwik.js")),
    ("fathom", ("cdn.usefathom.com",)),
)

_CHAT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("intercom", ("widget.intercom.io", "intercomsettings", "js.intercomcdn.com")),
    ("tawk", ("embed.tawk.to", "tawk.to/chat", "tawk_api")),
    ("drift", ("js.driftt.com", "drift.load", "driftt.com")),
    ("crisp", ("client.crisp.chat", "$crisp", "crisp_website_id")),
    ("tidio", ("code.tidio.co", "tidiochat")),
    ("livechat", ("cdn.livechatinc.com", "livechatinc.com", "__lc.license")),
    ("messenger", ("fb-customerchat", "customerchat.js", "fb-customer-chat")),
)

_BOOKING_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("calendly", ("calendly.com",)),
    ("acuity", ("acuityscheduling.com", "squarespacescheduling.com")),
    ("setmore", ("setmore.com", "my.setmore")),
    ("square", ("squareup.com/appointments", "book.squareup.com", "square.site/book")),
    ("opentable", ("opentable.com", "opentable.ca")),
    ("resy", ("resy.com",)),
    ("housecallpro", ("housecallpro.com", "book.housecallpro")),
    ("servicetitan", ("servicetitan.com", "st-booking", "servicetitan.io")),
    ("mindbody", ("mindbodyonline.com", "mindbody.io", "healcode")),
)

_CRM_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hubspot", ("js.hs-scripts.com", "js.hsforms.net", "hs-analytics.net",
                 "hubspot.com/cta", "js.hscollectedforms.net")),
    ("salesforce", ("pardot.com", "pi.pardot.com", "webto.salesforce.com",
                    "salesforceliveagent", "force.com")),
    ("zoho", ("salesiq.zoho", "zohopublic", "crm.zoho", "zohoforms")),
    ("pipedrive", ("pipedrive.com", "pipedriveassets.com", "leadbooster")),
    ("activecampaign", ("activehosted.com", "prism.app-us1.com", "activecampaign.com")),
    ("mailchimp", ("chimpstatic.com", "list-manage.com", "mailchi.mp",
                   "mc.us.list-manage")),
    ("klaviyo", ("static.klaviyo.com", "klaviyo.com/onsite", "static-tracking.klaviyo")),
)

_FRAMEWORK_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nextjs", ("__next_data__", "/_next/static")),
    ("nuxt", ("__nuxt__", "/_nuxt/")),
    ("react", ("data-reactroot", "react-dom", "_reactlistening", "react.production.min")),
    ("vue", ("vue.runtime", "vue.min.js", "data-v-app", "__vue__")),
    ("angular", ("ng-version", "angular.min.js", "ng-app=")),
    ("svelte", ("svelte-", "/_app/immutable")),
    ("jquery", ("jquery.min.js", "jquery-3", "jquery/1.", "jquery/2.")),
)

# Form embeds that render no <form> of their own but are still a lead route.
_FORM_EMBEDS = ("jotform", "typeform", "formstack", "wufoo", "docs.google.com/forms",
                "gravityforms", "hsforms.net", "formidable", "cognitoforms")

# schema.org types that mean "a place customers walk into".
_LOCALBUSINESS_TYPES = (
    "localbusiness", "restaurant", "plumber", "electrician", "dentist", "hvacbusiness",
    "generalcontractor", "roofingcontractor", "homeandconstructionbusiness", "store",
    "medicalbusiness", "healthandbeautybusiness", "beautysalon", "hairsalon", "spa",
    "automotivebusiness", "autorepair", "legalservice", "attorney", "accountingservice",
    "veterinarycare", "childcare", "professionalservice", "foodestablishment",
    "lodgingbusiness", "realestateagent", "financialservice", "movingcompany",
    "cleaningservice", "locksmith", "pestcontrol", "landscaper", "daycare",
)

# ── HTML helpers ──

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Third-party pages are unbounded; everything past this is boilerplate or data.
_SCAN_CAP = 600_000
_READ_CAP = 900_000

_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")
_A_RE = re.compile(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"']*)["'][^>]*>(.*?)</a>""")
_HEAD_RE = re.compile(r"(?is)<h([1-3])\b[^>]*>(.*?)</h\1\s*>")
_TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")
_META_RE = re.compile(r"(?is)<meta\b[^>]*>")
_ATTR_RE = re.compile(r"""(?is)([a-z0-9_:\-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_FORM_RE = re.compile(r"(?is)<form\b.*?</form\s*>")
_FORM_OPEN_RE = re.compile(r"(?i)<form\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\(\d{3}\)|\b\d{3})[\s.\-]\d{3}[\s.\-]\d{4}\b|\+\d{1,3}[\s.\-]?\d{2,4}[\s.\-]?\d{3,4}[\s.\-]?\d{2,4}")
_MONEY_RE = re.compile(r"[$£€]\s?\d{1,3}(?:[,.\s]\d{3})*(?:\.\d{2})?\b")
_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,20}(?:(\d{4})\s*[-–—]\s*)?(\d{4})", re.I)
_ASSET_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|svg|ico|css|js|json|xml|zip|rar|mp4|mp3|avi|mov|"
    r"woff2?|ttf|eot|doc|docx|xls|xlsx|csv)(?:$|[?#])", re.I)

_SOCIAL_HOSTS = ("facebook.com", "fb.com", "instagram.com", "linkedin.com",
                 "twitter.com", "x.com", "youtube.com", "tiktok.com")
_SOCIAL_JUNK_RE = re.compile(
    r"(?:/sharer|/share\.php|/intent/|/plugins/|/dialog/|/tr\?|/hashtag/)", re.I)
# Feed widgets render their profile links in JavaScript, so the markup shows the
# plugin and no <a>. Telling a business with an Instagram feed on the home page
# that it has no social presence is exactly the kind of miss that kills a reply.
_SOCIAL_EMBEDS = ("instagram-feed", "smashballoon", "sbi_", "facebook.com/plugins/page",
                  "fb-page", "elfsight", "juicer.io", "curator.io", "taggbox",
                  "twitter-timeline", "lightwidget")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_MDY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.I)
_DMY_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b",
    re.I)

_POSTAL_RES = (
    re.compile(r"\b[A-Z]\d[A-Z][ \-]?\d[A-Z]\d\b"),                    # Canada
    re.compile(r",\s*[A-Z]{2}\s+(\d{5})(?:-\d{4})?\b"),                # US, needs a state
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b"),              # UK
)

# Nav labels that describe the site, not what the business sells.
_NAV_STOPWORDS = frozenset({
    "home", "about", "about us", "contact", "contact us", "blog", "news", "careers",
    "jobs", "gallery", "portfolio", "testimonials", "reviews", "faq", "faqs", "privacy",
    "privacy policy", "terms", "terms of service", "sitemap", "login", "log in",
    "sign in", "sign up", "register", "cart", "checkout", "search", "menu", "shop",
    "store", "book now", "book online", "get a quote", "request a quote", "free quote",
    "call us", "email us", "locations", "our team", "team", "services", "our services",
    "what we do", "pricing", "prices", "français", "english", "espanol", "español",
    "more", "read more", "learn more", "next", "previous", "skip to content", "close",
})

# Label openings that mark a button rather than a thing the business sells.
_CTA_PREFIXES = ("get a", "get your", "get in", "request a", "book a", "book your",
                 "call ", "schedule a", "claim your", "start your", "contact ",
                 "learn ", "view ", "see our", "read ", "download ", "click ")

_SERVICE_WORDS = (
    "repair", "install", "service", "cleaning", "maintenance", "inspection",
    "consultation", "treatment", "appointment", "booking", "estimate", "quote",
    "emergency", "replacement", "removal", "design", "training", "therapy", "grooming",
    "detailing", "renovation", "landscaping", "roofing", "plumbing", "heating",
    "cooling", "electrical", "dental", "legal", "accounting", "tutoring", "catering",
)

_APPOINTMENT_WORDS = (
    "appointment", "book a", "booking", "schedule a", "consultation", "call us to book",
    "same day service", "emergency", "estimate", "free quote", "walk-in", "reservation",
)

_QUOTE_PHRASES = (
    "request a quote", "get a quote", "free quote", "request an estimate",
    "free estimate", "get an estimate", "request a proposal", "request pricing",
    "get a free quote", "book an estimate",
)

_CAREER_HREFS = ("/careers", "/career", "/jobs", "/join-us", "/join-our-team",
                 "/work-with-us", "/employment", "/vacancies", "/hiring")
_CAREER_WORDS = ("careers", "we're hiring", "we are hiring", "join our team",
                 "current openings", "job openings", "now hiring", "apply now")

_BLOG_HREF_RE = re.compile(r"/(?:blog|news|articles|insights|updates|posts|stories)(?:/|$|\?)", re.I)
_BLOG_WORDS = frozenset({"blog", "news", "articles", "insights", "stories"})

_PDF_FORM_WORDS = ("form", "application", "intake", "waiver", "agreement", "contract",
                   "registration", "questionnaire", "checklist", "new patient",
                   "credit app", "quote request", "estimate request")

# Verbs a link label opens with before it gets to the name of the thing.
_DOC_LEAD_RE = re.compile(
    r"(?i)^(?:download|get|view|open|print|complete|fill\s+(?:in|out))\b\s*(?:the|our|your|a)?\s*")
_DOC_NAME_RE = re.compile(r"[a-z][a-z' ]+[a-z]")

_BOOKING_LINK_RE = re.compile(
    r"/(?:book|booking|book-online|book-now|schedule|scheduling|appointment|appointments|"
    r"reserve|reservations|make-an-appointment|request-appointment)(?:/|$|\?)", re.I)
_BOOKING_TEXT = ("book now", "book online", "book an appointment", "schedule an appointment",
                 "schedule online", "make an appointment", "request an appointment",
                 "reserve a table", "book a table", "book a service")

_CALL_CTA = ("call now", "call us now", "call today", "call us today", "give us a call",
             "speak to", "24/7", "call anytime")


def _clean_text(fragment: str) -> str:
    """Tag-free, entity-decoded, whitespace-collapsed text from a markup fragment."""
    out = _TAG_RE.sub(" ", fragment or "")
    out = _html.unescape(out)
    return _WS_RE.sub(" ", out).strip()


def _visible_text(html: str) -> str:
    body = _COMMENT_RE.sub(" ", html)
    body = _SCRIPT_RE.sub(" ", body)
    return _clean_text(body)


def _attrs(tag: str) -> dict:
    out = {}
    for name, dq, sq, bare in _ATTR_RE.findall(tag):
        out[name.lower()] = dq or sq or bare
    return out


def _host(url: str) -> str:
    host = urllib.parse.urlsplit(url if "//" in url else "//" + url).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _path(url: str) -> str:
    return urllib.parse.urlsplit(url).path or "/"


class _Page:
    """One fetched page, pre-chewed into the four views detection needs."""

    __slots__ = ("url", "path", "html", "low", "text", "links")

    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.path = _path(url)
        self.html = html[:_SCAN_CAP]
        self.low = self.html.lower()
        self.text = _visible_text(self.html)
        self.links = [(href.strip(), _clean_text(label)[:80])
                      for href, label in _A_RE.findall(self.html)]


def _hit(low: str, markers) -> int:
    return sum(1 for m in markers if m in low)


def _best_vendor(low: str, table) -> str:
    """Highest-scoring vendor in `table`; ties go to catalogue order."""
    best, best_score = "", 0
    for name, markers in table:
        score = _hit(low, markers)
        if score > best_score:
            best, best_score = name, score
    return best


def _all_vendors(low: str, table) -> list[str]:
    return [name for name, markers in table if _hit(low, markers)]


# ── Dates ──


def _parse_dates(blob: str, today: datetime.date) -> list[datetime.date]:
    """Every plausible past date in `blob`. Future and pre-2000 dates are noise."""
    found: list[datetime.date] = []

    def _keep(year: int, month: int, day: int) -> None:
        try:
            when = datetime.date(year, month, day)
        except ValueError:
            return
        if datetime.date(2000, 1, 1) <= when <= today:
            found.append(when)

    for year, month, day in _ISO_DATE_RE.findall(blob):
        _keep(int(year), int(month), int(day))
    for month, day, year in _MDY_RE.findall(blob):
        _keep(int(year), _MONTHS[month[:3].lower()], int(day))
    for day, month, year in _DMY_RE.findall(blob):
        _keep(int(year), _MONTHS[month[:3].lower()], int(day))
    return found


def _latest_content_date(pages, today: datetime.date):
    """Newest date the site shows for its own content, or None.

    Machine-readable dates (`<time datetime>`, `article:published_time`,
    JSON-LD `datePublished`) are trusted on their own. Dates in visible text are
    only read from pages that look like a blog or article, because "call before
    31 December" on a promo page is not a publishing date.
    """
    dates: list[datetime.date] = []
    for page in pages:
        machine = " ".join(re.findall(r'(?is)datetime\s*=\s*["\']([^"\']{4,40})["\']', page.html))
        machine += " " + " ".join(re.findall(
            r'(?is)"date(?:published|modified|created)"\s*:\s*"([^"]{4,40})"', page.html))
        machine += " " + " ".join(re.findall(
            r'(?is)<meta[^>]+article:(?:published|modified)_time[^>]+content\s*=\s*["\']([^"\']+)["\']',
            page.html))
        dates.extend(_parse_dates(machine, today))

        article_like = bool(_BLOG_HREF_RE.search(page.path)) or "<article" in page.low
        if article_like:
            dates.extend(_parse_dates(page.text[:40_000], today))
    return max(dates) if dates else None


# ── Technology ──


def _tech(pages) -> dict:
    low = "\n".join(p.low for p in pages)

    forms = 0
    for page in pages:
        forms += len(_FORM_OPEN_RE.findall(page.html))

    cms = _best_vendor(low, _CMS_MARKERS)
    ecommerce = _best_vendor(low, _ECOMMERCE_MARKERS)
    # A Shopify storefront is its own CMS; a WooCommerce shop is still WordPress.
    if not cms and ecommerce == "shopify":
        cms = "shopify"
    if not cms and pages:
        cms = "custom"

    return {
        "cms": cms,
        "ecommerce": ecommerce,
        "analytics": _all_vendors(low, _ANALYTICS_MARKERS),
        "chat": _best_vendor(low, _CHAT_MARKERS),
        "booking": _best_vendor(low, _BOOKING_MARKERS),
        "crm": _best_vendor(low, _CRM_MARKERS),
        "forms": forms,
        "frameworks": _all_vendors(low, _FRAMEWORK_MARKERS),
    }


# ── Page facts ──


def _is_contact_form(block: str) -> bool:
    """A form a prospect writes to, as opposed to the site's search box."""
    low = block.lower()
    opening = low[:low.find(">") + 1] if ">" in low else low
    if "search" in opening or 'role="search"' in low:
        return False
    if 'type="search"' in low or "type='search'" in low:
        return False
    if "<textarea" in low or 'type="email"' in low or "type='email'" in low:
        return True
    return bool(re.search(r"""name\s*=\s*["'][^"']*(?:email|message|enquiry|inquiry|comment|phone)""", low))


def _document_name(label: str, href: str) -> str:
    """What a downloadable form is called, in the words its owner uses for it.

    Their own link text first, then the file name with the slug taken out of it.
    Anything still shaped like machinery is no name at all and "" comes back, so
    the sentence can fall to "the paperwork" — which beats handing the reader
    employment-application-form.pdf and calling it evidence.
    """
    stem = href.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    stem = _WS_RE.sub(" ", re.sub(r"[\-_+%]+", " ", stem))
    for candidate in (label, stem):
        name = _DOC_LEAD_RE.sub("", str(candidate or "").strip()).strip().lower()
        if not (8 <= len(name) <= 40) or len(name.split()) < 2:
            continue
        if not _DOC_NAME_RE.fullmatch(name):
            continue
        if not any(w in name for w in _PDF_FORM_WORDS):
            continue
        return name if name.startswith("the ") else "the " + name
    return ""


def _social_links(pages) -> list[str]:
    out = []
    for page in pages:
        for href, _label in page.links:
            low = href.lower()
            if not any(h in low for h in _SOCIAL_HOSTS):
                continue
            if _SOCIAL_JUNK_RE.search(low):
                continue
            # A bare host with no profile path is a broken footer icon.
            if not urllib.parse.urlsplit(low if "//" in low else "//" + low).path.strip("/"):
                continue
            out.append(href)
    return out


def _location_count(pages) -> int:
    codes: set[str] = set()
    for page in pages:
        text = page.text[:60_000]
        for pattern in _POSTAL_RES:
            for match in pattern.findall(text):
                codes.add(re.sub(r"[\s\-]", "", match).upper())
    if len(codes) >= 2:
        return len(codes)
    addresses = sum(len(re.findall(r"(?i)<address\b", p.html)) for p in pages)
    return max(addresses, 1 if codes else 0)


def _copyright_year(pages, today: datetime.date) -> int:
    best = 0
    for page in pages:
        # The notice lives at the bottom; searching the tail avoids picking up a
        # 1998 "established in" line from the body copy.
        for start, end in _COPYRIGHT_RE.findall(page.text[-4000:] or page.text):
            for year in (end, start):
                if not year:
                    continue
                value = int(year)
                if 1990 <= value <= today.year + 1:
                    best = max(best, value)
    return best


def _signals(pages, tech: dict, base_url: str, load_ms: int) -> tuple[dict, dict]:
    """Every boolean the gap table reads, plus the evidence bits it quotes."""
    today = datetime.date.today()
    html_all = "\n".join(p.html for p in pages)
    low = html_all.lower()
    text = " ".join(p.text for p in pages)
    text_low = text.lower()
    facts: dict = {}

    hrefs = [(href.lower(), label.lower()) for page in pages for href, label in page.links]

    # ── lead routes ──
    contact_form = False
    for page in pages:
        blocks = _FORM_RE.findall(page.html)
        # Some builders never close the <form>, so fall back to the whole page.
        if any(_is_contact_form(b) for b in blocks) or (
                not blocks and _FORM_OPEN_RE.search(page.html) and _is_contact_form(page.html)):
            contact_form = True
            break
    if not contact_form and any(e in low for e in _FORM_EMBEDS):
        contact_form = True

    booking_link = any(_BOOKING_LINK_RE.search(href) or any(t in label for t in _BOOKING_TEXT)
                       for href, label in hrefs)
    has_booking = bool(tech["booking"]) or booking_link or any(t in text_low for t in _BOOKING_TEXT)
    if tech["booking"]:
        facts["booking_vendor"] = tech["booking"]

    has_phone = "tel:" in low or bool(_PHONE_RE.search(text))
    # A tappable number is a real second route in, so it disqualifies
    # "the form is the only way to reach them" on its own.
    call_cta = (any(href.startswith("tel:") for href, _ in hrefs)
                or (has_phone and any(c in text_low for c in _CALL_CTA)))
    has_email = "mailto:" in low or "data-cfemail" in low or bool(_EMAIL_RE.search(text))

    newsletter = any(w in text_low for w in ("newsletter", "mailing list", "subscribe to our"))
    newsletter = newsletter or "list-manage.com" in low or "klaviyo" in low

    quote_phrase = next((q for q in _QUOTE_PHRASES if q in text_low), "")
    has_quote_form = bool(quote_phrase) and (contact_form or tech["forms"] > 0)
    if quote_phrase:
        facts["quote_phrase"] = quote_phrase

    # ── content ──
    # Word-level matching, because "Newsletter" is not a news section.
    has_blog = any(_BLOG_HREF_RE.search(href) or (set(re.findall(r"[a-z]+", label)) & _BLOG_WORDS)
                   for href, label in hrefs)
    has_blog = has_blog or any(_BLOG_HREF_RE.search(p.path) for p in pages)
    latest = _latest_content_date(pages, today)
    blog_year = latest.year if latest else 0
    blog_stale = bool(has_blog and latest and (today - latest).days > STALE_BLOG_DAYS)
    if latest:
        facts["latest_date"] = latest.strftime("%B %Y")

    careers = any(any(c in href for c in _CAREER_HREFS) for href, _ in hrefs)
    careers = careers or any(w in text_low for w in _CAREER_WORDS)

    pdf_form = ""
    for href, label in hrefs:
        if ".pdf" not in href:
            continue
        if any(w in href or w in label for w in _PDF_FORM_WORDS):
            pdf_form = href
            facts["pdf_form"] = _document_name(label, href)
            break

    pricing_link = any("/pricing" in href or "/prices" in href or "/rates" in href
                       or label in ("pricing", "prices", "our prices", "rates")
                       for href, label in hrefs)
    money_hits = len(_MONEY_RE.findall(text))
    has_pricing = pricing_link or money_hits >= 3 or "our prices" in text_low

    testimonials = any(w in text_low for w in (
        "testimonial", "what our clients say", "what our customers say", "customer reviews",
        "google reviews", "trustpilot", "5-star", "five star"))
    gallery = any("/gallery" in href or "/portfolio" in href or "/our-work" in href
                  or "/projects" in href or label in ("gallery", "portfolio", "our work")
                  for href, label in hrefs)
    gallery = gallery or any(w in low for w in ("fancybox", "photoswipe", "lightbox"))

    locations = _location_count(pages)

    # ── schema ──
    has_schema = "application/ld+json" in low or "schema.org" in low
    schema_types = " ".join(re.findall(r'(?is)"@type"\s*:\s*"([^"]{2,40})"', html_all)).lower()
    schema_types += " " + " ".join(re.findall(
        r"""(?is)itemtype\s*=\s*["'][^"']*schema\.org/([A-Za-z]+)""", html_all)).lower()
    has_local_schema = any(t in schema_types for t in _LOCALBUSINESS_TYPES)

    viewport = bool(re.search(r"""(?is)<meta[^>]+name\s*=\s*["']viewport["'][^>]*>""", html_all))
    copyright_year = _copyright_year(pages, today)

    total_kb = sum(len(p.html) for p in pages) // 1024
    avg_kb = int(total_kb / len(pages)) if pages else 0

    signals = {
        "has_ssl": base_url.lower().startswith("https://"),
        "mobile_viewport": viewport,
        "has_schema": has_schema,
        "has_localbusiness_schema": has_local_schema,
        "has_blog": has_blog,
        "blog_stale": blog_stale,
        "blog_year": blog_year,
        "has_online_booking": has_booking,
        "has_live_chat": bool(tech["chat"]),
        "has_contact_form": contact_form,
        "has_phone": has_phone,
        "has_email": has_email,
        "has_social": bool(_social_links(pages)) or any(e in low for e in _SOCIAL_EMBEDS),
        "has_pricing": has_pricing,
        "has_testimonials": testimonials,
        "has_gallery": gallery,
        "has_careers": careers,
        "has_newsletter": newsletter,
        "has_pdf_forms": bool(pdf_form),
        "has_multiple_locations": locations >= 2,
        "location_count": locations,
        "has_quote_form": has_quote_form,
        "copyright_year": copyright_year,
        "stale_copyright": bool(copyright_year and copyright_year < today.year - 1),
        "avg_page_kb": avg_kb,
        "slow": load_ms > 3000,
    }
    facts["call_cta"] = call_cta
    facts["appointment_shaped"] = any(w in text_low for w in _APPOINTMENT_WORDS)
    return signals, facts


# ── Services offered ──


def _services(pages, title: str, brand: str) -> list[str]:
    """Up to 12 things the business sells, in the words the site uses."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        label = _WS_RE.sub(" ", str(label or "")).strip(" -–—|·•,:")
        if not (2 < len(label) <= 40) or len(label.split()) > 6:
            return
        key = label.lower()
        if key in _NAV_STOPWORDS or key in seen or (brand and key == brand.lower()):
            return
        if key.startswith(_CTA_PREFIXES):
            return
        if not re.search(r"[A-Za-z]{3}", label) or key.startswith(("http", "©")):
            return
        seen.add(key)
        out.append(label)

    # A /services/ URL is the site telling you outright.
    for page in pages:
        for href, label in page.links:
            if re.search(r"/(?:services|service|treatments|solutions|what-we-do)/[a-z0-9\-]{3,}",
                         href, re.I):
                _add(label)
    for page in pages:
        for href, label in page.links:
            low = label.lower()
            if any(w in low for w in _SERVICE_WORDS) and not href.lower().startswith(("mailto:", "tel:")):
                _add(label)
    for page in pages:
        for level, body in _HEAD_RE.findall(page.html):
            if level == "1":
                continue
            label = _clean_text(body)
            if any(w in label.lower() for w in _SERVICE_WORDS):
                _add(label)
    if len(out) < 3 and title:
        for part in re.split(r"[|•·\-–—]", title):
            if any(w in part.lower() for w in _SERVICE_WORDS):
                _add(part)
    return out[:12]


def _meta(pages, base_url: str) -> tuple[str, str, str, str]:
    """(title, description, h1, brand) read from the home page."""
    if not pages:
        return "", "", "", ""
    home = pages[0]

    title_match = _TITLE_RE.search(home.html)
    title = _clean_text(title_match.group(1))[:160] if title_match else ""

    description = ""
    site_name = ""
    for tag in _META_RE.findall(home.html):
        attrs = _attrs(tag)
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        content = _clean_text(attrs.get("content") or "")
        if key in ("description", "og:description") and content and not description:
            description = content[:300]
        elif key == "og:site_name" and content and not site_name:
            site_name = content[:80]

    h1 = ""
    for level, body in _HEAD_RE.findall(home.html):
        if level == "1":
            h1 = _clean_text(body)[:160]
            break

    brand = site_name
    if not brand:
        schema_name = re.search(r'(?is)"name"\s*:\s*"([^"]{2,80})"', home.html)
        if schema_name:
            brand = _clean_text(schema_name.group(1))
    if not brand and title:
        # "Acme Plumbing | Toronto Plumbers" -> the shorter, name-shaped half.
        parts = [p.strip() for p in re.split(r"[|•·]|\s[-–—]\s", title) if p.strip()]
        brand = parts[0] if parts else title
    if not brand:
        root = _host(base_url).split(".")[0]
        brand = root.replace("-", " ").title()
    return title, description, h1, brand[:80]


# ── Gaps ──


def _gap(code: str, evidence: str) -> dict:
    entry = GAP_CATALOGUE[code]
    return {
        "code": code,
        "title": entry["title"],
        "subject_phrase": entry["subject_phrase"],
        "severity": entry["severity"],
        "evidence": evidence[:120],
        "services": list(entry["services"]),
    }


def _gaps(tech: dict, signals: dict, facts: dict) -> list[dict]:
    """Fire the catalogue against the signals, with evidence a human could check.

    Evidence is read by the owner of the site, in a sentence that offers to help,
    so it says what *they* would find if they looked: a page to open, a phone to
    try it on, a line in the footer. It never reports what the crawler did.
    Nobody warms to a stranger who says he fetched six of their pages.

    Which is why almost none of these sentences is assembled. A value spliced in
    from crawler state arrives in the reader's own words only by luck — a path is
    "/" for a home-page form, a tally of `<form` tags counts the search box, a
    vendor key is spelled "woocommerce". Three values survive because the owner
    can walk to them: their own link text, the month on their newest post, and
    the year in their footer. Everything else is written out as a sentence.
    """
    fired: list[dict] = []

    appointment_shaped = signals["has_contact_form"] and (
        facts.get("appointment_shaped") or facts.get("has_services"))

    if not signals["has_online_booking"] and appointment_shaped:
        fired.append(_gap("no_online_booking",
                          "asking for a time means filling in the form and waiting "
                          "for someone to answer"))
    if not tech["crm"] and (signals["has_contact_form"] or tech["forms"]):
        fired.append(_gap("no_crm_signals",
                          "nothing is hooked up to file a name and a number after the "
                          "form is sent"))
    if (signals["has_contact_form"] and not signals["has_live_chat"]
            and not signals["has_online_booking"] and not facts.get("call_cta")):
        fired.append(_gap("contact_form_only",
                          "the form is the only way in: no chat, no booking, no number to tap"))
    if not signals["has_contact_form"] and not signals["has_newsletter"] and tech["forms"] == 0:
        fired.append(_gap("no_lead_capture",
                          "nothing on the site asks a visitor for a name or an email"))
    if not tech["chat"]:
        fired.append(_gap("no_live_chat",
                          "no chat box on the site, so a question waits for the phone or a form"))
    if signals["has_quote_form"]:
        fired.append(_gap("quote_by_form",
                          f'"{facts.get("quote_phrase", "request a quote")}" on the site '
                          "ends in a form somebody has to read"))
    if not tech["analytics"]:
        fired.append(_gap("no_analytics",
                          "no analytics on any page, so last month's visits went uncounted"))
    if signals["has_careers"]:
        fired.append(_gap("careers_manual",
                          "the site is advertising jobs, so CVs are arriving to be read and sorted"))
    if tech["ecommerce"]:
        fired.append(_gap("ecommerce_manual",
                          "there is a shop on the site, so every order needs picking, "
                          "invoicing and chasing"))
    if signals["has_pdf_forms"]:
        fired.append(_gap("pdf_forms",
                          f"{facts.get('pdf_form') or 'the paperwork'} is a PDF to print, "
                          "fill in and send back"))
    if signals["has_multiple_locations"]:
        fired.append(_gap("multi_location",
                          "every address on the site takes its own calls and answers "
                          "its own messages"))
    if not signals["mobile_viewport"]:
        fired.append(_gap("no_mobile",
                          "open the site on a phone and it loads the full desktop layout"))
    if signals["has_blog"] and signals["blog_stale"]:
        fired.append(_gap("stale_blog",
                          f"the newest post on the blog is dated {facts.get('latest_date', '')}"))
    if not signals["has_social"]:
        fired.append(_gap("no_social_presence",
                          "nothing on the site links out to Facebook, Instagram or LinkedIn"))
    if signals["stale_copyright"]:
        fired.append(_gap("stale_site", f"the footer still reads {signals['copyright_year']}"))
    if signals["slow"]:
        fired.append(_gap("slow_site",
                          "the home page takes a few seconds before anything shows up "
                          "on screen"))
    if not signals["has_schema"]:
        fired.append(_gap("no_schema",
                          "Google gets no hours, no prices and no reviews from the pages"))
    if not signals["has_pricing"] and not tech["ecommerce"]:
        fired.append(_gap("price_opaque",
                          "not a rate, a range or a starting figure on any page, so every "
                          "enquiry has to ask"))

    order = list(GAP_CATALOGUE)
    # A gap with nothing in the catalogue behind it sorts last whatever its
    # severity. The email names `gaps[0]` and then makes an offer, so a headline
    # with no offer behind it either borrows an unrelated one or leaves the
    # reader with a sentence that answers nothing. It still fires, still scores
    # and still reaches the model's brief, where it is an observation and not a
    # promise.
    fired.sort(key=lambda g: (not g["services"], -g["severity"], order.index(g["code"])))
    return fired


# Each gap past the headline is worth a little less than the one before it.
# Straight addition put every site with a contact form over the cap — three
# severity-3 gaps and a couple of severity-2s already total 117 — so the score
# column the operator sorts leads by read 100 the whole way down, and a joiner
# with one stale page outranked nobody. The taper is also the honest reading:
# the email names one gap and leans on a second, so a fourteenth gap is not
# fourteen times the opportunity, it is the same pitch with more behind it.
_GAP_DECAY = 0.7


def _score(gaps, reachable: bool, has_email: bool) -> int:
    total = 0.0
    weight = 9.0
    for gap in gaps:
        total += int(gap["severity"]) * weight
        weight *= _GAP_DECAY
    if reachable:
        total += 10
    if has_email:
        total += 5
    return max(0, min(100, round(total)))


# ── Result shape ──


def _blank(url: str, error: str = "") -> dict:
    return {
        "url": url, "final_url": "", "reachable": False, "status": 0,
        "load_ms": 0, "pages": [], "page_count": 0,
        "title": "", "description": "", "h1": "", "brand": "",
        "tech": {"cms": "", "ecommerce": "", "analytics": [], "chat": "", "booking": "",
                 "crm": "", "forms": 0, "frameworks": []},
        "services": [],
        "signals": {
            "has_ssl": False, "mobile_viewport": False, "has_schema": False,
            "has_localbusiness_schema": False, "has_blog": False, "blog_stale": False,
            "blog_year": 0, "has_online_booking": False, "has_live_chat": False,
            "has_contact_form": False, "has_phone": False, "has_email": False,
            "has_social": False, "has_pricing": False, "has_testimonials": False,
            "has_gallery": False, "has_careers": False, "has_newsletter": False,
            "has_pdf_forms": False, "has_multiple_locations": False, "location_count": 0,
            "has_quote_form": False, "copyright_year": 0, "stale_copyright": False,
            "avg_page_kb": 0, "slow": False,
        },
        "gaps": [], "opportunity_score": 0, "error": error,
    }


def _order_pages(pages: dict, base_url: str) -> list[_Page]:
    """Real pages, home first — every later step treats pages[0] as the home page."""
    items = []
    for url, html in (pages or {}).items():
        if isinstance(url, str) and isinstance(html, str) and html.strip():
            items.append((url, html))
    if not items:
        return []

    base_host = _host(base_url)

    def _rank(item) -> tuple:
        url = item[0]
        path = _path(url).rstrip("/")
        same_host = _host(url) == base_host if base_host else True
        return (0 if (same_host and path in ("", "/")) else 1, len(path), url)

    items.sort(key=_rank)
    return [_Page(url, html) for url, html in items]


def _audit(pages: dict, base_url: str, *, final_url: str = "", status: int = 0,
           load_ms: int = 0, error: str = "") -> dict:
    """The whole audit. Both public entry points land here."""
    ordered = _order_pages(pages, base_url)
    result = _blank(base_url, error)
    if not ordered:
        result["final_url"] = final_url or base_url
        result["status"] = status
        return result

    home_url = final_url or ordered[0].url or base_url
    tech = _tech(ordered)
    title, description, h1, brand = _meta(ordered, home_url)
    services = _services(ordered, title, brand)
    signals, facts = _signals(ordered, tech, home_url, load_ms)
    facts["has_services"] = bool(services)
    gaps = _gaps(tech, signals, facts)

    result.update({
        "url": base_url,
        "final_url": home_url,
        "reachable": True,
        "status": status or 200,
        "load_ms": load_ms,
        "pages": [p.url for p in ordered],
        "page_count": len(ordered),
        "title": title, "description": description, "h1": h1, "brand": brand,
        "tech": tech,
        "services": services,
        "signals": signals,
        "gaps": gaps,
        "opportunity_score": _score(gaps, True, signals["has_email"]),
    })
    return result


# ── Public API ──


def audit_from_html(pages: dict, base_url: str) -> dict:
    """Audit `{url: html}` with no network at all. Every detection rule lives here.

    `load_ms` is 0 and `slow` is therefore False: speed is a property of the
    fetch, not of the markup, so the pure path never claims it.
    """
    try:
        return _audit(pages, base_url or "")
    except Exception as exc:
        return _blank(base_url or "", f"{type(exc).__name__}: {exc}"[:200])


def audit_site(url: str, *, max_pages: int = 6, timeout: float = 8.0,
               prefetched: dict | None = None) -> dict:
    """Audit a live site, reusing `prefetched` HTML from `harvest_site` if given.

    Never raises and never blocks forever: one timed home fetch, then the best
    internal pages in parallel, each capped in size.
    """
    try:
        url = str(url or "").strip()
        if not url:
            return _blank("", "no url")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        usable = {k: v for k, v in (prefetched or {}).items()
                  if isinstance(k, str) and isinstance(v, str) and v.strip()}
        if usable:
            trimmed = dict(list(usable.items())[:max(1, max_pages)])
            return _audit(trimmed, url, final_url="", status=200, load_ms=0)

        started = time.perf_counter()
        home_html, final_url, status, error = _fetch(url, timeout)
        load_ms = int((time.perf_counter() - started) * 1000)
        if not home_html:
            return _blank(url, error or "empty response")

        pages = {final_url: home_html}
        targets = _crawl_targets(home_html, final_url, max(0, max_pages - 1))
        if targets:
            workers = min(len(targets), 6)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for target, (html, resolved, _status, _err) in zip(
                        targets, pool.map(lambda t: _fetch(t, timeout), targets)):
                    if html:
                        pages.setdefault(resolved or target, html)

        return _audit(pages, url, final_url=final_url, status=status, load_ms=load_ms)
    except Exception as exc:
        return _blank(str(url or ""), f"{type(exc).__name__}: {exc}"[:200])


# ── Fetching ──

# Which pages tell you most about how a business runs, best first.
_CRAWL_PRIORITY: tuple[tuple[str, ...], ...] = (
    ("/services", "/our-services", "/what-we-do", "/treatments", "/solutions"),
    ("/contact", "/contact-us", "/get-in-touch"),
    ("/about", "/about-us", "/our-story"),
    ("/book", "/booking", "/appointments", "/schedule", "/request-appointment"),
    ("/pricing", "/prices", "/rates", "/plans"),
    ("/careers", "/jobs", "/join-us", "/employment"),
    ("/locations", "/our-locations", "/branches", "/find-us"),
    ("/blog", "/news", "/articles", "/insights"),
    ("/shop", "/store", "/products", "/collections"),
)


def _crawl_targets(html: str, base_url: str, limit: int) -> list[str]:
    if limit <= 0:
        return []
    base_host = _host(base_url)
    seen: set[str] = {base_url.rstrip("/")}
    buckets: list[list[str]] = [[] for _ in _CRAWL_PRIORITY]

    for href, label in _A_RE.findall(html):
        href = href.strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        full = urllib.parse.urljoin(base_url, href).split("#")[0]
        if not full.startswith(("http://", "https://")) or _ASSET_RE.search(full):
            continue
        if base_host and _host(full) != base_host:
            continue
        key = full.rstrip("/")
        if key in seen:
            continue
        path_and_label = (_path(full) + " " + _clean_text(label)).lower()
        for index, words in enumerate(_CRAWL_PRIORITY):
            if any(w in path_and_label or w.lstrip("/") in path_and_label for w in words):
                seen.add(key)
                buckets[index].append(full)
                break

    targets: list[str] = []
    for bucket in buckets:
        for target in bucket:
            targets.append(target)
            if len(targets) >= limit:
                return targets
    return targets


_LEGACY_CHARSET_RE = re.compile(r"(?i)^(?:iso[\-_]?8859|latin[\-_]?\d|windows[\-_]?12|cp12)")


def _charset(headers, raw: bytes) -> str:
    """The charset the response declares, header first, then the markup. "" if none."""
    content_type = (headers.get("Content-Type") or "") if headers else ""
    match = re.search(r"charset\s*=\s*([A-Za-z0-9_\-]+)", content_type, re.I)
    if match:
        return match.group(1)
    match = re.search(rb'charset\s*=\s*["\']?([A-Za-z0-9_\-]+)', raw[:2048], re.I)
    if match:
        return match.group(1).decode("ascii", "replace")
    return ""


def _decode(raw: bytes, headers) -> str:
    """Page bytes to text, strictly where possible. Mirrors `core.enrich._decode_body`.

    A candidate only wins if it decodes *strictly*, so an undeclared latin-1 page
    falls through to cp1252 instead of arriving as "Caf� Andr�".
    `errors="replace"` is the last resort and nothing else: the brand and title
    read here are most of what `digest()` sends the model, and U+FFFD in them
    becomes U+FFFD in a live email.

    A legacy single-byte label never fails, because every byte is a character in
    it, and stale `charset=iso-8859-1` defaults sit on plenty of utf-8 pages. When
    the bytes are genuinely multi-byte utf-8 they are utf-8, whatever the header says.
    """
    declared = _charset(headers, raw).strip().strip("'\"")
    candidates = [declared, "utf-8", "cp1252"]
    if declared and _LEGACY_CHARSET_RE.match(declared) and not raw.isascii():
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    for name in candidates:
        if not name:
            continue
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _fetch(url: str, timeout: float) -> tuple[str, str, int, str]:
    """(html, final_url, status, error). Capped read, decoded honestly, never raises."""
    request = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        # No brotli: stdlib cannot decode it and a garbled body reads as an empty site.
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_READ_CAP)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            final_url = response.geturl() or url
            status = getattr(response, "status", 0) or response.getcode() or 0
            headers = response.headers
    except urllib.error.HTTPError as exc:
        return "", url, getattr(exc, "code", 0) or 0, f"HTTP {getattr(exc, 'code', '')}"
    except Exception as exc:
        return "", url, 0, f"{type(exc).__name__}: {exc}"[:200]

    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                pass

    return _decode(raw, headers), final_url, status, ""


# ── Digest ──


def _digest_lines(audit: dict, services: int, signals: int, gaps: int) -> list[str]:
    tech = audit.get("tech") or {}
    sig = audit.get("signals") or {}
    domain = _host(str(audit.get("final_url") or audit.get("url") or ""))
    brand = str(audit.get("brand") or audit.get("title") or "").strip()

    lines = ["SITE: " + " | ".join(p for p in (domain, brand[:60]) if p)]

    offered = [str(s).strip() for s in (audit.get("services") or []) if str(s).strip()]
    if offered and services > 0:
        lines.append("WHAT: " + ", ".join(offered[:services]))

    stack = [str(tech.get("cms") or "custom site")]
    if tech.get("ecommerce"):
        stack.append(f"{tech['ecommerce']} store")
    stack.append(str(tech.get("chat") or "no chat"))
    stack.append(str(tech.get("booking") or "no booking"))
    stack.append(str(tech.get("crm") or "no crm"))
    analytics = [str(a) for a in (tech.get("analytics") or [])][:3]
    stack.append("+".join(analytics) if analytics else "no analytics")
    lines.append("STACK: " + " | ".join(stack))

    facts = [
        "contact form yes" if sig.get("has_contact_form") else "no contact form",
        "phone yes" if sig.get("has_phone") else "no phone",
        (f"blog {sig.get('blog_year')}" if sig.get("blog_year")
         else ("blog undated" if sig.get("has_blog") else "no blog")),
    ]
    if sig.get("has_careers"):
        facts.append("careers page yes")
    if sig.get("has_multiple_locations"):
        facts.append(f"{sig.get('location_count', 2)} locations")
    if not sig.get("has_pricing"):
        facts.append("no pricing")
    if not sig.get("has_social"):
        facts.append("no social links")
    if not sig.get("mobile_viewport"):
        facts.append("no mobile layout")
    if sig.get("stale_copyright") and sig.get("copyright_year"):
        facts.append(f"copyright {sig['copyright_year']}")
    if sig.get("slow"):
        facts.append("slow to load")
    if facts and signals > 0:
        lines.append("SIGNALS: " + ", ".join(facts[:signals]))

    top = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)][:max(1, gaps)]
    if top:
        lines.append("TOP GAPS: " + "; ".join(
            f"{str(g.get('title') or g.get('code') or '').strip()} ({int(g.get('severity') or 1)})"
            for g in top))
    return lines


def digest(audit: dict, max_chars: int = 1200) -> str:
    """The five-line brief that is the *only* thing the model ever receives.

    Every character here is billed on every lead, so the shape is fixed and the
    content is trimmed rather than allowed to overflow: services first, then
    secondary signals, then gaps down to the headline one.
    """
    try:
        if not isinstance(audit, dict):
            return ""
        budget = max(80, int(max_chars))
        text = ""
        for services, signals, gaps in ((6, 9, 3), (4, 7, 3), (3, 5, 3),
                                        (2, 4, 2), (1, 3, 2), (0, 2, 1)):
            text = "\n".join(_digest_lines(audit, services, signals, gaps))
            if len(text) <= budget:
                return text
        # A site with absurdly long service names still has to fit the budget.
        return text[:budget].rstrip()
    except Exception:
        return ""
