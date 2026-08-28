"""The application's icons, drawn rather than shipped.

The app contains none. Every affordance in it is a word: "Add", "Remove",
"Reset", "Start Scraping", "← Back", and a services list whose group
headings are told apart from their rows by weight alone. A toolbar of words is
readable and it is also flat — nothing in it can be found by shape, scanned for
at a glance, or recognised from across a desk, and a table cell that has to say
"external link" in text spends a column on saying it.

Drawn here for one reason and it is the same reason `ui/theme.py` generates the
sheet: this ships as a one-file PyInstaller executable, so `image: url(...)`,
a `.qrc` and an icon font are all unavailable — QPixmap resolves a path through
the filesystem, and there is no filesystem beside a frozen exe. What is
available is QPainter, which is also the only option that is resolution
independent: every icon here is a path in a 0..1 unit box, scaled to whatever
pixel size the theme asks for and stroked with a pen whose width is a fraction
of that box, so the same declaration is crisp at 12px, at 24px and at 200%
Windows scaling.

One set, one weight, one corner radius, one optical size. The register is
Apple's SF Symbols: an even 2/24 stroke, round caps and round joins, generous
negative space, and no icon carrying detail it cannot hold at 16px. Nothing
here is an emoji or a Unicode glyph standing in for a drawing -- those inherit
the text font, ignore the palette, and on Windows arrive in colour whatever the
theme says.

Colour comes from a theme token and never from a literal. `tone` names a role
the same way `ui/components.py` names one -- a text tier, or a semantic
family's own readable ink -- so an icon beside a label is the colour of the
label, and both change together when the theme does. Size is a spacing token,
so an icon is a grid value like everything else.

Rendered pixmaps are cached per (name, tone, size, device pixel ratio), because
a table with an icon in a column repaints these on every scroll of every row:
`QIcon` asks its source for a pixmap each time it paints one, and drawing
thirty paths per cell per frame is a cost paid for nothing when the answer is
the same pixmap every time. The theme is part of the key by way of the colour
it resolves to, so switching palette invalidates the entries it should and
keeps the ones it should.
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QApplication

from ui import theme as _theme

# ── The register ─────────────────────────────────────────────────────────────
# Every number below is a fraction of the icon's own box, so the whole set
# scales as one drawing. The stroke is 2 units at 24 — SF Symbols' Regular
# weight — and the corner radius 3 at 24, which is the only radius in the set:
# a rounded join gives every polyline the same corner for free, so a shape
# never needs one of its own.

STROKE = 2.0 / 24.0
CORNER = 3.0 / 24.0

# ── Tone and size, both from the palette ─────────────────────────────────────
# The tone names are `ui/components.py`'s tone names and resolve the same way,
# so `icon("mail", tone="danger")` and a danger-toned label beside it are one
# colour. `onAccent` is here because an icon on a filled button is the one
# place the text ramp is the wrong answer.

_TEXT_TONES = ("primary", "secondary", "tertiary", "disabled")
FAMILIES = ("accent", "success", "warning", "danger", "info")
TONES = _TEXT_TONES + ("onAccent",) + FAMILIES

# Icons sit in the line of text or in a control, so their sizes are the grid's
# own: 12, 16, 20 and 24 at every density, named rather than passed as numbers.
SIZES = {"xs": "3", "sm": "4", "md": "5", "lg": "6"}

_CURRENT = None


def use_theme(t) -> None:
    """Name the theme every icon drawn from now on takes its colour from."""
    global _CURRENT
    _CURRENT = t


def active_theme():
    """The theme in force: the one named here, else the one the app wears.

    The second half is what makes this work with no call site at all. Changing
    the appearance already goes through `theme.apply()`, which records what it
    applied, so an icon follows the palette the window is actually wearing
    without every screen having to be told about it as well.
    """
    if _CURRENT is not None:
        return _CURRENT
    return _theme.worn() or _theme.theme()


def _colour(t, tone: str) -> str:
    if tone in _TEXT_TONES:
        return t.color["text.%s" % tone]
    if tone == "onAccent":
        return t.color["text.onAccent"]
    if tone in FAMILIES:
        return t.color["%s.text" % tone]
    raise KeyError("no such tone: %r" % (tone,))


def _pixels(t, size: str) -> int:
    if size not in SIZES:
        raise KeyError("no such size: %r" % (size,))
    return t.space[SIZES[size]]


def pixels(size: str = "md") -> int:
    """What a size name is worth right now, for `setIconSize`.

    A QIcon holds one drawing and Qt scales it to whatever a widget asks for,
    so a button whose `iconSize` disagrees with the size the icon was drawn at
    gets a resampled bitmap — the one way this set can end up soft. A caller
    that sets both from here cannot disagree with itself.
    """
    return _pixels(active_theme(), size)


def _ratio() -> float:
    """The device pixel ratio to render at, and 1.0 with no application yet."""
    app = QApplication.instance()
    return float(app.devicePixelRatio()) if app is not None else 1.0


# ── The drawing surface ──────────────────────────────────────────────────────


class _Sketch:
    """One icon's geometry in a 0..1 box: strokes in one path, dots in another.

    Two paths and not one because a dot — the tittle on an `i`, the pupil of an
    eye — is the one mark in the set that is filled, and a filled sub-path
    inside a stroked path would be stroked as well.
    """

    def __init__(self):
        self.stroke = QPainterPath()
        self.fill = QPainterPath()

    def line(self, x1, y1, x2, y2):
        self.stroke.moveTo(x1, y1)
        self.stroke.lineTo(x2, y2)

    def poly(self, points, *, close=False):
        self.stroke.moveTo(*points[0])
        for point in points[1:]:
            self.stroke.lineTo(*point)
        if close:
            self.stroke.closeSubpath()

    def rect(self, x, y, width, height, radius=CORNER):
        self.stroke.addRoundedRect(QRectF(x, y, width, height), radius, radius)

    def circle(self, cx, cy, radius):
        self.stroke.addEllipse(QPointF(cx, cy), radius, radius)

    def dot(self, cx, cy, radius):
        self.fill.addEllipse(QPointF(cx, cy), radius, radius)

    def arc(self, cx, cy, radius, start, sweep):
        """An arc in degrees, Qt's way round: 0 is east and positive is up."""
        box = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        self.stroke.arcMoveTo(box, start)
        self.stroke.arcTo(box, start, sweep)

    def head(self, tip, heading, length=0.16, spread=140.0):
        """An arrowhead: two strokes back from `tip`, either side of `heading`.

        Written as a rotation of the direction the line is travelling rather
        than as four hand-placed points, so every arrow in the set opens by the
        same angle and no two of them disagree by a degree.
        """
        for turn in (spread, -spread):
            angle = math.radians(heading + turn)
            self.line(tip[0], tip[1],
                      tip[0] + math.cos(angle) * length,
                      tip[1] - math.sin(angle) * length)


