"""Offline tests for core.enrich's email extraction. No network, no Qt.

Run:  venv/Scripts/python.exe -m tests.test_enrich_email
(or `python -m pytest tests/ -q` where pytest is installed).

Every fixture here is a hand-written miniature of something the enricher gets
wrong when it is naive: a Cloudflare-obfuscated span, an address spelled out in
brackets, a JSON-LD block, a page whose only "email" is an asset filename. The
copy corpus is the mirror image — the deobfuscator must not *invent* addresses,
because a fabricated recipient is worse than a missing one.

`_fetch_page` is the module's single network seam, so the crawl tests stub it.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import enrich as E  # noqa: E402


# ── Helpers ──

def cf_encode(address: str, key: int = 0x2a) -> str:
    """Produce a Cloudflare data-cfemail payload the way Cloudflare does."""
    return ("%02x" % key) + "".join("%02x" % (ord(c) ^ key) for c in address)


def email_of(html: str, base: str = "https://acmeroofing.ca") -> str:
    return E.extract_contacts(html, base)["email"]


class _StubFetch:
    """Replace the network seam with a fixed {url: html} map."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls = []
        self._real = E._fetch_page

    def __enter__(self):
        E._fetch_page = self._fetch
        return self

    def __exit__(self, *exc):
        E._fetch_page = self._real
        return False

    def _fetch(self, url, timeout=8.0):
        self.calls.append(url)
        html = self.pages.get(url) or self.pages.get(url.rstrip("/"))
        if html is None:
            return "", "", "http 404"
        return url, html, ""


# ── Cloudflare ──

def test_cfemail_decode():
    assert E._decode_cfemail(cf_encode("info@acmeroofing.ca")) == "info@acmeroofing.ca"
    assert E._decode_cfemail(cf_encode("bob@x.ca", key=0xff)) == "bob@x.ca"
    assert E._decode_cfemail("") == ""
    assert E._decode_cfemail("zzzz") == ""
    assert E._decode_cfemail("abc") == ""          # odd length
    print("cfemail decode: OK")


def test_cfemail_in_page():
    html = (
        '<a href="/cdn-cgi/l/email-protection" class="__cf_email__" '
        'data-cfemail="%s">[email&#160;protected]</a>' % cf_encode("hello@acmeroofing.ca")
    )
    assert email_of(html) == "hello@acmeroofing.ca"

    href = ('<a href="/cdn-cgi/l/email-protection#%s">contact</a>'
            % cf_encode("bookings@acmeroofing.ca"))
    assert email_of(href) == "bookings@acmeroofing.ca"
    print("cfemail in page: OK")


# ── Obfuscation forms ──

def test_obfuscation_forms():
    cases = {
        "info(at)acmeroofing(dot)ca": "info@acmeroofing.ca",
        "info [at] acmeroofing [dot] ca": "info@acmeroofing.ca",
        "info {at} acmeroofing {dot} ca": "info@acmeroofing.ca",
        "info&#64;acmeroofing&#46;ca": "info@acmeroofing.ca",
        "info%40acmeroofing.ca": "info@acmeroofing.ca",
    }
    for raw, expected in cases.items():
        got = email_of("<p>Reach us: %s</p>" % raw)
        assert got == expected, (raw, got)
    print("obfuscation forms: OK")


def test_a_bracketed_at_needs_a_bracketed_dot():
    """Brackets alone are not the whole signal — the dot has to answer them.

    `info (at) acmeroofing.ca` used to be read as an address on the strength of
    the brackets alone, and nothing bounded the false-positive side of that: the
    identical shape reads `Follow us (at) acme.ca`, `Call Dot (at) acme.pizza`
    and `Open (at) shorts.mov` as mailboxes. Deliberate obfuscation is
    consistent — whoever writes `(at)` writes `(dot)` too — so a `(at)` whose
    domain keeps every dot literal is someone writing the English word inside
    brackets. Requiring a spelled `at` to be answered by a spelled `dot` is
    structural and symmetric: no word list, and nothing that can go stale.

    The first three lines below are the cost, and they are now a miss. The rest
    were mints, which is the trade this makes deliberately.
    """
    for raw in ("info(at)acmeroofing.ca", "info [at] acmeroofing.ca",
                "info {at} acmeroofing.ca", "Follow us (at) acme.ca",
                "Call Dot (at) acme.pizza", "Open (at) shorts.mov"):
        assert E._scan_page("<p>%s</p>" % raw, "x") == {}, raw
    # One spelled dot anywhere in the domain is enough to prove the intent, so
    # a mixed line is still read rather than held to an all-or-nothing rule.
    assert E._scan_page("<p>info (at) shop.acmeroofing (dot) ca</p>", "x") == {
        "info@shop.acmeroofing.ca": "deobfuscated"}
    print("bracketed at needs a bracketed dot: OK")


def test_bracketed_markers_carry_any_case_and_any_suffix():
    """Brackets are the whole signal, so nothing else has to be inspected.

    No case rule and no roster of acceptable suffixes: a business that shouts
    its own contact line keeps its address, and `.plumbing` works the day it is
    delegated rather than the day someone remembers to edit a list.
    """
    cases = {
        "INFO (AT) ACMEROOFING (DOT) CA": "info@acmeroofing.ca",
        "info [AT] acmeroofing [DOT] ca": "info@acmeroofing.ca",
        "Email Sarah (at) acmeroofing (dot) ca": "sarah@acmeroofing.ca",
        "mike.reid (at) acmeroofing (dot) ca": "mike.reid@acmeroofing.ca",
        # The name that beat the last guard is just an address in brackets.
        "office (at) polka (dot) salon": "office@polka.salon",
    }
    for raw, expected in cases.items():
        # Read on the site that owns the address. An address on a domain the
        # business does not own is refused by the ranker however it is spelled,
        # and this fixture is about the brackets, not about ownership.
        assert email_of("<p>%s</p>" % raw,
                        "https://" + expected.partition("@")[2]) == expected, raw
    for tld in MODERN_GTLDS:
        html = "<p>Reach us: info (at) joespizza (dot) %s</p>" % tld
        assert E._scan_page(html, "x") == {"info@joespizza." + tld: "deobfuscated"}, tld
    # A suffix that is not TLD-shaped is still not an address.
    assert E._scan_page("<p>info (at) joespizza (dot) 44</p>", "x") == {}
    assert E._scan_page("<p>info (at) joespizza (dot) c0m</p>", "x") == {}
    print("bracketed markers take any case/suffix: OK (%d gTLDs)" % len(MODERN_GTLDS))


def test_angle_brackets_are_not_obfuscation_markers():
    """`<at>` and `<dot>` are markup, not a convention anyone writes.

    The angle pair is the one bracket that occurs naturally in a document, so
    an XML data island or a mis-escaped code sample walks straight through a
    marker class that accepts it.
    """
    for raw in ("info <at> acmeroofing.ca", "info <at> acmeroofing <dot> ca",
                "<at>info</at><dot>acmeroofing.ca</dot>",
                "<contact><at>sales</at><domain>acmeroofing.ca</domain></contact>"):
        assert E._scan_page("<p>%s</p>" % raw, "x") == {}, raw
    print("angle brackets are not markers: OK")


def test_fromcharcode():
    codes = ",".join(str(ord(c)) for c in "sales@acmeroofing.ca")
    html = "<script>var a=String.fromCharCode(%s);document.write(a);</script>" % codes
    assert email_of(html) == "sales@acmeroofing.ca"
    print("fromCharCode: OK")


def test_prose_is_not_an_email():
    """The spaced ` at ` / ` dot ` form must not manufacture addresses."""
    prose = [
        "<p>We look at the dot com era differently.</p>",
        "<p>Arrive at the office dot Ask for Sam.</p>",
        "<p>Best in class at what we do.</p>",
    ]
    for html in prose:
        assert email_of(html) == "", html
    print("prose not an email: OK")


# Ordinary marketing copy. Every one of these lines pairs a word with a domain
# across a bare " at " — the shape a naive obfuscation rule reads as an address.
# A fabricated address is not a near miss: it becomes best_email, exports to the
# CSV and gets cold-emailed, so the bar here is zero, not few.
PROSE_AT_LINES = (
    "Order online at acmepizza.com",
    "Shop at acmepizza.com and save 10%",
    "Book at acmepizza.com or call the store",
    "Serving customers at acmepizza.com since 1998",
    "Read reviews at yelp.ca before you visit",
    "Find us at maps.google.com",
    "See the full menu at acmepizza.com/menu",
    "Apply at acmepizza.com/careers",
    "Track your order at acmepizza.com/orders",
    "Follow along at instagram.com",
    "Watch the short film at youtube.com",
    "Download the app at apple.com",
    "Compare plans at acmepizza.com/pricing",
    "Register at eventbrite.ca for the launch night",
    "Support hours are posted at acmepizza.com/help",
    "Terms and conditions at acmepizza.com/terms",
    "Gift cards at acmepizza.com make a great present",
    "Catering at acmepizza.com for parties of ten or more",
    "Meet the team at acmepizza.com/about",
    "Everything at acmepizza.com is made fresh each morning",
    "Learn more at acmepizza.com/faq",
    "See photos at flickr.com",
    "Reserve a table at opentable.ca",
    "Details at acmepizza.com/promo",
    "Newsletter signup at acmepizza.com/subscribe",
    "Careers at acmepizza.com are posted every Monday",
    "Our suppliers are listed at acmepizza.com/sourcing",
    "Nutrition information at acmepizza.com/nutrition",
    "Franchise enquiries at acmepizza.com/franchise",
    "Vouchers redeemable at acmepizza.com or in store",
    "Rated best pizza at the 2019 food awards",
    "Now hiring at our new Danforth location",
    "Delivery starts at 11am daily",
    "Prices start at 9.99 per person",
    "Free parking at the rear of the building",
    "Ask at reception for the wifi password",
)


