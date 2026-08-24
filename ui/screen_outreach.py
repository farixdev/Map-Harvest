"""Outreach — the screen that turns a scrape into a mailing and watches it go.

Four steps in the order the work happens: Leads (who), Campaign (what they
receive), Sending (the run), Stats (what came back). They are steps, not
categories, which is why each one ends by pointing at the next — and they are
the shell's tabs now, not this screen's. `ui/app.py` draws one bar at
`control.header`; this file hands it `("Leads", "Campaign", "Sending", "Stats")`
through `set_subtabs` and draws no chrome of its own.

Three rules shape the code below.

Nothing slow runs on the GUI thread. Crawling a site, calling a model and
opening an SMTP session are minutes of network time, so they happen in the
workers in `core.campaign`; this file starts them and draws what they emit.

Nothing is previewed that could not be sent. The preview goes through
`core.templates.render` — the same call the send loop makes — and refuses to
draw anything still carrying a `{{token}}`.

Nothing here says no without saying how through, and nothing says yes about a
queue that is not moving. A disabled control carries the sentence that would
re-enable it, and the Sending tab reports what is actually happening rather
than what was true when the campaign was prepared.

Every value this screen paints comes from `ui/theme.py` through
`ui/components.py`. It used to keep a parallel dark-only palette of its own —
eight constants, `_GREEN` among them at the exact value the contract ordered
darkened — and paint with `setForeground`, so the light theme rendered the
Score and Headline-gap columns at 1.02-1.90:1. That palette is gone. What the
audit measured, and what replaced each of it:

  * the lead table gave every surplus pixel to Status. At 2560 Status reached
    1800px for the word "audited" while Headline gap — the column the whole
    audit exists to produce — stayed frozen at 240px with 16 of 20 cells cut
    and 11 of those with no tooltip; at 1280 Status took 510px for a 59px word
    while business names clipped at 200px and 37% of the table was empty. It is
    `components.table()` from a column spec now: Status and Score are `fit` and
    capped in characters, Headline gap is the heaviest `stretch` in the spec,
    and every cut cell answers a hover with the whole of its text.
  * status and score were carried by colour alone, with `bounced`, `failed` and
    `suppressed` at an identical 1.00:1. They are `components.status_pill()` and
    `components.score_badge()`, painted through `_BadgeDelegate` — see its
    docstring for why a delegate and not `setCellWidget`, and for the two
    numbers that decided it.
  * suppressing a lead was permanent, unconfirmed, and silently acted on one
    row of a multi-row selection. It goes through `components.confirm()`, it
    acts on the whole selection, and the toast that reports it carries Undo.
  * the dry-run banner was a full-width 44px QPushButton wearing a dashed
    border — the same component as the 28px header badge, at a different size.
    The shell owns that state now; this screen says what the mode costs in a
    sentence and names the campaign it applies to.
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

from PyQt5.QtCore import QEvent, QPoint, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QRegion
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QProgressBar, QScrollArea, QSizePolicy, QStackedWidget,
    QStyle, QStyleOptionViewItem, QStyledItemDelegate, QTextBrowser,
    QVBoxLayout, QWidget,
)

from core import campaign as _campaign
from core import outreach_db as _db
from core import settings as _settings
from core import templates as _templates
from core.ai import AIClient
from core.campaign import AuditWorker, OutreachWorker, plan_campaign
from ui import components
from ui import theme as _theme
from ui.components import Cell, Column

# ── The email document ───────────────────────────────────────────────────────
# An email is not chrome. It is rendered by the recipient's mail client, which
# is not wearing this app's theme and never will be, so the preview is drawn in
# the light palette whatever the app is in — that is where a near-white page
# and near-black ink are role tokens rather than two hexes chosen by hand.
# `text.primary` on `raised` measures 17.16:1, which is what an inbox looks
# like; painting the document in the dark palette would show the user a message
# their reader will never see.

_PAPER = _theme.theme("light")

# The recipient's font stack, deliberately not a theme token: it names what
# their client will substitute, not what this app wants to be seen in.
_MAIL_FAMILY = ("-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, "
                "sans-serif")

# How wide the paper is allowed to get, in characters. The audit measured this
# preview as the widest object on the screen at 2154px on a 2560px window,
# against the roughly 600px a real inbox renders a message in — a line nobody
# can track back from, and three times what it is being previewed for. A count
# rather than a pixel width for the reason every cap in `components` is a
# count: the thing being sized is text.
_PAPER_CH = 76

# ── The lead table ───────────────────────────────────────────────────────────
# What each column *is*, rather than how many pixels it was handed. `fit` holds
# a short fixed value and is sized to its own content and then capped; `stretch`
# carries meaning and shares what is left over by weight. Both bounds are in
# characters.
#
# The weights are the screen's own priorities and they are the whole of the
# first finding: Headline gap is the heaviest column in the spec because it is
# the one thing the audit pass produces that nobody can get anywhere else, and
# Status is `fit` with a 16-character cap because "suppressed" is eleven
# characters and no window makes it longer.
#
# City and Category are here because they could not be searched at all: the
# filter box reads the lead record, and until this pass the record's city and
# category were not on screen for it to read.

_LEAD_COLUMNS = (
    Column("Business", "stretch", weight=3, min_ch=14, max_ch=36),
    Column("Email", "stretch", weight=3, min_ch=16, max_ch=52),
    Column("City", "fit", min_ch=8, max_ch=20, sample="Scarborough"),
    Column("Category", "fit", min_ch=8, max_ch=22, sample="Roofing contractor"),
    Column("Score", "fit", min_ch=13, max_ch=16, sample="88 · moderate"),
    Column("Headline gap", "stretch", weight=5, min_ch=16, max_ch=80),
    Column("Status", "fit", min_ch=12, max_ch=16, sample="⊘ suppressed"),
)

(_COL_NAME, _COL_EMAIL, _COL_CITY, _COL_CATEGORY, _COL_SCORE, _COL_GAP,
 _COL_STATUS) = range(len(_LEAD_COLUMNS))

# Which lead field each column sorts and searches on. The two badge columns
# sort on the value behind the badge — an em dash compares greater than any
# digit as text, so a Score column sorted as written floats every unaudited
# lead above the best prospect.
_COL_KEYS = {_COL_NAME: "name", _COL_EMAIL: "email", _COL_CITY: "city",
             _COL_CATEGORY: "category", _COL_STATUS: "status"}

# The badge a cell paints, and the value it sorts on. `+ 1` and `+ 2` belong to
# `components` (the untruncated text and the sort key), so this starts at `+ 3`.
_BADGE_ROLE = Qt.UserRole + 3

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
_DAY_SEC = 86400.0
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# How many leads the preview picker and the "who is about to be queued"
# sentence name before they stop counting. Counts, not sizes.
_PREVIEW_CAP = 500
_NAMED_IN_SUMMARY = 3

# How many characters the campaign column and the suppression list are allowed
# to run to. The column has to hold the widest primary button on the screen and
# nothing more; the list holds an address and a reason and nothing more.
_COLUMN_CH = 52
_SUPPRESSION_CH = 64

# `ai_provider`, said the way the Settings screen offers it. Every campaign
# setting the user is likely to want back is edited there; this screen's job is
# to name the ones that shape what it is about to do.
_AI_PROVIDERS = {
    "auto": "AI: Groq, then OpenRouter",
    "groq": "AI: Groq",
    "openrouter": "AI: OpenRouter",
    "off": "AI off — plain templates",
}


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


def _measure(fm: QFontMetrics, chars: int) -> int:
    """The width `chars` characters of this app's own copy take in `fm`.

    The same measurement `components` takes for every capped label and every
    capped column, so a width computed here and a width computed there mean the
    same thing — and taken by calling it rather than by copying it, because a
    second implementation of "how wide is a character" is how the caps drift.
    It reaches a private name only because `components` has not published one;
    see the handover note.
    """
    return components._measure(fm, chars)


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
                     for reason, count in named[:_NAMED_IN_SUMMARY])


def _reason_list(counts, limit: int = _NAMED_IN_SUMMARY) -> str:
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


def _names_of(leads, limit: int = _NAMED_IN_SUMMARY) -> str:
    """"Alpha Plumbing, Zeta Roofing and 41 more" — who a run is about to touch.

    Prepare campaign never said how many leads it was queueing or who they
    were, and it acts on the selection when there is one and on everything the
    filters show when there is not — so the two readings of "prepare" differed
    by hundreds of strangers and the button looked identical either way.
    """
    named = [_text_of(lead.get("name")).strip()
             or _text_of(lead.get("email")).strip() or "an unnamed lead"
             for lead in leads[:limit]]
    if not named:
        return "nobody"
    rest = len(leads) - len(named)
    if rest > 0:
        named.append("%d more" % rest)
    if len(named) == 1:
        return named[0]
    return "%s and %s" % (", ".join(named[:-1]), named[-1])


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


# ── Local layout helpers ─────────────────────────────────────────────────────
# Every layout on this screen states its margins and its spacing in tokens. The
# audit found 36 layouts silently inheriting Qt's default 9px, which is off the
# 4px grid in both directions and is why nothing lined up with anything.


def _rows(owner=None, *, margin="0", spacing="3", t=None):
    box = QVBoxLayout(owner) if owner is not None else QVBoxLayout()
    return _grid(box, t or components.active_theme(), margin, spacing)


def _cols(owner=None, *, margin="0", spacing="3", t=None):
    box = QHBoxLayout(owner) if owner is not None else QHBoxLayout()
    return _grid(box, t or components.active_theme(), margin, spacing)


def _grid(box, t, margin, spacing):
    step = t.space[margin]
    box.setContentsMargins(step, step, step, step)
    box.setSpacing(t.space[spacing])
    return box


def _combo(t) -> QComboBox:
    """A bare dropdown at the toolbar height.

    `components.select()` is the labelled form — a caption, a help line and a
    reserved error line — which is right in a form and three rows too tall in a
    filter bar. The sheet styles `QComboBox` itself, so the only thing set here
    is which height token it takes.
    """
    combo = QComboBox()
    combo.setFixedHeight(t.control["sm"])
    return combo


def _thin_bar(t):
    """The 2px progress rule, at the one length the tokens call a rule.

    Taken here rather than left to the sheet's own `height`, which sets the
    size hint and loses to QProgressBar's font-derived minimum — measured 21px
    against a 2px hint, so an empty bar read as an empty input box.
    """
    bar = QProgressBar()
    bar.setTextVisible(False)
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setFixedHeight(t.space["hair"])
    return bar


class _ElidedLabel(QLabel):
    """A one-line label that shortens with an ellipsis instead of being cut.

    Qt clips a plain QLabel at the widget edge and paints no cue, so the
    Campaign To/From line rendered as "…@gmail.cc" at the default window size
    and stopped dead at "From Sam" at the minimum — with `toolTip()` empty, the
    user had nothing telling them a sender address was missing from the line
    that says which account will send. The full text is always the tooltip, so
    what has been dropped is one hover away.

    `components` has no elided single-line label; see the handover note.
    """

    # The floor is a few characters and an ellipsis: below that the line stops
    # being readable and the tooltip is the only copy that matters anyway.
    _FLOOR_CH = 8

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setObjectName("muted")
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
        # it sits in cannot shrink past it — the same clip, one level up.
        hint = super().minimumSizeHint()
        floor = self.fontMetrics().horizontalAdvance("…") * self._FLOOR_CH
        return QSize(min(hint.width(), floor), hint.height())

    def sizeHint(self) -> QSize:
        # super() first: it polishes the widget, and the sheet's font is what
        # the elision has to be measured against. Reading `fontMetrics()` before
        # that call answers for whatever font the label had before styling.
        height = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full)
                     + components.BORDER * 2, height)

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


# ── Badges in a table cell ───────────────────────────────────────────────────


class _BadgeDelegate(QStyledItemDelegate):
    """Paints `components`' own pill or badge into a cell, one widget per value.

    A delegate rather than `setCellWidget`, and the reason is measured rather
    than argued. Filling a 500-lead table with a `status_pill()` and a
    `score_badge()` per row builds a thousand QLabels and takes **9,692 ms**;
    the same table with plain cells takes 76 ms. This delegate keeps one widget
    per *distinct* value — eleven statuses and four score bands, whatever the
    row count — and renders it into the cell's own painter: **59 ms** to build
    and 11 ms to paint the whole table. That is 164x, and it is the difference
    between a lead list that opens and one that hangs.

    The widgets themselves are still `components.status_pill()` and
    `components.score_badge()` called unchanged, so nothing about how a status
    or a band is coloured, marked or shaped is decided in this file. What is
    decided here is only where the pill sits in the cell. This whole class
    belongs in `ui/components.py` beside the two builders it calls; see the
    handover note.
    """

    def __init__(self, build, parent=None):
        super().__init__(parent)
        self._build = build
        self._made: dict = {}

    def badge(self, key):
        """The one widget for `key`, built on first sight and kept."""
        made = self._made.get(key)
        if made is None:
            made = self._build(key)
            made.ensurePolished()
            made.adjustSize()
            self._made[key] = made
        return made

    def paint(self, painter, option, index) -> None:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        # The row's own ground, its selection and its rail still come from the
        # sheet; only the text is taken away, because the badge replaces it.
        style_option.text = ""
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, style_option, painter, widget)

        key = index.data(_BADGE_ROLE)
        if key is None:
            return
        badge = self.badge(key)
        size = badge.sizeHint()
        badge.resize(size)
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(option.rect.x() + components.BORDER,
                          option.rect.y()
                          + (option.rect.height() - size.height()) // 2)
        badge.render(painter, QPoint(), QRegion(), QWidget.DrawChildren)
        painter.restore()


# ── Per-day bars ─────────────────────────────────────────────────────────────

class _DayBars(QWidget):
    """Sent and still-queued volume per day, painted directly.

    A bar row is a handful of rectangles. Pulling in a charting dependency to
    draw them would cost more than it saves, and painted here they take the
    accent and the rule straight out of the theme, so the chart changes palette
    with everything else.
    """

    # How many days fit before the row stops being readable, and how wide a bar
    # and its slot may get. Counts and token multiples, so a dense theme draws a
    # denser chart.
    _DAYS = 14

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._t = t
        self._days: list[tuple[str, int, int]] = []
        self.setMinimumHeight(t.space["9"] * 2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_days(self, days) -> None:
        self._days = list(days or [])[-self._DAYS:]
        self.update()

    def paintEvent(self, event) -> None:
        if not self._days:
            return
        t = self._t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = painter.font()
        font.setPixelSize(t.font["caption"][0])
        painter.setFont(font)

        label_h, value_h = t.space["4"], t.space["3"]
        top = value_h
        floor = self.height() - label_h
        span = max(1, floor - top)
        peak = max(1, max(sent + queued for _, sent, queued in self._days))

        # Bars keep a sane width and the row is centred, so a three-day campaign
        # reads as a short row rather than three lonely bars flung across the
        # full width of the card.
        slot = min(float(t.space["9"] + t.space["6"]),
                   self.width() / float(len(self._days)))
        width = max(float(t.space["2"]),
                    min(float(t.space["8"] + t.space["2"]), slot - t.space["3"]))
        origin = max(0.0, (self.width() - slot * len(self._days)) / 2.0)
        radius = t.radius["sm"]
        painter.setPen(Qt.NoPen)

        for index, (label, sent, queued) in enumerate(self._days):
            base = origin + index * slot
            left = base + (slot - width) / 2.0
            total = sent + queued
            height = span * (total / float(peak))
            sent_h = height * (sent / float(total)) if total else 0.0

            painter.setBrush(QColor(t.color["border.subtle"]))
            painter.drawRoundedRect(int(left), int(floor - height), int(width),
                                    int(max(float(t.space["hair"]), height)),
                                    radius, radius)
            if sent_h >= t.space["hair"]:
                painter.setBrush(QColor(t.color["accent.default"]))
                painter.drawRoundedRect(int(left), int(floor - sent_h), int(width),
                                        int(sent_h), radius, radius)

            painter.setPen(QColor(t.color["text.tertiary"]))
            painter.drawText(int(base), floor + t.space["hair"], int(slot), label_h,
                             Qt.AlignHCenter | Qt.AlignVCenter, label)
            if total and width >= t.space["5"]:
                painter.setPen(QColor(t.color["text.secondary"]))
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

    TABS = ("Leads", "Campaign", "Sending", "Stats")

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
        self._sort = (_COL_SCORE, Qt.DescendingOrder)
        self._campaign_id = 0
        self._plan: dict = {}
        self._sending = False
        self._paused = False
        # (message, level) for every activity line, so a theme change can put
        # the log back rather than emptying it mid-run.
        self._log_lines: list[tuple[str, str]] = []
        # Per-account rehearsal tally, keyed by lowercased address. A dry run
        # keeps its sends out of the `sends` table on purpose so a rehearsal
        # cannot spend an account's real quota, which left the Accounts card
        # reading "0 / 10 today" throughout the one operation it exists to
        # report on. Counted here instead, and shown next to — never inside —
        # the real number.
        self._rehearsed: dict[str, int] = {}

        # Shell bookkeeping. `_handed_over` is set once the tabs have reached a
        # shell; `_echoing` is true only while the shell is calling back into
        # this screen, and it is what stops the answer from deleting the button
        # whose click is still on the stack.
        self._handed_over = False
        self._echoing = False
        self._context_line = ""

        self._build()
        self.refresh()

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ── The shell ────────────────────────────────────────────────────────────

    def subtabs(self) -> tuple:
        """The four steps, for whatever chrome is drawing them.

        The screens no longer carry a top bar of their own — the audit found
        four screens with four different ones, each with its own Home and
        Settings button — so the tabs are handed back rather than drawn here.
        `(labels, on_change, current)` is the shape `AppShell.set_subtabs` takes.
        """
        return tuple(self.TABS), self._on_shell_tab, self.pages.currentIndex()

    def _host(self) -> tuple:
        """The shell this screen is sitting in, and the key it is filed under.

        Found by asking rather than by being told, because the window registers
        a *factory*: a screen is built on its first visit, from inside the call
        that is about to show it, so there is no moment before that at which
        anything could hand it a reference. `built(key)` is the shell's own
        public answer to "which screen is this", so nothing private is reached
        into and a screen with no shell around it — every test that builds one
        on its own — simply gets nothing back.
        """
        host = self.parentWidget()
        while host is not None:
            if hasattr(host, "set_subtabs") and hasattr(host, "built"):
                for key in host.built():
                    if host.built(key) is self:
                        return host, key
                return None, ""
            host = host.parentWidget()
        return None, ""

    def showEvent(self, event) -> None:
        """Hand the tabs over the first time this screen lands in a shell.

        Once, not on every visit: from here on the shell owns which of the four
        is checked — its own row moves it when the user clicks and `_goto_tab`
        moves it when this screen does — and re-publishing the strip on every
        visit would overwrite the shell's record of it with this screen's stale
        copy.
        """
        super().showEvent(event)
        if self._handed_over:
            return
        host, key = self._host()
        if host is None:
            return
        host.set_subtabs(key, self.TABS, self._on_shell_tab,
                         self.pages.currentIndex())
        self._handed_over = True

    def _on_shell_tab(self, index: int) -> None:
        """The shell moved the tab. Change the page, and do not answer back."""
        self._echoing = True
        try:
            self._goto_tab(index)
        finally:
            self._echoing = False

    def _tell_shell(self, index: int) -> None:
        if self._echoing or not self._handed_over:
            return
        host, key = self._host()
        if host is not None:
            host.set_subtabs(key, self.TABS, self._on_shell_tab, index)

    def _publish_state(self) -> None:
        """One line of live state for the shell, and only while there is any.

        The bar's context line is for what is happening right now on a screen
        the user may have walked away from — a send loop that is still mailing
        strangers is the case it exists for. At rest it says nothing, and it is
        only written when it changes: `set_context` rebuilds the whole second
        row, and rebuilding it on a one-second timer would delete the sub-tab
        buttons under the user's pointer once a second.
        """
        line = ""
        if self._sending:
            stats = self._stats()
            line = "Paused after %d of %d" % (_int_of(stats.get("sent")),
                                              _int_of(stats.get("total"))) \
                if self._paused else \
                "Sending — %d of %d" % (_int_of(stats.get("sent")),
                                        _int_of(stats.get("total")))
        elif self._auditing:
            line = "Auditing sites"
        elif self._planning:
            line = "Preparing a campaign"
        if line == self._context_line:
            return
        self._context_line = line
        host, key = self._host()
        if host is not None:
            host.set_context(key, line, tone="warning" if line else "info")

    def _tell_shell_mode(self) -> None:
        """The dry-run pill is the shell's; this screen only reports the switch."""
        host, key = self._host()
        if host is not None:
            host.set_dry_run(bool(self.settings.get("dry_run", True)))

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        t = components.active_theme()
        root = _rows(self, margin="5", spacing="3", t=t)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_leads_page(t))
        self.pages.addWidget(self._build_campaign_page(t))
        self.pages.addWidget(self._build_sending_page(t))
        self.pages.addWidget(self._build_stats_page(t))
        root.addWidget(self.pages, stretch=1)

        self.toaster = components.Toaster(self)
        root.addWidget(self.toaster.widget)

    # ── Leads tab ────────────────────────────────────────────────────────────

    def _build_leads_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)

        bar = _cols(margin="0", spacing="2", t=t)
        bar.addWidget(components.section_label("Leads"))

        self.lead_search = components.search_field("Filter by name, email, city…")
        self.lead_search.setToolTip(
            "Matches the business name, the address, the city, the category, "
            "the headline gap and the status")
        self.lead_search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.lead_search)

        self.status_filter = _combo(t)
        for label, _key in _STATUS_FILTERS:
            self.status_filter.addItem(label)
        self.status_filter.currentIndexChanged.connect(lambda _i: self._apply_filters())
        bar.addWidget(self.status_filter)

        bar.addStretch()
        self.lead_counts = components.body_label("", tone="tertiary")
        self.lead_counts.setWordWrap(False)
        bar.addWidget(self.lead_counts)
        box.addLayout(bar)

        self.lead_stack = QStackedWidget()
        self.lead_table = self._make_table(t)
        self.lead_stack.addWidget(self.lead_table)
        self.lead_stack.addWidget(components.empty_state(
            title="No leads yet",
            body="Scrape a city and press Start Outreach on the results, or "
                 "import a CSV that has an email column. A business with no "
                 "email address cannot be contacted and is left out.",
            action="Scrape a city", on_action=self.home_signal.emit))
        box.addWidget(self.lead_stack, stretch=1)

        self.lead_progress = _thin_bar(t)
        self.lead_progress.hide()
        box.addWidget(self.lead_progress)

        box.addLayout(self._build_lead_actions(t))
        box.addWidget(components.hint(
            "Double-click a lead to open its website, right-click one for more, "
            "click a column header to sort. Auditing is what fills the Score "
            "and Headline gap columns, and what the email copy is built from."))
        return page

    def _build_lead_actions(self, t):
        """The bulk row: what a selection of five hundred leads can be told to do.

        There was one action on this screen and it was Audit, so a list of five
        hundred could be crawled in a batch and then had to be suppressed,
        copied or reviewed one right-click at a time. Every button here reads
        the selection, and says how many rows it is about to act on.
        """
        actions = _cols(margin="0", spacing="2", t=t)

        import_csv = components.button("Import CSV…", kind="secondary", size="sm",
                                       on_click=self._on_import_csv)
        import_csv.setToolTip("Load leads from a spreadsheet export")
        actions.addWidget(import_csv)

        self.copy_btn = components.button("Copy emails", kind="secondary", size="sm",
                                          on_click=self._on_copy_emails)
        actions.addWidget(self.copy_btn)

        self.suppress_btn = components.button("Suppress…", kind="danger", size="sm",
                                              on_click=self._on_suppress_clicked)
        actions.addWidget(self.suppress_btn)

        self.lead_status = components.body_label("", tone="tertiary")
        self.lead_status.setWordWrap(False)
        actions.addWidget(self.lead_status)
        actions.addStretch()

        self.audit_btn = components.button("Audit all", kind="primary", size="lg",
                                           on_click=self._on_audit_clicked)
        actions.addWidget(self.audit_btn)
        return actions

    def _make_table(self, t):
        """The lead table, from the spec at the top of this file.

        `sortable=False` and the header driven by hand, because the two badge
        columns are painted by a delegate from data on the item rather than
        from its text, and `QTableWidget.sortItems` reorders items under a
        painted row. Sorting the records and rebuilding is one pass over a list
        this screen already holds.
        """
        table = components.table(_LEAD_COLUMNS, density=t.density, sortable=False)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setItemDelegateForColumn(
            _COL_SCORE, _BadgeDelegate(components.score_badge, table))
        table.setItemDelegateForColumn(
            _COL_STATUS, _BadgeDelegate(components.status_pill, table))
        table.customContextMenuRequested.connect(self._show_lead_menu)
        table.cellDoubleClicked.connect(self._on_lead_double_clicked)
        table.itemSelectionChanged.connect(self._on_lead_selection_changed)

        head = table.horizontalHeader()
        head.setSectionsClickable(True)
        head.setSortIndicatorShown(True)
        head.setSortIndicator(*self._sort)
        head.sectionClicked.connect(self._on_header_clicked)
        return table

    # ── Campaign tab ─────────────────────────────────────────────────────────

    def _build_campaign_page(self, t) -> QWidget:
        page = QWidget()
        columns = _cols(page, margin="0", spacing="4", t=t)
        columns.addWidget(self._build_campaign_column(t))
        columns.addWidget(self._build_preview_column(t), stretch=1)
        return page

    def _build_campaign_column(self, t) -> QWidget:
        left = _rows(margin="0", spacing="3", t=t)

        template_card = components.card(title="Template")
        self.template_combo = _combo(t)
        self._refresh_templates()
        self.template_combo.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        template_card.body_layout.addWidget(self.template_combo)
        self.followup_hint = components.hint("")
        template_card.body_layout.addWidget(self.followup_hint)
        left.addWidget(template_card)

        who_card = components.card(title="Previewing")
        self.preview_combo = _combo(t)
        self.preview_combo.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        who_card.body_layout.addWidget(self.preview_combo)
        who_card.body_layout.addWidget(components.hint(
            "Selecting a lead on the Leads tab previews that one."))
        left.addWidget(who_card)

        profile_card = components.card(title="Sender profile")
        self.profile_summary = components.body_label("", tone="secondary",
                                                     max_chars=_COLUMN_CH)
        profile_card.body_layout.addWidget(self.profile_summary)
        self.profile_problem = components.body_label("", tone="danger",
                                                     max_chars=_COLUMN_CH)
        self.profile_problem.hide()
        profile_card.body_layout.addWidget(self.profile_problem)
        self.profile_fix_btn = components.button(
            "Fix in Settings", kind="danger", size="sm",
            on_click=self.settings_signal.emit)
        profile_card.body_layout.addWidget(self.profile_fix_btn,
                                           alignment=Qt.AlignLeft)
        left.addWidget(profile_card)

        plan_card = components.card(title="Schedule")
        self.plan_summary = components.body_label("", tone="secondary",
                                                  max_chars=_COLUMN_CH)
        plan_card.body_layout.addWidget(self.plan_summary)
        self.plan_warning = components.body_label("", tone="danger",
                                                  max_chars=_COLUMN_CH)
        self.plan_warning.hide()
        plan_card.body_layout.addWidget(self.plan_warning)
        # Who "prepare" is about to queue, on screen before it is pressed. The
        # button acts on the selection when there is one and on everything the
        # filters show when there is not, and it looked identical either way.
        self.plan_targets = components.hint("", max_chars=_COLUMN_CH)
        plan_card.body_layout.addWidget(self.plan_targets)
        self.plan_progress = _thin_bar(t)
        self.plan_progress.hide()
        plan_card.body_layout.addWidget(self.plan_progress)

        # Stacked, not side by side: the two labels want more than the column
        # is wide, so a row clipped the primary button mid-glyph. Full width
        # also stops the label ever deciding the layout — a wider font or a
        # translation cannot squeeze either button again.
        self.prepare_btn = components.button("Prepare campaign", kind="primary",
                                             size="lg",
                                             on_click=self._on_prepare_clicked)
        plan_card.body_layout.addWidget(self.prepare_btn)
        self.goto_sending_btn = components.button(
            "Open Sending", kind="secondary", size="lg",
            on_click=lambda: self._goto_tab(2))
        self.goto_sending_btn.hide()
        plan_card.body_layout.addWidget(self.goto_sending_btn)
        left.addWidget(plan_card)
        left.addStretch()

        # The four cards want more height than the window's own minimum leaves
        # this column, so at 880x620 the Schedule card used to be handed less
        # than its *minimum* — the plan's last line vanished and the two buttons
        # were pushed into each other. Nothing here can be made shorter without
        # dropping something the user has to read, so the column scrolls
        # instead: full height when there is room, a scrollbar when there is
        # not, and never a silent clip.
        holder = QWidget()
        holder.setLayout(left)
        width = max(_measure(QFontMetrics(holder.font()), _COLUMN_CH)
                    + t.space["4"] * 2,
                    self.prepare_btn.sizeHint().width(),
                    self.goto_sending_btn.sizeHint().width())
        holder.setFixedWidth(width)

        self.campaign_scroll = QScrollArea()
        self.campaign_scroll.setWidget(holder)
        self.campaign_scroll.setWidgetResizable(True)
        self.campaign_scroll.setFrameShape(QFrame.NoFrame)
        self.campaign_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.campaign_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # The bar overlays nothing in Qt, so the viewport has to be wide enough
        # for the column plus the bar or the cards lose those pixels the moment
        # it appears.
        self.campaign_scroll.setFixedWidth(
            width + self.campaign_scroll.verticalScrollBar().sizeHint().width())
        self.campaign_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        return self.campaign_scroll

    def _build_preview_column(self, t) -> QWidget:
        """The message as it will be delivered, at the width an inbox shows it.

        Capped, and that is the finding: on a 2560px window this pane measured
        2154px, so the copy being checked before it goes to a stranger was laid
        out at three and a half times the measure it will actually be read at.
        The holder takes the cap and the row round it takes the slack, so the
        page still fills the window and the paper does not.
        """
        holder = QWidget()
        right = _rows(holder, margin="0", spacing="2", t=t)

        subject_row = _cols(margin="0", spacing="2", t=t)
        subject_row.addWidget(components.section_label("Subject"))
        subject_row.addStretch()
        self.subject_count = components.body_label("", tone="tertiary")
        self.subject_count.setWordWrap(False)
        subject_row.addWidget(self.subject_count)
        right.addLayout(subject_row)

        self.subject_label = components.heading("—", "h2")
        self.subject_label.setWordWrap(True)
        right.addWidget(self.subject_label)

        meta_row = _cols(margin="0", spacing="2", t=t)
        self.preview_meta = _ElidedLabel()
        meta_row.addWidget(self.preview_meta, stretch=1)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        for index, label in enumerate(("Text", "HTML")):
            tab = components.button(label, kind="tab", size="sm")
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            self.view_group.addButton(tab, index)
            meta_row.addWidget(tab)
        self.view_group.idClicked.connect(lambda _i: self._refresh_preview())
        right.addLayout(meta_row)

        self.preview = QTextBrowser()
        self.preview.setObjectName("email_paper")
        self.preview.setOpenExternalLinks(False)
        self.preview.setOpenLinks(False)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.preview, stretch=1)
        self.preview_hint = components.hint("")
        right.addWidget(self.preview_hint)

        holder.setMaximumWidth(_measure(QFontMetrics(holder.font()), _PAPER_CH)
                               + _PAPER.space["5"] * 2)
        return holder

    # ── Sending tab ──────────────────────────────────────────────────────────

    def _build_sending_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)

        pick = _cols(margin="0", spacing="2", t=t)
        pick.addWidget(components.section_label("Campaign"))
        self.campaign_combo = _combo(t)
        self.campaign_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.campaign_combo.setMaximumWidth(
            _measure(QFontMetrics(page.font()), _COLUMN_CH))
        self.campaign_combo.currentIndexChanged.connect(self._on_campaign_changed)
        pick.addWidget(self.campaign_combo)
        pick.addStretch()

        # Built here and replaced by `_refresh_mode`: the one control that mails
        # real strangers has to be the filled danger button and nothing else,
        # and in a dry run it is not that control at all.
        self.start_btn = components.button("Start sending", kind="primary",
                                           size="lg",
                                           on_click=self._on_start_clicked)
        self.start_row = pick
        self.start_at = pick.count()
        pick.addWidget(self.start_btn)
        self.pause_btn = components.button("Pause", kind="secondary", size="lg",
                                           on_click=self._on_pause_clicked)
        self.pause_btn.setEnabled(False)
        pick.addWidget(self.pause_btn)
        self.stop_btn = components.button("Stop", kind="danger", size="lg",
                                          on_click=self._on_stop_clicked)
        self.stop_btn.setEnabled(False)
        pick.addWidget(self.stop_btn)
        box.addLayout(pick)

        self.send_note = components.hint("")
        box.addWidget(self.send_note)

        progress_row = _cols(margin="0", spacing="3", t=t)
        self.send_status = components.body_label("Nothing queued yet",
                                                 tone="secondary")
        self.send_status.setWordWrap(False)
        progress_row.addWidget(self.send_status)
        progress_row.addStretch()
        self.next_send_label = components.body_label("", tone="tertiary")
        self.next_send_label.setWordWrap(False)
        progress_row.addWidget(self.next_send_label)
        box.addLayout(progress_row)

        # Why the queue is not moving, when it is not moving. A run that has
        # stalled used to report itself as "ready to send" over a frozen queue,
        # which is the one sentence that keeps a user waiting for something that
        # was never going to happen.
        self.send_reason = components.body_label("", tone="warning")
        self.send_reason.hide()
        box.addWidget(self.send_reason)

        self.send_progress = _thin_bar(t)
        box.addWidget(self.send_progress)

        middle = _cols(margin="0", spacing="4", t=t)
        accounts_card = components.card(title="Accounts today")
        self.accounts_holder = _rows(margin="0", spacing="3", t=t)
        accounts_card.body_layout.addLayout(self.accounts_holder)
        accounts_card.body_layout.addStretch()
        accounts_card.setFixedWidth(
            _measure(QFontMetrics(page.font()), _COLUMN_CH) + t.space["4"] * 2)
        middle.addWidget(accounts_card)

        log_side = _rows(margin="0", spacing="2", t=t)
        log_side.addWidget(components.section_label("Activity"))
        self.log_list = QListWidget()
        self.log_list.setObjectName("saved_list")
        self.log_list.setSelectionMode(QAbstractItemView.NoSelection)
        # Without wrapping, a line longer than the panel makes the whole list
        # scroll sideways: the placeholder sentence and a business with a long
        # name both run past the panel's width at the window's minimum size.
        # Wrapped, the panel stays a column of text at every width and no line
        # is ever hidden off to the right.
        self.log_list.setWordWrap(True)
        log_side.addWidget(self.log_list, stretch=1)
        middle.addLayout(log_side, stretch=1)
        box.addLayout(middle, stretch=1)
        self._repaint_log()

        box.addWidget(components.hint(
            "Sends are spaced by a random gap inside your sending window and "
            "stop at each account's daily cap. Closing the app pauses the run; "
            "the queue survives and picks up where it left off."))
        return page

    # ── Stats tab ────────────────────────────────────────────────────────────

    # What each tile counts, and the sentence that says what the number means.
    # Six numbers with six one-word captions were six numbers nobody could act
    # on; the hint is what turns a count into a finding.
    _TILES = (
        ("queued", "Queued", "info", "Built and waiting for its slot."),
        ("sent", "Sent", "accent", "Delivered to the address, not yet answered."),
        ("failed", "Failed", "danger", "The server refused it. Check the account."),
        ("replied", "Replied", "accent", "Someone wrote back. This is the number that matters."),
        ("bounced", "Bounced", "danger", "The address does not exist. It is suppressed."),
        ("skipped", "Skipped", "warning", "Not sent: suppressed, already contacted, or unusable."),
    )

    def _build_stats_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)

        head = _cols(margin="0", spacing="2", t=t)
        head.addWidget(components.section_label("Results"))
        head.addStretch()
        self.stats_campaign = components.body_label("", tone="tertiary")
        self.stats_campaign.setWordWrap(False)
        head.addWidget(self.stats_campaign)
        box.addLayout(head)

        # The row ends in a stretch rather than sharing the window between six
        # tiles: at 2560 each one was a 400px box round a two-digit number.
        tiles = _cols(margin="0", spacing="3", t=t)
        self.tiles: dict[str, QFrame] = {}
        for key, caption, tone, note in self._TILES:
            tile = components.stat_tile(caption, "0", tone=tone, hint=note)
            tiles.addWidget(tile)
            self.tiles[key] = tile
        tiles.addStretch()
        box.addLayout(tiles)

        # Not a tile, because it is not a count of messages: it is how many of
        # them say anything about the business they are addressed to. A user
        # reading six numbers has no way to tell two hundred personalised
        # emails from two hundred form letters, and that is the difference
        # between outreach and spam.
        self.personal_note = components.body_label("", tone="secondary")
        box.addWidget(self.personal_note)

        bars_card = components.card(title="Volume by day")
        self.day_bars = _DayBars(t)
        bars_card.body_layout.addWidget(self.day_bars)
        self.bars_empty = components.hint(
            "Nothing scheduled yet. Prepare a campaign and the days it spans "
            "appear here, filling in as messages go out.")
        bars_card.body_layout.addWidget(self.bars_empty)
        box.addWidget(bars_card)

        supp_head = _cols(margin="0", spacing="2", t=t)
        supp_head.addWidget(components.section_label("Suppression list"))
        self.supp_count = components.body_label("", tone="tertiary")
        self.supp_count.setWordWrap(False)
        supp_head.addWidget(self.supp_count)
        supp_head.addStretch()
        self.unsuppress_btn = components.button(
            "Remove selected", kind="secondary", size="sm",
            on_click=self._on_unsuppress_clicked)
        supp_head.addWidget(self.unsuppress_btn)
        box.addLayout(supp_head)

        # An address and a reason, and no more room than those need: the list
        # used to take every pixel the window had for a column of 30-character
        # strings.
        self.supp_list = QListWidget()
        self.supp_list.setObjectName("saved_list")
        self.supp_list.setWordWrap(True)
        self.supp_list.setMaximumWidth(
            _measure(QFontMetrics(page.font()), _SUPPRESSION_CH))
        supp_row = _cols(margin="0", spacing="3", t=t)
        supp_row.addWidget(self.supp_list)
        supp_row.addWidget(components.hint(
            "Every address here is permanently excluded from planning and "
            "sending, follow-ups included. Remove one only if the person asked "
            "to be contacted again."), stretch=1)
        box.addLayout(supp_row, stretch=1)
        return page

    # ── The theme, live ──────────────────────────────────────────────────────

    def restyle(self) -> None:
        """Wear the palette the app is in now.

        Every component resolves its colours in Python at build time and writes
        them into its own stylesheet, which beats the application sheet — so a
        repolish alone leaves this screen in the palette it was constructed in.
        The state worth carrying across is small: which tab is open, what is in
        the filter box, which campaign is picked, and the activity log, which is
        the one thing on this screen that cannot be read back off the database.

        `_redraw` and not `refresh`, and that distinction is the whole reason
        the two are separate: a theme change is not a moment to go back to the
        settings file. A campaign half set up in memory — an account being
        tried, a switch thrown from a dialog and not yet saved — would be
        rolled back by a repaint, which is the last thing a repaint may do.
        """
        tab = self.pages.currentIndex()
        search, status = self._search, self.status_filter.currentIndex()
        view = self.view_group.checkedId()

        holder = QWidget()
        holder.setLayout(self.layout())
        holder.deleteLater()

        self._build()
        self.status_filter.setCurrentIndex(max(0, status))
        for button in self.view_group.buttons():
            button.setChecked(self.view_group.id(button) == max(0, view))
        self._redraw()
        if search:
            self.lead_search.setText(search)
        self._goto_tab(tab)

    # ── Public API ───────────────────────────────────────────────────────────

    def load_from_results(self, records: list[dict]) -> None:
        """Take the rows the Results screen just scraped into the lead pool."""
        try:
            leads = [self._lead_from_record(r, "scrape") for r in (records or [])
                     if isinstance(r, dict)]
            self._import_leads(leads, "the last scrape")
        except Exception:
            self._toast("Could not read those results.", tone="danger")

    def refresh(self) -> None:
        """Re-read settings and the database and redraw everything."""
        try:
            self.settings = _settings.load_settings()
            self._redraw()
        except Exception:
            pass

    def _redraw(self) -> None:
        """Everything `refresh` does except going back to the settings file."""
        self._refresh_mode()
        self._refresh_templates()
        self._refresh_campaigns()
        self._reload_leads()
        self._refresh_profile()
        self._refresh_accounts()
        self._refresh_stats()
        self._refresh_plan_summary()
        self._publish_state()

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

    def _import_leads(self, leads: list, origin: str, dropped: int = 0) -> None:
        """Take a batch in, and account for every row of it.

        Every count is reported, because the one that was not is the one that
        mattered: a spreadsheet whose email column is half empty imported
        silently, and the campaign built from it was quietly a third the size
        the user thought they had bought.
        """
        known = {_text_of(lead.get("email")).strip().lower()
                 for lead in self._leads}
        added = merged = 0
        for lead in leads:
            email = _text_of(lead.get("email")).strip().lower()
            if "@" not in email:
                dropped += 1
                continue
            if not _db.upsert_lead(self.conn, lead):
                dropped += 1
                continue
            if email in known:
                merged += 1
            else:
                added += 1
                known.add(email)

        self._reload_leads()
        self._goto_tab(0)
        if not added and not merged:
            self._toast("Nothing in %s had an email address, so there is nobody "
                        "to contact yet." % origin, tone="warning")
            return

        parts = ["Imported %s from %s." % (_plural(added, "lead"), origin)]
        if merged:
            parts.append("%s already on the list %s updated."
                         % (_plural(merged, "lead"), "was" if merged == 1 else "were"))
        if dropped:
            parts.append("%d %s no email address and %s left out."
                         % (dropped, "row had" if dropped == 1 else "rows had",
                            "was" if dropped == 1 else "were"))
        parts.append("Audit them next to score the opportunity.")
        self._toast("  ".join(parts),
                    tone="warning" if dropped else "success")

    def _on_import_csv(self) -> None:
        start = self.settings.get("export_dir") or os.path.expanduser("~")
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import leads", start, "CSV files (*.csv);;All files (*)")
        if not path:
            return
        leads, dropped, problem = self._read_csv(path)
        if problem:
            self._toast(problem, tone="danger")
            return
        self._import_leads(leads, os.path.basename(path), dropped)

    def _read_csv(self, path: str) -> tuple:
        """Rows from a CSV, mapped onto lead columns. (leads, dropped, problem).

        `dropped` is the count this used to throw away without a word: a row
        with no address in the email column cannot be contacted, and until now
        the only evidence of it was a lead count that did not match the file.
        """
        dropped = 0
        try:
            # utf-8-sig because a spreadsheet export leads with a BOM, which
            # would otherwise turn the first header into "﻿email".
            with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
                reader = csv.DictReader(handle)
                headers = {_norm_key(h): h for h in (reader.fieldnames or []) if h}
                mapping = {_CSV_ALIASES[key]: raw for key, raw in headers.items()
                           if key in _CSV_ALIASES}
                if "email" not in mapping:
                    return [], 0, ("That file has no email column, so none of it "
                                   "can be contacted. Expected a header called "
                                   "email.")
                leads = []
                for index, row in enumerate(reader):
                    if index >= _CSV_MAX_ROWS:
                        break
                    lead = {field: _text_of(row.get(column)).strip()
                            for field, column in mapping.items()}
                    lead["source"] = "csv"
                    if "@" in lead.get("email", ""):
                        leads.append(lead)
                    else:
                        dropped += 1
        except OSError as exc:
            return [], 0, "Could not read that file: %s" % exc
        except (csv.Error, UnicodeError):
            return [], 0, "That file is not readable as CSV."
        if not leads:
            return [], dropped, ("None of the %s in that file had an email "
                                 "address, so there is nobody to contact."
                                 % _plural(dropped, "row"))
        return leads, dropped, ""

    # ── Leads: table ─────────────────────────────────────────────────────────

    def _reload_leads(self) -> None:
        self._leads = _db.list_leads(self.conn)
        self._generic.clear()
        self._fill_table()
        self.lead_stack.setCurrentIndex(0 if self._leads else 1)
        self._refresh_preview_choices()

    def _fill_table(self) -> None:
        """Rebuild every row, in the order the header says.

        Sorted here rather than by `QTableWidget.sortItems`, because the Score
        and Status columns are painted from data on the item rather than from
        its text and Qt's own sort reorders the items under a painted row. A
        rebuild is one pass over a list this screen is already holding.
        """
        table = self.lead_table
        column, order = self._sort
        self._leads.sort(key=lambda lead: self._sort_key(lead, column),
                         reverse=order == Qt.DescendingOrder)
        table.setRowCount(0)
        for lead in self._leads:
            self._append_lead_row(lead)
        table.horizontalHeader().setSortIndicator(column, order)
        # Retaking the widths is also what clears a horizontal scrollbar left
        # behind by a hand-dragged column: the width the user set survives
        # until the next reload, and a reload is when the table gets its
        # geometry back from the spec.
        table.relayout()
        self._apply_filters()

    def _sort_key(self, lead: dict, column: int):
        if column == _COL_SCORE:
            return _int_of(lead.get("opportunity_score"))
        if column == _COL_GAP:
            return self._gap_text(lead)[0].lower()
        return _text_of(lead.get(_COL_KEYS.get(column, "name"))).strip().lower()

    def _on_header_clicked(self, column: int) -> None:
        current, order = self._sort
        if column == current:
            order = (Qt.AscendingOrder if order == Qt.DescendingOrder
                     else Qt.DescendingOrder)
        else:
            order = Qt.DescendingOrder if column == _COL_SCORE else Qt.AscendingOrder
        self._sort = (column, order)
        self._fill_table()

    def _gap_text(self, lead: dict) -> tuple:
        """(what the Headline gap column says, and whether it is a form letter)."""
        audit = _loads(lead.get("audit_json"))
        gaps = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)]
        reason = self._generic_reason(lead, audit)
        gap = _text_of(gaps[0].get("title")).strip() if gaps else ""
        if not gap and reason:
            # This lead's email is three paragraphs that could have been written
            # before the crawl. That is what the column has to say, because "no
            # clear gap" reads as a thin prospect rather than as a form letter.
            gap = "form letter — " + _templates.generic_reason(reason)
        return (gap or ("not audited yet" if not audit else "no clear gap"),
                bool(reason))

    def _append_lead_row(self, lead: dict) -> None:
        table = self.lead_table
        score = _int_of(lead.get("opportunity_score"))
        status = _text_of(lead.get("status")).strip() or "new"
        audit = _loads(lead.get("audit_json"))
        gaps = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)]
        gap, generic = self._gap_text(lead)

        row = table.add_row((
            Cell(text=_text_of(lead.get("name")).strip() or "—",
                 sort=_text_of(lead.get("name")).strip().lower()),
            Cell(text=_text_of(lead.get("email")).strip()),
            Cell(text=_text_of(lead.get("city")).strip()),
            Cell(text=_text_of(lead.get("category")).strip()),
            Cell(text="", sort=score,
                 tip="Opportunity %d of 100 — %s, %s" % (
                     score, components.score_band(score)[1],
                     _plural(len(gaps), "gap"))
                 if score > 0 else "Not audited yet"),
            Cell(text=gap,
                 tip="Nothing is known about this business, so the email says "
                     "nothing about it. Filter to Generic email to review or "
                     "exclude these before sending." if generic else ""),
            Cell(text="", sort=status, tip=self._status_tip(status)),
        ), data=lead)

        # The two badge columns carry their value rather than their words: the
        # delegate paints `components.status_pill()` and `score_badge()` from
        # this, and the item text is the badge's own label so a screen reader
        # and the filter box are told what the pill says.
        self._set_badge(row, _COL_SCORE, score)
        self._set_badge(row, _COL_STATUS, status)

    def _set_badge(self, row: int, column: int, key) -> None:
        item = self.lead_table.item(row, column)
        if item is None:
            return
        item.setData(_BADGE_ROLE, key)
        delegate = self.lead_table.itemDelegateForColumn(column)
        label = delegate.badge(key).text() if isinstance(delegate, _BadgeDelegate) \
            else _text_of(key)
        item.setText(label)
        item.setData(components.FULL_ROLE, label)

    @staticmethod
    def _status_tip(status: str) -> str:
        spec = components.STATUS_PILLS.get(status)
        if spec is None:
            return "Status: %s" % status
        return {"outline": "Chosen, not sent: %s.",
                "subtle": "In flight: %s.",
                "solid": "Ended badly: %s."}.get(spec.fill, "%s.") % status

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
            hide = hide or (self._search and not self._matches(lead))
            table.setRowHidden(row, bool(hide))
            visible += 0 if hide else 1

        self._refresh_lead_counts(visible)
        self._refresh_lead_actions()

    def _matches(self, lead: dict) -> bool:
        """Does the filter box's text appear anywhere in this lead?

        Read off the record rather than off the painted row, which is what lets
        the city and the category be searched at all — and what keeps the two
        badge columns searchable now that what they paint is a pill rather than
        a word.
        """
        needle = self._search
        haystack = [_text_of(lead.get(field))
                    for field in ("name", "email", "city", "category", "phone",
                                  "website", "status", "source")]
        haystack.append(self._gap_text(lead)[0])
        haystack.append(components.score_band(
            _int_of(lead.get("opportunity_score")))[1])
        return any(needle in value.lower() for value in haystack if value)

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

    def _target_sentence(self, verb: str) -> str:
        leads = self._target_leads()
        scope = "selected" if self._selected_leads() else "shown by the filter"
        return "%s %s (%s) — %s." % (verb, _plural(len(leads), "lead"), scope,
                                     _names_of(leads))

    def _on_lead_selection_changed(self) -> None:
        self._refresh_lead_actions()
        chosen = self._selected_leads()
        if len(chosen) == 1:
            self._select_preview_lead(_int_of(chosen[0].get("id")))

    def _refresh_lead_actions(self) -> None:
        """Every bulk control says what it is about to act on, and how many.

        A disabled one says what would enable it: the audit found controls that
        simply stopped responding, with nothing on screen saying why.
        """
        if not hasattr(self, "audit_btn"):
            return
        chosen = self._selected_leads()
        targets = self._target_leads()
        count = len(chosen)

        self.audit_btn.setText("Audit selected (%d)" % count if count
                               else "Audit all (%d)" % len(targets))
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
                "%s Crawl each website, score the automation gaps and write "
                "the personalised lines (%s). Nothing is sent."
                % (self._target_sentence("Audits"), self._ai_summary()))

        self.suppress_btn.setText("Suppress…" if count <= 1
                                  else "Suppress %d…" % count)
        self.suppress_btn.setEnabled(bool(chosen))
        self.suppress_btn.setToolTip(
            "Never contact %s again. You will be asked first."
            % _names_of(chosen) if chosen else
            "Select the rows to exclude — this acts on the whole selection")

        self.copy_btn.setText("Copy emails" if count <= 1
                              else "Copy %d emails" % count)
        self.copy_btn.setEnabled(bool(targets))
        self.copy_btn.setToolTip(
            "Copy every address %s to the clipboard, one per line"
            % ("selected" if chosen else "shown") if targets else
            "There are no addresses on screen to copy")

        if hasattr(self, "plan_targets"):
            self.plan_targets.setText(self._target_sentence("Queues"))
            self.prepare_btn.setText("Prepare campaign (%d)" % len(targets)
                                     if targets else "Prepare campaign")

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
        # A right-click inside the selection acts on the selection; one outside
        # it acts on the row under the pointer, which is what the pointer said
        # it would do.
        chosen = self._selected_leads()
        if lead not in chosen:
            chosen = [lead]

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
        menu.addAction("Suppress %s (never contact)" % _plural(len(chosen), "lead"),
                       lambda: self._suppress(chosen))
        menu.exec_(self.lead_table.viewport().mapToGlobal(pos))

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(_text_of(text))
        self.lead_status.setText("Copied to clipboard")

    def _on_copy_emails(self) -> None:
        addresses = [_text_of(lead.get("email")).strip()
                     for lead in self._target_leads()
                     if _text_of(lead.get("email")).strip()]
        if not addresses:
            self._toast("There are no addresses on screen to copy.",
                        tone="warning")
            return
        QApplication.clipboard().setText("\n".join(addresses))
        self.lead_status.setText("Copied %s" % _plural(len(addresses), "address"))

    def _on_suppress_clicked(self) -> None:
        chosen = self._selected_leads()
        if not chosen:
            self._toast("Select the leads to exclude first — Suppress acts on "
                        "the whole selection.", tone="warning")
            return
        self._suppress(chosen)

    def _suppress(self, leads: list) -> None:
        """Exclude a whole selection, after asking, and offer the way back.

        Three findings in one action. It was permanent and unconfirmed, so a
        misplaced right-click ended a lead for good; it read `currentItem` and
        so acted on one row of a selection of fifty without saying which; and
        the only feedback was a six-second grey line that could not carry an
        undo. It asks first, it names who it is about to exclude, and the toast
        that reports it carries the way back.
        """
        wanted = [(lead, _text_of(lead.get("email")).strip(),
                   _text_of(lead.get("status")).strip() or "new")
                  for lead in leads
                  if "@" in _text_of(lead.get("email"))]
        if not wanted:
            self._toast("Those leads have no address to exclude.", tone="warning")
            return

        if not components.confirm(
                self,
                title="Never contact %s again?" % _plural(len(wanted), "lead"),
                body="%s will be excluded from every campaign from now on, "
                     "follow-ups included, and anything already queued for "
                     "them is cancelled. This is what an unsubscribe does, so "
                     "use it when someone has asked."
                     % _names_of([lead for lead, _e, _s in wanted]),
                confirm_text="Suppress %s" % _plural(len(wanted), "lead"),
                danger=True):
            return

        for _lead, email, _status in wanted:
            _db.suppress(self.conn, email, "suppressed from the leads table")
        self._reload_leads()
        self._refresh_stats()

        restore = [(email, status) for _lead, email, status in wanted]
        self._toast(
            "%s will never be contacted again, and anything queued for them "
            "has been cancelled." % _names_of([l for l, _e, _s in wanted]),
            tone="warning", action="Undo",
            on_action=lambda: self._undo_suppress(restore))

    def _undo_suppress(self, restore: list) -> None:
        """Put a suppression back, and say plainly what it cannot put back.

        The addresses become contactable again and each lead returns to the
        status it was on. What stays cancelled is what was in the queue: those
        rows carry a send time that has since passed, and re-queueing them would
        mail the whole batch at once.
        """
        back = 0
        for email, status in restore:
            if self._unsuppress(email):
                _db.upsert_lead(self.conn, {"email": email, "status": status})
                _db.log_event(self.conn, "unsuppressed", email)
                back += 1
        self._reload_leads()
        self._refresh_stats()
        if not back:
            self._toast("Could not undo that.", tone="danger")
            return
        self._toast("%s can be contacted again. Nothing was re-queued — "
                    "prepare a new campaign to include them."
                    % _plural(back, "address"), tone="success")

    def _preview_lead(self, lead: dict) -> None:
        self._select_preview_lead(_int_of(lead.get("id")))
        self._goto_tab(1)

    # ── Leads: auditing ──────────────────────────────────────────────────────

    def _on_audit_clicked(self) -> None:
        if self._auditing:
            return
        if not self._retire(self.audit_worker):
            self._toast("The last crawl is still finishing. Press Audit again "
                        "in a moment.", tone="warning")
            return
        leads = self._target_leads()
        if not leads:
            self._toast("There are no leads to audit yet. Import a CSV, or "
                        "scrape a city on the Scrape screen.", tone="warning")
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
        self._publish_state()

    def _on_audit_progress(self, done: int, total: int) -> None:
        self.lead_progress.setRange(0, max(1, total))
        self.lead_progress.setValue(done)

    def _on_audit_log(self, message: str, level: str) -> None:
        if level in ("info", "error"):
            self.lead_status.setText(message)

    def _on_lead_audited(self, lead: dict) -> None:
        """Rewrite one row where it stands so scores land while the run runs.

        In place, and not re-sorted: a table that reorders itself under the
        pointer while five hundred crawls come back is a table nobody can read
        a row of. The order is retaken when the run ends.
        """
        if not isinstance(lead, dict):
            return
        lead_id = _int_of(lead.get("id"))
        row = -1
        for index, existing in enumerate(self._leads):
            if _int_of(existing.get("id")) == lead_id:
                self._leads[index] = lead
                break
        for index in range(self.lead_table.rowCount()):
            if _int_of(self._lead_at(index).get("id")) == lead_id:
                row = index
                break
        if row < 0:
            return

        self._generic.pop(lead_id, None)
        score = _int_of(lead.get("opportunity_score"))
        status = _text_of(lead.get("status")).strip() or "new"
        gap, generic = self._gap_text(lead)
        table = self.lead_table
        for column, value in ((_COL_NAME, _text_of(lead.get("name")).strip() or "—"),
                              (_COL_EMAIL, _text_of(lead.get("email")).strip()),
                              (_COL_CITY, _text_of(lead.get("city")).strip()),
                              (_COL_CATEGORY, _text_of(lead.get("category")).strip()),
                              (_COL_GAP, gap)):
            item = table.item(row, column)
            if item is not None:
                item.setText(value)
                item.setData(components.FULL_ROLE, value)
        gap_item = table.item(row, _COL_GAP)
        if gap_item is not None and generic:
            gap_item.setToolTip(
                "Nothing is known about this business, so the email says "
                "nothing about it. Filter to Generic email to review or "
                "exclude these before sending.")
        name_item = table.item(row, _COL_NAME)
        if name_item is not None:
            name_item.setData(Qt.UserRole, lead)
        self._set_badge(row, _COL_SCORE, score)
        self._set_badge(row, _COL_STATUS, status)
        self._apply_filters()

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
        self._refresh_lead_actions()
        self._refresh_preview()
        self._publish_state()

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

    def _profile_problems(self) -> list:
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
                        "switch the check back on in Settings."
                        % (verb.capitalize(), listed), tone="warning")
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
                        "on and never stop you. Settings can put the check back.",
                        tone="warning")
        if not go:
            self.settings_signal.emit()
        return go

    def _ask(self, title: str, body: str, proceed: str) -> tuple:
        """A blocking question with a way through. (go ahead, stop asking).

        Its own dialog rather than `components.confirm()`, and the difference is
        the checkbox. `confirm()` remembers an answer in this process; this one
        writes `require_profile_complete` to the settings file, so the switch a
        user throws here is the same switch they find on the Settings screen.
        The shared dialog is what the destructive actions on this screen use —
        suppressing a selection, and the one button that mails real people.

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
            lines.append(who)
        if company:
            lines.append(company)
        accounts = self._accounts()
        if accounts:
            lines.append("Sending from "
                         + ", ".join(a["email"] for a in accounts[:_NAMED_IN_SUMMARY]))

        # Both halves of every problem, on the card and not only in the dialog:
        # a warning without its consequence is either ignored or obeyed for the
        # wrong reason, and the dialog is the one place the user cannot get
        # back to. The line above them is what says which of the three is the
        # one that can end a campaign on its own.
        problems = self._profile_problems()
        for problem, cost in problems:
            lines.append("Needs attention: %s — %s." % (problem, cost))
        self.profile_summary.setText("\n".join(lines) or "Nothing set up yet.")
        self.profile_problem.setText(
            "Nothing can be queued until a Gmail account is added in Settings."
            if not self._accounts() else
            "%s will push more of this campaign into spam folders."
            % _plural(len(problems), "thing").capitalize())
        self.profile_problem.setVisible(bool(problems))
        self.profile_fix_btn.setVisible(bool(problems))
        self.profile_fix_btn.setText(
            "Fix %s in Settings" % _plural(len(problems), "thing"))

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
        for lead in self._leads[:_PREVIEW_CAP]:
            combo.addItem(self._preview_label(lead), _int_of(lead.get("id")))
        if previous is not None:
            index = combo.findData(previous)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setEnabled(combo.count() > 0)
        combo.setToolTip("" if combo.count() else
                         "Import leads on the Leads tab and they appear here")
        self._refresh_preview()

    @staticmethod
    def _preview_label(lead: dict) -> str:
        return "%s — %s" % (_text_of(lead.get("name")).strip() or "Unnamed",
                            _text_of(lead.get("email")).strip())

    def _select_preview_lead(self, lead_id: int) -> None:
        lead_id = _int_of(lead_id)
        index = self.preview_combo.findData(lead_id)
        if index < 0:
            # Past the combo's cap. Selecting a lead on the Leads tab must still
            # preview that lead rather than silently previewing someone else.
            lead = next((l for l in self._leads if _int_of(l.get("id")) == lead_id), None)
            if lead is None:
                return
            self.preview_combo.insertItem(0, self._preview_label(lead), lead_id)
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
            self.subject_count.setText("unresolved field")
            self._show_paper(
                "This message still contains an unresolved merge field (%s), so "
                "it is not being shown and would not be sent. Fill in the sender "
                "profile in Settings, or audit this lead, and check again."
                % leak.group(0))
            return

        self.subject_label.setText(subject)
        over = len(subject) > _templates.SUBJECT_MAX
        self.subject_count.setText(
            "%d / %d characters%s" % (len(subject), _templates.SUBJECT_MAX,
                                      " — too long for an inbox" if over else ""))

        accounts = self._accounts()
        account = accounts[0] if accounts else {}
        sender = _text_of(account.get("display_name")).strip() or \
            _text_of(self._profile().get("sender_name")).strip()
        from_line = "%s <%s>" % (sender, account.get("email", "no account yet")) \
            if sender else _text_of(account.get("email")) or "no account yet"
        self.preview_meta.setText("To %s <%s>  ·  From %s" % (
            _text_of(lead.get("name")).strip() or "there",
            _text_of(lead.get("email")).strip(), from_line))

        if self.view_group.checkedId() == 1:
            self.preview.setHtml(body_html)
        else:
            self.preview.setHtml(self._as_paper(body_text))
        self._paint_paper()

    def _as_paper(self, body_text: str) -> str:
        """Plain text laid out as the recipient's mail client would show it.

        Every value is inline and every one of them comes out of the **light**
        theme: a mail client is not wearing this app's palette, and an email
        preview that does not look like an email is not a preview.
        """
        blocks = []
        for para in re.split(r"\n\s*\n", _text_of(body_text).strip()):
            lines = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
            if lines:
                blocks.append('<p style="margin:0 0 %dpx 0;">%s</p>'
                              % (_PAPER.space["3"], "<br>".join(lines)))
        return self._paper_html("".join(blocks), _PAPER.font["h3"][0],
                                _PAPER.color["text.primary"])

    def _show_paper(self, message: str) -> None:
        self.preview.setHtml(self._paper_html(
            html.escape(message), _PAPER.font["body"][0],
            _PAPER.color["text.secondary"]))
        self._paint_paper()

    @staticmethod
    def _paper_html(body: str, size: int, ink: str) -> str:
        return ('<div style="font-family:%s;font-size:%dpx;line-height:1.6;'
                'color:%s;">%s</div>' % (_MAIL_FAMILY, size, ink, body))

    def _paint_paper(self) -> None:
        """Paint the document itself, in the palette a reader will see it in.

        The QSS rule for `#email_paper` styles the well the document sits in;
        this is the page inside it. Both come from the theme — the light one,
        deliberately: the body carries near-black ink, and on the dark app's own
        surface it would be invisible.
        """
        frame = self.preview.document().rootFrame()
        fmt = frame.frameFormat()
        fmt.setBackground(QColor(_PAPER.color["raised"]))
        fmt.setMargin(_PAPER.space["5"])
        frame.setFrameFormat(fmt)

    # ── Campaign: planning ───────────────────────────────────────────────────

    def _on_prepare_clicked(self) -> None:
        if self._planning or self._auditing or not self._retire(self.plan_worker):
            self._toast("A crawl is still running — this starts the moment it "
                        "finishes, and nothing is lost by waiting.",
                        tone="warning")
            return

        leads = self._target_leads()
        if not leads:
            self._goto_tab(0)
            self._toast("Import some leads before preparing a campaign.",
                        tone="warning")
            return

        if not self._profile_gate("prepare"):
            return

        template = self._first_touch_template()
        if template is None:
            self._toast("There is no first-touch template to write from, so this "
                        "install has no copy. Reinstalling restores them.",
                        tone="danger")
            return

        name = "%s · %s · %s" % (template.name, _plural(len(leads), "lead"),
                                 datetime.now().strftime("%d %b %H:%M"))
        campaign_id = _db.create_campaign(self.conn, name, template.id,
                                          self._profile(), self.settings)
        if not campaign_id:
            self._toast("Could not create the campaign — the database is "
                        "unavailable.", tone="danger")
            return

        self._campaign_id = campaign_id
        self._planning = True
        self.prepare_btn.setEnabled(False)
        self.goto_sending_btn.hide()
        self.plan_progress.setRange(0, max(1, len(leads)))
        self.plan_progress.setValue(0)
        self.plan_progress.show()
        self.plan_warning.hide()
        self.plan_summary.setText(
            "Auditing and queueing %s. This crawls each website, so it takes a "
            "while." % _plural(len(leads), "lead"))

        worker = _PlanWorker(campaign_id, leads, template.id, self.settings)
        worker.progress_signal.connect(self._on_plan_progress)
        worker.plan_signal.connect(self._on_plan_ready)
        worker.finished.connect(self._on_plan_finished)
        self.plan_worker = worker
        worker.start()
        self._publish_state()

    def _on_plan_progress(self, done: int, total: int, message: str) -> None:
        self.plan_progress.setRange(0, max(1, total))
        self.plan_progress.setValue(done)
        self.plan_summary.setText("%d of %d — %s" % (done, total, message))

    def _on_plan_ready(self, plan: dict) -> None:
        self._plan = plan if isinstance(plan, dict) else {}
        self.plan_progress.hide()
        self._refresh_plan_summary()
        if _int_of(self._plan.get("queued")):
            self.goto_sending_btn.show()
        self._refresh_campaigns()
        self._reload_leads()
        self._refresh_stats()

    def _refresh_plan_summary(self) -> None:
        """What the card says about the schedule, at every stage it has one.

        Three stages and not one. Before a campaign is prepared the card used
        to be blank, which is the one moment the user is deciding whether to
        prepare it — so the window, the caps and the follow-up spacing were
        invisible exactly when they were being agreed to. After a plan there is
        the plan. After a failure there is the reason, and it is in the warning
        label rather than mixed into the summary, because "nothing was queued"
        must not read as a schedule.
        """
        warning = ""
        if not self._plan:
            summary = ("Nothing queued yet. When you prepare a campaign it "
                       "will send %s." % self._rules_summary())
        elif _text_of(self._plan.get("error")) and not _int_of(self._plan.get("queued")):
            summary = ""
            warning = "Nothing was queued: %s." % _text_of(self._plan.get("error"))
        else:
            summary, warning = self._plan_sentence(self._plan)
            if self._plan.get("cancelled"):
                # Stopped part-way. What is queued is real, so say so rather
                # than presenting a partial plan as the whole campaign.
                summary = "Stopped before the whole list was planned.\n" + summary
        self.plan_summary.setText(summary)
        self.plan_warning.setText(warning)
        self.plan_warning.setVisible(bool(warning))

    def _plan_sentence(self, plan: dict) -> tuple:
        """(what was queued, what is wrong with it) — never one paragraph."""
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

        lines = [head + "."]
        followups = _int_of(plan.get("followups"))
        if followups:
            lines.append("Plus %s on the same threads." % _plural(followups, "follow-up"))
        skipped = _int_of(plan.get("skipped"))
        if skipped:
            lines.append("%d skipped — %s."
                         % (skipped, _reason_list(plan.get("skip_reasons"))))
        last = plan.get("last_send")
        if last:
            lines.append("Last message lands %s." % _clock(last))

        warning = ""
        generic = _int_of(plan.get("generic"))
        if generic:
            # The one number a user cannot get anywhere else. Two hundred form
            # letters sent in the belief that they are personal is the failure
            # the accept-list in `core.templates` trades personalisation to
            # avoid, and it is only a good trade if it is visible.
            warning = ("%d of %s could not be personalised — %s."
                       % (generic, _plural(queued, "email"),
                          _generic_sentence(plan.get("generic_reasons"))))
        return "\n".join(lines), warning

    def _on_plan_finished(self) -> None:
        self._planning = False
        self.prepare_btn.setEnabled(True)
        self._publish_state()

    # ── Sending ──────────────────────────────────────────────────────────────

    def _refresh_mode(self) -> None:
        """Say which mode the next run is in, and dress Start to match it.

        The banner this replaces was a full-width 44px QPushButton with a dashed
        border — the same component as the 28px badge in the header, at a
        different height, so the app's loudest safety statement was also its
        clearest example of one control with six sizes. The pill on the shell's
        bar says which mode this is on every screen; what belongs here is what
        the mode costs, and a Start button that cannot be mistaken for the other
        one.
        """
        dry = bool(self.settings.get("dry_run", True))
        self._tell_shell_mode()
        self._set_start_kind("primary" if dry else "danger_primary")

        if dry:
            self.start_btn.setText("Start rehearsal")
            self.send_note.setText(
                "Dry run: messages are rendered and logged, no SMTP connection "
                "is opened, nothing leaves this machine and no quota is spent. "
                "The queue goes back as it was, so the campaign is still ready "
                "to send for real. Turn dry run off in Settings when you are.\n"
                "%s. All of it is editable in Settings." % self._rules_summary())
        else:
            self.start_btn.setText("Start sending")
            self.send_note.setText(
                "Live: this mails real businesses from %s.\n%s. All of it is "
                "editable in Settings."
                % (_plural(len(self._accounts()), "account"),
                   self._rules_summary()))

    def _set_start_kind(self, kind: str) -> None:
        """Rebuild Start in the kind the mode calls for, in place.

        Rebuilt rather than repainted because `components.button` decides the
        colour from the kind at build time and that is the whole point of the
        kind: one green currently means Save, Start Scraping and "mail twenty
        real strangers", and `danger_primary` is the only filled red in the app
        precisely so the last of those cannot wear the first one's clothes.
        """
        if self.start_btn.property("kind") == kind:
            return
        made = components.button(self.start_btn.text(), kind=kind, size="lg",
                                 on_click=self._on_start_clicked)
        made.setEnabled(self.start_btn.isEnabled())
        made.setToolTip(self.start_btn.toolTip())
        self.start_row.insertWidget(self.start_at, made)
        self.start_row.removeWidget(self.start_btn)
        self.start_btn.setParent(None)
        self.start_btn.deleteLater()
        self.start_btn = made

    def _refresh_campaigns(self) -> None:
        rows = _db.list_campaigns(self.conn)
        combo = self.campaign_combo
        previous = self._campaign_id or _int_of(combo.currentData())
        combo.blockSignals(True)
        combo.clear()
        for row in rows:
            combo.addItem("%s  ·  %s" % (_text_of(row.get("name")),
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

    def _spent_accounts(self) -> tuple:
        """(accounts that have room today, accounts that have not)."""
        zone = self.settings.get("send_timezone")
        ramp = None
        if self._campaign_id:
            try:
                ramp = _campaign.campaign_start_day(self.conn, self._campaign_id,
                                                    self.settings)
            except Exception:
                ramp = None
        free, spent = [], []
        for account in self._accounts():
            email = _text_of(account.get("email"))
            cap = max(1, _campaign.account_daily_cap(account, self.settings,
                                                     ramp_start=ramp))
            used = _db.sent_today(self.conn, email, zone)
            (spent if used >= cap else free).append(email)
        return free, spent

    def _send_health(self) -> tuple:
        """(what the queue is doing, why it is not moving) — never "ready".

        The finding this closes is the worst kind: a campaign that had stalled
        reported itself as "N messages ready to send" while the queue sat
        frozen, so the screen's most reassuring sentence was printed at exactly
        the moment nothing was going to happen. Every branch below either
        describes movement or names what is stopping it.
        """
        stats = self._stats()
        queued, total = _int_of(stats.get("queued")), _int_of(stats.get("total"))
        sent = _int_of(stats.get("sent"))

        if not self._campaign_id:
            return "No campaign yet", ("Prepare one on the Campaign tab and it "
                                       "appears here.")
        if self._sending:
            if self._paused:
                return ("Paused after %d of %d" % (sent, total),
                        "The queue keeps its times. Press Resume to carry on.")
            # A running worker is not the same as a moving queue. Outside the
            # window the loop naps and the log says so exactly once, so the
            # screen read "Sending" while nothing left for hours -- the same
            # reassuring-at-the-worst-moment failure this function exists to
            # close, one branch further in.
            now = time.time()
            if not _campaign.in_send_window(now, self.settings):
                return ("Holding — %d of %d done" % (sent, total),
                        "Outside your sending window. The queue restarts at %s; "
                        "widen the window in Settings if that is too late."
                        % _clock(_campaign.next_window_open(now, self.settings)))
            free, _spent = self._spent_accounts()
            if not free:
                return ("Holding — %d of %d done" % (sent, total),
                        "Every account has hit today's cap. Sending resumes "
                        "tomorrow, or raise the cap in Settings.")
            return "Sending — %d of %d done" % (sent, total), ""
        if queued <= 0:
            return ("Nothing left in this campaign's queue",
                    "Prepare another campaign on the Campaign tab to queue more.")

        free, spent = self._spent_accounts()
        if not free and not spent:
            return ("Stalled — %s queued and no account to send from"
                    % _plural(queued, "message"),
                    "Add a Gmail account and an app password in Settings; until "
                    "then nothing in this queue can leave.")

        due = self._next_due_ts()
        if not due:
            return ("Stalled — %s queued, none of it can go out"
                    % _plural(queued, "message"),
                    "Every queued message is addressed to a suppressed address, "
                    "so the send loop skips all of them. Prepare a new campaign "
                    "from the leads you can still contact.")

        now = time.time()
        if due > now:
            return ("%s queued — the next one is due %s"
                    % (_plural(queued, "message"), _clock(due)),
                    "Nothing goes out before then. Press Start sending to have "
                    "the run waiting when it does.")
        if not _campaign.in_send_window(now, self.settings):
            return ("Held — %s overdue, outside the sending window"
                    % _plural(queued, "message"),
                    "The window reopens %s. Widen the days or the hours in "
                    "Settings to send sooner."
                    % _clock(_campaign.next_window_open(now, self.settings)))
        if not free:
            return ("Held — every account has hit today's cap",
                    "%s stays queued until tomorrow. Raise the daily cap, or "
                    "add another account, in Settings."
                    % _plural(queued, "message"))
        return ("Not sending — %s overdue since %s"
                % (_plural(queued, "message"), _clock(due)),
                "The window is open and there is room on the account. Nothing "
                "will leave until you press Start sending.")

    def _refresh_send_controls(self) -> None:
        stats = self._stats()
        queued = _int_of(stats.get("queued"))
        total = _int_of(stats.get("total"))

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
            "Rehearse this campaign's queue — nothing is sent"
            if self.settings.get("dry_run", True) else
            "Mail this campaign's queue to real businesses, on the schedule in "
            "Settings. You will be asked to confirm."))
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

        headline, why = self._send_health()
        self.send_status.setText(headline)
        self.send_reason.setText(why)
        self.send_reason.setVisible(bool(why))

    def _on_start_clicked(self) -> None:
        if self._sending:
            return
        if not self._retire(self.send_worker):
            self._toast("The last run is still closing its connection. Press "
                        "Start again in a moment.", tone="warning")
            return
        if not self._campaign_id:
            self._goto_tab(1)
            self._toast("There is no campaign yet. Prepare one here and it "
                        "appears on the Sending tab.", tone="warning")
            return
        queued = _int_of(self._stats().get("queued"))
        if queued <= 0:
            self._toast("This campaign has nothing queued. Prepare one first.",
                        tone="warning")
            return
        if not self._profile_gate("send"):
            return

        dry = bool(self.settings.get("dry_run", True))
        if not dry and not components.confirm(
                self,
                title="Mail %s to real businesses?" % _plural(queued, "message"),
                body="Dry run is off, so this campaign sends for real, from %s, "
                     "on the schedule in Settings: %s.\n\nThere is no way to "
                     "recall a message once it has left."
                     % (_plural(len(self._accounts()), "account"),
                        self._rules_summary()),
                confirm_text="Send for real",
                danger=True, remember_key=""):
            return

        self._clear_log()
        self._rehearsed.clear()
        worker = OutreachWorker(self._campaign_id, self.settings, dry_run=dry)
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
        self._publish_state()

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
        self._publish_state()

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
        self._publish_state()

    def _on_send_done(self) -> None:
        self._sending = False
        self._paused = False
        self.pause_btn.setText("Pause")
        self._reload_leads()
        self._refresh_stats()
        self._refresh_campaigns()
        self._publish_state()

    # ── Sending: the activity log ────────────────────────────────────────────

    def _clear_log(self) -> None:
        self._log_lines = []
        self._repaint_log()

    def _append_log(self, message: str, level: str = "info") -> None:
        self._log_lines.insert(0, ("%s  %s" % (datetime.now().strftime("%H:%M:%S"),
                                               message), level))
        del self._log_lines[_LOG_LIMIT:]
        self._repaint_log()

    # Which tier of ink a line takes. A failure has to look like one at a
    # glance, and a delivery like a delivery, in a panel that is otherwise 400
    # identical grey lines.
    _LOG_INK = {"error": "danger.text", "done": "accent.text",
                "active": "text.primary"}

    def _repaint_log(self) -> None:
        """Draw the log, or the sentence that says what would fill it.

        Same treatment as the suppression list: an unexplained bordered box is
        the one surface on this screen that would not say what belongs in it.
        """
        t = components.active_theme()
        self.log_list.clear()
        if not self._log_lines:
            item = QListWidgetItem(
                "Nothing sent yet. Press Start and each message appears here "
                "as it goes out.")
            item.setFlags(Qt.NoItemFlags)
            self.log_list.addItem(item)
            return
        for message, level in self._log_lines:
            item = QListWidgetItem(message)
            item.setForeground(QColor(
                t.color[self._LOG_INK.get(level, "text.secondary")]))
            self.log_list.addItem(item)

    # ── Sending: accounts and countdown ──────────────────────────────────────

    def _refresh_accounts(self) -> None:
        t = components.active_theme()
        _clear_layout(self.accounts_holder)
        accounts = self._accounts()
        if not accounts:
            self.accounts_holder.addWidget(components.hint(
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

            holder = QWidget()
            row = _rows(holder, margin="0", spacing="1", t=t)
            head = _cols(margin="0", spacing="2", t=t)
            address = _ElidedLabel(email)
            head.addWidget(address, stretch=1)

            # Two separate numbers, never one sum: a rehearsal has spent none of
            # this account's real quota and the card must not imply that it has.
            counter = components.body_label(
                "%d / %d today" % (used, cap),
                tone="danger" if used >= cap else "secondary")
            counter.setWordWrap(False)
            tip = "Daily cap for this account, warm-up included"
            if rehearsed:
                counter.setText("%d rehearsed  ·  %d / %d today"
                                % (rehearsed, used, cap))
                tip += ".  %s rehearsed in this dry run — no real quota spent." \
                    % _plural(rehearsed, "message")
            counter.setToolTip(tip)
            head.addWidget(counter)
            row.addLayout(head)

            bar = _thin_bar(t)
            bar.setRange(0, cap)
            # The rehearsal moves the bar because the bar is what the user
            # watches while the run goes; `used` is what governs the cap.
            bar.setValue(min(used + rehearsed, cap))
            row.addWidget(bar)
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
        for row in _db.due_messages(self.conn, horizon, limit=_LOG_LIMIT):
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
        now = time.time()
        if not due:
            self.next_send_label.setText("")
        elif self._paused:
            self.next_send_label.setText("Paused · next was due %s" % _clock(due))
        elif due > now:
            self.next_send_label.setText("Next send in %s · %s"
                                         % (_countdown(due - now), _clock(due)))
        elif self._sending:
            self.next_send_label.setText("Sending now")
        else:
            self.next_send_label.setText("Overdue since %s" % _clock(due))
        self._refresh_send_controls()

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
        self.stats_campaign.setText(_text_of(row.get("name")) or "No campaign yet")

    def _paint_stats(self, stats: dict) -> None:
        stats = stats if isinstance(stats, dict) else {}
        for key, tile in self.tiles.items():
            tile.value_label.setText(str(_int_of(stats.get(key))))

    # A campaign of five hundred is a long list to walk on a tab change, and the
    # answer past a couple of thousand would not change what the user does about
    # it. Each lead past the cap is a rendered context, at a third of a
    # millisecond each.
    _PERSONAL_SCAN_MAX = 2000

    def _personalisation_counts(self) -> tuple:
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
        counts: dict = {}
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
            self.personal_note.setText(
                "%d of %s could not be personalised — %s. Those say nothing "
                "about the business they are addressed to beyond its name."
                % (generic, _plural(total, "email"), _generic_sentence(why)))
        else:
            self.personal_note.setText(
                "All %s in this campaign say something about the business they "
                "are addressed to." % _plural(total, "email"))
        self.personal_note.setVisible(bool(total))

    def _per_day(self) -> list:
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

        buckets: dict = {}
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
            item.setToolTip("%s — %s" % (email, reason or "no reason recorded"))
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
            self._toast("Select an address to remove first.", tone="warning")
            return
        self._undo_suppress([(email, "new")])

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
        index = max(0, min(len(self.TABS) - 1, _int_of(index)))
        self.pages.setCurrentIndex(index)
        self._on_tab_changed(index)
        self._tell_shell(index)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self._refresh_lead_actions()
        elif index == 1:
            self._refresh_templates()
            self._refresh_profile()
            self._refresh_preview()
        elif index == 2:
            self._refresh_accounts()
            self._refresh_send_controls()
        elif index == 3:
            self._refresh_stats()

    def _toast(self, message: str, *, tone: str = "info", action=None,
               on_action=None):
        """One message, in a channel that says whether it is good news.

        The six-second QLabel this replaces said everything in one voice and
        took itself off screen whatever it said, so a suppression that could not
        be undone read exactly like an import that worked, and both were gone
        before they were read. A danger toast waits for the user, and one
        carrying an action waits long enough to reach it.
        """
        return self.toaster.show(message, tone=tone, action=action,
                                 on_action=on_action)
