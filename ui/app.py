"""The window, the shell every screen sits in, and an orderly shutdown.

Everything this module used to say about how the app *looks* now lives in
`ui/theme.py` and `ui/components.py`. What is left is chrome and lifecycle:
which screen is showing, one bar that says so, and which background threads
have to be stopped before the window can go away.

Two measurements shaped the rest of this file.

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
"""

import signal
import sys
import traceback

from PyQt5.QtCore import QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout,
    QWidget,
)

from core import outreach_db
from core.settings import load_settings, save_settings
from ui import components
from ui import theme as theme_module

# The stack keys, and the order the destinations appear in the bar.
INPUT, RESULTS, OUTREACH, SETTINGS = "input", "results", "outreach", "settings"


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
        """Show `key`, building it on first visit and opening it with `kw`."""
        screen = self.screen(key)
        opener = self._openers.get(key)
        if opener is not None:
            opener(screen, **kw)
        self._stack.setCurrentWidget(screen)
        self.current_key = key
        self._sync()

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

        # Where Back returns to, so settings opened from outreach does not dump
        # the user on the home screen with their campaign half set up.
        self._settings_return = INPUT
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

    def on_outreach(self, records):
        self.shell.go(OUTREACH, records=records)

    def on_settings(self):
        if self.shell.current_key != SETTINGS:
            self._settings_return = self.shell.current_key or INPUT
        self.shell.go(SETTINGS)

    def on_settings_closed(self):
        self.shell.go(self._settings_return)

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
        self.apply_appearance(settings)

    # ── Appearance ───────────────────────────────────────────────────────

    def toggle_theme(self):
        """The other palette, live, and remembered."""
        wanted = "light" if self.theme.name == "dark" else "dark"
        self.settings["theme"] = wanted
        try:
            save_settings(self.settings)
        except Exception:
            traceback.print_exc()
        self.apply_appearance(self.settings)

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
        if app is not None:
            theme_module.apply(app, wanted)
        components.use_theme(wanted)
        self.shell.restyle(wanted)
        self._repolish()

    def _repolish(self) -> None:
        """Every widget alive re-asks the style what it looks like.

        `setStyleSheet` on the application repolishes what exists, but the
        style itself is replaced with each theme — `TickStyle` holds the
        palette it paints check marks with — so the tree is walked once here
        and every screen that knows how to rebuild its own components is asked
        to.
        """
        for widget in self.findChildren(QWidget) + [self]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        for screen in self.shell.screens():
            restyle = getattr(screen, "restyle", None)
            if callable(restyle):
                restyle()

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
