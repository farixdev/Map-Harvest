"""The screen a scrape is watched on, and the table the whole product is for.

Everything here paints through `ui/theme.py` and `ui/components.py`. The screen
used to carry its own 50px top bar, its own empty-state card, its own six-second
toast and its own table geometry, and every one of those was a finding:

  * the table gave every column a flat 130px with `stretchLastSection` on, so at
    1280x860 with 20 leads Address, Website and Email were cut in 20 of 20 rows
    while at 880 the same table scrolled 400px sideways and at 2560 the last
    column alone took 1550px. Not one of those 70 cut cells had a tooltip: the
    old row builder only attached one past 80 characters, and an email address
    is 44. It is `components.table()` now, from a column spec that says which
    columns carry meaning, what each one's floor and cap are **in characters**,
    and what a cell that does not fit answers with on hover.
  * the three nothings — waiting for the first row, a run that found none, a
    filter that hid them all — were three copies of one bare bordered box, and a
    scrape that failed was a status line cut to 60 characters with the error
    itself nowhere on screen. They are `loading_state`, `empty_state` and
    `error_state`, and the error page and the danger toast both carry the whole
    message.

The screen keeps every signal and every worker call it had. What it lost is its
chrome: the shell owns the product name, the destinations and Settings, so
`home_signal` is now emitted by the empty state's own button rather than by a
button in a bar of this screen's own.
"""

import webbrowser

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QMenu, QProgressBar, QScrollArea,
    QStackedWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.exporter import export_csv, FIELD_LABELS
from core.scraper import ScrapeWorker
from ui import components
from ui.components import Cell, Column

# ── The column spec ──────────────────────────────────────────────────────────
# What each scraped field is, rather than how many pixels it was given. `fit`
# holds a short fixed value and is sized to its content and capped; `stretch`
# carries meaning and shares the spare room by weight. Both bounds are in
# characters, because the thing being sized is text and a pixel means something
# different at every font size and every DPI.
#
# The weights are the product's own priorities: `email` is the field the whole
# application exists to harvest, so it grows first, and `name` and `address`
# follow it. The floors are what a column has to keep even on the narrowest
# supported window, so their sum has to leave the 880px case without a
# horizontal scrollbar; measured at 880 the eight-column default sums to 829px
# inside an 830px viewport.
#
# A cap is in characters and a character is priced at what one costs in running
# English — `components._SAMPLE` measures 5.60px at this table's 12px — and none
# of these columns holds running English. "reception@harbourfrontdentalcare
# .example.com" is 44 characters and 257px, which is 5.84 a character, so a cap
# written as the 44 it looks like is 11px short of the value it was sized for
# and every address in the table ends in an ellipsis. Every cap here is
# therefore the measured width of a real value of that kind, converted back into
# prose characters: 52 for an email, 54 for a website, 54 for a street
# address.

_COLUMNS = {
    "domain":       Column("Search Domain", "fit", min_ch=8, max_ch=22),
    "area":         Column("Search Area", "fit", min_ch=8, max_ch=22),
    "name":         Column("Business Name", "stretch", weight=3, min_ch=14,
                           max_ch=36),
    "category":     Column("Category", "fit", min_ch=8, max_ch=22,
                           sample="Roofing contractor"),
    # A cap is also a cap on the header, and both of these were under their
    # own: "Rating" rendered in 34px of room and needs 37, and "Review Count"
    # in 37px and needs 78, so the two columns whose whole job is to be short
    # were the two with a cut label. The count is "Reviews" because the shorter
    # word is what makes a 63px column honest rather than a 96px one.
    "rating":       Column("Rating", "fit", align="right", min_ch=4, max_ch=8,
                           sample="4.8"),
    "review_count": Column("Reviews", "fit", align="right", min_ch=4,
                           max_ch=10, sample="12,345"),
    "hours":        Column("Hours", "stretch", weight=1, min_ch=10, max_ch=28),
    "address":      Column("Address", "stretch", weight=3, min_ch=12, max_ch=54),
    "website":      Column("Website", "stretch", weight=2, min_ch=12, max_ch=54),
    "phone":        Column("Phone", "fit", min_ch=10, max_ch=20,
                           sample="+1 416-555-0142"),
    "maps_link":    Column("Maps Link", "stretch", weight=1, min_ch=10,
                           max_ch=28),
    "latitude":     Column("Latitude", "fit", align="right", min_ch=6,
                           max_ch=10),
    "longitude":    Column("Longitude", "fit", align="right", min_ch=6,
                           max_ch=10),
    "place_id":     Column("Place ID", "fit", min_ch=8, max_ch=20),
    "email":        Column("Email", "stretch", weight=5, min_ch=16, max_ch=52),
    "facebook":     Column("Facebook", "stretch", weight=1, min_ch=10,
                           max_ch=28),
    "instagram":    Column("Instagram", "stretch", weight=1, min_ch=10,
                           max_ch=28),
    "linkedin":     Column("LinkedIn", "stretch", weight=1, min_ch=10,
                           max_ch=28),
    "twitter":      Column("Twitter/X", "stretch", weight=1, min_ch=10,
                           max_ch=28),
    "youtube":      Column("YouTube", "stretch", weight=1, min_ch=10,
                           max_ch=28),
    "review_1":     Column("Review 1", "stretch", weight=2, min_ch=12,
                           max_ch=56),
    "review_2":     Column("Review 2", "stretch", weight=2, min_ch=12,
                           max_ch=56),
    "review_3":     Column("Review 3", "stretch", weight=2, min_ch=12,
                           max_ch=56),
}

