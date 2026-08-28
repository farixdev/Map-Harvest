"""Offline tests for the two marks the sheet uses to say "here".

Keyboard focus and list selection are both state, and state is only real if it
is painted, so every assertion here counts pixels that changed between two grabs
of the same widget rather than looking for a rule in the sheet. A rule proves
nothing on its own: the focus defect was a sheet that read as complete, with
`QLineEdit:focus` in it, sitting over a Qt painting path that drops the focus
rect the moment a QPushButton is given a background colour.

What every expected colour, width and ratio is read from is `ui.theme`. The
values that used to be spelled out here — a white ring, a #242426 list — were
the previous design, and a test that pins a colour cannot survive a theme
change. The app now has two palettes, so every assertion about a ratio or an
ordering runs against both of them.

Two of those assertions are inverted from what they used to say, and
deliberately. The ring is no longer white: `docs/DESIGN_SYSTEM.md` measures the
old white ring at 17.01:1 against a selected tab at 1.50:1 and rules that no
focus treatment may ever outweigh the selection beside it, so the ring is now a
1px `accent.border` — 3.70:1 on the page in dark, 4.81:1 in light — and white on
a focused control is now the failure rather than the pass. The rail on a
selected row is `accent.default` for the same reason.

The measurements hold whether or not `QT_QPA_FONTDIR` is set, so they mean the
same thing on a machine that paints glyphs offscreen and on one that does not:
a label is identical in both grabs of a pair and so drops out of the difference.

Widgets built here are built once and reused. Qt on the offscreen platform does
not survive a test that churns top-level windows while it still points at one of
them as the active window, which is why there is a single bench rather than a
fresh host per button.

`tests/conftest.py` has already pointed `core.settings` and `core.templates` at a
temp profile by the time this imports, so building a screen cannot read or write
a real ~/.leadforge — `core.outreach_db` resolves its own path through
`core.settings.SETTINGS_DIR` on every call and is redirected by the same fixture.
"""
import contextlib
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QEvent, QPoint, QRect, QSize, Qt  # noqa: E402
from PyQt5.QtGui import QImage, QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QListWidget, QPushButton, QWidget,
)

from ui import theme as TH  # noqa: E402
from ui import screen_outreach as SO  # noqa: E402
from ui.screen_input import InputScreen  # noqa: E402
from ui.screen_results import ResultsScreen  # noqa: E402
from ui.screen_settings import SettingsScreen  # noqa: E402

THEME = TH.theme()

# Both palettes, because every ratio below is a claim about a relationship and a
# relationship that only holds in the dark theme is half a contract.
THEMES = (TH.theme("dark"), TH.theme("light"))

DEFAULT_SIZE, MINIMUM_SIZE = (1080, 760), (880, 620)

# Every button the sheet paints differently. The empty name is the plain
# QPushButton the rest of them inherit from.
VARIANTS = ("", "outlined", "danger", "tab", "start_btn", "live", "rehearsal",
            "reveal")

# Bench geometry, and deliberately not tokens. These exist to give a
# measurement a frame that does not move: a ring counted against one side of a
# button, or a row counted against the list under it, means the same number
# from run to run only while the frame is fixed. Sizing the bench from
# `control` would hand the density switch a say in what the tests measure.
_BENCH_BUTTON, _BENCH_LIST = QSize(140, 36), QSize(240, 160)

_APP = None
_BENCH: dict = {}
_SCREENS: dict = {}

# Which palette the process is wearing right now. Held in a variable rather
# than worked out from the sheet because every helper below calls `_app()` on
# its way past, and one that restored the default would strip the light theme
# off the widget between the grab that set it and the grab that measured it.
_WEARING = THEME


def _app(theme=None) -> QApplication:
    """The one QApplication for this module, styled as `ui.app.run` styles it.

    Re-applied only when something else in the run has left a different sheet
    on it: `setStyleSheet` repolishes every widget alive in the process, and the
    sweeps below ask for this app a few hundred times.
    """
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    wanted = theme or _WEARING
    if _APP.styleSheet() != TH.stylesheet(wanted):
        TH.apply(_APP, wanted)
    return _APP