# The corpus that settled it: a business writing its own copy in lowercase,
# spelled dot and all. Four guards were tried against this shape — a TLD
# roster, a prose word list, a Title-Case rule — and ordinary house style beat
# every one, because `the team at bright dot design build websites` and
# `info at acme dot com` are the same string to any rule that can be written
# here. Line 7 is the other half of it: a word list can only ever be English.
LOWERCASE_COPY_LINES = (
    "the team at bright dot design build websites for local trades.",
    "our studio at 5 dot lane is open weekdays from nine.",
    "we started at kirk dot studio back in 2014 with one bench.",
    "the workshop at maple dot works runs classes every second saturday.",
    "you can find us at corner dot cafe on the high street.",
    "the crew at north dot builders finished the extension in six weeks.",
    "liegt at 8 dot strasse im zweiten stock.",
    "everything at harbour dot supply is cut to order.",
    "the counter at pine dot bakery opens at six every morning.",
    "the front desk at ridge dot dental takes walk-ins on tuesdays.",
    "our chairs at willow dot salon are booked a fortnight ahead.",
    "the kitchen at slate dot catering handles parties of eighty.",
    "the yard at oak dot timber is stacked by species.",
    "the office at delta dot legal has moved to the second floor.",
    "the garage at victor dot motors is behind the laundrette.",
    "the shop at anchor dot marine sells rope by the metre.",
    "the gallery at chalk dot press hangs new work each month.",
    "the studio at ember dot ceramics fires twice a week.",
    "the bench at copper dot repairs takes drop-offs until four.",
    "the desk at summit dot travel books group tours only.",
    "we opened at bloom dot florist on a wet tuesday in march.",
    "the bar at ledger dot lounge stops serving at eleven.",
    "the surgery at heath dot vet is closed for lunch.",
    "the depot at stone dot haulage runs a night shift.",
    "the loft at atlas dot studios rents by the day.",
    "the pitch at rowan dot sports is floodlit until ten.",
    "the range at falcon dot archery is members only.",
    "the pool at cedar dot fitness is twenty five metres.",
)

# An agency site that never states an address anywhere. It used to hand back
# `studio@5.lane` — a fabricated recipient on a page with nothing to find.
AGENCY_PAGE = """<!doctype html>
<html><head><title>bright</title></head><body>
  <h1>bright</h1>
  <p>the team at bright dot design build websites for local trades.</p>
  <p>our studio at 5 dot lane is open weekdays from nine.</p>
  <p>no brief is too small. call the studio and ask for the diary.</p>
</body></html>"""

# The shape the deleted arm read as an address, spelled out here so the corpus
# cannot quietly stop carrying it. A corpus that no longer reaches the danger
# is a green test guarding nothing — which is how an earlier prose test came to
# test nothing at all.
BARE_AT_SHAPE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9][A-Za-z0-9._%+\-]{0,62}"
    r"\s+at\s+(?:[A-Za-z0-9][A-Za-z0-9\-]{0,61}\s+dot\s+)+[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9\-])", re.I)


def test_the_copy_corpus_provably_carries_the_fabricating_shape():
    """Every line still looks exactly like the thing that used to be minted.

    This asserts the corpus, not the module: if someone rewrites these lines
    into something harmless, the zero below stops meaning anything and this
    fails first.
    """
    carrying = [l for l in LOWERCASE_COPY_LINES if BARE_AT_SHAPE.search(l)]
    assert len(carrying) == len(LOWERCASE_COPY_LINES), [
        l for l in LOWERCASE_COPY_LINES if l not in carrying]
    assert BARE_AT_SHAPE.search("info at acmeroofing dot ca")
    print("copy corpus carries the shape: OK (%d/%d lines)"
          % (len(carrying), len(LOWERCASE_COPY_LINES)))


def test_bare_spaced_at_and_dot_yield_nothing():
    """`info at acme dot com` is no longer read as an address, by design.

    Stated plainly because it is a real cost: a site that writes its address
    that way and nowhere else is now a miss. The corpus is why — there is no
    rule available here that keeps the first line and refuses the other 28.
    """
    assert E._scan_page("<p>info at acmeroofing dot ca</p>", "x") == {}
    assert email_of("<p>Reach us: info at acmeroofing dot ca</p>") == ""
    assert E._scan_page("<p>info at acmeroofing (dot) ca</p>", "x") == {}

    lines = LOWERCASE_COPY_LINES + PROSE_AT_LINES
    minted = {}
    for line in lines:
        found = E._scan_page("<p>%s</p>" % line, "https://acmepizza.com/")
        if found:
            minted[line] = found
        assert email_of("<p>%s</p>" % line, "https://acmepizza.com/") == "", line
    assert minted == {}, minted
    # And in bulk, the way the copy actually arrives — one page, many lines.
    page = "<html><body>%s</body></html>" % "".join("<p>%s</p>" % l for l in lines)
    assert E._scan_page(page, "https://acmepizza.com/") == {}
    assert email_of(page, "https://acmepizza.com/") == ""
    print("bare at/dot yields nothing: OK (%d lines)" % len(lines))


def test_a_site_that_never_states_an_address_returns_none():
    """The whole failure in one page, end to end through harvest_site."""
    assert E._scan_page(AGENCY_PAGE, "https://bright.example/") == {}
    assert E.extract_contacts(AGENCY_PAGE, "https://bright.example/")["email"] == ""
    with _StubFetch({"https://bright.example": AGENCY_PAGE}):
        site = E.harvest_site("bright.example", verify_dns=False)
    assert site["reachable"] is True, site
    assert site["best_email"] == "" and site["emails"] == [], site["emails"]
    print("no address means no address: OK")


def test_the_deleted_arm_is_gone_not_merely_unreached():
    """A corpus proves what a page does; it cannot prove a branch is absent.

    These read the module itself, so re-introducing a bare `at` marker — or a
    gate to police one — fails here even if every fixture above stays green.
    """
    for name in ("_spaced_is_plausible", "_spelled_form_is_lowercase",
                 "_PROSE_WORDS", "_BARE_AT_RE", "_SPELLED_DOT_RE",
                 "_scan_reversed", "_BIDI_RE"):
        assert not hasattr(E, name), name
    for pattern in (E._AT_BARE, E._AT_SPELLED, E._DOT_SPELLED, E._DOT_MARKER,
                    E._OBFUSCATED_RE.pattern):
        assert r"\bat\b" not in pattern, pattern
        assert r"\bdot\b" not in pattern, pattern
    for marker in (E._AT_BARE, E._AT_SPELLED, E._DOT_SPELLED, E._DOT_MARKER,
                   E._LOCAL_DOT):
        assert "<" not in marker and ">" not in marker, marker
    # The bare `@` and `%40` may not carry the whitespace the bracketed form
    # needs: sharing it is what read `Follow us @acmepizza.studio` as an address.
    assert r"\s" not in E._AT_BARE, E._AT_BARE
    print("deleted arm is absent from the module: OK")


def test_a_page_of_copy_with_one_bracketed_address_finds_only_that():
    """Removing the arm must not cost the address that is genuinely there."""
    page = "<html><body>%s<p>Reach us: info (at) acmepizza (dot) com</p></body></html>" % (
        "".join("<p>%s</p>" % line for line in LOWERCASE_COPY_LINES + PROSE_AT_LINES))
    found = E._scan_page(page, "https://acmepizza.com/")
    assert found == {"info@acmepizza.com": "deobfuscated"}, found
    assert email_of(page, "https://acmepizza.com/") == "info@acmepizza.com"
    print("real address survives the copy: OK")


# The extensions a small business actually registers. A gate that enumerates
# the suffixes it will accept discards every one of these, and the list can
# only ever be as current as the day someone last edited it.
MODERN_GTLDS = (
    "pizza", "cafe", "salon", "dental", "plumbing", "agency", "photography",
    "kitchen", "florist", "construction", "contractors", "garden", "fitness",
    "clothing", "jewelry", "coffee", "tattoo", "vet", "dentist", "physio",
)


def test_a_domain_label_is_a_name_not_a_word():
    """`home`, `work`, `best` and `time` are ordinary words and ordinary
    domains. Nothing inspects the labels any more, so nothing can drop them."""
    for label in ("home", "work", "best", "time", "good", "first", "now"):
        html = "<p>Reach us: info (at) %s (dot) ca</p>" % label
        assert E._scan_page(html, "x") == {"info@%s.ca" % label: "deobfuscated"}, label
    for prefix in ("office", "post", "team", "help", "general", "ask"):
        html = "<p>Reach us: %s (at) acme (dot) com</p>" % prefix
        assert E._scan_page(html, "x") == {"%s@acme.com" % prefix: "deobfuscated"}, prefix
    print("domain labels are names, not words: OK")


