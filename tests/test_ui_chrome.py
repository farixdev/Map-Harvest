"""Offline tests for the chrome the four screens are made of.

Everything here is a rendering or geometry contract, so the assertions are
against pixels and measured widths rather than against the code that produced
them: a QSS rule can be beaten by a more specific one and a layout can hand a
widget less than it asked for, and in both cases the only honest witness is what
the screen actually shows.

Qt runs on the offscreen platform. With `QT_QPA_FONTDIR` unset it paints no
glyphs — a label renders as empty ground — but it does report real font metrics
either way, so widths, `sizeHint()` and `elidedText()` all mean what they say.
Every colour assertion is therefore either about a painted shape, or is scoped
to a rect no glyph can reach: the tick is measured inside the check indicator
alone, so a run with fonts installed and a run without measure the same thing.

Every colour, size and height an assertion expects is read from `ui.theme`.
This file used to spell out a #1C1C1E page, a #2C2C2E card, a white focus ring
and six button heights, and every one of them was one palette's answer to a
question the tokens now own; the app ships two palettes and two densities, so a
pinned hex is a test that will pass right up until the interface changes and
then describe one nobody sees. Where an assertion is a claim about a ratio or an
ordering it runs against both palettes.

The last section measures `ui.app` rather than a screen: one bar over a stack
of screens that are built the first time they are asked for, the routes between
them, and a theme that changes without a restart. It builds a real MainWindow,
so it is held to the same rule as everything else here — nothing it constructs
may reach a real profile.

`SETTINGS_DIR` is redirected into a temp directory before any screen is built,
so constructing one can never read or write a developer's real ~/.mapharvest —
`core.outreach_db` resolves its own path through it on every call, so the
database goes to the same place.
"""
import ast
import contextlib
import itertools
import os
import re
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Named here as well as in `tests/test_modals.py`, and the reason is a whole
# class of failure this file had without it. Every geometry assertion below is
# a width in the app's own font, and Qt reads this variable once, when the
# QApplication is constructed; with no fonts to load the offscreen platform
# falls back to a face whose metrics are wider, so a table that fits at 1280
# overflows and a label that elides stops eliding. In a whole-suite run an
# earlier module builds the QApplication and the metrics are whatever that
# module got, which is why `pytest tests/test_ui_chrome.py` on its own used to
# fail five geometry tests that `pytest tests/` passed. Setting it here makes
# the answer the same either way.
os.environ.setdefault(
    "QT_QPA_FONTDIR",
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import (  # noqa: E402
    QEvent, QObject, QPoint, QRect, QSize, Qt, QtMsgType,
    qInstallMessageHandler,
)
from PyQt5.QtGui import QFontMetrics, QKeyEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSplitter, QStyle, QStyleOptionButton,
    QStyleOptionViewItem, QWidget,
)

from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402
from core import templates as TPL  # noqa: E402
from core.campaign import OutreachWorker  # noqa: E402
from ui import app as APP  # noqa: E402
from ui import command_palette as CP  # noqa: E402
from ui import components as CO  # noqa: E402
from ui import theme as TH  # noqa: E402
from ui import screen_outreach as SO  # noqa: E402
from ui import domain_list_dialog as DLG  # noqa: E402
from ui import screen_settings as SS  # noqa: E402
from ui.screen_input import InputScreen  # noqa: E402
from ui.screen_results import ResultsScreen  # noqa: E402
from ui.screen_settings import SettingsScreen  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="mapharvest-ui-chrome-")
_APP = None
_SCREENS: dict = {}

THEME = TH.theme()

# Both palettes, for every assertion that is a claim about a relationship
# rather than about a widget's wiring.
THEMES = (TH.theme("dark"), TH.theme("light"))

# Which palette the process is wearing right now. Held in a variable rather than
# worked out from the sheet, because every helper here calls `_app()` on its way
# past and one that restored the default would strip the theme under test off
# the widget between the grab that set it and the grab that measured it.
_WEARING = THEME

ACCOUNT = "samantha.whitfield@gmail.com"
FROM_LINE = ("To Zeta Roofing <zeta@example.com>  ·  "
             "From Sam Whitfield <%s>" % ACCOUNT)
ADDRESS = "Suite 1200 - 12 King Street West\nToronto, ON M5H 1A1\nCanada"

# The two sizes every screen has to survive: what `MainWindow` opens at, and the
# minimum it will let the user drag down to.
DEFAULT_SIZE, MINIMUM_SIZE = (1080, 760), (880, 620)


def _app() -> QApplication:
    """The one QApplication for this module, styled exactly as `ui.app.run`.

    Through `theme.apply`, not by hand: the check indicators are painted by
    `TickStyle` and the sheet deliberately says nothing about them, so an app
    that only gets the sheet is not the app the user runs.

    It deliberately does *not* touch `components.use_theme`: the Appearance tab
    sets that itself while a density is being picked, and a helper every other
    helper calls on its way past would put it back a line later. `_screen` owns
    that half.
    """
    global _APP
    if _APP is None:
        ST.SETTINGS_DIR = _TMP
        ST.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
        _APP = QApplication.instance() or QApplication([])
    TH.apply(_APP, _WEARING)
    return _APP


@contextlib.contextmanager
def _wearing(theme):
    """The whole process in `theme`, and back to the default afterwards.

    `setStyleSheet` repolishes every widget alive in the process, which is the
    point: a screen built under one palette has to be measured in the one under
    test, not in the one it was constructed in. A repolish is no longer the
    whole of it — see `_screen`.
    """
    global _WEARING
    saved, _WEARING = _WEARING, theme
    try:
        yield _app()
    finally:
        _WEARING = saved
        _app()


# Which palette each built screen is currently wearing, so one is restyled at
# most once per palette it is actually measured in.
_WORN: dict = {}


def _screen(kind: str):
    """One built screen per kind, over a seeded throwaway database.

    Handed back wearing the palette the process is in, which takes the same
    three steps `MainWindow.apply_appearance` takes and not just the first:
    the sheet onto the application, the theme into `ui.components`, and
    `restyle()` on the screen. The third is what a repolish cannot do — a
    colour a component wrote into a widget's own stylesheet is not reachable
    from the application sheet at all, so without it a card built in the dark
    palette is still painting `#2A2E33` while the page around it is light.
    """
    app = _app()
    if kind not in _SCREENS:
        CO.use_theme(_WEARING)
        if kind == "outreach":
            conn = DB.connect(os.path.join(_TMP, "outreach.db"))
            DB.upsert_lead(conn, {"email": "zeta@example.com", "name": "Zeta Roofing",
                                  "opportunity_score": 30, "status": "audited",
                                  "source": "test"})
            screen = SO.OutreachScreen()
            screen.settings["smtp_accounts"] = [
                {"email": ACCOUNT, "display_name": "Sam Whitfield",
                 "enabled": True, "daily_cap": 10}]
            screen.settings["sender_profile"] = {"sender_name": "Sam Whitfield",
                                                 "postal_address": ADDRESS}
            screen._reload_leads()
        else:
            screen = {"input": InputScreen, "results": ResultsScreen,
                      "settings": SettingsScreen}[kind]()
        screen.resize(QSize(*DEFAULT_SIZE))
        screen.show()
        app.processEvents()
        _SCREENS[kind] = screen
        _WORN[kind] = _WEARING

    screen = _SCREENS[kind]
    if _WORN.get(kind) is not _WEARING:
        CO.use_theme(_WEARING)
        restyle = getattr(screen, "restyle", None)
        if callable(restyle):
            restyle()
            app.processEvents()
        _WORN[kind] = _WEARING
    return screen


def _sized(screen, size):
    """`screen` laid out at `size`, with the layout actually run."""
    app = _app()
    screen.resize(QSize(*size))
    if screen.layout() is not None:
        screen.layout().activate()
    app.processEvents()
    app.processEvents()
    return screen


def _histogram(widget, rect=None) -> dict:
    """Every colour `widget` paints inside `rect`, counted."""
    image = widget.grab().toImage()
    box = rect if rect is not None else image.rect()
    counts: dict = {}
    for y in range(max(0, box.top()), min(image.height() - 1, box.bottom()) + 1):
        for x in range(max(0, box.left()), min(image.width() - 1, box.right()) + 1):
            name = image.pixelColor(x, y).name().upper()
            counts[name] = counts.get(name, 0) + 1
    return counts


# Every colour `theme.TickStyle` puts inside a check indicator except the tick
# itself: the two fills, the three edges, and the dead grey.
_INDICATOR_COLOURS = ("inset", "accent.default", "surface", "border.subtle",
                      "border.default", "border.strong")


def _tick_floor(theme) -> int:
    """The channel value nothing but the tick can reach inside an indicator.

    One brighter than the brightest channel of anything else painted in there,
    so the count below cannot be padded by an edge or a fill — and antialiasing
    between two of those colours only ever lands between them, so it cannot
    reach the floor either. 191 in dark, 231 in light; the literal 200 this
    replaced happened to sit between the two and would have counted the light
    theme's own well as a tick.
    """
    return 1 + max(int(theme.color[name][start:start + 2], 16)
                   for name in _INDICATOR_COLOURS for start in (1, 3, 5))


def _tick_pixels(counts: dict, theme) -> int:
    """Pixels inside an indicator bright enough to be the tick and nothing else."""
    floor = _tick_floor(theme)
    return sum(count for colour, count in counts.items()
               if all(int(colour[start:start + 2], 16) >= floor
                      for start in (1, 3, 5)))


def _indicator_rect(box: QCheckBox):
    option = QStyleOptionButton()
    option.initFrom(box)
    return box.style().subElementRect(box.style().SE_CheckBoxIndicator, option, box)


def _row_indicator_rect(listing: QListWidget, index: int):
    """Where a checkable row's indicator sits, in viewport coordinates.

    Asked of the style rather than guessed at from the row, because the row is
    the whole width of the list and the label lives in it: a count taken over
    the row would score `text.secondary` — #C9D4E8, every channel over 200 — as
    a tick on every row that has any words in it.
    """
    option = QStyleOptionViewItem()
    option.initFrom(listing)
    option.rect = listing.visualItemRect(listing.item(index))
    option.features |= QStyleOptionViewItem.HasCheckIndicator
    return listing.style().subElementRect(
        QStyle.SE_ItemViewItemCheckIndicator, option, listing)


# ── U1: every checkbox must draw a tick ──────────────────────────────────────

def test_a_checked_box_paints_a_tick_and_an_unchecked_one_does_not():
    """The whole defect: checked and unchecked differed only in brightness.

    Measured inside the indicator alone, so the label cannot contribute, and
    against a floor computed from the palette rather than a fixed 200 — the
    light theme's own input well is #E0E3E6 and would have cleared that.
    """
    for theme in THEMES:
        with _wearing(theme) as app:
            box = QCheckBox("Dry run — build and log every email, send none")
            box.resize(box.sizeHint())
            box.show()

            marks = {}
            for state in (False, True):
                box.setChecked(state)
                app.processEvents()
                rect = _indicator_rect(box)
                assert rect.width() >= 12 and rect.height() >= 12, \
                    "there is no indicator left to measure: %s" % rect
                marks[state] = _tick_pixels(_histogram(box, rect), theme)

            assert marks[False] == 0, "%s: an unchecked box paints %d tick pixels" % (
                theme.name, marks[False])
            assert marks[True] >= 10, (
                "%s: a checked box paints only %d tick pixels — that is the "
                "tickless indicator again" % (theme.name, marks[True]))
            box.hide()


def test_a_checked_list_row_paints_the_same_tick():
    """The 21 'Data to scrape' boxes are list items, a different primitive."""
    for theme in THEMES:
        with _wearing(theme) as app:
            listing = QListWidget()
            listing.setObjectName("service_list")
            for label, state in (("Email", Qt.Unchecked), ("Phone", Qt.Checked)):
                item = QListWidgetItem(label)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(state)
                listing.addItem(item)
            listing.resize(220, 90)
            listing.show()
            app.processEvents()

            marks = []
            for index in (0, 1):
                rect = _row_indicator_rect(listing, index)
                assert rect.width() >= 12 and rect.height() >= 12, \
                    "there is no row indicator left to measure: %s" % rect
                marks.append(_tick_pixels(
                    _histogram(listing.viewport(), rect), theme))

            assert marks[0] == 0, "%s: an unchecked row paints %d tick pixels" % (
                theme.name, marks[0])
            assert marks[1] >= 10, "%s: a checked row paints only %d tick pixels" % (
                theme.name, marks[1])
            listing.hide()


def test_the_sheet_leaves_the_indicator_to_the_style():
    """A single `::indicator` rule anywhere would beat `TickStyle` again."""
    for theme in THEMES:
        rules = [line for line in _sheet(theme).splitlines()
                 if "::indicator" in line]
        assert rules == [], "the %s sheet styles an indicator again: %s" % (
            theme.name, rules)


def test_the_dry_run_toggle_itself_draws_a_tick():
    """The safety switch, on the real screen, at both window sizes."""
    screen = _screen("settings")
    box = screen.dry_run_cb
    # Unchecking it opens a confirmation dialog; this test is about paint.
    box.blockSignals(True)
    try:
        for theme in THEMES:
            with _wearing(theme) as app:
                for size in (DEFAULT_SIZE, MINIMUM_SIZE):
                    _sized(screen, size)
                    marks = {}
                    for state in (False, True):
                        box.setChecked(state)
                        app.processEvents()
                        marks[state] = _tick_pixels(
                            _histogram(box, _indicator_rect(box)), theme)
                    assert marks[False] == 0 and marks[True] >= 10, (
                        "%s: dry run reads the same either way at %dx%d: %s"
                        % ((theme.name,) + size + (marks,)))
    finally:
        box.setChecked(True)
        box.blockSignals(False)


# ── U2: the To/From line must elide, not clip ────────────────────────────────

def test_an_elided_label_shortens_and_keeps_the_whole_thing_in_a_tooltip():
    """Driven by width alone, off any layout that could resize it back."""
    app = _app()
    label = SO._ElidedLabel(FROM_LINE)
    # Shown, because Qt holds a hidden widget's resize event back until it is.
    label.show()
    app.processEvents()
    label.resize(QSize(label.sizeHint().width() + 40, 17))
    app.processEvents()
    assert label.text() == FROM_LINE, "elided a line that had room: %r" % label.text()

    label.resize(QSize(120, 17))
    app.processEvents()
    assert label.text() != FROM_LINE, "the line is still being clipped, not elided"
    assert label.text().endswith("…"), \
        "a shortened line must say so: %r" % label.text()
    assert label.toolTip() == FROM_LINE, \
        "the sending account has to stay readable somewhere"
    assert label.fullText() == FROM_LINE


def test_the_campaign_from_line_elides_in_place_at_the_window_minimum():
    """Font-independent on purpose: it used to depend on one name being too wide.

    The line it measured overflows in an offscreen run with no fonts installed
    and fits once real ones are, so the test passed on a bare machine, failed on
    a developer's, and proved nothing on either — the machine it has to hold for
    is the one the app ships to, and nobody knows what is installed there. So
    the line is built to overflow at any metric, and what is asserted is the
    behaviour: the label shortens whatever it is given to something that fits
    inside the column, says it has done so, and keeps the whole line in the
    tooltip.
    """
    screen = _screen("outreach")
    for size in (DEFAULT_SIZE, MINIMUM_SIZE):
        _sized(screen, size)
        screen._goto_tab(1)
        label = screen.preview_meta
        _sized(screen, size)

        line = FROM_LINE
        while label.fontMetrics().width(line) <= 4 * max(1, label.width()):
            line = "%s  ·  %s" % (line, FROM_LINE)
        label.setText(line)
        _sized(screen, size)

        assert label.toolTip() == line, \
            "the line that names the sending account has no tooltip at %dx%d" % size
        assert label.fullText() == line, "the whole line is no longer kept at %dx%d" % size
        assert label.text() != line, \
            "a line four times the label's width was not shortened at %dx%d" % size
        assert label.text().endswith("…"), (
            "%dx%d shows %r with no cue that it was cut"
            % (size + (label.text(),)))
        assert label.fontMetrics().width(label.text()) <= label.width(), (
            "the shortened line is %dpx of %dpx at %dx%d, so it is still being "
            "clipped" % ((label.fontMetrics().width(label.text()), label.width())
                         + size))
        assert label.width() <= label.parentWidget().width(), (
            "the line widened the column it sits in at %dx%d, which is how it "
            "pushes everything beside it off the page" % size)

    screen.preview_meta.setText(FROM_LINE)
    _sized(screen, DEFAULT_SIZE)


# ── U3: the Campaign left column must adapt, not clip ────────────────────────

def _campaign_column(screen):
    """The widget the Campaign tab's cards are laid out on.

    Reached through a button that is unarguably in it rather than by attribute
    name, so these tests measure the column wherever it ends up living — which
    is the whole point: the fix moved it inside a scroll area, and an assertion
    phrased in terms of that scroll area could only ever confirm its own
    existence.
    """
    schedule_card = screen.prepare_btn.parentWidget()
    assert schedule_card.objectName() == "card", \
        "Prepare campaign is no longer on a card: %r" % schedule_card
    return schedule_card.parentWidget()


def _laid_out_campaign(screen, size):
    """The Campaign tab at `size`, with a three-line plan and both buttons up."""
    _sized(screen, size)
    screen._goto_tab(1)
    screen.plan_summary.setText("43 messages over 4 days.<br>"
                                "First goes out Mon 9:14 AM.<br>"
                                "Last message lands Thu 12:00 AM")
    screen.goto_sending_btn.show()
    _sized(screen, size)
    return _campaign_column(screen)


def _scroll_ancestor(widget):
    """The scroll area `widget` lives in, if any."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


def test_nothing_in_the_campaign_column_is_placed_out_of_reach():
    """The defect, stated as the invariant it broke.

    The column asked for more height than the row had and was handed less, so
    its bottom simply fell off the page: the plan's last line disappeared and
    the two buttons were pushed into each other, with nothing on screen saying
    so. Nothing in the column can be made shorter, so the only honest outcomes
    are 'it fits' or 'it scrolls' — never 'it is quietly cut'.
    """
    screen = _screen("outreach")
    for size in (DEFAULT_SIZE, MINIMUM_SIZE):
        column = _laid_out_campaign(screen, size)
        beyond = column.height() - column.parentWidget().height()
        area = _scroll_ancestor(column)
        reachable = area.verticalScrollBar().maximum() if area is not None else 0
        assert beyond <= reachable, (
            "%dpx of the campaign column hangs past the bottom of what holds "
            "it at %dx%d and only %d of that can be scrolled to"
            % ((beyond,) + size + (reachable,)))

        overlap = screen.prepare_btn.geometry().intersected(
            screen.goto_sending_btn.geometry())
        assert not overlap.isValid() or overlap.width() * overlap.height() == 0, \
            "Prepare campaign overlaps Open Sending at %dx%d" % size


def test_the_campaign_column_scrolls_exactly_its_overflow():
    screen = _screen("outreach")

    column = _laid_out_campaign(screen, MINIMUM_SIZE)
    area = _scroll_ancestor(column)
    assert area is not None, "the campaign column cannot scroll at all"
    overflow = column.height() - area.viewport().height()
    assert overflow > 0, (
        "the column no longer overflows at the window minimum, so the "
        "assertion below proves nothing")
    assert area.verticalScrollBar().maximum() == overflow, (
        "%dpx of the column is off the bottom and the scrollbar reaches %d"
        % (overflow, area.verticalScrollBar().maximum()))
    assert area.horizontalScrollBar().maximum() == 0, \
        "nothing may be pushed off sideways"

    column = _laid_out_campaign(screen, DEFAULT_SIZE)
    assert column.height() <= _scroll_ancestor(column).viewport().height(), \
        "the column overflows even at the default size"


# ── U4: the Accounts card during a rehearsal ─────────────────────────────────

def test_a_dry_run_shows_on_the_accounts_card_without_spending_quota():
    screen = _screen("outreach")
    app = _app()
    screen._goto_tab(2)
    screen._rehearsed.clear()
    screen._refresh_accounts()
    app.processEvents()

    def card():
        holder = screen.accounts_holder.itemAt(0).widget()
        counter = [lb for lb in holder.findChildren(QLabel) if "today" in lb.text()][0]
        bar = holder.findChildren(QProgressBar)[0]
        return counter.text(), bar.value(), counter.toolTip()

    before, before_bar, _ = card()
    assert "rehearsed" not in before and "0 / 10 today" in before

    worker = OutreachWorker(0, {"dry_run": True}, dry_run=True)
    screen.send_worker = worker
    try:
        for _ in range(2):
            screen._on_message_sent({"lead_id": 0, "step": 0, "account_email": ACCOUNT})
    finally:
        screen.send_worker = None
    app.processEvents()

    after, after_bar, tip = card()
    assert "2 rehearsed" in after, "the card said nothing about the rehearsal: %r" % after
    assert "0 / 10 today" in after, \
        "a rehearsal must not be added into the real count: %r" % after
    assert after_bar == before_bar + 2, "the bar gave no feedback during the run"
    assert "no real quota spent" in tip

    zone = screen.settings.get("send_timezone")
    assert DB.sent_today(screen.conn, ACCOUNT, zone) == 0, \
        "a rehearsal spent real quota"
    screen._rehearsed.clear()
    screen._refresh_accounts()


def test_a_live_send_is_not_counted_as_a_rehearsal():
    screen = _screen("outreach")
    screen._rehearsed.clear()
    worker = OutreachWorker(0, {"dry_run": False}, dry_run=False)
    screen.send_worker = worker
    try:
        screen._on_message_sent({"lead_id": 0, "step": 0, "account_email": ACCOUNT})
    finally:
        screen.send_worker = None
    assert screen._rehearsed == {}, \
        "a live send was tallied as rehearsed: %s" % screen._rehearsed


# ── U5: the postal address box ───────────────────────────────────────────────

def test_a_three_line_address_fits_the_postal_box_whole():
    screen = _screen("settings")
    app = _app()
    for size in (DEFAULT_SIZE, MINIMUM_SIZE):
        _sized(screen, size)
        edit = screen.postal_edit
        edit.setPlainText(ADDRESS)
        app.processEvents()
        wanted = edit.document().documentLayout().documentSize().height()
        assert wanted > 0, "the document was never laid out, so this proves nothing"
        assert wanted <= edit.viewport().height(), (
            "the address needs %dpx and the box shows %d at %dx%d"
            % (wanted, edit.viewport().height(), size[0], size[1]))
        assert edit.verticalScrollBar().maximum() == 0, \
            "the address box still scrolls at %dx%d" % size


def test_the_postal_box_does_not_creep_when_asked_twice():
    """It sizes itself from `contentsMargins`, not from a stale viewport."""
    screen = _screen("settings")
    screen.postal_edit.setPlainText(ADDRESS)
    _app().processEvents()
    first = screen.postal_edit.height()
    for _ in range(3):
        screen._fit_postal_box()
        _app().processEvents()
    assert screen.postal_edit.height() == first, \
        "the box grew from %d to %d just by being measured" % (first, screen.postal_edit.height())


def test_the_postal_box_keeps_a_floor_and_a_ceiling():
    screen = _screen("settings")
    edit = screen.postal_edit
    edit.setPlainText("")
    _app().processEvents()
    empty = edit.height()
    edit.setPlainText("\n".join("line %d" % n for n in range(30)))
    _app().processEvents()
    tall = edit.height()
    assert empty >= 4 * edit.fontMetrics().lineSpacing(), \
        "an empty address box no longer reads as one"
    assert tall > empty, "the box never grows"
    assert tall <= 11 * edit.fontMetrics().lineSpacing() + 40, \
        "a pasted essay pushes the rest of the page off screen"
    edit.setPlainText(ADDRESS)


# ── U6: the results table's empty state ──────────────────────────────────────

def _visible_card_text(screen) -> str:
    card = screen.table_stack.currentWidget().findChild(QFrame, "card")
    if card is None:
        return ""
    return " · ".join(label.text() for label in card.findChildren(QLabel))


def test_an_empty_results_table_says_what_to_do_next():
    screen = _screen("results")
    screen.results = []
    screen.table.setRowCount(0)
    screen._is_running = False
    screen._update_table_page()
    _app().processEvents()

    assert screen.table_stack.currentIndex() == screen.NOTHING_PAGE
    assert screen.table_stack.currentWidget() is not screen.table
    text = _visible_card_text(screen)
    assert "Nothing collected" in text and "Scrape" in text, \
        "the empty table still says nothing: %r" % text

    # The way out, and that it goes anywhere. The screen has no bar of its own
    # to carry Home any more, so this button is the whole of it.
    page = screen.table_stack.currentWidget()
    assert page.action_button is not None and \
        page.action_button.text() == "Change the search"
    routed = []
    screen.home_signal.connect(lambda: routed.append(True))
    try:
        page.action_button.click()
        assert routed == [True], "the empty state's only way out routes nowhere"
    finally:
        screen.home_signal.disconnect()


def test_a_run_that_has_not_produced_a_row_yet_says_so():
    screen = _screen("results")
    screen.results = []
    screen.table.setRowCount(0)
    screen._is_running = True
    screen._update_table_page()
    try:
        assert screen.table_stack.currentIndex() == screen.WAITING_PAGE
        assert "Looking for businesses" in _visible_card_text(screen)
    finally:
        screen._is_running = False


def test_the_table_comes_back_the_moment_a_row_arrives():
    screen = _screen("results")
    screen.setup(["plumbers"], ["Toronto"], ["name", "email"], max_results=10)
    screen.add_table_row({"name": "Zeta Roofing", "email": "zeta@example.com"})
    _app().processEvents()
    assert screen.table_stack.currentWidget() is screen.table

    screen._apply_search("nothing matches this")
    assert screen.table_stack.currentIndex() == screen.FILTERED_PAGE, \
        "a filter that hides every row leaves a bare rectangle again"
    assert "Clear the filter" in _visible_card_text(screen)
    screen._apply_search("")
    assert screen.table_stack.currentWidget() is screen.table


# ── U7: the export-folder browse button ──────────────────────────────────────

def test_the_browse_button_is_wide_enough_for_its_own_label():
    screen = _screen("input")
    for size in (DEFAULT_SIZE, MINIMUM_SIZE):
        _sized(screen, size)
        button = screen.export_browse_btn
        assert button.text() == "…"
        # The `+ size` used to sit outside the `%`, so this message was four
        # placeholders against two arguments plus a tuple concatenated onto a
        # string: the assertion could only ever fail by raising a TypeError
        # instead of saying which button was how many pixels short.
        assert button.width() >= button.sizeHint().width(), (
            "the browse button is %dpx wide and needs %d at %dx%d, so it "
            "renders '..'" % ((button.width(), button.sizeHint().width())
                              + tuple(size)))
        assert button.height() == THEME.control["md"] == \
            screen.export_dir_input.height(), (
            "it must line up with the field it sits beside: %dpx against the "
            "field's %dpx, and control.md is %d"
            % (button.height(), screen.export_dir_input.height(),
               THEME.control["md"]))


# ── U8: every colour the sheet writes text in has to clear its ground ────────

_AA, _COMPONENT = 4.5, 3.0

# CIE76. `ui/theme.py` builds both palettes so that no two tokens sit inside 2.0
# of one another, which is the floor "these are two different colours" means.
_JND = 2.0


def _grounds(theme) -> tuple:
    """(page, card) — the two the sheet's transparent text can land on."""
    return theme.color["canvas"].upper(), theme.color["surface"].upper()


