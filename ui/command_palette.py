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

The shape is the one Spotlight and Raycast settled on and it is three
decisions, each answering something a flat list of names does badly:

* **A query field that is the largest thing in the panel**, flush to the top
  edge with a rule under it rather than floating inside a padded box. It is the
  only control here, the caret is already in it, and everything below it is a
  consequence of what is typed into it — so it reads as the surface rather than
  as a widget on the surface.
* **Headings over runs of rows.** Forty-odd commands in one column is a list to
  read; the same forty under "Destination", "Section" and "Actions" is a list to
  scan. A heading is not a row: it takes no selection, and the arrow keys step
  straight past it, so the keyboard never lands anywhere Return would do
  nothing.
* **A mark on every row.** A column of identical text is told apart only by
  reading it, which is what a palette exists to avoid — the eye should reach
  Start sending by its shape before it has finished reading "Start".

There is no drop shadow under the card and that is deliberate rather than
missed. Depth here is already carried by the scrim: the panel sits on 55–66%
black over the whole window in both palettes, which separates it from the page
by more than a shadow could add — and a black shadow cast onto a black wash is
a blur nobody can see, re-rendered through a graphics effect every time the
list changes height, which is every keystroke.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt5.QtCore import QEvent, QRect, QSize, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QFrame, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from ui import components as C
from ui import icons as I
from ui import theme as _theme

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

# Where a command goes when its `where` is its own label rather than a place.
# The shell writes the control's own wording into `where` — "Start rehearsal",
# "Audit 40 leads" — which is worth showing on the row and is not a heading,
# because a heading with one row under it is a row with a title on it.
OTHER = "Actions"

# The mark a row wears, read off the verb the shell already wrote into its
# name. Read off the name and not handed down from the shell because the seam
# is the whole point of this file: a screen contributes a command without this
# module importing one, so it contributes a *sentence*, and the sentence is
# what there is to go on. Ordered, first hit wins, so the narrower pairs sit
# above the wider ones: "go back" before "go to", and "start" above "scrap" so
# that "Scrape — Start scraping" is a run rather than a search.
_MARKS = (
    ("go back", "chevron-left"),
    ("go to", "chevron-right"),
    ("dry run", "eye"),
    ("sidebar", "columns"),
    ("theme", "eye"),
    ("density", "table"),
    ("settings", "gear"),
    ("prepare", "check"),
    ("start", "play"),
    ("pause", "pause"),
    ("stop", "stop"),
    ("audit", "search"),
    ("scrap", "search"),
    ("export", "document"),
    ("copy", "copy"),
    ("suppress", "minus"),
    ("template", "document"),
    ("save", "check"),
    ("discard", "reset"),
    ("send", "send"),
    ("mail", "mail"),
    ("account", "person"),
    ("stats", "chart"),
    ("compose", "pencil"),
    ("lead", "table"),
)

# What a row wears when nothing in its name is recognised. A command is a thing
# to go and do, so the default is the mark that says "this leads somewhere"
# rather than a mark that claims to know what kind of thing it is.
_DEFAULT_MARK = "chevron-right"

# The three moves that drive the whole surface, said in words rather than in
# arrow and return glyphs. Those arrive from the text font, ignore the icon
# set's weight and the palette's ink, and are missing outright from some of the
# faces Windows will substitute — which is a footer that reads as three empty
# boxes on exactly the machine least able to explain why.
KEYS = "Arrows move · Return runs · Escape closes"


# ── What a command is ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Command:
    """One line in the palette: a name, a thing to do, and where it lives.

    `available` is a question and not a flag because the answer changes while
    the palette is closed — a send starts, a selection empties, a campaign is
    prepared — and a flag captured at registration would be a lie by the time
    anybody read it.

    `group` and `icon` are both optional and both have an answer when they are
    left out, which is what keeps every existing caller a caller: the heading
    falls back to `where` and the mark is read off the name.
    """

    key: str
    title: str
    run: Callable[[], None]
    where: str = ""
    shortcut: str = ""
    available: Optional[Callable[[], bool]] = None
    group: str = ""
    icon: str = ""

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

    def mark(self) -> str:
        """The icon on the left: the one it was given, or the one it earns."""
        if self.icon in I.ICONS:
            return self.icon
        name = self.title.lower()
        for word, drawn in _MARKS:
            if word in name:
                return drawn
        return _DEFAULT_MARK