def test_spelled_dots_in_the_local_part():
    """`jane dot smith (at) acme (dot) ca` is jane.smith, not smith.

    Keeping only the last token mints an address that looks real and does not
    exist — the same harm as inventing one out of copy. The bare ` dot ` is
    kept in the local part for exactly that reason, and it cannot fire on its
    own: an `at` marker has to follow it immediately.
    """
    cases = {
        "jane dot smith (at) acme (dot) ca": "jane.smith@acme.ca",
        "mary dot lou dot chen [at] acme [dot] ca": "mary.lou.chen@acme.ca",
        "jane [dot] smith {at} acme {dot} ca": "jane.smith@acme.ca",
        "jane.smith (at) acme (dot) ca": "jane.smith@acme.ca",
    }
    for raw, expected in cases.items():
        found = E._scan_page("<p>Reach us: %s</p>" % raw, "x")
        assert found == {expected: "deobfuscated"}, (raw, found)
    # A bare `dot` glued to its neighbours is a local part, not a separator.
    assert E._scan_page("<p>dot@transportation.gov</p>", "x") == {
        "dot@transportation.gov": "text"}
    assert E._clean_email("info.dot.com@acme.ca") == "info.dot.com@acme.ca"
    # With no `at` marker behind it, a spelled local dot is inert.
    for line in LOWERCASE_COPY_LINES + PROSE_AT_LINES:
        assert E._scan_page("<p>%s</p>" % line, "x") == {}, line
    print("spelled dots in the local part: OK")


def test_no_page_is_ever_read_backwards():
    """Nothing is mirrored, so no mirror can be handed to the CSV.

    `direction:rtl` is boilerplate in every RTL-capable theme, and every
    `first.last@domain` — the commonest personal address there is — mirrors
    into a second string that parses as an address, matches the person shape
    and outscores the real one. `service.desk@partnerco.com` scores 18 as a
    role; its mirror `moc.ocrentrap@ksed.ecivres` scores 30 as a person, and
    that was best_email on any themed page carrying a dotted local part.

    The reseller's own line is now refused as well, and the assertion below is
    inverted to say so: `partnerco.com` is not a domain `acmeroofing.ca` owns,
    so nothing on this page can be the roofer's address. That is the ownership
    rule, not this one — the mirror is what is on trial here, and it is now
    provably absent on both sides of the floor.
    """
    themed = ('<style>.rtl-support{direction:rtl}</style>'
              '<p>our reseller: service.desk@partnerco.com</p>')
    assert E._scan_page(themed, "x") == {"service.desk@partnerco.com": "text"}
    ranked = E._rank_emails({"https://acmeroofing.ca/": E._scan_page(themed)},
                            "acmeroofing.ca")
    assert [r["email"] for r in ranked] == [], ranked
    # On the site that does own it, the address comes back whole and the mirror
    # is still nowhere.
    ranked = E._rank_emails({"https://partnerco.com/": E._scan_page(themed)},
                            "partnerco.com")
    assert [r["email"] for r in ranked] == ["service.desk@partnerco.com"], ranked

    for markup in ('<div style="direction:rtl">%s</div>',
                   '<span style="unicode-bidi:bidi-override">%s</span>',
                   '<bdo dir="rtl">%s</bdo>'):
        page = markup % '<a href="mailto:jane.smith@acmeroofing.ca">Jane</a>'
        assert E._scan_page(page, "x") == {"jane.smith@acmeroofing.ca": "mailto"}, markup
    print("nothing is read backwards: OK")


def test_a_backwards_literal_is_refused_outright():
    """What is left on a page that genuinely uses the trick.

    The address is written backwards in the source, so a plain scan reads the
    literal `ac.emca@htims.enaj` as ordinary text. `enaj` is not a delegated
    TLD, so `_clean_email` refuses it before it can be scored at all, and the
    refusal does not depend on `verify_dns` — which matters, because the paths
    the app actually runs (`enrich_website`, and the campaign's audit pass) all
    disable it. This used to survive at score 30 on exactly those paths and put
    a garbage string in the Email column.

    The deliverability penalty still exists and is asserted below on a domain
    that is TLD-shaped but does not resolve, which is the case it is for.
    """
    page = ('<style>.x{direction:rtl}</style>'
            '<p>jane.smith@acme.ca</p><p>ac.emca@htims.enaj</p>')
    found = E._scan_page(page, "https://acme.ca/")
    assert found == {"jane.smith@acme.ca": "text"}, found

    # End to end, with DNS off — the path the app really runs.
    trick = ('<html><body><span style="unicode-bidi:bidi-override;direction:rtl">'
             '%s</span></body></html>' % "jane.smith@acme.ca"[::-1])
    with _StubFetch({"https://widgets.example": trick}):
        assert E.harvest_site("widgets.example", verify_dns=False)["best_email"] == ""

    # A domain that is TLD-shaped but dead is still the DNS check's job.
    dead = E._scan_page('<p>jane.smith@acme.ca</p><p>sales@ghostfirm.ca</p>',
                        "https://acme.ca/")
    assert dead == {"jane.smith@acme.ca": "text", "sales@ghostfirm.ca": "text"}
    live = {"acme.ca": True, "ghostfirm.ca": False}
    ranked = E._rank_emails({"https://acme.ca/": dead}, "acme.ca", deliverable=live)
    assert [r["email"] for r in ranked] == ["jane.smith@acme.ca"], ranked
    # The penalty reaches a text-scanned candidate, not only a decoded one.
    assert ranked[0]["method"] == "text"
    contact = E._rank_emails({"https://acme.ca/contact/": dead}, "acme.ca",
                             deliverable=live)
    assert [r["email"] for r in contact] == ["jane.smith@acme.ca"], contact
    print("backwards literal refused outright: OK")


def test_phantom_mirror_does_not_disable_free_mail_fallback():
    """The +8 free-mail rule only fires when nothing else scores; a phantom
    counted as 'something else' and silently suppressed it."""
    html = "<p>Email jane.smith@gmail.com for a quote.</p>"
    assert email_of(html, "https://acmeroofing.ca") == "jane.smith@gmail.com"
    ranked = E._rank_emails({"https://acmeroofing.ca/": E._scan_page(html)},
                            "acmeroofing.ca")
    assert [(r["email"], r["score"]) for r in ranked] == [
        ("jane.smith@gmail.com", 38)], ranked      # +30 person, +8 alone
    print("free-mail fallback not suppressed: OK")


def test_entity_encoded_address():
    encoded = "".join("&#%d;" % ord(c) for c in "office@acmeroofing.ca")
    assert email_of("<span>%s</span>" % encoded) == "office@acmeroofing.ca"
    print("entity-encoded address: OK")


# ── Structured data ──

def test_jsonld_email():
    html = """
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"LocalBusiness",
     "name":"Acme Roofing",
     "contactPoint":{"@type":"ContactPoint","email":"mailto:info@acmeroofing.ca"}}
    </script>
    """
    assert email_of(html) == "info@acmeroofing.ca"
    print("json-ld email: OK")


def test_jsonld_broken_json_still_yields_email():
    html = ('<script type="application/ld+json">{"@type":"Org", '
            '"email":"info@acmeroofing.ca",}</script>')   # trailing comma
    assert email_of(html) == "info@acmeroofing.ca"
    print("json-ld fallback: OK")


def test_microdata_email():
    html = ('<div itemscope><span itemprop="email" '
            'content="office@acmeroofing.ca"></span></div>')
    assert email_of(html) == "office@acmeroofing.ca"
    html_text = '<span itemprop="email">reception@acmeroofing.ca</span>'
    assert email_of(html_text) == "reception@acmeroofing.ca"
    print("microdata email: OK")


# ── Priority and scoring ──

def test_mailto_beats_body_text():
    html = ('<a href="mailto:office@acmeroofing.ca?subject=Hi%20there">write</a>'
            '<p>or office2@acmeroofing.ca</p>')
    found = E._scan_page(html, "https://acmeroofing.ca")
    assert found["office@acmeroofing.ca"] == "mailto", found
    assert email_of(html) == "office@acmeroofing.ca"
    print("mailto priority: OK")


def test_scoring_order():
    html = """
      <p>mike.reid@acmeroofing.ca</p>
      <p>info@acmeroofing.ca</p>
      <p>careers@acmeroofing.ca</p>
      <p>privacy@acmeroofing.ca</p>
      <p>noreply@acmeroofing.ca</p>
      <p>partner@somewhereelse.ca</p>
    """
    ranked = E._rank_emails({"https://acmeroofing.ca/": E._scan_page(html)},
                            "acmeroofing.ca")
    order = [r["email"] for r in ranked]
    assert order[0] == "mike.reid@acmeroofing.ca", order      # person +30
    assert order[1] == "info@acmeroofing.ca", order           # role +18
    assert "noreply@acmeroofing.ca" not in order, order       # junk -100
    assert "partner@somewhereelse.ca" not in order, order     # off-domain, no signal
    assert order.index("careers@acmeroofing.ca") > order.index("info@acmeroofing.ca")
    assert order.index("privacy@acmeroofing.ca") > order.index("info@acmeroofing.ca")
    kinds = {r["email"]: r["kind"] for r in ranked}
    assert kinds["mike.reid@acmeroofing.ca"] == "personal", kinds
    assert kinds["info@acmeroofing.ca"] == "role", kinds
    print("scoring order: OK")


def test_same_domain_preference():
    html = ('<a href="mailto:hello@marketingagency.io">agency</a>'
            '<a href="mailto:hello@acmeroofing.ca">us</a>')
    assert email_of(html) == "hello@acmeroofing.ca"
    # A lookalike suffix is a different business, not the same domain.
    html2 = ('<a href="mailto:hello@notacmeroofing.ca">x</a>'
             '<a href="mailto:hello@acmeroofing.ca">y</a>')
    assert email_of(html2, "https://www.acmeroofing.ca") == "hello@acmeroofing.ca"
    print("same-domain preference: OK")


def test_free_mail_only_when_alone():
    site = "https://acmeroofing.ca"
    only = '<p>Email acmeroofers@gmail.com for a quote.</p>'
    assert email_of(only, site) == "acmeroofers@gmail.com"
    both = only + '<a href="mailto:info@acmeroofing.ca">info</a>'
    assert email_of(both, site) == "info@acmeroofing.ca"
    ranked = E._rank_emails({site: E._scan_page(both)}, "acmeroofing.ca")
    assert [r["email"] for r in ranked] == ["info@acmeroofing.ca"], ranked
    print("free-mail fallback: OK")


