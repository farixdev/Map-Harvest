"""The widget vocabulary the screens have each been re-inventing on their own.

`screen_outreach.py` is 2591 lines and `screen_settings.py` is 2747, and both of
them open with the same forty lines: a `_section` label, a `_hint` label, a
`_card` frame, a `_button` that takes a pixel height, a password field with a
reveal toggle, a toast. Two copies means two behaviours, and the audit measured
what that costs — one button rendered at six different heights, nine radii, a
table whose every column was a flat 130px. None of it is fixable at the call
site, because the reason it happened is that there was nowhere shared to put a
widget. This module is that place, and `ui/theme.py` is where every value it
paints comes from.

Four pieces here each close a measured finding rather than a matter of taste.

`table()` takes a column **spec**, not a list of pixel widths. Today every
column is 130px with `stretchLastSection` on, so Email, Website and Address are
clipped in 20 of 20 rows while Review Count is handed 268px for an 18px number;
at 2560px the Status column reaches 1800px for the word "audited" while the
Headline gap column — the one carrying the entire value of the audit — stays
frozen at 240px with 16 of 20 cells elided and 11 of those with no tooltip. So a
column declares whether it is a fixed short value or a column that carries
meaning, every column has a floor and a **cap** measured in characters, the
stretch is shared by weight between the columns that say something, headers
align to their data, and any cell whose text does not fit answers with the whole
of it on hover.

`status_pill()` stops colour carrying meaning alone. Nine statuses currently map
onto seven colours with `bounced`, `failed` and `suppressed` at an identical
1.00:1, so the pill pairs colour with a label and with a shape — a fill kind and
a leading mark — and no two statuses share all three.

`empty_state`, `loading_state` and `error_state` exist as a set because a data
surface needs all three and the app ships one. A bordered box with a header row
and nothing in it tells a user nothing about which of the three they are
looking at.

`Toaster` replaces the single 6-second bottom-edge QLabel that is the entire
feedback channel of this application. It carries a tone, it can carry an action
so that suppressing a lead becomes undoable, and on the danger tone it does not
dismiss itself — a message that says something went wrong and then vanishes
before it is read is worse than no message.

Every value is read from the theme at build time. There is not a hex literal, a
font size, a spacing number or a fixed height in this file that did not come out
of `ui/theme.py`, and `tests/test_components.py` asserts that against the source
text rather than trusting the claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QEvent, QObject, QSize, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton, QToolTip,
    QVBoxLayout, QWidget,
)

from ui import theme as _theme

# The one length in the app that is not a spacing value and has no token: the
# width of a rule. `space.hair` is 2 and is documented for optical nudges, and a
# 1px border is neither a nudge nor a step on the 4px grid. Declared once here
# so every border and every divider in this file measures the same.
BORDER = 1

# Newest last, oldest evicted. Four stacked messages is a wall, and the fourth
# arriving is what pushes the first off the bottom of a 620px window unread.
MAX_TOASTS = 3

# How long a toast stays, by tone, in milliseconds. Not a motion token: motion
# values are how long a thing takes to move, and these are how long a sentence
# needs to be read. A danger toast never leaves on its own — 0 means it waits
# for the user — and any toast carrying an action waits longer, because an undo
# nobody had time to reach is a decoration.
DWELL = {"info": 6000, "success": 4500, "warning": 9000, "danger": 0}
ACTION_DWELL = 12000

# How many rows a content-sized column looks at before deciding how wide it is.
# Qt's own default is 1000, and a drag-resize of a 500-lead table asks for a
# delegate size hint on every one of them several times a second. A count, not a
# length — the theme has no token for it because it is not something anyone sees.
SIZE_SAMPLE_ROWS = 200

# Item data slots, the same two both screens already use: the untruncated cell
# text for search and tooltips, and the value the column actually sorts on.
FULL_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2


# ── The theme in force ───────────────────────────────────────────────────────
# `theme.apply()` writes the sheet onto the QApplication and keeps no record of
# which Theme it wrote, and nothing in `ui/theme.py` answers "which one is
# loaded". Components need the answer at build time — a pill fill and a control
# height are resolved in Python, not in the sheet — so this module keeps the
# reference. `ui/app.py` sets both in the same call at migration; until it does,
# the default is the same default `theme()` returns.

_CURRENT = None


def use_theme(t) -> None:
    """Name the theme every component built from now on reads its values from."""
    global _CURRENT
    _CURRENT = t


def active_theme():
    """The theme in force, defaulting to whatever `theme.theme()` defaults to."""
    return _CURRENT if _CURRENT is not None else _theme.theme()


# ── Measurement and local rules ──────────────────────────────────────────────


# A line of the app's own copy, measured to find out what one character of it
# actually costs. `QFontMetrics.averageCharWidth` is the obvious answer and it
# is wrong by 15%: it averages the font's glyph table, where every capital and
# every digit counts once, and running English is mostly lowercase and spaces.
# At 13px it reports 7.0px per character where this sentence measures 6.03, so
# a cap set from it comes out 94 characters wide when it was asked for 80.
_SAMPLE = ("Every address here is permanently excluded from planning and "
           "sending, follow-ups included. Remove one only if the person "
           "asked to be contacted again.")


def _measure(fm: QFontMetrics, chars: int) -> int:
    """The width `chars` characters of running text take in `fm`."""
    unit = fm.horizontalAdvance(_SAMPLE) / len(_SAMPLE)
    if unit <= 0:
        unit = fm.averageCharWidth() or fm.horizontalAdvance("x") or 1
    return int(unit * max(1, int(chars)))


def _px(value) -> str:
    return "%dpx" % int(value)


def _rule(selector: str, **decls) -> str:
    """One QSS rule, with every value interpolated and none of them written out.

    Widget-local sheets exist here for what the application sheet cannot say:
    it has no selector for a nine-state pill or a five-tone tile, and giving it
    one would mean a rule per state per tone. Qt gives a widget's own sheet
    priority over an ancestor's and does so regardless of specificity — see
    `_paint_all`, where that turns out to matter — so these override the
    application sheet for this widget and for nothing else.
    """
    body = " ".join("%s: %s;" % (name.replace("_", "-"), value)
                    for name, value in decls.items() if value is not None)
    return "%s { %s }" % (selector, body)


def _paint(widget: QWidget, selector: str, **decls) -> None:
    widget.setStyleSheet(_rule(selector, **decls))


def _paint_all(widget: QWidget, rules) -> None:
    """Several rules on one widget: a resting state, a hover, and a focus ring.

    The focus ring is why this exists, and it is measured, not assumed. Qt hands
    a widget's own stylesheet priority over an ancestor's *regardless of
    specificity* — an app sheet saying `QPushButton:focus { border: accent }`
    loses to a widget sheet saying `QPushButton { border: hairline }`, and a
    focused control painted here would silently have no ring at all. Anything in
    this file that takes focus and writes its own `border` writes its own
    `:focus` with it.
    """
    widget.setStyleSheet(" ".join(_rule(selector, **decls)
                                  for selector, decls in rules))


def _ring(t) -> dict:
    """The focus ring, at the one width every control keeps in every state.

    Never white and never 2px: the audit measured a white ring at 17.01:1
    against a selected tab at 1.50:1, and the contract's rule is that focus is
    subordinate to selection. Held at `BORDER` in every state so the contents
    rect never shifts when the ring arrives.
    """
    return {"border": "%s solid %s" % (_px(BORDER), t.color["accent.border"]),
            "outline": "none"}


_TEXT_TONES = ("primary", "secondary", "tertiary", "disabled", "onAccent")
FAMILIES = ("accent", "success", "warning", "danger", "info")


def _ink(t, tone: str) -> str:
    """The colour a tone name asks for: a text tier, or a family's own text."""
    if tone in _TEXT_TONES:
        return t.color["text.%s" % tone]
    if tone in FAMILIES:
        return t.color["%s.text" % tone]
    return t.color["text.secondary"]


