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

`surfaceActive` is the sixth ground, and the two rules the contract writes for
it — every `text.*` at 4.5:1 on it, `border.default` at 3:1 on it — are the two
this palette cannot keep. Both are out of range rather than badly chosen, and
the range is what `tests/test_theme.py` measures rather than takes on trust:

  * the ink. A selected ground has to clear 3:1 against the ground it replaces,
    and the dimmest text tier has to clear 4.5:1 against the selected ground, so
    the dimmest tier ends up 13.5x the row it is read on. `surface` sits at
    l=0.0776 (l is L+0.05, the WCAG offset), which puts that tier at l>=1.048 —
    brighter than white. What is achievable is one ink rather than four, and the
    sheet paints exactly that: every rule that grounds on `surfaceActive` names
    `text.onAccent`, which measures 4.50:1 in dark and 13.97:1 in light, and
    `test_a_selected_ground_never_carries_ink_it_cannot_read` holds it there.
  * the outline. `border.default` and `surfaceActive` both sit at least 3x above
    `surface`, so putting 3:1 between the two of them needs one of them 9x above
    `surface` — l>=0.70, a near-white hairline on every control — while the ink
    rule caps the selected ground at l<=0.2333. The sheet answers it a rule at a
    time instead: a control whose ground becomes `surfaceActive` takes its edge
    in `text.onAccent` too.

The same arithmetic is why the focus ring is dimmer than the resting border in
dark and cannot be lifted above it. Rule 5 puts `border.default` at 3x the
lightest ground it touches (3 x l raised = 0.340) and the readable-selection cap
puts any subordinate mark at or under l=0.233, so the resting border is 46%
louder than the loudest ring the ordering permits. Spelled as a span, the dark
theme is asked for 1.4 x 1.4 (the surface ladder) x 3 (the border on `raised`)
x 4.5 (white on the selection) = 26.46:1 between the page and its brightest ink,
and black to white is 21:1. It is 26% short before a single colour is chosen.
The light theme has the range — its page is a mid grey with 8.9:1 of room below
it — and carries the full ordering: rest 3.94, focus 5.34, selection 6.55 on the
page, and the same order on all five grounds.

Where a rule could not be met, the reason is arithmetic and is written down at
the token that gives way — see `_DARK`'s surfaceActive and accent.border.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from string import Template
from typing import Mapping

from PyQt5 import sip
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
#   canvas 0.0049  inset 0.0132  surface 0.0276  surfaceHover 0.0425
#   raised 0.0634  surfaceActive 0.1833   border.default 0.2946
#   text.disabled 0.3528  text.tertiary 0.5035  text.primary 0.8975
#
# canvas -> surface is 1.41:1 and surface -> raised 1.46:1, so a card reads as a
# card and a dialog sits above the page. `raised` is the lightest ground any
# text lands on, which is what fixes the floor for the whole text ramp: 4.5:1 on
# it needs L >= 0.4610, and that is why `text.tertiary` is as bright as it is.
#
# The greys carry a single cool cast — b a few points over r at every step —
# rather than the blue-grey the palette used to run, because that is what the
# register being aimed at does: a charcoal that reads as neutral beside a
# saturated blue and does not compete with it for hue.

