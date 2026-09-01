"""Offline tests for ui.screen_outreach and the global sheet in ui.app.

These are the screen's appearance and shutdown contracts, so most of them
assert against pixels rather than against the code that produced them: a QSS
rule can be silently beaten by a more specific one, and the only witness to
that is what the widget actually paints.

Qt runs on the offscreen platform, which has no font database. Every assertion
here is therefore written to be independent of text metrics — colours are
sampled, and widths are compared against each widget's own `sizeHint()` rather
than against fixed pixel counts.

Every colour those samples are compared against comes from `ui.theme`. The
hexes this file used to spell out — a #1C1C1E page, a #2C2C2E card, a Stop
button whose red began "#FF6" — were one palette's values, and the app now
ships two; a test that pins a colour cannot survive a theme change, and pinning
one is how a passing suite ends up describing an interface nobody sees any more.
Where an assertion is about a ratio rather than about a widget's wiring it runs
against both palettes.

`SETTINGS_DIR` is redirected into a temp directory before the screen is built,
so constructing it can never read or write a developer's real ~/.leadforge —
`core.outreach_db` resolves its own path through it on every call, so the
database goes to the same place.
"""
import contextlib
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtGui import QPalette  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QLabel, QVBoxLayout, QWidget,
)

from core import outreach_db as DB  # noqa: E402
from core import settings as ST  # noqa: E402
from ui import app as APP  # noqa: E402
from ui import components as CO  # noqa: E402
from ui import theme as TH  # noqa: E402
from ui import screen_outreach as SO  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="leadforge-outreach-ui-")
_APP = None
_SCREEN = None

THEME = TH.theme()

# Both palettes, for the assertions that are a claim about a relationship. One
# that only holds in the dark theme is half a contract.
THEMES = (TH.theme("dark"), TH.theme("light"))

_WEARING = THEME

# The two sizes every screen has to survive: what `MainWindow` opens at, and the
# minimum it will let the user drag down to. Window geometry, not design tokens
# — `ui.theme` has no say in how big the window is.
DEFAULT_SIZE, MINIMUM_SIZE = (1080, 760), (880, 620)

# Bench geometry for the card built from scratch below — big enough to hold a
# row of children and small enough to sample whole, and deliberately not a
# token: it is a frame for a measurement, not a size the app ever paints.
_BENCH_CARD = QSize(240, 80)


def _app() -> QApplication:
    """The one QApplication for this module, styled exactly as `ui.app.run`.

    Both halves of it, and the second half is not optional any more.
    `ui.components` resolves a colour in Python at build time and writes it into
    the widget's own stylesheet, which beats the application's — so a process
    that only calls `theme.apply` leaves every component-built widget wearing
    the palette it was constructed in. `ui.app.run` calls `components.use_theme`
    in the same breath, and so does this.
    """
    global _APP
    if _APP is None:
        ST.SETTINGS_DIR = _TMP
        ST.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
        _APP = QApplication.instance() or QApplication([])
    if _APP.styleSheet() != TH.stylesheet(_WEARING):
        TH.apply(_APP, _WEARING)
    CO.use_theme(_WEARING)
    return _APP


@contextlib.contextmanager
def _wearing(theme):
    """The whole process in `theme`, and back to the default afterwards.

    Three steps, exactly the three `MainWindow.apply_appearance` takes: the
    sheet onto the application, the theme into `ui.components`, and `restyle()`
    on the screen. The third is what this used to be missing — a repolish alone
    cannot reach a colour a component wrote into a widget's own sheet, so the
    screen would be measured in the palette it was built in and every ratio
    below would be a ratio from the other theme.
    """
    global _WEARING
    saved, _WEARING = _WEARING, theme
    try:
        _restyle(_app())
        yield _APP
    finally:
        _WEARING = saved
        _restyle(_app())


def _restyle(app) -> None:
    """Put the built screen into the palette the process is now wearing."""
    if _SCREEN is not None:
        _SCREEN.restyle()
        app.processEvents()


def _screen():
    """A built OutreachScreen over a seeded throwaway database.

    Built once: constructing it starts a timer and opens the store, and the
    tests below only read from it or drive it through its own handlers.
    """
    global _SCREEN
    if _SCREEN is None:
        app = _app()
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        # Inserted worst-score-first so a table that kept the database's id
        # order, or reversed it, is distinguishable from one sorted by score.
        for name, score in (("Zeta Roofing", 30), ("Alpha Plumbing", 88),
                            ("Mid Electric", 55)):
            DB.upsert_lead(conn, {"email": "%s@example.com" % name.split()[0].lower(),
                                  "name": name, "opportunity_score": score,
                                  "status": "audited", "source": "test"})
        DB.suppress(conn, "nobody@example.com", "test fixture")
        _SCREEN = SO.OutreachScreen()
        _SCREEN.resize(QSize(*DEFAULT_SIZE))
        _SCREEN.show()
        app.processEvents()
    return _SCREEN


def _colours(widget) -> set:
    """Every colour `widget` paints, as uppercase hex."""
    image = widget.grab().toImage()
    return {image.pixelColor(x, y).name().upper()
            for y in range(image.height()) for x in range(image.width())}


def _histogram(image, rect) -> dict:
    """Every colour inside `rect` of `image`, counted."""
    counts: dict = {}
    for y in range(max(0, rect.top()), min(image.height() - 1, rect.bottom()) + 1):
        for x in range(max(0, rect.left()), min(image.width() - 1, rect.right()) + 1):
            name = image.pixelColor(x, y).name().upper()
            counts[name] = counts.get(name, 0) + 1
    return counts


def _luminance(colour: str) -> float:
    """WCAG relative luminance of an "#rrggbb" string."""
    channels = []
    for pair in (colour[1:3], colour[3:5], colour[5:7]):
        value = int(pair, 16) / 255.0
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(one: str, two: str) -> float:
    first, second = _luminance(one) + 0.05, _luminance(two) + 0.05
    return max(first, second) / min(first, second)


# What an unpainted pixel comes back as. A widget with a transparent
# background yields a grab whose buffer was never written to, and that is Qt's
# own answer, not a colour any palette names.
_UNPAINTED = "#000000"


@contextlib.contextmanager
def _no_review_dialog(accept: bool = True):
    """`_on_plan_ready` without the modal it opens, for an offscreen run.

    Not a weakening of anything: Qt 5.15's offscreen platform has no event loop
    a modal can return from, so `_CampaignReviewDialog.exec_()` never comes
    back and the whole suite hangs on the call. That is not hypothetical — it
    is what `tests/test_outreach_screen.py` did before this existed, and a run
    of the file stopped dead at the second `_on_plan_ready` below.

    The dialog itself is driven for real, with a platform and a click, by
    `tests/test_modals.py::test_campaign_review_dialog_shows_metrics_and_can_be_accepted`.
    That is the rule the whole file follows: modals are somebody else's process.
    """
    from PyQt5.QtWidgets import QDialog

    class _Stub(QDialog):
        opened = []

        def __init__(self, plan, parent=None):
            super().__init__(parent)
            _Stub.opened.append(plan)

        def exec_(self):
            return QDialog.Accepted if accept else QDialog.Rejected

    real = SO._CampaignReviewDialog
    SO._CampaignReviewDialog = _Stub
    try:
        yield _Stub.opened
    finally:
        SO._CampaignReviewDialog = real
        _Stub.opened = []


def _audit(error: str = "", status: int = 0, gap: str = "",
           reason: str = None) -> dict:
    """One `core.audit` result, in the shape the crawl writes today.

    Built through `core.audit.unreachable_reason` rather than with the codes
    spelled out, because that is what `core.audit._blank` does: a test that
    hard-coded the mapping would keep passing after the crawl changed it, which
    is the one thing these assertions exist to catch.

    `reason` forces a code directly — for the states that answer 200 and still
    tell the crawl nothing, where there is no error string to derive it from.
    """
    from core import audit as AU

    code = reason if reason is not None else AU.unreachable_reason(error, status)
    return {
        "url": "https://example.test",
        "final_url": "" if code else "https://example.test",
        "reachable": not code, "status": status,
        "load_ms": 0, "pages": [], "page_count": 0, "title": "", "description": "",
        "h1": "", "brand": "", "tech": {}, "services": [],
        "signals": {"has_email": False},
        "gaps": [{"key": "g", "title": gap, "severity": 3}] if gap else [],
        "opportunity_score": 61 if gap else 0, "error": error,
        "unreachable_reason": code,
        "unreachable_detail": AU.unreachable_detail(code),
    }


def _accent(widget) -> str:
    """The colour `widget` paints most, ignoring the unpainted ground.

    For these transparent outlined buttons that is the border, which is what
    carries their enabled/disabled state.
    """
    image = widget.grab().toImage()
    counts: dict = {}
    for y in range(image.height()):
        for x in range(image.width()):
            name = image.pixelColor(x, y).name().upper()
            counts[name] = counts.get(name, 0) + 1
    counts.pop(_UNPAINTED, None)
    return max(counts, key=counts.get) if counts else ""


# ── D18: pluralisation ───────────────────────────────────────────────────────

def test_plural_uses_es_after_a_sibilant():
    assert SO._plural(0, "address") == "0 addresses"
    assert SO._plural(2, "address") == "2 addresses"
    assert SO._plural(3, "box") == "3 boxes"
    assert SO._plural(2, "batch") == "2 batches"


def test_plural_keeps_the_existing_call_sites_intact():
    assert SO._plural(1, "address") == "1 address"
    assert SO._plural(1, "lead") == "1 lead"
    for count, word, expected in (
        (0, "lead", "0 leads"), (2, "site", "2 sites"), (2, "gap", "2 gaps"),
        (0, "email", "0 emails"), (3, "day", "3 days"),
        (2, "account", "2 accounts"), (2, "follow-up", "2 follow-ups"),
        (2, "thing", "2 things"), (0, "message", "0 messages"),
    ):
        assert SO._plural(count, word) == expected


def test_suppression_counter_reads_as_english():
    screen = _screen()
    screen._refresh_suppression()
    assert "addresss" not in screen.supp_count.text()
    assert screen.supp_count.text() == "1 address"


# ── D9: labels must not paint over their card ────────────────────────────────

