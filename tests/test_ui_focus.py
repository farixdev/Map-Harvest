"""Offline tests for the two marks the sheet uses to say "here".

Keyboard focus and list selection are both state, and state is only real if it
is painted, so every assertion here counts pixels that changed between two grabs
of the same widget rather than looking for a rule in `ui.app.QSS`. A rule proves
nothing on its own: the focus defect was a sheet that read as complete, with
`QLineEdit:focus` in it, sitting over a Qt painting path that drops the focus
rect the moment a QPushButton is given a background colour.

The measurements hold whether or not `QT_QPA_FONTDIR` is set, so they mean the
same thing on a machine that paints glyphs offscreen and on one that does not:
a label is identical in both grabs of a pair and so drops out of the difference.
The only place glyphs show up at all is the handful of antialiased edge pixels a
lighter hover fill pushes to white, which is why that comparison is a ratio.

Widgets built here are built once and reused. Qt on the offscreen platform does
not survive a test that churns top-level windows while it still points at one of
them as the active window, which is why there is a single bench rather than a
fresh host per button.

`tests/conftest.py` has already pointed `core.settings` and `core.templates` at a
temp profile by the time this imports, so building a screen cannot read or write
a real ~/.mapharvest.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QEvent, QPoint, QSize, Qt  # noqa: E402
from PyQt5.QtGui import QImage, QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QListWidget, QPushButton, QWidget,
)

from ui import app as APP  # noqa: E402
from ui.screen_input import InputScreen  # noqa: E402
from ui.screen_results import ResultsScreen  # noqa: E402
from ui.screen_settings import SettingsScreen  # noqa: E402

DEFAULT_SIZE, MINIMUM_SIZE = (1080, 760), (880, 620)

# Every button the sheet paints differently. The empty name is the plain
# QPushButton the rest of them inherit from.
VARIANTS = ("", "outlined", "danger", "tab", "start_btn", "live", "rehearsal",
            "reveal")

_WHITE = b"\xff\xff\xff\xff"
_APP = None
_BENCH: dict = {}
_SCREENS: dict = {}


def _app() -> QApplication:
    """The one QApplication for this module, styled as `ui.app.run` styles it.

    Re-applied only when something else in the run has changed the sheet:
    `setStyleSheet` repolishes every widget alive in the process, and the sweep
    below asks for this app a few hundred times.
    """
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    if _APP.styleSheet() != APP.QSS:
        APP.install_style(_APP)
    return _APP


def _variant(name: str) -> QPushButton:
    button = QPushButton("Save changes")
    if name:
        button.setObjectName(name)
    button.setCheckable(name in ("tab", "reveal"))
    button.setFixedSize(QSize(140, 36))
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
        host.resize(QSize(150 * (len(VARIANTS) + 1), 60))
        host.show()
        QApplication.setActiveWindow(host)
        app.processEvents()
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


def _differs(before: QImage, after: QImage) -> tuple:
    """(changed pixels, of how many, how many of them came out pure white).

    Read out of the raw buffers rather than pixel by pixel: the screen sweep
    below compares a few hundred grabs, and `pixel()` on every one of them
    costs more than the rest of this file put together.
    """
    one = before.convertToFormat(QImage.Format_RGB32)
    two = after.convertToFormat(QImage.Format_RGB32)
    assert one.size() == two.size(), "%s against %s" % (one.size(), two.size())
    left = one.constBits().asstring(one.byteCount())
    right = two.constBits().asstring(two.byteCount())
    changed = white = 0
    for start in range(0, len(left), 4):
        end = start + 4
        if left[start:end] == right[start:end]:
            continue
        changed += 1
        if right[start:end] == _WHITE:
            white += 1
    return changed, one.width() * one.height(), white


def _screen(kind: str):
    if kind not in _SCREENS:
        app = _app()
        screen = {"input": InputScreen, "results": ResultsScreen,
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
    """
    _host, elsewhere = _bench()
    for name in VARIANTS:
        button = _BENCH[name]
        plain = _grab(button, elsewhere)
        changed, total, white = _differs(
            plain, _grab(button, elsewhere, focus=True))
        assert changed >= total // 20, (
            "%s changes %d of its %d pixels when it takes focus"
            % (name or "default", changed, total))
        assert white >= 150, (
            "%s turns %d pixels white on focus, which is not a ring anyone "
            "could see" % (name or "default", white))


