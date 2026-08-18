import signal
import sys
import traceback

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPalette, QPen, QPolygonF,
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QProxyStyle, QStackedWidget, QStyle,
    QStyleFactory,
)

from ui.screen_input import InputScreen
from ui.screen_outreach import OutreachScreen
from ui.screen_results import ResultsScreen
from ui.screen_settings import SettingsScreen

QSS = """
QWidget {
    background-color: #1C1C1E;
    color: #E5E5E7;
    font-family: 'DM Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #1C1C1E;
}

/* The QWidget rule above matches QLabel as well, so without this every label
   paints an opaque #1C1C1E rectangle over whatever it sits on — most visibly
   the #2C2C2E cards, where each caption showed up as a darker bar. Labels that
   do want a fill (#toast, #warning) set it under their own id selector, which
   is more specific and still wins. */
QLabel {
    background: transparent;
}

/* Same rule, one level up. A layout needs a widget to live on, so half the
   containers on these screens are bare QWidgets, and the QWidget rule hands
   each of them the page's own colour — which is how the Accounts card ended up
   with a #1C1C1E hole punched through its fill wherever a per-account row sat.
   The leading dot matches instances of QWidget itself and not of its
   subclasses, so scaffolding paints nothing and everything that is really a
   surface — the screens, the dialogs, anything carrying an id — is untouched. */
.QWidget {
    background: transparent;
}

QLineEdit, QTextEdit {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 8px 12px;
    color: #E5E5E7;
    font-size: 13px;
    selection-background-color: #636366;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #636366;
    outline: none;
}

QPushButton {
    background-color: #636366;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 0 16px;
    height: 36px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #78787A;
}

QPushButton:disabled {
    background-color: #3A3A3C;
    color: #636366;
}

QPushButton#outlined {
    background-color: transparent;
    color: #E5E5E7;
    border: 1px solid #48484A;
}

QPushButton#outlined:hover {
    background-color: #3A3A3C;
}

QPushButton#outlined:disabled {
    color: #636366;
    border-color: #3A3A3C;
}

QPushButton#danger {
    background-color: transparent;
    color: #FF6B6B;
    border: 1px solid rgba(255, 107, 107, 0.35);
}

QPushButton#live {
    background-color: #B3261E;
    color: #FFFFFF;
    border: 1px solid #D4483F;
    font-weight: 600;
}

QPushButton#live:hover {
    background-color: #C62828;
}

QPushButton#rehearsal {
    background-color: transparent;
    color: #A0A0A6;
    border: 1px dashed #5A5A5E;
    font-weight: 500;
}

QPushButton#rehearsal:hover {
    background-color: #2C2C2E;
    color: #E5E5E7;
}

QPushButton#danger:hover {
    background-color: rgba(255, 107, 107, 0.10);
}

/* Without this the id selector beats QPushButton:disabled and a disabled Stop
   stays as bright as a live one. Same dimming as #outlined:disabled. */
QPushButton#danger:disabled {
    background-color: transparent;
    color: #636366;
    border-color: #3A3A3C;
}

/* No `::indicator` rule here, and that is deliberate — see `TickStyle`. A
   stylesheet indicator can only carry a fill and a border, and a tick has to
   come from `image: url(...)`, which Qt resolves through QPixmap and therefore
   only from a real file or a compiled .qrc. Neither survives a one-file
   PyInstaller build cleanly, so the whole indicator is painted in code. The
   moment any `QCheckBox::indicator` rule reappears here it wins over the style
   and the tick disappears again. */
QCheckBox {
    spacing: 8px;
    color: #E5E5E7;
    font-size: 13px;
}

QTableWidget {
    background-color: #242426;
    alternate-background-color: #2A2A2C;
    border: 1px solid #3A3A3C;
    border-radius: 10px;
    gridline-color: #3A3A3C;
    font-size: 12px;
    color: #E5E5E7;
}

QTableWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid #3A3A3C;
}

QTableWidget::item:selected {
    background-color: #3A3A3C;
    color: #FFFFFF;
}

QHeaderView::section {
    background-color: #2C2C2E;
    color: #AEAEB2;
    font-size: 11px;
    font-weight: 600;
    padding: 8px 10px;
    height: 34px;
    border: none;
    border-bottom: 1px solid #3A3A3C;
    border-right: 1px solid #3A3A3C;
}

QScrollBar:vertical {
    background: #1C1C1E;
    width: 10px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #48484A;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    height: 10px;
    background: #1C1C1E;
}

QScrollBar::handle:horizontal {
    background: #48484A;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QListWidget {
    background-color: #242426;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 6px 4px;
    font-size: 12px;
    color: #AEAEB2;
}

QListWidget::item {
    padding: 4px 8px;
    border: none;
    background: transparent;
}

QProgressBar {
    background-color: #3A3A3C;
    border: none;
    border-radius: 1px;
    height: 2px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: #8E8E93;
    border-radius: 1px;
}

/* One grey for the whole secondary tier — section labels, captions, counts,
   unselected tabs, table headers, the reveal toggle. It has to clear WCAG AA
   on the #2C2C2E cards as well as on the #1C1C1E page, and the card is the
   tighter of the two: #AEAEB2 measures 6.30:1 there and 7.69:1 on the page.
   It sits a step under body text (#E5E5E7, 11.08:1 on a card) and a step over
   #hint, so the three tiers stay tellable apart on either ground. */
QLabel#section_label {
    color: #AEAEB2;
    font-size: 11px;
    font-weight: 500;
}

QPushButton#tab {
    background-color: transparent;
    color: #AEAEB2;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    height: 28px;
    font-size: 12px;
    font-weight: 500;
}

QPushButton#tab:hover {
    color: #E5E5E7;
    background-color: #2C2C2E;
}

QPushButton#tab:checked {
    color: #E5E5E7;
    background-color: #3A3A3C;
}

/* Hints sit on two grounds — the #1C1C1E page and the #2C2C2E cards — and
   since labels stopped painting their own ground the card is the darker of the
   two. #949499 is the dimmest grey that still clears WCAG AA on both (4.62:1 on
   a card, 5.64:1 on the page); the old #636366 measured 2.33:1 on a card. */
QLabel#hint {
    color: #949499;
    font-size: 12px;
    line-height: 1.4;
}

QLabel#app_name {
    color: #E5E5E7;
    font-size: 15px;
    font-weight: 600;
}

QLabel#count_label {
    color: #E5E5E7;
    font-size: 15px;
    font-weight: 600;
}

QLabel#count_sub {
    color: #AEAEB2;
    font-size: 13px;
    font-weight: 400;
    padding-top: 1px;
}

QLabel#muted {
    color: #AEAEB2;
    font-size: 12px;
}

QLabel#status_text {
    color: #AEAEB2;
    font-size: 12px;
    font-weight: 400;
}

QTableWidget#results_table {
    border-radius: 8px;
}

QWidget#results_header {
    background-color: transparent;
    border-bottom: 1px solid #3A3A3C;
}

QFrame#card {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 12px;
}

QScrollArea {
    border: none;
    background: #1C1C1E;
}

QScrollArea > QWidget > QWidget {
    background: #1C1C1E;
}

QDialog {
    background-color: #1C1C1E;
}

QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background: #3A3A3C;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #E5E5E7;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #FFFFFF;
}

QSlider::sub-page:horizontal {
    background: #636366;
    border-radius: 2px;
}

QSpinBox#spin {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 6px;
    padding: 4px 8px;
    color: #E5E5E7;
}

QSpinBox#spin::up-button, QSpinBox#spin::down-button {
    width: 16px;
    border: none;
    background: transparent;
}

QListWidget#saved_list {
    background-color: #242426;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 4px;
    font-size: 12px;
    color: #AEAEB2;
}

QListWidget#saved_list::item {
    padding: 6px 8px;
    border-radius: 4px;
}

QListWidget#saved_list::item:selected {
    background-color: #3A3A3C;
    color: #E5E5E7;
}

QListWidget#saved_list::item:hover {
    background-color: #2C2C2E;
}

QLabel#toast {
    background-color: #2C2C2E;
    border: 1px solid #48484A;
    border-radius: 8px;
    color: #E5E5E7;
    font-size: 12px;
    padding: 12px 14px;
}

/* Black on the green rather than white. 14px/600 is not WCAG large text, so
   4.5:1 applies, and white manages 3.18:1 on #22A559. Darkening the fill to
   meet white instead is not open: the lightest green white clears is about
   #1C8749, which is already down to 3.06:1 against the #2C2C2E cards these
   buttons sit on, and a fill that close to its card stops reading as a button
   — with nothing left underneath for a :pressed that still looks pressed.
   Black clears every state as the greens stand: 6.59:1 here, 8.12:1 on
   :hover, 5.09:1 on :pressed. */
QPushButton#start_btn {
    background-color: #22A559;
    color: #000000;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    padding: 0 20px;
}

QPushButton#start_btn:hover {
    background-color: #26B863;
}

QPushButton#start_btn:pressed {
    background-color: #1D8F4C;
}

QPushButton#start_btn:disabled {
    background-color: #3A3A3C;
    color: #636366;
}

QLineEdit#search_box {
    padding: 4px 10px;
    font-size: 12px;
    border-radius: 6px;
}

QComboBox {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 6px;
    padding: 5px 10px;
    color: #E5E5E7;
}

QComboBox:hover { border-color: #636366; }
QComboBox QAbstractItemView {
    background-color: #2C2C2E;
    color: #E5E5E7;
    selection-background-color: #3A3A3C;
    border: 1px solid #48484A;
    outline: none;
}

QDoubleSpinBox#spin {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 6px;
    padding: 4px 8px;
    color: #E5E5E7;
}

QMenu {
    background-color: #2C2C2E;
    border: 1px solid #48484A;
    border-radius: 8px;
    padding: 4px;
    color: #E5E5E7;
}

QMenu::item {
    padding: 6px 18px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #3A3A3C;
}

QMenu::separator {
    height: 1px;
    background: #3A3A3C;
    margin: 4px 6px;
}

QPushButton#reveal {
    background-color: transparent;
    color: #AEAEB2;
    border: 1px solid #3A3A3C;
    border-radius: 6px;
    padding: 0 8px;
    height: 34px;
    font-size: 11px;
    font-weight: 500;
}

QPushButton#reveal:hover {
    color: #E5E5E7;
    border-color: #48484A;
}

QPushButton#reveal:checked {
    color: #E5E5E7;
    background-color: #3A3A3C;
}

QLabel#status_ok {
    color: #34C759;
    font-size: 12px;
}

QLabel#status_err {
    color: #FF6B6B;
    font-size: 12px;
}

QLabel#status_busy {
    color: #AEAEB2;
    font-size: 12px;
}

QLabel#warning {
    color: #FFB340;
    background-color: rgba(255, 179, 64, 0.10);
    border: 1px solid rgba(255, 179, 64, 0.30);
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 500;
}

QFrame#divider {
    background: #3A3A3C;
    border: none;
    max-height: 1px;
}

QListWidget#service_list {
    background-color: #242426;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 4px;
    font-size: 12px;
    color: #AEAEB2;
}

QListWidget#service_list::item {
    padding: 3px 6px;
    border-radius: 4px;
}

QListWidget#service_list::item:disabled {
    color: #AEAEB2;
    font-weight: 600;
    padding-top: 8px;
}

/* The "Data to scrape" boxes had the same tickless indicator as QCheckBox, and
   for the same reason. `TickStyle` paints these too — a view item's check mark
   is a different primitive, so it handles both. */

QProgressBar#budget_bar {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}

QProgressBar#budget_bar::chunk {
    background-color: #636366;
    border-radius: 4px;
}

QDateEdit#spin {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 6px;
    padding: 4px 8px;
    color: #E5E5E7;
}

QDateEdit#spin::up-button, QDateEdit#spin::down-button {
    width: 16px;
    border: none;
    background: transparent;
}
"""


