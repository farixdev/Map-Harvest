"""The icon set, measured in the pixels it actually paints.

`ui/icons.py` is the one place in the app besides `TickStyle` that puts colour
on screen without going through the stylesheet, so nothing here reads the
module's own constants back to itself. Every assertion below renders the icon
and counts what came out: how much ink there is, what colour it is, and whether
it changed when the palette did.

Three properties carry the set. It has to *paint* — an icon that renders empty
is a control with a blank square where its meaning was, and at 12px with a
one-pixel stroke that is a real failure mode rather than a theoretical one. It
has to paint in the *tone it was asked for*, because the whole point of drawing
these rather than shipping them is that they follow the theme. And no two names
may render the same drawing, which is what catches the copy-and-paste that
leaves `duplicate` looking exactly like `copy`.

The pixel scan reads the image's own buffer rather than calling `pixel()` per
point: the full matrix is 32 icons x 10 tones x 4 sizes x 2 themes, and 2,560
renders at one Python call per pixel is a minute of the suite's time for the
same numbers this gets in under a second.

`tests/conftest.py` has already pointed `core.settings` and `core.templates` at
a temp profile by the time this imports, so nothing here can read or write a
real ~/.mapharvest.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from PyQt5.QtCore import QRectF  # noqa: E402
from PyQt5.QtGui import QColor, QIcon, QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui import icons as IC  # noqa: E402
from ui import theme as TH  # noqa: E402

# The roles the four screens actually ask for, named here rather than in the
# module so that dropping one from the set is a failure and not a silent edit.
NEEDED = ("search", "table", "send", "gear", "mail", "document", "person",
          "chart", "filter", "columns", "eye", "plus", "minus", "pencil",
          "duplicate", "copy", "trash", "reset", "play", "pause", "stop",
          "check", "warning", "error", "info", "chevron-up", "chevron-down",
          "chevron-left", "chevron-right", "external", "clock", "calendar")

_APP = None


def _app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture(autouse=True)
def _restore():
    """Leave the process wearing what it was wearing.

    Every other UI file in the suite shares this QApplication, and an icon
    module still pointed at the light palette — or an application still wearing
    it — is a failure in whichever file happens to run next.
    """
    _app()
    yield
    IC.use_theme(None)
    IC.forget()
    TH.apply(_app(), TH.theme())


# ── Reading what was painted ─────────────────────────────────────────────────


def _image(name, *, tone="secondary", size="md") -> QImage:
    return IC.pixmap(name, tone=tone, size=size).toImage().convertToFormat(
        QImage.Format_ARGB32)


def _ink(image: QImage) -> tuple:
    """(coverage in whole pixels, alpha-weighted mean rgb, alpha on the rim).

    Alpha-weighted because antialiasing is the whole reason a stroke is not one
    colour: a pixel the stroke half covers carries the ink at half alpha, and
    weighting by that recovers the colour the pen was actually holding — a
    plain average over painted pixels would be dragged around by whichever
    edges a particular drawing happens to have.
    """
    data = image.bits().asstring(image.byteCount())
    stride, wide, tall = image.bytesPerLine(), image.width(), image.height()
    weight, channels, rim = 0, [0, 0, 0], 0
    for y in range(tall):
        row = data[y * stride:y * stride + wide * 4]
        for x in range(wide):
            blue, green, red, alpha = row[x * 4:x * 4 + 4]
            if not alpha:
                continue
            weight += alpha
            for index, value in enumerate((red, green, blue)):
                channels[index] += value * alpha
            if x in (0, wide - 1) or y in (0, tall - 1):
                rim = max(rim, alpha)
    if not weight:
        return 0.0, None, rim
    return weight / 255.0, tuple(round(c / weight) for c in channels), rim


def _rgb(colour: str) -> tuple:
    one = QColor(colour)
    return one.red(), one.green(), one.blue()


def _bytes(name, *, tone="secondary", size="lg") -> bytes:
    image = _image(name, tone=tone, size=size)
    return image.bits().asstring(image.byteCount())


# ── The set itself ───────────────────────────────────────────────────────────


def test_the_set_covers_every_role_the_app_asks_for():
    """The app had zero icons, so the failure to guard against is an incomplete
    set arriving instead of a complete one."""
    assert set(NEEDED) <= set(IC.ICONS), \
        "missing: %s" % sorted(set(NEEDED) - set(IC.ICONS))
    assert len(IC.ICONS) == len(set(IC.ICONS)), "a name is in the set twice"
    assert all(name == name.lower() and " " not in name for name in IC.ICONS)


def test_no_two_names_draw_the_same_icon():
    """The copy-and-paste this file exists to catch, as pixels.

    `duplicate` and `copy` are the pair at risk — they are one shape apart —
    and the four chevrons are the pair that would be caught by nothing else,
    since three of them are one definition rotated and a wrong angle is still a
    chevron.
    """
    drawn = {}
    for name in IC.ICONS:
        painted = _bytes(name)
        clash = drawn.get(painted)
        assert clash is None, "%s and %s render identically" % (clash, name)
        drawn[painted] = name


def test_every_icon_paints_something_at_every_size_and_tone():
    """An icon that renders empty is a blank square where a meaning was."""
    empty = []
    for palette in ("dark", "light"):
        IC.use_theme(TH.theme(palette))
        for name in IC.ICONS:
            for size in IC.SIZES:
                for tone in IC.TONES:
                    coverage, _mean, _rim = _ink(_image(name, tone=tone,
                                                        size=size))
                    if coverage < 4.0:
                        empty.append("%s %s %s at %s covers %.2f px"
                                     % (palette, name, tone, size, coverage))
    assert not empty, "\n  " + "\n  ".join(empty)


def test_more_pixels_arrive_as_the_size_token_grows():
    """The sizes have to be sizes: a set that renders 12px art into a 24px box
    is four names for one icon."""
    for name in IC.ICONS:
        coverage = [_ink(_image(name, size=size))[0]
                    for size in ("xs", "sm", "md", "lg")]
        assert coverage == sorted(coverage), "%s: %s" % (name, coverage)
        assert coverage[-1] > 1.5 * coverage[0], "%s: %s" % (name, coverage)


def test_every_icon_is_painted_in_the_tone_it_was_asked_for():
    """The whole reason these are drawn: the palette reaches them.

    Measured over the entire matrix, and against the token the tone resolves
    to rather than against a colour written down here. The tolerance is one
    unit per channel, which is what unpremultiplying an antialiased edge back
    to 8-bit costs — the worst case over all 2,560 combinations measures
    exactly 1.
    """
    wrong = []
    for palette in ("dark", "light"):
        look = TH.theme(palette)
        IC.use_theme(look)
        for tone in IC.TONES:
            wanted = _rgb(IC._colour(look, tone))
            for size in IC.SIZES:
                for name in IC.ICONS:
                    _coverage, mean, _rim = _ink(_image(name, tone=tone,
                                                        size=size))
                    if mean is None or max(abs(a - b)
                                           for a, b in zip(mean, wanted)) > 2:
                        wrong.append("%s %s %s at %s came out %s, not %s"
                                     % (palette, name, tone, size, mean,
                                        wanted))
    assert not wrong, "\n  " + "\n  ".join(wrong[:12])


def test_a_tone_is_the_same_colour_as_the_label_it_sits_beside():
    """`tone` names a role, and a role resolves to the palette's own ink.

    An icon tinted a colour of its own is the defect the token system exists to
    prevent, so the mapping is checked against the tokens rather than trusted.
    """
    look = TH.theme()
    for tone in ("primary", "secondary", "tertiary", "disabled"):
        assert IC._colour(look, tone) == look.color["text.%s" % tone]
    assert IC._colour(look, "onAccent") == look.color["text.onAccent"]
    for family in IC.FAMILIES:
        assert IC._colour(look, family) == look.color["%s.text" % family]
    assert set(IC.TONES) == {"primary", "secondary", "tertiary", "disabled",
                             "onAccent"} | set(IC.FAMILIES)


def test_an_icon_follows_the_palette_the_window_is_wearing():
    """No call site has to be told about the theme for this to work.

    `theme.apply()` is the call every appearance change already makes, and it
    records what it applied; `icons.active_theme()` reads that. Without this
    path an icon built by a screen that was never handed a theme keeps the dark
    palette's ink on a light page — which is the same defect as a card painting
    `surface` from the wrong palette, one drawing further in.
    """
    app = _app()
    IC.use_theme(None)
    for palette in ("light", "dark"):
        look = TH.theme(palette)
        TH.apply(app, look)
        assert IC.active_theme().name == palette
        _coverage, mean, _rim = _ink(_image("mail", tone="primary"))
        assert mean == _rgb(look.color["text.primary"]), palette

    TH.apply(app, TH.theme("dark"))
    IC.use_theme(TH.theme("light"))
    _coverage, mean, _rim = _ink(_image("mail", tone="primary"))
    assert mean == _rgb(TH.theme("light").color["text.primary"]), \
        "an explicitly named theme lost to the one the app is wearing"


def test_the_two_palettes_do_not_draw_the_same_icon_twice():
    """A tint nobody can see is not a tint."""
    IC.use_theme(TH.theme("dark"))
    dark = _bytes("gear", tone="primary")
    IC.use_theme(TH.theme("light"))
    assert _bytes("gear", tone="primary") != dark


# ── Geometry ─────────────────────────────────────────────────────────────────


def test_nothing_is_drawn_outside_the_box_it_is_given():
    """Every path, plus the pen that thickens it, inside the unit square.

    Measured on the geometry rather than on the raster, because a shape clipped
    by half a pixel at 24px is invisible in a pixel count and is a shape that
    loses a whole limb at 12px on a scaled display.
    """
    box = QRectF(0.0, 0.0, 1.0, 1.0)
    for name in IC.ICONS:
        sketch = IC._Sketch()
        IC._DRAW[name](sketch)
        for path, half in ((sketch.stroke, IC.STROKE / 2), (sketch.fill, 0.0)):
            if path.isEmpty():
                continue
            bounds = path.controlPointRect().adjusted(-half, -half, half, half)
            assert box.contains(bounds), "%s reaches %s" % (name, bounds)


def test_an_icon_takes_its_size_from_the_grid_and_not_from_a_number():
    """A pixel size at a call site is the defect this module replaces."""
    look = TH.theme()
    assert [IC._pixels(look, size) for size in ("xs", "sm", "md", "lg")] == \
        [look.space["3"], look.space["4"], look.space["5"], look.space["6"]]
    assert [IC._pixels(look, size) for size in ("xs", "sm", "md", "lg")] == \
        [12, 16, 20, 24]
    for size in IC.SIZES:
        image = _image("check", size=size)
        assert image.width() == image.height() == IC._pixels(look, size)

    # The number a caller needs for `setIconSize`, so a button cannot ask for a
    # size the icon was not drawn at and get a resampled one back.
    IC.use_theme(look)
    assert IC.pixels("sm") == look.space["4"]
    assert IC.pixels() == IC._pixels(look, "md")
    with pytest.raises(KeyError):
        IC.pixels("enormous")


def test_a_name_a_tone_or_a_size_that_is_not_one_says_so():
    """Loudly, and at the call site: the three of them are closed sets, and a
    typo that silently renders nothing is a blank square nobody can explain."""
    for call in (lambda: IC.icon("nope"),
                 lambda: IC.icon("mail", tone="chartreuse"),
                 lambda: IC.icon("mail", size="enormous"),
                 lambda: IC.pixmap("nope")):
        with pytest.raises(KeyError):
            call()


def test_the_drawing_is_resolution_independent():
    """The reason these are paths: the same declaration at twice the density.

    A 20px icon on a 200% display is 40 device pixels of drawing carrying a
    device pixel ratio of 2, not a 20px bitmap doubled — so it has more ink in
    it, not the same ink twice the size.
    """
    look = TH.theme()
    colour, px = look.color["text.secondary"], IC._pixels(look, "md")
    one = IC._render("gear", colour, px, 1.0)
    two = IC._render("gear", colour, px, 2.0)
    assert (one.width(), one.height()) == (px, px)
    assert (two.width(), two.height()) == (px * 2, px * 2)
    assert one.devicePixelRatio() == 1.0 and two.devicePixelRatio() == 2.0
    assert _ink(two.toImage().convertToFormat(QImage.Format_ARGB32))[0] > \
        3 * _ink(one.toImage().convertToFormat(QImage.Format_ARGB32))[0]


# ── The cache, which a table leans on ────────────────────────────────────────


def test_the_same_icon_is_drawn_once_and_handed_back_after_that():
    """A results table repaints these on every scroll of every row."""
    IC.forget()
    first = IC.icon("check", tone="success", size="sm")
    assert isinstance(first, QIcon)
    assert IC.icon("check", tone="success", size="sm") is first
    assert len(IC._PIXMAPS) == 1 and len(IC._ICONS) == 1

    for changed in (dict(tone="danger"), dict(size="md"), dict(name="warning")):
        call = dict(name="check", tone="success", size="sm")
        call.update(changed)
        assert IC.icon(call.pop("name"), **call) is not first
    assert len(IC._PIXMAPS) == 4


def test_a_change_of_palette_does_not_hand_back_yesterday_s_colour():
    """The cache key carries the colour, so a theme switch invalidates exactly
    the entries it should and keeps the ones it should."""
    IC.forget()
    IC.use_theme(TH.theme("dark"))
    dark = IC.pixmap("gear", tone="primary")
    IC.use_theme(TH.theme("light"))
    light = IC.pixmap("gear", tone="primary")
    assert light is not dark
    assert len(IC._PIXMAPS) == 2

    IC.use_theme(TH.theme("dark"))
    assert IC.pixmap("gear", tone="primary") is dark
    assert len(IC._PIXMAPS) == 2


def test_forget_empties_both_caches():
    IC.pixmap("mail")
    IC.icon("mail")
    assert IC._PIXMAPS and IC._ICONS
    IC.forget()
    assert not IC._PIXMAPS and not IC._ICONS