def test_contact_page_bonus():
    per_page = {
        "https://acmeroofing.ca/": {"a@acmeroofing.ca": "text"},
        "https://acmeroofing.ca/contact/": {"b@acmeroofing.ca": "text"},
    }
    ranked = E._rank_emails(per_page, "acmeroofing.ca")
    assert ranked[0]["email"] == "b@acmeroofing.ca", ranked
    assert ranked[0]["source"] == "https://acmeroofing.ca/contact/"
    assert [r["score"] for r in ranked] == [60, 50], ranked
    print("contact-page bonus: OK")


def test_the_contact_bonus_cannot_lift_a_candidate_over_the_floor():
    """The floor is the backstop against a string with no signal at all.

    /contact/ is the page the crawler prioritises, so a bonus applied before
    the floor promotes junk on exactly the pages most likely to carry any: an
    off-domain address with nothing else going for it scores 0, and +10 put it
    into the CSV as best_email.
    """
    junk = {"partner@somewhereelse.ca": "text"}
    for page_url in ("https://acmeroofing.ca/", "https://acmeroofing.ca/contact/",
                     "https://acmeroofing.ca/kontakt/",
                     "https://acmeroofing.ca/get-in-touch/"):
        assert E._rank_emails({page_url: junk}, "acmeroofing.ca") == [], page_url
    # A candidate that clears the floor on its own still takes the bonus.
    real = {"info@acmeroofing.ca": "text"}
    ranked = E._rank_emails({"https://acmeroofing.ca/contact/": real},
                            "acmeroofing.ca")
    assert [(r["email"], r["score"]) for r in ranked] == [
        ("info@acmeroofing.ca", 78)], ranked          # +50 domain, +18 role, +10 page
    # The DNS penalty is taken before the floor too, so a dead domain has to
    # survive on its own merits rather than on where the page was found.
    dead = E._rank_emails({"https://acmeroofing.ca/contact/": real}, "acmeroofing.ca",
                          deliverable={"acmeroofing.ca": False})
    assert [(r["email"], r["score"]) for r in dead] == [
        ("info@acmeroofing.ca", 18)], dead            # 68 - 60 dns, then +10 page
    weak = E._rank_emails({"https://acmeroofing.ca/contact/": junk}, "acmeroofing.ca",
                          deliverable={"somewhereelse.ca": False})
    assert weak == [], weak
    print("contact bonus cannot lift over the floor: OK")


def test_dns_penalty_and_unknown():
    per_page = {"https://acmeroofing.ca/": {"info@acmeroofing.ca": "mailto"}}
    dead = E._rank_emails(per_page, "acmeroofing.ca",
                          deliverable={"acmeroofing.ca": False})
    assert dead[0]["score"] == 93 - 60, dead
    assert dead[0]["deliverable"] is False
    unknown = E._rank_emails(per_page, "acmeroofing.ca",
                             deliverable={"acmeroofing.ca": None})
    assert unknown[0]["score"] == 93, unknown
    assert unknown[0]["deliverable"] is None
    print("dns penalty: OK")


# ── Ownership ──

# A large share of small-business sites were built by somebody else and say so
# in the footer, and those bylines carry real, deliverable addresses: the
# agency's studio inbox, the theme author's, the host's support desk. An
# address is not the business's for having been printed on its page — and this
# failure is worse than finding nothing, because the letter opens by naming
# what is wrong with the site and then lands in the inbox of the people who
# built it.
#
# Four shapes, so that neither half of the rule can be satisfied by breaking
# the other: a page whose only address is the builder's must yield nothing; a
# business genuinely on free mail must still yield it; a page carrying both
# must yield the business's; and a business writing from a sibling domain it
# plainly owns must keep it.
OWNERSHIP_CORPUS = [

    # The only address on the page belongs to whoever built it.
    ("credit-footer-mailto", "https://coastlinedrywall.com/", """
     <html><body><h1>Coastline Drywall</h1>
     <p>Taping and boarding across the South Shore since 2004.</p>
     <p>Call 902-555-0148 for a quote.</p>
     <footer><p>&copy; 2026 Coastline Drywall. Site by
     <a href="https://northpixelmedia.com">North Pixel Media</a> &mdash;
     <a href="mailto:studio@northpixelmedia.com">studio@northpixelmedia.com</a>
     </p></footer></body></html>""", ""),

    ("builder-support-desk", "https://oakvillewindows.com/", """
     <html><body><h1>Oakville Windows &amp; Doors</h1>
     <p>Vinyl and wood replacement windows. Free measure, 905-555-0110.</p>
     <footer><p>Powered by <a href="https://webcraftstudios.com">WebCraft
     Studios</a>. Site issues? support@webcraftstudios.com</p></footer>
     </body></html>""", ""),

    ("theme-author", "https://brightlanelandscapes.ca/", """
     <html><body><h1>Brightlane Landscapes</h1>
     <p>Beds, borders and dry stone walling in the Annapolis Valley.</p>
     <footer><small>Theme by Lumen Themes (hello@lumenthemes.com)</small>
     </footer></body></html>""", ""),

    ("designer-byline", "https://rivertonautobody.com/", """
     <html><body><h1>Riverton Auto Body</h1>
     <p>Collision repair, insurance work, courtesy cars.</p>
     <p class="credit">Website designed by
     <a href="https://kestreldesign.co">Kestrel Design Co.</a>
     <a href="mailto:hello@kestreldesign.co">say hello</a></p>
     </body></html>""", ""),

    ("hosting-support", "https://pineboughcafe.ca/", """
     <html><body><h1>Pinebough Cafe</h1>
     <p>Breakfast until two. 14 Mill Street.</p>
     <footer><p>Hosting by Northwind Hosting &mdash; help@northwindhosting.net
     </p></footer></body></html>""", ""),

    ("dev-shop-credit", "https://summitcranehire.co.uk/", """
     <html><body><h1>Summit Crane Hire</h1>
     <p>Spider cranes and operators across the North West.</p>
     <footer><p>Web development by Halo Digital, dev@halodigital.co.uk</p>
     </footer></body></html>""", ""),

    # The freelancer works from a Gmail account, which is exactly the shape the
    # free-mail rule exists to keep. Position is the only thing that tells them
    # apart, and without it this address is the lead.
    ("freelancer-on-gmail", "https://maplegroveplumbing.ca/", """
     <html><body><h1>Maple Grove Plumbing</h1>
     <p>Drains, boilers, emergency call-outs. Ring 613-555-0177.</p>
     <footer><p>Site by Jane Mercer Design &mdash; janemercerdesign@gmail.com
     </p></footer></body></html>""", ""),

    ("credit-behind-cloudflare", "https://quaysidebooks.ie/", """
     <html><body><h1>Quayside Books</h1>
     <p>Second-hand and antiquarian, open Thursday to Sunday.</p>
     <footer><p>Site by Tern Studio
     <a href="/cdn-cgi/l/email-protection" class="__cf_email__"
        data-cfemail="__CF__">[email&#160;protected]</a></p></footer>
     </body></html>""", ""),

    # The business really is on free mail.
    ("free-mail-alone", "https://delrayelectric.ca/", """
     <html><body><h1>Delray Electric</h1>
     <p>Panel upgrades and EV chargers. Email delrayelectric@gmail.com
     or call 519-555-0163.</p></body></html>""", "delrayelectric@gmail.com"),

    ("free-mail-mailto", "https://thecopperkettle.co.uk/", """
     <html><body><h1>The Copper Kettle</h1>
     <p>Bookings: <a href="mailto:thecopperkettle@outlook.com">email us</a>
     </p></body></html>""", "thecopperkettle@outlook.com"),

    ("free-mail-family-tld", "https://hillsidefarmshop.ca/", """
     <html><body><h1>Hillside Farm Shop</h1>
     <p>Beef boxes and preserves. Orders to hillsidefarm@yahoo.ca.</p>
     </body></html>""", "hillsidefarm@yahoo.ca"),

    ("free-mail-beside-a-credit", "https://northshoretiling.com/", """
     <html><body><h1>North Shore Tiling</h1>
     <p>Wet rooms and floors. Email northshoretiling@gmail.com for quotes.</p>
     <footer><p>Powered by <a href="https://webcraftstudios.com">WebCraft
     Studios</a>. Support: support@webcraftstudios.com</p></footer>
     </body></html>""", "northshoretiling@gmail.com"),

    # The business's own address, with a credit underneath it.
    ("own-domain-plus-credit", "https://acmeroofing.ca/", """
     <html><body><h1>Acme Roofing</h1>
     <p>Shingles and flat roofs. info@acmeroofing.ca</p>
     <footer><p>Site by <a href="https://northpixelmedia.com">North Pixel
     Media</a> &mdash; studio@northpixelmedia.com</p></footer>
     </body></html>""", "info@acmeroofing.ca"),

    ("own-mailto-plus-theme", "https://harbourviewdental.ca/contact/", """
     <html><body><h1>Contact</h1>
     <a href="mailto:reception@harbourviewdental.ca">Reception</a>
     <footer><p>Theme by Lumen Themes (hello@lumenthemes.com)</p></footer>
     </body></html>""", "reception@harbourviewdental.ca"),

    ("own-domain-plus-powered-by", "https://fernhillbakery.ca/", """
     <html><body><h1>Fernhill Bakery</h1>
     <p>Trade orders: orders@fernhillbakery.ca</p>
     <footer><p>Powered by WebCraft Studios &mdash;
     support@webcraftstudios.com</p></footer></body></html>""",
     "orders@fernhillbakery.ca"),

    ("jsonld-plus-credit", "https://wrenfieldironworks.co.uk/", """
     <html><head><script type="application/ld+json">
     {"@type":"LocalBusiness","name":"Wrenfield Ironworks",
      "email":"orders@wrenfieldironworks.co.uk"}
     </script></head><body><h1>Wrenfield Ironworks</h1>
     <footer><p>Web design by Halo Digital, dev@halodigital.co.uk</p></footer>
     </body></html>""", "orders@wrenfieldironworks.co.uk"),

    ("own-person-plus-credit", "https://stonebridgelaw.ca/", """
     <html><body><h1>Stonebridge Law</h1>
     <p>Wills and estates. Write to e.novak@stonebridgelaw.ca.</p>
     <footer><p>Site by Kestrel Design Co. &mdash; hello@kestreldesign.co</p>
     </footer></body></html>""", "e.novak@stonebridgelaw.ca"),

    # A sibling domain the business plainly owns.
    ("sibling-longer-name", "https://harbourviewdental.ca/", """
     <html><body><h1>Harbourview Dental</h1>
     <p>New patients welcome. Write to info@harbourviewdentalgroup.com.</p>
     </body></html>""", "info@harbourviewdentalgroup.com"),

    ("sibling-other-tld", "https://acmeroofing.ca/contact/", """
     <html><body><h1>Contact Acme Roofing</h1>
     <a href="mailto:info@acmeroofing.com">info@acmeroofing.com</a>
     </body></html>""", "info@acmeroofing.com"),

    ("sibling-trading-name", "https://wrenfield.works/", """
     <html><body><h1>Wrenfield</h1>
     <p>Bench joinery since 1954. Trade orders: orders@wrenfieldjoinery.com</p>
     </body></html>""", "orders@wrenfieldjoinery.com"),

    ("sibling-hyphen", "https://oakville-windows.ca/", """
     <html><body><h1>Oakville Windows</h1>
     <p>Showroom open Saturdays. sales@oakvillewindows.ca</p>
     </body></html>""", "sales@oakvillewindows.ca"),

    # Controls.
    ("third-party-no-credit", "https://bramptontowing.ca/", """
     <html><body><h1>Brampton Towing</h1>
     <p>24-hour recovery, 905-555-0192.</p>
     <p>Franchise enquiries are handled by
     partners@fleetgroupholdings.com.</p></body></html>""", ""),

    ("own-domain-beats-a-vendor", "https://lakeshoredental.ca/contact/", """
     <html><body><h1>Contact</h1>
     <p>Reception: info@lakeshoredental.ca</p>
     <p>Invoices are handled by billing@dentalbillingpartners.com.</p>
     </body></html>""", "info@lakeshoredental.ca"),

    # An agency credits itself, on its own site, and keeps its own address.
    ("the-agency-s-own-site", "https://northpixelmedia.com/", """
     <html><body><h1>North Pixel Media</h1>
     <p>Websites for trades and clinics.</p>
     <footer><p>Site by North Pixel Media &mdash;
     <a href="mailto:studio@northpixelmedia.com">studio@northpixelmedia.com</a>
     </p></footer></body></html>""", "studio@northpixelmedia.com"),
]


