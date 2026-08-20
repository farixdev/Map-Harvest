"""Every value the interface paints, and the one place that turns them into QSS.

The audit that produced `docs/DESIGN_SYSTEM.md` measured 30 colours in the sheet
and 26 more as Python literals, 18 pairs of them below the just-noticeable
difference; five type sizes inside an 11-15px band; nine radii; and a focus ring
at 17.01:1 sitting beside a selected tab at 1.50:1. None of that is fixable one
call site at a time, because the reason it happened is that there was nowhere
else to put a colour. This module is that place.

Two things live here and nothing else does. The **tokens** — two full palettes,
one type scale, one spacing grid, three radii, one control height per size per
density — and the **generator** that renders them as the application stylesheet.
`stylesheet()` is the only producer of QSS in the app; a hex literal, a font
size, a spacing number or a radius written anywhere else under `ui/` is a defect.

Both palettes are built to five constraints and every one of them is asserted in
`tests/test_theme.py` against numbers computed from the tokens themselves, so a
future edit that drops a ratio fails the suite instead of shipping:

  1. every `text.*` clears 4.5:1 on canvas, surface, surfaceHover, raised and
     inset — `text.disabled` is exempt at 3:1, as WCAG exempts inactive controls
  2. `text.onAccent` clears 4.5:1 on every filled state of every accent family
  3. canvas -> surface -> raised each step at least 1.4:1
  4. no two tokens within CIE76 delta-E 2.0 unless one is a documented
     hover/active step of the other
  5. `border.default` clears 3:1 on every surface it sits on or separates from

and to two more the audit's critical finding demands: the selected ground clears
3:1 against canvas, surface and inset and carries readable ink, and the focus
ring never measures louder than the selection it sits beside.

Where a rule could not be met, the reason is arithmetic and is written down at
the token that gives way — see `_DARK`'s surfaceActive and accent.border.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from string import Template
from typing import Mapping

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPalette, QPen, QPolygonF,
)
from PyQt5.QtWidgets import (
    QApplication, QProxyStyle, QStyle, QStyleFactory,
)

# ── Colour ───────────────────────────────────────────────────────────────────
# Names are roles. `text.secondary`, never `grey`; a screen that wants a colour
# names what the colour is for and gets whichever theme is loaded.
#
# The dark palette is laid out along relative luminance, because every rule in
# the contract is a luminance ratio:
#
#   canvas 0.0040  inset 0.0115  surface 0.0269  surfaceHover 0.0418
#   raised 0.0639  surfaceActive 0.1825   border.default 0.3012
#   text.disabled 0.3579  text.tertiary 0.5032  text.primary 0.9284
#
# canvas -> surface is 1.42:1 and surface -> raised 1.48:1, so a card reads as a
# card and a dialog sits above the page. `raised` is the lightest ground any
# text lands on, which is what fixes the floor for the whole text ramp: 4.5:1 on
# it needs L >= 0.4585, and that is why `text.tertiary` is as bright as it is.

_DARK = {
    "canvas": "#0C0D10",
    "surface": "#2A2E33",
    "surfaceHover": "#363A40",
    # The selected ground, and the tightest value in the palette. White ink caps
    # it at L 0.1833 (4.5:1) and 3:1 against `surface` floors it at L 0.1807;
    # #707782 measures L 0.1825, which is 4.52:1 for the ink and 3.03:1 for the
    # ground. There is no room above or below, which is the price of a selection
    # that is both legible and unmistakable on a dark page.
    "surfaceActive": "#707782",
    "raised": "#43484F",
    "inset": "#1A1C20",
    "scrim": "rgba(0, 0, 0, 0.66)",

    "border.subtle": "#4B4F57",
    "border.default": "#8D96A3",
    "border.strong": "#A5AEBE",

    "text.primary": "#F5F7FA",
    "text.secondary": "#C9D4E8",
    "text.tertiary": "#B2BDCE",
    "text.disabled": "#99A2B1",
    "text.onAccent": "#FFFFFF",

    # The brand green, and the app icon's own hue. Every filled state is dark
    # enough for white ink at 4.5:1, which is what "the accent fill must darken
    # until it passes" costs: the old #22A559 with white measured 3.18:1.
    "accent.subtle": "#053418",
    "accent.default": "#15743D",
    "accent.hover": "#1A8547",
    "accent.active": "#116534",
    # The focus ring. 3.70:1 on canvas and 3.24:1 on inset, against a selection
    # that measures 4.31:1 and 3.78:1 on the same two grounds — subordinate
    # everywhere, which is the rule. It cannot also clear 3:1 on `surface`
    # (it measures 2.60:1 there): doing so would need a ring above 4.31:1 on
    # canvas, and the selection cannot go higher without dropping its ink under
    # 4.5:1. Ordering wins, because the audit's critical finding is the ordering.
    "accent.border": "#177C42",
    "accent.text": "#5CD98F",

    # Teal, not a second green. `accent` is the brand and `success` is a state,
    # and two ramps of six values each in one hue is how a palette ends up with
    # 18 pairs under the just-noticeable difference.
    "success.subtle": "#01352E",
    "success.default": "#067165",
    "success.hover": "#098274",
    "success.active": "#056357",
    "success.border": "#077A6D",
    "success.text": "#45DFCB",

    "warning.subtle": "#422D02",
    "warning.default": "#865E0A",
    "warning.hover": "#996C0D",
    "warning.active": "#745007",
    "warning.border": "#90650B",
    "warning.text": "#F5C86A",

    "danger.subtle": "#62171A",
    "danger.default": "#B83439",
    "danger.hover": "#D33D42",
    "danger.active": "#A02C30",
    "danger.border": "#C7383E",
    "danger.text": "#FFA3A8",

    "info.subtle": "#0D3760",
    "info.default": "#2067AD",
    "info.hover": "#2677C6",
    "info.active": "#1A5996",
    "info.border": "#236FB9",
    "info.text": "#86C2FF",
}

# Built to the same five rules from the light end, not by inverting the dark one
# — an inversion of a palette whose lightest ground is L 0.06 lands nowhere near
# a usable light theme.
#
# The page is a mid grey and that is arithmetic, not taste. `raised` has to stay
# clear of pure white so `text.onAccent` is not the same token twice over, which
# puts it at L 0.9035; `surface` is then capped at L 0.6150 by the 1.4:1 step
# below it, and `canvas` at L 0.4090 by the step below that. A #F7F8FA page with
# white cards measures 1.08:1 and fails rule 3 outright.
#
# One consequence runs through the whole palette: against a canvas at L 0.4090
# the brightest possible mark is white at 2.29:1, so everything the page has to
# distinguish — buttons, rails, focus rings, the selected ground — is darker
# than the page rather than lighter.

_LIGHT = {
    "canvas": "#A4ACB9",
    "surface": "#CACDD5",
    "surfaceHover": "#BFC3CC",
    "surfaceActive": "#222B3B",
    "raised": "#F3F4F5",
    "inset": "#E0E3E6",
    "scrim": "rgba(12, 13, 16, 0.55)",

    "border.subtle": "#9AA3B2",
    "border.default": "#474B53",
    "border.strong": "#363A40",

    "text.primary": "#121316",
    "text.secondary": "#1D1F23",
    "text.tertiary": "#3C4046",
    "text.disabled": "#525760",
    "text.onAccent": "#FFFFFF",

    "accent.subtle": "#BDD9C4",
    "accent.default": "#116534",
    "accent.hover": "#0E5B2F",
    "accent.active": "#0C5129",
    "accent.border": "#084622",
    "accent.text": "#043116",

    "success.subtle": "#B7DBD4",
    "success.default": "#056357",
    "success.hover": "#045A4F",
    "success.active": "#035047",
    "success.border": "#02453C",
    "success.text": "#013029",

    "warning.subtle": "#EED6BE",
    "warning.default": "#745007",
    "warning.hover": "#694906",
    "warning.active": "#5E4105",
    "warning.border": "#523703",
    "warning.text": "#392502",

    "danger.subtle": "#F0C4C4",
    "danger.default": "#A02C30",
    "danger.hover": "#93272B",
    "danger.active": "#842226",
    "danger.border": "#721C1F",
    "danger.text": "#511114",

    "info.subtle": "#C8D7F4",
    "info.default": "#1A5996",
    "info.hover": "#175089",
    "info.active": "#14477B",
    "info.border": "#103D6A",
    "info.text": "#082A4B",
}

# Steps of one another by design, and the only pairs exempt from the delta-E
# floor. Anything else that lands inside 2.0 is two names for one colour.
STEP_PAIRS = frozenset(
    [frozenset(("surface", "surfaceHover")),
     frozenset(("surfaceHover", "surfaceActive")),
     frozenset(("surface", "surfaceActive"))]
    + [frozenset(("%s.%s" % (fam, one), "%s.%s" % (fam, two)))
       for fam in ("accent", "success", "warning", "danger", "info")
       for one, two in (("default", "hover"), ("hover", "active"),
                        ("default", "active"))]
)

# ── Type ─────────────────────────────────────────────────────────────────────
# name -> (px, weight). Ratios step about 1.2 between tiers instead of the
# +1px march the audit found, and there is a heading tier at last: the largest
# text in the app used to be 15px.

_FONT = {
    "display": (28, 600),
    "h1": (20, 600),
    "h2": (16, 600),
    "h3": (14, 600),
    "body": (13, 400),
    "bodyMed": (13, 500),
    "small": (12, 400),
    "caption": (11, 500),
    "mono": (12, 400),
}

# `caption` is specified with +0.4px tracking. Qt 5's stylesheet parser has no
# `letter-spacing` property — it accepts `font`, `font-family`, `font-size`,
# `font-style` and `font-weight` and nothing else — so the value lives here for
# `QFont.setLetterSpacing` to apply and is not emitted into the sheet.
TRACKING = {"caption": 0.4}

# Named one family at a time rather than as a CSS list where it matters: Qt 5
# hands `font-family` to QFont::setFamily, which matches the whole string.
_SANS = "'DM Sans', 'Segoe UI', 'Inter', sans-serif"
_MONO = "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace"

# ── Space, radius, control, motion ───────────────────────────────────────────

_SPACE = {"0": 0, "1": 4, "2": 8, "3": 12, "4": 16, "5": 20,
          "6": 24, "7": 32, "8": 40, "9": 48, "hair": 2}

_RADIUS = {"sm": 4, "md": 6, "lg": 10, "pill": 999}

_CONTROL = {
    "comfortable": {"xs": 24, "sm": 28, "md": 32, "lg": 40, "row": 36, "header": 44},
    "compact": {"xs": 22, "sm": 26, "md": 28, "lg": 36, "row": 28, "header": 40},
}

_MOTION = {"instant": 0, "fast": 120, "base": 180, "slow": 260}


# ── The theme object ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Theme:
    """One resolved look: a palette, a scale, and a density already applied."""

    name: str
    density: str
    color: Mapping[str, str]
    font: Mapping[str, tuple]
    space: Mapping[str, int]
    radius: Mapping[str, int]
    control: Mapping[str, int]
    motion: Mapping[str, int]


_PALETTES = {"dark": _DARK, "light": _LIGHT}

DEFAULT_THEME, DEFAULT_DENSITY = "dark", "comfortable"


def theme(name: str = DEFAULT_THEME, density: str = DEFAULT_DENSITY) -> Theme:
    """The named theme at the named density, falling back rather than raising.

    A settings file written by a future build — or hand-edited — must not stop
    the app from starting, so an unknown name is the default name.
    """
    palette = _PALETTES.get(name)
    if palette is None:
        name, palette = DEFAULT_THEME, _PALETTES[DEFAULT_THEME]
    if density not in _CONTROL:
        density = DEFAULT_DENSITY
    return Theme(name=name, density=density, color=dict(palette),
                 font=dict(_FONT), space=dict(_SPACE), radius=dict(_RADIUS),
                 control=dict(_CONTROL[density]), motion=dict(_MOTION))


THEMES: dict = {name: theme(name) for name in _PALETTES}


def from_settings(settings: Mapping) -> Theme:
    """The theme a settings dict asks for, defaulting to dark and comfortable.

    Read with `.get` on purpose: `core.settings` drops keys outside its schema,
    so a profile written before this module existed carries neither key and has
    to keep working.
    """
    return theme(str(settings.get("theme") or DEFAULT_THEME),
                 str(settings.get("density") or DEFAULT_DENSITY))


def token(t: Theme, path: str):
    """One value by dotted path — `token(t, "color.text.primary")`.

    `font` takes a third segment because a tier is a pair:
    `font.body.size` and `font.body.weight`.
    """
    group, _, rest = path.partition(".")
    if group == "font":
        tier, _, part = rest.partition(".")
        px, weight = t.font[tier]
        if part in ("", "size", "px"):
            return px
        if part == "weight":
            return weight
        raise KeyError(path)
    table = {"color": t.color, "space": t.space, "radius": t.radius,
             "control": t.control, "motion": t.motion}.get(group)
    if table is None or rest not in table:
        raise KeyError(path)
    return table[rest]


# ── The stylesheet ───────────────────────────────────────────────────────────


class _Placeholders(Template):
    """`${color.text.primary}`, so the sheet says what it is asking for.

    Template's own identifier pattern stops at the first dot, which would leave
    every colour in the sheet spelled as an abbreviation nobody can grep for.
    """

    idpattern = r"[_a-z][_a-z0-9]*(?:\.[_a-z0-9]+)*"
    flags = re.IGNORECASE


class _Tokens:
    """The mapping `_Placeholders.substitute` reads, backed by `token()`.

    `family.sans` and `family.mono` are answered here rather than by `token()`
    because a font stack is not a per-theme value and the dataclass the contract
    specifies has no field for one.
    """

    _FAMILIES = {"family.sans": _SANS, "family.mono": _MONO}

    def __init__(self, t: Theme):
        self._theme = t

    def __getitem__(self, path: str):
        if path in self._FAMILIES:
            return self._FAMILIES[path]
        return token(self._theme, path)


# Written as one template rather than assembled per widget, because the order
# rules are real: `QLabel`'s transparent background has to follow `QWidget`'s
# fill, and the focus block has to come last so a ring is not lost to a hover
# rule of equal specificity that happened to be written later.
_SHEET = """
/* ── Ground ──────────────────────────────────────────────────────────────── */

