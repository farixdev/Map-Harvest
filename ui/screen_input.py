"""The screen the product opens on, and the closed loop it used to leave open.

Everything here paints through `ui/theme.py` and `ui/components.py`. What it
used to paint through was its own 31px top bar, its own three tabs, its own
section labels and one hex literal, and what that cost was measured:

  * **the first run came back with nothing to mail.** `email` was unticked by
    default, so the default scrape produced no email column, Start Outreach
    imported 0 leads, and the only way back to a scrape that had one was a full
    re-run of Chrome. Email is on by default now, because the address is the
    field the whole application exists to collect, and the sentence under the
    grid says what it costs rather than leaving the user to find out.
  * **the export folder was a modal dialog before the first scrape.** It is
    filled in with a real folder now and created on the way past, so the default
    path from a fresh install to a scrape is four clicks rather than eleven.
  * **an invalid field shook for 200ms and said nothing.** Every rule this
    screen enforces is now a sentence under the field it belongs to, and the
    field it belongs to takes the keyboard.
  * **at 2560 the page was empty boxes and 600px-wide checkbox cells.** No text
    on this screen runs past `MEASURE_CH` characters, because every measured
    thing on it is a component that caps itself, and the blocks reflow into one
    to four of those columns depending on what the window can hold — so the wide
    window gains a column rather than the same column stretched.

The chrome is gone with the tabs: the shell owns the product name, the
destinations and Settings, so this screen draws none of them. `settings_signal`
and `outreach_signal` stay because the window still routes them, and every
public method and signal the app calls is unchanged.
"""

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from core.settings import add_saved_search, load_settings, save_settings
from ui import components
from ui.domain_list_dialog import DomainListDialog, ListDialog

# The measure of one column of this page, in characters, handed to the
# components that know what a character costs. Nothing here converts it to
# pixels: `body_label` and `hint` cap themselves and re-take the cap when the
# font lands, so a column is exactly as wide as its own widest capped sentence
# in whatever font and at whatever DPI the machine is running.
MEASURE_CH = 60

# Which block goes in which column, by how many columns there is room for. The
# audit measured this page at 2560 as "mostly empty boxes and 600px-wide
# checkbox cells", and the two ways out of that are to cap the measure and use
# the space or to cap it and leave a gutter. This is the first: a wide window
# gains a column, and what is left over after four is the gutter.
SEARCH, FIELDS, FILTERS, OUTPUT, RECENT = range(5)

_LAYOUTS = {
    1: ((SEARCH, FIELDS, FILTERS, OUTPUT, RECENT),),
    2: ((SEARCH, OUTPUT, RECENT), (FIELDS, FILTERS)),
    3: ((SEARCH, OUTPUT), (FIELDS, RECENT), (FILTERS,)),
    4: ((SEARCH,), (FIELDS,), (FILTERS,), (OUTPUT, RECENT)),
}

MAX_COLUMNS = max(_LAYOUTS)


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


def _block(t, title: str) -> QWidget:
    """One titled section of the page, on the page's own ground.

    Deliberately not a `card()`. Five cards down a column is five borders drawn
    around things that are one thing, and this is the screen the audit called
    "mostly empty boxes" — the rule over a group of controls is the section
    label, and the ground under it is the page.
    """
    widget = QWidget()
    box = _rows(widget, margin="0", spacing="3", t=t)
    box.addWidget(components.section_label(title))
    widget.body = box
    return widget