def headings(commands) -> dict:
    """Which heading each command sits under, decided over the whole list.

    A `where` two or more commands share is a place — Destination, Section,
    Appearance — and earns a heading. A `where` only one command has is not a
    place at all: the shell writes a screen's own control label in there, so
    "Start rehearsal" is that row's right-hand column and never a group of one.

    Decided over everything registered rather than over what a query left
    standing, so a heading does not change under the reader as they type: with
    the count taken from the matches, typing four letters until one destination
    is left would move that destination out of Destination and into Actions.
    """
    shared: dict = {}
    for command in commands:
        shared[command.where] = shared.get(command.where, 0) + 1
    named = {}
    for command in commands:
        if command.group:
            named[command.key] = command.group
        elif command.where and shared.get(command.where, 0) > 1:
            named[command.key] = command.where
        else:
            named[command.key] = OTHER
    return named


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

# What each row carries beyond its name. Named rather than written as
# `Qt.UserRole + 2` at the two ends of the file, because a delegate reading one
# role and a list writing another is a bug that renders as an empty column.
HINT, LIVE, HEADING, MARK = (Qt.UserRole, Qt.UserRole + 1,
                             Qt.UserRole + 2, Qt.UserRole + 3)

# Qt 5's QFont weight runs 0-99 and the type scale is written in CSS weights,
# which is the scale the generated sheet speaks. Its parser converts one to the
# other by dividing by eight, so a delegate — which paints with a QFont and has
# no sheet to hand a rule to — converts the same way, and a heading painted here
# comes out the weight a `components.section_label` comes out beside it.
_CSS_TO_QT = 8