_DARK = {
    # Very dark charcoal and deliberately not black: at 14/15/21 it is a ground
    # a shadow can still fall on. It also sits at the top of the window rule 3
    # leaves it — `surface` is capped by the selected ground below, and 1.4:1
    # under that cap is L <= 0.0054.
    "canvas": "#0E0F15",
    "surface": "#2D2E34",
    "surfaceHover": "#383A41",
    # The selected ground, and the tightest value in the palette. White ink caps
    # it at L 0.1833 (4.5:1) and 3:1 against `surface` floors it at L 0.1828;
    # #727780 measures L 0.1833, which is 4.50:1 for the ink and 3.01:1 for the
    # ground. There is no room above or below, which is the price of a selection
    # that is both legible and unmistakable on a dark page — and it is why the
    # ink on this one ground is `text.onAccent` and not the four-tier text ramp:
    # the ramp's dimmest tier would have to measure l >= 1.048, and white is
    # 1.05. Every rule in the sheet that grounds here says so.
    "surfaceActive": "#727780",
    "raised": "#45474F",
    "inset": "#1D1E24",
    "scrim": "rgba(0, 0, 0, 0.66)",

    # A hairline, at last. `border.subtle` used to sit at L 0.0778, above
    # `raised`, where a divider inside a card was a line you read before the
    # words beside it; at L 0.0501 it sits below `raised` and still clears the
    # just-noticeable difference from both grounds it can be drawn between
    # (delta-E 3.5 from `raised`, 3.4 from `surfaceHover`).
    "border.subtle": "#3D3F48",
    # Loud, and not by choice: rule 5 puts it 3:1 above `raised`, the lightest
    # ground it is drawn on, which floors it at L 0.2907. It measures 3.04:1
    # there and 6.28:1 on the page.
    "border.default": "#8F93A6",
    "border.strong": "#A6AAB9",

    "text.primary": "#F3F3F5",
    "text.secondary": "#D1D3D8",
    "text.tertiary": "#BABCC3",
    "text.disabled": "#9EA0A9",
    "text.onAccent": "#FFFFFF",

    # The accent is a blue now, and the constraint that shaped the old green
    # shapes it identically: rule 2 asks white to clear 4.5:1 on every filled
    # state, so the blue is as light as that allows and no lighter. #0A84FF
    # itself measures 3.11:1 with white and cannot be the fill; scaled down its
    # own hue line it becomes #0869CC, which measures 5.38:1, with the hover at
    # 4.65:1 — the tightest fill in the palette — and the pressed state at
    # 6.51:1. That is the whole cost of a filled accent that can be read.
    "accent.subtle": "#03274C",
    "accent.default": "#0869CC",
    "accent.hover": "#0973DE",
    "accent.active": "#075DB3",
    # The focus ring. 3.83:1 on canvas and 3.33:1 on inset, against a selection
    # that measures 4.25:1 and 3.69:1 on the same two grounds — subordinate
    # everywhere, which is the rule. It cannot also clear 3:1 on `surface`
    # (it measures 2.71:1 there): doing so would need a ring above 4.24:1 on
    # canvas, and the selection cannot go higher without dropping its ink under
    # 4.5:1. Ordering wins, because the audit's critical finding is the ordering.
    #
    # It is also dimmer than the border it replaces — 3.83:1 against
    # `border.default`'s 6.28:1 on canvas — and that is the one defect in this
    # file with no colour that fixes it. A ring above the resting border has to
    # clear 3 x l(raised) = 0.340, and a ring at or under the selection cannot
    # pass l = 0.233; the two bounds cross by 46%, so the whole window is empty
    # and every blue in it is a blue that outranks the selection. What is left
    # is holding the ring at the top of the window it does have: it measures 90%
    # of the selection on every ground, and
    # `test_the_focus_ring_is_as_loud_as_the_ordering_lets_it_be` keeps it there.
    # The light palette has the range and carries the full ordering.
    "accent.border": "#086ED5",
    "accent.text": "#88C4FF",

    # Green is a state again, now that the brand is not one. The five families
    # are the platform's own semantic set — blue, green, orange, red, teal —
    # which is also what keeps them apart: `info` is the teal and not a second
    # blue, for the same reason `success` used to be the teal and not a second
    # green. Two ramps of six values each in one hue is how a palette ends up
    # with 18 pairs under the just-noticeable difference.
    "success.subtle": "#0A2A12",
    "success.default": "#1B7732",
    "success.hover": "#1E8337",
    "success.active": "#18682C",
    "success.border": "#208A3A",
    "success.text": "#4FD871",

    "warning.subtle": "#472D03",
    "warning.default": "#996006",
    "warning.hover": "#A56706",
    "warning.active": "#895505",
    "warning.border": "#B06E07",
    "warning.text": "#FFC56A",

    "danger.subtle": "#681C18",
    "danger.default": "#C5352D",
    "danger.hover": "#D53A31",
    "danger.active": "#B03028",
    "danger.border": "#E33E34",
    "danger.text": "#FFA39E",

    "info.subtle": "#0D3036",
    "info.default": "#1F7381",
    "info.hover": "#227E8E",
    "info.active": "#1C6673",
    "info.border": "#248697",
    "info.text": "#83CFDD",
}