def load_font(app: QApplication) -> None:
    families = QFontDatabase().families()
    if "DM Sans" in families:
        app.setFont(QFont("DM Sans", 13))
    elif sys.platform == "darwin":
        app.setFont(QFont(".AppleSystemUIFont", 13))
    else:
        app.setFont(QFont("Segoe UI", 13))


_PLACEHOLDER = "#949499"

_BOX_SIZE = 16
_BOX_OFF = "#2C2C2E"
_BOX_ON = "#636366"
_BOX_EDGE = "#48484A"
_BOX_EDGE_HOVER = "#78787A"
_BOX_DEAD = "#3A3A3C"
_TICK = "#FFFFFF"


class TickStyle(QProxyStyle):
    """Paints every check indicator in the app, tick included.

    The sheet used to style the indicator and could only give it a fill, so a
    ticked box and an empty one differed by nothing but brightness — most
    dangerously on "Dry run — build and log every email, send none", where the
    user has no way to tell whether the safety toggle is on.

    Painting it here rather than in the sheet is what makes the tick possible at
    all: `image: url(...)` needs a file on disk or a compiled resource, and this
    ships as a one-file executable. It also covers the two primitives at once —
    QCheckBox draws `PE_IndicatorCheckBox`, a checkable list row draws
    `PE_IndicatorItemViewItemCheck` — so the service list gets the same mark.
    """

    _CHECK_PRIMITIVES = (QStyle.PE_IndicatorCheckBox,
                         QStyle.PE_IndicatorItemViewItemCheck)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return _BOX_SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element not in self._CHECK_PRIMITIVES:
            super().drawPrimitive(element, option, painter, widget)
            return

        state = option.state
        live = bool(state & QStyle.State_Enabled)
        on = bool(state & QStyle.State_On)
        mixed = bool(state & QStyle.State_NoChange)

        # A square centred in whatever the caller reserved: the delegate hands
        # over the whole check column, and an indicator stretched across it
        # would sit off to one side of its own label. The half pixel puts the
        # 1px pen on pixel centres — without it the border straddles two columns
        # and antialiases into a soft #3A3A3C smudge instead of a crisp edge.
        side = min(option.rect.width(), option.rect.height(), _BOX_SIZE)
        left = option.rect.x() + (option.rect.width() - side) // 2
        top = option.rect.y() + (option.rect.height() - side) // 2
        box = QRectF(left + 0.5, top + 0.5, side - 1, side - 1)

        if not live:
            fill, edge = _BOX_DEAD, _BOX_DEAD
        elif on or mixed:
            fill, edge = _BOX_ON, _BOX_ON
        elif state & QStyle.State_MouseOver:
            fill, edge = _BOX_OFF, _BOX_EDGE_HOVER
        else:
            fill, edge = _BOX_OFF, _BOX_EDGE

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(edge), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(box, 4, 4)

        if on or mixed:
            mark = QPen(QColor(_TICK if live else _BOX_OFF), max(1.8, side / 7.5))
            mark.setCapStyle(Qt.RoundCap)
            mark.setJoinStyle(Qt.RoundJoin)
            painter.setPen(mark)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(self._mark(box, on))
        painter.restore()

    @staticmethod
    def _mark(box: QRectF, on: bool) -> QPolygonF:
        """The tick, or the dash a tri-state box shows for "partly"."""
        x, y, w, h = box.x(), box.y(), box.width(), box.height()
        if not on:
            return QPolygonF([QPointF(x + w * 0.26, y + h * 0.50),
                              QPointF(x + w * 0.74, y + h * 0.50)])
        return QPolygonF([QPointF(x + w * 0.23, y + h * 0.52),
                          QPointF(x + w * 0.42, y + h * 0.73),
                          QPointF(x + w * 0.77, y + h * 0.28)])


