"""Full-screen settings — everything the outreach side needs before it may send.

The old settings lived in a tab of `InputScreen`, sized for two controls. Cold
email needs an order of magnitude more: a sender identity that satisfies
CAN-SPAM, Gmail app passwords, a sending window, warm-up ramps, follow-up gaps
and an AI provider. Cramming that back into a tab would have produced a wall of
half-labelled fields, so it gets a screen. `InputScreen` keeps `headless` and the
result-limit cap where the user already knows to find them.

Four things here are safety features rather than decoration:

* **Secrets are never on screen in plaintext by default.** Every credential field
  is `QLineEdit.Password` with an explicit reveal toggle, and is written through
  `settings.set_secret` so `settings.json` holds ciphertext.
* **Verify and Test run off the GUI thread.** An SMTP handshake to Gmail takes
  seconds and an unreachable host takes the full timeout; doing that inline would
  freeze the window and look like a crash. Both report the provider's real error
  string, because "wrong password" and "app passwords are disabled for this
  account" need completely different fixes.
* **Turning dry-run off is a decision, not a click.** It is the one toggle that
  converts a rehearsal into mail landing in strangers' inboxes, so it asks first
  and defaults to on.
* **A compliance guardrail is a switch, not a wall.** The unsubscribe line, the
  postal address and the profile check are what keep a sender out of the spam
  folder and inside the law, so each one is on by default and each one says in
  plain words what turning it off costs. None of them stops a send.

Three more are what the design-system audit measured on this screen and what
this file now answers with:

* **Save and Discard are the only two ways out.** Back used to save everything
  on the way past and put "Saved" on a screen the user had already left, so a
  half-finished sending window was committed by the act of leaving and nothing
  the user could see said so. Both commands sit in a footer that is on screen
  whenever the settings are, the footer says whether there is anything
  outstanding, and navigating away no longer decides anything.
* **The scheduler's own limits are shown beside the numbers that ask for them.**
  `core.campaign` composes the global cap, the per-account cap and the warm-up
  ramp as a minimum, forces an inverted window to one hour and reads an empty
  day set as Monday to Friday. The UI used to report the requested value back as
  if it were in force; where the two differ, both are on screen.
* **Appearance is a section rather than a hand edit.** Both palettes and both
  densities exist in `ui/theme.py` and neither could be reached: writing
  `theme` or `density` into settings.json was the only route and `_merge` used
  to drop them. They are controls now, and they take effect while you watch.

The shape all of that is laid out in is one shape, and that is the fourth thing
the audit measured here. It found seven left edges on the Sending tab alone —
four of them within 30px of each other and two 4px apart in adjacent groups —
because every group built its own geometry and sized its own label column to
its own longest word. There is one geometry now and it is the one macOS System
Settings uses: a section is a title and one muted line saying what it is for;
under that are groups; a group is a caption over a box; a box is rows with a
hairline between each pair, the label on the left and the control on the right;
a row that needs explaining carries one quiet line under it; and a sentence
about a whole group sits under its box rather than inside it. `_Group` below is
all of that, and nothing on this screen lays out a row of its own.

Every value this screen paints comes from `ui.theme` through `ui.components`.
There is no hex literal, font size, spacing number or fixed height in this file
that did not come out of a token — the one exception is the preview document,
which is an email and not a piece of this application's chrome, and which says
so at `_PAPER` below.

The Templates tab is the largest thing on this screen, and it is a real editor
rather than a text box for three reasons:

* **Nothing is guessed at.** Every merge field is a chip with a tooltip saying
  what it resolves to and what happens to the sentence when it resolves to
  nothing, because `render` deletes a holed sentence whole and copy written
  without knowing that reads fine in the editor and ships as a stub.
* **The preview is the send path.** `build_context`, then
  `campaign.apply_compliance`, then `render` — the three calls the send loop
  makes, in that order, against a built-in sample lead. So the editor tells the
  truth before a single lead is imported, and a compliance switch changes what
  is on screen rather than only what leaves.
* **Validation warns and never blocks.** Warnings are amber, errors are red, and
  Save writes either way. Nothing here decides that copy is wrong; being told
  is not being stopped.

Every call into the template store goes through the small wrappers under
"Template store" below. The store is a file, every call to it can fail on a
read-only profile folder or a full disk, and none of those failures may reach a
Qt slot as an exception or reach the user as the word "Saved".
"""

from __future__ import annotations

import html
import re
import time

from PyQt5.QtCore import QDate, QEvent, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontDatabase, QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QStackedWidget, QTextEdit, QToolTip, QVBoxLayout, QWidget,
)

from core import ai as ai_client
from core import campaign as _campaign
from core import mailer
from core import templates as _templates
from core.settings import (
    SMTP_ACCOUNT_DEFAULTS, ai_budget_left, get_secret, load_settings,
    save_settings, set_secret,
)
from core.templates import AUTO_ARMY_SERVICES, MERGE_FIELDS
from ui import components as C
from ui import theme as _theme

# ── Constants ────────────────────────────────────────────────────────────────

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TONES = (("direct", "Direct"), ("friendly", "Friendly"), ("consultative", "Consultative"))
PROVIDERS = (
    ("auto", "Auto — Groq, then OpenRouter"),
    ("groq", "Groq only"),
    ("openrouter", "OpenRouter only"),
    ("off", "Off — plain templates"),
)

# Where Gmail actually hides the thing. This is the single most asked support
# question, so it sits on the screen rather than in a tooltip.
APP_PASSWORD_HINT = (
    "App Passwords live in Google Account → Security → 2-Step Verification → "
    "App passwords. 2-Step Verification must be on first. Your normal Gmail "
    "password will always be rejected."
)

# A short curated list; the combo stays editable so any IANA name can be typed.
# Enumerating zoneinfo would offer several hundred names, most of which nobody
# sells into, and the point of this control is picking the customer's own hour.
#
# Windows ships no tz database of its own, which is why `tzdata` is a runtime
# requirement: without it every name below resolves to nothing and the sending
# window silently follows this machine's clock — the one thing the window
# exists to prevent. `_zone_note` says so on screen if it is ever missing.
COMMON_ZONES = (
    "local", "America/Toronto", "America/New_York", "America/Chicago",
    "America/Denver", "America/Los_Angeles", "America/Vancouver",
    "Europe/London", "Europe/Dublin", "Europe/Berlin", "Europe/Madrid",
    "Asia/Karachi", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore",
    "Australia/Sydney",
)

# The three tones a validation line can carry, as token paths rather than as the
# three hexes that used to sit here. They are reached through the module's own
# `__getattr__`, so `screen_settings._RED` still answers with a colour — and
# answers with the theme that is loaded rather than the one that was loaded at
# import. This screen is where the palette is switched, so a colour frozen at
# import time is a colour that goes wrong the moment somebody uses the control
# two tabs over.
_TONE_INK = {"_RED": "danger.text", "_AMBER": "warning.text",
             "_GREEN": "success.text"}


def __getattr__(name: str) -> str:
    ink = _TONE_INK.get(name)
    if ink is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return C.active_theme().color[ink]


def _tone_ink(name: str) -> str:
    """The same three colours, for this module's own use.

    A module-level `__getattr__` answers attribute access on the module object
    and is never consulted for a global name lookup inside it, so the functions
    below ask here rather than reading `_RED` and getting a NameError.
    """
    return C.active_theme().color[_TONE_INK[name]]


# The preview document, and the only colours on this screen that do not come out
# of `ui/theme.py`. An email is not part of this application's chrome: the
# recipient's mail client draws it on white paper in near-black ink whatever
# theme the sender happens to be wearing, and a preview that followed the app's
# palette would be a preview of something nobody is ever sent. Written as
# lightness rather than as hex so that "no hex literal in this file" means what
# it says, and so the two greys read as the one ramp they are.
_PAPER = QColor(Qt.white)
_PAPER_INK = QColor.fromHslF(0.0, 0.0, 0.10)
_PAPER_META = QColor.fromHslF(0.0, 0.0, 0.42)

# The paper's type, for the same reason and under the same exception. A mail
# client draws a 16px subject over a 15px message whatever the sender's own
# interface is set to, so a preview written in this screen's 13px body is a
# preview of something nobody is ever sent. Kept here rather than inline in
# `_as_paper` because `_preview_floor` below is the sum of them and the two must
# not be able to drift.
_PAPER_TYPE = {"subject": 16, "meta": 12, "body": 15, "note": 14}
_PAPER_LEADING = 1.6
_PAPER_RULE, _PAPER_GAP, _PAPER_PAD = 4, 14, 16
_PAPER_FAMILY = "-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"

# One more than the follow-up cap on the Sending tab, so a template can always be
# written for the last step the scheduler will ever ask for.
_MAX_STEP = 5

# Long enough that a fast typist is never fighting the renderer, short enough
# that the preview still reads as live.
_PREVIEW_DEBOUNCE_MS = 250

# The two appearance controls, and what they are called on screen. Both keys
# already exist in `core.settings`; until this tab there was no way to reach
# either of them but a hand edit of settings.json.
THEME_CHOICES = (("dark", "Dark"), ("light", "Light"))
DENSITY_CHOICES = (("comfortable", "Comfortable"), ("compact", "Compact"))

# A lead table, three rows of it, so that picking a density is picking something
# that can be seen rather than a word. Built by `components.table`, so the row
# height under the control is the row height every table in the app will get.
DENSITY_PREVIEW_COLUMNS = (
    C.Column("Business", kind="stretch", min_ch=12, max_ch=28),
    C.Column("Status", min_ch=7, max_ch=10),
    C.Column("Score", align="right", min_ch=5, max_ch=6),
)
DENSITY_PREVIEW_ROWS = (
    ("Northgate Roofing", "audited", "72"),
    ("Harbour Dental", "queued", "58"),
    ("Kelsey Plumbing", "sent", "41"),
)

# Every form label on this screen, so column one is the same width on every tab.
# The audit measured seven left edges on the Sending tab alone, four of them
# within 30px of each other and two of them 4px apart in adjacent groups: each
# grid sized its own label column to its own longest word, so no two groups
# agreed and nothing on the page lined up with anything else. The widest string
# here is what every label now reserves, measured in the font that is drawing
# it rather than guessed at in pixels.
FORM_LABELS = (
    "Provider", "API key", "Model", "Max tokens per lead", "Monthly token cap",
    "Company", "Your name", "Your title", "Website", "Reply-to", "Phone",
    "Calendar link", "Postal address", "Tone", "Services you sell",
    "Proof points", "Gmail address", "App password", "Display name",
    "Daily cap", "Sending days", "Sending window", "Timezone",
    "Minimum gap between sends", "Maximum gap between sends",
    "Daily cap per account", "Hourly cap per account", "Start at",
    "Increase each day by", "Stop increasing at", "Wait between touches",
    "Follow-ups per lead", "Unsubscribe address", "Theme", "Density",
)

# Everything a paste can carry that ends a line, with whatever horizontal space
# sits either side of it: a run of them collapses to the single space a subject
# field can actually draw.
_BREAK_RE = re.compile(r"[ \t]*[\r\n\x0b\x0c\x85\u2028\u2029]+[ \t]*")



# ── Templates ────────────────────────────────────────────────────────────────

# What each merge field resolves to, and what the copy does when it resolves to
# nothing. The second half is the half nobody can guess: `render` deletes a
# sentence that lost a value from its middle, so a line written around
# `{{gap_1}}` disappears entirely on a lead with no audit — which reads
# perfectly in the editor and ships as a shorter email. Fields the footer
# already carries say so, because writing them into the body prints them twice.
MERGE_FIELD_HELP: dict[str, tuple[str, str]] = {
    "business_name": ("the business name on the lead record",
                      'it reads "your business"'),
    "first_name": ("the contact's first name, or the given name in front of their "
                   "email address",
                   'it reads "there", so the greeting still works'),
    "category": ("the Google Maps category, lower-cased to sit mid-sentence",
                 'it reads "local" — keep a noun behind it'),
    "website_domain": ("the host the audit crawled, without the www",
                       'it reads "your site"'),
    "gap_1": ("the headline gap the audit found, written for the middle of a sentence",
              "the whole sentence holding it is deleted"),
    "gap_2": ("the second gap the audit found",
              "the whole sentence holding it is deleted"),
    "gap_1_evidence": ("the line on their own site that proves the headline gap",
                       "the whole sentence holding it is deleted"),
    "gap_1_subject": ("the headline gap phrased neutrally, for a subject line",
                      "the subject falls back to the business name"),
    "service_1": ("the best-matching service for the gaps found, in your catalogue's wording",
                  "nothing is ticked under Services you sell"),
    "service_2": ("the second-best service for the gaps found",
                  "nothing is ticked under Services you sell"),
    "service_3": ("the third-best service for the gaps found",
                  "nothing is ticked under Services you sell"),
    "ai_subject": ("the subject line the model wrote for this lead — on a first "
                   "touch it replaces the subject above automatically, so it does "
                   "not need writing in",
                   "the subject above is sent as written"),
    "ai_opener": ("the model's opening line about this business, kept only when it "
                  "says something about them and nothing about you",
                  "the whole sentence holding it is deleted"),
    "ai_ps": ("the model's postscript, already prefixed P.S.",
              "the line disappears cleanly"),
    "sender_name": ("your name from the Sender tab", "the line is trimmed around it"),
    "sender_title": ("your title from the Sender tab", "the line is trimmed around it"),
    "company": ("your company from the Sender tab — the footer prints it already",
                "the line is trimmed around it"),
    "company_website": ("your website from the Sender tab", "the line is trimmed around it"),
    "calendar_link": ("your calendar link, falling back to your website — this is "
                      "the one link a first touch is allowed",
                      "the sentence offering a call is deleted"),
    "phone": ("your phone number from the Sender tab", "the line is trimmed around it"),
    "postal_address": ("your postal address — the footer prints it already",
                       "the line is trimmed around it"),
    "unsubscribe_line": ("the opt-out sentence — the footer appends it already",
                         "a reply-to-unsubscribe sentence with no address in it"),
    "proof_point": ("one of your proof points, picked so the same lead always sees "
                    "the same one",
                    "the whole paragraph is deleted"),
}

# The skeleton New starts from. A blank editor is a worse blank page than a
# working first touch somebody can cut down, and every field in it is one that
# carries a fallback or deletes cleanly.
NEW_TEMPLATE_SUBJECT = "{{gap_1_subject}} at {{business_name}}"
NEW_TEMPLATE_BODY = (
    "Hi {{first_name}},\n"
    "\n"
    "{{ai_opener}}\n"
    "\n"
    "One thing stands out on {{website_domain}}: {{gap_1}}.\n"
    "\n"
    "We build {{service_1}} on the tools you run today, and nothing waits on "
    "someone remembering.\n"
    "\n"
    "Worth fifteen minutes to see if it fits? {{calendar_link}}\n"
    "\n"
    "{{sender_name}}\n"
    "{{sender_title}}, {{company}}"
)

# The lead the preview renders against. Invented, and obviously so, but shaped
# exactly like a real record after a crawl: a name, a mailbox that reads as a
# person, a host, a category, and two gaps straight out of `core.audit`'s own
# catalogue with its own evidence wording. Without it the editor shows nothing
# until a campaign exists, which is the wrong way round — the copy is written
# first.
SAMPLE_LEAD: dict = {
    "id": 0,
    "name": "Northgate Roofing",
    "email": "dana@northgateroofing.ca",
    "website": "https://northgateroofing.ca",
    "domain": "northgateroofing.ca",
    "category": "Roofing contractor",
    "phone": "+1 416 555 0142",
}

SAMPLE_AUDIT: dict = {
    "reachable": True,
    "final_url": "https://northgateroofing.ca",
    "gaps": [
        {"code": "no_online_booking", "title": "no online booking", "severity": 3,
         "subject_phrase": "online booking",
         "evidence": "asking for a time means filling in the form and waiting "
                     "for someone to answer",
         "services": ["appointment booking", "Lead Automation"]},
        {"code": "no_crm_signals", "title": "no CRM behind the form", "severity": 3,
         "subject_phrase": "leads from the contact form",
         "evidence": "nothing is hooked up to file a name and a number after the "
                     "form is sent",
         "services": ["CRM & Sales Automation", "automatically add leads to CRM"]},
    ],
}

# No `subject`: a model subject replaces the template's own on a first touch, and
# an editor that hides the line you are typing is not an editor.
SAMPLE_AI: dict = {
    "opener": "I read the booking page on northgateroofing.ca before writing, and "
              "every time slot ends in the same form.",
    "ps": "The booking form is still the only way to ask for a time on "
          "northgateroofing.ca.",
}


# ── Small helpers ────────────────────────────────────────────────────────────


def _t():
    """The theme every value on this screen is read from, at build time.

    Build time and not import time: `ui/components.py` resolves a control height
    and a pill fill in Python rather than in the sheet, so a widget carries the
    palette it was constructed under until something rebuilds it. `restyle`
    below is what rebuilds it.
    """
    return C.active_theme()


def _section_label(text: str) -> QLabel:
    return C.section_label(text)


def _hint(text: str) -> QLabel:
    """The quiet line under a control, capped at a readable measure.

    Through `components.hint`, which caps the line at 80 characters. This screen
    held most of the 24 wrapped labels the audit measured at 101 to 211
    characters per line — three times the width an eye can track back from
    without losing its place.
    """
    return C.hint(text)


def _divider() -> QFrame:
    return C.divider()