def _grid(layout, t, *, margin: str = "0", spacing: str = "2"):
    """Every layout in this file states its margins and its spacing.

    The audit found 36 layouts silently on Qt's default 9px, which is off the
    grid in both directions and is why nothing in the app lines up with anything
    else. A layout that reaches this helper cannot inherit that default.
    """
    step = t.space[margin]
    layout.setContentsMargins(step, step, step, step)
    layout.setSpacing(t.space[spacing])
    return layout


def _vbox(owner=None, *, margin: str = "0", spacing: str = "2", t=None):
    t = t or active_theme()
    return _grid(QVBoxLayout(owner) if owner is not None else QVBoxLayout(),
                 t, margin=margin, spacing=spacing)


def _hbox(owner=None, *, margin: str = "0", spacing: str = "2", t=None):
    t = t or active_theme()
    return _grid(QHBoxLayout(owner) if owner is not None else QHBoxLayout(),
                 t, margin=margin, spacing=spacing)


# ── Text ─────────────────────────────────────────────────────────────────────


class _MeasuredLabel(QLabel):
    """A wrapped label that will not run past a maximum number of characters.

    The cap has to be re-taken when the font changes, and the font always
    changes: a widget is built before the sheet reaches it, so the width
    computed in the constructor is a width in whatever font Qt handed out at
    construction. Without the `changeEvent` half, every label in the app keeps a
    measure taken against the wrong metrics until something resizes it.
    """

    def __init__(self, text: str = "", chars: int = 80, parent=None):
        super().__init__(text, parent)
        self._chars = max(1, int(chars))
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._cap()

    def measure(self) -> int:
        return self._chars

    def sizeHint(self) -> QSize:
        """As wide as the sentence needs, and never wider than the measure.

        QLabel's own wrapped size hint aims at a squarish block, so a one-line
        message in a wide row comes back at a third of its natural width and
        wraps to two lines inside a frame that was sized for one — measured as a
        toast asking for 36px of text in 28px of label, with the second line cut
        off. Asking for what the text actually needs, capped, means a short
        message stays one line and a paragraph is still a paragraph.
        """
        natural = self.fontMetrics().horizontalAdvance(self.text())
        width = max(1, min(natural, self.maximumWidth()))
        return QSize(width, self.heightForWidth(width))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._cap()

    def _cap(self) -> None:
        self.setMaximumWidth(_measure(self.fontMetrics(), self._chars))
        self.updateGeometry()


def heading(text: str, level: str = "h2") -> QLabel:
    """A heading at one of the four heading tiers.

    The application sheet gives every QLabel the body size, and a sheet beats
    `setFont`, so a heading has to arrive as a rule of its own or it renders at
    13px like everything else — which is how the largest text in the app came to
    be 15px.
    """
    t = active_theme()
    if level not in ("display", "h1", "h2", "h3"):
        level = "h2"
    size, weight = t.font[level]
    label = QLabel(text)
    label.setProperty("role", "heading")
    label.setProperty("level", level)
    _paint(label, "QLabel", color=t.color["text.primary"],
           font_size=_px(size), font_weight=weight, background="transparent")
    return label


def section_label(text: str) -> QLabel:
    """The uppercase rule over a group of controls.

    Carries the +0.4px tracking the type scale specifies. Qt 5's stylesheet
    parser has no `letter-spacing`, which is why `theme.TRACKING` exists and why
    it is applied here through QFont instead of through the sheet.
    """
    t = active_theme()
    label = QLabel(str(text).upper())
    label.setObjectName("section_label")
    size, weight = t.font["caption"]
    _paint(label, "QLabel#section_label", color=t.color["text.tertiary"],
           font_size=_px(size), font_weight=weight, background="transparent")
    font = QFont(label.font())
    font.setLetterSpacing(QFont.AbsoluteSpacing, _theme.TRACKING["caption"])
    label.setFont(font)
    return label


def body_label(text: str, *, tone: str = "secondary", max_chars: int = 80) -> QLabel:
    """Wrapped body copy, capped at a readable measure.

    80 characters is the cap because 25 of the 29 wrapped labels in the app run
    past 90 characters per line and most of them sit at 207-212, which is three
    times the width the eye can track back from without losing the line. This is
    the only thing in the app that should wrap text.
    """
    t = active_theme()
    size, weight = t.font["body"]
    label = _MeasuredLabel(text, max_chars)
    label.setProperty("role", "body")
    label.setProperty("tone", tone)
    _paint(label, "QLabel", color=_ink(t, tone),
           font_size=_px(size), font_weight=weight, background="transparent")
    return label


def hint(text: str, *, max_chars: int = 80) -> QLabel:
    """The quiet line under a control that says what it costs."""
    t = active_theme()
    size, weight = t.font["small"]
    label = _MeasuredLabel(text, max_chars)
    label.setObjectName("hint")
    _paint(label, "QLabel#hint", color=t.color["text.tertiary"],
           font_size=_px(size), font_weight=weight, background="transparent")
    return label


# `stat_tile` and `text_field` take `hint` and `help` as keyword names the
# contract fixes, and both shadow something inside their own body. The aliases
# are how those two reach the real helpers.
_hint = hint


# ── Chrome ───────────────────────────────────────────────────────────────────


def divider(orientation=Qt.Horizontal) -> QFrame:
    """A hairline rule, in the one thickness the whole app draws borders at."""
    t = active_theme()
    line = QFrame()
    line.setObjectName("divider")
    if orientation == Qt.Vertical:
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(BORDER)
        line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        _paint(line, "QFrame#divider", background=t.color["border.subtle"],
               border="none", max_width=_px(BORDER))
    else:
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(BORDER)
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _paint(line, "QFrame#divider", background=t.color["border.subtle"],
               border="none", max_height=_px(BORDER))
    return line