QWidget {
    background-color: ${color.canvas};
    color: ${color.text.primary};
    font-family: ${family.sans};
    font-size: ${font.body.size}px;
}

QMainWindow {
    background-color: ${color.canvas};
}

/* The QWidget rule above matches QLabel as well, so without this every label
   paints an opaque page rectangle over whatever it sits on — most visibly the
   cards, where each caption showed up as a darker bar. Labels that do want a
   fill (#toast, #warning) set it under their own id selector, which is more
   specific and still wins. */
QLabel {
    background: transparent;
}

/* Same rule, one level up. A layout needs a widget to live on, so half the
   containers on these screens are bare QWidgets, and the QWidget rule hands
   each of them the page's own colour. The leading dot matches instances of
   QWidget itself and not of its subclasses, so scaffolding paints nothing and
   everything that is really a surface is untouched. */
.QWidget {
    background: transparent;
}

QDialog {
    background-color: ${color.raised};
}

QScrollArea {
    border: none;
    background: transparent;
}

QScrollArea > QWidget > QWidget {
    background: transparent;
}

QSplitter::handle {
    background: transparent;
}

QToolTip {
    background-color: ${color.raised};
    color: ${color.text.primary};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.sm}px;
    padding: ${space.1}px ${space.2}px;
    font-size: ${font.small.size}px;
}