class _FormLabel(QLabel):
    """A form label that reserves the width every other form label reserves.

    Re-measured whenever the font changes, for the reason `components`'
    own measured widgets are: a widget is built before the sheet reaches it, so
    a width taken in the constructor is a width in whatever font Qt handed out
    at construction — and this one has to agree with every other label on the
    screen or the column it defines is not a column.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._cap()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._cap()

    def _cap(self) -> None:
        fm = self.fontMetrics()
        self.setMinimumWidth(max(fm.horizontalAdvance(one)
                                 for one in FORM_LABELS))
        self.updateGeometry()


def _form_label(text: str) -> QLabel:
    return _FormLabel(text)


def _form_grid() -> QGridLayout:
    """The one geometry every form on this screen is laid out on.

    Column zero holds a `_FormLabel` and never stretches; column one holds the
    control and takes whatever is going. Every grid states its spacings, because
    the audit found 36 layouts silently inheriting Qt's 9px default, which is
    off the 4px grid in both directions.
    """
    t = _t()
    grid = QGridLayout()
    grid.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                            t.space["0"])
    grid.setHorizontalSpacing(t.space["3"])
    grid.setVerticalSpacing(t.space["2"])
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    return grid


def _row_box() -> QHBoxLayout:
    """A row of controls that sits in column one, so it starts where they do."""
    t = _t()
    box = QHBoxLayout()
    box.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                           t.space["0"])
    box.setSpacing(t.space["2"])
    return box


def _loose(*widgets) -> QHBoxLayout:
    """Controls that sit at the right of column one rather than filling it.

    A combo handed a whole stretching column becomes a 700px well holding the
    word "Direct", so the column gives back everything the control does not ask
    for. The stretch goes in *front* of them, and that is the difference
    between a form and a settings pane: a row reads as a label on the left and
    its value on the right with the distance between them, so every value in a
    group lines up on one right edge whether it is a dropdown, a number or a
    row of day boxes. A field that holds a sentence — an address, a URL, a
    model name — is added to the column directly instead and fills it, because
    what that control is being asked for is room.
    """
    box = _row_box()
    box.addStretch()
    for widget in widgets:
        box.addWidget(widget)
    return box


# ── The register ─────────────────────────────────────────────────────────────
# The shape of this screen, in one place, because the audit's finding about it
# was that there were seven shapes: each group built its own geometry, sized
# its own label column to its own longest word, and hung its own explanatory
# line wherever the code happened to be — so no two groups agreed and adjacent
# ones sat 4px apart on the Sending tab alone.
#
# One shape, and it is System Settings': a section is a title and one muted
# line saying what it is for; under that are groups; a group is a caption over
# a box; a box is rows with a hairline between each pair, the label on the left
# and the control on the right; a row that needs explaining carries one quiet
# line under it; and a sentence about the whole group sits under the box rather
# than inside it. Nothing on this screen builds any of that itself, which is
# what makes "one grid" a property of the file rather than a habit.


def _measured(label: QWidget) -> QHBoxLayout:
    """A capped label in a row of its own, so it is measured at its own width.

    `ui/components.py` caps every wrapped label at the 80-character measure the
    type scale asks for, with `setMaximumWidth`. A QVBoxLayout asks an item
    `heightForWidth` for the width the *layout* has rather than the width the
    item will be given, so a sentence capped at 432px inside a 990px box is
    asked how tall it is at 990px, answers with one line, and is then drawn at
    432px in a 17px slot with its second line cut through. Measured on the
    Compliance tab: three switch descriptions given 16px each where the text
    needed 33, and the dry-run paragraph given 40 where it needed 65.

    A horizontal box allocates the widths first — respecting the maximum — and
    only then asks each item how tall it is at the width it actually got, so
    every one of them comes back with the height it will be drawn at.
    """
    row = _row_box()
    row.addWidget(label)
    row.addStretch()
    return row


def _page_head(title: str, description: str) -> QVBoxLayout:
    """What this section is, and the one line saying what it is for.

    `h2` and not `h1`: the shell's own header draws the screen's name at `h1`
    directly above this, so a section titled at the same tier would read as a
    second screen rather than as a page of the one on show.
    """
    t = _t()
    head = QVBoxLayout()
    head.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                            t.space["0"])
    head.setSpacing(t.space["1"])
    head.addWidget(C.heading(title, "h2"))
    if description:
        head.addLayout(_measured(C.body_label(description, tone="secondary")))
    return head


class _Group:
    """One box of rows: a caption over it, hairlines in it, footnotes under it.

    A caption is optional and is left off wherever it would only say the row's
    own label back — a box holding one "Postal address" row under a caption
    reading POSTAL ADDRESS is the same word twice in two type tiers, which
    reads as a mistake rather than as a hierarchy.
    """

    def __init__(self, column: QVBoxLayout, title: str = "", actions=()):
        t = _t()
        self.box = QVBoxLayout()
        self.box.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                    t.space["0"])
        self.box.setSpacing(t.space["2"])

        self.caption = _section_label(title) if title else None
        if title or actions:
            head = _row_box()
            if self.caption is not None:
                head.addWidget(self.caption)
            head.addStretch()
            for action in actions:
                head.addWidget(action)
            self.box.addLayout(head)

        self.frame = QFrame()
        self.frame.setObjectName("card")
        self.stack = QVBoxLayout(self.frame)
        self.stack.setContentsMargins(t.space["4"], t.space["3"], t.space["4"],
                                      t.space["3"])
        self.stack.setSpacing(t.space["2"])
        self.box.addWidget(self.frame)
        column.addLayout(self.box)
        self._rows = 0

    def _rule(self) -> None:
        """The hairline between two rows, and never above the first one."""
        if self._rows:
            self.stack.addWidget(_divider())
        self._rows += 1

    def field(self, label: str, control, note=None):
        """One labelled row: the name on the left, the control on the right.

        A grid of its own per row, which looks like the opposite of the "one
        grid" this section is about and is what makes it true. Every row is laid
        out on the same two columns — a `_FormLabel` reserving the widest label
        on the screen, then whatever the control asks for — so they all line up;
        and keeping the row's *description* out of the grid keeps the grid
        honest. Measured: a word-wrapped label inside a QGridLayout puts that
        layout through a height-for-width pass whose column geometry is the one
        left in `cellRect` afterwards, so three groups on the Sending tab
        reported column one at 187 while every other group on the screen
        reported 204 — the same 17px stagger between adjacent groups the audit
        found, arriving this time from Qt's own cached column data rather than
        from the label widths.
        """
        self._rule()
        grid = _form_grid()
        grid.addWidget(_form_label(label), 0, 0)
        if isinstance(control, QWidget):
            grid.addWidget(control, 0, 1)
        else:
            grid.addLayout(control, 0, 1)
        self.stack.addLayout(grid)
        return self._under(note)

    def wide(self, control, note=None):
        """A row that carries its own name: a checkbox, a preview, a bar."""
        self._rule()
        if isinstance(control, QWidget):
            self.stack.addWidget(control)
        else:
            self.stack.addLayout(control)
        return self._under(note)

    def _under(self, note):
        """The quiet line under the row above it — a sentence, or a made label.

        Full width and starting at the row's own left edge rather than under the
        control, because it explains the row and not the value: indented to
        column one it would read as a caption on whatever is in the box above
        it, which for a row of seven day checkboxes is the last of the seven.

        A made label because half of these are notes the screen updates and
        hides: `_hint("")` built once and kept, so that saying nothing costs no
        layout and nothing moves when it starts saying something.
        """
        if note is None or note == "":
            return None
        line = note if isinstance(note, QWidget) else _hint(note)
        self.stack.addLayout(_measured(line))
        return line

    def foot(self, note):
        """The sentence about the whole group, under the box rather than in it."""
        line = note if isinstance(note, QWidget) else _hint(note)
        self.box.addLayout(_measured(line))
        return line


def _line(placeholder: str = "") -> QLineEdit:
    """A single-line box at the one height every input on this screen is."""
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setFixedHeight(_t().control["md"])
    return edit


def _combo(items=()) -> QComboBox:
    """A dropdown of (value, label) pairs, the data being what gets stored."""
    combo = QComboBox()
    combo.setFixedHeight(_t().control["md"])
    for value, label in items or ():
        combo.addItem(str(label), value)
    return combo


def _status(label: QLabel, text: str, kind: str = "busy") -> None:
    """Colour a one-line result label. `kind` is ok | err | busy."""
    label.setObjectName({"ok": "status_ok", "err": "status_err"}.get(kind, "status_busy"))
    label.setText(text)
    label.setVisible(bool(text))
    label.style().unpolish(label)
    label.style().polish(label)


# A line of this app's own copy, to measure what one character of running text
# costs. `QFontMetrics.averageCharWidth` is the obvious answer and it is 15%
# wide: it averages the glyph table, where every capital and every digit counts
# once, and English prose is mostly lowercase and spaces.
_MEASURE_SAMPLE = ("App Passwords live in Google Account, Security, 2-Step "
                   "Verification. Your normal Gmail password will be rejected.")


def _chars_wide(fm, chars: int) -> int:
    """The width `chars` characters of running text take in `fm`."""
    unit = fm.horizontalAdvance(_MEASURE_SAMPLE) / max(1, len(_MEASURE_SAMPLE))
    if unit <= 0:
        unit = fm.averageCharWidth() or fm.horizontalAdvance("x") or 1
    return int(unit * max(1, int(chars)))


class _StatusNote(QLabel):
    """A result line: wrapped, capped at a readable measure, tone from the sheet.

    Not `components.body_label`, though the cap is the same 80 characters: these
    carry `status_ok`, `status_err` and `status_busy`, and a component paints
    its colour into its own stylesheet — which beats the sheet that is supposed
    to be colouring them.
    """

    _CHARS = 80

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self._cap()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._cap()

    def _cap(self) -> None:
        self.setMaximumWidth(_chars_wide(self.fontMetrics(), self._CHARS))
        self.updateGeometry()


class _StatusLine(QLabel):
    """The footer's one line, elided rather than allowed to widen the screen.

    A store failure is a sentence and not a word — "Could not save: [Errno 13]
    Permission denied" — and nothing in a footer row wraps, so a message put
    there is paid for in the screen's own minimum width: a window the user can
    no longer drag back down. Elided in place, with the whole of it in the
    tooltip, and reporting a minimum of nothing so the row can always shrink.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""

    def setText(self, text: str) -> None:
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._elide()

    def text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        room = self.width()
        QLabel.setText(self, self.fontMetrics().elidedText(
            self._full, Qt.ElideRight, room) if room > 0 else self._full)


def _preview_floor() -> int:
    """The shortest preview that still shows an email rather than an envelope.

    Summed off the document `_as_paper` builds rather than measured once and
    written down: the paper's own padding twice, the subject line, the rule
    under it, the To line, the gap after it, and two lines of the message. Two
    lines is the least that says anything about the copy — one is a greeting —
    so this is the floor the preview is never squeezed past, at any window size
    and with any number of findings on screen.
    """
    return int(2 * _PAPER_PAD
               + _PAPER_TYPE["subject"] * _PAPER_LEADING + _PAPER_RULE
               + _PAPER_TYPE["meta"] * _PAPER_LEADING + _PAPER_PAD
               + 2 * _PAPER_TYPE["body"] * _PAPER_LEADING + _PAPER_GAP)


def _editor_floor() -> int:
    """What is left for the boxes once the preview has its floor.

    The name row, the subject line, the body's own first lines and one row of
    the merge palette, which is enough to know where in the editor the column
    has been scrolled to.
    """
    t = _t()
    return 3 * t.control["md"] + t.control["xs"] + 4 * t.space["2"]


def _findings_ceiling() -> int:
    """How much of the page the validation findings may ever take.

    Nothing caps how many one template collects, and a word-wrapped label handed
    a dozen of them grew past 250px and pushed the preview under the bottom of
    the window — so the pane showing what the copy will look like disappeared
    exactly when the copy most needed looking at.
    """
    return 2 * _t().space["9"]


def _palette_ceiling() -> int:
    """How much of the page the 21 merge chips may ever take.

    Three rows. At the window minimum they reflow to five and took 165px of a
    308px column, which is what put the body editor 70px below the fold with its
    BODY label sliced in half.
    """
    t = _t()
    return 3 * t.control["xs"] + 2 * t.space["1"]


class _Spin(QSpinBox):
    """A number box as wide as the number it holds, and no wider.

    Its six call sites used to pass a pixel width each — 84, 90, 96, 100, 100,
    130 — which is six widths for four kinds of number and none of them right at
    any font but the one they were measured in. A count of digits is what the
    box is actually being asked for, so that is what it takes.
    """

    def __init__(self, chars: int, suffix: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("spin")
        self._chars = max(1, int(chars))
        self.setSuffix(suffix)
        self._cap()

    def measure(self) -> int:
        return self._chars

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._cap()

    def _cap(self) -> None:
        t = _t()
        fm = self.fontMetrics()
        room = fm.horizontalAdvance("0" * self._chars + self.suffix())
        # The well, its two arrows and the padding either side of them: all
        # spacing tokens, so the box grows with the density rather than with a
        # number somebody once measured.
        self.setFixedWidth(room + t.space["4"] + 2 * t.space["3"])
        self.setFixedHeight(t.control["md"])


def _spin(minimum: int, maximum: int, step: int = 1, suffix: str = "",
          chars: int = 4) -> QSpinBox:
    box = _Spin(chars, suffix)
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    return box


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _int_of(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rich(text: str, colour: str = "") -> str:
    """Coloured label text.

    The global sheet fixes a colour on every QLabel and a stylesheet beats
    `setPalette`, so emphasis inside a label has to arrive as rich text. The
    default is the theme's own body ink, so a line with no tone still reads as
    part of the page.
    """
    return '<span style="color:%s">%s</span>' % (
        colour or _t().color["text.primary"], html.escape(text))


def _zone_note(name: str) -> str:
    """The line under the timezone box: rich text, or "" for the machine clock.

    The question is not whether `name` looks like an IANA name, it is what the
    scheduler will make of it, so it goes to the scheduler's own resolver rather
    than to a second one here that could answer differently.

    An unresolvable zone is the dangerous case and it used to pass in silence.
    `core.campaign._zone` degrades to local time so that a bad name can never
    take the send loop down, which leaves the user believing their mail goes out
    at nine in the customer's morning when it goes out at nine in their own.
    """
    label = str(name or "").strip()
    if not label or label.lower() == "local":
        return ""
    if _campaign._zone({"send_timezone": label}) is None:
        return _rich(
            "%s could not be resolved on this machine, so the window would follow "
            "this computer's clock instead. Reinstall requirements.txt — it ships "
            "the tzdata package — and restart." % label, _tone_ink("_RED"))
    return _rich("Resolved: the hours above are kept in %s, whatever this machine's "
                 "clock reads." % label, _tone_ink("_GREEN"))


def _step_name(step: int) -> str:
    step = max(0, _int_of(step))
    return "First touch" if step == 0 else "Follow-up %d" % step


# ── What the scheduler will actually do ──────────────────────────────────────
# `core.campaign` composes rather than obeys: three caps become their minimum,
# an inverted window becomes one hour, an empty day set becomes Monday to
# Friday. Every one of those used to happen in silence with the requested number
# still on screen — a user who set a 40/day cap and left the warm-up ramp on was
# told 40 and sent 10. These four answer "and what will really happen", and each
# returns "" when nothing is being overridden, because a note beside every field
# is a note nobody reads.


def _hours_note(start: int, end: int) -> str:
    """The window the scheduler keeps, when it is not the one that was asked for."""
    kept_start, kept_end = _campaign._hours(
        {"send_start_hour": start, "send_end_hour": end})
    if (kept_start, kept_end) == (start, end):
        return ""
    return ("Sends between %02d:00 and %02d:00. A window that ends before it "
            "opens would send nothing at all, so the scheduler holds it to one "
            "hour." % (kept_start, kept_end))


def _days_note(chosen) -> str:
    """The days the scheduler keeps, when none were ticked."""
    kept = _campaign._send_days({"send_days": list(chosen)})
    if set(chosen) == kept:
        return ""
    return ("Sends on %s. No day ticked means every day is off, which the "
            "scheduler reads as the working week rather than as never."
            % ", ".join(DAY_NAMES[day] for day in sorted(kept)))


def _gap_note(low: int, high: int) -> str:
    """The pacing range the scheduler keeps, when the two were the wrong way up."""
    kept_low, kept_high = _campaign._gap_bounds(
        {"send_min_gap_sec": low, "send_max_gap_sec": high})
    if (kept_low, kept_high) == (low, high):
        return ""
    return ("Waits between %d and %d seconds. A minimum above the maximum is a "
            "range nothing fits in, so the two are swapped."
            % (kept_low, kept_high))


def _cap_note(settings: dict, accounts) -> str:
    """The daily cap in force, when the warm-up ramp is lower than the number set.

    Asked of `account_daily_cap` rather than worked out again here, because a
    second implementation of the same rule is a second answer waiting to differ
    from the one the send loop uses.
    """
    asked = max(0, _int_of(settings.get("daily_cap_per_account"), 0))
    rows = [a for a in (accounts or []) if isinstance(a, dict)] or [{}]
    kept = min(_campaign.account_daily_cap(account, settings)
               for account in rows)
    if kept >= asked or asked <= 0:
        return ""
    return ("Sends %d today, not %d. Each account's own cap and the warm-up "
            "ramp both have to allow a message, and the smallest of the three "
            "is what goes out." % (kept, asked))


def _mono_family() -> str:
    """The first monospace family this machine actually has.

    Named one at a time rather than as a CSS list: Qt 5's stylesheet parser
    hands `font-family` straight to `QFont::setFamily`, so a comma-separated
    fallback chain resolves to whatever the whole string matched — usually
    nothing, and the body editor silently goes back to a proportional font with
    the merge fields out of line.
    """
    try:
        families = set(QFontDatabase().families())
    except Exception:
        return "monospace"
    for name in ("Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono",
                 "Liberation Mono", "Courier New"):
        if name in families:
            return name
    return "monospace"


def _field_tooltip(field: str) -> str:
    resolves, empty = MERGE_FIELD_HELP.get(
        field, ("a value the campaign supplies", "the sentence around it is deleted"))
    return "{{%s}}\n\nResolves to %s.\nWhen it is empty, %s." % (field, resolves, empty)


# ── Template store ─────────────────────────────────────────────

# `core.templates` owns the file. This section owns what the screen does when a
# call into it does not do what it says: every one of them reaches the disk, and
# the disk is where a profile folder turns out to be read-only and a home
# directory turns out to be full. None of them may raise into a Qt slot, and
# none of them may report a write that did not land.

def _all_templates() -> list:
    try:
        found = list(_templates.all_templates())
    except Exception:
        found = list(_templates.BUILTIN_TEMPLATES)
    return [t for t in found if str(getattr(t, "id", "") or "").strip()]


def _is_builtin(template_id: str) -> bool:
    try:
        return bool(_templates.is_builtin(template_id))
    except Exception:
        return False


def _is_overridden(template_id: str) -> bool:
    try:
        return bool(_templates.is_overridden(template_id))
    except Exception:
        return False


def _marker(template_id: str) -> str:
    """The word that tells the user whose template this is."""
    if not _is_builtin(template_id):
        return "custom"
    return "edited" if _is_overridden(template_id) else "built-in"


def _save_template(template) -> bool:
    """Write a template, and confirm it arrived.

    `save_user_template` returns nothing and swallows a store it could not
    write, so the only honest confirmation is reading the id back and finding
    the copy that was just sent. Saying "Saved" over a write that never happened
    is how an evening of edits is lost.

    The name is deliberately not compared. The store fills a blank one in with
    the id, because a blank row in the picker is a template nobody can pick, and
    the reload straight after this puts what was really stored back on screen.
    """
    try:
        _templates.save_user_template(template)
        stored = _templates.get_template(str(template.id))
    except Exception:
        return False
    return bool(stored) and stored.body == template.body         and stored.subject == template.subject         and _int_of(stored.step) == _int_of(template.step)


def _delete_template(template_id: str) -> bool:
    try:
        _templates.delete_user_template(template_id)
        return _templates.get_template(template_id) is None
    except Exception:
        return False


def _reset_stored_template(template_id: str) -> bool:
    try:
        _templates.reset_template(template_id)
    except Exception:
        return False
    return not _is_overridden(template_id)


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(text or "").lower())).strip("_")