def ownership_pages():
    """The corpus with its Cloudflare payload encoded the way Cloudflare does."""
    return [(name, url, html.replace("__CF__", cf_encode("hello@ternstudio.ie")),
             want) for name, url, html, want in OWNERSHIP_CORPUS]


def test_no_page_hands_back_somebody_elses_address():
    """24 pages, one right answer each, and nine of them are "nothing".

    Precision on this corpus was 63.6% — 14 of the 22 addresses it handed back
    were the business's, and 7 of the other 8 were the agency, theme author or
    host printed in the footer. The eighth was worse: a tiler's own Gmail
    address lost to the builder's support desk sitting on the same page.
    """
    wrong, given = [], 0
    for name, url, html, want in ownership_pages():
        got = E.extract_contacts(html, url)["email"]
        given += bool(got)
        if got != want:
            wrong.append((name, got or "(nothing)", want or "(nothing)"))
    assert wrong == [], (
        "%d of %d pages answer with the wrong address:\n" % (len(wrong), given)
        + "\n".join("  %-26s gave %-34s want %s" % w for w in wrong))
    print("ownership corpus: OK (%d pages, %d addresses, all correct)"
          % (len(OWNERSHIP_CORPUS), given))


def test_a_credit_line_is_not_a_page_stating_its_address():
    """The byline is read as provenance, and only when it is the sole one.

    An address written plainly anywhere on the site keeps its own method, so a
    footer credit that repeats an address the business also states outright
    cannot demote it — which is what happens on an agency's own site, and on
    any business whose builder links its address twice.
    """
    credit = ('<footer><p>Site by <a href="https://northpixelmedia.com">North'
              ' Pixel</a> &mdash; studio@northpixelmedia.com</p></footer>')
    assert E._scan_page(credit, "x") == {"studio@northpixelmedia.com": "credit"}
    stated = '<p>Write to studio@northpixelmedia.com.</p>' + credit
    assert E._scan_page(stated, "x") == {"studio@northpixelmedia.com": "text"}
    # Across a crawl, too: one page's byline says nothing about a page that
    # prints the address outright.
    per_page = {"https://acme.ca/": {"hello@buildco.com": "credit"},
                "https://acme.ca/contact/": {"hello@buildco.com": "mailto"}}
    ranked = E._rank_emails(per_page, "buildco.com")
    assert [r["email"] for r in ranked] == ["hello@buildco.com"], ranked
    assert ranked[0]["method"] == "mailto", ranked

    # The window is one line. A credit above the shop's own address must not
    # reach down into it, or the rule deletes the only address it has.
    footer = ('<footer><p>Proudly powered by WordPress</p>'
              '<p>Email thekiln@gmail.com</p></footer>')
    assert E._scan_page(footer, "x") == {"thekiln@gmail.com": "text"}
    print("a credit line is not a stated address: OK")


def test_only_a_domain_the_business_owns_counts_as_its_own():
    """Same domain, same brand under another suffix, or a brand it extends.

    A prefix and not a shared substring, deliberately: `windows.io` and
    `oakvillewindows.com` share a tail and are two companies, while a business
    that outgrows its name extends it to the right.
    """
    for domain, site in (("acmeroofing.ca", "acmeroofing.ca"),
                         ("mail.acmeroofing.ca", "acmeroofing.ca"),
                         ("acmeroofing.com", "acmeroofing.ca"),
                         ("acme-roofing.ca", "acmeroofing.ca"),
                         ("wrenfieldjoinery.com", "wrenfield.works"),
                         ("harbourviewdental.ca", "harbourviewdentalgroup.com")):
        assert E._owns(domain, site), (domain, site)
    for domain, site in (("northpixelmedia.com", "coastlinedrywall.com"),
                         ("webcraftstudios.com", "oakvillewindows.com"),
                         ("windows.io", "oakvillewindows.com"),
                         ("notacmeroofing.ca", "acmeroofing.ca"),
                         ("gmail.com", "acmeroofing.ca"),
                         ("acme.com", "acmecorp.ca"),
                         ("acmeroofing.ca", "")):
        assert not E._owns(domain, site), (domain, site)
    print("only an owned domain counts as the business's: OK")


def test_the_credit_scan_cannot_invent_an_address():
    """A window is a raw slice of markup, so it cuts tags and addresses in half.

    That has to be harmless, because a fabricated recipient is the one failure
    worse than the one this rule fixes. It is: the window scan can only
    nominate addresses the page already yielded, and the blanked scan is only
    ever subtracted from that nomination, so neither can put a key into the
    result. The pad walks the cut through the address a character at a time.
    """
    for pad in range(280, 341):
        page = ('<p>Site by North Pixel ' + "x" * pad
                + ' write to office@coastlinedrywall.com</p>')
        found = E._scan_page(page, "https://coastlinedrywall.com/")
        assert set(found) == {"office@coastlinedrywall.com"}, (pad, found)
        ranked = E._rank_emails({"https://coastlinedrywall.com/": found},
                                "coastlinedrywall.com")
        assert [r["email"] for r in ranked] == ["office@coastlinedrywall.com"], pad
    print("the credit scan invents nothing: OK (%d cut positions)"
          % len(range(280, 341)))


def test_a_crawl_never_mails_the_agency_that_built_the_site():
    """The whole path, because the byline is on every page of a real site.

    This is the shape that was measured on live sites: a drywall contractor
    with no address of its own, whose footer credit made the agency's studio
    inbox `best_email` — the pitch opens by naming what is wrong with the site
    and arrives at the people who built it.
    """
    credit = ('<footer><p>&copy; 2026 Coastline Drywall. Site by '
              '<a href="https://northpixelmedia.com">North Pixel Media</a> '
              '<a href="mailto:studio@northpixelmedia.com">studio</a></p>'
              '</footer>')
    home = ('<html><body><h1>Coastline Drywall</h1><a href="/contact/">Contact'
            '</a>' + credit + '</body></html>')
    for contact, expected in (
            ('<html><body><h1>Contact</h1><p>Call 902-555-0148.</p>'
             + credit + '</body></html>', ""),
            ('<html><body><h1>Contact</h1>'
             '<a href="mailto:office@coastlinedrywall.com">office</a>'
             + credit + '</body></html>', "office@coastlinedrywall.com")):
        pages = {"https://coastlinedrywall.com": home,
                 "https://coastlinedrywall.com/contact/": contact}
        with _StubFetch(pages):
            site = E.harvest_site("coastlinedrywall.com", max_pages=4,
                                  verify_dns=False)
        assert site["best_email"] == expected, site["emails"]
        assert [r["email"] for r in site["emails"]] == (
            [expected] if expected else []), site["emails"]
    print("a crawl never mails the agency: OK")


