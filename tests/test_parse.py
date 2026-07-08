"""Offline verification of core.parse against a real captured Google Maps feed.

Run:  venv/Scripts/python.exe -m tests.test_parse
(from the project root). No browser or network required.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parse import parse_feed_html, parse_place_url, place_key  # noqa: E402

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "debug",
    "initial_Roofing_company.html",
)


def test_url_parsing():
    href = (
        "https://www.google.com/maps/place/Alpine+Roofing/data=!4m7!3m6"
        "!1s0x89d4cc9b150c8a9f:0xb0887b40788685bd!8m2!3d43.656728!4d-79.338035"
        "!16s%2Fg%2F1tk68nss!19sChIJn4oMFZvM1IkRvYWGeEB7iLA?authuser=0&hl=en"
    )
    info = parse_place_url(href)
    assert info["latitude"] == "43.656728", info
    assert info["longitude"] == "-79.338035", info
    assert info["place_id"] == "ChIJn4oMFZvM1IkRvYWGeEB7iLA", info
    assert info["cid"] == "0x89d4cc9b150c8a9f:0xb0887b40788685bd", info
    assert place_key(href) == "ChIJn4oMFZvM1IkRvYWGeEB7iLA"
    print("URL parsing: OK")


def test_feed_parsing():
    if not os.path.isfile(FIXTURE):
        print(f"SKIP feed test — fixture missing: {FIXTURE}")
        return

    with open(FIXTURE, encoding="utf-8") as f:
        cards = parse_feed_html(f.read())

    assert len(cards) == 8, f"expected 8 cards, got {len(cards)}"

    keys = [c["_key"] for c in cards]
    assert len(set(keys)) == 8, "place keys not unique"

    organic = [c for c in cards if not c["_sponsored"]]
    sponsored = [c for c in cards if c["_sponsored"]]
    assert len(sponsored) == 2, f"expected 2 sponsored, got {len(sponsored)}"
    assert len(organic) == 6, f"expected 6 organic, got {len(organic)}"

    for c in cards:
        assert c["name"], f"missing name: {c}"
        assert c["rating"], f"missing rating: {c['name']}"
        assert c["phone"], f"missing phone: {c['name']}"
        assert c["category"], f"missing category: {c['name']}"
        assert c["address"], f"missing address: {c['name']}"
        assert c["latitude"] and c["longitude"], f"missing coords: {c['name']}"
        assert c["place_id"], f"missing place_id: {c['name']}"

    # Organic listings must expose a real website (not an ad redirect).
    for c in organic:
        assert c["website"].startswith("http"), f"bad website: {c['name']} -> {c['website']}"

    print(f"Feed parsing: OK — {len(cards)} cards")
    print(f"{'NAME':22} {'RATING':6} {'CATEGORY':20} {'PHONE':17} WEBSITE")
    for c in cards:
        tag = "[AD] " if c["_sponsored"] else ""
        print(
            f"{tag}{c['name'][:20]:20} {c['rating']:6} {c['category'][:18]:20} "
            f"{c['phone']:17} {c['website'][:34]}"
        )


if __name__ == "__main__":
    test_url_parsing()
    test_feed_parsing()
    print("\nALL TESTS PASSED")