@contextlib.contextmanager
def _wearing(theme):
    """The whole process in `theme`, and back to the default afterwards.

    Every widget this module has built is repolished on the way in and on the
    way out, which is the point: the bench and the screens have to be measured
    in the palette under test, not in the one they were constructed under.
    """
    global _WEARING
    saved, _WEARING = _WEARING, theme
    try:
        yield _app(theme)
    finally:
        _WEARING = saved
        _app()


# ── What the sheet says the two marks are ────────────────────────────────────


def _sheet(theme) -> str:
    return re.sub(r"/\*.*?\*/", "", TH.stylesheet(theme), flags=re.S)


def _rules(theme) -> list:
    """(selector, body) for every rule in `theme`'s sheet, in source order."""
    return [(" ".join(selector.split()), body)
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}",
                                             _sheet(theme))]


def _ring_inks(theme) -> dict:
    """objectName -> the colour that button's focus ring is painted in.

    Read out of the generated sheet rather than assumed to be one token,
    because it is not quite one: `#start_btn` rests on `accent.border` already —
    the rim that keeps a filled accent button findable on the page — so its ring
    steps to `accent.subtle` instead, and a ring painted in the colour the
    border already was is the defect, not the rule. The empty key is the plain
    QPushButton. Later rules win, which is Qt's own tie-break on equal
    specificity.
    """
    inks = {}
    for selectors, body in _rules(theme):
        found = re.search(r"\bborder:\s*\d+px\s+\w+\s+(#[0-9A-Fa-f]{6})", body)
        if found is None:
            continue
        for selector in selectors.split(","):
            named = re.fullmatch(r"\s*QPushButton(?:#(\w+))?:focus\s*",
                                 selector)
            if named:
                inks[named.group(1) or ""] = found.group(1).upper()
    return inks


def _rail(theme) -> tuple:
    """(colour, width) of the rail the sheet gives a selected list row."""
    for selectors, body in _rules(theme):
        if "saved_list" not in selectors or "::item:selected" not in selectors:
            continue
        found = re.search(r"border-left:\s*(\d+)px\s+solid\s+(#[0-9A-Fa-f]{6})",
                          body)
        if found:
            return found.group(2).upper(), int(found.group(1))
    raise AssertionError("no rail on a selected row in the %s sheet" % theme.name)


def _variant(name: str) -> QPushButton:
    button = QPushButton("Save changes")
    if name:
        button.setObjectName(name)
    button.setCheckable(name in ("tab", "reveal"))
    button.setFixedSize(_BENCH_BUTTON)
    return button


def _bench() -> tuple:
    """One page carrying a button of every variant, and somewhere else to look.

    The spare button is what makes "unfocused" a state a button can be measured
    in: park the keyboard on a widget that is not the one being grabbed.
    """
    if "host" not in _BENCH:
        app = _app()
        host = QWidget()
        row = QHBoxLayout(host)
        for name in VARIANTS:
            button = _variant(name)
            _BENCH[name] = button
            row.addWidget(button)
        elsewhere = QPushButton("elsewhere")
        _BENCH["elsewhere"] = elsewhere
        row.addWidget(elsewhere)
        _BENCH["host"] = host
        host.resize(QSize((_BENCH_BUTTON.width() + 10)
                          * (len(VARIANTS) + 1),
                          _BENCH_BUTTON.height() + 24))
        host.show()
    # Re-activated on every call, not only on the first: `setFocus` inside an
    # inactive window records where the focus would go if that window ever got
    # it and paints nothing, which reads exactly like a ring that is missing.
    QApplication.setActiveWindow(_BENCH["host"])
    _app().processEvents()
    return _BENCH["host"], _BENCH["elsewhere"]


def _grab(widget, elsewhere, focus=False, hover=False, checked=False) -> QImage:
    """`widget` painted in one state, with the keyboard parked where it belongs."""
    widget.setAttribute(Qt.WA_UnderMouse, hover)
    if widget.isCheckable():
        widget.setChecked(checked)
    (widget if focus else elsewhere).setFocus(Qt.TabFocusReason)
    _app().processEvents()
    assert widget.hasFocus() == focus, \
        "the keyboard would not go where this measurement needs it"
    return widget.grab().toImage()