# ── The set ──────────────────────────────────────────────────────────────────
# What the app actually needs: the four screens, the actions on them, the
# states a row can be in, and the marks a table and a menu ask for.


def _search(s):
    s.circle(0.44, 0.44, 0.26)
    s.line(0.63, 0.63, 0.83, 0.83)


def _table(s):
    s.rect(0.14, 0.18, 0.72, 0.64)
    s.line(0.14, 0.40, 0.86, 0.40)
    s.line(0.46, 0.40, 0.46, 0.82)


def _columns(s):
    s.rect(0.14, 0.18, 0.72, 0.64)
    s.line(0.38, 0.18, 0.38, 0.82)
    s.line(0.62, 0.18, 0.62, 0.82)


def _send(s):
    s.poly([(0.87, 0.13), (0.13, 0.45), (0.45, 0.55), (0.55, 0.87)], close=True)
    s.line(0.87, 0.13, 0.45, 0.55)


def _gear(s):
    # The teeth start inside the rim rather than clear of it, and that 0.04 of
    # overlap is the whole icon: a small circle with eight rays around it at a
    # distance is the brightness control on every platform there is, and
    # settings and brightness are two things a toolbar cannot afford to
    # confuse. Attached, they are teeth on a wheel.
    s.circle(0.5, 0.5, 0.28)
    s.circle(0.5, 0.5, 0.11)
    for step in range(8):
        angle = math.radians(step * 45.0)
        s.line(0.5 + math.cos(angle) * 0.24, 0.5 - math.sin(angle) * 0.24,
               0.5 + math.cos(angle) * 0.40, 0.5 - math.sin(angle) * 0.40)


def _mail(s):
    s.rect(0.12, 0.24, 0.76, 0.52)
    s.poly([(0.17, 0.30), (0.5, 0.55), (0.83, 0.30)])


