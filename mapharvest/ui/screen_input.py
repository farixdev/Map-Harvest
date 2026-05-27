from PyQt5.QtCore import QPoint, QPropertyAnimation, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QLabel,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QStackedWidget,
    QScrollArea,
)


class InputScreen(QWidget):
    start_signal = pyqtSignal(object, object, object)  # domains, area, fields

    def __init__(self):
        super().__init__()

        self.mode = "single"  # "single" | "list"

        # Topbar
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(52)

        app_name = QLabel("MapHarvest")
        app_name.setObjectName("app_name")
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(20, 0, 0, 0)
        top_layout.addWidget(app_name)
        top_layout.addStretch()

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        scroll.setWidget(content)

        # Section labels
        domain_label = QLabel("DOMAIN(S)")
        domain_label.setObjectName("section_label")

        area_label = QLabel("AREA")
        area_label.setObjectName("section_label")
        data_label = QLabel("DATA TO SCRAPE")
        data_label.setObjectName("section_label")

        # Domain toggle
        self.single_btn = QPushButton("Single")
        self.single_btn.setObjectName("toggle_active")
        self.single_btn.setFixedHeight(28)

        self.list_btn = QPushButton("List")
        self.list_btn.setObjectName("toggle_inactive")
        self.list_btn.setFixedHeight(28)

        toggle_frame = QFrame()
        toggle_frame.setStyleSheet("background: #F0EFEB; border-radius: 8px; padding: 3px")
        toggle_layout = QHBoxLayout(toggle_frame)
        toggle_layout.setContentsMargins(3, 0, 3, 0)
        toggle_layout.setSpacing(8)
        toggle_layout.addWidget(self.single_btn)
        toggle_layout.addWidget(self.list_btn)

        self.single_btn.clicked.connect(self.set_single_mode)
        self.list_btn.clicked.connect(self.set_list_mode)

        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText("e.g. restaurants")

        self.domain_list = QTextEdit()
        self.domain_list.setPlaceholderText("e.g.\nrestaurants\ncoffee shops")
        self.domain_list.setFixedHeight(90)

        self.domain_stack = QStackedWidget()
        self.domain_stack.addWidget(self._wrap_input(self.domain_input))
        self.domain_stack.addWidget(self._wrap_input(self.domain_list))
        self.domain_stack.setCurrentIndex(0)

        # Area input
        self.area_input = QLineEdit()
        self.area_input.setPlaceholderText("e.g. Lahore")

        # Fields checkboxes (2 columns)
        self.fields = {
            "name": QCheckBox("Business Name"),
            "rating": QCheckBox("Rating"),
            "address": QCheckBox("Address"),
            "website": QCheckBox("Website"),
            "phone": QCheckBox("Phone Number"),
            "maps_link": QCheckBox("Maps Link"),
        }

        # checked by default
        for cb in self.fields.values():
            cb.setChecked(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)

        keys = list(self.fields.keys())
        col_count = 2
        for idx, key in enumerate(keys):
            row = idx // col_count
            col = idx % col_count
            grid.addWidget(self.fields[key], row, col)

        # Start button full width
        self.start_btn = QPushButton("Start Scraping")
        self.start_btn.setFixedHeight(40)
        self.start_btn.clicked.connect(self.on_start_clicked)

        # Layout content strict order
        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(28, 28, 28, 28)
        main_layout.setSpacing(14)

        main_layout.addWidget(domain_label)

        main_layout.addWidget(self._frame_for_toggle(toggle_frame))
        # Domain input area
        main_layout.addWidget(self.domain_stack)

        main_layout.addWidget(area_label)
        main_layout.addWidget(self._wrap_input(self.area_input))

        main_layout.addWidget(data_label)
        main_layout.addLayout(grid)

        main_layout.addSpacing(10)
        main_layout.addWidget(self.start_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(topbar)
        layout.addWidget(scroll)

    def _frame_for_toggle(self, frame: QFrame):
        # domain toggle is already wrapped; just ensure left alignment
        wrapper = QFrame()
        w_layout = QVBoxLayout(wrapper)
        w_layout.setContentsMargins(0, 0, 0, 0)
        w_layout.addWidget(frame)

        return wrapper

    def _wrap_input(self, w: QWidget):
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet("QFrame#card { background-color:#FFFFFF; border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; }")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        c_layout.addWidget(w)
        return card

    def set_single_mode(self):
        self.mode = "single"
        self.single_btn.setObjectName("toggle_active")
        self.list_btn.setObjectName("toggle_inactive")
        self.domain_stack.setCurrentIndex(0)

    def set_list_mode(self):
        self.mode = "list"
        self.list_btn.setObjectName("toggle_active")
        self.single_btn.setObjectName("toggle_inactive")
        self.domain_stack.setCurrentIndex(1)

    def get_checked_fields(self):
        keys = [k for k, cb in self.fields.items() if cb.isChecked()]
        return keys

    def shake(self, widget):
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(200)
        pos = widget.pos()
        from PyQt5.QtCore import QPoint as _QPoint

        anim.setKeyValueAt(0, pos)
        anim.setKeyValueAt(0.2, pos + _QPoint(-6, 0))
        anim.setKeyValueAt(0.4, pos + _QPoint(6, 0))
        anim.setKeyValueAt(0.6, pos + _QPoint(-4, 0))
        anim.setKeyValueAt(0.8, pos + _QPoint(4, 0))
        anim.setKeyValueAt(1.0, pos)
        anim.start()
        self._anim = anim

    def validate(self):
        if self.mode == "single":
            domains = [self.domain_input.text().strip()]
        else:
            domains = [d.strip() for d in self.domain_list.toPlainText().splitlines() if d.strip()]

        area = self.area_input.text().strip()
        fields = self.get_checked_fields()

        if not domains or not domains[0]:
            self.shake(self.domain_input)
            return None, None, None
        if not area:
            self.shake(self.area_input)
            return None, None, None
        if not fields:
            return None, None, None

        return domains, area, fields

    def on_start_clicked(self):
        domains, area, fields = self.validate()
        if not domains:
            # brief shake for checkbox section could be added; spec says briefly flash label red.
            return
        self.start_signal.emit(domains, area, fields)