def _luminance(colour: str) -> float:
    """Relative luminance of a #RRGGBB, by the WCAG 2.1 definition."""
    channels = []
    for start in (1, 3, 5):
        value = int(colour[start:start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(one: str, other: str) -> float:
    first, second = _luminance(one), _luminance(other)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _sheet(theme=None) -> str:
    return re.sub(r"/\*.*?\*/", "", TH.stylesheet(theme or _WEARING), flags=re.S)


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


def _ink_rules(theme) -> list:
    """(selector, colour, grounds) for every rule that paints text.

    A rule that names its own background is measured against that; anything
    else is transparent and can land on either ground, so it is measured
    against both. `:disabled` is skipped, and only because WCAG exempts an
    inactive component — not because those greys would pass.
    """
    rules = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _sheet(theme)):
        selector = " ".join(selector.split())
        ink = re.search(r"(?<!-)\bcolor:\s*(#[0-9A-Fa-f]{6})", body)
        if ink is None or ":disabled" in selector:
            continue
        own = re.search(r"background(?:-color)?:\s*(#[0-9A-Fa-f]{6})", body)
        grounds = [own.group(1).upper()] if own else list(_grounds(theme))
        rules.append((selector, ink.group(1).upper(), grounds))
    return rules


def test_the_two_grounds_are_the_ones_that_actually_paint():
    """The ratios below are worth no more than the grounds they assume.

    Both are read off rendered pixels rather than off the sheet, since a card
    that turned out to paint something else would make every ratio in this
    section a different number — and both are compared against `color.canvas`
    and `color.surface` rather than against a hex, so a palette change moves
    the expectation with the paint instead of leaving this describing a page
    the app stopped drawing.
    """
    for theme in THEMES:
        with _wearing(theme):
            wanted_page, wanted_card = _grounds(theme)
            page = _histogram(_sized(_screen("input"), DEFAULT_SIZE))
            assert max(page, key=page.get) == wanted_page, (
                "%s: the page paints %s and canvas is %s"
                % (theme.name, max(page, key=page.get), wanted_page))

            outreach = _screen("outreach")
            outreach._goto_tab(1)
            frame = outreach.prepare_btn.parentWidget()
            assert frame.objectName() == "card", \
                "this is measuring %r, not a card" % frame.objectName()
            card = _histogram(frame)
            assert max(card, key=card.get) == wanted_card, (
                "%s: a card paints %s and surface is %s"
                % (theme.name, max(card, key=card.get), wanted_card))


def test_no_text_colour_falls_under_aa_on_either_ground():
    """The defect: a grey chosen on the page and then used on a card.

    #8E8E93 measured 5.22:1 on the old page and 4.27:1 on the old card, so
    every label that moved onto a card took a passing colour under the floor
    with it. The sweep is over the whole sheet because fixing the two labels
    that were reported and leaving the next one for the next pass is how this
    got here — and over both sheets, because which of the two grounds is the
    harder one is not the same in each: the light page is a mid grey, so there
    the card is the easier of the two.
    """
    under = ["%s %s — %s on %s is %.2f:1"
             % (theme.name, selector, ink, ground, _contrast(ink, ground))
             for theme in THEMES
             for selector, ink, grounds in _ink_rules(theme)
             for ground in grounds if _contrast(ink, ground) < _AA]
    assert not under, "text under %.1f:1:\n  %s" % (_AA, "\n  ".join(under))


def test_the_primary_button_reads_in_every_state_it_paints():
    """Start Scraping, Audit all, Prepare campaign, Start sending, Save.

    Its label is `font.h3` — 14px/600, which is not WCAG large text — so it
    needs 4.5:1 on all three live fills. That half is unchanged and is now
    measured in both palettes: 5.84 / 4.68 / 7.16 in dark, 7.16 / 8.22 / 9.44
    in light.

    The other half is inverted, and the contract inverted it. This used to
    require the fill itself to stay 3:1 clear of a card, and its own docstring
    named the trap: "darkening the green until white passes walks the fill
    straight into the card". `docs/DESIGN_SYSTEM.md` resolves that tension the
    other way — rule 2 makes the ink on the fill mandatory, and `ui/theme.py`
    writes down the price at the token that pays it, `accent.border` measuring
    2.60:1 on `surface`. So the accent fill is 2.34:1 on a card in dark and the
    old assertion cannot be met by any green that also carries white text.

    What replaces it is the thing that actually keeps the button findable, and
    it is stricter than a single ratio: the button has to have an edge in every
    state it paints, from either its fill or the 1px rim it carries in all of
    them, against the page these primary actions are laid on — 3.70:1 at worst
    — and its fill has to be a different colour from a card by a wide margin of
    the palette's own just-noticeable difference, since on a card what tells it
    apart is hue rather than luminance.
    """
    for theme in THEMES:
        sheet = _sheet(theme)
        ink = re.search(r"QPushButton#start_btn\s*\{[^}]*?(?<!-)\bcolor:\s*"
                        r"(#[0-9A-Fa-f]{6})", sheet).group(1).upper()
        rim = re.search(r"QPushButton#start_btn\s*\{[^}]*?\bborder:\s*\d+px"
                        r"\s+\w+\s+(#[0-9A-Fa-f]{6})", sheet).group(1).upper()
        fills = [fill.upper() for fill in re.findall(
            r"QPushButton#start_btn(?::(?:hover|pressed))?\s*\{[^}]*?"
            r"background-color:\s*(#[0-9A-Fa-f]{6})", sheet)]
        assert len(fills) == 3, "base, :hover and :pressed, not %s" % fills
        page, card = _grounds(theme)

        for fill in fills:
            assert _contrast(ink, fill) >= _AA, "%s: %s on %s is %.2f:1" % (
                theme.name, ink, fill, _contrast(ink, fill))
            edge = max(_contrast(fill, page), _contrast(rim, page))
            assert edge >= _COMPONENT, (
                "%s: %s on the page is %.2f:1 and its %s rim %.2f:1, so the "
                "button has no edge left in that state"
                % (theme.name, fill, _contrast(fill, page), rim,
                   _contrast(rim, page)))
            assert _delta_e(fill, card) >= 10 * _JND, (
                "%s: %s is delta-E %.1f from a card, which is close enough "
                "that the button stops being a shape on one"
                % (theme.name, fill, _delta_e(fill, card)))

        base, hover, pressed = fills
        for state, fill in (("hover", hover), ("pressed", pressed)):
            # Delta-E rather than a contrast ratio, and the light theme is why:
            # its hover moves 4.95 of just-noticeable difference and is plainly
            # a different green, while measuring 1.148:1 in luminance — a
            # ratio floor would have called a visible state invisible.
            assert _delta_e(base, fill) >= 2 * _JND, (
                "%s: :%s is %s against a %s base — delta-E %.2f, which is no "
                "feedback at all"
                % (theme.name, state, fill, base, _delta_e(base, fill)))


# ── U9: the template editor ──────────────────────────────────────────────────

_STORES = itertools.count()


@contextlib.contextmanager
def _own_store(screen):
    """A template store of this module's own, with the screen reloaded off it.

    `core.templates` resolves its path once at import and every other test file
    that touches templates points it somewhere else, so a UI test that writes a
    template would otherwise land in whichever temp store won the import race
    and be read back as a fixture by a file that never asked for it. Starting
    empty also means every built-in below is the wording it shipped with.
    """
    saved = TPL.TEMPLATES_PATH
    TPL.TEMPLATES_PATH = os.path.join(_TMP, "templates-%d.json" % next(_STORES))
    try:
        screen._reload_templates()
        yield
    finally:
        TPL.TEMPLATES_PATH = saved
        screen._reload_templates()


class _Answers:
    """Every dialog these tests reach, answered without one being shown.

    A modal in an offscreen run never comes back, so the button the user presses
    has to arrive as an input. Everything else on QMessageBox passes through
    untouched, because the same name in the same module is what `_store_failed`
    reaches for when a write is refused.
    """

    def __init__(self, answer):
        self._answer = answer
        self.asked = 0

    def __getattr__(self, name):
        return getattr(QMessageBox, name)

    def question(self, *_args, **_kwargs):
        self.asked += 1
        return self._answer

    def warning(self, *_args, **_kwargs):
        return QMessageBox.Ok


@contextlib.contextmanager
def _answering(answer):
    saved = SS.QMessageBox
    dialog = _Answers(answer)
    SS.QMessageBox = dialog
    try:
        yield dialog
    finally:
        SS.QMessageBox = saved


def _templates_page(screen, size=DEFAULT_SIZE):
    """The settings screen with the Templates tab up and its layout run.

    Made the active window as well as the visible one: `setFocus` on a widget
    in an inactive window records the window's own focus and nothing more, so
    `hasFocus` reads False and any assertion about the keyboard would be
    measuring which screen an earlier test happened to show last.
    """
    screen.pages.setCurrentIndex(screen.TABS.index("Templates"))
    screen.activateWindow()
    _app().setActiveWindow(screen)
    _sized(screen, size)
    _sized(screen, size)
    return screen


def _row(screen, template_id):
    listing = screen.template_list
    for row in range(listing.count()):
        item = listing.item(row)
        if str(item.data(Qt.UserRole) or "") == template_id:
            return item
    raise AssertionError("no row for %r in the picker" % template_id)


def _click_row(screen, template_id) -> None:
    screen.template_list.setCurrentItem(_row(screen, template_id))
    _app().processEvents()


def _highlighted(screen) -> str:
    item = screen.template_list.currentItem()
    return str(item.data(Qt.UserRole) or "") if item is not None else ""


def test_the_highlighted_row_and_the_loaded_template_never_disagree():
    """The blocker: edits landing in a template nobody is looking at.

    Answering the prompt with Save writes the template being left, and the
    reload behind that write put the highlight back on it while the editor went
    on to the row that was picked — so the list read "Headline gap — edited"
    over an editor holding `time_saved`, and every keystroke after it went into
    `time_saved`. Both halves are read straight off the two widgets, because the
    whole failure was that they could differ.

    All three answers, both directions, and a click on the row that is already
    current — which is how the user reached for the way back and did not get it,
    since `currentItemChanged` never fires for an item already current.
    """
    screen = _templates_page(_screen("settings"))
    typed = "a line that was never saved"
    with _own_store(screen):
        for start, target in (("gap_direct", "time_saved"),
                              ("time_saved", "gap_direct")):
            for answer, name, stays in (
                    (QMessageBox.Save, "Save", False),
                    (QMessageBox.Discard, "Discard", False),
                    (QMessageBox.Cancel, "Cancel", True)):
                screen._reload_templates(start)
                _click_row(screen, start)
                screen.template_body_edit.setPlainText(typed)
                _app().processEvents()
                assert screen._template_dirty, "the edit was never noticed"

                with _answering(answer) as dialog:
                    _click_row(screen, target)
                assert dialog.asked == 1, \
                    "%s: unsaved changes were dropped without asking" % name

                wanted = start if stays else target
                where = "%s %s -> %s" % (name, start, target)
                assert screen._template_id == wanted, \
                    "%s: the editor holds %r" % (where, screen._template_id)
                assert _highlighted(screen) == wanted, (
                    "%s: the editor holds %r and the list highlights %r"
                    % (where, screen._template_id, _highlighted(screen)))
                assert screen.template_name_edit.text() == \
                    TPL.get_template(wanted).name, (
                        "%s: the name box reads %r over template %r"
                        % (where, screen.template_name_edit.text(), wanted))

                # The recovery the user reached for, on the row already current.
                with _answering(QMessageBox.Cancel):
                    _click_row(screen, _highlighted(screen))
                assert screen._template_id == _highlighted(screen) == wanted, \
                    "%s: clicking the highlighted row split the two again" % where

                if answer == QMessageBox.Save:
                    assert TPL.get_template(start).body == typed, \
                        "Save wrote the edit somewhere other than %r" % start
                    assert TPL.get_template(target).body != typed, \
                        "the edit landed in %r, which nobody was editing" % target
                    TPL.reset_template(start)
                if answer == QMessageBox.Cancel:
                    assert screen._template_dirty, \
                        "Cancel threw away the changes it was asked to keep"
                    assert screen.template_body_edit.toPlainText() == typed, \
                        "Cancel reloaded the template over the unsaved text"


def test_a_save_the_store_refuses_leaves_the_editor_where_it_is():
    """`_store_failed` says to copy the text somewhere safe; it must still be there.

    A refused write used to count as a clean save, so the switch went ahead and
    the only copy of the edit went with it.
    """
    screen = _templates_page(_screen("settings"))
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        _click_row(screen, "gap_direct")
        screen.template_body_edit.setPlainText("the only copy of this line")
        _app().processEvents()

        # A null byte, because `_write_store` creates the folders it is given:
        # this is refused before the filesystem is ever asked, which is the same
        # False the screen sees from a read-only profile or a full disk.
        broken = os.path.join(_TMP, "no", "such", "\0", "templates.json")
        saved_path, TPL.TEMPLATES_PATH = TPL.TEMPLATES_PATH, broken
        try:
            with _answering(QMessageBox.Save):
                _click_row(screen, "time_saved")
        finally:
            TPL.TEMPLATES_PATH = saved_path

        assert screen._template_id == _highlighted(screen) == "gap_direct", \
            "the screen moved on from a write that never happened"
        assert screen.template_body_edit.toPlainText() == "the only copy of this line"
        assert screen._template_dirty, "unsaved text was marked saved"


def _subject_counter(screen) -> tuple:
    """(characters, colour) as the counter is painting them right now."""
    text = screen.template_subject_count.text()
    colour = re.search(r"color:(#[0-9A-Fa-f]{6})", text).group(1).upper()
    return int(re.search(r"(\d+) / \d+", text).group(1)), colour


def test_the_subject_counter_measures_the_length_that_gets_cut():
    """The counter measured `render`'s output, which is already cut to the cap.

    So `len(subject) > limit` was unreachable and the red state was dead code:
    200 x's, 120 emoji, an RTL line and seven chained merge fields all read
    green at 55 of 55 or less, while the findings panel two rows below warned
    that the same subject was too long. The number has to be taken before the
    cut, or it is a warning that cannot fire.
    """
    screen = _templates_page(_screen("settings"))
    limit = TPL.SUBJECT_MAX
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        for subject in ("x" * 200,
                        "\U0001F600" * 120,
                        "مرحبا " * 20,
                        " ".join(["{{business_name}}"] * 7),
                        "a quick question about the booking form on your "
                        "website today please"):
            screen.template_subject_edit.setText(subject)
            screen._refresh_template_preview()
            count, colour = _subject_counter(screen)
            assert count > limit, (
                "a %d-character subject counts as %d of %d, and no red state "
                "can ever be reached from there" % (len(subject), count, limit))
            assert colour == SS._RED, \
                "%d of %d is painted %s" % (count, limit, colour)
            assert "cut" in screen.template_subject_count.text(), \
                "the counter is over the cap and does not say what happens"

            # The two panels may never contradict each other on screen.
            if "characters as written" in screen.template_issues.text():
                assert colour == SS._RED, (
                    "the findings panel warns the subject is too long and the "
                    "counter reads %d of %d in green" % (count, limit))

        screen.template_subject_edit.setText("booking at {{business_name}}")
        screen._refresh_template_preview()
        count, colour = _subject_counter(screen)
        assert count <= limit and colour == SS._GREEN, \
            "a subject that fits reads %d of %d in %s" % (count, limit, colour)
        assert "cut" not in screen.template_subject_count.text()


def _findings(count: int) -> list:
    return [{"level": "warning", "field": "subject",
             "message": "finding number %d, written long enough that it wraps "
                        "across the column it is shown in" % index}
            for index in range(count)]


def test_a_wall_of_findings_cannot_push_the_preview_off_the_page():
    """Twelve findings grew the label to 256px and put the preview under the fold.

    The preview is what the copy is being fixed against, so it cannot be the
    thing that disappears when the copy is at its worst. Bounded, scrolled, and
    still sized to what it holds when it holds little.
    """
    screen = _templates_page(_screen("settings"))
    for size in (DEFAULT_SIZE, MINIMUM_SIZE):
        _templates_page(screen, size)
        pane, body = screen.template_issues_pane, screen.template_issues

        screen._show_template_issues(_findings(12))
        _sized(screen, size)
        many = pane.height()
        assert many < body.height(), (
            "the pane is %dpx for %dpx of findings at %dx%d, so it is still "
            "growing to fit" % ((many, body.height()) + size))
        missed = (body.height() - pane.viewport().height()
                  - pane.verticalScrollBar().maximum())
        assert missed == 0, \
            "%dpx of findings is out of reach at %dx%d" % ((missed,) + size)

        screen._show_template_issues(_findings(24))
        _sized(screen, size)
        assert pane.height() == many, (
            "twice the findings made the pane %dpx instead of %d"
            % (pane.height(), many))

        screen._show_template_issues(_findings(1))
        _sized(screen, size)
        assert pane.height() < many, \
            "one finding still reserves the full %dpx at %dx%d" % ((many,) + size)
        assert pane.verticalScrollBar().maximum() == 0, \
            "one finding already needs scrolling at %dx%d" % size

        screen._show_template_issues(_findings(12))
        _sized(screen, size)
        hidden = _preview_hidden(screen)
        assert hidden == 0, (
            "%dpx of the preview is off the page under twelve findings at "
            "%dx%d" % ((hidden,) + size))

    screen._show_template_issues([])


# The message the preview is measured against: a greeting, a paragraph and a
# sign-off, so that "shows the body" means lines of somebody's copy rather than
# a From/To card with white space under it.
SAMPLE_BODY = ("Hi there,\n\nI had a look at your website this morning and the "
               "booking form on the contact page drops every enquiry that "
               "arrives after hours.\n\nWe build the automation that catches "
               "them.\n\nSam")


def _preview_hidden(screen) -> int:
    """Pixels of the preview outside the page it is drawn on."""
    preview, page = screen.template_preview, screen.pages.currentWidget()
    on_page = QRect(preview.mapTo(page, preview.rect().topLeft()), preview.size())
    return preview.height() - on_page.intersected(page.rect()).height()


def _body_lines_shown(preview) -> int:
    """Lines of the message itself wholly inside the preview, unscrolled.

    Off the laid-out document rather than off the widget's height, because how
    much of an email a given number of pixels holds is a question about the
    font that is drawing it, and the answer differs between this machine and
    the one that ships.
    """
    document = preview.document()
    layout = document.documentLayout()
    top = preview.verticalScrollBar().value()
    bottom = top + preview.viewport().height()
    shown, block, index = 0, document.begin(), 0
    while block.isValid():
        # Block 0 is the subject line and block 1 the To line; the message is
        # everything under them.
        if index >= 2:
            base = layout.blockBoundingRect(block).top()
            lines = block.layout()
            for line in range(lines.lineCount()):
                box = lines.lineAt(line).rect()
                if base + box.top() >= top and base + box.bottom() <= bottom:
                    shown += 1
        block, index = block.next(), index + 1
    return shown


def test_the_preview_shows_the_message_itself_at_the_window_minimum():
    """The half of the contract that did not hold: 0px of preview above the fold.

    The findings pane was bounded, but the column it lived in was not, so once
    the pane filled the preview started below the bottom edge of an 880x620
    window — and scrolled all the way down it was 84px of From and To with no
    message under it. The preview is the only thing on the screen that says
    what will actually be sent, so it is now pinned under the boxes instead of
    queued behind them: whole, unscrolled, and never smaller than two lines of
    the copy, whatever the findings pane is doing.
    """
    screen = _templates_page(_screen("settings"))
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        screen.template_subject_edit.setText("a question about your booking form")
        screen.template_body_edit.setPlainText(SAMPLE_BODY)
        for size in (DEFAULT_SIZE, MINIMUM_SIZE):
            for count in (0, 1, 12, 24):
                _templates_page(screen, size)
                screen._refresh_template_preview()
                screen._show_template_issues(_findings(count))
                _sized(screen, size)
                preview = screen.template_preview
                where = "%d findings at %dx%d" % ((count,) + size)

                hidden = _preview_hidden(screen)
                assert hidden == 0, \
                    "%dpx of the preview is off the page with %s" % (hidden, where)
                assert preview.verticalScrollBar().value() == 0, \
                    "the preview starts scrolled with %s" % where
                shown = _body_lines_shown(preview)
                assert shown >= 2, (
                    "%dpx of preview shows %d lines of the message with %s"
                    % (preview.height(), shown, where))
        screen._show_template_issues([])


def test_neither_half_of_the_template_editor_can_be_dragged_away():
    """The split is the user's to move, and neither end of it may be an edge case.

    Dragged to the top the boxes would go, dragged to the bottom the preview
    would, and both are the failure this layout exists to prevent — so the
    handle stops at a floor on each side rather than at zero.
    """
    screen = _templates_page(_screen("settings"), MINIMUM_SIZE)
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        screen.template_body_edit.setPlainText(SAMPLE_BODY)
        screen._refresh_template_preview()
        split = screen.template_preview.window().findChild(QSplitter, "template_split")
        assert split is not None, "the template editor has no split to drag"
        assert not split.childrenCollapsible(), \
            "either half of the editor can be collapsed to nothing"

        for sizes in ([10000, 1], [1, 10000]):
            split.setSizes(sizes)
            _sized(screen, MINIMUM_SIZE)
            editing, preview = split.sizes()
            assert min(editing, preview) > 0, \
                "dragging to %r left a half of the editor with no height" % sizes
            assert _preview_hidden(screen) == 0, \
                "dragging to %r put the preview off the page" % sizes
            assert _body_lines_shown(screen.template_preview) >= 2, (
                "dragging to %r left %dpx of preview showing %d lines of the "
                "message" % (sizes, screen.template_preview.height(),
                             _body_lines_shown(screen.template_preview)))

            box = screen.template_subject_edit
            area = _scroll_ancestor(box)
            bottom = box.mapTo(area.widget(), box.rect().bottomLeft()).y()
            reach = area.verticalScrollBar().maximum() + area.viewport().height()
            assert bottom <= reach, (
                "dragging to %r put the subject line %dpx past the furthest the "
                "boxes scroll" % (sizes, bottom - reach))
        split.setSizes([10000, 1])
        screen._show_template_issues([])


def test_a_long_template_name_keeps_the_marker_that_says_whose_it_is():
    """The row's marker is the only thing saying whether Delete or Reset applies.

    It sits at the end of the row, and the elide was set to cut the end.
    """
    screen = _templates_page(_screen("settings"))
    with _own_store(screen):
        TPL.save_user_template(TPL.Template(
            id="a_very_long_name", name="A" * 84, step=0,
            subject="hello {{business_name}}", body="One line.\n"))
        screen._reload_templates("a_very_long_name")
        _templates_page(screen, MINIMUM_SIZE)

        listing = screen.template_list
        text = _row(screen, "a_very_long_name").text()
        metrics, width = listing.fontMetrics(), listing.viewport().width()
        assert metrics.width(text) > width, (
            "an 84-character name fits %dpx of picker, so this proves nothing"
            % width)

        shown = metrics.elidedText(text, listing.textElideMode(), width)
        assert shown != text, "the row is being clipped, not elided"
        assert shown.endswith("custom"), \
            "the marker is gone from a shortened row: %r" % shown
        assert not metrics.elidedText(text, Qt.ElideRight, width).endswith("custom"), \
            "cutting from the right keeps the marker here, so this proves nothing"

    _templates_page(screen)


def _key(widget, code) -> None:
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, code, Qt.NoModifier))
    _app().processEvents()