def install_style(app: QApplication) -> None:
    """The app's whole look: the drawn indicators, the palette, then the sheet.

    One call, because the parts are not independent — `QSS` deliberately
    leaves every check indicator unstyled so `TickStyle` can paint it, and a
    sheet applied over a plain Fusion would leave the boxes light grey on a dark
    page. Anything that builds these screens (including the tests) should come
    through here rather than setting them by hand.

    Placeholder text is set here and not in the sheet because Qt has no
    stylesheet property for it — `::placeholder` parses and then does nothing.
    Left alone it is the text colour at half alpha, which lands around #89898B
    on a field and measures 4.0:1; named outright it paints opaque. It takes
    the #hint grey rather than the secondary one, so an empty field still reads
    as empty next to a filled one, and clears AA on a field at 4.62:1.
    """
    app.setStyle(TickStyle(QStyleFactory.create("Fusion")))
    palette = app.palette()
    palette.setColor(QPalette.PlaceholderText, QColor(_PLACEHOLDER))
    app.setPalette(palette)
    app.setStyleSheet(QSS)


INPUT_SCREEN, RESULTS_SCREEN, OUTREACH_SCREEN, SETTINGS_SCREEN = range(4)


def _screen_threads(screen) -> list:
    """Every QThread a screen is currently running.

    Found by inspection rather than by name: each screen owns its own workers
    (the scrape, the send loop, the audit pass, the credential probes) and this
    window has no business knowing what they are called. A hard-coded list would
    go quietly out of date, and a thread missed here is a send loop that outlives
    the window.
    """
    running = []
    for value in vars(screen).values():
        candidates = value if isinstance(value, (list, tuple)) else (value,)
        running.extend(
            v for v in candidates if isinstance(v, QThread) and v.isRunning()
        )
    return running