/* ── Text ────────────────────────────────────────────────────────────────── */

QLabel#app_name {
    color: ${color.text.primary};
    font-size: ${font.h2.size}px;
    font-weight: ${font.h2.weight};
}

QLabel#count_label {
    color: ${color.text.primary};
    font-size: ${font.h2.size}px;
    font-weight: ${font.h2.weight};
}

QLabel#count_sub {
    color: ${color.text.secondary};
    font-size: ${font.body.size}px;
    font-weight: ${font.body.weight};
}

QLabel#section_label {
    color: ${color.text.tertiary};
    font-size: ${font.caption.size}px;
    font-weight: ${font.caption.weight};
}

QLabel#muted {
    color: ${color.text.secondary};
    font-size: ${font.small.size}px;
}

QLabel#status_text {
    color: ${color.text.secondary};
    font-size: ${font.small.size}px;
    font-weight: ${font.small.weight};
}

QLabel#hint {
    color: ${color.text.tertiary};
    font-size: ${font.small.size}px;
}

QLabel#status_ok {
    color: ${color.success.text};
    font-size: ${font.small.size}px;
}

QLabel#status_err {
    color: ${color.danger.text};
    font-size: ${font.small.size}px;
}

QLabel#status_busy {
    color: ${color.text.tertiary};
    font-size: ${font.small.size}px;
}