def test_a_site_on_a_builder_platform_keeps_its_address():
    """`stitchandsole.wixsite.com` registers as `wixsite.com`, which is not the
    shop. Ownership cannot be read there at all, so the off-domain rule stands
    down rather than throwing away the one address such a business has."""
    page = '<p>Orders: hello@stitchandsole.ca</p>'
    ranked = E._rank_emails({"https://stitchandsole.wixsite.com/home":
                             E._scan_page(page)}, "wixsite.com")
    assert [r["email"] for r in ranked] == ["hello@stitchandsole.ca"], ranked
    # And the same address on an ordinary site that does not own it is refused.
    assert E._rank_emails({"https://someoneelse.ca/": E._scan_page(page)},
                          "someoneelse.ca") == []
    print("a builder-platform site keeps its address: OK")


# ── Junk ──

def test_junk_rejected():
    html = """
      <span>noreply@acmeroofing.ca</span>
      <span>postmaster@acmeroofing.ca</span>
      <span>605a7baede844d278b89dc95ae0a9123@sentry-next.wixpress.com</span>
      <span>wordpress@acmeroofing.ca</span>
      <img src="logo@2x.png"><img src="hero@2x.webp">
      <a href="https://cdn.acme.com/x/main@1.2.3/dist.js">js</a>
      <span>hello@mailinator.com</span>
      <span>info@example.com</span>
    """
    ranked = E._rank_emails({"https://acmeroofing.ca/": E._scan_page(html)},
                            "acmeroofing.ca")
    assert ranked == [], ranked
    assert email_of(html) == ""
    print("junk rejection: OK")


def test_percent_encoded_mailto_yields_one_address_not_two():
    """`mailto:info%2Bweb@acme.ca` is one address written in href syntax.

    The raw href is scanned as text as well as unquoted as a mailto, and
    `_EMAIL_RE` permits `%`, so the page handed back the real `info+web@acme.ca`
    *and* a phantom `info%2bweb@acme.ca` that landed in harvest_site()['emails'].
    """
    plus = '<a href="mailto:info%2Bweb@acme.ca">write</a>'
    assert E._scan_page(plus, "x") == {"info+web@acme.ca": "mailto"}, E._scan_page(plus)
    # The worst case: unquoting gives a space, so the address is unusable and
    # the *only* thing the page produced was the phantom. Nothing is correct.
    space = '<a href="mailto:sales%20team@acme.ca">write</a>'
    assert E._scan_page(space, "x") == {}, E._scan_page(space)
    assert E._clean_email("info%2bweb@acme.ca") == ""
    assert E._clean_email("sales%20team@acme.ca") == ""
    # A literal `%` that is not an escape is not href syntax, and `%40` written
    # out in body text is still the obfuscation the deobfuscator handles.
    assert E._clean_email("50%off@acme.ca") == "50%off@acme.ca"
    assert E._scan_page("<p>info%40acmeroofing.ca</p>", "x") == {
        "info@acmeroofing.ca": "deobfuscated"}
    ranked = E._rank_emails({"https://acme.ca/": E._scan_page(plus)}, "acme.ca")
    assert [r["email"] for r in ranked] == ["info+web@acme.ca"], ranked
    print("percent-encoded mailto: OK")


def test_punycode_tld_is_never_truncated():
    """`info@shop.xn--p1ai` must not come back as `info@shop.xn`.

    A TLD pattern of `[A-Za-z]{2,24}` stops at the first hyphen, and the prefix
    it hands back cleans successfully — `.xn` is not a TLD, so that is a minted
    address, not a miss. Both the plain scan and the deobfuscator produced it.
    """
    for raw in ("info@shop.xn--p1ai", "mail@xn--80akhbyknj4f.xn--p1ai"):
        assert E._EMAIL_RE.findall(raw) == [raw], E._EMAIL_RE.findall(raw)
        assert E._clean_email(raw) == raw
        found = E._scan_page("<p>%s</p>" % raw, "x")
        assert found == {raw: "text"}, found
        assert raw.rpartition(".")[0] not in found, found
    assert E._scan_page('<a href="mailto:info@shop.xn--p1ai">x</a>', "x") == {
        "info@shop.xn--p1ai": "mailto"}
    # A suffix the pattern cannot express is refused whole rather than cut down.
    assert E._scan_page("<p>info@shop.xn-p1ai</p>", "x") == {}
    assert E._scan_page("<p>info at shop dot xn-p1ai</p>", "x") == {}
    assert E._scan_page("<p>info@acme.com2</p>", "x") == {}
    print("punycode not truncated: OK")


def test_mov_gtld_is_a_mailbox_not_an_asset():
    """`.mov` is a live Google gTLD, sold to exactly this tool's market.

    It is also a video extension, so the extension alone cannot decide it. The
    density descriptor in front of it can: `clip@2x.mov` is a file, and the
    `@2x` hole stays shut for every other extension too.
    """
    for address in ("info@shorts.mov", "hello@northsidefilms.mov",
                    "bookings@studio.mov"):
        assert E._clean_email(address) == address, address
    for asset in ("clip@2x.mov", "reel@3x.mov", "TRAILER@2X.MOV"):
        assert E._clean_email(asset) == "", asset
    # The neighbours the fix must not disturb, in both directions.
    assert E._clean_email("hero@2x.avif") == ""
    assert E._clean_email("promo@2x.webm") == ""
    assert E._clean_email("hello@2x.ca") == "hello@2x.ca"
    assert E._clean_email("info@1x.com") == "info@1x.com"
    page = ('<html><body><h1>Contact</h1><p>info@shorts.mov</p>'
            '<video src="promo@2x.mov"></video></body></html>')
    assert E._scan_page(page, "https://shorts.mov/") == {"info@shorts.mov": "text"}
    assert email_of(page, "https://shorts.mov/contact/") == "info@shorts.mov"
    print("mov gTLD: OK")


def test_addresses_split_across_tags_are_missed_not_invented():
    """Declined on purpose: `_strip_tags` cannot tell the trick from the trap.

    `in<span>fo</span>@acme.com` and `info<span style="display:none">REMOVE
    </span>@acme.com` are the same shape in the markup — an anti-scrape split
    and an anti-scrape poison — and only computed styles separate them. Joining
    the fragments would read the second as `inforemove@acme.com`, an address
    that does not exist and would be mailed. `_strip_tags` also substitutes a
    space, so wiring it in does not even recover the first case.

    So the split forms stay a miss. This test exists to make that deliberate:
    wire the stripper in naively and the poison line fails loudly.
    """
    for missed in ('<p>in<span>fo</span>@acme.com</p>',
                   '<p>info@acme<!-- x -->.com</p>',
                   "<p>info​@acme.com</p>"):
        assert E._scan_page(missed, "x") == {}, missed
    poison = '<p>info<span style="display:none">REMOVE</span>@acme.com</p>'
    assert E._scan_page(poison, "x") == {}, E._scan_page(poison, "x")
    assert "inforemove@acme.com" not in E._scan_page(poison, "x")
    # The same page with the address written once, plainly, still works.
    assert E._scan_page(poison + '<p>info@acme.com</p>', "x") == {
        "info@acme.com": "text"}
    print("split addresses missed, never invented: OK")


def test_a_block_boundary_is_not_an_intra_word_split():
    """The guard that refuses a split token must not refuse an ordinary page.

    A token broken by markup that opens and closes inside it — `in<b></b>fo@` —
    is one word to a reader, so the address is refused rather than truncated to
    `fo@`. Markup that only closes what it did not open is the opposite: two
    blocks, and `Contact</h2><p>info@acme.ca` is how a minified contact page is
    shaped. Reading balance rather than a roster of element names is what keeps
    these apart, so both directions are asserted together — a rule that refused
    these would cost more addresses than every fabrication it prevented.
    """
    for html in ('<h1>Contact</h1><p>info@acme.ca</p>',
                 '<h2>Contact</h2><p>info@acme.ca</p>',
                 '<p>Contact</p><p>info@acme.ca</p>',
                 '<span>Email</span><span>info@acme.ca</span>',
                 '<td>Email</td><td>info@acme.ca</td>',
                 '<div>Email<br/>info@acme.ca</div>',
                 '<b>Email</b>: info@acme.ca'):
        assert E._scan_page(html, "x") == {"info@acme.ca": "text"}, html
    # Balanced markup interpolated into the token is the split, and it is
    # refused whole — never handed back as the tail it could still express.
    for html in ('<p>in<b></b>fo@acme.ca</p>',
                 '<p>in<!--x-->fo@acme.ca</p>',
                 '<p>in<span style="display:none">XX</span>fo@acme.ca</p>',
                 '<p>i<span class="hidden">nospam</span>nfo@acme.ca</p>'):
        assert E._scan_page(html, "x") == {}, html
    print("block boundaries survive the join guard: OK")