def _document(s):
    s.poly([(0.26, 0.12), (0.54, 0.12), (0.76, 0.34), (0.76, 0.88),
            (0.26, 0.88)], close=True)
    s.poly([(0.54, 0.12), (0.54, 0.34), (0.76, 0.34)])


def _person(s):
    s.circle(0.5, 0.33, 0.16)
    path = s.stroke
    path.moveTo(0.19, 0.87)
    path.cubicTo(0.19, 0.62, 0.81, 0.62, 0.81, 0.87)


def _chart(s):
    s.line(0.28, 0.80, 0.28, 0.52)
    s.line(0.50, 0.80, 0.50, 0.32)
    s.line(0.72, 0.80, 0.72, 0.44)


def _filter(s):
    s.line(0.16, 0.30, 0.84, 0.30)
    s.line(0.26, 0.50, 0.74, 0.50)
    s.line(0.38, 0.70, 0.62, 0.70)


def _eye(s):
    path = s.stroke
    path.moveTo(0.10, 0.50)
    path.quadTo(0.5, 0.16, 0.90, 0.50)
    path.quadTo(0.5, 0.84, 0.10, 0.50)
    s.dot(0.5, 0.50, 0.085)


def _plus(s):
    s.line(0.5, 0.20, 0.5, 0.80)
    s.line(0.20, 0.5, 0.80, 0.5)


def _minus(s):
    s.line(0.20, 0.5, 0.80, 0.5)


def _pencil(s):
    s.poly([(0.16, 0.84), (0.23, 0.63), (0.64, 0.22), (0.78, 0.36),
            (0.37, 0.77)], close=True)
    s.line(0.58, 0.28, 0.72, 0.42)


def _copy(s):
    s.rect(0.32, 0.12, 0.54, 0.54)
    s.rect(0.14, 0.34, 0.54, 0.54)


def _duplicate(s):
    s.rect(0.16, 0.16, 0.68, 0.68)
    s.line(0.5, 0.33, 0.5, 0.67)
    s.line(0.33, 0.5, 0.67, 0.5)


def _trash(s):
    s.line(0.14, 0.26, 0.86, 0.26)
    s.poly([(0.37, 0.26), (0.37, 0.14), (0.63, 0.14), (0.63, 0.26)])
    s.poly([(0.23, 0.26), (0.28, 0.86), (0.72, 0.86), (0.77, 0.26)])


def _reset(s):
    # The head goes at the end of the sweep and not at its start: an arrowhead
    # on the end the pen began at is an arrow pointing back down its own line,
    # which reads as a hook rather than as a turn.
    start, sweep = 60.0, -300.0
    s.arc(0.5, 0.5, 0.31, start, sweep)
    end = math.radians(start + sweep)
    s.head((0.5 + math.cos(end) * 0.31, 0.5 - math.sin(end) * 0.31),
           start + sweep - 90.0, length=0.20)


def _play(s):
    s.poly([(0.32, 0.19), (0.81, 0.50), (0.32, 0.81)], close=True)


def _pause(s):
    s.line(0.37, 0.22, 0.37, 0.78)
    s.line(0.63, 0.22, 0.63, 0.78)


def _stop(s):
    s.rect(0.25, 0.25, 0.50, 0.50)


def _check(s):
    s.poly([(0.21, 0.52), (0.41, 0.72), (0.79, 0.28)])


def _warning(s):
    s.poly([(0.5, 0.13), (0.91, 0.83), (0.09, 0.83)], close=True)
    s.line(0.5, 0.41, 0.5, 0.60)
    s.dot(0.5, 0.72, 0.048)


def _error(s):
    s.circle(0.5, 0.5, 0.34)
    s.line(0.36, 0.36, 0.64, 0.64)
    s.line(0.64, 0.36, 0.36, 0.64)


def _info(s):
    s.circle(0.5, 0.5, 0.34)
    s.line(0.5, 0.46, 0.5, 0.70)
    s.dot(0.5, 0.32, 0.048)


def _chevron(heading):
    """One chevron, pointing whichever way it is asked to.

    Four drawings from one definition, because four hand-written chevrons is
    four chances for one of them to open by a different angle.
    """
    def draw(s, heading=heading):
        angle = math.radians(heading)
        cos, sin = math.cos(angle), -math.sin(angle)
        points = []
        for along, across in ((-0.13, -0.26), (0.13, 0.0), (-0.13, 0.26)):
            points.append((0.5 + along * cos - across * sin,
                           0.5 + along * sin + across * cos))
        s.poly(points)
    return draw


