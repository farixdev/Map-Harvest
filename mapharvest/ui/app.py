import os

from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget
from PyQt5.QtCore import QSize

from mapharvest.ui.screen_input import InputScreen
from mapharvest.ui.screen_results import ResultsScreen
from mapharvest.core.scraper import ScrapeWorker


QSS = """
QWidget {
    background-color: #FAFAF9;
    color: #111110;
    font-family: 'DM Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}

QLineEdit, QTextEdit {
    background-color: #FFFFFF;
    border: 1px solid rgba(0, 0, 0, 0.10);
    border-radius: 8px;
    padding: 8px 12px;
    color: #111110;
    font-size: 13px;
    selection-background-color: #111110;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #111110;
    outline: none;
}

QLineEdit::placeholder, QTextEdit::placeholder {
    color: #A8A49E;
}

QPushButton {
    background-color: #111110;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 0 16px;
    height: 36px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #2C2C2B;
}

QPushButton:disabled {
    background-color: #E5E4E0;
    color: #A8A49E;
}

QPushButton#outlined {
    background-color: transparent;
    color: #111110;
    border: 1px solid rgba(0, 0, 0, 0.12);
}

QPushButton#outlined:hover {
    background-color: #F5F4F2;
}

QPushButton#outlined:disabled {
    color: #A8A49E;
    border-color: rgba(0, 0, 0, 0.06);
}

QPushButton#danger {
    background-color: transparent;
    color: #DC2626;
    border: 1px solid rgba(220, 38, 38, 0.20);
}

QPushButton#danger:hover {
    background-color: rgba(220, 38, 38, 0.05);
}

QPushButton#toggle_active {
    background-color: #111110;
    color: #FFFFFF;
    border-radius: 6px;
    font-size: 12px;
    height: 28px;
    padding: 0 12px;
}

QPushButton#toggle_inactive {
    background-color: transparent;
    color: #6F6E69;
    border: none;
    border-radius: 6px;
    font-size: 12px;
    height: 28px;
    padding: 0 12px;
}

QPushButton#toggle_inactive:hover {
    background-color: #F0EFEB;
    color: #111110;
}

QCheckBox {
    spacing: 8px;
    color: #111110;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #D0CEC8;
    border-radius: 4px;
    background: #FFFFFF;
}

QCheckBox::indicator:checked {
    background-color: #111110;
    border-color: #111110;
    image: url(none);
}

QCheckBox::indicator:hover {
    border-color: #111110;
}

QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 10px;
    gridline-color: transparent;
    font-size: 12px;
    color: #111110;
}

QTableWidget::item {
    padding: 0 12px;
    border-bottom: 1px solid #F0EFEB;
    height: 36px;
}

QTableWidget::item:selected {
    background-color: #F5F4F2;
    color: #111110;
}

QHeaderView::section {
    background-color: #FFFFFF;
    color: #A8A49E;
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0 12px;
    height: 36px;
    border: none;
    border-bottom: 1px solid #F0EFEB;
}

QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #D0CEC8;
    border-radius: 3px;
    min-height: 20px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    height: 6px;
    background: transparent;
}

QScrollBar::handle:horizontal {
    background: #D0CEC8;
    border-radius: 3px;
}

QListWidget {
    background-color: #F5F4F2;
    border: none;
    border-radius: 8px;
    padding: 8px 4px;
    font-size: 12px;
    color: #6F6E69;
}

QListWidget::item {
    padding: 4px 8px;
    border: none;
    background: transparent;
}

QProgressBar {
    background-color: #EEEDE9;
    border: none;
    border-radius: 2px;
    height: 3px;
    text-align: right;
}

QProgressBar::chunk {
    background-color: #111110;
    border-radius: 2px;
}

QLabel#section_label {
    color: #A8A49E;
    font-size: 11px;
    font-weight: 500;
}

QLabel#app_name {
    color: #111110;
    font-size: 15px;
    font-weight: 500;
}

QFrame#topbar {
    background-color: #FFFFFF;
    border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

QFrame#card {
    background-color: #FFFFFF;
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 12px;
}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MapHarvest")
        self.setFixedSize(QSize(520, 640))

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.input_screen = InputScreen()
        self.results_screen = ResultsScreen()

        self.stack.addWidget(self.input_screen)
        self.stack.addWidget(self.results_screen)

        self.stack.setCurrentIndex(0)

        self.input_screen.start_signal.connect(self.on_start)
        self.results_screen.stop_signal.connect(self.on_stop)

    def on_start(self, domains, area, fields):
        self.results_screen.setup(domains, area, fields)
        self.setFixedSize(QSize(900, 680))
        self.stack.setCurrentIndex(1)

        worker = ScrapeWorker(domains, area, fields)
        self.results_screen.start(worker, domains, area, fields)

    def on_stop(self):
        self.results_screen.stop_worker()


def run():
    import sys

    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)

    win = MainWindow()
    win.show()

    sys.exit(app.exec_())

