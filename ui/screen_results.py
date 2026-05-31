from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QVBoxLayout, QFrame,
    QHeaderView, QFileDialog, QAbstractItemView,
    QSizePolicy,
)

from core.exporter import export_csv, FIELD_LABELS
from core.scraper import ScrapeWorker


class ResultsScreen(QWidget):
    stop_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.fields = []
        self.domain = ""
        self.area = ""
        self._domains = []
        self._headless = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(48)
        top_row = QHBoxLayout(topbar)
        top_row.setContentsMargins(20, 0, 20, 0)
        top_row.setSpacing(8)

        name_lbl = QLabel("MapHarvest")
        name_lbl.setObjectName("app_name")

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setObjectName("outlined")
        self.export_btn.setFixedHeight(30)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_csv)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setFixedHeight(30)
        self.stop_btn.clicked.connect(self._on_stop_clicked)

        top_row.addWidget(name_lbl)
        top_row.addStretch()
        top_row.addWidget(self.export_btn)
        top_row.addWidget(self.stop_btn)
        root.addWidget(topbar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(12)

        # Status area — flat, no card
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

        root.addWidget(body)

    def setup(self, domains: list, area: str, fields: list, headless: bool = False):
        self.results = []
        self.area = area
        self._domains = domains
        self._headless = headless
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
        self.progress_bar.setMaximum(100)
        self.count_label.setText("0")
        self.count_sub.setText("collected")
        self.row_count_label.setText("")
        self.status_label.setText("Starting...")
        self.area_label.setText(f"{', '.join(domains)} · {area}")

        self.export_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def start_worker(self):
        self.worker = ScrapeWorker(
            self._domains, self.area, self.fields, headless=self._headless,
        )
        self.worker.log_signal.connect(self._on_log)
        self.worker.result_signal.connect(self.add_table_row)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.done_signal.connect(self.on_done)
        self.worker.error_signal.connect(self.on_error)
        self.worker.start()

    def stop_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Stopping...")

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
        self.progress_bar.setMaximum(max(collected, 1))
        self.progress_bar.setValue(collected)
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
        self.export_btn.setEnabled(True)
        self.row_count_label.setText(f"{len(self.results)} row{'s' if len(self.results) != 1 else ''}")

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV",
            f"{self.domain}_in_{self.area}.csv",
            "CSV Files (*.csv)",
        )
        if path:
            export_csv(self.results, self.domain, self.area, self.fields, path)

    def on_done(self):
        self.stop_btn.setEnabled(False)
        n = len(self.results)
        self.status_label.setText(f"Done — {n} businesses" if n else "Done — no results")
        self.count_sub.setText("total")
        if n:
            self.row_count_label.setText(f"{n} row{'s' if n != 1 else ''}")

    def on_error(self, message: str):
        self.status_label.setText(f"Error: {message[:60]}")
        self.stop_btn.setEnabled(False)

    def _on_stop_clicked(self):
        self.stop_signal.emit()