# Built to the same five rules from the light end, not by inverting the dark one
# — an inversion of a palette whose lightest ground is L 0.06 lands nowhere near
# a usable light theme.
#
# The page is a mid grey and that is arithmetic, not taste. `raised` has to stay
# clear of pure white so `text.onAccent` is not the same token twice over, which
# puts it at L 0.9375; `surface` is then capped at L 0.6457 by the 1.4:1 step
# below it, and `canvas` at L 0.4420 by the step below that. The #F5F5F7 page
# with white cards the register would otherwise ask for measures 1.08:1 and
# fails rule 3 outright — a ladder of 1.4:1 twice over is 1.96x of offset
# luminance between the page and a popover, and there is not that much room
# between a near-white card and white. So the light theme takes the register's
# hues and its blue, and its own page tone is what the contract leaves it.
#
# One consequence runs through the whole palette: against a canvas at L 0.4420
# the brightest possible mark is white at 2.13:1, so everything the page has to
# distinguish — buttons, rails, focus rings, the selected ground — is darker
# than the page rather than lighter. It is also the theme with the range for
# the full ordering the audit asked for, and it carries it: on the page a
# resting border measures 3.94:1, a focus ring 5.34:1 and a selected row
# 6.55:1, and the same order holds on all five grounds.

_LIGHT = {
    "canvas": "#AEB1BF",
    "surface": "#D0D2DA",
    "surfaceHover": "#C3C5D0",
    "surfaceActive": "#2A2C31",
    "raised": "#F7F8F9",
    "inset": "#E4E5EA",
    "scrim": "rgba(12, 13, 16, 0.55)",

    "border.subtle": "#979BAD",
    "border.default": "#4B4D57",
    "border.strong": "#3E4049",

    "text.primary": "#0E1012",
    "text.secondary": "#202227",
    "text.tertiary": "#3B3E44",
    "text.disabled": "#535760",
    "text.onAccent": "#FFFFFF",

    # The same blue, read the other way up: the fill darkens where the dark
    # theme's brightens, and `subtle` is a pale tint of it rather than a deep
    # one. White measures 7.02:1 on the resting fill here.
    "accent.subtle": "#C1E0FF",
    "accent.default": "#0758AB",
    "accent.hover": "#06509C",
    "accent.active": "#05498C",
    "accent.border": "#043A70",
    "accent.text": "#032950",

    "success.subtle": "#B5EFC3",
    "success.default": "#196B2D",
    "success.hover": "#166129",
    "success.active": "#145825",
    "success.border": "#124E21",
    "success.text": "#0C3516",

    "warning.subtle": "#FFE6BE",
    "warning.default": "#945C06",
    "warning.hover": "#885405",
    "warning.active": "#7B4C05",
    "warning.border": "#704604",
    "warning.text": "#4A2E03",

    "danger.subtle": "#FFCECC",
    "danger.default": "#B03028",
    "danger.hover": "#A12B25",
    "danger.active": "#922721",
    "danger.border": "#84241E",
    "danger.text": "#581814",

    "info.subtle": "#BDE6ED",
    "info.default": "#1B6371",
    "info.hover": "#195A66",
    "info.active": "#16515C",
    "info.border": "#13464F",
    "info.text": "#0D3036",
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
#
# The sizes are the ones the platform scale this system takes its register from
# gives its title tiers — 28 for a large title, 22 and 17 for the two below it,
# 13 for body — rather than the web's 20 and 16, which at this density read as
# a document rather than as an application. Four heading tiers at 600, four
# body tiers at 400 and 500, and that is the whole hierarchy: a large calm
# title, medium section headings, small muted descriptions.

_FONT = {
    "display": (28, 600),
    "h1": (22, 600),
    "h2": (17, 600),
    "h3": (14, 600),
    "body": (13, 400),
    "bodyMed": (13, 500),
    "small": (12, 400),
    "caption": (11, 500),
    "mono": (12, 400),
}

# Tracking, in px, for the tiers that ask for it: the large sizes tighten and
# the uppercase caption opens, which is the same shape the reference scale uses
# and the reason a 28px title set at 0 looks loose beside 13px body. A tier that
# is not named here is at 0. Qt 5's stylesheet parser has no `letter-spacing`
# property — it accepts `font`, `font-family`, `font-size`, `font-style` and
# `font-weight` and nothing else — so these live here for
# `QFont.setLetterSpacing` to apply and are not emitted into the sheet.
TRACKING = {"display": -0.4, "h1": -0.3, "h2": -0.2, "caption": 0.4}

# Named one family at a time rather than as a CSS list where it matters: Qt 5
# hands `font-family` to QFont::setFamily, which takes the FIRST name in the
# list and then falls back through its own matching, not through the rest of
# the list — the old stack led with 'DM Sans', which is on no Windows machine
# this ships to, so every rule in the sheet asked for a font that was never
# there and got whatever `base_font()` had already set.
#
# Segoe UI, and not Segoe UI Variable, which is the newer face and the closer
# match on paper. Qt 5.15 registers one style for the variable face —
# `QFontDatabase.styles` answers ['Regular'] — so the scale's own three weights
# collapse into two. Measured through the sheet, at 17px, as the ink one string
# takes: Segoe UI Variable draws `font-weight: 400` and `500` identically
# (122091 units each) and only 600 differs (188592, and synthesised at that),
# while Segoe UI separates all three — 121149, 162135, 209576. A hierarchy
# carried on weight cannot be set in a face with one weight, so the hierarchy
# chooses the face. `tests/test_theme.py` re-measures both.
#
# Qt maps a CSS weight to its own 0-99 scale by dividing by eight, so the 600
# these tiers are specified at arrives as QFont::Bold and 500 as DemiBold. That
# is the register the scale wants — a drawn semibold for emphasis, bold for the
# heading tiers — and it is worth knowing before anyone reads 600 as semibold
# and lowers it.
_SANS = "'Segoe UI', 'Segoe UI Variable', sans-serif"
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

/* The shell's two rows. The first is a surface so the one bar in the app reads
   as chrome rather than as the top of the page — the audit found four screens
   with four different top bars, and the reason none of them read as the same
   object is that each was the page colour with a different height. The second
   row carries the sub-tabs and sits on the page, so a screen's own tabs read as
   belonging to the screen and not to the product. */
QWidget#app_bar {
    background-color: ${color.surface};
    border-bottom: 1px solid ${color.border.subtle};
}