def _external(s):
    s.poly([(0.58, 0.20), (0.22, 0.20), (0.22, 0.78), (0.80, 0.78),
            (0.80, 0.44)])
    s.line(0.50, 0.50, 0.81, 0.19)
    s.head((0.81, 0.19), 45.0, length=0.20)


def _clock(s):
    s.circle(0.5, 0.5, 0.34)
    s.line(0.5, 0.50, 0.5, 0.29)
    s.line(0.5, 0.50, 0.66, 0.58)


def _calendar(s):
    s.rect(0.13, 0.21, 0.74, 0.67)
    s.line(0.13, 0.42, 0.87, 0.42)
    s.line(0.34, 0.12, 0.34, 0.28)
    s.line(0.66, 0.12, 0.66, 0.28)


_DRAW = {
    # The four screens and the work each one does.
    "search": _search,
    "table": _table,
    "columns": _columns,
    "send": _send,
    "gear": _gear,
    "mail": _mail,
    "document": _document,
    "person": _person,
    "chart": _chart,
    "filter": _filter,
    "eye": _eye,
    # What a screen lets you do to a row, a template or a mailbox.
    "plus": _plus,
    "minus": _minus,
    "pencil": _pencil,
    "copy": _copy,
    "duplicate": _duplicate,
    "trash": _trash,
    "reset": _reset,
    # A run, and where it is.
    "play": _play,
    "pause": _pause,
    "stop": _stop,
    # What a row or a message came out as.
    "check": _check,
    "warning": _warning,
    "error": _error,
    "info": _info,
    "clock": _clock,
    "calendar": _calendar,
    # Chrome.
    "chevron-up": _chevron(90.0),
    "chevron-down": _chevron(-90.0),
    "chevron-left": _chevron(180.0),
    "chevron-right": _chevron(0.0),
    "external": _external,
}

ICONS = tuple(_DRAW)


# ── Rendering, once per (name, tone, size, ratio) ────────────────────────────

_PIXMAPS: dict = {}
_ICONS: dict = {}


def _render(name: str, colour: str, px: int, ratio: float) -> QPixmap:
    """One icon, drawn at `px` logical pixels for a display at `ratio`."""
    side = max(1, int(round(px * ratio)))
    canvas = QPixmap(side, side)
    canvas.setDevicePixelRatio(ratio)
    canvas.fill(Qt.transparent)

    sketch = _Sketch()
    _DRAW[name](sketch)

    ink = QColor(colour)
    pen = QPen(ink, STROKE)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    # The whole set is drawn in a unit box and scaled here, which is what makes
    # one definition sharp at every size: the pen scales with the transform, so
    # its width stays the same fraction of the icon instead of being a constant
    # that is heavy at 12px and hairline at 24.
    painter.scale(side, side)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(sketch.stroke)
    if not sketch.fill.isEmpty():
        painter.setPen(Qt.NoPen)
        painter.setBrush(ink)
        painter.drawPath(sketch.fill)
    painter.end()
    return canvas


def pixmap(name: str, *, tone: str = "secondary", size: str = "md") -> QPixmap:
    """The icon as a pixmap, for the places a QIcon is the wrong shape."""
    if name not in _DRAW:
        raise KeyError("no such icon: %r" % (name,))
    t = active_theme()
    colour, px, ratio = _colour(t, tone), _pixels(t, size), _ratio()
    key = (name, tone, size, ratio, colour, px)
    found = _PIXMAPS.get(key)
    if found is None:
        found = _render(name, colour, px, ratio)
        _PIXMAPS[key] = found
    return found


def icon(name: str, *, tone: str = "secondary", size: str = "md") -> QIcon:
    """One icon from `ICONS`, in a theme colour, at a grid size."""
    if name not in _DRAW:
        raise KeyError("no such icon: %r" % (name,))
    t = active_theme()
    colour, px, ratio = _colour(t, tone), _pixels(t, size), _ratio()
    key = (name, tone, size, ratio, colour, px)
    found = _ICONS.get(key)
    if found is None:
        found = QIcon(pixmap(name, tone=tone, size=size))
        _ICONS[key] = found
    return found


def forget() -> None:
    """Drop every cached pixmap: for tests, and for a palette edited live."""
    _PIXMAPS.clear()
    _ICONS.clear()
