"""Website enrichment — find the address a business actually answers.

This module is the difference between a list of businesses and a list of leads,
so it is built against the three things that hide a contact address on a real
small-business website:

* **Obfuscation.** Cloudflare's `data-cfemail`, `info (at) acme (dot) ca`, HTML
  entities and `String.fromCharCode` blobs all exist to beat a naive regex, and
  they work: a Cloudflare-fronted site — a large share of the market — yields
  literally zero addresses without the XOR pass below.
* **Location.** The address is usually not on the home page. We rank the site's
  own links (contact > about > team > support > impressum) and fetch the best
  few *concurrently*, because one extra sequential page per lead is what turns a
  five-minute scrape into an hour.
* **Encoding.** A hardcoded utf-8 decode mangles the Latin-1, cp1252 and
  Shift-JIS sites that are exactly the neglected businesses worth pitching.
* **Ownership.** An address printed on a page is not always the page's own. A
  "Site by" footer, a theme credit and a builder's support desk are all real
  mailboxes belonging to somebody else, and the pitch opens by naming what is
  wrong with the site — so mailing one sends that letter to the agency that
  built it. An address counts as the business's only when it sits on a domain
  the business plainly owns, or on free mail, which is what a plumber with a
  Gmail account genuinely uses.

Candidates are **scored, not first-one-wins**. One page routinely carries a
webmaster alias, a careers inbox, a privacy contact and the owner's address;
picking the wrong one costs a lead and, if it is `postmaster@`, some sender
reputation too. Anything scoring zero or less is dropped entirely — no address
is an honest result, a wrong address is not.

Everything above the network layer is a pure function over already-fetched HTML
(`extract_contacts`, `_scan_page`, `_rank_emails`), so the whole extraction
surface is unit-tested offline in `tests/test_enrich_email.py`. `_fetch_page` is
the single network seam.

`harvest_site` returns the HTML it fetched under `"html"` so `core.audit` runs
off the same download: one lead, one crawl.
"""

from __future__ import annotations

import codecs
import concurrent.futures
import gzip
import html as _htmlmod
import http.client
import io
import json
import re
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request

from core.tlds import is_tld as _is_tld
import zlib

try:  # MX is a better deliverability signal than an A record, when available
    import dns.resolver as _dns
except ImportError:
    _dns = None

try:  # decides only whether we advertise br in Accept-Encoding
    import brotli as _brotli
except ImportError:
    try:
        import brotlicffi as _brotli
    except ImportError:
        _brotli = None


# ── Public surface ──

ENRICH_KEYS = ("email", "facebook", "instagram", "linkedin", "twitter", "youtube")

# Departmental inboxes: not a person, but a real human reads them.
# "hallo" and "kontakt" were already here, so the list had always reached past
# English — just not far enough to rank `hei@` or `contacto@` as the front desk
# they are, which left the general inbox losing to whatever department happened
# to be printed on the contact page.
EMAIL_ROLE_PREFIXES = frozenset({
    "info", "contact", "contactus", "hello", "hallo", "hey", "hi", "sales",
    "office", "enquiries", "enquiry", "inquiries", "inquiry", "admin",
    "bookings", "booking", "reception", "frontdesk", "reservations", "kontakt",
    "mail", "email", "general", "ask", "team", "support", "help", "service",
    "customerservice", "orders", "studio", "shop", "welcome", "post",
    "hei", "hola", "bonjour", "ciao", "contacto", "contatto", "correo",
    "buero", "sekretariat", "empfang", "praxis", "cabinet", "accueil",
    "reservas", "citas", "atencioncliente", "recepcion",
})

# Machine mailboxes. Sending here is at best ignored and at worst a spam report.
EMAIL_JUNK_PREFIXES = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "dontreply",
    "postmaster", "abuse", "webmaster", "hostmaster", "mailer-daemon",
    "mailerdaemon", "bounce", "bounces", "daemon", "root", "nobody",
    "unsubscribe", "notifications", "notification", "alerts", "automated",
    "autoresponder", "system", "wordpress", "cron",
})

# Real inboxes, wrong human — a recruiter or a lawyer is not the buyer.
_CAREERS_PREFIXES = frozenset({
    "careers", "career", "jobs", "job", "hiring", "recruit", "recruiting",
    "recruitment", "resume", "resumes", "cv", "hr", "apply", "applications",
    "bewerbung", "bewerbungen", "karriere", "stellen", "recrutement",
    "candidature", "candidatures", "empleo", "rrhh",
})
_LEGAL_PREFIXES = frozenset({
    "privacy", "legal", "dpo", "gdpr", "compliance", "dmca", "copyright",
    "abuse-report", "security",
})

_FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "msn.com", "protonmail.com", "proton.me", "mail.com", "zoho.com",
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "bellsouth.net",
    "sympatico.ca", "rogers.com", "shaw.ca", "telus.net", "bell.net",
    "videotron.ca", "cogeco.ca", "btinternet.com", "sky.com", "orange.fr",
    "web.de", "t-online.de", "bigpond.com", "xtra.co.nz",
})
# Families with dozens of country variants (yahoo.co.uk, hotmail.fr, ...).
_FREE_MAIL_FAMILIES = frozenset({"yahoo", "hotmail", "outlook", "live", "gmx",
                                 "yandex", "laposte", "libero", "freenet"})

_DISPOSABLE_SUBSTR = (
    "mailinator", "guerrillamail", "10minutemail", "tempmail", "temp-mail",
    "throwaway", "trashmail", "yopmail", "sharklasers", "maildrop",
    "dispostable", "getnada", "fakeinbox", "spamgourmet", "mailnesia",
    "discard.email", "grr.la", "spam4.me",
)


# ── Fetching ──

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_ACCEPT_ENCODING = "gzip, deflate" + (", br" if _brotli else "")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": _ACCEPT_ENCODING,
    "Accept-Language": "en-US,en;q=0.9",
}
_MAX_BYTES = 1_500_000
_DNS_TIMEOUT = 3.0