QWidget#sub_bar {
    background-color: ${color.canvas};
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

/* The selected ground takes the outline with it. `border.default` measures
   1.48:1 on `surfaceActive` in dark and 1.66:1 in light, so a control that is
   pressed or checked lost its edge at exactly the moment it was being acted on
   — and no border token can fix that, because one that clears 3:1 on the
   selected ground has to sit either three times above it (L >= 0.65, a
   near-white hairline round every control in the app) or three times below it
   (L <= 0.028, which then fails 3:1 on `surface`). What does clear it is the
   ink the ground already carries: `text.onAccent` measures 4.50:1 on the dark
   selection and 13.97:1 on the light one, so on this ground the edge and the
   label are the same colour. */
QPushButton:pressed {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
    border-color: ${color.text.onAccent};
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
    border-color: ${color.text.onAccent};
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
    border-color: ${color.text.onAccent};
}

/* ── Tabs: selection is the louder signal ────────────────────────────────── */
/* A selected tab changes ground (4.25:1 on the page in dark) and gains a 2px
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

/* The bar exists to be read rather than clicked: it is where a keyboard user
   finds out that Ctrl+K opens the palette and Ctrl+3 goes to Outreach, because
   Qt writes an action's shortcut beside its name. Unstyled it arrives in the
   platform's own grey, which on the dark palette is a white strip across the
   top of the window. */
QMenuBar {
    background-color: ${color.surface};
    color: ${color.text.secondary};
    border-bottom: 1px solid ${color.border.subtle};
    font-size: ${font.small.size}px;
}

QMenuBar::item {
    padding: ${space.1}px ${space.3}px;
    border-radius: ${radius.sm}px;
    background: transparent;
}

QMenuBar::item:selected {
    background-color: ${color.surfaceHover};
    color: ${color.text.primary};
}

QMenuBar::item:pressed {
    background-color: ${color.surfaceActive};
    color: ${color.text.onAccent};
}

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

/* ── The command palette ─────────────────────────────────────────────────── */

/* The one place `scrim` is spent. It was defined in both palettes and used by
   nothing, so the app had no way to say "this is the thing you are answering"
   — and the palette covers its parent whole, so without a ground it would sit
   over a live screen with no edge to it. */
QWidget#command_scrim {
    background-color: ${color.scrim};
}

QFrame#command_card {
    background-color: ${color.raised};
    border: 1px solid ${color.border.default};
    border-radius: ${radius.lg}px;
}

