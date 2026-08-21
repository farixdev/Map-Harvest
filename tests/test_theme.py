"""The palettes, measured — and the sheet, diffed against the one it replaces.

Every rule in `docs/DESIGN_SYSTEM.md` section 1 is a number, so every assertion
here is a number computed from the tokens rather than a value copied out of
them. `_contrast` and `_delta_e` are written out in full below for exactly that
reason: a test that imports the app's own colour maths proves the maths agrees
with itself, and the audit it exists to prevent a repeat of — 18 pairs of
colours under the just-noticeable difference, a focus ring at 17.01:1 beside a
selected tab at 1.50:1 — was produced by people who were sure their colours were
fine.

The last two tests are pixels, not tokens, because the focus-versus-selection
rule is about what a user sees and a QSS rule can always be beaten by a more
specific one.

`tests/conftest.py` has already pointed `core.settings` and `core.templates` at
a temp profile by the time this imports, so nothing here can read or write a
real ~/.mapharvest.
"""
import itertools
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QPushButton, QWidget,
)

from core import settings as ST  # noqa: E402
from ui import theme as TH  # noqa: E402

AA, LARGE, COMPONENT, LADDER, JND = 4.5, 3.0, 3.0, 1.4, 2.0

# Every ground the four-tier text ramp can be painted on. `surfaceActive` is the
# sixth ground and is deliberately not in this list: the contract asks for the
# whole ramp on it, and the arithmetic below — see
# `test_the_selected_ground_cannot_carry_the_text_ramp` — puts the dimmest tier
# brighter than white. What the selected ground carries is one ink,
# `text.onAccent`, and the two tests after it hold the sheet to that.
GROUNDS = ("canvas", "surface", "surfaceHover", "raised", "inset")
SELECTED = "surfaceActive"
FAMILIES = ("accent", "success", "warning", "danger", "info")
FILL_STATES = ("default", "hover", "active")

_APP = None


def _app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


# ── Colour maths, written out ────────────────────────────────────────────────


