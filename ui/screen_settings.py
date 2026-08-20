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
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStackedWidget, QTextEdit, QToolTip, QVBoxLayout, QWidget,
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

# Every colour below already appears in the global QSS — the green and red from
# the status labels, the amber from QLabel#warning — so a validation line reads
# as the same language as the rest of the app rather than a fourth accent.
_GREEN = "#34C759"
_AMBER = "#FFB340"
_RED = "#FF6B6B"

# One more than the follow-up cap on the Sending tab, so a template can always be
# written for the last step the scheduler will ever ask for.
_MAX_STEP = 5

# Long enough that a fast typist is never fighting the renderer, short enough
# that the preview still reads as live.
_PREVIEW_DEBOUNCE_MS = 250

# The shortest preview that still shows an email rather than an envelope,
# measured off the document `_as_paper` builds: the subject line ends 39px down,
# the To line 60px, the first line of the message 97px and the line under it
# 145px. Two lines of the message is the least that says anything about the copy
# — one is a greeting — so this is the floor the preview is never squeezed past,
# at any window size and with any number of findings on screen.
_PREVIEW_FLOOR = 150

# What is left for the boxes once the preview has its floor: the name row, the
# subject line and the first row of the palette, which is enough to know where
# in the editor the column has been scrolled to.
_EDITOR_FLOOR = 150

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

def _section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section_label")
    return label


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


def _status(label: QLabel, text: str, kind: str = "busy") -> None:
    """Colour a one-line result label. `kind` is ok | err | busy."""
    label.setObjectName({"ok": "status_ok", "err": "status_err"}.get(kind, "status_busy"))
    label.setText(text)
    label.setVisible(bool(text))
    label.style().unpolish(label)
    label.style().polish(label)