def _tab_stops(start, limit: int = 400) -> list:
    """Everything Tab would land on, walking forward from `start`."""
    stops, widget = [], start
    for _ in range(limit):
        widget = widget.nextInFocusChain()
        if widget is start:
            break
        if (widget.focusPolicy() & Qt.TabFocus) and widget.isEnabled() \
                and widget.isVisibleTo(start.window()):
            stops.append(widget)
    return stops


def test_the_merge_field_palette_can_be_reached_and_used_from_the_keyboard():
    """Every chip was NoFocus, so Tab never landed on one and 23 tooltips were
    mouse-only.

    The chips have to stay NoFocus — one that took the focus would take the
    caret it inserts at with it — so the bar takes the focus instead, and the
    caret goes back where it was before the insert lands.
    """
    screen = _templates_page(_screen("settings"))
    bar = screen.template_chips
    assert {chip.focusPolicy() for chip in bar._chips} == {Qt.NoFocus}, \
        "a chip that can be focused loses the caret the field is inserted at"
    assert bar in _tab_stops(screen.template_subject_edit), \
        "Tab out of the subject line never reaches the merge fields"

    bar.setFocus()
    _app().processEvents()
    assert bar.hasFocus(), "the bar will not take the focus"

    marked = [chip for chip in bar._chips if chip.styleSheet()]
    assert len(marked) == 1 and marked[0].text() == bar.current_field(), \
        "nothing on screen says which chip Enter would insert"
    assert marked[0].toolTip().strip(), \
        "the chip a keyboard lands on has no tooltip to read"

    first = bar.current_field()
    _key(bar, Qt.Key_Right)
    assert bar.current_field() != first, "the arrow keys do not walk the row"
    _key(bar, Qt.Key_Left)
    assert bar.current_field() == first, "the arrow keys only go one way"
    _key(bar, Qt.Key_End)
    assert bar.current_field() == bar._chips[-1].text()
    _key(bar, Qt.Key_Home)
    assert bar.current_field() == first

    screen.template_subject_edit.setFocus()
    screen.template_subject_edit.setText("hello world")
    screen.template_subject_edit.setCursorPosition(5)
    _app().processEvents()
    bar.setFocus()
    _app().processEvents()
    _key(bar, Qt.Key_Return)
    assert screen.template_subject_edit.text() == "hello{{%s}} world" % first, (
        "the field landed somewhere other than the caret: %r"
        % screen.template_subject_edit.text())
    assert screen.template_subject_edit.hasFocus(), \
        "the caret was not handed back to the box being written in"

    screen.template_body_edit.setFocus()
    screen.template_body_edit.setPlainText("line one\nline two")
    cursor = screen.template_body_edit.textCursor()
    cursor.setPosition(4)
    screen.template_body_edit.setTextCursor(cursor)
    _app().processEvents()
    bar.setFocus()
    _app().processEvents()
    _key(bar, Qt.Key_Space)
    assert screen.template_body_edit.toPlainText() == \
        "line{{%s}} one\nline two" % first, (
            "the body caret was not restored: %r"
            % screen.template_body_edit.toPlainText())

    screen.template_body_edit.clearFocus()
    _app().processEvents()
    assert not any(chip.styleSheet() for chip in bar._chips), \
        "a chip is still marked with the palette out of focus"

    # Settled, because the screen is shared: an editor left unsaved makes the
    # next row-click anywhere in this file open a modal nobody is there to answer.
    screen._reload_templates()


def _caret(editor) -> tuple:
    """(text, start, end) as the box is drawing them right now.

    Read off the widget rather than off the screen's own bookkeeping: what the
    user aims a merge field at is the caret they can see, and the whole failure
    these tests are about was the screen believing something else.
    """
    if isinstance(editor, QLineEdit):
        start = editor.selectionStart()
        if start < 0:
            return editor.text(), editor.cursorPosition(), editor.cursorPosition()
        return editor.text(), start, start + len(editor.selectedText())
    cursor = editor.textCursor()
    return (editor.toPlainText(), min(cursor.anchor(), cursor.position()),
            max(cursor.anchor(), cursor.position()))


def _typed(editor, text: str) -> None:
    QTest.keyClicks(editor, text)
    _app().processEvents()


def _pressed(editor, key, times: int = 1, modifier=Qt.NoModifier) -> None:
    for _ in range(times):
        QTest.keyClick(editor, key, modifier)
    _app().processEvents()


def _clicked_into(editor, share: float = 0.25) -> None:
    """A mouse click a quarter of the way along the first line of `editor`.

    A share of the width rather than a pixel offset, and where it lands is read
    back off the box afterwards, because how many characters fit in a quarter of
    a column is a question about the font doing the drawing.
    """
    target = editor if isinstance(editor, QLineEdit) else editor.viewport()
    down = min(editor.fontMetrics().height(), target.height() // 2)
    QTest.mouseClick(target, Qt.LeftButton,
                     pos=QPoint(int(target.width() * share), down))
    _app().processEvents()


def _clicked_chip(screen) -> str:
    """The first chip, clicked with the mouse, and the token it inserts."""
    chip = screen.template_chips._chips[0]
    QTest.mouseClick(chip, Qt.LeftButton)
    _app().processEvents()
    return "{{%s}}" % chip.text()


# Every way the caret gets somewhere, since the offset was only ever written
# down on a focus change and none of these is one.
LONG_LINE = "a quick question about the booking form on your website this week"
_PLACEMENTS = (
    ("an empty box", "", lambda editor: None),
    ("typed to the end", "confirmation", lambda editor: _typed(editor, "confirmation")),
    ("arrowed into the middle of a word", "confirmation",
     lambda editor: (_typed(editor, "confirmation"),
                     _pressed(editor, Qt.Key_Left, 4))),
    ("clicked into with the mouse", LONG_LINE,
     lambda editor: (_typed(editor, LONG_LINE), _clicked_into(editor))),
    ("with a word selected", "hello cruel world",
     lambda editor: (_typed(editor, "hello cruel world"),
                     _pressed(editor, Qt.Key_Left, 6),
                     _pressed(editor, Qt.Key_Left, 5, Qt.ShiftModifier))),
)


def test_a_merge_chip_lands_at_the_caret_that_is_on_screen():
    """The blocker: the field went in at an offset written down on a focus change.

    Typing, an arrow key, a click inside the box and a drag across a word are
    none of them focus changes, so none of them was recorded — and the insert
    *moved* the caret to whatever had been. What kept that honest was luck: the
    scrolling column took the click focus the chip refuses, so most clicks
    refreshed the offset on the way past. A selection did not survive the trip,
    because a `QLineEdit` empties one as it loses the focus, so a field aimed at
    a highlighted word landed after the word rather than over it. And with the
    focus anywhere but the boxes — in the picker, a row-switch later — the
    offset belonged to a template that was no longer on screen.

    Both boxes, every way of putting a caret somewhere, and the keystroke after
    the insert, because where the caret ends up is what makes the next word land
    in the wrong place too.
    """
    screen = _templates_page(_screen("settings"))
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        for editor, box in ((screen.template_subject_edit, "subject"),
                            (screen.template_body_edit, "body")):
            for name, seed, place in _PLACEMENTS:
                if isinstance(editor, QLineEdit):
                    editor.setText("")
                else:
                    editor.setPlainText("")
                editor.setFocus()
                _app().processEvents()
                place(editor)

                text, start, end = _caret(editor)
                where = "%s / %s" % (box, name)
                assert text == seed, \
                    "%s: the box holds %r before the chip is clicked" % (where, text)
                if name == "clicked into with the mouse":
                    assert 0 < start < len(text), (
                        "%s: the click landed at %d of %d, so nothing about "
                        "the middle of a line is being tested"
                        % (where, start, len(text)))
                if name == "with a word selected":
                    assert end - start == 5, \
                        "%s: %d characters are selected, not 5" % (where, end - start)

                token = _clicked_chip(screen)
                after, at, _to = _caret(editor)
                assert after == text[:start] + token + text[end:], (
                    "%s: the field landed somewhere other than the caret: %r"
                    % (where, after))
                assert at == start + len(token), (
                    "%s: the caret ended at %d, not %d — every keystroke after "
                    "this one lands there" % (where, at, start + len(token)))
                assert editor.hasFocus(), \
                    "%s: the caret was not handed back to the box" % where

                _typed(editor, "!")
                assert _caret(editor)[0] == text[:start] + token + "!" + text[end:], (
                    "%s: the keystroke after the insert went somewhere else: %r"
                    % (where, _caret(editor)[0]))

        screen._reload_templates()


def test_a_field_from_the_palette_replaces_what_the_box_had_selected():
    """Reaching the palette with Tab is the one time the caret has to be restored.

    A `QLineEdit` drops its selection on the way out of the focus, so a field
    inserted from the keyboard used to land at the end of the words the user had
    highlighted and leave them there — the one gesture in a text box that means
    "put this in place of that", answered by putting it after.
    """
    screen = _templates_page(_screen("settings"))
    bar = screen.template_chips
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        for editor, box in ((screen.template_subject_edit, "subject"),
                            (screen.template_body_edit, "body")):
            if isinstance(editor, QLineEdit):
                editor.setText("")
            else:
                editor.setPlainText("")
            editor.setFocus()
            _app().processEvents()
            _typed(editor, "hello cruel world")
            _pressed(editor, Qt.Key_Left, 6)
            _pressed(editor, Qt.Key_Left, 5, Qt.ShiftModifier)
            text, start, end = _caret(editor)
            assert text[start:end] == "cruel", \
                "%s: the selection is %r" % (box, text[start:end])

            bar.setFocus()
            _app().processEvents()
            token = "{{%s}}" % bar.current_field()
            _key(bar, Qt.Key_Return)
            after, at, _to = _caret(editor)
            assert after == "hello %s world" % token, \
                "%s: the selection was not replaced: %r" % (box, after)
            assert at == start + len(token), \
                "%s: the caret ended at %d, not %d" % (box, at, start + len(token))

        screen._reload_templates()


def test_a_field_clicked_from_the_picker_forgets_the_last_template_s_caret():
    """Switching rows leaves the focus in the list, and the offset behind it.

    Measured on the way in: the caret was left 40 characters into one template,
    the row was changed, and a chip clicked from there put the field 40
    characters into the template that had just loaded — in the middle of the
    word "Answering" — and dragged the caret in after it. An offset is only ever
    an offset into the words it was taken from.
    """
    screen = _templates_page(_screen("settings"))
    with _own_store(screen):
        screen._reload_templates("gap_direct")
        _click_row(screen, "gap_direct")
        body = screen.template_body_edit
        body.setFocus()
        cursor = body.textCursor()
        cursor.setPosition(40)
        body.setTextCursor(cursor)
        _app().processEvents()
        assert body.textCursor().position() == 40, "the caret never reached 40"

        screen.template_list.setFocus()
        _click_row(screen, "time_saved")
        loaded = body.toPlainText()
        assert len(loaded) > 40, "the template that loaded is too short to tell"

        token = _clicked_chip(screen)
        after, at, _to = _caret(body)
        assert after == token + loaded, (
            "the field landed %d characters into a template loaded after the "
            "caret got there: %r" % (after.find(token), after[:64]))
        assert at == len(token), \
            "the caret was dragged to %d rather than left after the field" % at

        screen._reload_templates()


def test_what_the_editor_cannot_hold_is_never_changed_in_silence():
    """A store entry with step 99 loaded as Follow-up 5 and looked saved.

    The combo cannot hold 99, so the first Save wrote 5 over a number somebody
    put there on purpose, with nothing on screen in between. The subject box is
    the same shape of problem: it cannot draw a stored line break, so it says
    that it flattened one rather than writing the flattened line back unasked.
    """
    screen = _templates_page(_screen("settings"), MINIMUM_SIZE)
    with _own_store(screen):
        # What an ordinary unsaved edit costs the header, to measure the notice
        # against. Not the Save button's position: nothing in that row can
        # shrink or wrap, so a long label is paid for in the screen's own
        # minimum width and the button never leaves the window it widened.
        screen._reload_templates("gap_direct")
        screen.template_body_edit.setPlainText("an ordinary unsaved edit")
        _sized(screen, MINIMUM_SIZE)
        assert screen.save_status.text() == "Unsaved"
        plain = screen.minimumSizeHint().width()
        screen._reload_templates("gap_direct")

        for name, template, expect in (
                ("step", TPL.Template(id="hand_edited", name="Hand edited",
                                      step=99, subject="hello there",
                                      body="One line.\n"), "99"),
                ("subject", TPL.Template(id="two_line", name="Two line", step=0,
                                         subject="first line\nsecond line",
                                         body="One line.\n"), "line breaks")):
            TPL.save_user_template(template)
            screen._reload_templates(template.id)
            _sized(screen, MINIMUM_SIZE)

            assert screen._template_dirty, (
                "%s: the row opened looking saved, so the next Save writes the "
                "editor's version" % name)
            assert expect in screen.template_issues.text(), \
                "%s: nothing says it changed: %r" % (name, screen.template_issues.text())
            assert screen.template_issues_pane.isVisible(), \
                "%s: the notice is in a panel nobody can see" % name

            # The findings panel wraps and scrolls; the header does neither, so
            # a sentence put there is carried straight into what the whole
            # screen says it needs. Measured at 2356px against the 1168px an
            # ordinary "Unsaved" costs — a window nobody could drag back down.
            assert screen.minimumSizeHint().width() <= plain, (
                "%s: the notice took the screen minimum from %dpx to %dpx"
                % (name, plain, screen.minimumSizeHint().width()))

        assert TPL.get_template("hand_edited").step == 99, \
            "opening the row rewrote the store on its own"
        assert TPL.get_template("two_line").subject == "first line\nsecond line", \
            "opening the row rewrote the stored subject on its own"

        TPL.save_user_template(TPL.Template(
            id="ordinary", name="Ordinary", step=2,
            subject="hello {{business_name}}", body="One line.\n"))
        screen._reload_templates("ordinary")
        _app().processEvents()
        assert not screen._template_dirty, \
            "a template the editor can hold whole is being reported as changed"
        assert "line breaks" not in screen.template_issues.text()

    _templates_page(screen)


def test_a_pasted_subject_stores_the_single_line_it_shows():
    """The box drew the newlines as spaces and handed them back as newlines.

    Nothing downstream is at risk — both `_clean_subject` and the header guard
    strip them — but a field that saves what it cannot show cannot be read back.
    """
    screen = _templates_page(_screen("settings"))
    edit = screen.template_subject_edit
    for pasted in ("first line\nsecond line", "first line\r\nsecond line",
                   "first line%ssecond line" % chr(0x2029)):
        edit.setText(pasted)
        _app().processEvents()
        assert edit.text() == edit.displayText(), \
            "stored %r and shows %r" % (edit.text(), edit.displayText())
        assert edit.text() == "first line second line", \
            "%r flattened to %r" % (pasted, edit.text())

    typed = "  spacing  the user  typed  "
    edit.setText(typed)
    _app().processEvents()
    assert edit.text() == typed, \
        "flattening ate spacing nobody pasted: %r" % edit.text()

    screen._reload_templates()


def test_a_disabled_reset_says_why_it_is_unavailable():
    """Every other disabled control on these screens gives the reason.

    Reset gave its own description instead, which on a greyed-out button reads
    as a button that is broken.
    """
    screen = _templates_page(_screen("settings"))
    with _own_store(screen):
        TPL.save_user_template(TPL.Template(
            id="mine", name="Mine", step=0,
            subject="hello {{business_name}}", body="One line.\n"))

        screen._reload_templates("mine")
        assert not screen.template_reset_btn.isEnabled()
        custom = screen.template_reset_btn.toolTip()

        screen._reload_templates("question")
        assert not screen.template_reset_btn.isEnabled(), \
            "an untouched built-in has nothing to be put back to"
        shipped = screen.template_reset_btn.toolTip()

        screen.template_subject_edit.setText("a subject of my own")
        _app().processEvents()
        assert screen._save_open_template(quiet=True)
        assert screen.template_reset_btn.isEnabled()
        live = screen.template_reset_btn.toolTip()
        TPL.reset_template("question")

    assert len({custom, shipped, live}) == 3, (
        "the three states share a tooltip: custom=%r shipped=%r live=%r"
        % (custom, shipped, live))
    for state, tip in (("a template of the user's own", custom),
                       ("an untouched built-in", shipped)):
        assert tip != live, \
            "Reset on %s still describes what the enabled button does" % state


# ── U10: the keyboard focus ring ─────────────────────────────────────────────

# Every button variant the sheet defines, with the control token whose height
# the sheet builds it at. "" is the default one, which carries no id. The
# heights used to be written out — 30, 28, 44, 34 — and every one of them is a
# number `control` now owns and the contract fixes at one height per size:
# "QPushButton#outlined renders at one height. A caller wanting another size
# picks a size token; it may not set a pixel height."
_VARIANTS = (("", "md"), ("outlined", "md"), ("danger", "md"), ("tab", "sm"),
             ("start_btn", "lg"), ("live", "md"), ("rehearsal", "md"),
             ("reveal", "md"))

# The hosts outlive the calls that build them. A host dropped on return takes
# its child button down with it the moment Python collects it, and grabbing a
# deleted widget takes the interpreter with it rather than failing an assert.
_HOSTS: list = []


def _ring_inks(theme) -> dict:
    """objectName -> the colour that button's focus ring is painted in.

    Read out of the generated sheet rather than assumed to be one token,
    because it is not quite one: `#start_btn` rests on `accent.border` already,
    so its ring steps to `accent.subtle` — a ring painted in the colour the
    border already was changes no pixels, which is the defect and not the rule.
    The empty key is the plain QPushButton, and later rules win, which is Qt's
    own tie-break on equal specificity.
    """
    inks = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _sheet(theme)):
        found = re.search(r"\bborder:\s*\d+px\s+\w+\s+(#[0-9A-Fa-f]{6})", body)
        if found is None:
            continue
        for selector in " ".join(selectors.split()).split(","):
            named = re.fullmatch(r"\s*QPushButton(?:#(\w+))?:focus\s*", selector)
            if named:
                inks[named.group(1) or ""] = found.group(1).upper()
    return inks


def _ring_width(theme) -> int:
    """How many pixels wide the sheet says a focus ring is."""
    widths = {int(width) for width in re.findall(
        r"QPushButton[^{}]*:focus[^{}]*\{[^{}]*?\bborder:\s*(\d+)px", _sheet(theme))}
    assert len(widths) == 1, "the sheet draws rings of %s px" % sorted(widths)
    return widths.pop()


def _ring_depth(theme) -> int:
    """How far in from an edge a ring pixel may legitimately sit.

    The ring is `_ring_width` of border, so it paints at depth 0 through
    width-1; the two extra are the antialiasing the `radius.md` corners blend
    it across. Measured deepest today: 2.
    """
    return _ring_width(theme) + 2


def _variant(name, size, checkable=False, text="Send campaign"):
    """One button of `name`'s variant, alone on a host wide enough to hold it.

    The host exists so the button is laid out rather than sized by hand: a
    button handed its geometry directly never runs the sheet's padding through
    a layout, and the padding is half of what this section measures.
    """
    app = _app()
    host = QWidget()
    host.resize(420, 100)
    row = QHBoxLayout(host)
    margin = _WEARING.space["5"]
    row.setContentsMargins(margin, margin, margin, margin)
    button = QPushButton(text)
    if name:
        button.setObjectName(name)
    button.setCheckable(checkable)
    button.setFixedHeight(_WEARING.control[size])
    row.addWidget(button)
    row.addStretch()
    host.show()
    app.processEvents()
    host.layout().activate()
    app.processEvents()
    _HOSTS.append(host)
    return button


def _pixels(widget, app):
    """`widget` as painted. The app is passed in, not fetched.

    `_app()` reinstalls the sheet on every call, which repolishes every widget
    on every built screen — cheap once, and three seconds a button when it sits
    inside a loop over a hundred of them.
    """
    app.processEvents()
    return widget.grab().toImage()


def _changed(before, after) -> list:
    return [(x, y) for y in range(before.height()) for x in range(before.width())
            if before.pixel(x, y) != after.pixel(x, y)]


def _focused(button, app=None) -> tuple:
    """`button` unfocused then focused: (changed pixels, geometries, image).

    The window is made the active one first. `setFocus` inside an inactive
    window only records where the focus would go if that window ever got it, so
    without this every button measures as painting nothing at all — which reads
    exactly like a ring that is missing, and depends on nothing but which test
    happened to show a window last.

    `app` is a way in for the loops that measure a hundred buttons, and the
    reason is `_app()`: it reinstalls the sheet every call, and a sheet
    reinstalled repolishes every widget on every screen this module has built.
    """
    app = app or _app()
    window = button.window()
    window.activateWindow()
    app.setActiveWindow(window)
    app.processEvents()

    button.clearFocus()
    dark, unlit = QRect(button.geometry()), _pixels(button, app)
    button.setFocus(Qt.TabFocusReason)
    assert button.hasFocus(), \
        "the keyboard will not go to %r, so there is nothing to measure" % button
    bright, lit = QRect(button.geometry()), _pixels(button, app)
    button.clearFocus()
    return _changed(unlit, lit), (dark, bright), lit


def _depth(point, image) -> int:
    """How far `point` sits from the nearest edge of `image`."""
    x, y = point
    return min(x, y, image.width() - 1 - x, image.height() - 1 - y)


def _contents_rect(button) -> QRect:
    """The box Qt lays `button`'s label out in — border and padding removed.

    Asked of the style rather than worked out from the font, because this is the
    rect `outline` was painting on: a ring that touches it is the defect, and a
    ring that stays off it cannot be crossing glyphs that are drawn inside it.
    """
    option = QStyleOptionButton()
    option.initFrom(button)
    option.text = button.text()
    return button.style().subElementRect(
        button.style().SE_PushButtonContents, option, button)