QLabel#warning {
    color: ${color.warning.text};
    background-color: ${color.warning.subtle};
    border: 1px solid ${color.warning.border};
    border-radius: ${radius.md}px;
    padding: ${space.2}px ${space.3}px;
    font-size: ${font.small.size}px;
    font-weight: ${font.bodyMed.weight};
}

QLabel#toast {
    background-color: ${color.raised};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    color: ${color.text.primary};
    font-size: ${font.small.size}px;
    padding: ${space.3}px ${space.3}px;
}

QFrame#divider {
    background: ${color.border.subtle};
    border: none;
    max-height: 1px;
}

QFrame#card {
    background-color: ${color.surface};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.lg}px;
}

QWidget#results_header {
    background-color: transparent;
    border-bottom: 1px solid ${color.border.subtle};
}

/* ── Fields ──────────────────────────────────────────────────────────────── */
/* Every control carries a 1px border in every state and only its colour moves,
   so the contents rect never shifts under a focus ring. */

QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser {
    background-color: ${color.inset};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.2}px ${space.3}px;
    color: ${color.text.primary};
    font-size: ${font.body.size}px;
    selection-background-color: ${color.surfaceActive};
    selection-color: ${color.text.onAccent};
}

QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {
    border-color: ${color.border.strong};
}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

QLineEdit#search_box {
    padding: ${space.1}px ${space.2}px;
    font-size: ${font.small.size}px;
}

/* The email preview is a document, not chrome: the message body carries its own
   near-black ink and the screens paint the document itself white. What the
   sheet owns is the well it sits in. */
QTextEdit#email_paper, QTextBrowser#email_paper {
    background-color: ${color.inset};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.2}px;
}

QComboBox {
    background-color: ${color.inset};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.1}px ${space.2}px;
    color: ${color.text.primary};
    font-size: ${font.body.size}px;
}

QComboBox:hover {
    border-color: ${color.border.strong};
}

QComboBox:disabled {
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

QComboBox QAbstractItemView {
    background-color: ${color.raised};
    color: ${color.text.primary};
    selection-background-color: ${color.surfaceActive};
    selection-color: ${color.text.onAccent};
    border: 1px solid ${color.border.default};
    outline: none;
}

QSpinBox#spin, QDoubleSpinBox#spin, QDateEdit#spin {
    background-color: ${color.inset};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.1}px ${space.2}px;
    color: ${color.text.primary};
    font-size: ${font.body.size}px;
}

QSpinBox#spin::up-button, QSpinBox#spin::down-button,
QDoubleSpinBox#spin::up-button, QDoubleSpinBox#spin::down-button,
QDateEdit#spin::up-button, QDateEdit#spin::down-button {
    width: ${space.4}px;
    border: none;
    background: transparent;
}

/* No `::indicator` rule here, and that is deliberate — see `TickStyle` in
   `ui/app.py`. A stylesheet indicator can only carry a fill and a border, and a
   tick has to come from `image: url(...)`, which Qt resolves through QPixmap
   and therefore only from a real file or a compiled .qrc. Neither survives a
   one-file PyInstaller build cleanly, so the whole indicator is painted in
   code. The moment any `QCheckBox::indicator` rule appears here it wins over
   the style and the tick disappears again. */
