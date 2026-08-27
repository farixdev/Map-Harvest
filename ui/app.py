"""The window, the shell every screen sits in, and an orderly shutdown.

Everything this module used to say about how the app *looks* now lives in
`ui/theme.py` and `ui/components.py`. What is left is chrome and lifecycle:
which screen is showing, one bar that says so, and which background threads
have to be stopped before the window can go away.

Four measurements shaped the rest of this file.

The first: the window used to construct all four screens in `__init__` — the
2747-line settings screen included — before the user had asked for any of them.
That is 691 widgets and 531ms of construction to show a home screen made of
nine. `AppShell.register` takes a *factory* and calls it on the first visit
instead, and the screen modules are imported inside those factories for the
same reason: importing `ui.screen_settings` and `ui.screen_outreach` is most of
the second the app spent starting.

The second: the audit found four screens with four different top bars — 31px,
70px, 50px, 31px — each carrying its own Home and Settings buttons, so "where
am I" and "how do I leave" were answered differently on every screen. The shell
owns one bar at `control.header`: the product name and the primary destinations
on the left, global state and actions on the right — the dry-run pill, the
theme toggle, Settings. A screen with sub-tabs hands them back through
`set_subtabs` and the shell draws them in a second row directly underneath, so
there is exactly one place to look. A left rail was considered and rejected:
horizontal space is this app's scarcest resource — the audit found table columns
clipped in 20 of 20 rows — and a rail takes 200px from every one of them.

Theme and density are read from the settings file at startup and change while
the app is running. `apply_appearance` re-applies the sheet, rebuilds the shell
chrome and repolishes the tree: the chrome has to be rebuilt rather than
repolished because `ui/components.py` resolves its colours in Python at build
time, so a repolish alone leaves every component wearing the palette it was
constructed in. A theme toggle that needs a restart is not a theme toggle.

The third measurement is what that change *cost*. With all four screens built,
picking a density on the home screen took 5,328ms of CPU — CPU and not wall
clock, because wall clock on the machine this was measured on swings five to
one — and almost all of it belonged to the three screens the user was not
looking at. They were told the stylesheet had changed and re-measured every
label they hold; then `SettingsScreen.restyle()` rebuilt four tab pages behind a
window nobody could see; then a polish walk covered all 827 widgets in the
process. `AppShell.asleep` keeps the broadcast off them, `restyle_screens` pays
for the screen on show and records what the others owe, `go` settles that debt
in the instant before a screen is put on top, and `_repolish` walks
`AppShell.onstage()`. The same change is 1,000ms, and arriving on a screen that
owes one costs 203ms.

The fourth is that none of this was reachable from a keyboard. The audit found
no shortcuts, no mnemonics, no menu bar, and Escape and Return doing nothing
anywhere in the app. The layer that fixes it belongs here rather than on any
screen, because a shortcut that only works on Outreach is not a shortcut: the
menu bar names every destination and the key that reaches it, `keyPressEvent`
gives Escape and Return one meaning each, and `ui/command_palette.py` puts
every action in the app one Ctrl+K away. The shell owns the command registry
and hands it to the palette, so a screen contributes its own entries — a
`commands()` method, or simply a row of sub-tabs it has already handed over —
without the palette knowing that screens exist.
"""

import contextlib
import signal
import sys
import traceback

from PyQt5.QtCore import QEvent, QObject, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QAction, QApplication,
    QComboBox, QHBoxLayout, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core import outreach_db
from core.settings import load_settings, save_settings
from ui import components
from ui import theme as theme_module
from ui.command_palette import Command, CommandPalette

# The stack keys, and the order the destinations appear in the bar.
INPUT, RESULTS, OUTREACH, SETTINGS = "input", "results", "outreach", "settings"

# How many steps back Escape can walk. Deep enough that Settings opened from a
# half-built campaign returns to it, shallow enough that Escape is a way out of
# where you are rather than a rewind of the whole session.
HISTORY = 8


def current_theme():
    """What the user's profile asks for, and dark/comfortable when it cannot say.

    Wrapped, because a settings file that cannot be read is not a reason to
    refuse to start: the app has to come up in *some* theme so the user can get
    to the screen that would fix it.
    """
    try:
        return theme_module.from_settings(load_settings())
    except Exception:
        return theme_module.theme()


# ── Threads ──────────────────────────────────────────────────────────────────


def _screen_threads(screen) -> list:
    """Every QThread a screen is currently running.

    Found by inspection rather than by name: each screen owns its own workers
    (the scrape, the send loop, the audit pass, the credential probes) and this
    window has no business knowing what they are called. A hard-coded list would
    go quietly out of date, and a thread missed here is a send loop that outlives
    the window.

    Two searches, because there are two ways a screen holds a worker and each
    finds only its own. The scrape, the send loop, the audit pass and the plan
    are plain attributes with no Qt parent, so they exist for `vars` and are
    invisible to `findChildren`. The settings screen's probes are the other way
    round: `_FetchModelsProbe` is constructed with `parent=self` and stored in
    no attribute at all, so `vars` never saw it — it was never stopped, and
    closing the window destroyed a QThread that was still running its HTTP
    call, which Qt answers by aborting the process.
    """
    running, seen = [], set()
    for value in list(vars(screen).values()) + screen.findChildren(QThread):
        candidates = value if isinstance(value, (list, tuple)) else (value,)
        for worker in candidates:
            if not isinstance(worker, QThread) or id(worker) in seen:
                continue
            seen.add(id(worker))
            if worker.isRunning():
                running.append(worker)
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


# ── Appearance ───────────────────────────────────────────────────────────────


# The five events Qt broadcasts to every widget alive when the application's
# font, palette or stylesheet is replaced.
_APPEARANCE = frozenset((
    QEvent.StyleChange, QEvent.FontChange, QEvent.PaletteChange,
    QEvent.ApplicationFontChange, QEvent.ApplicationPaletteChange))


