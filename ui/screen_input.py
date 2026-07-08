import os

from PyQt5.QtCore import QPoint, QPropertyAnimation, QTimer, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QLabel, QLineEdit,
    QPushButton, QCheckBox, QGridLayout,
    QHBoxLayout, QVBoxLayout, QFrame,
    QScrollArea, QStackedWidget, QButtonGroup,
    QSlider, QSpinBox, QListWidget, QListWidgetItem,
    QFileDialog,
)

from core.settings import load_settings, save_settings, add_saved_search
from ui.domain_list_dialog import DomainListDialog


class InputScreen(QWidget):
    start_signal = pyqtSignal(list, str, list, bool, int, str)

    FIELD_KEYS = [
        "name", "category", "rating", "review_count", "hours",
        "address", "website", "phone", "maps_link",
        "latitude", "longitude", "place_id",
        "review_1", "review_2", "review_3",
    ]
    FIELD_NAMES = [
        "Business Name", "Category", "Rating", "Review Count", "Hours",
        "Address", "Website", "Phone Number", "Maps Link",
        "Latitude", "Longitude", "Place ID",
        "Review 1", "Review 2", "Review 3",
    ]

    # Hours and Reviews require opening each listing's page (slower). Everything
    # else is read straight from the results feed, so leave the slow ones off by
    # default to keep scrapes fast.
    DEFAULT_OFF_FIELDS = {"hours", "review_1", "review_2", "review_3"}

    def __init__(self):
        super().__init__()
        self._extra_domains: list[str] = []
        self._anim = None
        self.settings = load_settings()
        self._build()
        self._apply_settings_to_ui()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)
        app_name = QLabel("MapHarvest")
        app_name.setObjectName("app_name")
        header.addWidget(app_name)
        header.addStretch()

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self.scrape_tab_btn = QPushButton("Scrape")
        self.scrape_tab_btn.setObjectName("tab")
        self.scrape_tab_btn.setCheckable(True)
        self.scrape_tab_btn.setChecked(True)
        self.settings_tab_btn = QPushButton("Settings")
        self.settings_tab_btn.setObjectName("tab")
        self.settings_tab_btn.setCheckable(True)
        self.tab_group.addButton(self.scrape_tab_btn, 0)
        self.tab_group.addButton(self.settings_tab_btn, 1)
        tab_row.addWidget(self.scrape_tab_btn)
        tab_row.addWidget(self.settings_tab_btn)
        header.addLayout(tab_row)
        root.addLayout(header)
        root.addSpacing(20)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_scrape_page())
        self.pages.addWidget(self._build_settings_page())
        root.addWidget(self.pages, stretch=1)

        self.tab_group.idClicked.connect(self.pages.setCurrentIndex)

    def _build_scrape_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        columns = QHBoxLayout()
        columns.setSpacing(40)

        left = QVBoxLayout()
        left.setSpacing(0)

        domain_header = QHBoxLayout()
        domain_header.addWidget(self._section_label("Domain"))
        domain_header.addStretch()
        self.domain_count_label = QLabel("")
        self.domain_count_label.setObjectName("muted")
        domain_header.addWidget(self.domain_count_label)
        left.addLayout(domain_header)
        left.addSpacing(8)

        domain_row = QHBoxLayout()
        domain_row.setSpacing(10)
        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText("e.g. car dealers")
        self.domain_input.setFixedHeight(40)
        self.list_btn = QPushButton("List")
        self.list_btn.setObjectName("outlined")
        self.list_btn.setFixedSize(72, 40)
        self.list_btn.setToolTip("Add multiple domains to scrape")
        self.list_btn.clicked.connect(self._open_domain_list)
        domain_row.addWidget(self.domain_input, stretch=1)
        domain_row.addWidget(self.list_btn)
        left.addLayout(domain_row)
        left.addSpacing(20)

        left.addWidget(self._section_label("Area"))
        left.addSpacing(8)
        self.area_input = QLineEdit()
        self.area_input.setPlaceholderText("e.g. Lahore")
        self.area_input.setFixedHeight(40)
        left.addWidget(self.area_input)
        left.addSpacing(20)

        limit_header = QHBoxLayout()
        limit_header.addWidget(self._section_label("Max Results"))
        limit_header.addStretch()
        self.max_results_label = QLabel("50")
        self.max_results_label.setObjectName("muted")
        limit_header.addWidget(self.max_results_label)
        left.addLayout(limit_header)
        left.addSpacing(10)

        self.max_results_slider = QSlider(Qt.Horizontal)
        self.max_results_slider.setObjectName("limit_slider")
        self.max_results_slider.setMinimum(5)
        self.max_results_slider.setMaximum(100)
        self.max_results_slider.setValue(50)
        self.max_results_slider.setTickPosition(QSlider.TicksBelow)
        self.max_results_slider.setTickInterval(25)
        self.max_results_slider.valueChanged.connect(self._on_max_slider_changed)
        left.addWidget(self.max_results_slider)
        left.addSpacing(20)

        left.addWidget(self._section_label("Export Folder"))
        left.addSpacing(8)
        export_row = QHBoxLayout()
        export_row.setSpacing(10)
        self.export_browse_btn = QPushButton("…")
        self.export_browse_btn.setObjectName("outlined")
        self.export_browse_btn.setFixedSize(40, 40)
        self.export_browse_btn.setToolTip("Choose folder for automatic CSV exports")
        self.export_browse_btn.clicked.connect(self._browse_export_dir)
        self.export_dir_input = QLineEdit()
        self.export_dir_input.setReadOnly(True)
        self.export_dir_input.setPlaceholderText("Select a folder on your computer")
        self.export_dir_input.setFixedHeight(40)
        export_row.addWidget(self.export_browse_btn)
        export_row.addWidget(self.export_dir_input, stretch=1)
        left.addLayout(export_row)
        left.addSpacing(20)

        left.addWidget(self._section_label("Recent Searches"))
        left.addSpacing(8)

        self.saved_list = QListWidget()
        self.saved_list.setObjectName("saved_list")
        self.saved_list.setMinimumHeight(120)
        self.saved_list.itemClicked.connect(self._load_saved_search)
        left.addWidget(self.saved_list, stretch=1)

        right = QVBoxLayout()
        right.setSpacing(0)

        self.fields_section_label = self._section_label("Data to Scrape")
        right.addWidget(self.fields_section_label)
        right.addSpacing(14)

        self.checkboxes = {}
        fields_grid = QGridLayout()
        fields_grid.setHorizontalSpacing(24)
        fields_grid.setVerticalSpacing(10)
        fields_grid.setColumnStretch(0, 1)
        fields_grid.setColumnStretch(1, 1)
        for i, (key, name) in enumerate(zip(self.FIELD_KEYS, self.FIELD_NAMES)):
            cb = QCheckBox(name)
            cb.setChecked(key not in self.DEFAULT_OFF_FIELDS)
            if key in self.DEFAULT_OFF_FIELDS:
                cb.setToolTip("Opens each listing's page — noticeably slower")
            self.checkboxes[key] = cb
            fields_grid.addWidget(cb, i // 2, i % 2)
        right.addLayout(fields_grid)
        right.addSpacing(10)
        fields_hint = QLabel(
            "Hours and Reviews open each listing individually and are much "
            "slower. Everything else is read directly from the results feed."
        )
        fields_hint.setObjectName("hint")
        fields_hint.setWordWrap(True)
        right.addWidget(fields_hint)
        right.addStretch(1)

        self.start_btn = QPushButton("Start Scraping")
        self.start_btn.setObjectName("start_btn")
        self.start_btn.setFixedHeight(44)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self._on_start)
        right.addWidget(self.start_btn)

        columns.addLayout(left, stretch=1)
        columns.addLayout(right, stretch=1)
        root.addLayout(columns, stretch=1)

        self._update_domain_count_label()
        return page

    def _build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        layout.addWidget(self._section_label("Browser"))
        layout.addSpacing(10)
        self.headless_cb = QCheckBox("Run headless")
        self.headless_cb.setChecked(False)
        self.headless_cb.setToolTip("Hide the Chrome window while scraping")
        self.headless_cb.toggled.connect(self._persist_settings)
        layout.addWidget(self.headless_cb)
        hint = QLabel("When off, Chrome opens visibly so you can watch progress.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addSpacing(22)

        layout.addWidget(self._section_label("Result Limit"))
        layout.addSpacing(10)
        cap_row = QHBoxLayout()
        cap_row.addWidget(QLabel("Slider maximum"))
        cap_row.addStretch()
        self.limit_cap_spin = QSpinBox()
        self.limit_cap_spin.setObjectName("spin")
        self.limit_cap_spin.setRange(25, 1000)
        self.limit_cap_spin.setSingleStep(25)
        self.limit_cap_spin.setValue(100)
        self.limit_cap_spin.setFixedWidth(80)
        self.limit_cap_spin.valueChanged.connect(self._on_limit_cap_changed)
        cap_row.addWidget(self.limit_cap_spin)
        layout.addLayout(cap_row)
        cap_hint = QLabel("Raise this to allow the scrape slider to go above 100.")
        cap_hint.setObjectName("hint")
        cap_hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(cap_hint)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_label")
        return lbl

    def _apply_settings_to_ui(self):
        cap = int(self.settings.get("max_limit_cap", 100))
        default_max = int(self.settings.get("default_max_results", 50))
        self.limit_cap_spin.blockSignals(True)
        self.limit_cap_spin.setValue(cap)
        self.limit_cap_spin.blockSignals(False)
        self._update_slider_cap(cap)
        self.max_results_slider.blockSignals(True)
        self.max_results_slider.setValue(min(default_max, cap))
        self.max_results_slider.blockSignals(False)
        self._on_max_slider_changed(self.max_results_slider.value())
        self.headless_cb.blockSignals(True)
        self.headless_cb.setChecked(bool(self.settings.get("headless", False)))
        self.headless_cb.blockSignals(False)
        export_dir = self.settings.get("export_dir") or ""
        self.export_dir_input.setText(export_dir)
        self._refresh_saved_list()

    def _update_slider_cap(self, cap: int):
        value = self.max_results_slider.value()
        self.max_results_slider.setMaximum(cap)
        if value > cap:
            self.max_results_slider.setValue(cap)

    def _on_limit_cap_changed(self, cap: int):
        self.settings["max_limit_cap"] = cap
        self._update_slider_cap(cap)
        self._persist_settings()

    def _on_max_slider_changed(self, value: int):
        self.max_results_label.setText(str(value))
        self.settings["default_max_results"] = value

    def _persist_settings(self):
        self.settings["headless"] = self.headless_cb.isChecked()
        self.settings["max_limit_cap"] = self.limit_cap_spin.value()
        self.settings["default_max_results"] = self.max_results_slider.value()
        self.settings["export_dir"] = self.export_dir_input.text().strip()
        save_settings(self.settings)

    def _browse_export_dir(self):
        start = self.export_dir_input.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select export folder", start)
        if path:
            self.export_dir_input.setText(path)
            self.settings["export_dir"] = path
            save_settings(self.settings)

    def export_dir(self) -> str:
        return self.export_dir_input.text().strip()

    def _format_saved_search(self, entry: dict) -> str:
        domains = ", ".join(entry.get("domains") or [])
        area = entry.get("area", "")
        limit = entry.get("max_results", 50)
        return f"{domains} · {area} · max {limit}"

    def _refresh_saved_list(self):
        self.saved_list.clear()
        for entry in self.settings.get("saved_searches") or []:
            item = QListWidgetItem(self._format_saved_search(entry))
            item.setData(Qt.UserRole, entry)
            self.saved_list.addItem(item)
        if self.saved_list.count() == 0:
            item = QListWidgetItem("No recent searches yet")
            item.setFlags(Qt.NoItemFlags)
            self.saved_list.addItem(item)

    def _load_saved_search(self, item: QListWidgetItem):
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        domains = entry.get("domains") or []
        if domains:
            self.domain_input.setText(domains[0])
            self._extra_domains = domains[1:]
            self._update_domain_count_label()
        self.area_input.setText(entry.get("area", ""))
        limit = int(entry.get("max_results", 50))
        cap = self.max_results_slider.maximum()
        self.max_results_slider.setValue(min(limit, cap))

    def _open_domain_list(self):
        dialog = DomainListDialog(self._extra_domains, self)
        if dialog.exec_() == DomainListDialog.Accepted:
            self._extra_domains = dialog.domains()
            self._update_domain_count_label()

    def _update_domain_count_label(self):
        count = len(self._extra_domains)
        if count:
            self.domain_count_label.setText(f"+{count} in list")
            self.list_btn.setText(f"List ({count})")
        else:
            self.domain_count_label.setText("")
            self.list_btn.setText("List")

    def _get_domains(self) -> list[str]:
        seen = set()
        domains = []
        for raw in [self.domain_input.text().strip(), *self._extra_domains]:
            d = raw.strip()
            if d and d.lower() not in seen:
                seen.add(d.lower())
                domains.append(d)
        return domains

    def get_checked_fields(self) -> list:
        return [key for key, cb in self.checkboxes.items() if cb.isChecked()]

    def is_headless(self) -> bool:
        return self.headless_cb.isChecked()

    def max_results(self) -> int:
        return self.max_results_slider.value()

    def validate(self):
        domains = self._get_domains()
        area = self.area_input.text().strip()
        fields = self.get_checked_fields()
        export_dir = self.export_dir()

        if not domains:
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self.shake(self.domain_input)
            return None, None, None, None
        if not area:
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self.shake(self.area_input)
            return None, None, None, None
        if not export_dir or not os.path.isdir(export_dir):
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self.shake(self.export_dir_input)
            return None, None, None, None
        if not fields:
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self._flash_fields_label()
            return None, None, None, None

        return domains, area, fields, export_dir

    def shake(self, widget):
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(200)
        pos = widget.pos()
        anim.setKeyValueAt(0, pos)
        anim.setKeyValueAt(0.2, pos + QPoint(-6, 0))
        anim.setKeyValueAt(0.4, pos + QPoint(6, 0))
        anim.setKeyValueAt(0.6, pos + QPoint(-4, 0))
        anim.setKeyValueAt(0.8, pos + QPoint(4, 0))
        anim.setKeyValueAt(1.0, pos)
        anim.start()
        self._anim = anim

    def _flash_fields_label(self):
        self.fields_section_label.setStyleSheet("color: #FF6B6B; font-size: 11px; font-weight: 500;")
        QTimer.singleShot(600, lambda: self.fields_section_label.setStyleSheet(""))

    def _on_start(self):
        validated = self.validate()
        if validated[0] is not None:
            domains, area, fields, export_dir = validated
            limit = self.max_results()
            self.settings = add_saved_search(self.settings, domains, area, limit)
            self._refresh_saved_list()
            self._persist_settings()
            self.start_signal.emit(
                domains, area, fields, self.is_headless(), limit, export_dir,
            )
