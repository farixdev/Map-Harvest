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

`SETTINGS_DIR` is redirected into a temp directory before any screen is built,
so constructing one can never read or write a developer's real ~/.mapharvest —
`core.outreach_db` resolves its own path through it on every call, so the
database goes to the same place.
"""
import contextlib
import itertools
import os
import re
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QEvent, QPoint, QRect, QSize, Qt  # noqa: E402
from PyQt5.QtGui import QKeyEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSplitter, QStyle, QStyleOptionButton, QStyleOptionViewItem,
    QWidget,
)

from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402
from core import templates as TPL  # noqa: E402
from core.campaign import OutreachWorker  # noqa: E402
from ui import theme as TH  # noqa: E402
from ui import screen_outreach as SO  # noqa: E402
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
    test, not in the one it was constructed in.
    """
    global _WEARING
    saved, _WEARING = _WEARING, theme
    try:
        yield _app()
    finally:
        _WEARING = saved
        _app()


def _screen(kind: str):
    """One built screen per kind, over a seeded throwaway database."""
    if kind not in _SCREENS:
        app = _app()
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
    return _SCREENS[kind]


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
    assert "Nothing collected" in text and "Home screen" in text, \
        "the empty table still says nothing: %r" % text


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
        assert button.width() >= button.sizeHint().width(), (
            "the browse button is %dpx wide and needs %d at %dx%d, so it "
            "renders '..'" % (button.width(), button.sizeHint().width()) + size)
        assert button.height() == THEME.control["lg"], (
            "it must stay square-ish beside the field: %dpx against the %dpx "
            "the search field beside it takes from control.lg"
            % (button.height(), THEME.control["lg"]))


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