def test_labels_inside_a_card_do_not_paint_their_own_background():
    """Every label on a #card must leave the card's fill showing behind it.

    The global sheet's `QWidget` rule matches QLabel too, so without an
    explicit transparent rule each label paints an opaque `canvas` rectangle on
    the `surface` card. Sampled from the card's own render at the label's
    corner: a transparent label grabbed on its own yields an unpainted buffer,
    so the parent is the only honest witness.
    """
    screen = _screen()
    app = _app()
    checked = 0
    for tab in range(4):
        screen._goto_tab(tab)
        app.processEvents()
        for frame in screen.findChildren(QFrame, "card"):
            image = frame.grab().toImage()
            if image.width() < 6 or image.height() < 6:
                continue
            fill = image.pixelColor(image.width() // 2, 3).name().upper()
            for label in frame.findChildren(QLabel):
                if not label.text() or not label.isVisible():
                    continue
                corner = label.mapTo(frame, label.rect().bottomRight())
                if not image.rect().contains(corner):
                    continue
                painted = image.pixelColor(corner.x(), corner.y()).name().upper()
                assert painted == fill, (
                    "label %r paints %s over a %s card"
                    % (label.text()[:32], painted, fill))
                checked += 1
    assert checked > 10, "expected to sample the cards on every tab"


def test_the_sheet_still_lets_a_label_carry_its_own_fill():
    """#toast and #warning set a background under an id selector, which wins."""
    for theme in THEMES:
        sheet = TH.stylesheet(theme)
        assert "QLabel {\n    background: transparent;\n}" in sheet
        assert "QLabel#toast" in sheet
        assert "background-color: %s;" % theme.color["raised"] in sheet


# ── D20: a disabled Stop must look disabled ──────────────────────────────────

def test_disabled_danger_button_dims():
    """#danger is an id selector, so it used to beat QPushButton:disabled.

    Pause is the reference: it is the outlined button beside Stop, it dims
    correctly, and the two are enabled and disabled together.

    "Red" is `danger.border` and `danger.text` now rather than a hex beginning
    "#FF6", and the two are read out of whichever palette is loaded: Stop is a
    transparent outlined button, so the colour it paints most is its border,
    which is what carries the state. Both palettes, because a rule about what a
    disabled control may not still be painting is a rule in both.
    """
    for theme in THEMES:
        with _wearing(theme) as app:
            screen = _screen()
            screen._goto_tab(2)
            app.processEvents()

            live = {theme.color[name].upper()
                    for name in ("danger.border", "danger.text",
                                 "danger.default")}
            screen.stop_btn.setEnabled(True)
            app.processEvents()
            assert _accent(screen.stop_btn) == theme.color["danger.border"].upper(), (
                "%s: an enabled Stop paints %s, not the danger outline %s"
                % (theme.name, _accent(screen.stop_btn),
                   theme.color["danger.border"]))

            screen.stop_btn.setEnabled(False)
            screen.pause_btn.setEnabled(False)
            app.processEvents()
            dead = _colours(screen.stop_btn)
            assert not (dead & live), (
                "%s: a disabled Stop still paints its live danger colours: %s"
                % (theme.name, sorted(dead & live)))
            assert _accent(screen.stop_btn) == _accent(screen.pause_btn), \
                "Stop and Pause must dim to the same thing when both are disabled"
            assert _accent(screen.stop_btn) == theme.color["border.subtle"].upper(), (
                "%s: a disabled button outlines itself in %s, not %s"
                % (theme.name, _accent(screen.stop_btn),
                   theme.color["border.subtle"]))


# ── D21: the lead table's sort order ─────────────────────────────────────────

def test_lead_table_sorts_by_score_not_reverse_alphabetically():
    screen = _screen()
    screen._reload_leads()
    header = screen.lead_table.horizontalHeader()
    assert header.sortIndicatorSection() == SO._COL_SCORE
    assert header.sortIndicatorOrder() == Qt.DescendingOrder

    table = screen.lead_table
    names = [table.item(row, SO._COL_NAME).text() for row in range(table.rowCount())]
    assert names == ["Alpha Plumbing", "Mid Electric", "Zeta Roofing"]
    # The old defect: a fresh QTableWidget's indicator is (column 0, descending),
    # which reversed the database order by business name.
    assert names != sorted(names, reverse=True) or len(set(names)) < 2


def test_a_column_header_reverses_the_order_it_already_sorts_by():
    """Sorting is driven by hand now, so the header has to still work.

    `components.table()` is built with `sortable=False` on this screen because
    Score and Status are painted by a delegate from data on the item rather
    than from its text, and `QTableWidget.sortItems` reorders items underneath
    a painted row. The records are sorted and the rows rebuilt instead, and
    this is the header contract that swap has to keep.
    """
    screen = _screen()
    table = screen.lead_table
    try:
        screen._on_header_clicked(SO._COL_NAME)
        assert table.horizontalHeader().sortIndicatorSection() == SO._COL_NAME
        assert [table.item(row, SO._COL_NAME).text()
                for row in range(table.rowCount())] == \
            ["Alpha Plumbing", "Mid Electric", "Zeta Roofing"]

        screen._on_header_clicked(SO._COL_NAME)
        assert table.horizontalHeader().sortIndicatorOrder() == Qt.DescendingOrder
        assert [table.item(row, SO._COL_NAME).text()
                for row in range(table.rowCount())] == \
            ["Zeta Roofing", "Mid Electric", "Alpha Plumbing"]
    finally:
        screen._sort = (SO._COL_SCORE, Qt.DescendingOrder)
        screen._reload_leads()


def test_unaudited_leads_sort_below_scored_ones():
    """An em dash compares greater than a digit as text; the score must not."""
    screen = _screen()
    conn = DB.connect(os.path.join(_TMP, "outreach.db"))
    DB.upsert_lead(conn, {"email": "fresh@example.com", "name": "Fresh Lead",
                          "opportunity_score": 0, "status": "new", "source": "test"})
    try:
        screen._reload_leads()
        table = screen.lead_table
        badges = [table.item(row, SO._COL_SCORE).text()
                  for row in range(table.rowCount())]
        assert [badge.split(" ·")[0] for badge in badges] == \
            ["88", "55", "30", "—"]
    finally:
        conn.execute("DELETE FROM leads WHERE email = ?", ("fresh@example.com",))
        conn.commit()
        screen._reload_leads()


def test_score_and_status_never_rest_on_colour_alone():
    """The finding: nine statuses over seven hexes, three of them identical.

    `bounced`, `failed` and `suppressed` measured 1.00:1 against each other, so
    a monochrome print, a colour-blind reader and a screenshot at 50% all read
    three different outcomes as one — and the opportunity score was a coloured
    number with no band word anywhere near it. Both columns carry the label
    `components` gives them, so the cell says what it means with the colour
    switched off, and a screen reader and the filter box read the same words.
    """
    screen = _screen()
    screen._reload_leads()
    table = screen.lead_table
    for row in range(table.rowCount()):
        score = table.item(row, SO._COL_SCORE).text()
        status = table.item(row, SO._COL_STATUS).text()
        assert "·" in score and score.split("·")[1].strip(), \
            "the score cell is %r — a number and no band" % score
        assert status.strip(), "the status cell says nothing"
        assert table.tooltip_at(row, SO._COL_SCORE), \
            "the score answers a hover with nothing"
        assert table.tooltip_at(row, SO._COL_STATUS), \
            "the status answers a hover with nothing"

    marks = {status: CO.status_pill(status).text()
             for status in ("bounced", "failed", "suppressed")}
    assert len(set(marks.values())) == 3, \
        "the three worst outcomes still read as one: %s" % marks


# ── D19: the Campaign tab's primary actions must fit ─────────────────────────

def test_campaign_buttons_are_never_clipped():
    """Both at the default window size and at the window's minimum.

    The 340px left column could not fit 'Prepare campaign' and 'Open Sending'
    side by side, so the primary button was cut mid-glyph with no ellipsis.
    """
    screen = _screen()
    app = _app()
    for width, height in (DEFAULT_SIZE, MINIMUM_SIZE):
        screen.resize(QSize(width, height))
        screen._goto_tab(1)
        screen.goto_sending_btn.show()
        screen.layout().activate()
        app.processEvents()
        for button in (screen.prepare_btn, screen.goto_sending_btn):
            assert button.width() >= button.sizeHint().width(), (
                "%r is %dpx wide but needs %d at %dx%d"
                % (button.text(), button.width(), button.sizeHint().width(),
                   width, height))
    screen.resize(QSize(*DEFAULT_SIZE))


# ── D22: the activity log's empty state ──────────────────────────────────────

def test_activity_log_starts_with_a_placeholder():
    screen = _screen()
    assert screen.log_list.count() == 1
    item = screen.log_list.item(0)
    assert "Nothing sent yet" in item.text()
    assert item.flags() == Qt.NoItemFlags, "the placeholder must not be selectable"


def test_first_log_line_replaces_the_placeholder():
    screen = _screen()
    screen._clear_log()
    screen._append_log("alpha@example.com — first touch", "done")
    assert screen.log_list.count() == 1
    assert "Nothing sent yet" not in screen.log_list.item(0).text()
    screen._append_log("mid@example.com — first touch", "done")
    assert screen.log_list.count() == 2
    screen._clear_log()
    assert screen.log_list.count() == 1


# ── D10: quitting during a plan must not hard-kill the thread ────────────────

def test_plan_worker_stops_co_operatively():
    """`ui.app._stop_thread` must get the thread back without terminating it.

    A real plan is minutes of site crawling, so quitting mid-plan is the normal
    exit path rather than an edge case. Without a `stop()` the escalation in
    `_stop_thread` waits out its full five seconds and then calls
    `QThread.terminate()` in the middle of a crawl.
    """
    _app()
    seen = {"leads": 0, "cancelled": False}
    original = SO.plan_campaign

    def fake_plan(conn, **kwargs):
        should_stop = kwargs.get("should_stop")
        assert callable(should_stop), "plan_campaign was not handed a stop check"
        for index in range(2000):
            seen["leads"] = index
            if should_stop():
                seen["cancelled"] = True
                return {"queued": index, "cancelled": True}
            time.sleep(0.002)
        return {"queued": 2000}

    SO.plan_campaign = fake_plan
    try:
        worker = SO._PlanWorker(1, [{"email": "a@example.com"}], "tpl", {})
        assert hasattr(worker, "stop"), "_stop_thread looks for stop()"
        worker.start()
        deadline = time.time() + 5.0
        while seen["leads"] < 3 and time.time() < deadline:
            time.sleep(0.005)
        assert seen["leads"] >= 3, "the worker never started planning"

        started = time.time()
        APP._stop_thread(worker)
        elapsed = time.time() - started
    finally:
        SO.plan_campaign = original

    assert elapsed < 2.0, (
        "_stop_thread took %.2fs — it fell through wait(5000) to terminate()"
        % elapsed)
    assert seen["cancelled"], "the plan unwound by itself rather than being killed"
    assert not worker.isRunning()


def test_a_cancelled_plan_says_so():
    """The same two assertions, with the review dialog stubbed out.

    The second `_on_plan_ready` opens `_CampaignReviewDialog`, and an offscreen
    `exec_()` never returns — so this call used to hang the process and take
    the whole file with it. `_no_review_dialog` is why it does not; the dialog
    is driven for real in `tests/test_modals.py`.
    """
    screen = _screen()
    with _no_review_dialog() as opened:
        screen._on_plan_ready({"queued": 4, "days": 1, "cancelled": True})
        assert "Stopped before" in screen.plan_summary.text()
        assert not opened, "a cancelled plan must not ask for approval"

        screen._on_plan_ready({"queued": 4, "days": 1})
        assert "Stopped before" not in screen.plan_summary.text()
        assert len(opened) == 1, "a finished plan is reviewed before it counts"


def test_a_plan_that_queued_nothing_does_not_open_a_review():
    """A dialog whose whole content is six zeros is a click for nothing.

    The card's own warning already says why nothing was queued, and it stays on
    screen where a dismissed dialog does not.
    """
    screen = _screen()
    with _no_review_dialog() as opened:
        screen._on_plan_ready({"queued": 0, "skipped": 3,
                               "skip_reasons": {"already contacted": 3}})
        assert not opened
    assert screen.plan_warning.isVisible() or screen.plan_summary.text()


def test_a_refused_plan_takes_its_messages_back_out():
    screen = _screen()
    campaign = DB.create_campaign(screen.conn, "refused", "gap_direct", {}, {})
    DB.queue_message(screen.conn, {"campaign_id": campaign,
                                   "lead_id": screen._leads[0]["id"], "step": 0,
                                   "subject": "s", "body_text": "b",
                                   "scheduled_at": 1.0})
    screen._campaign_id = campaign
    with _no_review_dialog(accept=False):
        screen._on_plan_ready({"queued": 1, "days": 1})
    assert SO._int_of(DB.campaign_stats(screen.conn, campaign).get("total")) == 0
    assert SO._text_of(DB.get_campaign(screen.conn, campaign).get("status")) == "failed"
    # And the Sending tab says which of the two empty queues this is. "Nothing
    # left in this campaign's queue" is what a finished campaign says, and this
    # one never started.
    screen._campaign_id = campaign
    head, why = screen._send_health()
    assert "discarded" in head, head
    assert "Prepare a new one" in why, why


# ── R14: a container inside a card must not punch through it ─────────────────

def test_a_plain_container_paints_nothing_on_a_card():
    """The rule itself, built from scratch rather than found on a screen.

    A QLayout needs a widget to live on, so cards end up holding bare QWidgets
    whose only job is to hold a row of children. The sheet's `QWidget` rule
    matches every one of them and gives it the page's own `canvas`, which lands
    on top of the card fill — the labels inside are transparent and therefore
    innocent, so sampling them alone finds nothing. Asserted here so a card that
    nests a container tomorrow is covered without anyone remembering to check.

    Both palettes, since the rule is about which of two grounds wins and both
    of them move when the theme does.
    """
    for theme in THEMES:
        with _wearing(theme) as app:
            page = theme.color["canvas"].upper()
            card = theme.color["surface"].upper()
            frame = QFrame()
            frame.setObjectName("card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(theme.space["4"], theme.space["3"],
                                   theme.space["4"], theme.space["3"])
            holder = QWidget()
            inner = QVBoxLayout(holder)
            inner.setContentsMargins(0, 0, 0, 0)
            label = QLabel("sam@example.com")
            label.setObjectName("muted")
            inner.addWidget(label)
            box.addWidget(holder)
            frame.resize(_BENCH_CARD)
            frame.show()
            app.processEvents()

            image = frame.grab().toImage()
            counts = _histogram(image, image.rect())
            assert counts.get(page, 0) == 0, (
                "%s: a container painted %d px of page colour onto the card"
                % (theme.name, counts[page]))
            assert image.pixelColor(image.width() // 2, 3).name().upper() == card, (
                "%s: the card itself paints %s, not %s"
                % (theme.name, image.pixelColor(image.width() // 2, 3).name(),
                   card))
            frame.hide()


def test_no_card_paints_page_colour_behind_a_label_with_an_account_set_up():
    """The Accounts card only exists once there is an account to draw.

    With none configured it is a single hint sentence added straight to the
    card's own layout, which is why the earlier sweep of every card on every tab
    passed while this one was wrong: the per-account rows — and the containers
    they need — are built by `_refresh_accounts` from the settings file.
    """
    screen = _screen()
    app = _app()
    page = THEME.color["canvas"].upper()
    data = ST.load_settings()
    data["smtp_accounts"] = [{"email": "sam@example.com", "app_password": "app-pw",
                              "display_name": "Sam", "daily_cap": 10,
                              "enabled": True}]
    ST.save_settings(data)
    seen = []
    try:
        screen.refresh()
        for tab in range(4):
            screen._goto_tab(tab)
            app.processEvents()
            for frame in screen.findChildren(QFrame, "card"):
                if not frame.isVisible():
                    continue
                image = frame.grab().toImage()
                if image.width() < 6 or image.height() < 6:
                    continue
                fill = image.pixelColor(image.width() // 2, 3).name().upper()
                for label in frame.findChildren(QLabel):
                    if not label.text() or not label.isVisible():
                        continue
                    rect = label.rect().translated(
                        label.mapTo(frame, label.rect().topLeft()))
                    counts = _histogram(image, rect)
                    assert counts.get(page, 0) == 0, (
                        "%d of %d px behind %r are page colour, not the %s card"
                        % (counts[page], sum(counts.values()),
                           label.text()[:32], fill))
                    seen.append(label.text())
    finally:
        data = ST.load_settings()
        data["smtp_accounts"] = []
        ST.save_settings(data)
        screen.refresh()

    assert any("sam@example.com" in text for text in seen), \
        "the Accounts card was never drawn, so nothing was proved"
    assert any("/ 10 today" in text for text in seen), \
        "the per-account counter was never drawn"
    assert len(seen) > 10, "expected to sample the cards on every tab"


# ── R15: the activity log must not scroll sideways ───────────────────────────

def test_the_activity_log_never_scrolls_sideways():
    """At the window's minimum the log panel is 494px, and its lines are longer.

    The screen's own layout will not shrink below ~1140px, so the minimum size
    is imposed here the same way `MainWindow` imposes it: an explicit minimum
    that beats the layout's. Both the placeholder sentence and a real line for a
    business with a long name have to fit without a horizontal scrollbar, which
    is the only thing that hides text off to the right.
    """
    screen = _screen()
    app = _app()
    screen._goto_tab(2)
    screen.setMinimumSize(QSize(1, 1))
    try:
        for width, height in (DEFAULT_SIZE, MINIMUM_SIZE):
            screen.resize(QSize(width, height))
            screen.layout().activate()
            app.processEvents()
            log = screen.log_list
            assert not log.horizontalScrollBar().isVisible(), (
                "the log scrolls sideways at %dx%d — viewport %dpx"
                % (width, height, log.viewport().width()))
            for index in range(log.count()):
                item = log.item(index)
                assert log.visualItemRect(item).width() <= log.viewport().width(), (
                    "%r is wider than the %dpx panel at %dx%d"
                    % (item.text()[:32], log.viewport().width(), width, height))

            screen._append_log(
                "Alpha Plumbing Renovations and Roofing Contractors — follow-up 2",
                "done")
            app.processEvents()
            assert not log.horizontalScrollBar().isVisible(), (
                "a long log line scrolls the panel sideways at %dx%d" % (width, height))
            screen._clear_log()
    finally:
        screen.setMinimumSize(QSize(0, 0))
        screen.resize(QSize(*DEFAULT_SIZE))
        screen._clear_log()
        app.processEvents()


# ── R16: hint text has to be readable on both of its grounds ─────────────────

def test_hint_text_clears_wcag_aa_on_the_page_and_on_a_card():
    """#hint sentences sit on `canvas` on some screens and `surface` on cards.

    Read back off a real label rather than out of the sheet, because a more
    specific rule could always be beating the one that names the colour. Both
    grounds and both texts now come from the palette that is loaded, and the
    sweep runs over both palettes: the ratios are 10.24:1 and 7.20:1 in dark,
    4.56:1 and 6.56:1 in light, and which of the two grounds is the harder one
    is not the same in each — the light page is a mid grey, so there the card is
    the *easier* ground and a test written against the dark theme's ordering
    would have measured the wrong one.
    """
    for theme in THEMES:
        with _wearing(theme) as app:
            screen = _screen()
            screen._goto_tab(2)
            app.processEvents()
            hint = screen.send_note.palette().color(
                QPalette.WindowText).name().upper()
            assert hint == theme.color["text.tertiary"].upper(), (
                "%s: a hint paints %s and the hint token is %s"
                % (theme.name, hint, theme.color["text.tertiary"]))
            for name in ("canvas", "surface"):
                ground = theme.color[name]
                assert _contrast(hint, ground) >= 4.5, (
                    "%s: %s on %s (%s) is %.2f:1, under WCAG AA's 4.5:1"
                    % (theme.name, hint, name, ground, _contrast(hint, ground)))
            body, card = theme.color["text.primary"], theme.color["surface"]
            assert _contrast(body, card) > _contrast(hint, card), (
                "%s: a hint must stay quieter than the body text beside it — "
                "%.2f:1 against %.2f:1"
                % (theme.name, _contrast(hint, card), _contrast(body, card)))


def test_every_hint_on_the_screen_uses_that_colour():
    """One rule, so one colour — including the hints inside cards."""
    screen = _screen()
    app = _app()
    hint = screen.send_note.palette().color(QPalette.WindowText).name().upper()
    checked = 0
    for tab in range(4):
        screen._goto_tab(tab)
        app.processEvents()
        for label in screen.findChildren(QLabel):
            if label.objectName() != "hint" or not label.isVisible():
                continue
            painted = label.palette().color(QPalette.WindowText).name().upper()
            assert painted == hint, "%r paints %s, not %s" % (
                label.text()[:32], painted, hint)
            checked += 1
    assert checked >= 4, "expected hints on more than one tab"


# ── P1: the lead table must not cost O(N²) to fill in ────────────────────────
# Every assertion here counts row visits rather than milliseconds. The finding
# was a complexity, not a constant — 77.1ms a lead at 500 leads and 796.4ms a
# lead at 5,000 — and a clock cannot tell those apart on a machine that happens
# to be fast. A count that does not move when the table quadruples can.


@contextlib.contextmanager
def _extra_leads(count: int):
    """`count` throwaway leads in the store, with the screen showing them."""
    screen = _screen()
    screen.resize(QSize(*DEFAULT_SIZE))
    screen._goto_tab(0)
    screen.layout().activate()
    _app().processEvents()
    conn = DB.connect(os.path.join(_TMP, "outreach.db"))
    for index in range(count):
        DB.upsert_lead(conn, {"email": "bulk%04d@example.com" % index,
                              "name": "Bulk Business %04d" % index,
                              "city": "Toronto", "category": "Roofing contractor",
                              "opportunity_score": index % 101,
                              "status": "new", "source": "bulk"})
    screen._reload_leads()
    try:
        yield screen
    finally:
        conn.execute("DELETE FROM leads WHERE source = 'bulk'")
        conn.commit()
        screen.lead_search.setText("")
        screen.status_filter.setCurrentIndex(0)
        screen._reload_leads()
        _app().processEvents()


def _audit_cost(screen, row: int) -> tuple:
    """(rows read, rows re-hidden) for one audited lead landing on `row`.

    The two surfaces the old code walked. `_lead_at` is every record the screen
    reads to answer a question about the table; `setRowHidden` is every row the
    filter pass re-decides. Both used to be O(N) per audited lead, and
    `_refresh_lead_actions` inside the pass made it three times O(N).
    """
    table = screen.lead_table
    seen = {"read": 0, "hidden": 0}
    real_read = type(screen)._lead_at
    real_hide = type(table).setRowHidden

    def counted_read(index):
        seen["read"] += 1
        return real_read(screen, index)

    def counted_hide(index, hide):
        seen["hidden"] += 1
        return real_hide(table, index, hide)

    screen._lead_at = counted_read
    table.setRowHidden = counted_hide
    try:
        lead = screen._leads[row]
        screen._on_lead_audited(dict(lead, status="audited",
                                     opportunity_score=64,
                                     audit_json='{"gaps": [{"title": "No booking"}]}'))
    finally:
        del screen._lead_at
        del table.setRowHidden
    return seen["read"], seen["hidden"]


def test_an_audited_lead_costs_the_same_whatever_the_table_holds():
    """The whole of the first finding, as a number that must not move.

    `_on_lead_audited` used to call `_apply_filters`, which walked every row,
    and `_refresh_lead_actions` inside it walked the table three more times to
    write four button labels. So one audit pass over N leads cost N² row
    visits: 38.6 seconds of frozen window at 500 leads, 66 minutes at 5,000.

    An audited lead now rewrites its own row, adjusts the counts it moved, and
    re-decides the visibility of that row alone — so quadrupling the table
    leaves the cost of one audit exactly where it was.
    """
    small = large = None
    with _extra_leads(40) as screen:
        small = _audit_cost(screen, 0)
    with _extra_leads(160) as screen:
        large = _audit_cost(screen, 0)

    assert small == large, (
        "one audited lead cost %s row visits in a 43-row table and %s in a "
        "163-row one — the work is still proportional to the table"
        % (small, large))
    reads, hidden = large
    assert hidden <= 1, (
        "an audited lead re-decided %d rows' visibility; only its own may move"
        % hidden)
    assert reads <= 8, (
        "an audited lead read %d records; the labels need a count and three "
        "names" % reads)


def test_a_row_is_read_from_the_list_it_was_built_from():
    """Not through `Qt.UserRole`, which marshals a dict at 50µs a read.

    The record used to ride on the row's own first cell, so every pass over
    the table paid Qt to convert a dict out of a QVariant once per row — more
    than half the cost of filtering, and it was paid again for every audited
    lead. Row `n` is `self._leads[n]` by construction, so the list is the
    answer and the cell carries nothing.
    """
    screen = _screen()
    screen._reload_leads()
    assert screen.lead_table.rowCount() == len(screen._leads)
    for row in range(screen.lead_table.rowCount()):
        assert screen._lead_at(row) is screen._leads[row], \
            "row %d does not answer with the record it was built from" % row
        assert screen.lead_table.item(row, SO._COL_NAME).data(Qt.UserRole) is None, \
            "the row is still carrying its record through a QVariant"
    assert screen._lead_at(-1) == {} and screen._lead_at(9999) == {}, \
        "a row off either end has to read as nothing, not raise"


def test_an_audit_run_leaves_the_counts_a_full_recount_would():
    """The bookkeeping the brute-force rebuild used to do for free.

    Carrying the counts instead of recounting them is only worth anything if
    they come out the same, so this drives a run of audits through a *filtered*
    table — where each one changes whether its row is shown — and then checks
    every carried number against one worked out from scratch.
    """
    with _extra_leads(60) as screen:
        screen.status_filter.setCurrentIndex(
            [key for _label, key in SO._STATUS_FILTERS].index("audited"))
        _app().processEvents()
        before = screen._visible

        audited = 0
        for row in range(0, 60, 4):
            lead = screen._leads[row]
            if SO._text_of(lead.get("status")).strip() == "audited":
                continue
            screen._on_lead_audited(
                dict(lead, status="audited", opportunity_score=91,
                     audit_json='{"gaps": [{"title": "No online booking"}]}'))
            audited += 1
        assert audited, "the run audited nothing, so it proved nothing"

        table = screen.lead_table
        shown = sum(1 for row in range(table.rowCount())
                    if not table.isRowHidden(row))
        assert screen._visible == shown == before + audited, (
            "the shown count drifted: carried %d, actually %d, expected %d"
            % (screen._visible, shown, before + audited))

        fresh: dict = {}
        for lead in screen._leads:
            key = SO._text_of(lead.get("status")).strip() or "new"
            fresh[key] = fresh.get(key, 0) + 1
        assert {k: v for k, v in screen._buckets.items() if v} == fresh, (
            "the status buckets drifted: carried %s, actually %s"
            % (screen._buckets, fresh))
        assert screen._generic_count == \
            sum(1 for reason in screen._generic.values() if reason)

        line = screen.lead_counts.text()
        screen._apply_filters()
        assert screen.lead_counts.text() == line, (
            "the incremental line %r is not what the full pass writes (%r)"
            % (line, screen.lead_counts.text()))
        assert screen.audit_btn.text() == "Audit all (%d)" % shown


def test_the_send_controls_ask_the_store_for_the_tally_once():
    """It read `campaign_stats` and then had `_send_health` read it again.

    Once per second while a campaign runs, and once more on every stats signal,
    for a query that answers the same question both times.
    """
    screen = _screen()
    calls = []
    real = DB.campaign_stats

    def counted(conn, campaign_id):
        calls.append(campaign_id)
        return real(conn, campaign_id)

    DB.campaign_stats = counted
    try:
        screen._campaign_id = 1
        screen._refresh_send_controls()
    finally:
        DB.campaign_stats = real
        screen._campaign_id = 0
    assert len(calls) == 1, \
        "one refresh counted the campaign %d times" % len(calls)


def test_naming_a_target_agrees_however_it_was_counted():
    """`_named` takes a count and three records; `_names_of` takes the list.

    The button labels moved onto the first because materialising five thousand
    records to print "and 4,997 more" is the finding one level down. The two
    have to keep saying the same thing.
    """
    leads = [{"name": "Alpha Plumbing"}, {"name": "Mid Electric"},
             {"name": "Zeta Roofing"}, {"email": "four@example.com"}, {}]
    for size in range(len(leads) + 1):
        picked = leads[:size]
        assert SO._named(len(picked), picked) == SO._names_of(picked)
    assert SO._named(0, []) == "nobody"
    assert SO._named(1, leads[:1]) == "Alpha Plumbing"
    assert SO._named(500, leads[:3]) == \
        "Alpha Plumbing, Mid Electric, Zeta Roofing and 497 more"
    assert SO._named(5, [{}]) == "an unnamed lead and 4 more"


# ── P2: a theme change must not rebuild a screen nobody is looking at ─────────

def test_a_theme_change_waits_for_a_hidden_screen_to_be_shown():
    """913ms of this screen was charged to a click on another one.

    `MainWindow._repolish` asks every built screen to `restyle()`, and this one
    answered by rebuilding four tab pages and re-reading the store — for a
    window the user had walked away from, inside the click that changed the
    setting. The debt is recorded and paid by `showEvent`, which is the first
    moment paying it buys the user anything.
    """
    screen = _screen()
    app = _app()
    other = TH.theme("light" if _WEARING.name == "dark" else "dark")
    table = screen.lead_table
    screen.hide()
    app.processEvents()
    try:
        TH.apply(app, other)
        CO.use_theme(other)
        screen.restyle()
        assert screen._stale, "a hidden screen has to remember what it owes"
        assert screen.lead_table is table, \
            "a screen nobody is looking at rebuilt itself inside the click"

        screen.show()
        app.processEvents()
        assert not screen._stale, "the debt was recorded and never paid"
        assert screen.lead_table is not table, \
            "being shown did not rebuild the screen the theme change skipped"
        painted = screen.send_note.palette().color(
            QPalette.WindowText).name().upper()
        assert painted == other.color["text.tertiary"].upper(), (
            "the screen came back wearing %s, not the %s theme's %s"
            % (painted, other.name, other.color["text.tertiary"]))
    finally:
        TH.apply(app, _WEARING)
        CO.use_theme(_WEARING)
        screen.show()
        screen.restyle()
        screen.resize(QSize(*DEFAULT_SIZE))
        app.processEvents()


def test_a_theme_change_does_not_go_back_to_the_store_for_the_leads():
    """A palette has nothing to say about a lead.

    Re-reading them also threw away every Headline gap the screen had worked
    out — a JSON decode and a `core.templates` call per row — so the rebuild
    paid for the audit-derived half of the table twice.
    """
    screen = _screen()
    app = _app()
    screen._reload_leads()
    held = list(screen._leads)
    calls = []
    real = DB.list_leads

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    DB.list_leads = counted
    try:
        screen.restyle()
        app.processEvents()
    finally:
        DB.list_leads = real
    assert not calls, "a theme change went back to the store %d times" % len(calls)
    assert [lead.get("email") for lead in screen._leads] == \
        [lead.get("email") for lead in held], "the rebuild lost the leads"
    assert screen.lead_table.rowCount() == len(held), \
        "the rebuilt table does not hold the leads the screen was holding"
    assert screen._lead_at(0) is screen._leads[0], \
        "the rebuilt table lost the row-to-record mapping"


# ── P3: the table must not build a cell for a lead nobody is looking at ──────
# The first finding closed the O(N²) audit tick. What was left was the constant:
# every pass that rebuilt the table built a `QTableWidgetItem` per cell for
# every lead in the store — 35,000 of them at 5,000 leads, for the twenty rows
# an 800px window can show. These count items rather than milliseconds for the
# same reason the P1 tests count row visits: the claim is about what the work is
# proportional to, and a fast machine cannot tell 20 from 5,000 on a clock.


@contextlib.contextmanager
def _leads_tab_restored():
    """Put the leads tab back the way the rest of this file expects it.

    The screen is a module singleton, so a test that hides a column or saves a
    view is writing state every test after it will read — and the tests above
    leave it on the Sending tab at the window's minimum, where the lead table
    has no laid-out viewport to answer questions about.
    """
    screen = _screen()
    screen.resize(QSize(*DEFAULT_SIZE))
    screen._goto_tab(0)
    screen.layout().activate()
    _app().processEvents()
    try:
        yield screen
    finally:
        screen._views = []
        screen._view_name = ""
        screen._set_columns(range(len(SO._LEAD_COLUMNS)))
        screen.lead_search.setText("")
        screen.status_filter.setCurrentIndex(0)
        screen._sort = (SO._COL_SCORE, Qt.DescendingOrder)
        screen._save_view_state(now=True)
        screen._reload_leads()
        _app().processEvents()


def _built_rows(screen) -> int:
    """How many of the table's rows are actually made of cells."""
    table = screen.lead_table
    return sum(1 for row in range(table.rowCount())
               if table.item(row, 0) is not None)


def test_only_the_rows_on_screen_are_made_of_cells():
    """A row per lead so the scrollbar is honest; cells only where they show.

    The old fill built every cell of every row. At 5,000 leads that was 35,000
    items and 2,379ms of the 2,648ms pass inside `components._Table.add_row`,
    once per reload, once per header click, and once at the end of every audit
    run. Measured on the same store, before and after, back to back:
    `_fill_table` 677ms -> 29ms, a column-header sort 781ms -> 32ms.
    """
    with _extra_leads(400) as screen:
        table = screen.lead_table
        assert table.rowCount() == len(screen._leads) == 403, table.rowCount()
        built = _built_rows(screen)
        assert built < 120, (
            "%d of %d rows were built for a window that shows about twenty"
            % (built, table.rowCount()))
        assert built >= 1, "nothing was built at all"
        assert screen._painted, "the screen does not know what it built"
        assert built == len(screen._painted), (
            "the screen thinks it built %d rows and the table holds %d"
            % (len(screen._painted), built))


def test_a_bigger_list_does_not_build_a_bigger_table():
    """The whole of the second finding, as a number that must not move."""
    with _extra_leads(100) as screen:
        small = _built_rows(screen)
    with _extra_leads(600) as screen:
        large = _built_rows(screen)
    assert large <= small + 8, (
        "a 103-row table built %d rows and a 603-row one built %d — the work "
        "is still proportional to the list" % (small, large))


def test_a_filter_that_hides_almost_everything_builds_almost_nothing():
    """The band is a stretch of the model, not a list of rows to build.

    A search matching nothing leaves `rowAt(0)` with no visible row to answer
    with, so the walk that finds the bottom of the band runs to the end of the
    table. Painting that band whole would build the 35,000 items the virtual
    window exists to avoid — on the keystroke that matched nothing.

    The rows already built stay built; they are off screen and they cost
    nothing to leave alone. What must not happen is the other 367 joining them.
    """
    with _extra_leads(400) as screen:
        app = _app()
        was = _built_rows(screen)
        screen.lead_search.setText("nothing here matches this")
        app.processEvents()
        assert screen._visible == 0, screen._visible
        assert _built_rows(screen) <= was, (
            "a search that matched nothing built %d rows on top of the %d "
            "already there" % (_built_rows(screen) - was, was))

        screen.lead_search.setText("Bulk Business 0399")
        app.processEvents()
        assert screen._visible == 1, screen._visible
        assert _built_rows(screen) <= was + 2, (
            "one matching lead out of 403 brought %d more rows with it"
            % (_built_rows(screen) - was))


def test_scrolling_builds_the_row_it_scrolled_to():
    """A row the user cannot see has no cells, and gets them the moment it shows."""
    with _extra_leads(400) as screen:
        table, app = screen.lead_table, _app()
        last = table.rowCount() - 1
        assert table.item(last, 0) is None, \
            "the bottom of a 403-row table was built before anyone looked"
        # `scrollToBottom` and not `setValue(maximum())`: the view lays itself
        # out on a posted event, so a scrollbar asked for its range in the same
        # turn as the rows were counted still answers 0.
        table.scrollToBottom()
        app.processEvents()
        item = table.item(last, 0)
        assert item is not None, "scrolling to the end left an empty row"
        expected = SO._text_of(screen._lead_at(last).get("name")).strip() or "—"
        assert item.text() == expected, (
            "row %d reads %r and holds %r" % (last, item.text(), expected))
        assert _built_rows(screen) < 400, \
            "a scroll to the end kept every row it passed"


def test_a_selection_of_hundreds_is_counted_rather_than_listed():
    """`selectedIndexes()` is one object per cell, and there are seven per row.

    Ctrl+A over 5,000 leads handed the label refresh 35,000 `QModelIndex`
    objects to build a set of 5,000 numbers from, on the keystroke and again
    for every button label. The ranges know the answer: 196ms -> 8ms.
    """
    with _extra_leads(400) as screen:
        table, app = screen.lead_table, _app()
        table.selectAll()
        app.processEvents()
        assert screen._selected_count() == table.rowCount()
        assert screen.audit_btn.text() == "Audit selected (%d)" % table.rowCount()

        indexes = len(table.selectedIndexes())
        assert indexes == table.rowCount() * table.columnCount(), indexes
        assert screen._selected_count() * table.columnCount() == indexes, (
            "the count and the indexes disagree: %d rows, %d cells"
            % (screen._selected_count(), indexes))
        table.clearSelection()
        app.processEvents()


@contextlib.contextmanager
def _counting_personalisation():
    """Count the `core.templates` calls a reload makes. The expensive one."""
    calls = []
    real = SO._templates.personalisation

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    SO._templates.personalisation = counted
    try:
        yield calls
    finally:
        SO._templates.personalisation = real


def test_a_reload_keeps_what_the_record_did_not_change():
    """Suppressing one lead used to re-derive five thousand headline gaps.

    The three derived caches were emptied wholesale on every reload, so one row
    moving cost a JSON decode and a `core.templates.personalisation` call for
    every lead in the store — 76ms of the reload at 5,000, for a question none
    of them had a new answer to.
    """
    with _extra_leads(60) as screen:
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        conn.execute("UPDATE leads SET audit_json = ? WHERE source = 'bulk'",
                     ('{"gaps": [{"title": "No online booking"}]}',))
        conn.commit()
        screen._reload_leads()
        held = dict(screen._gaps)
        assert len(held) >= 60, "the reload derived nothing to keep"

        with _counting_personalisation() as calls:
            screen._reload_leads()
        assert not calls, (
            "an unchanged store asked core.templates %d times again"
            % len(calls))
        assert screen._gaps == held, "an unchanged store threw the answers away"

        moved = screen._leads[0]
        lead_id = SO._int_of(moved.get("id"))
        DB.upsert_lead(conn, {"email": moved.get("email"),
                              "audit_json": '{"gaps": [{"title": "No live chat"}]}'})
        with _counting_personalisation() as calls:
            screen._reload_leads()
        assert len(calls) <= 1, (
            "one changed lead cost %d personalisation calls" % len(calls))
        assert screen._gaps.get(lead_id) != held.get(lead_id), \
            "the lead whose crawl changed kept its old headline gap"
        assert len(screen._gaps) >= len(held) - 1, \
            "one changed lead emptied the whole cache again"


def test_a_settings_change_does_not_leave_a_stale_answer_behind():
    """The personalisation answer depends on the profile, not only on the lead.

    Keeping the caches across a reload is only safe if what they were derived
    from is compared in full — and half of that is the sender profile the
    `core.templates` call reads. A stamp that only watched the record would
    leave every Headline gap on screen answering for a company the user has
    since renamed.
    """
    with _extra_leads(60) as screen:
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        conn.execute("UPDATE leads SET audit_json = ? WHERE source = 'bulk'",
                     ('{"gaps": [{"title": "No online booking"}]}',))
        conn.commit()
        screen._reload_leads()
        assert screen._gaps, "nothing was derived to go stale"

        saved = screen.settings.get("sender_profile")
        try:
            screen.settings = dict(screen.settings,
                                   sender_profile={"company": "Somewhere Else"})
            with _counting_personalisation() as calls:
                screen._reload_leads()
            assert len(calls) >= 60, (
                "a changed sender profile re-asked core.templates %d times; "
                "every audited lead's answer depends on it" % len(calls))
            assert screen._rules_stamp == SO._rules_stamp(screen.settings), \
                "the screen did not notice the settings had moved"
        finally:
            screen.settings = dict(screen.settings, sender_profile=saved)
            screen._reload_leads()


# ── B1: which columns the table shows ────────────────────────────────────────

def test_a_hidden_column_leaves_no_gap_in_the_table():
    """Hidden in place is not the same as absent from the spec.

    `components._Table` shares the window out between the columns it was
    handed, so a column hidden after the fact keeps its share and leaves a band
    of unpainted table beside the last one. The table is rebuilt from the
    columns that are wanted instead.
    """
    with _leads_tab_restored() as screen:
        app = _app()
        wanted = (SO._COL_NAME, SO._COL_EMAIL, SO._COL_SCORE, SO._COL_GAP)
        before = {field: screen.lead_table.columnWidth(field)
                  for field in wanted}

        screen._set_columns(wanted)
        app.processEvents()
        table = screen.lead_table
        assert table.columnCount() == len(wanted), table.columnCount()
        assert [table.horizontalHeaderItem(i).text()
                for i in range(table.columnCount())] == \
            [SO._LEAD_COLUMNS[f].title for f in wanted]

        after = {field: table.columnWidth(column)
                 for column, field in enumerate(wanted)}
        narrower = {SO._LEAD_COLUMNS[f].title: (before[f], after[f])
                    for f in wanted if after[f] < before[f]}
        assert not narrower, (
            "switching three columns off left the rest no wider — the space "
            "went to the columns that are not painted: %s" % narrower)
        assert sum(after.values()) > sum(before.values()), (
            "the four remaining columns take %dpx, the same four took %dpx "
            "when there were seven" % (sum(after.values()), sum(before.values())))
        assert table.item(0, 2).data(SO._BADGE_ROLE) is not None, \
            "the score badge lost its value when the column moved"


def test_business_cannot_be_switched_off():
    """A table of nameless rows is a state no user should be able to reach."""
    with _leads_tab_restored() as screen:
        screen._set_columns({SO._COL_EMAIL})
        assert SO._COL_NAME in screen._fields, screen._fields
        assert screen.lead_table.columnCount() == 2, \
            "Business did not come back with the one column that was asked for"


def test_the_chosen_columns_outlive_the_screen():
    """A column switched off this morning is off again this afternoon."""
    with _leads_tab_restored() as screen:
        screen._set_columns({SO._COL_NAME, SO._COL_EMAIL, SO._COL_STATUS})
        with open(SO._views_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
        assert stored["columns"] == ["name", "email", "status"], stored["columns"]
        assert SO._fields_from_keys(stored["columns"], ()) == \
            (SO._COL_NAME, SO._COL_EMAIL, SO._COL_STATUS)


def test_a_sort_survives_the_column_it_sorts_on_being_hidden():
    """The screen sorts by a field; a column is only where a field is painted."""
    with _leads_tab_restored() as screen:
        screen._on_header_clicked(0)                      # Business, ascending
        assert screen._sort[0] == SO._COL_NAME
        ordered = [SO._text_of(lead.get("name")) for lead in screen._leads]

        screen._set_columns({SO._COL_NAME, SO._COL_EMAIL, SO._COL_SCORE})
        assert screen._sort[0] == SO._COL_NAME, screen._sort
        assert [SO._text_of(lead.get("name")) for lead in screen._leads] == ordered

        screen._set_columns({SO._COL_EMAIL, SO._COL_SCORE})
        assert [SO._text_of(lead.get("name")) for lead in screen._leads] == ordered, \
            "hiding the sorted column reordered the table"


# ── B2: a filter worth coming back to ────────────────────────────────────────

def test_a_saved_view_comes_back_whole():
    """A view is a filter, a sort and a set of columns under a name."""
    with _leads_tab_restored() as screen:
        app = _app()
        screen.lead_search.setText("city:toronto")
        screen.status_filter.setCurrentIndex(
            [key for _label, key in SO._STATUS_FILTERS].index("audited"))
        screen._on_header_clicked(0)
        screen._set_columns({SO._COL_NAME, SO._COL_EMAIL, SO._COL_GAP})
        app.processEvents()
        screen._store_view("Toronto audited")

        screen._clear_view()
        app.processEvents()
        assert screen.lead_search.text() == ""
        assert screen._wanted_status() == ""
        assert screen._view_name == ""

        screen._apply_view("Toronto audited")
        app.processEvents()
        assert screen.lead_search.text() == "city:toronto"
        assert screen._wanted_status() == "audited"
        assert screen._sort[0] == SO._COL_NAME
        assert screen.lead_table.columnCount() == 3
        assert screen._view_name == "Toronto audited"


def test_changing_a_filter_makes_it_an_unsaved_view():
    """A saved view is only worth anything if it stays what it was saved as."""
    with _leads_tab_restored() as screen:
        app = _app()
        screen._store_view("Everything")
        assert "Everything" in screen.view_btn.text(), screen.view_btn.text()

        screen.lead_search.setText("plumbing")
        app.processEvents()
        assert screen._view_name == "", \
            "typing in the filter box rewrote the saved view under the user"
        assert screen.view_btn.text() == "Views"
        assert [view["name"] for view in screen._views] == ["Everything"], \
            "the saved view itself was lost"


def test_a_view_is_stored_by_column_name_not_by_index():
    """A column added to the spec must not renumber a view saved last month."""
    with _leads_tab_restored() as screen:
        screen._set_columns({SO._COL_NAME, SO._COL_SCORE})
        screen._store_view("Just the scores")
        with open(SO._views_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
        view = stored["views"][0]
        assert view["columns"] == ["name", "score"], view
        assert view["sort"][0] in SO._FIELD_OF_KEY, view["sort"]
        assert not any(isinstance(value, int) for value in view["columns"])


def test_a_keystroke_does_not_reach_the_disk_and_a_decision_does():
    """A write-then-rename per character typed is 3.6ms of GUI-thread disk.

    The file still has to be right — what is put off is only the write, and
    only for the churn. Saving a view is a decision the user is owed the file
    for, so it goes down in the same call.
    """
    with _leads_tab_restored() as screen:
        app = _app()
        writes = []
        real = SO._write_views

        def counted(state):
            writes.append(dict(state))
            return real(state)

        SO._write_views = counted
        try:
            screen.lead_search.setText("roof")
            screen.lead_search.setText("roofing")
            app.processEvents()
            assert not writes, "two keystrokes cost %d writes" % len(writes)
            assert screen._save_timer.isActive(), \
                "the write was skipped rather than put off"

            screen._flush_view_state()
            assert len(writes) == 1, len(writes)
            assert writes[-1]["search"] == "roofing", writes[-1]

            screen._store_view("Roofers")
            assert len(writes) == 2, \
                "saving a view did not reach the disk in the same call"
            assert writes[-1]["current"] == "Roofers", writes[-1]
        finally:
            SO._write_views = real


def test_the_filter_the_app_closed_on_is_the_one_it_opens_on():
    """The lead filters survived a tab switch; a named view has to survive a night."""
    with _leads_tab_restored() as screen:
        app = _app()
        screen.lead_search.setText("category:roofing")
        screen.status_filter.setCurrentIndex(
            [key for _label, key in SO._STATUS_FILTERS].index("audited"))
        screen._set_columns({SO._COL_NAME, SO._COL_CITY})
        app.processEvents()

        second = SO.OutreachScreen()
        try:
            assert second.lead_search.text() == "category:roofing", \
                second.lead_search.text()
            assert second._wanted_status() == "audited"
            assert second._fields == (SO._COL_NAME, SO._COL_CITY), second._fields
            assert second.lead_table.columnCount() == 2
        finally:
            second._tick.stop()
            second.deleteLater()
            app.processEvents()


# ── B3: what a selection can be told to do ───────────────────────────────────

def test_every_bulk_action_reads_the_same_selection():
    """Four actions, one selection, and every label says how many rows it acts on."""
    with _leads_tab_restored() as screen:
        app = _app()
        table = screen.lead_table
        table.clearSelection()
        table.selectRow(0)
        table.selectionModel().select(
            table.model().index(1, 0),
            table.selectionModel().Select | table.selectionModel().Rows)
        app.processEvents()
        assert screen._selected_count() == 2, screen._selected_count()
        assert screen.audit_btn.text() == "Audit selected (2)"
        assert screen.suppress_btn.text() == "Suppress 2…"
        assert screen.remove_btn.text() == "Remove 2…"
        assert screen.export_btn.text() == "Export 2…"
        assert screen.copy_btn.text() == "Copy 2"
        for button in (screen.suppress_btn, screen.remove_btn,
                       screen.export_btn, screen.copy_btn):
            assert button.isEnabled() and button.toolTip(), button.text()

        table.clearSelection()
        app.processEvents()
        assert screen.remove_btn.text() == "Remove…"
        assert not screen.remove_btn.isEnabled()
        assert screen.remove_btn.toolTip(), \
            "a disabled bulk action with nothing to read"


def test_export_writes_the_rows_the_filter_shows_and_every_column():
    """The filter is the list; switching a column off is not a redaction.

    Exporting what is *shown* is the point — a filter narrowed to one lead is
    the list the user built. Exporting only the columns on screen would not be:
    hiding the Email column to read the table more easily is not an instruction
    to throw the addresses away on the way out of the app.
    """
    with _leads_tab_restored() as screen:
        app = _app()
        target = os.path.join(_TMP, "exported-leads.csv")
        screen.lead_search.setText("alpha")
        screen._set_columns({SO._COL_NAME, SO._COL_SCORE})
        app.processEvents()
        assert screen._visible == 1, screen._visible

        saved = SO.QFileDialog.getSaveFileName
        SO.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (target, ""))
        try:
            screen._on_export_clicked()
        finally:
            SO.QFileDialog.getSaveFileName = saved

        with open(target, encoding="utf-8-sig") as handle:
            lines = [line.rstrip("\n").rstrip("\r") for line in handle]
        assert len(lines) == 2, "the filter shows one lead and the CSV has %d rows" \
            % (len(lines) - 1)
        assert lines[0].startswith(
            ",".join(column.title for column in SO._LEAD_COLUMNS)), (
            "the two columns switched off were left out of the file: %r"
            % lines[0])
        assert lines[0].endswith("Phone,Website,Source"), lines[0]
        assert "Alpha Plumbing" in lines[1]
        assert "alpha@example.com" in lines[1], \
            "the hidden Email column was redacted out of the export"
        assert ",88," in lines[1], \
            "the score went out as a badge rather than as a number: %r" % lines[1]


def test_remove_forgets_the_selection_without_unsubscribing_it():
    """Suppress and Remove are not the same promise, and must not be confused.

    A suppressed address is kept precisely so it can never be mailed again.
    A removed lead is one that should not have been imported, and importing it
    again tomorrow has to work.
    """
    with _leads_tab_restored() as screen:
        app = _app()
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        DB.upsert_lead(conn, {"email": "doomed@example.com", "name": "Doomed Ltd",
                              "status": "new", "source": "test"})
        screen._reload_leads()
        before = len(screen._leads)
        lead = next(l for l in screen._leads
                    if l.get("email") == "doomed@example.com")

        agreed = CO.confirm
        CO.confirm = lambda *args, **kwargs: True
        try:
            screen._remove([lead])
        finally:
            CO.confirm = agreed
        app.processEvents()

        assert len(screen._leads) == before - 1, len(screen._leads)
        assert "doomed@example.com" not in \
            [l.get("email") for l in screen._leads]
        assert screen.lead_table.rowCount() == before - 1
        assert not DB.is_suppressed(conn, "doomed@example.com"), \
            "Remove quietly unsubscribed an address as well as forgetting it"
        assert DB.upsert_lead(conn, {"email": "doomed@example.com",
                                     "name": "Doomed Ltd", "source": "test"}), \
            "a removed lead cannot be imported again"
        conn.execute("DELETE FROM leads WHERE email = ?", ("doomed@example.com",))
        conn.commit()


def test_remove_asks_before_it_forgets_anything():
    with _leads_tab_restored() as screen:
        asked = {}
        agreed = CO.confirm
        CO.confirm = lambda *args, **kwargs: asked.update(kwargs) or False
        try:
            screen._remove([screen._leads[0]])
        finally:
            CO.confirm = agreed
        assert asked.get("danger") is True, asked
        assert "Forget" in asked.get("title", ""), asked
        assert "Suppress" in asked.get("body", ""), \
            "the dialog does not say which of the two this is"
        assert len(screen._leads) == 3, "a declined Remove removed something"


# ── B4: the filter box ───────────────────────────────────────────────────────

def test_two_words_in_two_fields_find_the_lead_that_says_both():
    """`toronto roofing` used to match nothing at all.

    The record says "Toronto" in the city and "Roofing contractor" in the
    category, the two are newline-joined so a needle cannot span them, and a
    single-substring match therefore had no way to ask for both. Every word is
    a needle now, and each has to land somewhere.
    """
    with _leads_tab_restored() as screen:
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        DB.upsert_lead(conn, {"email": "roofer@example.com", "name": "Peak Cover",
                              "city": "Toronto", "category": "Roofing contractor",
                              "status": "new", "source": "test"})
        DB.upsert_lead(conn, {"email": "plumber@example.com", "name": "Peak Pipes",
                              "city": "Toronto", "category": "Plumber",
                              "status": "new", "source": "test"})
        try:
            screen._reload_leads()
            assert _shown(screen, "toronto roofing") == {"Peak Cover"}
            assert _shown(screen, "toronto") == {"Peak Cover", "Peak Pipes"}
            assert _shown(screen, "peak plumber") == {"Peak Pipes"}
            assert _shown(screen, "toronto nowhere") == set()
        finally:
            conn.execute("DELETE FROM leads WHERE source = 'test' "
                         "AND email LIKE '%er@example.com'")
            conn.commit()


def test_a_field_in_front_of_the_colon_looks_in_that_field_alone():
    with _leads_tab_restored() as screen:
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        DB.upsert_lead(conn, {"email": "a@example.com", "name": "Toronto Roofing",
                              "city": "Hamilton", "category": "Roofing contractor",
                              "status": "new", "source": "test"})
        DB.upsert_lead(conn, {"email": "b@example.com", "name": "Peak Cover",
                              "city": "Toronto", "category": "Dentist",
                              "status": "new", "source": "test"})
        try:
            screen._reload_leads()
            assert _shown(screen, "toronto") == {"Toronto Roofing", "Peak Cover"}
            assert _shown(screen, "city:toronto") == {"Peak Cover"}
            assert _shown(screen, "business:toronto") == {"Toronto Roofing"}
            assert _shown(screen, "category:dentist") == {"Peak Cover"}
            # A colon that is not a field is text, not a bad query.
            assert _shown(screen, "nosuchfield:toronto") == set()
        finally:
            conn.execute("DELETE FROM leads WHERE email IN "
                         "('a@example.com', 'b@example.com')")
            conn.commit()


def test_a_quoted_phrase_stays_one_needle():
    """One *term* still has to sit inside one field, which is what quoting says."""
    with _leads_tab_restored() as screen:
        conn = DB.connect(os.path.join(_TMP, "outreach.db"))
        DB.upsert_lead(conn, {"email": "c@example.com", "name": "Roofing",
                              "city": "Barrie", "category": "Contractor",
                              "status": "new", "source": "test"})
        DB.upsert_lead(conn, {"email": "d@example.com", "name": "Peak Cover",
                              "city": "Barrie", "category": "Roofing contractor",
                              "status": "new", "source": "test"})
        try:
            screen._reload_leads()
            assert _shown(screen, '"roofing contractor"') == {"Peak Cover"}
            assert _shown(screen, "roofing contractor") == \
                {"Peak Cover", "Roofing"}
        finally:
            conn.execute("DELETE FROM leads WHERE email IN "
                         "('c@example.com', 'd@example.com')")
            conn.commit()


def test_the_query_parser_reads_what_the_user_typed():
    assert SO._parse_query("toronto") == [("", "toronto")]
    assert SO._parse_query("toronto roofing") == \
        [("", "toronto"), ("", "roofing")]
    assert SO._parse_query("city:Toronto") == [("city", "toronto")]
    assert SO._parse_query('city:"north york"') == [("city", "north york")]
    assert SO._parse_query('"roofing contractor"') == [("", "roofing contractor")]
    assert SO._parse_query("business:peak") == [("name", "peak")]
    # Not a field, so not a query — a time is a string like any other.
    assert SO._parse_query("9:30") == [("", "9:30")]
    assert SO._parse_query("   ") == []


# ── L3-A: which site is not reachable, and why ───────────────────────────

def test_a_crawl_that_failed_is_read_off_the_keys_the_audit_writes():
    """The crawl's own `unreachable_reason`, not a second opinion about `error`.

    `core.audit` classifies the failure and `core.enrich` writes the words it
    reads, so a third vocabulary here would be a third dialect for one fact.
    What this screen adds is the register a column needs — four words to scan
    down — and the sentence beside it comes back from the crawl unedited.
    """
    from core import audit as AU

    assert SO._site_failure(_audit(gap="No online booking"))[0] == ""
    assert SO._site_failure({})[0] == "", "nobody has crawled this one"

    words, detail, raw = SO._site_failure(_audit("HTTP 403", status=403))
    assert words == SO._SITE_WORDS["http_403"]
    assert detail == AU.unreachable_detail("http_403")
    assert raw == "HTTP 403", "the crawl's own line is kept whole"

    # Every code the crawl can produce has words of its own, and no two of the
    # ones an operator actually meets read the same.
    assert set(AU.UNREACHABLE_REASONS) <= set(SO._SITE_WORDS), (
        "the crawl grew a reason this table has no words for: %s"
        % sorted(set(AU.UNREACHABLE_REASONS) - set(SO._SITE_WORDS)))
    assert len(set(SO._SITE_WORDS.values())) >= len(SO._SITE_WORDS) - 2

    for error, code in (
        ("URLError: <urlopen error [Errno 11001] getaddrinfo failed>", "dns"),
        ("timeout: The read operation timed out", "timeout"),
        ("SSLCertVerificationError: certificate has expired", "tls"),
        ("ConnectionRefusedError: [WinError 10061] refused", "refused"),
        ("empty response", "empty"),
        ("no url", "no_url"),
        ("Something nobody has seen", "unreachable"),
    ):
        assert SO._site_failure(_audit(error))[0] == SO._SITE_WORDS[code], error

    # A page that answered 200 and still told the crawl nothing. There is no
    # error string to read here at all, which is why the reason is the key.
    for code in ("challenge", "parked", "js_only", "cookie_wall"):
        blob = _audit(reason=code)
        assert SO._site_failure(blob)[0] == SO._SITE_WORDS[code], code

    # A code this build has never heard of is spelled out rather than flattened
    # back into "unreachable": a new reason from the crawl is worth more on
    # screen than a guess is.
    made_up = SO._site_failure({"reachable": False,
                                "unreachable_reason": "geo_blocked"})
    assert made_up[0] == "geo blocked", made_up


def test_a_store_crawled_before_the_reason_existed_still_reads():
    """A blob with no `unreachable_reason` goes through the crawl's own reader.

    Not through a copy of it here. `core.audit.unreachable_reason` is the
    function that filled the key in the first place, so a lead crawled last
    month lands in the same bucket as one crawled today.
    """
    assert SO._site_failure({"error": "timeout: timed out"})[0] == \
        SO._SITE_WORDS["timeout"]
    assert SO._site_failure({"reachable": False, "status": 404})[0] == \
        SO._SITE_WORDS["http_404"]
    # Silence is not evidence. A hand-written blob that never mentions
    # `reachable` must not paint "unreachable" over every row in an old store.
    assert SO._site_failure({"gaps": [{"title": "No booking"}]})[0] == ""


def test_a_site_that_answered_is_not_reported_as_a_failure():
    """A crawl that read pages and still logged a note is not an outage.

    The reason is the key and the error line is not: a sub-page that 404ed on a
    site whose home page read perfectly well must not paint the row as
    unreachable, and it is the difference between a note about the crawl and a
    site nothing could be learned from.
    """
    ok = dict(_audit(gap="No online booking"), error="one sub-page 404ed")
    words, _detail, raw = SO._site_failure(ok)
    assert words == ""
    assert raw == "one sub-page 404ed", "the line is still kept"


def test_the_leads_table_says_which_site_failed_and_how():
    """Four unreachable leads, four different reasons, all on screen.

    Before this pass every one of them painted the same cell — "form letter —
    the site could not be reached" — so the table knew the answer to "which
    site is not reachable?" and would not give it.
    """
    screen = _screen()
    app = _app()
    conn = screen.conn
    cases = {
        "L3 Timeout": _audit("timeout: The read operation timed out"),
        "L3 Blocked": _audit("HTTP 403", status=403),
        "L3 NoDns": _audit("URLError: <urlopen error getaddrinfo failed>"),
        "L3 Fine": _audit(gap="No online booking"),
    }
    for name, blob in cases.items():
        lead_id = DB.upsert_lead(conn, {"email": "%s@l3.test" % name.split()[1].lower(),
                                        "name": name, "website": "https://l3.test",
                                        "status": "audited", "source": "test"})
        DB.set_lead_audit(conn, lead_id, blob, {})
    try:
        screen._reload_leads()
        app.processEvents()
        painted = {}
        for row in range(screen.lead_table.rowCount()):
            lead = screen._lead_at(row)
            if SO._text_of(lead.get("name")).startswith("L3 "):
                painted[lead["name"]] = screen._site_text(lead)[0]
        assert painted == {"L3 Timeout": SO._SITE_WORDS["timeout"],
                           "L3 Blocked": SO._SITE_WORDS["http_403"],
                           "L3 NoDns": SO._SITE_WORDS["dns"],
                           "L3 Fine": ""}, painted
        assert len(set(painted.values())) == 4, (
            "four different failures must not read as one: %s" % painted)

        # And the Headline gap column says it in prose rather than repeating
        # the one sentence it used to give all of them.
        gaps = {name: screen._gap_text(lead)[0]
                for row in range(screen.lead_table.rowCount())
                for lead in [screen._lead_at(row)]
                for name in [SO._text_of(lead.get("name"))]
                if name.startswith("L3 ")}
        from core import audit as AU
        assert gaps["L3 Timeout"] == ("form letter — "
                                      + AU.unreachable_detail("timeout")), gaps
        assert "could not be reached" not in gaps["L3 NoDns"], gaps
    finally:
        for name in cases:
            conn.execute("DELETE FROM leads WHERE name = ?", (name,))
        conn.commit()
        screen._reload_leads()
        app.processEvents()


def test_unreachable_is_one_click_and_survives_as_a_saved_view():
    screen = _screen()
    app = _app()
    conn = screen.conn
    lead_id = DB.upsert_lead(conn, {"email": "down@l3.test", "name": "L3 Down",
                                    "website": "https://down.l3.test",
                                    "status": "audited", "source": "test"})
    DB.set_lead_audit(conn, lead_id, _audit("timeout: timed out"), {})
    try:
        screen._reload_leads()
        app.processEvents()
        index = SO._STATUS_KEYS.index("~failed")
        assert SO._STATUS_FILTERS[index][0] == "Unreachable sites", (
            "the filter has to say what it selects")
        screen.status_filter.setCurrentIndex(index)
        app.processEvents()
        shown = {SO._text_of(screen._lead_at(row).get("name"))
                 for row in range(screen.lead_table.rowCount())
                 if not screen.lead_table.isRowHidden(row)}
        assert shown == {"L3 Down"}, shown

        # A view is a filter under a name, and the key it stores is the one
        # every view written before this pass already carries.
        view = screen._current_view("unreachable")
        assert view["status"] == "~failed"
        screen.status_filter.setCurrentIndex(0)
        app.processEvents()
        screen._set_view(view)
        app.processEvents()
        assert screen._wanted_status() == "~failed"
        assert "unreachable sites" in SO._view_sentence(view).lower()
    finally:
        screen.status_filter.setCurrentIndex(0)
        conn.execute("DELETE FROM leads WHERE name = 'L3 Down'")
        conn.commit()
        screen._reload_leads()
        app.processEvents()


def test_a_new_column_arrives_switched_on_for_a_file_that_predates_it():
    """A column nobody was offered is not a column anybody switched off.

    `lead_views.json` stores the columns that are *shown*, so a file listing
    the seven keys this screen used to have is indistinguishable from a user
    who turned the eighth off — unless the file also says which keys it could
    have named. Without `known`, adding Site would have hidden it for every
    user who has ever opened the Leads tab.
    """
    old_file = {"columns": list(SO._KNOWN_BEFORE)}
    fields = SO._fields_wanted(old_file, range(len(SO._LEAD_COLUMNS)))
    assert SO._COL_SITE in fields, "a field the file predates must arrive on"

    # A user of this build who really did switch it off keeps it off.
    chosen = [key for key in SO._FIELD_KEYS if key != "site"]
    kept = SO._fields_wanted({"columns": chosen, "known": list(SO._FIELD_KEYS)},
                             range(len(SO._LEAD_COLUMNS)))
    assert SO._COL_SITE not in kept
    assert SO._COL_NAME in kept, "Business is pinned whatever the file says"


def test_a_retry_that_changed_nothing_says_so():
    """The one outcome a retry must never report as success.

    A crawl that comes back with the same four failures leaves the table
    looking exactly as it did, so a screen that only says "Audit finished"
    hands the user four form letters and a reason to believe they are not.
    """
    screen = _screen()
    app = _app()
    conn = screen.conn
    ids = []
    for name, blob in (("L3 R1", _audit("timeout: timed out")),
                       ("L3 R2", _audit("timeout: timed out")),
                       ("L3 R3", _audit("HTTP 403", status=403))):
        lead_id = DB.upsert_lead(conn, {"email": "%s@l3.test" % name.replace(" ", ""),
                                        "name": name, "website": "https://l3.test",
                                        "status": "audited", "source": "test"})
        DB.set_lead_audit(conn, lead_id, blob, {})
        ids.append(lead_id)
    try:
        screen._reload_leads()
        app.processEvents()

        # Nothing moved.
        screen._audit_retry = True
        screen._audit_before = {lead_id: screen._site_text(
            next(l for l in screen._leads if l["id"] == lead_id))[0]
            for lead_id in ids}
        said = screen._audit_report()
        assert "nothing changed" in said.lower(), said
        assert "2 %s" % SO._SITE_WORDS["timeout"] in said, said
        assert "1 %s" % SO._SITE_WORDS["http_403"] in said, said

        # One of them answers this time.
        DB.set_lead_audit(conn, ids[0], _audit(gap="No online booking"), {})
        screen._reload_leads()
        screen._audit_retry = True
        screen._audit_before = {ids[0]: SO._SITE_WORDS["timeout"],
                                ids[1]: SO._SITE_WORDS["timeout"],
                                ids[2]: SO._SITE_WORDS["http_403"]}
        said = screen._audit_report()
        assert "1 answered this time" in said, said
        assert "2 still unreachable" in said, said

        # A plain run reports the same fact in its own words.
        screen._audit_retry = False
        screen._audit_before = {ids[1]: SO._SITE_WORDS["timeout"],
                                ids[2]: SO._SITE_WORDS["http_403"]}
        said = screen._audit_report()
        assert said.startswith("Audited 2 sites"), said
        assert "2 could not be read" in said, said
    finally:
        conn.execute("DELETE FROM leads WHERE name LIKE 'L3 R%'")
        conn.commit()
        screen._reload_leads()
        app.processEvents()


def test_the_campaign_card_counts_the_form_letters_before_prepare_is_pressed():
    screen = _screen()
    app = _app()
    conn = screen.conn
    for name, blob in (("L3 P1", _audit("timeout: timed out")),
                       ("L3 P2", _audit("timeout: timed out"))):
        lead_id = DB.upsert_lead(conn, {"email": "%s@l3.test" % name.replace(" ", ""),
                                        "name": name, "website": "https://l3.test",
                                        "status": "audited", "source": "test"})
        DB.set_lead_audit(conn, lead_id, blob, {})
    try:
        screen._reload_leads()
        screen._goto_tab(1)
        app.processEvents()
        said = screen.plan_targets.text()
        assert "have a site nothing could read" in said, said
        assert "form letter" in said, said
    finally:
        screen._goto_tab(0)
        conn.execute("DELETE FROM leads WHERE name LIKE 'L3 P%'")
        conn.commit()
        screen._reload_leads()
        app.processEvents()


# ── L3-B: the Sending tab must never look dead ───────────────────────────

class _FakeWorker:
    """A run, for a screen that is only being asked what it would say."""

    def __init__(self, dry: bool = True):
        self.dry_run = dry

    def isRunning(self):
        return True

    def stop(self):
        self.stopped = True


def _queued(screen, count: int = 2, when: float = 1.0) -> int:
    screen.conn.execute("DELETE FROM messages")
    screen.conn.execute("DELETE FROM sends")
    screen.conn.commit()
    campaign = DB.create_campaign(screen.conn, "l3-states", "gap_direct", {},
                                  screen.settings)
    for index in range(count):
        DB.queue_message(screen.conn, {
            "campaign_id": campaign, "lead_id": screen._leads[index]["id"],
            "step": 0, "subject": "s", "body_text": "b",
            "account_email": "rota@example.com", "scheduled_at": when})
    screen._campaign_id = campaign
    return campaign


@contextlib.contextmanager
def _sendable(screen):
    """One Gmail account, an open window, and the state put back afterwards."""
    saved = dict(screen.settings)
    screen.settings["smtp_accounts"] = [{"email": "rota@example.com",
                                         "app_password": "x" * 16,
                                         "enabled": True, "daily_cap": 20}]
    screen.settings["send_days"] = [0, 1, 2, 3, 4, 5, 6]
    screen.settings["send_start_hour"] = 0
    # 24, not 23: `_hours` reads the end as exclusive, so 23 closes the window
    # for the last hour of the day and a suite run at 23:10 saw every "the queue
    # is moving" assertion below report a hold instead.
    screen.settings["send_end_hour"] = 24
    screen.settings["dry_run"] = True
    screen._benched_at = 0.0
    try:
        yield
    finally:
        screen.settings = saved
        screen._sending = False
        screen._paused = False
        screen._stopping = False
        screen.send_worker = None
        screen._benched_at = 0.0
        screen.conn.execute("DELETE FROM messages")
        screen.conn.execute("DELETE FROM sends")
        screen.conn.execute("DELETE FROM events")
        screen.conn.commit()


def test_every_state_the_sending_tab_can_be_in_says_what_is_happening():
    """Eighteen states, driven one at a time, each read off the screen.

    The rule is the one the function's docstring states: a headline that
    describes movement, or a reason that names what is stopping it and when it
    changes. Three of these used to fail it outright — a run with no account
    configured said "Every account has hit today's cap", an account benched for
    the day by an AUTH failure was counted as one with room, and a campaign the
    run had stopped invited the user to press Start again.
    """
    screen = _screen()
    with _sendable(screen):
        screen._campaign_id = 0
        head, why = screen._send_health()
        assert head == "No campaign yet" and "Prepare one" in why

        campaign = _queued(screen)
        head, why = screen._send_health()
        assert "Not sending" in head and "Start sending" in why, (head, why)

        # Running, and every one of these is a moving-or-explained pair.
        screen._sending = True
        screen.send_worker = _FakeWorker()
        head, why = screen._send_health()
        assert head.startswith("Rehearsing"), head

        screen._paused = True
        assert screen._send_health()[0].startswith("Paused after")
        screen._paused = False

        screen._stopping = True
        head, why = screen._send_health()
        assert head.startswith("Stopping"), head
        assert "in flight" in why, why
        screen._stopping = False

        # A one-hour window that cannot be the hour this suite is running in.
        # It was hard-coded to 03:00–04:00, which is a closed window for
        # twenty-three hours of the day and an open one for the twenty-fourth:
        # run at 03:06 and this branch reported "Rehearsing", correctly, against
        # an assertion that the queue was held.
        shut = (time.localtime().tm_hour + 2) % 22
        screen.settings["send_start_hour"] = shut
        screen.settings["send_end_hour"] = shut + 1
        head, why = screen._send_health()
        assert head.startswith("Holding") and "restarts at" in why, (head, why)
        screen.settings["send_start_hour"] = 0
        screen.settings["send_end_hour"] = 24

        # No account at all, while running. This is the sentence that used to
        # be about a cap on a set with nothing in it.
        accounts = screen.settings["smtp_accounts"]
        screen.settings["smtp_accounts"] = []
        head, why = screen._send_health()
        assert head.startswith("Holding"), head
        assert "no Gmail account" in why, why
        assert "cap" not in why, "there is no account to have a cap"
        screen.settings["smtp_accounts"] = accounts

        # Benched for the day by the run itself. The worker writes the event
        # and then logs it at error level, and that line is what spends the
        # two-second memo on `_benched_today` — so the status line and the
        # Accounts card say "stopped for today" on the same tick the log does
        # rather than up to two seconds behind it.
        screen._benched_at = time.time()
        DB.log_event(screen.conn, "account_stopped",
                     "rota@example.com: AUTH: 535 bad credentials")
        screen._append_log("rota@example.com stopped for today — AUTH", "error")
        assert screen._benched_at == 0.0, (
            "an error line did not spend the benched-accounts memo")
        head, why = screen._send_health()
        assert head.startswith("Holding"), head
        assert "rota@example.com" in why and "refused" in why, why
        screen.conn.execute("DELETE FROM events")
        screen.conn.commit()
        screen._benched_at = 0.0

        # Waiting out the pacing gap: awake, free, and nothing due yet.
        screen.conn.execute("UPDATE messages SET scheduled_at = ?",
                            (time.time() + 300,))
        screen.conn.commit()
        head, why = screen._send_health()
        assert "next at" in head, head
        assert "gap between messages" in why, why
        screen.conn.execute("UPDATE messages SET scheduled_at = 1.0")
        screen.conn.commit()

        screen._sending = False
        screen.send_worker = None

        # Every account at its cap is not the same as no account.
        for _ in range(40):
            DB.record_send(screen.conn, "rota@example.com", time.time())
        head, why = screen._send_health()
        assert "cap" in head and "midnight" in why, (head, why)
        screen.conn.execute("DELETE FROM sends")
        screen.conn.commit()

        # The run stopped itself. Start would only stop again.
        DB.set_campaign_status(screen.conn, campaign, "stopped")
        head, why = screen._send_health()
        assert head.startswith("Stopped"), head
        assert "no account left" in why, why
        DB.set_campaign_status(screen.conn, campaign, "scheduled")

        # Queued past the horizon `_next_due_ts` looks over. The old branch
        # blamed the suppression list without ever counting it.
        screen.conn.execute("UPDATE messages SET scheduled_at = ?",
                            (time.time() + 400 * 86400,))
        screen.conn.commit()
        head, why = screen._send_health()
        assert head.startswith("Stalled"), head
        assert "suppressed" not in why, "nothing here is suppressed: %s" % why
        assert "due inside the next year" in why, why


def test_stop_keeps_saying_it_is_stopping():
    """The tick used to overwrite the one sentence Stop wrote.

    `_refresh_send_controls` runs once a second off `_on_tick`, so the label
    `_on_stop_clicked` set lived for under a second and the screen went back to
    reporting a run that was on its way down as one that was sending.
    """
    screen = _screen()
    app = _app()
    with _sendable(screen):
        _queued(screen)
        screen._sending = True
        screen.send_worker = _FakeWorker()
        screen._goto_tab(2)
        app.processEvents()

        screen._on_stop_clicked()
        assert "Stopping" in screen.send_status.text()
        assert not screen.stop_btn.isEnabled()
        assert not screen.pause_btn.isEnabled()

        screen._on_tick()
        app.processEvents()
        assert "Stopping" in screen.send_status.text(), (
            "one tick put back the sentence Stop replaced")
        assert not screen.stop_btn.isEnabled(), (
            "one tick re-armed Stop on a run that is already stopping")

        screen._on_send_done()
        assert not screen._stopping


def test_send_now_says_why_it_cannot_rather_than_waiting_to_be_pressed():
    """The one control on the tab nothing ever disabled.

    It sat lit over a campaign that did not exist, promising to ignore the
    sending window, and answered the press with a toast — so the screen's
    answer to "why did nothing happen" arrived only after the click and only
    for as long as a toast lives.
    """
    screen = _screen()
    app = _app()
    with _sendable(screen):
        screen._campaign_id = 0
        screen._refresh_send_controls()
        app.processEvents()
        assert not screen.send_now_btn.isEnabled()
        assert "no campaign" in screen.send_now_btn.toolTip().lower()

        _queued(screen, 2)
        screen._refresh_send_controls()
        app.processEvents()
        assert screen.send_now_btn.isEnabled()
        assert "Daily caps still apply" in screen.send_now_btn.toolTip()

        for _ in range(40):
            DB.record_send(screen.conn, "rota@example.com", time.time())
        screen._refresh_send_controls()
        app.processEvents()
        assert not screen.send_now_btn.isEnabled()
        assert "allowance for today" in screen.send_now_btn.toolTip()


def test_the_shell_line_reports_the_run_the_sending_tab_reports():
    """A rehearsal writes `rehearsed` and never `sent`.

    The bar is visible from every screen, and it counted `sent` — so a dry run
    reported "Sending — 0 of 500" for the whole of the one operation it was
    there to report on, which is the frozen number `_send_health` was fixed for
    one level down.
    """
    screen = _screen()
    with _sendable(screen):
        _queued(screen, 2)
        screen._sending = True
        screen.send_worker = _FakeWorker(dry=True)
        screen._context_line = "!"
        screen._publish_state()
        assert screen._context_line == screen._send_health()[0]
        assert screen._context_line.startswith("Rehearsing"), screen._context_line


def _shown(screen, needle) -> set:
    """The business names the filter box leaves on screen for `needle`."""
    screen.lead_search.setText(needle)
    _app().processEvents()
    table = screen.lead_table
    names = {SO._text_of(screen._lead_at(row).get("name"))
             for row in range(table.rowCount()) if not table.isRowHidden(row)}
    assert screen._visible == len(names), (
        "the count line says %d and %d rows are shown"
        % (screen._visible, len(names)))
    return names


# ── Lens 1: the Sending tab and Prepare campaign ────────────────────────────


def test_every_lead_column_in_the_spec_has_a_cell_to_put_in_it():
    """The crash, guarded by the shape that caused it rather than by the symptom.

    `_LEAD_COLUMNS` grew a Phone column and `_lead_cells` did not, so the last
    field indexed an eight-tuple and raised. The raise happened inside
    `_paint_window`, which Qt calls from an event filter, and PyQt5 answers an
    unhandled exception in a virtual with `qFatal` — the process died with
    0xC0000409 the moment the lead table held one row. `pytest tests/` stopped
    dead at test 62 of 933 and every UI file after it was unreachable.

    Asserted against the spec's own length, because the defect was two lists
    that had to be the same length and were not, and the symptom was a crash
    with no traceback anywhere.
    """
    screen = _screen()
    for lead in ({}, screen._leads[0], {"name": "X", "phone": "+1 416 555 0142"}):
        assert len(screen._lead_cells(lead)) == len(screen._fields)
    assert len(SO._LEAD_COLUMNS) == len(screen._fields), \
        "the screen is not showing every column, so this proves less than it looks"


def test_the_status_line_names_an_hourly_hold_the_log_is_reporting():
    """The handover's open finding: daily headroom, and the hour spent.

    `_send_health` covered "outside the window" and "every account capped" and
    not the hourly cap, so a run the loop had parked for 48 minutes read
    "Sending — 0 of N done" with no reason under it while the log beside it said
    "Holding for the hourly limit". Driven: five sends inside the trailing hour
    against a cap of five, with 15 of 20 a day still unspent.
    """
    screen = _screen()
    app = _app()
    with _sendable(screen):
        screen.settings["hourly_cap_per_account"] = 5
        _queued(screen, 2, when=time.time() - 60)
        now = time.time()
        for step in range(5):
            DB.record_send(screen.conn, "rota@example.com", now - 600 - step * 30)

        # The loop's own answer, so the two cannot be measured apart.
        held = SO._campaign.hourly_hold(screen.conn, ["rota@example.com"],
                                        screen.settings, now)
        assert held > 0, "the bench did not actually spend the hour"
        assert screen._account_room()[0] == ["rota@example.com"], \
            "the day is spent too, so this is not the hourly branch"

        screen._sending = True
        screen.send_worker = _FakeWorker(dry=False)
        screen._on_tick()
        app.processEvents()
        head, why = screen.send_status.text(), screen.send_reason.text()
        assert head.startswith("Holding"), head
        assert "hour" in why, why
        # `isHidden` rather than `isVisible`: this page is not the one on screen
        # in a suite that never opened it, and what is being asserted is that
        # `_refresh_send_controls` did not hide the reason itself.
        assert not screen.send_reason.isHidden(), "the reason was written and not shown"
        # And the clock three lines above it, which used to say "Sending now"
        # over the same queue.
        clock = screen.next_send_label.text()
        assert clock.startswith("Held"), clock
        assert "Sending now" not in clock


def test_a_rehearsal_that_spends_the_day_is_reported_as_spending_it():
    """The same lie as the hourly hold, one cap up and only in a dry run.

    `OutreachWorker._daily_room` counts the run's own rehearsals against the
    day — pacing a dry run realistically is the point of it — and none of them
    reach `sends`, so this screen could not see them. Driven with five
    rehearsed against a cap of five: the loop's `_daily_room` was 0 and the
    screen read "Rehearsing — 0 of 30 built" with no reason and "Sending now".
    """
    screen = _screen()
    app = _app()
    with _sendable(screen):
        screen.settings["smtp_accounts"][0]["daily_cap"] = 5
        _queued(screen, 2, when=time.time() - 60)
        now = time.time()
        worker = _FakeWorker(dry=True)
        worker._rehearsed = {"rota@example.com": [now - 60 * k for k in range(5)]}
        screen._sending = True
        screen.send_worker = worker

        assert screen._account_room() == ([], ["rota@example.com"], []), \
            screen._account_room()
        screen._on_tick()
        app.processEvents()
        assert screen.send_status.text().startswith("Holding"), \
            screen.send_status.text()
        assert "cap" in screen.send_reason.text()
        # And the clock names midnight rather than "Sending now": a spent day
        # does not free when the window next opens, it frees when the day does.
        clock = screen.next_send_label.text()
        assert clock.startswith("Held"), clock
        assert screen._day_frees(now) > now


def test_one_repaint_asks_the_store_each_question_once():
    """The clock and the status line are painted from one reading, not two.

    Both used to ask independently — the due row, the campaign row and
    `sent_today` per account, twice each per tick — and two readings of a store
    a send loop is writing to can disagree. The count is the cheap half of the
    contract; the guarantee is that they are the same numbers.
    """
    screen = _screen()
    with _sendable(screen):
        _queued(screen, 2, when=time.time() + 600)
        asked = []
        screen.conn.set_trace_callback(asked.append)
        try:
            screen._on_tick()
        finally:
            screen.conn.set_trace_callback(None)
        for sql in set(asked):
            assert asked.count(sql) == 1, (
                "one tick ran this %d times:\n%s" % (asked.count(sql), sql))

        # And the memo is not a cache: it does not outlive the repaint.
        assert screen._reading is None


def test_an_arriving_log_line_leaves_the_reader_where_they_were():
    """The console inserts at the top, so a reader has to be pushed down by one.

    By *one line*, in the units the scrollbar counts in. It used to add the row
    height in pixels to a bar in `ScrollPerItem` mode, which moved the reader
    nineteen lines for every line that arrived: parked at "line 107" of 200, ten
    new lines took them to "line 014" with the bar pinned to its maximum.
    """
    screen = _screen()
    app = _app()
    screen._goto_tab(2)
    screen._clear_log()
    for index in range(200):
        screen._append_log("line %03d" % index, "info", 0)
    app.processEvents()

    listing = screen.log_list
    bar = listing.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    app.processEvents()

    def top():
        item = listing.item(listing.indexAt(listing.viewport().rect().topLeft()).row())
        return item.text() if item is not None else ""

    was = top()
    assert was, "nothing is on screen to be scrolled away from"
    for index in range(10):
        screen._append_log("NEW %02d" % index, "info", 0)
    app.processEvents()
    assert top() == was, "the reader was moved from %r to %r" % (was, top())

    # Unscrolled, the newest line is still the one on screen.
    bar.setValue(0)
    app.processEvents()
    screen._append_log("NEWEST", "info", 0)
    app.processEvents()
    assert "NEWEST" in top()
    screen._clear_log()


def test_a_log_line_costs_the_same_into_a_full_console_as_an_empty_one():
    """`append` used to redraw every line for each line that arrived.

    A send loop logs two lines a message, so the cost of watching a run grew
    with how long you had watched it: 0.12ms into an empty console and 5.97ms
    into a full one, measured on this tab. The ratio is asserted rather than a
    millisecond count, because a wall-clock figure on a busy machine is noise.
    """
    screen = _screen()
    screen._clear_log()

    def cost() -> float:
        best = None
        for _ in range(20):
            start = time.perf_counter()
            screen._append_log("Business 0001 — first touch", "done", 1)
            taken = time.perf_counter() - start
            best = taken if best is None else min(best, taken)
        return best

    empty = cost()
    while len(screen._log_lines) < SO._LOG_LIMIT:
        screen._append_log("filler", "info", 0)
    full = cost()
    assert len(screen._log_lines) == SO._LOG_LIMIT, "the console stopped trimming"
    assert full < empty * 8, (
        "a line into a full console costs %.3fms against %.3fms into an empty "
        "one — the redraw is back" % (full * 1000, empty * 1000))
    screen._clear_log()


def test_the_accounts_card_is_repainted_rather_than_rebuilt():
    """It runs once per message a run sends, so it may not build widgets.

    Rebuilding cost 4.51ms a call and it was called twice a message, which is
    most of why a burst of send signals froze the window for 624ms at a stretch.
    The widgets are the contract: same objects, new numbers.
    """
    screen = _screen()
    app = _app()
    with _sendable(screen):
        screen._refresh_accounts()
        app.processEvents()
        cards = dict(screen._account_cards)
        assert cards, "no account card was built to keep"

        DB.record_send(screen.conn, "rota@example.com", time.time())
        screen._benched_at = 0.0
        screen._refresh_accounts()
        app.processEvents()
        assert screen._account_cards == cards, \
            "the card was rebuilt for a number that changed"
        counter, bar = cards["rota@example.com"]
        assert "1 / " in counter.text(), counter.text()
        assert bar.value() == 1, bar.value()

        # A different set of addresses is the one thing that does rebuild it.
        screen.settings["smtp_accounts"] = [
            {"email": "other@example.com", "app_password": "x" * 16,
             "enabled": True, "daily_cap": 20}]
        screen._refresh_accounts()
        app.processEvents()
        assert set(screen._account_cards) == {"other@example.com"}


def test_prepare_says_what_is_running_instead_of_promising_to_queue_itself():
    """The toast used to promise something nothing in the screen does.

    "A crawl is still running — this starts the moment it finishes" covered all
    three reasons Prepare can refuse, and no press is ever remembered. Driven
    with the Leads tab's audit running, the button said that and then never
    prepared anything.
    """
    screen = _screen()
    assert screen._prepare_blocked() == ""

    screen._auditing = True
    try:
        blocked = screen._prepare_blocked()
    finally:
        screen._auditing = False
    assert "Leads tab" in blocked, blocked
    assert "press Prepare again" in blocked, blocked
    assert "the moment it finishes" not in blocked, blocked

    screen._planning = True
    try:
        assert "already being prepared" in screen._prepare_blocked()
    finally:
        screen._planning = False


def test_a_refused_press_does_not_hang_the_window_first():
    """`_retire` waited two seconds to report that it had done nothing.

    Measured: 2,007ms of frozen GUI thread and then a toast. Both callers refuse
    and say so, so the wait bought nothing; what is left is the millisecond a
    thread whose `run` has returned needs to finish letting go.
    """
    from PyQt5.QtCore import QThread

    class _Busy(QThread):
        def run(self):
            self.msleep(1500)

    screen = _screen()
    worker = _Busy()
    worker.start()
    try:
        start = time.perf_counter()
        assert screen._retire(worker) is False
        taken = (time.perf_counter() - start) * 1000.0
    finally:
        worker.wait(5000)
    assert taken < 500, "a refused press held the GUI thread for %.0fms" % taken
    assert screen._retire(None) is True
    assert screen._retire(worker) is True, "a finished worker is still refused"


def test_the_plan_bar_only_ever_moves_forwards():
    """Three passes, one bar. It used to fill and reset once per pass.

    Each pass reported its own index against the whole lead count, so a 300-lead
    campaign drove the bar to the end, back to 1, to the end, back to 1, and to
    the end — which reads as a run that crashed and started over, twice.
    """
    from core import campaign as CA

    seen = []
    conn = DB.connect(os.path.join(_TMP, "outreach.db"))
    campaign = DB.create_campaign(conn, "meter", "gap_direct", {}, {})
    settings = {"smtp_accounts": [{"email": "rota@example.com",
                                  "app_password": "x" * 16, "enabled": True,
                                  "daily_cap": 50}],
                "send_days": [0, 1, 2, 3, 4, 5, 6], "send_start_hour": 0,
                "send_end_hour": 24, "audit_enabled": False,
                "followup_enabled": False,
                "sender_profile": {"name": "Sam", "company": "Auto Army",
                                   "email": "rota@example.com",
                                   "sign_off": "Sam",
                                   "postal_address": "1 King St W"}}
    leads = [dict(lead) for lead in DB.list_leads(conn, limit=50)]
    was = {SO._text_of(lead.get("email")): SO._text_of(lead.get("status"))
           for lead in leads}
    for lead in leads:
        lead["audit_json"] = json.dumps(_audit(gap="No HTTPS"))
    try:
        CA.plan_campaign(conn, campaign_id=campaign, leads=leads,
                         template_id="gap_direct",
                         profile=settings["sender_profile"], settings=settings,
                         ai=None, progress=lambda d, t, m: seen.append((d, t)))
    finally:
        # This one plans against the module's shared store rather than a
        # throwaway, because the bar's scale is a property of a real pass over
        # real leads. What it may not do is leave those leads marked 'queued'
        # for whatever runs next.
        DB.delete_campaign_messages(conn, campaign)
        DB.set_campaign_status(conn, campaign, "failed")
        for email, status in was.items():
            DB.upsert_lead(conn, {"email": email, "status": status})

    assert seen, "the plan reported no progress at all"
    assert len({total for _done, total in seen}) == 1, \
        "the bar was rescaled mid-run: %s" % sorted({t for _d, t in seen})
    for (was, _t1), (now, _t2) in zip(seen, seen[1:]):
        assert now >= was, "the bar went from %d back to %d" % (was, now)
    assert seen[-1][0] == seen[-1][1], \
        "the bar stopped at %d of %d" % seen[-1]


def test_close_all_at_exit():
    """Not a test — Windows will not delete the temp dir with the db open."""
    DB.close_all()
