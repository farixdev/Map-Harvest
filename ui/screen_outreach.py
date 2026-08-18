"""Outreach — the screen that turns a scrape into a mailing and watches it go.

Four tabs, in the order the work actually happens: Leads (who), Campaign (what
they receive), Sending (the run), Stats (what came back). The tabs are steps,
not categories, which is why each one ends by pointing at the next.

Two rules shape most of the code below.

Nothing slow runs on the GUI thread. Crawling a site, calling a model and
opening an SMTP session are all minutes of network time, so they happen in the
workers in `core.campaign`; this file starts them and draws what they emit,
and the only work it does itself is reading a local SQLite file.

Nothing is previewed that could not be sent. The preview goes through
`core.templates.render` — the same call the send loop makes — and refuses to
draw anything still carrying a `{{token}}`. A leaked merge field in a live cold
email is the most expensive bug this system can ship, and this screen is the
last place a human sees the copy before it leaves.

Nothing here says no without saying how through. An unfinished sender profile,
an empty queue, a template that has gone missing: each one is stated in plain
words with what it costs, and then the user decides. `require_profile_complete`
in `core.settings` is the switch behind the one question that is still asked —
on, it asks and offers to go ahead anyway; off, the same warnings are printed
and nothing is asked at all. A disabled control on this screen always carries
the sentence that says what would re-enable it.

Styling comes entirely from the global QSS in `ui/app.py`. Controls that need a
look carry an objectName from that sheet; the one rule that does not exist yet
(`QTextBrowser#email_paper`) is listed for the integrator, and the preview also
paints its own document background so it stays readable without it.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import time
import webbrowser
from datetime import datetime

from PyQt5.QtCore import QEvent, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from core import campaign as _campaign
from core import outreach_db as _db
from core import settings as _settings
from core import templates as _templates
from core.ai import AIClient
from core.campaign import AuditWorker, OutreachWorker, plan_campaign
from ui.screen_results import SortableItem

# ── Palette ──────────────────────────────────────────────────────────────────

# Every colour here already appears in the global QSS — the greens from
# QPushButton#start_btn, the red from QPushButton#danger, the greys from the
# label rules — so a banded table cell reads as the same visual language as the
# buttons instead of introducing a third accent.
_GREEN = "#22A559"
_GREEN_BRIGHT = "#26B863"
_RED = "#FF6B6B"
_TEXT = "#E5E5E7"
_SOFT = "#AEAEB2"
# Both of these used to sit under WCAG AA on the card and table grounds
# (#8E8E93 measured 4.27:1 on a card, #636366 measured 2.33:1) and they carry
# real information — the Score and Headline-gap columns of a freshly imported
# lead are written in the dimmest of them. Worst-case ratios on the lightest
# ground either appears on (#2C2C2E): 5.36:1 and 4.62:1.
_MUTED = "#A0A0A6"
_DIM = "#949499"
_RULE = "#48484A"

# Opportunity score bands, highest first. A high score means many automatable
# gaps, so green is the good prospect and red is the thin one.
_SCORE_BANDS = ((70, _GREEN, "strong"), (45, _SOFT, "moderate"), (1, _RED, "thin"))

_STATUS_COLOURS = {
    "new": _MUTED, "audited": _TEXT, "queued": _SOFT, "sent": _GREEN,
    "replied": _GREEN_BRIGHT, "bounced": _RED, "failed": _RED,
    "skipped": _DIM, "suppressed": _RED,
}

# ── Table ────────────────────────────────────────────────────────────────────

_LEAD_COLUMNS = ("Business", "Email", "Score", "Headline gap", "Status")
_COL_NAME, _COL_EMAIL, _COL_SCORE, _COL_GAP, _COL_STATUS = range(5)
_COL_WIDTHS = (200, 210, 68, 240, 96)

# Keys are lead statuses, except the two prefixed "~": those filter on whether
# the lead's email would say anything about them, which is not a status and is
# the only way to send to the personalised half of a list and leave the rest.
_STATUS_FILTERS = (
    ("All leads", ""), ("Not audited", "new"), ("Audited", "audited"),
    ("Personalised", "~personal"), ("Generic email", "~generic"),
    ("Queued", "queued"), ("Sent", "sent"), ("Replied", "replied"),
    ("Bounced", "bounced"), ("Suppressed", "suppressed"),
)

# Columns a CSV may name in any of the ways people actually name them.
_CSV_ALIASES = {
    "email": "email", "e_mail": "email", "email_address": "email", "emails": "email",
    "name": "name", "business": "name", "business_name": "name", "company": "name",
    "company_name": "name", "title": "name",
    "website": "website", "url": "website", "site": "website", "web": "website",
    "domain": "website",
    "phone": "phone", "phone_number": "phone", "telephone": "phone", "tel": "phone",
    "city": "city", "area": "city", "town": "city", "location": "city",
    "category": "category", "type": "category", "industry": "category",
    "rating": "rating", "stars": "rating",
    "maps_link": "maps_link", "map_link": "maps_link", "google_maps": "maps_link",
}

# A CSV is a hand-made file; past this it is a mistake, not a lead list.
_CSV_MAX_ROWS = 20000

# Anything that still looks like a merge token after rendering. The preview
# refuses to draw a body matching this rather than showing the user copy that
# would embarrass them in a stranger's inbox.
_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")

_LOG_LIMIT = 400
_TOAST_MS = 6000
_DAY_SEC = 86400.0
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# `ai_provider`, said the way the Settings screen offers it. Every campaign
# setting the user is likely to want back is edited there; this screen's job is
# to name the ones that shape what it is about to do.
_AI_PROVIDERS = {
    "auto": "AI: Groq, then OpenRouter",
    "groq": "AI: Groq",
    "openrouter": "AI: OpenRouter",
    "off": "AI off — plain templates",
}

# Item data slots. `+ 1` keeps the untruncated cell text for search and
# tooltips, `+ 2` the numeric opportunity score the Score column sorts on.
_FULL_ROLE = Qt.UserRole + 1
_SCORE_ROLE = Qt.UserRole + 2


# ── Small helpers ────────────────────────────────────────────────────────────

def _text_of(value) -> str:
    return "" if value is None else str(value)


def _int_of(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _loads(blob) -> dict:
    """A stored JSON blob as a dict. Anything unreadable reads as empty."""
    if isinstance(blob, dict):
        return blob
    try:
        value = json.loads(blob or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _clip(text: str, limit: int) -> str:
    text = _text_of(text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _rich(text: str, colour: str = "", size: int = 0, bold: bool = False) -> str:
    """Inline-styled label text.

    The global sheet fixes a colour and a size on every QLabel, and a stylesheet
    beats `setFont`/`setPalette`, so emphasis inside a label has to arrive as
    rich text. Used sparingly: the stat tiles, and the lines that must read as a
    warning.
    """
    style = []
    if colour:
        style.append("color:%s" % colour)
    if size:
        style.append("font-size:%dpx" % size)
    if bold:
        style.append("font-weight:600")
    return '<span style="%s">%s</span>' % (";".join(style), html.escape(text))


def _clock(ts: float) -> str:
    """"Mon 9:14 AM" — the format the plan summary and the log both use."""
    try:
        stamp = datetime.fromtimestamp(float(ts)).strftime("%a %I:%M %p")
    except (OSError, OverflowError, TypeError, ValueError):
        return "—"
    return stamp.replace(" 0", " ")


def _countdown(seconds: float) -> str:
    seconds = int(max(0.0, float(seconds)))
    if seconds < 1:
        return "now"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def _plural(count: int, word: str) -> str:
    """"3 leads", "1 lead", "0 addresses".

    A bare "s" is wrong on every sibilant ending, and this screen counts
    addresses. Nothing here is irregular, so the sibilant rule is the whole of
    the English needed.
    """
    if count == 1:
        return "1 %s" % word
    suffix = "es" if word.endswith(("s", "x", "z", "ch", "sh")) else "s"
    return "%d %s%s" % (count, word, suffix)


def _generic_sentence(counts) -> str:
    """Why a campaign's form letters are form letters, biggest cause first.

    One cause is named on its own. Several are counted out, because "those sites
    were unreachable" said of a group where a third of them answered perfectly
    well is the same small untruth this counter exists to stop.
    """
    ranked = sorted(((_text_of(reason), _int_of(count)) for reason, count
                     in (counts or {}).items()), key=lambda item: (-item[1], item[0]))
    named = [(reason, count) for reason, count in ranked
             if _templates.generic_reason(reason, plural=True)]
    if not named:
        return "the crawl found nothing to say about them"
    if len(named) == 1:
        return _templates.generic_reason(named[0][0], plural=True)
    return ", ".join("%d because %s" % (count, _templates.generic_reason(reason, plural=True))
                     for reason, count in named[:3])


def _reason_list(counts, limit: int = 3) -> str:
    """"9 already contacted, 2 suppressed" — the plan's own words, biggest first.

    `core.campaign` counts every skip against a reason precisely so this line
    can name it. The old sentence recited the three usual causes whether they
    applied or not, which made a record the renderer choked on indistinguishable
    from an address somebody unsubscribed.
    """
    if not isinstance(counts, dict) or not counts:
        return "already contacted, suppressed, or no usable address"
    ranked = sorted(counts.items(), key=lambda item: (-_int_of(item[1]), _text_of(item[0])))
    named = ["%d %s" % (_int_of(count), _text_of(reason)) for reason, count in ranked[:limit]]
    if len(ranked) > limit:
        named.append("%d for other reasons" % sum(_int_of(c) for _r, c in ranked[limit:]))
    return ", ".join(named)


def _norm_key(key: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _text_of(key).strip().lower())).strip("_")


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section_label")
    return label


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    return label


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 14, 16, 14)
    box.setSpacing(8)
    if title:
        box.addWidget(_section(title))
    return frame, box


def _button(text: str, style: str = "", height: int = 30) -> QPushButton:
    btn = QPushButton(text)
    if style:
        btn.setObjectName(style)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def _thin_bar() -> QProgressBar:
    bar = QProgressBar()
    bar.setFixedHeight(2)
    bar.setTextVisible(False)
    bar.setRange(0, 100)
    bar.setValue(0)
    return bar


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)


class _ElidedLabel(QLabel):
    """A one-line label that shortens with an ellipsis instead of being cut.

    Qt clips a plain QLabel at the widget edge and paints no cue, so the
    Campaign To/From line rendered as "…@gmail.cc" at the default window size
    and stopped dead at "From Sam" at the minimum — with `toolTip()` empty, the
    user had nothing telling them a sender address was missing from the line
    that says which account will send. The full text is always the tooltip, so
    what has been dropped is one hover away.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = _text_of(text)
        self.setToolTip(self._full)
        self._paint_elided()

    def fullText(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        # Otherwise QLabel asks for the whole string as its minimum and the row
        # it sits in cannot shrink past it — the same clip, one level up. The
        # floor is a few characters and an ellipsis: below that the line stops
        # being readable and the tooltip is the only copy that matters anyway.
        hint = super().minimumSizeHint()
        floor = self.fontMetrics().horizontalAdvance("…") * 8
        return QSize(min(hint.width(), floor), hint.height())

    def sizeHint(self) -> QSize:
        # super() first: it polishes the widget, and the sheet's font is what
        # the elision has to be measured against. Reading `fontMetrics()` before
        # that call answers for whatever font the label had before styling.
        height = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._paint_elided()

    def changeEvent(self, event) -> None:
        # The sheet arrives after construction, and the label is first elided
        # against whatever font it had before that. Without this the line keeps
        # a shortening measured in the wrong font until something resizes it.
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._paint_elided()

    def _paint_elided(self) -> None:
        width = max(0, self.width() - self.margin() * 2)
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, width) \
            if width else self._full
        QLabel.setText(self, shown)


