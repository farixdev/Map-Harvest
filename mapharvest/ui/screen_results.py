import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QHeaderView,
    QFileDialog,
    QListWidgetItem,
    QAbstractItemView,
    QGridLayout,
    QApplication,
    QSpacerItem,
    QSizePolicy,
    QTableView,
)
from PyQt5.QtWidgets import QLineEdit  # noqa: F401
from PyQt5.QtWidgets import QCheckBox  # noqa: F401

from PyQt5.QtWidgets import QWidget as _QWidget
from PyQt5.QtWidgets import QHBoxLayout as _QHBoxLayout
from PyQt5.QtWidgets import QLabel as _QLabel

from mapharvest.core.exporter import export_csv
from mapharvest.core.scraper import ScrapeWorker


class ResultsScreen(QWidget):
    stop_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.worker = None

        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(52)

        self.app_name = QLabel("MapHarvest")
        self.app_name.setObjectName("app_name")

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setObjectName("outlined")
        self.export_btn.setEnabled(False)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")

        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(20, 0, 20, 0)
        top_layout.addWidget(self.app_name)
        top_layout.addStretch(1)
        top_layout.addWidget(self.export_btn)
        top_layout.addSpacing(12)
        top_layout.addWidget(self.stop_btn)

        # Log list
        self.log_list = QListWidget()
        self.log_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.log_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_list.setStyleSheet("QListWidget { background-color: #F5F4F2; border: none; }")
        self.log_list.setFixedHeight(150)

        log_frame = QFrame()
        log_frame.setObjectName("card")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(self.log_list)

        # Progress
        self.progress_label = QLabel("0 / 0")
        self.progress_label.setAlignment(Qt.AlignRight)
        self.progress_label.setStyleSheet("color: #A8A49E; font-size: 11px;")

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progress")
        self.progress_bar.setTextVisible(False)

        # Table
        self.table = QTableWidget(0, 0)
        self.table.setObjectName("table")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(topbar)
        layout.addWidget(log_frame)

        prog_row = QHBoxLayout()
        prog_row.addStretch(1)
        prog_row.addWidget(self.progress_label)
        layout.addLayout(prog_row)
        layout.addWidget(self.progress_bar)

        layout.addWidget(self.table, 1)

        self.fields = []
        self.domain = ""
        self.area = ""

        self.stop_btn.clicked.connect(self.stop_worker)
        self.export_btn.clicked.connect(self.export_csv)

    def setup(self, domains, area, fields):
        # flatten results across domains into one table
        self.fields = fields
        self.domain = domains[0] if domains else "domain"
        self.area = area

        self.log_list.clear()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(self.fields))

        headers = []
        from mapharvest.core.exporter import FIELD_LABELS

        for f in self.fields:
            headers.append(FIELD_LABELS.get(f, f))

        self.table.setHorizontalHeaderLabels(headers)

        self.progress_bar.setValue(0)
        self.progress_label.setText("0 / 0")

        self.export_btn.setEnabled(False)

    def append_log(self, message: str, status: str):
        dot = {"active": "● ", "done": "● ", "pending": "○ "}.get(status, "○ ")

        colors = {"active": "#16A34A", "done": "#A8A49E", "pending": "#D0CEC8"}
        dot_color = colors.get(status, "#D0CEC8")

        item_widget = _QWidget()
        layout = _QHBoxLayout(item_widget)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        dot_label = _QLabel(dot.strip())
        dot_label.setTextFormat(Qt.PlainText)
        dot_label.setStyleSheet(f"color: {dot_color}; font-size: 8px;")

        text_label = _QLabel(message)
        text_label.setStyleSheet("color: #6F6E69; font-size: 12px;")

        layout.addWidget(dot_label)
        layout.addWidget(text_label)
        layout.addStretch(1)

        item = QListWidgetItem(self.log_list)
        item.setSizeHint(item_widget.sizeHint())
        self.log_list.setItemWidget(item, item_widget)
        self.log_list.scrollToBottom()

    def update_progress(self, current: int, total: int):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"{current} / {total}")

    def add_table_row(self, data: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)

        from PyQt5.QtCore import Qt as _Qt

        for col, field in enumerate(self.fields):
            value = data.get(field, "")
            item = QTableWidgetItem(str(value))
            item.setFlags(item.flags() & ~_Qt.ItemIsEditable)
            self.table.setItem(row, col, item)

        self.table.scrollToBottom()

        if row == 0:
            self.export_btn.setEnabled(True)

    def start_worker(self):
        # Start uses the settings already set in setup()
        # domains/area/fields are stored in self via setup.
        pass

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV",
            f"{self.domain}_in_{self.area}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return

        # results are stored in a local list in start_worker
        if not hasattr(self, "results"):
            return

        output_dir = os.path.dirname(path)
        export_csv(self.results, self.domain, self.area, self.fields, output_dir)

    def start(self, worker: ScrapeWorker, domains, area, fields):
        self.worker = worker
        self.results = []
        self._total_estimate = 0

        worker.log_signal.connect(self.append_log)
        worker.result_signal.connect(self._on_result)
        worker.progress_signal.connect(self.update_progress)
        worker.done_signal.connect(self.on_done)
        worker.error_signal.connect(self.on_error)
        worker.start()

    def start_worker_with_config(self, domains, area, fields):
        self.results = []
        worker = ScrapeWorker(domains, area, fields)
        self.start(worker, domains, area, fields)

    def _on_result(self, data: dict):
        self.results.append(data)
        self.add_table_row(data)

    def stop_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()

    def on_done(self):
        if hasattr(self, "results"):
            if len(self.results) > 0:
                self.export_btn.setEnabled(True)

    def on_error(self, message: str):
        self.append_log(f"Error: {message}", "done")