def _painted(image, changed, colour: str) -> list:
    """The points among `changed` that came out exactly `colour`."""
    wanted = colour.upper()
    return [point for point in changed
            if image.pixelColor(*point).name().upper() == wanted]


def _dominant(image, changed) -> str:
    """The colour most of the changed pixels came out."""
    counts: dict = {}
    for point in changed:
        name = image.pixelColor(*point).name().upper()
        counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def _ring_floor(button) -> int:
    """The fewest exact-ink pixels a ring on `button` can honestly be.

    One full side of the button, which is a length rather than a share on
    purpose. The corners cost a fixed ~58 pixels of blend whatever the button
    measures — `radius.md` is `radius.md` on a 496px header button and on a
    38px tab — so a fraction of the changed pixels is a floor that tightens as
    the control gets smaller, and the smallest control here is a 38x31 tab
    where it would leave 10% of headroom. Measured margins against one side:
    57% at the tightest and 200% at the widest.
    """
    return max(button.width(), button.height())


def test_the_sheet_draws_no_button_ring_with_outline():
    """The blocker, at its source.

    Qt paints `outline` on the CONTENTS rect, not outside the border box the
    way CSS does, so a ring declared that way lands inside the padding and comes
    out exactly as wide as the label — a line struck through the text. The
    `outline: none` declarations elsewhere in the sheet are the opposite
    instruction and are fine; anything that draws is not.

    The lookahead used to sit after `\\s*`, which backtracks to nothing, so the
    space in `outline: none` satisfied "not none" and every correct declaration
    read as a violation. It went unnoticed while no QPushButton rule wrote
    `outline` at all; the generated sheet writes one in each of its focus rules
    and the bug fired on all three at once. The whitespace is inside the
    lookahead now, and the two spellings are asserted against directly so the
    next reader can see which way round it goes.
    """
    banned = re.compile(r"\boutline(?:-\w+)?\s*:(?!\s*none\b)")
    assert not banned.search("outline: none;") and \
        not banned.search("outline:none;"), "the guard no longer allows none"
    assert banned.search("outline: 2px solid #FFFFFF;"), \
        "the guard no longer catches a ring"

    for theme in THEMES:
        drawn = [" ".join(rule.split()) for rule in
                 re.findall(r"QPushButton[^{}]*\{[^{}]*\}", _sheet(theme))
                 if banned.search(rule)]
        assert drawn == [], (
            "a button ring is drawn with outline again in %s:\n  %s"
            % (theme.name, "\n  ".join(drawn)))


def test_every_button_variant_lights_up_at_its_own_edge():
    """The ring has to reach the button's perimeter and stay off its label.

    Measured as a pixel diff rather than read off the sheet, because the whole
    defect was a rule that said 2px around the control and painted 2px through
    the words. Each edge is checked on its own: a ring that lost one side would
    still pass a bounding-box test.

    The ring is 1px now rather than 2, so the depth it may reach comes from the
    sheet instead of a constant, and the band the label is checked against is
    the contents rect with that depth taken off it. Both of those are the same
    slack this test always carried; what has changed is that the ring's own
    pixels now sit *on* the contents rect's outermost row, because a 1px border
    is flush against it — and `#rehearsal` is dashed, which Qt paints one row
    inside the solid ring that replaces it. Neither is a line through a label,
    and the deep-pixel check above is what actually catches one: an `outline`
    ring draws its left and right edges 17px in.
    """
    app = _app()
    depth = _ring_depth(THEME)
    for name, size in _VARIANTS:
        button = _variant(name, size)
        changed, _, lit = _focused(button, app)
        variant = name or "the default button"

        assert changed, "%s paints nothing at all when it takes the focus" % variant

        buried = [point for point in changed if _depth(point, lit) > depth]
        assert not buried, (
            "%s changes %d pixels more than %dpx in from its edge, the deepest "
            "at %s — that is the ring painting inside the control"
            % (variant, len(buried), depth,
               max(buried, key=lambda point: _depth(point, lit))))

        label = _contents_rect(button).intersected(
            lit.rect().adjusted(depth, depth, -depth, -depth))
        struck = [point for point in changed if label.contains(*point)]
        assert not struck, (
            "%s draws %d of its %d ring pixels inside %s, where the label is "
            "— that is the line through the text"
            % (variant, len(struck), len(changed), label))

        wide, tall = lit.width(), lit.height()
        for edge, near in (("left", lambda x, y: x <= 1),
                           ("top", lambda x, y: y <= 1),
                           ("right", lambda x, y: x >= wide - 2),
                           ("bottom", lambda x, y: y >= tall - 2)):
            assert any(near(*point) for point in changed), \
                "%s draws no ring along its %s edge" % (variant, edge)


def test_the_ring_is_the_accent_ring_on_every_variant_and_white_on_none():
    """The reversal. This used to require the ring to come out white.

    White was chosen because it was the only ink that cleared 3:1 on every
    variant — 5.99:1 on the default grey, 3.18:1 on #start_btn's green, 6.54:1
    on #live's red. `docs/DESIGN_SYSTEM.md` measured what that cost: 17.01:1 on
    the page beside a selected tab at 1.50:1, a focus mark eleven times the
    selection it competed with, and it rules that no focus treatment may ever
    outweigh the selection beside it — never white, never 2px, never brighter.
    So a white pixel on a focused button is the failure now, and what the ring
    has to be instead is the ink the sheet names for that variant, which is
    read back out of the sheet so the two cannot drift apart.

    "One ink everywhere" survives with one documented exception, and it is the
    exception that proves the rule: `#start_btn` rests on `accent.border`, so a
    ring in `accent.border` repainted its border in the colour it already was
    and changed zero of its 5,800 pixels. It rings in `accent.subtle` instead.

    The old floor was a share of the changed pixels; this one is a length, for
    the reason `_ring_floor` gives, and it is paired with the stronger claim
    the share never made: the ink has to be the colour *most* of the ring came
    out, so a ring that is mostly something else fails even if enough of it is
    right.
    """
    app = _app()
    for name, size in _VARIANTS:
        button = _variant(name, size)
        changed, _, lit = _focused(button, app)
        variant = name or "the default button"
        ink = _ring_inks(THEME)[name]

        bleached = _painted(lit, changed, THEME.color["text.onAccent"])
        assert not bleached, (
            "%s turns %d pixels %s when it takes the focus — the ring is white "
            "again" % (variant, len(bleached), THEME.color["text.onAccent"]))

        assert _dominant(lit, changed) == ink, (
            "%s rings in %s and the sheet says %s"
            % (variant, _dominant(lit, changed), ink))
        ringed = _painted(lit, changed, ink)
        assert len(ringed) >= _ring_floor(button), (
            "%s lights %d pixels of %s and its longest side is %d, so less "
            "than one side of it is ringed"
            % (variant, len(ringed), ink, _ring_floor(button)))


def test_no_button_moves_by_a_pixel_when_the_focus_arrives():
    """Why the ring is a border already there rather than one that grows.

    A border added on `:focus` would widen the box the moment a button was
    tabbed to and shove its whole row along, so the width is held at whatever
    the sheet says in every state and only the colour moves. Both the geometry
    the layout gave the button and the size it asks for are checked, since a
    changed sizeHint is a shove that lands on the next relayout rather than
    immediately.
    """
    app = _app()
    for name, size in _VARIANTS:
        button = _variant(name, size)
        wanted = button.sizeHint()
        _, (dark, bright), _ = _focused(button, app)
        variant = name or "the default button"
        assert dark == bright, \
            "%s moves from %s to %s on focus" % (variant, dark, bright)

        button.setFocus(Qt.TabFocusReason)
        app.processEvents()
        assert button.sizeHint() == wanted, (
            "%s asks for %s focused and %s unfocused"
            % (variant, button.sizeHint(), wanted))
        button.clearFocus()


def test_the_ring_survives_the_two_states_it_must_not_be_confused_with():
    """:hover and :checked are both fills, and both outrank a bare `:focus`.

    Qt ranks selectors by CSS2 specificity and settles ties by source order, so
    `#reveal:hover` — which writes `border-color` — would take the ring off a
    focused button under the pointer if the focus rule came first in the sheet.
    It comes last, and this is what says so.
    """
    app = _app()
    for name, size in (("reveal", "md"), ("tab", "sm"), ("outlined", "md")):
        button = _variant(name, size, checkable=True)
        changed, _, _ = _focused(button, app)
        ink = _ring_inks(THEME)[name]

        for state in ("hovered", "checked"):
            if state == "hovered":
                button.setAttribute(Qt.WA_UnderMouse, True)
            else:
                button.setChecked(True)
            button.style().unpolish(button)
            button.style().polish(button)
            button.setFocus(Qt.TabFocusReason)
            lit = _pixels(button, app)
            ringed = _painted(lit, changed, ink)
            assert _dominant(lit, changed) == ink and \
                len(ringed) >= _ring_floor(button), (
                    "a %s #%s keeps %d of its %d ring pixels %s, and rings "
                    "mostly in %s" % (state, name, len(ringed), len(changed),
                                      ink, _dominant(lit, changed)))
            button.clearFocus()
            button.setChecked(False)
            button.setAttribute(Qt.WA_UnderMouse, False)
            button.style().unpolish(button)
            button.style().polish(button)


def _focusable(screen) -> list:
    return [button for button in screen.findChildren(QPushButton)
            if (button.focusPolicy() & Qt.TabFocus) and button.isEnabled()
            and button.isVisible() and button.width() > 4 and button.height() > 4]


def test_every_focusable_button_on_every_screen_wears_the_ring():
    """The sweep the reported defect needed: 29 of 36 buttons were struck through.

    Over every page of every screen, because the variants are not spread evenly
    — #reveal exists only on Gmail, #start_btn only in a header — and the button
    holding the focus when the app opens was among the broken ones.
    """
    app, struck, seen = _app(), [], 0
    depth, inks = _ring_depth(THEME), _ring_inks(THEME)
    for kind in ("input", "results", "outreach", "settings"):
        screen = _sized(_screen(kind), DEFAULT_SIZE)
        pages = getattr(screen, "pages", None)
        opened = pages.currentIndex() if pages is not None else None
        for index in (range(pages.count()) if pages is not None else [None]):
            if index is not None:
                pages.setCurrentIndex(index)
                _sized(screen, DEFAULT_SIZE)
            for button in _focusable(screen):
                seen += 1
                changed, (unlit, focused), lit = _focused(button, app)
                ink = inks.get(button.objectName(), inks[""])
                label = _contents_rect(button).intersected(
                    lit.rect().adjusted(depth, depth, -depth, -depth))
                if not changed:
                    fault = "paints no ring"
                elif [p for p in changed if label.contains(*p)]:
                    fault = "draws its ring through the label"
                elif [p for p in changed if _depth(p, lit) > depth]:
                    fault = "draws its ring inside the control"
                elif unlit != focused:
                    fault = "moves from %s to %s" % (unlit, focused)
                elif _painted(lit, changed, THEME.color["text.onAccent"]):
                    fault = "rings in white"
                elif _dominant(lit, changed) != ink:
                    fault = "rings mostly in %s, not %s" % (
                        _dominant(lit, changed), ink)
                elif len(_painted(lit, changed, ink)) < _ring_floor(button):
                    fault = "rings %d pixels of %s down a %dpx side" % (
                        len(_painted(lit, changed, ink)), ink,
                        _ring_floor(button))
                else:
                    continue
                struck.append("%s/%s %r %s" % (kind, button.objectName()
                                               or "default", button.text(), fault))
        if opened is not None:
            pages.setCurrentIndex(opened)
            _sized(screen, DEFAULT_SIZE)

    assert seen >= 36, "only %d focusable buttons were reachable" % seen
    assert not struck, "%d of %d buttons:\n  %s" % (len(struck), seen,
                                                    "\n  ".join(struck))


# ── U11: the shell every screen now sits in ──────────────────────────────────

_WINDOW: list = []


def _window():
    """The one MainWindow for this module, over the same throwaway profile.

    Built once and kept, for the reason every other host in this file is: Qt on
    the offscreen platform does not survive a test that churns top-level windows
    while it still points at one of them as the active window.
    """
    if not _WINDOW:
        _app()
        window = APP.MainWindow()
        window.resize(QSize(*DEFAULT_SIZE))
        window.show()
        _app().processEvents()
        _WINDOW.append(window)
    return _WINDOW[0]


@contextlib.contextmanager
def _stubbed(owner, name: str, replacement):
    """One method replaced on one instance, and put back afterwards.

    `on_start` ends in `start_worker`, which opens a browser and scrapes Google
    Maps. The route is what these tests are about, so the two calls that would
    do real work arrive as recordings instead.
    """
    owner.__dict__[name] = replacement
    try:
        yield
    finally:
        owner.__dict__.pop(name, None)


def _bar(window) -> QWidget:
    """The navigation rail. Still `app_bar`, because it is still the one bar.

    It was a strip across the top and it is a rail down the left, and every
    assertion here that used to read its height now reads its width. The
    objectName did not move with it on purpose: the sheet's `QWidget#app_bar`
    rule is the same rule — the app's one piece of persistent chrome, grounded
    in `surface` — and a second name for it would be a second rule to keep in
    step.
    """
    return window.findChild(QWidget, "app_bar")


def _head(window) -> QWidget:
    """The page header inside the content pane: title, description, state."""
    return window.findChild(QWidget, "results_header")


def _sub(window) -> QWidget:
    """The section row under the header, which exists only while the rail is
    collapsed. Open, a screen's sections are indented rows in the rail itself."""
    return window.findChild(QWidget, "sub_bar")


def _bar_buttons(window, kind: str = "") -> list:
    """Every button in the shell's chrome, matched on objectName or on kind.

    Three widgets now rather than one, which is the whole of why this had to
    change: the destinations are in the rail, the dry-run pill and the theme
    toggle are in the page header, and a screen's sections are in whichever of
    the two has room for them.
    """
    found = []
    for part in (_bar(window), _head(window), _sub(window)):
        if part is None:
            continue
        for button in part.findChildren(QPushButton):
            if (not kind or button.objectName() == kind
                    or button.property("kind") == kind):
                found.append(button)
    return found


def _nav_rows(window, depth: int = 0) -> list:
    """The rail's rows at one depth: destinations at 0, sections at 1."""
    return [button for button in _bar(window).findChildren(QPushButton)
            if button.property("kind") == "nav"
            and button.property("depth") == depth]


def _sections(window) -> list:
    """A screen's sections wherever the shell has put them."""
    if window.shell.collapsed():
        return [b for b in _sub(window).findChildren(QPushButton)]
    return _nav_rows(window, depth=1)


def _context_lines(window) -> list:
    """The shell's line of state, which is not the page's own description.

    Width and not only `isVisible`, because those two disagreed and the answer
    was 0px: an `Ignored` size policy in a row with a stretching item beside it
    is handed no width at all, so the line was laid out, reported visible, and
    painted nothing on every screen in the app.
    """
    return [label.full_text() for label in _head(window).findChildren(QLabel)
            if label.property("role") == "context" and label.isVisible()
            and label.full_text() and label.width() > 0]


def _wide(window) -> None:
    """Wide enough that the rail opens itself, and laid out at that size."""
    _sized(window, (APP.RAIL_BREAKPOINT, DEFAULT_SIZE[1]))


def test_the_window_builds_the_screen_it_shows_and_not_the_other_three():
    """The measurement this whole shell exists for.

    `MainWindow.__init__` used to construct all four screens — the 2747-line
    settings screen among them — before the user had asked for any: 691 widgets
    and 531ms to show a home screen made of nine. Registering a factory and
    calling it on the first visit builds one, and the screen modules are
    imported inside those factories, so the 2591-line outreach screen is not
    even parsed until someone goes there.
    """
    window = _window()
    assert window.shell.current_key == APP.INPUT
    assert window.shell.built() == (APP.INPUT,), \
        "the window built %s before anyone asked" % (window.shell.built(),)
    for key in (APP.RESULTS, APP.OUTREACH, APP.SETTINGS):
        assert window.shell.built(key) is None, "%s was built eagerly" % key

    window.shell.go(APP.OUTREACH)
    _app().processEvents()
    assert set(window.shell.built()) == {APP.INPUT, APP.OUTREACH}
    assert window.shell.built(APP.SETTINGS) is None, \
        "visiting Outreach built the settings screen too"
    assert window.shell.screen(APP.OUTREACH) is window.shell.built(APP.OUTREACH), \
        "the second visit built a second screen"
    window.shell.go(APP.INPUT)


def test_one_rail_at_one_width_over_every_screen():
    """Was `test_one_bar_at_one_height_over_every_screen`, inverted twice.

    The finding it was written for has not changed: four screens carried four
    top bars — 31px, 70px, 50px, 31px — so where the user was and how to leave
    was answered differently on each. There is still exactly one piece of
    persistent chrome and it is still the shell's. It is a rail now, so the
    measurement is `components.rail_width` and not `control.header`.

    The second inversion is Settings. It used to be a button on the right of the
    bar and deliberately marked nothing, so this asserted that the bar showed no
    selection at all while the user was on it — which meant the one screen a
    user can get lost in was the one screen the chrome refused to locate them
    on. Settings is a row in the rail's footer now, and it is marked like any
    other row.
    """
    window = _window()
    bar = _bar(window)
    assert bar is not None, "the window has no rail"
    for key in (APP.RESULTS, APP.OUTREACH, APP.SETTINGS, APP.INPUT):
        window.shell.go(key)
        _sized(window, DEFAULT_SIZE)
        assert len(window.findChildren(QWidget, "app_bar")) == 1, \
            "%s brought a second rail with it" % key
        # Identity, not just presence: a collapsed rail draws no sections, so
        # nothing about it changes when the user moves between screens and it
        # must not be rebuilt for one. `_sync` runs once per message of a
        # running campaign as well, and this is what says it is cheap.
        assert _bar(window) is bar, "the rail was rebuilt on the way to %s" % key
        wanted = CO.rail_width(THEME, collapsed=window.shell.collapsed())
        assert _bar(window).isVisible() and _bar(window).width() == wanted, (
            "the rail is %dpx on %s and rail_width says %d"
            % (_bar(window).width(), key, wanted))
        assert _bar(window).height() == window.shell.height(), \
            "the rail does not run the height of the shell on %s" % key

        checked = [button.accessibleName() for button in _nav_rows(window)
                   if button.isChecked()]
        assert checked == [window.shell._labels[key]], \
            "the rail says %s and the user is on %s" % (checked, key)


def test_every_navigation_route_still_arrives_where_it_did():
    """The eight routes the window carried before the shell, one at a time.

    Both halves of the scrape hand-off are recorded rather than run, and the key
    the shell is showing is recorded with them: `setup` has to happen before the
    switch so the screen is never painted holding the last run's rows, and
    `start_worker` after it so the first row arrives on a screen the user can
    already see.
    """
    window = _window()
    shell = window.shell
    inputs = shell.screen(APP.INPUT)
    results = shell.screen(APP.RESULTS)
    outreach = shell.screen(APP.OUTREACH)
    settings = shell.screen(APP.SETTINGS)

    seen = []
    with _stubbed(results, "setup",
                  lambda *a, **k: seen.append(("setup", shell.current_key))), \
            _stubbed(results, "start_worker",
                     lambda: seen.append(("start", shell.current_key))), \
            _stubbed(results, "stop_worker",
                     lambda: seen.append(("stop", shell.current_key))):
        shell.go(APP.INPUT)
        inputs.start_signal.emit(["plumbers"], ["Toronto"], ["name"], False,
                                 10, "", {})
        assert shell.current_key == APP.RESULTS
        assert seen == [("setup", APP.INPUT), ("start", APP.RESULTS)], \
            "the scrape hand-off ran as %s" % seen

        results.stop_signal.emit()
        assert seen[-1] == ("stop", APP.RESULTS)

    results.home_signal.emit()
    assert shell.current_key == APP.INPUT

    records = [{"name": "Zeta Roofing", "email": "zeta@example.com"}]
    handed = []
    with _stubbed(outreach, "load_from_results", handed.append):
        results.outreach_signal.emit(records)
    assert shell.current_key == APP.OUTREACH
    assert handed == [records], "the records never reached the outreach screen"

    outreach.home_signal.emit()
    assert shell.current_key == APP.INPUT
    inputs.outreach_signal.emit()
    assert shell.current_key == APP.OUTREACH
    outreach.settings_signal.emit()
    assert shell.current_key == APP.SETTINGS
    settings.back_signal.emit()
    assert shell.current_key == APP.OUTREACH

    shell.go(APP.INPUT)
    inputs.settings_signal.emit()
    assert shell.current_key == APP.SETTINGS
    settings.back_signal.emit()
    assert shell.current_key == APP.INPUT


def test_a_finished_scrape_reaches_outreach_without_taking_the_user_there():
    """The ninth route, and the only one of them that is not a navigation.

    `harvested_signal` is how a finished scrape joins the pool whether or not
    anybody asked for it, and it was the one route in the list above with no
    test of any kind: the eight that navigate are each covered by the key they
    land on, and this one is covered by nothing precisely because it lands
    nowhere. What it has to do is reach the outreach screen — which is lazy, so
    a harvest that waited for a visit would be a harvest lost to the next Home —
    and leave the user exactly where they were, because a scrape finishing while
    its owner reads the results is not a request to be moved off them.
    """
    window = _window()
    shell = window.shell
    outreach = shell.screen(APP.OUTREACH)
    records = [{"name": "Zeta Roofing", "email": "zeta@example.com"}]

    shell.go(APP.RESULTS)
    kept = []
    with _stubbed(outreach, "absorb_scrape", kept.append):
        shell.screen(APP.RESULTS).harvested_signal.emit(records)
    assert kept == [records], "the finished scrape never reached the pool"
    assert shell.current_key == APP.RESULTS, \
        "a finished scrape moved the user to %s" % shell.current_key

    # And from anywhere else, because the signal is the screen's and not the
    # shell's: a user who pressed Home while the scrape ran is the case the
    # route exists for.
    shell.go(APP.INPUT)
    with _stubbed(outreach, "absorb_scrape", kept.append):
        shell.screen(APP.RESULTS).harvested_signal.emit(records)
    assert kept == [records, records]
    assert shell.current_key == APP.INPUT


def test_back_out_of_settings_returns_to_the_screen_it_was_opened_from():
    """Opened from a half-built campaign, Back may not answer with Home."""
    window = _window()
    shell = window.shell
    for key in (APP.OUTREACH, APP.RESULTS, APP.INPUT):
        shell.go(key)
        window.on_settings()
        assert shell.current_key == APP.SETTINGS
        shell.screen(APP.SETTINGS).back_signal.emit()
        assert shell.current_key == key, \
            "Back from settings opened on %s landed on %s" % (
                key, shell.current_key)

    shell.go(APP.OUTREACH)
    window.on_settings()
    window.on_settings()
    shell.screen(APP.SETTINGS).back_signal.emit()
    assert shell.current_key == APP.OUTREACH, \
        "settings opened from settings made settings the way back"
    shell.go(APP.INPUT)


def test_the_chrome_says_whether_the_next_campaign_mails_real_people():
    """Dry run is the one piece of global state a wrong guess about is expensive.

    It lived on one tab of one screen, then on the top bar; it is in the page
    header now, in the two shapes the sheet keeps for it — dashed for a
    rehearsal, filled red for live. The header is where it belongs and the rail
    is not: the rail collapses to 56px, and the one control in the app that says
    whether Start mails real businesses may not be a control that can lose its
    label.
    """
    window = _window()
    saved = dict(window.settings)
    try:
        pill = [b for b in _bar_buttons(window)
                if b.objectName() in ("rehearsal", "live")]
        assert len(pill) == 1 and pill[0].text() == "Dry run"
        assert pill[0].objectName() == "rehearsal"

        window.on_settings_saved(dict(saved, dry_run=False))
        _app().processEvents()
        pill = [b for b in _bar_buttons(window)
                if b.objectName() in ("rehearsal", "live")]
        assert len(pill) == 1 and pill[0].text() == "LIVE", \
            "the bar still says %r with dry run off" % pill[0].text()
        assert pill[0].objectName() == "live"
    finally:
        window.on_settings_saved(saved)
        _app()