def _packed(colour: str) -> bytes:
    """One `#RRGGBB` as the four bytes `Format_RGB32` stores it, little-endian."""
    r, g, b = (int(colour[start:start + 2], 16) for start in (1, 3, 5))
    return bytes((b, g, r, 0xFF))


def _differs(before: QImage, after: QImage, ink: str) -> tuple:
    """(changed pixels, of how many, how many came out exactly `ink`).

    Read out of the raw buffers rather than pixel by pixel: the screen sweep
    below compares a few hundred grabs, and `pixel()` on every one of them
    costs more than the rest of this file put together.
    """
    one = before.convertToFormat(QImage.Format_RGB32)
    two = after.convertToFormat(QImage.Format_RGB32)
    assert one.size() == two.size(), "%s against %s" % (one.size(), two.size())
    left = one.constBits().asstring(one.byteCount())
    right = two.constBits().asstring(two.byteCount())
    wanted = _packed(ink)
    changed = marked = 0
    for start in range(0, len(left), 4):
        end = start + 4
        if left[start:end] == right[start:end]:
            continue
        changed += 1
        if right[start:end] == wanted:
            marked += 1
    return changed, one.width() * one.height(), marked


def _screen(kind: str):
    if kind not in _SCREENS:
        app = _app()
        screen = {"input": InputScreen, "results": ResultsScreen,
                  "outreach": SO.OutreachScreen,
                  "settings": SettingsScreen}[kind]()
        screen.resize(QSize(*DEFAULT_SIZE))
        screen.show()
        app.processEvents()
        _SCREENS[kind] = screen
    return _SCREENS[kind]


def _sized(screen, size):
    """`screen` laid out at `size` and holding the keyboard, layout run."""
    app = _app()
    QApplication.setActiveWindow(screen)
    screen.resize(QSize(*size))
    if screen.layout() is not None:
        screen.layout().activate()
    app.processEvents()
    app.processEvents()
    return screen


def _focusable(screen) -> list:
    """Every button on `screen` a Tab key could actually land on."""
    return [button for button in screen.findChildren(QPushButton)
            if button.isVisible() and button.isEnabled()
            and button.focusPolicy() != Qt.NoFocus
            and button.width() > 8 and button.height() > 8]


# ── U9: a keyboard user has to be able to see where they are ─────────────────

def test_every_button_variant_says_when_it_holds_focus():
    """The defect, on one button of every kind the sheet paints.

    Nothing marked a focused button, and nothing could: naming a background
    colour on QPushButton moves it onto Qt's stylesheet painting path, which
    never asks Fusion for the focus rect it would otherwise draw. Measured
    across the four screens, all 72 buttons came out identical pixel for pixel
    focused and unfocused — Stop and Start sending among them.

    The ink is now the ring token for that variant rather than white, and it is
    read out of the sheet so the two cannot drift apart. `#start_btn` is the
    reason the ink is looked up per variant instead of assumed: it rests on
    `accent.border`, so a ring in `accent.border` repainted its border in the
    colour it already was and changed exactly zero of its 5,800 pixels.
    """
    _host, elsewhere = _bench()
    for theme in THEMES:
        with _wearing(theme):
            inks = _ring_inks(theme)
            for name in VARIANTS:
                button = _BENCH[name]
                ink = inks[name]
                plain = _grab(button, elsewhere)
                changed, total, marked = _differs(
                    plain, _grab(button, elsewhere, focus=True), ink)
                where = "%s %s" % (theme.name, name or "default")
                # Lengths, not shares of the area. The ring is 1px of border,
                # so what it can possibly light is the perimeter, and the
                # corners cost a fixed handful of blended pixels whatever the
                # control measures — a fraction of w*h is a floor that grows
                # with the wrong number and tightens as the button shrinks.
                # `#tab` is the tightest of the eight, because its 2px bottom
                # rail stays transparent in both states so only three sides
                # ever light: 218 changed against a 176px half-perimeter, and
                # 192 of ink against a 140px longest side.
                assert changed >= button.width() + button.height(), (
                    "%s changes %d of its %d pixels when it takes focus"
                    % (where, changed, total))
                assert marked >= max(button.width(), button.height()), (
                    "%s paints %d pixels of %s on focus and its longest side "
                    "is %d, so less than one side of it is ringed"
                    % (where, marked, ink, max(button.width(), button.height())))


