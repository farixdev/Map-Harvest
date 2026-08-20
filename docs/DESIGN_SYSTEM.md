# MapHarvest Design System — Implementation Contract

Binding spec for `ui/theme.py` and `ui/components.py`. Every value the interface paints
comes from here. A hex literal, font size, spacing number or radius written anywhere else
in `ui/` is a defect.

## Why this exists — the measured state it replaces

The audit measured what a decade of ad-hoc styling produced:

| | Before |
|---|---|
| Colours | 30 values in the sheet, 26 more literals in Python, **18 pairs below the just-noticeable difference** (tightest ΔE 0.94), two conflicting `_GREEN` constants |
| Type | Five sizes in an 11–15px band, adjacent ratios 1.07–1.09 — a linear march, not a scale. **No heading tier; the largest text in the app is 15px** |
| Surfaces | Four fills spanning **1.22:1**, no shadows, a modal the same colour as the page. 96–99% of painted pixels sit under L=0.05 on 11 of 13 screens |
| Buttons | **One component, six heights** — `QPushButton#outlined` at 26, 28, 30, 32, 34 and 40px |
| Spacing | 9 margin values, 12 layout spacings, 16 paddings; 9/10/14 off any grid; **36 layouts silently on Qt's default 9px** |
| Radii | **Nine** values (1,2,4,5,6,7,8,10,12); `QTableWidget` declared 10px and 8px |
| Chrome | Four screens, four top bars: 70px / 50px / none |
| State | Focus ring **17.01:1**, selected tab **1.50:1** — the focus ring is 11× the selection it competes with |

The last row is the critical one and drives a rule below: **no focus treatment may ever
outweigh the selection treatment it sits beside.**

---

## 1. `ui/theme.py`

```python
@dataclass(frozen=True)
class Theme:
    name: str                    # "dark" | "light"
    density: str                 # "comfortable" | "compact"
    color: Mapping[str, str]
    font: Mapping[str, tuple]    # name -> (px, weight)
    space: Mapping[str, int]
    radius: Mapping[str, int]
    control: Mapping[str, int]
    motion: Mapping[str, int]

THEMES: dict[str, Theme]                       # by name
def theme(name: str = "dark", density: str = "comfortable") -> Theme
def stylesheet(t: Theme) -> str                # the whole QSS, generated
def token(t: Theme, path: str) -> str | int    # "color.text.primary"
def apply(app: QApplication, t: Theme) -> None # font + style + sheet, in one place
```

`stylesheet()` is the **only** producer of QSS. `ui/app.py` keeps no literal sheet.

### Colour — the full token set

Both themes define every key. Names describe **role**, never appearance: `text.secondary`,
not `grey`. Nothing may be added to a screen file.

```
canvas            page background
surface           cards, panels, table body
surfaceHover      row and control hover
surfaceActive     row pressed / selected ground
raised            dialogs, popovers, menus, dropdowns, command palette
inset             input wells, preview paper, code
scrim             modal backdrop (semi-transparent black)

border.subtle     dividers inside a surface
border.default    control and card outlines
border.strong     emphasised outline, hovered control

text.primary      body and headings
text.secondary    supporting copy, table meta
text.tertiary     hints, captions, placeholder
text.disabled     inactive controls
text.onAccent     text on any filled accent

accent.subtle     tinted background for accent chips/rows
accent.default    brand fill
accent.hover
accent.active
accent.border
accent.text       accent-coloured text on canvas/surface

success / warning / danger / info
  each: .subtle .default .hover .border .text
```

**Hard constraints, to be asserted in tests, measured not assumed:**

1. Every `text.*` token clears **4.5:1** against `canvas`, `surface`, `surfaceHover`,
   `surfaceActive`, `raised` and `inset` — every ground it can actually be painted on,
   `surfaceActive` included, because a selected row still has to be read. `text.disabled`
   is exempt (WCAG exempts inactive controls) but must still clear **3:1**, since 1.90:1
   today makes disabled controls read as absent rather than unavailable.