class _Unbothered(QObject):
    """Keeps those five off a screen that is about to be built again anyway.

    `app.setStyleSheet` is the single most expensive thing an appearance change
    does, and almost all of it belongs to widgets nobody can see. Measured with
    all four screens built: 2,937ms of CPU for one swap over 769 widgets, of
    which 630 belong to the three screens off show — against 145ms for the same
    swap with only the home screen built.

    The cost is not Qt matching selectors. It is what this app's own widgets do
    when they are told: `_MeasuredLabel`, `_MeasuredEdit` and the settings
    screen's fields all re-measure their text on a style change, which one swap
    turns into 30,807 calls to `QFontMetrics.horizontalAdvance` and the layout
    invalidations that follow. Swallowing the five events for a screen the shell
    has already decided will rebuild itself before it is next shown takes the
    same swap to 1,695ms — the re-measure is not skipped, it happens once, in
    the rebuild, instead of twice.

    Which is the whole of the invariant, and why this is installed from
    `AppShell.asleep` and nowhere else: a screen is silenced only while it is in
    `_owes`, and every key in `_owes` is restyled by `go` before it can be seen.
    """

    def eventFilter(self, _watched, event) -> bool:
        return event.type() in _APPEARANCE


# ── The shell ────────────────────────────────────────────────────────────────