def test_focus_cannot_be_mistaken_for_hover_or_checked():
    """A ring, against the two states the sheet already spends fills on.

    Both of those repaint the middle of a button, so the mark that means "the
    keyboard is here" cannot be read as "the pointer is here" or "this tab is
    the open one". The comparison is an order of magnitude rather than nothing
    at all because a lighter hover fill under white label text shifts a handful
    of antialiased edge pixels the whole way to white — which is real, and is
    about a hundredth of a ring.
    """
    _host, elsewhere = _bench()
    for name in VARIANTS:
        button = _BENCH[name]
        plain = _grab(button, elsewhere)
        focused = _differs(plain, _grab(button, elsewhere, focus=True))[2]
        assert focused > 0, "%s lost its focus mark" % (name or "default")

        states = [("hover", _differs(plain, _grab(button, elsewhere, hover=True)))]
        if button.isCheckable():
            states.append(
                ("checked", _differs(plain, _grab(button, elsewhere, checked=True))))
        for state, (changed, _total, white) in states:
            assert changed > 0, \
                "%s no longer shows %s at all" % (name or "default", state)
            assert white * 10 <= focused, (
                "%s turns %d pixels white on %s against %d on focus, which is "
                "close enough to read as the same mark"
                % (name or "default", white, state, focused))


def test_no_button_on_any_screen_is_silent_about_focus():
    """The same thing on the real screens, at both window sizes.

    The variants above are built by hand; these are the buttons a user tabs
    through, laid out by the screens that own them and at the two sizes
    `MainWindow` allows.
    """
    for kind in ("input", "results", "settings"):
        screen = _screen(kind)
        for size in (DEFAULT_SIZE, MINIMUM_SIZE):
            _sized(screen, size)
            buttons = _focusable(screen)
            assert len(buttons) > 1, \
                "%s has nothing to tab through at %dx%d" % ((kind,) + size)
            for button in buttons:
                elsewhere = next(other for other in buttons if other is not button)
                changed, total, white = _differs(
                    _grab(button, elsewhere),
                    _grab(button, elsewhere, focus=True))
                assert white >= 40 and changed >= total // 40, (
                    "%r on %s at %dx%d changes %d of %d pixels and %d white "
                    "ones when it takes focus"
                    % ((button.text(), kind) + size + (changed, total, white)))


# ── U10: which row is selected has to be visible as a shape ──────────────────

_LIST_GROUND, _COMPONENT, _AA = "#242426", 3.0, 4.5


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


def _listing() -> QListWidget:
    """A saved-searches list whose rows differ in nothing but their state."""
    if "list" not in _BENCH:
        app = _app()
        listing = QListWidget()
        listing.setObjectName("saved_list")
        listing.setMouseTracking(True)
        for _ in range(5):
            listing.addItem("Roofing contractors — Toronto")
        listing.setFixedSize(QSize(240, 160))
        listing.show()
        app.processEvents()
        _BENCH["list"] = listing
    listing = _BENCH["list"]
    listing.setCurrentRow(-1)
    listing.clearSelection()
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
    """
    listing = _listing()
    row = _row(listing, 2)
    ground = _fill(listing, row)
    assert ground == _LIST_GROUND, \
        "an unselected row paints %s, so the ratios below mean nothing" % ground

    listing.setCurrentRow(2)
    _app().processEvents()
    fill = _fill(listing, row)
    assert _contrast(fill, ground) >= _COMPONENT, (
        "a selected row paints %s on %s — %.2f:1"
        % (fill, ground, _contrast(fill, ground)))
    assert _contrast("#FFFFFF", fill) >= _AA, (
        "the label on a selected row is %.2f:1 on its own fill"
        % _contrast("#FFFFFF", fill))


def test_the_selected_row_carries_a_rail_the_others_do_not():
    """The fill clears 3:1 with little to spare, so a rail carries the rest.

    Measured down the first few columns of the row, where a `border-left` lands
    and where nothing else in this list ever paints.
    """
    listing = _listing()
    row = _row(listing, 2)
    # The leftmost 6px of the row: wide enough for a 3px rail and the pixels
    # either side of it, too narrow for any label to reach into.
    edge = row.adjusted(0, 0, 6 - row.width(), 0)

    assert _counts(listing, edge).get("#FFFFFF", 0) == 0, \
        "an unselected row already paints the rail"

    listing.setCurrentRow(2)
    _app().processEvents()
    rail = _counts(listing, edge).get("#FFFFFF", 0)
    assert rail >= 2 * (row.height() - 6), (
        "the selected row paints %d rail pixels down %d of height"
        % (rail, row.height()))
    assert _contrast("#FFFFFF", _LIST_GROUND) >= _COMPONENT


def test_the_pointer_does_not_wipe_the_selection_out_from_under_itself():
    """`::item:hover` matches a selected row too, and used to win.

    It carries the same weight as `::item:selected` and sits later in the
    sheet, so running the pointer down the list repainted whichever row was
    picked in #2C2C2E — 1.11:1 on the list — at exactly the moment the user was
    choosing one.
    """
    listing = _listing()
    row = _row(listing, 2)
    listing.setCurrentRow(2)
    _app().processEvents()
    picked = _fill(listing, row)

    _point_at(listing, row)
    under_pointer = _fill(listing, row)
    assert under_pointer == picked, (
        "the selected row paints %s on its own and %s under the pointer"
        % (picked, under_pointer))
    assert _contrast(under_pointer, _LIST_GROUND) >= _COMPONENT, (
        "the selected row is %.2f:1 while the pointer is on it"
        % _contrast(under_pointer, _LIST_GROUND))