def card(*, title: str = "", subtitle: str = "", body=None, actions=()) -> QFrame:
    """A titled panel. `body` is a widget or a layout; `actions` sit at the foot.

    `body_layout` is exposed on the returned frame because half the callers
    build their contents after the card rather than before it, and the
    alternative is each of them re-creating the same margins by hand — which is
    where the nine different card paddings in the audit came from.
    """
    t = active_theme()
    frame = QFrame()
    frame.setObjectName("card")
    _paint(frame, "QFrame#card", background_color=t.color["surface"],
           border="%s solid %s" % (_px(BORDER), t.color["border.default"]),
           border_radius=_px(t.radius["lg"]))

    box = _vbox(frame, margin="4", spacing="3", t=t)
    if title:
        box.addWidget(section_label(title))
    if subtitle:
        box.addWidget(body_label(subtitle, tone="secondary"))

    inner = _vbox(margin="0", spacing="2", t=t)
    box.addLayout(inner)
    if isinstance(body, QWidget):
        inner.addWidget(body)
    elif body is not None:
        inner.addLayout(body)

    if actions:
        row = _hbox(margin="0", spacing="2", t=t)
        row.addStretch()
        for action in actions:
            row.addWidget(_as_widget(action))
        box.addLayout(row)

    frame.body_layout = inner
    return frame


def _as_widget(spec) -> QWidget:
    """A widget, a `button()` kwargs dict, or a (text, callback) pair."""
    if isinstance(spec, QWidget):
        return spec
    if isinstance(spec, dict):
        return button(**spec)
    text, on_click = spec
    return button(text, on_click=on_click)


def screen_header(title: str, *, subtitle: str = "", actions=(), tabs=(),
                  on_tab=None) -> QWidget:
    """The one top bar, at the one height.

    The audit found four screens with four different top bars at 70px, 50px and
    none. This is `control.header` on every screen, and the tabs it carries are
    the sheet's own `QPushButton#tab` so the selection rail and the focus ring
    keep the order the contract sets between them.

    The returned widget exposes `tab_buttons`, `tab_group` and
    `select_tab(index)`. The group is a real exclusive QButtonGroup carrying
    `idClicked`, which is what both screens already connect to, so migrating one
    is a swap rather than a rewrite of its tab wiring.
    """
    t = active_theme()
    header = QWidget()
    header.setObjectName("results_header")
    header.setFixedHeight(t.control["header"])
    _paint(header, "QWidget#results_header", background_color="transparent",
           border_bottom="%s solid %s" % (_px(BORDER), t.color["border.subtle"]))

    box = _hbox(header, margin="0", spacing="2", t=t)
    box.setContentsMargins(t.space["5"], t.space["0"], t.space["5"], t.space["0"])

    name = heading(title, "h2")
    box.addWidget(name)
    if subtitle:
        sub = body_label(subtitle, tone="tertiary")
        sub.setWordWrap(False)
        box.addWidget(sub)

    buttons = []
    group = QButtonGroup(header)
    group.setExclusive(True)
    if tabs:
        box.addSpacing(t.space["4"])
        strip = _hbox(margin="0", spacing="1", t=t)
        for index, label in enumerate(tabs):
            tab = button(label, kind="tab", size="sm")
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            group.addButton(tab, index)
            strip.addWidget(tab)
            buttons.append(tab)
        box.addLayout(strip)
        if on_tab is not None:
            group.idClicked.connect(on_tab)

    box.addStretch()
    for action in actions:
        box.addWidget(_as_widget(action))

    header.tab_buttons = buttons
    header.tab_group = group
    header.select_tab = lambda index: _select_tab(buttons, index)
    return header


def _select_tab(buttons, index: int) -> None:
    """Move the selection without firing `on_tab`: the caller already knows."""
    for at, tab in enumerate(buttons):
        tab.setChecked(at == index)


# ── Controls ─────────────────────────────────────────────────────────────────

# Kind decides colour; the caller never does. One green in the app currently
# means "Save", "Start Scraping" and "mail 20 real strangers", and the whole
# point of naming the kind is that the last of those cannot wear the first
# one's clothes. `danger_primary` is the filled red, it is the only filled red,
# and the contract pairs it with `confirm()` every time.
#
# Every name here has a rule in `theme.stylesheet()`. `ghost` is the one kind
# the sheet has no rule for, so it carries no objectName at all: an objectName
# the sheet does not style is a promise nothing keeps, and it is what a screen
# reaches for next when it wants "the same look, one more place". The kind is on
# the widget as a property instead, which is what a caller or a test asks.
_KINDS = {
    "primary": "start_btn",
    "secondary": "outlined",
    "ghost": "",
    "danger": "danger",
    "danger_primary": "live",
    "tab": "tab",
    "rehearsal": "rehearsal",
}

_SIZES = ("xs", "sm", "md", "lg")


def button(text: str, *, kind: str = "secondary", size: str = "md", icon=None,
           on_click=None) -> QPushButton:
    """One button, one height per size token, colour chosen by kind.

    The height is set here rather than left to the sheet because the sheet can
    only state one height per selector, and `QPushButton#outlined` was rendering
    at 26, 28, 30, 32, 34 and 40px across the app precisely because each call
    site set its own. A caller that wants a different height picks a different
    size; there is no pixel argument to pass.
    """
    t = active_theme()
    if size not in _SIZES:
        size = "md"
    name = _KINDS.get(kind, _KINDS["secondary"])
    btn = QPushButton(text)
    btn.setProperty("kind", kind if kind in _KINDS else "secondary")
    btn.setProperty("size", size)
    if name:
        btn.setObjectName(name)
    btn.setFixedHeight(t.control[size])
    btn.setCursor(Qt.PointingHandCursor)
    if icon is not None:
        btn.setIcon(icon if isinstance(icon, QIcon) else QIcon(str(icon)))
    if kind == "ghost":
        _paint_all(btn, (
            ("QPushButton", {
                "background_color": "transparent",
                "color": t.color["text.secondary"],
                "border": "%s solid transparent" % _px(BORDER),
                "border_radius": _px(t.radius["md"]),
                "padding": "0 %s" % _px(t.space["3"]),
                "font_size": _px(t.font["body"][0]),
                "font_weight": t.font["bodyMed"][1]}),
            ("QPushButton:hover", {"color": t.color["text.primary"],
                                   "background_color": t.color["surfaceHover"]}),
            ("QPushButton:pressed", {"background_color": t.color["surfaceActive"],
                                     "color": t.color["text.onAccent"]}),
            ("QPushButton:disabled", {"color": t.color["text.disabled"]}),
            ("QPushButton:focus", _ring(t)),
        ))
    if on_click is not None:
        btn.clicked.connect(lambda _checked=False: on_click())
    return btn


