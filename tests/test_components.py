"""The component library, measured — widths, heights, ratios and characters.

Every claim `ui/components.py` makes is a number, so every assertion here is a
number taken off a built widget rather than off the code that built it. That
distinction is the whole point: a QSS rule can be beaten by another one, a
layout can hand a widget less than it asked for, and a cap written in characters
only means something once a real font has been measured. The audit these
components exist to answer — 20 of 20 rows with a clipped Email column, 11
elided cells with no tooltip, one button at six heights, 25 of 29 labels past 90
characters per line — was produced by a codebase whose authors were sure their
widths were fine.

Four groups of test here, and they prove different kinds of thing.

The source-discipline group reads `ui/components.py` as text and as a syntax
tree. It is the only way to prove a negative — that no hex literal, no font
size, no spacing number and no fixed pixel height was written anywhere in the
file — and a negative is exactly what the contract asks for.

The measurement group builds widgets under a real font at real window sizes and
reads back what they became. The three window sizes are the ones that matter:
880px is the minimum `MainWindow` allows, 1080px is what it opens at, and
2560px is where the audit's two critical column findings appear.

The colour group computes WCAG contrast from the tokens the components chose,
written out in full below rather than imported, for the same reason
`tests/test_theme.py` writes it out: maths that agrees with itself proves
nothing.

The pixel group is one test and it earns its place. Qt hands a widget's own
stylesheet priority over an ancestor's *regardless of specificity*, so a rule
that reads correctly can put nothing on the screen — a control here that paints
its own border silently loses the application sheet's focus ring. That one is
read back off the painted image.

`tests/conftest.py` has already pointed `core.settings` and `core.templates` at
a temp profile before this imports, and nothing here touches either — but the
components are built through the same `theme.apply()` the app uses, so the
numbers are the app's numbers.
"""
import ast
import inspect
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Without a font directory the offscreen platform has no families to measure
# and every width below is zero, which passes any "less than" assertion in the
# file for entirely the wrong reason.
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from PyQt5.QtCore import QEvent, Qt  # noqa: E402
from PyQt5.QtGui import QHelpEvent, QTextLayout  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QFrame, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QToolButton, QVBoxLayout, QWidget,
)

from ui import components as C  # noqa: E402
from ui import theme as TH  # noqa: E402

AA, COMPONENT = 4.5, 3.0

# What `MainWindow` allows the user to drag down to, what it opens at, and the
# width the audit measured its two critical column findings at.
MINIMUM, DEFAULT, WIDE = 880, 1080, 2560

_APP = None


