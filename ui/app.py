"""The window, the four screens it stacks, and an orderly shutdown.

Everything this module used to say about how the app looks now lives in
`ui/theme.py`: one call to `theme.apply()` replaces the 643-line stylesheet
literal, the font setup and the style installation that used to sit here, and
`TickStyle` moved next to the tokens it paints with. What is left is chrome and
lifecycle — which screen is showing, and which background threads have to be
stopped before the window can go away.

The theme and density come from the settings file when it names them and
default to dark and comfortable when it does not, which is every profile
written before this existed.
"""

import signal
import sys
import traceback

from PyQt5.QtCore import QSize, QThread, QTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget

from core.settings import load_settings
from ui import theme as theme_module
from ui.screen_input import InputScreen
from ui.screen_outreach import OutreachScreen
from ui.screen_results import ResultsScreen
from ui.screen_settings import SettingsScreen


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
    # Before the QApplication exists, not after: Qt only honours
    # AA_EnableHighDpiScaling while none has been constructed, and set later it
    # is ignored in silence and the whole app renders at 1x on a scaled display.
    theme_module.enable_high_dpi()

    app = QApplication(sys.argv)
    theme_module.apply(app, current_theme())
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