def icon_button(icon, *, tooltip: str, size: str = "md") -> QToolButton:
    """A square button carrying a glyph or a QIcon, and always a tooltip.

    Always, because an icon with no name is a guess: the tooltip is also the
    accessible name, so a screen reader and a hovering mouse are told the same
    thing.
    """
    t = active_theme()
    if size not in _SIZES:
        size = "md"
    side = t.control[size]
    btn = QToolButton()
    btn.setProperty("role", "icon")
    if isinstance(icon, QIcon):
        btn.setIcon(icon)
    else:
        btn.setText(str(icon))
    btn.setFixedSize(side, side)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)
    btn.setAccessibleName(tooltip)
    # The application sheet has no QToolButton rule at all, so without the
    # `:focus` line here this control is the one thing in the app a keyboard can
    # reach and not see.
    _paint_all(btn, (
        ("QToolButton", {"background_color": "transparent",
                         "color": t.color["text.secondary"],
                         "border": "%s solid transparent" % _px(BORDER),
                         "border_radius": _px(t.radius["md"]),
                         "font_size": _px(t.font["body"][0])}),
        ("QToolButton:hover", {"color": t.color["text.primary"],
                               "background_color": t.color["surfaceHover"]}),
        ("QToolButton:pressed", {"background_color": t.color["surfaceActive"],
                                 "color": t.color["text.onAccent"]}),
        ("QToolButton:disabled", {"color": t.color["text.disabled"]}),
        ("QToolButton:focus", _ring(t)),
    ))
    return btn


class _Field(QWidget):
    """A labelled input with room reserved for a help line and an error line.

    Both lines are built even when empty and hidden rather than absent, because
    a form that grows a row of text when validation fails moves every control
    below it — and the control that just moved is the one the user was about to
    click.
    """

    def __init__(self, edit: QWidget, label: str, help_text: str, t, parent=None):
        super().__init__(parent)
        self.edit = edit
        box = _vbox(self, margin="0", spacing="1", t=t)
        self.label = None
        if label:
            self.label = section_label(label)
            box.addWidget(self.label)
        box.addWidget(edit)
        self.help = _hint(help_text)
        self.help.setVisible(bool(help_text))
        box.addWidget(self.help)
        self.error = body_label("", tone="danger")
        self.error.hide()
        box.addWidget(self.error)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:
        self.edit.setText(value or "")

    def set_error(self, message: str = "") -> None:
        self.error.setText(message or "")
        self.error.setVisible(bool(message))


class _SecretEdit(QWidget):
    """A password field with a reveal toggle, in one place instead of two.

    A live Gmail app password sitting visible in a box is both a shoulder-surf
    and the reason people paste screenshots of their credentials into support
    threads; a masked field with no way to check what was typed is how they get
    locked out instead. Both screens implemented this, differently.
    """

    def __init__(self, placeholder: str, t, parent=None):
        super().__init__(parent)
        box = _hbox(self, margin="0", spacing="2", t=t)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFixedHeight(t.control["md"])
        self.toggle = QPushButton("Show")
        self.toggle.setObjectName("reveal")
        self.toggle.setCheckable(True)
        self.toggle.setFixedHeight(t.control["md"])
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setToolTip("Show the password in clear text")
        self.toggle.toggled.connect(self._on_toggled)
        box.addWidget(self.edit, stretch=1)
        box.addWidget(self.toggle)

    def _on_toggled(self, shown: bool) -> None:
        self.edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.toggle.setText("Hide" if shown else "Show")

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value or "")
        self.toggle.setChecked(False)

    @property
    def editingFinished(self):
        return self.edit.editingFinished


def text_field(*, placeholder: str = "", label: str = "", help: str = "",
               error: str = "", secret: bool = False) -> QWidget:
    """A text input with its label, its help line and its error line."""
    t = active_theme()
    if secret:
        edit = _SecretEdit(placeholder, t)
    else:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(t.control["md"])
    field = _Field(edit, label, help, t)
    if error:
        field.set_error(error)
    return field


def select(items, *, label: str = "", help: str = "") -> QWidget:
    """A dropdown with its label and help line. Items are strings or (text, data)."""
    t = active_theme()
    combo = QComboBox()
    combo.setFixedHeight(t.control["md"])
    for item in items or ():
        if isinstance(item, (tuple, list)) and len(item) == 2:
            combo.addItem(str(item[0]), item[1])
        else:
            combo.addItem(str(item))
    field = _Field(combo, label, help, t)
    field.combo = combo
    field.text = combo.currentText
    field.setText = lambda value: combo.setCurrentText(str(value or ""))
    return field


def toggle(text: str, *, help: str = "") -> QCheckBox:
    """A checkbox, and the sentence saying what turning it off costs.

    The contract returns a QCheckBox and a checkbox cannot hold a second line,
    so the sentence is attached as `help_label` for the caller to place. It is
    also the tooltip, so the cost is on screen either way — the compliance
    switches are the case that matters, and "Off, the footer loses its opt-out
    line" is not something to leave to a caller to remember.
    """
    box = QCheckBox(text)
    box.setCursor(Qt.PointingHandCursor)
    box.help_label = None
    if help:
        box.setToolTip(help)
        box.help_label = _hint(help)
    return box


class _MeasuredEdit(QLineEdit):
    """A field capped at a number of characters, re-measured when the font lands."""

    def __init__(self, chars: int, parent=None):
        super().__init__(parent)
        self._chars = max(1, int(chars))
        self._cap()

    def measure(self) -> int:
        return self._chars

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._cap()

    def _cap(self) -> None:
        self.setMaximumWidth(_measure(self.fontMetrics(), self._chars))


def search_field(placeholder: str = "Search…", *, max_chars: int = 30) -> QLineEdit:
    """A filter box sized to the query, not to the window.

    Capped in characters rather than pixels for the same reason the table caps
    its columns: given a stretch, a search box on a 2560px screen becomes a
    2000px well holding the word "roof".
    """
    t = active_theme()
    edit = _MeasuredEdit(max_chars)
    edit.setObjectName("search_box")
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.setFixedHeight(t.control["sm"])
    return edit


class _Chip(QFrame):
    """A short value that can be clicked, and optionally taken off."""

    clicked = pyqtSignal()
    removed = pyqtSignal()

    def __init__(self, text: str, removable: bool, t, parent=None):
        super().__init__(parent)
        self.setProperty("role", "chip")
        self.setFixedHeight(t.control["xs"])
        _paint_all(self, (
            ("QFrame", {"background_color": t.color["surfaceHover"],
                        "color": t.color["text.primary"],
                        "border": "%s solid %s" % (_px(BORDER),
                                                   t.color["border.subtle"]),
                        "border_radius": _px(t.radius["pill"])}),
            ("QFrame:hover", {"border_color": t.color["border.strong"]}),
            ("QFrame:focus", _ring(t)),
        ))
        box = _hbox(self, margin="0", spacing="1", t=t)
        box.setContentsMargins(t.space["2"], t.space["0"], t.space["2"], t.space["0"])
        self.label = QLabel(str(text))
        size, weight = t.font["small"]
        _paint(self.label, "QLabel",
               color=t.color["text.primary"], font_size=_px(size),
               font_weight=weight, background="transparent")
        box.addWidget(self.label)
        self.remove_button = None
        if removable:
            self.remove_button = icon_button("×", tooltip="Remove %s" % text,
                                             size="xs")
            self.remove_button.clicked.connect(self.removed.emit)
            box.addWidget(self.remove_button)

    def text(self) -> str:
        return self.label.text()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