class _ScoreItem(SortableItem):
    """A Score cell that sorts on the number behind it.

    The column shows an em dash for a lead nobody has audited yet, and an em
    dash compares greater than any digit as text, so sorting the column as
    written would float every unaudited lead above the best prospect. The score
    itself is stashed on the item and compared directly.
    """

    def __lt__(self, other):
        return _int_of(self.data(_SCORE_ROLE), -1) < _int_of(other.data(_SCORE_ROLE), -1)


def _score_band(score: int) -> tuple[str, str]:
    for floor, colour, label in _SCORE_BANDS:
        if score >= floor:
            return colour, label
    return _DIM, "not audited"


def _empty_state(headline: str, sentence: str) -> QWidget:
    """A card that says what is missing and what to do about it.

    Every empty table on this screen gets one. An empty grid with a header row
    tells the user nothing; a sentence naming the next action tells them
    everything, and this app has three places where the correct next action is
    on a different screen.
    """
    page = QWidget()
    box = QVBoxLayout(page)
    box.setContentsMargins(0, 0, 0, 0)
    box.addStretch()

    frame, inner = _card()
    inner.setSpacing(6)
    title = QLabel(headline)
    title.setObjectName("count_label")
    title.setAlignment(Qt.AlignCenter)
    body = _hint(sentence)
    body.setAlignment(Qt.AlignCenter)
    inner.addWidget(title)
    inner.addWidget(body)

    row = QHBoxLayout()
    row.addStretch()
    frame.setMaximumWidth(460)
    row.addWidget(frame)
    row.addStretch()
    box.addLayout(row)
    box.addStretch()
    return page


# ── Per-day bars ─────────────────────────────────────────────────────────────

class _DayBars(QWidget):
    """Sent and still-queued volume per day, painted directly.

    A bar row is a handful of rectangles. Pulling in a charting dependency to
    draw them would cost more than it saves, and this way the bars use the same
    greens as the rest of the screen for free.
    """

    def __init__(self):
        super().__init__()
        self._days: list[tuple[str, int, int]] = []
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_days(self, days) -> None:
        self._days = list(days or [])[-14:]
        self.update()

    def paintEvent(self, event) -> None:
        if not self._days:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)

        label_h, value_h = 16, 14
        top = value_h
        floor = self.height() - label_h
        span = max(1, floor - top)
        peak = max(1, max(sent + queued for _, sent, queued in self._days))

        # Bars keep a sane width and the row is centred, so a three-day campaign
        # reads as a short row rather than three lonely bars flung across the
        # full width of the card.
        slot = min(74.0, self.width() / float(len(self._days)))
        width = max(6.0, min(48.0, slot - 12.0))
        origin = max(0.0, (self.width() - slot * len(self._days)) / 2.0)
        painter.setPen(Qt.NoPen)

        for index, (label, sent, queued) in enumerate(self._days):
            base = origin + index * slot
            left = base + (slot - width) / 2.0
            total = sent + queued
            height = span * (total / float(peak))
            sent_h = height * (sent / float(total)) if total else 0.0

            painter.setBrush(QColor(_RULE))
            painter.drawRoundedRect(int(left), int(floor - height), int(width),
                                    int(max(2.0, height)), 3, 3)
            if sent_h >= 2.0:
                painter.setBrush(QColor(_GREEN))
                painter.drawRoundedRect(int(left), int(floor - sent_h), int(width),
                                        int(sent_h), 3, 3)

            painter.setPen(QColor(_MUTED))
            painter.drawText(int(base), floor + 2, int(slot), label_h,
                             Qt.AlignHCenter | Qt.AlignVCenter, label)
            if total and width >= 20:
                painter.setPen(QColor(_SOFT))
                painter.drawText(int(base), int(floor - height) - value_h,
                                 int(slot), value_h,
                                 Qt.AlignHCenter | Qt.AlignBottom, str(total))
            painter.setPen(Qt.NoPen)


# ── Planning worker ──────────────────────────────────────────────────────────

class _PlanWorker(QThread):
    """Runs `plan_campaign` off the GUI thread.

    Planning audits every lead that has not been audited yet — a site crawl and
    possibly a model call each — so on a real list it is minutes of network
    work. `core.campaign` ships a worker for auditing and one for sending but
    none for planning, so the thread lives here. It holds no state beyond its
    arguments and exists only to keep the window painting.

    Stopping is co-operative, because quitting the app mid-plan is the normal
    way a plan ends rather than an edge case. `plan_campaign` is handed
    `should_stop` and checks it between leads, so `ui.app._stop_thread` gets its
    thread back at the next lead boundary instead of sitting out `wait(5000)`
    and calling `terminate()` in the middle of a crawl. What was already queued
    is committed and survives in the database either way.
    """

    plan_signal = pyqtSignal(dict)
    progress_signal = pyqtSignal(int, int, str)

    def __init__(self, campaign_id: int, leads: list, template_id: str, settings: dict):
        super().__init__()
        self.campaign_id = _int_of(campaign_id)
        self.leads = list(leads or [])
        self.template_id = _text_of(template_id)
        self._settings = settings if isinstance(settings, dict) else {}
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            ai = AIClient(self._settings)
        except Exception:
            ai = None
        try:
            plan = plan_campaign(
                None, campaign_id=self.campaign_id, leads=self.leads,
                template_id=self.template_id,
                profile=self._settings.get("sender_profile") or {},
                settings=self._settings, ai=ai, progress=self._progress,
                should_stop=self._should_stop,
            )
        except Exception as exc:
            plan = {"error": "%s: %s" % (type(exc).__name__, exc), "queued": 0}
        self.plan_signal.emit(plan if isinstance(plan, dict) else {})

    def _should_stop(self) -> bool:
        return not self._running

    def _progress(self, done, total, message) -> None:
        self.progress_signal.emit(_int_of(done), _int_of(total), _text_of(message))


# ── The screen ───────────────────────────────────────────────────────────────