def _qt_weight(css) -> int:
    return max(0, min(99, int(css) // _CSS_TO_QT))


class _CommandRow(QStyledItemDelegate):
    """A heading, or a mark and two columns: what it is called and how else to
    reach it.

    Painted rather than laid out. A row per command built from labels is eight
    widgets each, rebuilt on every keystroke of the query, and the second column
    has to right-align against a list whose width the sheet decides; a delegate
    draws every column in one pass and costs nothing to redraw.
    """

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._theme = t

    def sizeHint(self, option, index) -> QSize:
        t = self._theme
        tall = t.control["xs"] if index.data(HEADING) else t.control["row"]
        return QSize(option.rect.width(), tall)

    def paint(self, painter, option, index) -> None:
        painter.save()
        if index.data(HEADING):
            self._heading(painter, option, index)
        else:
            self._command(painter, option, index)
        painter.restore()

    def _heading(self, painter, option, index) -> None:
        """The caption over a run of rows, in the app's own section register."""
        t = self._theme
        size, weight = t.font["caption"]
        font = QFont(option.font)
        font.setPixelSize(size)
        font.setWeight(_qt_weight(weight))
        font.setLetterSpacing(QFont.AbsoluteSpacing, _theme.TRACKING["caption"])
        painter.setFont(font)
        painter.setPen(QColor(t.color["text.tertiary"]))
        box = option.rect.adjusted(t.space["3"], t.space["0"],
                                   -t.space["3"], t.space["0"])
        painter.drawText(box, Qt.AlignLeft | Qt.AlignBottom,
                         str(index.data(Qt.DisplayRole) or "").upper())

    def _command(self, painter, option, index) -> None:
        t = self._theme
        title = index.data(Qt.DisplayRole) or ""
        hint = index.data(HINT) or ""
        live = bool(index.data(LIVE))
        chosen = bool(option.state & QStyle.State_Selected) and live

        if chosen:
            painter.fillRect(option.rect, QColor(t.color["surfaceActive"]))
            # The same rail a selected row wears everywhere else in the app,
            # because selection has to outweigh focus and this row has both.
            rail = QRect(option.rect)
            rail.setWidth(t.space["hair"])
            painter.fillRect(rail, QColor(t.color["accent.default"]))

        if not live:
            ink, second, tone = (t.color["text.disabled"],
                                 t.color["text.disabled"], "disabled")
        elif chosen:
            ink, second, tone = (t.color["text.onAccent"],
                                 t.color["text.onAccent"], "onAccent")
        else:
            ink, second, tone = (t.color["text.primary"],
                                 t.color["text.tertiary"], "secondary")

        box = option.rect.adjusted(t.space["3"], t.space["0"],
                                   -t.space["3"], t.space["0"])
        # `ui/icons.py` resolves its own colour from the palette the app is
        # wearing, which is set before this widget is rebuilt in it, so the mark
        # and the ink beside it are always the same theme's.
        drawn = I.pixels("sm")
        mark = QRect(box.left(), box.top() + (box.height() - drawn) // 2,
                     drawn, drawn)
        painter.drawPixmap(mark, I.pixmap(str(index.data(MARK) or _DEFAULT_MARK),
                                          tone=tone, size="sm"))
        box.setLeft(mark.right() + t.space["2"])

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
        # Which command each row of the list widget is, headings being -1. The
        # two are no longer the same index and every keystroke depends on the
        # difference: an arrow key steps through commands and the selection it
        # sets is a row.
        self._at: list = []
        self._headings: dict = {}
        self.hide()
        self._build()
        if parent is not None:
            parent.installEventFilter(self)

    # ── Building ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        """The query, the rule under it, the list, and the keys in the footer.

        Zero margins on the card and margins on each band inside it, so the two
        rules run edge to edge. A rule inset from the panel's own border reads
        as a line somebody drew; a rule that meets both edges is the seam
        between two parts of one object, which is what these are.
        """
        t = self._theme
        self.card = QFrame(self)
        self.card.setObjectName("command_card")
        page = QVBoxLayout(self.card)
        page.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                t.space["0"])
        page.setSpacing(t.space["0"])

        self.query = C.search_field("Type a command…", max_chars=COLUMNS)
        self.query.setClearButtonEnabled(False)
        # `search_field` is the filter box a toolbar carries — `control.sm` of
        # height, `small` of type, in a bordered well — and here it is not a
        # control on a surface, it is the surface. So it takes the height and
        # the size a primary control takes, and it gives up the well: a box
        # drawn inside a panel whose whole purpose is that box reads as a form
        # to fill in rather than as a caret to type at, and the ring that box
        # would grow on focus would be a ring around the only focusable thing
        # in the card. Everything the sheet says about a QLineEdit that these
        # four declarations do not name still applies.
        self.query.setFixedHeight(t.control["lg"])
        self.query.setStyleSheet(
            "QLineEdit { font-size: %dpx; padding: %dpx %dpx; "
            "background: transparent; border: none; }"
            % (t.font["h2"][0], t.space["0"], t.space["1"]))
        self.query.textChanged.connect(self._on_query)
        self.query.installEventFilter(self)
        page.addWidget(self._band(self.query, "3", "2"))
        page.addWidget(C.divider())

        self.list = QListWidget(self.card)
        self.list.setObjectName("command_list")
        self.list.setItemDelegate(_CommandRow(t, self.list))
        # Off, because the rows are no longer one height: a heading is shorter
        # than a command and with this on Qt paints every row at the first one's
        # size, which draws each command inside a heading's box.
        self.list.setUniformItemSizes(False)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFixedHeight(ROWS * t.control["row"])
        self.list.itemActivated.connect(lambda item: self._run(item))
        self.list.itemClicked.connect(lambda item: self._run(item))

        self.nothing = C.body_label("No command goes by that name.",
                                    tone="tertiary")
        self.nothing.hide()
        self.body = QWidget(self.card)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(t.space["1"], t.space["1"], t.space["1"],
                                t.space["1"])
        body.setSpacing(t.space["1"])
        body.addWidget(self.list)
        body.addWidget(self.nothing)
        page.addWidget(self.body)

        page.addWidget(C.divider())
        self.keys = C.hint(KEYS)
        page.addWidget(self._band(self.keys, "3", "2"))

    def _band(self, inner: QWidget, side: str, ends: str) -> QWidget:
        """One horizontal band of the card, inset from the two rules it sits
        between."""
        t = self._theme
        holder = QWidget(self.card)
        box = QVBoxLayout(holder)
        box.setContentsMargins(t.space[side], t.space[ends], t.space[side],
                               t.space[ends])
        box.setSpacing(t.space["0"])
        box.addWidget(inner)
        return holder

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
        self._headings = headings(self._commands)
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

    def groups(self) -> list:
        """The headings on show, in the order they are drawn."""
        seen = []
        for command in self._shown:
            name = self._headings.get(command.key, OTHER)
            if name not in seen:
                seen.append(name)
        return seen

    def highlighted(self):
        at = self._command_at(self.list.currentRow())
        return self._shown[at] if 0 <= at < len(self._shown) else None

    # ── The query ────────────────────────────────────────────────────────

    def _on_query(self, text: str) -> None:
        self._fill(text)

    def _fill(self, text: str) -> None:
        """Rebuild the list: the matches, gathered under one heading each.

        Gathered rather than partitioned where the ranking happens to change
        group, which is the version this replaced and which drew Actions twice
        with Section between them — the shell registers the dry-run switch
        among the appearance commands and every screen's own actions after its
        sections, so a run-length heading is a heading per run and not per
        group.

        The order inside a group is the ranking's, and the order *of* the
        groups is the ranking's too — whichever group the best match sits in is
        drawn first, so what was typed decides the top of the list rather than
        a registration order the reader cannot see.
        """
        order, gathered = [], {}
        for command in rank(self._commands, text.strip()):
            name = self._headings.get(command.key, OTHER)
            if name not in gathered:
                gathered[name] = []
                order.append(name)
            gathered[name].append(command)

        self.list.clear()
        self._shown, self._at = [], []
        for name in order:
            head = QListWidgetItem(name)
            head.setData(HEADING, True)
            head.setFlags(Qt.NoItemFlags)
            self.list.addItem(head)
            self._at.append(-1)
            for command in gathered[name]:
                item = QListWidgetItem(command.title)
                item.setData(HINT, "" if command.hint() == name
                             else command.hint())
                item.setData(LIVE, command.enabled())
                item.setData(MARK, command.mark())
                item.setToolTip(command.title)
                self.list.addItem(item)
                self._at.append(len(self._shown))
                self._shown.append(command)
        self.nothing.setVisible(not self._shown)
        self.list.setVisible(bool(self._shown))
        self._size_list()
        self._select(0, +1)
        if self.isVisible():
            # Measured again because the list has just changed height, and a
            # card whose geometry was set once keeps the room the first answer
            # needed however short the next one is.
            self._place()

    def _command_at(self, row: int):
        """Which command a row of the list is, or -1 for a heading."""
        return self._at[row] if 0 <= row < len(self._at) else -1

    def _row_of(self, at: int) -> int:
        """Which row of the list a command is, or -1 for one not on show."""
        try:
            return self._at.index(at)
        except ValueError:
            return -1

    def _size_list(self) -> None:
        """As tall as it has rows, up to the ceiling a shortlist is allowed.

        Sized rather than scrolled to a fixed box: a query that leaves two
        commands standing should read as two commands, not as two rows adrift
        in six of empty well. Counted in commands and measured in pixels,
        because a heading is neither a command nor the same height as one — a
        ceiling of eight rows that spent three of them on headings would show
        five of the answers it found.
        """
        t = self._theme
        counted, tall = 0, 0
        for row, at in enumerate(self._at):
            # Tested before the row is measured rather than after, so the
            # heading of the group the ceiling cuts off is left outside the box
            # with the rows it would have introduced.
            if counted >= ROWS:
                break
            counted += 1 if at >= 0 else 0
            tall += self.list.sizeHintForRow(row)
        self.list.setFixedHeight(max(tall, t.control["row"]))

    def _select(self, start: int, step: int) -> None:
        """Land on the first runnable command at or after `start`.

        A dimmed row is shown because knowing the app can do something is worth
        more than hiding it, and skipped because a Return that lands on one
        would do nothing at all and say nothing about why. A heading is skipped
        for the harder version of the same reason: it is not a command, so
        stopping on one would leave Return with nothing to run at all.
        """
        count = len(self._shown)
        for offset in range(count):
            at = (start + offset * step) % count if count else -1
            if at < 0:
                break
            if self._shown[at].enabled():
                self.list.setCurrentRow(self._row_of(at))
                return
        self.list.setCurrentRow(-1)

    def _step(self, step: int) -> None:
        at = self._command_at(self.list.currentRow())
        start = (at + step) % len(self._shown) if self._shown else 0
        self._select(start, step)

    def _run(self, item=None) -> None:
        # A double click is `itemClicked` and then `itemActivated`, and the
        # first of them has already closed the palette — so without this, one
        # double click on Start sending starts it twice.
        if not self.isVisible():
            return
        command = self.highlighted()
        if item is not None:
            at = self._command_at(self.list.row(item))
            command = self._shown[at] if at >= 0 else None
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