def chip(text: str, *, on_click=None, removable: bool = False) -> QWidget:
    """A pill-shaped short value: a merge field, a service, an active filter.

    A chip that does something takes the focus and answers Space and Return.
    The merge-field palette in `screen_settings` is the case that proves it:
    every chip there refuses focus so the whole palette, and every tooltip
    explaining what a merge field resolves to, is unreachable from a keyboard.
    """
    widget = _Chip(text, removable, active_theme())
    if on_click is not None:
        widget.clicked.connect(lambda: on_click())
        widget.setCursor(Qt.PointingHandCursor)
        widget.setFocusPolicy(Qt.StrongFocus)
    return widget


# ── Data: the table ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Column:
    """What a column is, instead of how many pixels it was given.

    `kind` is the whole of the first critical finding. A `fit` column holds a
    short fixed value — a score, a status, a rating — and is sized to its
    content and then capped, so Review Count stops taking 268px for an 18px
    number and Status stops reaching 1800px for the word "audited". A `stretch`
    column carries meaning and shares what is left over by `weight`, so the
    Headline gap column grows with the window instead of staying frozen at
    240px while the columns beside it grow.

    `min_ch` and `max_ch` are in characters because the thing being sized is
    text, and a cap in pixels means something different at every font size and
    every DPI. Every column has both: a column with no cap is the bug.

    Left at 0, `max_ch` is derived from `kind` rather than defaulted to one
    number, because the two kinds want opposite caps. A short fixed value has
    nothing to gain past its own longest string, so 24 characters is generous.
    A column carrying a sentence is prose, and prose stops being readable at the
    same 80 characters `body_label` caps a paragraph at — which is also why a
    2560px window ends up with space to the right of the last column rather than
    with a column stretched past the point anyone can read it.
    """

    title: str
    kind: str = "fit"
    weight: int = 1
    align: str = "left"
    min_ch: int = 6
    max_ch: int = 0
    sample: str = ""

    def cap(self) -> int:
        if self.max_ch > 0:
            return max(self.max_ch, self.min_ch)
        return max(80 if self.kind == "stretch" else 24, self.min_ch)


_ALIGN = {"left": Qt.AlignLeft, "right": Qt.AlignRight, "center": Qt.AlignHCenter}


@dataclass(frozen=True)
class Cell:
    """One value, and the three things a cell needs that its text cannot say."""

    text: str = ""
    sort: object = None
    tone: str = ""
    tip: str = ""


class _Item(QTableWidgetItem):
    """A cell that sorts on the value it was given rather than on its own text.

    The Score column shows an em dash for a lead nobody has audited, and an em
    dash compares greater than any digit as text — so sorting the column as
    written floats every unaudited lead above the best prospect. Any cell can
    carry an explicit sort key; without one, two numbers compare as numbers and
    anything else compares case-insensitively as text.
    """

    def __lt__(self, other):
        mine, theirs = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                return str(mine) < str(theirs)
        one, two = self._number(self.text()), self._number(other.text())
        if one is not None and two is not None:
            return one < two
        return self.text().lower() < other.text().lower()

    @staticmethod
    def _number(text):
        try:
            return float((text or "").replace(",", "").strip())
        except ValueError:
            return None