class AppShell(QWidget):
    """One persistent bar over a stack of screens built on first visit.

    `register` records how to build a screen and never builds it; `go` is what
    builds it. The `opener` a caller registers alongside the factory is the one
    thing a screen needs doing to it before it is shown — `setup` for a scrape,
    `load_from_results` for a hand-off, `refresh` for a screen that caches the
    settings file — and it runs before the switch so nothing is ever painted
    holding the last visit's contents.
    """

    theme_toggled = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, title: str = "MapHarvest", parent=None):
        super().__init__(parent)
        self._title = title
        self._theme = components.active_theme()
        self._factories: dict = {}
        self._openers: dict = {}
        self._labels: dict = {}
        self._destinations: list = []
        self._screens: dict = {}
        self._subtabs: dict = {}
        self._context: dict = {}
        self._dry_run = True
        self._nav: dict = {}
        self._sub_row = None
        self._sub_tabs: list = []
        self._sub_labels: tuple = ()
        self._context_label = None
        self._context_state = None
        self.current_key = ""
        # Where Escape goes, most recent last, and how much of it is worth
        # keeping: enough that backing out of a detour returns to the work, not
        # so much that Escape becomes a walk through the whole session.
        self._history: list = []
        # Something to ask for commands whenever the palette opens, rather than
        # a list of them captured once: half of what the palette offers is only
        # true for an instant.
        self._sources: list = []
        self._palette = None
        # The screens that owe a restyle because the theme changed while they
        # were behind another one.
        self._owes: set = set()

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._bar = QWidget()
        self._sub = QWidget()
        self._stack = QStackedWidget()
        self._root.addWidget(self._bar)
        self._root.addWidget(self._sub)
        self._root.addWidget(self._stack, 1)
        self.restyle(self._theme)

    # ── Registration and navigation ──────────────────────────────────────

    def register(self, key: str, title: str, factory, *, opener=None,
                 destination: bool = True) -> None:
        """Name a screen and how to build it. Nothing is constructed here."""
        self._factories[key] = factory
        self._labels[key] = title
        if opener is not None:
            self._openers[key] = opener
        if destination and key not in self._destinations:
            self._destinations.append(key)
            self._rebuild_bar()

    def go(self, key: str, **kw) -> None:
        """Show `key`, building it on first visit and opening it with `kw`.

        The restyle debt is settled first, before the opener and long before
        the switch, so a screen that missed a theme change is never painted for
        even one frame in the palette it was built in.
        """
        screen = self.screen(key)
        self._settle(key)
        opener = self._openers.get(key)
        if opener is not None:
            opener(screen, **kw)
        if self.current_key and self.current_key != key:
            self._history.append(self.current_key)
            del self._history[:-HISTORY]
        self._stack.setCurrentWidget(screen)
        self.current_key = key
        self._sync()

    def back(self) -> bool:
        """Return to the screen this one was reached from, if there was one."""
        for key in reversed(self._history):
            if key != self.current_key:
                self.retreat(key)
                return True
        del self._history[:]
        return False

    def retreat(self, key: str) -> None:
        """Go to `key` as a way *back*: the trail is cut at it, not extended.

        The distinction is the whole of what makes Escape work twice. Leaving
        Settings is a navigation like any other as far as `go` is concerned, so
        without this it pushed Settings onto the trail — and the next Escape
        dutifully walked forward into the screen the first one had just left.
        """
        keep = list(self._history)
        while keep and keep[-1] != key:
            keep.pop()
        if keep:
            keep.pop()
        self.go(key)
        self._history = keep

    def screen(self, key: str):
        """The screen for `key`, built now if this is its first visit."""
        if key not in self._screens:
            built = self._factories[key]()
            self._screens[key] = built
            self._stack.addWidget(built)
        return self._screens[key]

    def built(self, key: str = ""):
        """The screens that exist: one by key, or every key built so far."""
        if key:
            return self._screens.get(key)
        return tuple(k for k in self._factories if k in self._screens)

    def screens(self) -> list:
        return list(self._screens.values())

    def destinations(self) -> tuple:
        """The primary keys, in the order the bar draws them."""
        return tuple(self._destinations)

    def label(self, key: str) -> str:
        return self._labels.get(key, key)

    def can_go_back(self) -> bool:
        return any(key != self.current_key for key in self._history)

    def go_subtab(self, key: str, label: str) -> bool:
        """Show `key` with its sub-tab called `label` selected.

        Through the callback the screen itself handed over with the row, so
        this reaches a tab page without knowing what a tab page is or which
        screen has any. False when no screen has published a row by that name,
        which is what makes it safe to offer from a palette.
        """
        labels, on_change, _current = self._subtabs.get(key, ((), None, 0))
        if label not in labels:
            return False
        self.go(key)
        self._on_subtab(list(labels).index(label))
        return True

    # ── Commands ─────────────────────────────────────────────────────────

    def add_commands(self, source) -> None:
        """Register something that answers with commands when it is asked.

        A source and not a list, because half of what a palette offers is only
        true for an instant: Stop sending can be run only while a campaign is
        running, the theme command is named after the palette it is *not*
        wearing, and a command captured at registration would go on claiming
        both long after they stopped being so.
        """
        self._sources.append(source)

    def commands(self) -> list:
        """Everything the app can be asked to do, as of right now.

        Three contributors, none of which the palette knows about. The window
        registers what the shell itself owns. Every screen that has handed over
        a row of sub-tabs gets one command per tab for free, because publishing
        that row is already a statement about where a user might want to go. And
        a screen with a `commands()` method of its own contributes it — the same
        idiom `_chrome` uses for routes, so a screen that has not grown one yet
        is not a crash.
        """
        found = []
        for source in self._sources:
            found.extend(source())
        for key, (labels, _on_change, _current) in sorted(self._subtabs.items()):
            for label in labels:
                found.append(Command(
                    key="%s.%s" % (key, label),
                    title="%s — %s" % (self._labels.get(key, key), label),
                    run=(lambda at=key, name=label: self.go_subtab(at, name)),
                    where="Section"))
        for key in self.built():
            contribute = getattr(self._screens[key], "commands", None)
            if callable(contribute):
                found.extend(contribute())
        return found

    def palette(self) -> CommandPalette:
        """The palette, built on first use like every screen in the stack."""
        if self._palette is None:
            self._palette = CommandPalette(self)
        return self._palette

    def open_palette(self) -> None:
        self.palette().open_with(self.commands())

    def dismiss_palette(self) -> bool:
        """Close the palette if it is open. False when there was nothing open."""
        if self._palette is None or not self._palette.isVisible():
            return False
        self._palette.dismiss()
        return True

    # ── What the bar says ────────────────────────────────────────────────

    def set_subtabs(self, key: str, labels, on_change, current: int = 0) -> None:
        """The second row, for a screen that has one. Empty labels remove it."""
        if labels:
            self._subtabs[key] = (tuple(labels), on_change, int(current))
        else:
            self._subtabs.pop(key, None)
        self._sync()

    def set_context(self, key: str, text: str = "", tone: str = "info") -> None:
        """One line of state for `key` — what is running, what is selected."""
        if text:
            self._context[key] = (str(text), tone)
        else:
            self._context.pop(key, None)
        self._sync()

    def set_dry_run(self, dry_run: bool) -> None:
        """Which way the safety switch is thrown, for the pill on the right."""
        if bool(dry_run) == self._dry_run:
            return
        self._dry_run = bool(dry_run)
        self._rebuild_bar()

    # ── Chrome ───────────────────────────────────────────────────────────

    def restyle(self, t) -> None:
        """Rebuild the bar in `t`.

        Rebuilt rather than repolished because every component resolves its
        colours from the theme in force at build time and writes them into its
        own stylesheet, and a widget's own sheet beats the application's.
        """
        self._theme = t
        self._replace(1, "_sub", self._make_sub())
        self._rebuild_bar()
        if self._palette is not None:
            self._palette.restyle(t)

    @contextlib.contextmanager
    def asleep(self):
        """Every screen owes a restyle, and the ones off show are not disturbed.

        Opened around the whole of an appearance change. `_owes` is written
        first because the sheet swap inside is what the screens off show are
        being spared, and `_Unbothered` explains what that is worth.
        """
        self._owes = set(self.built())
        guard = _Unbothered(self)
        watched = []
        for key in self._owes:
            if key == self.current_key:
                continue
            screen = self._screens[key]
            watched.append(screen)
            watched.extend(screen.findChildren(QWidget))
        for widget in watched:
            widget.installEventFilter(guard)
        try:
            yield
        finally:
            for widget in watched:
                widget.removeEventFilter(guard)
            guard.deleteLater()

    def restyle_screens(self) -> None:
        """Repaint the screen on show. The others keep owing until they are.

        The measurement this exists for: with all four screens built, one
        density change broke down as 1,887ms of sheet swap, 677ms of screens
        the user could not see rebuilding themselves inside the click — 626ms
        of `SettingsScreen.restyle()` laying out four tab pages behind a window
        nobody was looking at and 50ms of the results screen — a 140ms polish
        walk over all 827 widgets, and 178ms for the one screen on show.
        Nothing is skipped, only deferred: `go` settles the debt in the instant
        before a screen is put on top, so the first frame anyone sees is
        already in the new palette.

        Deferred rather than discarded because a screen is not only a look. The
        home screen holds a half-typed scrape, the results screen a running
        worker, the outreach screen a campaign set up in memory and a send loop,
        and the settings screen edits that are not on disk. Rebuilding from the
        factory would be faster still and would throw every one of them away.
        """
        self._settle(self.current_key)

    def _settle(self, key: str) -> None:
        if key not in self._owes:
            return
        self._owes.discard(key)
        restyle = getattr(self._screens.get(key), "restyle", None)
        if callable(restyle):
            restyle()

    def owes(self) -> tuple:
        """The screens still wearing the theme the app has stopped using."""
        return tuple(sorted(self._owes))

    def onstage(self) -> list:
        """Every widget the user can actually see: the chrome and one screen.

        What `MainWindow._repolish` walks. The walk used to cover every widget
        in the process — 827 of them at 140ms — 630 of which belong to screens
        that are behind this one and are going to rebuild themselves the moment
        they are next shown.
        """
        tree = [self, self._bar, self._sub]
        tree.extend(self._bar.findChildren(QWidget))
        tree.extend(self._sub.findChildren(QWidget))
        screen = self._screens.get(self.current_key)
        if screen is not None:
            tree.append(screen)
            tree.extend(screen.findChildren(QWidget))
        if self._palette is not None:
            tree.append(self._palette)
            tree.extend(self._palette.findChildren(QWidget))
        return tree

    def _rebuild_bar(self) -> None:
        self._replace(0, "_bar", self._make_bar())
        self._sync()

    def _replace(self, at: int, attr: str, made: QWidget) -> None:
        old = getattr(self, attr)
        setattr(self, attr, made)
        self._root.insertWidget(at, made)
        self._root.removeWidget(old)
        old.setParent(None)
        old.deleteLater()

    def _make_bar(self) -> QWidget:
        t = self._theme
        bar = QWidget()
        bar.setObjectName("app_bar")
        bar.setFixedHeight(t.control["header"])

        row = QHBoxLayout(bar)
        row.setContentsMargins(t.space["5"], t.space["0"], t.space["5"],
                               t.space["0"])
        row.setSpacing(t.space["2"])
        row.addWidget(components.heading(self._title, "h2"))
        row.addSpacing(t.space["4"])

        # No QButtonGroup, and that is the reason there is a comment here: an
        # exclusive group refuses to let the last checked button go, so on
        # Settings — which is an action on the right and not a destination on
        # the left — the bar would have gone on claiming the user was still
        # wherever they had been. `_sync` owns the checked state instead.
        self._nav = {}
        for key in self._destinations:
            tab = components.button(self._labels.get(key, key), kind="tab",
                                    size="sm")
            tab.setCheckable(True)
            tab.clicked.connect(lambda _checked=False, at=key: self.go(at))
            row.addWidget(tab)
            self._nav[key] = tab

        row.addStretch()
        row.addWidget(self._make_pill())

        other = "light" if t.name == "dark" else "dark"
        toggle = components.icon_button("☀" if t.name == "dark" else "☾",
                                        tooltip="Switch to the %s theme" % other,
                                        size="sm")
        toggle.clicked.connect(lambda _checked=False: self.theme_toggled.emit())
        row.addWidget(toggle)

        settings = components.button("Settings", kind="secondary", size="sm")
        settings.clicked.connect(
            lambda _checked=False: self.settings_requested.emit())
        row.addWidget(settings)
        return bar

    def _make_pill(self):
        """The one control that says whether Start sending mails real people."""
        if self._dry_run:
            pill = components.button("Dry run", kind="rehearsal", size="sm")
            pill.setToolTip("Every message is built and logged, none is sent. "
                            "Change it under Settings — Sending.")
        else:
            pill = components.button("LIVE", kind="danger_primary", size="sm")
            pill.setToolTip("Sending is live: a campaign mails real businesses. "
                            "Change it under Settings — Sending.")
        pill.clicked.connect(lambda _checked=False: self.settings_requested.emit())
        return pill

    def _make_sub(self) -> QWidget:
        t = self._theme
        sub = QWidget()
        sub.setObjectName("sub_bar")
        row = QHBoxLayout(sub)
        row.setContentsMargins(t.space["5"], t.space["1"], t.space["5"],
                               t.space["1"])
        row.setSpacing(t.space["1"])
        # The stretch outlives the widgets either side of it, so the row reads
        # [tabs…][stretch][context] whichever half is being replaced.
        row.addStretch()
        self._sub_row = row
        self._sub_tabs = []
        self._sub_labels = ()
        self._context_label = None
        self._context_state = None
        return sub

    def _sync(self) -> None:
        """Make the bar agree with where the user is, replacing only what moved.

        This used to empty the whole second row and build it again on every
        call, and every call includes each `set_context` — which a running
        campaign makes once per message, because the line counts them. So the
        four sub-tab buttons were deleted and recreated under the user's pointer
        every time a message went out, at 2.2ms a time even when nothing about
        them had changed.

        Both screens with a second row had grown a workaround for it rather than
        the shell being fixed: the outreach screen keeps its own copy of the
        context line to avoid publishing an unchanged one, and the settings
        screen defers publishing to the next turn of the event loop so the
        rebuild cannot land inside the click or the keystroke that caused it.
        The row is the shell's, so knowing whether it needs rebuilding is the
        shell's job.
        """
        for key, tab in self._nav.items():
            tab.setChecked(key == self.current_key)

        labels, _on_change, current = self._subtabs.get(
            self.current_key, ((), None, 0))
        labels = tuple(labels)
        if labels != self._sub_labels:
            self._replace_subtabs(labels)
        for index, tab in enumerate(self._sub_tabs):
            tab.setChecked(index == current)

        text, tone = self._context.get(self.current_key, ("", "info"))
        if (text, tone) != self._context_state:
            self._replace_context(text, tone)
        self._sub.setVisible(bool(labels) or bool(text))

    def _replace_subtabs(self, labels: tuple) -> None:
        for tab in self._sub_tabs:
            self._discard(tab)
        self._sub_tabs = []
        for index, label in enumerate(labels):
            tab = components.button(label, kind="tab", size="sm")
            tab.setCheckable(True)
            tab.clicked.connect(
                lambda _checked=False, at=index: self._on_subtab(at))
            self._sub_row.insertWidget(index, tab)
            self._sub_tabs.append(tab)
        self._sub_labels = labels

    def _replace_context(self, text: str, tone: str) -> None:
        """Built rather than relabelled: `body_label` bakes its tone in.

        Removed outright when there is nothing to say, so an empty context
        leaves no label behind for a stray `findChildren` — or a screen reader —
        to find in the row.
        """
        if self._context_label is not None:
            self._discard(self._context_label)
            self._context_label = None
        if text:
            line = components.body_label(text, tone=tone)
            line.setWordWrap(False)
            line.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._sub_row.addWidget(line)
            self._context_label = line
        self._context_state = (text, tone)

    def _discard(self, widget: QWidget) -> None:
        self._sub_row.removeWidget(widget)
        widget.setParent(None)
        widget.deleteLater()

    def _on_subtab(self, index: int) -> None:
        """The row moved by hand rather than rebuilt: this runs inside a click.

        `_sync` leaves a row whose labels have not changed alone now, so the
        button whose signal is on the stack survives the answer either way. It
        still moves the check here rather than waiting to be told, because a tab
        has to look pressed the instant it is pressed and `on_change` is free to
        take its time.
        """
        labels, on_change, _current = self._subtabs.get(
            self.current_key, ((), None, 0))
        self._subtabs[self.current_key] = (labels, on_change, index)
        for at, tab in enumerate(self._sub_tabs):
            tab.setChecked(at == index)
        if on_change is not None:
            on_change(index)


