from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QVBoxLayout,
    QHeaderView, QAbstractItemView,
    QSizePolicy,
)

from core.exporter import export_csv, FIELD_LABELS
from core.scraper import ScrapeWorker


class ResultsScreen(QWidget):
    stop_signal = pyqtSignal()
    home_signal = pyqtSignal()

    TOAST_MS = 5000

    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.fields = []
        self.domain = ""
        self.area = ""
        self._domains = []
        self._headless = False
        self._max_results = 50
        self._export_dir = ""
        self._is_running = False
        self._is_paused = False
        self._stopped_by_user = False
        self._toast_timer = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("results_header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        name_lbl = QLabel("MapHarvest")
        name_lbl.setObjectName("app_name")

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setObjectName("outlined")
        self.pause_btn.setFixedHeight(30)
        self.pause_btn.clicked.connect(self._on_pause_clicked)

        self.action_btn = QPushButton("Stop")
        self.action_btn.setObjectName("danger")
        self.action_btn.setFixedHeight(30)
        self.action_btn.clicked.connect(self._on_action_clicked)

        top_row.addWidget(name_lbl)
        top_row.addStretch()
        top_row.addWidget(self.pause_btn)
        top_row.addWidget(self.action_btn)
        header_layout.addLayout(top_row)

        self.export_path_label = QLabel("")
        self.export_path_label.setObjectName("muted")
        self.export_path_label.setWordWrap(True)
        header_layout.addWidget(self.export_path_label)

        root.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 8, 20, 16)
        body_layout.setSpacing(12)

        status_block = QVBoxLayout()
        status_block.setSpacing(6)
        status_block.setContentsMargins(0, 0, 0, 0)

        top_line = QHBoxLayout()
        top_line.setSpacing(16)
        top_line.setContentsMargins(0, 0, 0, 0)

        count_wrap = QHBoxLayout()
        count_wrap.setSpacing(6)
        count_wrap.setContentsMargins(0, 0, 0, 0)
        count_wrap.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.count_label = QLabel("0")
        self.count_label.setObjectName("count_label")
        self.count_sub = QLabel("collected")
        self.count_sub.setObjectName("count_sub")
        count_wrap.addWidget(self.count_label)
        count_wrap.addWidget(self.count_sub)
        top_line.addLayout(count_wrap)
        top_line.addStretch()

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("status_text")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top_line.addWidget(self.status_label)
        status_block.addLayout(top_line)

        self.area_label = QLabel("")
        self.area_label.setObjectName("muted")
        self.area_label.setAlignment(Qt.AlignLeft)
        status_block.addWidget(self.area_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        status_block.addWidget(self.progress_bar)

        body_layout.addLayout(status_block)

        table_header = QHBoxLayout()
        table_header.setContentsMargins(0, 4, 0, 0)
        table_title = QLabel("Results")
        table_title.setObjectName("section_label")
        self.row_count_label = QLabel("")
        self.row_count_label.setObjectName("muted")
        table_header.addWidget(table_title)
        table_header.addStretch()
        table_header.addWidget(self.row_count_label)
        body_layout.addLayout(table_header)

        self.table = QTableWidget(0, 0)
        self.table.setObjectName("results_table")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.horizontalHeader().setDefaultSectionSize(130)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body_layout.addWidget(self.table, stretch=1)

        self.toast_label = QLabel("")
        self.toast_label.setObjectName("toast")
        self.toast_label.setWordWrap(True)
        self.toast_label.setAlignment(Qt.AlignCenter)
        self.toast_label.hide()
        body_layout.addWidget(self.toast_label)

        root.addWidget(body)

    def _restyle(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _set_running_mode(self):
        self._is_running = True
        self._is_paused = False
        self.pause_btn.setText("Pause")
        self.pause_btn.setEnabled(True)
        self.pause_btn.show()
        self.action_btn.setText("Stop")
        self.action_btn.setObjectName("danger")
        self._restyle(self.action_btn)
        self.action_btn.setEnabled(True)

    def _set_idle_mode(self):
        self._is_running = False
        self._is_paused = False
        self.pause_btn.hide()
        self.action_btn.setText("Scrape Another")
        self.action_btn.setObjectName("outlined")
        self._restyle(self.action_btn)
        self.action_btn.setEnabled(True)

    def setup(
        self,
        domains: list,
        area: str,
        fields: list,
        headless: bool = False,
        max_results: int = 50,
        export_dir: str = "",
    ):
        self._stopped_by_user = False
        self.results = []
        self.area = area
        self._domains = domains
        self._headless = headless
        self._max_results = max_results
        self._export_dir = export_dir or ""
        self.domain = domains[0] if domains else ""

        self.fields = list(fields)
        if len(domains) > 1 and "domain" not in self.fields:
            self.fields = ["domain"] + self.fields

        labels = [FIELD_LABELS.get(f, f) for f in self.fields]
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(labels))
        self.table.setHorizontalHeaderLabels(labels)

        if labels:
            self.table.setColumnWidth(0, 180)

        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.count_label.setText("0")
        self.count_sub.setText(f"of {self._max_results}")
        self.row_count_label.setText("")
        self.status_label.setText("Starting...")
        self.area_label.setText(f"{', '.join(domains)} · {area}")

        if self._export_dir:
            self.export_path_label.setText(f"Saving exports to {self._export_dir}")
        else:
            self.export_path_label.setText("")

        self._hide_toast()
        self._set_running_mode()

    def start_worker(self):
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
            self._domains,
            self.area,
            self.fields,
            headless=self._headless,
            max_results=self._max_results,
        )
        self.worker.log_signal.connect(self._on_log)
        self.worker.result_signal.connect(self.add_table_row)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.done_signal.connect(self.on_done)
        self.worker.error_signal.connect(self.on_error)
        self.worker.paused_signal.connect(self._on_paused)
        if len(self._domains) > 1:
            self.worker.domain_finished_signal.connect(self._on_domain_finished)
        self.worker.start()

    def stop_worker(self):
        self._stopped_by_user = True
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self.pause_btn.setEnabled(False)
        self.action_btn.setEnabled(False)
        self.status_label.setText("Stopping...")

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
            self.status_label.setText("Resuming...")

    def _on_log(self, message: str, status: str):
        if status == "active":
            self.status_label.setText(message)
        elif status == "done" and message.startswith("#"):
            short = message.lstrip("#").strip()
            if len(short) > 48:
                short = short[:45] + "..."
            self.status_label.setText(short)

    def update_progress(self, collected: int):
        self.count_label.setText(str(collected))
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.progress_bar.setValue(min(collected, self._max_results))
        self.row_count_label.setText(f"{collected} row{'s' if collected != 1 else ''}")

    def add_table_row(self, data: dict):
        self.results.append(data)
        row = self.table.rowCount()
        self.table.insertRow(row)

        for col, field in enumerate(self.fields):
            raw = data.get(field, data.get("_domain", "") if field == "domain" else "")
            text = str(raw).strip() if raw else "—"
            if len(text) > 80:
                display = text[:77] + "..."
                tip = text
            else:
                display = text
                tip = ""

            item = QTableWidgetItem(display)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            if tip:
                item.setToolTip(tip)
            self.table.setItem(row, col, item)

        self.table.scrollToBottom()
        self.row_count_label.setText(f"{len(self.results)} row{'s' if len(self.results) != 1 else ''}")

    def _rows_for_domain(self, domain: str) -> list:
        return [
            r for r in self.results
            if r.get("domain", r.get("_domain", "")) == domain
        ]

    def _export_summary(self, domain: str, count: int, hit_limit: bool) -> str:
        if count <= 0:
            return f'"{domain} in {self.area}" — no results found. Skipping CSV.'
        if hit_limit:
            summary = f"{count} of {self._max_results} collected"
        elif self._max_results > 0 and count < self._max_results:
            summary = (
                f"{count} of {self._max_results} requested — "
                "Google Maps had fewer results"
            )
        else:
            summary = f"{count} collected"
        return f'"{domain} in {self.area}" — {summary}'

    def _save_domain_csv(self, domain: str, rows: list) -> str:
        return export_csv(rows, domain, self.area, self.fields, self._export_dir)

    def _show_toast(self, message: str, filepath: str | None = None):
        if filepath:
            text = f"{message}\nCSV saved to:\n{filepath}"
        else:
            text = message
        self.toast_label.setText(text)
        self.toast_label.show()
        if self._toast_timer is not None:
            self._toast_timer.stop()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)
        self._toast_timer.start(self.TOAST_MS)

    def _hide_toast(self):
        self.toast_label.hide()
        self.toast_label.clear()

    def _notify_domain_export(self, domain: str, count: int, hit_limit: bool):
        message = self._export_summary(domain, count, hit_limit)
        filepath = None
        if count > 0:
            rows = self._rows_for_domain(domain)
            if rows:
                filepath = self._save_domain_csv(domain, rows)
        self._show_toast(message, filepath)

    def _on_domain_finished(self, domain: str, count: int, max_results: int, hit_limit: bool):
        if not self.worker:
            return

        self._notify_domain_export(domain, count, hit_limit)

        self.results = []
        self.table.setRowCount(0)
        self.count_label.setText("0")
        self.progress_bar.setValue(0)
        self.row_count_label.setText("")
        self.domain = ""
        self.status_label.setText("Continuing to next domain...")

        self.worker.continue_next_domain()

    def on_done(self):
        n = len(self.results)
        if self._stopped_by_user:
            if n > 0 and self.domain:
                self._notify_domain_export(self.domain, n, False)
                self.status_label.setText("Stopped — partial results saved")
            else:
                self.status_label.setText("Stopped — no results collected")
        else:
            if len(self._domains) == 1 and self._domains:
                domain = self._domains[0]
                hit_limit = self._max_results > 0 and n >= self._max_results
                self._notify_domain_export(domain, n, hit_limit)
                self.status_label.setText(
                    f"Done — {n} businesses" if n else "Done — no results"
                )
            else:
                self.status_label.setText("Done — all domains scraped")

        self.count_sub.setText(f"of {self._max_results}")
        if n:
            self.row_count_label.setText(f"{n} row{'s' if n != 1 else ''}")
        self._set_idle_mode()
        self._stopped_by_user = False

    def on_error(self, message: str):
        self.status_label.setText(f"Error: {message[:60]}")
        self._set_idle_mode()

    def _on_action_clicked(self):
        if self._is_running:
            self.stop_signal.emit()
        else:
            self.home_signal.emit()