_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_.:\-]+)""", re.I)
_CHARSET_HEADER_RE = re.compile(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9_.:\-]+)", re.I)
_LEGACY_CHARSET_RE = re.compile(
    r"(?:iso[-_]?8859|windows[-_]?125|cp125|latin|(?:us[-_]?)?ascii)", re.I)

_unverified_ctx = None


def _ssl_context(verify: bool):
    """A verifying context, or a permissive one for the retry.

    An expired or self-signed certificate is a *signal* — those sites are
    neglected, which is the whole target market. Refusing to read them would
    throw away leads to protect a page of public marketing copy.
    """
    if verify:
        return ssl.create_default_context()
    global _unverified_ctx
    if _unverified_ctx is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _unverified_ctx = ctx
    return _unverified_ctx


def _decompress(raw: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    if "gzip" in enc:
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read(_MAX_BYTES)
        except (OSError, EOFError, zlib.error):
            return raw
    if "deflate" in enc:
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(raw, wbits)
            except zlib.error:
                continue
        return raw
    if "br" in enc and _brotli is not None:
        try:
            return _brotli.decompress(raw)
        except Exception:
            return raw
    return raw


def _is_multibyte_utf8(raw: bytes) -> bool:
    if raw.isascii():
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _decode_body(raw: bytes, content_type: str) -> str:
    """Decode page bytes using the declared charset, not a guess.

    Two traps, both common enough to mangle real pages:

    * A candidate only wins if it decodes *strictly*, so a wrong label falls
      through instead of producing mojibake.
    * A legacy single-byte label never fails, because every byte is a valid
      character in it — and stale `charset=iso-8859-1` defaults sit on plenty
      of utf-8 pages. When the bytes are genuinely multi-byte utf-8, they are
      utf-8 whatever the header claims.
    """
    declared = []
    header = _CHARSET_HEADER_RE.search(content_type or "")
    if header:
        declared.append(header.group(1))
    meta = _META_CHARSET_RE.search(raw[:2048])
    if meta:
        declared.append(meta.group(1).decode("ascii", "ignore"))

    candidates = declared + ["utf-8", "cp1252"]
    if _is_multibyte_utf8(raw) and any(
            _LEGACY_CHARSET_RE.match(d.strip().strip("'\"")) for d in declared):
        candidates.insert(0, "utf-8")

    usable = []
    for name in candidates:
        name = (name or "").strip().strip("'\"").lower()
        if not name or name in usable:
            continue
        try:
            codecs.lookup(name)
        except (LookupError, TypeError):
            continue
        usable.append(name)
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(usable[0] if usable else "utf-8", "replace")


def _short_error(exc: Exception) -> str:
    """One lowercase word or two — this ends up in a GUI column, not a log.

    Every word here is read back by `core.audit.unreachable_reason`, which turns
    it into the sentence the Leads table prints. "unreachable" used to swallow a
    refused connection, a dropped one and a redirect loop alike, and the three
    of them want three different things done about the lead: the first is a host
    that is up and saying no, the second is worth one retry, the third is a site
    whose new address the operator can go and find.
    """
    if isinstance(exc, urllib.error.HTTPError):
        # urllib raises this, not a URLError, when the redirect chain never ends.
        if "redirect" in str(getattr(exc, "reason", "") or "").lower():
            return "redirect loop"
        return "http %d" % exc.code
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return "reset"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.gaierror):
            return "dns"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout"
        if isinstance(reason, ssl.SSLError):
            return "ssl"
        if isinstance(reason, ConnectionRefusedError):
            return "refused"
        if isinstance(reason, (ConnectionResetError, ConnectionAbortedError)):
            return "reset"
        return "unreachable"
    if isinstance(exc, ssl.SSLError):
        return "ssl"
    if isinstance(exc, http.client.HTTPException):
        return "reset"
    return type(exc).__name__.lower()


def _get(url: str, timeout: float, verify: bool) -> tuple[str, str, str]:
    req = urllib.request.Request(url, headers=_HEADERS)
    error = ""
    try:
        resp = urllib.request.urlopen(req, timeout=timeout,
                                      context=_ssl_context(verify))
    except urllib.error.HTTPError as exc:
        # A 403/404 often still carries the full page; read it and say so.
        if exc.fp is None:
            raise
        resp, error = exc, "http %d" % exc.code
    with resp:
        ctype = resp.headers.get("Content-Type", "")
        if ctype and not re.search(r"html|xml|text|json", ctype, re.I):
            return resp.geturl(), "", "content-type"
        raw = resp.read(_MAX_BYTES)
        raw = _decompress(raw, resp.headers.get("Content-Encoding", ""))
        return resp.geturl(), _decode_body(raw, ctype), error


def _fetch_page(url: str, timeout: float = 8.0) -> tuple[str, str, str]:
    """Fetch one page. Returns `(final_url, html, error)` and never raises.

    The module's only network call, and therefore the seam the offline tests
    replace. Redirects are followed by urllib; `final_url` is where we landed.
    """
    for verify in (True, False):
        try:
            return _get(url, timeout, verify)
        except urllib.error.URLError as exc:
            if verify and isinstance(getattr(exc, "reason", None), ssl.SSLError):
                continue
            return "", "", _short_error(exc)
        except Exception as exc:
            return "", "", _short_error(exc)
    return "", "", "ssl"


# ── Hosts and domains ──

# Suffixes where the registrable name is the third label from the right.
_MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "edu.au", "co.nz", "net.nz", "org.nz",
    "co.za", "co.in", "net.in", "org.in", "co.jp", "or.jp", "ne.jp",
    "com.br", "com.mx", "com.ar", "com.co", "com.sg", "com.hk", "com.tr",
    "com.tw", "co.kr", "co.il", "co.th", "com.my", "com.ph", "com.pk",
})
# The reserved names never resolve; everything else is decided by the IANA list
# in `core/tlds.py`. A denylist of bad suffixes can only ever name the ones
# somebody thought of, which is how `hero@2x.jxl`, `lark@v3.ttf` and a rot13
# blob all cleaned successfully and were exported as contacts.
_INVALID_TLDS = frozenset({"local", "localhost", "invalid", "test", "example",
                           "localdomain", "internal", "lan"})


def _host_of(url: str) -> str:
    raw = (url or "").strip()
    if raw and "//" not in raw:
        raw = "//" + raw
    host = urllib.parse.urlsplit(raw).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _registrable(host: str) -> str:
    """`www.shop.acme.co.uk` -> `acme.co.uk`. Good enough without a PSL copy."""
    host = (host or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2:
        return host
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# A site *on* one of these has no registrable domain of its own —
# `stitchandsole.wixsite.com` registers as `wixsite.com`, which is the
# platform's domain and not the shop's. Ownership is unjudgeable there, so the
# off-domain rule below stands down rather than throwing away the one address
# such a business has. Missing a platform here costs a lead; listing one that
# does not belong only restores the older, laxer behaviour for that host — both
# failure modes are bounded, which is why a short list is safe where the
# denylist in `_clean_email` would not be.
_PLATFORM_DOMAINS = frozenset({
    "wixsite.com", "wix.com", "squarespace.com", "myshopify.com",
    "wordpress.com", "weebly.com", "business.site", "godaddysites.com",
    "jimdosite.com", "jimdofree.com", "webnode.com", "blogspot.com",
    "github.io", "netlify.app", "vercel.app", "webflow.io", "square.site",
    "site123.me", "tilda.ws", "mystrikingly.com", "yolasite.com",
    "companysite.net", "websitebuilder.com", "duda.co", "ionos.space",
})

# The shortest name worth reading as a brand. Below it, `long.startswith(short)`
# is coincidence rather than evidence: half the web starts with `mail`, `north`
# or `west`, and a four-letter head would hand a whole hosting company the
# ownership of a business that merely shares its first syllable.
_BRAND_MIN = 5


def _brand(domain: str) -> str:
    """The name a business trades under: `oakville-windows.ca` -> `oakvillewindows`."""
    return _registrable(domain).split(".")[0].replace("-", "")


def _owns(domain: str, site_domain: str) -> bool:
    """True when `domain` plainly belongs to the business behind `site_domain`.

    Three shapes, and only three, because every one of them has to be a shape a
    third party cannot wear by accident:

    * the site's own registrable domain;
    * the same brand under another suffix — `acmeroofing.ca` writing from
      `acmeroofing.com` is the commonest sibling there is;
    * a brand the site's brand *begins*, or that begins the site's brand:
      `wrenfield.works` -> `wrenfieldjoinery.com`, `harbourviewdental.ca` ->
      `harbourviewdentalgroup.com`. A prefix, deliberately, not a shared
      substring: a business extends its name to the right, while a suffix match
      is what `windows.io` and `oakvillewindows.com` share and they are two
      companies.

    Everything else — the agency in the footer, the theme author, the host's
    support desk — is somebody else's mail, however plausible it looks.
    """
    reg = _registrable(domain)
    if not reg or not site_domain:
        return False
    if reg == site_domain:
        return True
    name, site = _brand(reg), _brand(site_domain)
    if not name or not site:
        return False
    if name == site:
        return True
    short, long = (name, site) if len(name) < len(site) else (site, name)
    return len(short) >= _BRAND_MIN and long.startswith(short)


# ── Address cleaning ──

# A split a reader cannot see is not a token boundary. Zero-width marks, the
# soft hyphen, the BOM, the bidi controls, an NBSP between two word characters
# and markup that opens and closes inside a word all render as nothing, and none
# of them is a local-part character — so the scan restarted after the split and
# handed back the tail. `in<zwsp>fo@acmeroofing.ca` reads as `info@acmeroofing.ca`
# to a human and yielded `fo@acmeroofing.ca`, a mailbox nobody owns. `_mark_joins`
# rewrites every one of them to `_JOIN`, which both patterns refuse on the
# leading side, so a split token is refused whole. Refused, not rejoined:
# `info<span style="display:none">REMOVE</span>@acme.com` is the same shape in
# the markup as an anti-scrape split, and rejoining reads it as the address
# `inforemove@acme.com`, which is the fabrication this file exists to prevent.
_JOIN = "\x00"
# Codepoints, not literals: an invisible character sitting in this source
# file is exactly what an editor or a formatter strips without telling
# anyone, and a stripped codepoint would take the guard away with it.
# Soft hyphen, ZWSP, ZWNJ, ZWJ, LRM, RLM, word joiner, BOM.
_INVISIBLE = "".join(map(chr, (0x00ad, 0x200b, 0x200c, 0x200d,
                               0x200e, 0x200f, 0x2060, 0xfeff)))

# Every quantifier is bounded and every start is anchored to a token boundary.
# The obvious `[A-Za-z0-9._%+-]+@...` is quadratic: a 200 KB base64 blob with a
# single `@` after it backtracks for minutes, and minified JS is full of long
# unbroken runs. RFC caps the local part at 64 and a label at 63 anyway.
# The final label is alphabetic *or* punycode: `\.[A-Za-z]{2,24}` alone stops at
# the first hyphen, so `info@shop.xn--p1ai` was handed back as `info@shop.xn` —
# a truncation that cleans successfully and is mailed to nobody. Both boundaries
# are what make a truncation impossible: any token whose suffix this pattern
# cannot express is refused whole rather than cut down to the part it can, and
# any token whose head is held off by a `_JOIN` is refused rather than started
# part-way through.
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-" + _JOIN + r"])"
    r"[A-Za-z0-9._%+\-]{1,64}@"
    r"[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,8}"
    r"\.(?:xn--[A-Za-z0-9]{2,59}|[A-Za-z]{2,24})"
    r"(?![A-Za-z0-9\-])")

# Domain substrings that are never a business contact: CDNs, analytics, the
# platform's own plumbing, and the placeholders that ship inside themes.
_JUNK_DOMAIN_SUBSTR = (
    "sentry", "wixpress", "wix.com", "godaddy", "squarespace", "shopify",
    "cloudflare", "googleapis", "gstatic", "schema.org", "w3.org", "jquery",
    "bootstrapcdn", "fontawesome", "example.", "yourdomain", "domain.com",
    "email.com", "test.com", "sentry.io", "mailerlite", "wordpress.org",
    "cloudfront", "akamai", "jsdelivr", "unpkg", "googletagmanager",
    "doubleclick", "fbcdn", "cdninstagram", "gravatar", "wp.com", "typekit",
    "yourcompany", "yoursite", "yourbusiness", "mysite.com", "sentry-next",
    "placeholder", "adobe.com", "w3schools",
)
# `logo@2x.png`, `hero@3x.avif` — a density descriptor, not a mailbox. The
# extension is what makes it a file; the `@2x` shape alone does not, because
# `1x.com` and `3x.com` are registered domains with real mailboxes behind them.
# `.mov` is the one suffix here that is also a delegated gTLD, and the video
# and production shops it was sold to are exactly this tool's market, so it
# needs *both* halves of the signal — `clip@2x.mov` is a file, `info@shorts.mov`
# is a mailbox.
_EMAIL_ASSET_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|avif|heic|heif|bmp|tiff?|svg|css|js|ico|woff2?"
    r"|mp4|webm|avi|m4v|m4a|ogg|pdf)$"
    r"|@\d{1,2}x\.mov$", re.I)
# A percent escape is href syntax, not a local part: `mailto:info%2Bweb@acme.ca`
# is one address, and scanning the raw href as text minted `info%2bweb@acme.ca`
# alongside it. The `mailto:` pass unquotes before cleaning, so the real address
# is unaffected and only the phantom is dropped.
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9a-f]{2}")
_HEX_LOCAL_RE = re.compile(r"^[0-9a-f]{20,}$")
# A JSON escape glued to the local part: `>info@acme.ca` scanned raw reads as
# `u003einfo@acme.ca`, because the backslash is not a local-part character and
# the match starts after it. Refused rather than trimmed back, and only on a
# text scan — trimming reads `mailto:x27andy@acme.ca` as andy's address, and a
# `mailto:` href is the page stating its address outright. The real one is not
# lost either way: `_scan_page` runs `_js_unescape` and the same address turns
# up whole in that pass. Deliberately only the escapes for < > & " ' —
# anything looser refuses real local parts.
_ESCAPE_PREFIX_RE = re.compile(r"^(?:u00(?:3c|3e|26|22|27)|x(?:3c|3e|26|22|27))+", re.I)


def _clean_email(email: str, *, from_text: bool = False) -> str:
    """Normalise and reject. Returns "" for anything that is not an address."""
    email = (email or "").strip().strip(".<>,;:()[]'\"").lower()
    if not _EMAIL_RE.fullmatch(email):
        return ""
    if _EMAIL_ASSET_RE.search(email):
        return ""
    local, _, domain = email.partition("@")
    if len(local) > 64 or len(domain) > 253:
        return ""
    if _PERCENT_ESCAPE_RE.search(local):
        return ""
    if from_text and _ESCAPE_PREFIX_RE.match(local):
        return ""
    if any(j in domain for j in _JUNK_DOMAIN_SUBSTR):
        return ""
    tld = domain.rsplit(".", 1)[-1]
    if tld in _INVALID_TLDS or not _is_tld(tld):
        return ""
    if any(lbl.startswith("-") or lbl.endswith("-") or not lbl
           for lbl in domain.split(".")):
        return ""
    # Long hex local parts are Sentry/analytics tracking IDs, not people.
    if _HEX_LOCAL_RE.match(local):
        return ""
    # A real address has at least one letter in the local part.
    if not re.search(r"[a-z]", local):
        return ""
    return email


def _is_free_mail(domain: str) -> bool:
    reg = _registrable(domain)
    return reg in _FREE_MAIL_DOMAINS or reg.split(".")[0] in _FREE_MAIL_FAMILIES


def _is_disposable(domain: str) -> bool:
    return any(s in domain for s in _DISPOSABLE_SUBSTR)


def _local_tokens(local: str) -> list[str]:
    return [t for t in re.split(r"[._\-+]", local) if t]


def _hits(local: str, words: frozenset) -> bool:
    """True when the local part *is* one of `words`, or leads with one."""
    if local in words:
        return True
    tokens = _local_tokens(local)
    return bool(tokens) and tokens[0] in words


# first.last, first_last, f.last — the shapes a company actually issues.
_PERSON_RE = re.compile(r"^[a-z]{2,}[._\-][a-z]{2,}$|^[a-z]\.[a-z]{2,}$")

# Tokens that give a `word-word` local away as a list or a department.
_NOT_PERSON_TOKENS = (
    EMAIL_ROLE_PREFIXES | EMAIL_JUNK_PREFIXES | _CAREERS_PREFIXES | _LEGAL_PREFIXES
    | frozenset({
        "announce", "announcements", "news", "newsletter", "list", "lists",
        "users", "group", "groups", "updates", "subscribe", "digest", "all",
        "everyone", "staff", "dev", "devs", "developers", "marketing", "media",
        "press", "events", "event", "accounts", "accounting", "billing",
        "invoice", "invoices", "payments", "web", "hosting", "domains", "tech",
        "it", "noc", "sales", "quotes", "quote", "estimate", "estimates",
    })
)
_FIRST_NAMES = frozenset("""
adam adrian alan albert alex alexander alice amanda amy andrea andrew angela ann
anna anne anthony antonio ashley barbara ben benjamin beth bill bob brad brandon
brenda brian bruce bryan carl carlos carol caroline catherine charles cheryl chris
christian christina christine christopher cindy claire colin craig curtis dan
daniel danny darren dave david dawn dean debbie deborah denis dennis derek diana
diane dominic donald donna doug douglas ed eddie edward eileen elaine elena
elizabeth emily emma eric erica erin eugene evan fiona frank fred gary gavin
george gerald gina glen gordon grace graham grant greg gregory hannah harry heather
helen henry holly ian irene isabel jack jackie jacob james jamie jane janet jason
jay jean jeff jeffrey jenna jennifer jeremy jerry jessica jill jim jimmy joan joe
joel john johnny jon jonathan jordan jose joseph josh joshua joyce juan judith
judy julia julie justin karen karl kate katherine kathleen kathy katie keith kelly
ken kenneth kevin kim kimberly kirk kris kristen kyle larry laura lauren lawrence
lee leo leslie linda lisa liz logan lori louis lucas lucy luke lynn maria marie
marilyn mario mark martin mary matt matthew maureen megan melissa michael michelle
mike miguel monica nancy natalie nathan neil nicholas nick nicola nicole noel
norman oliver olivia oscar pam pamela pat patricia patrick paul paula pedro peter
philip phil rachel ralph randy ray raymond rebecca regina renee rich richard rick
rob robert roberta robin rod roger ron ronald rose ross roy russell ruth ryan sally
sam samantha samuel sandra sara sarah scott sean shane shannon sharon shaun shawn
sheila shirley simon sophie stacey stan stephanie stephen steve steven stuart sue
susan suzanne sylvia tammy tanya ted teresa terry theresa thomas tim timothy tina
todd tom tony tracey tracy travis trevor troy valerie vanessa vera veronica
vicki victor victoria vincent virginia walter wanda wayne wendy will william
willie yolanda yvonne zachary
""".split())


def _looks_personal(local: str) -> bool:
    """True for an address issued to a human.

    `first.last` shape alone is not enough: `python-announce@`, `sqlite-users@`
    and `sales-team@` wear it too, and promoting a mailing list over the
    company's own inbox is exactly the kind of near-miss that wastes a lead.
    """
    tokens = _local_tokens(local)
    if any(t in _NOT_PERSON_TOKENS for t in tokens):
        return False
    if _PERSON_RE.match(local):
        return True
    return bool(tokens) and tokens[0] in _FIRST_NAMES


# ── Decoding tricks ──

def _decode_cfemail(hexstr: str) -> str:
    """Undo Cloudflare's email obfuscation.

    The payload is hex; byte 0 is a per-page XOR key and every later byte is the
    address XORed with it. Without this a Cloudflare-fronted site — a very large
    share of small-business hosting — hands back no address at all.
    """
    data = (hexstr or "").strip()
    if len(data) < 6 or len(data) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", data):
        return ""
    try:
        raw = bytes.fromhex(data)
    except ValueError:
        return ""
    key = raw[0]
    try:
        return bytes(b ^ key for b in raw[1:]).decode("utf-8")
    except UnicodeDecodeError:
        return ""


_JS_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})")


def _js_unescape(text: str) -> str:
    def repl(m):
        return chr(int(m.group(1) or m.group(2), 16))
    return _JS_ESCAPE_RE.sub(repl, text).replace("\\/", "/")


_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.I | re.S)
# A script can hold an address a page means to show; a stylesheet never can, and
# on a page builder's output it is most of the bytes. Kept separate so the
# expensive scans can drop the one without losing the other.
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html or "")
    return _htmlmod.unescape(_TAG_RE.sub(" ", text))


_NBSP = chr(0x00a0)
_TOKEN_CHAR = r"[A-Za-z0-9._%+\-]"
_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9]")

# Markup that opens and closes between two token characters is an interpolation
# into one word, not a boundary: `in<b></b>fo@`, `in<!--x-->fo@` and
# `i<span class="hidden">nospam</span>nfo@` each render as a single run, and the
# scan used to restart after the tag and keep the tail. Markup that only closes
# what it did not open, or opens what it does not close, is a real boundary —
# `Contact</h2><p>info@acme.ca` is two blocks, and reading that as a split would
# lose the address on every minified contact page. Balance is the whole signal,
# so no roster of element names is needed and none can go stale. Interpolated
# text is held to token characters for the same reason: the colon in
# `Email<b>:</b>info@acme.ca` is a boundary a reader can see.
_INLINE_MARKUP = (
    r"(?:<!--.{0,2000}?-->"
    r"|<([A-Za-z][A-Za-z0-9]{0,15})\b[^>]{0,2000}>" + _TOKEN_CHAR + r"{0,64}"
    r"</\1\s{0,8}>)")
_MARKUP_JOIN_RE = re.compile(
    r"(?<=" + _TOKEN_CHAR + r")(?:" + _INLINE_MARKUP + r"){1,4}"
    r"(?=" + _TOKEN_CHAR + r")", re.I | re.S)
# Character references are judged by what they decode to, so `&zwnj;`, `&shy;`
# and `&#8203;` need no roster of their own and the hex spelling comes free.
_JOIN_CHAR_RE = re.compile(
    r"[" + _INVISIBLE + _NBSP + r"]"
    r"|&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,30});")


def _mark_joins(text: str) -> str:
    """Rewrite every split a reader cannot see to `_JOIN`."""
    def repl(m):
        raw = m.group(0)
        char = _htmlmod.unescape(raw) if raw.startswith("&") else raw
        if len(char) != 1:
            return raw
        if char in _INVISIBLE:
            return _JOIN
        # NBSP is an ordinary space everywhere except between two word
        # characters, where nothing renders between them and it is an
        # intra-word artefact rather than a separator.
        if (char == _NBSP and m.start() > 0
                and _WORD_CHAR_RE.match(m.string, m.start() - 1)
                and _WORD_CHAR_RE.match(m.string, m.end())):
            return _JOIN
        return raw
    return _JOIN_CHAR_RE.sub(repl, _MARKUP_JOIN_RE.sub(_JOIN, text))


# ── Scanning one page ──

# Trust order, and therefore what `_add` keeps when one address turns up twice:
# a plainly written address is a *better* provenance than the same string
# rebuilt by the deobfuscator, so "text" outranks "deobfuscated".
_METHOD_RANK = {"mailto": 0, "jsonld": 1, "cfemail": 2, "text": 3,
                "deobfuscated": 4, "js": 5, "credit": 6}
_METHOD_BONUS = {"mailto": 25, "jsonld": 20, "cfemail": 20, "deobfuscated": 0,
                 "js": 0, "text": 0, "credit": 0}

_MAILTO_RE = re.compile(r"mailto:([^\"'>\s\\]+)", re.I)
_CFEMAIL_RE = re.compile(
    r"data-cfemail\s*=\s*[\"']([0-9a-fA-F]+)[\"']|"
    r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", re.I)
_JSONLD_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>",
    re.I | re.S)
_JSONLD_EMAIL_RE = re.compile(r"[\"']email[\"']\s*:\s*[\"']([^\"']{5,120})[\"']", re.I)
_ITEMPROP_RE = re.compile(
    r"<[a-z][a-z0-9]*\b([^>]*itemprop\s*=\s*[\"']?email[\"']?[^>]*)>([^<]{0,200})",
    re.I)
_ATTR_VALUE_RE = re.compile(r"(content|href|value)\s*=\s*[\"']([^\"']+)[\"']", re.I)
_FROMCHARCODE_RE = re.compile(
    r"String\.fromCharCode\(\s*([0-9xXa-fA-F,\s]+?)\s*\)", re.I)

# `at`/`dot` written out, bracketed only. An unbracketed `at` between two words
# is ordinary English — "the team at bright dot design build websites for local
# trades" is a sentence, not team@bright.design — and nothing available here
# separates the two, because a business writing its own copy in lowercase is
# indistinguishable from an address written out longhand. Brackets are the
# whole signal: nobody types `(at)` by accident. The angle pair is deliberately
# absent, because `<at>` also matches inside an XML data island or a
# mis-escaped code sample.
#
# The two spellings are not interchangeable and must not share a whitespace
# rule. `_EMAIL_RE` requires the local part to abut a literal `@`, and that is
# what stops `Follow us @acmepizza.studio` from reading as `us@acmepizza.studio`;
# a shared `\s*` handed that abutment away here, so every social handle, credit
# line and caption on the web minted an address — `Built @ north.studio`,
# `Design @ mono.works`, `Volg ons @debakkerij.amsterdam`. Whitespace is only
# ever part of the *bracketed* form, so only the bracketed form may carry it.
_AT_BARE = r"(?:@|%40)"
_AT_SPELLED = r"[\(\[\{]\s*at\s*[\)\]\}]"
_DOT_SPELLED = r"[\(\[\{]\s*dot\s*[\)\]\}]"
_DOT_MARKER = r"(?:\.|%2e|" + _DOT_SPELLED + r")"
# Someone who spells the dot in the domain spells it in `jane dot smith` too.
# The bare form survives here alone, where it cannot fire on its own: an `at`
# marker has to follow it immediately, so `jane dot smith (at) acme (dot) ca`
# keeps its first name instead of arriving as `smith@acme.ca`. A truncated
# address is a fabricated one — it cleans, it looks real, it goes nowhere.
# Only the separated forms count: a bare `dot` glued to its neighbours is a
# local part in its own right (`dot@transportation.gov`), and a form that the
# surrounding character class could also swallow makes the scan backtrack.
_LOCAL_DOT = r"(?:\s+dot\s+|\s*" + _DOT_SPELLED + r"\s*)"

_LOCAL_ATOM = r"[A-Za-z0-9][A-Za-z0-9._%+\-]{0,62}"
# Which local part applies is decided by the `at` marker that follows it.
# A bare `@`/`%40` means the address was typed literally, and nobody types
# `jane dot smith@acme.ca` — so the word before a literal `@` is prose, and
# `Ask Dot orders@wrenfield.works` must not arrive as `ask.orders@...`.
# A spelled `(at)` means the whole address was obfuscated, so a spelled dot
# in the local part is part of it.
_LOCAL_PART_BARE = _LOCAL_ATOM
_LOCAL_PART_SPELLED = _LOCAL_ATOM + r"(?:" + _LOCAL_DOT + _LOCAL_ATOM + r"){0,3}"
_LABEL = r"[A-Za-z0-9][A-Za-z0-9\-]{0,61}"
# Whitespace belongs to the *spelled* separator alone. A literal dot in a
# domain abuts on both sides, so a sentence-ending full stop cannot extend the
# domain by the next word: `Write to orders@wrenfield.works. Visit the yard`
# stops at `works` instead of minting `orders@wrenfield.works.visit`.
_DOT_SEP = r"(?:\.|%2e|\s*" + _DOT_SPELLED + r"\s*)"
_DOMAIN = r"(?:" + _LABEL + _DOT_SEP + r")+[A-Za-z]{2,24}"
# A spelled `at` has to be answered by a spelled `dot` somewhere in the domain.
# Deliberate obfuscation is consistent: whoever writes `(at)` writes `(dot)`
# too, so a domain whose dots are all literal means the writer used the English
# word — `Follow us (at) acme.ca`, `Call Dot (at) acme.pizza`, `Open (at)
# shorts.mov`. The rule is structural and symmetric rather than a word list, so
# it cannot go stale. It costs `info (at) acme.ca`, which is a miss; the shapes
# it removes were mints.
_DOMAIN_SPELLED = (r"(?:" + _LABEL + _DOT_SEP + r"){0,8}"
                   + _LABEL + r"\s*" + _DOT_SPELLED + r"\s*"
                   r"(?:" + _LABEL + _DOT_SEP + r"){0,8}"
                   r"[A-Za-z]{2,24}")
_OBFUSCATED_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-" + _JOIN + r"])"
    r"(?:(?P<lbare>" + _LOCAL_PART_BARE + r")" + _AT_BARE
    + r"(?P<dbare>" + _DOMAIN + r")"
    r"|(?P<lspelled>" + _LOCAL_PART_SPELLED + r")"
    r"\s*" + _AT_SPELLED + r"\s*(?P<dspelled>" + _DOMAIN_SPELLED + r"))"
    r"(?![A-Za-z0-9\-])",
    re.I,
)
_DOT_SPLIT_RE = re.compile(r"\s*" + _DOT_MARKER + r"\s*", re.I)
_LOCAL_DOT_SPLIT_RE = re.compile(_LOCAL_DOT, re.I)


def _add(found: dict, email: str, method: str) -> None:
    """Record `email`, keeping the most trustworthy method that produced it."""
    if not email:
        return
    prev = found.get(email)
    if prev is None or _METHOD_RANK[method] < _METHOD_RANK[prev]:
        found[email] = method


def _scan_mailto(html: str, found: dict) -> None:
    for raw in _MAILTO_RE.findall(html):
        raw = raw.split("?")[0].replace("&amp;", "&")
        for part in re.split(r"[,;]", urllib.parse.unquote(raw)):
            _add(found, _clean_email(part), "mailto")


def _scan_cfemail(html: str, found: dict) -> None:
    for a, b in _CFEMAIL_RE.findall(html):
        _add(found, _clean_email(_decode_cfemail(a or b)), "cfemail")


def _walk_json(node, out: list) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == "email":
                if isinstance(value, str):
                    out.append(value)
                elif isinstance(value, list):
                    out += [v for v in value if isinstance(v, str)]
            else:
                _walk_json(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, out)


def _scan_jsonld(html: str, found: dict) -> None:
    for block in _JSONLD_RE.findall(html):
        values: list = []
        text = block.strip()
        try:
            _walk_json(json.loads(text), values)
        except (ValueError, RecursionError):
            # Trailing commas and template comments are common; fall back to a
            # regex over the same block rather than losing the whole payload.
            values = _JSONLD_EMAIL_RE.findall(text)
        for value in values:
            _add(found, _clean_email(value.split("?")[0].replace("mailto:", "")),
                 "jsonld")


def _scan_microdata(html: str, found: dict) -> None:
    for attrs, text in _ITEMPROP_RE.findall(html):
        candidates = [v for _, v in _ATTR_VALUE_RE.findall(attrs)]
        candidates.append(text)
        for value in candidates:
            value = urllib.parse.unquote(value.split("?")[0]).replace("mailto:", "")
            email = _clean_email(value.strip())
            if email:
                _add(found, email, "jsonld")
                break


def _scan_fromcharcode(html: str, found: dict) -> None:
    for blob in _FROMCHARCODE_RE.findall(html):
        chars = []
        for token in blob.split(","):
            token = token.strip()
            try:
                code = int(token, 16) if token.lower().startswith("0x") else int(token)
            except ValueError:
                chars = []
                break
            if 0 < code < 0x110000:
                chars.append(chr(code))
        if chars:
            _scan_plain("".join(chars), found, "js")


def _scan_plain(text: str, found: dict, method: str) -> None:
    for raw in _EMAIL_RE.findall(text):
        _add(found, _clean_email(raw, from_text=True), method)


def _scan_obfuscated(text: str, found: dict) -> None:
    for m in _OBFUSCATED_RE.finditer(text):
        local = ".".join(p for p in (s.strip() for s in _LOCAL_DOT_SPLIT_RE.split(
            m.group("lbare") or m.group("lspelled"))) if p)
        labels = [p for p in (s.strip() for s in _DOT_SPLIT_RE.split(
            m.group("dbare") or m.group("dspelled"))) if p]
        if not local or len(labels) < 2:
            continue
        rebuilt = local + "@" + ".".join(labels)
        email = _clean_email(rebuilt, from_text=True)
        if email:
            _add(found, email, "text" if rebuilt == m.group(0) else "deobfuscated")


def _scan_into(html: str, found: dict) -> None:
    """Run every extraction pass over one chunk of markup."""
    _scan_mailto(html, found)
    _scan_jsonld(html, found)
    _scan_cfemail(html, found)
    _scan_microdata(html, found)
    _scan_plain(_mark_joins(html), found, "text")
    _scan_fromcharcode(html, found)

    plain = _js_unescape(_htmlmod.unescape(html))
    marked = _mark_joins(plain)
    if plain != html:
        _scan_plain(marked, found, "deobfuscated")
    # An obfuscated address is one a human is meant to read, so the scan for it
    # runs over what a human sees. Two reasons, and they point the same way.
    #
    # Correctness: `bookings <b>[at]</b> acme <b>[dot]</b> com` renders as one
    # line and is one of the commonest ways a small site hides its address, but
    # the tags sit *between* the tokens rather than inside a word, so
    # `_mark_joins` rightly leaves them and the spelled pattern never closed.
    #
    # Cost: `_OBFUSCATED_RE` is the most expensive pattern in this file and its
    # price is the length of the text, not the number of addresses in it. Run
    # over raw markup it spent its whole budget on an inlined stylesheet — 21 ms
    # of a 160 KB page, against 6 ms with the CSS dropped and 0.1 ms over the
    # readable text alone, on a scan that runs once per page per lead. Scripts
    # stay in the first pass: an address written into a JS string is one the
    # page means to show, and dropping them costs it its provenance.
    #
    # Order is the safety argument and it is not interchangeable. `_mark_joins`
    # runs first, so every split a reader cannot see is already a `_JOIN` that
    # the pattern refuses on the leading side; strip first and `in<b></b>fo@`
    # comes back as `fo@`, a truncation that cleans and looks real and goes
    # nowhere. Then the tags are replaced by a space, never by nothing, because
    # a space is a boundary `_LOCAL_ATOM` cannot cross and joining
    # `Contact</h2><p>info@` into `contactinfo@` is the fabrication this file
    # exists to prevent. Cut here rather than through `_strip_tags`, whose
    # trailing unescape would decode `&amp;#8203;` a second time and hand back
    # the zero-width space `_mark_joins` was given no chance to see.
    _scan_obfuscated(_STYLE_RE.sub(" ", marked), found)
    _scan_obfuscated(_TAG_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", marked)), found)


# ── Credit lines ──

# "Site by", "Powered by", "Theme by": the byline of whoever built the site,
# and the one place on a business's own page where a printed address reliably
# belongs to somebody else. The window opens at the marker and only runs
# forwards: a credit sits under the footer's contact block often enough that
# reaching backwards would swallow the business's own address, and losing a
# real one is the failure this whole module is arranged against.
#
# The heads are the vocabulary of *web* work on purpose. `made by`, `created by`
# and `crafted by` were left out although they are credits too — on a maker's
# site they are the business describing its own product ("every loaf made by
# hand — hello@fernhill.ca"), and a marker that fires there deletes the address
# it was meant to protect.
_SP = r"(?:\s|&nbsp;|&#(?:160|[xX]0*[aA]0);)"
_CREDIT_HEAD = (r"(?:web" + _SP + r"{0,2})?"
                r"(?:site|design|development|dev|build|branding|theme|template"
                r"|hosting)"
                r"|website|powered|designed|developed|built|hosted|maintained"
                r"|managed|realisiert|erstellt|umgesetzt|r[eé]alis[eé]")
#
# The marker is matched backwards from its `by`, and that is a cost decision,
# not a stylistic one. Run forwards, the head alternation has to be tried at
# every word boundary on the page: 12 ms of a 142 KB page, against 4 ms for the
# same answer read backwards, on a scan that runs once per page per lead. `by`
# is a plain literal alternation the engine can skip through, and the expensive
# half then runs once per `by` against a 56-character window rather than a
# hundred thousand times against the page.
_CREDIT_BY_RE = re.compile(r"\b(?:by|par|von|door)\b", re.I)
_CREDIT_HEAD_RE = re.compile(
    r"\b(?:" + _CREDIT_HEAD + r")"
    r"(?:" + _SP + r"{1,8}(?:&amp;|&|\+|and|und|/)?" + _SP + r"{0,8}"
    r"(?:" + _CREDIT_HEAD + r"))?"
    + _SP + r"{1,8}\Z", re.I)
# How far back of its `by` a credit's head can sit: `Design &amp; development by`
# is the longest shape here, with room for the entity spelling of its spaces.
_CREDIT_LEAD = 56
# A credit is one line, and a line ends where a block does. Without this the
# window runs on into whatever the footer prints next, and a footer that prints
# `Proudly powered by WordPress` above the shop's own Gmail address would have
# the credit rule delete the one address the business has. Inline markup — the
# anchor around the agency's name, the em dash, the `<span>` around its logo —
# is not a line ending and must not close the window early.
_BLOCK_EDGE_RE = re.compile(
    r"<\s*/?\s*(?:br|p|div|footer|section|article|main|header|nav|aside|form|"
    r"address|blockquote|hr|table|thead|tbody|tr|td|th|ul|ol|li|dl|dt|dd|"
    r"h[1-6])\b", re.I)
# The cap for a byline that wraps its anchor over three indented lines.
_CREDIT_WINDOW = 320
_CREDIT_MAX = 12


def _credit_spans(html: str) -> list:
    """The credit lines on a page, as merged `(start, end)` offsets."""
    spans: list = []
    for by in _CREDIT_BY_RE.finditer(html):
        head = _CREDIT_HEAD_RE.search(html, max(0, by.start() - _CREDIT_LEAD),
                                      by.start())
        if head is None:
            continue
        start, end = head.start(), min(by.start() + _CREDIT_WINDOW, len(html))
        edge = _BLOCK_EDGE_RE.search(html, by.end(), end)
        if edge is not None:
            end = edge.start()
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            continue
        if len(spans) >= _CREDIT_MAX:
            break
        spans.append((start, end))
    return spans


def _blank_spans(html: str, spans: list) -> str:
    """`html` with `spans` replaced by spaces of the same length.

    Spaces, never nothing: a boundary the scan cannot cross is the point, and
    closing the gap would join `Contact</p>` to the address after the credit.
    """
    out, cut = [], 0
    for start, end in spans:
        out.append(html[cut:start])
        out.append(" " * (end - start))
        cut = end
    out.append(html[cut:])
    return "".join(out)


def _credit_only(html: str, found: dict) -> tuple:
    """The addresses on this page that appear *nowhere* but a credit line.

    Two scans decide it, and the order of the subtraction is what keeps them
    honest. The window scan can only nominate addresses `found` already holds,
    and the blanked scan is only ever subtracted, so neither pass can add an
    address to the page's results — a sliced tag or a blanked `<script>` open
    tag can cost this a suppression, never mint a recipient.

    Nothing is spent on the second scan unless the first found an address
    inside a credit line, which on the overwhelming majority of pages — the
    ones whose footer credit carries no address at all — is never.
    """
    spans = _credit_spans(html)
    if not spans:
        return ()
    inside: dict = {}
    for start, end in spans:
        _scan_into(html[start:end], inside)
    suspect = set(inside) & set(found)
    if not suspect:
        return ()
    elsewhere: dict = {}
    _scan_into(_blank_spans(html, spans), elsewhere)
    return tuple(suspect - set(elsewhere))


def _scan_page(html: str, page_url: str = "") -> dict:
    """Every address on one page, as `{email: method}`. Pure, no network."""
    found: dict = {}
    if not html:
        return found
    _scan_into(html, found)
    for email in _credit_only(html, found):
        found[email] = "credit"
    return found


# ── Socials and phones ──

_SOCIAL_PATTERNS = {
    "facebook": re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com/[A-Za-z0-9._%\-/?=&]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._%\-/?=&]+", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|pub)/[A-Za-z0-9._%\-/?=&]+", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9._%\-/?=&]+", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/[A-Za-z0-9._%\-/?=&@]+", re.I),
}

# Share/intent widgets and tracking pixels, not the business's own profile.
# NOTE: do not reject on a bare "=" — real profiles carry query strings
# (facebook.com/profile.php?id=..., youtube.com/channel/x?sub=1).
_SOCIAL_JUNK = re.compile(
    r"(?:/sharer|/share\.php|/intent/|/plugins/|/dialog/|/tr\?"
    r"|facebook\.com/(?:tr|plugins|sharer)|/hashtag/)",
    re.I,
)

_TEL_RE = re.compile(r"tel:([+0-9().\-\s]{7,25})", re.I)
_PHONE_TEXT_RE = re.compile(
    r"(?<![\d\-])(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")


def _scan_socials(html: str) -> dict:
    out = {k: "" for k in _SOCIAL_PATTERNS}
    for key, pat in _SOCIAL_PATTERNS.items():
        for url in pat.findall(html or ""):
            url = url.rstrip("\"'.,);")
            if _SOCIAL_JUNK.search(url):
                continue
            # Skip bare domain roots like facebook.com/ — no profile there.
            path = urllib.parse.urlsplit(url).path.strip("/")
            if not path or path.lower() in ("home", "share"):
                continue
            out[key] = url
            break
    return out


def _scan_phones(html: str, limit: int = 5) -> list:
    out, seen = [], set()
    for raw in _TEL_RE.findall(html or "") + _PHONE_TEXT_RE.findall(_strip_tags(html)):
        number = re.sub(r"[^\d+]", "", raw)
        digits = re.sub(r"\D", "", number)
        if not 9 <= len(digits) <= 15 or digits in seen:
            continue
        seen.add(digits)
        out.append(raw.strip())
        if len(out) >= limit:
            break
    return out


# ── Scoring ──

def _static_score(email: str, site_domain: str, accept_free_mail: bool,
                  *, credit: bool = False) -> tuple:
    """Score and kind from the address alone. Returns `(score, kind)`.

    The two vetoes here are ownership vetoes, and they are -100 rather than a
    withheld bonus because nothing else on the page may buy them back: a role
    prefix, a `mailto:` and a /contact/ page together are worth 53 points, and
    that was exactly the arithmetic that made `support@webcraftstudios.com` the
    best address on a window fitter's site with no address of its own.

    * An address on a domain the business does not own is somebody else's,
      whatever else it has going for it. Free mail is the deliberate exception
      — nobody *owns* gmail.com and a plumber writing from one is the case the
      free-mail rule exists for — and so is a site on a builder platform, where
      the registrable domain belongs to the platform and ownership cannot be
      read at all.
    * An address whose only appearance on the site is inside a credit line is
      the builder's, and that holds for free mail too: `Site by Jane Mercer
      Design — janemercerdesign@gmail.com` is a freelancer's inbox, and the
      free-mail rule would otherwise hand it the lead. It does not hold for an
      address the business owns, because an agency's own site credits itself.
    """
    local, _, domain = email.partition("@")
    score, kind = 0, "generic"
    free = _is_free_mail(domain)
    owned = _owns(domain, site_domain)

    if owned:
        # The exact domain outranks a sibling: when a business writes from both,
        # the one its website is on is the one it reads.
        score += 50 if _registrable(domain) == site_domain else 35

    if _hits(local, EMAIL_JUNK_PREFIXES):
        score -= 100
        kind = "role"
    elif _hits(local, EMAIL_ROLE_PREFIXES):
        score += 18
        kind = "role"
    elif _looks_personal(local):
        score += 30
        kind = "personal"

    if _hits(local, _CAREERS_PREFIXES):
        score -= 25
    if _hits(local, _LEGAL_PREFIXES):
        score -= 20

    if _is_disposable(domain):
        score -= 100
    if free:
        if kind == "generic":
            kind = "free"
        if not accept_free_mail:
            score -= 100

    if not owned and (credit or (site_domain and not free
                                 and site_domain not in _PLATFORM_DOMAINS)):
        score -= 100
    return score, kind


def _entry_key(entry: tuple) -> tuple:
    """Which of two sightings of one address is the better provenance.

    A credit line always loses, whatever page it is on: a footer byline on
    /contact/ carries the page bonus, and comparing the bonuses alone would let
    it outrank the same address written plainly on the home page and mark a
    stated address as the builder's.
    """
    method_bonus, page_bonus, method, _ = entry
    return (method != "credit", method_bonus + page_bonus)


def _is_contact_url(url: str) -> bool:
    path = urllib.parse.urlsplit(url or "").path.lower()
    return bool(re.search(r"contact|kontakt|contacto|get-in-touch", path))


def _rank_emails(per_page: dict, site_domain: str, *, accept_free_mail: bool = True,
                 deliverable: dict | None = None) -> list:
    """Turn `{page_url: {email: method}}` into the scored `emails` list.

    Pure: the DNS verdicts arrive pre-resolved in `deliverable`, so the whole
    ranking is testable without a network.
    """
    deliverable = deliverable or {}
    best: dict = {}
    credited: set = set()
    stated: set = set()
    for page_url, found in (per_page or {}).items():
        page_bonus = 10 if _is_contact_url(page_url) else 0
        for email, method in (found or {}).items():
            (credited if method == "credit" else stated).add(email)
            entry = (_METHOD_BONUS.get(method, 0), page_bonus, method, page_url)
            current = best.get(email)
            if current is None or _entry_key(entry) > _entry_key(current):
                best[email] = entry
    # One page's footer credit says nothing about a page that states the same
    # address outright: the builder is only the builder when *every* appearance
    # of the address across the crawl is a byline.
    credited -= stated

    rows = []
    for email, (method_bonus, page_bonus, method, page_url) in best.items():
        domain = email.partition("@")[2]
        score, kind = _static_score(email, site_domain, accept_free_mail,
                                    credit=email in credited)
        score += method_bonus
        verdict = deliverable.get(domain, None)
        if verdict is False:
            score -= 60
        rows.append({"email": email, "score": score, "kind": kind,
                     "source": page_url, "method": method,
                     "deliverable": verdict, "_free": _is_free_mail(domain),
                     "_page_bonus": page_bonus})

    # The gmail address on a plumber's site is the owner's real inbox — but
    # only reach for it when nothing on the company's own domain survived.
    if accept_free_mail and not any(r["score"] > 0 and not r["_free"] for r in rows):
        for row in rows:
            if row["_free"]:
                row["score"] += 8

    # The floor is the backstop against a candidate with no signal at all, so
    # it is tested on what the address is worth by itself. Where a page was
    # found is a tie-breaker between real candidates, not evidence that a
    # zero-score string is an address — and /contact/ is the page the crawler
    # prioritises, so a bonus applied first lifts junk on exactly the pages
    # most likely to carry any.
    ranked = [r for r in rows if r["score"] > 0]
    for row in ranked:
        row["score"] += row.pop("_page_bonus")
        row.pop("_free", None)
    ranked.sort(key=lambda r: (-r["score"], _METHOD_RANK[r["method"]], r["email"]))
    return ranked


# ── Deliverability ──

# One city's scrape hits the same handful of domains over and over, and a DNS
# round trip is slower than everything else in this module put together.
_DNS_CACHE: dict = {}
_DNS_LOCK = threading.Lock()


def _resolve(domain: str) -> bool:
    if _dns is not None:
        try:
            answers = _dns.resolve(domain, "MX", lifetime=_DNS_TIMEOUT)
            if answers:
                return True
        except Exception:
            pass  # fall through: plenty of domains take mail on the A record
    socket.getaddrinfo(domain, None)
    return True


def _deliverable(domain: str, timeout: float = _DNS_TIMEOUT):
    """True / False / None (unknown). Cached for the process lifetime."""
    domain = (domain or "").strip().lower()
    if not domain:
        return None
    with _DNS_LOCK:
        if domain in _DNS_CACHE:
            return _DNS_CACHE[domain]

    result = []

    def run():
        try:
            result.append(bool(_resolve(domain)))
        except (socket.gaierror, socket.herror, UnicodeError):
            result.append(False)
        except Exception:
            result.append(None)

    # getaddrinfo has no timeout argument, so the bound comes from the join;
    # the thread is a daemon and cannot hold up shutdown if the resolver hangs.
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    verdict = result[0] if result else None
    with _DNS_LOCK:
        _DNS_CACHE[domain] = verdict
    return verdict


def _deliverable_many(domains: list, workers: int = 6) -> dict:
    """Resolve several domains at once — serially this is the slowest step here."""
    domains = [d for d in dict.fromkeys(domains) if d]
    if not domains:
        return {}
    if len(domains) == 1:
        return {domains[0]: _deliverable(domains[0])}
    count = max(1, min(int(workers or 1), len(domains)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        return dict(zip(domains, pool.map(_deliverable, domains)))


# ── Crawl ──

# Where a contact address actually lives, in the order it is worth a fetch.
_PAGE_HINTS = (
    (100, re.compile(r"contact|kontakt|contacto|contatti|get-in-touch|reach-us", re.I)),
    (80, re.compile(r"about|our-story|who-we-are|company|meet", re.I)),
    (60, re.compile(r"team|staff|people|leadership|management", re.I)),
    (40, re.compile(r"support|help|customer-service|enquir|inquir", re.I)),
    (30, re.compile(r"impressum|imprint|mentions-legales|legal-notice|legal", re.I)),
)
_LINK_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                      re.I | re.S)
_ASSET_HREF_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|svg|ico|css|js|json|xml|pdf|docx?|xlsx?|zip|rar|"
    r"mp[34]|avi|mov|woff2?|ttf|eot)(?:$|[?#])", re.I)


def _rank_links(html: str, base_url: str, limit: int) -> list:
    """Same-host contact-ish URLs, best first, deduped and capped at `limit`."""
    if limit <= 0 or not html:
        return []
    # Host, not registrable domain: `shop.` / `blog.` / `careers.` are other
    # sites, and with a two-page budget one of them crowds out the real
    # /contact. _host_of drops `www.`, so http->https and www variants match.
    site = _host_of(base_url)
    scored, seen = [], set()
    for href, anchor in _LINK_RE.findall(html):
        href = href.strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        if _ASSET_HREF_RE.search(href):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if not url.startswith(("http://", "https://")):
            continue
        if site and _host_of(url) != site:
            continue
        url, _, _ = url.partition("#")
        key = url.rstrip("/").lower()
        if not key or key in seen or key == base_url.rstrip("/").lower():
            continue
        text = _strip_tags(anchor)[:80]
        weight = 0
        for value, pattern in _PAGE_HINTS:
            if pattern.search(href) or pattern.search(text):
                weight = max(weight, value)
        if not weight:
            continue
        # A nav link sits near the site root; a blog post about contacting us
        # does not. Shallow paths win ties.
        depth = urllib.parse.urlsplit(url).path.strip("/").count("/")
        seen.add(key)
        scored.append((-(weight - depth * 5), len(url), url))
    scored.sort()
    return [url for _, _, url in scored[:limit]]


def _fetch_many(urls: list, timeout: float, workers: int) -> dict:
    """Fetch pages concurrently. Returns `{final_url: html}` for what worked."""
    pages: dict = {}
    if not urls:
        return pages
    count = max(1, min(int(workers or 1), len(urls)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        futures = {pool.submit(_fetch_page, url, timeout): url for url in urls}
        for future in concurrent.futures.as_completed(futures):
            try:
                final_url, html, _ = future.result()
            except Exception:
                continue
            if html:
                pages[final_url or futures[future]] = html
    return pages


def _normalise_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    return url


def harvest_site(url: str, *, max_pages: int = 4, timeout: float = 8.0,
                 workers: int = 6, verify_dns: bool = True,
                 accept_free_mail: bool = True) -> dict:
    """Crawl a business site and return every contact signal it exposes.

    Never raises. An unreachable site comes back with `reachable=False` and a
    short `error`; the caller keeps going. The fetched HTML is returned under
    `"html"` so `core.audit` can work from this crawl instead of its own.
    """
    out = {
        "url": url or "", "final_url": "", "reachable": False, "emails": [],
        "best_email": "", "socials": {k: "" for k in _SOCIAL_PATTERNS},
        "phones": [], "pages": [], "html": {}, "error": "",
    }
    try:
        return _harvest(out, max_pages, timeout, workers, verify_dns,
                        accept_free_mail)
    except Exception as exc:
        out["error"] = out["error"] or _short_error(exc)
        return out


def _status_of(error: str) -> int:
    """The HTTP status inside an error string like "http 404", else 0.

    `_fetch_page` reports a status as text because most of its failures have no
    status at all -- a refused connection, an NXDOMAIN, a timeout. This reads
    one back out when there is one.
    """
    text = str(error or "").strip().lower()
    if not text.startswith("http "):
        return 0
    digits = text[5:].strip().split()[0] if text[5:].strip() else ""
    return int(digits) if digits.isdigit() else 0


def _harvest(out: dict, max_pages: int, timeout: float, workers: int,
             verify_dns: bool, accept_free_mail: bool) -> dict:
    start = _normalise_url(out["url"])
    if not start:
        out["error"] = "no url"
        return out

    final_url, html, error = _fetch_page(start, timeout)
    if not html and error in ("ssl", "unreachable") and start.startswith("https://"):
        # Hosts with no TLS at all are still worth a lead. Deliberately not
        # retried after a timeout or an NXDOMAIN: plain http fails the same way
        # and the second wait is paid on every dead site in the list.
        final_url, html, error = _fetch_page("http://" + start[8:], timeout)
    if not html:
        out["final_url"] = final_url or start
        out["error"] = error or "unreachable"
        return out

    # An error page still carries a body, and a branded 404 reads like a website
    # to every rule in the audit -- which is how "no way to capture a lead" got
    # written from an expired domain's error page and mailed as an observation
    # about the business. `if not html` never caught it because the body is
    # real; the status is the only thing that knows. Expired and reclaimed
    # domains serving a styled 404 are exactly what a scraped list is full of.
    status = _status_of(error)
    if status >= 400:
        out["final_url"] = final_url or start
        out["error"] = error
        return out

    final_url = final_url or start
    out.update({"final_url": final_url, "reachable": True, "error": error})
    pages = {final_url: html}
    pages.update(_fetch_many(_rank_links(html, final_url, max(0, int(max_pages) - 1)),
                             timeout, workers))

    per_page = {page_url: _scan_page(page_html, page_url)
                for page_url, page_html in pages.items()}
    site_domain = _registrable(_host_of(final_url))

    ranked = _rank_emails(per_page, site_domain, accept_free_mail=accept_free_mail)
    if verify_dns and ranked:
        # Only survivors are worth a DNS round trip, and only a handful of them.
        domains = list(dict.fromkeys(r["email"].partition("@")[2] for r in ranked))[:8]
        ranked = _rank_emails(per_page, site_domain, accept_free_mail=accept_free_mail,
                              deliverable=_deliverable_many(domains, workers))

    socials = out["socials"]
    phones: list = []
    for page_url in pages:
        for key, value in _scan_socials(pages[page_url]).items():
            if value and not socials[key]:
                socials[key] = value
        for phone in _scan_phones(pages[page_url]):
            if phone not in phones:
                phones.append(phone)

    out.update({
        "emails": ranked,
        "best_email": ranked[0]["email"] if ranked else "",
        "phones": phones[:5],
        "pages": list(pages),
        "html": pages,
    })
    return out


# ── Legacy API (core.scraper depends on these exactly as they are) ──

def extract_contacts(html: str, base_url: str = "") -> dict:
    """Pure extraction of contacts from a page's HTML. No network.

    The flat one-email-one-social shape the results table and CSV export have
    always used. Internally this is the same scorer `harvest_site` runs, minus
    the crawl and the DNS check, so a wrong pick here is a wrong pick there.
    """
    out = {k: "" for k in ENRICH_KEYS}
    if not html:
        return out
    try:
        site_domain = _registrable(_host_of(base_url))
        ranked = _rank_emails({base_url: _scan_page(html, base_url)}, site_domain)
        if ranked:
            out["email"] = ranked[0]["email"]
        out.update(_scan_socials(html))
    except Exception:
        return out
    return out


def enrich_website(url: str, timeout: float = 8.0, fields: tuple = ENRICH_KEYS) -> dict:
    """Fetch a business website (home + its best contact page) and extract contacts.

    Returns only the requested `fields`. Never raises — failures yield "".

    Deliberately narrower than `harvest_site`: this runs inside the scrape loop
    once per result, so it stays at two pages and skips DNS. The outreach path
    calls `harvest_site` directly when it wants the full picture.
    """
    out = {k: "" for k in fields}
    if not url:
        return out
    try:
        site = harvest_site(url, max_pages=2, timeout=timeout, workers=2,
                            verify_dns=False)
        contacts = dict(site["socials"])
        contacts["email"] = site["best_email"]
    except Exception:
        return out
    return {k: contacts.get(k, "") for k in fields}