# ── The window ───────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MapHarvest")
        # Minimum, not fixed: the outreach and settings screens are dense enough
        # that a user with a big monitor should be able to maximise the window
        # and see a whole lead table at once.
        self.setMinimumSize(QSize(880, 620))
        self.resize(QSize(1080, 760))

        self.settings = load_settings()
        self.theme = theme_module.from_settings(self.settings)
        components.use_theme(self.theme)

        self.shell = AppShell()
        self.setCentralWidget(self.shell)
        self.shell.register(INPUT, "Scrape", self._build_input)
        self.shell.register(RESULTS, "Results", self._build_results,
                            opener=self._open_results)
        self.shell.register(OUTREACH, "Outreach", self._build_outreach,
                            opener=self._open_outreach)
        self.shell.register(SETTINGS, "Settings", self._build_settings,
                            opener=self._open_settings, destination=False)
        self.shell.set_dry_run(self.settings.get("dry_run", True))
        self.shell.theme_toggled.connect(self.toggle_theme)
        self.shell.settings_requested.connect(self.on_settings)
        self.shell.add_commands(self._commands)

        # Where Back returns to, so settings opened from outreach does not dump
        # the user on the home screen with their campaign half set up.
        self._settings_return = INPUT
        self._build_menus()
        self.shell.go(INPUT)

    # ── Screens ──────────────────────────────────────────────────────────
    # Imported here rather than at the top of the module: `ui.screen_settings`
    # and `ui.screen_outreach` are 5,338 lines between them and importing both
    # is most of the second the app used to spend before it drew anything.

    def _build_input(self):
        from ui.screen_input import InputScreen
        screen = InputScreen()
        screen.start_signal.connect(self.on_start)
        self._chrome(screen, "settings_signal", self.on_settings)
        self._chrome(screen, "outreach_signal", self.on_outreach_direct)
        return screen

    def _build_results(self):
        from ui.screen_results import ResultsScreen
        screen = ResultsScreen()
        screen.stop_signal.connect(self.on_stop)
        screen.outreach_signal.connect(self.on_outreach)
        self._chrome(screen, "harvested_signal", self.on_harvested)
        self._chrome(screen, "home_signal", self.on_home)
        return screen

    def _build_outreach(self):
        from ui.screen_outreach import OutreachScreen
        screen = OutreachScreen()
        self._chrome(screen, "home_signal", self.on_home)
        self._chrome(screen, "settings_signal", self.on_settings)
        return screen

    def _build_settings(self):
        from ui.screen_settings import SettingsScreen
        screen = SettingsScreen()
        screen.saved_signal.connect(self.on_settings_saved)
        self._chrome(screen, "back_signal", self.on_settings_closed)
        return screen

    @staticmethod
    def _chrome(screen, name: str, slot) -> None:
        """Wire a route a screen may already have handed back to the shell.

        The migration takes the Home, Back and Settings buttons off the screens
        one screen at a time, and the window has to keep working at every step
        of it: a route that has gone is a route the bar now owns, not a crash.
        The signals that carry data rather than chrome — `start_signal`,
        `stop_signal`, the records hand-off, `saved_signal` — are connected
        outright, because those are not the shell's to take over.
        """
        route = getattr(screen, name, None)
        if route is not None:
            route.connect(slot)

    # ── Openers ──────────────────────────────────────────────────────────

    @staticmethod
    def _open_results(screen, **kw) -> None:
        if kw:
            screen.setup(kw["domains"], kw["areas"], kw["fields"],
                         kw["headless"], kw["max_results"], kw["export_dir"],
                         kw["filters"])

    @staticmethod
    def _open_outreach(screen, records=None, **_kw) -> None:
        if records is None:
            screen.refresh()
        else:
            screen.load_from_results(records)

    @staticmethod
    def _open_settings(screen, **_kw) -> None:
        screen.refresh()

    # ── Routes ───────────────────────────────────────────────────────────

    def on_start(self, domains, areas, fields, headless=False, max_results=50,
                 export_dir="", filters=None):
        self.shell.go(RESULTS, domains=domains, areas=areas, fields=fields,
                      headless=headless, max_results=max_results,
                      export_dir=export_dir, filters=filters or {})
        self.shell.screen(RESULTS).start_worker()

    def on_stop(self):
        self.shell.screen(RESULTS).stop_worker()

    def on_home(self):
        self.shell.go(INPUT)

    def on_outreach_direct(self):
        self.shell.go(OUTREACH)

    def on_harvested(self, records):
        """A finished scrape goes into the pool whether or not it is asked for.

        Built rather than deferred: the outreach screen is lazy, and a harvest
        that waits for the user to visit it is a harvest lost to the next Home.
        """
        try:
            self.shell.screen("outreach").absorb_scrape(records)
        except Exception:
            pass

    def on_outreach(self, records):
        self.shell.go(OUTREACH, records=records)

    def on_settings(self):
        if self.shell.current_key != SETTINGS:
            self._settings_return = self.shell.current_key or INPUT
        self.shell.go(SETTINGS)

    def on_settings_closed(self):
        # `retreat` and not `go`, because this is the way out of a detour: a
        # trail that recorded it would answer the next Escape with Settings.
        self.shell.retreat(self._settings_return)

    def on_settings_saved(self, settings):
        # Both screens cache their own copy of the file; hand them the new one
        # so the next thing either of them writes is not a stale snapshot.
        self.settings = settings
        home = self.shell.built(INPUT)
        if home is not None:
            home.apply_settings(settings)
        outreach = self.shell.built(OUTREACH)
        if outreach is not None:
            outreach.refresh()
        self.shell.set_dry_run(settings.get("dry_run", True))
        self._sync_dry_run()
        self.apply_appearance(settings)

    # ── The menu bar ─────────────────────────────────────────────────────

    def _build_menus(self) -> None:
        """Where a keyboard user finds out that any of this exists.

        The bar is worth more read than clicked. Qt writes an action's shortcut
        in a second column beside its name, so a menu is the one surface that
        answers "what can I press" without anybody having to be told to press
        something first, and the mnemonics come with it: Alt+G, Alt+V, Alt+H.

        Every shortcut here carries a modifier, and that is the rule rather than
        the taste. A shortcut is matched before the key reaches whatever has the
        focus, so an unmodified one is a character a text field never receives —
        which is how a search box stops accepting the letter you gave to a
        command. The two unmodified keys the app does answer, Escape and Return,
        are handled in `keyPressEvent` instead, where they arrive only if the
        focused widget did not want them, and F1 asks `_typing` before it acts.
        Back and Submit therefore spell their keys in their own text: they are
        real, and they are deliberately not shortcuts.
        """
        bar = self.menuBar()
        bar.clear()

        go = bar.addMenu("&Go")
        self._action(go, "Command &palette…", "Ctrl+K", self.open_palette)
        go.addSeparator()
        for at, key in enumerate(self.shell.destinations(), start=1):
            self._action(go, "&%d  %s" % (at, self.shell.label(key)),
                         "Ctrl+%d" % at, lambda k=key: self.shell.go(k))
        go.addSeparator()
        self._action(go, "&Settings", "Ctrl+,", self.on_settings)
        self._action(go, "&Back\tEsc", "", self.on_escape)

        view = bar.addMenu("&View")
        self._theme_action = self._action(view, "", "Ctrl+Shift+T",
                                          self.toggle_theme)
        self._density_action = self._action(view, "", "Ctrl+Shift+D",
                                            self.toggle_density)
        view.addSeparator()
        self._dry_action = self._action(view, "D&ry run", "",
                                        self.toggle_dry_run)
        self._dry_action.setCheckable(True)
        self._sync_menu()

        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "&Keyboard shortcuts and commands", "F1",
                     self.on_help)
        self._action(help_menu, "&Submit the form in front of you\tEnter", "",
                     self.on_submit)

    def _action(self, menu, text: str, keys: str, slot) -> QAction:
        action = QAction(text, self)
        if keys:
            action.setShortcut(QKeySequence(keys))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    def _sync_menu(self) -> None:
        """Name what each toggle would do next, not the state it is already in.

        A menu item reading "Theme" says nothing anybody can act on. "Switch to
        the light theme" is the same item telling them which way it goes, and it
        is the sentence the palette already uses for the same command. The
        mnemonic stays on the noun so Alt+V,T and Alt+V,D do not move under the
        user every time the answer changes.
        """
        if getattr(self, "_theme_action", None) is None:
            return
        other = "light" if self.theme.name == "dark" else "dark"
        self._theme_action.setText("Switch to the %s &theme" % other)
        density = ("compact" if self.theme.density == "comfortable"
                   else "comfortable")
        self._density_action.setText("Switch to the %s &density" % density)
        self._sync_dry_run()

    def on_help(self) -> None:
        """F1: the list of what exists, which is the palette itself.

        One list rather than two. A second window enumerating shortcuts is a
        second thing to keep in step with the first, and it is strictly worse
        than the palette at the only job either of them has: the palette names
        every command, shows the key beside the ones that have one, and will
        run the command while it is being read.
        """
        if self._typing():
            return
        self.open_palette()

    def open_palette(self) -> None:
        """Ctrl+K again closes it: the key that opened it is also the way out."""
        if self.shell.dismiss_palette():
            return
        self.shell.open_palette()

    # ── Commands ─────────────────────────────────────────────────────────
    # Every action a screen owns is offered as the button that already does it,
    # never as a copy of the slot behind it. The button carries its own label,
    # its own enabled state and whatever confirmation the screen wraps it in, so
    # a command cannot drift from the control beside it and cannot become a
    # second, unconfirmed way to mail two hundred strangers.

    _ACTIONS = (
        (OUTREACH, "audit_btn", "Audit the leads"),
        (OUTREACH, "prepare_btn", "Prepare a campaign"),
        (OUTREACH, "start_btn", "Start sending"),
        (OUTREACH, "pause_btn", "Pause sending"),
        (OUTREACH, "stop_btn", "Stop sending"),
        (OUTREACH, "copy_btn", "Copy the selected addresses"),
        (OUTREACH, "suppress_btn", "Suppress the selected leads"),
        (RESULTS, "action_btn", "Stop the scrape"),
        (RESULTS, "pause_btn", "Pause the scrape"),
        (RESULTS, "export_btn", "Export the results to CSV"),
        (RESULTS, "outreach_btn", "Take these leads to Outreach"),
        (INPUT, "start_btn", "Start scraping"),
        (SETTINGS, "save_btn", "Save the settings"),
        (SETTINGS, "discard_btn", "Discard the unsaved settings"),
    )

    def _commands(self) -> list:
        """What the shell itself can be asked to do, named as of this instant."""
        found = []
        for at, key in enumerate(self.shell.destinations(), start=1):
            found.append(Command(
                key="go.%s" % key, title="Go to %s" % self.shell.label(key),
                run=(lambda k=key: self.shell.go(k)),
                where="Destination", shortcut="Ctrl+%d" % at))
        found.append(Command(
            key="go.settings", title="Go to Settings", run=self.on_settings,
            where="Destination", shortcut="Ctrl+,"))
        found.append(Command(
            key="go.back", title="Go back", run=self.on_escape,
            where="Destination", shortcut="Esc",
            available=self.shell.can_go_back))

        other = "light" if self.theme.name == "dark" else "dark"
        found.append(Command(
            key="view.theme", title="Switch to the %s theme" % other,
            run=self.toggle_theme, where="Appearance", shortcut="Ctrl+Shift+T"))
        density = ("compact" if self.theme.density == "comfortable"
                   else "comfortable")
        found.append(Command(
            key="view.density", title="Switch to the %s density" % density,
            run=self.toggle_density, where="Appearance",
            shortcut="Ctrl+Shift+D"))
        live = not self.settings.get("dry_run", True)
        found.append(Command(
            key="view.dry_run",
            title=("Turn dry run back on" if live
                   else "Turn dry run off and send for real"),
            run=self.toggle_dry_run, where="Sending"))

        for key, attribute, title in self._ACTIONS:
            button = self._action_button(key, attribute)
            if button is None:
                continue
            found.append(Command(
                key="%s.%s" % (key, attribute),
                title="%s — %s" % (self.shell.label(key), title),
                run=(lambda k=key, a=attribute: self._press(k, a)),
                # What the control itself currently says, which is not always
                # what it does: Start reads "Start rehearsal" while dry run is
                # on, and Audit counts what it would audit.
                where=button.text(),
                available=(lambda b=button: b.isEnabled())))
        return found

    def _action_button(self, key: str, attribute: str):
        """A screen's own control, if that screen has been built and has one.

        Built, because asking an unbuilt screen for its buttons would construct
        every screen in the app the first time anybody pressed Ctrl+K — which is
        the whole of what `register` was written to avoid. A command that is not
        offered until its screen exists is also the honest answer: what a lead
        table can do depends on what is selected in it.
        """
        screen = self.shell.built(key)
        button = getattr(screen, attribute, None) if screen is not None else None
        return button if isinstance(button, QAbstractButton) else None

    def _press(self, key: str, attribute: str) -> None:
        self.shell.go(key)
        button = self._action_button(key, attribute)
        if button is not None and button.isEnabled():
            button.click()

    # ── Keys ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        """Escape and Return, and only when nothing nearer wanted them.

        Handled here rather than as shortcuts because Qt matches a shortcut
        before the key ever reaches the focused widget, and both of these keys
        already belong to something else half the time: Escape closes a combo
        box's popup and rejects a dialog, Return inserts a newline in a text
        editor. A key event that has travelled all the way up to the window is
        one that every one of those declined, which makes this the only place
        where answering it cannot take a keystroke away from anybody.
        """
        key = event.key()
        if key == Qt.Key_Escape and self.on_escape():
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and self.on_submit():
            event.accept()
            return
        super().keyPressEvent(event)

    def on_escape(self) -> bool:
        """Back out of one thing: the palette, then Settings, then the screen."""
        if self.shell.dismiss_palette():
            return True
        if self.shell.current_key == SETTINGS:
            self.on_settings_closed()
            return True
        return self.shell.back()

    def on_submit(self) -> bool:
        """Return: press the button this form is asking to have pressed.

        The audit's finding was that Enter did nothing anywhere, which on a
        screen whose whole job is a form is the difference between typing a
        search and running one. What it presses is scoped rather than global:
        the search starts at whatever has the focus and walks outwards, so the
        first enclosing card, page or screen that owns a primary button is the
        one that answers, and a form nested inside another cannot fire the
        outer one's action.

        A view or a text editor answers Return itself and is left alone, because
        a table row and a template body both mean something by it.
        """
        focus = QApplication.focusWidget()
        if isinstance(focus, QAbstractButton):
            if focus.isEnabled() and focus.isVisible():
                focus.click()
                return True
            return False
        if self._within(focus, (QAbstractItemView, QTextEdit, QPlainTextEdit)):
            return False
        button = self._primary_near(focus)
        if button is None:
            return False
        button.click()
        return True

    def _primary_near(self, focus):
        """The nearest primary button to `focus`, searching outwards from it.

        Outwards and not downwards: the first enclosing card, page or screen
        that owns one answers, so a form nested inside another cannot fire the
        outer one's action. The screen itself is the last resort, which is also
        the answer when nothing has the focus at all.
        """
        screen = self.shell.built(self.shell.current_key)
        if screen is None:
            return None
        node = focus
        while node is not None and node is not screen:
            found = self._primary_in(node)
            if found is not None:
                return found
            node = node.parentWidget()
        return self._primary_in(screen)

    @staticmethod
    def _primary_in(node):
        for button in node.findChildren(QPushButton):
            if button.property("kind") in ("primary", "danger_primary") \
                    and button.isVisible() and button.isEnabled():
                return button
        return None

    def _within(self, widget, kinds) -> bool:
        """Whether `widget` is one of `kinds`, or sits inside one on this screen."""
        screen = self.shell.built(self.shell.current_key)
        node = widget
        while node is not None:
            if isinstance(node, kinds):
                return True
            if node is screen or node is self:
                return False
            node = node.parentWidget()
        return False

    @staticmethod
    def _typing() -> bool:
        """Whether the focus is somewhere a keystroke is a character.

        The one guard an unmodified shortcut needs. F1 is the only one the app
        has, and it is guarded rather than exempted so that the next one added
        inherits the rule instead of rediscovering it.
        """
        focus = QApplication.focusWidget()
        if isinstance(focus, QComboBox):
            return focus.isEditable()
        return isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit,
                                  QAbstractSpinBox))

    # ── Appearance ───────────────────────────────────────────────────────

    def toggle_theme(self):
        """The other palette, live, and remembered."""
        wanted = "light" if self.theme.name == "dark" else "dark"
        self._remember("theme", wanted)

    def toggle_density(self):
        """The other row height, live, and remembered."""
        wanted = ("compact" if self.theme.density == "comfortable"
                  else "comfortable")
        self._remember("density", wanted)

    def _remember(self, name: str, value: str) -> None:
        self.settings[name] = value
        try:
            save_settings(self.settings)
        except Exception:
            traceback.print_exc()
        self.apply_appearance(self.settings)

    def toggle_dry_run(self):
        """Throw the safety switch, and ask first in the direction that mails.

        The one setting in the app a wrong guess about costs somebody else
        something: dry run off means the next campaign reaches real businesses.
        Turning it back on is a retreat and needs no ceremony; turning it off is
        `components.confirm` with the same wording the pill in the bar carries,
        because a command surface that can arm a live send by fuzzy match on
        three letters must not be able to do it silently.
        """
        wanted = not self.settings.get("dry_run", True)
        if not wanted and not components.confirm(
                self, title="Send for real?",
                body="Dry run off means the next campaign mails real "
                     "businesses. Every message is sent from your own mailbox "
                     "and cannot be recalled.",
                confirm_text="Turn dry run off", danger=True):
            self._sync_dry_run()
            return
        self.settings["dry_run"] = wanted
        try:
            save_settings(self.settings)
        except Exception:
            traceback.print_exc()
        self.shell.set_dry_run(wanted)
        self._sync_dry_run()
        outreach = self.shell.built(OUTREACH)
        if outreach is not None:
            outreach.refresh()

    def _sync_dry_run(self) -> None:
        """Keep the tick in the View menu on the same side as the pill."""
        action = getattr(self, "_dry_action", None)
        if action is not None:
            action.setChecked(bool(self.settings.get("dry_run", True)))

    def apply_appearance(self, settings=None) -> None:
        """Wear what the settings ask for, without a restart."""
        if settings is not None:
            self.settings = settings
        wanted = theme_module.from_settings(self.settings)
        app = QApplication.instance()
        worn = (app is not None
                and app.styleSheet() == theme_module.stylesheet(wanted))
        if wanted == self.theme and worn:
            return

        self.theme = wanted
        with self.shell.asleep():
            if app is not None:
                theme_module.apply(app, wanted)
            components.use_theme(wanted)
            self.shell.restyle(wanted)
            # Rebuilt before the walk and not after it: a screen that answers
            # `restyle` by building itself again throws away every widget the
            # walk would have polished, so the old order paid for the whole of
            # the screen on show twice and showed the second copy.
            self.shell.restyle_screens()
            self._repolish()
        self._sync_menu()

    def _repolish(self) -> None:
        """What is on screen re-asks the style what it looks like.

        `setStyleSheet` on the application repolishes what exists, but the style
        itself carries colours the sheet cannot state — `TickStyle` paints every
        check mark — so the tree is walked once here as well.

        Walked as far as the user can see and no further. It used to cover every
        widget in the process, which with four screens built is 827 of them at
        140ms, and 630 of those belong to screens behind this one that
        `restyle_screens` has already decided will rebuild themselves before
        anybody sees them again. Repolishing a widget that is about to be
        replaced is work paid for twice and shown once.
        """
        bar = self.menuBar()
        for widget in self.shell.onstage() + [bar, self]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    # ── Shutdown ─────────────────────────────────────────────────────────

    def shutdown_worker(self) -> None:
        """Stop every screen's background thread before the window goes away.

        Two distinct failures this prevents. A running scrape leaves the QThread
        alive and an orphaned chrome.exe behind, so the app appears to hang on
        exit and Chrome processes pile up. A running campaign is worse: the send
        loop would carry on mailing real businesses after the user quit.
        """
        results = self.shell.built(RESULTS)
        if results is not None:
            scrape = getattr(results, "worker", None)
            if scrape is not None and scrape.isRunning():
                # Suppresses the "stopped unexpectedly" path in the results screen.
                results._stopped_by_user = True

        for screen in self.shell.screens():
            for worker in _screen_threads(screen):
                _stop_thread(worker)

    @staticmethod
    def shutdown_store() -> None:
        """Close the outreach database, after the threads that write to it.

        Nothing closed it before: `close_all` existed for the test suite, which
        needs it because Windows will not delete a temp directory holding an
        open database, and the app itself simply exited with the handle open.
        What that costs is the checkpoint. SQLite folds the write-ahead log back
        into the database when the last connection closes, so a process that
        never closes leaves the log behind and the next start replays it. Opening
        and closing this window over a seeded store left 3.5MB of
        `outreach.db-wal` against a 4KB `outreach.db`; closing it puts 2MB into
        the database and empties the log.

        Separate from `shutdown_worker` and called after it on purpose: the send
        loop and the audit pass write through this connection, so it may not be
        closed until they have been stopped and joined.
        """
        outreach_db.close_all()

    def closeEvent(self, event):
        self.shutdown_worker()
        self.shutdown_store()
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
    # Before the QApplication exists, not after: Qt only honours
    # AA_EnableHighDpiScaling while none has been constructed, and set later it
    # is ignored in silence and the whole app renders at 1x on a scaled display.
    theme_module.enable_high_dpi()

    app = QApplication(sys.argv)
    started = current_theme()
    theme_module.apply(app, started)
    components.use_theme(started)
    _install_excepthook()

    window = MainWindow()

    # Qt's event loop is C++, so Python never runs while idle and a Ctrl+C sits
    # queued until some slot happens to execute (which is why it used to surface
    # as a bogus traceback inside whatever button you clicked next). Handle
    # SIGINT explicitly and tick a timer so Python gets a chance to process it.
    def _on_sigint(*_):
        window.shutdown_worker()
        window.shutdown_store()
        app.quit()

    signal.signal(signal.SIGINT, _on_sigint)
    idle = QTimer()
    idle.start(200)
    idle.timeout.connect(lambda: None)
    app.aboutToQuit.connect(window.shutdown_worker)
    app.aboutToQuit.connect(window.shutdown_store)

    window.show()
    sys.exit(app.exec_())
