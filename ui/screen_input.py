from PyQt5.QtCore import QPoint, QPropertyAnimation, QTimer, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QLabel, QLineEdit,
    QPushButton, QCheckBox, QGridLayout,
    QHBoxLayout, QVBoxLayout, QFrame,
    QScrollArea, QStackedWidget, QButtonGroup,
)

from ui.domain_list_dialog import DomainListDialog


class InputScreen(QWidget):
    start_signal = pyqtSignal(list, str, list, bool)

    FIELD_KEYS = [
        "name", "category", "rating", "review_count", "status", "hours",
        "address", "website", "phone", "maps_link",
        "review_1", "review_2", "review_3",
    ]
    FIELD_NAMES = [
        "Business Name", "Category", "Rating", "Review Count", "Status", "Hours",
        "Address", "Website", "Phone Number", "Maps Link",
        "Review 1", "Review 2", "Review 3",
    ]

    def __init__(self):
        super().__init__()
        self._extra_domains: list[str] = []
        self._anim = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        # Header row: title + tabs
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        domain_header = QHBoxLayout()
        domain_header.addWidget(self._section_label("Domain"))
        domain_header.addStretch()
        self.domain_count_label = QLabel("")
        self.domain_count_label.setObjectName("muted")
        domain_header.addWidget(self.domain_count_label)
        layout.addLayout(domain_header)
        layout.addSpacing(6)

        domain_row = QHBoxLayout()
        domain_row.setSpacing(8)
        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText("e.g. car dealers")
        self.domain_input.setFixedHeight(38)
        self.list_btn = QPushButton("List")
        self.list_btn.setObjectName("outlined")
        self.list_btn.setFixedSize(68, 38)
        self.list_btn.setToolTip("Add multiple domains to scrape")
        self.list_btn.clicked.connect(self._open_domain_list)
        domain_row.addWidget(self.domain_input)
        domain_row.addWidget(self.list_btn)
        layout.addLayout(domain_row)
        layout.addSpacing(18)

        layout.addWidget(self._section_label("Area"))
        layout.addSpacing(6)
        self.area_input = QLineEdit()
        self.area_input.setPlaceholderText("e.g. Lahore")
        self.area_input.setFixedHeight(38)
        layout.addWidget(self.area_input)
        layout.addSpacing(18)

        self.fields_section_label = self._section_label("Data to Scrape")
        layout.addWidget(self.fields_section_label)
        layout.addSpacing(8)

        self.checkboxes = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for i, (key, name) in enumerate(zip(self.FIELD_KEYS, self.FIELD_NAMES)):
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.checkboxes[key] = cb
            grid.addWidget(cb, i // 2, i % 2)
        layout.addLayout(grid)
        layout.addSpacing(24)

        self.start_btn = QPushButton("Start Scraping")
        self.start_btn.setFixedHeight(40)
        self.start_btn.clicked.connect(self._on_start)
        layout.addWidget(self.start_btn)
        layout.addStretch()

        scroll.setWidget(body)
        self._update_domain_count_label()
        return scroll

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        layout.addWidget(self._section_label("Browser"))
        layout.addSpacing(10)

        self.headless_cb = QCheckBox("Run headless")
        self.headless_cb.setChecked(False)
        self.headless_cb.setToolTip("Hide the Chrome window while scraping")
        layout.addWidget(self.headless_cb)

        hint = QLabel("When off, Chrome opens visibly so you can watch progress.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_label")
        return lbl

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

    def validate(self):
        domains = self._get_domains()
        area = self.area_input.text().strip()
        fields = self.get_checked_fields()

        if not domains:
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self.shake(self.domain_input)
            return None, None, None
        if not area:
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self.shake(self.area_input)
            return None, None, None
        if not fields:
            self.pages.setCurrentIndex(0)
            self.scrape_tab_btn.setChecked(True)
            self._flash_fields_label()
            return None, None, None

        return domains, area, fields

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
        domains, area, fields = self.validate()
        if domains is not None:
            self.start_signal.emit(domains, area, fields, self.is_headless())