class _Field(QWidget):
    """A labelled input, and the two lines that say what it wants and what is wrong.

    The error is the whole of task D: an invalid field used to answer with a
    200ms shake and no words, so the one rule a user cannot guess — that the
    export folder has to exist — read as the Start button simply not working.

    The message appears under its own field and pushes what is below it down.
    What may not move is the control that was just clicked, and it does not:
    Start Scraping sits in the footer, outside the scrolling page these fields
    are laid out in, so it is in the same place before and after.
    """

    def __init__(self, t, label: str, *, placeholder: str = "",
                 help_text: str = "", trailing=None, read_only: bool = False,
                 parent=None):
        super().__init__(parent)
        box = _rows(self, margin="0", spacing="1", t=t)

        head = _cols(margin="0", spacing="2", t=t)
        head.addWidget(components.section_label(label))
        head.addStretch()
        self.note = components.body_label("", tone="tertiary")
        self.note.setWordWrap(False)
        head.addWidget(self.note)
        box.addLayout(head)

        row = _cols(margin="0", spacing="2", t=t)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFixedHeight(t.control["md"])
        self.edit.setReadOnly(read_only)
        row.addWidget(self.edit, stretch=1)
        if trailing is not None:
            row.addWidget(trailing)
        box.addLayout(row)

        self.help = components.hint(help_text, max_chars=MEASURE_CH)
        self.help.setVisible(bool(help_text))
        box.addWidget(self.help)

        self.error = components.body_label("", tone="danger",
                                           max_chars=MEASURE_CH)
        self.error.hide()
        box.addWidget(self.error)
        self.edit.textChanged.connect(lambda _text: self.set_error(""))

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value or "")

    def set_note(self, text: str) -> None:
        self.note.setText(text or "")

    def set_error(self, message: str = "") -> None:
        self.error.setText(message or "")
        self.error.setVisible(bool(message))