QCheckBox {
    spacing: ${space.2}px;
    color: ${color.text.primary};
    font-size: ${font.body.size}px;
    background: transparent;
}

QCheckBox:disabled {
    color: ${color.text.disabled};
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
/* One height per kind, from `control`. The rest, sizes included, is the same
   box in every variant: 1px border, `radius.md`, `space.4` of side padding. */

QPushButton {
    background-color: ${color.surface};
    color: ${color.text.primary};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: 0 ${space.4}px;
    height: ${control.md}px;
    font-size: ${font.body.size}px;
    font-weight: ${font.bodyMed.weight};
}

QPushButton:hover {
    background-color: ${color.surfaceHover};
    border-color: ${color.border.strong};
}

QPushButton:pressed {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
}

QPushButton:disabled {
    background-color: ${color.surface};
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

QPushButton#outlined {
    background-color: transparent;
    color: ${color.text.primary};
    border: 1px solid ${color.border.default};
}

QPushButton#outlined:hover {
    background-color: ${color.surfaceHover};
    border-color: ${color.border.strong};
}

QPushButton#outlined:pressed {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
}

QPushButton#outlined:disabled {
    background-color: transparent;
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

/* The safe primary action. */
QPushButton#start_btn {
    background-color: ${color.accent.default};
    color: ${color.text.onAccent};
    border: 1px solid ${color.accent.border};
    border-radius: ${radius.md}px;
    height: ${control.lg}px;
    font-size: ${font.h3.size}px;
    font-weight: ${font.h3.weight};
    padding: 0 ${space.5}px;
}

QPushButton#start_btn:hover {
    background-color: ${color.accent.hover};
}

QPushButton#start_btn:pressed {
    background-color: ${color.accent.active};
}

QPushButton#start_btn:disabled {
    background-color: ${color.surface};
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

/* Destructive, but reversible — Stop, Remove. */
QPushButton#danger {
    background-color: transparent;
    color: ${color.danger.text};
    border: 1px solid ${color.danger.border};
}

QPushButton#danger:hover {
    background-color: ${color.danger.subtle};
}

QPushButton#danger:pressed {
    background-color: ${color.danger.active};
    color: ${color.text.onAccent};
}

/* Without this the id selector beats QPushButton:disabled and a disabled Stop
   stays as loud as a live one. */
QPushButton#danger:disabled {
    background-color: transparent;
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

/* The one button that mails real strangers. It is the only filled danger in
   the app, and it is filled precisely so it cannot be mistaken for the others. */
QPushButton#live {
    background-color: ${color.danger.default};
    color: ${color.text.onAccent};
    border: 1px solid ${color.danger.border};
    font-weight: ${font.h3.weight};
}

QPushButton#live:hover {
    background-color: ${color.danger.hover};
}

QPushButton#live:pressed {
    background-color: ${color.danger.active};
}

QPushButton#live:disabled {
    background-color: ${color.surface};
    color: ${color.text.disabled};
    border-color: ${color.border.subtle};
}

/* The rehearsal badge. Dashed, because "this is not the real thing" is worth
   saying in a shape and not only in a word. */
QPushButton#rehearsal {
    background-color: transparent;
    color: ${color.text.tertiary};
    border: 1px dashed ${color.border.default};
    font-weight: ${font.bodyMed.weight};
}

QPushButton#rehearsal:hover {
    background-color: ${color.surfaceHover};
    color: ${color.text.primary};
}

QPushButton#reveal {
    background-color: transparent;
    color: ${color.text.secondary};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.sm}px;
    padding: 0 ${space.2}px;
    height: ${control.md}px;
    font-size: ${font.caption.size}px;
    font-weight: ${font.caption.weight};
}

QPushButton#reveal:hover {
    color: ${color.text.primary};
    border-color: ${color.border.strong};
}

QPushButton#reveal:checked {
    color: ${color.text.onAccent};
    background-color: ${color.surfaceActive};
}

/* ── Tabs: selection is the louder signal ────────────────────────────────── */
/* A selected tab changes ground (4.31:1 on the page in dark) and gains a 2px
   accent rail. The base state carries the same 1px box and the same 2px rail
   in `transparent`, so nothing moves when either mark arrives. */

QPushButton#tab {
    background-color: transparent;
    color: ${color.text.tertiary};
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    border-radius: ${radius.sm}px;
    padding: 0 ${space.3}px;
    height: ${control.sm}px;
    font-size: ${font.small.size}px;
    font-weight: ${font.bodyMed.weight};
}

QPushButton#tab:hover {
    color: ${color.text.primary};
    background-color: ${color.surfaceHover};
}

QPushButton#tab:checked {
    color: ${color.text.onAccent};
    background-color: ${color.surfaceActive};
    border-bottom-color: ${color.accent.default};
}

/* ── Lists and tables ────────────────────────────────────────────────────── */

QListWidget {
    background-color: ${color.inset};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.1}px;
    font-size: ${font.small.size}px;
    color: ${color.text.secondary};
}