def test_a_screen_hands_its_sub_tabs_to_the_shell_and_they_stay_with_it():
    """One place the user looks to know where they are, in both rail states.

    `set_subtabs` is the same call it always was and the screens that make it
    know nothing about where the row lands. What changed is that there are two
    answers: indented rows under the destination while the rail is open, which
    is how Finder and System Settings show a section, and the row under the page
    header while it is collapsed — because 56px does not hold a word, and a
    section that cannot be reached with a mouse has been removed.

    The row is published *after* the navigation and not before it, which is the
    order that makes this test test anything. Arriving on Outreach runs the
    screen's own opener, and the outreach screen answers it by publishing its
    real row with its real selected index — so a fixture row installed first is
    overwritten before the first assertion reads it, and what the assertion then
    measures is whichever tab the screen happened to be left on by an earlier
    test in this module. It passed on that leftover and failed the moment it was
    run on its own.
    """
    window = _window()
    shell = window.shell
    picked = []
    tabs = ("Leads", "Campaign", "Sending", "Stats")
    shell.go(APP.OUTREACH)
    _sized(window, DEFAULT_SIZE)
    shell.set_subtabs(APP.OUTREACH, tabs, picked.append, current=1)
    assert shell.collapsed(), "the default window is wide enough for an open rail"

    row = _sub(window)
    assert row.isVisible(), "the section row is not on screen"
    buttons = _sections(window)
    assert [button.text() for button in buttons] == list(tabs)
    assert [button.text() for button in buttons if button.isChecked()] == \
        ["Campaign"], "the row does not say which section is open"

    buttons[2].click()
    _app().processEvents()
    assert picked == [2], "clicking a section told the screen %s" % picked
    assert [b.text() for b in _sections(window) if b.isChecked()] == ["Sending"]

    # Open the rail and the same four move into it, indented, still on Sending.
    _wide(window)
    assert not shell.collapsed(), "the rail did not open on a wide window"
    assert not _sub(window).isVisible(), \
        "the section row is still taking height under an open rail"
    inside = _nav_rows(window, depth=1)
    assert [b.accessibleName() for b in inside] == list(tabs), \
        "the open rail draws %s" % [b.accessibleName() for b in inside]
    assert [b.accessibleName() for b in inside if b.isChecked()] == ["Sending"]
    inside[0].click()
    _app().processEvents()
    assert picked == [2, 0], "a rail section told the screen %s" % picked

    # And the destination they belong to is not itself filled while they show:
    # two stacked pills is one block, not two marks.
    parent = [b for b in _nav_rows(window)
              if b.accessibleName() == shell.label(APP.OUTREACH)][0]
    assert not parent.isChecked(), \
        "the destination and its open section are both filled"
    assert parent.property("current") is True, \
        "nothing in the rail says which destination is open"

    _sized(window, DEFAULT_SIZE)
    shell.go(APP.INPUT)
    _sized(window, DEFAULT_SIZE)
    assert not _sections(window), \
        "one screen's sections followed the user to another"
    assert not _sub(window).isVisible(), \
        "an empty section row still takes height from the page"

    shell.go(APP.OUTREACH)
    assert [b.text() for b in _sections(window)] == list(tabs)
    shell.set_subtabs(APP.OUTREACH, (), None)
    shell.go(APP.INPUT)


def test_the_footer_destination_draws_its_sections_like_any_other():
    """Settings is in the rail's footer, and it has the most sections of all.

    The test above proves the rail draws a *destination's* sections and proves
    nothing about the footer, which is a different loop in `_make_bar` — and
    the footer loop drew the row and not the sections under it. What that cost
    is the whole screen: the section row under the page header is hidden while
    the rail is open, because open is where sections are supposed to live, so
    with the rail open on Settings the user saw the AI tab and had no way to
    reach Sender, Templates, Gmail, Sending, Compliance or Appearance by mouse
    at all. Six of seven tabs, reachable only from the command palette.

    Driven through the real screen and not a fixture row, because the fixture
    is what hid it: `set_subtabs` on a key that happens to be a destination
    exercises the loop that always worked.
    """
    window = _window()
    shell = window.shell
    window.on_settings()
    _wide(window)
    assert not shell.collapsed(), "the rail did not open on a wide window"

    published, _on_change, _current = shell._subtabs.get(
        APP.SETTINGS, ((), None, 0))
    assert len(published) > 1, \
        "the settings screen published %s, so this proves nothing" % (published,)

    inside = _nav_rows(window, depth=1)
    assert [button.accessibleName() for button in inside] == list(published), (
        "the open rail draws %s under Settings, which published %s"
        % ([button.accessibleName() for button in inside], list(published)))
    assert not _sub(window).isVisible(), \
        "the section row is taking height under an open rail"

    # And every one of them actually moves the screen, which is the claim that
    # matters: a row that is drawn and inert is the same bug with a picture.
    screen = shell.screen(APP.SETTINGS)
    inside[-1].click()
    _app().processEvents()
    assert [b.accessibleName() for b in _nav_rows(window, depth=1)
            if b.isChecked()] == [published[-1]], \
        "the last section did not take the selection"
    assert screen.pages.currentIndex() == len(published) - 1, \
        "the rail moved its own mark and not the screen"

    inside = _nav_rows(window, depth=1)
    inside[0].click()
    _app().processEvents()
    assert screen.pages.currentIndex() == 0

    footer = [b for b in _nav_rows(window)
              if b.accessibleName() == shell.label(APP.SETTINGS)][0]
    assert not footer.isChecked(), \
        "Settings and its open section are both filled"
    assert footer.property("current") is True, \
        "nothing in the rail says Settings is the open destination"

    window.on_settings_closed()
    _sized(window, DEFAULT_SIZE)


def test_the_shell_carries_one_line_of_context_per_screen():
    """Moved into the page header with the rest of the global state.

    It used to sit at the right-hand end of the sub-tab row, which meant a
    screen with no sections had to grow a row for one line of text, and a screen
    with sections had its state competing with its navigation for the same
    24px. The header carries it now — beside the dry-run pill, where the rest of
    what is true everywhere already is.
    """
    window = _window()
    shell = window.shell
    shell.set_context(APP.INPUT, "3 saved searches", tone="info")
    shell.go(APP.INPUT)
    _sized(window, DEFAULT_SIZE)
    assert _context_lines(window) == ["3 saved searches"]

    # Whole, and at every size the window opens at: it competes for the header's
    # width with the description beside it, and losing that competition is the
    # 0px failure `_context_lines` measures for.
    for size in (MINIMUM_SIZE, DEFAULT_SIZE, AUDIT_SIZE):
        _sized(window, size)
        line = [lb for lb in _head(window).findChildren(QLabel)
                if lb.property("role") == "context"][0]
        assert not line.is_elided(), \
            "the context line is cut at %dx%d" % size
        assert line.width() >= line.sizeHint().width(), (
            "%d: the line is %dpx and its sentence needs %d"
            % (size[0], line.width(), line.sizeHint().width()))
    _sized(window, DEFAULT_SIZE)

    shell.go(APP.OUTREACH)
    _sized(window, DEFAULT_SIZE)
    assert not [lb for lb in _head(window).findChildren(QLabel)
                if lb.property("role") == "elided" and lb.isVisible()
                and lb.full_text() == "3 saved searches"], \
        "the context line followed the user off the screen it belongs to"

    shell.go(APP.INPUT)
    shell.set_context(APP.INPUT, "")
    _sized(window, DEFAULT_SIZE)
    assert _context_lines(window) == [],         "an emptied context line is still showing"


def test_the_theme_changes_while_the_app_is_running():
    """A theme toggle that needs a restart is not a theme toggle.

    Measured as paint rather than as state: the bar is grabbed in each palette
    and the colour most of it comes out has to be that palette's own `surface`.
    The preference is written to the settings file as well, because a toggle
    that forgets is the same defect one launch later.
    """
    window = _window()
    saved = dict(window.settings)
    app = QApplication.instance()
    try:
        window.shell.go(APP.INPUT)
        _sized(window, DEFAULT_SIZE)
        painted = _histogram(_bar(window))
        assert max(painted, key=painted.get) == THEME.color["surface"].upper()

        window.toggle_theme()
        app.processEvents()
        light = TH.theme("light")
        assert window.theme.name == "light"
        assert app.styleSheet() == TH.stylesheet(light), \
            "the sheet on the application is not the light one"
        assert ST.load_settings()["theme"] == "light", \
            "the choice was not written to the settings file"

        window.layout().activate()
        app.processEvents()
        painted = _histogram(_bar(window))
        assert max(painted, key=painted.get) == light.color["surface"].upper(), (
            "the bar still paints %s and the light surface is %s"
            % (max(painted, key=painted.get), light.color["surface"].upper()))

        window.toggle_theme()
        app.processEvents()
        painted = _histogram(_bar(window))
        assert max(painted, key=painted.get) == THEME.color["surface"].upper(), \
            "the bar did not come back to the dark palette"
    finally:
        window.settings = dict(saved)
        ST.save_settings(saved)
        window.apply_appearance(saved)
        _app()


def test_the_compact_density_reaches_the_controls_without_a_restart():
    """The other half of what the settings file could not ask for.

    Measured on the rail rather than on a bar tab, and on both of the two
    numbers a density owns there: the height of a row, which is `control.lg`,
    and the width of the rail when it is collapsed, which `rail_width` composes
    out of `control.md` and the grid. The open width is five 40px steps and is
    the same in both densities on purpose — a destination name is the same
    number of characters however tall the row holding it is.
    """
    window = _window()
    saved = dict(window.settings)
    try:
        window.shell.go(APP.INPUT)
        _sized(window, DEFAULT_SIZE)
        assert _nav_rows(window)[0].height() == THEME.control["lg"]
        assert _bar(window).width() == CO.rail_width(THEME, collapsed=True)

        window.apply_appearance(dict(saved, density="compact"))
        _sized(window, DEFAULT_SIZE)
        compact = TH.theme(THEME.name, "compact")
        assert window.theme.density == "compact"
        assert _nav_rows(window)[0].height() == compact.control["lg"], (
            "a compact rail row is %dpx and control.lg is %d"
            % (_nav_rows(window)[0].height(), compact.control["lg"]))
        assert _bar(window).width() == CO.rail_width(compact, collapsed=True), (
            "a compact collapsed rail is %dpx and rail_width says %d"
            % (_bar(window).width(), CO.rail_width(compact, collapsed=True)))

        _wide(window)
        assert _bar(window).width() == CO.rail_width(compact), \
            "the open rail is not the width the tokens compose"
    finally:
        window.apply_appearance(saved)
        _sized(window, DEFAULT_SIZE)
        assert _bar(window).width() == CO.rail_width(THEME, collapsed=True)


def test_shutting_the_window_stops_only_the_screens_that_exist():
    """The window used to hold four screens; it now holds what it has built."""
    window = _window()
    window.shutdown_worker()
    for screen in window.shell.screens():
        assert APP._screen_threads(screen) == [], \
            "%r is still running a thread after shutdown" % screen


# ── U12: the first run, the table's widths, and the page's measure ───────────
# The auditor's worst journey in the product is not visual. Email was unticked
# by default, so the default scrape produced no email column; Start Outreach
# then imported nothing; and leaving Results made the scraped table permanently
# unreachable, so the way to find out about the checkbox was a full second run
# of Chrome. Everything in this section is one of the three parts of that, or
# one of the two geometry findings on the screens it happens on.

SITE = "https://www.harbourfrontdental-%02d.example.com"
MAIL = "reception%02d@harbourfrontdental.example.com"
STREET = "%d King Street West, Suite 1200, Toronto, ON M5H 1A1"

LEAD_FIELDS = ["name", "category", "rating", "review_count", "address",
               "website", "phone", "email"]

# 1280 is the size the audit measured the clipping at and 2560 the one it
# measured the ballooning at; `MINIMUM_SIZE` and `DEFAULT_SIZE` are the two the
# window itself uses.
WIDE_SIZE, AUDIT_SIZE = (2560, 1440), (1280, 860)

# What a 44-character address — the length the audit named — takes in the
# table's own 12px, plus the cell's padding.
EMAIL_PX = 275


def _leads(count: int = 20) -> list:
    return [{"name": "Harbourfront Dental Care %d" % i,
             "category": "Roofing contractor",
             "rating": "4.%d" % (i % 10),
             "review_count": "%d" % (120 + i * 37),
             "address": STREET % (10 + i),
             "website": SITE % i,
             "phone": "+1 416-555-01%02d" % i,
             "email": MAIL % i} for i in range(count)]


def _filled(screen, rows: list):
    """The results screen holding `rows`, over the throwaway profile."""
    screen.setup(["dentists"], ["Toronto"], LEAD_FIELDS, max_results=len(rows),
                 export_dir=_TMP)
    for row in rows:
        screen.add_table_row(row)
    return screen


def _column(screen, field: str) -> int:
    return screen.fields.index(field)


def _elided(screen, field: str) -> int:
    column = _column(screen, field)
    return sum(1 for row in range(screen.table.rowCount())
               if screen.table.is_elided(row, column))


def _refilled(edit, text: str) -> None:
    """Replace whatever is in `edit` with `text`, typed rather than assigned.

    Not `_typed`, which this used to be called and which is defined in U9 above
    for a caret contract it is the opposite of: that one adds text at the caret,
    this one clears the box first. Two functions of one name in one module meant
    the later definition answered both, so U9's "the keystroke after the insert
    lands where the caret was left" was measured against a box that had just
    been emptied — and passed on a screen that had thrown the insert away.
    """
    edit.setFocus(Qt.OtherFocusReason)
    edit.clear()
    QTest.keyClicks(edit, text)


def test_the_default_field_set_comes_back_with_an_address_to_mail():
    """Part one of the worst journey, and the only part that is one checkbox.

    `email` was in `DEFAULT_OFF_FIELDS` because it is slow — it fetches each
    business website — so the scrape a new user runs without changing anything
    returned no email column at all, and the campaign built from it had nobody
    to send to. It is still slow and it is on anyway: the address is the one
    field the product exists to produce.
    """
    screen = _screen("input")
    fields = screen.get_checked_fields()
    assert "email" in fields, \
        "the default scrape still collects no address: %s" % fields
    assert "email" not in screen.DEFAULT_OFF_FIELDS
    assert "email" in screen.SLOW_FIELDS, \
        "the cost of the default has stopped being recorded"
    assert screen.ENRICH_FIELDS - {"email"} <= set(screen.DEFAULT_OFF_FIELDS), \
        "the socials came on with it, and they are the slow half"

    try:
        screen._select_fast_fields()
        assert "email" not in screen.get_checked_fields(), \
            "Fast only has to still mean fast"
        screen._select_default_fields()
        assert "email" in screen.get_checked_fields(), \
            "there is no one-click way back to the shipping default"
    finally:
        screen._select_default_fields()


def test_the_export_folder_is_filled_in_before_anyone_is_asked():
    """The modal dialog that stood between a fresh install and its first scrape.

    The rule — the folder has to exist — was enforced by a 200ms shake, so the
    first thing this screen did to a new user was refuse to start and not say
    why. It arrives filled in with a real path, and the path is made on the way
    past rather than demanded first.
    """
    screen = _screen("input")
    saved = screen.export_dir()
    assert saved, "the export folder is empty on a fresh screen again"
    assert os.path.basename(saved) == "LeadForge", \
        "the default export folder is %r" % saved

    wanted = os.path.join(_TMP, "exports", "first-run")
    assert not os.path.isdir(wanted)
    try:
        screen.export_dir_input.setText(wanted)
        _refilled(screen.domain_input, "dentists")
        _refilled(screen.area_input, "Toronto")
        validated = screen.validate()
        assert validated is not None, "a folder that can be made was refused"
        assert os.path.isdir(wanted), "the folder was not made"
        assert validated[3] == wanted
    finally:
        screen.export_dir_input.setText(saved)
        screen.domain_input.clear()
        screen.area_input.clear()


def test_an_invalid_field_answers_in_words_beside_itself():
    """Part of the same thing: the shake said nothing and moved a control 6px.

    Every rule this screen keeps is now a sentence under the field it belongs
    to, and the field takes the keyboard. The control that may not move is the
    one that was just clicked, and Start Scraping is in the footer rather than
    in the scrolling page, so it is where it was.
    """
    screen = _sized(_screen("input"), DEFAULT_SIZE)
    saved = screen.export_dir()
    try:
        screen.domain_input.clear()
        screen.area_input.clear()
        before = screen.start_btn.mapTo(screen, QPoint(0, 0))

        assert screen.validate() is None
        _app().processEvents()
        assert screen.domain_field.error.isVisible(), \
            "an empty domain still refuses in silence"
        assert len(screen.domain_field.error.text()) > 20, \
            "the error is %r" % screen.domain_field.error.text()
        # `focusWidget`, not `hasFocus`: the latter is also a claim about which
        # top-level window is active, and every other host in this file takes
        # that away on its way past.
        assert screen.focusWidget() is screen.domain_input, \
            "the field the message is about does not have the keyboard"
        assert screen.domain_field.error.mapTo(screen, QPoint(0, 0)).y() > \
            screen.domain_input.mapTo(screen, QPoint(0, 0)).y(), \
            "the message is not under the field it is about"
        assert screen.start_btn.mapTo(screen, QPoint(0, 0)) == before, \
            "the button that was just clicked moved out from under the pointer"

        _refilled(screen.domain_input, "dentists")
        assert not screen.domain_field.error.isVisible(), \
            "the error outlived the thing it was about"

        assert screen.validate() is None
        assert screen.area_field.error.isVisible(), "the next rule says nothing"

        _refilled(screen.area_input, "Toronto")
        screen.export_dir_input.setText(os.path.join(_TMP, "no*such:folder"))
        assert screen.validate() is None
        assert screen.export_field.error.isVisible(), \
            "a folder that cannot be made is refused in silence"

        screen.export_dir_input.setText(_TMP)
        for box in screen.checkboxes.values():
            box.setChecked(False)
        assert screen.validate() is None
        assert screen.fields_error.isVisible(), \
            "an empty field list is refused with a 600ms colour change again"
    finally:
        screen._select_default_fields()
        screen.export_dir_input.setText(saved)
        screen.domain_input.clear()
        screen.area_input.clear()
        screen._clear_errors()


def test_a_fresh_install_reaches_outreach_without_a_second_scrape():
    """The loop, end to end, counted in the clicks it costs.

    Eleven on these two screens before: two fields, three for the folder
    dialog, Start, Start Outreach into an empty campaign, back to Home, the
    Email checkbox, Start again, Start Outreach again — and the second Start is
    a full re-run of Chrome. Four now, and this test is the four: the domain,
    the area, Start Scraping, Start Outreach. Everything else the run needs is
    already true when the screen is built.
    """
    screen = _screen("input")
    results = _screen("results")
    saved_dir = screen.export_dir()
    exports = os.path.join(_TMP, "first-loop")
    started, handed = [], []
    screen.start_signal.connect(lambda *a: started.append(a))
    results.outreach_signal.connect(handed.append)
    try:
        screen.export_dir_input.setText(exports)
        _refilled(screen.domain_input, "dentists")        # click 1
        _refilled(screen.area_input, "Toronto")           # click 2
        screen.start_btn.click()                       # click 3

        assert len(started) == 1, "Start Scraping refused the default screen"
        domains, areas, fields, _headless, limit, export_dir, _filters = started[0]
        assert domains == ["dentists"] and areas == ["Toronto"]
        assert "email" in fields, "the first scrape still has no address column"
        assert export_dir == exports and os.path.isdir(exports)
        assert limit > 0

        _filled(results, _leads(3))
        results.outreach_btn.click()                   # click 4
        assert len(handed) == 1 and len(handed[0]) == 3, \
            "the hand-off carried %s" % (handed,)
        assert all(record.get("email") for record in handed[0]), \
            "the leads handed to Outreach still have no address to send to"
    finally:
        screen.start_signal.disconnect()
        results.outreach_signal.disconnect()
        results._set_idle_mode()
        screen.export_dir_input.setText(saved_dir)
        screen.domain_input.clear()
        screen.area_input.clear()


def test_the_scrape_is_still_reachable_after_leaving_the_results_screen():
    """The third part, and the one that cost a re-scrape all on its own.

    Home had no control that returned to Results, and coming back in went
    through `setup`, which clears `self.results` — so the rows a run had just
    produced were gone for good the moment the user pressed Home. The shell
    carries Results as a destination and opens it without arguments, so the
    table survives the round trip.
    """
    window = _window()
    shell = window.shell
    results = shell.screen(APP.RESULTS)
    rows = _leads(4)
    try:
        _filled(results, rows)
        assert results.table.rowCount() == len(rows)
        assert "Results" in [button.accessibleName()
                             for button in _nav_rows(window)], \
            "the rail carries no way back to the scrape"

        shell.go(APP.INPUT)
        shell.go(APP.RESULTS)
        _app().processEvents()

        assert shell.screen(APP.RESULTS) is results, "a second screen was built"
        assert results.table.rowCount() == len(rows), (
            "the round trip emptied the table: %d rows left"
            % results.table.rowCount())
        assert [record["email"] for record in results.results] == \
            [record["email"] for record in rows]
        assert results.table_stack.currentWidget() is results.table
    finally:
        results._set_idle_mode()
        shell.go(APP.INPUT)


def test_the_results_table_hands_its_width_to_the_columns_that_carry_it():
    """The first critical geometry finding, at the three sizes it was found at.

    Every column was a flat 130px with `stretchLastSection` on, so at 1280 with
    20 leads Website, Email and Address were cut in 20 of 20 rows while Rating
    had 115px of slack and Review Count 250px; at 880 the same table scrolled
    400px sideways; at 2560 the last column alone took 1550px. The spec sizes a
    short fixed value to its content and shares the rest between the columns
    that carry meaning, so what follows is the same table three times.
    """
    screen = _filled(_screen("results"), _leads(20))
    try:
        measured = {}
        for size in (MINIMUM_SIZE, AUDIT_SIZE, WIDE_SIZE):
            _sized(screen, size)
            table = screen.table
            widths = {field: table.columnWidth(_column(screen, field))
                      for field in LEAD_FIELDS}
            measured[size[0]] = widths

            assert not table.horizontalScrollBar().maximum(), (
                "%d: the table scrolls %dpx sideways"
                % (size[0], table.horizontalScrollBar().maximum()))
            assert sum(widths.values()) <= table.viewport().width(), \
                "%d: the columns are wider than the table" % size[0]

            # Nothing that does not fit may be silent about it: 11 of the 16
            # cut cells the audit counted had no tooltip at all.
            tipless = [(row, column)
                       for row in range(table.rowCount())
                       for column in range(table.columnCount())
                       if table.is_elided(row, column)
                       and not table.tooltip_at(row, column)]
            assert not tipless, (
                "%d: %d cut cells answer a hover with nothing"
                % (size[0], len(tipless)))

            # A number is as wide as the number, whatever the window is.
            for field in ("rating", "review_count"):
                assert widths[field] <= widths["email"] // 2, (
                    "%d: %s takes %dpx beside a %dpx email"
                    % (size[0], field, widths[field], widths["email"]))

        for width in (AUDIT_SIZE[0], WIDE_SIZE[0]):
            assert measured[width]["email"] >= EMAIL_PX, (
                "%d: the email column is %dpx and a 44-character address needs "
                "%d" % (width, measured[width]["email"], EMAIL_PX))

        _sized(screen, AUDIT_SIZE)
        assert _elided(screen, "email") == 0, (
            "the email is still cut in %d of 20 rows at 1280"
            % _elided(screen, "email"))
        assert _elided(screen, "name") == 0

        _sized(screen, WIDE_SIZE)
        for field in LEAD_FIELDS:
            assert _elided(screen, field) == 0, (
                "%s is cut in %d of 20 rows on a 2560px window"
                % (field, _elided(screen, field)))
        widest = max(measured[WIDE_SIZE[0]].values())
        assert widest <= 2 * measured[AUDIT_SIZE[0]]["email"], \
            "a column ballooned to %dpx on the wide window" % widest
    finally:
        _sized(screen, DEFAULT_SIZE)


