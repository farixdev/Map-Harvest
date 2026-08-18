"""Offline tests for ui.screen_outreach and the global sheet in ui.app.

These are the screen's appearance and shutdown contracts, so most of them
assert against pixels rather than against the code that produced them: a QSS
rule can be silently beaten by a more specific one, and the only witness to
that is what the widget actually paints.

Qt runs on the offscreen platform, which has no font database. Every assertion
here is therefore written to be independent of text metrics — colours are
sampled, and widths are compared against each widget's own `sizeHint()` rather
than against fixed pixel counts.

`SETTINGS_DIR` is redirected into a temp directory before the screen is built,
so constructing it can never read or write a developer's real ~/.mapharvest.
"""
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
from ui import screen_outreach as SO  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="mapharvest-outreach-ui-")
_APP = None
_SCREEN = None


def _app() -> QApplication:
    """The one QApplication for this module, styled exactly as `ui.app.run`."""
    global _APP
    if _APP is None:
        ST.SETTINGS_DIR = _TMP
        ST.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyle("Fusion")
        _APP.setStyleSheet(APP.QSS)
    return _APP


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
        _SCREEN.resize(QSize(1080, 760))
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
    counts.pop("#000000", None)
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
    explicit transparent rule each label paints an opaque #1C1C1E rectangle on
    the #2C2C2E card. Sampled from the card's own render at the label's corner:
    a transparent label grabbed on its own yields an unpainted buffer, so the
    parent is the only honest witness.
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
    assert "QLabel {\n    background: transparent;\n}" in APP.QSS
    assert "QLabel#toast" in APP.QSS and "background-color: #2C2C2E" in APP.QSS


# ── D20: a disabled Stop must look disabled ──────────────────────────────────

def test_disabled_danger_button_dims():
    """#danger is an id selector, so it used to beat QPushButton:disabled.

    Pause is the reference: it is the outlined button beside Stop, it dims
    correctly, and the two are enabled and disabled together.
    """
    screen = _screen()
    app = _app()
    screen._goto_tab(2)
    app.processEvents()

    screen.stop_btn.setEnabled(True)
    app.processEvents()
    assert _accent(screen.stop_btn).startswith("#FF6"), \
        "an enabled Stop should still be red"

    screen.stop_btn.setEnabled(False)
    screen.pause_btn.setEnabled(False)
    app.processEvents()
    dead = _colours(screen.stop_btn)
    assert not any(c.startswith("#FF6") for c in dead), \
        "a disabled Stop still paints its live red: %s" % sorted(dead)
    assert _accent(screen.stop_btn) == _accent(screen.pause_btn), \
        "Stop and Pause must dim to the same thing when both are disabled"


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


def test_unaudited_leads_sort_below_scored_ones():
    """An em dash compares greater than a digit as text; the score must not."""
    screen = _screen()
    conn = DB.connect(os.path.join(_TMP, "outreach.db"))
    DB.upsert_lead(conn, {"email": "fresh@example.com", "name": "Fresh Lead",
                          "opportunity_score": 0, "status": "new", "source": "test"})
    try:
        screen._reload_leads()
        table = screen.lead_table
        scores = [table.item(row, SO._COL_SCORE).text()
                  for row in range(table.rowCount())]
        assert scores == ["88", "55", "30", "—"]
    finally:
        conn.execute("DELETE FROM leads WHERE email = ?", ("fresh@example.com",))
        conn.commit()
        screen._reload_leads()


# ── D19: the Campaign tab's primary actions must fit ─────────────────────────

def test_campaign_buttons_are_never_clipped():
    """Both at the default window size and at the window's minimum.

    The 340px left column could not fit 'Prepare campaign' and 'Open Sending'
    side by side, so the primary button was cut mid-glyph with no ellipsis.
    """
    screen = _screen()
    app = _app()
    for width, height in ((1080, 760), (880, 620)):
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
    screen.resize(QSize(1080, 760))


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
    screen = _screen()
    screen._on_plan_ready({"queued": 4, "days": 1, "cancelled": True})
    assert "Stopped before" in screen.plan_summary.text()
    screen._on_plan_ready({"queued": 4, "days": 1})
    assert "Stopped before" not in screen.plan_summary.text()


# ── R14: a container inside a card must not punch through it ─────────────────

def test_a_plain_container_paints_nothing_on_a_card():
    """The rule itself, built from scratch rather than found on a screen.

    A QLayout needs a widget to live on, so cards end up holding bare QWidgets
    whose only job is to hold a row of children. The sheet's `QWidget` rule
    matches every one of them and gives it the page's own #1C1C1E, which lands
    on top of the card fill — the labels inside are transparent and therefore
    innocent, so sampling them alone finds nothing. Asserted here so a card that
    nests a container tomorrow is covered without anyone remembering to check.
    """
    app = _app()
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 14, 16, 14)
    holder = QWidget()
    inner = QVBoxLayout(holder)
    inner.setContentsMargins(0, 0, 0, 0)
    label = QLabel("sam@example.com")
    label.setObjectName("muted")
    inner.addWidget(label)
    box.addWidget(holder)
    frame.resize(QSize(240, 80))
    frame.show()
    app.processEvents()

    image = frame.grab().toImage()
    counts = _histogram(image, image.rect())
    assert counts.get("#1C1C1E", 0) == 0, (
        "a container painted %d px of page colour onto the card"
        % counts["#1C1C1E"])
    assert image.pixelColor(image.width() // 2, 3).name().upper() == "#2C2C2E"
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
                    assert counts.get("#1C1C1E", 0) == 0, (
                        "%d of %d px behind %r are page colour, not the %s card"
                        % (counts["#1C1C1E"], sum(counts.values()),
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
        for width, height in ((1080, 760), (880, 620)):
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
        screen.resize(QSize(1080, 760))
        screen._clear_log()
        app.processEvents()


# ── R16: hint text has to be readable on both of its grounds ─────────────────

def test_hint_text_clears_wcag_aa_on_the_page_and_on_a_card():
    """#hint sentences sit on #1C1C1E on some screens and #2C2C2E on cards.

    Read back off a real label rather than out of the sheet, because a more
    specific rule could always be beating the one that names the colour. The
    card is the harder ground of the two, and it stopped being hidden behind the
    label's own fill once labels went transparent.
    """
    screen = _screen()
    app = _app()
    screen._goto_tab(2)
    app.processEvents()
    hint = screen.send_note.palette().color(QPalette.WindowText).name().upper()
    for ground in ("#1C1C1E", "#2C2C2E"):
        assert _contrast(hint, ground) >= 4.5, (
            "%s on %s is %.2f:1, under WCAG AA's 4.5:1"
            % (hint, ground, _contrast(hint, ground)))
    body = "#E5E5E7"
    assert _contrast(body, "#2C2C2E") > _contrast(hint, "#2C2C2E"), \
        "a hint must stay quieter than the body text beside it"


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


def test_close_all_at_exit():
    """Not a test — Windows will not delete the temp dir with the db open."""
    DB.close_all()