/* The rows are painted by a delegate, so this says only where they sit: no
   well, no second border inside the card that already has one. */
QListWidget#command_list {
    background: transparent;
    border: none;
    padding: ${space.0}px;
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
   It is subordinate by measurement now — 3.83:1 on the page against a selection
   at 4.25:1. */

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
   the page, 3.83:1 in dark and 5.34:1 in light — so a ring in the same token
   repainted the border in the colour it already was and changed exactly zero
   pixels. The primary action was the one control in the app with no focus
   indicator at all, which is the defect this block exists to prevent.

   `accent.subtle` steps the other way instead: 2.79:1 against the fill in dark
   and 5.14:1 in light, far enough off it that it can never be mistaken for the
   selection beside it (1.27:1 on the page against a selection at 4.25:1) and
   still a
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
        self.wear(t)

    def wear(self, t: Theme) -> None:
        """Take `t`'s colours, in place of a second style being installed.

        `QApplication.setStyle` hands the style it replaces to `deleteLater`,
        and a deferred delete is only collected by an event loop — so a theme
        toggle that builds a new style each time leaves the old ones queued, and
        every one of them is asked to polish every widget in the process. The
        cost grows with each switch: measured on a window with 701 widgets, four
        changes in a row took 683ms, 695ms, 2070ms and 2609ms. Worn in place
        they are 683ms every time.
        """
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

    Which is why the scaling attribute is asked for only while there is no
    application to ignore it. `apply()` runs on every live theme and density
    change, long after the QApplication exists, and Qt answers a late attempt
    with `Attribute Qt::AA_EnableHighDpiScaling must be set before
    QCoreApplication is created` on stderr — one line per appearance change, 18
    in a sweep that changed the density fifteen times, and the only warning this
    app produces that is its own. A log that is all noise is a log nobody reads
    the one real line in. The ordering in `run()` is already right, so nothing
    about how the app scales changes here: what goes is a request Qt was
    throwing away.
    """
    if QApplication.instance() is None:
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
    for family in ("Segoe UI", "Segoe UI Variable", system.family()):
        if family in families:
            _BASE_FONT = QFont(family, size)
            break
    return QFont(_BASE_FONT)


# (application, its TickStyle, the theme it is wearing). One tuple rather than
# a lookup because Qt allows one QApplication at a time; the app is held with it
# so a process that outlives one — the test suite does not, but a future one
# might — cannot be handed a style belonging to an application that has gone.
_WORN: tuple = ()


def worn():
    """The theme `apply()` last put on the application, or None before any.

    `ui/icons.py` reads it. An icon is drawn on demand from wherever a widget
    is built, which is nowhere near the call that chose the appearance, and the
    alternative — handing every screen a theme so it can hand it to every icon
    — is how a palette ends up half-applied after a switch.
    """
    return _WORN[2] if _WORN else None


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

    Applying the theme the app already wears returns without touching anything,
    and `_WORN` is how it knows, because the obvious test does not work: once a
    stylesheet is set, `app.style()` answers with Qt's own QStyleSheetStyle
    wrapper and never with the TickStyle underneath it, so the `isinstance`
    guard this replaces was always False and every call did the full work.
    `setStyleSheet` repolishes every widget alive in the process — 550ms once
    four screens are built — and the geometry sweeps call this once per helper,
    a few hundred times per run.
    """
    global _WORN

    sheet = stylesheet(t)
    style = (_WORN[1] if _WORN and _WORN[0] is app
             and not sip.isdeleted(_WORN[1]) else None)
    if style is not None and _WORN[2] == t and app.styleSheet() == sheet:
        return

    enable_high_dpi()
    app.setFont(base_font())
    if style is None:
        style = TickStyle(QStyleFactory.create("Fusion"), t)
        app.setStyle(style)
    else:
        style.wear(t)

    palette = app.palette()
    palette.setColor(QPalette.PlaceholderText, QColor(t.color["text.tertiary"]))
    palette.setColor(QPalette.Window, QColor(t.color["canvas"]))
    palette.setColor(QPalette.WindowText, QColor(t.color["text.primary"]))
    palette.setColor(QPalette.ToolTipBase, QColor(t.color["raised"]))
    palette.setColor(QPalette.ToolTipText, QColor(t.color["text.primary"]))
    app.setPalette(palette)
    app.setStyleSheet(sheet)
    _WORN = (app, style, t)