def test_the_focus_ring_is_never_white_and_never_outshines_the_selection():
    """The reversal, and the reason for it, measured on every variant.

    This used to assert that at least 150 pixels of a focused button came out
    pure white. `docs/DESIGN_SYSTEM.md` measured that ring at 17.01:1 on the
    page beside a selected tab at 1.50:1 — eleven times the signal it competed
    with — and rules that focus is subordinate: a 1px `accent.border` ring,
    never white, never 2px, never brighter than the selection beside it. So the
    assertion is inverted. A white pixel on a focused control is now the
    failure, the ring has to be the token the sheet names, and the token has to
    measure quieter than `surfaceActive` on every ground either can land on.
    """
    _host, elsewhere = _bench()
    for theme in THEMES:
        with _wearing(theme):
            inks, white = _ring_inks(theme), theme.color["text.onAccent"]
            for name in VARIANTS:
                button = _BENCH[name]
                plain = _grab(button, elsewhere)
                lit = _grab(button, elsewhere, focus=True)
                where = "%s %s" % (theme.name, name or "default")

                _changed, _total, bleached = _differs(plain, lit, white)
                assert bleached == 0, (
                    "%s turns %d pixels %s when it takes the focus — the ring "
                    "is white again" % (where, bleached, white))

                changed, _total, marked = _differs(plain, lit, inks[name])
                assert marked > changed * 0.7, (
                    "%s paints %d of its %d changed pixels in %s, so the ring "
                    "is some other colour" % (where, marked, changed, inks[name]))

            for ground in ("canvas", "surface", "surfaceHover", "raised",
                           "inset"):
                for ink in set(inks.values()):
                    ring = _contrast(ink, theme.color[ground])
                    picked = _contrast(theme.color["surfaceActive"],
                                       theme.color[ground])
                    assert ring <= picked, (
                        "%s: a %s ring is %.2f:1 on %s and the selection it "
                        "sits beside is %.2f:1"
                        % (theme.name, ink, ring, ground, picked))


def test_focus_cannot_be_mistaken_for_hover_or_checked():
    """A ring, against the two states the sheet already spends fills on.

    Both of those repaint the middle of a button, so the mark that means "the
    keyboard is here" cannot be read as "the pointer is here" or "this tab is
    the open one". The comparison is an order of magnitude rather than nothing
    at all because a fill can always blend a stray pixel into the ring's own
    colour at an antialiased corner — which is real, and is about a hundredth
    of a ring.
    """
    _host, elsewhere = _bench()
    for theme in THEMES:
        with _wearing(theme):
            inks = _ring_inks(theme)
            for name in VARIANTS:
                button, ink = _BENCH[name], inks[name]
                where = "%s %s" % (theme.name, name or "default")
                plain = _grab(button, elsewhere)
                focused = _differs(
                    plain, _grab(button, elsewhere, focus=True), ink)[2]
                assert focused > 0, "%s lost its focus mark" % where

                states = [("hover",
                           _differs(plain, _grab(button, elsewhere, hover=True),
                                    ink))]
                if button.isCheckable():
                    states.append(
                        ("checked",
                         _differs(plain, _grab(button, elsewhere, checked=True),
                                  ink)))
                for state, (changed, _total, marked) in states:
                    assert changed > 0, \
                        "%s no longer shows %s at all" % (where, state)
                    assert marked * 10 <= focused, (
                        "%s paints %d pixels of %s on %s against %d on focus, "
                        "which is close enough to read as the same mark"
                        % (where, marked, ink, state, focused))
            assert inks  # the sheet named at least one ring