class _Table(QTableWidget):
    """A table that decides its own column widths from a spec, and keeps them.

    `stretchLastSection` is off and stays off: it is what handed the last column
    every spare pixel on a wide window regardless of whether that column had
    anything to say. Widths are recomputed whenever the viewport changes size,
    which is when the findings appear — the 2560px case is a resize.

    Every cell answers with its full text on hover when what is painted does not
    fit. That is checked against the column's current width rather than set once
    at insert, because a cell that fits at 1080px is elided at 880px and 11 of
    the 16 elided cells the audit counted had no tooltip at all.
    """

    def __init__(self, columns, sortable: bool, t, parent=None):
        super().__init__(0, len(columns), parent)
        self._columns = list(columns)
        self._t = t
        self._chrome = t.space["2"] * 2 + t.space["hair"]

        self.setObjectName("results_table")
        self.setHorizontalHeaderLabels([c.title for c in self._columns])
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setSortingEnabled(bool(sortable))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._laying_out = False

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(t.control["row"])

        head = self.horizontalHeader()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(QHeaderView.Interactive)
        head.setSortIndicatorShown(bool(sortable))
        head.setHighlightSections(False)
        head.setResizeContentsPrecision(SIZE_SAMPLE_ROWS)

        # A right-aligned number under a centred header is why a wide column's
        # header floats a hundred pixels from the values it labels.
        for index, column in enumerate(self._columns):
            item = self.horizontalHeaderItem(index)
            if item is not None:
                item.setTextAlignment(_ALIGN.get(column.align, Qt.AlignLeft)
                                      | Qt.AlignVCenter)

    # ── Widths ──

    def columns(self) -> list:
        return list(self._columns)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout()

    def relayout(self) -> None:
        """Re-take every column width from the spec and the space available.

        Guarded against itself: setting a column width can make a scrollbar
        appear, which resizes the viewport, which asks for this again.
        """
        total = self.viewport().width()
        if total <= 0 or not self._columns or self._laying_out:
            return
        self._laying_out = True
        try:
            self._take_widths(total)
        finally:
            self._laying_out = False

    def _take_widths(self, total: int) -> None:
        fm = self.fontMetrics()
        floors = [self._floor(c, fm) for c in self._columns]
        caps = [self._cap(c, fm) for c in self._columns]

        widths = []
        for index, column in enumerate(self._columns):
            if column.kind == "stretch":
                widths.append(floors[index])
            else:
                widths.append(min(max(self._natural(index, column, fm),
                                      floors[index]), caps[index]))

        spare = total - sum(widths)
        growing = [i for i, c in enumerate(self._columns) if c.kind == "stretch"]
        while spare > 0 and growing:
            weight = sum(max(1, self._columns[i].weight) for i in growing)
            handed = 0
            for index in list(growing):
                share = spare * max(1, self._columns[index].weight) // weight
                take = min(share, caps[index] - widths[index])
                if take <= 0:
                    growing.remove(index)
                    continue
                widths[index] += take
                handed += take
                if widths[index] >= caps[index]:
                    growing.remove(index)
            if handed <= 0:
                break
            spare -= handed

        floor = self.horizontalHeader().minimumSectionSize()
        for index, width in enumerate(widths):
            self.setColumnWidth(index, max(width, floor))

    def _floor(self, column: Column, fm: QFontMetrics) -> int:
        return _measure(fm, column.min_ch) + self._chrome

    def _cap(self, column: Column, fm: QFontMetrics) -> int:
        return max(_measure(fm, column.cap()) + self._chrome,
                   self._floor(column, fm))

    def _natural(self, index: int, column: Column, fm: QFontMetrics) -> int:
        want = fm.horizontalAdvance(column.title) + self._chrome
        if column.sample:
            want = max(want, fm.horizontalAdvance(column.sample) + self._chrome)
        if self.rowCount():
            want = max(want, self.sizeHintForColumn(index))
        return want

    # ── Rows ──

    def add_row(self, values, *, data=None) -> int:
        """Append a row. Values are strings or `Cell`s; `data` rides on column 0."""
        was_sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        row = self.rowCount()
        self.insertRow(row)
        for index, value in enumerate(list(values)[:len(self._columns)]):
            cell = value if isinstance(value, Cell) else Cell(text=str(value))
            item = _Item(cell.text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(
                _ALIGN.get(self._columns[index].align, Qt.AlignLeft)
                | Qt.AlignVCenter)
            item.setData(FULL_ROLE, cell.text)
            if cell.sort is not None:
                item.setData(SORT_ROLE, cell.sort)
            if cell.tip:
                item.setToolTip(cell.tip)
            if cell.tone:
                item.setForeground(_qcolor(self._t, cell.tone))
            self.setItem(row, index, item)
        if data is not None and self.item(row, 0) is not None:
            self.item(row, 0).setData(Qt.UserRole, data)
        self.setSortingEnabled(was_sorting)
        return row

    # ── Tooltips ──

    def tooltip_at(self, row: int, column: int) -> str:
        """What hovering this cell should say: its own tip, or what was cut off."""
        item = self.item(row, column)
        if item is None:
            return ""
        own = item.toolTip()
        if own:
            return own
        full = item.data(FULL_ROLE)
        full = item.text() if full is None else str(full)
        return full if self.is_elided(row, column) else ""

    def is_elided(self, row: int, column: int) -> bool:
        item = self.item(row, column)
        if item is None:
            return False
        full = item.data(FULL_ROLE)
        full = item.text() if full is None else str(full)
        room = self.columnWidth(column) - self._chrome
        return self.fontMetrics().horizontalAdvance(full) > room

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                tip = self.tooltip_at(index.row(), index.column())
                if tip:
                    QToolTip.showText(event.globalPos(), tip, self.viewport())
                else:
                    QToolTip.hideText()
                return True
        return super().viewportEvent(event)


def _qcolor(t, tone: str) -> QColor:
    return QColor(_ink(t, tone))


def table(columns, *, density: str = "comfortable", sortable: bool = True) -> QTableWidget:
    """Every table in the app. `columns` are `Column`s, or plain titles.

    A plain title is read as a fit column, which is the safe default: a column
    nobody has thought about should be sized to what it holds and capped, not
    handed the window.
    """
    base = active_theme()
    t = _theme.theme(base.name, density)
    spec = [c if isinstance(c, Column) else Column(str(c)) for c in columns]
    return _Table(spec, sortable, t)


# ── Data: pills, badges, tiles ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Pill:
    """One status: a colour family, a fill kind, a mark and a border style.

    Four carriers, so that losing any one of them still leaves the state
    readable. `bounced`, `failed` and `suppressed` are the same hex today and
    measure 1.00:1 against each other, which means a monochrome print, a
    colour-blind reader and a screenshot at 50% all read three different
    outcomes as one.
    """

    family: str
    fill: str
    mark: str = ""
    edge: str = "solid"


# Marks are all from Segoe UI Symbol's coverage, which every supported Windows
# build ships, and each one is a different glyph from every other.
STATUS_PILLS = {
    "new":        _Pill("neutral", "outline", "", "dashed"),
    "audited":    _Pill("info", "outline", "", "solid"),
    "queued":     _Pill("info", "subtle", "", "solid"),
    "sending":    _Pill("info", "subtle", "›", "solid"),
    "rehearsed":  _Pill("warning", "subtle", "", "dashed"),
    "sent":       _Pill("accent", "subtle", "", "solid"),
    "replied":    _Pill("accent", "subtle", "✓", "solid"),
    "bounced":    _Pill("danger", "solid", "×", "solid"),
    "failed":     _Pill("danger", "solid", "!", "solid"),
    "skipped":    _Pill("neutral", "outline", "–", "solid"),
    "suppressed": _Pill("danger", "outline", "⊘", "solid"),
}

_UNKNOWN = _Pill("neutral", "outline", "?", "dashed")


def _edge(t, family: str) -> str:
    """The outline of a small tinted badge, and why it is not `*.border`.

    A badge is a UI component, so its boundary has to clear 3:1 against the card
    it sits on. `accent.subtle` measures 1.02:1 on `surface` in dark — the fill
    is invisible — and `accent.border` only lifts that to 2.60:1, still short.
    The family's own `*.text` is the same hue and measures 7.79:1 there, so the
    outline both carries the family colour and is actually a boundary.
    """
    if family == "neutral":
        return t.color["border.default"]
    return t.color["%s.text" % family]


def _pill_colours(t, spec: _Pill) -> tuple:
    """(ground, ink, edge) for a fill kind, all three from the palette."""
    edge = _edge(t, spec.family)
    if spec.family == "neutral":
        ground = "transparent" if spec.fill == "outline" else t.color["surface"]
        return ground, t.color["text.secondary"], edge
    if spec.fill == "solid":
        return t.color["%s.default" % spec.family], t.color["text.onAccent"], edge
    if spec.fill == "subtle":
        return (t.color["%s.subtle" % spec.family],
                t.color["%s.text" % spec.family], edge)
    return "transparent", t.color["%s.text" % spec.family], edge


def status_pill(status: str) -> QWidget:
    """A status as colour, label and shape together, never as colour alone."""
    t = active_theme()
    key = str(status or "").strip().lower()
    spec = STATUS_PILLS.get(key, _UNKNOWN)
    ground, ink, edge = _pill_colours(t, spec)

    label = QLabel(("%s %s" % (spec.mark, key or "unknown")).strip())
    label.setProperty("role", "pill")
    label.setProperty("status", key)
    label.setProperty("fill", spec.fill)
    label.setProperty("mark", spec.mark)
    label.setAlignment(Qt.AlignCenter)
    label.setFixedHeight(t.control["xs"])
    label.setToolTip(key or "unknown")
    label.setAccessibleName("status %s" % (key or "unknown"))
    size, weight = t.font["caption"]
    _paint(label, "QLabel", background_color=ground, color=ink,
           border="%s %s %s" % (_px(BORDER), spec.edge, edge),
           border_radius=_px(t.radius["pill"]),
           padding="0 %s" % _px(t.space["2"]),
           font_size=_px(size), font_weight=weight)
    return label


# Highest band first. A high score means many automatable gaps, so the strong
# prospect is the accent and the thin one is the warning — and the band is
# spelled out beside the number, so nothing is carried by the colour alone.
SCORE_BANDS = ((70, "accent", "strong"), (45, "info", "moderate"),
               (1, "warning", "thin"))
SCORE_NONE = ("tertiary", "not audited")


def score_band(value) -> tuple:
    """(tone, label) for an opportunity score, and the not-audited case for 0."""
    try:
        score = int(value)
    except (TypeError, ValueError):
        return SCORE_NONE
    for floor, tone, label in SCORE_BANDS:
        if score >= floor:
            return tone, label
    return SCORE_NONE


def score_badge(value) -> QWidget:
    """The opportunity score as a number, a band word and a colour, in that order."""
    t = active_theme()
    tone, band = score_band(value)
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 0
    shown = "%d" % score if score > 0 else "—"

    label = QLabel("%s · %s" % (shown, band))
    label.setProperty("role", "score")
    label.setProperty("band", band)
    label.setAlignment(Qt.AlignCenter)
    label.setFixedHeight(t.control["xs"])
    label.setToolTip("Opportunity %s of 100 — %s prospect"
                     % (shown, band) if score > 0 else "Not audited yet")
    label.setAccessibleName("opportunity %s, %s" % (shown, band))
    size, weight = t.font["caption"]
    ink = _ink(t, tone)
    ground = t.color["%s.subtle" % tone] if tone in FAMILIES else "transparent"
    edge = _edge(t, tone if tone in FAMILIES else "neutral")
    _paint(label, "QLabel", background_color=ground, color=ink,
           border="%s solid %s" % (_px(BORDER), edge),
           border_radius=_px(t.radius["sm"]),
           padding="0 %s" % _px(t.space["2"]),
           font_size=_px(size), font_weight=weight)
    return label


def stat_tile(label: str, value, *, tone: str = "neutral", hint: str = "") -> QFrame:
    """One number, what it counts, and optionally what it means."""
    t = active_theme()
    frame = QFrame()
    frame.setObjectName("card")
    _paint(frame, "QFrame#card", background_color=t.color["surface"],
           border="%s solid %s" % (_px(BORDER), t.color["border.default"]),
           border_radius=_px(t.radius["lg"]))
    box = _vbox(frame, margin="3", spacing="1", t=t)

    ink = t.color["text.primary"] if tone == "neutral" else _ink(t, tone)
    size, weight = t.font["h1"]
    number = QLabel(str(value))
    number.setProperty("role", "stat_value")
    number.setProperty("tone", tone)
    _paint(number, "QLabel", color=ink, font_size=_px(size),
           font_weight=weight, background="transparent")
    box.addWidget(number)

    caption = QLabel(str(label))
    caption.setProperty("role", "stat_label")
    small, small_weight = t.font["small"]
    _paint(caption, "QLabel", color=t.color["text.secondary"],
           font_size=_px(small), font_weight=small_weight, background="transparent")
    box.addWidget(caption)

    frame.value_label = number
    frame.caption_label = caption
    frame.hint_label = None
    if hint:
        note = _hint(hint)
        box.addWidget(note)
        frame.hint_label = note
    return frame


# ── The three states every data surface needs ────────────────────────────────
# One helper builds all three, because the difference between "there is nothing
# here yet", "it has not arrived" and "it did not work" is the sentence and the
# affordance, not the layout. A screen that shows a bare bordered box for all
# three has told the user which of them it is: none.


def _state(kind: str, title: str, body: str, action, on_action, t) -> QWidget:
    page = QWidget()
    page.setProperty("role", "state")
    page.setProperty("state", kind)
    outer = _vbox(page, margin="0", spacing="2", t=t)
    outer.addStretch()

    # No width of its own: the sentence inside is a `body_label` and carries the
    # 80-character cap, so the panel is exactly as wide as readable copy and no
    # wider, at any window size and any font.
    panel = card()
    panel.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    inner = panel.body_layout
    inner.setSpacing(t.space["3"])

    # The three differ in more than a property name: an error reads as an error
    # before it is read at all, which is the difference between a user retrying
    # and a user waiting for something that already failed.
    head = heading(title, "h2")
    head.setAlignment(Qt.AlignCenter)
    if kind == "error":
        size, weight = t.font["h2"]
        _paint(head, "QLabel", color=t.color["danger.text"],
               font_size=_px(size), font_weight=weight, background="transparent")
    inner.addWidget(head)

    sentence = body_label(body, tone="secondary")
    sentence.setAlignment(Qt.AlignCenter)
    inner.addWidget(sentence)

    page.title_label = head
    page.body_label = sentence
    page.action_button = None
    page.progress = None

    if kind == "loading":
        bar = QProgressBar()
        bar.setProperty("role", "state_progress")
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(t.space["hair"])
        inner.addWidget(bar)
        page.progress = bar

    if action:
        row = _hbox(margin="0", spacing="2", t=t)
        row.addStretch()
        kind_name = "danger" if kind == "error" else "primary"
        btn = button(action, kind=kind_name, size="md", on_click=on_action)
        row.addWidget(btn)
        row.addStretch()
        inner.addLayout(row)
        page.action_button = btn

    holder = _hbox(margin="0", spacing="0", t=t)
    holder.addStretch()
    holder.addWidget(panel)
    holder.addStretch()
    outer.addLayout(holder)
    outer.addStretch()
    page.panel = panel
    return page


def empty_state(*, title: str, body: str, action=None, on_action=None) -> QWidget:
    """Nothing here yet, and the sentence naming what would put something here."""
    return _state("empty", title, body, action, on_action, active_theme())


def loading_state(*, label: str = "") -> QWidget:
    """Work in progress. Indeterminate, because none of these jobs know their end."""
    t = active_theme()
    return _state("loading", label or "Working…",
                  "This runs in the background. The screen stays usable while it does.",
                  None, None, t)


def error_state(*, title: str, body: str, retry=None) -> QWidget:
    """It did not work, what went wrong, and the way to try it again."""
    t = active_theme()
    if callable(retry):
        return _state("error", title, body, "Try again", retry, t)
    if isinstance(retry, (tuple, list)) and len(retry) == 2:
        return _state("error", title, body, str(retry[0]), retry[1], t)
    return _state("error", title, body, None, None, t)


# ── Feedback: toasts ─────────────────────────────────────────────────────────


class _Toast(QFrame):
    """One message. Carries its own dismissal, and its own timer or none."""

    closed = pyqtSignal(object)

    def __init__(self, text: str, tone: str, action, on_action, timeout, t,
                 parent=None):
        super().__init__(parent)
        self.tone = tone
        self.setObjectName("toast")
        self.setProperty("tone", tone)
        # Hugs its message rather than spanning the window. The label inside
        # carries the same 80-character measure every paragraph in the app does,
        # so a stretched toast would either centre the sentence in a 2560px bar
        # — the first word landing somewhere different for every message — or
        # wrap it to two lines inside a frame sized for one. Hugging makes both
        # impossible: the bar is as wide as the sentence and no wider.
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        family = tone if tone in FAMILIES else "info"
        _paint(self, "QFrame#toast",
               background_color=t.color["%s.subtle" % family],
               border="%s solid %s" % (_px(BORDER), _edge(t, family)),
               border_radius=_px(t.radius["md"]))

        box = _hbox(self, margin="3", spacing="3", t=t)
        self.text_label = body_label(text, tone=family)
        self.text_label.setWordWrap(True)
        box.addWidget(self.text_label, stretch=1)

        self.action_button = None
        if action:
            self.action_button = button(str(action), kind="ghost", size="sm")
            self.action_button.clicked.connect(self._on_action(on_action))
            box.addWidget(self.action_button)

        self.close_button = icon_button("×", tooltip="Dismiss", size="xs")
        self.close_button.clicked.connect(self.dismiss)
        box.addWidget(self.close_button)

        self.timer = None
        dwell = self._dwell(tone, action, timeout)
        if dwell > 0:
            self.timer = QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.dismiss)
            self.timer.start(dwell)

    @staticmethod
    def _dwell(tone: str, action, timeout) -> int:
        if timeout is not None:
            return max(0, int(timeout))
        if tone == "danger":
            return 0
        base = DWELL.get(tone, DWELL["info"])
        return max(base, ACTION_DWELL) if action else base

    def _on_action(self, callback):
        def fire(_checked=False):
            if callback is not None:
                callback()
            self.dismiss()
        return fire

    def dismiss(self) -> None:
        if self.timer is not None:
            self.timer.stop()
        self.closed.emit(self)