QListWidget::item {
    padding: ${space.1}px ${space.2}px;
    border: none;
    background: transparent;
}

QListWidget#saved_list, QListWidget#service_list {
    background-color: ${color.inset};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.1}px;
    font-size: ${font.small.size}px;
    color: ${color.text.secondary};
}

QListWidget#saved_list::item, QListWidget#service_list::item {
    padding: ${space.1}px ${space.2}px;
    border-left: 2px solid transparent;
    border-radius: ${radius.sm}px;
}

/* `:hover` is spelled out on the selected row as well because `::item:hover`
   matches a selected row too, and it is how mousing across a list used to wipe
   the selection out from under the pointer. */
QListWidget#saved_list::item:selected,
QListWidget#saved_list::item:selected:hover,
QListWidget#service_list::item:selected,
QListWidget#service_list::item:selected:hover {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
    border-left: 2px solid ${color.accent.default};
}

QListWidget#saved_list::item:hover, QListWidget#service_list::item:hover {
    background-color: ${color.surfaceHover};
}

/* The service list's group headings are disabled rows, not dead ones. */
QListWidget#service_list::item:disabled {
    color: ${color.text.tertiary};
    font-weight: ${font.h3.weight};
    padding-top: ${space.2}px;
}

QTableWidget {
    background-color: ${color.inset};
    alternate-background-color: ${color.surface};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.lg}px;
    gridline-color: ${color.border.subtle};
    font-size: ${font.small.size}px;
    color: ${color.text.primary};
}

QTableWidget::item {
    padding: ${space.2}px ${space.2}px;
    border-bottom: 1px solid ${color.border.subtle};
    border-left: 2px solid transparent;
}

QTableWidget::item:selected, QTableWidget::item:selected:hover {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
    border-left: 2px solid ${color.accent.default};
}

QTableWidget::item:hover {
    background-color: ${color.surfaceHover};
}

/* Declared once. The old sheet gave this table a 10px radius and then an 8px
   one under its id, and painted both. */
QTableWidget#results_table {
    border-radius: ${radius.lg}px;
}

QHeaderView::section {
    background-color: ${color.surface};
    color: ${color.text.tertiary};
    font-size: ${font.caption.size}px;
    font-weight: ${font.caption.weight};
    padding: ${space.2}px ${space.2}px;
    height: ${control.sm}px;
    border: none;
    border-bottom: 1px solid ${color.border.subtle};
    border-right: 1px solid ${color.border.subtle};
}

/* ── Menus ───────────────────────────────────────────────────────────────── */

QMenu {
    background-color: ${color.raised};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.md}px;
    padding: ${space.1}px;
    color: ${color.text.primary};
}

QMenu::item {
    padding: ${space.1}px ${space.4}px;
    border-radius: ${radius.sm}px;
}

QMenu::item:selected {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
}

QMenu::item:disabled {
    color: ${color.text.disabled};
}

QMenu::separator {
    height: 1px;
    background: ${color.border.subtle};
    margin: ${space.1}px ${space.1}px;
}

/* ── Bars and sliders ────────────────────────────────────────────────────── */

QScrollBar:vertical {
    background: transparent;
    width: ${space.2}px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: ${color.border.subtle};
    border-radius: ${radius.sm}px;
    min-height: ${space.6}px;
}

QScrollBar::handle:vertical:hover {
    background: ${color.border.default};
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: transparent;
    height: ${space.2}px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: ${color.border.subtle};
    border-radius: ${radius.sm}px;
    min-width: ${space.6}px;
}

QScrollBar::handle:horizontal:hover {
    background: ${color.border.default};
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QProgressBar {
    background-color: ${color.inset};
    border: none;
    border-radius: ${radius.pill}px;
    height: ${space.hair}px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: ${color.accent.default};
    border-radius: ${radius.pill}px;
}

QProgressBar#budget_bar {
    background-color: ${color.inset};
    border: 1px solid ${color.border.subtle};
    border-radius: ${radius.sm}px;
    height: ${space.2}px;
    text-align: center;
    color: transparent;
}

QProgressBar#budget_bar::chunk {
    background-color: ${color.accent.default};
    border-radius: ${radius.sm}px;
}

QSlider::groove:horizontal {
    border: none;
    height: ${space.1}px;
    background: ${color.inset};
    border-radius: ${radius.sm}px;
}

QSlider::handle:horizontal {
    background: ${color.border.strong};
    width: ${space.3}px;
    height: ${space.3}px;
    margin: -${space.1}px 0;
    border-radius: ${radius.md}px;
}

QSlider::handle:horizontal:hover {
    background: ${color.text.primary};
}

QSlider::sub-page:horizontal {
    background: ${color.accent.default};
    border-radius: ${radius.sm}px;
}