def test_no_button_on_any_screen_is_silent_about_focus():
    """The same thing on the real screens, at both window sizes.

    The variants above are built by hand; these are the buttons a user tabs
    through, laid out by the screens that own them and at the two sizes
    `MainWindow` allows — 45x40 up to 496x44, which is why the floors are
    lengths taken off each button rather than the flat 40 pixels and 2.5% of
    area they replace. A 496px header button clears 40 white pixels with three
    of its four sides missing.
    """
    inks = _ring_inks(THEME)
    for kind in ("input", "results", "settings"):
        screen = _screen(kind)
        for size in (DEFAULT_SIZE, MINIMUM_SIZE):
            _sized(screen, size)
            buttons = _focusable(screen)
            assert len(buttons) > 1, \
                "%s has nothing to tab through at %dx%d" % ((kind,) + size)
            for button in buttons:
                elsewhere = next(other for other in buttons if other is not button)
                ink = inks.get(button.objectName(), inks[""])
                changed, _total, marked = _differs(
                    _grab(button, elsewhere),
                    _grab(button, elsewhere, focus=True), ink)
                side = max(button.width(), button.height())
                assert marked >= side and \
                    changed >= button.width() + button.height(), (
                        "%r on %s at %dx%d is %dx%d and changes %d pixels, %d "
                        "of them %s, when it takes focus"
                        % ((button.text(), kind) + size
                           + (button.width(), button.height(), changed, marked,
                              ink)))


# ── U10: which row is selected has to be visible as a shape ──────────────────

_COMPONENT, _AA = 3.0, 4.5


