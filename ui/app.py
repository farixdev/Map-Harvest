import sys

from PyQt5.QtCore import QSize
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget

from ui.screen_input import InputScreen
from ui.screen_results import ResultsScreen

QSS = """
QWidget {
    background-color: #1C1C1E;
    color: #E5E5E7;
    font-family: 'DM Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #1C1C1E;
}

QLineEdit, QTextEdit {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 8px 12px;
    color: #E5E5E7;
    font-size: 13px;
    selection-background-color: #636366;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #636366;
    outline: none;
}

QLineEdit::placeholder, QTextEdit::placeholder {
    color: #8E8E93;
}

QPushButton {
    background-color: #636366;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 0 16px;
    height: 36px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #78787A;
}

QPushButton:disabled {
    background-color: #3A3A3C;
    color: #636366;
}

QPushButton#outlined {
    background-color: transparent;
    color: #E5E5E7;
    border: 1px solid #48484A;
}

QPushButton#outlined:hover {
    background-color: #3A3A3C;
}

QPushButton#outlined:disabled {
    color: #636366;
    border-color: #3A3A3C;
}

QPushButton#danger {
    background-color: transparent;
    color: #FF6B6B;
    border: 1px solid rgba(255, 107, 107, 0.35);
}

QPushButton#danger:hover {
    background-color: rgba(255, 107, 107, 0.10);
}

QCheckBox {
    spacing: 8px;
    color: #E5E5E7;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #48484A;
    border-radius: 4px;
    background: #2C2C2E;
}

QCheckBox::indicator:checked {
    background-color: #636366;
    border-color: #636366;
}

QCheckBox::indicator:hover {
    border-color: #78787A;
}

QTableWidget {
    background-color: #242426;
    alternate-background-color: #2A2A2C;
    border: 1px solid #3A3A3C;
    border-radius: 10px;
    gridline-color: #3A3A3C;
    font-size: 12px;
    color: #E5E5E7;
}

QTableWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid #3A3A3C;
}

QTableWidget::item:selected {
    background-color: #3A3A3C;
    color: #FFFFFF;
}

QHeaderView::section {
    background-color: #2C2C2E;
    color: #8E8E93;
    font-size: 11px;
    font-weight: 600;
    padding: 8px 10px;
    height: 34px;
    border: none;
    border-bottom: 1px solid #3A3A3C;
    border-right: 1px solid #3A3A3C;
}

QScrollBar:vertical {
    background: #1C1C1E;
    width: 10px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #48484A;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    height: 10px;
    background: #1C1C1E;
}

QScrollBar::handle:horizontal {
    background: #48484A;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QListWidget {
    background-color: #242426;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 6px 4px;
    font-size: 12px;
    color: #AEAEB2;
}

QListWidget::item {
    padding: 4px 8px;
    border: none;
    background: transparent;
}

QProgressBar {
    background-color: #3A3A3C;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: #8E8E93;
    font-size: 10px;
}

QProgressBar::chunk {
    background-color: #636366;
    border-radius: 3px;
}

QLabel#section_label {
    color: #8E8E93;
    font-size: 11px;
    font-weight: 500;
}

QLabel#app_name {
    color: #E5E5E7;
    font-size: 15px;
    font-weight: 600;
}

QLabel#count_label {
    color: #E5E5E7;
    font-size: 22px;
    font-weight: 600;
}

QLabel#count_sub {
    color: #8E8E93;
    font-size: 12px;
}

QLabel#muted {
    color: #8E8E93;
    font-size: 11px;
}

QLabel#status_text {
    color: #E5E5E7;
    font-size: 13px;
    font-weight: 500;
}

QFrame#status_card {
    background-color: #242426;
    border: 1px solid #3A3A3C;
    border-radius: 10px;
}

QFrame#divider {
    background-color: #3A3A3C;
}

QTableWidget#results_table {
    border-radius: 10px;
}

QFrame#topbar {
    background-color: #242426;
    border-bottom: 1px solid #3A3A3C;
}

QFrame#card {
    background-color: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 12px;
}

QScrollArea {
    border: none;
    background: #1C1C1E;
}

QScrollArea > QWidget > QWidget {
    background: #1C1C1E;
}

QDialog {
    background-color: #1C1C1E;
}
"""


def load_font(app: QApplication) -> None:
    families = QFontDatabase().families()
    if "DM Sans" in families:
        app.setFont(QFont("DM Sans", 13))
    elif sys.platform == "darwin":
        app.setFont(QFont(".AppleSystemUIFont", 13))
    else:
        app.setFont(QFont("Segoe UI", 13))


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

        self.input_screen.start_signal.connect(self.on_start)
        self.results_screen.stop_signal.connect(self.on_stop)

    def on_start(self, domains, area, fields):
        self.results_screen.setup(domains, area, fields)
        self.setFixedSize(QSize(960, 720))
        self.stack.setCurrentIndex(1)
        self.results_screen.start_worker()

    def on_stop(self):
        self.results_screen.stop_worker()


def run():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    load_font(app)
    app.setStyleSheet(QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