def test_a_scrape_that_fails_puts_the_whole_message_on_screen():
    """It was cut to 60 characters, and the rest of it existed nowhere.

    A Selenium failure names the driver, the binary and the version that did
    not match, and all three are past the 60th character. The error state
    carries the whole of it, selectable because the next thing anyone does with
    one of these is paste it somewhere, and the toast that carries it as well
    is the one tone that waits to be dismissed.
    """
    screen = _screen("results")
    message = ("Message: session not created: This version of ChromeDriver "
               "only supports Chrome version 121. Current browser version is "
               "126.0.6478.127 with binary path C:\\Program Files\\Google\\"
               "Chrome\\Application\\chrome.exe")
    try:
        screen.results = []
        screen.table.setRowCount(0)
        screen.on_error(message)
        _app().processEvents()

        assert screen.table_stack.currentIndex() == screen.ERROR_PAGE
        shown = screen.error_page.body_label.text()
        assert shown == message, (
            "the error on screen is %d characters of %d"
            % (len(shown), len(message)))
        assert screen.error_page.body_label.textInteractionFlags() & \
            Qt.TextSelectableByMouse, "the message cannot be copied"
        assert screen.error_page.action_button is not None, \
            "the failed run offers no way out"

        toasts = screen.toaster.toasts()
        assert toasts and toasts[-1].tone == "danger"
        assert message in toasts[-1].text_label.text()
        assert toasts[-1].timer is None, \
            "the failure toast takes itself off screen while it is being read"
    finally:
        screen.toaster.clear()
        screen._error = ""
        screen._update_table_page()


def test_the_recent_searches_box_says_what_would_fill_it():
    """It was a 596x440 bordered box holding one grey sentence."""
    screen = _screen("input")
    saved = list(screen.settings.get("saved_searches") or [])
    try:
        screen.settings["saved_searches"] = []
        screen._refresh_saved_list()
        _app().processEvents()
        assert screen.saved_stack.currentWidget() is screen.saved_empty
        assert screen.saved_list.count() == 0, \
            "the empty list still holds a row that cannot be clicked"
        card = screen.saved_empty.findChild(QFrame, "card")
        assert card is not None, "the empty state is a bare rectangle again"
        assert "Nothing saved yet" in " ".join(
            label.text() for label in card.findChildren(QLabel))

        screen.settings["saved_searches"] = [
            {"domains": ["dentists"], "area": "Toronto", "max_results": 20}]
        screen._refresh_saved_list()
        assert screen.saved_stack.currentWidget() is screen.saved_list
        assert screen.saved_list.count() == 1
    finally:
        screen.settings["saved_searches"] = saved
        screen._refresh_saved_list()


def test_the_home_page_gains_a_column_rather_than_stretching_one():
    """The second geometry finding: at 2560 this was empty boxes and 600px cells.

    Every sentence on the page is capped in characters by the component that
    draws it, so a column is exactly as wide as readable copy and no wider —
    and a window with room for another column is given another column instead
    of the same one stretched across it.
    """
    screen = _screen("input")
    seen = {}
    for size in (MINIMUM_SIZE, DEFAULT_SIZE, AUDIT_SIZE, WIDE_SIZE):
        _sized(screen, size)
        page = screen.page
        seen[size[0]] = page.columns_shown()

        measured = [widget for widget in screen.findChildren(QWidget)
                    if widget.isVisible()
                    and isinstance(widget, (QLabel, QLineEdit, QCheckBox))]
        widest = max(measured, key=lambda widget: widget.width())
        assert widest.width() <= page.column_width(), (
            "%d: %s is %dpx against a %dpx measure"
            % (size[0], widest.__class__.__name__, widest.width(),
               page.column_width()))

        overflowing = [widget for widget in measured
                       if widget.mapTo(screen, QPoint(0, 0)).x() + widget.width()
                       > screen.width()]
        assert not overflowing, \
            "%d: %d controls run off the side" % (size[0], len(overflowing))

    assert seen[MINIMUM_SIZE[0]] >= 2, \
        "the minimum window fell back to one column: %s" % seen
    assert seen[WIDE_SIZE[0]] > seen[AUDIT_SIZE[0]] > seen[DEFAULT_SIZE[0]], \
        "the page does not gain a column as the window grows: %s" % seen
    _sized(screen, DEFAULT_SIZE)



# ── U13: the settings screen, after it gave its chrome back ──────────────────
# Everything below is a defect the design-system audit measured on this one
# screen: two rows of chrome, no way to cancel, a scheduler that overrode the
# numbers the UI reported back, controls out of reach at the window minimum,
# seven left edges on one tab, the merge palette wearing the navigation control,
# and an unconfirmed, unannounced, unrecoverable Remove.


@contextlib.contextmanager
def _agreeing(answer: bool):
    """`components.confirm` answered without a modal nobody could close."""
    saved = SS.C.confirm
    asked = []

    def stub(_parent, **kwargs):
        asked.append(kwargs)
        return answer

    SS.C.confirm = stub
    try:
        yield asked
    finally:
        SS.C.confirm = saved


def _form_labels(page) -> list:
    return [label for label in page.findChildren(SS._FormLabel)
            if label.isVisible()]


def _control_edges(page) -> set:
    """Where column one starts, taken off every form grid on the page."""
    edges = set()
    for grid in page.findChildren(QGridLayout):
        for row in range(grid.rowCount()):
            head, cell = grid.itemAtPosition(row, 0), grid.itemAtPosition(row, 1)
            if head is None or cell is None:
                continue
            if isinstance(head.widget(), SS._FormLabel):
                edges.add(grid.cellRect(row, 1).x())
    return edges


def test_the_settings_screen_draws_no_bar_of_its_own():
    """The two rows of chrome, and which of them the screen was still drawing.

    It carried a Back button, a title and a strip of seven tabs directly under
    the shell's own bar — the same navigation control twice, one row apart. The
    tabs are handed back through `subtabs()` now and everything else is gone.
    """
    screen = _sized(_screen("settings"), DEFAULT_SIZE)
    labels, on_change, _current = screen.subtabs()
    assert labels == screen.TABS and callable(on_change)
    assert len(labels) == screen.pages.count(), \
        "%d tabs over %d pages" % (len(labels), screen.pages.count())

    assert screen.findChild(QWidget, "app_bar") is None, \
        "the screen brought a top bar of its own"
    strip = [button for button in screen.findChildren(QPushButton)
             if button.objectName() == "tab"]
    assert strip == [], \
        "the screen still draws its own tabs: %s" % [b.text() for b in strip]
    named = {button.text() for button in screen.findChildren(QPushButton)}
    assert not named & {"Back", "Home", "Settings"}, \
        "the screen still draws its own chrome: %s" % sorted(named)

    on_change(screen.TABS.index("Gmail"))
    assert screen.pages.currentIndex() == screen.TABS.index("Gmail"), \
        "the shell cannot move the page"
    on_change(0)


def test_leaving_settings_writes_nothing_and_saving_says_so_where_it_shows():
    """Back used to save the whole file on the way past.

    So a half-finished sending window was committed by the act of navigating
    away, and the one confirmation it produced was written into a header on a
    screen the user was no longer looking at. Both decisions are commands in a
    footer now, the footer is on screen whenever the settings are, and it says
    which of them there is anything to do.
    """
    screen = _sized(_screen("settings"), DEFAULT_SIZE)
    screen.pages.setCurrentIndex(screen.TABS.index("Sending"))
    _sized(screen, DEFAULT_SIZE)
    on_disk = ST.load_settings()
    # Settled first: this screen is shared with every other test in the file and
    # several of them type into it, and what is measured here is the step from
    # "nothing outstanding" to "something is".
    screen.settings = ST.load_settings()
    screen._load_into_ui()
    _app().processEvents()
    was = screen.hourly_cap_spin.value()
    try:
        assert not screen._dirty
        assert not screen.discard_btn.isEnabled(), \
            "Discard offers to undo a screen nobody has touched"

        screen.hourly_cap_spin.setValue(was + 3)
        _app().processEvents()
        assert screen._dirty, "an edited spin box is not noticed"
        assert screen.save_status.text() == "Unsaved changes", \
            "the footer says %r" % screen.save_status.text()
        assert screen.discard_btn.isEnabled()

        # Leaving. Nothing may reach the file, and the screen must keep the edit.
        left = []
        screen.back_signal.connect(lambda: left.append(True))
        with _answering(QMessageBox.Cancel) as dialog:
            screen._on_back()
        assert dialog.asked == 1, "leaving with unsaved changes asked nothing"
        assert left == [], "Cancel left the screen anyway"
        assert ST.load_settings().get("hourly_cap_per_account") == \
            on_disk.get("hourly_cap_per_account"), \
            "leaving wrote the edit to the settings file"
        assert screen.hourly_cap_spin.value() == was + 3, \
            "the unsaved edit was thrown away by being asked about"

        # Saving. The file moves and the confirmation is on this screen.
        assert screen._on_save()
        assert ST.load_settings().get("hourly_cap_per_account") == was + 3
        assert screen.save_status.text() == "Saved"
        assert screen.save_status.isVisible(), \
            "the confirmation is on a screen nobody is looking at"
        assert not screen._dirty and not screen.discard_btn.isEnabled()

        # Discarding. The widgets go back to the file, and it asks first.
        screen.hourly_cap_spin.setValue(was + 9)
        _app().processEvents()
        with _agreeing(False) as asked:
            screen._on_discard()
        assert len(asked) == 1, "Discard threw the edits away without asking"
        assert screen.hourly_cap_spin.value() == was + 9, \
            "declining the question discarded anyway"
        with _agreeing(True):
            screen._on_discard()
        assert screen.hourly_cap_spin.value() == was + 3, \
            "Discard did not put the field back to the file"
        assert not screen._dirty
    finally:
        screen.hourly_cap_spin.setValue(was)
        screen._on_save()
        ST.save_settings(on_disk)
        screen.settings = ST.load_settings()
        screen._load_into_ui()
        screen.pages.setCurrentIndex(0)


def test_the_scheduler_s_own_limits_are_shown_beside_the_numbers_that_ask():
    """`core.campaign` composes rather than obeys, and the UI used to hide it.

    Three caps become their minimum, an inverted window becomes one hour, an
    empty day set becomes Monday to Friday. Every one of those happened in
    silence with the requested number still on screen — a user who set a 40/day
    cap and left the warm-up ramp on was told 40 and sent 10. Each note is empty
    when nothing is being overridden, because a note beside every field is a
    note nobody reads.
    """
    screen = _sized(_screen("settings"), DEFAULT_SIZE)
    screen.pages.setCurrentIndex(screen.TABS.index("Sending"))
    _sized(screen, DEFAULT_SIZE)
    on_disk = ST.load_settings()
    try:
        # A schedule the scheduler will keep exactly as it is asked to, over no
        # accounts and with no ramp, so that "nothing is annotated" means the
        # notes are quiet rather than that nothing was set.
        with _agreeing(True):
            for row in list(screen._account_rows):
                screen._remove_account_row(row)
        screen.toaster.clear()
        for box in screen.day_boxes:
            box.setChecked(True)
        screen.warmup_cb.setChecked(False)
        screen.start_hour_spin.setValue(9)
        screen.end_hour_spin.setValue(17)
        screen.min_gap_spin.setValue(60)
        screen.max_gap_spin.setValue(240)
        screen.daily_cap_spin.setValue(40)
        _app().processEvents()
        quiet = [note for note in (screen.days_note, screen.window_note,
                                   screen.gap_note, screen.cap_note)
                 if note.isVisible()]
        assert quiet == [], \
            "a setting nothing overrides is annotated anyway: %s" % (
                [note.text() for note in quiet],)

        for box in screen.day_boxes:
            box.setChecked(False)
        screen.end_hour_spin.setValue(9)
        screen.min_gap_spin.setValue(300)
        screen.max_gap_spin.setValue(120)
        _app().processEvents()

        assert screen.days_note.isVisible() and "Mon" in screen.days_note.text(), \
            "no day ticked is read as the working week and says nothing"
        kept = SS._campaign._hours({"send_start_hour": 9, "send_end_hour": 9})
        assert screen.window_note.isVisible() and \
            "%02d:00" % kept[1] in screen.window_note.text(), \
            "an inverted window is forced to an hour in silence: %r" \
            % screen.window_note.text()
        assert screen.gap_note.isVisible() and "120" in screen.gap_note.text(), \
            "an inverted pacing range is swapped in silence: %r" \
            % screen.gap_note.text()

        # And the per-account cap, which is the one the audit caught: a warm-up
        # ramp lower than the number in the box.
        screen.daily_cap_spin.setValue(40)
        screen.warmup_cb.setChecked(True)
        screen.warmup_start_spin.setValue(10)
        screen.warmup_max_spin.setValue(40)
        screen._add_account_row({"email": "ramp@example.com", "daily_cap": 40,
                                 "warmup_started": "2099-01-01"})
        _app().processEvents()
        row = screen._account_rows[-1]
        assert screen.cap_note.isVisible(), \
            "the ramp is lower than the cap and the page says nothing"
        # isVisibleTo, not isVisible: the row lives on the Gmail tab while
        # this test stands on Sending, so the subtree is legitimately hidden.
        # The guarantee is that the label is not hidden within its own row --
        # that it shows when the user is actually looking at the account.
        assert row.effective_label.isVisibleTo(row) and \
            "not 40" in row.effective_label.text(), \
            "the account row reports the number it was asked for: %r" \
            % row.effective_label.text()
    finally:
        with _agreeing(True):
            for row in list(screen._account_rows):
                screen._remove_account_row(row)
        screen.toaster.clear()
        ST.save_settings(on_disk)
        screen.settings = ST.load_settings()
        screen._load_into_ui()
        screen.pages.setCurrentIndex(0)


def test_every_form_on_every_tab_lines_up_on_one_pair_of_edges():
    """The audit measured seven left edges on the Sending tab alone.

    Four of them within 30px of each other and two of them 4px apart in adjacent
    groups, because each grid sized its own label column to its own longest
    word. There is one label column for the whole screen now, reserved from the
    widest string any tab uses and measured in the font that draws it, so a tab
    is a page rather than a stack of unrelated forms.
    """
    screen = _sized(_screen("settings"), DEFAULT_SIZE)
    opened = screen.pages.currentIndex()
    labels, controls, seen = set(), set(), 0
    try:
        for index in range(screen.pages.count()):
            screen.pages.setCurrentIndex(index)
            _sized(screen, DEFAULT_SIZE)
            page = screen.pages.currentWidget()
            here = _form_labels(page)
            seen += len(here)
            labels |= {label.mapTo(page, QPoint(0, 0)).x() for label in here}
            labels |= {label.width() for label in here}
            controls |= _control_edges(page)
    finally:
        screen.pages.setCurrentIndex(opened)
        _sized(screen, DEFAULT_SIZE)

    assert seen >= 30, "only %d form labels were reachable" % seen
    assert len(labels) == 2, (
        "the label column starts or ends in more than one place: %s"
        % sorted(labels))
    assert len(controls) == 1, \
        "column one starts at %s" % sorted(controls)


def test_the_merge_palette_is_a_chip_and_not_the_navigation_control():
    """Twenty-one merge fields were `QPushButton#tab`, the app's own top-level
    navigation, and the keyboard cursor on them reused the selected-tab fill
    exactly — so the chip under the caret read as the open tab.

    A chip is a short value and a tab is a place. They are different components
    because they are different promises.
    """
    screen = _templates_page(_screen("settings"))
    chips = screen.template_chips._chips
    assert len(chips) == len(TPL.MERGE_FIELDS)
    assert not any(isinstance(chip, QPushButton) for chip in chips), \
        "a merge field is still a button"
    assert {chip.property("role") for chip in chips} == {"chip"}, \
        "the palette is not built by components.chip()"

    theme = TH.theme()
    selected = theme.color["surfaceActive"].upper()
    cursor = screen.template_chips._marked
    assert selected not in cursor.upper(), \
        "the chip cursor is painted in the selected-tab fill"
    assert theme.color["accent.border"].upper() in cursor.upper(), \
        "the chip cursor is not the app's focus ink: %r" % cursor


def test_taking_a_mailbox_off_the_rota_asks_first_and_can_be_taken_back():
    """Remove was one click, unconfirmed, unannounced and unrecoverable.

    What went with the row was the app password, and Google never shows an app
    password twice — so an accidental click cost a trip to a Google account page
    to mint a new one.
    """
    screen = _sized(_screen("settings"), DEFAULT_SIZE)
    screen.pages.setCurrentIndex(screen.TABS.index("Gmail"))
    _sized(screen, DEFAULT_SIZE)
    before = len(screen._account_rows)
    try:
        screen._add_account_row({"email": "first@example.com", "daily_cap": 10})
        screen._add_account_row({"email": "second@example.com", "daily_cap": 20})
        screen._account_rows[-2].set_password("abcd efgh ijkl mnop")
        _app().processEvents()
        row = screen._account_rows[-2]

        with _agreeing(False) as asked:
            screen._remove_account_row(row)
        assert len(asked) == 1, "Remove took the mailbox without asking"
        assert asked[0].get("danger") is True, \
            "removing a mailbox is not asked as a destructive action"
        assert row in screen._account_rows, "declining removed it anyway"

        with _agreeing(True):
            screen._remove_account_row(row)
        assert row not in screen._account_rows, "the row survived Remove"
        toasts = screen.toaster.toasts()
        assert len(toasts) == 1, "removing a mailbox announced nothing"
        undo = toasts[-1].findChild(QPushButton)
        assert undo is not None and undo.text().lower() == "undo", \
            "the announcement carries no way back"

        undo.click()
        _app().processEvents()
        addresses = [one.email() for one in screen._account_rows]
        assert "first@example.com" in addresses, "Undo did not put it back"
        restored = screen._account_rows[addresses.index("first@example.com")]
        assert restored.app_password() == "abcd efgh ijkl mnop", \
            "Undo put the mailbox back without its app password"
        # Upper-cased since the accounts became grouped boxes like every other
        # group on the screen: the ordinal is the box's caption now and a
        # caption is `components.section_label`, which upper-cases what it is
        # given. What is asserted here is the renumbering — that Undo puts the
        # row back and the headings count from one again — and that is unchanged;
        # the case is the register the caption is drawn in.
        assert [one.title_label.text() for one in screen._account_rows] == \
            ["ACCOUNT %d" % (n + 1) for n in range(len(screen._account_rows))], \
            "the rows are numbered %s" % [one.title_label.text()
                                          for one in screen._account_rows]
    finally:
        with _agreeing(True):
            while len(screen._account_rows) > before:
                screen._remove_account_row(screen._account_rows[-1])
        screen.toaster.clear()
        screen._dirty = False
        screen.pages.setCurrentIndex(0)
        _sized(screen, DEFAULT_SIZE)


def test_the_way_to_add_a_mailbox_stays_where_it_is_at_the_window_minimum():
    """It scrolled with the accounts, so it moved further away with each one.

    At 880x620 with two accounts configured it started below the bottom edge:
    the control for getting out of an empty state was reachable only by
    scrolling past what was missing.
    """
    screen = _sized(_screen("settings"), MINIMUM_SIZE)
    screen.pages.setCurrentIndex(screen.TABS.index("Gmail"))
    _sized(screen, MINIMUM_SIZE)
    before = len(screen._account_rows)
    page = screen.pages.currentWidget()
    try:
        seen = []
        for count in range(4):
            _sized(screen, MINIMUM_SIZE)
            button = screen.add_account_btn
            top = button.mapTo(page, QPoint(0, 0)).y()
            seen.append(top)
            assert button.isVisible(), \
                "Add account is not on screen with %d accounts" % count
            assert 0 <= top and top + button.height() <= page.height(), (
                "Add account sits at %d..%d of a %dpx page with %d accounts"
                % (top, top + button.height(), page.height(), count))
            assert _scroll_ancestor(button) is None, \
                "Add account is inside the column that scrolls"
            screen._add_account_row({"email": "a%d@example.com" % count})
        assert len(set(seen)) == 1, \
            "Add account moved as accounts were added: %s" % seen
    finally:
        with _agreeing(True):
            while len(screen._account_rows) > before:
                screen._remove_account_row(screen._account_rows[-1])
        screen.toaster.clear()
        screen._dirty = False
        screen.pages.setCurrentIndex(0)
        _sized(screen, DEFAULT_SIZE)


def test_the_body_label_is_whole_and_the_editor_reachable_at_the_minimum():
    """The BODY label was sliced in half by the bottom edge of an 880x620 window.

    The merge palette reflowed to five rows and took 165px of a 308px column,
    which put the body editor below the fold with its own label cut through.
    Both are bounded now, and the editor is reachable by scrolling the column
    that holds it rather than being clipped by the page.
    """
    screen = _templates_page(_screen("settings"), MINIMUM_SIZE)
    palette = screen.template_chips_pane
    assert palette.height() <= palette.ceiling(), \
        "the merge palette is %dpx against a %dpx ceiling" % (
            palette.height(), palette.ceiling())
    assert palette.height() < screen.template_chips.height() \
        or screen.template_chips.rows() <= 3, \
        "the palette is still growing to whatever the chips need"

    editor = screen.template_body_edit
    column = _scroll_ancestor(editor)
    assert column is not None, "the body editor is not in a scrolling column"
    label = next(one for one in screen.pages.currentWidget().findChildren(QLabel)
                 if one.text() == "BODY")
    for widget in (label, editor):
        bottom = widget.mapTo(column.widget(), QPoint(0, widget.height())).y()
        reach = column.verticalScrollBar().maximum() + column.viewport().height()
        assert bottom <= reach, (
            "%r ends %dpx past the furthest the column scrolls"
            % (widget.__class__.__name__, bottom - reach))
    _templates_page(screen)


def test_the_appearance_tab_changes_both_palettes_while_you_watch():
    """Both palettes and both densities existed and neither could be reached.

    Writing `theme` or `density` into settings.json by hand was the only route,
    and `core.settings._merge` used to drop both keys on the next save. They are
    controls now, they apply as they are picked, and the density preview under
    them is a real table so what is being chosen can be seen.
    """
    screen = _sized(_screen("settings"), DEFAULT_SIZE)
    screen.pages.setCurrentIndex(screen.TABS.index("Appearance"))
    _sized(screen, DEFAULT_SIZE)
    on_disk = ST.load_settings()
    started = TH.from_settings(screen.settings)
    try:
        assert [screen.theme_combo.itemData(n)
                for n in range(screen.theme_combo.count())] == ["dark", "light"]
        assert [screen.density_combo.itemData(n)
                for n in range(screen.density_combo.count())] == \
            ["comfortable", "compact"]

        comfortable = screen.density_preview.verticalHeader().defaultSectionSize()
        assert comfortable == TH.theme(started.name, "comfortable").control["row"]

        screen.density_combo.setCurrentIndex(1)
        _app().processEvents()
        _app().processEvents()
        compact = screen.density_preview.verticalHeader().defaultSectionSize()
        assert compact == TH.theme(started.name, "compact").control["row"], \
            "the preview row is %dpx and compact is %dpx" % (
                compact, TH.theme(started.name, "compact").control["row"])
        assert compact < comfortable, "the preview does not show the difference"
        assert SS.C.active_theme().density == "compact", \
            "picking a density changed a preview and nothing else"
        assert screen.template_subject_edit.height() == \
            TH.theme(started.name, "compact").control["md"], \
            "the controls on the other tabs did not follow the density"

        screen.theme_combo.setCurrentIndex(1)
        _app().processEvents()
        _app().processEvents()
        assert SS.C.active_theme().name == "light", \
            "picking a theme did not reach the app"
        assert screen.pages.currentIndex() == screen.TABS.index("Appearance"), \
            "changing the theme moved the user off the tab they were on"
        assert screen._dirty, "an appearance choice is not something to save"
        assert ST.load_settings().get("theme") != "light", \
            "the choice was written to the file without anyone pressing Save"

        assert screen._on_save()
        assert ST.load_settings().get("theme") == "light"
        assert ST.load_settings().get("density") == "compact"
    finally:
        screen.settings["theme"] = started.name
        screen.settings["density"] = started.density
        ST.save_settings(on_disk)
        screen.settings = ST.load_settings()
        screen._load_into_ui()
        SS.C.use_theme(started)
        TH.apply(_app(), started)
        screen.restyle()
        screen.pages.setCurrentIndex(0)
        _sized(screen, DEFAULT_SIZE)
        _app()