def _luminance(colour: str) -> float:
    channels = []
    for start in (1, 3, 5):
        value = int(colour[start:start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(one: str, other: str) -> float:
    first, second = _luminance(one), _luminance(other)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _listing(name: str = "saved_list") -> QListWidget:
    """A list whose rows differ in nothing but their state."""
    if name not in _BENCH:
        app = _app()
        listing = QListWidget()
        listing.setObjectName(name)
        listing.setMouseTracking(True)
        for _ in range(5):
            listing.addItem("Roofing contractors — Toronto")
        listing.setFixedSize(_BENCH_LIST)
        listing.show()
        app.processEvents()
        _BENCH[name] = listing
    listing = _BENCH[name]
    QApplication.setActiveWindow(listing)
    listing.setCurrentRow(-1)
    listing.clearSelection()
    listing.clearFocus()
    _app().sendEvent(listing.viewport(), QEvent(QEvent.Leave))
    _app().processEvents()
    return listing


def _row(listing, index: int):
    """Where a row sits, in the coordinates its own grab is measured in."""
    return listing.visualItemRect(listing.item(index))


def _counts(listing, rect) -> dict:
    """Every colour painted inside `rect` of the list's viewport, counted."""
    image = listing.viewport().grab().toImage()
    seen: dict = {}
    for y in range(max(0, rect.top()), min(image.height() - 1, rect.bottom()) + 1):
        for x in range(max(0, rect.left()), min(image.width() - 1, rect.right()) + 1):
            name = image.pixelColor(x, y).name().upper()
            seen[name] = seen.get(name, 0) + 1
    return seen


def _fill(listing, rect) -> str:
    """The colour a row is mostly painted in."""
    counts = _counts(listing, rect)
    return max(counts, key=counts.get)


def _point_at(listing, rect) -> None:
    centre = rect.center()
    _app().sendEvent(listing.viewport(), QMouseEvent(
        QEvent.MouseMove, QPoint(centre.x(), centre.y()),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    _app().processEvents()


def test_the_selected_row_is_a_ground_change_you_can_actually_see():
    """The defect: #3A3A3C on #242426, a 1.37:1 change of ground.

    WCAG 1.4.11 asks 3:1 of anything carrying state, and that fill sat so close
    to the list under it that the selection was left resting on the ink getting
    brighter — a difference nobody reads as "this is the one".

    The intent survives untouched; what changed is that the list's own ground is
    read from `color.inset` and the selected fill from `color.surfaceActive`
    instead of being spelled out here, so the assertion is about the ratio
    rather than about one palette's two hexes. Both palettes are measured:
    3.78:1 in dark, 11.04:1 in light.
    """
    for theme in THEMES:
        with _wearing(theme):
            listing = _listing()
            row = _row(listing, 2)
            ground = _fill(listing, row)
            assert ground == theme.color["inset"].upper(), (
                "%s: an unselected row paints %s and the list is %s, so the "
                "ratios below mean nothing"
                % (theme.name, ground, theme.color["inset"]))

            listing.setCurrentRow(2)
            _app().processEvents()
            fill = _fill(listing, row)
            assert fill == theme.color["surfaceActive"].upper(), (
                "%s: a selected row paints %s, not the selected ground %s"
                % (theme.name, fill, theme.color["surfaceActive"]))
            assert _contrast(fill, ground) >= _COMPONENT, (
                "%s: a selected row paints %s on %s — %.2f:1"
                % (theme.name, fill, ground, _contrast(fill, ground)))
            ink = theme.color["text.onAccent"]
            assert _contrast(ink, fill) >= _AA, (
                "%s: the label on a selected row is %.2f:1 on its own fill"
                % (theme.name, _contrast(ink, fill)))


def test_the_selected_row_carries_a_rail_the_others_do_not():
    """The fill clears 3:1 with little to spare, so a rail carries the rest.

    Measured down the first few columns of the row, where a `border-left` lands
    and where nothing else in this list ever paints.

    The rail used to be white and is now `accent.default`, for the reason the
    ring is no longer white: a mark that outshines the selection is the defect
    this system exists to fix, and the rail belongs to the selection. Its
    colour and its width are both read out of the generated sheet, so a rail
    the sheet stops drawing fails here rather than quietly passing against a
    constant nobody updated.
    """
    for theme in THEMES:
        with _wearing(theme):
            colour, width = _rail(theme)
            assert colour == theme.color["accent.default"].upper(), (
                "%s: the rail is %s and the accent is %s"
                % (theme.name, colour, theme.color["accent.default"]))

            listing = _listing()
            row = _row(listing, 2)
            # The leftmost 6px of the row: wide enough for the rail and the
            # pixels either side of it, too narrow for any label to reach into.
            edge = row.adjusted(0, 0, 6 - row.width(), 0)

            assert _counts(listing, edge).get(colour, 0) == 0, \
                "%s: an unselected row already paints the rail" % theme.name

            listing.setCurrentRow(2)
            _app().processEvents()
            rail = _counts(listing, edge).get(colour, 0)
            # The rounded ends eat a corner's worth of the rail at each end.
            floor = width * (row.height() - 2 * theme.radius["sm"])
            assert rail >= floor, (
                "%s: the selected row paints %d rail pixels down %d of height, "
                "and a %dpx rail on a %dpx row is %d"
                % (theme.name, rail, row.height(), width, row.height(), floor))
            ground = theme.color["inset"].upper()
            assert _delta_e(colour, ground) >= 2 * _JND, (
                "%s: the rail is delta-E %.1f from the list under it, which is "
                "two names for one colour"
                % (theme.name, _delta_e(colour, ground)))
            assert _delta_e(colour, theme.color["surfaceActive"]) >= 2 * _JND, (
                "%s: the rail is delta-E %.1f from the row it sits on"
                % (theme.name, _delta_e(colour, theme.color["surfaceActive"])))


def test_the_pointer_does_not_wipe_the_selection_out_from_under_itself():
    """`::item:hover` matches a selected row too, and used to win.

    It carries the same weight as `::item:selected` and sits later in the
    sheet, so running the pointer down the list repainted whichever row was
    picked in #2C2C2E — 1.11:1 on the list — at exactly the moment the user was
    choosing one.
    """
    for theme in THEMES:
        with _wearing(theme):
            listing = _listing()
            row = _row(listing, 2)
            listing.setCurrentRow(2)
            _app().processEvents()
            picked = _fill(listing, row)

            _point_at(listing, row)
            under_pointer = _fill(listing, row)
            assert under_pointer == picked, (
                "%s: the selected row paints %s on its own and %s under the "
                "pointer" % (theme.name, picked, under_pointer))
            ground = theme.color["inset"]
            assert _contrast(under_pointer, ground) >= _COMPONENT, (
                "%s: the selected row is %.2f:1 while the pointer is on it"
                % (theme.name, _contrast(under_pointer, ground)))


# ── The audit's critical finding, on the controls that actually carry it ─────

# CIE76. `ui/theme.py` builds both palettes so that no two tokens sit inside
# 2.0 of one another, which is the number below the two marks have to clear.
_JND = 2.0

# What the selection has to beat the ring by in painted pixels. The measured
# margins are 11x on the narrowest tab in the app and about 6x on a list row;
# 3x is the floor, and it is the same one `tests/test_theme.py` holds a pair of
# synthetic tabs to.
_SELECTION_MARGIN = 3


def _lab(colour: str) -> tuple:
    """CIE L*a*b* under D65, the space delta-E 76 is defined in."""
    def linear(value):
        return (value / 12.92 if value <= 0.03928
                else ((value + 0.055) / 1.055) ** 2.4)

    r, g, b = (linear(int(colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(one: str, other: str) -> float:
    return sum((a - b) ** 2 for a, b in zip(_lab(one), _lab(other))) ** 0.5


def _histogram(widget, rect=None) -> dict:
    """Every colour `widget` paints, counted — clipped to `rect` if given."""
    image = (widget.grab(rect) if rect is not None else widget.grab()).toImage()
    seen: dict = {}
    for y in range(image.height()):
        for x in range(image.width()):
            name = image.pixelColor(x, y).name().upper()
            seen[name] = seen.get(name, 0) + 1
    return seen


def _added(before: dict, after: dict) -> dict:
    """The colours a state painted that the resting state did not."""
    return {colour: count for colour, count in after.items()
            if count > before.get(colour, 0)}


def _outweighs(where, ground, picked, ring) -> None:
    """Selection has to beat focus on both counts a user actually perceives."""
    assert picked, "%s: selection paints nothing at all" % where
    assert ring, "%s: focus paints nothing at all" % where

    picked_ink = max(picked, key=picked.get)
    ring_ink = max(ring, key=ring.get)
    picked_px, ring_px = sum(picked.values()), sum(ring.values())

    assert picked_px >= _SELECTION_MARGIN * ring_px, (
        "%s: selection repaints %d px and focus %d — the ring is not "
        "subordinate" % (where, picked_px, ring_px))
    assert _contrast(picked_ink, ground) >= _contrast(ring_ink, ground), (
        "%s: selection paints %s at %.2f:1 on %s and focus %s at %.2f:1"
        % (where, picked_ink, _contrast(picked_ink, ground), ground, ring_ink,
           _contrast(ring_ink, ground)))
    assert _contrast(picked_ink, ground) >= _COMPONENT, (
        "%s: the selected state is %.2f:1 against the unselected one"
        % (where, _contrast(picked_ink, ground)))
    assert _delta_e(picked_ink, ring_ink) >= _JND, (
        "%s: selection paints %s and focus %s, delta-E %.2f apart — one mark "
        "wearing two names" % (where, picked_ink, ring_ink,
                               _delta_e(picked_ink, ring_ink)))


def _tabs(screen) -> list:
    return [button for button in screen.findChildren(QPushButton)
            if button.objectName() == "tab" and button.isCheckable()
            and button.isVisible()]


def _own_tabs(screen) -> list:
    """A screen's own tabs, on whichever of its pages still carries them.

    A screen's *steps* belong to the shell now — Outreach hands Leads, Campaign,
    Sending and Stats to `set_subtabs` and draws no strip of its own, and the
    bar that draws them is measured in `tests/test_ui_chrome.py`. What is left
    on the screen is an in-page selection: the Text/HTML switch over the email
    preview, which is exactly the mark this test is about, and which lives on
    the Campaign page rather than on the one the screen opens with.
    """
    tabs = _tabs(screen)
    pages = getattr(screen, "pages", None)
    if len(tabs) >= 2 or pages is None:
        return tabs
    for index in range(pages.count()):
        pages.setCurrentIndex(index)
        _sized(screen, DEFAULT_SIZE)
        tabs = _tabs(screen)
        if len(tabs) >= 2:
            return tabs
    return tabs


def test_selection_outweighs_focus_on_every_tab_and_every_row():
    """The audit's critical finding, guarded where it actually happened.

    "Focus ring 17.01:1, selected tab 1.50:1 — the focus ring is 11x the
    selection it competes with" is the one row of the audit table that drives a
    rule of its own in `docs/DESIGN_SYSTEM.md`, and until now nothing measured
    it on a real control. `tests/test_theme.py` holds one synthetic pair of tabs
    to it; this holds every tab on every screen and every row of both list
    variants the sheet paints, in both palettes.

    Three grabs of the same control against one resting grab — selected and
    unfocused, then focused and unselected — and selection has to win on the
    two counts a user perceives: how much of the control changes, and how far
    the change lands from the ground it changed from. Tabs are grabbed through
    their window because `#tab` is transparent when it is neither, so a grab of
    the button alone would measure both marks against an unpainted buffer
    rather than against the page.

    The tabs are the app's own, on the screens that own them. The rows are on a
    bench of this module's, because the only list a screen shows without one of
    its tabs being opened first holds a "nothing saved yet" placeholder with
    `NoItemFlags` — a row that cannot be selected proves nothing about the mark
    that says it is.

    Measured today: a selected tab repaints 1,171-2,753 px at 4.30:1 in dark
    and 6.22:1 in light, against a ring of 106-157 px at 3.70:1 and 4.81:1 —
    between 11x and 26x the pixels, and louder on every one of them.
    """
    # Every screen is asked and none is required to answer, which is the
    # navigation contract rather than a gap in this test. A screen's *steps*
    # belong to the shell now — Home's Scrape/Filters/Settings strip sat
    # directly under the shell's own destinations, two rows of identical tabs
    # with the second inside the page, and Outreach's four and Settings' seven
    # went the same way — so what is measured here is whatever in-page tab a
    # screen still owns, and the shell's own row is measured in
    # `tests/test_ui_chrome.py`. The counter at the end is what stops a future
    # migration turning this into a test that measures nothing and passes.
    measured = 0
    for theme in THEMES:
        with _wearing(theme) as app:
            for kind in ("input", "results", "outreach", "settings"):
                screen = _sized(_screen(kind), DEFAULT_SIZE)
                tabs = _own_tabs(screen)
                if len(tabs) < 2:
                    continue
                measured += 1
                park = next(button for button in _focusable(screen)
                            if button not in tabs)
                # Which tab the screen opened on, so the bar and the page it
                # controls are not left disagreeing for whatever runs next.
                opened = next((one for one in tabs if one.isChecked()), tabs[0])
                for tab in tabs:
                    spare = next(other for other in tabs if other is not tab)
                    box = QRect(tab.mapTo(screen, tab.rect().topLeft()),
                                tab.size())
                    where = "%s %s tab %r" % (theme.name, kind, tab.text())
                    # Blocked, not clicked: these are auto-exclusive, so the
                    # only way to leave one unchecked is to check another, and
                    # a real toggle would change the page under the grab.
                    for button in tabs:
                        button.blockSignals(True)
                    try:
                        spare.setChecked(True)
                        park.setFocus(Qt.TabFocusReason)
                        app.processEvents()
                        resting = _histogram(screen, box)
                        ground = max(resting, key=resting.get)

                        tab.setChecked(True)
                        app.processEvents()
                        picked = _added(resting, _histogram(screen, box))

                        spare.setChecked(True)
                        tab.setFocus(Qt.TabFocusReason)
                        app.processEvents()
                        ring = _added(resting, _histogram(screen, box))
                        tab.clearFocus()
                    finally:
                        for button in tabs:
                            button.blockSignals(False)
                    _outweighs(where, ground, picked, ring)

                opened.blockSignals(True)
                opened.setChecked(True)
                opened.blockSignals(False)
                app.processEvents()

            for name in ("saved_list", "service_list"):
                listing = _listing(name)
                for index in range(listing.count()):
                    where = "%s %s row %d" % (theme.name, name, index)
                    QApplication.setActiveWindow(listing)
                    listing.setCurrentRow(-1)
                    listing.clearSelection()
                    listing.clearFocus()
                    app.processEvents()
                    resting = _histogram(listing)
                    ground = max(resting, key=resting.get)

                    listing.setCurrentRow(index)
                    app.processEvents()
                    picked = _added(resting, _histogram(listing))

                    listing.setCurrentRow(-1)
                    listing.clearSelection()
                    listing.setFocus(Qt.TabFocusReason)
                    app.processEvents()
                    ring = _added(resting, _histogram(listing))
                    listing.clearFocus()
                    app.processEvents()
                    _outweighs(where, ground, picked, ring)

    assert measured, ("no screen carries an in-page tab any more, so the tab "
                      "half of this measured nothing at all")