def test_a_spelled_address_survives_the_markup_wrapped_around_it():
    """`bookings <b>[at]</b> acme <b>[dot]</b> com` renders as one line.

    Bolding the separator is one of the commonest ways a small site hides its
    address, and the tags sit *between* the tokens rather than inside a word, so
    `_mark_joins` correctly leaves them alone and the spelled pattern never
    closed. The address was simply missed — on a page whose only contact route
    it was.

    The structural rule that governs the spelled form is untouched and still
    does the work: a spelled `at` has to be answered by a spelled `dot`, so
    `Follow us [at] acme.ca` stays refused with the markup in place exactly as
    it is without it.
    """
    for html, expected in (
            ('<p>bookings <b>[at]</b> sierravistadetail <b>[dot]</b> com</p>',
             "bookings@sierravistadetail.com"),
            ('<p>info <span>(at)</span> acme <span>(dot)</span> ca</p>',
             "info@acme.ca"),
            ('<p>info <em>[at]</em> acme <em>[dot]</em> co <em>[dot]</em> uk</p>',
             "info@acme.co.uk")):
        assert E._scan_page(html, "x") == {expected: "deobfuscated"}, html
    for refused in ('<p>Follow us <b>[at]</b> acme.ca</p>',
                    '<p>Call Dot <b>(at)</b> acme.pizza</p>'):
        assert E._scan_page(refused, "x") == {}, refused
    print("a spelled address survives the markup wrapped around it: OK")


def test_stripping_tags_for_the_spelled_scan_still_invents_nothing():
    """The stripper is wired in for one pass, and the order is the whole guard.

    `_mark_joins` runs first, so every split a reader cannot see is already a
    `_JOIN` that both patterns refuse on the leading side; only then are the
    remaining tags replaced, and by a space rather than by nothing. Both halves
    are load-bearing and both are measured: strip before marking and
    `in<b></b>fo@acme.ca` is handed back as `fo@acme.ca`, the truncation that
    cleans and looks real and goes nowhere. Substitute the empty string instead
    of a space and `Contact</h2><p>info@acme.ca` becomes `contactinfo@acme.ca`,
    and the poison span becomes `inforemove@acme.com`.
    """
    poison = '<p>info<span style="display:none">REMOVE</span>@acme.com</p>'
    assert E._scan_page(poison, "x") == {}, E._scan_page(poison, "x")
    for html in ('<p>in<b></b>fo@acme.ca</p>',
                 '<p>i<span class="hidden">nospam</span>nfo@acme.ca</p>',
                 '<td>jane</td><td>@</td><td>acme.ca</td>',
                 '<p>Contact</p><p>@acme.ca</p>'):
        assert E._scan_page(html, "x") == {}, html
    # A boundary between two blocks is still just a boundary, and the address
    # after it is still read whole.
    for html in ('<h2>Contact</h2><p>info@acme.ca</p>',
                 '<td>Email</td><td>info@acme.ca</td>'):
        assert E._scan_page(html, "x") == {"info@acme.ca": "text"}, html
    print("stripping tags for the spelled scan invents nothing: OK")


def test_the_front_desk_is_the_front_desk_in_any_language():
    """`hei@` and `contacto@` are `hello@` and `contact@`, and scored below them.

    The list already carried `hallo` and `kontakt`, so it had always reached
    past English — just not far enough, which left the general inbox losing to
    whichever department happened to be printed on the contact page.
    """
    pages = {"https://nordfjell.no/": '<a href="mailto:hei@nordfjell.no">hei</a>',
             "https://nordfjell.no/pages/contact":
                 '<a href="mailto:wholesale@nordfjell.no">wholesale</a>'}
    ranked = E._rank_emails({u: E._scan_page(h, u) for u, h in pages.items()},
                            "nordfjell.no")
    assert ranked[0]["email"] == "hei@nordfjell.no", ranked

    # And a mailbox that reads CVs is still the wrong human, whatever it is
    # called: `bewerbung@` has to lose to the general inbox the way `careers@`
    # already does.
    for careers, general in (("bewerbung@x.de", "info@x.de"),
                             ("recrutement@x.fr", "contact@x.fr")):
        domain = careers.partition("@")[2]
        ranked = E._rank_emails({"https://x.test/": {careers: "mailto", general: "mailto"}},
                                domain)
        assert ranked[0]["email"] == general, ranked
    print("the front desk is the front desk in any language: OK")


def test_a_mailto_local_part_is_never_rewritten():
    """`mailto:x27andy@acme.ca` is the page stating its address outright.

    Trimming a JSON escape off the front of it invents `andy@acme.ca` — a
    mailbox nothing on the page named. The repair was there for raw text where
    a `\\u003e` sits against the local part, and the raw href is scanned as
    text too, so confining it to the mailto pass was not enough: the phantom
    is refused outright, on both routes.
    """
    for local in ("x27andy", "u0026co", "x22quinn", "u003csam"):
        address = local + "@acme.ca"
        html = '<a href="mailto:%s">write</a>' % address
        assert E._scan_page(html, "x") == {address: "mailto"}, address
        assert E._clean_email(address) == address, address
        assert E._clean_email(address, from_text=True) == "", address
    # The address the repair existed for is still recovered whole, and with the
    # same provenance: _scan_page unescapes the page and reads it there, where
    # it is written plainly and needs no repair at all.
    esc = chr(92) + "u003e"                # a real JSON escape, backslash and all
    assert E._scan_page("<p>%sinfo@acme.ca</p>" % esc, "x") == {
        "info@acme.ca": "text"}
    assert E._scan_page('<script>var h="%sinfo@acme.ca";</script>' % esc, "x") == {
        "info@acme.ca": "text"}
    # With the backslash genuinely gone there is no way to tell a mangled
    # escape from a local part, so it is a miss rather than a guess.
    assert E._scan_page("<p>u003einfo@acme.ca</p>", "x") == {}
    print("mailto local parts are never rewritten: OK")


def test_clean_email_edges():
    assert E._clean_email("INFO@AcmeRoofing.CA ") == "info@acmeroofing.ca"
    assert E._clean_email("info@acmeroofing.ca.") == "info@acmeroofing.ca"
    assert E._clean_email("u003einfo@acmeroofing.ca", from_text=True) == ""
    assert E._clean_email("logo@2x.png") == ""
    assert E._clean_email("12345@acmeroofing.ca") == ""
    assert E._clean_email("info@server.local") == ""
    assert E._clean_email("not an email") == ""
    print("clean-email edges: OK")


def test_srcset_assets_are_not_addresses():
    """`hero@2x.avif` is a density descriptor. On a contact page with no real
    address it otherwise becomes best_email."""
    for asset in ("hero@2x.avif", "shot@2x.heic", "photo@3x.heif", "bg@2x.bmp",
                  "scan@2x.tiff", "scan@2x.tif", "clip@2x.webm", "clip@2x.mov",
                  "clip@2x.avi", "clip@2x.m4v", "tune@2x.m4a", "tune@2x.ogg"):
        assert E._clean_email(asset) == "", asset
    # ...without taking real domains that merely start with a digit.
    assert E._clean_email("info@24x7support.ca") == "info@24x7support.ca"
    assert E._clean_email("info@2xl.ca") == "info@2xl.ca"
    # The `@2x` shape is not the signal — `1x.com` and `3x.com` are registered
    # domains, and a rule that reads the shape alone deletes their mail.
    assert E._clean_email("info@1x.com") == "info@1x.com"
    assert E._clean_email("info@3x.com") == "info@3x.com"
    assert E._clean_email("hello@2x.ca") == "hello@2x.ca"

    page = ('<html><body><h1>Contact</h1>'
            '<img src="hero@2x.avif" srcset="hero@2x.avif 2x, hero@3x.heic 3x">'
            '<video src="promo@2x.webm"></video></body></html>')
    assert email_of(page, "https://acmeroofing.ca/contact/") == ""
    assert E._scan_page(page, "https://acmeroofing.ca/contact/") == {}
    print("srcset assets rejected: OK")


# ── Decoding ──

def test_charset_handling():
    body = "<html><body>café info@acmeroofing.ca</body></html>"
    assert "café" in E._decode_body(body.encode("cp1252"),
                                        "text/html; charset=windows-1252")
    # A lying header must not win over bytes that only make sense as utf-8.
    assert "café" in E._decode_body(body.encode("utf-8"),
                                         "text/html; charset=iso-8859-1")
    # No header at all: the <meta> in the first 2 KB is the only signal.
    jp = '<meta charset="shift_jis"><body>屋根 info@acmeroofing.ca</body>'
    decoded = E._decode_body(jp.encode("shift_jis"), "")
    assert "屋根" in decoded and "info@acmeroofing.ca" in decoded
    assert E._decode_body(b"\xff\xfe\x00bad", "text/html") is not None
    print("charset handling: OK")


# ── Crawl ──

HOME = """
<html><body>
  <nav>
    <a href="/">Home</a>
    <a href="/services/roof-repair">Roof repair</a>
    <a href="/blog/2019/how-to-pick-a-roofer">Blog post</a>
    <a href="/contact-us/">Contact us</a>
    <a href="/about/">About</a>
    <a href="https://facebook.com/AcmeRoofingTO">Facebook</a>
    <a href="/brochure.pdf">Brochure</a>
    <a href="mailto:noreply@acmeroofing.ca">no reply</a>
  </nav>
  <p>Call <a href="tel:+1 (416) 555-0199">416-555-0199</a></p>
</body></html>
"""
CONTACT = """
<html><body>
  <h1>Contact</h1>
  <a href="mailto:mike.reid@acmeroofing.ca">Mike Reid</a>
  <p>Accounts: billing@acmeroofing.ca</p>
</body></html>
"""
ABOUT = ('<html><body><p>Founded 1998. info (at) acmeroofing (dot) ca</p>'
         '</body></html>')


