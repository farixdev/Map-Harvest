"""Offline tests for core.enrich and core.filters. No network required."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.enrich import extract_contacts  # noqa: E402
from core import filters as F  # noqa: E402


SAMPLE_HTML = """
<html><head></head><body>
  <a href="mailto:info@acmeroofing.ca">Email us</a>
  <p>Call or email sales@acmeroofing.ca for a quote.</p>
  <img src="logo@2x.png">
  <a href="https://www.facebook.com/AcmeRoofingTO">FB</a>
  <a href="https://facebook.com/sharer/sharer.php?u=x">share</a>
  <a href="https://instagram.com/acme.roofing/">IG</a>
  <a href="https://www.linkedin.com/company/acme-roofing/">LI</a>
  <a href="https://twitter.com/intent/tweet?text=x">tweet</a>
  <a href="https://x.com/acmeroofing">X</a>
  <span>noreply@sentry.wixpress.com</span>
  <span>605a7baede844d278b89dc95ae0a9123@sentry-next.wixpress.com</span>
  <span>hero@2x.png</span>
</body></html>
"""


def test_enrich_extract():
    c = extract_contacts(SAMPLE_HTML, "https://acmeroofing.ca")
    assert c["email"] == "info@acmeroofing.ca", c["email"]           # own-domain preferred
    assert c["facebook"] == "https://www.facebook.com/AcmeRoofingTO", c["facebook"]
    assert c["instagram"] == "https://instagram.com/acme.roofing/", c["instagram"]
    assert c["linkedin"] == "https://www.linkedin.com/company/acme-roofing/", c["linkedin"]
    assert c["twitter"] == "https://x.com/acmeroofing", c["twitter"]  # share/intent skipped
    # blocklisted junk (incl. hex-local sentry-next.wixpress + asset) not chosen
    assert "wixpress" not in c["email"] and "sentry" not in c["email"]
    assert not c["email"].startswith("605a"), "hex tracking email leaked"
    assert ".png" not in c["email"]
    print("enrich extract: OK ->", {k: v for k, v in c.items() if v})


def test_enrich_empty():
    c = extract_contacts("", "https://x.com")
    assert all(v == "" for v in c.values())
    print("enrich empty: OK")


def test_social_with_query_string():
    # Non-vanity profile URLs carry a query string — must NOT be dropped as junk.
    html = (
        '<a href="https://www.facebook.com/profile.php?id=100063123456789">FB</a>'
        '<a href="https://www.youtube.com/channel/UCabc?sub_confirmation=1">YT</a>'
    )
    c = extract_contacts(html, "https://biz.com")
    assert c["facebook"] == "https://www.facebook.com/profile.php?id=100063123456789", c["facebook"]
    assert c["youtube"].startswith("https://www.youtube.com/channel/UCabc"), c["youtube"]
    print("social query-string: OK")


def test_www_prefix_and_domain_pref():
    # www. must be a prefix-strip, and a lookalike domain must not be preferred.
    html = (
        'contact <a href="mailto:partner@notwisdom.com">x</a> or '
        '<a href="mailto:hello@wisdom.com">y</a>'
    )
    c = extract_contacts(html, "https://www.wisdom.com")
    assert c["email"] == "hello@wisdom.com", c["email"]
    print("www prefix + domain preference: OK")


def rec(**kw):
    base = {"name": "", "category": "", "rating": "", "review_count": "",
            "phone": "", "website": "", "email": ""}
    base.update(kw)
    return base


def test_filters():
    # min_rating
    spec = {"min_rating": 4.5}
    assert F.cheap_pass(rec(rating="4.6"), spec)
    assert not F.cheap_pass(rec(rating="4.1"), spec)
    assert not F.cheap_pass(rec(rating=""), spec)  # no rating treated as 0

    # website presence
    assert F.cheap_pass(rec(website="http://x.com"), {"require_website": True})
    assert not F.cheap_pass(rec(website=""), {"require_website": True})
    assert F.cheap_pass(rec(website=""), {"require_no_website": True})
    assert not F.cheap_pass(rec(website="http://x.com"), {"require_no_website": True})

    # name include/exclude
    assert F.cheap_pass(rec(name="Joe's Roofing"), {"name_include": ["roof"]})
    assert not F.cheap_pass(rec(name="Joe's Plumbing"), {"name_include": ["roof"]})
    assert not F.cheap_pass(rec(name="Cheap Roofing"), {"name_exclude": ["cheap"]})

    # category include
    assert F.cheap_pass(rec(category="Roofing contractor"), {"category_include": ["roofing"]})
    assert not F.cheap_pass(rec(category="Cafe"), {"category_include": ["roofing"]})

    # full: phone / email / reviews
    assert F.full_pass(rec(phone="123", rating="5"), {"require_phone": True})
    assert not F.full_pass(rec(phone="", rating="5"), {"require_phone": True})
    assert F.full_pass(rec(email="a@b.com"), {"require_email": True})
    assert not F.full_pass(rec(email=""), {"require_email": True})
    assert F.full_pass(rec(review_count="120"), {"min_reviews": 100})
    assert not F.full_pass(rec(review_count="80"), {"min_reviews": 100})
    assert not F.full_pass(rec(review_count="500"), {"max_reviews": 100})

    # flags
    assert F.needs_reviews({"min_reviews": 10})
    assert F.needs_phone({"require_phone": True})
    assert F.needs_email({"require_email": True})
    assert not F.is_active({})
    assert F.is_active({"min_rating": 4})
    print("filters: OK")


if __name__ == "__main__":
    test_enrich_extract()
    test_enrich_empty()
    test_social_with_query_string()
    test_www_prefix_and_domain_pref()
    test_filters()
    print("\nALL ENRICH/FILTER TESTS PASSED")