/* ── Focus, last ─────────────────────────────────────────────────────────── */
/* `outline` was the obvious property and it is the wrong one: Qt paints an
   outline on the CONTENTS rect, not outside the border box, so a ring drawn
   that way lands inside the padding and comes out as a line struck through the
   label. The ring is the border instead, held at 1px in every state so the box
   never changes size and only its colour moves.

   Every id is named because Qt ranks selectors by CSS2 specificity: a bare
   `QPushButton:focus` loses to `QPushButton#outlined` on `border`, and the
   button would keep its resting hairline while focused. The block sits last in
   the sheet for the same arithmetic one rung down — `#reveal:hover` also writes
   `border-color` and ties with `#reveal:focus`, and a tie goes to whichever
   came later.

   The ring is `accent.border` and never white: white measured 17.01:1 against a
   selected tab at 1.50:1, which is the finding this whole system exists to fix.
   It is subordinate by measurement now — 3.70:1 on the page against a selection
   at 4.31:1. */

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox#spin:focus, QDoubleSpinBox#spin:focus, QDateEdit#spin:focus {
    border: 1px solid ${color.accent.border};
    outline: none;
}

QPushButton:focus,
QPushButton#outlined:focus,
QPushButton#danger:focus,
QPushButton#live:focus,
QPushButton#rehearsal:focus,
QPushButton#reveal:focus {
    border: 1px solid ${color.accent.border};
    outline: none;
}

/* The one button the shared ink cannot mark. `#start_btn` already rests on a
   1px `accent.border` — the rim that keeps a filled accent button findable on
   the page, 3.70:1 in dark and 4.81:1 in light — so a ring in the same token
   repainted the border in the colour it already was and changed exactly zero
   pixels. The primary action was the one control in the app with no focus
   indicator at all, which is the defect this block exists to prevent.

   `accent.subtle` steps the other way instead: 2.38:1 against the fill in dark
   and 4.73:1 in light, dark enough that it can never outrank the selection
   beside it (1.40:1 on the page against a selection at 4.31:1) and still a
   1px ring on a border that was 1px already, so no geometry moves. */
QPushButton#start_btn:focus {
    border: 1px solid ${color.accent.subtle};
    outline: none;
}

/* A tab that is focused keeps its rail, and a tab that is selected keeps its
   ring: both marks, neither lost. */
QPushButton#tab:focus {
    border: 1px solid ${color.accent.border};
    border-bottom: 2px solid transparent;
    outline: none;
}

QPushButton#tab:checked:focus {
    border: 1px solid ${color.accent.border};
    border-bottom: 2px solid ${color.accent.default};
    outline: none;
}

/* Every id spelled out here for the reason the button block spells its own out,
   and it is not theoretical: `QListWidget#saved_list` writes `border` under an
   id selector, so a bare `QListWidget:focus` lost to it on CSS2 specificity and
   the two lists that carry every saved search and every scrape service kept
   their resting hairline while focused — 0 pixels changed against 734 on a
   QListWidget with no id. `#email_paper` is the same rule one widget over. */