class Toaster(QObject):
    """The app's feedback channel, in place of one 6-second label at the bottom.

    What the label could not do is the reason this exists. It could not say
    whether a message was good news or a failure, so a send that bounced and a
    campaign that was prepared read identically. It could not carry an action,
    so suppressing a lead — permanent, and one right-click away — had no undo.
    And it dismissed itself after six seconds whatever it said, which is how an
    error about a Gmail password disappears while the user is still reading the
    first line.

    `widget` is what the screen puts in its layout, and it stays hidden while
    there is nothing to say so it costs no vertical space on a 620px window.
    """

    def __init__(self, host: QWidget = None, *, parent=None):
        super().__init__(parent if parent is not None else host)
        t = active_theme()
        self._t = t
        self.widget = QWidget(host)
        self.widget.setProperty("role", "toaster")
        self._box = _vbox(self.widget, margin="0", spacing="2", t=t)
        self.widget.hide()
        self._live = []

    def show(self, text: str, *, tone: str = "info", action=None, on_action=None,
             timeout=None) -> QWidget:
        """Say something. `danger` waits for the user; an action makes it an undo."""
        if tone not in FAMILIES:
            tone = "info"
        toast = _Toast(text, tone, action, on_action, timeout, self._t,
                       self.widget)
        toast.closed.connect(self._remove)
        self._box.addWidget(toast, alignment=Qt.AlignLeft)
        self._live.append(toast)
        while len(self._live) > MAX_TOASTS:
            self._live[0].dismiss()
        self.widget.show()
        return toast

    def clear(self) -> None:
        for toast in list(self._live):
            toast.dismiss()

    def toasts(self) -> list:
        return list(self._live)

    def _remove(self, toast) -> None:
        if toast in self._live:
            self._live.remove(toast)
        self._box.removeWidget(toast)
        toast.setParent(None)
        toast.deleteLater()
        if not self._live:
            self.widget.hide()