def test_harvest_site_shape():
    pages = {
        "https://acmeroofing.ca": HOME,
        "https://acmeroofing.ca/contact-us/": CONTACT,
        "https://acmeroofing.ca/about/": ABOUT,
    }
    with _StubFetch(pages) as stub:
        site = E.harvest_site("acmeroofing.ca", max_pages=4, verify_dns=False)

    assert site["reachable"] is True and site["error"] == "", site
    assert site["final_url"] == "https://acmeroofing.ca"
    assert site["best_email"] == "mike.reid@acmeroofing.ca", site["emails"]
    assert set(site["html"]) == set(site["pages"]) == set(pages), site["pages"]
    assert site["socials"]["facebook"] == "https://facebook.com/AcmeRoofingTO"
    assert site["phones"], site["phones"]
    assert "noreply@acmeroofing.ca" not in [e["email"] for e in site["emails"]]
    assert {"email", "score", "kind", "source", "method",
            "deliverable"} == set(site["emails"][0])
    assert site["emails"][0]["source"] == "https://acmeroofing.ca/contact-us/"
    # The blog post, the PDF and the off-site link are never worth a fetch.
    assert not any("blog" in u or ".pdf" in u or "facebook" in u for u in stub.calls)
    print("harvest_site shape: OK ->", site["best_email"], site["pages"])


def test_rank_links_is_same_host_not_same_domain():
    """A subdomain is another site. With a two-page budget it also starves the
    real one: both score 100 and the shorter off-host URL wins the tie."""
    html = ("<a href='https://contact.example.com/'>Contact</a>"
            "<a href='/contact-us'>Contact Us</a>")
    for limit in (1, 2, 4):
        assert E._rank_links(html, "https://www.example.com/", limit) == [
            "https://www.example.com/contact-us"], limit
    for href in ("https://shop.example.com/contact", "https://blog.example.com/about",
                 "https://careers.example.com/team"):
        assert E._rank_links("<a href='%s'>Contact</a>" % href,
                             "https://www.example.com/", 4) == [], href
    # http->https and www variants are the same host, per spec 3.9.
    assert E._rank_links("<a href='http://example.com/contact/'>Contact</a>",
                         "https://www.example.com/", 1) == [
        "http://example.com/contact/"]
    assert E._rank_links("<a href='https://www.example.com/contact/'>Contact</a>",
                         "http://example.com/", 1) == [
        "https://www.example.com/contact/"]
    print("rank_links same-host: OK")


def test_harvest_spends_its_page_budget_on_the_real_host():
    # The off-host URL is the shorter of the two, so it also wins the len()
    # tie-break — the budget goes to a subdomain that has no address on it.
    home = ("<html><body>"
            "<a href='https://contact.acmeroofing.ca/'>Contact</a>"
            "<a href='/contact-us/'>Contact Us</a></body></html>")
    pages = {
        "https://acmeroofing.ca": home,
        "https://acmeroofing.ca/contact-us/": CONTACT,
    }
    with _StubFetch(pages) as stub:
        site = E.harvest_site("acmeroofing.ca", max_pages=2, verify_dns=False)
    assert not any("shop." in url for url in stub.calls), stub.calls
    assert site["pages"] == ["https://acmeroofing.ca",
                             "https://acmeroofing.ca/contact-us/"], site["pages"]
    assert site["best_email"] == "mike.reid@acmeroofing.ca", site["emails"]
    print("harvest stays on host: OK")


def test_harvest_site_caps_pages():
    pages = {
        "https://acmeroofing.ca": HOME,
        "https://acmeroofing.ca/contact-us/": CONTACT,
        "https://acmeroofing.ca/about/": ABOUT,
    }
    with _StubFetch(pages) as stub:
        site = E.harvest_site("https://acmeroofing.ca", max_pages=2,
                              verify_dns=False)
    assert len(site["pages"]) == 2, site["pages"]
    assert len(stub.calls) == 2, stub.calls
    assert "https://acmeroofing.ca/contact-us/" in site["pages"]
    print("harvest_site page cap: OK")


def test_harvest_site_unreachable():
    with _StubFetch({}):
        site = E.harvest_site("nosuchsite.example", verify_dns=False)
    assert site["reachable"] is False
    assert site["best_email"] == "" and site["emails"] == []
    assert site["error"], site
    assert site["html"] == {} and site["pages"] == []
    assert E.harvest_site("")["error"] == "no url"
    print("harvest_site unreachable: OK")


# ── Legacy shape ──

def test_extract_contacts_shape():
    out = E.extract_contacts(HOME, "https://acmeroofing.ca")
    assert tuple(out) == E.ENRICH_KEYS, tuple(out)
    assert all(isinstance(v, str) for v in out.values())
    assert E.extract_contacts("", "https://acmeroofing.ca") == {
        k: "" for k in E.ENRICH_KEYS}
    print("extract_contacts shape: OK")


def test_enrich_website_shape():
    pages = {
        "https://acmeroofing.ca": HOME,
        "https://acmeroofing.ca/contact-us/": CONTACT,
    }
    with _StubFetch(pages):
        out = E.enrich_website("acmeroofing.ca")
        assert tuple(out) == E.ENRICH_KEYS, tuple(out)
        assert out["email"] == "mike.reid@acmeroofing.ca", out
        assert out["facebook"] == "https://facebook.com/AcmeRoofingTO"

        subset = E.enrich_website("acmeroofing.ca", fields=("email",))
        assert subset == {"email": "mike.reid@acmeroofing.ca"}, subset

    assert E.enrich_website("") == {k: "" for k in E.ENRICH_KEYS}
    print("enrich_website shape: OK")


def test_email_regex_stays_linear():
    """Regression: one unbroken 200 KB run took four minutes to scan.

    Minified JS and base64 blobs are full of long runs of local-part
    characters, and an unbounded `[...]+@` backtracks over all of them.
    """
    evil = "a" * 200_000 + "@acmeroofing.ca"
    start = time.time()
    found = E._scan_page(evil, "")
    assert time.time() - start < 5.0, "email scan went quadratic again"
    assert found == {}, found        # and no 64-char fragment invented mid-run
    print("regex stays linear: OK")


def test_dns_cache_is_reused():
    """One city's scrape hits the same domains repeatedly; resolve them once."""
    calls = []
    real, domain = E._resolve, "cache-probe.acmeroofing.ca"
    E._resolve = lambda d: calls.append(d) or True
    try:
        assert E._deliverable(domain) is True
        assert E._deliverable(domain) is True
        assert E._deliverable_many([domain, domain]) == {domain: True}
    finally:
        E._resolve = real
        E._DNS_CACHE.pop(domain, None)
    assert calls == [domain], calls
    print("dns cache: OK")


def test_never_raises():
    junk = ["<html", "<a href=mailto:>", "\x00\xff" * 50, "<script>{{{", "&#x;"]
    for html in junk:
        assert isinstance(E.extract_contacts(html, "notaurl"), dict)
        assert isinstance(E._scan_page(html, ""), dict)
    with _StubFetch({"https://x.ca": "<html>" + "a" * 5000}):
        assert E.harvest_site("x.ca", verify_dns=False)["reachable"] is True
    print("never raises: OK")


if __name__ == "__main__":
    test_cfemail_decode()
    test_cfemail_in_page()
    test_obfuscation_forms()
    test_a_bracketed_at_needs_a_bracketed_dot()
    test_bracketed_markers_carry_any_case_and_any_suffix()
    test_angle_brackets_are_not_obfuscation_markers()
    test_fromcharcode()
    test_prose_is_not_an_email()
    test_the_copy_corpus_provably_carries_the_fabricating_shape()
    test_bare_spaced_at_and_dot_yield_nothing()
    test_a_site_that_never_states_an_address_returns_none()
    test_the_deleted_arm_is_gone_not_merely_unreached()
    test_a_page_of_copy_with_one_bracketed_address_finds_only_that()
    test_a_domain_label_is_a_name_not_a_word()
    test_spelled_dots_in_the_local_part()
    test_no_page_is_ever_read_backwards()
    test_a_backwards_literal_is_refused_outright()
    test_phantom_mirror_does_not_disable_free_mail_fallback()
    test_entity_encoded_address()
    test_jsonld_email()
    test_jsonld_broken_json_still_yields_email()
    test_microdata_email()
    test_mailto_beats_body_text()
    test_scoring_order()
    test_same_domain_preference()
    test_free_mail_only_when_alone()
    test_contact_page_bonus()
    test_the_contact_bonus_cannot_lift_a_candidate_over_the_floor()
    test_dns_penalty_and_unknown()
    test_no_page_hands_back_somebody_elses_address()
    test_a_credit_line_is_not_a_page_stating_its_address()
    test_only_a_domain_the_business_owns_counts_as_its_own()
    test_the_credit_scan_cannot_invent_an_address()
    test_a_crawl_never_mails_the_agency_that_built_the_site()
    test_a_site_on_a_builder_platform_keeps_its_address()
    test_junk_rejected()
    test_percent_encoded_mailto_yields_one_address_not_two()
    test_a_mailto_local_part_is_never_rewritten()
    test_punycode_tld_is_never_truncated()
    test_mov_gtld_is_a_mailbox_not_an_asset()
    test_addresses_split_across_tags_are_missed_not_invented()
    test_a_block_boundary_is_not_an_intra_word_split()
    test_a_spelled_address_survives_the_markup_wrapped_around_it()
    test_stripping_tags_for_the_spelled_scan_still_invents_nothing()
    test_the_front_desk_is_the_front_desk_in_any_language()
    test_clean_email_edges()
    test_srcset_assets_are_not_addresses()
    test_charset_handling()
    test_harvest_site_shape()
    test_rank_links_is_same_host_not_same_domain()
    test_harvest_spends_its_page_budget_on_the_real_host()
    test_harvest_site_caps_pages()
    test_harvest_site_unreachable()
    test_extract_contacts_shape()
    test_enrich_website_shape()
    test_email_regex_stays_linear()
    test_dns_cache_is_reused()
    test_never_raises()
    print("\nALL ENRICH EMAIL TESTS PASSED")