def _channels(colour: str) -> tuple:
    """The three sRGB channels of a #RRGGBB, 0..1."""
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", colour), colour
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _linear(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    """Relative luminance, by the WCAG 2.1 definition."""
    r, g, b = (_linear(c) for c in _channels(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(one: str, other: str) -> float:
    first, second = _luminance(one) + 0.05, _luminance(other) + 0.05
    return max(first, second) / min(first, second)


def _lab(colour: str) -> tuple:
    """CIE L*a*b* under D65, the space delta-E 76 is defined in."""
    r, g, b = (_linear(c) for c in _channels(colour))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(one: str, other: str) -> float:
    """CIE76. Below 2.0 two colours are one colour with two names."""
    return sum((a - b) ** 2 for a, b in zip(_lab(one), _lab(other))) ** 0.5


def _solid(theme) -> dict:
    """Every colour token that is a flat hex — everything but the scrim."""
    return {name: value for name, value in theme.color.items()
            if value.startswith("#")}


def _themes() -> list:
    return [TH.theme("dark"), TH.theme("light")]


def test_the_helpers_agree_with_the_definitions_they_implement():
    """The ratios below are worth nothing if the maths under them is wrong."""
    assert round(_contrast("#FFFFFF", "#000000"), 2) == 21.0
    assert round(_contrast("#777777", "#777777"), 2) == 1.0
    assert round(_luminance("#FFFFFF"), 4) == 1.0
    assert round(_luminance("#000000"), 4) == 0.0
    # The WCAG worked example: #FF0000 on white is 3.998:1.
    assert round(_contrast("#FF0000", "#FFFFFF"), 2) == 4.0
    assert round(_lab("#FFFFFF")[0], 1) == 100.0
    assert round(_delta_e("#FFFFFF", "#FFFFFF"), 6) == 0.0
    # One 8-bit step of grey is nowhere near a just-noticeable difference.
    assert _delta_e("#808080", "#818181") < 0.5


# ── Rule 1: every text token on every ground it can land on ──────────────────


def test_every_text_token_clears_aa_on_every_ground():
    """The defect: a grey chosen on the page and then reused on a card.

    #8E8E93 measured 5.22:1 on the old page and 4.27:1 on the old cards, so
    every label that moved onto a card took a passing colour under the floor.
    """
    under = []
    for theme in _themes():
        for name, colour in theme.color.items():
            if not name.startswith("text.") or name == "text.onAccent":
                continue
            floor = LARGE if name == "text.disabled" else AA
            for ground in GROUNDS:
                ratio = _contrast(colour, theme.color[ground])
                if ratio < floor:
                    under.append("%s %s %s on %s is %.2f:1 (needs %.1f)"
                                 % (theme.name, name, colour, ground, ratio, floor))
    assert not under, "\n  " + "\n  ".join(under)


def test_disabled_text_reads_as_unavailable_rather_than_absent():
    """1.90:1 is what made a disabled control read as a rendering bug."""
    for theme in _themes():
        for ground in GROUNDS:
            ratio = _contrast(theme.color["text.disabled"], theme.color[ground])
            assert ratio >= LARGE, "%s disabled on %s is %.2f:1" % (
                theme.name, ground, ratio)
        # And still quieter than the tier above it, or the ramp says nothing.
        assert (_contrast(theme.color["text.tertiary"], theme.color["surface"])
                > _contrast(theme.color["text.disabled"], theme.color["surface"]))


def test_the_text_ramp_is_ordered_and_separated():
    """Four tiers that a reader can actually tell apart, on the tightest ground."""
    for theme in _themes():
        ramp = [theme.color["text.primary"], theme.color["text.secondary"],
                theme.color["text.tertiary"], theme.color["text.disabled"]]
        ratios = [_contrast(ink, theme.color["raised"]) for ink in ramp]
        assert ratios == sorted(ratios, reverse=True), \
            "%s text ramp is out of order: %s" % (theme.name, ratios)
        for one, other in zip(ramp, ramp[1:]):
            assert _delta_e(one, other) >= JND


def test_every_accent_coloured_text_clears_aa_on_every_ground():
    under = []
    for theme in _themes():
        for family in FAMILIES:
            ink = theme.color["%s.text" % family]
            for ground in GROUNDS:
                ratio = _contrast(ink, theme.color[ground])
                if ratio < AA:
                    under.append("%s %s.text %s on %s is %.2f:1"
                                 % (theme.name, family, ink, ground, ratio))
    assert not under, "\n  " + "\n  ".join(under)


# ── Rule 2: the ink every filled accent is painted with ──────────────────────


def test_every_filled_accent_carries_readable_ink():
    """White on the old #22A559 measured 3.18:1, which is why that fill moved.

    Every state is checked, not only the resting one: a :hover or :pressed fill
    that drops the label under the floor is the same defect one interaction
    later.
    """
    under = []
    for theme in _themes():
        ink = theme.color["text.onAccent"]
        for family in FAMILIES:
            for state in FILL_STATES:
                fill = theme.color["%s.%s" % (family, state)]
                ratio = _contrast(ink, fill)
                if ratio < AA:
                    under.append("%s %s on %s.%s %s is %.2f:1"
                                 % (theme.name, ink, family, state, fill, ratio))
    assert not under, "\n  " + "\n  ".join(under)


def test_the_old_green_would_have_failed_this_test():
    """The measurement the contract quotes, so the floor cannot be argued down."""
    assert round(_contrast("#FFFFFF", "#22A559"), 2) == 3.18
    assert _contrast("#FFFFFF", TH.theme("dark").color["accent.default"]) >= AA


# ── Rule 3: the surface ladder ───────────────────────────────────────────────


def test_a_card_reads_as_a_card_and_a_dialog_sits_above_the_page():
    """Four fills used to span 1.22:1 and a modal was the page colour."""
    for theme in _themes():
        for lower, upper in (("canvas", "surface"), ("surface", "raised")):
            ratio = _contrast(theme.color[lower], theme.color[upper])
            assert ratio >= LADDER, "%s %s -> %s is only %.3f:1" % (
                theme.name, lower, upper, ratio)


def test_elevation_runs_one_way_in_both_themes():
    """Lighter means higher, dark theme and light theme alike."""
    for theme in _themes():
        ladder = [_luminance(theme.color[name])
                  for name in ("canvas", "surface", "raised")]
        assert ladder == sorted(ladder), "%s ladder is %s" % (theme.name, ladder)


# ── Rule 4: nothing under the just-noticeable difference ─────────────────────


def test_no_two_tokens_are_the_same_colour_twice():
    """18 pairs used to sit under delta-E 2.0, the tightest at 0.94.

    Only a documented step of another token is exempt, and the exemption list
    lives in `ui/theme.py` where anyone adding a token will see it.
    """
    for theme in _themes():
        colours = _solid(theme)
        tight = []
        for one, other in itertools.combinations(sorted(colours), 2):
            if frozenset((one, other)) in TH.STEP_PAIRS:
                continue
            distance = _delta_e(colours[one], colours[other])
            if distance < JND:
                tight.append("%s %s %s / %s %s delta-E %.2f"
                             % (theme.name, one, colours[one], other,
                                colours[other], distance))
        assert not tight, "\n  " + "\n  ".join(tight)


def test_even_the_documented_steps_are_visible_steps():
    """A hover nobody can see is not a hover state."""
    for theme in _themes():
        for pair in TH.STEP_PAIRS:
            one, other = sorted(pair)
            distance = _delta_e(theme.color[one], theme.color[other])
            assert distance >= 1.0, "%s %s / %s delta-E %.2f" % (
                theme.name, one, other, distance)


# ── Rule 5: borders ──────────────────────────────────────────────────────────


def test_border_default_separates_both_surfaces_it_touches():
    for theme in _themes():
        for ground in GROUNDS:
            ratio = _contrast(theme.color["border.default"], theme.color[ground])
            assert ratio >= COMPONENT, "%s border.default on %s is %.2f:1" % (
                theme.name, ground, ratio)


def test_the_border_ramp_is_ordered():
    """subtle < default < strong, measured against the page."""
    for theme in _themes():
        ramp = [_contrast(theme.color["border.%s" % name], theme.color["canvas"])
                for name in ("subtle", "default", "strong")]
        assert ramp == sorted(ramp), "%s border ramp is %s" % (theme.name, ramp)


# ── The audit's critical finding: selection over focus ───────────────────────


def test_the_selected_ground_is_a_ground_change_you_can_see():
    """1.50:1 is what made the wrong tab read as the current one."""
    for theme in _themes():
        picked = theme.color["surfaceActive"]
        for ground in ("canvas", "surface", "inset"):
            ratio = _contrast(picked, theme.color[ground])
            assert ratio >= COMPONENT, "%s selection on %s is %.2f:1" % (
                theme.name, ground, ratio)
        ink = _contrast(theme.color["text.onAccent"], picked)
        assert ink >= AA, "%s selected ink is %.2f:1" % (theme.name, ink)


def test_the_focus_ring_never_outweighs_the_selection_beside_it():
    """The rule the whole contract is built around, as two ratios.

    The old ring measured 17.01:1 on the page against a selected tab at 1.50:1
    — eleven times the signal it was competing with.
    """
    for theme in _themes():
        for ground in GROUNDS:
            ring = _contrast(theme.color["accent.border"], theme.color[ground])
            picked = _contrast(theme.color["surfaceActive"], theme.color[ground])
            assert ring <= picked, (
                "%s focus is %.2f:1 and selection %.2f:1 on %s"
                % (theme.name, ring, picked, ground))


def test_the_focus_ring_is_still_visible_where_focus_actually_lands():
    """Subordinate is not the same as absent: the page and the wells."""
    for theme in _themes():
        for ground in ("canvas", "inset"):
            ring = _contrast(theme.color["accent.border"], theme.color[ground])
            assert ring >= COMPONENT, "%s ring on %s is %.2f:1" % (
                theme.name, ground, ring)


def test_the_focus_ring_is_not_white_in_either_theme():
    for theme in _themes():
        assert theme.color["accent.border"].upper() != "#FFFFFF"


# ── Focus against rest, which is the other half of the ordering ──────────────
#
# `l` below is the WCAG offset luminance, L+0.05, because every bound in this
# section is a ratio of two of them and writing it that way is what makes the
# arithmetic checkable rather than assertable.


def _l(colour: str) -> float:
    return _luminance(colour) + 0.05


def test_taking_focus_is_a_gain_in_emphasis_where_the_palette_can_afford_one():
    """Focus has to outrank REST, not only sit under selection.

    A control that takes the keyboard and comes out *less* outlined than it was
    at rest has answered "you are here" with a dimming, and for a red-green
    colour-blind reader — for whom the green ring and the grey border it
    replaces differ mostly in luminance — that is the whole signal going the
    wrong way.

    The light palette carries the full ordering on every ground and this is
    what pins it: rest 3.83, focus 4.81, selection 6.22 on the page. The dark
    palette cannot, and the test below measures why rather than excusing it.
    """
    for theme in _themes():
        for ground in GROUNDS:
            rest = _contrast(theme.color["border.default"], theme.color[ground])
            ring = _contrast(theme.color["accent.border"], theme.color[ground])
            picked = _contrast(theme.color[SELECTED], theme.color[ground])
            if 3 * _l(theme.color["raised"]) > _l(theme.color["text.onAccent"]) / AA:
                continue                      # out of range; measured below
            assert rest < ring <= picked, (
                "%s on %s: rest %.2f, focus %.2f, selection %.2f"
                % (theme.name, ground, rest, ring, picked))


def test_the_dark_palette_has_no_room_for_a_ring_above_its_resting_border():
    """The bound, computed from the tokens, so nobody re-attempts it blind.

    Two rules pin the two ends. Rule 5 puts `border.default` at 3:1 on the
    lightest ground it touches, and a subordinate mark cannot measure louder
    than a selected ground that still has to carry readable ink at 4.5:1. In
    dark those come out at l >= 0.342 and l <= 0.233 — the window between them
    is empty, and it is empty by 46%.

    As a span: the dark theme is asked for 1.4 x 1.4 (the surface ladder) x 3
    (the border on `raised`) x 4.5 (white on the selection) = 26.46:1 from the
    page to its brightest ink. Black to white is 21:1.
    """
    dark, light = TH.theme("dark"), TH.theme("light")

    floor = 3 * _l(dark.color["raised"])
    ceiling = _l(dark.color["text.onAccent"]) / AA
    assert floor > ceiling, (
        "the dark palette now has room for a ring above its resting border "
        "(floor %.4f, ceiling %.4f) — assert the ordering instead of this"
        % (floor, ceiling))
    assert round(floor / ceiling, 2) == 1.46

    demanded = LADDER * LADDER * COMPONENT * AA
    assert round(demanded, 2) == 26.46
    assert demanded > _contrast("#FFFFFF", "#000000")

    # And the reason the light palette can: its page is a mid grey, so a mark
    # has room to get louder by going *down*, and the ladder runs the other way.
    assert _l(light.color["canvas"]) / _l(light.color["text.primary"]) > 8.0
    assert _l(light.color["border.default"]) > _l(light.color["accent.border"]) \
        > _l(light.color[SELECTED])


def test_the_focus_ring_is_as_loud_as_the_ordering_lets_it_be():
    """Subordinate is a ceiling, not a target — the ring sits just under it.

    Dark measures 86% of the selection on every ground and light 77%. Without
    a floor here, "focus is subordinate" reads as a licence to dim the ring,
    which is how a ring ends up at 3.70:1 beside a resting border at 6.50:1.
    """
    for theme in _themes():
        for ground in GROUNDS:
            ring = _contrast(theme.color["accent.border"], theme.color[ground])
            picked = _contrast(theme.color[SELECTED], theme.color[ground])
            assert ring >= 0.75 * picked, (
                "%s: the ring is %.2f:1 on %s where the selection it may go up "
                "to is %.2f:1" % (theme.name, ring, ground, picked))


# ── The sixth ground ─────────────────────────────────────────────────────────


def test_the_selected_ground_cannot_carry_the_text_ramp():
    """Why `surfaceActive` is one ink and not four, as arithmetic.

    A selected ground clears 3:1 against the row it replaces and the dimmest
    text tier clears 4.5:1 against the selected ground, so that tier ends up
    13.5x the ground the rest of the ramp is read on. Against `surface` at
    l=0.0769 that is l >= 1.038, and white is 1.05 — the ramp would be four
    whites, which rule 4 forbids and a reader cannot tell apart anyway.
    """
    for theme in _themes():
        ground = max(_l(theme.color[name]) for name in ("surface", "inset"))
        needed = AA * COMPONENT * ground
        assert needed > _l(theme.color["text.tertiary"]), (
            "%s: the ramp now reaches the selected ground (needs l %.4f, "
            "tertiary is %.4f) — assert the ramp on it instead of this"
            % (theme.name, needed, _l(theme.color["text.tertiary"])))
        # What is achievable, and what the sheet is held to below.
        assert _contrast(theme.color["text.onAccent"], theme.color[SELECTED]) >= AA


def _rules(sheet: str) -> list:
    """(selector, body) for every rule in `sheet`, comments stripped."""
    sheet = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    found = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
        for one in selectors.split(","):
            one = " ".join(one.split())
            if one:
                found.append((one, body))
    return found


def _resting(selector: str) -> str:
    """The same selector with its states taken off: `#tab:checked` -> `#tab`.

    `::item` survives and `:selected` does not, which is the distinction: one
    names a sub-element the sheet styles in its own right, the other names a
    state of it.
    """
    return re.sub(r"(?<!:):(?!:)[a-zA-Z-]+", "", selector)


def _declared(body: str, prop: str) -> str:
    found = re.search(r"(?<![\w-])%s:\s*(#[0-9A-Fa-f]{6})" % prop, body)
    return found.group(1).upper() if found else ""


def test_a_selected_ground_never_carries_ink_it_cannot_read():
    """Every rule that paints on the selected ground, checked against it.

    The token rule the contract asks for is out of range — the test above
    measures by how much — so this is the guarantee that replaces it, and it is
    the stronger one: a future rule that grounds on `surfaceActive` and reaches
    for `text.primary` fails here whatever the palette says.
    """
    for theme in _themes():
        picked = theme.color[SELECTED].upper()
        seen = 0
        for selector, body in _rules(TH.stylesheet(theme)):
            # A widget's own fill and the highlight it draws behind selected
            # text are two different grounds in one rule — `QLineEdit` sets both
            # — so each is asked for its own ink rather than for whichever
            # declaration happens to come first.
            for fill, ink in (("background-color", "color"),
                              ("background", "color"),
                              ("selection-background-color", "selection-color")):
                if _declared(body, fill) != picked:
                    continue
                seen += 1
                painted = _declared(body, ink)
                assert painted, \
                    "%s: %s grounds on the selection and names no %s" % (
                        theme.name, selector, ink)
                ratio = _contrast(painted, picked)
                assert ratio >= AA, \
                    "%s: %s paints %s on the selection at %.2f:1" % (
                        theme.name, selector, painted, ratio)
        assert seen >= 8, "only %d rules ground on the selection" % seen


def test_a_control_that_is_pressed_or_checked_keeps_its_own_outline():
    """`border.default` measures 1.51:1 on the dark selection and 1.62:1 on the
    light one, so a control that was outlined at rest lost its edge at the
    moment it was pressed. No border token fixes it — one clearing 3:1 on the
    selected ground sits either 3x above it (a near-white hairline on every
    control) or 3x below it, which then fails 3:1 on `surface` — so the rule is
    that a control whose ground becomes the selection re-declares its edge.

    Scoped to the outline and not to a rail: `border-left` on a selected row is
    a mark against the row beside it, not the row's own outline.
    """
    for theme in _themes():
        picked = theme.color[SELECTED].upper()
        rules = _rules(TH.stylesheet(theme))
        resting = {selector: body for selector, body in rules}
        checked = 0
        for selector, body in rules:
            if _declared(body, "background-color") != picked:
                continue
            base = _resting(selector)
            if base == selector:
                continue
            outline = re.search(r"(?<![\w-])border:\s*\d+px\s+\w+\s+"
                                r"(#[0-9A-Fa-f]{6})", resting.get(base, ""))
            if outline is None:
                continue                      # nothing was there to lose
            checked += 1
            edge = _declared(body, "border-color") or _declared(body, "border")
            assert edge, (
                "%s: %s takes the selected ground and keeps %s's %s outline, "
                "which measures %.2f:1 on it"
                % (theme.name, selector, base, outline.group(1),
                   _contrast(outline.group(1), picked)))
            ratio = _contrast(edge, picked)
            assert ratio >= COMPONENT, (
                "%s: %s outlines in %s on the selection at %.2f:1"
                % (theme.name, selector, edge, ratio))
        assert checked >= 3, "only %d rules were measured" % checked


# ── The token set itself ─────────────────────────────────────────────────────

REQUIRED_COLOURS = (
    ["canvas", "surface", "surfaceHover", "surfaceActive", "raised", "inset",
     "scrim"]
    + ["border.%s" % n for n in ("subtle", "default", "strong")]
    + ["text.%s" % n for n in ("primary", "secondary", "tertiary", "disabled",
                               "onAccent")]
    + ["%s.%s" % (fam, part) for fam in FAMILIES
       for part in ("subtle", "default", "hover", "active", "border", "text")]
)


def test_both_themes_define_every_key_and_no_others():
    for theme in _themes():
        assert sorted(theme.color) == sorted(REQUIRED_COLOURS), \
            "%s palette differs from the contract's token set" % theme.name


def test_the_type_scale_has_a_heading_tier_and_steps_rather_than_marches():
    """The audit: five sizes inside an 11-15px band, largest over smallest 1.36.

    What was missing is a heading tier, so that is what is measured. 28 down to
    14 is four steps of about 1.2, and the scale as a whole now spans 2.55x. The
    four body tiers keep the +1px steps the contract fixes them at, and are told
    apart by weight and role as well as by size.
    """
    scale = TH.theme().font
    tiers = ["display", "h1", "h2", "h3", "body", "small", "caption"]
    sizes = [scale[name][0] for name in tiers]
    assert sizes == [28, 20, 16, 14, 13, 12, 11]
    assert max(sizes) / min(sizes) >= 2.5
    for bigger, smaller in zip(sizes[:4], sizes[1:4]):
        assert 1.14 <= bigger / smaller <= 1.45, \
            "%d/%d is %.2f" % (bigger, smaller, bigger / smaller)
    assert scale["body"][0] == scale["bodyMed"][0]
    assert scale["bodyMed"][1] > scale["body"][1]
    assert all(scale[name][1] >= 600 for name in ("display", "h1", "h2", "h3"))
    assert TH.TRACKING["caption"] == 0.4


def test_space_radius_control_and_motion_are_the_contract_values():
    theme = TH.theme()
    assert [theme.space[str(step)] for step in range(10)] == \
        [0, 4, 8, 12, 16, 20, 24, 32, 40, 48]
    assert theme.space["hair"] == 2
    assert theme.radius == {"sm": 4, "md": 6, "lg": 10, "pill": 999}
    assert theme.control == {"xs": 24, "sm": 28, "md": 32, "lg": 40,
                             "row": 36, "header": 44}
    assert TH.theme(density="compact").control == {
        "xs": 22, "sm": 26, "md": 28, "lg": 36, "row": 28, "header": 40}
    assert theme.motion == {"instant": 0, "fast": 120, "base": 180, "slow": 260}


def test_every_spacing_the_sheet_writes_is_on_the_grid():
    """The audit found 9 margins, 12 spacings and 16 paddings, several off-grid.

    Font sizes count as tokens here because the sheet writes them in px too; a
    1px hairline is the one literal left, and it is a border width, not a space.
    """
    theme = TH.theme()
    allowed = (set(theme.space.values()) | set(theme.radius.values())
               | set(theme.control.values())
               | {size for size, _weight in theme.font.values()} | {1})
    sheet = re.sub(r"/\*.*?\*/", "", TH.stylesheet(theme), flags=re.S)
    off = sorted({int(value) for value in re.findall(r"(\d+)px", sheet)}
                 - allowed)
    assert not off, "sizes off every token: %s" % off


def test_token_reads_by_path_and_refuses_a_path_that_is_not_one():
    theme = TH.theme()
    assert TH.token(theme, "color.text.primary") == theme.color["text.primary"]
    assert TH.token(theme, "space.4") == 16
    assert TH.token(theme, "radius.lg") == 10
    assert TH.token(theme, "control.md") == 32
    assert TH.token(theme, "motion.fast") == 120
    assert TH.token(theme, "font.body.size") == 13
    assert TH.token(theme, "font.body.weight") == 400
    for path in ("color.text.nope", "nope.at.all", "font.body.colour", "space.11"):
        try:
            TH.token(theme, path)
        except KeyError:
            continue
        raise AssertionError("%r resolved to something" % path)


def test_a_settings_file_can_actually_ask_for_a_theme():
    """The two keys `from_settings` reads were not in the settings schema.

    `core.settings` keeps only the keys `DEFAULT_SETTINGS` names, so writing
    {"theme": "light", "density": "compact"} into settings.json and reading it
    back returned dark and comfortable — the light palette and the compact
    density were both built, both measured, and unreachable from the app.
    """
    assert ST.DEFAULT_SETTINGS["theme"] == TH.DEFAULT_THEME
    assert ST.DEFAULT_SETTINGS["density"] == TH.DEFAULT_DENSITY

    saved = ST.load_settings()
    try:
        ST.save_settings(dict(saved, theme="light", density="compact"))
        back = ST.load_settings()
        assert (back["theme"], back["density"]) == ("light", "compact"), \
            "the file said light/compact and it read back %r/%r" % (
                back["theme"], back["density"])
        wanted = TH.from_settings(back)
        assert (wanted.name, wanted.density) == ("light", "compact")
        assert wanted.control["md"] == TH.theme("light", "compact").control["md"]
    finally:
        ST.save_settings(saved)
    assert TH.from_settings(ST.load_settings()).name == saved["theme"]


def test_an_unreadable_preference_still_starts_the_app():
    assert TH.from_settings({}).name == "dark"
    assert TH.from_settings({}).density == "comfortable"
    assert TH.from_settings({"theme": "light"}).name == "light"
    assert TH.from_settings({"theme": "solarized"}).name == "dark"
    assert TH.from_settings({"density": "compact"}).density == "compact"
    assert TH.from_settings({"density": "roomy"}).density == "comfortable"
    assert sorted(TH.THEMES) == ["dark", "light"]


# ── The generated sheet ──────────────────────────────────────────────────────

# Every selector the 643-line literal in `ui/app.py` carried, enumerated from
# that source before it was deleted. A selector missing from the generated sheet
# is a widget that silently lost its styling.
AUDITED_SELECTORS = (
    "QWidget", "QMainWindow", "QLabel", ".QWidget", "QLineEdit", "QTextEdit",
    "QLineEdit:focus", "QTextEdit:focus", "QPushButton", "QPushButton:hover",
    "QPushButton:disabled", "QPushButton#outlined", "QPushButton#outlined:hover",
    "QPushButton#outlined:disabled", "QPushButton#danger", "QPushButton#live",
    "QPushButton#live:hover", "QPushButton#rehearsal", "QPushButton#rehearsal:hover",
    "QPushButton#danger:hover", "QPushButton#danger:disabled", "QCheckBox",
    "QTableWidget", "QTableWidget::item", "QTableWidget::item:selected",
    "QHeaderView::section", "QScrollBar:vertical", "QScrollBar::handle:vertical",
    "QScrollBar::add-line:vertical", "QScrollBar::sub-line:vertical",
    "QScrollBar:horizontal", "QScrollBar::handle:horizontal",
    "QScrollBar::add-line:horizontal", "QScrollBar::sub-line:horizontal",
    "QListWidget", "QListWidget::item", "QProgressBar", "QProgressBar::chunk",
    "QLabel#section_label", "QPushButton#tab", "QPushButton#tab:hover",
    "QPushButton#tab:checked", "QLabel#hint", "QLabel#app_name",
    "QLabel#count_label", "QLabel#count_sub", "QLabel#muted", "QLabel#status_text",
    "QTableWidget#results_table", "QWidget#results_header", "QFrame#card",
    "QScrollArea", "QScrollArea > QWidget > QWidget", "QDialog",
    "QSlider::groove:horizontal", "QSlider::handle:horizontal",
    "QSlider::handle:horizontal:hover", "QSlider::sub-page:horizontal",
    "QSpinBox#spin", "QSpinBox#spin::up-button", "QSpinBox#spin::down-button",
    "QListWidget#saved_list", "QListWidget#saved_list::item",
    "QListWidget#saved_list::item:selected",
    "QListWidget#saved_list::item:selected:hover",
    "QListWidget#saved_list::item:hover", "QLabel#toast", "QPushButton#start_btn",
    "QPushButton#start_btn:hover", "QPushButton#start_btn:pressed",
    "QPushButton#start_btn:disabled", "QLineEdit#search_box", "QComboBox",
    "QComboBox:hover", "QComboBox QAbstractItemView", "QDoubleSpinBox#spin",
    "QMenu", "QMenu::item", "QMenu::item:selected", "QMenu::separator",
    "QPushButton#reveal", "QPushButton#reveal:hover", "QPushButton#reveal:checked",
    "QPushButton:focus", "QPushButton#outlined:focus", "QPushButton#danger:focus",
    "QPushButton#live:focus", "QPushButton#rehearsal:focus", "QPushButton#tab:focus",
    "QPushButton#start_btn:focus", "QPushButton#reveal:focus", "QLabel#status_ok",
    "QLabel#status_err", "QLabel#status_busy", "QLabel#warning", "QFrame#divider",
    "QListWidget#service_list", "QListWidget#service_list::item",
    "QListWidget#service_list::item:disabled", "QProgressBar#budget_bar",
    "QProgressBar#budget_bar::chunk", "QDateEdit#spin",
    "QDateEdit#spin::up-button", "QDateEdit#spin::down-button",
)


def _selectors(sheet: str) -> list:
    sheet = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    found = []
    for selector, _body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
        for one in selector.split(","):
            one = " ".join(one.split())
            if one:
                found.append(one)
    return found


def test_the_generated_sheet_covers_every_selector_the_old_one_did():
    """A missed selector is a widget that silently lost its look."""
    for theme in _themes():
        generated = set(_selectors(TH.stylesheet(theme)))
        missing = [s for s in AUDITED_SELECTORS if s not in generated]
        assert not missing, "%s sheet drops %d selector(s): %s" % (
            theme.name, len(missing), missing)


def test_the_generated_sheet_parses_as_rules_and_nothing_is_left_unsubstituted():
    for theme in _themes():
        sheet = TH.stylesheet(theme)
        assert "$" not in sheet, "a placeholder survived into the sheet"
        assert sheet.count("{") == sheet.count("}")
        assert len(_selectors(sheet)) > len(AUDITED_SELECTORS)


def test_every_colour_in_the_sheet_came_out_of_the_palette():
    """The point of the whole module: no hex is written anywhere else."""
    for theme in _themes():
        known = {value.upper() for value in _solid(theme).values()}
        painted = {value.upper()
                   for value in re.findall(r"#[0-9A-Fa-f]{6}\b",
                                           TH.stylesheet(theme))}
        assert painted <= known, "stray colours: %s" % sorted(painted - known)
        assert len(painted) >= 20, "the sheet paints almost nothing"


def test_the_sheet_is_px_throughout_so_windows_text_scaling_reaches_it():
    """The app used to mix pt and px, so its own text ignored the OS setting."""
    for theme in _themes():
        sheet = re.sub(r"/\*.*?\*/", "", TH.stylesheet(theme), flags=re.S)
        assert not re.search(r"\d\s*pt\b", sheet), "the sheet writes points"
        assert not re.search(r"\d\s*(em|rem|%)\b", sheet)


def test_the_sheet_still_leaves_the_check_indicator_to_the_style():
    """A single `::indicator` rule anywhere beats `TickStyle` and the tick goes."""
    for theme in _themes():
        sheet = re.sub(r"/\*.*?\*/", "", TH.stylesheet(theme), flags=re.S)
        rules = [line for line in sheet.splitlines() if "::indicator" in line]
        assert rules == [], "the sheet styles an indicator again: %s" % rules


def test_every_object_name_the_screens_use_is_styled():
    """Scanned from the screens, so a new objectName cannot arrive unstyled.

    Two are exempt and stay exempt for a reason: `limit_slider` is a QSlider and
    takes the generic slider rules, and `template_split` is a QSplitter whose
    handle `screen_settings` still styles itself.
    """
    exempt = {"limit_slider", "template_split"}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    names = set()
    for entry in sorted(os.listdir(os.path.join(here, "ui"))):
        if not entry.endswith(".py"):
            continue
        with open(os.path.join(here, "ui", entry), encoding="utf-8") as handle:
            names.update(re.findall(r'setObjectName\("([^"]+)"\)', handle.read()))
    sheet = TH.stylesheet(TH.theme())
    unstyled = sorted(name for name in names - exempt
                      if "#%s" % name not in sheet)
    assert not unstyled, "objectNames with no rule: %s" % unstyled


def test_the_two_themes_produce_two_different_sheets_of_the_same_shape():
    dark, light = TH.stylesheet(TH.theme("dark")), TH.stylesheet(TH.theme("light"))
    assert dark != light
    assert _selectors(dark) == _selectors(light)


def test_density_changes_control_heights_and_nothing_else():
    comfortable = TH.stylesheet(TH.theme("dark", "comfortable"))
    compact = TH.stylesheet(TH.theme("dark", "compact"))
    assert comfortable != compact
    assert _selectors(comfortable) == _selectors(compact)
    assert "height: 32px" in comfortable and "height: 28px" in compact


# ── Pixels: the ordering the audit asked for, sampled ────────────────────────


def _tabs() -> tuple:
    """Two #tab buttons on one host, and the app styled the way `run()` styles it."""
    app = _app()
    TH.apply(app, TH.theme("dark"))
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    made = []
    for label in ("Leads", "Campaign"):
        button = QPushButton(label)
        button.setObjectName("tab")
        button.setCheckable(True)
        button.setFixedSize(QSize(120, 28))
        row.addWidget(button)
        made.append(button)
    host.resize(QSize(260, 40))
    host.show()
    QApplication.setActiveWindow(host)
    app.processEvents()
    return host, made[0], made[1]


def _grab(button, elsewhere, *, checked=False, focus=False) -> dict:
    """`button` painted in one state, as a colour histogram."""
    button.setChecked(checked)
    (button if focus else elsewhere).setFocus(Qt.TabFocusReason)
    _app().processEvents()
    assert button.hasFocus() == focus
    image = button.grab().toImage()
    counts: dict = {}
    for y in range(image.height()):
        for x in range(image.width()):
            name = image.pixelColor(x, y).name().upper()
            counts[name] = counts.get(name, 0) + 1
    return counts


def _added(before: dict, after: dict) -> dict:
    """The colours a state painted that the resting state did not."""
    return {colour: count for colour, count in after.items()
            if count > before.get(colour, 0)}


def test_a_selected_tab_outweighs_a_focused_one_on_the_screen():
    """The defect, sampled: the focus ring used to be eleven times the selection.

    Two grabs of the same button — selected and unfocused, then focused and
    unselected — against the same resting grab. Selection has to win on both
    counts a user actually perceives: how much of the control changes, and how
    far the change is from the ground it changed from.
    """
    host, tab, other = _tabs()
    try:
        resting = _grab(tab, other)
        ground = max(resting, key=resting.get)

        selected = _added(resting, _grab(tab, other, checked=True))
        focused = _added(resting, _grab(tab, other, focus=True))
        assert selected and focused, "a state stopped painting anything"

        picked = max(selected, key=selected.get)
        ring = max(focused, key=focused.get)
        picked_px = sum(selected.values())
        ring_px = sum(focused.values())

        assert picked_px > 3 * ring_px, (
            "selection repaints %d px and focus %d — the ring is not subordinate"
            % (picked_px, ring_px))
        assert _contrast(picked, ground) >= _contrast(ring, ground), (
            "selection paints %s at %.2f:1 on %s and focus %s at %.2f:1"
            % (picked, _contrast(picked, ground), ground, ring,
               _contrast(ring, ground)))
        assert _contrast(picked, ground) >= COMPONENT, (
            "a selected tab is %.2f:1 against an unselected one"
            % _contrast(picked, ground))
        assert "#FFFFFF" not in focused, "the ring is white again"
    finally:
        host.hide()


def test_focus_moves_no_geometry_because_the_border_is_there_either_way():
    """A 1px ring on a 1px transparent base: only the colour moves."""
    host, tab, other = _tabs()
    try:
        other.setFocus(Qt.TabFocusReason)
        _app().processEvents()
        resting = (tab.contentsRect(), tab.sizeHint())
        tab.setFocus(Qt.TabFocusReason)
        _app().processEvents()
        assert (tab.contentsRect(), tab.sizeHint()) == resting, \
            "taking focus moved the contents rect"
    finally:
        host.hide()


def test_a_tab_that_is_both_selected_and_focused_shows_both_marks():
    host, tab, other = _tabs()
    try:
        resting = _grab(tab, other)
        selected = _added(resting, _grab(tab, other, checked=True))
        both = _added(resting, _grab(tab, other, checked=True, focus=True))
        ring = TH.theme("dark").color["accent.border"].upper()
        rail = TH.theme("dark").color["accent.default"].upper()
        assert max(selected, key=selected.get) in both, "the selection was lost"
        assert ring in both, "the ring was lost under the selection"
        assert rail in both, "the accent rail was lost under the ring"
    finally:
        host.hide()