# Sorted as numbers rather than as the text they are painted as, so an empty
# rating sinks instead of floating above 4.9 the way an em dash does.
_NUMERIC = ("rating", "review_count", "latitude", "longitude")

# What an empty cell shows. Held here rather than written at three call sites
# because `_row_matches` and the CSV path both have to agree with it.
BLANK = "—"


class SortableItem(QTableWidgetItem):
    """Numeric-aware cell, kept because `ui/screen_outreach.py` imports it.

    This screen's own table is `components.table()`, whose `_Item` does the same
    job against an explicit sort key. The class stays until the outreach screen
    migrates, so that migration is one import change and not a broken screen.
    """

    def __lt__(self, other):
        a, b = self._num(self.text()), self._num(other.text())
        if a is not None and b is not None:
            return a < b
        return self.text().lower() < other.text().lower()

    @staticmethod
    def _num(text):
        t = (text or "").replace(",", "").strip()
        try:
            return float(t)
        except ValueError:
            return None


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


def _scrolled(page: QWidget) -> QScrollArea:
    """A page that can outgrow the area it is shown in.

    The error state is the one that can: a scrape can fail with a Selenium stack
    trace, and the contract says the whole message is on screen. Bounded by a
    scrollbar is on screen; cut to 60 characters is not.
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(page)
    return area


class ResultsScreen(QWidget):
    stop_signal = pyqtSignal()
    home_signal = pyqtSignal()
    outreach_signal = pyqtSignal(list)   # the collected records, to outreach
    # Emitted whenever a scrape settles, so the pool keeps the harvest even
    # when the user goes Home instead of Start Outreach. Leaving that screen
    # used to discard the run: nothing on Home returns to Results and
    # re-entering clears self.results, so the only copy was a CSV on disk.
    harvested_signal = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.fields = []
        self._domains = []
        self._areas = []
        self._headless = False
        self._max_results = 50
        self._export_dir = ""
        self._filters = {}
        self._multi = False
        self._is_running = False
        self._is_paused = False
        self._stopped_by_user = False
        self._search_text = ""
        self._error = ""
        self._build()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build(self):
        t = components.active_theme()
        root = _rows(self, margin="5", spacing="4", t=t)

        root.addLayout(self._build_status(t))
        root.addWidget(components.divider())
        root.addLayout(self._build_table_header(t))
        root.addWidget(self._build_table_stack(t), stretch=1)
        root.addWidget(components.hint(
            "Double-click a row to open it in Google Maps, right-click one for "
            "more, click a header to sort."))

        self.toaster = components.Toaster(self)
        root.addWidget(self.toaster.widget)

        self._set_idle_mode()

    def _build_status(self, t):
        block = _rows(margin="0", spacing="2", t=t)

        line = _cols(margin="0", spacing="2", t=t)
        self.count_label = components.heading("0", "h1")
        self.count_sub = components.body_label("collected", tone="secondary")
        self.count_sub.setWordWrap(False)
        line.addWidget(self.count_label)
        line.addWidget(self.count_sub)
        line.addStretch()

        for button in self._build_actions(t):
            line.addWidget(button)
        block.addLayout(line)

        meta = _cols(margin="0", spacing="3", t=t)
        self.area_label = components.body_label("", tone="tertiary")
        self.area_label.setWordWrap(False)
        self.status_label = components.body_label("Ready", tone="secondary")
        self.status_label.setWordWrap(False)
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        meta.addWidget(self.area_label)
        meta.addStretch()
        meta.addWidget(self.status_label)
        block.addLayout(meta)

        self.export_path_label = components.hint("")
        block.addWidget(self.export_path_label)

        # `space.hair`, the one place a 2px rule is a rule and not a layout
        # step — and taken here rather than left to the sheet's own `height`,
        # which sets the size hint and loses to QProgressBar's font-derived
        # minimum: measured 21px against a 2px hint, so the empty bar read as
        # an empty input box the width of the screen.
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(t.space["hair"])
        block.addWidget(self.progress_bar)
        return block

    def _build_actions(self, t):
        self.export_btn = components.button(
            "Export CSV", kind="secondary", size="sm",
            on_click=self._on_export_clicked)
        self.export_btn.setToolTip("Save the current results to a CSV file")

        self.outreach_btn = components.button(
            "Start Outreach", kind="primary", size="sm",
            on_click=self._on_outreach_clicked)
        self.outreach_btn.setToolTip(
            "Audit these businesses and prepare a cold-email campaign")

        self.pause_btn = components.button(
            "Pause", kind="secondary", size="sm",
            on_click=self._on_pause_clicked)

        self.action_btn = components.button(
            "Stop", kind="danger", size="sm",
            on_click=self._on_action_clicked)
        self.action_btn.setToolTip("Stop the scrape and keep what it has")
        return (self.export_btn, self.outreach_btn, self.pause_btn,
                self.action_btn)

    def _build_table_header(self, t):
        row = _cols(margin="0", spacing="3", t=t)
        row.addWidget(components.section_label("Results"))
        self.search_input = components.search_field("Filter results…")
        self.search_input.textChanged.connect(self._apply_search)
        row.addWidget(self.search_input)
        row.addStretch()
        self.row_count_label = components.body_label("", tone="tertiary")
        self.row_count_label.setWordWrap(False)
        row.addWidget(self.row_count_label)
        return row

    TABLE_PAGE, WAITING_PAGE, NOTHING_PAGE, FILTERED_PAGE, ERROR_PAGE = range(5)

    def _build_table_stack(self, t):
        self.table_stack = QStackedWidget()
        self.table = self._make_table(self.fields)
        self.table_stack.addWidget(self.table)

        self.empty_waiting = components.loading_state(
            label="Looking for businesses")
        self.empty_waiting.body_label.setText(
            "Each business appears here the moment it is read off the map, and "
            "the counter above tracks how far through the run this is.")
        self.table_stack.addWidget(self.empty_waiting)

        self.empty_nothing = components.empty_state(
            title="Nothing collected",
            body="This run found no business at all. Go back to Scrape for a "
                 "broader area or a different search term, and loosen the "
                 "'must have' filters — those drop a listing before it ever "
                 "reaches this table.",
            action="Change the search", on_action=self.home_signal.emit)
        self.table_stack.addWidget(self.empty_nothing)

        self.empty_filtered = components.empty_state(
            title="No row matches that filter",
            body="Every collected business is still here. Clear the filter box "
                 "above to see them again.",
            action="Clear the filter", on_action=self.search_input.clear)
        self.table_stack.addWidget(self.empty_filtered)

        self.error_page = None
        self.error_holder = _scrolled(QWidget())
        self.table_stack.addWidget(self.error_holder)
        return self.table_stack

    def _make_table(self, fields):
        """A table built from the spec for `fields`, wired to this screen."""
        t = components.active_theme()
        spec = [_COLUMNS.get(field, Column(FIELD_LABELS.get(field, field)))
                for field in fields] or [Column("Business Name", "stretch")]
        table = components.table(spec, density=t.density, sortable=False)
        table.customContextMenuRequested.connect(self._show_row_menu)
        table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        return table

    # ── The theme, live ──────────────────────────────────────────────────────

    def restyle(self):
        """Wear the palette the app is in now.

        Every component resolves its colours in Python at build time and writes
        them into its own stylesheet, which beats the application sheet — so a
        repolish alone leaves this screen in the palette it was constructed in.
        The state is small enough to carry across: the records, the fields, the
        search box and which mode the screen is in.
        """
        search, running, paused = self._search_text, self._is_running, self._is_paused
        status, area = self.status_label.text(), self.area_label.text()
        export, error = self.export_path_label.text(), self._error
        records, count = list(self.results), self.count_label.text()
        sub, progress = self.count_sub.text(), self.progress_bar.value()

        holder = QWidget()
        holder.setLayout(self.layout())
        # setLayout moves the LAYOUT to the holder but leaves the widgets it
        # manages parented to this screen, so deleting the holder reclaimed an
        # empty box and the old tree survived every rebuild. Each appearance
        # change abandoned ~868 widgets, and setStyleSheet repolishes every
        # widget alive, so each change cost more than the last without bound.
        for _stale in self.children():
            if isinstance(_stale, QWidget):
                _stale.setParent(holder)
        holder.deleteLater()

        self.results = []
        self._build()
        self._rebuild_table(self.fields)
        for record in records:
            self.add_table_row(record)

        self.status_label.setText(status)
        self.area_label.setText(area)
        self.export_path_label.setText(export)
        self.export_path_label.setVisible(bool(export))
        self.count_label.setText(count)
        self.count_sub.setText(sub)
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.progress_bar.setValue(progress)
        self._is_paused = paused
        if running:
            self._set_running_mode()
        if error:
            self._show_error(error)
        if search:
            self.search_input.setText(search)
        self._update_row_count()

    # ── Mode ─────────────────────────────────────────────────────────────────

    def _set_running_mode(self):
        self._is_running = True
        self._is_paused = False
        self.pause_btn.setText("Pause")
        self.pause_btn.setEnabled(True)
        self.pause_btn.show()
        self.action_btn.show()
        self.action_btn.setEnabled(True)
        self.table.setSortingEnabled(False)
        self._update_table_page()

    def _set_idle_mode(self):
        """Nothing is running, so the two run controls have nothing to act on.

        They are hidden rather than relabelled. "Stop" used to become "Scrape
        Another" and route Home, which is a destination the shell's bar now
        carries on every screen — a second one on this screen is the duplicated
        chrome the navigation contract exists to remove.
        """
        self._is_running = False
        self._is_paused = False
        self.pause_btn.hide()
        self.action_btn.hide()
        self.table.setSortingEnabled(True)
        self._update_table_page()

    # ── setup / worker ───────────────────────────────────────────────────────

    def setup(self, domains, areas, fields, headless=False, max_results=50,
              export_dir="", filters=None):
        self._stopped_by_user = False
        self._error = ""
        self.results = []
        self._domains = list(domains)
        self._areas = list(areas) if isinstance(areas, list) else [areas]
        self._headless = headless
        self._max_results = max_results
        self._export_dir = export_dir or ""
        self._filters = filters or {}
        self._multi = len(self._domains) * len(self._areas) > 1

        self.fields = list(fields)
        if len(self._areas) > 1 and "area" not in self.fields:
            self.fields = ["area"] + self.fields
        if len(self._domains) > 1 and "domain" not in self.fields:
            self.fields = ["domain"] + self.fields
        self._rebuild_table(self.fields)

        self.search_input.clear()
        self._search_text = ""
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.count_label.setText("0")
        self.count_sub.setText("of %d" % self._max_results)
        self.row_count_label.setText("")
        self.status_label.setText("Starting…")
        self.area_label.setText("%s · %s" % (", ".join(self._domains),
                                             ", ".join(self._areas)))
        self.export_path_label.setText(
            "Saving exports to %s" % self._export_dir if self._export_dir else "")
        self.export_path_label.setVisible(bool(self._export_dir))
        self.toaster.clear()
        self._set_running_mode()

    def _rebuild_table(self, fields):
        """Swap in a table built for `fields`. Columns are a spec, not a count.

        A new widget rather than `setColumnCount`, because the widths come from
        a column spec the table is constructed with — the alternative is a table
        holding a spec for the last run's fields.
        """
        old = self.table
        self.table = self._make_table(fields)
        self.table_stack.insertWidget(self.TABLE_PAGE, self.table)
        self.table_stack.removeWidget(old)
        old.setParent(None)
        old.deleteLater()
        self._update_table_page()

    def start_worker(self):
        # Never let two scrapes overlap — a previous worker that is still
        # shutting down would leave a second Chrome running alongside the new one.
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(5000):
                self.worker.abort()
                self.worker.wait(5000)

        if self.worker is not None:
            for signal, slot in (
                (self.worker.log_signal, self._on_log),
                (self.worker.result_signal, self.add_table_row),
                (self.worker.progress_signal, self.update_progress),
                (self.worker.done_signal, self.on_done),
                (self.worker.error_signal, self.on_error),
                (self.worker.paused_signal, self._on_paused),
                (self.worker.domain_finished_signal, self._on_domain_finished),
            ):
                try:
                    signal.disconnect(slot)
                except TypeError:
                    pass

        self.worker = ScrapeWorker(
            self._domains, self._areas, self.fields,
            headless=self._headless, max_results=self._max_results,
            filters=self._filters,
        )
        self.worker.log_signal.connect(self._on_log)
        self.worker.result_signal.connect(self.add_table_row)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.done_signal.connect(self.on_done)
        self.worker.error_signal.connect(self.on_error)
        self.worker.paused_signal.connect(self._on_paused)
        if self._multi:
            self.worker.domain_finished_signal.connect(self._on_domain_finished)
        self.worker.start()

    def stop_worker(self):
        self._stopped_by_user = True
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self.pause_btn.setEnabled(False)
        self.action_btn.setEnabled(False)
        self.status_label.setText("Stopping…")

    def _on_pause_clicked(self):
        if not self.worker or not self.worker.isRunning():
            return
        if self._is_paused:
            self.worker.resume()
        else:
            self.worker.pause()

    def _on_paused(self, paused: bool):
        self._is_paused = paused
        if paused:
            self.pause_btn.setText("Resume")
            self.status_label.setText("Paused")
        else:
            self.pause_btn.setText("Pause")
            self.status_label.setText("Resuming…")

    def _on_log(self, message: str, status: str):
        if status == "active":
            self.status_label.setText(message)
        elif status == "done" and message.startswith("#"):
            self.status_label.setText(message.lstrip("#").strip())

    def update_progress(self, collected: int):
        self.count_label.setText(str(collected))
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.progress_bar.setValue(min(collected, self._max_results))

    # ── table ────────────────────────────────────────────────────────────────

    def _value(self, data: dict, field: str) -> str:
        if field == "domain":
            raw = data.get("domain", data.get("_domain", ""))
        elif field == "area":
            raw = data.get("area", data.get("_area", ""))
        else:
            raw = data.get(field, "")
        return str(raw).strip() if raw else ""

    def add_table_row(self, data: dict):
        """Append one business. The cell carries its whole value, not a stub.

        The old builder cut every value to 80 characters before it reached the
        table and attached a tooltip only past that length, so an email address
        was cut by the column and had nothing to answer a hover with. The table
        elides what does not fit and `tooltip_at` hands back the whole of it.
        """
        self.results.append(data)
        cells = []
        for field in self.fields:
            text = self._value(data, field)
            if field in _NUMERIC:
                cells.append(Cell(text=text or BLANK,
                                  sort=self._number(text)))
            else:
                cells.append(Cell(text=text or BLANK,
                                  sort=(text or "").lower()))
        row = self.table.add_row(cells, data=data)

        if self._search_text and not self._row_matches(row, self._search_text):
            self.table.setRowHidden(row, True)
        else:
            self.table.scrollToBottom()
        self._update_row_count()

    @staticmethod
    def _number(text: str) -> float:
        """A sort key that sinks an empty cell instead of floating it.

        An em dash compares greater than any digit as text, so a column sorted
        as written puts every business nobody rated above the best-rated one.
        """
        try:
            return float((text or "").replace(",", "").strip())
        except ValueError:
            return float("-inf")

    def _update_row_count(self):
        total = len(self.results)
        visible = sum(0 if self.table.isRowHidden(r) else 1
                      for r in range(self.table.rowCount()))
        if visible != total:
            self.row_count_label.setText("%d of %d rows" % (visible, total))
        else:
            self.row_count_label.setText(
                "%d row%s" % (total, "s" if total != 1 else ""))
        self._update_table_page()

    def _update_table_page(self):
        """Which of the five pages the table area shows.

        Five, because "nothing here" is five different situations and each one
        needs a different sentence: rows to show, a run that has not produced
        its first row, a run that produced none, a filter that hid every row
        there is, and a run that failed outright.
        """
        total = len(self.results)
        visible = sum(0 if self.table.isRowHidden(r) else 1
                      for r in range(self.table.rowCount()))
        if visible:
            page = self.TABLE_PAGE
        elif total:
            page = self.FILTERED_PAGE
        elif self._error:
            page = self.ERROR_PAGE
        elif self._is_running:
            page = self.WAITING_PAGE
        else:
            page = self.NOTHING_PAGE
        self.table_stack.setCurrentIndex(page)

    def _row_matches(self, row: int, text: str) -> bool:
        for col in range(self.table.columnCount()):
            it = self.table.item(row, col)
            if it is None:
                continue
            full = it.data(components.FULL_ROLE)
            hay = full if isinstance(full, str) else it.text()
            if text in hay.lower():
                return True
        return False

    def _apply_search(self, text: str):
        self._search_text = (text or "").strip().lower()
        for row in range(self.table.rowCount()):
            hide = bool(self._search_text) and not self._row_matches(
                row, self._search_text)
            self.table.setRowHidden(row, hide)
        self._update_row_count()

    def _record_for_row(self, row: int) -> dict:
        it = self.table.item(row, 0)
        if it is None:
            return {}
        data = it.data(Qt.UserRole)
        return data if isinstance(data, dict) else {}

    def _on_cell_double_clicked(self, row: int, _col: int):
        rec = self._record_for_row(row)
        url = rec.get("maps_link") or rec.get("_href")
        if url:
            webbrowser.open(url)

    def _show_row_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        rec = self._record_for_row(index.row())
        if not rec:
            return
        menu = QMenu(self)
        maps_url = rec.get("maps_link") or rec.get("_href")
        website = rec.get("website")
        phone = rec.get("phone")
        email = rec.get("email")

        if maps_url:
            menu.addAction("Open in Google Maps", lambda: webbrowser.open(maps_url))
        if website:
            menu.addAction("Open website", lambda: webbrowser.open(website))
        if maps_url or website:
            menu.addSeparator()
        if phone:
            menu.addAction("Copy phone", lambda: self._copy(phone))
        if email:
            menu.addAction("Copy email", lambda: self._copy(email))
        if rec.get("name"):
            menu.addAction("Copy name", lambda: self._copy(rec.get("name")))
        menu.addAction("Copy row (tab-separated)", lambda: self._copy_row(rec))
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _copy(self, text: str):
        QApplication.clipboard().setText(str(text))
        self.status_label.setText("Copied to clipboard")

    def _copy_row(self, rec: dict):
        self._copy("\t".join(self._value(rec, field) for field in self.fields))

    # ── export / summaries ───────────────────────────────────────────────────

    def _current_task(self):
        if self.results:
            last = self.results[-1]
            return last.get("_domain", ""), last.get("_area", "")
        d = self._domains[0] if self._domains else ""
        a = self._areas[0] if self._areas else ""
        return d, a

    def _export_summary(self, domain, area, count, hit_limit) -> str:
        label = "%s in %s" % (domain, area)
        if count <= 0:
            return '"%s" — no results found. Skipping CSV.' % label
        if hit_limit:
            summary = "%d of %d collected" % (count, self._max_results)
        elif self._max_results > 0 and count < self._max_results:
            summary = ("%d of %d requested — Google Maps had fewer"
                       % (count, self._max_results))
        else:
            summary = "%d collected" % count
        return '"%s" — %s' % (label, summary)

    def _export_rows(self, rows):
        """Ensure the pseudo-columns (domain/area) are populated for CSV."""
        out = []
        for r in rows:
            r2 = dict(r)
            if "domain" in self.fields and not r2.get("domain"):
                r2["domain"] = r2.get("_domain", "")
            if "area" in self.fields and not r2.get("area"):
                r2["area"] = r2.get("_area", "")
            out.append(r2)
        return out

    def _notify_task_export(self, domain, area, count, hit_limit, rows=None):
        message = self._export_summary(domain, area, count, hit_limit)
        filepath = None
        failure = ""
        rows = self.results if rows is None else rows
        if count > 0 and rows:
            try:
                filepath = export_csv(
                    self._export_rows(rows), domain, area, self.fields,
                    self._export_dir,
                )
            except Exception as e:
                failure = str(e)
        if failure:
            self._show_toast("%s  CSV save failed: %s" % (message, failure),
                             tone="danger")
        elif filepath:
            self._show_toast("%s\nCSV saved to %s" % (message, filepath),
                             tone="success")
        else:
            self._show_toast(message)

    def _show_toast(self, message: str, *, tone: str = "info"):
        """One message, in a channel that says whether it is good news.

        The 6-second QLabel this replaces said everything in one voice and took
        itself off screen whatever it said, so a CSV that failed to save read
        exactly like one that saved and was gone before it was read. A danger
        toast waits for the user.
        """
        return self.toaster.show(message, tone=tone)

    def _on_domain_finished(self, domain, area, count, max_results, hit_limit):
        if not self.worker:
            return
        self._notify_task_export(domain, area, count, hit_limit,
                                 rows=list(self.results))
        self.results = []
        self._rebuild_table(self.fields)
        self.count_label.setText("0")
        self.progress_bar.setValue(0)
        self.row_count_label.setText("")
        self._update_table_page()
        self.status_label.setText("Continuing to next search…")
        self.worker.continue_next_domain()

    def on_done(self):
        n = len(self.results)
        if n:
            self.harvested_signal.emit(list(self.results))
        if self._stopped_by_user:
            if n > 0:
                d, a = self._current_task()
                self._notify_task_export(d, a, n, False)
                self.status_label.setText("Stopped — partial results saved")
            else:
                self.status_label.setText("Stopped — no results collected")
        else:
            if not self._multi:
                d = self._domains[0] if self._domains else ""
                a = self._areas[0] if self._areas else ""
                hit_limit = self._max_results > 0 and n >= self._max_results
                self._notify_task_export(d, a, n, hit_limit)
                self.status_label.setText(
                    "Done — %d businesses" % n if n else "Done — no results")
            else:
                self.status_label.setText("Done — all searches scraped")

        self.count_sub.setText("of %d" % self._max_results)
        self._update_row_count()
        self._set_idle_mode()
        self._stopped_by_user = False

    # ── failure ──────────────────────────────────────────────────────────────

    def on_error(self, message: str):
        """A scrape that failed, with the reason on screen rather than cut to 60.

        Two carriers, because there are two situations. With no rows the table
        area is free and the error takes it, whole, with the way out beside it.
        With rows already collected the table is what the user needs to keep
        looking at, so the message goes to a danger toast — which, alone among
        the tones, waits to be dismissed rather than vanishing mid-sentence.
        """
        text = str(message or "").strip() or "The scrape stopped for an unknown reason."
        self._error = text
        self.status_label.setText("Stopped by an error")
        self._show_error(text)
        self._show_toast("The scrape stopped: %s" % text, tone="danger")
        self._set_idle_mode()

    def _show_error(self, text: str):
        page = components.error_state(
            title="The scrape stopped",
            body=text,
            retry=("Change the search", self.home_signal.emit))
        # Selectable, because the next thing anyone does with a Selenium message
        # is paste it somewhere — and a message that can only be read is a
        # message that gets retyped wrongly.
        page.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        page.body_label.setToolTip(text)
        old = self.error_holder
        self.error_page = page
        self.error_holder = _scrolled(page)
        self.table_stack.insertWidget(self.ERROR_PAGE, self.error_holder)
        self.table_stack.removeWidget(old)
        old.setParent(None)
        old.deleteLater()
        self._update_table_page()

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_export_clicked(self):
        if not self.results:
            self._show_toast("Nothing to export yet.", tone="warning")
            return
        d, a = self._current_task()
        self._notify_task_export(d, a, len(self.results), False)

    def _on_outreach_clicked(self):
        if not self.results:
            self._show_toast("Nothing to send outreach to yet.", tone="warning")
            return
        self.outreach_signal.emit(list(self.results))

    def _on_action_clicked(self):
        if self._is_running:
            self.stop_signal.emit()
        else:
            self.home_signal.emit()