2. `text.onAccent` clears **4.5:1** on every `*.default` fill it is painted on. White on
   today's `#22A559` is 3.18:1 — the accent fill must darken until it passes, or the text
   changes. This is deliberately *not* a constraint on `*.text`: those are accent-coloured
   text for `canvas` and `surface` and are never painted on their own fill.
3. Adjacent surfaces (`canvas`→`surface`→`raised`) each differ by **≥ 1.4:1**, so a card
   reads as a card and a dialog reads as above the page.
4. No two tokens in the whole set are within **ΔE 2.0** (CIE76) unless one is a documented
   hover/active pair of the other.
5. `border.default` clears **3:1** against both the surface it sits on and the surface it
   separates from, `surfaceActive` included — a selected row's own outline must not vanish.

### Status colour — nine states, not seven

`bounced`, `failed` and `suppressed` are currently the same hex, and `sent`/`replied`
differ by ΔE ≈ 5. Colour alone must stop carrying meaning:

- A `StatusPill` component (below) pairs **colour + label + shape**. Terminal-bad states
  (`bounced`, `failed`) are filled; user-chosen states (`suppressed`, `skipped`) are
  outlined; in-flight states (`queued`, `sending`, `rehearsed`) are subtle-filled;
  good states (`sent`, `replied`) are accent-tinted with `replied` carrying a mark.
- Opportunity score keeps its colour band but gains a numeric value and a text band label,
  so a monochrome or colour-blind reader loses nothing.

### Type — a real scale with a heading tier

```
display  28 / 600      screen titles
h1       20 / 600      section headings
h2       16 / 600      card and panel titles
h3       14 / 600      sub-headings, table group headers
body     13 / 400      default
bodyMed  13 / 500      emphasis inside body
small    12 / 400      table meta, secondary
caption  11 / 500      uppercase section labels, +0.4px letter-spacing
mono     12 / 400      template bodies, merge fields, technical values
```

Ratios step ~1.2 between tiers rather than +1px. **Line length is capped at 80 characters**
for any wrapped body text — 25 of 29 wrapped labels currently run 90+ cpl, most at 207–212.
`components.body_label()` enforces this with a maximum width; nothing else should wrap text.

**DPI:** the app currently mixes pt and px, so its own text ignores Windows text scaling
while menus and tooltips follow it. `apply()` enables high-DPI scaling and
`AA_UseHighDpiPixmaps`, sets the base font in **points** from the system default, and every
QSS size is **px** (which Qt scales). One system, no mixing.

### Space — 4px grid, ten values

```
space.0=0  .1=4  .2=8  .3=12  .4=16  .5=20  .6=24  .7=32  .8=40  .9=48
hair=2                          only for optical nudges, never layout
```

Every `setContentsMargins`, `setSpacing` and QSS padding uses one of these. **Every layout
sets its spacing explicitly** — the 36 layouts silently inheriting Qt's 9px are the reason
nothing lines up.

### Radius — three values

```
radius.sm=4     chips, badges, inputs inside a row
radius.md=6     buttons, inputs, table cells
radius.lg=10    cards, panels, dialogs
radius.pill=999 status pills, counts
```

### Controls — one height per size, per density

```                comfortable   compact
control.xs            24           22     chips
control.sm            28           26     secondary/toolbar buttons
control.md            32           28     default buttons, inputs, selects
control.lg            40           36     primary actions, search field
control.row           36           28     table row height
control.header        44           40     screen chrome height
```

`QPushButton#outlined` renders at **one** height. A caller wanting another size picks a
size token; it may not set a pixel height.

### Motion

```
motion.instant=0   motion.fast=120   motion.base=180   motion.slow=260
```

Hover/press = `fast`. Screen and panel transitions = `base`. Toast in/out = `base`.
Easing is `QEasingCurve.OutCubic`. **Nothing animates on data arriving** — a table that
animates each incoming row during a 500-lead scrape is noise, and it costs repaints.

### Focus vs selection — the rule the audit's critical finding demands

- **Selection** is the louder signal. Tabs carry a 2px accent bottom rail plus
  `text.primary` ink plus `surfaceActive` ground. List and table rows carry a 2px accent
  left rail plus `surfaceActive`. Selection must clear **3:1** against the unselected state.