def _new_id(name: str, step: int, taken=()) -> str:
    """An id for a template the user just made, unique against `taken`.

    Minted once, at creation, and never again: renaming a template that already
    has queued follow-ups pointing at it would otherwise orphan them. The store
    picks the id; the loop below only exists so that a store which cannot answer
    still hands back something usable rather than an empty string, which would
    be a template that cannot be saved, picked or reset.
    """
    try:
        made = str(_templates.new_template_id(name, step) or "").strip()
    except Exception:
        made = ""
    if made and made not in taken:
        return made
    base = _slug(name) or "template"
    candidate = "%s_%d" % (base, _int_of(step))
    suffix = 2
    while candidate in taken:
        candidate = "%s_%d_%d" % (base, _int_of(step), suffix)
        suffix += 1
    return candidate


def _validate_template(template, ctx: dict | None = None) -> list[dict]:
    """Findings for `template`, checked against `ctx` where there is one.

    The context is what turns "your body has no postal address" — true of every
    well-written body, because the footer carries it — into "the profile this
    would send from has no postal address", which is the one worth saying.
    """
    try:
        found = _templates.validate_template(template, ctx)
    except Exception:
        return []
    return [i for i in found if isinstance(i, dict)] if isinstance(found, list) else []


def _note(field: str, message: str) -> dict:
    """A finding this screen raised itself, shaped like one from the store."""
    return {"level": "warning", "field": field, "message": message}


def _merged_subject(template, ctx: dict | None) -> str:
    """The subject with its fields filled, taken before the send path cuts it.

    `render` hands back the line that will actually be sent, and `_clean_subject`
    has already cut that to `SUBJECT_MAX` — so anything measured off it is at
    most the limit, and a counter fed from it can never say the limit was
    passed. This is the same string `_clean_subject` measures when it decides
    whether to cut, one step earlier: resolved and tidied, not yet shortened.

    Falls back to what is typed rather than to nothing, so a store that has
    moved its internals costs the count its accuracy and not its existence.
    """
    source = str(getattr(template, "subject", "") or "")
    merged = dict(ctx or {})
    if not merged:
        return source
    # The same swap `render` makes: a model subject replaces the template's own
    # on a first touch, so that is the line whose length matters there.
    if _int_of(getattr(template, "step", 0)) == 0:
        source = str(merged.get("ai_subject") or "").strip() or source
    try:
        return _templates._subject_rules(_templates._resolve(source, merged))
    except Exception:
        return source


# ── Merge field palette ──────────────────────────────────────────────────────

class _FlexEdit(QTextEdit):
    """A text box that asks for the height it was given a minimum for.

    `QAbstractScrollArea.sizeHint` is a flat 192px whatever the box holds, and a
    scroll area works out how tall a column needs to be by adding up
    `heightForWidth` — which falls back to `sizeHint` for anything that has
    none, these two boxes included. Two of them added 216px the column did not
    need, so an editor that fit inside a 760px window reported itself too tall
    and pushed its own last line under a scrollbar. Stretch still expands both
    the moment there is room.
    """

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.minimumHeight())


class _ChipBar(QWidget):
    """Every merge field as a chip that wraps to whatever width it is given.

    A grid would have to pick a column count, and the field names run from
    `phone` to `gap_1_evidence`; at four columns half the row is air and at six
    the long names are cut. So the chips are laid out by hand and the widget
    reports the height that layout actually needed, which is what lets the page
    around it scroll instead of clipping the last row.

    They are `components.chip()` now and not `QPushButton#tab`. Twenty-one merge
    fields wearing the app's top-level navigation control said, in the only
    language a control has, that clicking one navigates somewhere — and the
    keyboard cursor was the selected-tab fill exactly, so the chip under the
    caret read as the open tab. A chip is a short value; a tab is a place. They
    are different components because they are different promises.

    No chip may take the focus — a chip that could would take it on the click
    too, and the caret the field is inserted at goes with it. That left the
    whole palette, and every tooltip in it, unreachable from the keyboard. So
    the bar takes the focus the chips refuse: Tab lands here once, the arrow
    keys walk the row, and Enter inserts.
    """

    insert_requested = pyqtSignal(str)

    _STEPS = {Qt.Key_Right: 1, Qt.Key_Down: 1, Qt.Key_Left: -1, Qt.Key_Up: -1}

    def __init__(self, fields, parent=None):
        super().__init__(parent)
        t = _t()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAccessibleName("Merge fields")
        self._gap = t.space["1"]
        self._height = t.control["xs"]
        self._chips: list = []
        self._at = 0

        # The chip's resting look, lifted off a chip and hung on the bar so that
        # every chip wears it without owning it. What that buys is the cursor
        # below: a widget's own sheet beats an ancestor's whatever the
        # specificity, so the one chip Enter would insert is the one chip with a
        # sheet of its own, and clearing that sheet puts it back in the row.
        sample = C.chip("sample")
        self._resting = sample.styleSheet()
        sample.deleteLater()
        self.setStyleSheet(self._resting)

        # A ring and not a fill, and 1px like every other ring in the app: the
        # box never changes size, so nothing behind the cursor shifts along the
        # row on an arrow key. `accent.border` is the focus token, which is what
        # this is — there is nothing selected in a palette, only somewhere the
        # keyboard is.
        self._marked = "%s QFrame { border: %dpx solid %s; }" % (
            self._resting, C.BORDER, t.color["accent.border"])

        for field in fields:
            chip = C.chip(field)
            chip.setParent(self)
            chip.setStyleSheet("")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(_field_tooltip(field))
            chip.clicked.connect(lambda name=field:
                                 self.insert_requested.emit(name))
            self._chips.append(chip)
        self._reflow()

    def current_field(self) -> str:
        return self._chips[self._at].text() if self._chips else ""

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._mark()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._mark()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if not self._chips:
            super().keyPressEvent(event)
            return
        if key in self._STEPS:
            self._at = (self._at + self._STEPS[key]) % len(self._chips)
        elif key == Qt.Key_Home:
            self._at = 0
        elif key == Qt.Key_End:
            self._at = len(self._chips) - 1
        elif key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.insert_requested.emit(self.current_field())
            return
        else:
            super().keyPressEvent(event)
            return
        self._mark()

    def _mark(self) -> None:
        """Show which chip Enter would insert, and say what that field does.

        The tooltips are the point of the palette — what a field resolves to and
        what happens to the sentence when it resolves to nothing — and one only a
        mouse can reach is one half the users never read.
        """
        marked = self.hasFocus()
        for index, chip in enumerate(self._chips):
            chip.setStyleSheet(
                self._marked if marked and index == self._at else "")
        if not marked:
            QToolTip.hideText()
            return
        chip = self._chips[self._at]
        QToolTip.showText(chip.mapToGlobal(chip.rect().bottomLeft()),
                          chip.toolTip(), chip)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        """How tall the palette would be in `width`, without moving anything.

        The pane around this one asks before it hands over a width, so the
        answer has to be available without the chips having been placed yet.
        """
        return self._flow(max(1, int(width)), place=False)

    def rows(self, width: int = 0) -> int:
        """How many rows the chips wrap to, which is what the ceiling is set in."""
        wanted = self._flow(max(1, int(width or self.width())), place=False)
        return max(1, (wanted + self._gap) // (self._height + self._gap))

    def _reflow(self) -> None:
        wanted = self._flow(max(1, self.width()), place=True)
        # Guarded: setting a fixed height re-runs the parent layout, which can
        # hand this widget a new width and call straight back in here.
        if wanted and wanted != self.minimumHeight():
            self.setFixedHeight(wanted)

    def _flow(self, width: int, *, place: bool) -> int:
        x = y = row_height = 0
        for chip in self._chips:
            chip_width = min(chip.sizeHint().width(), width)
            if x and x + chip_width > width:
                x = 0
                y += row_height + self._gap
                row_height = 0
            if place:
                chip.setGeometry(x, y, chip_width, self._height)
            x += chip_width + self._gap
            row_height = max(row_height, self._height)
        return y + row_height


class _FlatLine(QLineEdit):
    """A one-line field whose stored value stays one line.

    A subject pasted out of a document arrives with its newlines still in it and
    a QLineEdit draws none of them: `displayText` shows spaces, `text` hands back
    the breaks, and the value written to the store then holds characters the
    editor cannot render back. Nothing downstream is at risk — `_clean_subject`
    and the mailer's own header guard both strip them — but a field that saves
    something other than what it shows is a field that cannot be proof-read.
    Flattening on the way in makes the two the same thing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flattening = False
        self.textChanged.connect(self._flatten)

    def _flatten(self, text: str) -> None:
        if self._flattening:
            return
        flat = _BREAK_RE.sub(" ", text)
        if flat == text:
            return
        at = self.cursorPosition() - (len(text) - len(flat))
        self._flattening = True
        try:
            self.setText(flat)
        finally:
            self._flattening = False
        self.setCursorPosition(max(0, min(at, len(flat))))


class _BoundedPane(QScrollArea):
    """Something that can grow without limit, given a ceiling and a scrollbar.

    Two things on the Templates tab can: the validation findings, which nothing
    caps — a word-wrapped label handed a dozen of them grew past 250px and
    pushed the preview under the bottom of the window, so the pane showing what
    the copy will look like disappeared exactly when the copy most needed
    looking at — and the merge-field palette, whose 21 chips reflow to five rows
    at the window minimum and took 165px of a 308px column, which is what put
    the body editor 70px below the fold with its BODY label at the very edge of
    it. Both are references. Neither may cost the thing being written.

    `heightForWidth` rather than a plain maximum, because both of them wrap: how
    tall they are is a function of how wide the column is, and a fixed height
    would be either mostly air or a scrollbar depending on the window.
    """

    def __init__(self, body: QWidget, ceiling: int, parent=None):
        super().__init__(parent)
        self._ceiling = max(1, int(ceiling))
        self.setWidget(body)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMaximumHeight(self._ceiling)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def ceiling(self) -> int:
        return self._ceiling

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        body, chrome = self.widget(), 2 * self.frameWidth()
        wanted = body.heightForWidth(max(1, width - chrome)) if body is not None else 0
        if wanted <= 0 and body is not None:
            wanted = body.sizeHint().height()
        return max(0, min(wanted + chrome, self._ceiling))

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(),
                     self.heightForWidth(max(1, self.width())))


def _secret_field(placeholder: str = "") -> QWidget:
    """A password box with a reveal toggle, from the shared library.

    Both this screen and the outreach screen implemented one, differently. A
    live Gmail app password sitting in a visible box is both a shoulder-surfing
    hole and the reason users paste screenshots of their credentials into
    support threads; a masked field with no way to check what was typed is how
    they get locked out instead. `components.text_field(secret=True)` is that
    control, once, and it carries the error line this one never had.
    """
    field = C.text_field(placeholder=placeholder, secret=True)
    field.editingFinished = field.edit.editingFinished
    field.textChanged = field.edit.edit.textChanged
    field.toggle = field.edit.toggle
    return field


class ModelComboBox(QComboBox):
    """An editable QComboBox that acts like QLineEdit (with text/setText API) for seamless settings integration."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setPlaceholderText(placeholder)
        self.setFixedHeight(_t().control["md"])

    def text(self) -> str:
        return self.currentText()

    def setText(self, val: str) -> None:
        self.setEditText(val)


class _FetchModelsProbe(QThread):
    """Fetches AI models from the provider's API on a background thread to prevent UI freezing."""
    result_signal = pyqtSignal(list)

    def __init__(self, provider: str, api_key: str, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.api_key = api_key

    def run(self) -> None:
        try:
            models = ai_client.fetch_models(self.provider, self.api_key)
            self.result_signal.emit(models)
        except Exception:
            self.result_signal.emit([])


class _Probe(QThread):
    """Runs one blocking credential check off the GUI thread.

    Both checks talk to a remote server with a timeout measured in seconds. Run
    inline they would freeze the window for long enough to look like a hang, and
    Windows would paint the "not responding" ghost over it.
    """

    result_signal = pyqtSignal(bool, str, int)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task
        self._cancelled = False

    def stop(self) -> None:
        """Drop the answer rather than delivering it into a closing window.

        The socket call itself cannot be interrupted; what this prevents is a
        result arriving after the widgets that would display it are gone.
        """
        self._cancelled = True

    def run(self) -> None:
        try:
            ok, message, latency_ms = self._task()
        except Exception as exc:
            # The callees promise not to raise, but a thread that dies silently
            # leaves its button disabled and its row stuck on "Checking…"
            # for ever, which is worse than showing the exception text.
            ok, message, latency_ms = False, str(exc), 0
        if not self._cancelled:
            self.result_signal.emit(bool(ok), str(message), int(latency_ms))


# ── Gmail account row ────────────────────────────────────────────────────────

class _AccountRow(QWidget):
    """One Gmail sending account: credentials, caps, warm-up and a live Verify.

    The row owns no thread — it asks the screen to verify it and is told the
    answer, so all thread lifetime stays in one place.
    """

    remove_requested = pyqtSignal(object)
    verify_requested = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, account: dict, index: int, parent=None):
        super().__init__(parent)
        t = _t()
        root = QVBoxLayout(self)
        root.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                               t.space["0"])
        root.setSpacing(t.space["3"])

        self.enabled_cb = C.toggle(
            "Enabled", help="Disabled accounts stay configured but never send")
        self.remove_btn = C.button("Remove", kind="danger", size="sm",
                                   on_click=lambda: self.remove_requested.emit(self))
        self.remove_btn.setToolTip("Take this mailbox off the sending rota")

        # One account is one group, in the same box as every other group on
        # this screen: its ordinal is the caption and the two commands that act
        # on the whole mailbox sit beside it, so the card's own edge is what
        # separates one account from the next.
        group = _Group(root, "Account %d" % index,
                       actions=(self.enabled_cb, self.remove_btn))
        self.title_label = group.caption

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@gmail.com")
        self.email_edit.setFixedHeight(t.control["md"])
        group.field("Gmail address", self.email_edit)

        self.password_field = _secret_field("16-character app password")
        self.verify_btn = C.button(
            "Verify", kind="secondary", size="md",
            on_click=lambda: self.verify_requested.emit(self))
        self.verify_btn.setToolTip(
            "Sign in to Gmail with these credentials without sending anything")
        self.status_label = _StatusNote()
        self.status_label.setObjectName("status_busy")
        self.status_label.hide()
        password_row = _row_box()
        password_row.addWidget(self.password_field, stretch=1)
        password_row.addWidget(self.verify_btn)
        group.field("App password", password_row, note=self.status_label)

        self.display_edit = QLineEdit()
        self.display_edit.setPlaceholderText("Name recipients see, e.g. Sam Rivera")
        self.display_edit.setFixedHeight(t.control["md"])
        group.field("Display name", self.display_edit)

        caps_row = _row_box()
        caps_row.addStretch()
        self.daily_cap_spin = _spin(1, 500, 5, "/day", chars=3)
        self.daily_cap_spin.setToolTip("This account's own ceiling; the global cap still applies")
        self.warmup_cb = C.toggle(
            "Warm up from",
            help="Ramp this account's daily volume from the warm-up start date")
        self.warmup_date = QDateEdit()
        self.warmup_date.setObjectName("spin")
        self.warmup_date.setDisplayFormat("yyyy-MM-dd")
        self.warmup_date.setCalendarPopup(True)
        self.warmup_date.setFixedHeight(t.control["md"])
        self.warmup_cb.toggled.connect(self.warmup_date.setEnabled)
        caps_row.addWidget(self.daily_cap_spin)
        caps_row.addWidget(self.warmup_cb)
        caps_row.addWidget(self.warmup_date)
        # What this account will actually send today, when that is not what the
        # box above it says. The three caps compose as a minimum in
        # `core.campaign`, so an account ramping up from a warm-up date sends
        # ten while the field reads forty.
        self.effective_label = _hint("")
        self.effective_label.setVisible(False)
        group.field("Daily cap", caps_row, note=self.effective_label)

        self.imap_cb = C.toggle(
            "Read replies and bounces on this mailbox (IMAP)",
            help="Lets the app detect replies and hard bounces on this mailbox")
        group.wide(self.imap_cb)

        self._load(account or {})
        self._watch()

    def renumber(self, index: int) -> None:
        """The ordinal in the heading, after a row above this one has gone.

        Upper-cased here as well as in `components.section_label`, which does it
        once at construction: a caption renumbered through `setText` alone comes
        back in sentence case and reads as a different tier from the caption
        over every other group on the screen.
        """
        self.title_label.setText(("Account %d" % index).upper())

    def _watch(self) -> None:
        """One signal out for every edit anywhere in the row.

        Wired after `_load`, so restoring a saved account is not reported as a
        change to it.
        """
        for edit in (self.email_edit, self.display_edit):
            edit.textChanged.connect(lambda _text: self.changed.emit())
        self.password_field.textChanged.connect(lambda _text: self.changed.emit())
        self.daily_cap_spin.valueChanged.connect(lambda _v: self.changed.emit())
        self.warmup_date.dateChanged.connect(lambda _d: self.changed.emit())
        for box in (self.enabled_cb, self.imap_cb, self.warmup_cb):
            box.toggled.connect(lambda _on: self.changed.emit())

    def _load(self, account: dict) -> None:
        self.email_edit.setText(str(account.get("email") or ""))
        self.display_edit.setText(str(account.get("display_name") or ""))
        self.enabled_cb.setChecked(bool(account.get("enabled", True)))
        self.imap_cb.setChecked(bool(account.get("imap_enabled", False)))
        try:
            cap = int(account.get("daily_cap") or SMTP_ACCOUNT_DEFAULTS["daily_cap"])
        except (TypeError, ValueError):
            cap = SMTP_ACCOUNT_DEFAULTS["daily_cap"]
        self.daily_cap_spin.setValue(max(1, min(500, cap)))

        started = QDate.fromString(str(account.get("warmup_started") or ""), "yyyy-MM-dd")
        self.warmup_cb.setChecked(started.isValid())
        self.warmup_date.setDate(started if started.isValid() else QDate.currentDate())
        self.warmup_date.setEnabled(self.warmup_cb.isChecked())

    def show_effective_cap(self, settings: dict) -> None:
        """Say what this mailbox will really send today, when it is not the box.

        Asked of `core.campaign.account_daily_cap`, which is the function the
        send loop asks, so the number on screen cannot drift from the number
        that goes out.
        """
        asked = self.daily_cap_spin.value()
        kept = _campaign.account_daily_cap(self.to_dict(), settings or {})
        note = ("" if kept >= asked else
                "Sends %d today, not %d — the warm-up ramp or the global cap is "
                "lower than this." % (kept, asked))
        self.effective_label.setText(note)
        self.effective_label.setVisible(bool(note))

    # ── verify ──

    def email(self) -> str:
        return self.email_edit.text().strip()

    def app_password(self) -> str:
        return self.password_field.text()

    def set_password(self, value: str) -> None:
        self.password_field.setText(value)

    def set_verifying(self) -> None:
        self.verify_btn.setEnabled(False)
        _status(self.status_label, "Signing in to Gmail…", "busy")

    def show_verify_result(self, ok: bool, message: str, latency_ms: int) -> None:
        self.verify_btn.setEnabled(True)
        if ok:
            _status(self.status_label, f"✓ Signed in ({latency_ms} ms)", "ok")
        else:
            _status(self.status_label, message, "err")

    def to_dict(self) -> dict:
        """The stored shape, with the password left blank for `set_secret`."""
        return {
            "email": self.email(),
            "app_password": "",
            "display_name": self.display_edit.text().strip(),
            "daily_cap": self.daily_cap_spin.value(),
            "enabled": self.enabled_cb.isChecked(),
            "warmup_started": (
                self.warmup_date.date().toString("yyyy-MM-dd")
                if self.warmup_cb.isChecked() else ""
            ),
            "imap_enabled": self.imap_cb.isChecked(),
        }