QListWidget:focus, QListWidget#saved_list:focus, QListWidget#service_list:focus,
QTableWidget:focus, QTableWidget#results_table:focus,
QTextEdit#email_paper:focus, QTextBrowser#email_paper:focus,
QAbstractScrollArea:focus {
    border: 1px solid ${color.accent.border};
    outline: none;
}
"""


def stylesheet(t: Theme) -> str:
    """The whole application sheet for `t`. The only producer of QSS in the app."""
    return _Placeholders(_SHEET).substitute(_Tokens(t))


# ── The one thing the sheet cannot paint ─────────────────────────────────────


class TickStyle(QProxyStyle):
    """Paints every check indicator in the app, tick included.

    The sheet used to style the indicator and could only give it a fill, so a
    ticked box and an empty one differed by nothing but brightness — most
    dangerously on "Dry run — build and log every email, send none", where the
    user has no way to tell whether the safety toggle is on.

    Painting it here rather than in the sheet is what makes the tick possible at
    all: `image: url(...)` needs a file on disk or a compiled resource, and this
    ships as a one-file executable. It also covers the two primitives at once —
    QCheckBox draws `PE_IndicatorCheckBox`, a checkable list row draws
    `PE_IndicatorItemViewItemCheck` — so the service list gets the same mark.

    It lives beside the tokens rather than in `ui/app.py` because it is the one
    thing in the app that paints a colour without going through the sheet, and
    it therefore has to read the same palette the sheet does.
    """

    _CHECK_PRIMITIVES = (QStyle.PE_IndicatorCheckBox,
                         QStyle.PE_IndicatorItemViewItemCheck)

    def __init__(self, base, t: Theme):
        super().__init__(base)
        self._box = t.control["xs"] - 8          # 16px at comfortable
        self._radius = t.radius["sm"]
        self._off = t.color["inset"]
        self._on = t.color["accent.default"]
        self._edge = t.color["border.default"]
        self._edge_hover = t.color["border.strong"]
        self._dead = t.color["surface"]
        self._dead_edge = t.color["border.subtle"]
        self._tick = t.color["text.onAccent"]

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return self._box
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element not in self._CHECK_PRIMITIVES:
            super().drawPrimitive(element, option, painter, widget)
            return

        state = option.state
        live = bool(state & QStyle.State_Enabled)
        on = bool(state & QStyle.State_On)
        mixed = bool(state & QStyle.State_NoChange)

        # A square centred in whatever the caller reserved: the delegate hands
        # over the whole check column, and an indicator stretched across it
        # would sit off to one side of its own label. The half pixel puts the
        # 1px pen on pixel centres — without it the border straddles two columns
        # and antialiases into a soft smudge instead of a crisp edge.
        side = min(option.rect.width(), option.rect.height(), self._box)
        left = option.rect.x() + (option.rect.width() - side) // 2
        top = option.rect.y() + (option.rect.height() - side) // 2
        box = QRectF(left + 0.5, top + 0.5, side - 1, side - 1)

        if not live:
            fill, edge = self._dead, self._dead_edge
        elif on or mixed:
            fill, edge = self._on, self._on
        elif state & QStyle.State_MouseOver:
            fill, edge = self._off, self._edge_hover
        else:
            fill, edge = self._off, self._edge

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(edge), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(box, self._radius, self._radius)

        if on or mixed:
            mark = QPen(QColor(self._tick if live else self._off),
                        max(1.8, side / 7.5))
            mark.setCapStyle(Qt.RoundCap)
            mark.setJoinStyle(Qt.RoundJoin)
            painter.setPen(mark)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(self._mark(box, on))
        painter.restore()

    @staticmethod
    def _mark(box: QRectF, on: bool) -> QPolygonF:
        """The tick, or the dash a tri-state box shows for "partly"."""
        x, y, w, h = box.x(), box.y(), box.width(), box.height()
        if not on:
            return QPolygonF([QPointF(x + w * 0.26, y + h * 0.50),
                              QPointF(x + w * 0.74, y + h * 0.50)])
        return QPolygonF([QPointF(x + w * 0.23, y + h * 0.52),
                          QPointF(x + w * 0.42, y + h * 0.73),
                          QPointF(x + w * 0.77, y + h * 0.28)])


# ── Application ──────────────────────────────────────────────────────────────


def enable_high_dpi() -> None:
    """The two attributes that make px mean the same thing at any DPI.

    Separate from `apply()` because Qt only honours `AA_EnableHighDpiScaling`
    while no QApplication exists yet — set afterwards it is silently ignored and
    the app renders at 1x on a scaled display. `run()` calls this first;
    `apply()` calls it again so a caller that only has an app still gets the
    pixmap half.
    """
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


_BASE_FONT = None


def base_font() -> QFont:
    """The app font: the system's own point size, in whichever family we have.

    Points, deliberately and only here. The audit found the app mixing pt and
    px, so its own text ignored the Windows text-scaling setting while its menus
    and tooltips followed it. Everything the sheet sets is px — which Qt scales
    with the display — and this single point size is what those px sit on and
    what any widget the sheet does not reach inherits.

    Answered once and remembered: `QFontDatabase().families()` enumerates every
    font installed on the machine, which costs hundreds of milliseconds, and
    `apply()` is called once per screen in the tests and hundreds of times in a
    sweep.
    """
    global _BASE_FONT
    if _BASE_FONT is not None:
        return QFont(_BASE_FONT)

    system = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    size = system.pointSize()
    if size <= 0:
        size = 9 if sys.platform.startswith("win") else 10
    families = set(QFontDatabase().families())
    _BASE_FONT = QFont(system.family(), size)
    for family in ("DM Sans", "Segoe UI", system.family()):
        if family in families:
            _BASE_FONT = QFont(family, size)
            break
    return QFont(_BASE_FONT)


def apply(app: QApplication, t: Theme) -> None:
    """The app's whole look in one call: DPI, font, style, palette, sheet.

    One call because the parts are not independent. The sheet deliberately
    leaves every check indicator unstyled so `TickStyle` can paint it, and a
    sheet applied over a plain Fusion would leave the boxes light grey on a dark
    page; the palette carries the two colours Qt has no stylesheet property for.

    Placeholder text is one of those two: `::placeholder` parses and then does
    nothing. Left alone it is the text colour at half alpha; named outright it
    paints opaque, and it takes `text.tertiary` so an empty field still reads as
    empty next to a filled one.

    Applying the theme the app already wears returns without touching anything.
    `setStyleSheet` repolishes every widget alive in the process — around 0.7s
    once four screens are built — and the geometry sweeps call this once per
    helper, a few hundred times per run.
    """
    sheet = stylesheet(t)
    if app.styleSheet() == sheet and isinstance(app.style(), TickStyle):
        return

    enable_high_dpi()
    app.setFont(base_font())
    app.setStyle(TickStyle(QStyleFactory.create("Fusion"), t))

    palette = app.palette()
    palette.setColor(QPalette.PlaceholderText, QColor(t.color["text.tertiary"]))
    palette.setColor(QPalette.Window, QColor(t.color["canvas"]))
    palette.setColor(QPalette.WindowText, QColor(t.color["text.primary"]))
    palette.setColor(QPalette.ToolTipBase, QColor(t.color["raised"]))
    palette.setColor(QPalette.ToolTipText, QColor(t.color["text.primary"]))
    app.setPalette(palette)
    app.setStyleSheet(sheet)