def test_the_list_dialog_can_be_resized_and_carries_a_floor():
    """`setFixedSize(400, 320)`: two defects, not one.

    A user pasting forty search terms got a 240px well and no way to see more
    than eight of them at a time on a monitor with room for forty. A user at
    150% Windows text scaling got the same box with type half again as large in
    it, so Save went off the bottom edge — and a dialog whose Save cannot be
    reached cannot be used.
    """
    dialog = DLG.DomainListDialog(["dentists", "roofers"])
    try:
        opened = dialog.size()
        assert dialog.maximumWidth() >= WIDE_SIZE[0], \
            "the dialog is capped at %dpx wide" % dialog.maximumWidth()
        dialog.resize(QSize(1200, 900))
        assert (dialog.width(), dialog.height()) == (1200, 900), \
            "the dialog refused to grow: %s" % (dialog.size(),)

        dialog.resize(QSize(1, 1))
        floor = dialog.size()
        assert floor.width() > 0 and floor.height() > 0
        assert floor.height() >= dialog.save_btn.height(), \
            "the dialog can be squeezed smaller than its own Save button"

        # And the opening size is measured, so text scaling moves it.
        font = dialog.text_edit.font()
        assert opened.height() >= 10 * dialog.fontMetrics().lineSpacing(), \
            "the box opens at %dpx, under ten rows of its own type" % opened.height()
        assert font.pointSizeF() > 0 or font.pixelSize() > 0
    finally:
        dialog.deleteLater()


# ── U14: the keyboard, the palette, and what an appearance change costs ──────
# The audit's finding was that keyboard support was effectively absent: no
# shortcuts, no mnemonics, no menu bar, Enter did not submit and Escape did
# nothing, on any of the four screens. Everything below is measured against the
# shell rather than a screen, because a shortcut that works on one screen and
# not the next is not a shortcut — the layer has to be somewhere every screen
# inherits it from, and the shell is the only such place.
#
# Nothing here opens a modal. Qt 5.15 offscreen aborts the process on any
# QMessageBox that is actually shown, so the one command that would ask a
# question — arming a live send — has `components.confirm` answered as an input.


def _menus(window) -> dict:
    """The menu bar as {title: menu}, with the mnemonic markers left in."""
    found = {}
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None:
            found[action.text()] = menu
    return found


def _menu_keys(window) -> dict:
    """Every menu item in the window as {text: shortcut}."""
    found = {}
    for menu in _menus(window).values():
        for action in menu.actions():
            if not action.isSeparator():
                found[action.text()] = action.shortcut().toString()
    return found


@contextlib.contextmanager
def _captured_messages():
    """Every warning Qt writes while the block runs, as a list of strings."""
    seen = []

    def handler(mode, _context, text):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            seen.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        yield seen
    finally:
        qInstallMessageHandler(previous)


def _keyboard_in(window):
    """Make `window` the active one, so `setFocus` actually moves the focus.

    Same reason `_focused` in U10 does it: `setFocus` inside an inactive window
    only records where the focus would go if that window ever got it, so
    without this every assertion about what has the keyboard measures whatever
    test happened to show a window last. Every screen in this module is shown
    as its own top-level, so which window is active depends on what ran before.
    """
    app = _app()
    # Drained before the window is activated and not only after. Every screen in
    # this module is shown as its own top-level, and a `show()` still sitting in
    # the queue from an earlier test activates whatever it belongs to the moment
    # it is delivered — which, activated first, is the window it takes back.
    app.processEvents()
    app.processEvents()
    if not window.isVisible():
        window.show()
    window.raise_()
    window.activateWindow()
    app.setActiveWindow(window)
    app.processEvents()
    assert app.activeWindow() is window, \
        "the active window is %r, so nothing in %r can hold the keyboard" % (
            app.activeWindow(), window)
    return window


def _focused_on(widget):
    """The keyboard on `widget`, its window activated in the same breath.

    Activated immediately before rather than at the top of the test: showing a
    screen and switching the stack both move the active window on the offscreen
    platform, so an activation that happens a few statements earlier has often
    been undone by the time the focus is asked for.
    """
    app = _app()
    _keyboard_in(widget.window())
    widget.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    assert widget.hasFocus(), (
        "the keyboard will not go to %r: visible=%s enabled=%s policy=%s "
        "window shown=%s active=%r"
        % (widget, widget.isVisible(), widget.isEnabled(),
           int(widget.focusPolicy()), widget.window().isVisible(),
           app.activeWindow()))
    return widget


@contextlib.contextmanager
def _all_four_built(window):
    """Every screen constructed and the home screen on top, then home again."""
    for key in (APP.RESULTS, APP.OUTREACH, APP.SETTINGS):
        window.shell.screen(key)
    window.shell.go(APP.INPUT)
    _app().processEvents()
    try:
        yield window.shell
    finally:
        window.shell.go(APP.INPUT)
        _app().processEvents()


def test_the_menu_bar_names_every_destination_and_the_key_that_reaches_it():
    """The app had no menu bar at all, so nothing named a key anywhere.

    Qt writes an action's shortcut in a second column beside its name, which
    makes the bar the one surface that answers "what can I press" without the
    user having to be told to press something first. Three menus, each with a
    mnemonic, and every destination in the bar carrying its own Ctrl+digit.
    """
    window = _window()
    titles = list(_menus(window))
    assert titles == ["&Go", "&View", "&Help"], "the menus are %s" % titles

    keys = _menu_keys(window)
    assert keys.get("Command &palette…") == "Ctrl+K"
    for at, key in enumerate(window.shell.destinations(), start=1):
        wanted = "&%d  %s" % (at, window.shell.label(key))
        assert keys.get(wanted) == "Ctrl+%d" % at, \
            "%r is on %r" % (wanted, keys.get(wanted))
    assert keys.get("&Settings") == "Ctrl+,"
    assert keys.get("&Keyboard shortcuts and commands") == "F1"

    # Back and Submit spell their keys in their own text on purpose: a shortcut
    # is matched before the focused widget sees the key, so Escape and Return
    # registered here would be taken away from every dialog and every editor
    # in the app.
    assert keys.get("&Back\tEsc") == ""
    assert keys.get("&Submit the form in front of you\tEnter") == ""


def test_a_shortcut_reaches_every_destination_from_every_screen():
    """One layer, owned by the shell, so no screen can be missing it."""
    window = _window()
    shell = window.shell
    keyed = {}
    for menu in _menus(window).values():
        for action in menu.actions():
            keyed[action.shortcut().toString()] = action
    try:
        for start in (APP.INPUT, APP.OUTREACH, APP.SETTINGS):
            shell.go(start)
            for at, key in enumerate(shell.destinations(), start=1):
                keyed["Ctrl+%d" % at].trigger()
                _app().processEvents()
                assert shell.current_key == key, \
                    "Ctrl+%d from %s landed on %s" % (at, start,
                                                      shell.current_key)
            keyed["Ctrl+,"].trigger()
            assert shell.current_key == APP.SETTINGS
    finally:
        shell.go(APP.INPUT)


def test_an_unmodified_shortcut_does_not_fire_while_a_field_is_being_typed_in():
    """The rule every shortcut in the app is written to obey.

    Qt matches a shortcut before the key reaches whatever has the focus, so an
    unmodified one is a character a search box never receives. F1 is the only
    unmodified key the menu registers and it asks first; the modified ones do
    not have to, which is the reason they carry a modifier.
    """
    window = _window()
    shell = window.shell
    shell.go(APP.INPUT)
    field = _focused_on(shell.screen(APP.INPUT).domain_input)
    try:
        assert window._typing() is True

        window.on_help()
        _app().processEvents()
        assert not shell.palette().isVisible(), \
            "F1 opened the palette out from under someone typing a search"

        # And with the focus on something that is not text, the same key works.
        _focused_on(_nav_rows(window)[0])
        assert window._typing() is False
        window.on_help()
        _app().processEvents()
        assert shell.palette().isVisible(), "F1 does nothing anywhere at all"
    finally:
        shell.dismiss_palette()
        field.clear()
        shell.go(APP.INPUT)


def test_escape_backs_out_of_one_thing_at_a_time():
    """Escape did nothing anywhere. It now undoes exactly one step.

    The palette first, because it is the thing in front; then Settings, which
    has a way back of its own that has to keep meaning what it meant; then the
    screen, which returns to wherever it was reached from.
    """
    window = _keyboard_in(_window())
    shell = window.shell
    try:
        shell.go(APP.INPUT)
        shell.go(APP.OUTREACH)
        window.on_settings()
        assert shell.current_key == APP.SETTINGS
        window.open_palette()
        assert shell.palette().isVisible()

        QTest.keyClick(window, Qt.Key_Escape)
        _app().processEvents()
        assert not shell.palette().isVisible(), "Escape left the palette open"
        assert shell.current_key == APP.SETTINGS, \
            "one Escape closed the palette and left the screen too"

        QTest.keyClick(window, Qt.Key_Escape)
        _app().processEvents()
        assert shell.current_key == APP.OUTREACH, \
            "Escape out of Settings landed on %s" % shell.current_key

        QTest.keyClick(window, Qt.Key_Escape)
        _app().processEvents()
        assert shell.current_key == APP.INPUT, \
            "Escape did not walk back to where the visit started"

        # And nothing is left to undo: Escape on the home screen is not a way
        # to keep rewinding through the whole session.
        while window.on_escape():
            pass
        assert window.on_escape() is False
    finally:
        shell.go(APP.INPUT)


def test_return_presses_the_button_the_form_in_front_of_you_is_asking_for():
    """Enter did not submit anything, on a product that is mostly forms."""
    window = _window()
    shell = window.shell
    shell.go(APP.INPUT)
    home = shell.screen(APP.INPUT)
    results = shell.screen(APP.RESULTS)
    saved_dir = home.export_dir()
    started = []
    with _stubbed(results, "setup", lambda *a, **k: started.append("setup")), \
            _stubbed(results, "start_worker",
                     lambda: started.append("start")):
        try:
            home.export_dir_input.setText(os.path.join(_TMP, "keyboard-run"))
            home.domain_input.setText("dentists")
            home.area_input.setText("Toronto")
            _focused_on(home.domain_input)

            assert window._primary_near(QApplication.focusWidget()) \
                is home.start_btn, "Return would press something else"
            QTest.keyClick(window, Qt.Key_Return)
            _app().processEvents()
            assert started == ["setup", "start"], \
                "Return on a filled form did %s" % (started,)
            assert shell.current_key == APP.RESULTS
        finally:
            home.export_dir_input.setText(saved_dir)
            home.domain_input.clear()
            home.area_input.clear()
            results._set_idle_mode()
            shell.go(APP.INPUT)


def test_return_presses_the_button_that_has_the_focus_and_not_the_form_s():
    """A focused control answers for itself. Qt only does this inside a dialog."""
    window = _window()
    shell = window.shell
    shell.go(APP.OUTREACH)
    _app().processEvents()
    row = [button for button in _nav_rows(window)
           if button.accessibleName() == shell.label(APP.INPUT)][0]
    try:
        _focused_on(row)
        QTest.keyClick(window, Qt.Key_Return)
        _app().processEvents()
        assert shell.current_key == APP.INPUT, \
            "Return on a focused rail row did not press it"
    finally:
        shell.go(APP.INPUT)


def test_return_is_left_alone_where_it_already_means_something():
    """A template body and a list row both mean something by Return already.

    Which is why this is `keyPressEvent` on the window and not a shortcut: a
    shortcut is matched before the focused widget ever sees the key, so Return
    registered as one would insert no newline anywhere in the app again.
    """
    window = _window()
    shell = window.shell
    try:
        # Shown once first: a screen publishes its row of sub-tabs to the shell
        # from `showEvent`, so there is nothing to reach by name until it has.
        shell.go(APP.SETTINGS)
        _app().processEvents()
        assert shell.go_subtab(APP.SETTINGS, "Templates"), \
            "the settings screen published no Templates section"
        _sized(window, DEFAULT_SIZE)
        screen = shell.screen(APP.SETTINGS)

        _focused_on(screen.template_body_edit)
        assert window.on_submit() is False, \
            "Return in the template body fired the screen's primary button"

        _focused_on(screen.template_list)
        assert window.on_submit() is False, \
            "Return on a template row fired the screen's primary button"
    finally:
        shell.go(APP.INPUT)


def test_the_palette_reaches_a_destination_from_the_keyboard_alone():
    """Ctrl+K, three letters, Return — and the palette is never in the way."""
    window = _window()
    shell = window.shell
    shell.go(APP.INPUT)
    try:
        _keyboard_in(window)
        window.open_palette()
        _app().processEvents()
        palette = shell.palette()
        assert palette.isVisible()
        assert QApplication.focusWidget() is palette.query, \
            "the palette opened without the caret in it"

        # The same key closes it. A surface you can only dismiss with a key
        # other than the one that opened it is one more thing to remember.
        window.open_palette()
        _app().processEvents()
        assert not palette.isVisible(), "Ctrl+K twice left the palette open"
        _keyboard_in(window)
        window.open_palette()
        _app().processEvents()

        QTest.keyClicks(palette.query, "gto")
        _app().processEvents()
        titles = [command.title for command in palette.commands()]
        assert titles[:3] == ["Go to Scrape", "Go to Results",
                              "Go to Outreach"], \
            "a three-letter query answered with %s" % titles[:3]

        QTest.keyClick(palette.query, Qt.Key_Down)
        QTest.keyClick(palette.query, Qt.Key_Down)
        assert palette.highlighted().title == "Go to Outreach"
        QTest.keyClick(palette.query, Qt.Key_Return)
        _app().processEvents()
        assert not palette.isVisible(), "running a command left the palette up"
        assert shell.current_key == APP.OUTREACH
    finally:
        shell.dismiss_palette()
        shell.go(APP.INPUT)


def test_the_palette_matches_the_letters_of_a_name_and_not_a_substring():
    """The whole reason it is worth typing into: nobody spells it out."""
    named = [CP.Command(key=str(at), title=title, run=lambda: None)
             for at, title in enumerate(
                 ("Go to Outreach", "Go to Settings",
                  "Outreach — Prepare a campaign",
                  "Outreach — Start sending",
                  "Switch to the light theme"))]
    assert [c.title for c in CP.rank(named, "prep")] == \
        ["Outreach — Prepare a campaign"]
    assert CP.rank(named, "zzz") == []
    assert [c.title for c in CP.rank(named, "")] == [c.title for c in named], \
        "an empty query reordered the list instead of leaving it alone"

    # A word start outranks the same letters found scattered, which is what
    # puts the command being thought of at the top of the list.
    assert CP.score("out", "Outreach — Start sending") > \
        CP.score("out", "Go to Outreach")
    assert CP.score("x", "Outreach") is None


def test_the_palette_offers_a_screen_s_own_actions_as_the_controls_they_are():
    """Every action is the button that already does it, never a copy of it.

    Which is what keeps a command honest: it wears the control's own label —
    Start reads "Start rehearsal" while dry run is on — it is dimmed exactly
    when the control is disabled, and it cannot become a second, unconfirmed
    way to mail two hundred strangers.
    """
    window = _window()
    shell = window.shell
    shell.go(APP.OUTREACH)
    _app().processEvents()
    screen = shell.screen(APP.OUTREACH)
    try:
        offered = {command.title: command for command in shell.commands()}
        start = offered.get("Outreach — Start sending")
        assert start is not None, "the palette cannot start a send"
        assert start.where == screen.start_btn.text(), \
            "the command says %r and the button says %r" % (
                start.where, screen.start_btn.text())
        assert start.enabled() == screen.start_btn.isEnabled(), \
            "the command and its button disagree about whether it can run"

        assert "Outreach — Prepare a campaign" in offered
        assert "Outreach — Audit the leads" in offered
        assert "Outreach — Stop sending" in offered

        # And a screen that publishes a row of sub-tabs gets one command per
        # tab without the shell knowing what a tab page is.
        for label in screen.TABS:
            assert "Outreach — %s" % label in offered, \
                "no way to reach the %s section by typing" % label
        offered["Outreach — Stats"].run()
        _app().processEvents()
        assert screen.pages.currentIndex() == list(screen.TABS).index("Stats")
    finally:
        shell.go(APP.INPUT)


def _blended(ground: str, scrim: str) -> tuple:
    """`scrim` composited over `ground`, as Qt will paint it, per channel."""
    red, green, blue, alpha = [float(part)
                               for part in re.findall(r"[\d.]+", scrim)]
    if alpha > 1:
        alpha /= 255.0
    under = [int(ground[at:at + 2], 16) for at in (1, 3, 5)]
    return tuple(round(over * alpha + below * (1 - alpha))
                 for over, below in zip((red, green, blue), under))


def test_the_palette_dims_the_screen_it_is_covering():
    """Measured as paint, because the first version of this parsed and did not.

    `scrim` was defined in both palettes and spent nowhere, and the rule that
    finally spent it matched the palette, was parsed, and painted nothing:
    Qt honours `background-color` on a *subclass* of QWidget only once the
    widget is told its background is styled. The ground behind the card came
    out `canvas` exactly, which is what a scrim looks like when it is missing.
    """
    window = _window()
    saved = dict(window.settings)
    try:
        for name in ("dark", "light"):
            theme = TH.theme(name, window.theme.density)
            # Through `_wearing` as well as `apply_appearance`: every helper in
            # this module calls `_app()` on its way past, and `_app()` puts the
            # module's own palette back on the application — so a window styled
            # by hand is measured in whichever theme `_WEARING` names.
            with _wearing(theme):
                window.apply_appearance(dict(saved, theme=name))
                window.shell.go(APP.INPUT)
                _sized(window, DEFAULT_SIZE)
                window.open_palette()
                _sized(window, DEFAULT_SIZE)

                painted = _histogram(window.shell)
                ground = max(painted, key=painted.get)
                channels = tuple(int(ground[at:at + 2], 16)
                                 for at in (1, 3, 5))
                wanted = _blended(theme.color["canvas"], theme.color["scrim"])
                assert ground != theme.color["canvas"].upper(), \
                    "the %s palette covers the screen and dims nothing" % name
                assert all(abs(was - want) <= 1
                           for was, want in zip(channels, wanted)), (
                    "the ground behind the card is %s and %s over %s is "
                    "#%02X%02X%02X"
                    % ((ground, theme.color["scrim"], theme.color["canvas"])
                       + wanted))
                window.shell.dismiss_palette()
                _app().processEvents()
    finally:
        window.shell.dismiss_palette()
        window.settings = dict(saved)
        ST.save_settings(saved)
        window.apply_appearance(saved)
        window.shell.go(APP.INPUT)
        _app()


def test_opening_the_palette_builds_no_screen_that_was_not_already_built():
    """The palette may not undo what the whole shell was written to do.

    Asking every registered screen for its buttons would construct all four the
    first time anybody pressed Ctrl+K — 531ms and the 2,747-line settings
    screen, to fill a list. A command from a screen that does not exist yet is
    also a claim nobody can check: what a lead table can do depends on what is
    selected in it.
    """
    window = _window()
    shell = window.shell
    shell.go(APP.INPUT)
    before = set(shell.built())
    try:
        window.open_palette()
        _app().processEvents()
        assert shell.palette().commands(), "the palette opened on nothing"
        assert set(shell.built()) == before, \
            "opening the palette built %s" % (set(shell.built()) - before,)
    finally:
        shell.dismiss_palette()
        shell.go(APP.INPUT)


def test_arming_a_live_send_from_the_palette_asks_first():
    """The one command in the list a wrong guess about costs somebody else."""
    window = _window()
    saved = dict(window.settings)
    asked = []

    def refuse(_parent, **kwargs):
        asked.append(kwargs)
        return False

    real = APP.components.confirm
    APP.components.confirm = refuse
    try:
        window.settings["dry_run"] = True
        window.toggle_dry_run()
        assert len(asked) == 1, "turning dry run off asked nothing"
        assert asked[0]["danger"] is True
        assert window.settings["dry_run"] is True, \
            "the answer was No and dry run went off anyway"

        APP.components.confirm = lambda _parent, **kw: True
        window.toggle_dry_run()
        assert window.settings["dry_run"] is False
        pill = [b for b in _bar_buttons(window)
                if b.objectName() in ("rehearsal", "live")]
        assert pill and pill[0].text() == "LIVE", \
            "the bar did not follow the command"

        # And back is a retreat, so it is not a question.
        asked[:] = []
        APP.components.confirm = refuse
        window.toggle_dry_run()
        assert asked == [], "putting the safety back on asked permission"
        assert window.settings["dry_run"] is True
    finally:
        APP.components.confirm = real
        window.settings = dict(saved)
        ST.save_settings(saved)
        window.shell.set_dry_run(saved.get("dry_run", True))
        window.shell.go(APP.INPUT)
        _app()


def test_a_theme_change_is_paid_for_by_the_screen_on_show_and_owed_by_the_rest():
    """The measurement: 5,328ms of CPU for one density change on the home screen.

    677ms of it was screens the user could not see rebuilding themselves inside
    the click, `SettingsScreen.restyle()` alone being 626ms of tab pages behind
    a window nobody was looking at. Nothing is skipped, only deferred: `go`
    settles the debt in the instant before a screen is put on top, so the first
    frame anyone sees is already in the new palette. The same change is now
    1,000ms, and arriving on a screen that owes one costs 203ms.
    """
    window = _window()
    saved = dict(window.settings)
    with _all_four_built(window) as shell:
        settings_screen = shell.built(APP.SETTINGS)
        pages = settings_screen.pages
        try:
            window.apply_appearance(dict(saved, density="compact"))
            _app().processEvents()

            assert set(shell.owes()) == {APP.RESULTS, APP.OUTREACH,
                                         APP.SETTINGS}, \
                "the screens off show owe %s" % (shell.owes(),)
            assert settings_screen.pages is pages, \
                "a screen nobody is looking at rebuilt itself inside the click"

            shell.go(APP.SETTINGS)
            _app().processEvents()
            assert APP.SETTINGS not in shell.owes(), "the debt was never paid"
            assert settings_screen.pages is not pages, \
                "arriving on the screen did not put it in the new palette"
            assert settings_screen.template_subject_edit.height() == \
                TH.theme(window.theme.name, "compact").control["md"], \
                "the screen came back at the wrong density"
        finally:
            window.apply_appearance(saved)
            _app().processEvents()


class _Counted(QObject):
    """Counts the appearance events one widget is actually told about."""

    def __init__(self):
        super().__init__()
        self.seen = 0

    def eventFilter(self, _watched, event) -> bool:
        if event.type() in APP._APPEARANCE:
            self.seen += 1
        return False


def test_a_screen_off_show_is_never_told_the_style_changed():
    """The other half of the deferral, and the expensive half.

    `app.setStyleSheet` broadcasts to every widget alive, and this app's own
    widgets answer by re-measuring their text: one swap with four screens built
    is 30,807 calls to `QFontMetrics.horizontalAdvance`, 2,937ms of CPU over 769
    widgets against 145ms over the 142 the home screen alone owns. A screen the
    shell has already decided to rebuild does not need telling, and the work is
    not skipped — it happens once, in the rebuild, instead of twice.
    """
    window = _window()
    saved = dict(window.settings)
    # `QApplication.instance()` and not `_app()`: that helper reinstalls this
    # module's own theme every time it is called, which is a second, unguarded
    # sheet swap — and one landing inside the measurement counts 50 events.
    app = QApplication.instance()
    with _all_four_built(window) as shell:
        counter = _Counted()
        watched = shell.built(APP.SETTINGS).template_subject_edit
        watched.installEventFilter(counter)
        try:
            window.apply_appearance(dict(saved, density="compact"))
            app.processEvents()
            assert counter.seen == 0, (
                "a field on a screen nobody can see was told about the style "
                "change %d times" % counter.seen)
            watched.removeEventFilter(counter)

            # And the same field on the screen on show is told, so a count of
            # zero above is a measurement and not an event filter that never ran.
            shell.go(APP.SETTINGS)
            app.processEvents()
            counter.seen = 0
            watched = shell.built(APP.SETTINGS).template_subject_edit
            watched.installEventFilter(counter)
            window.apply_appearance(dict(saved, density="comfortable"))
            app.processEvents()
            assert counter.seen > 0, \
                "the screen on show was not told about the style change either"
        finally:
            watched.removeEventFilter(counter)
            window.apply_appearance(saved)
            _app().processEvents()