# ── Screen ───────────────────────────────────────────────────────────────────

class SettingsScreen(QWidget):
    back_signal = pyqtSignal()
    saved_signal = pyqtSignal(dict)

    TABS = ("AI", "Sender", "Templates", "Gmail", "Sending", "Compliance",
            "Appearance")

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self._probes: list[_Probe] = []
        self._account_rows: list[_AccountRow] = []
        self._templates: list = []
        self._template_id = ""
        self._template_dirty = False
        self._template_loading = False
        self._merge_target = None
        self._merge_span = (-1, -1)
        self._template_notes: list = []
        self._dirty = False
        self._loading = False
        self._building = False
        self._published = None
        self._publish_timer = QTimer(self)
        self._publish_timer.setSingleShot(True)
        self._publish_timer.timeout.connect(self._push_to_shell)
        self._build()
        self._load_into_ui()

    # ── public API ──

    def refresh(self) -> None:
        """Re-read the settings and the template store before showing.

        `InputScreen` writes the same settings file (headless, the limit cap,
        saved searches). Editing a copy loaded at startup would silently roll
        those back on the next save here. The templates are re-read for the
        same reason: the file can be edited by hand, and a picker showing
        yesterday's copy is a template somebody edits twice.

        What it will not do is reload over edits nobody has committed. Back used
        to save on the way out, so navigating away and back was a round trip
        through the disk; the two explicit commands mean leaving decides
        nothing, and a reload here would quietly decide it after all — the one
        outcome Discard exists to make the user's own choice.
        """
        self._publish_to_shell()
        if self._dirty:
            return
        self.settings = load_settings()
        self._load_into_ui()

    def subtabs(self) -> tuple:
        """The six-and-one tabs, for whatever chrome is drawing them.

        The screens no longer carry a top bar of their own — the audit found
        four screens with four different ones — so the tabs are handed back
        rather than drawn here. `(labels, on_change, current)` is the shape
        `AppShell.set_subtabs` takes.
        """
        return tuple(self.TABS), self._goto_tab, self.pages.currentIndex()

    def _goto_tab(self, index: int) -> None:
        self.pages.setCurrentIndex(max(0, min(len(self.TABS) - 1, int(index))))

    def _host(self) -> tuple:
        """The shell this screen is sitting in, and the key it is filed under.

        Found by asking rather than by being told, because the window registers
        a *factory*: a screen is built on its first visit, from inside the call
        that is about to show it, and there is no moment before that at which
        anything could hand it a reference. `built(key)` is the shell's own
        public answer to "which screen is this", so nothing private is reached
        into and a screen with no shell around it — every test that builds one
        on its own — simply gets nothing back.
        """
        host = self.parentWidget()
        while host is not None:
            if hasattr(host, "set_subtabs") and hasattr(host, "built"):
                for key in host.built():
                    if host.built(key) is self:
                        return host, key
                return None, ""
            host = host.parentWidget()
        return None, ""

    def _publish_to_shell(self) -> None:
        """Ask for the shell's second row to be brought up to date, once.

        On the next turn of the event loop and never inside the call that asked,
        for two reasons that are the same reason. `AppShell.set_subtabs` rebuilds
        the row, and one route in here is a click on a button in that row — the
        shell's own `_on_subtab` moves the buttons by hand rather than rebuilding
        them for exactly this. The other is a keystroke: an editor marks itself
        unsaved on every character typed, and a row of buttons torn down and
        rebuilt per keypress is a row that flickers and loses the pointer.
        """
        if not self._publish_timer.isActive():
            self._publish_timer.start(0)

    def _push_to_shell(self) -> None:
        host, key = self._host()
        if host is None:
            return
        state = (key, self.pages.currentIndex(), self._outstanding())
        if state == self._published:
            return
        self._published = state
        host.set_subtabs(key, self.TABS, self._goto_tab, state[1])
        host.set_context(key, state[2], tone="warning")

    def _outstanding(self) -> str:
        """What has been changed and not committed, in one line or none."""
        if self._dirty and self._template_dirty:
            return "Unsaved changes, and an unsaved template"
        if self._dirty:
            return "Unsaved changes"
        if self._template_dirty:
            return "Unsaved template"
        return ""

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Forgotten rather than compared: the shell rebuilds its own chrome on a
        # theme change, so a row that matches what was published last is not
        # necessarily a row that is still on screen.
        self._published = None
        self._publish_to_shell()

    def hideEvent(self, event) -> None:
        """Leaving this screen decides nothing.

        Back used to write the whole file on the way past and put "Saved" on a
        screen the user had already left, so a half-finished sending window was
        committed by the act of walking away and nothing on screen said so. The
        footer owns both decisions now: navigating away keeps every edit exactly
        where it is, and the shell's own context line goes on saying there are
        some.
        """
        super().hideEvent(event)

    # ── appearance ───────────────────────────────────────────────────────────

    def restyle(self) -> None:
        """Wear the palette and the density the app is in now.

        Rebuilt rather than repolished, and `MainWindow._repolish` calls this on
        every screen that has one for the reason it exists: `ui/components.py`
        resolves a control height and a pill fill in Python at build time and
        writes them into the widget's own stylesheet, which beats the
        application's — so a repolish alone leaves this screen wearing whatever
        it was constructed in.

        Nothing typed is lost. What is on screen and not on disk is folded back
        into `self.settings` first and read out again after, which is the round
        trip Save makes minus the file, and the open template is carried across
        by hand because it lives in a second store this is not writing to. The
        two dirty flags survive with it: a theme change is not a save.
        """
        editor = self._editor_state()
        at = self.pages.currentIndex()
        dirty, template_dirty = self._dirty, self._template_dirty
        notes, status = list(self._template_notes), self.save_status.text()
        self._collect()
        worn = C.active_theme()
        self.settings["theme"], self.settings["density"] = worn.name, worn.density

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
        self._load_into_ui()
        self._goto_tab(at)
        self._restore_editor(editor, notes)
        self._dirty, self._template_dirty = dirty, template_dirty
        if status:
            self.save_status.setText(status)
            self.save_status.setVisible(True)
        self._refresh_footer()
        self._published = None
        self._publish_to_shell()

    def _editor_state(self) -> dict:
        """What the template editor is holding, which no store has a copy of."""
        return {
            "id": self._template_id,
            "name": self.template_name_edit.text(),
            "subject": self.template_subject_edit.text(),
            "body": self.template_body_edit.toPlainText(),
            "step": self.template_step_combo.currentIndex(),
        }

    def _restore_editor(self, state: dict, notes) -> None:
        if not state.get("id"):
            return
        self._template_loading = True
        try:
            self._template_id = state["id"]
            self._select_row(state["id"])
            self.template_name_edit.setText(state["name"])
            self.template_subject_edit.setText(state["subject"])
            self.template_body_edit.setPlainText(state["body"])
            self.template_step_combo.setCurrentIndex(state["step"])
        finally:
            self._template_loading = False
        self._template_notes = list(notes)
        self._merge_span = (-1, -1)
        self._refresh_template_buttons()
        self._refresh_template_preview()

    # ── construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        """The seven pages, the toaster and the footer. No bar of its own.

        No header, no Back button and no tab strip: the audit found four screens
        carrying four different top bars, each with its own Home and Settings,
        and the shell draws the one that is left. `subtabs()` hands these seven
        back to it.
        """
        t = _t()
        # Set while the pages are going up, because half of them wire a signal
        # to a note that reads a widget on another page: the Days boxes are
        # connected before the warm-up spins the note asks about exist.
        self._building = True
        root = QVBoxLayout(self)
        root.setContentsMargins(t.space["5"], t.space["4"], t.space["5"],
                                t.space["4"])
        root.setSpacing(t.space["3"])

        self.pages = QStackedWidget()
        self.pages.addWidget(self._scrolled(self._build_ai_page()))
        self.pages.addWidget(self._scrolled(self._build_sender_page()))
        # Not `_scrolled`: this page scrolls its own editor column and keeps the
        # picker, its four buttons and the preview pinned. Wrapped whole, New
        # and Delete sit below the preview and go off the bottom of a 760px
        # window, so the way to add a template is to scroll past the one you are
        # writing.
        self.pages.addWidget(self._build_templates_page())
        # Nor this one: "Add account" scrolled with the accounts, so it moved
        # further out of reach with every account added and at the window
        # minimum it started below the fold.
        self.pages.addWidget(self._build_gmail_page())
        self.pages.addWidget(self._scrolled(self._build_sending_page()))
        self.pages.addWidget(self._scrolled(self._build_compliance_page()))
        self.pages.addWidget(self._scrolled(self._build_appearance_page()))
        self.pages.currentChanged.connect(lambda _index: self._publish_to_shell())
        root.addWidget(self.pages, stretch=1)

        # Above the footer rather than over the page: an undo the user has to
        # chase across a moving surface is not an undo.
        self.toaster = C.Toaster(self)
        root.addWidget(self.toaster.widget)
        root.addWidget(_divider())
        root.addLayout(self._build_footer())
        self._building = False
        self._watch_dirty()

    def _build_footer(self) -> QHBoxLayout:
        """Save, Discard, Done, and the line that says whether there is anything
        outstanding.

        On screen whenever the settings are, which is the whole of the point:
        this screen had no Cancel at all, and its one confirmation was written
        into a header the user had already navigated away from by the time it
        appeared.
        """
        t = _t()
        row = QHBoxLayout()
        row.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                               t.space["0"])
        row.setSpacing(t.space["2"])

        self.save_status = _StatusLine()
        self.save_status.setObjectName("status_busy")
        row.addWidget(self.save_status, stretch=1)

        self.discard_btn = C.button("Discard changes", kind="secondary",
                                    size="md", on_click=self._on_discard)
        self.save_btn = C.button("Save changes", kind="primary", size="md",
                                 on_click=lambda: self._on_save())
        self.done_btn = C.button("Done", kind="secondary", size="md",
                                 on_click=self._on_back)
        self.done_btn.setToolTip("Go back to the screen this was opened from")
        for button in (self.discard_btn, self.save_btn, self.done_btn):
            row.addWidget(button)
        return row

    def _refresh_footer(self) -> None:
        """Enable what applies, and say why on what does not."""
        outstanding = bool(self._dirty or self._template_dirty)
        self.save_btn.setEnabled(True)
        self.save_btn.setToolTip(
            "Write every field on these tabs to your settings file"
            if outstanding else
            "Everything on these tabs is already what the settings file holds")
        self.discard_btn.setEnabled(outstanding)
        self.discard_btn.setToolTip(
            "Put every field back to what the settings file holds"
            if outstanding else
            "Nothing has been changed, so there is nothing to put back")

    def _scrolled(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _page(self, title: str = "", description: str = "") -> tuple:
        """An empty section, titled, and the column its groups are added to.

        The right margin is the gutter a vertical scrollbar needs: without it
        the bar is drawn over the last few pixels of every control on the page.
        """
        t = _t()
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(t.space["0"], t.space["0"], t.space["3"],
                                  t.space["0"])
        column.setSpacing(t.space["6"])
        if title:
            column.addLayout(_page_head(title, description))
        return page, column

    # ── AI ───────────────────────────────────────────────────────────────────

    def _build_ai_page(self) -> QWidget:
        page, column = self._page(
            "AI",
            "What the model is allowed to write into an email, and what it is "
            "allowed to spend doing it.")

        # No caption on this one: the box holds a single row already labelled
        # Provider, and PROVIDER over "Provider" is one word in two tiers.
        provider = _Group(column)
        self.provider_combo = _combo(PROVIDERS)
        provider.field("Provider", _loose(self.provider_combo))
        provider.foot(
            "The model writes one subject line, one opener and one PS per lead. "
            "With the provider off, emails still send using the plain templates."
        )

        self.groq_key, self.groq_model, self.groq_status = self._provider_block(
            column, "Groq", "gsk_…", "llama-3.3-70b-versatile", "groq")
        self.groq_key.editingFinished.connect(self._fetch_groq_models)
        (self.openrouter_key, self.openrouter_model,
         self.openrouter_status) = self._provider_block(
            column, "OpenRouter", "sk-or-…",
            "meta-llama/llama-3.3-70b-instruct", "openrouter")
        self.openrouter_key.editingFinished.connect(self._fetch_openrouter_models)

        budget = _Group(column, "Token budget")
        self.tokens_per_lead_spin = _spin(60, 600, 10, "", 3)
        self.monthly_cap_spin = _spin(0, 100_000_000, 100_000, "", 9)
        budget.field("Max tokens per lead", _loose(self.tokens_per_lead_spin))
        budget.field("Monthly token cap", _loose(self.monthly_cap_spin))

        self.budget_bar = QProgressBar()
        self.budget_bar.setObjectName("budget_bar")
        self.budget_bar.setTextVisible(False)
        self.budget_label = QLabel("")
        self.budget_label.setObjectName("muted")
        budget.wide(self.budget_bar, note=self.budget_label)
        budget.foot(
            "Answers are cached per business, so re-running the same leads costs "
            "nothing. When the cap is spent the plain templates take over."
        )

        column.addStretch()
        return page

    def _provider_block(self, column: QVBoxLayout, name: str,
                        key_placeholder: str, model_placeholder: str,
                        provider: str):
        key_field = _secret_field(key_placeholder)
        test_btn = C.button("Test", kind="secondary", size="md")
        test_btn.setToolTip(
            "Send one five-token request to %s and report the round trip" % name)
        status = _StatusNote()
        status.setObjectName("status_busy")
        status.hide()
        model_edit = ModelComboBox(model_placeholder)

        key_row = _row_box()
        key_row.addWidget(key_field, stretch=1)
        key_row.addWidget(test_btn)

        group = _Group(column, name)
        # The result of Test is a note about the key above it and not about the
        # provider, so it sits under that row rather than at the foot of the box
        # where it would be reporting on the model line as well.
        group.field("API key", key_row, note=status)
        # Filling the column rather than hugging the right edge: this dropdown
        # is editable and what gets typed into it is
        # `meta-llama/llama-3.3-70b-instruct`, so what it is being asked for is
        # room. The timezone box next door is editable too and does hug the
        # right, because the longest thing it ever holds is `America/Vancouver`.
        group.field("Model", model_edit)

        test_btn.clicked.connect(
            lambda: self._test_provider(provider, key_field, model_edit,
                                        test_btn, status))
        return key_field, model_edit, status

    # ── Sender profile ───────────────────────────────────────────────────────

    def _build_sender_page(self) -> QWidget:
        page, column = self._page(
            "Sender",
            "Who every email is from, and the lines each one signs off with.")

        identity = _Group(column, "Identity")
        self.profile_edits: dict[str, QLineEdit] = {}
        rows = (
            ("company", "Company", "Auto Army"),
            ("sender_name", "Your name", "Name signed at the bottom of every email"),
            ("sender_title", "Your title", "e.g. Automation consultant"),
            ("website", "Website", "https://…"),
            ("reply_to", "Reply-to", "Where replies land, if not the sending account"),
            ("phone", "Phone", "Optional, shown in the footer"),
            ("calendar_link", "Calendar link", "The one link every first-touch email carries"),
        )
        for key, label, placeholder in rows:
            edit = _line(placeholder)
            self.profile_edits[key] = edit
            identity.field(label, edit)

        postal = _Group(column)
        self.postal_edit = QTextEdit()
        self.postal_edit.setPlaceholderText(
            "Street, city, region, postal code, country")
        self.postal_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.postal_edit.document().contentsChanged.connect(self._fit_postal_box)
        self._fit_postal_box()
        postal.field("Postal address", self.postal_edit)
        postal.foot(
            "Legally required in the footer of every commercial email. Campaigns "
            "check for it before sending unless you switch that check off under "
            "Compliance."
        )

        tone = _Group(column)
        self.tone_combo = _combo(TONES)
        tone.field("Tone", _loose(self.tone_combo))

        services = _Group(column)
        self.services_list = QListWidget()
        self.services_list.setObjectName("service_list")
        self.services_list.setMinimumHeight(6 * _t().control["row"])
        # A list of editable things should answer a double-click. Renaming is
        # the only edit a service has, and it is refused with a reason on the
        # shipped ones rather than silently doing nothing.
        self.services_list.itemDoubleClicked.connect(
            lambda _item: self._rename_service())
        services.field("Services you sell", self.services_list)

        # Under the list and inside the box, which is where a list's own
        # commands go: above it they read as commands on the group, and the
        # group is the sender profile rather than the catalogue.
        head = _row_box()
        head.addStretch()
        head.addWidget(C.button("Add service", kind="secondary", size="sm",
                                on_click=self._add_service))
        self.rename_service_btn = C.button("Rename", kind="secondary", size="sm",
                                           on_click=self._rename_service)
        head.addWidget(self.rename_service_btn)
        self.remove_service_btn = C.button("Remove", kind="danger", size="sm",
                                           on_click=self._remove_service)
        head.addWidget(self.remove_service_btn)
        for text, checked in (("All", True), ("None", False)):
            head.addWidget(C.button(
                text, kind="secondary", size="sm",
                on_click=lambda state=checked: self._set_all_services(state)))
        services.wide(head)
        services.foot(
            "Only the ticked services are ever offered in an email, and always "
            "in this exact wording. Add your own with Add service; the ones you "
            "add can be renamed and removed. The shipped ones can be unticked."
        )

        proof = _Group(column)
        self.proof_edit = QTextEdit()
        self.proof_edit.setFixedHeight(3 * _t().control["row"])
        self.proof_edit.setPlaceholderText(
            "One per line, e.g. cut a roofing client's quote turnaround from 2 "
            "days to 20 minutes")
        proof.field("Proof points", self.proof_edit)

        column.addStretch()
        return page

    def _fit_postal_box(self) -> None:
        """Grow the postal box to whatever address is actually in it.

        It used to be a flat 70px, which leaves 52px of viewport — three lines
        of 17px. The placeholder asks for street, city, region, postal code and
        country, and a routine three-line address already pushed 'Canada' under
        the bottom edge and raised an inner scrollbar. This is the CAN-SPAM
        field the hint underneath says blocks every send, so a value the user
        cannot read back is the one thing it must never be.

        Bounded at both ends: four lines even when empty, so the box still reads
        as an address field, and ten before it starts scrolling, so a pasted
        essay cannot push the rest of the page off screen.

        The chrome comes from `contentsMargins`, which the sheet's padding and
        border feed and which is stable the moment the widget is polished.
        Deriving it from `height() - viewport().height()` instead looks more
        direct and is wrong: the viewport only catches up with a new fixed
        height on the next resize event, so each call would read a stale
        difference and the box would grow by 12px every time it was asked.
        """
        edit = self.postal_edit
        document = edit.document()
        # QTextEdit only hands the document a text width when the widget is
        # shown, and this page starts hidden behind the AI tab — without it the
        # document measures zero high and every address would get the floor.
        document.setTextWidth(edit.viewport().width())

        chrome = edit.contentsMargins().top() + edit.contentsMargins().bottom()
        line = max(1, edit.fontMetrics().lineSpacing())
        margins = 2 * int(document.documentMargin())
        wanted = int(document.documentLayout().documentSize().height())
        floor, ceiling = 4 * line + margins, 10 * line + margins
        edit.setFixedHeight(min(max(wanted, floor), ceiling) + chrome)

    # ── Templates ────────────────────────────────────────────────────────────

    def _build_templates_page(self) -> QWidget:
        t = _t()
        page = QWidget()
        stack = QVBoxLayout(page)
        stack.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                 t.space["0"])
        stack.setSpacing(t.space["3"])
        # The one section titled without a line under it, and the line costs
        # exactly what it is worth: measured at the 880x620 window minimum, the
        # editor column has 246px of viewport with the title alone and 223 with
        # the line as well, against a BODY label whose bottom edge sits at 233 —
        # so the sentence explaining this page is paid for by slicing through
        # the label of the box the page is for. That was a defect once already
        # and it is not worth buying back. The four column captions and the
        # preview say what this section is; the title says which section it is.
        stack.addLayout(_page_head("Templates", ""))

        columns = QHBoxLayout()
        columns.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                   t.space["0"])
        columns.setSpacing(t.space["4"])
        stack.addLayout(columns, stretch=1)
        columns.addWidget(self._build_template_list_column())

        editor = QWidget()
        editor.setLayout(self._build_template_editor_column())
        scroll = QScrollArea()
        scroll.setWidget(editor)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # No click focus: a merge chip is `Qt.NoFocus` so that clicking one
        # leaves the caret where the user put it, and a scrolling column that
        # takes the focus the chip refused undoes that from behind — the box
        # empties its selection on the way out and the caret stops being drawn
        # at the very moment the user is aiming a field at it.
        scroll.setFocusPolicy(Qt.NoFocus)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMinimumHeight(_editor_floor())

        # The split is draggable because which half matters is the user's call,
        # not this screen's: reading the copy back wants a tall preview and
        # rewriting it wants tall boxes. Neither half may be dragged away —
        # both carry a minimum, and the growth goes mostly to the boxes so that
        # a wider window does not turn into a wall of preview.
        split = QSplitter(Qt.Vertical)
        split.setObjectName("template_split")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(t.space["2"])
        # The one widget on this screen that styles itself, and the reason is
        # that `QSplitter::handle` in the application sheet cannot say "only
        # this splitter": a rule there would put a rule over every handle in the
        # app, and the outreach screen's splitters are not this one.
        split.setStyleSheet(
            "QSplitter#template_split::handle { background: transparent; "
            "border-top: %dpx solid %s; }" % (C.BORDER, t.color["border.subtle"]))
        split.addWidget(scroll)
        split.addWidget(self._build_template_preview_panel())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        # A proportion rather than two heights: the boxes and the palette ask
        # for more than either window size has, so the preview opens on its
        # floor — the whole message is a drag away and the copy being written
        # keeps the rest.
        split.setSizes([4 * _editor_floor(), _preview_floor()])
        columns.addWidget(split, stretch=1)
        return page

    def _build_template_list_column(self) -> QWidget:
        t = _t()
        column = QVBoxLayout()
        column.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                  t.space["0"])
        column.setSpacing(t.space["2"])
        column.addWidget(_section_label("Your templates"))

        self.template_list = QListWidget()
        self.template_list.setObjectName("saved_list")
        # Elided, not clipped: a name somebody typed can be any length. From the
        # middle, because the marker that says whether the row is theirs sits at
        # the end of it — cut from the right, an 84-character name renders with
        # nothing to say whether Delete or Reset is the button for it.
        self.template_list.setTextElideMode(Qt.ElideMiddle)
        self.template_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.template_list.setMinimumHeight(6 * t.control["row"])
        self.template_list.currentItemChanged.connect(self._on_template_row_changed)
        column.addWidget(self.template_list, stretch=1)

        buttons = QGridLayout()
        buttons.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                   t.space["0"])
        buttons.setHorizontalSpacing(t.space["2"])
        buttons.setVerticalSpacing(t.space["2"])
        specs = (
            ("template_new_btn", "New", "secondary", 0, 0, self._on_template_new,
             "Start a new template from a working first touch"),
            ("template_copy_btn", "Duplicate", "secondary", 0, 1,
             self._on_template_duplicate,
             "Copy what is in the editor into a template of your own"),
            ("template_delete_btn", "Delete", "danger", 1, 0,
             self._on_template_delete, "Remove this template for good"),
            ("template_reset_btn", "Reset", "secondary", 1, 1,
             self._on_template_reset,
             "Put this built-in template back to the wording it shipped with"),
        )
        for attr, label, kind, row, col, handler, tip in specs:
            button = C.button(label, kind=kind, size="sm", on_click=handler)
            button.setToolTip(tip)
            setattr(self, attr, button)
            buttons.addWidget(button, row, col)
        column.addLayout(buttons)
        column.addWidget(_hint(
            "Built-in templates can be edited and put back afterwards. Copies you "
            "make are yours, and only yours can be deleted."
        ))

        holder = QWidget()
        holder.setLayout(column)
        # Fixed, but not to a number: the column is as wide as the picker and
        # the two buttons under it ask for, in whatever font and density is
        # loaded. The 252px it used to be was one font's answer.
        holder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        return holder

    def _build_template_editor_column(self) -> QVBoxLayout:
        t = _t()
        column = QVBoxLayout()
        column.setContentsMargins(t.space["0"], t.space["0"], t.space["3"],
                                  t.space["0"])
        column.setSpacing(t.space["2"])

        head = _row_box()
        self.template_name_edit = _line("What you will call this in the picker")
        self.template_name_edit.textChanged.connect(self._mark_template_dirty)
        self.template_name_edit.returnPressed.connect(
            lambda: self._save_open_template())
        self.template_step_combo = _combo(
            [(step, _step_name(step)) for step in range(_MAX_STEP + 1)])
        self.template_step_combo.setToolTip(
            "Which touch this template writes: the first email, or the follow-up "
            "that goes out when nobody has replied to the one before it")
        self.template_step_combo.currentIndexChanged.connect(
            lambda _index: self._mark_template_dirty())
        self.template_save_btn = C.button(
            "Save template", kind="primary", size="md",
            on_click=lambda: self._save_open_template())
        head.addWidget(_section_label("Name"))
        head.addWidget(self.template_name_edit, stretch=1)
        head.addWidget(_section_label("Step"))
        head.addWidget(self.template_step_combo)
        head.addWidget(self.template_save_btn)
        column.addLayout(head)

        subject_row = _row_box()
        subject_row.addWidget(_section_label("Subject"))
        subject_row.addStretch()
        self.template_subject_count = QLabel("")
        self.template_subject_count.setObjectName("muted")
        self.template_subject_count.setTextFormat(Qt.RichText)
        subject_row.addWidget(self.template_subject_count)
        column.addLayout(subject_row)

        self.template_subject_edit = _FlatLine()
        self.template_subject_edit.setPlaceholderText(
            "Lower case, no shouting, under 55 characters")
        self.template_subject_edit.setFixedHeight(t.control["md"])
        self.template_subject_edit.textChanged.connect(self._mark_template_dirty)
        self.template_subject_edit.returnPressed.connect(
            lambda: self._save_open_template())
        self._watch_caret(self.template_subject_edit)
        column.addWidget(self.template_subject_edit)

        # Bounded, because the 21 chips reflow to five rows at the window
        # minimum and took 165px of a 308px column — which is what put the body
        # editor 70px below the fold with its BODY label sliced in half. The
        # palette is a reference; the copy is the work.
        self.template_chips = _ChipBar(MERGE_FIELDS)
        self.template_chips.insert_requested.connect(self._insert_merge_field)
        self.template_chips_pane = _BoundedPane(self.template_chips,
                                                _palette_ceiling())
        # The bar takes the focus, not the pane around it: a scroll area that
        # took it would sit between the subject line and the chips in the tab
        # order and empty the selection a field is about to replace.
        self.template_chips_pane.setFocusPolicy(Qt.NoFocus)
        column.addWidget(self.template_chips_pane)
        column.addWidget(_hint(
            "Click a field to drop it in at the cursor. Hover one for what it "
            "resolves to."
        ))

        column.addWidget(_section_label("Body"))
        self.template_body_edit = _FlexEdit()
        self.template_body_edit.setAcceptRichText(False)
        self.template_body_edit.setMinimumHeight(3 * t.control["md"])
        # A family and a size and nothing else: a widget's own sheet beats an
        # ancestor's only for what it declares, so the well, the border and the
        # focus ring still come from `ui/theme.py`.
        self.template_body_edit.setStyleSheet(
            "QTextEdit { font-family: '%s'; font-size: %dpx; }"
            % (_mono_family(), t.font["mono"][0]))
        self.template_body_edit.setPlaceholderText(
            "Plain text. Blank lines are paragraphs; the footer is added for you.")
        self.template_body_edit.textChanged.connect(self._mark_template_dirty)
        self._watch_caret(self.template_body_edit)
        column.addWidget(self.template_body_edit, stretch=1)

        self.template_issues = QLabel("")
        self.template_issues.setObjectName("muted")
        self.template_issues.setTextFormat(Qt.RichText)
        self.template_issues.setWordWrap(True)
        self.template_issues.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.template_issues_pane = _BoundedPane(self.template_issues,
                                                 _findings_ceiling())
        self.template_issues_pane.hide()
        column.addWidget(self.template_issues_pane)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._refresh_template_preview)
        return column

    def _build_template_preview_panel(self) -> QWidget:
        """The preview, taken out of the scrolling column and pinned under it.

        It used to be the last thing in the same column as the boxes, which
        reads fine at 1080x760 and fails at the window minimum: the palette, the
        two boxes and a populated findings pane already ask for more than 620px
        of window has to give, so the preview began below the fold — and even
        scrolled all the way down it was 84px of From/To card with no message
        under it. A pane that only shows up when the copy is already short is
        the wrong pane to lose, because this is the one thing on the screen that
        says what will actually be sent.

        So it stops scrolling. The boxes scroll behind it, the preview stays put
        at every window size, and `_preview_floor` is the least of the message
        it may ever be squeezed to.
        """
        t = _t()
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(t.space["0"], t.space["0"], t.space["3"],
                                  t.space["0"])
        column.setSpacing(t.space["2"])
        column.addWidget(_section_label("Preview"))

        self.template_preview = _FlexEdit()
        self.template_preview.setObjectName("email_paper")
        # Read-only rather than a browser: a calendar link in a preview must not
        # be clickable, and a QTextEdit that cannot be typed in never follows one.
        self.template_preview.setReadOnly(True)
        # The floor is the document's, so the widget needs the sheet's padding
        # and border on top of it. `ensurePolished` first: those two only reach
        # `contentsMargins` when the sheet has been applied to the widget, and
        # unpolished it reports the 1px frame alone — a floor 16px short, which
        # is exactly the line of the message this is here to guarantee.
        self.template_preview.ensurePolished()
        margins = self.template_preview.contentsMargins()
        self.template_preview.setMinimumHeight(
            _preview_floor() + margins.top() + margins.bottom())
        column.addWidget(self.template_preview, stretch=1)
        column.addWidget(_hint(
            "Rendered for a sample lead through the same code that sends, footer "
            "and unsubscribe line included."
        ))
        return panel

    # ── Templates: the list ──────────────────────────────────────────────────

    def _reload_templates(self, select_id: str = "") -> None:
        self._templates = _all_templates()
        ids = [str(t.id) for t in self._templates]
        wanted = select_id or self._template_id
        if wanted not in ids:
            wanted = ids[0] if ids else ""
        self._template_id = wanted
        self._template_loading = True
        try:
            self._fill_template_list()
        finally:
            self._template_loading = False
        self._load_template_into_editor(self._template_by_id(wanted))

    def _fill_template_list(self) -> None:
        self.template_list.clear()
        grouped: dict[int, list] = {}
        for template in self._templates:
            grouped.setdefault(_int_of(getattr(template, "step", 0)), []).append(template)

        for step in sorted(grouped):
            header = QListWidgetItem(_step_name(step))
            header.setFlags(Qt.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self.template_list.addItem(header)
            for template in grouped[step]:
                marker = _marker(str(template.id))
                item = QListWidgetItem("   %s — %s" %
                                       (str(template.name or "").strip() or "Untitled", marker))
                item.setData(Qt.UserRole, str(template.id))
                item.setToolTip("%s\n%s · %s" % (str(template.name or "Untitled"),
                                                 _step_name(step), marker))
                self.template_list.addItem(item)
                if str(template.id) == self._template_id:
                    self.template_list.setCurrentItem(item)

    def _template_by_id(self, template_id: str):
        for template in self._templates:
            if str(template.id) == template_id:
                return template
        return None

    def _load_template_into_editor(self, template) -> None:
        """Put `template` on screen, and say whatever the editor could not hold.

        Both notes below come out of a store somebody edited by hand, and both
        used to be applied in silence: the row opened looking saved, and the
        first Save wrote the editor's version over a value nobody had chosen to
        change. They go in the findings panel rather than the header, which is
        sized for two words, and they mark the editor unsaved so that leaving
        the row asks first.
        """
        stored_step = _int_of(getattr(template, "step", 0))
        shown_step = max(0, min(_MAX_STEP, stored_step))
        stored_subject = str(getattr(template, "subject", "") or "")
        self._template_loading = True
        try:
            self.template_name_edit.setText(str(getattr(template, "name", "") or ""))
            self.template_subject_edit.setText(stored_subject)
            self.template_body_edit.setPlainText(str(getattr(template, "body", "") or ""))
            self.template_step_combo.setCurrentIndex(shown_step)
        finally:
            self._template_loading = False

        # An offset means nothing once the words under it have been replaced, and
        # a row is switched from the picker with the focus in the list rather
        # than in either box. Forgotten, a chip clicked from there inserts at
        # whatever caret the boxes were left holding, which is the top of the
        # copy that was just loaded.
        self._merge_span = (-1, -1)
        self._template_dirty = False
        self._template_notes = []
        if template is not None and shown_step != stored_step:
            self._template_notes.append(_note(
                "step", "Stored as step %d, which is not one this app ever "
                        "sends. It is shown as %s, and Save writes that."
                        % (stored_step, _step_name(shown_step))))
        if self.template_subject_edit.text() != stored_subject:
            self._template_notes.append(_note(
                "subject", "The stored subject has line breaks a single line "
                           "cannot show. They read as spaces here, and Save "
                           "writes them that way."))
        if self._template_notes:
            self._mark_template_dirty()
        self._refresh_template_buttons()
        self._refresh_footer()
        self._refresh_template_preview()

    def _refresh_template_buttons(self) -> None:
        """Enable what applies, and say why on everything that does not.

        A disabled control that describes what it would do reads as a control
        that is not working. Both of these say what is true of the template in
        the editor instead, which is the sentence that answers the question.
        """
        has = bool(self._template_id)
        builtin = has and _is_builtin(self._template_id)
        edited = builtin and _is_overridden(self._template_id)
        self.template_copy_btn.setEnabled(has)
        self.template_save_btn.setEnabled(has)
        self.template_delete_btn.setEnabled(has and not builtin)
        self.template_delete_btn.setToolTip(
            "No template is open" if not has else
            "A built-in template cannot be removed — Reset puts it back instead"
            if builtin else "Remove this template for good")
        self.template_reset_btn.setEnabled(edited)
        self.template_reset_btn.setToolTip(
            "No template is open" if not has else
            "Put this built-in template back to the wording it shipped with"
            if edited else
            "This built-in template is already the wording it shipped with"
            if builtin else
            "This template is your own, so there is no shipped wording to go "
            "back to — Delete removes it")
        for edit in (self.template_name_edit, self.template_subject_edit,
                     self.template_body_edit):
            edit.setEnabled(has)

    def _on_template_row_changed(self, current, _previous) -> None:
        if self._template_loading or current is None:
            return
        chosen = str(current.data(Qt.UserRole) or "")
        if not chosen or chosen == self._template_id:
            return
        if self._template_dirty and not self._offer_to_save():
            # Only the highlight comes back. Reloading the editor here would
            # overwrite the unsaved text with the copy on disk, which is the one
            # thing Cancel was pressed to prevent.
            self._sync_template_row()
            return
        self._show_template(chosen)

    def _sync_template_row(self) -> None:
        """Put the highlight on the template the editor is holding."""
        self._template_loading = True
        try:
            self._select_row(self._template_id)
        finally:
            self._template_loading = False

    def _show_template(self, template_id: str) -> None:
        """Put one template under the highlight and in the editor, together.

        Answering the unsaved-changes question with Save writes the template
        being left, and the reload behind that write rebuilds the list around
        it — so by the time the switch itself happened the highlight had been
        put back on the row being left while the editor went on to load the row
        that was picked. The two then disagreed with nothing on screen saying
        so, the highlighted row read "Headline gap — edited" over an editor
        holding a different record, and every keystroke after it was written
        into that record. Clicking the highlighted row could not undo it either:
        `currentItemChanged` does not fire for an item that is already current.

        So neither half moves without the other, whichever answer came back and
        whichever direction the switch went in.
        """
        self._template_id = template_id
        self._sync_template_row()
        self._load_template_into_editor(self._template_by_id(template_id))

    def _select_row(self, template_id: str) -> None:
        for row in range(self.template_list.count()):
            item = self.template_list.item(row)
            if str(item.data(Qt.UserRole) or "") == template_id:
                self.template_list.setCurrentItem(item)
                return

    def _offer_to_save(self) -> bool:
        """Ask before losing edits. False means the screen stays where it is.

        A Save the store refused counts as staying put. `_store_failed` has just
        told the user that what is on screen is only on screen and to copy it
        somewhere safe; switching away underneath that message would throw away
        the text it was asking them to rescue.
        """
        name = self.template_name_edit.text().strip() or "This template"
        answer = QMessageBox.question(
            self, "Unsaved changes",
            "%s has changes you have not saved.\n\nSave them before switching?" % name,
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save and not self._save_open_template(quiet=True):
            return False
        self._template_dirty = False
        self._refresh_footer()
        return True

    # ── Templates: editing ───────────────────────────────────────────────────

    def _editor_template(self):
        return _templates.Template(
            id=self._template_id,
            name=self.template_name_edit.text().strip(),
            step=max(0, _int_of(self.template_step_combo.currentData())),
            subject=self.template_subject_edit.text(),
            body=self.template_body_edit.toPlainText(),
        )

    def _mark_template_dirty(self) -> None:
        """Say the open template has changes, every time and not only the first.

        Every time, because the footer this writes into is shared with the
        settings either side of it: a template edited after a sending window was
        touched would otherwise leave "Unsaved changes" standing over an editor
        whose own state had moved on.
        """
        if self._template_loading:
            return
        self._template_dirty = True
        _status(self.save_status, "Unsaved", "busy")
        self._refresh_footer()
        self._publish_to_shell()
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    def _watch_caret(self, editor) -> None:
        """Follow the caret in `editor` wherever it goes.

        A merge field lands where the user is looking, and where they are
        looking is where the caret is *now* — after the arrow key, after the
        click inside the box, after the drag that selected a word. Focus events
        alone cannot see any of that: they fire when the box is entered and
        left, and every one of those moves happens in between. A field inserted
        at an offset recorded two gestures ago lands somewhere else and drags
        the caret with it, so the next keystroke is wrong too.
        """
        editor.installEventFilter(self)
        editor.cursorPositionChanged.connect(lambda *_a: self._caret_moved(editor))
        editor.selectionChanged.connect(lambda *_a: self._caret_moved(editor))

    def _caret_moved(self, editor) -> None:
        """Record the caret, but only while the box owns it.

        Losing the focus is itself a caret move as far as Qt is concerned: a
        `QLineEdit` drops its selection on the way out, and that arrives here as
        one more `selectionChanged`. Taking it would throw away the selection
        the user is still looking at, half a second before a chip is asked to
        replace it. The filter below has already written down the truth by then.
        """
        if editor is None or not editor.hasFocus():
            return
        self._merge_target = editor
        self._merge_span = self._caret_of(editor)

    def eventFilter(self, obj, event):
        """Remember which box a merge field should land in, and where in it.

        The chips insert at the cursor, and the cursor is in whichever of the two
        editors was last used. Without this every field goes into the body, which
        is wrong exactly when somebody is writing a subject line.

        Both edges of the focus matter. Entering a box with Tab selects its whole
        contents, which is a real selection a field is allowed to replace, and
        leaving it for the palette is the last instant at which a `QLineEdit`
        still knows what was selected.
        """
        if event.type() in (QEvent.FocusIn, QEvent.FocusOut) and obj in (
                getattr(self, "template_subject_edit", None),
                getattr(self, "template_body_edit", None)):
            self._merge_target = obj
            self._merge_span = self._caret_of(obj)
        return super().eventFilter(obj, event)

    def _caret_of(self, editor) -> tuple:
        """Where the caret sits and what it has hold of, as (anchor, position)."""
        if editor is self.template_subject_edit:
            at, start = editor.cursorPosition(), editor.selectionStart()
            if start < 0:
                return (at, at)
            end = start + len(editor.selectedText())
            return (end, start) if at == start else (start, end)
        cursor = editor.textCursor()
        return (cursor.anchor(), cursor.position())

    def _restore_caret(self, editor) -> None:
        """Put the caret, and any selection with it, back where it was left."""
        anchor, at = self._merge_span
        if at < 0:
            return
        if editor is self.template_subject_edit:
            end = len(editor.text())
            anchor, at = min(max(anchor, 0), end), min(max(at, 0), end)
            if anchor == at:
                editor.setCursorPosition(at)
            else:
                editor.setSelection(min(anchor, at), abs(at - anchor))
            return
        end = len(editor.toPlainText())
        cursor = editor.textCursor()
        cursor.setPosition(min(max(anchor, 0), end))
        cursor.setPosition(min(max(at, 0), end), QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)

    def _insert_merge_field(self, field: str) -> None:
        """Drop `field` in at the caret, over whatever the caret has hold of.

        Nothing is moved when the box still has the focus — a chip is
        `Qt.NoFocus` and the column behind it takes no click focus either, so a
        clicked chip leaves the caret and the selection exactly as they are on
        screen and the insert goes straight in. The restore is for the keyboard
        route, where reaching the palette with Tab genuinely takes the focus out
        of the box and a `QLineEdit` forgets what was selected on the way.
        """
        token = "{{%s}}" % field
        target = (self.template_subject_edit
                  if self._merge_target is self.template_subject_edit
                  else self.template_body_edit)
        if not target.hasFocus():
            self._restore_caret(target)
        if target is self.template_subject_edit:
            target.insert(token)
        else:
            target.insertPlainText(token)
        target.setFocus()
        self._merge_target = target
        self._merge_span = self._caret_of(target)

    def _save_open_template(self, quiet: bool = False) -> bool:
        if not self._template_id:
            return False
        if quiet and not self._template_dirty:
            return True
        template = self._editor_template()
        if not _save_template(template):
            self._store_failed("saved")
            return False
        self._stored(template.id, "Template saved", announce=not quiet)
        return True

    def _stored(self, template_id: str, message: str, announce: bool = True) -> None:
        """Settle the screen after a write the store accepted.

        The announcement is `saved_signal`, which the Outreach screen answers by
        rebuilding its template picker off the same store. Without it a template
        written here does not appear over there until something unrelated is
        saved, and the obvious reading of that is that it was not written.
        `announce` is off for the write folded into the screen's own Save, which
        emits once for everything a moment later.
        """
        self._template_dirty = False
        self._reload_templates(template_id)
        _status(self.save_status, message, "ok")
        self._refresh_footer()
        self._publish_to_shell()
        if announce:
            self.saved_signal.emit(self.settings)

    def _store_failed(self, verb: str) -> None:
        """Say a template write did not happen, loudly enough to be believed.

        The footer has room for one line and this needs more than one: a store
        that will not accept a write is a read-only profile directory or a full
        disk, and a user who reads "Saved" and closes the window loses the
        evening. The short form stays in the footer for the record.
        """
        _status(self.save_status, "Not saved", "err")
        QMessageBox.warning(
            self, "Templates could not be written",
            "This template could not be %s.\n\n"
            "Templates live in a file beside your settings, and nothing was "
            "written to it — a read-only profile folder or a full disk is the "
            "usual cause. What is on screen is still only on screen, so copy it "
            "somewhere safe before closing this window." % verb,
            QMessageBox.Ok, QMessageBox.Ok,
        )

    def _on_template_new(self) -> None:
        if self._template_dirty and not self._offer_to_save():
            return
        taken = {str(t.id) for t in self._templates}
        step = max(0, _int_of(self.template_step_combo.currentData()))
        template = _templates.Template(
            id=_new_id("New template", step, taken), name="New template", step=step,
            subject=NEW_TEMPLATE_SUBJECT, body=NEW_TEMPLATE_BODY)
        if not _save_template(template):
            self._store_failed("added")
            return
        self._stored(template.id, "Template added")
        self.template_name_edit.setFocus()
        self.template_name_edit.selectAll()

    def _on_template_duplicate(self) -> None:
        if not self._template_id:
            return
        if self._template_dirty and not self._offer_to_save():
            return
        source = self._editor_template()
        taken = {str(t.id) for t in self._templates}
        name = (source.name or "Untitled") + " copy"
        template = _templates.Template(
            id=_new_id(name, source.step, taken), name=name, step=source.step,
            subject=source.subject, body=source.body)
        if not _save_template(template):
            self._store_failed("copied")
            return
        self._stored(template.id, "Template copied")
        self.template_name_edit.setFocus()
        self.template_name_edit.selectAll()

    def _on_template_delete(self) -> None:
        if not self._template_id or _is_builtin(self._template_id):
            return
        name = self.template_name_edit.text().strip() or "this template"
        answer = QMessageBox.question(
            self, "Delete template?",
            "%s will be removed. Campaigns already queued keep the copy they were "
            "built with; nothing else brings it back." % name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if not _delete_template(self._template_id):
            self._store_failed("deleted")
            return
        self._template_id = ""
        self._stored("", "Template deleted")

    def _on_template_reset(self) -> None:
        if not self._template_id:
            return
        name = self.template_name_edit.text().strip() or "this template"
        answer = QMessageBox.question(
            self, "Reset template?",
            "%s goes back to the wording it shipped with. Your edits to it are "
            "lost." % name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if not _reset_stored_template(self._template_id):
            self._store_failed("reset")
            return
        self._stored(self._template_id, "Template reset")

    # ── Templates: preview ───────────────────────────────────────────────────

    def _preview_settings(self) -> dict:
        live = dict(self.settings)
        live["unsubscribe_mailto"] = self.unsubscribe_edit.text().strip()
        live["append_unsubscribe"] = self.append_unsubscribe_cb.isChecked()
        live["append_postal_address"] = self.append_postal_cb.isChecked()
        return live

    def _refresh_template_preview(self) -> None:
        template = self._editor_template()
        try:
            live = self._preview_settings()
            ctx = _templates.build_context(
                dict(SAMPLE_LEAD), dict(SAMPLE_AUDIT), dict(SAMPLE_AI),
                self._profile_values(), live)
            # The same call the send loop makes, and the reason the two
            # compliance switches change what this pane draws rather than
            # quietly changing only what goes out.
            ctx = _campaign.apply_compliance(ctx, live)
            subject, body_text, _body_html = _templates.render(template, ctx)
        except Exception:
            ctx, subject, body_text = None, "", ""
        self._show_template_issues(_validate_template(template, ctx))

        limit = getattr(_templates, "SUBJECT_MAX", 55)
        merged = len(_merged_subject(template, ctx))
        over = merged > limit
        self.template_subject_count.setText(_rich(
            "%d / %d once merged%s" % (merged, limit,
                                       " — cut before sending" if over else ""),
            _tone_ink("_RED") if over else _tone_ink("_GREEN")))

        if not body_text.strip():
            self._show_paper(
                "Nothing to preview yet. A body with at least one line in it "
                "renders here as the recipient would see it.")
            return
        self.template_preview.setHtml(self._as_paper(subject, body_text))
        self._paint_paper()

    def _show_template_issues(self, issues) -> None:
        lines = []
        # The screen's own notes first: they are about what loading the row
        # already changed, which is older news than anything about the copy.
        for issue in list(self._template_notes) + list(issues):
            level = str(issue.get("level") or "").strip().lower()
            field = str(issue.get("field") or "").strip()
            message = str(issue.get("message") or "").strip()
            if not message:
                continue
            colour = _tone_ink("_RED") if level.startswith("err") \
                else _tone_ink("_AMBER")
            lines.append(_rich("%s: %s" % (field, message) if field else message, colour))
        self.template_issues.setText("<br>".join(lines))
        self.template_issues_pane.setVisible(bool(lines))
        self.template_issues_pane.updateGeometry()

    def _as_paper(self, subject: str, body_text: str) -> str:
        """The message laid out as the recipient's mail client would show it.

        Every colour and size is inline and every one of them comes from the
        `_PAPER` constants rather than from `ui/theme.py`: this widget's QSS is
        written for an application, and a preview that does not look like an
        email is not a preview.
        """
        blocks = []
        for para in re.split(r"\n\s*\n", str(body_text or "").strip()):
            rows = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
            if rows:
                blocks.append('<p style="margin:0 0 %dpx 0;">%s</p>'
                              % (_PAPER_GAP, "<br>".join(rows)))
        return (
            '<div style="font-family:%s;color:%s;">'
            '<p style="margin:0 0 %dpx 0;font-size:%dpx;font-weight:600;">%s</p>'
            '<p style="margin:0 0 %dpx 0;font-size:%dpx;color:%s;">To %s &lt;%s&gt;</p>'
            '<div style="font-size:%dpx;line-height:%s;">%s</div></div>'
            % (_PAPER_FAMILY, _PAPER_INK.name(),
               _PAPER_RULE, _PAPER_TYPE["subject"],
               html.escape(subject or "(no subject)"),
               _PAPER_PAD, _PAPER_TYPE["meta"], _PAPER_META.name(),
               html.escape(str(SAMPLE_LEAD.get("name") or "")),
               html.escape(str(SAMPLE_LEAD.get("email") or "")),
               _PAPER_TYPE["body"], _PAPER_LEADING, "".join(blocks))
        )

    def _show_paper(self, message: str) -> None:
        self.template_preview.setHtml(
            '<div style="font-family:%s;font-size:%dpx;line-height:%s;color:%s;">'
            '%s</div>'
            % (_PAPER_FAMILY, _PAPER_TYPE["note"], _PAPER_LEADING,
               _PAPER_META.name(), html.escape(message)))
        self._paint_paper()

    def _paint_paper(self) -> None:
        """Paint the preview document itself white.

        The body carries its own near-black text colour, so on the app's dark
        surface it would otherwise be invisible — and there is no QSS rule for
        `#email_paper` to lean on.
        """
        frame = self.template_preview.document().rootFrame()
        fmt = frame.frameFormat()
        fmt.setBackground(_PAPER)
        fmt.setMargin(_PAPER_PAD)
        frame.setFrameFormat(fmt)

    # ── Gmail accounts ───────────────────────────────────────────────────────

    def _build_gmail_page(self) -> QWidget:
        """The accounts, with the way to add one pinned above them.

        "Add account" used to sit under the last account inside the same scroll,
        so it moved further away with every account added and at 880x620 it
        started below the fold: the control for getting out of an empty state
        was reachable only by scrolling past what was missing.
        """
        t = _t()
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                                  t.space["0"])
        column.setSpacing(t.space["3"])
        column.addLayout(_page_head(
            "Gmail",
            "The mailboxes that do the sending, and what each of them is "
            "allowed to send."))

        head = _row_box()
        head.addWidget(_section_label("Sending accounts"))
        head.addStretch()
        self.add_account_btn = C.button("Add account", kind="secondary",
                                        size="sm", on_click=self._on_add_account)
        self.add_account_btn.setToolTip("Put another Gmail mailbox on the rota")
        head.addWidget(self.add_account_btn)
        column.addLayout(head)
        column.addLayout(_measured(_hint(APP_PASSWORD_HINT)))

        body = QWidget()
        wrap = QVBoxLayout(body)
        wrap.setContentsMargins(t.space["0"], t.space["0"], t.space["3"],
                                t.space["0"])
        wrap.setSpacing(t.space["4"])
        self.accounts_box = QVBoxLayout()
        self.accounts_box.setContentsMargins(t.space["0"], t.space["0"],
                                             t.space["0"], t.space["0"])
        self.accounts_box.setSpacing(t.space["4"])
        wrap.addLayout(self.accounts_box)

        self.no_accounts_label = C.body_label(
            "No sending accounts yet — add one to send anything.")
        wrap.addLayout(_measured(self.no_accounts_label))
        wrap.addStretch()
        column.addWidget(self._scrolled(body), stretch=1)

        column.addLayout(_measured(_hint(
            "Several accounts spread the volume and keep any one mailbox under "
            "Gmail's limits. Verify each one before the first campaign."
        )))
        from core.secrets import is_secure_store_available
        status_text = (
            "Credential storage: System Secure (Windows DPAPI)"
            if is_secure_store_available() else
            "Credential storage: Obfuscated Fallback (Linux/macOS)"
        )
        column.addLayout(_measured(_hint(status_text)))
        return page

    # ── Sending schedule ─────────────────────────────────────────────────────

    def _build_sending_page(self) -> QWidget:
        page, column = self._page(
            "Sending",
            "When mail goes out, how fast it goes, and how much of it any one "
            "day carries.")

        days = _Group(column)
        days_row = _row_box()
        days_row.addStretch()
        self.day_boxes: list[QCheckBox] = []
        for name in DAY_NAMES:
            box = QCheckBox(name)
            box.setCursor(Qt.PointingHandCursor)
            box.toggled.connect(lambda _on: self._refresh_schedule_notes())
            self.day_boxes.append(box)
            days_row.addWidget(box)
        self.days_note = _hint("")
        self.days_note.setVisible(False)
        days.field("Sending days", days_row, note=self.days_note)

        window = _Group(column, "Window")
        self.start_hour_spin = _spin(0, 23, 1, ":00", 2)
        self.end_hour_spin = _spin(1, 24, 1, ":00", 2)
        self.timezone_combo = QComboBox()
        self.timezone_combo.setEditable(True)
        self.timezone_combo.setFixedHeight(_t().control["md"])
        self.timezone_combo.addItems(list(COMMON_ZONES))
        hours_row = _row_box()
        hours_row.addStretch()
        hours_row.addWidget(self.start_hour_spin)
        hours_row.addWidget(QLabel("to"))
        hours_row.addWidget(self.end_hour_spin)
        self.window_note = _hint("")
        self.window_note.setVisible(False)
        # Each note under the row it is about rather than at the foot of the
        # box: the window is composed by one rule and the zone resolved by
        # another, and a stack of three sentences under two controls says
        # nothing about which sentence answers which.
        window.field("Sending window", hours_row, note=self.window_note)
        self.timezone_note = _hint("")
        self.timezone_note.setVisible(False)
        window.field("Timezone", _loose(self.timezone_combo),
                     note=self.timezone_note)
        for spin in (self.start_hour_spin, self.end_hour_spin):
            spin.valueChanged.connect(lambda _value: self._refresh_schedule_notes())
        window.foot(
            "Local time unless you name an IANA zone. Naming one sends at those "
            "hours in the customer's day rather than in yours."
        )
        self.timezone_combo.currentTextChanged.connect(self._refresh_timezone_note)

        pacing = _Group(column, "Pacing")
        self.min_gap_spin = _spin(5, 7200, 15, " s", 4)
        self.max_gap_spin = _spin(5, 7200, 15, " s", 4)
        self.daily_cap_spin = _spin(1, 500, 5, "", 3)
        self.hourly_cap_spin = _spin(1, 200, 1, "", 3)
        self.gap_note = _hint("")
        self.gap_note.setVisible(False)
        self.cap_note = _hint("")
        self.cap_note.setVisible(False)
        for label, widget, note in (
                ("Minimum gap between sends", self.min_gap_spin, None),
                ("Maximum gap between sends", self.max_gap_spin, self.gap_note),
                ("Daily cap per account", self.daily_cap_spin, self.cap_note),
                ("Hourly cap per account", self.hourly_cap_spin, None)):
            pacing.field(label, _loose(widget), note=note)
            widget.valueChanged.connect(
                lambda _value: self._refresh_schedule_notes())
        pacing.foot(
            "Each gap is picked at random inside the range. A fixed interval is "
            "the clearest automation fingerprint a filter can look for."
        )

        warm = _Group(column, "Warm-up")
        self.warmup_cb = C.toggle(
            "Ramp new accounts up gradually",
            help="The ramp starts from each account's own warm-up date")
        self.warmup_cb.toggled.connect(lambda _on: self._refresh_schedule_notes())
        warm.wide(self.warmup_cb, note=self.warmup_cb.help_label)
        self.warmup_start_spin = _spin(1, 200, 1, "/day", 3)
        self.warmup_step_spin = _spin(1, 100, 1, "/day", 3)
        self.warmup_max_spin = _spin(1, 500, 5, "/day", 3)
        for label, widget in (("Start at", self.warmup_start_spin),
                              ("Increase each day by", self.warmup_step_spin),
                              ("Stop increasing at", self.warmup_max_spin)):
            warm.field(label, _loose(widget))
            widget.valueChanged.connect(
                lambda _value: self._refresh_schedule_notes())
        warm.foot(
            "A brand-new Gmail account that sends 40 cold emails on day one gets "
            "throttled or suspended. The ramp starts from each account's warm-up "
            "date."
        )

        follow = _Group(column, "Follow-ups")
        self.followup_cb = C.toggle(
            "Send follow-ups when nobody replies",
            help="A reply, a bounce or an unsubscribe cancels the rest")
        follow.wide(self.followup_cb, note=self.followup_cb.help_label)
        self.followup_gap_spin = _spin(1, 60, 1, " days", 2)
        self.followup_steps_spin = _spin(0, 5, 1, "", 2)
        follow.field("Wait between touches", _loose(self.followup_gap_spin))
        follow.field("Follow-ups per lead", _loose(self.followup_steps_spin))
        follow.foot(
            "The wait is a floor, not an exact gap: a follow-up is placed no "
            "sooner than this, then queued behind whatever the day's caps and "
            "sending window allow, so on a long list it usually lands later than "
            "the number above."
        )

        column.addStretch()
        return page

    def _refresh_schedule_notes(self) -> None:
        """Say what the scheduler will really do, wherever that is not what was asked.

        `core.campaign` composes rather than obeys — three caps become their
        minimum, an inverted window becomes one hour, an empty day set becomes
        Monday to Friday — and every one of those used to happen in silence with
        the requested number still on screen. Each note is empty when nothing is
        being overridden, because a note beside every field is a note nobody
        reads.
        """
        if self._building:
            return
        live = self._schedule_values()
        chosen = live["send_days"]
        for label, note in (
                (self.days_note, _days_note(chosen)),
                (self.window_note, _hours_note(live["send_start_hour"],
                                               live["send_end_hour"])),
                (self.gap_note, _gap_note(live["send_min_gap_sec"],
                                          live["send_max_gap_sec"])),
                (self.cap_note, _cap_note(live, [row.to_dict()
                                                 for row in self._account_rows]))):
            label.setText(note)
            label.setVisible(bool(note))
        for row in self._account_rows:
            row.show_effective_cap(live)

    def _schedule_values(self) -> dict:
        """The sending settings as the widgets hold them, for the notes above.

        Asked of the widgets and not of `self.settings`, because the whole point
        of the notes is to answer the number that is being typed rather than the
        one that was last saved.
        """
        return {
            "send_days": [i for i, box in enumerate(self.day_boxes)
                          if box.isChecked()],
            "send_start_hour": self.start_hour_spin.value(),
            "send_end_hour": self.end_hour_spin.value(),
            "send_min_gap_sec": self.min_gap_spin.value(),
            "send_max_gap_sec": self.max_gap_spin.value(),
            "daily_cap_per_account": self.daily_cap_spin.value(),
            "hourly_cap_per_account": self.hourly_cap_spin.value(),
            "warmup_enabled": self.warmup_cb.isChecked(),
            "warmup_start": self.warmup_start_spin.value(),
            "warmup_step": self.warmup_step_spin.value(),
            "warmup_max": self.warmup_max_spin.value(),
        }

    # ── Compliance ───────────────────────────────────────────────────────────

    def _build_compliance_page(self) -> QWidget:
        page, column = self._page(
            "Compliance",
            "What every email is required to carry, and the switch that decides "
            "whether any of it is really sent.")

        unsub = _Group(column)
        self.unsubscribe_edit = _line("unsubscribe@yourdomain.com")
        unsub.field("Unsubscribe address", self.unsubscribe_edit)
        unsub.foot(
            "Blank uses the sending account's own address. Every email carries a "
            "List-Unsubscribe header, and an unsubscribe cancels that lead's "
            "queued follow-ups."
        )

        carried = _Group(column, "What every email carries")
        self.append_unsubscribe_cb = self._guard_toggle(
            carried, "Append the unsubscribe line",
            "Off means a recipient has no way to opt out. That is what gets a "
            "sender reported and filtered, and for commercial mail it is illegal "
            "in most countries.")
        self.append_postal_cb = self._guard_toggle(
            carried, "Append the postal address",
            "Off removes the physical address CAN-SPAM requires in every "
            "commercial email. Filters read a missing one as a spam signal on "
            "its own.")
        self.require_profile_cb = self._guard_toggle(
            carried, "Require a complete sender profile before sending",
            "Off lets a campaign start with your name, your address or a verified "
            "sending account missing, at your own risk.")
        carried.foot(
            "All three are on because they are what keeps mail out of the spam "
            "folder. Each one can be switched off, and each one says what that "
            "costs."
        )

        rehearsal = _Group(column, "Dry run")
        self.dry_run_cb = QCheckBox(
            "Dry run — build and log every email, send none")
        self.dry_run_cb.setCursor(Qt.PointingHandCursor)
        self.dry_run_cb.toggled.connect(self._on_dry_run_toggled)
        self.live_warning = C.body_label(
            "LIVE SENDING IS ON. Starting a campaign will deliver real email to "
            "real businesses from your Gmail account.", tone="danger")
        rehearsal.wide(self.dry_run_cb, note=self.live_warning)
        rehearsal.foot(
            "A dry run walks the whole schedule and renders every message, but "
            "opens no SMTP connection and spends none of the day's quota. The "
            "queue goes back exactly as it was, so the campaign is still ready "
            "to send for real afterwards. Use it once before any new campaign."
        )

        column.addStretch()
        return page

    def _guard_toggle(self, group: _Group, label: str, cost: str) -> QCheckBox:
        """One protection, on by default, with the price of turning it off under it.

        The user asked not to be blocked and that is what these are: switches,
        not walls. What they must never be is silent — a footer quietly missing
        its unsubscribe line is a deliverability problem that shows up weeks
        later as a dead domain.
        """
        toggle = C.toggle(label, help=cost)
        toggle.setChecked(True)
        toggle.toggled.connect(lambda _on: self._schedule_template_preview())
        group.wide(toggle, note=toggle.help_label)
        return toggle

    # ── Appearance ───────────────────────────────────────────────────────────

    def _build_appearance_page(self) -> QWidget:
        """The two controls that make half the design system reachable at all.

        Both palettes and both densities exist in `ui/theme.py` and neither
        could be got at: writing `theme` or `density` into settings.json by hand
        was the only route, and `core.settings._merge` used to drop both keys on
        the next save. They take effect while you watch, because a theme control
        that needs a restart is not a theme control — and because the thing a
        density is chosen by is what it does to a table row, which is on this
        page under them.
        """
        page, column = self._page(
            "Appearance",
            "How this app looks. Both controls take effect while you watch.")

        look = _Group(column, "Look")
        self.theme_combo = _combo(THEME_CHOICES)
        self.density_combo = _combo(DENSITY_CHOICES)
        look.field("Theme", _loose(self.theme_combo))
        look.field("Density", _loose(self.density_combo))
        for combo in (self.theme_combo, self.density_combo):
            combo.currentIndexChanged.connect(
                lambda _index: self._on_appearance_picked())
        look.foot(
            "Both apply as you pick them. Save writes the choice to your "
            "settings file so the app opens in it next time."
        )

        preview = _Group(column, "What density does to a row")
        self.density_preview_box = QVBoxLayout()
        self.density_preview_box.setContentsMargins(
            _t().space["0"], _t().space["0"], _t().space["0"], _t().space["0"])
        self.density_preview_box.setSpacing(_t().space["2"])
        self.density_preview = None
        self.density_note = _hint("")
        preview.wide(self.density_preview_box, note=self.density_note)
        self._rebuild_density_preview()

        column.addStretch()
        return page

    def _rebuild_density_preview(self) -> None:
        """A real table at the density that is picked, so the choice is a picture.

        Built by `components.table` rather than drawn here, so what is shown is
        the row height every lead table in the app will get and not a rectangle
        that resembles one.
        """
        density = str(self.density_combo.currentData()
                      or _theme.DEFAULT_DENSITY)
        box = self.density_preview_box
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        table = C.table(DENSITY_PREVIEW_COLUMNS, density=density, sortable=False)
        for row in DENSITY_PREVIEW_ROWS:
            table.add_row(row)
        rows = _theme.theme(_t().name, density).control["row"]
        table.setFixedHeight(table.horizontalHeader().sizeHint().height()
                             + len(DENSITY_PREVIEW_ROWS) * rows
                             + 2 * C.BORDER)
        table.setFocusPolicy(Qt.NoFocus)
        box.addWidget(table)
        self.density_preview = table
        self.density_note.setText(
            "A row is %dpx here and a toolbar button %dpx. Comfortable is the "
            "default; compact fits about a fifth more leads on the same screen."
            % (rows, _theme.theme(_t().name, density).control["sm"]))

    def _on_appearance_picked(self) -> None:
        """Wear the choice now, from inside the signal that made it.

        Safe, and only because `restyle` hands the old layout to a throwaway
        widget and calls `deleteLater` on it rather than deleting it: the combo
        whose `currentIndexChanged` is still on the stack outlives the call it
        is in and is collected when the event loop next comes round. Deferring
        this to a zero timer instead read as the careful choice and was the
        fragile one — a zero timer fires when the queue is empty, so how many
        turns of the loop it takes for a theme to arrive is a question about
        what else the app happens to be doing.
        """
        if self._loading:
            return
        self._mark_dirty()
        self._rebuild_density_preview()
        self._apply_appearance()

    def _apply_appearance(self) -> None:
        wanted = {"theme": str(self.theme_combo.currentData() or ""),
                  "density": str(self.density_combo.currentData() or "")}
        worn = C.active_theme()
        if (wanted["theme"], wanted["density"]) == (worn.name, worn.density):
            return
        self.settings.update(wanted)
        window = self.window()
        apply = getattr(window, "apply_appearance", None)
        if callable(apply) and window is not self:
            apply(self.settings)
            return
        # No window to ask — a screen built on its own, which is every test that
        # constructs one. The palette still has to change, because the density
        # preview under the control is the thing being looked at.
        app = QApplication.instance()
        chosen = _theme.from_settings(self.settings)
        if app is not None:
            _theme.apply(app, chosen)
        C.use_theme(chosen)
        self.restyle()

    def _schedule_template_preview(self) -> None:
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    # ── what has been changed and not committed ──────────────────────────────

    def _watch_dirty(self) -> None:
        """Notice every edit, so the footer can say whether there is anything to save.

        Named one control at a time rather than swept off `findChildren`,
        because a sweep would take the template editor with it — and those two
        boxes write to a different file, are saved by a different button and
        already have `_mark_template_dirty` of their own. The one place the two
        meet is `_outstanding`, which says both.
        """
        for edit in list(self.profile_edits.values()) + [self.unsubscribe_edit]:
            edit.textChanged.connect(lambda _text: self._mark_dirty())
        for box in (self.postal_edit, self.proof_edit):
            box.textChanged.connect(self._mark_dirty)
        for field in (self.groq_key, self.openrouter_key):
            field.textChanged.connect(lambda _text: self._mark_dirty())
        for combo in (self.provider_combo, self.tone_combo, self.theme_combo,
                      self.density_combo):
            combo.currentIndexChanged.connect(lambda _index: self._mark_dirty())
        for combo in (self.groq_model, self.openrouter_model,
                      self.timezone_combo):
            combo.editTextChanged.connect(lambda _text: self._mark_dirty())
        for spin in (self.tokens_per_lead_spin, self.monthly_cap_spin,
                     self.start_hour_spin, self.end_hour_spin,
                     self.min_gap_spin, self.max_gap_spin, self.daily_cap_spin,
                     self.hourly_cap_spin, self.warmup_start_spin,
                     self.warmup_step_spin, self.warmup_max_spin,
                     self.followup_gap_spin, self.followup_steps_spin):
            spin.valueChanged.connect(lambda _value: self._mark_dirty())
        for toggle in (list(self.day_boxes)
                       + [self.warmup_cb, self.followup_cb, self.dry_run_cb,
                          self.append_unsubscribe_cb, self.append_postal_cb,
                          self.require_profile_cb]):
            toggle.toggled.connect(lambda _on: self._mark_dirty())
        self.services_list.itemChanged.connect(lambda _item: self._mark_dirty())

    def _mark_dirty(self) -> None:
        """One edit, anywhere on these tabs, and both places that say so."""
        if self._loading or self._building:
            return
        self._dirty = True
        _status(self.save_status, "Unsaved changes", "busy")
        self._refresh_footer()
        self._publish_to_shell()

    def _on_discard(self) -> None:
        """Put every field back to the file. The one thing Back used to prevent.

        Asked before it happens, because what it throws away is the only copy:
        an evening of sending-window arithmetic lives in these widgets and
        nowhere else until Save.
        """
        if not (self._dirty or self._template_dirty):
            return
        if not C.confirm(
                self, title="Discard changes?",
                body="Every field on these tabs goes back to what your settings "
                     "file holds, and any template open in the editor goes back "
                     "to what the template store holds. Neither can be brought "
                     "back afterwards.",
                confirm_text="Discard", danger=True):
            return
        self.settings = load_settings()
        self._dirty = False
        self._template_dirty = False
        self._template_notes = []
        self._load_into_ui()
        _status(self.save_status, "Changes discarded", "ok")
        self._refresh_footer()
        self._publish_to_shell()

    # ── load ─────────────────────────────────────────────────────────────────

    def _load_into_ui(self) -> None:
        settings = self.settings
        self._loading = True
        try:
            self._fill_from(settings)
        finally:
            self._loading = False
        # Every widget now holds what the file holds, so by definition there is
        # nothing outstanding. Leaving the flag set here left Discard armed over
        # a screen that matched disk, and the footer offering to undo nothing.
        self._dirty = False
        self._refresh_schedule_notes()
        self._refresh_footer()
        self._publish_to_shell()

    def _fill_from(self, settings: dict) -> None:
        self._select_data(self.provider_combo, settings.get("ai_provider", "auto"))
        self.groq_key.setText(get_secret(settings, "groq_api_key"))
        self.groq_model.setText(str(settings.get("groq_model") or ""))
        self._fetch_groq_models()
        self.openrouter_key.setText(get_secret(settings, "openrouter_api_key"))
        self.openrouter_model.setText(str(settings.get("openrouter_model") or ""))
        self._fetch_openrouter_models()
        self.tokens_per_lead_spin.setValue(self._int(settings.get("ai_max_tokens_per_lead"), 220))
        self.monthly_cap_spin.setValue(self._int(settings.get("ai_monthly_token_cap"), 2_000_000))
        for label in (self.groq_status, self.openrouter_status):
            _status(label, "", "busy")
        self._refresh_budget()

        profile = settings.get("sender_profile") or {}
        for key, edit in self.profile_edits.items():
            edit.setText(str(profile.get(key) or ""))
        self.postal_edit.setPlainText(str(profile.get("postal_address") or ""))
        # Explicitly, not via `contentsChanged`: replacing an empty document
        # with an empty one emits nothing, and the box would keep whatever
        # height it was built with.
        self._fit_postal_box()
        self._select_data(self.tone_combo, profile.get("tone", "direct"))
        self._load_services(profile.get("services") or [])
        self.proof_edit.setPlainText("\n".join(str(p) for p in (profile.get("proof_points") or [])))

        self._load_accounts(settings.get("smtp_accounts") or [])

        days = {self._int(d, -1) for d in (settings.get("send_days") or [])}
        for index, box in enumerate(self.day_boxes):
            box.setChecked(index in days)
        self.start_hour_spin.setValue(self._int(settings.get("send_start_hour"), 9))
        self.end_hour_spin.setValue(self._int(settings.get("send_end_hour"), 17))
        self.timezone_combo.setEditText(str(settings.get("send_timezone") or "local"))
        # Explicitly as well as on the signal: setting the same text the combo
        # already holds emits nothing, and a saved zone that stopped resolving
        # would then load with no note under it.
        self._refresh_timezone_note()
        self.min_gap_spin.setValue(self._int(settings.get("send_min_gap_sec"), 60))
        self.max_gap_spin.setValue(self._int(settings.get("send_max_gap_sec"), 240))
        self.daily_cap_spin.setValue(self._int(settings.get("daily_cap_per_account"), 40))
        self.hourly_cap_spin.setValue(self._int(settings.get("hourly_cap_per_account"), 12))
        self.warmup_cb.setChecked(bool(settings.get("warmup_enabled", True)))
        self.warmup_start_spin.setValue(self._int(settings.get("warmup_start"), 10))
        self.warmup_step_spin.setValue(self._int(settings.get("warmup_step"), 5))
        self.warmup_max_spin.setValue(self._int(settings.get("warmup_max"), 40))

        self.followup_cb.setChecked(bool(settings.get("followup_enabled", True)))
        self.followup_gap_spin.setValue(self._int(settings.get("followup_gap_days"), 4))
        self.followup_steps_spin.setValue(self._int(settings.get("followup_max_steps"), 2))

        self.unsubscribe_edit.setText(str(settings.get("unsubscribe_mailto") or ""))
        for key, box in (("append_unsubscribe", self.append_unsubscribe_cb),
                         ("append_postal_address", self.append_postal_cb),
                         ("require_profile_complete", self.require_profile_cb)):
            box.blockSignals(True)
            box.setChecked(bool(settings.get(key, True)))
            box.blockSignals(False)
        # Blocked: this is a restore, not a decision, so it must not raise the
        # "you are about to send real email" question.
        self.dry_run_cb.blockSignals(True)
        self.dry_run_cb.setChecked(bool(settings.get("dry_run", True)))
        self.dry_run_cb.blockSignals(False)
        self.live_warning.setVisible(not self.dry_run_cb.isChecked())

        self._select_data(self.theme_combo,
                          settings.get("theme") or _theme.DEFAULT_THEME)
        self._select_data(self.density_combo,
                          settings.get("density") or _theme.DEFAULT_DENSITY)
        self._rebuild_density_preview()
        _status(self.save_status, "", "busy")

        # Unsaved copy outlives a reload of the file around it: nothing in
        # `settings.json` can invalidate a paragraph somebody is halfway through
        # typing, and silently replacing it is the one thing a template editor
        # must never do.
        if self._template_dirty:
            self._refresh_template_preview()
        else:
            self._reload_templates(self._template_id)

    @staticmethod
    def _int(value, default: int = 0) -> int:
        return _int_of(value, default)

    def _profile_values(self) -> dict:
        """The sender profile as the widgets currently hold it.

        Read by `_collect` on the way to disk and by the template preview on
        every keystroke, so what the editor shows is rendered from the same
        identity a send would use — including edits not saved yet.
        """
        values = {key: edit.text().strip() for key, edit in self.profile_edits.items()}
        values["postal_address"] = self.postal_edit.toPlainText().strip()
        values["tone"] = self.tone_combo.currentData()
        values["services"] = self._checked_services()
        values["proof_points"] = _lines(self.proof_edit.toPlainText())
        return values

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(str(value or ""))
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _load_services(self, selected) -> None:
        chosen = {str(s).strip().lower() for s in selected if str(s).strip()}
        known: set[str] = set()
        self.services_list.clear()
        for category, names in AUTO_ARMY_SERVICES.items():
            header = QListWidgetItem(category)
            header.setFlags(Qt.NoItemFlags)
            self.services_list.addItem(header)
            for name in names:
                known.add(name.lower())
                item = QListWidgetItem(f"   {name}")
                item.setData(Qt.UserRole, name)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if name.lower() in chosen else Qt.Unchecked)
                self.services_list.addItem(item)

        # Anything the user added by hand keeps its place instead of vanishing on
        # the next save.
        extras = [str(s).strip() for s in selected if str(s).strip().lower() not in known]
        if not extras:
            return
        header = QListWidgetItem("Custom")
        header.setFlags(Qt.NoItemFlags)
        self.services_list.addItem(header)
        for name in extras:
            item = QListWidgetItem(f"   {name}")
            item.setData(Qt.UserRole, name)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.services_list.addItem(item)

    # ── services the user owns ───────────────────────────────────────────────

    def _custom_service_item(self):
        """The selected row, when it is one of theirs rather than a shipped one.

        Shipped services are the wording the gap-to-service mapping is written
        against, so renaming one would silently break that link; they are
        unticked instead. Anything under Custom is the user's to change.
        """
        item = self.services_list.currentItem()
        if item is None or not item.data(Qt.UserRole):
            return None
        name = str(item.data(Qt.UserRole))
        for names in AUTO_ARMY_SERVICES.values():
            if any(name.lower() == known.lower() for known in names):
                return None
        return item

    def _service_names(self) -> set:
        out = set()
        for row in range(self.services_list.count()):
            name = self.services_list.item(row).data(Qt.UserRole)
            if name:
                out.add(str(name).strip().lower())
        return out

    def _ask_service(self, title: str, preset: str = "") -> str:
        text, ok = QInputDialog.getText(self, title, "Service, in the wording an "
                                        "email should use:", QLineEdit.Normal, preset)
        if not ok:
            return ""
        text = " ".join(str(text).split())
        if not text:
            return ""
        if text.lower() in self._service_names() and text.lower() != preset.lower():
            self.toaster.show("%s is already on the list." % text, tone="warning")
            return ""
        return text

    def _add_service(self) -> None:
        name = self._ask_service("Add a service")
        if not name:
            return
        chosen = self._checked_services() + [name]
        self._load_services(chosen)
        self._mark_dirty()
        self.toaster.show("Added %s. Save to keep it." % name, tone="success")

    def _rename_service(self) -> None:
        item = self._custom_service_item()
        if item is None:
            self.toaster.show("Pick one of your own services to rename. The shipped "
                              "ones can be unticked but not reworded.", tone="info")
            return
        was = str(item.data(Qt.UserRole))
        name = self._ask_service("Rename service", was)
        if not name:
            return
        chosen = [name if s == was else s for s in self._checked_services()]
        self._load_services(chosen)
        self._mark_dirty()
        self.toaster.show("Renamed to %s. Save to keep it." % name, tone="success")

    def _remove_service(self) -> None:
        item = self._custom_service_item()
        if item is None:
            self.toaster.show("Pick one of your own services to remove. The shipped "
                              "ones can be unticked instead.", tone="info")
            return
        was = str(item.data(Qt.UserRole))
        chosen = [s for s in self._checked_services() if s != was]
        self._load_services(chosen)
        self._mark_dirty()
        self.toaster.show("Removed %s. Save to keep it." % was, tone="success")

    def _set_all_services(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.services_list.count()):
            item = self.services_list.item(row)
            if item.data(Qt.UserRole):
                item.setCheckState(state)

    def _checked_services(self) -> list[str]:
        out = []
        for row in range(self.services_list.count()):
            item = self.services_list.item(row)
            name = item.data(Qt.UserRole)
            if name and item.checkState() == Qt.Checked:
                out.append(str(name))
        return out

    def _refresh_timezone_note(self) -> None:
        note = _zone_note(self.timezone_combo.currentText())
        self.timezone_note.setText(note)
        self.timezone_note.setVisible(bool(note))

    def _refresh_budget(self) -> None:
        cap = max(0, self._int(self.settings.get("ai_monthly_token_cap"), 0))
        left = ai_budget_left(self.settings)
        used = max(0, cap - left)
        self.budget_bar.setMaximum(max(cap, 1))
        self.budget_bar.setValue(min(used, max(cap, 1)))
        month = str(self.settings.get("ai_tokens_month") or "this month")
        self.budget_label.setText(f"{used:,} of {cap:,} tokens used ({month})")

    # ── accounts ──

    def _load_accounts(self, accounts) -> None:
        for row in self._account_rows:
            row.setParent(None)
            row.deleteLater()
        self._account_rows = []
        self._relist_accounts()
        for account in accounts:
            if isinstance(account, dict):
                self._add_account_row(account)

    def _add_account_row(self, account: dict, at: int = -1) -> None:
        row = _AccountRow(account, len(self._account_rows) + 1)
        row.remove_requested.connect(self._remove_account_row)
        row.verify_requested.connect(self._verify_account_row)
        row.changed.connect(self._mark_dirty)
        row.changed.connect(self._refresh_schedule_notes)
        email = str(account.get("email") or "").strip()
        if email:
            row.set_password(get_secret(self.settings, f"smtp_accounts.{email}.app_password"))
        if 0 <= at < len(self._account_rows):
            self._account_rows.insert(at, row)
        else:
            self._account_rows.append(row)
        self._relist_accounts()

    def _relist_accounts(self) -> None:
        """Put the rows back in the column, renumbered from the top.

        Rebuilt from `_account_rows` rather than edited in place: the titles are
        ordinals, so an insert or a removal anywhere but the end leaves every
        heading below it wrong.

        No rule between them any more. Each account is a box of its own now, so
        a hairline in the gap is a line between two things that already have
        edges — the same mark the app uses *inside* a box to separate two rows,
        spent outside one to separate two boxes.
        """
        while self.accounts_box.count():
            item = self.accounts_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                if not isinstance(widget, _AccountRow):
                    widget.deleteLater()
        for index, row in enumerate(self._account_rows):
            row.renumber(index + 1)
            self.accounts_box.addWidget(row)
            row.show()
        self._update_accounts_placeholder()
        self._refresh_schedule_notes()

    def _on_add_account(self) -> None:
        self._add_account_row({})
        self._mark_dirty()
        self._account_rows[-1].email_edit.setFocus()

    def _remove_account_row(self, row: _AccountRow) -> None:
        """Taking a mailbox off the rota, asked first and undoable afterwards.

        It was none of those things: one click, no confirmation, no announcement
        and no way back — and what went with the row was the app password, which
        the user then has to go and mint again in their Google account because
        Google never shows an existing one twice.
        """
        if row not in self._account_rows:
            return
        name = row.email() or "This account"
        if not C.confirm(
                self, title="Remove %s?" % name,
                body="It comes off the sending rota and its app password is "
                     "forgotten. Google never shows an app password twice, so "
                     "putting it back means minting a new one.",
                confirm_text="Remove", danger=True):
            return
        at = self._account_rows.index(row)
        restore = (at, row.to_dict(), row.app_password())
        self._account_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._relist_accounts()
        self._mark_dirty()
        self.toaster.show("%s is off the sending rota." % name, tone="warning",
                          action="Undo",
                          on_action=lambda saved=restore: self._restore_account(saved))

    def _restore_account(self, saved) -> None:
        at, account, password = saved
        self._add_account_row(account, at=at)
        self._account_rows[min(at, len(self._account_rows) - 1)].set_password(password)
        self._mark_dirty()

    def _update_accounts_placeholder(self) -> None:
        self.no_accounts_label.setVisible(not self._account_rows)

    def _verify_account_row(self, row: _AccountRow) -> None:
        email, password = row.email(), row.app_password()
        if not email or not password:
            _status(row.status_label, "Enter the address and app password first.", "err")
            return
        row.set_verifying()
        self._start_probe(
            lambda: self._timed(lambda: mailer.verify_account(email, password)),
            row.show_verify_result,
        )

    # ── AI test ──

    def _test_provider(self, provider: str, key_field: QWidget, model_edit: QWidget,
                       button: QPushButton, status: QLabel) -> None:
        api_key, model = key_field.text(), model_edit.text().strip()
        if not api_key:
            _status(status, "Enter an API key first.", "err")
            return
        button.setEnabled(False)
        _status(status, "Calling the model…", "busy")

        def finished(ok: bool, message: str, latency_ms: int) -> None:
            button.setEnabled(True)
            _status(status, f"✓ {message} in {latency_ms} ms" if ok else message,
                    "ok" if ok else "err")

        self._start_probe(
            lambda: ai_client.test_provider(provider, api_key, model), finished)

    def _fetch_groq_models(self) -> None:
        self._fetch_models_async("groq", self.groq_key, self.groq_model)

    def _fetch_openrouter_models(self) -> None:
        self._fetch_models_async("openrouter", self.openrouter_key, self.openrouter_model)

    def _fetch_models_async(self, provider: str, key_field: QWidget,
                            model_combo: ModelComboBox) -> None:
        api_key = key_field.text()
        if not api_key:
            return

        probe = _FetchModelsProbe(provider, api_key, parent=self)
        def on_models_fetched(models: list[str]) -> None:
            if not models:
                return
            current = model_combo.text()
            model_combo.blockSignals(True)
            model_combo.clear()
            for m in models:
                model_combo.addItem(m)
            if current:
                model_combo.setText(current)
            model_combo.blockSignals(False)

        probe.result_signal.connect(on_models_fetched)
        probe.finished.connect(probe.deleteLater)
        probe.start()

    # ── probes ──

    @staticmethod
    def _timed(call):
        """Wrap a (ok, message) call so it reports a latency like `test_provider`."""
        started = time.perf_counter()
        ok, message = call()
        return ok, message, int((time.perf_counter() - started) * 1000)

    def _start_probe(self, task, on_result) -> None:
        probe = _Probe(task, parent=self)
        probe.result_signal.connect(on_result)
        probe.finished.connect(lambda: self._drop_probe(probe))
        self._probes.append(probe)
        probe.start()

    def _drop_probe(self, probe: _Probe) -> None:
        if probe in self._probes:
            self._probes.remove(probe)
        probe.deleteLater()

    # ── save ─────────────────────────────────────────────────────────────────

    def _on_dry_run_toggled(self, checked: bool) -> None:
        if checked:
            self.live_warning.hide()
            return

        answer = QMessageBox.warning(
            self, "Turn off dry run?",
            "Real emails will be sent to real businesses from your Gmail account, "
            "on the schedule below, as soon as a campaign starts.\n\n"
            "Nothing about a send can be undone once it leaves. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.dry_run_cb.blockSignals(True)
            self.dry_run_cb.setChecked(True)
            self.dry_run_cb.blockSignals(False)
            return
        self.live_warning.show()

    def _collect(self) -> None:
        """Fold the widgets back into `self.settings` in place.

        In place, because the dict also carries keys this screen never shows —
        the scrape settings, saved searches, the AI token counter — and rebuilding
        it would drop them.
        """
        settings = self.settings

        settings["ai_provider"] = self.provider_combo.currentData()
        settings["groq_model"] = self.groq_model.text().strip()
        settings["openrouter_model"] = self.openrouter_model.text().strip()
        settings["ai_max_tokens_per_lead"] = self.tokens_per_lead_spin.value()
        settings["ai_monthly_token_cap"] = self.monthly_cap_spin.value()

        settings.setdefault("sender_profile", {}).update(self._profile_values())

        settings["send_days"] = [i for i, box in enumerate(self.day_boxes) if box.isChecked()]
        settings["send_start_hour"] = self.start_hour_spin.value()
        settings["send_end_hour"] = self.end_hour_spin.value()
        settings["send_timezone"] = self.timezone_combo.currentText().strip() or "local"
        # An inverted range would hand the scheduler a gap it can never satisfy.
        low, high = sorted((self.min_gap_spin.value(), self.max_gap_spin.value()))
        settings["send_min_gap_sec"] = low
        settings["send_max_gap_sec"] = high
        settings["daily_cap_per_account"] = self.daily_cap_spin.value()
        settings["hourly_cap_per_account"] = self.hourly_cap_spin.value()
        settings["warmup_enabled"] = self.warmup_cb.isChecked()
        settings["warmup_start"] = self.warmup_start_spin.value()
        settings["warmup_step"] = self.warmup_step_spin.value()
        settings["warmup_max"] = self.warmup_max_spin.value()

        settings["followup_enabled"] = self.followup_cb.isChecked()
        settings["followup_gap_days"] = self.followup_gap_spin.value()
        settings["followup_max_steps"] = self.followup_steps_spin.value()

        settings["unsubscribe_mailto"] = self.unsubscribe_edit.text().strip()
        settings["append_unsubscribe"] = self.append_unsubscribe_cb.isChecked()
        settings["append_postal_address"] = self.append_postal_cb.isChecked()
        settings["require_profile_complete"] = self.require_profile_cb.isChecked()
        settings["dry_run"] = self.dry_run_cb.isChecked()

        settings["theme"] = str(self.theme_combo.currentData()
                                or _theme.DEFAULT_THEME)
        settings["density"] = str(self.density_combo.currentData()
                                  or _theme.DEFAULT_DENSITY)

        self._collect_accounts()
        # Written last so a renamed account keeps its key: `set_secret` looks the
        # row up by address, and the rows only carry the new addresses now.
        set_secret(settings, "groq_api_key", self.groq_key.text())
        set_secret(settings, "openrouter_api_key", self.openrouter_key.text())

    def _collect_accounts(self) -> None:
        rows, seen = [], set()
        for row in self._account_rows:
            email = row.email()
            key = email.lower()
            if not email or key in seen:
                continue
            seen.add(key)
            rows.append((row, row.to_dict()))

        self.settings["smtp_accounts"] = [entry for _, entry in rows]
        for row, entry in rows:
            set_secret(self.settings,
                       f"smtp_accounts.{entry['email']}.app_password", row.app_password())

    def _on_save(self) -> bool:
        """Write both stores, and say so where the user is still standing."""
        # First, because the template store is a separate file and a user who
        # pressed Save with a half-written follow-up open means both.
        self._save_open_template(quiet=True)
        self._collect()
        try:
            save_settings(self.settings)
        except OSError as exc:
            _status(self.save_status, f"Could not save: {exc}", "err")
            self._refresh_footer()
            return False
        self._dirty = False
        self._refresh_budget()
        _status(self.save_status, "Saved", "ok")
        self._refresh_footer()
        self._publish_to_shell()
        self.saved_signal.emit(self.settings)
        return True

    def _on_back(self) -> None:
        """Leave, having asked about anything that is not written down yet.

        Back used to call `_on_save` on its way out, so a sending window
        somebody was halfway through setting up was committed by the act of
        navigating away, and the "Saved" it produced appeared on a screen they
        were no longer looking at.
        """
        if (self._dirty or self._template_dirty) and not self._offer_to_leave():
            return
        self.back_signal.emit()

    def _offer_to_leave(self) -> bool:
        """Save, discard or stay. False means the screen stays where it is."""
        answer = QMessageBox.question(
            self, "Unsaved changes",
            "%s. Save before leaving?" % self._outstanding(),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self._on_save()
        self.settings = load_settings()
        self._dirty = False
        self._template_dirty = False
        self._template_notes = []
        self._load_into_ui()
        return True