def _app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture(autouse=True)
def _sweep():
    """Destroy every window a test built, before the next test changes theme.

    `theme.apply()` repolishes every widget alive in the process, and the cost
    is linear in how many there are: 0.013s with none alive, 0.50s at 900 and
    1.81s at 1800. This file switches theme on nearly every one of its 179
    tests, so widgets left behind are paid for by every test after them and by
    every other file in the run.

    `deleteLater` alone does not do it. `processEvents()` does not deliver
    `DeferredDelete` — Qt holds those until the event loop unwinds to the level
    that posted them, and there is no loop running under pytest — so the widgets
    stay alive and the sweep silently does nothing. That one line is the whole
    difference between this file taking 59 seconds and taking 2.8.
    """
    yield
    app = _app()
    for widget in list(app.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture(params=["dark", "light"])
def look(request):
    """A themed application, and the theme the components are reading from."""
    app = _app()
    t = TH.theme(request.param, "comfortable")
    TH.apply(app, t)
    C.use_theme(t)
    yield t
    C.forget()


@pytest.fixture
def dark():
    app = _app()
    t = TH.theme("dark", "comfortable")
    TH.apply(app, t)
    C.use_theme(t)
    yield t
    C.forget()


def _shown(widget: QWidget, width: int, height: int = 700) -> QWidget:
    """A widget realised inside a window of a given size, laid out and settled."""
    host = QWidget()
    box = QVBoxLayout(host)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(widget)
    host.resize(width, height)
    host.show()
    _app().processEvents()
    widget._test_host = host
    return widget


# ── Colour maths, written out ────────────────────────────────────────────────


def _channels(colour: str) -> tuple:
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", colour), colour
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _linear(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    r, g, b = (_linear(c) for c in _channels(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(one: str, other: str) -> float:
    first, second = _luminance(one) + 0.05, _luminance(other) + 0.05
    return max(first, second) / min(first, second)


# ── Source discipline ────────────────────────────────────────────────────────

_SOURCE_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ui", "components.py")
_SOURCE = open(_SOURCE_PATH, encoding="utf-8").read()
_TREE = ast.parse(_SOURCE)

# Every call that fixes a geometry. A numeric literal reaching any of them is a
# number that did not come out of the theme, which is how the app ended up with
# nine radii and one button at six heights.
_GEOMETRY = frozenset((
    "setContentsMargins", "setSpacing", "addSpacing", "addStrut",
    "setFixedHeight", "setFixedWidth", "setFixedSize",
    "setMinimumHeight", "setMinimumWidth", "setMaximumHeight",
    "setMaximumWidth", "setMinimumSize", "setMaximumSize",
    "setDefaultSectionSize", "setMinimumSectionSize",
    "setPointSize", "setPixelSize", "setLetterSpacing",
))

# The one length the theme has no token for: the width of a rule. It is
# declared once, at the top of the module, and nothing else may be a literal.
_ALLOWED_NAMES = frozenset(("BORDER",))


def _docstrings(tree) -> set:
    """The string nodes that are documentation, not values Qt will ever read."""
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            found.add(id(first.value))
    return found


def _names_bound_to_numbers(tree) -> set:
    """Every local or module name in the file that holds a bare number.

    This is what stops the rule below being satisfied by moving the literal one
    line up: `height = 34` then `setFixedHeight(height)` is the same defect
    wearing a name, and it is exactly how a screen file grows a private
    `_BUTTON_HEIGHT`.
    """
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
                and not isinstance(value.value, bool)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    return bound


_DOCSTRINGS = _docstrings(_TREE)
_NUMERIC_NAMES = _names_bound_to_numbers(_TREE)


def test_no_hex_literal_in_the_source():
    """Not one colour is written down here; they all come from the palette."""
    found = re.findall(r"#[0-9A-Fa-f]{6}\b", _SOURCE)
    assert found == [], found


def test_no_named_or_functional_colour_in_the_source():
    """No `white`, no `rgb(...)`, no second way to say a colour."""
    banned = re.compile(
        r"\brgba?\s*\(|[\"'](?:white|black|red|green|blue|gray|grey|silver)[\"']",
        re.IGNORECASE)
    assert banned.search(_SOURCE) is None, banned.search(_SOURCE).group(0)


def test_no_pixel_number_in_any_string():
    """Every `px` in every emitted rule is interpolated, never written.

    Docstrings are excluded and only docstrings are: the prose here quotes the
    audit's own measurements — 240px, 1800px, 26/28/30/32/34/40 — and a rule Qt
    will parse is a different thing from a sentence explaining why the rule
    exists.
    """
    literals = [node.value for node in ast.walk(_TREE)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in _DOCSTRINGS]
    offenders = [s for s in literals if re.search(r"\d+\s*px", s)]
    assert offenders == [], offenders


def test_no_numeric_literal_reaches_a_geometry_call():
    """Every margin, spacing, height and font size is a token lookup."""
    banned = _NUMERIC_NAMES - _ALLOWED_NAMES
    offenders = []
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _GEOMETRY:
            continue
        for argument in list(node.args) + [k.value for k in node.keywords]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, (int, float)):
                offenders.append("%s(... %r ...) line %d"
                                 % (node.func.attr, argument.value, node.lineno))
            if isinstance(argument, ast.Name) and argument.id in banned:
                offenders.append("%s(%s) line %d"
                                 % (node.func.attr, argument.id, node.lineno))
    assert offenders == [], offenders


def test_every_object_name_this_file_sets_has_a_rule_in_the_sheet():
    """An objectName the sheet does not style is a promise nothing keeps.

    `tests/test_theme.py` scans every file under `ui/` for exactly this, so a
    component inventing an id would break that test rather than this one. It is
    asserted here as well because this is the file that would do the inventing,
    and because the rule is the reason components identify themselves with a
    dynamic property instead: a widget that paints itself does not need an id,
    and an id it does not need is the next screen's excuse for a private rule.
    """
    names = set(re.findall(r'setObjectName\("([^"]+)"\)', _SOURCE))
    sheet = TH.stylesheet(TH.theme())
    unstyled = sorted(name for name in names if "#%s" % name not in sheet)
    assert unstyled == [], unstyled


def _focusable(widget) -> bool:
    return widget.focusPolicy() != Qt.NoFocus


_SELF_PAINTED = [
    ("ghost button", lambda: C.button("Undo", kind="ghost")),
    ("icon button", lambda: C.icon_button("x", tooltip="Dismiss")),
    ("chip", lambda: C.chip("company", on_click=lambda: None)),
]


def _top_edge(widget) -> str:
    """The colour actually painted on the widget's top border, as #RRGGBB.

    Painted, not declared. Qt's cascade here is not the CSS one — a widget's own
    stylesheet beats an ancestor's regardless of specificity, so an app sheet
    saying `QPushButton:focus { border: accent }` loses to a widget sheet saying
    `QPushButton { border: transparent }` — and a rule that reads correctly can
    still put nothing on the screen.
    """
    image = widget.grab().toImage()
    pixel = image.pixel(widget.width() // 2, 0)
    if (pixel >> 24) & 0xFF == 0:
        return ""
    return "#%06X" % (pixel & 0xFFFFFF)


@pytest.mark.parametrize("name,make", _SELF_PAINTED, ids=[n for n, _ in _SELF_PAINTED])
def test_a_control_that_paints_its_own_border_paints_its_own_focus_ring(
        look, name, make):
    """Every control here that writes its own border writes its own ring.

    Or it is the one thing in the app a keyboard can reach and not see: the
    application sheet has no QToolButton rule at all, and its `QPushButton:focus`
    is beaten by any local border rule.
    """
    host = QWidget()
    box = QVBoxLayout(host)
    park = QLineEdit()
    box.addWidget(park)
    widget = make()
    box.addWidget(widget)
    box.addStretch()
    host.resize(DEFAULT, 400)
    host.show()
    park.setFocus()
    _app().processEvents()

    assert _focusable(widget) is True
    resting = _top_edge(widget)
    widget.setFocus()
    _app().processEvents()
    assert widget.hasFocus() is True
    assert _top_edge(widget) == look.color["accent.border"].upper(), name
    assert resting != look.color["accent.border"].upper(), name


def test_the_ring_never_outweighs_the_selection_it_sits_beside(look):
    """The audit's critical finding: a white ring at 17.01:1 beside a tab at 1.50:1."""
    ring = _contrast(look.color["canvas"], look.color["accent.border"])
    selection = _contrast(look.color["canvas"], look.color["surfaceActive"])
    assert ring < selection, (round(ring, 2), round(selection, 2))
    assert look.color["accent.border"] != look.color["text.primary"]


def test_the_ring_never_changes_a_control_size(look):
    """Held at one hairline in every state, so nothing moves when focus arrives."""
    for name, make in _SELF_PAINTED:
        widget = _shown(make(), DEFAULT)
        before = (widget.width(), widget.height())
        widget.setFocus()
        _app().processEvents()
        assert (widget.width(), widget.height()) == before, name
        assert _border_widths(widget.styleSheet()) == {C.BORDER}, name


def _border_widths(sheet: str) -> set:
    """Every border width the sheet names — radius is a corner, not a width."""
    pattern = r"\bborder(?:-(?:top|left|right|bottom))?:\s*(\d+)px"
    return {int(n) for n in re.findall(pattern, sheet)}


def test_the_only_length_constant_in_the_file_is_the_hairline():
    """Four numbers are declared at module level and only one is a length.

    `MAX_TOASTS` and `SIZE_SAMPLE_ROWS` are counts and `ACTION_DWELL` is
    milliseconds; none of them is a pixel, and the theme has a token for none of
    them. A fifth entry appearing here is a length that has escaped
    `ui/theme.py`.
    """
    numbers = {}
    for node in _TREE.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, (int, float)) \
                and not isinstance(node.value.value, bool):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    numbers[target.id] = node.value.value
    assert set(numbers) == {"BORDER", "MAX_TOASTS", "ACTION_DWELL",
                            "SIZE_SAMPLE_ROWS"}, numbers
    assert numbers["BORDER"] == 1


def test_the_one_allowed_constant_is_a_hairline():
    """`BORDER` is the exception the test above grants, and it is 1px."""
    assert C.BORDER == 1


def test_every_component_the_contract_names_exists():
    """Section 2 of the contract, name by name."""
    named = ("screen_header", "section_label", "card", "divider", "heading",
             "body_label", "hint", "button", "icon_button", "text_field",
             "select", "toggle", "search_field", "chip", "table", "status_pill",
             "score_badge", "stat_tile", "empty_state", "loading_state",
             "error_state", "Toaster", "confirm")
    missing = [name for name in named if not hasattr(C, name)]
    assert missing == [], missing


def test_every_keyword_the_contract_names_is_accepted(dark):
    """Called with the contract's full keyword list, each one still builds."""
    calls = {
        "screen_header": lambda: C.screen_header(
            "T", subtitle="s", actions=(), tabs=("a", "b"), on_tab=lambda i: None),
        "card": lambda: C.card(title="t", subtitle="s", body=None, actions=()),
        "heading": lambda: C.heading("t", level="h1"),
        "body_label": lambda: C.body_label("t", tone="secondary", max_chars=80),
        "button": lambda: C.button("t", kind="primary", size="md", icon=None,
                                   on_click=lambda: None),
        "icon_button": lambda: C.icon_button("x", tooltip="t", size="md"),
        "text_field": lambda: C.text_field(placeholder="p", label="l", help="h",
                                           error="e", secret=True),
        "select": lambda: C.select(["a"], label="l", help="h"),
        "toggle": lambda: C.toggle("t", help="h"),
        "chip": lambda: C.chip("t", on_click=lambda: None, removable=True),
        "table": lambda: C.table(["A"], density="compact", sortable=False),
        "stat_tile": lambda: C.stat_tile("l", 1, tone="accent", hint="h"),
        "empty_state": lambda: C.empty_state(title="t", body="b", action="a",
                                             on_action=lambda: None),
        "loading_state": lambda: C.loading_state(label="l"),
        "error_state": lambda: C.error_state(title="t", body="b",
                                             retry=lambda: None),
    }
    for name, call in calls.items():
        assert call() is not None, name
    signature = inspect.signature(C.confirm)
    for keyword in ("title", "body", "confirm_text", "danger", "remember_key"):
        assert keyword in signature.parameters, keyword
    show = inspect.signature(C.Toaster.show)
    for keyword in ("tone", "action", "on_action", "timeout"):
        assert keyword in show.parameters, keyword


# ── Types the contract promises ──────────────────────────────────────────────


def test_each_factory_returns_the_type_the_contract_declares(look):
    assert isinstance(C.screen_header("MapHarvest"), QWidget)
    assert isinstance(C.section_label("Leads"), QLabel)
    assert isinstance(C.card(title="Template"), QFrame)
    assert isinstance(C.divider(), QFrame)
    assert isinstance(C.heading("Results"), QLabel)
    assert isinstance(C.body_label("copy"), QLabel)
    assert isinstance(C.hint("note"), QLabel)
    assert isinstance(C.button("Save"), QPushButton)
    assert isinstance(C.icon_button("x", tooltip="Remove"), QToolButton)
    assert isinstance(C.text_field(), QWidget)
    assert isinstance(C.select(["a"]), QWidget)
    assert isinstance(C.toggle("Dry run"), QCheckBox)
    assert isinstance(C.search_field(), QLineEdit)
    assert isinstance(C.chip("company"), QWidget)
    assert isinstance(C.table(["A"]), QTableWidget)
    assert isinstance(C.status_pill("sent"), QWidget)
    assert isinstance(C.score_badge(70), QWidget)
    assert isinstance(C.stat_tile("Sent", 4), QFrame)
    assert isinstance(C.empty_state(title="t", body="b"), QWidget)
    assert isinstance(C.loading_state(), QWidget)
    assert isinstance(C.error_state(title="t", body="b"), QWidget)


# ── Heights: one per size token, per density ─────────────────────────────────


@pytest.mark.parametrize("density", ["comfortable", "compact"])
@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_a_button_is_exactly_its_size_token(density, size):
    """The finding this closes: `QPushButton#outlined` at 26, 28, 30, 32, 34, 40."""
    app = _app()
    t = TH.theme("dark", density)
    TH.apply(app, t)
    C.use_theme(t)
    btn = _shown(C.button("Start Outreach", size=size), DEFAULT)
    assert btn.height() == t.control[size], (density, size, btn.height())


def test_one_kind_of_button_renders_at_one_height(dark):
    """Every kind at the same size token measures the same, colour aside."""
    heights = {kind: _shown(C.button("Go", kind=kind, size="md"), DEFAULT).height()
               for kind in ("primary", "secondary", "ghost", "danger",
                            "danger_primary")}
    assert set(heights.values()) == {dark.control["md"]}, heights


def test_other_controls_take_their_height_from_a_token(look):
    field = C.text_field(placeholder="you@gmail.com")
    secret = C.text_field(secret=True)
    assert _shown(field.edit, DEFAULT).height() == look.control["md"]
    assert _shown(secret.edit.edit, DEFAULT).height() == look.control["md"]
    assert _shown(secret.edit.toggle, DEFAULT).height() == look.control["md"]
    assert _shown(C.search_field(), DEFAULT).height() == look.control["sm"]
    assert _shown(C.status_pill("sent"), DEFAULT).height() == look.control["xs"]
    assert _shown(C.score_badge(72), DEFAULT).height() == look.control["xs"]
    assert _shown(C.chip("company"), DEFAULT).height() == look.control["xs"]
    header = _shown(C.screen_header("MapHarvest"), DEFAULT)
    assert header.height() == look.control["header"]


def test_the_icon_button_is_square_at_its_token(look):
    btn = _shown(C.icon_button("x", tooltip="Remove", size="sm"), DEFAULT)
    assert (btn.width(), btn.height()) == (look.control["sm"], look.control["sm"])


# ── Type: a real scale, and a capped measure ─────────────────────────────────


@pytest.mark.parametrize("level", ["display", "h1", "h2", "h3"])
def test_a_heading_renders_at_its_tier(look, level):
    """The audit's finding: the largest text in the app was 15px."""
    label = _shown(C.heading("Outreach", level), DEFAULT)
    label.ensurePolished()
    assert label.font().pixelSize() == look.font[level][0]


def test_the_heading_tiers_step_rather_than_march(look):
    sizes = [look.font[level][0] for level in ("h3", "h2", "h1", "display")]
    ratios = [b / a for a, b in zip(sizes, sizes[1:])]
    assert min(ratios) >= 1.14, ratios


PROSE = ("Every address here is permanently excluded from planning and sending, "
         "follow-ups included. Remove one only if the person asked to be "
         "contacted again, and nothing already queued is re-sent on its own. ") * 3


def _characters_per_line(label: QLabel) -> int:
    """How many characters land on the longest line the label actually draws.

    `QTextLine.textLength()` counts the space the break consumed, so the slice
    is stripped before it is counted: what is being measured is the characters a
    reader sees, not the characters the layout consumed.
    """
    label.ensurePolished()
    text = label.text()
    layout = QTextLayout(text, label.font())
    width = label.contentsRect().width()
    longest = 0
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        cut = text[line.textStart():line.textStart() + line.textLength()]
        longest = max(longest, len(cut.rstrip()))
    layout.endLayout()
    return longest


def test_body_label_holds_the_measure_at_eighty_characters(look):
    """25 of 29 wrapped labels run 90+ characters today, most at 207-212."""
    label = _shown(C.body_label(PROSE), WIDE)
    assert _characters_per_line(label) <= 80, _characters_per_line(label)


def test_the_cap_is_what_holds_it_and_not_the_window(look):
    """An uncapped QLabel in the same window is the number being replaced."""
    host = QWidget()
    box = QVBoxLayout(host)
    capped = C.body_label(PROSE)
    loose = QLabel(PROSE)
    loose.setWordWrap(True)
    box.addWidget(capped)
    box.addWidget(loose)
    box.addStretch()
    host.resize(WIDE, 900)
    host.show()
    _app().processEvents()
    assert _characters_per_line(loose) > 200
    assert _characters_per_line(capped) <= 80


def test_a_shorter_measure_is_honoured_too(look):
    label = _shown(C.body_label(PROSE, max_chars=48), WIDE)
    assert _characters_per_line(label) <= 48


def test_the_hint_is_capped_as_well(look):
    assert _characters_per_line(_shown(C.hint(PROSE), WIDE)) <= 80


def test_the_search_field_is_sized_to_the_query_not_the_window(look):
    field = _shown(C.search_field(), WIDE)
    assert field.width() < WIDE // 4, field.width()


# ── The table: the two critical column findings ──────────────────────────────

LEAD_COLUMNS = [
    C.Column("Business", kind="stretch", weight=2, min_ch=14),
    C.Column("Email", kind="stretch", weight=2, min_ch=16),
    C.Column("Score", kind="fit", align="right", min_ch=8, max_ch=12,
             sample="100 · strong"),
    C.Column("Headline gap", kind="stretch", weight=3, min_ch=18),
    C.Column("Status", kind="fit", min_ch=8, max_ch=12, sample="suppressed"),
]

GAP = "no online booking anywhere on the site, and the contact form is a mailto link"
NAME = "Northgate Roofing and Exteriors Limited"
MAIL = "hello.northgate.roofing.toronto@example.com"


def _lead_table(width: int):
    grid = C.table(LEAD_COLUMNS)
    for index in range(20):
        grid.add_row([
            C.Cell(NAME),
            C.Cell(MAIL),
            C.Cell("72", sort=72, tone="accent"),
            C.Cell(GAP),
            C.Cell("audited"),
        ], data={"id": index})
    _shown(grid, width)
    grid.relayout()
    return grid


def _widths(grid) -> list:
    return [grid.columnWidth(i) for i in range(grid.columnCount())]


def test_the_last_section_no_longer_takes_the_spare(look):
    """`stretchLastSection` is what handed a wide window to whichever column was last."""
    grid = _lead_table(DEFAULT)
    assert grid.horizontalHeader().stretchLastSection() is False


def test_no_column_takes_over_a_quarter_of_a_wide_window(look):
    """The 2560px finding, as a number nothing in the library supplied.

    The old build hands the Status column 1832px of a 2528px viewport for the
    word "audited", because `stretchLastSection` gives the last column every
    spare pixel whether or not it has anything to put in them.
    """
    grid = _lead_table(WIDE)
    viewport = grid.viewport().width()
    assert max(_widths(grid)) <= viewport // 4, (_widths(grid), viewport)


@pytest.mark.parametrize("width", [MINIMUM, DEFAULT, WIDE])
def test_the_distribution_never_overshoots_a_cap(look, width):
    """The share-out loop hands out what is left without exceeding any maximum."""
    grid = _lead_table(width)
    over = []
    for index, column in enumerate(grid.columns()):
        cap = grid._cap(column, grid.fontMetrics())
        if grid.columnWidth(index) > cap:
            over.append((column.title, grid.columnWidth(index), cap))
    assert over == [], over


def test_the_short_fixed_column_stays_short_at_every_width(look):
    """Review Count was handed 268px for an 18px number."""
    seen = []
    for width in (MINIMUM, DEFAULT, WIDE):
        grid = _lead_table(width)
        titles = [c.title for c in grid.columns()]
        seen.append((grid.columnWidth(titles.index("Score")),
                     grid.columnWidth(titles.index("Status"))))
    for score, status in seen:
        assert score <= 120, seen
        assert status <= 120, seen
    assert len({s for s, _ in seen}) == 1, seen


def test_the_column_that_carries_meaning_grows_with_the_window(look):
    """The Headline gap column stayed frozen at 240px while the window doubled."""
    narrow = _lead_table(MINIMUM)
    wide = _lead_table(WIDE)
    titles = [c.title for c in narrow.columns()]
    at = titles.index("Headline gap")
    assert wide.columnWidth(at) > narrow.columnWidth(at), (
        narrow.columnWidth(at), wide.columnWidth(at))


def test_the_stretch_is_shared_by_weight(look):
    """Weight 3 takes more of the spare than weight 2 does."""
    grid = _lead_table(DEFAULT)
    titles = [c.title for c in grid.columns()]
    gap = grid.columnWidth(titles.index("Headline gap"))
    email = grid.columnWidth(titles.index("Email"))
    assert gap > email, (gap, email)


def test_the_columns_fit_inside_the_viewport(look):
    """No horizontal scrollbar at any supported width."""
    for width in (MINIMUM, DEFAULT, WIDE):
        grid = _lead_table(width)
        assert sum(_widths(grid)) <= grid.viewport().width(), (width, _widths(grid))


@pytest.mark.parametrize("width", [MINIMUM, DEFAULT, WIDE])
def test_every_elided_cell_answers_with_the_whole_of_it(look, width):
    """16 of 20 cells elided, 11 of them with nothing on hover."""
    grid = _lead_table(width)
    elided = [(r, c) for r in range(grid.rowCount())
              for c in range(grid.columnCount()) if grid.is_elided(r, c)]
    silent = [at for at in elided if not grid.tooltip_at(*at)]
    assert silent == [], silent


def test_a_cell_that_fits_says_nothing_on_hover(look):
    """A tooltip repeating what is already on screen is noise."""
    grid = _lead_table(WIDE)
    titles = [c.title for c in grid.columns()]
    at = titles.index("Score")
    assert grid.is_elided(0, at) is False
    assert grid.tooltip_at(0, at) == ""


def test_the_tooltip_reaches_the_viewport_event(look):
    """The measurement above is what the hover path actually calls."""
    grid = _lead_table(MINIMUM)
    titles = [c.title for c in grid.columns()]
    at = titles.index("Headline gap")
    point = grid.visualRect(grid.model().index(0, at)).center()
    handled = grid.viewportEvent(
        QHelpEvent(QEvent.ToolTip, point, grid.viewport().mapToGlobal(point)))
    assert handled is True
    assert grid.tooltip_at(0, at) == GAP


def test_headers_align_to_the_data_under_them(look):
    """A centred header over a right-aligned number floats away from its column."""
    grid = _lead_table(DEFAULT)
    for index, column in enumerate(grid.columns()):
        header = grid.horizontalHeaderItem(index).textAlignment()
        cell = grid.item(0, index).textAlignment()
        assert header & Qt.AlignHorizontal_Mask == cell & Qt.AlignHorizontal_Mask, \
            column.title


@pytest.mark.parametrize("density", ["comfortable", "compact"])
def test_the_row_height_is_the_density_token(density):
    app = _app()
    t = TH.theme("dark", density)
    TH.apply(app, t)
    C.use_theme(t)
    grid = C.table(["A"], density=density)
    assert grid.verticalHeader().defaultSectionSize() == t.control["row"]


def test_a_plain_title_is_read_as_a_capped_fit_column(look):
    grid = C.table(["Business", "Email"])
    assert [c.kind for c in grid.columns()] == ["fit", "fit"]
    assert all(c.cap() > 0 for c in grid.columns())


def test_a_cell_sorts_on_the_value_it_was_given(look):
    """An em dash compares greater than any digit, which floated unaudited leads first."""
    grid = C.table([C.Column("Score", align="right")])
    for text, key in (("—", -1), ("72", 72), ("9", 9)):
        grid.add_row([C.Cell(text, sort=key)])
    grid.sortItems(0, Qt.AscendingOrder)
    assert [grid.item(r, 0).text() for r in range(3)] == ["—", "9", "72"]


def test_a_row_carries_its_record(look):
    grid = _lead_table(DEFAULT)
    assert grid.item(0, 0).data(Qt.UserRole) == {"id": 0}


# ── Status: colour, label and shape, never colour alone ──────────────────────


def test_nine_statuses_and_more_are_all_covered():
    """The screens use nine; the contract adds `sending` and `rehearsed`."""
    for status in ("new", "audited", "queued", "sending", "rehearsed", "sent",
                   "replied", "bounced", "failed", "skipped", "suppressed"):
        assert status in C.STATUS_PILLS, status


def test_no_two_statuses_are_the_same_thing(look):
    """`bounced`, `failed` and `suppressed` are one hex today, at 1.00:1."""
    seen = {}
    for status, spec in C.STATUS_PILLS.items():
        shape = (spec.family, spec.fill, spec.mark, spec.edge)
        assert shape not in seen, (status, seen.get(shape))
        seen[shape] = status


def test_a_shared_colour_is_always_split_by_shape(look):
    """Where two statuses land on one colour, the fill or the mark separates them."""
    entries = list(C.STATUS_PILLS.items())
    for index, (one, first) in enumerate(entries):
        for two, second in entries[index + 1:]:
            if C._pill_colours(look, first) != C._pill_colours(look, second):
                continue
            assert (first.fill, first.mark, first.edge) != \
                   (second.fill, second.mark, second.edge), (one, two)


def test_every_status_says_its_own_name(look):
    """The label is the third carrier, and it is never dropped."""
    for status in C.STATUS_PILLS:
        pill = C.status_pill(status)
        assert status in pill.text()
        assert status in pill.accessibleName()


def test_the_marks_are_all_different_glyphs():
    marks = [spec.mark for spec in C.STATUS_PILLS.values() if spec.mark]
    assert len(marks) == len(set(marks)), marks


def test_a_status_nobody_planned_for_still_renders(look):
    pill = C.status_pill("quarantined")
    assert "quarantined" in pill.text()


def test_every_pill_is_legible_on_its_own_ground(look):
    """Ink over fill, at every one of the eleven states."""
    worst = None
    for status, spec in C.STATUS_PILLS.items():
        ground, ink, _edge = C._pill_colours(look, spec)
        if ground == "transparent":
            ground = look.color["surface"]
        ratio = _contrast(ground, ink)
        worst = min(worst, ratio) if worst is not None else ratio
        assert ratio >= AA, (status, round(ratio, 2))
    assert worst >= AA


def test_every_pill_has_a_visible_boundary_on_a_card(look):
    """A badge is a UI component: 3:1 against the surface it sits on."""
    for status, spec in C.STATUS_PILLS.items():
        _ground, _ink, edge = C._pill_colours(look, spec)
        ratio = _contrast(look.color["surface"], edge)
        assert ratio >= COMPONENT, (status, round(ratio, 2))


# ── Score: a number and a word, not only a colour ────────────────────────────


@pytest.mark.parametrize("score,band", [(92, "strong"), (70, "strong"),
                                        (69, "moderate"), (45, "moderate"),
                                        (44, "thin"), (1, "thin"),
                                        (0, "not audited")])
def test_the_score_badge_spells_out_its_band(look, score, band):
    badge = C.score_badge(score)
    assert band in badge.text(), badge.text()
    if score:
        assert str(score) in badge.text()
    else:
        assert "—" in badge.text()


def test_an_unreadable_score_is_not_audited(look):
    assert "not audited" in C.score_badge(None).text()
    assert "not audited" in C.score_badge("").text()


def test_the_score_badge_is_legible_in_every_band(look):
    for score in (92, 50, 10, 0):
        tone, _band = C.score_band(score)
        ground = look.color["%s.subtle" % tone] if tone in C.FAMILIES \
            else look.color["surface"]
        assert _contrast(ground, C._ink(look, tone)) >= AA, (score, tone)


# ── The three states every data surface needs ────────────────────────────────


def test_all_three_states_exist_and_are_distinguishable(look):
    empty = C.empty_state(title="No leads yet", body="Scrape a city first.")
    loading = C.loading_state(label="Auditing 40 sites…")
    error = C.error_state(title="Groq refused the key", body="Check it in Settings.")
    kinds = [w.property("state") for w in (empty, loading, error)]
    assert kinds == ["empty", "loading", "error"]
    # Not only a property name: the error reads as one before it is read.
    assert look.color["danger.text"] in error.title_label.styleSheet()
    assert look.color["danger.text"] not in empty.title_label.styleSheet()
    assert _contrast(look.color["surface"], look.color["danger.text"]) >= AA


def test_the_empty_state_names_the_next_action(look):
    calls = []
    state = C.empty_state(title="No leads yet", body="Import a CSV.",
                          action="Import CSV…", on_action=lambda: calls.append(1))
    assert state.action_button is not None
    state.action_button.click()
    assert calls == [1]


def test_the_loading_state_is_indeterminate(look):
    state = C.loading_state(label="Working")
    assert isinstance(state.progress, QProgressBar)
    assert (state.progress.minimum(), state.progress.maximum()) == (0, 0)
    assert state.action_button is None


def test_the_error_state_offers_the_way_back(look):
    calls = []
    state = C.error_state(title="Could not reach Groq", body="The key was refused.",
                          retry=lambda: calls.append(1))
    assert state.action_button is not None
    state.action_button.click()
    assert calls == [1]


def test_an_error_with_no_retry_still_renders(look):
    state = C.error_state(title="Gone", body="Nothing to do about it.")
    assert state.action_button is None


def test_a_state_keeps_its_sentence_readable(look):
    state = _shown(C.empty_state(title="No leads yet", body=PROSE), WIDE)
    assert _characters_per_line(state.body_label) <= 80


# ── Toasts ───────────────────────────────────────────────────────────────────


def test_a_plain_toast_dismisses_itself(look):
    host = QWidget()
    toaster = C.Toaster(host)
    toast = toaster.show("Prepared 42 messages.")
    assert toast.timer is not None
    assert toast.timer.interval() == C.DWELL["info"]


def test_a_danger_toast_never_dismisses_itself(look):
    """The error about a Gmail password used to vanish after six seconds."""
    host = QWidget()
    toaster = C.Toaster(host)
    toast = toaster.show("Gmail refused the app password.", tone="danger")
    assert toast.timer is None


def test_a_toast_with_an_action_waits_longer_than_one_without(look):
    host = QWidget()
    toaster = C.Toaster(host)
    plain = toaster.show("Suppressed hello@example.com.", tone="warning")
    undo = toaster.show("Suppressed hello@example.com.", tone="warning",
                        action="Undo", on_action=lambda: None)
    assert undo.timer.interval() > plain.timer.interval()
    assert undo.timer.interval() == C.ACTION_DWELL


def test_the_action_runs_and_then_the_toast_goes(look):
    """Suppressing a lead is permanent, silent and unconfirmed today."""
    calls = []
    host = QWidget()
    toaster = C.Toaster(host)
    toast = toaster.show("Suppressed hello@example.com.", action="Undo",
                         on_action=lambda: calls.append(1))
    toast.action_button.click()
    _app().processEvents()
    assert calls == [1]
    assert toaster.toasts() == []


def test_a_toast_can_always_be_dismissed_by_hand(look):
    host = QWidget()
    toaster = C.Toaster(host)
    toast = toaster.show("Gmail refused the app password.", tone="danger")
    toast.close_button.click()
    _app().processEvents()
    assert toaster.toasts() == []


def test_an_explicit_timeout_beats_the_tone(look):
    host = QWidget()
    toaster = C.Toaster(host)
    assert toaster.show("x", tone="danger", timeout=1500).timer.interval() == 1500
    assert toaster.show("x", tone="info", timeout=0).timer is None


def test_the_stack_is_capped(look):
    host = QWidget()
    toaster = C.Toaster(host)
    for index in range(6):
        toaster.show("message %d" % index, tone="danger")
    _app().processEvents()
    assert len(toaster.toasts()) == C.MAX_TOASTS


def test_a_toast_starts_in_the_same_place_whatever_it_says(look):
    """Its own measure makes the label narrower than the row, and Qt centres that.

    Two messages of different lengths then start at two different x, which is
    unreadable at a glance — and a glance is all the time a toast gets.
    """
    host = QWidget()
    box = QVBoxLayout(host)
    toaster = C.Toaster(host)
    box.addWidget(toaster.widget)
    box.addStretch()
    short = toaster.show("Sent.", tone="warning")
    long = toaster.show("Suppressed hello@northgateroofing.ca — it will never be "
                        "contacted again.", tone="warning", action="Undo",
                        on_action=lambda: None)
    host.resize(WIDE, 600)
    host.show()
    _app().processEvents()
    assert short.text_label.geometry().x() == long.text_label.geometry().x()


def test_the_toaster_costs_no_space_while_it_is_quiet(look):
    host = QWidget()
    toaster = C.Toaster(host)
    box = QVBoxLayout(host)
    box.addWidget(toaster.widget)
    host.resize(DEFAULT, 700)
    host.show()
    _app().processEvents()
    assert toaster.widget.isVisible() is False
    toaster.show("Prepared 42 messages.")
    _app().processEvents()
    assert toaster.widget.isVisible() is True
    toaster.clear()
    _app().processEvents()
    assert toaster.widget.isVisible() is False


def test_every_tone_is_legible(look):
    for tone in C.FAMILIES:
        ground = look.color["%s.subtle" % tone]
        assert _contrast(ground, look.color["%s.text" % tone]) >= AA, tone
        assert _contrast(look.color["canvas"], C._edge(look, tone)) >= COMPONENT, tone


# ── Confirmation ─────────────────────────────────────────────────────────────


def test_a_remembered_answer_asks_nothing(look):
    """No dialog is constructed at all, which is what makes this testable offline."""
    C.forget()
    C.remember("suppress_lead", True)
    assert C.confirm(None, title="Suppress?", body="Permanent.",
                     confirm_text="Suppress", remember_key="suppress_lead") is True


def test_forgetting_brings_the_question_back(look):
    C.remember("suppress_lead", True)
    C.forget("suppress_lead")
    assert C.remembered("suppress_lead") is False


def test_an_empty_key_is_never_remembered(look):
    C.remember("", True)
    assert C.remembered("") is False


def test_the_default_button_is_the_way_out(look):
    """Return on a dialog nobody read must not be what deletes the template."""
    box, go, checkbox = C._confirm_box(
        None, "Delete template?", "This cannot be undone.", "Delete", True,
        "delete_template")
    assert box.defaultButton() is not go
    assert box.escapeButton() is not go
    assert isinstance(checkbox, QCheckBox)


def test_the_destructive_button_carries_the_destructive_role(look):
    box, go, _checkbox = C._confirm_box(
        None, "Remove account?", "It stops sending.", "Remove", True, "")
    assert box.buttonRole(go) == QMessageBox.DestructiveRole


def test_a_safe_confirmation_is_not_destructive(look):
    box, go, checkbox = C._confirm_box(
        None, "Save changes?", "The template is edited.", "Save", False, "")
    assert box.buttonRole(go) == QMessageBox.AcceptRole
    assert checkbox is None


def test_the_remember_store_can_be_pointed_somewhere_that_lasts(look):
    """`core.settings` drops keys outside its schema, so this is the seam."""
    written = {}
    C.set_remember_store(load=written.get,
                         save=lambda key, value: written.__setitem__(key, value))
    try:
        C.remember("delete_template", True)
        assert written == {"delete_template": True}
        assert C.remembered("delete_template") is True
    finally:
        C.set_remember_store(None, None)
        C.forget()


# ── Composites the screens already build by hand ─────────────────────────────


def test_the_secret_field_masks_until_it_is_revealed(look):
    field = C.text_field(placeholder="app password", secret=True)
    edit = field.edit
    edit.setText("abcd efgh ijkl mnop")
    assert edit.edit.echoMode() == QLineEdit.Password
    edit.toggle.setChecked(True)
    assert edit.edit.echoMode() == QLineEdit.Normal
    assert edit.toggle.text() == "Hide"
    assert edit.text() == "abcd efgh ijkl mnop"


def test_a_field_keeps_room_for_its_error_line(look):
    """A form that grows a row on failure moves the control under the pointer."""
    field = _shown(C.text_field(label="Email", help="The account that sends"), DEFAULT)
    before = field.height()
    field.set_error("That address is not a Gmail account.")
    _app().processEvents()
    assert field.error.isVisible() is True
    assert field.error.text() == "That address is not a Gmail account."
    field.set_error("")
    assert field.error.isVisible() is False
    assert before > 0


def test_a_select_carries_its_data(look):
    field = C.select([("Groq", "groq"), ("OpenRouter", "openrouter")],
                     label="Provider")
    assert isinstance(field.combo, QComboBox)
    assert field.combo.itemData(1) == "openrouter"


def test_a_toggle_carries_the_cost_of_turning_it_off(look):
    cost = "Off, the footer loses its opt-out line."
    box = C.toggle("Append unsubscribe", help=cost)
    assert box.toolTip() == cost
    assert box.help_label is not None
    assert cost in box.help_label.text()


def test_a_chip_can_be_clicked_and_taken_off(look):
    fired = []
    piece = C.chip("company", on_click=lambda: fired.append("click"), removable=True)
    piece.removed.connect(lambda: fired.append("remove"))
    piece.clicked.emit()
    piece.remove_button.click()
    assert fired == ["click", "remove"]


def test_the_header_drives_its_own_tabs(look):
    picked = []
    header = _shown(C.screen_header("MapHarvest", subtitle="Outreach",
                                    tabs=("Leads", "Campaign", "Sending", "Stats"),
                                    on_tab=picked.append), DEFAULT)
    assert len(header.tab_buttons) == 4
    assert header.tab_buttons[0].isChecked() is True
    header.tab_buttons[2].click()
    assert picked == [2]
    assert [b.isChecked() for b in header.tab_buttons] == [False, False, True, False]
    # The group the screens already connect to, so migrating one is a swap.
    assert header.tab_group.exclusive() is True
    assert header.tab_group.id(header.tab_buttons[3]) == 3
    header.select_tab(0)
    assert [b.isChecked() for b in header.tab_buttons] == [True, False, False, False]
    assert picked == [2]


def test_the_header_is_the_same_height_on_every_screen(look):
    """Four screens, four top bars: 70px, 50px and none."""
    heights = {_shown(C.screen_header(title), DEFAULT).height()
               for title in ("MapHarvest", "Settings", "Results", "Outreach")}
    assert heights == {look.control["header"]}


def test_a_card_takes_a_body_after_it_is_built(look):
    panel = C.card(title="Template", subtitle="What they receive")
    panel.body_layout.addWidget(QLabel("body"))
    assert panel.body_layout.count() == 1


def test_a_divider_is_a_hairline_in_both_directions(look):
    horizontal = _shown(C.divider(), DEFAULT)
    vertical = _shown(C.divider(Qt.Vertical), DEFAULT)
    assert horizontal.height() == C.BORDER
    assert vertical.width() == C.BORDER


def test_the_stat_tile_reads_as_a_number_and_a_caption(look):
    tile = C.stat_tile("Sent", 128, tone="accent", hint="in the last 7 days")
    assert tile.value_label.text() == "128"
    assert tile.caption_label.text() == "Sent"
    assert tile.hint_label is not None
    tile.value_label.ensurePolished()
    assert tile.value_label.font().pixelSize() == look.font["h1"][0]


# ── The theme is the only source ─────────────────────────────────────────────


def test_a_component_follows_whichever_theme_is_loaded():
    """The same call under two themes paints two different grounds."""
    app = _app()
    sheets = {}
    for name in ("dark", "light"):
        t = TH.theme(name)
        TH.apply(app, t)
        C.use_theme(t)
        sheets[name] = C.status_pill("bounced").styleSheet()
        assert t.color["danger.default"] in sheets[name]
    assert sheets["dark"] != sheets["light"]


def test_every_colour_a_component_paints_is_in_the_palette(look):
    """Pulled back out of the generated rules and matched against the tokens."""
    known = set(look.color.values()) | {"transparent", "none"}
    widgets = [C.status_pill(s) for s in C.STATUS_PILLS] + [
        C.score_badge(80), C.score_badge(0), C.card(title="t"), C.divider(),
        C.heading("t"), C.body_label("t"), C.hint("t"), C.section_label("t"),
        C.button("t", kind="ghost"), C.icon_button("x", tooltip="t"),
        C.chip("t"), C.stat_tile("t", 1, tone="danger"),
        C.screen_header("t"),
    ]
    painted = set()
    for widget in widgets:
        painted.update(re.findall(r"#[0-9A-Fa-f]{6}", widget.styleSheet()))
    unknown = {c for c in painted if c not in known}
    assert unknown == set(), unknown


def test_the_module_defaults_to_a_theme_even_when_nobody_set_one():
    C.use_theme(None)
    assert C.active_theme().name == TH.DEFAULT_THEME