def test_the_repolish_walk_stops_at_what_is_actually_on_screen():
    """140ms over 827 widgets, 630 of them behind the screen on show."""
    window = _window()
    with _all_four_built(window) as shell:
        onstage = shell.onstage()
        everything = window.findChildren(QWidget)
        assert len(onstage) < len(everything) // 2, (
            "the walk still covers %d of the %d widgets alive"
            % (len(onstage), len(everything)))

        seen = {id(widget) for widget in onstage}
        assert id(shell.built(APP.INPUT)) in seen, \
            "the screen on show is not in the walk"
        for key in (APP.RESULTS, APP.OUTREACH, APP.SETTINGS):
            hidden = shell.built(key).findChildren(QWidget)
            assert not (seen & {id(widget) for widget in hidden}), \
                "the walk reaches into %s, which nobody can see" % key
        assert id(_bar(window)) in seen, "the walk skips the bar it repaints"


def test_an_appearance_change_no_longer_warns_about_a_setting_it_cannot_change():
    """One identical warning per appearance change, and nothing else it says.

    `theme.apply()` asked for `AA_EnableHighDpiScaling` on every live theme and
    density change, long after the QApplication existed, and Qt answers a late
    attempt with a warning and nothing else: 18 lines from a sweep that changed
    the density fifteen times, and the only warning the app produced that was
    its own. Startup ordering was already right, so what goes is a request Qt
    was throwing away — and a log that is all noise is a log nobody reads the
    one real line in.
    """
    window = _window()
    saved = dict(window.settings)
    try:
        with _captured_messages() as seen:
            window.apply_appearance(dict(saved, density="compact"))
            window.apply_appearance(dict(saved, density="comfortable"))
            window.toggle_theme()
            window.toggle_theme()
            _app().processEvents()
        scaling = [line for line in seen if "HighDpiScaling" in line]
        assert scaling == [], "%d warnings from four appearance changes: %s" % (
            len(scaling), scaling[:1])

        # The harness can see the warning, so its absence above means something.
        with _captured_messages() as proof:
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        assert any("HighDpiScaling" in line for line in proof), \
            "the message handler never sees the warning this test is about"

        # And the half that can still be set after the app exists still is.
        TH.enable_high_dpi()
        assert QApplication.testAttribute(Qt.AA_UseHighDpiPixmaps) is True
    finally:
        window.settings = dict(saved)
        ST.save_settings(saved)
        window.apply_appearance(saved)
        _app()


def test_the_palette_writes_no_colour_and_no_size_of_its_own():
    """A new file under `ui/` starts at zero literals and stays there.

    The same three rules `tests/test_components.py` holds the component library
    to, scanned over the one file this commit adds: no hex, no `px` in any
    string Qt will parse, and no number reaching a call that fixes a geometry.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "ui", "command_palette.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)

    assert re.findall(r"#[0-9A-Fa-f]{6}\b", source) == [], "a colour is written"

    documentation = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) \
                    and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                documentation.add(id(first.value))
    written = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and id(node) not in documentation]
    assert [text for text in written if re.search(r"\d+\s*px", text)] == []

    geometry = ("setContentsMargins", "setSpacing", "addSpacing",
                "setFixedHeight", "setFixedWidth", "setFixedSize",
                "setMinimumHeight", "setMinimumWidth", "setMaximumWidth",
                "setMinimumSize", "setMaximumSize")
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) \
                or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in geometry:
            continue
        for argument in list(node.args) + [pair.value for pair in node.keywords]:
            if isinstance(argument, ast.Constant) \
                    and isinstance(argument.value, (int, float)):
                offenders.append("%s line %d" % (node.func.attr, node.lineno))
    assert offenders == [], offenders


# ── U15: the rail, the header, and what the chrome remembers ─────────────────
# The whole section exists to answer one objection. A left rail was rejected
# when this shell was built, and the reason given was sound: horizontal space is
# this app's scarcest resource, the audit found table columns clipped in 20 of
# 20 rows, and a 200px rail takes width from every one of them. So the first
# test here is the measurement that objection asked for, and the rail's default
# state is whatever that measurement says it has to be.

RAIL_ROWS = 20

# How far under `RAIL_BREAKPOINT` the count is still asked to hold. The floor is
# 25px under it and this is 20, which is inside the margin on purpose: the turn
# is one pixel wide and asserting on it is asserting on a rounding, so what is
# checked is that the margin exists and is most of what it claims to be.
RAIL_HEADROOM = 20


def _rail_free(window) -> None:
    """Put the width rule back in charge, whatever a test chose."""
    window.shell.restore_rail(None)
    _sized(window, DEFAULT_SIZE)


def _window_results(window, size, count: int = RAIL_ROWS):
    """The window's own results screen at `size`, holding `count` leads.

    Sized before the rows arrive and again after, because that is the order a
    scrape happens in: the window is whatever size it is and the table is built
    and filled inside it. A table filled at one width and measured at another is
    measuring the resize and not the layout.
    """
    window.shell.go(APP.RESULTS)
    _sized(window, size)
    screen = window.shell.screen(APP.RESULTS)
    _filled(screen, _leads(count))
    _sized(window, size)
    return screen


def _cut_cells(table) -> int:
    return sum(1 for row in range(table.rowCount())
               for column in range(table.columnCount())
               if table.is_elided(row, column))


def test_the_collapsed_rail_costs_the_two_tables_nothing():
    """The measurement the rejected rail was rejected for the want of.

    Read as three numbers at 1280x860 with twenty leads, which is the size and
    the case the audit measured its clipping at. The table's viewport with no
    rail at all is 1230px and it cuts 40 of its 160 cells. A collapsed rail is
    56px, so the honest comparison is this table at 1280 against the same table
    at 1280 plus the rail's own width — the width it would have had if the rail
    were not there — and the two counts have to be equal.

    The window at 1280 is also the case that decides the default, and the second
    half measures why: opened by hand at 1280 the rail leaves 1030px and the
    count goes to 60, and at `RAIL_BREAKPOINT` it leaves 1150 and the count is
    40 again. That is where the breakpoint comes from, and the third measurement
    is that it comes from there with room to spare. The count turns at a 1125px
    viewport, so the floor is 1375 and the breakpoint sits 25px above it —
    deliberately, because a threshold set on the exact pixel where a count turns
    is a threshold that rounding puts on the wrong side. `RAIL_HEADROOM` asserts
    that margin rather than describing it: a column spec that grew wide enough
    to lift the floor over the breakpoint would make the rail open itself onto a
    table it costs a column, which is the whole of what this test exists to
    prevent, and it would do it while the two assertions above still passed.

    The leads table is measured last and over the same two pane widths, as the
    column spec that decides them. It cuts 20 of 140 at both.
    """
    window = _window()
    screen = _window_results(window, AUDIT_SIZE)
    try:
        assert window.shell.collapsed(), \
            "the rail opened itself on a window it costs the table to open on"
        rail = _bar(window).width()
        assert rail == CO.rail_width(THEME, collapsed=True)

        table = screen.table
        with_rail = (table.viewport().width(), _cut_cells(table))
        assert not table.horizontalScrollBar().maximum(), \
            "the table scrolls sideways beside the rail"

        _sized(window, (AUDIT_SIZE[0] + rail, AUDIT_SIZE[1]))
        without = (table.viewport().width(), _cut_cells(table))
        assert without[0] - with_rail[0] == rail, (
            "the rail costs the table %dpx and is %dpx wide"
            % (without[0] - with_rail[0], rail))
        assert with_rail[1] == without[1], (
            "the rail cuts %d cells where the same table without it cuts %d"
            % (with_rail[1], without[1]))

        # And what it would have cost open, which is why it is not.
        window.shell.set_collapsed(False)
        _sized(window, AUDIT_SIZE)
        opened = _cut_cells(table)
        assert _bar(window).width() == CO.rail_width(THEME)
        assert opened > with_rail[1], (
            "an open rail at 1280 cuts %d cells and a collapsed one %d, so "
            "there was nothing to collapse for" % (opened, with_rail[1]))

        _sized(window, (APP.RAIL_BREAKPOINT, AUDIT_SIZE[1]))
        assert _cut_cells(table) == with_rail[1], (
            "an open rail at the breakpoint cuts %d cells and the collapsed "
            "rail cuts %d" % (_cut_cells(table), with_rail[1]))

        # And that the breakpoint is inside the band rather than on its edge.
        # Asserted 20px under it and not at the 1125px turn itself: reading a
        # single width on the turn is reading a rounding, and what this is for
        # is the headroom the comment on `RAIL_BREAKPOINT` claims rather than
        # the floor that headroom was measured from.
        _sized(window, (APP.RAIL_BREAKPOINT - RAIL_HEADROOM, AUDIT_SIZE[1]))
        assert _cut_cells(table) == with_rail[1], (
            "the breakpoint is on the edge of the band: %dpx under it an open "
            "rail already cuts %d cells against the collapsed rail's %d"
            % (RAIL_HEADROOM, _cut_cells(table), with_rail[1]))

        # The other table, over the two pane widths this one just measured.
        # Its own screen cannot be asked directly — the outreach layout will not
        # shrink past 1302px, so both halves of a resize land on one layout and
        # the comparison becomes a number against itself. What decides its
        # widths is `_LEAD_COLUMNS` through `components.table()`, and that is
        # measurable on its own in the pane the screen would hand it.
        leads = _spec_measured(_app(), SO._LEAD_COLUMNS, _lead_rows(),
                               (without[0], with_rail[0]))
        wide, narrow = leads[without[0]], leads[with_rail[0]]
        assert (wide[0], narrow[0]) == (without[0], with_rail[0]), \
            "the leads spec was not measured at the widths asked for: %s" % leads
        assert narrow[1] == wide[1], (
            "the leads spec cuts %d cells in the %dpx the rail leaves and %d "
            "in the %dpx it would have had"
            % (narrow[1], narrow[0], wide[1], wide[0]))
        assert narrow[1] > 0, \
            "nothing in these rows is long enough to be cut at either width"
    finally:
        screen._set_idle_mode()
        _rail_free(window)
        # Opening it by hand above wrote an answer down; the tests below measure
        # what an untouched profile does, so the file goes back to empty.
        APP.save_state({})
        window.shell.go(APP.INPUT)


def _lead_rows(count: int = RAIL_ROWS) -> list:
    """`count` rows shaped like the outreach screen's own seven columns.

    The email is `MAIL`, the same 44-character address the results table is
    measured against above, because the length is the whole point: a spec
    measured against values that all fit is a spec nothing has been asked of.
    """
    return [["Harbourfront Dental Care %d" % index, MAIL % index,
             "Scarborough", "Roofing contractor", "%d · moderate" % (30 + index),
             "No mobile layout and a 6.2 second load on a phone", "audited"]
            for index in range(count)]


def _spec_measured(app, columns, rows, viewports) -> dict:
    """One `components.table()` from `columns`, measured at each viewport width.

    The viewport and not the widget, and resized twice to get there: a table's
    frame and its scrollbar come out of the width it is given, so a table asked
    for 1230 lays its columns out in 1228 and the comparison is off by the frame
    at one end and by the frame plus a scrollbar at the other. Measuring the pane
    the screen would actually hand it is the only way this stands in for the real
    table at all.
    """
    table = CO.table(list(columns), density=THEME.density, sortable=False)
    for row in rows:
        table.add_row(row)
    table.show()
    try:
        measured = {}
        for want in viewports:
            for _pass in range(2):
                pad = table.width() - table.viewport().width()
                table.resize(QSize(want + pad, AUDIT_SIZE[1]))
                app.processEvents()
            measured[want] = (table.viewport().width(), _cut_cells(table))
        return measured
    finally:
        table.hide()
        table.deleteLater()


def test_the_rail_collapses_by_hand_and_the_answer_is_remembered():
    """Three ways in — the toggle, Ctrl+B, the palette — and one file out.

    Remembered beside `settings.json` and not in it: `core.settings` is a schema
    that drops every key it does not know on the next save, so chrome state
    stored there would survive exactly until somebody pressed Save.
    """
    window = _window()
    try:
        _sized(window, DEFAULT_SIZE)
        assert window.shell.collapsed()
        assert window.shell.rail_choice() is None, \
            "the width rule is not in charge of a rail nobody has touched"

        toggle = [b for b in _bar(window).findChildren(QWidget)
                  if b.property("role") == "icon"]
        assert len(toggle) == 1, "the rail carries no way to collapse it"
        toggle[0].click()
        _sized(window, DEFAULT_SIZE)
        assert not window.shell.collapsed(), "the toggle did not open the rail"
        assert _bar(window).width() == CO.rail_width(THEME)
        assert window.shell.rail_choice() is False

        written = APP.load_state()
        assert written.get("sidebar_collapsed") is False, \
            "the answer was not written down: %s" % written

        # Ctrl+B is the same command, and the menu says so.
        _keyboard_in(window)
        QTest.keyClick(window, Qt.Key_B, Qt.ControlModifier)
        _sized(window, DEFAULT_SIZE)
        assert window.shell.collapsed(), "Ctrl+B did not close the rail"
        assert window._rail_action.text() == "&Show the sidebar", \
            "the menu still offers to do what it has just done"
        assert APP.load_state().get("sidebar_collapsed") is True

        # And the palette offers it under the name of what it would do next.
        offered = [c for c in window._commands() if c.key == "view.sidebar"]
        assert len(offered) == 1 and offered[0].title == "Show the sidebar"
        assert offered[0].shortcut == "Ctrl+B"
        offered[0].run()
        _sized(window, DEFAULT_SIZE)
        assert not window.shell.collapsed()
    finally:
        _rail_free(window)
        APP.save_state({})


def test_a_window_dragged_narrow_does_not_answer_for_the_user():
    """The width rule may move the rail. It may not claim anybody asked it to.

    The distinction is the whole of what makes the remembered answer worth
    keeping: a window dragged narrow and wide again must leave the profile
    exactly as it found it, and a user who opened the rail on a narrow window
    has to still find it open the next time they are there.
    """
    window = _window()
    try:
        _rail_free(window)
        APP.save_state({})
        _sized(window, (APP.RAIL_BREAKPOINT, DEFAULT_SIZE[1]))
        assert not window.shell.collapsed(), \
            "the rail stayed shut on a wide window"
        _sized(window, DEFAULT_SIZE)
        assert window.shell.collapsed(), "the rail stayed open on a narrow one"
        assert "sidebar_collapsed" not in APP.load_state(), \
            "a resize wrote an answer the user never gave"

        # A choice, on the other hand, outranks the width in both directions.
        window.shell.set_collapsed(False)
        _sized(window, DEFAULT_SIZE)
        assert not window.shell.collapsed(), \
            "a rail opened by hand closed itself again on the same window"
        assert APP.load_state().get("sidebar_collapsed") is False
    finally:
        _rail_free(window)
        APP.save_state({})


def test_the_window_comes_back_the_size_and_place_it_was_left():
    """It never did. The audit found it opening at the same spot every launch.

    Driven through the same two calls a launch makes, against a geometry this
    test puts there, so what is measured is the round trip and not a guess about
    where a window manager will put a window.
    """
    window = _window()
    saved = QRect(window.geometry())
    try:
        wanted = QRect(120, 90, 1180, 700)
        window.setGeometry(wanted)
        _app().processEvents()
        window._remember_geometry()

        box = APP.load_state().get("window")
        assert box == {"x": wanted.x(), "y": wanted.y(),
                       "w": wanted.width(), "h": wanted.height()}, \
            "the window wrote down %s" % (box,)

        window.setGeometry(QRect(0, 0, *MINIMUM_SIZE))
        _app().processEvents()
        window.state = APP.load_state()
        window._restore_geometry()
        _app().processEvents()
        assert (window.width(), window.height()) == \
            (wanted.width(), wanted.height()), \
            "the window came back at %dx%d and was left at %dx%d" % (
                window.width(), window.height(),
                wanted.width(), wanted.height())

        # A position on a monitor that is no longer there is not a position.
        assert window._on_a_screen(QRect(10, 10, 400, 300)) is True
        assert window._on_a_screen(QRect(-9000, -9000, 400, 300)) is False
    finally:
        window.setGeometry(saved)
        APP.save_state({})
        _sized(window, DEFAULT_SIZE)


def test_settings_reached_from_the_rail_is_still_a_detour():
    """The rail is a fourth way into Settings and the only one that could skip.

    Every other route — the menu, the palette, the dry-run pill — goes through
    `on_settings`, which records the screen it was opened from so that Back
    returns to it. A footer row wired straight to `go` would have looked
    identical and quietly answered Back with the home screen, which is exactly
    the finding `_settings_return` exists for. Footer rows announce themselves
    instead, and the window routes them.
    """
    window = _window()
    shell = window.shell
    try:
        for key in (APP.OUTREACH, APP.RESULTS, APP.INPUT):
            shell.go(key)
            _sized(window, DEFAULT_SIZE)
            row = [b for b in _nav_rows(window)
                   if b.accessibleName() == shell.label(APP.SETTINGS)]
            assert len(row) == 1, "the rail has no Settings row"
            row[0].click()
            _app().processEvents()
            assert shell.current_key == APP.SETTINGS, \
                "the rail's Settings row did not open Settings from %s" % key

            shell.screen(APP.SETTINGS).back_signal.emit()
            assert shell.current_key == key, (
                "Settings opened from the rail on %s answered Back with %s"
                % (key, shell.current_key))
    finally:
        shell.go(APP.INPUT)
        _sized(window, DEFAULT_SIZE)


def test_the_page_header_says_what_the_screen_in_front_of_you_is_for():
    """One title in the h1 tier and one line under it, per destination.

    The audit's complaint about this app's chrome was that nothing in it says
    what anything does until you press it — four one-word tabs and no other
    words anywhere. The header is where the words go, and the line is capped by
    being elided rather than by being short: it may not set a floor on how
    narrow the window can be dragged.
    """
    window = _window()
    head = _head(window)
    try:
        for key in (APP.INPUT, APP.RESULTS, APP.OUTREACH, APP.SETTINGS):
            window.shell.go(key)
            _sized(window, DEFAULT_SIZE)
            assert _head(window) is head, "the header was rebuilt for %s" % key
            assert head.title_label.text() == window.shell.label(key), (
                "the header says %r on %s" % (head.title_label.text(), key))
            described = head.description_label
            assert described.isVisible() and described.full_text(), \
                "%s has a title and nothing saying what it is for" % key

        # The description shortens rather than widening what holds it, and only
        # when it has to: a line that fits claims no tooltip it does not need.
        short_line = "Audit the leads."
        head.set_description(short_line)
        _sized(window, MINIMUM_SIZE)
        assert not head.description_label.is_elided()
        assert head.description_label.toolTip() == "", \
            "a line that fits whole still answers a hover with itself"

        long_line = " ".join(
            ["A sentence considerably longer than any window this application "
             "is ever going to be opened at, and then said four more times."]
            * 4)
        head.set_description(long_line)
        _sized(window, MINIMUM_SIZE)
        assert head.description_label.is_elided(), \
            "a description this long fitted an 880px window whole"
        assert head.description_label.toolTip() == long_line, \
            "the cut line does not answer a hover with the whole of it"
        assert window.minimumWidth() <= MINIMUM_SIZE[0], \
            "one sentence in the header raised the window's own minimum"
    finally:
        window.shell.go(APP.INPUT)
        _sized(window, DEFAULT_SIZE)


def test_the_activity_console_tells_a_failure_from_a_delivery_by_shape():
    """The Sending log was 400 proportional lines tinted three ways.

    Colour alone carried which of them was a failure, which is the same finding
    `status_pill` exists for. The console pairs it with a drawn marker and sets
    the whole thing in the monospace face, so a timestamp is a ruler down the
    left instead of a column that moves with every digit.
    """
    with _wearing(TH.theme("dark")) as app:
        CO.use_theme(TH.theme("dark"))
        console = CO.log_console(title="Activity",
                                 placeholder="Nothing sent yet.")
        console.resize(QSize(*MINIMUM_SIZE))
        console.show()
        app.processEvents()

        assert console.list.count() == 1, "an empty console is a bare box"
        assert not console.list.item(0).flags() & Qt.ItemIsEnabled, \
            "the placeholder is a selectable log line"
        assert not console.copy_button.isEnabled()
        assert not console.clear_button.isEnabled()

        console.append("Sent to zeta@example.com", level="done", data=17,
                       tooltip="Double-click to read what was sent")
        console.append("SMTP refused the message", level="error")
        app.processEvents()

        assert console.list.count() == 2
        marks = [console.list.item(row).icon().isNull() for row in range(2)]
        assert marks == [False, False], "a log line arrived with no marker"
        inks = {console.list.item(row).foreground().color().name().upper()
                for row in range(2)}
        assert len(inks) == 2, "a failure and a delivery are the same colour"

        # Every line carries the stamp the caller did not have to write.
        assert re.match(r"^\d\d:\d\d:\d\d  SMTP refused",
                        console.list.item(0).text()), console.list.item(0).text()

        # Monospace, measured: two runs of different letters, one width.
        metrics = QFontMetrics(console.list.font())
        assert metrics.horizontalAdvance("MMMMMMMM") == \
            metrics.horizontalAdvance("iiiiiiii"), \
            "the console is set in a proportional face"

        # Copy is oldest first, whatever order it is read in.
        console.copy()
        board = QApplication.clipboard().text()
        assert board.index("Sent to zeta") < board.index("SMTP refused"), \
            "the copied log is in the order it is shown, not the order it ran"

        opened = []
        console.activated.connect(opened.append)
        console._on_activated(console.list.item(1))
        assert opened == [17], \
            "a line lost what it was about on its way into the console"

        console.clear()
        assert console.list.count() == 1 and not console.copy_button.isEnabled()
        console.hide()


def test_the_shell_writes_no_colour_and_no_size_of_its_own():
    """The rule `tests/test_components.py` holds the library to, on the shell.

    `ui/app.py` grew a rail, a page header and a persisted geometry in this
    commit, and every one of them is a chance to write a colour down. The two
    window sizes are the exception and are named constants with the reason on
    them: a window size is not something a palette or a density has an opinion
    about, and `MINIMUM_SIZE` and `DEFAULT_SIZE` are the only places either is
    written.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "ui", "app.py"), encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)

    assert re.findall(r"#[0-9A-Fa-f]{6}\b", source) == [], "a colour is written"
    assert re.search(r"\brgba?\s*\(", source) is None, "a colour is computed"

    documentation = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) \
                    and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                documentation.add(id(first.value))
    written = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and id(node) not in documentation]
    assert [text for text in written if re.search(r"\d+\s*px", text)] == [], \
        "the shell writes a stylesheet of its own"


def test_nothing_the_chrome_remembers_reaches_a_real_profile():
    """The same rule `tests/conftest.py` holds the other five paths to.

    The file is new and it is written on every close and every collapse, so it
    is exactly the kind of path that quietly resolves at import and lands in a
    developer's own profile. It resolves through `core.settings.SETTINGS_DIR` on
    every call instead, which is what makes the redirect above reach it.
    """
    real = os.path.join(os.path.expanduser("~"), ".leadforge")
    assert os.path.abspath(APP.state_path()).lower().startswith(
        os.path.abspath(_TMP).lower()), \
        "the chrome remembers itself at %s" % APP.state_path()
    assert not os.path.abspath(APP.state_path()).lower().startswith(real.lower())
    assert os.path.dirname(APP.state_path()) == ST.SETTINGS_DIR, \
        "the state file does not follow the settings directory"

    saved, ST.SETTINGS_DIR = ST.SETTINGS_DIR, os.path.join(_TMP, "elsewhere")
    try:
        assert os.path.dirname(APP.state_path()) == ST.SETTINGS_DIR, \
            "the path was captured at import and cannot be redirected"
    finally:
        ST.SETTINGS_DIR = saved