def _spin(minimum: int, maximum: int, step: int = 1, suffix: str = "", width: int = 90) -> QSpinBox:
    box = QSpinBox()
    box.setObjectName("spin")
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setFixedWidth(width)
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
    `setPalette`, so emphasis inside a label has to arrive as rich text.
    """
    return '<span style="color:%s">%s</span>' % (colour or "#E5E5E7", html.escape(text))


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
            "the tzdata package — and restart." % label, _RED)
    return _rich("Resolved: the hours above are kept in %s, whatever this machine's "
                 "clock reads." % label, _GREEN)


def _step_name(step: int) -> str:
    step = max(0, _int_of(step))
    return "First touch" if step == 0 else "Follow-up %d" % step


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
    """Every merge field as a button that wraps to whatever width it is given.

    A grid would have to pick a column count, and the field names run from
    `phone` to `gap_1_evidence`; at four columns half the row is air and at six
    the long names are cut. So the chips are laid out by hand and the widget
    reports the height that layout actually needed, which is what lets the page
    around it scroll instead of clipping the last row.

    No chip may take the focus — a chip that could would take it on the click
    too, and the caret the field is inserted at goes with it. That left the
    whole palette, and every tooltip in it, unreachable from the keyboard. So
    the bar takes the focus the chips refuse: Tab lands here once, the arrow
    keys walk the row, and Enter inserts.
    """

    insert_requested = pyqtSignal(str)

    # 28 because that is the height QPushButton#tab reserves in the global
    # sheet. Forced shorter, the sheet's own vertical padding eats the
    # descenders and every underscore in a field name disappears.
    _GAP = 5
    _CHIP_HEIGHT = 28
    # The sheet's own `#tab:checked` fill, applied by hand, because the chips
    # are not checkable. A fill and not a ring: `border` is part of the box, so
    # every chip behind the marked one would shift along the row on each arrow
    # key, and `outline` costs no width but paints nothing at all once a
    # background-color has put the button on Qt's own stylesheet painting path.
    _MARK = "color: #E5E5E7; background-color: #3A3A3C;"
    _STEPS = {Qt.Key_Right: 1, Qt.Key_Down: 1, Qt.Key_Left: -1, Qt.Key_Up: -1}

    def __init__(self, fields, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAccessibleName("Merge fields")
        self._chips: list[QPushButton] = []
        self._at = 0
        for field in fields:
            chip = QPushButton(field, self)
            chip.setObjectName("tab")
            chip.setFixedHeight(self._CHIP_HEIGHT)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setToolTip(_field_tooltip(field))
            chip.clicked.connect(lambda _checked=False, name=field:
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
            chip.setStyleSheet(self._MARK if marked and index == self._at else "")
        if not marked:
            QToolTip.hideText()
            return
        chip = self._chips[self._at]
        QToolTip.showText(chip.mapToGlobal(chip.rect().bottomLeft()),
                          chip.toolTip(), chip)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        width = max(1, self.width())
        x = y = row_height = 0
        for chip in self._chips:
            size = chip.sizeHint()
            chip_width = min(size.width(), width)
            if x and x + chip_width > width:
                x = 0
                y += row_height + self._GAP
                row_height = 0
            chip.setGeometry(x, y, chip_width, self._CHIP_HEIGHT)
            x += chip_width + self._GAP
            row_height = max(row_height, self._CHIP_HEIGHT)
        wanted = y + row_height
        # Guarded: setting a fixed height re-runs the parent layout, which can
        # hand this widget a new width and call straight back in here.
        if wanted and wanted != self.minimumHeight():
            self.setFixedHeight(wanted)


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


class _IssuePane(QScrollArea):
    """The validation findings, bounded and scrolled instead of unbounded.

    Nothing caps how many findings one template collects, and a word-wrapped
    label handed a dozen of them grew past 250px and pushed the preview under
    the bottom of the window — so the pane that shows what the copy will look
    like disappeared exactly when the copy most needed looking at. A ceiling and
    a scrollbar of its own keep the findings readable and the preview in place.

    `heightForWidth` rather than a plain maximum: the findings wrap, so how tall
    they are is a function of how wide the column is, and a fixed height would
    be either mostly air on one finding or a scrollbar on two.
    """

    _CEILING = 96

    def __init__(self, body: QLabel, parent=None):
        super().__init__(parent)
        self.setWidget(body)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMaximumHeight(self._CEILING)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        body, chrome = self.widget(), 2 * self.frameWidth()
        wanted = body.heightForWidth(max(1, width - chrome)) if body is not None else 0
        return max(0, min(wanted + chrome, self._CEILING))

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(),
                     self.heightForWidth(max(1, self.width())))


class _SecretField(QWidget):
    """Password-echo line edit with a reveal toggle.

    A live Gmail app password sitting in a visible box is both a shoulder-surfing
    hole and the reason users paste screenshots of their credentials into support
    threads. The toggle exists because a masked field with no way to check what
    you typed is how people get locked out.
    """

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFixedHeight(34)

        self.toggle = QPushButton("Show")
        self.toggle.setObjectName("reveal")
        self.toggle.setCheckable(True)
        self.toggle.setFixedSize(58, 34)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.toggled.connect(self._on_toggled)

        layout.addWidget(self.edit, stretch=1)
        layout.addWidget(self.toggle)

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


class ModelComboBox(QComboBox):
    """An editable QComboBox that acts like QLineEdit (with text/setText API) for seamless settings integration."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setPlaceholderText(placeholder)
        self.setFixedHeight(34)

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

    def __init__(self, account: dict, index: int, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel(f"Account {index}")
        title.setObjectName("muted")
        self.enabled_cb = QCheckBox("Enabled")
        self.enabled_cb.setToolTip("Disabled accounts stay configured but never send")
        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("danger")
        remove_btn.setFixedHeight(28)
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self.enabled_cb)
        head.addWidget(remove_btn)
        root.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@gmail.com")
        self.email_edit.setFixedHeight(34)
        grid.addWidget(QLabel("Gmail address"), 0, 0)
        grid.addWidget(self.email_edit, 0, 1, 1, 2)

        self.password_field = _SecretField("16-character app password")
        self.verify_btn = QPushButton("Verify")
        self.verify_btn.setObjectName("outlined")
        self.verify_btn.setFixedSize(84, 34)
        self.verify_btn.setToolTip("Sign in to Gmail with these credentials without sending anything")
        self.verify_btn.clicked.connect(lambda: self.verify_requested.emit(self))
        grid.addWidget(QLabel("App password"), 1, 0)
        grid.addWidget(self.password_field, 1, 1)
        grid.addWidget(self.verify_btn, 1, 2)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status_busy")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        grid.addWidget(self.status_label, 2, 1, 1, 2)

        self.display_edit = QLineEdit()
        self.display_edit.setPlaceholderText("Name recipients see, e.g. Sam Rivera")
        self.display_edit.setFixedHeight(34)
        grid.addWidget(QLabel("Display name"), 3, 0)
        grid.addWidget(self.display_edit, 3, 1, 1, 2)

        caps_row = QHBoxLayout()
        caps_row.setSpacing(10)
        self.daily_cap_spin = _spin(1, 500, 5, "/day", 96)
        self.daily_cap_spin.setToolTip("This account's own ceiling; the global cap still applies")
        caps_row.addWidget(self.daily_cap_spin)
        self.warmup_cb = QCheckBox("Warm up from")
        self.warmup_cb.setToolTip("Ramp this account's daily volume from the warm-up start date")
        self.warmup_date = QDateEdit()
        self.warmup_date.setObjectName("spin")
        self.warmup_date.setDisplayFormat("yyyy-MM-dd")
        self.warmup_date.setCalendarPopup(True)
        self.warmup_date.setFixedWidth(120)
        self.warmup_cb.toggled.connect(self.warmup_date.setEnabled)
        caps_row.addWidget(self.warmup_cb)
        caps_row.addWidget(self.warmup_date)
        caps_row.addStretch()
        grid.addWidget(QLabel("Daily cap"), 4, 0)
        grid.addLayout(caps_row, 4, 1, 1, 2)

        self.imap_cb = QCheckBox("Read replies and bounces on this mailbox (IMAP)")
        self.imap_cb.setToolTip("Lets the app detect replies and hard bounces on this mailbox")
        grid.addWidget(self.imap_cb, 5, 1, 1, 2)

        root.addLayout(grid)
        self._load(account or {})

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

    TABS = ("AI", "Sender", "Templates", "Gmail", "Sending", "Compliance")

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
        """
        self.settings = load_settings()
        self._load_into_ui()

    def hideEvent(self, event) -> None:
        """Leaving the screen commits whatever template was open.

        `_on_back` already writes the settings on the way out, and this is the
        same promise for the copy: a follow-up somebody was halfway through must
        not depend on their remembering a second button. Nothing is asked —
        a dialog raised by a screen that is disappearing, or by a window that is
        closing, is a dialog nobody can answer.
        """
        if self._template_dirty and self._template_id:
            if _save_template(self._editor_template()):
                self._template_dirty = False
        super().hideEvent(event)

    # ── construction ──

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(10)
        back_btn = QPushButton("Back")
        back_btn.setObjectName("outlined")
        back_btn.setFixedHeight(30)
        back_btn.clicked.connect(self._on_back)
        title = QLabel("Settings")
        title.setObjectName("app_name")
        header.addWidget(back_btn)
        header.addWidget(title)
        header.addStretch()

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for index, label in enumerate(self.TABS):
            btn = QPushButton(label)
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            self.tab_group.addButton(btn, index)
            header.addWidget(btn)

        header.addSpacing(8)
        self.save_status = QLabel("")
        self.save_status.setObjectName("status_busy")
        header.addWidget(self.save_status)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("start_btn")
        save_btn.setFixedHeight(30)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        header.addWidget(save_btn)
        root.addLayout(header)
        root.addSpacing(18)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._scrolled(self._build_ai_page()))
        self.pages.addWidget(self._scrolled(self._build_sender_page()))
        # Not `_scrolled`: this page scrolls its own editor column and keeps the
        # template list and its four buttons pinned. Wrapped whole, the New and
        # Delete buttons sit below the preview and go off the bottom of a 760px
        # window, so the way to add a template is to scroll past the one you are
        # writing.
        self.pages.addWidget(self._build_templates_page())
        self.pages.addWidget(self._scrolled(self._build_gmail_page()))
        self.pages.addWidget(self._scrolled(self._build_sending_page()))
        self.pages.addWidget(self._scrolled(self._build_compliance_page()))
        root.addWidget(self.pages, stretch=1)

        self.tab_group.idClicked.connect(self.pages.setCurrentIndex)

    def _scrolled(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    # ── AI ──

    def _build_ai_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)

        layout.addWidget(_section_label("Provider"))
        layout.addSpacing(10)
        self.provider_combo = QComboBox()
        for value, label in PROVIDERS:
            self.provider_combo.addItem(label, value)
        self.provider_combo.setFixedWidth(260)
        layout.addWidget(self.provider_combo)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "The model writes one subject line, one opener and one PS per lead. "
            "With the provider off, emails still send using the plain templates."
        ))
        layout.addSpacing(22)

        self.groq_key, self.groq_model, self.groq_status = self._provider_block(
            layout, "Groq", "gsk_…", "llama-3.3-70b-versatile", "groq")
        self.groq_key.editingFinished.connect(self._fetch_groq_models)
        layout.addSpacing(22)
        self.openrouter_key, self.openrouter_model, self.openrouter_status = self._provider_block(
            layout, "OpenRouter", "sk-or-…", "meta-llama/llama-3.3-70b-instruct", "openrouter")
        self.openrouter_key.editingFinished.connect(self._fetch_openrouter_models)
        layout.addSpacing(22)

        layout.addWidget(_section_label("Token budget"))
        layout.addSpacing(10)
        budget_grid = QGridLayout()
        budget_grid.setHorizontalSpacing(14)
        budget_grid.setVerticalSpacing(10)
        self.tokens_per_lead_spin = _spin(60, 600, 10, "", 96)
        self.monthly_cap_spin = _spin(0, 100_000_000, 100_000, "", 130)
        budget_grid.addWidget(QLabel("Max tokens per lead"), 0, 0)
        budget_grid.addWidget(self.tokens_per_lead_spin, 0, 1)
        budget_grid.addWidget(QLabel("Monthly token cap"), 1, 0)
        budget_grid.addWidget(self.monthly_cap_spin, 1, 1)
        budget_grid.setColumnStretch(2, 1)
        layout.addLayout(budget_grid)
        layout.addSpacing(12)

        self.budget_bar = QProgressBar()
        self.budget_bar.setObjectName("budget_bar")
        self.budget_bar.setTextVisible(False)
        self.budget_bar.setFixedHeight(10)
        layout.addWidget(self.budget_bar)
        layout.addSpacing(6)
        self.budget_label = QLabel("")
        self.budget_label.setObjectName("muted")
        layout.addWidget(self.budget_label)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Answers are cached per business, so re-running the same leads costs "
            "nothing. When the cap is spent the plain templates take over."
        ))
        layout.addStretch()
        return page

    def _provider_block(self, layout: QVBoxLayout, name: str, key_placeholder: str,
                        model_placeholder: str, provider: str):
        layout.addWidget(_section_label(name))
        layout.addSpacing(10)

        key_field = _SecretField(key_placeholder)
        test_btn = QPushButton("Test")
        test_btn.setObjectName("outlined")
        test_btn.setFixedSize(84, 34)
        test_btn.setToolTip(f"Send one five-token request to {name} and report the round trip")
        status = QLabel("")
        status.setObjectName("status_busy")
        status.setWordWrap(True)
        status.hide()

        model_edit = ModelComboBox(model_placeholder)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("API key"), 0, 0)
        grid.addWidget(key_field, 0, 1)
        grid.addWidget(test_btn, 0, 2)
        grid.addWidget(status, 1, 1, 1, 2)
        grid.addWidget(QLabel("Model"), 2, 0)
        grid.addWidget(model_edit, 2, 1, 1, 2)
        layout.addLayout(grid)

        test_btn.clicked.connect(
            lambda: self._test_provider(provider, key_field, model_edit, test_btn, status))
        return key_field, model_edit, status

    # ── Sender profile ──

    def _build_sender_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)

        layout.addWidget(_section_label("Identity"))
        layout.addSpacing(10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

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
        for row, (key, label, placeholder) in enumerate(rows):
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(34)
            self.profile_edits[key] = edit
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)
        layout.addSpacing(16)

        layout.addWidget(_section_label("Postal address"))
        layout.addSpacing(10)
        self.postal_edit = QTextEdit()
        self.postal_edit.setPlaceholderText("Street, city, region, postal code, country")
        self.postal_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.postal_edit.document().contentsChanged.connect(self._fit_postal_box)
        self._fit_postal_box()
        layout.addWidget(self.postal_edit)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Legally required in the footer of every commercial email. Campaigns "
            "check for it before sending unless you switch that check off under "
            "Compliance."
        ))
        layout.addSpacing(22)

        layout.addWidget(_section_label("Tone"))
        layout.addSpacing(10)
        self.tone_combo = QComboBox()
        for value, label in TONES:
            self.tone_combo.addItem(label, value)
        self.tone_combo.setFixedWidth(200)
        layout.addWidget(self.tone_combo)
        layout.addSpacing(22)

        services_head = QHBoxLayout()
        services_head.addWidget(_section_label("Services you sell"))
        services_head.addStretch()
        for text, checked in (("All", True), ("None", False)):
            btn = QPushButton(text)
            btn.setObjectName("outlined")
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _, state=checked: self._set_all_services(state))
            services_head.addWidget(btn)
        layout.addLayout(services_head)
        layout.addSpacing(10)
        self.services_list = QListWidget()
        self.services_list.setObjectName("service_list")
        self.services_list.setMinimumHeight(220)
        layout.addWidget(self.services_list)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Only the ticked services are ever offered in an email, and always in "
            "this exact wording."
        ))
        layout.addSpacing(22)

        layout.addWidget(_section_label("Proof points"))
        layout.addSpacing(10)
        self.proof_edit = QTextEdit()
        self.proof_edit.setFixedHeight(90)
        self.proof_edit.setPlaceholderText(
            "One per line, e.g. cut a roofing client's quote turnaround from 2 days to 20 minutes"
        )
        layout.addWidget(self.proof_edit)
        layout.addStretch()
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

    # ── Templates ──

    def _build_templates_page(self) -> QWidget:
        page = QWidget()
        columns = QHBoxLayout(page)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(16)
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
        scroll.setMinimumHeight(_EDITOR_FLOOR)

        # The split is draggable because which half matters is the user's call,
        # not this screen's: reading the copy back wants a tall preview and
        # rewriting it wants tall boxes. Neither half may be dragged away —
        # both carry a minimum, and the growth goes mostly to the boxes so that
        # a wider window does not turn into a wall of preview.
        split = QSplitter(Qt.Vertical)
        split.setObjectName("template_split")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.setStyleSheet(
            "QSplitter#template_split::handle { background: transparent; "
            "border-top: 1px solid #3A3A3C; }")
        split.addWidget(scroll)
        split.addWidget(self._build_template_preview_panel())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        # A proportion rather than two heights: the boxes and the palette ask
        # for more than either window size has, so the preview opens on its
        # floor — the whole message is a drag away and the copy being written
        # keeps the rest.
        split.setSizes([4 * _EDITOR_FLOOR, _PREVIEW_FLOOR])
        columns.addWidget(split, stretch=1)
        return page

    def _build_template_list_column(self) -> QWidget:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        column.addWidget(_section_label("Your templates"))

        self.template_list = QListWidget()
        self.template_list.setObjectName("saved_list")
        # Elided, not clipped: a name somebody typed can be any length. From the
        # middle, because the marker that says whether the row is theirs sits at
        # the end of it — cut from the right, an 84-character name renders with
        # nothing to say whether Delete or Reset is the button for it.
        self.template_list.setTextElideMode(Qt.ElideMiddle)
        self.template_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.template_list.setMinimumHeight(200)
        self.template_list.currentItemChanged.connect(self._on_template_row_changed)
        column.addWidget(self.template_list, stretch=1)

        buttons = QGridLayout()
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(8)
        specs = (
            ("template_new_btn", "New", "outlined", 0, 0, self._on_template_new,
             "Start a new template from a working first touch"),
            ("template_copy_btn", "Duplicate", "outlined", 0, 1, self._on_template_duplicate,
             "Copy what is in the editor into a template of your own"),
            ("template_delete_btn", "Delete", "danger", 1, 0, self._on_template_delete,
             "Remove this template for good"),
            ("template_reset_btn", "Reset", "outlined", 1, 1, self._on_template_reset,
             "Put this built-in template back to the wording it shipped with"),
        )
        for attr, label, style, row, col, handler, tip in specs:
            button = QPushButton(label)
            button.setObjectName(style)
            button.setFixedHeight(30)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            setattr(self, attr, button)
            buttons.addWidget(button, row, col)
        column.addLayout(buttons)
        column.addWidget(_hint(
            "Built-in templates can be edited and put back afterwards. Copies you "
            "make are yours, and only yours can be deleted."
        ))

        holder = QWidget()
        holder.setLayout(column)
        holder.setFixedWidth(252)
        return holder

    def _build_template_editor_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.template_name_edit = QLineEdit()
        self.template_name_edit.setPlaceholderText("What you will call this in the picker")
        self.template_name_edit.setFixedHeight(34)
        self.template_name_edit.textChanged.connect(self._mark_template_dirty)
        self.template_name_edit.returnPressed.connect(lambda: self._save_open_template())
        self.template_step_combo = QComboBox()
        for step in range(_MAX_STEP + 1):
            self.template_step_combo.addItem(_step_name(step), step)
        self.template_step_combo.setFixedWidth(140)
        self.template_step_combo.setToolTip(
            "Which touch this template writes: the first email, or the follow-up "
            "that goes out when nobody has replied to the one before it")
        self.template_step_combo.currentIndexChanged.connect(
            lambda _index: self._mark_template_dirty())
        self.template_save_btn = QPushButton("Save template")
        self.template_save_btn.setObjectName("start_btn")
        self.template_save_btn.setFixedHeight(34)
        self.template_save_btn.setCursor(Qt.PointingHandCursor)
        self.template_save_btn.clicked.connect(lambda: self._save_open_template())
        head.addWidget(QLabel("Name"))
        head.addWidget(self.template_name_edit, stretch=1)
        head.addWidget(QLabel("Step"))
        head.addWidget(self.template_step_combo)
        head.addWidget(self.template_save_btn)
        column.addLayout(head)

        subject_row = QHBoxLayout()
        subject_row.setSpacing(8)
        subject_row.addWidget(_section_label("Subject"))
        subject_row.addStretch()
        self.template_subject_count = QLabel("")
        self.template_subject_count.setObjectName("muted")
        self.template_subject_count.setTextFormat(Qt.RichText)
        subject_row.addWidget(self.template_subject_count)
        column.addLayout(subject_row)

        self.template_subject_edit = _FlatLine()
        self.template_subject_edit.setPlaceholderText("Lower case, no shouting, under 55 characters")
        self.template_subject_edit.setFixedHeight(34)
        self.template_subject_edit.textChanged.connect(self._mark_template_dirty)
        self.template_subject_edit.returnPressed.connect(lambda: self._save_open_template())
        self._watch_caret(self.template_subject_edit)
        column.addWidget(self.template_subject_edit)

        self.template_chips = _ChipBar(MERGE_FIELDS)
        self.template_chips.insert_requested.connect(self._insert_merge_field)
        column.addWidget(self.template_chips)
        column.addWidget(_hint(
            "Click a field to drop it in at the cursor. Hover one for what it "
            "resolves to."
        ))

        column.addWidget(_section_label("Body"))
        self.template_body_edit = _FlexEdit()
        self.template_body_edit.setAcceptRichText(False)
        self.template_body_edit.setMinimumHeight(84)
        self.template_body_edit.setStyleSheet(
            "font-family: '%s'; font-size: 12px;" % _mono_family())
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
        self.template_issues_pane = _IssuePane(self.template_issues)
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
        at every window size, and `_PREVIEW_FLOOR` is the least of the message
        it may ever be squeezed to.
        """
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(6)
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
            _PREVIEW_FLOOR + margins.top() + margins.bottom())
        column.addWidget(self.template_preview, stretch=1)
        column.addWidget(_hint(
            "Rendered for a sample lead through the same code that sends, footer "
            "and unsubscribe line included."
        ))
        return panel

    # ── Templates: the list ──

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
        return True

    # ── Templates: editing ──

    def _editor_template(self):
        return _templates.Template(
            id=self._template_id,
            name=self.template_name_edit.text().strip(),
            step=max(0, _int_of(self.template_step_combo.currentData())),
            subject=self.template_subject_edit.text(),
            body=self.template_body_edit.toPlainText(),
        )

    def _mark_template_dirty(self) -> None:
        if self._template_loading:
            return
        if not self._template_dirty:
            self._template_dirty = True
            _status(self.save_status, "Unsaved", "busy")
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
        if announce:
            self.saved_signal.emit(self.settings)

    def _store_failed(self, verb: str) -> None:
        """Say a template write did not happen, loudly enough to be believed.

        The header has room for two words and this needs more than two: a store
        that will not accept a write is a read-only profile directory or a full
        disk, and a user who reads "Saved" and closes the window loses the
        evening. The short form stays in the header for the record.
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

    # ── Templates: preview ──

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
            _RED if over else _GREEN))

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
            colour = _RED if level.startswith("err") else _AMBER
            lines.append(_rich("%s: %s" % (field, message) if field else message, colour))
        self.template_issues.setText("<br>".join(lines))
        self.template_issues_pane.setVisible(bool(lines))
        self.template_issues_pane.updateGeometry()

    def _as_paper(self, subject: str, body_text: str) -> str:
        """The message laid out as the recipient's mail client would show it.

        Every colour and size is inline: this widget's QSS is written for a dark
        app, and a preview that does not look like an email is not a preview.
        """
        blocks = []
        for para in re.split(r"\n\s*\n", str(body_text or "").strip()):
            rows = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
            if rows:
                blocks.append('<p style="margin:0 0 14px 0;">%s</p>' % "<br>".join(rows))
        return (
            '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
            'sans-serif;color:#1A1A1A;">'
            '<p style="margin:0 0 4px 0;font-size:16px;font-weight:600;">%s</p>'
            '<p style="margin:0 0 16px 0;font-size:12px;color:#6A6A6A;">To %s &lt;%s&gt;</p>'
            '<div style="font-size:15px;line-height:1.6;">%s</div></div>'
            % (html.escape(subject or "(no subject)"),
               html.escape(str(SAMPLE_LEAD.get("name") or "")),
               html.escape(str(SAMPLE_LEAD.get("email") or "")),
               "".join(blocks))
        )

    def _show_paper(self, message: str) -> None:
        self.template_preview.setHtml(
            '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
            'sans-serif;font-size:14px;line-height:1.6;color:#6A6A6A;">%s</div>'
            % html.escape(message))
        self._paint_paper()

    def _paint_paper(self) -> None:
        """Paint the preview document itself white.

        The body carries its own near-black text colour, so on the app's dark
        surface it would otherwise be invisible — and there is no QSS rule for
        `#email_paper` to lean on.
        """
        frame = self.template_preview.document().rootFrame()
        fmt = frame.frameFormat()
        fmt.setBackground(QColor("#FFFFFF"))
        fmt.setMargin(16)
        frame.setFrameFormat(fmt)

    # ── Gmail accounts ──

    def _build_gmail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)

        layout.addWidget(_section_label("Sending accounts"))
        layout.addSpacing(10)
        layout.addWidget(_hint(APP_PASSWORD_HINT))
        layout.addSpacing(16)

        self.accounts_box = QVBoxLayout()
        self.accounts_box.setSpacing(16)
        layout.addLayout(self.accounts_box)
        layout.addSpacing(14)

        self.no_accounts_label = QLabel("No sending accounts yet — add one to send anything.")
        self.no_accounts_label.setObjectName("muted")
        self.no_accounts_label.setWordWrap(True)
        layout.addWidget(self.no_accounts_label)
        layout.addSpacing(10)

        add_row = QHBoxLayout()
        add_btn = QPushButton("Add account")
        add_btn.setObjectName("outlined")
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(lambda: self._add_account_row({}))
        add_row.addWidget(add_btn)
        add_row.addStretch()
        layout.addLayout(add_row)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Several accounts spread the volume and keep any one mailbox under "
            "Gmail's limits. Verify each one before the first campaign."
        ))
        layout.addStretch()
        return page

    # ── Sending schedule ──

    def _build_sending_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)

        layout.addWidget(_section_label("Days"))
        layout.addSpacing(10)
        days_row = QHBoxLayout()
        days_row.setSpacing(14)
        self.day_boxes: list[QCheckBox] = []
        for name in DAY_NAMES:
            box = QCheckBox(name)
            self.day_boxes.append(box)
            days_row.addWidget(box)
        days_row.addStretch()
        layout.addLayout(days_row)
        layout.addSpacing(22)

        layout.addWidget(_section_label("Window"))
        layout.addSpacing(10)
        window_row = QHBoxLayout()
        window_row.setSpacing(10)
        self.start_hour_spin = _spin(0, 23, 1, ":00", 84)
        self.end_hour_spin = _spin(1, 24, 1, ":00", 84)
        self.timezone_combo = QComboBox()
        self.timezone_combo.setEditable(True)
        self.timezone_combo.addItems(list(COMMON_ZONES))
        self.timezone_combo.setFixedWidth(200)
        window_row.addWidget(QLabel("From"))
        window_row.addWidget(self.start_hour_spin)
        window_row.addWidget(QLabel("to"))
        window_row.addWidget(self.end_hour_spin)
        window_row.addSpacing(12)
        window_row.addWidget(QLabel("Timezone"))
        window_row.addWidget(self.timezone_combo)
        window_row.addStretch()
        layout.addLayout(window_row)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Local time unless you name an IANA zone. Naming one sends at those "
            "hours in the customer's day rather than in yours."
        ))
        self.timezone_note = _hint("")
        self.timezone_note.setVisible(False)
        layout.addWidget(self.timezone_note)
        self.timezone_combo.currentTextChanged.connect(self._refresh_timezone_note)
        layout.addSpacing(22)

        layout.addWidget(_section_label("Pacing"))
        layout.addSpacing(10)
        pace_grid = QGridLayout()
        pace_grid.setHorizontalSpacing(12)
        pace_grid.setVerticalSpacing(10)
        self.min_gap_spin = _spin(5, 7200, 15, " s", 100)
        self.max_gap_spin = _spin(5, 7200, 15, " s", 100)
        self.daily_cap_spin = _spin(1, 500, 5, "", 100)
        self.hourly_cap_spin = _spin(1, 200, 1, "", 100)
        for row, (label, widget) in enumerate((
            ("Minimum gap between sends", self.min_gap_spin),
            ("Maximum gap between sends", self.max_gap_spin),
            ("Daily cap per account", self.daily_cap_spin),
            ("Hourly cap per account", self.hourly_cap_spin),
        )):
            pace_grid.addWidget(QLabel(label), row, 0)
            pace_grid.addWidget(widget, row, 1)
        pace_grid.setColumnStretch(2, 1)
        layout.addLayout(pace_grid)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Each gap is picked at random inside the range. A fixed interval is "
            "the clearest automation fingerprint a filter can look for."
        ))
        layout.addSpacing(22)

        layout.addWidget(_section_label("Warm-up"))
        layout.addSpacing(10)
        self.warmup_cb = QCheckBox("Ramp new accounts up gradually")
        layout.addWidget(self.warmup_cb)
        layout.addSpacing(10)
        warm_grid = QGridLayout()
        warm_grid.setHorizontalSpacing(12)
        warm_grid.setVerticalSpacing(10)
        self.warmup_start_spin = _spin(1, 200, 1, "/day", 100)
        self.warmup_step_spin = _spin(1, 100, 1, "/day", 100)
        self.warmup_max_spin = _spin(1, 500, 5, "/day", 100)
        for row, (label, widget) in enumerate((
            ("Start at", self.warmup_start_spin),
            ("Increase each day by", self.warmup_step_spin),
            ("Stop increasing at", self.warmup_max_spin),
        )):
            warm_grid.addWidget(QLabel(label), row, 0)
            warm_grid.addWidget(widget, row, 1)
        warm_grid.setColumnStretch(2, 1)
        layout.addLayout(warm_grid)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "A brand-new Gmail account that sends 40 cold emails on day one gets "
            "throttled or suspended. The ramp starts from each account's warm-up date."
        ))
        layout.addSpacing(22)

        layout.addWidget(_section_label("Follow-ups"))
        layout.addSpacing(10)
        self.followup_cb = QCheckBox("Send follow-ups when nobody replies")
        layout.addWidget(self.followup_cb)
        layout.addSpacing(10)
        follow_grid = QGridLayout()
        follow_grid.setHorizontalSpacing(12)
        follow_grid.setVerticalSpacing(10)
        self.followup_gap_spin = _spin(1, 60, 1, " days", 100)
        self.followup_steps_spin = _spin(0, 5, 1, "", 100)
        follow_grid.addWidget(QLabel("Wait between touches"), 0, 0)
        follow_grid.addWidget(self.followup_gap_spin, 0, 1)
        follow_grid.addWidget(QLabel("Follow-ups per lead"), 1, 0)
        follow_grid.addWidget(self.followup_steps_spin, 1, 1)
        follow_grid.setColumnStretch(2, 1)
        layout.addLayout(follow_grid)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "The wait is a floor, not an exact gap: a follow-up is placed no sooner "
            "than this, then queued behind whatever the day's caps and sending "
            "window allow, so on a long list it usually lands later than the number "
            "above. A reply, a bounce or an unsubscribe cancels every remaining "
            "follow-up for that lead."
        ))
        layout.addStretch()
        return page

    # ── Compliance ──

    def _build_compliance_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)

        layout.addWidget(_section_label("Unsubscribe"))
        layout.addSpacing(10)
        self.unsubscribe_edit = QLineEdit()
        self.unsubscribe_edit.setPlaceholderText("unsubscribe@yourdomain.com")
        self.unsubscribe_edit.setFixedHeight(34)
        layout.addWidget(self.unsubscribe_edit)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Blank uses the sending account's own address. Every email carries a "
            "List-Unsubscribe header, and an unsubscribe cancels that lead's "
            "queued follow-ups."
        ))
        layout.addSpacing(26)

        layout.addWidget(_section_label("What every email carries"))
        layout.addSpacing(10)
        layout.addWidget(_hint(
            "All three are on because they are what keeps mail out of the spam "
            "folder. Each one can be switched off, and each one says what that costs."
        ))
        layout.addSpacing(12)
        self.append_unsubscribe_cb = self._guard_toggle(
            layout, "Append the unsubscribe line",
            "Off means a recipient has no way to opt out. That is what gets a "
            "sender reported and filtered, and for commercial mail it is illegal "
            "in most countries.")
        self.append_postal_cb = self._guard_toggle(
            layout, "Append the postal address",
            "Off removes the physical address CAN-SPAM requires in every "
            "commercial email. Filters read a missing one as a spam signal on "
            "its own.")
        self.require_profile_cb = self._guard_toggle(
            layout, "Require a complete sender profile before sending",
            "Off lets a campaign start with your name, your address or a verified "
            "sending account missing, at your own risk.")
        layout.addSpacing(26)

        layout.addWidget(_section_label("Dry run"))
        layout.addSpacing(10)
        self.dry_run_cb = QCheckBox("Dry run — build and log every email, send none")
        self.dry_run_cb.toggled.connect(self._on_dry_run_toggled)
        layout.addWidget(self.dry_run_cb)
        layout.addSpacing(12)
        self.live_warning = QLabel(
            "LIVE SENDING IS ON. Starting a campaign will deliver real email to "
            "real businesses from your Gmail account."
        )
        self.live_warning.setObjectName("warning")
        self.live_warning.setWordWrap(True)
        layout.addWidget(self.live_warning)
        layout.addSpacing(6)
        layout.addWidget(_hint(
            "A dry run walks the whole schedule and renders every message, but "
            "opens no SMTP connection and spends none of the day's quota. The "
            "queue goes back exactly as it was, so the campaign is still ready "
            "to send for real afterwards. Use it once before any new campaign."
        ))
        layout.addStretch()
        return page

    def _guard_toggle(self, layout: QVBoxLayout, label: str, cost: str) -> QCheckBox:
        """One protection, on by default, with the price of turning it off under it.

        The user asked not to be blocked and that is what these are: switches,
        not walls. What they must never be is silent — a footer quietly missing
        its unsubscribe line is a deliverability problem that shows up weeks
        later as a dead domain.
        """
        box = QCheckBox(label)
        box.setChecked(True)
        box.setToolTip(cost)
        box.toggled.connect(lambda _on: self._schedule_template_preview())
        layout.addWidget(box)
        layout.addSpacing(4)
        layout.addWidget(_hint(cost))
        layout.addSpacing(14)
        return box

    def _schedule_template_preview(self) -> None:
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    # ── load ─────────────────────────────────────────────────────────────────

    def _load_into_ui(self) -> None:
        settings = self.settings

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
        while self.accounts_box.count():
            item = self.accounts_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._account_rows = []

        for account in accounts:
            if isinstance(account, dict):
                self._add_account_row(account)
        self._update_accounts_placeholder()

    def _add_account_row(self, account: dict) -> None:
        if self._account_rows:
            self.accounts_box.addWidget(_divider())
        row = _AccountRow(account, len(self._account_rows) + 1)
        row.remove_requested.connect(self._remove_account_row)
        row.verify_requested.connect(self._verify_account_row)
        email = str(account.get("email") or "").strip()
        if email:
            row.set_password(get_secret(self.settings, f"smtp_accounts.{email}.app_password"))
        self.accounts_box.addWidget(row)
        self._account_rows.append(row)
        self._update_accounts_placeholder()

    def _remove_account_row(self, row: _AccountRow) -> None:
        if row not in self._account_rows:
            return
        self._account_rows.remove(row)
        # Renumbering by hand is fiddly with the dividers interleaved; rebuilding
        # from the rows that are left keeps the titles honest.
        remaining = [r.to_dict() for r in self._account_rows]
        passwords = [r.app_password() for r in self._account_rows]
        self._load_accounts(remaining)
        for new_row, password in zip(self._account_rows, passwords):
            new_row.set_password(password)

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

    def _test_provider(self, provider: str, key_field: _SecretField, model_edit: QLineEdit,
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

    def _fetch_models_async(self, provider: str, key_field: _SecretField, model_combo: ModelComboBox) -> None:
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
        # First, because the template store is a separate file and a user who
        # pressed Save with a half-written follow-up open means both.
        self._save_open_template(quiet=True)
        self._collect()
        try:
            save_settings(self.settings)
        except OSError as exc:
            _status(self.save_status, f"Could not save: {exc}", "err")
            return False
        self._refresh_budget()
        _status(self.save_status, "Saved", "ok")
        self.saved_signal.emit(self.settings)
        return True

    def _on_back(self) -> None:
        self._on_save()
        self.back_signal.emit()