- **Focus** is subordinate: a 1px `accent.border` ring on a transparent 1px base border, so
  geometry never shifts. Never white, never 2px, never brighter than selection.
- A control that is both focused and selected shows both without either being lost.
- **Focus must also outrank rest.** The dark ring first shipped at 3.70:1 on canvas where
  the resting border sat at 6.50:1, so taking focus made a control's outline *dimmer* than
  leaving it alone. Focus is a gain in emphasis, in every theme, on every ground.

---

## 2. `ui/components.py`

The shared library the app is missing. `screen_outreach.py` (2591 lines) and
`screen_settings.py` (2747) each re-implement cards, section headers, empty states,
password fields and toasts. Every one moves here, and screens shrink accordingly.

```python
# Chrome
def screen_header(title, *, subtitle="", actions=(), tabs=(), on_tab=None) -> QWidget
def section_label(text) -> QLabel
def card(*, title="", subtitle="", body=None, actions=()) -> QFrame
def divider(orientation=Qt.Horizontal) -> QFrame

# Text
def heading(text, level="h2") -> QLabel
def body_label(text, *, tone="secondary", max_chars=80) -> QLabel
def hint(text) -> QLabel

# Controls
def button(text, *, kind="secondary", size="md", icon=None, on_click=None) -> QPushButton
#   kind: primary | secondary | ghost | danger | danger_primary
def icon_button(icon, *, tooltip, size="md") -> QToolButton
def text_field(*, placeholder="", label="", help="", error="", secret=False) -> QWidget
def select(items, *, label="", help="") -> QWidget
def toggle(text, *, help="") -> QCheckBox
def search_field(placeholder="Search…") -> QLineEdit
def chip(text, *, on_click=None, removable=False) -> QWidget

# Data
def table(columns, *, density="comfortable", sortable=True) -> QTableWidget
def status_pill(status) -> QWidget
def score_badge(value) -> QWidget
def stat_tile(label, value, *, tone="neutral", hint="") -> QFrame

# States — the three every data surface needs
def empty_state(*, title, body, action=None, on_action=None) -> QWidget
def loading_state(*, label="") -> QWidget          # indeterminate, no spinner gimmicks
def error_state(*, title, body, retry=None) -> QWidget

# Feedback
class Toaster:                                      # replaces the 6-second bottom toast
    def show(self, text, *, tone="info", action=None, on_action=None, timeout=None)
    #   tone: info | success | warning | danger
    #   an action makes it an undo affordance; danger tone never auto-dismisses
def confirm(parent, *, title, body, confirm_text, danger=False, remember_key="") -> bool
```

### Component rules

- **Every table** is built by `table()`, which sets a real width policy: content-sized
  columns for short fixed values, stretch shared between the columns that carry meaning,
  and a **maximum** width so a 2560px window does not hand 1800px to the word "audited".
  Column headers align to their data — centred headers over left-aligned values are why
  a wide column's header floats 100px from what it labels.
- **Every data surface** supplies all three of empty, loading and error. A bare bordered
  box is a defect.
- **Every destructive action** goes through `confirm()`, or is undoable via a toast action,
  or both. Today: suppressing a lead, deleting a template and removing a Gmail account are
  all permanent, silent and unconfirmed.
- **`button(kind=...)` decides colour, not the caller.** One green currently means "Save",
  "Start Scraping" and "mail 20 real strangers". `primary` is for the safe primary action;
  `danger_primary` is for the one that contacts real people, and it always pairs with
  `confirm()`.
- Nothing sets a fixed pixel size except where a token says so.

---

## 3. Migration rules

1. `ui/theme.py` and `ui/components.py` land first; nothing else changes in that step.
2. Screens migrate one at a time, each fully, so the app is never half-styled.
3. After each screen: the full suite passes, and a contrast/geometry sweep asserts no token
   violation and no clipped or overlapping widget at every supported size.
4. **No behaviour changes during a styling migration.** Business logic, signals and worker
   lifecycles stay untouched; those are separate, separately-tested commits.
5. A screen is done when it contains **zero** hex literals, zero raw font sizes, zero raw
   spacing numbers and zero fixed heights.
