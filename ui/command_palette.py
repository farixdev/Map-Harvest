"""One surface that reaches every destination and every action by typing.

The audit found no shortcuts, no mnemonics and no menu bar anywhere in the app,
which for a product this dense is not a missing nicety: the outreach screen
alone carries four tab pages, a lead table, a campaign form and a send loop, and
every one of them was reachable only by moving a pointer to it. `ui/app.py` now
owns a keyboard layer — the menu bar spells the keys out and Escape and Return
mean what they mean everywhere else — and this is the half of it that scales.
A menu bar can hold a dozen actions before it stops being readable; a palette
holds every action the app has and stays one keystroke away from all of them.

It knows nothing about screens, and that is the point of the seam. The shell
hands `open_with` a list of `Command` records — a name, something to run, and a
question to ask about whether it can be run right now — so a screen contributes
its own entries through the shell without this module ever importing one, and
without growing a branch per screen. The shell rebuilds that list on every open,
so what the palette offers is what the app can actually do at that instant:
Stop sending is dimmed until a campaign is running, and picking a dimmed row is
refused rather than quietly doing nothing. Dimmed and not hidden, because the
answer to "can this app stop a send" is yes, and a list that hides everything
unavailable teaches nothing about what the app is.

It is a child widget covering the shell rather than a QDialog, and that is
measured rather than stylistic. Qt 5.15 on the offscreen platform aborts the
process on any modal that is actually shown — a stock QMessageBox with no
project code in it is enough — so a palette built as a modal dialog could not be
tested at all: the suite would go green by never opening it, which is exactly
how a process-killing crash sat behind 673 passing tests for weeks. A plain
child widget takes the focus, paints the scrim over the screen behind it, and
opens and closes under a test that can then read every row it shows.

Matching is a subsequence rather than a substring: "prep" finds Prepare
campaign, and "gto" finds Go to Outreach. Ranking prefers matches that start a
word and matches that run together, so the command the user is thinking of is
the one already selected when they stop typing.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt5.QtCore import QEvent, QRect, QSize, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QFrame, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from ui import components as C

# The palette in the two units it is actually being asked for: how long a
# command name is, and how many of them are worth showing before the list is a
# page rather than a shortlist. Both are read through the font that draws them,
# so Windows text scaling moves the box instead of clipping it.
COLUMNS, ROWS = 54, 8

# What a matched character is worth. A run of them is worth more than the same
# characters scattered through the name, and one that starts a word is worth
# more again — which is what puts "Outreach — Start sending" above "Go to
# Outreach" for the query "out", where a plain count of hits ties them.
_HIT, _RUN, _WORD = 1, 3, 5


# ── What a command is ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Command:
    """One line in the palette: a name, a thing to do, and where it lives.

    `available` is a question and not a flag because the answer changes while
    the palette is closed — a send starts, a selection empties, a campaign is
    prepared — and a flag captured at registration would be a lie by the time
    anybody read it.
    """

    key: str
    title: str
    run: Callable[[], None]
    where: str = ""
    shortcut: str = ""
    available: Optional[Callable[[], bool]] = None

    def enabled(self) -> bool:
        if self.available is None:
            return True
        try:
            return bool(self.available())
        except Exception:
            return False

    def hint(self) -> str:
        """The right-hand column: the key that also does this, or its home."""
        return self.shortcut or self.where


def score(query: str, title: str):
    """How well `title` answers `query`, or None when it does not answer it.

    Every character of the query has to appear in the name, in order, and that
    is the whole test for whether a row is shown; everything else here decides
    which of the rows that pass is selected first.
    """
    if not query:
        return _HIT
    wanted, name = query.lower(), title.lower()
    at, points, running, first = 0, 0, False, -1
    for index, letter in enumerate(name):
        if at >= len(wanted) or letter != wanted[at]:
            running = False
            continue
        if first < 0:
            first = index
        points += _RUN if running else _HIT
        if index == 0 or not name[index - 1].isalnum():
            points += _WORD
        at += 1
        running = True
    if at < len(wanted):
        return None
    return points - first


def rank(commands, query: str) -> list:
    """The commands that match, best first, registration order breaking ties.

    An empty query is not a tie to be broken but a question nobody has asked
    yet, so it answers in the order the shell registered: destinations, then
    appearance, then whatever the screen on show contributes. Ranked by name
    length instead — which is what a score every row shares comes down to — the
    palette opened on whichever command happened to have the shortest label.
    """
    if not query:
        return list(commands)
    scored = []
    for order, command in enumerate(commands):
        points = score(query, command.title)
        if points is not None:
            scored.append((-points, len(command.title), order, command))
    scored.sort(key=lambda row: row[:3])
    return [row[3] for row in scored]


# ── The row ──────────────────────────────────────────────────────────────────


class _CommandRow(QStyledItemDelegate):
    """Two columns in one row: what it is called, and how else to reach it.

    Painted rather than laid out. A row per command built from labels is eight
    widgets each, rebuilt on every keystroke of the query, and the second column
    has to right-align against a list whose width the sheet decides; a delegate
    draws both columns in one pass and costs nothing to redraw.
    """

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._theme = t

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self._theme.control["row"])

    def paint(self, painter, option, index) -> None:
        t = self._theme
        title = index.data(Qt.DisplayRole) or ""
        hint = index.data(Qt.UserRole) or ""
        live = bool(index.data(Qt.UserRole + 1))
        chosen = bool(option.state & QStyle.State_Selected) and live

        painter.save()
        if chosen:
            painter.fillRect(option.rect, QColor(t.color["surfaceActive"]))
            # The same rail a selected row wears everywhere else in the app,
            # because selection has to outweigh focus and this row has both.
            rail = QRect(option.rect)
            rail.setWidth(t.space["hair"])
            painter.fillRect(rail, QColor(t.color["accent.default"]))

        if not live:
            ink, second = t.color["text.disabled"], t.color["text.disabled"]
        elif chosen:
            ink, second = t.color["text.onAccent"], t.color["text.onAccent"]
        else:
            ink, second = t.color["text.primary"], t.color["text.tertiary"]

        box = option.rect.adjusted(t.space["3"], t.space["0"],
                                   -t.space["3"], t.space["0"])
        metrics = option.fontMetrics
        taken = metrics.horizontalAdvance(hint) + t.space["4"] if hint else 0
        named = QRect(box)
        named.setWidth(max(box.width() - taken, t.space["1"]))

        painter.setPen(QColor(ink))
        painter.drawText(named, Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(title, Qt.ElideRight,
                                            named.width()))
        if hint:
            painter.setPen(QColor(second))
            painter.drawText(box, Qt.AlignRight | Qt.AlignVCenter, hint)
        painter.restore()


# ── The palette ──────────────────────────────────────────────────────────────


class CommandPalette(QWidget):
    """The scrim, the query, and the shortlist under it.

    It covers its parent whole. That is what makes the scrim possible and it is
    also what makes dismissal obvious: everywhere outside the card is a click
    that closes, and there is no way to leave the app in a state where the
    palette is open and something behind it has the focus.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("command_scrim")
        # Without this the sheet's `background-color` on this widget is parsed
        # and then ignored: Qt honours the box model on a QWidget *subclass*
        # only when it is told the background is styled, so the scrim rule
        # existed, matched, and painted nothing at all. Measured as paint: the
        # ground behind the card came out `canvas` rather than 34% of it.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._theme = C.active_theme()
        self._commands: list = []
        self._shown: list = []
        self.hide()
        self._build()
        if parent is not None:
            parent.installEventFilter(self)

    # ── Building ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        t = self._theme
        self.card = QFrame(self)
        self.card.setObjectName("command_card")
        page = QVBoxLayout(self.card)
        page.setContentsMargins(t.space["4"], t.space["4"], t.space["4"],
                                t.space["4"])
        page.setSpacing(t.space["3"])

        self.query = C.search_field("Type a command…", max_chars=COLUMNS)
        self.query.setClearButtonEnabled(False)
        self.query.textChanged.connect(self._on_query)
        self.query.installEventFilter(self)
        page.addWidget(self.query)

        self.list = QListWidget(self.card)
        self.list.setObjectName("command_list")
        self.list.setItemDelegate(_CommandRow(t, self.list))
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFixedHeight(ROWS * t.control["row"])
        self.list.itemActivated.connect(lambda item: self._run(item))
        self.list.itemClicked.connect(lambda item: self._run(item))
        page.addWidget(self.list)

        self.nothing = C.body_label("No command goes by that name.",
                                    tone="tertiary")
        self.nothing.hide()
        page.addWidget(self.nothing)

    def restyle(self, t) -> None:
        """Wear `t`, by building the whole thing again in it.

        Same reason the shell's own bar is rebuilt rather than repolished:
        `ui/components.py` resolves its colours in Python at build time and
        writes them into each widget's own sheet, which beats the application's.
        """
        self._theme = t
        was_open = self.isVisible()
        self.card.setParent(None)
        self.card.deleteLater()
        self._build()
        if was_open:
            self.open_with(self._commands)

    # ── Opening and closing ──────────────────────────────────────────────

    def open_with(self, commands) -> None:
        """Show the palette over `commands`, with the query already empty."""
        self._commands = list(commands)
        self.query.blockSignals(True)
        self.query.clear()
        self.query.blockSignals(False)
        self._fill("")
        self._place()
        self.show()
        self.raise_()
        self.query.setFocus(Qt.ShortcutFocusReason)

    def dismiss(self) -> None:
        self.hide()

    def commands(self) -> list:
        """What the palette is showing, in the order it is showing it."""
        return list(self._shown)

    def highlighted(self):
        row = self.list.currentRow()
        return self._shown[row] if 0 <= row < len(self._shown) else None

    # ── The query ────────────────────────────────────────────────────────

    def _on_query(self, text: str) -> None:
        self._fill(text)

    def _fill(self, text: str) -> None:
        self._shown = rank(self._commands, text.strip())
        self.list.clear()
        for command in self._shown:
            item = QListWidgetItem(command.title)
            item.setData(Qt.UserRole, command.hint())
            item.setData(Qt.UserRole + 1, command.enabled())
            item.setToolTip(command.title)
            self.list.addItem(item)
        self.nothing.setVisible(not self._shown)
        self.list.setVisible(bool(self._shown))
        self._size_list()
        self._select(0, +1)
        if self.isVisible():
            # Measured again because the list has just changed height, and a
            # card whose geometry was set once keeps the room the first answer
            # needed however short the next one is.
            self._place()

    def _size_list(self) -> None:
        """As tall as it has rows, up to the ceiling a shortlist is allowed.

        Sized rather than scrolled to a fixed box: a query that leaves two
        commands standing should read as two commands, not as two rows adrift
        in six of empty well.
        """
        rows = min(max(len(self._shown), 1), ROWS)
        self.list.setFixedHeight(rows * self._theme.control["row"])

    def _select(self, start: int, step: int) -> None:
        """Land on the first runnable row at or after `start`.

        A dimmed row is shown because knowing the app can do something is worth
        more than hiding it, and skipped because a Return that lands on one
        would do nothing at all and say nothing about why.
        """
        count = len(self._shown)
        for offset in range(count):
            at = (start + offset * step) % count if count else -1
            if at < 0:
                break
            if self._shown[at].enabled():
                self.list.setCurrentRow(at)
                return
        self.list.setCurrentRow(-1)

    def _step(self, step: int) -> None:
        row = self.list.currentRow()
        start = (row + step) % len(self._shown) if self._shown else 0
        self._select(start, step)

    def _run(self, item=None) -> None:
        # A double click is `itemClicked` and then `itemActivated`, and the
        # first of them has already closed the palette — so without this, one
        # double click on Start sending starts it twice.
        if not self.isVisible():
            return
        command = self.highlighted()
        if item is not None:
            row = self.list.row(item)
            if 0 <= row < len(self._shown):
                command = self._shown[row]
        if command is None or not command.enabled():
            return
        self.dismiss()
        command.run()

    # ── Where it sits ────────────────────────────────────────────────────

    def _place(self) -> None:
        """Over the whole parent, with the card a fifth of the way down it.

        A fifth rather than centred: the list grows downwards as the query
        shortens, and a card pinned to the middle moves under the reader's eyes
        every time they delete a character.
        """
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        t = self._theme
        metrics = self.query.fontMetrics()
        width = min(metrics.horizontalAdvance("n" * COLUMNS) + 2 * t.space["4"],
                    max(parent.width() - 2 * t.space["7"], t.control["lg"]))
        self.card.adjustSize()
        height = min(self.card.sizeHint().height(),
                     max(parent.height() - 2 * t.space["7"], t.control["lg"]))
        self.card.setGeometry((parent.width() - width) // 2,
                              parent.height() // 5, width, height)

    # ── Keys and clicks ──────────────────────────────────────────────────

    def eventFilter(self, watched, event) -> bool:
        """The query box's arrows, and the parent's resizes.

        The keys are taken here rather than in `keyPressEvent` because the field
        has the focus the whole time the palette is open — that is what lets the
        user keep typing — and a QLineEdit answers Up and Down itself by moving
        the caret to the ends of the text.
        """
        if watched is self.query and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._step(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._run()
                return True
            if key == Qt.Key_Escape:
                self.dismiss()
                return True
        if watched is self.parentWidget() and event.type() == QEvent.Resize \
                and self.isVisible():
            self._place()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.dismiss()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        """Anywhere outside the card is a way out."""
        if not self.card.geometry().contains(event.pos()):
            self.dismiss()
            return
        super().mousePressEvent(event)