def _stop_thread(worker) -> None:
    """Escalating shutdown: co-operative, then forced, then terminated."""
    if hasattr(worker, "stop"):
        worker.stop()                 # checked inside the loop
    if worker.wait(5000):
        return
    # `abort` closes the resource the thread is blocked on — the browser for a
    # scrape, the SMTP socket for a send — so the call it is parked in fails
    # fast and `run()` can unwind.
    if hasattr(worker, "abort"):
        worker.abort()
        if worker.wait(5000):
            return
    worker.terminate()
    worker.wait(2000)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MapHarvest")
        # Minimum, not fixed: the outreach and settings screens are dense enough
        # that a user with a big monitor should be able to maximise the window
        # and see a whole lead table at once.
        self.setMinimumSize(QSize(880, 620))
        self.resize(QSize(1080, 760))

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.input_screen = InputScreen()
        self.results_screen = ResultsScreen()
        self.outreach_screen = OutreachScreen()
        self.settings_screen = SettingsScreen()

        self.stack.addWidget(self.input_screen)
        self.stack.addWidget(self.results_screen)
        self.stack.addWidget(self.outreach_screen)
        self.stack.addWidget(self.settings_screen)

        # Where Back returns to, so settings opened from outreach does not dump
        # the user on the home screen with their campaign half set up.
        self._settings_return = INPUT_SCREEN

        self.input_screen.start_signal.connect(self.on_start)
        self.input_screen.settings_signal.connect(self.on_settings)
        self.input_screen.outreach_signal.connect(self.on_outreach_direct)
        self.results_screen.stop_signal.connect(self.on_stop)
        self.results_screen.home_signal.connect(self.on_home)
        self.results_screen.outreach_signal.connect(self.on_outreach)
        self.outreach_screen.home_signal.connect(self.on_home)
        self.outreach_screen.settings_signal.connect(self.on_settings)
        self.settings_screen.back_signal.connect(self.on_settings_closed)
        self.settings_screen.saved_signal.connect(self.on_settings_saved)

    def on_start(self, domains, areas, fields, headless=False, max_results=50,
                 export_dir="", filters=None):
        self.results_screen.setup(
            domains, areas, fields, headless, max_results, export_dir, filters or {},
        )
        self.stack.setCurrentIndex(RESULTS_SCREEN)
        self.results_screen.start_worker()

    def on_stop(self):
        self.results_screen.stop_worker()

    def on_home(self):
        self.stack.setCurrentIndex(INPUT_SCREEN)

    def on_outreach_direct(self):
        self.outreach_screen.refresh()
        self.stack.setCurrentIndex(OUTREACH_SCREEN)

    def on_outreach(self, records):
        self.outreach_screen.load_from_results(records)
        self.stack.setCurrentIndex(OUTREACH_SCREEN)

    def on_settings(self):
        self._settings_return = self.stack.currentIndex()
        self.settings_screen.refresh()
        self.stack.setCurrentIndex(SETTINGS_SCREEN)

    def on_settings_closed(self):
        self.stack.setCurrentIndex(self._settings_return)

    def on_settings_saved(self, settings):
        # Both screens cache their own copy of the file; hand them the new one
        # so the next thing either of them writes is not a stale snapshot.
        self.input_screen.apply_settings(settings)
        self.outreach_screen.refresh()

    def shutdown_worker(self) -> None:
        """Stop every screen's background thread before the window goes away.

        Two distinct failures this prevents. A running scrape leaves the QThread
        alive and an orphaned chrome.exe behind, so the app appears to hang on
        exit and Chrome processes pile up. A running campaign is worse: the send
        loop would carry on mailing real businesses after the user quit.
        """
        scrape = getattr(self.results_screen, "worker", None)
        if scrape is not None and scrape.isRunning():
            # Suppresses the "stopped unexpectedly" path in the results screen.
            self.results_screen._stopped_by_user = True

        for screen in (self.results_screen, self.outreach_screen, self.settings_screen):
            for worker in _screen_threads(screen):
                _stop_thread(worker)

    def closeEvent(self, event):
        self.shutdown_worker()
        event.accept()


def _install_excepthook():
    """Keep the GUI alive (and loud) when a slot raises, instead of dying silently."""
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            QApplication.quit()
            return
        traceback.print_exception(exc_type, exc, tb)
    sys.excepthook = hook


def run():
    app = QApplication(sys.argv)
    load_font(app)
    install_style(app)
    _install_excepthook()

    window = MainWindow()

    # Qt's event loop is C++, so Python never runs while idle and a Ctrl+C sits
    # queued until some slot happens to execute (which is why it used to surface
    # as a bogus traceback inside whatever button you clicked next). Handle
    # SIGINT explicitly and tick a timer so Python gets a chance to process it.
    def _on_sigint(*_):
        window.shutdown_worker()
        app.quit()

    signal.signal(signal.SIGINT, _on_sigint)
    idle = QTimer()
    idle.start(200)
    idle.timeout.connect(lambda: None)
    app.aboutToQuit.connect(window.shutdown_worker)

    window.show()
    sys.exit(app.exec_())