class _Page(QWidget):
    """The body of the screen: blocks laid into as many columns as fit.

    How many fit is asked of the blocks themselves rather than of a pixel
    constant — every sentence on this page is capped in characters by the
    component that draws it, so the widest block's own size hint is the width of
    one column in the font actually in use.
    """

    def __init__(self, blocks, t, parent=None):
        super().__init__(parent)
        self._blocks = list(blocks)
        self._gap = t.space["7"]
        self._placed = 0
        self._laying_out = False

        row = _cols(self, margin="0", spacing="7", t=t)
        row.addStretch()
        self._columns = []
        for _ in range(MAX_COLUMNS):
            column = QWidget()
            _rows(column, margin="0", spacing="6", t=t)
            column.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            row.addWidget(column)
            self._columns.append(column)
        row.addStretch()
        self._place(1)

    def columns_shown(self) -> int:
        return self._placed

    def column_width(self) -> int:
        """What one column asks for, which is what caps the measure on this page."""
        return max(block.sizeHint().width() for block in self._blocks)

    def resizeEvent(self, event) -> None:
        """Guarded against itself: widening a column can move the scrollbar."""
        super().resizeEvent(event)
        if self._laying_out:
            return
        self._laying_out = True
        try:
            width = self.column_width()
            for column in self._columns:
                column.setFixedWidth(width)
            self._place(self._fits(self.width(), width))
        finally:
            self._laying_out = False

    def _fits(self, width: int, unit: int) -> int:
        step = unit + self._gap
        if step <= 0:
            return 1
        return max(1, min(MAX_COLUMNS, (width + self._gap) // step))

    def _place(self, columns: int) -> None:
        if columns == self._placed:
            return
        self._placed = columns
        for index, column in enumerate(self._columns):
            box = column.layout()
            while box.count():
                item = box.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
            column.setVisible(index < columns)
        for index, keys in enumerate(_LAYOUTS[columns]):
            box = self._columns[index].layout()
            for key in keys:
                block = self._blocks[key]
                box.addWidget(block)
                block.show()
            box.addStretch()


def _default_export_dir() -> str:
    """Where the CSVs go when the user has not said, so the first run needs no dialog.

    Filled in rather than created: nothing on this screen touches the disk until
    the scrape starts, and `_ensure_export_dir` is what makes the folder on the
    way past.
    """
    home = os.path.expanduser("~")
    documents = os.path.join(home, "Documents")
    base = documents if os.path.isdir(documents) else home
    return os.path.join(base, "MapHarvest")


def _ensure_export_dir(path: str) -> str:
    """Nothing if `path` is now a folder, or the sentence saying why it is not.

    Made here rather than demanded of the user, because the folder that does
    not exist yet is the one this screen just proposed.
    """
    if not path:
        return ("Choose the folder the CSV should be written to, or leave the "
                "one already here.")
    if os.path.isdir(path):
        return ""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as error:
        return "That folder could not be created: %s" % (error.strerror or error)
    return ""


class InputScreen(QWidget):
    # domains, areas, fields, headless, max_results, export_dir, filters
    start_signal = pyqtSignal(list, list, list, bool, int, str, dict)
    settings_signal = pyqtSignal()
    outreach_signal = pyqtSignal()

    FIELD_KEYS = [
        "name", "category", "rating", "review_count", "hours",
        "address", "website", "phone", "maps_link",
        "latitude", "longitude", "place_id",
        "email", "facebook", "instagram", "linkedin", "twitter", "youtube",
        "review_1", "review_2", "review_3",
    ]
    FIELD_NAMES = [
        "Business Name", "Category", "Rating", "Review Count", "Hours",
        "Address", "Website", "Phone Number", "Maps Link",
        "Latitude", "Longitude", "Place ID",
        "Email", "Facebook", "Instagram", "LinkedIn", "Twitter/X", "YouTube",
        "Review 1", "Review 2", "Review 3",
    ]

    # Fields that require opening each listing's page (Maps detail).
    DETAIL_FIELDS = {"hours", "review_1", "review_2", "review_3"}
    # Fields that require fetching each business website.
    ENRICH_FIELDS = {"email", "facebook", "instagram", "linkedin", "twitter", "youtube"}
    # The slow paths, and what the "Fast only" pick drops.
    SLOW_FIELDS = DETAIL_FIELDS | ENRICH_FIELDS
    # What a fresh install starts with. `email` is slow and is on anyway: it is
    # the one field Outreach cannot work without, and a default that leaves it
    # off is a default whose first scrape has to be run twice.
    DEFAULT_OFF_FIELDS = SLOW_FIELDS - {"email"}

    def __init__(self):
        super().__init__()
        self._extra_domains: list = []
        self._extra_areas: list = []
        self.settings = load_settings()
        self._build()
        self._apply_settings_to_ui()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build(self):
        t = components.active_theme()
        root = _rows(self, margin="5", spacing="4", t=t)

        blocks = [self._build_search(t), self._build_fields(t),
                  self._build_filters(t), self._build_output(t),
                  self._build_recent(t)]
        self.page = _Page(blocks, t)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        # The sheet rings a focused scroll area, and this one holds the whole
        # page: left as a tab stop it takes the keyboard before the first field
        # does and answers with a ring drawn round every control on the screen.
        area.setFocusPolicy(Qt.NoFocus)
        area.setWidget(self.page)
        root.addWidget(area, stretch=1)

        root.addWidget(components.divider())
        root.addLayout(self._build_footer(t))

    def _build_search(self, t) -> QWidget:
        block = _block(t, "Search")

        self.domain_list_btn = components.button(
            "List", kind="secondary", size="md", on_click=self._open_domain_list)
        self.domain_list_btn.setToolTip("Add more business types to this run")
        self.domain_field = _Field(
            t, "Domain", placeholder="e.g. car dealers",
            help_text="The kind of business to look for, as you would type it "
                      "into Google Maps.",
            trailing=self.domain_list_btn)
        self.domain_input = self.domain_field.edit
        block.body.addWidget(self.domain_field)

        self.area_list_btn = components.button(
            "List", kind="secondary", size="md", on_click=self._open_area_list)
        self.area_list_btn.setToolTip("Add more cities or areas to this run")
        self.area_field = _Field(
            t, "Area", placeholder="e.g. Lahore",
            help_text="Every domain is searched in every area, one run each.",
            trailing=self.area_list_btn)
        self.area_input = self.area_field.edit
        block.body.addWidget(self.area_field)

        limit = _cols(margin="0", spacing="2", t=t)
        limit.addWidget(components.section_label("Max results"))
        limit.addStretch()
        self.max_results_label = components.body_label("50", tone="secondary")
        self.max_results_label.setWordWrap(False)
        limit.addWidget(self.max_results_label)
        block.body.addLayout(limit)

        self.max_results_slider = QSlider(Qt.Horizontal)
        self.max_results_slider.setObjectName("limit_slider")
        self.max_results_slider.setMinimum(5)
        self.max_results_slider.setMaximum(100)
        self.max_results_slider.setValue(50)
        self.max_results_slider.setTickPosition(QSlider.TicksBelow)
        self.max_results_slider.setTickInterval(25)
        self.max_results_slider.valueChanged.connect(self._on_max_slider_changed)
        block.body.addWidget(self.max_results_slider)
        block.body.addWidget(components.hint(
            "How many businesses to keep per domain and area.",
            max_chars=MEASURE_CH))
        return block

    def _build_fields(self, t) -> QWidget:
        block = _block(t, "Data to scrape")

        picks = _cols(margin="0", spacing="2", t=t)
        for text, tip, slot in (
            ("All", "Every field, including the slow ones",
             self._select_all_fields),
            ("Default", "Everything fast, plus the email address",
             self._select_default_fields),
            ("Fast only", "Nothing that opens a listing or a website",
             self._select_fast_fields),
            ("None", "Clear every field", self._select_no_fields),
        ):
            pick = components.button(text, kind="ghost", size="sm",
                                     on_click=slot)
            pick.setToolTip(tip)
            picks.addWidget(pick)
        picks.addStretch()
        block.body.addLayout(picks)

        self.checkboxes = {}
        grid = QGridLayout()
        grid.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                t.space["0"])
        grid.setHorizontalSpacing(t.space["4"])
        grid.setVerticalSpacing(t.space["2"])
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, (key, name) in enumerate(zip(self.FIELD_KEYS,
                                                self.FIELD_NAMES)):
            if key == "email":
                tip = ("Fetches the business website — slower, and the one "
                       "field Outreach cannot send without")
            elif key in self.DETAIL_FIELDS:
                tip = "Opens each listing's page — slower"
            elif key in self.ENRICH_FIELDS:
                tip = "Fetches the business website — slower"
            else:
                tip = ""
            box = components.toggle(name, help=tip)
            box.setChecked(key not in self.DEFAULT_OFF_FIELDS)
            self.checkboxes[key] = box
            grid.addWidget(box, index // 2, index % 2)
        holder = QWidget()
        holder.setLayout(grid)
        block.body.addWidget(holder)

        block.body.addWidget(components.hint(
            "Email is on because Outreach needs an address to send to; it and "
            "the socials fetch each website, and Hours and Reviews open each "
            "listing. Both are slower.", max_chars=MEASURE_CH))
        self.fields_error = components.body_label("", tone="danger",
                                                  max_chars=MEASURE_CH)
        self.fields_error.hide()
        block.body.addWidget(self.fields_error)
        return block

    def _build_filters(self, t) -> QWidget:
        block = _block(t, "Only keep businesses that match")
        block.body.addWidget(components.hint(
            "Leave everything blank or zero to keep every result.",
            max_chars=MEASURE_CH))

        grid = self._form(t)
        self.min_rating_spin = self._spin(QDoubleSpinBox(), t)
        self.min_rating_spin.setRange(0.0, 5.0)
        self.min_rating_spin.setSingleStep(0.5)
        self.min_rating_spin.setDecimals(1)
        self.min_rating_spin.setSpecialValueText("Any")
        self.min_reviews_spin = self._spin(QSpinBox(), t)
        self.min_reviews_spin.setRange(0, 1000000)
        self.min_reviews_spin.setSingleStep(10)
        self.min_reviews_spin.setSpecialValueText("Any")
        self.max_reviews_spin = self._spin(QSpinBox(), t)
        self.max_reviews_spin.setRange(0, 1000000)
        self.max_reviews_spin.setSingleStep(10)
        self.max_reviews_spin.setSpecialValueText("No cap")
        self.website_combo = QComboBox()
        self.website_combo.setFixedHeight(t.control["md"])
        self.website_combo.addItems(["Any", "Has a website", "No website"])
        for row, (label, widget) in enumerate((
            ("Minimum rating", self.min_rating_spin),
            ("Minimum reviews", self.min_reviews_spin),
            ("Maximum reviews", self.max_reviews_spin),
            ("Website", self.website_combo),
        )):
            grid.addWidget(self._form_label(label), row, 0)
            grid.addWidget(widget, row, 1)
        block.body.addLayout(grid)
        block.body.addWidget(components.hint(
            "A review filter opens each listing to read the exact count, which "
            "is slower.", max_chars=MEASURE_CH))

        self.require_phone_cb = components.toggle(
            "Must have a phone number",
            help="Drops a listing Google Maps shows no number for")
        block.body.addWidget(self.require_phone_cb)
        self.require_email_cb = components.toggle(
            "Must have a discoverable email",
            help="Fetches the business website to find one, then drops the "
                 "listing if there is none")
        block.body.addWidget(self.require_email_cb)

        text_grid = self._form(t, fills=True)
        self.name_include_input = self._filter_edit(t, "any of these words")
        self.name_exclude_input = self._filter_edit(t, "none of these words")
        self.cat_include_input = self._filter_edit(t, "e.g. roofing, contractor")
        self.cat_exclude_input = self._filter_edit(t, "e.g. supplier")
        for row, (label, widget) in enumerate((
            ("Name includes", self.name_include_input),
            ("Name excludes", self.name_exclude_input),
            ("Category includes", self.cat_include_input),
            ("Category excludes", self.cat_exclude_input),
        )):
            text_grid.addWidget(self._form_label(label), row, 0)
            text_grid.addWidget(widget, row, 1)
        block.body.addLayout(text_grid)

        reset = _cols(margin="0", spacing="2", t=t)
        reset.addStretch()
        reset.addWidget(components.button("Reset filters", kind="ghost",
                                          size="sm",
                                          on_click=self._reset_filters))
        block.body.addLayout(reset)
        return block

    def _build_output(self, t) -> QWidget:
        block = _block(t, "Output and run")

        self.export_browse_btn = components.button(
            "…", kind="secondary", size="md", on_click=self._browse_export_dir)
        self.export_browse_btn.setToolTip("Choose the folder for CSV exports")
        self.export_field = _Field(
            t, "Export folder", placeholder="Choose a folder on your computer",
            help_text="Each finished search is written here as a CSV. The "
                      "folder is made on the first run if it is not there yet.",
            trailing=self.export_browse_btn, read_only=True)
        self.export_dir_input = self.export_field.edit
        block.body.addWidget(self.export_field)

        headless_help = ("Hides the Chrome window while scraping. Off, it "
                         "opens visibly so you can watch it work.")
        self.headless_cb = components.toggle("Run headless", help=headless_help)
        self.headless_cb.toggled.connect(self._persist_settings)
        block.body.addWidget(self.headless_cb)
        block.body.addWidget(components.hint(headless_help,
                                             max_chars=MEASURE_CH))

        cap = self._form(t)
        self.limit_cap_spin = self._spin(QSpinBox(), t)
        self.limit_cap_spin.setRange(25, 1000)
        self.limit_cap_spin.setSingleStep(25)
        self.limit_cap_spin.setValue(100)
        self.limit_cap_spin.valueChanged.connect(self._on_limit_cap_changed)
        cap.addWidget(self._form_label("Slider maximum"), 0, 0)
        cap.addWidget(self.limit_cap_spin, 0, 1)
        block.body.addLayout(cap)
        block.body.addWidget(components.hint(
            "Raise this to let the Max results slider go above 100.",
            max_chars=MEASURE_CH))

        block.body.addWidget(components.section_label("Outreach"))
        self.outreach_summary = components.body_label("", tone="secondary",
                                                      max_chars=MEASURE_CH)
        block.body.addWidget(self.outreach_summary)
        block.body.addWidget(components.hint(
            "Sender profile, AI provider, Gmail accounts, the sending window "
            "and compliance are all under Settings.", max_chars=MEASURE_CH))
        return block

    def _build_recent(self, t) -> QWidget:
        block = _block(t, "Recent searches")
        self.saved_stack = QStackedWidget()
        # A list expands by default, so on a tall window this block took every
        # spare pixel of its column and left the empty state's card floating
        # halfway down 700px of nothing.
        self.saved_stack.setSizePolicy(QSizePolicy.Preferred,
                                       QSizePolicy.Preferred)

        self.saved_list = QListWidget()
        self.saved_list.setObjectName("saved_list")
        self.saved_list.itemClicked.connect(self._load_saved_search)
        self.saved_stack.addWidget(self.saved_list)

        # Short on purpose: `empty_state` caps its own sentence at the 80
        # characters every paragraph in the app is capped at, and this page is
        # laid out in narrower columns than that, so the sentence that decides
        # how wide a column is has to be one this one fits inside.
        self.saved_empty = components.empty_state(
            title="Nothing saved yet",
            body="Each search you run is kept here for next time.")
        self.saved_stack.addWidget(self.saved_empty)
        block.body.addWidget(self.saved_stack)
        return block

    def _build_footer(self, t):
        row = _cols(margin="0", spacing="3", t=t)
        self.footer_note = components.body_label("", tone="tertiary",
                                                 max_chars=MEASURE_CH)
        self.footer_note.setWordWrap(False)
        row.addWidget(self.footer_note)
        row.addStretch()
        self.start_btn = components.button("Start Scraping", kind="primary",
                                           size="lg", on_click=self._on_start)
        row.addWidget(self.start_btn)
        return row

    # ── Form pieces ──────────────────────────────────────────────────────────

    @staticmethod
    def _form(t, *, fills: bool = False) -> QGridLayout:
        """A label column and a control column, and where the slack goes.

        A number and a three-item dropdown have a width of their own and gain
        nothing from more, so the slack goes to a third column and they keep it
        — this is the same defect as the 600px checkbox cell one control down.
        A field holding a list of words does use what it is given, so that grid
        hands the slack to the control instead.
        """
        grid = QGridLayout()
        grid.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                t.space["0"])
        grid.setHorizontalSpacing(t.space["3"])
        grid.setVerticalSpacing(t.space["2"])
        grid.setColumnStretch(1 if fills else 2, 1)
        return grid

    @staticmethod
    def _form_label(text: str):
        label = components.body_label(text, tone="secondary")
        label.setWordWrap(False)
        return label

    @staticmethod
    def _spin(spin, t):
        spin.setObjectName("spin")
        spin.setFixedHeight(t.control["md"])
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return spin

    @staticmethod
    def _filter_edit(t, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(t.control["md"])
        return edit

    # ── The theme, live ──────────────────────────────────────────────────────

    def restyle(self):
        """Wear the palette the app is in now.

        Every component resolves its colours in Python at build time and writes
        them into its own stylesheet, which beats the application sheet — so a
        repolish alone leaves this screen in the palette it was constructed in.
        What the user has typed is carried across; nothing else here is state.
        """
        state = self._state()
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
        self._build()
        self._apply_settings_to_ui()
        self._restore(state)

    def _state(self) -> dict:
        return {
            "domain": self.domain_input.text(),
            "area": self.area_input.text(),
            "extra_domains": list(self._extra_domains),
            "extra_areas": list(self._extra_areas),
            "max_results": self.max_results_slider.value(),
            "export_dir": self.export_dir_input.text(),
            "fields": self.get_checked_fields(),
            "filters": self.get_filters(),
            "headless": self.headless_cb.isChecked(),
        }

    def _restore(self, state: dict) -> None:
        self.domain_input.setText(state["domain"])
        self.area_input.setText(state["area"])
        self._extra_domains = list(state["extra_domains"])
        self._extra_areas = list(state["extra_areas"])
        self._update_domain_count_label()
        self._update_area_count_label()
        self.max_results_slider.setValue(
            min(state["max_results"], self.max_results_slider.maximum()))
        self.export_dir_input.setText(state["export_dir"])
        for key, box in self.checkboxes.items():
            box.setChecked(key in state["fields"])
        self.headless_cb.blockSignals(True)
        self.headless_cb.setChecked(state["headless"])
        self.headless_cb.blockSignals(False)
        self._apply_filters(state["filters"])

    # ── Field quick-select ───────────────────────────────────────────────────

    def _select_all_fields(self):
        for box in self.checkboxes.values():
            box.setChecked(True)
        self._clear_errors()

    def _select_default_fields(self):
        for key, box in self.checkboxes.items():
            box.setChecked(key not in self.DEFAULT_OFF_FIELDS)
        self._clear_errors()

    def _select_fast_fields(self):
        for key, box in self.checkboxes.items():
            box.setChecked(key not in self.SLOW_FIELDS)
        self._clear_errors()

    def _select_no_fields(self):
        for box in self.checkboxes.values():
            box.setChecked(False)

    def _reset_filters(self):
        self.min_rating_spin.setValue(0.0)
        self.min_reviews_spin.setValue(0)
        self.max_reviews_spin.setValue(0)
        self.website_combo.setCurrentIndex(0)
        self.require_phone_cb.setChecked(False)
        self.require_email_cb.setChecked(False)
        for edit in (self.name_include_input, self.name_exclude_input,
                     self.cat_include_input, self.cat_exclude_input):
            edit.clear()

    def _apply_filters(self, filters: dict) -> None:
        self.min_rating_spin.setValue(filters.get("min_rating", 0.0))
        self.min_reviews_spin.setValue(filters.get("min_reviews", 0))
        self.max_reviews_spin.setValue(filters.get("max_reviews", 0))
        index = 1 if filters.get("require_website") else 0
        self.website_combo.setCurrentIndex(
            2 if filters.get("require_no_website") else index)
        self.require_phone_cb.setChecked(bool(filters.get("require_phone")))
        self.require_email_cb.setChecked(bool(filters.get("require_email")))
        self.name_include_input.setText(filters.get("name_include", ""))
        self.name_exclude_input.setText(filters.get("name_exclude", ""))
        self.cat_include_input.setText(filters.get("category_include", ""))
        self.cat_exclude_input.setText(filters.get("category_exclude", ""))

    # ── Settings glue ────────────────────────────────────────────────────────

    def apply_settings(self, settings: dict):
        """Adopt a settings dict written elsewhere (the full settings screen).

        Both screens hold their own copy of the file. Without this the next
        `_persist_settings` here would write a copy loaded before the outreach
        settings existed and roll the whole lot back.
        """
        if not isinstance(settings, dict):
            return
        self.settings = settings
        self._apply_settings_to_ui()

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
        self.export_dir_input.setText(
            self.settings.get("export_dir") or _default_export_dir())
        self._refresh_saved_list()
        self._update_outreach_summary()

    def _update_outreach_summary(self):
        accounts = [a for a in (self.settings.get("smtp_accounts") or [])
                    if isinstance(a, dict) and a.get("enabled", True) and a.get("email")]
        provider = str(self.settings.get("ai_provider") or "auto")
        start = self.settings.get("send_start_hour", 9)
        end = self.settings.get("send_end_hour", 17)
        mode = ("Dry run — nothing is sent" if self.settings.get("dry_run", True)
                else "LIVE — real emails send")
        self.outreach_summary.setText(
            "%d sending account%s · AI %s · window %s:00–%s:00 · %s"
            % (len(accounts), "" if len(accounts) == 1 else "s", provider,
               start, end, mode))

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
            self.export_field.set_error("")
            self.settings["export_dir"] = path
            save_settings(self.settings)

    def export_dir(self) -> str:
        return self.export_dir_input.text().strip()

    # ── Recent searches ──────────────────────────────────────────────────────

    def _format_saved_search(self, entry: dict) -> str:
        domains = ", ".join(entry.get("domains") or [])
        area = entry.get("area", "")
        limit = entry.get("max_results", 50)
        return "%s · %s · max %s" % (domains, area, limit)

    def _refresh_saved_list(self):
        self.saved_list.clear()
        for entry in self.settings.get("saved_searches") or []:
            item = QListWidgetItem(self._format_saved_search(entry))
            item.setData(Qt.UserRole, entry)
            self.saved_list.addItem(item)
        self.saved_stack.setCurrentWidget(
            self.saved_list if self.saved_list.count() else self.saved_empty)

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
        self._extra_areas = list(entry.get("areas", []) or [])[1:] if entry.get("areas") else []
        self._update_area_count_label()
        limit = int(entry.get("max_results", 50))
        cap = self.max_results_slider.maximum()
        self.max_results_slider.setValue(min(limit, cap))

    # ── Domain / area lists ──────────────────────────────────────────────────

    def _open_domain_list(self):
        dialog = DomainListDialog(self._extra_domains, self)
        if dialog.exec_() == DomainListDialog.Accepted:
            self._extra_domains = dialog.domains()
            self._update_domain_count_label()

    def _open_area_list(self):
        dialog = ListDialog(
            self._extra_areas, self,
            title="Area List",
            hint="Enter one city/area per line. These run in addition to the main area.",
            placeholder="Toronto\nMississauga\nBrampton\nMarkham",
        )
        if dialog.exec_() == ListDialog.Accepted:
            self._extra_areas = dialog.items()
            self._update_area_count_label()

    def _update_domain_count_label(self):
        count = len(self._extra_domains)
        self.domain_field.set_note("+%d in list" % count if count else "")
        self.domain_list_btn.setText("List (%d)" % count if count else "List")

    def _update_area_count_label(self):
        count = len(self._extra_areas)
        self.area_field.set_note("+%d in list" % count if count else "")
        self.area_list_btn.setText("List (%d)" % count if count else "List")

    def _get_domains(self) -> list:
        seen, domains = set(), []
        for raw in [self.domain_input.text().strip(), *self._extra_domains]:
            value = raw.strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                domains.append(value)
        return domains

    def _get_areas(self) -> list:
        seen, areas = set(), []
        for raw in [self.area_input.text().strip(), *self._extra_areas]:
            value = raw.strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                areas.append(value)
        return areas

    # ── What the screen answers with ─────────────────────────────────────────

    def get_checked_fields(self) -> list:
        return [key for key, box in self.checkboxes.items() if box.isChecked()]

    def get_filters(self) -> dict:
        return {
            "min_rating": self.min_rating_spin.value(),
            "min_reviews": self.min_reviews_spin.value(),
            "max_reviews": self.max_reviews_spin.value(),
            "require_phone": self.require_phone_cb.isChecked(),
            "require_website": self.website_combo.currentIndex() == 1,
            "require_no_website": self.website_combo.currentIndex() == 2,
            "require_email": self.require_email_cb.isChecked(),
            "name_include": self.name_include_input.text(),
            "name_exclude": self.name_exclude_input.text(),
            "category_include": self.cat_include_input.text(),
            "category_exclude": self.cat_exclude_input.text(),
        }

    def is_headless(self) -> bool:
        return self.headless_cb.isChecked()

    def max_results(self) -> int:
        return self.max_results_slider.value()

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self):
        """The four rules this screen enforces, each said where it applies.

        The shake this replaces moved a control for 200ms and named neither the
        rule nor the field, which is why the export-folder rule — the only one
        of the four a user cannot guess — read as the button simply not working.
        """
        self._clear_errors()
        domains = self._get_domains()
        areas = self._get_areas()
        fields = self.get_checked_fields()
        export_dir = self.export_dir()

        if not domains:
            return self._refuse(
                self.domain_field,
                "Name the kind of business to look for — 'car dealers', "
                "'roofing contractors'.")
        if not areas:
            return self._refuse(
                self.area_field,
                "Name a city or area to search in — 'Lahore', 'Toronto'.")

        problem = _ensure_export_dir(export_dir)
        if problem:
            return self._refuse(self.export_field, problem)

        if not fields:
            self.fields_error.setText(
                "Tick at least one thing to collect. Default is a good start.")
            self.fields_error.show()
            self.footer_note.setText("Nothing is ticked, so there is nothing "
                                     "to collect.")
            return None

        return domains, areas, fields, export_dir

    def _refuse(self, field, message: str):
        field.set_error(message)
        field.edit.setFocus(Qt.OtherFocusReason)
        self.footer_note.setText("Fix the field marked below the box.")
        return None

    def _clear_errors(self):
        for field in (self.domain_field, self.area_field, self.export_field):
            field.set_error("")
        self.fields_error.hide()
        self.footer_note.setText("")

    # ── Start ────────────────────────────────────────────────────────────────

    def _on_start(self):
        validated = self.validate()
        if validated is None:
            return
        domains, areas, fields, export_dir = validated
        limit = self.max_results()
        primary_area = areas[0] if areas else ""
        self.settings = add_saved_search(self.settings, domains, primary_area, limit)
        self._refresh_saved_list()
        self._persist_settings()
        self.start_signal.emit(
            domains, areas, fields, self.is_headless(), limit, export_dir,
            self.get_filters(),
        )