# ── Feedback: confirmation ───────────────────────────────────────────────────
# The remembered answers live in this process and not in `core.settings`.
# `_merge` there keeps only keys present in `DEFAULT_SETTINGS` and drops
# everything else on the next save, so a confirmation key written to that file
# is silently gone the first time the user presses Save on the settings screen.
# Adding the key is a schema change to `core`, and this is a styling library.
# `set_remember_store` is the seam for whichever commit makes that change.

_REMEMBERED = {}
_LOAD_REMEMBERED = None
_SAVE_REMEMBERED = None


def set_remember_store(load=None, save=None) -> None:
    """Point `confirm()`'s "do not ask again" at something that outlives the run."""
    global _LOAD_REMEMBERED, _SAVE_REMEMBERED
    _LOAD_REMEMBERED, _SAVE_REMEMBERED = load, save


def remembered(key: str) -> bool:
    if not key:
        return False
    if _LOAD_REMEMBERED is not None:
        try:
            return bool(_LOAD_REMEMBERED(key))
        except Exception:
            return False
    return bool(_REMEMBERED.get(key))


def remember(key: str, value: bool = True) -> None:
    if not key:
        return
    _REMEMBERED[key] = bool(value)
    if _SAVE_REMEMBERED is not None:
        try:
            _SAVE_REMEMBERED(key, bool(value))
        except Exception:
            pass


def forget(key: str = "") -> None:
    if key:
        _REMEMBERED.pop(key, None)
    else:
        _REMEMBERED.clear()


def _confirm_box(parent, title: str, body: str, confirm_text: str, danger: bool,
                 remember_key: str):
    """The dialog, built but not shown, so its shape can be tested and reused."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(body)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    role = QMessageBox.DestructiveRole if danger else QMessageBox.AcceptRole
    go = box.addButton(confirm_text, role)
    # The default is the way out, never the irreversible one: Return on a dialog
    # the user did not read must not be what deletes the template.
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    checkbox = None
    if remember_key:
        checkbox = QCheckBox("Do not ask again")
        box.setCheckBox(checkbox)
    return box, go, checkbox


def confirm(parent, *, title: str, body: str, confirm_text: str,
            danger: bool = False, remember_key: str = "") -> bool:
    """Ask before something irreversible, and offer to stop asking.

    Suppressing a lead, deleting a template and removing a Gmail account are all
    permanent, silent and unconfirmed today. A key that has been remembered
    returns True without a dialog, which is the whole value of the checkbox: a
    user who has overridden the same warning twice has said what they want, and
    hunting for the setting that turns it off is a worse afternoon than the
    warning ever saved them.
    """
    if remember_key and remembered(remember_key):
        return True
    box, go, checkbox = _confirm_box(parent, title, body, confirm_text, danger,
                                     remember_key)
    box.exec_()
    agreed = box.clickedButton() is go
    if agreed and checkbox is not None and checkbox.isChecked():
        remember(remember_key, True)
    return agreed