class OutreachScreen(QWidget):
    """Leads, campaign, sending and stats for one outreach run."""

    home_signal = pyqtSignal()
    settings_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.settings = _settings.load_settings()
        try:
            self.conn = _db.connect()
        except Exception:                    # a dead store must not block the GUI
            self.conn = None

        # Workers are kept referenced after they finish rather than cleared in
        # the done handler. `done_signal` is emitted from inside `run()`, so
        # dropping the last reference there can destroy a QThread that has not
        # unwound yet; the flags below track "busy" instead, and a worker is
        # only replaced once its thread has actually stopped. They stay plain
        # attributes because `ui.app` finds a screen's threads by inspecting
        # them at shutdown.
        self.audit_worker = None
        self.plan_worker = None
        self.send_worker = None
        self._auditing = False
        self._planning = False

        self._leads: list[dict] = []
        # lead id -> why that lead's email would be a form letter, "" when it
        # would not. Filled row by row as the table is built, because the answer
        # comes out of `core.templates` and the table is the only place that
        # walks every lead.
        self._generic: dict[int, str] = {}
        self._search = ""
        self._campaign_id = 0
        self._plan: dict = {}
        self._sending = False
        self._paused = False
        self._toast_timer = None
        # Per-account rehearsal tally, keyed by lowercased address. A dry run
        # keeps its sends out of the `sends` table on purpose so a rehearsal
        # cannot spend an account's real quota, which left the Accounts card
        # reading "0 / 10 today" throughout the one operation it exists to
        # report on. Counted here instead, and shown next to — never inside —
        # the real number.
        self._rehearsed: dict[str, int] = {}

        self._build()
        self.refresh()

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QWidget()
        body_box = QVBoxLayout(body)
        body_box.setContentsMargins(20, 14, 20, 14)
        body_box.setSpacing(12)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_leads_page())
        self.pages.addWidget(self._build_campaign_page())
        self.pages.addWidget(self._build_sending_page())
        self.pages.addWidget(self._build_stats_page())
        body_box.addWidget(self.pages, stretch=1)

        self.toast_label = QLabel("")
        self.toast_label.setObjectName("toast")
        self.toast_label.setWordWrap(True)
        self.toast_label.setAlignment(Qt.AlignCenter)
        self.toast_label.hide()
        body_box.addWidget(self.toast_label)

        root.addWidget(body, stretch=1)
        self.tab_group.idClicked.connect(self._on_tab_changed)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("results_header")
        box = QHBoxLayout(header)
        box.setContentsMargins(20, 10, 20, 10)
        box.setSpacing(8)

        name = QLabel("MapHarvest")
        name.setObjectName("app_name")
        box.addWidget(name)
        box.addWidget(_muted("Outreach"))

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self._tab_btns = {}
        for index, label in enumerate(("Leads", "Campaign", "Sending", "Stats")):
            btn = _button(label, "tab", 28)
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            self.tab_group.addButton(btn, index)
            tabs.addWidget(btn)
            self._tab_btns[index] = btn
        box.addSpacing(16)
        box.addLayout(tabs)
        box.addStretch()

        # The mode badge lives in the header so it is on screen on every tab,
        # and it is a button because the thing a surprised user wants next is
        # the setting that put them in this mode.
        self.mode_badge = _button("DRY RUN", "rehearsal", 28)
        self.mode_badge.setToolTip("Open Settings to change the dry-run setting")
        self.mode_badge.clicked.connect(self.settings_signal.emit)
        box.addWidget(self.mode_badge)

        settings_btn = _button("Settings", "outlined")
        settings_btn.clicked.connect(self.settings_signal.emit)
        home_btn = _button("Home", "outlined")
        home_btn.clicked.connect(self.home_signal.emit)
        box.addWidget(settings_btn)
        box.addWidget(home_btn)
        return header

    # ── Leads tab ────────────────────────────────────────────────────────────

    def _build_leads_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(_section("Leads"))

        self.lead_search = QLineEdit()
        self.lead_search.setObjectName("search_box")
        self.lead_search.setPlaceholderText("Filter leads…")
        self.lead_search.setClearButtonEnabled(True)
        self.lead_search.setFixedHeight(28)
        self.lead_search.setMaximumWidth(240)
        self.lead_search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.lead_search)

        self.status_filter = QComboBox()
        self.status_filter.setFixedHeight(28)
        for label, _key in _STATUS_FILTERS:
            self.status_filter.addItem(label)
        self.status_filter.currentIndexChanged.connect(lambda _i: self._apply_filters())
        bar.addWidget(self.status_filter)

        bar.addStretch()
        self.lead_counts = _muted("")
        bar.addWidget(self.lead_counts)
        box.addLayout(bar)

        self.lead_stack = QStackedWidget()
        self.lead_table = self._new_table(_LEAD_COLUMNS, _COL_WIDTHS)
        # A fresh QTableWidget starts its indicator on (column 0, descending),
        # so re-enabling sorting after a reload used to silently reverse the
        # list by business name. Best prospect first is what this table is for;
        # a header click replaces it and the choice then survives every reload.
        self.lead_table.sortByColumn(_COL_SCORE, Qt.DescendingOrder)
        self.lead_table.customContextMenuRequested.connect(self._show_lead_menu)
        self.lead_table.cellDoubleClicked.connect(self._on_lead_double_clicked)
        self.lead_table.itemSelectionChanged.connect(self._on_lead_selection_changed)
        self.lead_stack.addWidget(self.lead_table)
        self.lead_stack.addWidget(_empty_state(
            "No leads yet",
            "Scrape a city on the Home screen and press Start Outreach on the "
            "results, or import a CSV that has an email column. Businesses "
            "without an email address cannot be contacted and are skipped.",
        ))
        box.addWidget(self.lead_stack, stretch=1)

        self.lead_progress = _thin_bar()
        self.lead_progress.hide()
        box.addWidget(self.lead_progress)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        import_csv = _button("Import CSV…", "outlined")
        import_csv.setToolTip("Load leads from a spreadsheet export")
        import_csv.clicked.connect(self._on_import_csv)
        actions.addWidget(import_csv)

        self.lead_status = _muted("")
        actions.addWidget(self.lead_status)
        actions.addStretch()

        self.audit_btn = _button("Audit all", "start_btn", 34)
        self.audit_btn.setToolTip(
            "Crawl each website, score the automation gaps and write the "
            "personalised lines. Nothing is sent.")
        self.audit_btn.clicked.connect(self._on_audit_clicked)
        actions.addWidget(self.audit_btn)
        box.addLayout(actions)

        box.addWidget(_hint(
            "Double-click a lead to open its website · right-click to copy or "
            "suppress · click a column header to sort. Auditing is what fills "
            "the Score and Headline gap columns, and what the email copy is "
            "built from."))
        return page

    def _new_table(self, columns, widths) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setObjectName("results_table")
        table.setHorizontalHeaderLabels(list(columns))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setShowGrid(False)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(36)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setMinimumSectionSize(60)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.setSortingEnabled(True)
        for index, width in enumerate(widths):
            table.setColumnWidth(index, width)
        return table

    # ── Campaign tab ─────────────────────────────────────────────────────────

    def _build_campaign_page(self) -> QWidget:
        page = QWidget()
        columns = QHBoxLayout(page)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(12)

        angle_card, angle_box = _card("Template")
        self.template_combo = QComboBox()
        self.template_combo.setFixedHeight(30)
        self._refresh_templates()
        self.template_combo.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        angle_box.addWidget(self.template_combo)
        self.followup_hint = _hint("")
        angle_box.addWidget(self.followup_hint)
        left.addWidget(angle_card)

        who_card, who_box = _card("Previewing")
        self.preview_combo = QComboBox()
        self.preview_combo.setFixedHeight(30)
        self.preview_combo.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        who_box.addWidget(self.preview_combo)
        who_box.addWidget(_hint(
            "Selecting a lead on the Leads tab previews that one."))
        left.addWidget(who_card)

        profile_card, profile_box = _card("Sender profile")
        self.profile_summary = QLabel("")
        self.profile_summary.setObjectName("muted")
        self.profile_summary.setWordWrap(True)
        self.profile_summary.setTextFormat(Qt.RichText)
        profile_box.addWidget(self.profile_summary)
        self.profile_fix_btn = _button("Fix in Settings", "danger")
        self.profile_fix_btn.clicked.connect(self.settings_signal.emit)
        profile_box.addWidget(self.profile_fix_btn, alignment=Qt.AlignLeft)
        left.addWidget(profile_card)

        plan_card, plan_box = _card("Schedule")
        self.plan_summary = QLabel("")
        self.plan_summary.setObjectName("muted")
        self.plan_summary.setWordWrap(True)
        self.plan_summary.setTextFormat(Qt.RichText)
        plan_box.addWidget(self.plan_summary)
        self.plan_progress = _thin_bar()
        self.plan_progress.hide()
        plan_box.addWidget(self.plan_progress)

        # Stacked, not side by side: "Prepare campaign" and "Open Sending" want
        # 164px and 120px of label between them, and the column is 340px wide
        # less its margins, so a row clipped the primary button mid-glyph. Full
        # width also stops the label ever deciding the layout — a wider font or
        # a translation cannot squeeze either button again.
        plan_actions = QVBoxLayout()
        plan_actions.setSpacing(8)
        self.prepare_btn = _button("Prepare campaign", "start_btn", 34)
        self.prepare_btn.setToolTip(
            "Audit, personalise and queue every selected lead. Still sends nothing.")
        self.prepare_btn.clicked.connect(self._on_prepare_clicked)
        plan_actions.addWidget(self.prepare_btn)
        self.goto_sending_btn = _button("Open Sending", "outlined", 34)
        self.goto_sending_btn.clicked.connect(lambda: self._goto_tab(2))
        self.goto_sending_btn.hide()
        plan_actions.addWidget(self.goto_sending_btn)
        plan_box.addLayout(plan_actions)
        left.addWidget(plan_card)
        left.addStretch()

        # The four cards want 574px and the window's own minimum height leaves
        # this column 542, so at 880x620 the Schedule card was handed 16px less
        # than its *minimum* — the plan's last line vanished and the two buttons
        # were pushed into each other. Nothing here can be made shorter without
        # dropping something the user has to read, so the column scrolls
        # instead: full height when there is room, a scrollbar when there is
        # not, and never a silent clip.
        left_holder = QWidget()
        left_holder.setLayout(left)
        left_holder.setFixedWidth(340)

        self.campaign_scroll = QScrollArea()
        self.campaign_scroll.setWidget(left_holder)
        self.campaign_scroll.setWidgetResizable(True)
        self.campaign_scroll.setFrameShape(QFrame.NoFrame)
        self.campaign_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.campaign_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # The bar overlays nothing in Qt, so the viewport has to be wide enough
        # for a 340px column plus the 10px bar or the cards lose those pixels
        # the moment it appears.
        bar_width = self.campaign_scroll.verticalScrollBar().sizeHint().width()
        self.campaign_scroll.setFixedWidth(340 + bar_width)
        self.campaign_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        columns.addWidget(self.campaign_scroll)

        right = QVBoxLayout()
        right.setSpacing(8)

        subject_row = QHBoxLayout()
        subject_row.setSpacing(8)
        subject_row.addWidget(_section("Subject"))
        subject_row.addStretch()
        self.subject_count = QLabel("")
        self.subject_count.setObjectName("muted")
        self.subject_count.setTextFormat(Qt.RichText)
        subject_row.addWidget(self.subject_count)
        right.addLayout(subject_row)

        self.subject_label = QLabel("—")
        self.subject_label.setObjectName("count_label")
        self.subject_label.setWordWrap(True)
        right.addWidget(self.subject_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.preview_meta = _ElidedLabel()
        self.preview_meta.setObjectName("muted")
        meta_row.addWidget(self.preview_meta, stretch=1)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        for index, label in enumerate(("Text", "HTML")):
            btn = _button(label, "tab", 26)
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            self.view_group.addButton(btn, index)
            meta_row.addWidget(btn)
        self.view_group.idClicked.connect(lambda _i: self._refresh_preview())
        right.addLayout(meta_row)

        self.preview = QTextBrowser()
        self.preview.setObjectName("email_paper")
        self.preview.setOpenExternalLinks(False)
        self.preview.setOpenLinks(False)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.preview, stretch=1)
        self.preview_hint = _hint("")
        right.addWidget(self.preview_hint)
        columns.addLayout(right, stretch=1)
        return page

    # ── Sending tab ──────────────────────────────────────────────────────────

    def _build_sending_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)

        self.send_badge = _button("", "rehearsal", 44)
        self.send_badge.setToolTip("Open Settings to change the dry-run setting")
        self.send_badge.clicked.connect(self.settings_signal.emit)
        box.addWidget(self.send_badge)
        self.send_note = _hint("")
        box.addWidget(self.send_note)

        pick = QHBoxLayout()
        pick.setSpacing(8)
        pick.addWidget(_section("Campaign"))
        self.campaign_combo = QComboBox()
        self.campaign_combo.setFixedHeight(28)
        self.campaign_combo.setMinimumWidth(280)
        self.campaign_combo.currentIndexChanged.connect(self._on_campaign_changed)
        pick.addWidget(self.campaign_combo)
        pick.addStretch()

        self.start_btn = _button("Start sending", "start_btn", 34)
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.pause_btn = _button("Pause", "outlined", 34)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.pause_btn.setEnabled(False)
        self.stop_btn = _button("Stop", "danger", 34)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setEnabled(False)
        for btn in (self.start_btn, self.pause_btn, self.stop_btn):
            pick.addWidget(btn)
        box.addLayout(pick)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.send_status = QLabel("Nothing queued yet")
        self.send_status.setObjectName("status_text")
        progress_row.addWidget(self.send_status)
        progress_row.addStretch()
        self.next_send_label = _muted("")
        progress_row.addWidget(self.next_send_label)
        box.addLayout(progress_row)

        self.send_progress = _thin_bar()
        box.addWidget(self.send_progress)

        middle = QHBoxLayout()
        middle.setSpacing(16)

        accounts_card, accounts_box = _card("Accounts today")
        self.accounts_holder = QVBoxLayout()
        self.accounts_holder.setSpacing(10)
        accounts_box.addLayout(self.accounts_holder)
        accounts_box.addStretch()
        accounts_card.setFixedWidth(320)
        middle.addWidget(accounts_card)

        log_side = QVBoxLayout()
        log_side.setSpacing(8)
        log_side.addWidget(_section("Activity"))
        self.log_list = QListWidget()
        self.log_list.setObjectName("saved_list")
        self.log_list.setSelectionMode(QAbstractItemView.NoSelection)
        # Without wrapping, a line longer than the panel makes the whole list
        # scroll sideways: the placeholder sentence and a business with a long
        # name both run past 500px, and the panel is 494px at the window's
        # minimum size. Wrapped, the panel stays a column of text at every
        # width and no line is ever hidden off to the right.
        self.log_list.setWordWrap(True)
        self._clear_log()
        log_side.addWidget(self.log_list, stretch=1)
        middle.addLayout(log_side, stretch=1)
        box.addLayout(middle, stretch=1)

        box.addWidget(_hint(
            "Sends are spaced by a random gap inside your sending window and "
            "stop at each account's daily cap. Closing the app pauses the run; "
            "the queue survives and picks up where it left off."))
        return page

    # ── Stats tab ────────────────────────────────────────────────────────────

    def _build_stats_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(_section("Results"))
        head.addStretch()
        self.stats_campaign = _muted("")
        head.addWidget(self.stats_campaign)
        box.addLayout(head)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles: dict[str, QLabel] = {}
        for key, caption, colour in (
            ("queued", "Queued", _SOFT), ("sent", "Sent", _GREEN),
            ("failed", "Failed", _RED), ("replied", "Replied", _GREEN_BRIGHT),
            ("bounced", "Bounced", _RED), ("skipped", "Skipped", _DIM),
        ):
            frame, inner = _card()
            inner.setSpacing(2)
            value = QLabel("")
            value.setTextFormat(Qt.RichText)
            value.setProperty("band", colour)
            inner.addWidget(value)
            inner.addWidget(_muted(caption))
            tiles.addWidget(frame)
            self.tiles[key] = value
        box.addLayout(tiles)

        # Not a tile, because it is not a count of messages: it is how many of
        # them say anything about the business they are addressed to. A user
        # reading six green numbers has no way to tell two hundred personalised
        # emails from two hundred form letters, and that is the difference
        # between outreach and spam.
        self.personal_note = QLabel("")
        self.personal_note.setObjectName("muted")
        self.personal_note.setWordWrap(True)
        self.personal_note.setTextFormat(Qt.RichText)
        box.addWidget(self.personal_note)

        bars_card, bars_box = _card("Volume by day")
        self.day_bars = _DayBars()
        bars_box.addWidget(self.day_bars)
        self.bars_empty = _hint(
            "Nothing scheduled yet. Prepare a campaign and the days it spans "
            "appear here, filling in green as messages go out.")
        bars_box.addWidget(self.bars_empty)
        box.addWidget(bars_card)

        supp_head = QHBoxLayout()
        supp_head.setSpacing(8)
        supp_head.addWidget(_section("Suppression list"))
        supp_head.addStretch()
        self.supp_count = _muted("")
        supp_head.addWidget(self.supp_count)
        self.unsuppress_btn = _button("Remove selected", "outlined")
        self.unsuppress_btn.clicked.connect(self._on_unsuppress_clicked)
        supp_head.addWidget(self.unsuppress_btn)
        box.addLayout(supp_head)

        self.supp_list = QListWidget()
        self.supp_list.setObjectName("saved_list")
        self.supp_list.setWordWrap(True)
        self.supp_list.setMinimumHeight(120)
        box.addWidget(self.supp_list, stretch=1)
        box.addWidget(_hint(
            "Every address here is permanently excluded from planning and "
            "sending, follow-ups included. Remove one only if the person asked "
            "to be contacted again."))
        return page

    # ── Public API ───────────────────────────────────────────────────────────

    def load_from_results(self, records: list[dict]) -> None:
        """Take the rows the Results screen just scraped into the lead pool."""
        try:
            leads = [self._lead_from_record(r, "scrape") for r in (records or [])
                     if isinstance(r, dict)]
            self._import_leads(leads, "the last scrape")
        except Exception:
            self._toast("Could not read those results.")

    def refresh(self) -> None:
        """Re-read settings and the database and redraw everything."""
        try:
            self.settings = _settings.load_settings()
            self._refresh_mode()
            self._refresh_templates()
            self._refresh_campaigns()
            self._reload_leads()
            self._refresh_profile()
            self._refresh_accounts()
            self._refresh_stats()
        except Exception:
            pass

    # ── Leads: import ────────────────────────────────────────────────────────

    def _lead_from_record(self, record: dict, source: str) -> dict:
        """One scrape row mapped onto the `leads` columns."""
        return {
            "email": record.get("email"),
            "name": record.get("name"),
            "website": record.get("website"),
            "phone": record.get("phone"),
            "city": record.get("city") or record.get("area") or record.get("_area"),
            "category": record.get("category") or record.get("domain") or record.get("_domain"),
            "rating": record.get("rating"),
            "maps_link": record.get("maps_link") or record.get("_href"),
            "source": source,
        }

    def _import_leads(self, leads: list, origin: str) -> None:
        added = 0
        for lead in leads:
            if "@" not in _text_of(lead.get("email")):
                continue
            if _db.upsert_lead(self.conn, lead):
                added += 1

        skipped = len(leads) - added
        self._reload_leads()
        self._goto_tab(0)
        if not added:
            self._toast("Nothing in %s had an email address, so there is nobody "
                        "to contact yet." % origin)
            return
        note = "Imported %s from %s." % (_plural(added, "lead"), origin)
        if skipped:
            note += "  %d had no email address and %s skipped." % (
                skipped, "was" if skipped == 1 else "were")
        self._toast(note + "  Audit them next to score the opportunity.")

    def _on_import_csv(self) -> None:
        start = self.settings.get("export_dir") or os.path.expanduser("~")
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import leads", start, "CSV files (*.csv);;All files (*)")
        if not path:
            return
        leads, problem = self._read_csv(path)
        if problem:
            self._toast(problem)
            return
        self._import_leads(leads, os.path.basename(path))

    def _read_csv(self, path: str) -> tuple[list, str]:
        """Rows from a CSV, mapped onto lead columns. (leads, problem)."""
        try:
            # utf-8-sig because a spreadsheet export leads with a BOM, which
            # would otherwise turn the first header into "﻿email".
            with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
                reader = csv.DictReader(handle)
                headers = {_norm_key(h): h for h in (reader.fieldnames or []) if h}
                mapping = {_CSV_ALIASES[key]: raw for key, raw in headers.items()
                           if key in _CSV_ALIASES}
                if "email" not in mapping:
                    return [], ("That file has no email column, so none of it can "
                                "be contacted. Expected a header called email.")
                leads = []
                for index, row in enumerate(reader):
                    if index >= _CSV_MAX_ROWS:
                        break
                    lead = {field: _text_of(row.get(column)).strip()
                            for field, column in mapping.items()}
                    lead["source"] = "csv"
                    if "@" in lead.get("email", ""):
                        leads.append(lead)
        except OSError as exc:
            return [], "Could not read that file: %s" % exc
        except (csv.Error, UnicodeError):
            return [], "That file is not readable as CSV."
        if not leads:
            return [], "No rows in that file had an email address."
        return leads, ""

    # ── Leads: table ─────────────────────────────────────────────────────────

    def _reload_leads(self) -> None:
        self._leads = _db.list_leads(self.conn)
        table = self.lead_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        self._generic.clear()
        for lead in self._leads:
            self._append_lead_row(lead)
        table.setSortingEnabled(True)
        self.lead_stack.setCurrentIndex(0 if self._leads else 1)
        self._apply_filters()
        self._refresh_preview_choices()

    def _append_lead_row(self, lead: dict) -> None:
        table = self.lead_table
        row = table.rowCount()
        table.insertRow(row)

        audit = _loads(lead.get("audit_json"))
        gaps = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)]
        score = _int_of(lead.get("opportunity_score"))
        status = _text_of(lead.get("status")).strip() or "new"
        reason = self._generic_reason(lead, audit)

        gap = _text_of(gaps[0].get("title")).strip() if gaps else ""
        if not gap and reason:
            # This lead's email is three paragraphs that could have been written
            # before the crawl. That is what the column has to say, because "no
            # clear gap" reads as a thin prospect rather than as a form letter.
            gap = "form letter — " + _templates.generic_reason(reason)
        values = (
            _text_of(lead.get("name")).strip() or "—",
            _text_of(lead.get("email")).strip(),
            str(score) if score > 0 else "—",
            gap or ("not audited yet" if not audit else "no clear gap"),
            status,
        )
        for column, value in enumerate(values):
            factory = _ScoreItem if column == _COL_SCORE else SortableItem
            item = factory(_clip(value, 60))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            item.setData(_FULL_ROLE, value)
            if len(value) > 60:
                item.setToolTip(value)
            table.setItem(row, column, item)

        table.item(row, _COL_NAME).setData(Qt.UserRole, lead)

        colour, band = _score_band(score)
        score_item = table.item(row, _COL_SCORE)
        score_item.setData(_SCORE_ROLE, score)
        score_item.setForeground(QColor(colour))
        score_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
        if score > 0:
            # A translucent wash rather than a solid fill: the alternating row
            # colours still show through, so the band reads as an annotation on
            # the row instead of replacing it.
            wash = QColor(colour)
            wash.setAlpha(36)
            score_item.setBackground(wash)
            score_item.setToolTip("Opportunity %d/100 — %s prospect, %s"
                                  % (score, band, _plural(len(gaps), "gap")))
        else:
            score_item.setToolTip("Not audited yet")

        if not gaps:
            gap_item = table.item(row, _COL_GAP)
            gap_item.setForeground(QColor(_RED if reason else _DIM))
            if reason:
                gap_item.setToolTip(
                    "Nothing is known about this business, so the email says "
                    "nothing about it: %s. Filter to Generic email to review or "
                    "exclude these before sending."
                    % _templates.generic_reason(reason))
        table.item(row, _COL_STATUS).setForeground(
            QColor(_STATUS_COLOURS.get(status, _MUTED)))

    def _generic_reason(self, lead: dict, audit: dict) -> str:
        """Why this lead's email would be a form letter, "" when it would not.

        Cached per lead: the answer comes from `core.templates`, so it is the
        same answer the send loop renders, and asking it once per row keeps the
        status filter free.

        A lead nobody has audited yet is not answered at all. Its email would be
        generic today, but the plan audits it first, and calling it a form
        letter before the crawl has run is a guess dressed as a fact.
        """
        lead_id = _int_of(lead.get("id"))
        if lead_id in self._generic:
            return self._generic[lead_id]
        reason = ""
        if audit:
            try:
                ok, reason = _templates.personalisation(
                    lead, audit, _loads(lead.get("ai_json")), self._profile(),
                    self.settings)
                reason = "" if ok else reason
            except Exception:
                reason = ""
        self._generic[lead_id] = reason
        return reason

    def _on_search_changed(self, text: str) -> None:
        self._search = _text_of(text).strip().lower()
        self._apply_filters()

    def _apply_filters(self) -> None:
        wanted = _STATUS_FILTERS[max(0, self.status_filter.currentIndex())][1]
        table = self.lead_table
        visible = 0
        for row in range(table.rowCount()):
            lead = self._lead_at(row)
            status = _text_of(lead.get("status")).strip() or "new"
            if wanted.startswith("~"):
                generic = bool(self._generic.get(_int_of(lead.get("id"))))
                hide = generic != (wanted == "~generic")
            else:
                hide = bool(wanted) and status != wanted
            hide = hide or (self._search and not self._row_matches(row, self._search))
            table.setRowHidden(row, bool(hide))
            visible += 0 if hide else 1

        self._refresh_lead_counts(visible)
        self._refresh_audit_button()

    def _row_matches(self, row: int, needle: str) -> bool:
        table = self.lead_table
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is None:
                continue
            full = item.data(_FULL_ROLE)
            if needle in (full if isinstance(full, str) else item.text()).lower():
                return True
        return False

    def _refresh_lead_counts(self, visible: int) -> None:
        total = len(self._leads)
        buckets: dict[str, int] = {}
        for lead in self._leads:
            key = _text_of(lead.get("status")).strip() or "new"
            buckets[key] = buckets.get(key, 0) + 1
        audited = total - buckets.get("new", 0)

        parts = [_plural(total, "lead")]
        if visible != total:
            parts = ["%d of %d shown" % (visible, total)]
        parts.append("%d audited" % audited)
        generic = sum(1 for reason in self._generic.values() if reason)
        if generic:
            parts.append("%d generic" % generic)
        for key, label in (("queued", "queued"), ("sent", "sent"),
                           ("replied", "replied"), ("suppressed", "suppressed")):
            if buckets.get(key):
                parts.append("%d %s" % (buckets[key], label))
        self.lead_counts.setText(" · ".join(parts))

    def _lead_at(self, row: int) -> dict:
        item = self.lead_table.item(row, _COL_NAME)
        data = item.data(Qt.UserRole) if item is not None else None
        return data if isinstance(data, dict) else {}

    def _selected_leads(self) -> list[dict]:
        rows = {index.row() for index in self.lead_table.selectedIndexes()}
        return [self._lead_at(row) for row in sorted(rows)
                if not self.lead_table.isRowHidden(row)]

    def _target_leads(self) -> list[dict]:
        """Selection if there is one, otherwise everything the filters show."""
        chosen = self._selected_leads()
        if chosen:
            return chosen
        return [self._lead_at(row) for row in range(self.lead_table.rowCount())
                if not self.lead_table.isRowHidden(row)]

    def _on_lead_selection_changed(self) -> None:
        self._refresh_audit_button()
        chosen = self._selected_leads()
        if len(chosen) == 1:
            self._select_preview_lead(_int_of(chosen[0].get("id")))

    def _refresh_audit_button(self) -> None:
        count = len(self._selected_leads())
        targets = self._target_leads()
        if count:
            self.audit_btn.setText("Audit selected (%d)" % count)
        else:
            self.audit_btn.setText("Audit all (%d)" % len(targets))
        self.audit_btn.setEnabled(bool(targets) and not self._auditing)
        if self._auditing:
            self.audit_btn.setToolTip("A crawl is already running")
        elif not targets:
            self.audit_btn.setToolTip(
                "Nothing to crawl yet — import a CSV or scrape a city first")
        else:
            # Says which model will write the personalised lines, because that
            # choice lives in Settings and shows up nowhere else on this screen.
            self.audit_btn.setToolTip(
                "Crawl each website, score the automation gaps and write the "
                "personalised lines (%s). Nothing is sent." % self._ai_summary())

    def _on_lead_double_clicked(self, row: int, _column: int) -> None:
        lead = self._lead_at(row)
        url = _text_of(lead.get("website")).strip() or _text_of(lead.get("maps_link")).strip()
        if url:
            webbrowser.open(url if "://" in url else "https://" + url)

    def _show_lead_menu(self, pos) -> None:
        index = self.lead_table.indexAt(pos)
        if not index.isValid():
            return
        lead = self._lead_at(index.row())
        if not lead:
            return

        menu = QMenu(self)
        website = _text_of(lead.get("website")).strip()
        email = _text_of(lead.get("email")).strip()
        if website:
            menu.addAction("Open website", lambda: webbrowser.open(
                website if "://" in website else "https://" + website))
        if lead.get("maps_link"):
            menu.addAction("Open in Google Maps",
                           lambda: webbrowser.open(_text_of(lead.get("maps_link"))))
        menu.addSeparator()
        if email:
            menu.addAction("Copy email", lambda: self._copy(email))
        if lead.get("name"):
            menu.addAction("Copy business name", lambda: self._copy(_text_of(lead.get("name"))))
        menu.addAction("Preview this email", lambda: self._preview_lead(lead))
        menu.addSeparator()
        menu.addAction("Suppress (never contact)", lambda: self._suppress(lead))
        menu.exec_(self.lead_table.viewport().mapToGlobal(pos))

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(_text_of(text))
        self.lead_status.setText("Copied to clipboard")

    def _suppress(self, lead: dict) -> None:
        email = _text_of(lead.get("email")).strip()
        if not email:
            return
        _db.suppress(self.conn, email, "suppressed from the leads table")
        self._reload_leads()
        self._refresh_stats()
        self._toast("%s will never be contacted again, and anything already "
                    "queued for them has been cancelled." % email)

    def _preview_lead(self, lead: dict) -> None:
        self._select_preview_lead(_int_of(lead.get("id")))
        self._goto_tab(1)

    # ── Leads: auditing ──────────────────────────────────────────────────────

    def _on_audit_clicked(self) -> None:
        if self._auditing:
            return
        if not self._retire(self.audit_worker):
            self._toast("The last crawl is still finishing. Press Audit again "
                        "in a moment.")
            return
        leads = self._target_leads()
        if not leads:
            self._toast("There are no leads to audit yet. Import a CSV, or "
                        "scrape a city on the Home screen.")
            return

        self._auditing = True
        self.audit_btn.setEnabled(False)
        self.lead_progress.setRange(0, max(1, len(leads)))
        self.lead_progress.setValue(0)
        self.lead_progress.show()
        self.lead_status.setText("Auditing %s…" % _plural(len(leads), "site"))

        worker = AuditWorker(leads, self.settings, self._template_id())
        worker.progress_signal.connect(self._on_audit_progress)
        worker.lead_signal.connect(self._on_lead_audited)
        worker.log_signal.connect(self._on_audit_log)
        worker.done_signal.connect(self._on_audit_done)
        self.audit_worker = worker
        worker.start()

    def _on_audit_progress(self, done: int, total: int) -> None:
        self.lead_progress.setRange(0, max(1, total))
        self.lead_progress.setValue(done)

    def _on_audit_log(self, message: str, level: str) -> None:
        if level in ("info", "error"):
            self.lead_status.setText(_clip(message, 90))

    def _on_lead_audited(self, lead: dict) -> None:
        """Rewrite one row in place so scores land while the run continues."""
        if not isinstance(lead, dict):
            return
        lead_id = _int_of(lead.get("id"))
        for index, existing in enumerate(self._leads):
            if _int_of(existing.get("id")) == lead_id:
                self._leads[index] = lead
                break
        row = self._find_row(lead_id)
        if row < 0:
            return

        table = self.lead_table
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        table.removeRow(row)
        self._append_lead_row(lead)
        # The rebuilt row lands at the bottom; sorting puts it back where the
        # user's chosen column says it belongs.
        table.setSortingEnabled(sorting)
        self._apply_filters()

    def _find_row(self, lead_id: int) -> int:
        for row in range(self.lead_table.rowCount()):
            if _int_of(self._lead_at(row).get("id")) == lead_id:
                return row
        return -1

    def _retire(self, worker) -> bool:
        """True once `worker` is safe to drop — finished, or never started."""
        if worker is None or not worker.isRunning():
            return True
        return bool(worker.wait(2000))

    def _on_audit_done(self) -> None:
        self._auditing = False
        self.lead_progress.hide()
        self.lead_status.setText("Audit finished")
        self._reload_leads()
        self._refresh_audit_button()
        self._refresh_preview()

    # ── Campaign: profile and preview ────────────────────────────────────────

    def _profile(self) -> dict:
        profile = self.settings.get("sender_profile")
        return profile if isinstance(profile, dict) else {}

    def _accounts(self) -> list[dict]:
        try:
            return _settings.smtp_accounts(self.settings)
        except Exception:
            return []

    def _ai_summary(self) -> str:
        """Which provider writes the personalised lines, in a few words."""
        provider = _text_of(self.settings.get("ai_provider") or "auto").strip().lower()
        return _AI_PROVIDERS.get(provider, "AI: " + provider if provider else "AI: auto")

    def _rules_summary(self) -> str:
        """The pacing rules in force, read off the settings themselves.

        Every one of these lives on the Settings screen and nowhere else, so a
        run that behaves unexpectedly — nothing going out on a Saturday, forty
        messages and then silence — has its explanation on the screen where it
        is happening rather than two screens away.
        """
        days = sorted({_int_of(d, -1) % 7 for d in (self.settings.get("send_days") or [])
                       if isinstance(d, (int, float))})
        when = ", ".join(_DAY_NAMES[d] for d in days) if days else "no days chosen"
        start = _int_of(self.settings.get("send_start_hour"), 9)
        end = _int_of(self.settings.get("send_end_hour"), 17)
        zone = _text_of(self.settings.get("send_timezone") or "local").strip()

        daily = _int_of(self.settings.get("daily_cap_per_account"), 40)
        hourly = _int_of(self.settings.get("hourly_cap_per_account"), 12)
        caps = "up to %d a day" % daily if daily > 0 else "no daily cap"
        caps += " and %d an hour" % hourly if hourly > 0 else " and no hourly cap"

        parts = ["%s, %d:00–%d:00 %s" % (when, start, end, zone), caps + " per account"]
        if self.settings.get("warmup_enabled", True):
            parts.append("new accounts ramping from %d a day"
                         % _int_of(self.settings.get("warmup_start"), 10))
        steps = _int_of(self.settings.get("followup_max_steps"), 2)
        if self.settings.get("followup_enabled", True) and steps > 0:
            parts.append("%s %d days apart" % (_plural(steps, "follow-up"),
                                               _int_of(self.settings.get("followup_gap_days"), 4)))
        else:
            parts.append("no follow-ups")
        return " · ".join(parts)

    def _profile_problems(self) -> list[tuple[str, str]]:
        """(what is missing, what it costs) for every gap in the sender profile.

        Both halves, because a warning without its consequence is either ignored
        or obeyed for the wrong reason. The first entry is the only one that can
        end a campaign on its own — a message needs an address to leave from —
        and it says so rather than being dressed the same as the other two.
        """
        profile = self._profile()
        problems = []
        if not self._accounts():
            problems.append(("no Gmail account is set up to send from",
                             "nothing can be queued until one is added in Settings"))
        if not _text_of(profile.get("sender_name")).strip():
            problems.append(("your name is missing from the sign-off",
                             "the email closes with a company and no person behind it"))
        if not _text_of(profile.get("postal_address")).strip():
            problems.append(("no postal address for the footer",
                             "CAN-SPAM requires one, and filters read a missing "
                             "address as bulk mail"))
        return problems

    def _profile_gate(self, verb: str) -> bool:
        """May this run go ahead? Asks first when the profile is unfinished.

        `require_profile_complete` decides whether the question is asked at all,
        never whether the answer may be yes: on, the user is shown what is
        missing and what it costs and can still choose `<verb> anyway`; off, the
        same list is printed as advice and the run starts. The Sender profile
        card carries the identical problems either way, so nothing about a
        campaign is decided in a dialog the user cannot get back to.
        """
        problems = self._profile_problems()
        if not problems:
            return True

        listed = "; ".join(problem for problem, _cost in problems)
        if not self.settings.get("require_profile_complete", True):
            self._toast("%s with an unfinished profile: %s. Warnings only — "
                        "switch the check back on in Settings." % (verb.capitalize(), listed))
            return True

        tail = ("You can go ahead. Every one of these makes the spam folder more "
                "likely." if self._accounts() else
                "You can go ahead, but with no account to send from there is "
                "nothing to queue the mail on.")
        go, stop_asking = self._ask(
            "%s with an unfinished profile?" % verb.capitalize(),
            "The sender profile is missing:\n\n%s\n\n%s"
            % ("\n".join("  •  %s — %s" % (problem, cost) for problem, cost in problems),
               tail),
            "%s anyway" % verb.capitalize())
        if stop_asking:
            self._remember("require_profile_complete", False)
            self._toast("This screen will warn about the sender profile from now "
                        "on and never stop you. Settings can put the check back.")
        if not go:
            self.settings_signal.emit()
        return go

    def _ask(self, title: str, body: str, proceed: str) -> tuple[bool, bool]:
        """A blocking question with a way through. (go ahead, stop asking).

        Two buttons and never one: a dialog whose only option is "OK" is a wall
        with a courtesy label on it. The checkbox is here because a user who
        overrides the same warning twice has told us what they want, and hunting
        for the setting that turns it off is a worse use of their afternoon than
        the warning ever saved them.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(body)
        box.setCheckBox(QCheckBox("Do not ask again — warn me, never stop me"))
        settings_btn = box.addButton("Open Settings", QMessageBox.AcceptRole)
        proceed_btn = box.addButton(proceed, QMessageBox.DestructiveRole)
        box.setDefaultButton(settings_btn)
        box.exec_()
        checkbox = box.checkBox()
        return (box.clickedButton() is proceed_btn,
                bool(checkbox is not None and checkbox.isChecked()))

    def _remember(self, key: str, value) -> None:
        """Write one setting straight to disk from here.

        The Settings screen re-reads the file every time it opens, so a switch
        thrown from this screen is the same switch when the user goes looking
        for it. A store that will not take it leaves the choice live for this
        session rather than taking the screen down.
        """
        self.settings[key] = value
        try:
            _settings.save_settings(self.settings)
        except Exception:
            pass

    def _refresh_profile(self) -> None:
        profile = self._profile()
        lines = []
        company = _text_of(profile.get("company")).strip()
        who = ", ".join(p for p in (_text_of(profile.get("sender_name")).strip(),
                                    _text_of(profile.get("sender_title")).strip()) if p)
        if who:
            lines.append(html.escape(who))
        if company:
            lines.append(html.escape(company))
        accounts = self._accounts()
        if accounts:
            lines.append("Sending from " + html.escape(
                ", ".join(a["email"] for a in accounts[:3])))

        problems = self._profile_problems()
        for problem, _cost in problems:
            lines.append(_rich("Needs attention: " + problem, _RED))
        self.profile_summary.setText("<br>".join(lines) or
                                     _rich("Nothing set up yet.", _RED))
        self.profile_fix_btn.setVisible(bool(problems))
        self.profile_fix_btn.setText("Fix %s in Settings" % _plural(len(problems), "thing")
                                     if problems else "Open Settings")

        steps = _int_of(self.settings.get("followup_max_steps"), 2)
        gap = _int_of(self.settings.get("followup_gap_days"), 4)
        if self.settings.get("followup_enabled", True) and steps > 0:
            self.followup_hint.setText(
                "%s queued alongside it, %d days apart, on the same thread."
                % (_plural(steps, "follow-up"), gap))
        else:
            self.followup_hint.setText("Follow-ups are switched off in Settings.")

    def _refresh_templates(self) -> None:
        """Rebuild the template list, keeping the chosen one where it survives.

        The catalogue is edited elsewhere in the app, so a template written a
        minute ago has to be pickable here without restarting — a screen holding
        the list it was built with makes the copy the user just wrote the one
        campaign they cannot send.
        """
        combo = self.template_combo
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for tpl in _templates.templates_for_step(0):
            combo.addItem(_text_of(tpl.name).strip() or _text_of(tpl.id), tpl.id)
        index = combo.findData(previous) if previous is not None else -1
        if index < 0 and combo.count():
            index = 0
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setEnabled(combo.count() > 0)
        combo.setToolTip(
            "The first email each lead receives; follow-ups are chosen for you"
            if combo.count() else "This build has no first-touch template")

    def _refresh_preview_choices(self) -> None:
        combo = self.preview_combo
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for lead in self._leads[:500]:
            label = "%s — %s" % (_clip(_text_of(lead.get("name")).strip() or "Unnamed", 34),
                                 _text_of(lead.get("email")).strip())
            combo.addItem(label, _int_of(lead.get("id")))
        if previous is not None:
            index = combo.findData(previous)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setEnabled(combo.count() > 0)
        combo.setToolTip("" if combo.count() else
                         "Import leads on the Leads tab and they appear here")
        self._refresh_preview()

    def _select_preview_lead(self, lead_id: int) -> None:
        lead_id = _int_of(lead_id)
        index = self.preview_combo.findData(lead_id)
        if index < 0:
            # Past the combo's cap. Selecting a lead on the Leads tab must still
            # preview that lead rather than silently previewing someone else.
            lead = next((l for l in self._leads if _int_of(l.get("id")) == lead_id), None)
            if lead is None:
                return
            self.preview_combo.insertItem(
                0, "%s — %s" % (_clip(_text_of(lead.get("name")).strip() or "Unnamed", 34),
                                _text_of(lead.get("email")).strip()), lead_id)
            index = 0
        if index != self.preview_combo.currentIndex():
            self.preview_combo.setCurrentIndex(index)

    def _template_id(self) -> str:
        return _text_of(self.template_combo.currentData())

    def _first_touch_template(self):
        """The chosen template, or any first touch, or None.

        The combo is filled from `templates_for_step(0)`, so a chosen id that no
        longer resolves means the catalogue moved under a screen that was
        already open. Falling back to a real first touch keeps the preview
        drawing and the campaign preparable; None means there is no copy in the
        build at all, which is not something the user did.
        """
        template = _templates.get_template(self._template_id())
        if template is not None:
            return template
        options = _templates.templates_for_step(0)
        return options[0] if options else None

    def _preview_target(self) -> dict:
        lead_id = _int_of(self.preview_combo.currentData())
        for lead in self._leads:
            if _int_of(lead.get("id")) == lead_id:
                return lead
        return self._leads[0] if self._leads else {}

    def _refresh_footer_hint(self) -> None:
        """Say what the footer under the preview does and does not carry.

        The sentence used to promise an unsubscribe line and an address in every
        message. Both are switches now, so the promise is read off the switches:
        a preview that shows a shorter footer than the reader expects has to be
        the preview saying so, not a surprise found in a spam folder later.
        """
        missing = [name for name, key in (("unsubscribe line", "append_unsubscribe"),
                                          ("postal address", "append_postal_address"))
                   if not self.settings.get(key, True)]
        note = ("This is the message as it will be delivered, footer and "
                "unsubscribe line included. The follow-ups reuse the same details.")
        if missing:
            note = ("This is the message as it will be delivered. The %s %s switched "
                    "off in Settings, so %s left out of the footer — more of this "
                    "campaign will land in spam. Every message still carries the "
                    "invisible List-Unsubscribe header."
                    % (" and the ".join(missing), "is" if len(missing) == 1 else "are",
                       "it is" if len(missing) == 1 else "they are"))
        self.preview_hint.setText(note)

    def _refresh_preview(self) -> None:
        self._refresh_footer_hint()
        lead = self._preview_target()
        if not lead:
            self.subject_label.setText("—")
            self.subject_count.setText("")
            self.preview_meta.setText("")
            self._show_paper(
                "Import some leads and this pane shows the exact message each "
                "one would receive, rendered with their own business name, "
                "their own headline gap and your footer.")
            return

        template = self._first_touch_template()
        if template is None:
            self._show_paper(
                "This build has no first-touch template to preview. Reinstalling "
                "restores them; nothing you did caused this.")
            return

        audit = _loads(lead.get("audit_json"))
        ai = _loads(lead.get("ai_json"))
        ctx = _campaign.apply_compliance(
            _templates.build_context(lead, audit, ai, self._profile(), self.settings),
            self.settings)
        subject, body_text, body_html = _templates.render(template, ctx)

        if not subject or not body_text:
            self.subject_label.setText("—")
            self.subject_count.setText("")
            self._show_paper(
                "This lead produced no usable copy. Audit it first — the "
                "template needs at least a business name to write a subject.")
            return

        # The renderer already strips surviving tokens; this is the second lock
        # on the same door, because the cost of being wrong once is a stranger
        # reading "Hi {{first_name}}".
        leak = _TOKEN_RE.search(subject) or _TOKEN_RE.search(body_text) or \
            _TOKEN_RE.search(body_html)
        if leak:
            self.subject_label.setText("—")
            self.subject_count.setText(_rich("unresolved field", _RED))
            self._show_paper(
                "This message still contains an unresolved merge field (%s), so "
                "it is not being shown and would not be sent. Fill in the sender "
                "profile in Settings, or audit this lead, and check again."
                % leak.group(0))
            return

        self.subject_label.setText(subject)
        over = len(subject) > _templates.SUBJECT_MAX
        self.subject_count.setText(_rich(
            "%d / %d characters" % (len(subject), _templates.SUBJECT_MAX),
            _RED if over else _GREEN))

        accounts = self._accounts()
        account = accounts[0] if accounts else {}
        sender = _text_of(account.get("display_name")).strip() or \
            _text_of(self._profile().get("sender_name")).strip()
        from_line = "%s <%s>" % (sender, account.get("email", "no account yet")) \
            if sender else _text_of(account.get("email")) or "no account yet"
        self.preview_meta.setText("To %s <%s>  ·  From %s" % (
            _clip(_text_of(lead.get("name")).strip() or "there", 30),
            _text_of(lead.get("email")).strip(), _clip(from_line, 46)))

        if self.view_group.checkedId() == 1:
            self.preview.setHtml(body_html)
        else:
            self.preview.setHtml(self._as_paper(body_text))
        self._paint_paper()

    def _as_paper(self, body_text: str) -> str:
        """Plain text laid out as the recipient's mail client would show it.

        Every colour and size is inline: the widget's own QSS is written for a
        dark app, and an email preview that does not look like an email is not
        a preview.
        """
        blocks = []
        for para in re.split(r"\n\s*\n", _text_of(body_text).strip()):
            lines = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
            if lines:
                blocks.append('<p style="margin:0 0 14px 0;">%s</p>' % "<br>".join(lines))
        return ('<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,'
                'Arial,sans-serif;font-size:15px;line-height:1.6;color:#1A1A1A;">'
                '%s</div>' % "".join(blocks))

    def _show_paper(self, message: str) -> None:
        self.preview.setHtml(
            '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,'
            'Arial,sans-serif;font-size:14px;line-height:1.6;color:#6A6A6A;">'
            "%s</div>" % html.escape(message))
        self._paint_paper()

    def _paint_paper(self) -> None:
        """Paint the document itself white.

        The QSS rule for `#email_paper` does the same for the widget, but the
        preview has to be readable whether or not that rule has landed — the
        message body carries its own near-black text colour, and on the app's
        dark surface it would be invisible.
        """
        frame = self.preview.document().rootFrame()
        fmt = frame.frameFormat()
        fmt.setBackground(QColor("#FFFFFF"))
        fmt.setMargin(18)
        frame.setFrameFormat(fmt)

    # ── Campaign: planning ───────────────────────────────────────────────────

    def _on_prepare_clicked(self) -> None:
        if self._planning or self._auditing or not self._retire(self.plan_worker):
            self._toast("A crawl is still running — this starts the moment it "
                        "finishes, and nothing is lost by waiting.")
            return

        leads = self._target_leads()
        if not leads:
            self._goto_tab(0)
            self._toast("Import some leads before preparing a campaign.")
            return

        if not self._profile_gate("prepare"):
            return

        template = self._first_touch_template()
        if template is None:
            self._toast("There is no first-touch template to write from, so this "
                        "install has no copy. Reinstalling restores them.")
            return

        name = "%s · %s · %s" % (template.name, _plural(len(leads), "lead"),
                                 datetime.now().strftime("%d %b %H:%M"))
        campaign_id = _db.create_campaign(self.conn, name, template.id,
                                          self._profile(), self.settings)
        if not campaign_id:
            self._toast("Could not create the campaign — the database is unavailable.")
            return

        self._campaign_id = campaign_id
        self._planning = True
        self.prepare_btn.setEnabled(False)
        self.goto_sending_btn.hide()
        self.plan_progress.setRange(0, max(1, len(leads)))
        self.plan_progress.setValue(0)
        self.plan_progress.show()
        self.plan_summary.setText("Auditing and queueing %s. This crawls each "
                                  "website, so it takes a while."
                                  % _plural(len(leads), "lead"))

        worker = _PlanWorker(campaign_id, leads, template.id, self.settings)
        worker.progress_signal.connect(self._on_plan_progress)
        worker.plan_signal.connect(self._on_plan_ready)
        worker.finished.connect(self._on_plan_finished)
        self.plan_worker = worker
        worker.start()

    def _on_plan_progress(self, done: int, total: int, message: str) -> None:
        self.plan_progress.setRange(0, max(1, total))
        self.plan_progress.setValue(done)
        self.plan_summary.setText(html.escape("%d of %d — %s" % (done, total, _clip(message, 60))))

    def _on_plan_ready(self, plan: dict) -> None:
        self._plan = plan if isinstance(plan, dict) else {}
        self.plan_progress.hide()
        error = _text_of(self._plan.get("error"))
        queued = _int_of(self._plan.get("queued"))

        if error and not queued:
            self.plan_summary.setText(_rich("Nothing was queued: %s." % error, _RED))
            return

        summary = self._plan_sentence(self._plan)
        if self._plan.get("cancelled"):
            # Stopped part-way. What is queued is real, so say so rather than
            # presenting a partial plan as the whole campaign.
            summary = _rich("Stopped before the whole list was planned.", _RED) \
                + "<br>" + summary
        self.plan_summary.setText(summary)
        self.goto_sending_btn.show()
        self._refresh_campaigns()
        self._reload_leads()
        self._refresh_stats()

    def _plan_sentence(self, plan: dict) -> str:
        queued = _int_of(plan.get("queued"))
        days = max(1, _int_of(plan.get("days")))
        accounts = [a for a in (plan.get("accounts") or []) if a]
        cap = _int_of(plan.get("daily_cap"))

        head = "%s across %s" % (_plural(queued, "email"), _plural(days, "day"))
        if cap and accounts:
            head += ", %d/day from %s" % (cap, _plural(len(accounts), "account"))
        first = plan.get("first_send")
        if first:
            head += ", first send %s" % _clock(first)

        lines = [_rich(head + ".", _TEXT)]
        followups = _int_of(plan.get("followups"))
        if followups:
            lines.append("Plus %s on the same threads." % _plural(followups, "follow-up"))

        generic = _int_of(plan.get("generic"))
        if generic:
            # The one number a user cannot get anywhere else. Two hundred form
            # letters sent in the belief that they are personal is the failure
            # the accept-list in `core.templates` trades personalisation to
            # avoid, and it is only a good trade if it is visible.
            lines.append(_rich(
                "%d of %s could not be personalised — %s."
                % (generic, _plural(queued, "email"),
                   _generic_sentence(plan.get("generic_reasons"))), _RED))
        skipped = _int_of(plan.get("skipped"))
        if skipped:
            lines.append("%d skipped — %s."
                         % (skipped, html.escape(_reason_list(plan.get("skip_reasons")))))
        last = plan.get("last_send")
        if last:
            lines.append("Last message lands %s." % _clock(last))
        return "<br>".join(lines)

    def _on_plan_finished(self) -> None:
        self._planning = False
        self.prepare_btn.setEnabled(True)

    # ── Sending ──────────────────────────────────────────────────────────────

    def _refresh_mode(self) -> None:
        dry = bool(self.settings.get("dry_run", True))
        # The loud styling belongs to the mode that mails strangers. Dressing
        # the rehearsal in red and the live run in the same grey outline as the
        # Home and Settings buttons had it exactly backwards: the state you can
        # take back was shouting and the one you cannot was furniture.
        self.mode_badge.setText("DRY RUN" if dry else "LIVE")
        self.mode_badge.setObjectName("rehearsal" if dry else "live")
        self._restyle(self.mode_badge)

        # Short enough that a narrow window cannot elide it down to "DRY R…" —
        # the sentence underneath carries the detail.
        if dry:
            self.send_badge.setText("DRY RUN — nothing is actually sent")
            self.send_badge.setObjectName("rehearsal")
            self.send_note.setText(
                "Messages are rendered, queued and marked sent, but no SMTP "
                "connection is opened and nothing leaves this machine. Turn "
                "dry run off in Settings when you are ready to send for real.\n"
                "%s. All of it is editable in Settings." % self._rules_summary())
        else:
            self.send_badge.setText("LIVE — real emails will be sent")
            self.send_badge.setObjectName("live")
            self.send_note.setText(
                "Sending from %s.\n%s. All of it is editable in Settings."
                % (_plural(len(self._accounts()), "account"), self._rules_summary()))
        self._restyle(self.send_badge)

    def _restyle(self, widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _refresh_campaigns(self) -> None:
        rows = _db.list_campaigns(self.conn)
        combo = self.campaign_combo
        previous = self._campaign_id or _int_of(combo.currentData())
        combo.blockSignals(True)
        combo.clear()
        for row in rows:
            combo.addItem("%s  ·  %s" % (_clip(_text_of(row.get("name")), 52),
                                         _text_of(row.get("status")) or "draft"),
                          _int_of(row.get("id")))
        index = combo.findData(previous) if previous else -1
        if index < 0 and combo.count():
            index = 0
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._campaign_id = _int_of(combo.currentData())
        self._refresh_send_controls()

    def _on_campaign_changed(self, _index: int) -> None:
        self._campaign_id = _int_of(self.campaign_combo.currentData())
        self._refresh_stats()
        self._refresh_send_controls()

    def _refresh_send_controls(self) -> None:
        stats = self._stats()
        queued = _int_of(stats.get("queued"))
        total = _int_of(stats.get("total"))
        sent = _int_of(stats.get("sent"))

        # A control that cannot be pressed has to say what would let it be. The
        # status line underneath says it once for the screen; the tooltip says
        # it where the pointer already is.
        if self._sending:
            blocked = "This campaign is already sending — Pause or Stop first"
        elif not self._campaign_id:
            blocked = "Prepare a campaign on the Campaign tab and it appears here"
        elif queued <= 0:
            blocked = "Nothing is queued — prepare a campaign to put messages in the queue"
        else:
            blocked = ""
        self.start_btn.setEnabled(not blocked)
        self.start_btn.setToolTip(blocked or (
            "Work through this campaign's queue on the schedule in Settings"))
        self.pause_btn.setEnabled(self._sending)
        self.pause_btn.setToolTip("Hold the run where it is; the queue keeps its times"
                                  if self._sending else "Nothing is running")
        self.stop_btn.setEnabled(self._sending)
        self.stop_btn.setToolTip(
            "Finish the message in flight and stop; whatever is queued stays queued"
            if self._sending else "Nothing is running")
        self.campaign_combo.setEnabled(not self._sending)
        self.campaign_combo.setToolTip(
            "Stop the run to switch campaigns" if self._sending else "")

        self.send_progress.setRange(0, max(1, total))
        self.send_progress.setValue(total - queued if total else 0)
        if not self._campaign_id:
            self.send_status.setText("No campaign yet — prepare one on the Campaign tab")
        elif self._sending:
            self.send_status.setText(
                "Paused after %d of %d" % (sent, total) if self._paused
                else "Sending — %d of %d done" % (sent, total))
        elif queued:
            self.send_status.setText("%s queued, %d already sent" % (_plural(queued, "message"), sent))
        else:
            self.send_status.setText("Nothing left in this campaign's queue")

    def _on_start_clicked(self) -> None:
        if self._sending:
            return
        if not self._retire(self.send_worker):
            self._toast("The last run is still closing its connection. Press "
                        "Start again in a moment.")
            return
        if not self._campaign_id:
            self._goto_tab(1)
            self._toast("There is no campaign yet. Prepare one here and it "
                        "appears on the Sending tab.")
            return
        if _int_of(self._stats().get("queued")) <= 0:
            self._toast("This campaign has nothing queued. Prepare one first.")
            return
        if not self._profile_gate("send"):
            return

        self._clear_log()
        self._rehearsed.clear()
        worker = OutreachWorker(self._campaign_id, self.settings,
                                dry_run=bool(self.settings.get("dry_run", True)))
        worker.log_signal.connect(self._append_log)
        worker.progress_signal.connect(self._on_send_progress)
        worker.message_sent_signal.connect(self._on_message_sent)
        worker.stats_signal.connect(self._on_stats_signal)
        worker.error_signal.connect(lambda msg: self._append_log(msg, "error"))
        worker.done_signal.connect(self._on_send_done)
        self.send_worker = worker

        self._sending = True
        self._paused = False
        self.pause_btn.setText("Pause")
        self._refresh_send_controls()
        worker.start()

    def _on_pause_clicked(self) -> None:
        if self.send_worker is None or not self._sending:
            return
        if self._paused:
            self.send_worker.resume()
            self._paused = False
            self.pause_btn.setText("Pause")
        else:
            self.send_worker.pause()
            self._paused = True
            self.pause_btn.setText("Resume")
        self._refresh_send_controls()

    def _on_stop_clicked(self) -> None:
        if self.send_worker is None or not self._sending:
            return
        self.send_worker.stop()
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.send_status.setText("Stopping — finishing the message in flight…")

    def _on_send_progress(self, done: int, total: int) -> None:
        self.send_progress.setRange(0, max(1, total))
        self.send_progress.setValue(done)

    def _on_message_sent(self, row: dict) -> None:
        if not isinstance(row, dict):
            return
        if getattr(self.send_worker, "dry_run", False):
            key = _text_of(row.get("account_email")).strip().lower()
            if key:
                self._rehearsed[key] = self._rehearsed.get(key, 0) + 1
                self._refresh_accounts()
        lead = _db.get_lead(self.conn, _int_of(row.get("lead_id")))
        who = _text_of(lead.get("name")).strip() or _text_of(lead.get("email")).strip()
        step = _int_of(row.get("step"))
        label = "follow-up %d" % step if step else "first touch"
        self._append_log("%s — %s" % (who or "lead", label), "done")

    def _on_stats_signal(self, stats: dict) -> None:
        if isinstance(stats, dict):
            self._paint_stats(stats)
        self._refresh_send_controls()
        self._refresh_accounts()

    def _on_send_done(self) -> None:
        self._sending = False
        self._paused = False
        self.pause_btn.setText("Pause")
        self._reload_leads()
        self._refresh_stats()
        self._refresh_campaigns()

    def _clear_log(self) -> None:
        """Empty the activity list back to its placeholder.

        Same treatment as the suppression list: an unexplained bordered box is
        the one surface on this screen that would not say what belongs in it.
        """
        self.log_list.clear()
        item = QListWidgetItem(
            "Nothing sent yet. Press Start sending and each message appears "
            "here as it goes out.")
        item.setFlags(Qt.NoItemFlags)
        self.log_list.addItem(item)
        self._log_empty = True

    def _append_log(self, message: str, level: str = "info") -> None:
        if self._log_empty:
            self.log_list.clear()
            self._log_empty = False
        item = QListWidgetItem("%s  %s" % (datetime.now().strftime("%H:%M:%S"),
                                           _clip(message, 120)))
        item.setForeground(QColor({"error": _RED, "done": _GREEN,
                                   "active": _TEXT}.get(level, _MUTED)))
        self.log_list.insertItem(0, item)
        while self.log_list.count() > _LOG_LIMIT:
            self.log_list.takeItem(self.log_list.count() - 1)

    # ── Sending: accounts and countdown ──────────────────────────────────────

    def _refresh_accounts(self) -> None:
        _clear_layout(self.accounts_holder)
        accounts = self._accounts()
        if not accounts:
            self.accounts_holder.addWidget(_hint(
                "No Gmail account yet. Add one in Settings — outreach needs an "
                "address and a Google App Password to send from."))
            return

        zone = self.settings.get("send_timezone")
        # The same warm-up origin the send loop uses. Without it an account with
        # no `warmup_started` would be shown its first-day cap on day nine of the
        # campaign, and the counter would read as stuck.
        ramp = None
        if self._campaign_id:
            try:
                ramp = _campaign.campaign_start_day(self.conn, self._campaign_id, self.settings)
            except Exception:
                ramp = None

        for account in accounts:
            email = _text_of(account.get("email"))
            used = _db.sent_today(self.conn, email, zone)
            rehearsed = self._rehearsed.get(email.strip().lower(), 0)
            cap = max(1, _campaign.account_daily_cap(account, self.settings, ramp_start=ramp))

            row = QVBoxLayout()
            row.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(_muted(_clip(email, 28)))
            head.addStretch()
            counter = QLabel()
            counter.setObjectName("muted")
            counter.setTextFormat(Qt.RichText)
            # Two separate numbers, never one sum: a rehearsal has spent none of
            # this account's real quota and the card must not imply that it has.
            counter.setText(_rich("%d / %d today" % (used, cap),
                                  _RED if used >= cap else _TEXT))
            tip = "Daily cap for this account, warm-up included"
            if rehearsed:
                counter.setText(_rich("%d rehearsed" % rehearsed, _SOFT) +
                                _rich("  ·  ", _DIM) + counter.text())
                tip += ".  %s rehearsed in this dry run — no real quota spent." \
                    % _plural(rehearsed, "message")
            counter.setToolTip(tip)
            head.addWidget(counter)
            row.addLayout(head)

            bar = _thin_bar()
            bar.setRange(0, cap)
            # The rehearsal moves the bar because the bar is what the user
            # watches while the run goes; `used` is what governs the cap.
            bar.setValue(min(used + rehearsed, cap))
            row.addWidget(bar)
            holder = QWidget()
            holder.setLayout(row)
            self.accounts_holder.addWidget(holder)

    def _next_due_ts(self) -> float:
        """When this campaign's next queued message is due. 0.0 if none.

        Read through `due_messages` with a far horizon rather than a query of
        its own, so the countdown and the send loop agree about which row is
        next — including the suppression filter that view applies.
        """
        if not self._campaign_id:
            return 0.0
        horizon = time.time() + 366 * _DAY_SEC
        for row in _db.due_messages(self.conn, horizon, limit=400):
            if _int_of(row.get("campaign_id")) == self._campaign_id:
                try:
                    return float(row.get("scheduled_at") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _on_tick(self) -> None:
        if self.pages.currentIndex() != 2 and not self._sending:
            return
        due = self._next_due_ts()
        if not due:
            self.next_send_label.setText("")
            return

        now = time.time()
        if self._paused:
            self.next_send_label.setText("Paused · next was due %s" % _clock(due))
        elif due > now:
            self.next_send_label.setText("Next send in %s · %s"
                                         % (_countdown(due - now), _clock(due)))
        elif not _campaign.in_send_window(now, self.settings):
            opens = _campaign.next_window_open(now, self.settings)
            self.next_send_label.setText("Outside the sending window · resumes %s"
                                         % _clock(opens))
        elif self._sending:
            self.next_send_label.setText("Sending now")
        else:
            self.next_send_label.setText("%s ready to send" % _plural(
                _int_of(self._stats().get("queued")), "message"))

    # ── Stats ────────────────────────────────────────────────────────────────

    def _stats(self) -> dict:
        if not self._campaign_id:
            return {}
        return _db.campaign_stats(self.conn, self._campaign_id)

    def _refresh_stats(self) -> None:
        self._paint_stats(self._stats())
        self._refresh_personalisation()
        self._refresh_days()
        self._refresh_suppression()
        row = _db.get_campaign(self.conn, self._campaign_id) if self._campaign_id else {}
        self.stats_campaign.setText(_clip(_text_of(row.get("name")), 64) or "No campaign yet")

    def _paint_stats(self, stats: dict) -> None:
        stats = stats if isinstance(stats, dict) else {}
        for key, label in self.tiles.items():
            colour = label.property("band") or _TEXT
            count = _int_of(stats.get(key))
            label.setText(_rich(str(count), colour if count else _DIM, 26, True))

    # A campaign of five hundred is a long list to walk on a tab change, and the
    # answer past a couple of thousand would not change what the user does about
    # it. Each lead past the cap is a rendered context, at a third of a
    # millisecond each.
    _PERSONAL_SCAN_MAX = 2000

    def _personalisation_counts(self) -> tuple[int, int, dict]:
        """(generic, first touches, why) for the campaign on screen.

        Worked out from the leads rather than stored against the messages:
        `core.outreach_db` has no column for it, and the answer is a property of
        the audit the message was rendered from, which is still on the lead.
        Read through `core.templates` so this and the Campaign tab cannot come
        to different conclusions about the same email.
        """
        if not self._campaign_id or self.conn is None:
            return 0, 0, {}
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT leads.* FROM messages JOIN leads "
                "ON leads.id = messages.lead_id "
                "WHERE messages.campaign_id = ? AND messages.step = 0 LIMIT ?",
                (self._campaign_id, self._PERSONAL_SCAN_MAX)).fetchall()
        except Exception:
            return 0, 0, {}

        profile = self._profile()
        counts: dict[str, int] = {}
        for row in rows:
            lead = dict(row)
            try:
                ok, reason = _templates.personalisation(
                    lead, _loads(lead.get("audit_json")), _loads(lead.get("ai_json")),
                    profile, self.settings)
            except Exception:
                continue
            if not ok:
                counts[reason] = counts.get(reason, 0) + 1
        return sum(counts.values()), len(rows), counts

    def _refresh_personalisation(self) -> None:
        generic, total, why = self._personalisation_counts()
        if not total:
            self.personal_note.setText("")
        elif generic:
            self.personal_note.setText(_rich(
                "%d of %s could not be personalised — %s. Those say nothing "
                "about the business they are addressed to beyond its name."
                % (generic, _plural(total, "email"), _generic_sentence(why)), _RED))
        else:
            self.personal_note.setText(_rich(
                "All %s in this campaign say something about the business they "
                "are addressed to." % _plural(total, "email"), _SOFT))
        self.personal_note.setVisible(bool(total))

    def _per_day(self) -> list[tuple[str, int, int]]:
        """(day label, sent, still queued) per day for this campaign.

        Bucketed here rather than in SQL because the day boundary is the user's
        local one, and because `core.outreach_db` exposes no per-day rollup —
        see the note in the handover about adding one.
        """
        if not self._campaign_id or self.conn is None:
            return []
        try:
            rows = self.conn.execute(
                "SELECT status, scheduled_at, sent_at FROM messages "
                "WHERE campaign_id = ?", (self._campaign_id,)).fetchall()
        except Exception:
            return []

        buckets: dict[str, list[int]] = {}
        for row in rows:
            status = _text_of(row["status"])
            when = row["sent_at"] if status == "sent" and row["sent_at"] else row["scheduled_at"]
            try:
                day = datetime.fromtimestamp(float(when or 0.0)).date()
            except (OSError, OverflowError, TypeError, ValueError):
                continue
            slot = buckets.setdefault(day.isoformat(), [0, 0])
            slot[0 if status == "sent" else 1] += 1

        return [(datetime.fromisoformat(day).strftime("%a %d"), sent, queued)
                for day, (sent, queued) in sorted(buckets.items())]

    def _refresh_days(self) -> None:
        days = self._per_day()
        self.day_bars.set_days(days)
        self.day_bars.setVisible(bool(days))
        self.bars_empty.setVisible(not days)

    def _refresh_suppression(self) -> None:
        rows = _db.suppression_list(self.conn)
        self.supp_list.clear()
        for row in rows:
            email = _text_of(row.get("email"))
            reason = _text_of(row.get("reason")).strip()
            item = QListWidgetItem("%s%s" % (email, "  ·  %s" % reason if reason else ""))
            item.setData(Qt.UserRole, email)
            self.supp_list.addItem(item)
        if not rows:
            item = QListWidgetItem("Nobody has unsubscribed or been suppressed yet.")
            item.setFlags(Qt.NoItemFlags)
            self.supp_list.addItem(item)
        self.supp_count.setText(_plural(len(rows), "address"))
        self.unsuppress_btn.setEnabled(bool(rows))
        self.unsuppress_btn.setToolTip(
            "Let a suppressed address be contacted again; nothing is re-queued"
            if rows else "Nobody is on the do-not-contact list")

    def _on_unsuppress_clicked(self) -> None:
        item = self.supp_list.currentItem()
        email = _text_of(item.data(Qt.UserRole)) if item is not None else ""
        if not email:
            self._toast("Select an address to remove first.")
            return
        if not self._unsuppress(email):
            self._toast("Could not remove that address.")
            return
        _db.upsert_lead(self.conn, {"email": email, "status": "new"})
        _db.log_event(self.conn, "unsuppressed", email)
        self._refresh_suppression()
        self._reload_leads()
        self._toast("%s can be contacted again. Nothing was re-queued — prepare "
                    "a new campaign to include them." % email)

    def _unsuppress(self, email: str) -> bool:
        """Drop one address from the do-not-contact list.

        Written against the connection because `core.outreach_db` has
        `suppress` but no inverse; the handover asks for one.
        """
        if self.conn is None:
            return False
        try:
            self.conn.execute("DELETE FROM suppression WHERE email = ?",
                              (_text_of(email).strip().lower(),))
            self.conn.commit()
            return True
        except Exception:
            return False

    # ── Chrome ───────────────────────────────────────────────────────────────

    def _goto_tab(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        button = self._tab_btns.get(index)
        if button is not None:
            button.setChecked(True)
        self._on_tab_changed(index)

    def _on_tab_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 1:
            self._refresh_templates()
            self._refresh_profile()
            self._refresh_preview()
        elif index == 2:
            self._refresh_accounts()
            self._refresh_send_controls()
        elif index == 3:
            self._refresh_stats()

    def _toast(self, message: str) -> None:
        self.toast_label.setText(message)
        self.toast_label.show()
        if self._toast_timer is not None:
            self._toast_timer.stop()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)
        self._toast_timer.start(_TOAST_MS)
