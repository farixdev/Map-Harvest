"""Outreach — the screen that turns a scrape into a mailing and watches it go.

Four steps in the order the work happens: Leads (who), Campaign (what they
receive), Sending (the run), Stats (what came back). They are steps, not
categories, which is why each one ends by pointing at the next — and they are
the shell's tabs now, not this screen's. `ui/app.py` draws one bar at
`control.header`; this file hands it `("Leads", "Campaign", "Sending", "Stats")`
through `set_subtabs` and draws no chrome of its own.

Five rules shape the code below.

Nothing slow runs on the GUI thread. Crawling a site, calling a model and
opening an SMTP session are minutes of network time, so they happen in the
workers in `core.campaign`; this file starts them and draws what they emit.
The same rule reaches the drawing, because a redraw that walks the whole lead
list once per lead is a network wait by another name. Five shapes of that were
measured and all five are closed here.

  * every audited lead used to rebuild the screen's whole idea of the table.
    `_on_lead_audited` called `_apply_filters`, which walked every row, and
    `_refresh_lead_actions` inside it walked the table three more times purely
    to write button labels — so a run of N audits cost O(N²). At 500 leads that
    was 77.1ms a lead, 38.6 seconds of frozen window for one audit pass; at
    5,000 it was 796.4ms a lead and over an hour. An audited lead now touches
    its own row and nothing else: the row it lives on is looked up in a map
    rather than searched for, the counts under the table are carried and
    adjusted rather than recounted, and the labels ask for a number and three
    names rather than for two whole lists.
  * a lead's record used to ride on its own table cell through `Qt.UserRole`.
    Qt has to marshal a dict in and out of a QVariant on every read, which
    measured 50µs a call — so reading the leads a filter pass needs cost more
    than everything else on the screen put together. Row `n` is `self._leads[n]`
    by construction, and that is what `_lead_at` answers with.
  * the table built a cell for every lead in the store. At 5,000 leads that is
    35,000 `QTableWidgetItem`s, one `insertRow` at a time, for the twenty rows
    an 800px window can show — 2,379ms of a 2,648ms pass inside
    `components._Table.add_row`, paid on every reload, on every column-header
    click and at the end of every audit run. The table still holds a row per
    lead, so the scrollbar tells the truth about the size of the list, but only
    the band around the viewport is made of cells; see `_paint_window`.
  * every button label read the selection through `selectedIndexes()`, which is
    one `QModelIndex` per *cell*. A Ctrl+A over 5,000 leads therefore built
    35,000 objects to work out one number, on the keystroke and again for each
    of five labels. The selection's ranges answer the same question.
  * a reload emptied every derived cache. Suppressing one lead out of five
    thousand re-decoded five thousand audit blobs and re-asked `core.templates`
    five thousand times for an answer none of them had changed. Each lead now
    carries a stamp of the fields those caches are derived from, plus one for
    the settings the personalisation call reads, and only what moved is
    forgotten.

  Measured on one 5,000-lead store, before and after, back to back, as the
  median of nine runs in milliseconds:

      _fill_table    677 -> 29      a column-header sort    781 ->  32
      _reload_leads  806 -> 103     the end-of-run reload  1008 -> 104
      Ctrl+A         196 ->   8     one audited lead landing  0.1 -> 0.3

  The same four at 500 leads: 92 -> 13, 91 -> 15, 115 -> 26, 96 -> 27. What is
  left of the reload is not this screen's — 49ms of its 103 is
  `core.outreach_db.list_leads` reading 5,000 rows with their audit blobs.
  Two things cost a little more than they did, and both are the price of
  something asked for: a keystroke in the filter box went 15ms -> 21ms because
  the box parses terms now instead of matching one substring, and a scroll from
  the top of the list to the bottom went 14ms -> 25ms because rows are built as
  they arrive rather than all of them beforehand.

Nothing on screen is rebuilt for a screen nobody is looking at. Changing the
theme or the density asks every built screen to `restyle()`, and this one used
to spend 913ms rebuilding four tab pages and re-reading the store for a window
the user had walked away from — inside the click that changed the setting. A
hidden screen records that it is stale and rebuilds when it is next shown.

Nothing is previewed that could not be sent. The preview goes through
`core.templates.render` — the same call the send loop makes — and refuses to
draw anything still carrying a `{{token}}`.

Nothing here says no without saying how through, and nothing says yes about a
queue that is not moving. A disabled control carries the sentence that would
re-enable it, and the Sending tab reports what is actually happening rather
than what was true when the campaign was prepared.

Nothing the user arranged is thrown away when the window closes. The leads tab
is a working surface and the work is the arrangement: which columns are worth
looking at, which filter narrows five thousand rows to the forty worth calling,
which order they are in. All of it lived for exactly one session. It is kept in
`lead_views.json` beside `settings.json` — not inside it, because
`core.settings._merge` keeps only the keys named in `DEFAULT_SETTINGS` and
drops the rest on the next save — and a named arrangement is a *view*, which
the user can leave and come back to. Changing anything a view did not say takes
the name off rather than rewriting it underneath them.

Every value this screen paints comes from `ui/theme.py` through
`ui/components.py`. It used to keep a parallel dark-only palette of its own —
eight constants, `_GREEN` among them at the exact value the contract ordered
darkened — and paint with `setForeground`, so the light theme rendered the
Score and Headline-gap columns at 1.02-1.90:1. That palette is gone. What the
audit measured, and what replaced each of it:

  * the lead table gave every surplus pixel to Status. At 2560 Status reached
    1800px for the word "audited" while Headline gap — the column the whole
    audit exists to produce — stayed frozen at 240px with 16 of 20 cells cut
    and 11 of those with no tooltip; at 1280 Status took 510px for a 59px word
    while business names clipped at 200px and 37% of the table was empty. It is
    `components.table()` from a column spec now: Status and Score are `fit` and
    capped in characters, Headline gap is the heaviest `stretch` in the spec,
    and every cut cell answers a hover with the whole of its text.
  * status and score were carried by colour alone, with `bounced`, `failed` and
    `suppressed` at an identical 1.00:1. They are `components.status_pill()` and
    `components.score_badge()`, painted through `_BadgeDelegate` — see its
    docstring for why a delegate and not `setCellWidget`, and for the two
    numbers that decided it.
  * suppressing a lead was permanent, unconfirmed, and silently acted on one
    row of a multi-row selection. It goes through `components.confirm()`, it
    acts on the whole selection, and the toast that reports it carries Undo.
  * the seven columns' floors add up to 1,380px, so at the 1,080px default
    window the lead table scrolled sideways and at the 880px minimum 500px of
    it was off the right-hand edge — with no way for the user to say which
    columns they cared about. Switching three off puts the table back inside
    the viewport exactly; that is what the Columns menu is for, and it is why
    the spec is handed to `components.table()` already trimmed rather than
    hidden in place afterwards.
  * the filter box could not be told where to look, and could not be given two
    words. `toronto roofing` matched nothing at all — the record says "Toronto"
    in the city and "Roofing contractor" in the category, and one substring
    cannot ask for both. Every word is its own needle now, `city:toronto` looks
    in one field, and a quoted phrase stays whole.
  * a batch of five hundred could be crawled in one press and then had to be
    dealt with one right-click at a time. Audit, Suppress, Export and Remove
    all read the same selection and all say how many rows they are about to
    touch.
  * the dry-run banner was a full-width 44px QPushButton wearing a dashed
    border — the same component as the 28px header badge, at a different size.
    The shell owns that state now; this screen says what the mode costs in a
    sentence and names the campaign it applies to.

The last pass gave this screen the platform dialect the rest of the app now
speaks. Every part of it is a component that already existed rather than a
second copy of one, and each closes something measured here:

  * the four steps are rows in the shell's sidebar, so nothing on the page said
    which of them was in front of you — a screen that opened on Stats and a
    screen that opened on Leads differed by their contents alone. Each page
    starts with `components.page_header()` now: the step's name and one muted
    line saying what it is for. The two uppercase rules those replace — "Leads"
    over the toolbar and "Results" over the tiles — are gone, and so is the
    second row of the leads toolbar: the count line moved into the header,
    which is what left room for Views, Columns and Import beside the filter
    box.
  * the activity log was a bare QListWidget of proportional 13px lines tinted
    three ways through `setForeground`, with the timestamp in the same ink as
    the message it stamps. It is `components.log_console()` now — monospace, a
    drawn marker per level in the semantic families, a copy action and a clear
    action — with `_LogLineDelegate` holding the stamps in `text.tertiary` so
    the eye runs down the messages and not down the clock, and a line arriving
    while the reader has scrolled away no longer yanks them back to the top.
  * the stat tiles were six boxes sharing one row — a seventh arrives after a
    rehearsal — and no two of them agreed: measured at 1080px they came out 81,
    160, 146, 216, 161 and 216px wide, each wrapping the same length of
    sentence differently. They are one width on a four-column grid that
    re-flows when a tile has nothing to say.
  * every control that does something carries the drawing for it from
    `ui/icons.py` — the leads toolbar, the bulk row, the send controls and the
    row menu — and not one of them holds a pixmap or a colour of its own.

This pass is about the one thing left over from all of that: the screen knew
things it would not say. Two complaints, one cause.

"Which site is not reachable?" had no answer on the screen that was hiding it.
Four leads whose sites had timed out, answered 403, had no DNS record and
carried a dead certificate all painted the same cell — "form letter — the site
could not be reached" — and the one place the real line surfaced was the
campaign preview, one tab and two clicks away, as `URLError: <urlopen error
[Errno 11001] getaddrinfo failed>`. So:

  * `_site_failure` reads `unreachable_reason` and `unreachable_detail` off the
    lead's `audit_json`. Those are the crawl's own keys — `core.audit` fills
    them, `core.enrich` writes the words they are derived from — and reading
    them rather than the `error` line is what keeps this screen from being a
    third dialect for one fact. It also catches what no error string could: a
    page that answered 200 and still told the crawl nothing, a bot check, a
    parked domain, a cookie wall, a shell that renders nothing without
    JavaScript. All of those are `reachable: False` with pages in the blob and
    no error at all. A blob from before those keys existed goes back through
    `core.audit.unreachable_reason`, the same function that filled them, so an
    old store reads exactly like a new one; a blob that says nothing about
    `reachable` and carries no error is not read as a failure at all, because
    silence is not evidence.
  * the one thing kept here is the register a column needs. The crawl's detail
    is a clause — "the domain name does not resolve" — which is what the
    tooltip, the preview and the Headline gap print; `_SITE_WORDS` is four
    words per code for the cell that gets scanned down forty rows. A code this
    build has never heard of is spelled out rather than flattened back into
    "unreachable".
  * `Site` is a column. Blank for every site that was read, which is what makes
    it scannable — the only rows carrying a word are the ones nothing was
    learned from. It sorts, it exports with the raw line beside the words, and
    the filter box reads both, so `timed out` finds every lead the crawl gave
    up on that way.
  * the "Failed Audits" filter is "Unreachable sites", which is what it always
    selected. Its key stays `~failed`, because that key is written into every
    saved view on disk and renaming it would drop a user's view back to All
    leads on the first run after the upgrade. It reads the same cached answer
    the column paints rather than decoding the blob once per row per keystroke.
  * a column added after a view was written is not one the user switched off —
    they were never offered it — so `lead_views.json` now records the keys it
    was written by and `_fields_wanted` turns anything newer on. Without it,
    adding Site would have hidden it from everyone who has ever opened this tab.
  * the row menu retries the crawl on exactly the rows under the pointer, and
    every crawl now reports what it changed. "Audit finished" was true of every
    run and said nothing about any of them; a retry that comes back with the
    same four failures leaves the table looking identical, which is the one
    outcome that must never read as success.
  * the Campaign card counts the form letters *before* Prepare is pressed, and
    the review dialog says which of four reasons produced them instead of
    printing "Generic Copies: 31".

The Sending tab could look dead in states it knew about. Eighteen were driven
one at a time and read off the screen; three were lying and three said nothing:

  * a run with no Gmail account configured at all reported "Every account has
    hit today's cap" — a sentence about a cap on an empty set.
  * an account the run had benched for the day after an AUTH or QUOTA failure
    was counted as one with room, so the screen read "Sending — 0 of 3" over a
    queue with nothing left to send from. `_benched_today` reads the
    `account_stopped` events the worker writes, which is the store's own record
    of the same fact and outlives the run.
  * a campaign the run had stopped for that reason read "Not sending — press
    Start sending", which starts a run that stops again immediately.
  * "Stopping — finishing the message in flight" lived for under a second:
    `_on_tick` repaints the line every second and put "Sending" back, and
    re-enabled the Stop button it had just disabled.
  * a run waiting out the pacing gap said only "Sending", with no clock on it,
    for as long as the gap lasted.
  * a queue scheduled past the year `_next_due_ts` looks over was reported as
    "every queued message is addressed to a suppressed address" — the one cause
    the branch could think of, asserted without counting a single one. It is
    counted now, and there is a third answer for when it is neither.
  * Send now was the one control on the tab nothing ever disabled. It sat lit
    over a campaign that did not exist, and the shell's context line — visible
    from every screen — counted `sent`, which a rehearsal never writes.

The cost of asking those questions, measured on this machine as the median of
25 calls with a 2,000-row event log: one second's `_refresh_send_controls` went
0.09ms -> 0.37ms, and the event-log read behind it is memoised for two seconds
because three things on the line want the same answer (1.81ms a read, three
reads a tick without it). The leads tab pays nothing: the form-letter tally is
counted on the way into the Campaign tab, where it is read, rather than on
every selection change on the Leads tab, where it is not. Counted the other way
it cost +42ms on a Ctrl+A over 5,000 rows and +8ms on every keystroke in the
filter box; counted this way, at 5,000 leads, `_apply_filters`, `_fill_table`,
a keystroke and a Ctrl+A all measure inside the noise of the same screen
without any of it, and one visit to the Campaign tab costs 12ms.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import time
import webbrowser
from datetime import datetime

from PyQt5.QtCore import (
    QEvent, QPoint, QRect, QSize, Qt, QThread, QTimer, pyqtSignal,
)
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPalette, QRegion
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar,
    QScrollArea, QSizePolicy, QStackedWidget, QStyle, QStyleOptionViewItem,
    QStyledItemDelegate, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from core import audit as _audit
from core import campaign as _campaign
from core import mailer as _mailer
from core import outreach_db as _db
from core import settings as _settings
from core import templates as _templates
from core import whatsapp as _wa
from core.ai import AIClient
from core.campaign import (AuditWorker, OutreachWorker, account_daily_cap,
                           plan_campaign, release_now)

# The WhatsApp copy, if this build carries it. Guarded for the reason
# `core.campaign.copy_for` is guarded: a build without it must still plan and
# send email rather than fail to import the screen that does both.
try:
    from core import wa_templates as _wa_templates
except Exception:                                # noqa: BLE001 — an absent channel
    _wa_templates = None
from ui import components
from ui import icons as _icons
from ui import theme as _theme
from ui.components import Cell, Column

# ── The email document ───────────────────────────────────────────────────────
# An email is not chrome. It is rendered by the recipient's mail client, which
# is not wearing this app's theme and never will be, so the preview is drawn in
# the light palette whatever the app is in — that is where a near-white page
# and near-black ink are role tokens rather than two hexes chosen by hand.
# `text.primary` on `raised` measures 17.16:1, which is what an inbox looks
# like; painting the document in the dark palette would show the user a message
# their reader will never see.

_PAPER = _theme.theme("light")

# The recipient's font stack, deliberately not a theme token: it names what
# their client will substitute, not what this app wants to be seen in.
_MAIL_FAMILY = ("-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, "
                "sans-serif")

# How wide the paper is allowed to get, in characters. The audit measured this
# preview as the widest object on the screen at 2154px on a 2560px window,
# against the roughly 600px a real inbox renders a message in — a line nobody
# can track back from, and three times what it is being previewed for. A count
# rather than a pixel width for the reason every cap in `components` is a
# count: the thing being sized is text.
_PAPER_CH = 76

# ── The lead table ───────────────────────────────────────────────────────────
# What each column *is*, rather than how many pixels it was handed. `fit` holds
# a short fixed value and is sized to its own content and then capped; `stretch`
# carries meaning and shares what is left over by weight. Both bounds are in
# characters.
#
# The weights are the screen's own priorities and they are the whole of the
# first finding: Headline gap is the heaviest column in the spec because it is
# the one thing the audit pass produces that nobody can get anywhere else, and
# Status is `fit` with a 16-character cap because "suppressed" is eleven
# characters and no window makes it longer.
#
# City and Category are here because they could not be searched at all: the
# filter box reads the lead record, and until this pass the record's city and
# category were not on screen for it to read.

_LEAD_COLUMNS = (
    Column("Business", "stretch", weight=3, min_ch=14, max_ch=36),
    Column("Email", "stretch", weight=3, min_ch=16, max_ch=52),
    # Beside the email because they are the same fact — how this lead can be
    # reached — and because a WhatsApp campaign cannot include a lead that has
    # no number at all. `fit` and capped: a phone number is a short fixed value,
    # and the longest E.164 there is fits inside the sample.
    Column("Phone", "fit", min_ch=12, max_ch=20, sample="+1 416-555-0142"),
    Column("City", "fit", min_ch=8, max_ch=20, sample="Scarborough"),
    Column("Category", "fit", min_ch=8, max_ch=22, sample="Roofing contractor"),
    # Blank for every site the crawl read, which is what makes it scannable:
    # the only rows carrying a word here are the ones nothing could be learned
    # about. See `_SITE_WORDS` for why it is four words and not the raw line.
    Column("Site", "fit", min_ch=10, max_ch=22, sample="rate-limited (429)"),
    Column("Score", "fit", min_ch=13, max_ch=16, sample="88 · moderate"),
    Column("Headline gap", "stretch", weight=5, min_ch=16, max_ch=80),
    Column("Status", "fit", min_ch=12, max_ch=16, sample="⊘ suppressed"),
)

(_COL_NAME, _COL_EMAIL, _COL_PHONE, _COL_CITY, _COL_CATEGORY, _COL_SITE,
 _COL_SCORE, _COL_GAP, _COL_STATUS) = range(len(_LEAD_COLUMNS))

# The eight names above are *fields*, not table columns, and that is the whole
# of the column-visibility change. A field is a thing a lead has; a column is a
# place on screen where one of them happens to be painted today. `_col_of` on
# the screen maps the first onto the second, and a hidden field simply has no
# entry — so the table `components.table()` is handed is a spec of exactly the
# columns the user asked for, and `_take_widths` shares the window between
# those and not between those plus four it is not painting.
#
# Business is not in the toggle list. A row with no business name on it cannot
# be told apart from the row above it, and a table whose every column can be
# turned off has a state in which it says nothing at all.
_FIXED_FIELDS = (_COL_NAME,)

# What each field is called in the file that remembers the choice. Names and
# not indices: a column added to the spec above must not silently renumber a
# saved view written last month.
_FIELD_KEYS = ("name", "email", "phone", "city", "category", "site", "score",
               "gap", "status")
_FIELD_OF_KEY = {key: index for index, key in enumerate(_FIELD_KEYS)}

# The keys a stored column list could name before `site` existed. An entry that
# does not say which keys it was written by was written by that build, and a
# field it never names is one the user was never offered — so it arrives
# switched on rather than hidden. Without this, adding a column would leave it
# off for every user who has ever opened the Leads tab, which is all of them:
# `lead_views.json` stores the columns that are *shown*, so a file listing the
# seven names below and no eighth is indistinguishable from a user who turned
# the eighth off.
_KNOWN_BEFORE = ("name", "email", "city", "category", "score", "gap", "status")

# Which lead field each column sorts and searches on. The two badge columns
# sort on the value behind the badge — an em dash compares greater than any
# digit as text, so a Score column sorted as written floats every unaudited
# lead above the best prospect.
_COL_KEYS = {_COL_NAME: "name", _COL_EMAIL: "email", _COL_PHONE: "phone",
             _COL_CITY: "city", _COL_CATEGORY: "category",
             _COL_STATUS: "status"}

# What the filter box will accept in front of a colon, and the lead field it
# then reads. The plain-English words are here because "business:" is what a
# user types and "name" is what the column is called in the database.
_SEARCH_FIELDS = {
    "name": "name", "business": "name", "company": "name",
    "email": "email", "e-mail": "email", "address": "email",
    "city": "city", "town": "city", "area": "city",
    "category": "category", "type": "category", "industry": "category",
    "phone": "phone", "tel": "phone",
    "website": "website", "site": "website", "url": "website",
    "status": "status", "source": "source",
}
_SEARCH_TERMS = re.compile(
    r'(?:(?P<field>[a-z][a-z-]*):)?(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"]+))')

_SEARCH_HELP = (
    "Every word has to land somewhere in the lead, so «toronto roofing» finds "
    "the roofers in Toronto. Put a field in front of the colon to look in one "
    "place — city:toronto, category:roofing, status:sent, email:gmail — and "
    "quote a phrase to keep it whole. Name, email, city, category, phone, "
    "website, source, headline gap, score band, status and why a site could "
    "not be read are all read — «timed out» finds every lead the crawl gave "
    "up on for that reason.")

# The badge a cell paints, and the value it sorts on. `+ 1` and `+ 2` belong to
# `components` (the untruncated text and the sort key), so this starts at `+ 3`.
_BADGE_ROLE = Qt.UserRole + 3

# `components.Column.align` said in Qt's own words. The row writer below builds
# its own items, so it is the one thing it cannot ask `components` for; see
# `_paint_row` for why it builds them and where the whole of it belongs.
_ALIGNMENT = {"left": Qt.AlignLeft, "right": Qt.AlignRight,
              "center": Qt.AlignHCenter}

# How many rows past the top and bottom of the viewport carry real cells. The
# table holds a row per lead so the scrollbar tells the truth about the list,
# but only this band is built out of `QTableWidgetItem`s: a flick of the wheel
# moves about a screenful, and the pad is what keeps the next screenful ready
# before it is asked for. Rows further out than `_WINDOW_KEEP` give their cells
# back, so a scroll from end to end of five thousand leads holds a few dozen
# rows of widgets rather than accumulating all five thousand.
_WINDOW_PAD = 24
_WINDOW_KEEP = 120

# Keys are lead statuses, except the three prefixed "~": those filter on
# something the crawl found rather than on where the lead is in the campaign,
# which is not a status and is the only way to send to the personalised half of
# a list and leave the rest.
#
# `~failed` is the key and "Unreachable sites" is the label, and the two
# deliberately disagree. What it selects has always been "the crawl could not
# read this site" — the word "audit" was never the failure, the site was — but
# the key is written into `lead_views.json` by every saved view, and renaming
# it would silently drop a user's saved view back to All leads on the first
# run after the upgrade.
_STATUS_FILTERS = (
    ("All leads", ""), ("Not audited", "new"), ("Audited", "audited"),
    ("Unreachable sites", "~failed"), ("Personalised", "~personal"),
    ("Generic email", "~generic"),
    # Not "has a phone number": a number is only usable if `to_wa_id` can turn
    # it into one, which for a Maps number scraped without a `+` depends on
    # whether a default region has been set. The filter therefore moves when
    # that setting does, which is the honest behaviour — the leads it hides are
    # exactly the ones a WhatsApp campaign would leave out.
    ("Has a usable number", "~phone"),
    ("Queued", "queued"), ("Sent", "sent"),
    ("Replied", "replied"), ("Bounced", "bounced"), ("Suppressed", "suppressed"),
)
_STATUS_KEYS = [key for _label, key in _STATUS_FILTERS]

# ── What the leads tab remembers ─────────────────────────────────────────────
# The filter box, the status picker, the sort and the chosen columns are the
# user's working set, not the app's configuration, and they are kept in a file
# of their own beside `settings.json` rather than inside it. Two reasons, and
# the first is a bug this would otherwise walk into: `core.settings._merge`
# keeps only the keys named in `DEFAULT_SETTINGS` and drops everything else on
# the next save, so a saved view written from here would disappear the first
# time the user pressed Save on the Settings screen. The second is that a
# named view is worth more than the session it was made in — the brief's whole
# point — and a value that has to survive a restart has to be on disk.
#
# The path is resolved on every call rather than captured at import, for the
# same reason `core.outreach_db._default_path` is: the test suite repoints
# `settings.SETTINGS_DIR`, and a module constant would have been read before it
# could.

_VIEWS_FILENAME = "lead_views.json"

# Past this it is a list nobody reads, and the picker stops being a shortcut.
_MAX_VIEWS = 24
_VIEW_NAME_CH = 40

# How long the file waits after a keystroke before it is written. A decision —
# saving a view, deleting one, choosing a column — goes to disk in the same
# call, because the user is entitled to see it stick. What is thrown away here
# is a write-then-rename per character typed in the filter box, measured at
# 3.6ms of GUI-thread disk on this machine and worse on a slower one.
_SAVE_AFTER_MS = 500

# Columns a CSV may name in any of the ways people actually name them.
_CSV_ALIASES = {
    "email": "email", "e_mail": "email", "email_address": "email", "emails": "email",
    "name": "name", "business": "name", "business_name": "name", "company": "name",
    "company_name": "name", "title": "name",
    "website": "website", "url": "website", "site": "website", "web": "website",
    "domain": "website",
    "phone": "phone", "phone_number": "phone", "telephone": "phone", "tel": "phone",
    "city": "city", "area": "city", "town": "city", "location": "city",
    "category": "category", "type": "category", "industry": "category",
    "rating": "rating", "stars": "rating",
    "maps_link": "maps_link", "map_link": "maps_link", "google_maps": "maps_link",
}

# A CSV is a hand-made file; past this it is a mistake, not a lead list.
_CSV_MAX_ROWS = 20000

# How many lead ids one DELETE names. Well under any SQLite's bound-variable
# limit, so a selection of any size is a loop rather than a refusal.
_DELETE_BATCH = 500

# Anything that still looks like a merge token after rendering. The preview
# refuses to draw a body matching this rather than showing the user copy that
# would embarrass them in a stranger's inbox.
_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")

_LOG_LIMIT = 400

# How far back the "which accounts are out of action today" question reads the
# event log. Every send writes an event, so a busy day is thousands of rows and
# the answer is only ever in the last few hundred: the scan stops at the first
# row older than local midnight, and this is the ceiling on how long it can run
# before it gets there.
_BENCH_SCAN = 400

# How long that answer is reused for. The status line is repainted once a
# second and three of the things on it want the same set, so without this the
# tick reads the event log three times: measured at 1.81ms a read over a
# 2,000-row log, which is 5.4ms of GUI thread every second for a fact that
# changes when an SMTP server refuses a password. Two seconds is inside the
# tick and far inside a human's idea of "at once".
_BENCH_TTL = 2.0

_DAY_SEC = 86400.0
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# How many leads the preview picker and the "who is about to be queued"
# sentence name before they stop counting. Counts, not sizes.
_PREVIEW_CAP = 500
_NAMED_IN_SUMMARY = 3

# How many characters the campaign column and the suppression list are allowed
# to run to. The column has to hold the widest primary button on the screen and
# nothing more; the list holds an address and a reason and nothing more.
_COLUMN_CH = 52
_SUPPRESSION_CH = 64

# `ai_provider`, said the way the Settings screen offers it. Every campaign
# setting the user is likely to want back is edited there; this screen's job is
# to name the ones that shape what it is about to do.
_AI_PROVIDERS = {
    "auto": "AI: Groq, then OpenRouter",
    "groq": "AI: Groq",
    "openrouter": "AI: OpenRouter",
    "off": "AI off — plain templates",
}

# ── What each of the four steps is for ───────────────────────────────────────
# The steps are rows in the shell's sidebar, so a page that does not name
# itself is a page whose only clue to which step it is is what happens to be on
# it — and the Leads and Stats tabs both open on a table of the same leads.
# One line each, in the tier the page header reads it in: what the step does,
# and what it hands to the next one.

_SECTION_NOTES = {
    "Leads": "Everyone there is an address for, what the crawl found on their "
             "site, and who is worth writing to.",
    "Campaign": "The template, who it is signed by, when it goes — and the "
                "exact message one of these leads would receive.",
    "Sending": "The queue as it goes out: which account, which message, and "
               "what is holding it up when nothing is moving.",
    "Stats": "What the campaign has produced so far, and every message it has "
             "already put in a stranger's inbox.",
}

# ── The dashboard ────────────────────────────────────────────────────────────
# A tile is as wide as its own caption and the sentence under it, and every one
# of them is the same width: seven tiles sharing a row came out 81, 160, 146,
# 216, 161 and 216px at 1080, which is six different boxes for six numbers that
# are read as one set. A character measure and a count, because what decides
# both is text.

_TILE_CH = 22
_TILES_PER_ROW = 4

# Which drawn mark each control carries. Named here rather than at the call
# site so the whole toolbar can be read as a set — two controls that do the
# same kind of thing to a selection take the same glyph, and Start takes a
# different one in each of the two modes precisely because it is not the same
# button in both.
_START_ICONS = {"primary": "play", "danger_primary": "send"}

# ── The two channels ─────────────────────────────────────────────────────────
# A channel is a choice on the Campaign tab and not a second screen. The same
# leads, the same crawl, the same gap-to-service pitch and the same Sending tab
# — what differs is the transport, the register the copy is written in, and
# every limit, and all three of those are answered by `core.campaign` rather
# than by a branch here.

EMAIL = _campaign.EMAIL
WHATSAPP = _campaign.WHATSAPP

_CHANNELS = ((EMAIL, "Email"), (WHATSAPP, "WhatsApp"))
_CHANNEL_INDEX = {key: index for index, (key, _label) in enumerate(_CHANNELS)}
_CHANNEL_LABEL = dict(_CHANNELS)

# What one message on each channel is called, in a sentence about a queue. The
# Sending tab counts and holds and refuses on both, and "12 emails queued" over
# a WhatsApp run is the sort of small untruth that makes a user distrust the
# whole panel.
_CHANNEL_NOUN = {EMAIL: "email", WHATSAPP: "message"}

# And what the thing sending them is called. Gmail has as many accounts as the
# user has added; WhatsApp has exactly one number, so "every account is at its
# cap" is a sentence about a set of one and reads as a bug.
_CHANNEL_SENDER = {EMAIL: "account", WHATSAPP: "number"}

# How wide the chat bubble is allowed to get, in characters. Narrower than the
# email paper's 76 because that is the difference being previewed: a WhatsApp
# message is read in a column about forty characters wide on a phone held in one
# hand, and sixty words laid out at inbox width does not look like the wall it
# will actually be.
_BUBBLE_CH = 40


# ── Small helpers ────────────────────────────────────────────────────────────

def _text_of(value) -> str:
    return "" if value is None else str(value)


def _int_of(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_of(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _paper_html(body: str, size: int, ink: str, *, monospace: bool = False) -> str:
    """Wrap a document in the recipient's font stack, at the light palette's ink.

    Module level rather than a method because two surfaces draw on this paper
    now — the preview of what a lead *would* get, and the record of what one
    already did — and a second copy of the page style is how the two come to
    disagree about what an email looks like.
    """
    return ('<div style="font-family:%s;font-size:%dpx;line-height:1.6;'
            'color:%s;white-space:%s;">%s</div>'
            % ("monospace" if monospace else _MAIL_FAMILY, size, ink,
               "pre-wrap" if monospace else "normal", body))


def _paint_paper(browser, ground: str = "raised") -> None:
    """Paint the document itself, in the palette a reader will see it in.

    The QSS rule for `#email_paper` styles the well the document sits in; this
    is the page inside it. Both come from the theme — the light one,
    deliberately: the body carries near-black ink, and on the dark app's own
    surface it would be invisible.

    `ground` is which of the light palette's surfaces the page is. An email is
    `raised`, because a mail client draws a message on white paper. A WhatsApp
    thread is `inset`: the bubble is the paper there, and a bubble drawn on the
    same value as the page behind it stops being a bubble.
    """
    frame = browser.document().rootFrame()
    fmt = frame.frameFormat()
    fmt.setBackground(QColor(_PAPER.color[ground]))
    fmt.setMargin(_PAPER.space["5"])
    frame.setFrameFormat(fmt)


def _bubble_html(lines: list, stamp: str = "") -> str:
    """One WhatsApp message drawn as the chat bubble it will arrive in.

    Not the email paper with the subject taken off, and the difference is the
    whole reason this exists. A message on this channel is read in a chat
    thread, on a phone, from a number the reader does not recognise, in a column
    about forty characters wide — and the one question the preview has to answer
    is whether sixty words reads as a note or as a wall. Laid out at inbox width
    it answers a question nobody asked.

    In the light palette for the reason the email preview is in it: this is the
    recipient's phone and not this application's chrome.

    Built as a table because Qt's rich text has no `border-radius`. A padded
    cell with a fill is as close to a rounded corner as a QTextBrowser gets, and
    the thing that has to read as a bubble is the block of tinted ground round
    the words rather than the shape of its corners.
    """
    body = "<br>".join(lines)
    tail = ("<div style=\"color:%s;font-size:%dpx;text-align:right;"
            "margin-top:%dpx;\">%s</div>"
            % (_PAPER.color["text.tertiary"], _PAPER.font["small"][0],
               _PAPER.space["1"], html.escape(stamp))) if stamp else ""
    return ('<div style="font-family:%s;">'
            '<table width="100%%" cellspacing="0" cellpadding="%d" '
            'style="background-color:%s;">'
            '<tr><td style="color:%s;font-size:%dpx;line-height:1.5;">%s%s</td>'
            '</tr></table></div>'
            % (_MAIL_FAMILY, _PAPER.space["3"],
               _PAPER.color["accent.subtle"], _PAPER.color["text.primary"],
               _PAPER.font["body"][0], body, tail))


def _loads(blob) -> dict:
    """A stored JSON blob as a dict. Anything unreadable reads as empty."""
    if isinstance(blob, dict):
        return blob
    try:
        value = json.loads(blob or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _clip(text: str, limit: int) -> str:
    text = _text_of(text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _measure(fm: QFontMetrics, chars: int) -> int:
    """The width `chars` characters of this app's own copy take in `fm`.

    The same measurement `components` takes for every capped label and every
    capped column, so a width computed here and a width computed there mean the
    same thing — and taken by calling it rather than by copying it, because a
    second implementation of "how wide is a character" is how the caps drift.
    It reaches a private name only because `components` has not published one;
    see the handover note.
    """
    return components._measure(fm, chars)


def _clock(ts: float) -> str:
    """"Mon 9:14 AM" — the format the plan summary and the log both use."""
    try:
        stamp = datetime.fromtimestamp(float(ts)).strftime("%a %I:%M %p")
    except (OSError, OverflowError, TypeError, ValueError):
        return "—"
    return stamp.replace(" 0", " ")


def _countdown(seconds: float) -> str:
    seconds = int(max(0.0, float(seconds)))
    if seconds < 1:
        return "now"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def _plural(count: int, word: str) -> str:
    """"3 leads", "1 lead", "0 addresses".

    A bare "s" is wrong on every sibilant ending, and this screen counts
    addresses. Nothing here is irregular, so the sibilant rule is the whole of
    the English needed.
    """
    if count == 1:
        return "1 %s" % word
    suffix = "es" if word.endswith(("s", "x", "z", "ch", "sh")) else "s"
    return "%d %s%s" % (count, word, suffix)


def _generic_sentence(counts) -> str:
    """Why a campaign's form letters are form letters, biggest cause first.

    One cause is named on its own. Several are counted out, because "those sites
    were unreachable" said of a group where a third of them answered perfectly
    well is the same small untruth this counter exists to stop.
    """
    ranked = sorted(((_text_of(reason), _int_of(count)) for reason, count
                     in (counts or {}).items()), key=lambda item: (-item[1], item[0]))
    named = [(reason, count) for reason, count in ranked
             if _templates.generic_reason(reason, plural=True)]
    if not named:
        return "the crawl found nothing to say about them"
    if len(named) == 1:
        return _templates.generic_reason(named[0][0], plural=True)
    return ", ".join("%d because %s" % (count, _templates.generic_reason(reason, plural=True))
                     for reason, count in named[:_NAMED_IN_SUMMARY])


def _reason_list(counts, limit: int = _NAMED_IN_SUMMARY) -> str:
    """"9 already contacted, 2 suppressed" — the plan's own words, biggest first.

    `core.campaign` counts every skip against a reason precisely so this line
    can name it. The old sentence recited the three usual causes whether they
    applied or not, which made a record the renderer choked on indistinguishable
    from an address somebody unsubscribed.
    """
    if not isinstance(counts, dict) or not counts:
        return "already contacted, suppressed, or no usable address"
    ranked = sorted(counts.items(), key=lambda item: (-_int_of(item[1]), _text_of(item[0])))
    named = ["%d %s" % (_int_of(count), _text_of(reason)) for reason, count in ranked[:limit]]
    if len(ranked) > limit:
        named.append("%d for other reasons" % sum(_int_of(c) for _r, c in ranked[limit:]))
    return ", ".join(named)


# ── Why a site could not be read ─────────────────────────────────────────────
# `core.audit` answers this now and this screen does not second-guess it. The
# crawl writes two keys on the lead beside `reachable`: `unreachable_reason`,
# one of the codes in `core.audit.UNREACHABLE_REASONS`, and
# `unreachable_detail`, the same code as the sentence a person reads. Both are
# "" exactly when the site was read.
#
# Reading the code rather than the raw `error` string matters twice over. A
# page that answered 200 and still told the crawl nothing — a bot check, a
# parked domain, a cookie wall, a shell that renders nothing without
# JavaScript — is `reachable: False` with pages in the blob and no `error` at
# all, so anything keyed on the error line would call it readable and let the
# email claim seven things are missing from a site nobody has seen. And the
# vocabulary is shared: `core.enrich._short_error` writes the words that
# `core.audit.unreachable_reason` reads, so a third table here would be a
# third dialect for one fact.
#
# What is *not* in `core.audit` is the register a column needs. The detail is a
# clause — "the domain name does not resolve" — which is what a tooltip and a
# preview want and what a cell scanned down forty rows cannot be. So the one
# thing kept here is four words per code, and an unknown code is printed rather
# than guessed at, because a new code from the crawl is more use spelled out
# than flattened into "unreachable".

_SITE_WORDS = {
    "no_url": "no website", "dns": "no DNS record", "refused": "refused",
    "timeout": "timed out", "tls": "bad certificate", "reset": "dropped",
    "redirect_loop": "redirect loop", "http_401": "login wall (401)",
    "http_403": "blocked (403)", "http_404": "dead page (404)",
    "http_410": "gone (410)", "http_429": "rate-limited (429)",
    "http_500": "server error", "http_503": "unavailable (503)",
    "http_error": "server error", "not_html": "not a web page",
    "empty": "empty reply", "parked": "parked domain",
    "under_construction": "coming soon", "challenge": "bot check",
    "cookie_wall": "cookie wall", "js_only": "needs JavaScript",
    "unreachable": "unreachable",
}


def _site_failure(audit) -> tuple:
    """(four words, the crawl's sentence, the line it recorded) for one audit.

    All three blank for a site that was read, and for a lead nobody has crawled
    yet — a site nothing has been tried on has not failed, and saying it has is
    the same guess-dressed-as-a-fact `_generic_reason` refuses to make.

    A blob written before the crawl carried the two keys is put through
    `core.audit.unreachable_reason`, which is the same function that filled
    them, so a store crawled last month reads exactly as one crawled today
    rather than going blank or being classified by a second opinion.
    """
    if not isinstance(audit, dict) or not audit:
        return "", "", ""
    raw = _text_of(audit.get("error")).strip()
    if "unreachable_reason" in audit:
        reason = _text_of(audit.get("unreachable_reason")).strip()
    elif audit.get("reachable"):
        return "", "", raw
    else:
        # No key at all. A hand-written blob, or one from a build before the
        # crawl recorded the reason. Its silence about `reachable` is not
        # evidence of a failure; only a recorded error is.
        if audit.get("reachable") is None and not raw:
            return "", "", raw
        reason = _audit.unreachable_reason(raw, _int_of(audit.get("status")))
    if not reason:
        return "", "", raw
    detail = _audit.unreachable_detail(reason) or "the site could not be read"
    return _SITE_WORDS.get(reason, reason.replace("_", " ")), detail, raw


def _site_tally(counts, limit: int = _NAMED_IN_SUMMARY) -> str:
    """"2 timed out, 1 blocked (403)" — a run's failures, biggest cause first."""
    ranked = sorted(((_text_of(words), _int_of(count))
                     for words, count in (counts or {}).items() if words),
                    key=lambda item: (-item[1], item[0]))
    if not ranked:
        return ""
    named = ["%d %s" % (count, words) for words, count in ranked[:limit]]
    if len(ranked) > limit:
        named.append("%d for other reasons" % sum(c for _w, c in ranked[limit:]))
    return ", ".join(named)


def _norm_key(key: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _text_of(key).strip().lower())).strip("_")


def _named(count: int, head, limit: int = _NAMED_IN_SUMMARY) -> str:
    """"Alpha Plumbing, Zeta Roofing and 41 more" — who a run is about to touch.

    Prepare campaign never said how many leads it was queueing or who they
    were, and it acts on the selection when there is one and on everything the
    filters show when there is not — so the two readings of "prepare" differed
    by hundreds of strangers and the button looked identical either way.

    A count and the first few, rather than the list, because the sentence only
    ever names three of them: the callers on the audit path have the count in
    hand already and materialising five thousand records to print "and 4,997
    more" is the whole of the second finding, one level down.
    """
    named = [_text_of(lead.get("name")).strip()
             or _text_of(lead.get("email")).strip() or "an unnamed lead"
             for lead in list(head)[:limit]]
    if not named:
        return "nobody"
    rest = _int_of(count) - len(named)
    if rest > 0:
        named.append("%d more" % rest)
    if len(named) == 1:
        return named[0]
    return "%s and %s" % (", ".join(named[:-1]), named[-1])


def _names_of(leads, limit: int = _NAMED_IN_SUMMARY) -> str:
    """`_named` for a caller that is already holding the whole list."""
    leads = list(leads)
    return _named(len(leads), leads, limit)


def _listed(items, limit: int = _NAMED_IN_SUMMARY) -> str:
    """"a@x, b@y and 2 more" — a handful of plain strings, as English.

    `_named` reads a business name off a lead record; this is for the places
    that are already holding the strings, which is every sentence about
    sending accounts.
    """
    named = [_text_of(item).strip() for item in list(items)[:limit]
             if _text_of(item).strip()]
    rest = len(list(items)) - len(named)
    if not named:
        return "nothing"
    if rest > 0:
        named.append("%d more" % rest)
    if len(named) == 1:
        return named[0]
    return "%s and %s" % (", ".join(named[:-1]), named[-1])


_STAMPED = ("status", "opportunity_score", "audit_json", "ai_json", "name",
            "email", "city", "category", "phone", "website", "source")


def _lead_stamp(lead: dict) -> tuple:
    """Everything the screen's derived caches read off one record.

    Raw values and no conversion: this is asked once per lead on every reload,
    and `_text_of` eleven times over five thousand leads costs more than the
    JSON decode it is there to save.
    """
    return tuple(lead.get(field) for field in _STAMPED)


def _rules_stamp(settings: dict) -> str:
    """The settings the personalisation answers depend on, as one comparable value.

    The whole dict rather than the handful of keys `core.templates` happens to
    read today: guessing at that list is how a cache goes stale the next time
    that module grows a rule.
    """
    try:
        return json.dumps(settings, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(sorted(settings)) if isinstance(settings, dict) else ""


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


# ── Saved views on disk ──────────────────────────────────────────────────────

def _views_path() -> str:
    """Where the leads tab keeps what it remembers. Resolved late, never cached."""
    return os.path.join(_settings.SETTINGS_DIR, _VIEWS_FILENAME)


def _fields_from_keys(keys, fallback) -> tuple:
    """A stored column list read back as field ids, in the spec's own order.

    Unknown names are dropped rather than refused, so a file written by a build
    that had a column this one does not still opens. The fixed fields are put
    back whatever the file says: a view saved before Business was pinned must
    not be able to produce a table of nameless rows.
    """
    wanted = {_FIELD_OF_KEY[key] for key in keys or ()
              if isinstance(key, str) and key in _FIELD_OF_KEY}
    if not wanted:
        return tuple(fallback)
    wanted.update(_FIXED_FIELDS)
    return tuple(field for field in range(len(_LEAD_COLUMNS)) if field in wanted)


def _fields_wanted(entry: dict, fallback) -> tuple:
    """The columns one stored entry asked for, plus any field it predates.

    A column added after the entry was written is not a column the user
    switched off — they were never offered it — so it arrives on. `known` is
    the key list the entry was saved by; an entry without one was saved before
    that was recorded, and `_KNOWN_BEFORE` is what it could have named.

    Read off the entry rather than off a bare key list because both callers
    have the whole dict, and the two halves have to be read together: a
    `columns` from one build and a `known` from another describe nothing.
    """
    entry = entry if isinstance(entry, dict) else {}
    wanted = set(_fields_from_keys(entry.get("columns"), fallback))
    seen = {key for key in (entry.get("known") or _KNOWN_BEFORE)
            if isinstance(key, str)}
    wanted.update(index for index, key in enumerate(_FIELD_KEYS)
                  if key not in seen)
    return tuple(field for field in range(len(_LEAD_COLUMNS)) if field in wanted)


def _keys_of_fields(fields) -> list:
    return [_FIELD_KEYS[field] for field in fields
            if 0 <= field < len(_FIELD_KEYS)]


def _read_views() -> dict:
    """Everything the leads tab remembered last time, or sane empties.

    A file this screen cannot read is a file it overwrites on the next save,
    and that is deliberate: the alternative is a lead table that will not open
    because a JSON file next to it has a stray comma in it.
    """
    try:
        with open(_views_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    views = [view for view in (data.get("views") or [])
             if isinstance(view, dict) and _text_of(view.get("name")).strip()]
    return {
        "columns": list(data.get("columns") or []),
        "known": list(data.get("known") or []),
        "views": views[:_MAX_VIEWS],
        "current": _text_of(data.get("current")),
        "search": _text_of(data.get("search")),
        "status": _text_of(data.get("status")),
        "sort": list(data.get("sort") or []),
    }


def _write_views(state: dict) -> bool:
    """Write-then-rename, for the same reason `core.settings` does."""
    path = _views_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1)
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        return False


# ── Reading the filter box ───────────────────────────────────────────────────

def _parse_query(text: str) -> list:
    """The filter box as a list of (field, needle) the table can answer.

    Three things a single `needle in haystack` could not do, and all three were
    asked for. `city:toronto` looks in one field, so a business called Toronto
    Roofing does not answer a search for the city. A quoted `"roofing
    contractor"` stays one needle. And two bare words are two needles that must
    both land, which is what makes `toronto roofing` find the roofers in
    Toronto — the case the old single-substring match could never match,
    because the record says "Toronto" in one field and "Roofing contractor" in
    another and no substring of it says both.

    An unknown prefix is not a field, it is text: `9:30` searches for `9:30`.
    """
    terms = []
    for match in _SEARCH_TERMS.finditer(_text_of(text).strip().lower()):
        field = match.group("field")
        quoted, bare = match.group("quoted"), match.group("bare")
        needle = quoted if quoted is not None else (bare or "")
        if field and field in _SEARCH_FIELDS:
            if needle:
                terms.append((_SEARCH_FIELDS[field], needle))
        elif field:
            terms.append(("", match.group(0)))
        elif needle:
            terms.append(("", needle))
    return terms


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


def _section_header(title: str, note: str, *, actions=(), t=None) -> QWidget:
    """The name of the step, and the one muted line saying what it is for.

    `components.page_header()` and not a heading of this screen's own, because
    the shell draws that same object at the top of the pane and two spellings
    of one title is how this app came to have four different top bars in the
    first place. What is set here is the one thing the component leaves to its
    caller: this is a *section* of a screen rather than the screen, so it sits
    flush with the page's own margin instead of indenting past it, and it takes
    the tight end of the spacing grid. That end is measured. The campaign
    column's four cards need 612px and the pane handed them 720 at the default
    window size, so 108px is the whole budget a header on that page has before
    the Schedule card's last line goes off the bottom of a window that fits it
    today. This one costs 55, and the column still fits at 653.
    """
    t = t or components.active_theme()
    header = components.page_header(title, description=note, actions=actions)
    header.actions_layout.setContentsMargins(t.space["0"], t.space["0"],
                                             t.space["0"], t.space["2"])
    return header


def _combo(t) -> QComboBox:
    """A bare dropdown at the toolbar height.

    `components.select()` is the labelled form — a caption, a help line and a
    reserved error line — which is right in a form and three rows too tall in a
    filter bar. The sheet styles `QComboBox` itself, so the only thing set here
    is which height token it takes.
    """
    combo = QComboBox()
    combo.setFixedHeight(t.control["sm"])
    return combo


def _thin_bar(t):
    """The 2px progress rule, at the one length the tokens call a rule.

    Taken here rather than left to the sheet's own `height`, which sets the
    size hint and loses to QProgressBar's font-derived minimum — measured 21px
    against a 2px hint, so an empty bar read as an empty input box.
    """
    bar = QProgressBar()
    bar.setTextVisible(False)
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setFixedHeight(t.space["hair"])
    return bar


class _ElidedLabel(QLabel):
    """A one-line label that shortens with an ellipsis instead of being cut.

    Qt clips a plain QLabel at the widget edge and paints no cue, so the
    Campaign To/From line rendered as "…@gmail.cc" at the default window size
    and stopped dead at "From Sam" at the minimum — with `toolTip()` empty, the
    user had nothing telling them a sender address was missing from the line
    that says which account will send. The full text is always the tooltip, so
    what has been dropped is one hover away.

    `components.elided_label()` has since arrived and this is deliberately not
    it, because the two make opposite promises about the tooltip. That one sets
    a tooltip only while the line is actually cut, which is right for a page
    description nobody needs the whole of; this one always carries the whole
    line, because the line it carries is the sending account and "which address
    is this going out from" has to be answerable at every window width. The two
    call sites here — the Campaign From line and the per-account rows — are
    pinned to that by `tests/test_ui_chrome.py`. Merging them means one label
    that takes the promise as an argument; see the handover note.
    """

    # The floor is a few characters and an ellipsis: below that the line stops
    # being readable and the tooltip is the only copy that matters anyway.
    _FLOOR_CH = 8

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setObjectName("muted")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = _text_of(text)
        self.setToolTip(self._full)
        self._paint_elided()

    def fullText(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        # Otherwise QLabel asks for the whole string as its minimum and the row
        # it sits in cannot shrink past it — the same clip, one level up.
        hint = super().minimumSizeHint()
        floor = self.fontMetrics().horizontalAdvance("…") * self._FLOOR_CH
        return QSize(min(hint.width(), floor), hint.height())

    def sizeHint(self) -> QSize:
        # super() first: it polishes the widget, and the sheet's font is what
        # the elision has to be measured against. Reading `fontMetrics()` before
        # that call answers for whatever font the label had before styling.
        height = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full)
                     + components.BORDER * 2, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._paint_elided()

    def changeEvent(self, event) -> None:
        # The sheet arrives after construction, and the label is first elided
        # against whatever font it had before that. Without this the line keeps
        # a shortening measured in the wrong font until something resizes it.
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange,
                            QEvent.ApplicationFontChange):
            self._paint_elided()

    def _paint_elided(self) -> None:
        width = max(0, self.width() - self.margin() * 2)
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, width) \
            if width else self._full
        QLabel.setText(self, shown)


# ── Badges in a table cell ───────────────────────────────────────────────────


class _BadgeDelegate(QStyledItemDelegate):
    """Paints `components`' own pill or badge into a cell, one widget per value.

    A delegate rather than `setCellWidget`, and the reason is measured rather
    than argued. Filling a 500-lead table with a `status_pill()` and a
    `score_badge()` per row builds a thousand QLabels and takes **9,692 ms**;
    the same table with plain cells takes 76 ms. This delegate keeps one widget
    per *distinct* value — eleven statuses and four score bands, whatever the
    row count — and renders it into the cell's own painter: **59 ms** to build
    and 11 ms to paint the whole table. That is 164x, and it is the difference
    between a lead list that opens and one that hangs.

    The widgets themselves are still `components.status_pill()` and
    `components.score_badge()` called unchanged, so nothing about how a status
    or a band is coloured, marked or shaped is decided in this file. What is
    decided here is only where the pill sits in the cell. This whole class
    belongs in `ui/components.py` beside the two builders it calls; see the
    handover note.
    """

    def __init__(self, build, parent=None):
        super().__init__(parent)
        self._build = build
        self._made: dict = {}

    def badge(self, key):
        """The one widget for `key`, built on first sight and kept."""
        made = self._made.get(key)
        if made is None:
            made = self._build(key)
            made.ensurePolished()
            made.adjustSize()
            self._made[key] = made
        return made

    def paint(self, painter, option, index) -> None:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        # The row's own ground, its selection and its rail still come from the
        # sheet; only the text is taken away, because the badge replaces it.
        style_option.text = ""
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, style_option, painter, widget)

        key = index.data(_BADGE_ROLE)
        if key is None:
            return
        badge = self.badge(key)
        size = badge.sizeHint()
        badge.resize(size)
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(option.rect.x() + components.BORDER,
                          option.rect.y()
                          + (option.rect.height() - size.height()) // 2)
        badge.render(painter, QPoint(), QRegion(), QWidget.DrawChildren)
        painter.restore()


# ── One line of the activity console ─────────────────────────────────────────


class _LogLineDelegate(QStyledItemDelegate):
    """A console line: the stamp in a muted gutter, the message in its own ink.

    `components.log_console()` gives every line a marker in its level's family
    and paints the whole line in that family's ink — the timestamp included, so
    four hundred lines are four hundred equally loud clocks and every message is
    read past one to reach it. A log is read down the messages; the stamps are
    the ruler beside them, and a ruler is quieter than what it measures. One
    QListWidgetItem carries one colour, so the two tones cannot come from the
    item: the line is drawn here instead.

    Everything it paints comes from the theme it was built with, and the two
    colours are the console's own — `text.tertiary` for the stamp, and for the
    message whatever ink `log_console()` already decided the level takes. It
    wraps rather than eliding because the panel is 375px at the window's
    minimum and one business name is wider than that, and a console that hides
    the end of a failure off the right-hand edge is the one thing worse than no
    console. This belongs in `ui/components.py` beside `log_console()`; see the
    handover note.
    """

    # The stamp `components.STAMP` writes, matched rather than measured off a
    # separator: a business called "09:00 Plumbing" must not be read as a time.
    _STAMP = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+")

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._muted = QColor(t.color["text.tertiary"])
        self._pad = t.space["hair"]
        self._gap = t.space["2"]
        self._mark = _icons.pixels("xs")

    # ── What a line is made of ───────────────────────────────────────────

    def _split(self, line: str) -> tuple:
        """(stamp, message). An unstamped line — the placeholder — is all message."""
        found = self._STAMP.match(_text_of(line))
        return (found.group(1), line[found.end():]) if found else ("", line)

    def _gutter(self, metrics: QFontMetrics) -> int:
        """As wide as a stamp and a gap, whatever the mono face measures at.

        Taken from the digits rather than from the string, because a monospace
        face is not guaranteed and a proportional fallback would leave every
        message starting at a different x — which is the whole of what the
        gutter is for.
        """
        return metrics.horizontalAdvance("0" * 8) + self._gap

    def _room(self, option) -> int:
        """The width to lay a line out in: what the view offered, or its viewport.

        Asked both ways because it is asked both ways: laying out a list, Qt
        hands `sizeHint` the viewport's rect for some calls and an empty one for
        others, and a line measured against a zero width claims one line where
        it needs three.
        """
        wide = option.rect.width()
        if wide <= 0:
            view = self.parent()
            wide = view.viewport().width() \
                if isinstance(view, QAbstractItemView) else 0
        return max(1, wide)

    def _ink(self, index, option) -> QColor:
        found = index.data(Qt.ForegroundRole)
        if hasattr(found, "color"):
            return found.color()
        return QColor(found) if found else option.palette.color(QPalette.Text)

    # ── Measuring and painting ───────────────────────────────────────────

    def sizeHint(self, option, index) -> QSize:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        metrics = QFontMetrics(style_option.font)
        stamp, message = self._split(style_option.text)
        room = self._room(option)
        # Deliberately narrower than the box the paint below is handed: the
        # stylesheet's own `::item` padding is inside that box and is not
        # visible from here, so a measure taken at the full width would wrap
        # one word later than the drawing does and leave the last line cut off.
        text = room - self._mark - self._gap * 2 \
            - (self._gutter(metrics) if stamp else 0)
        height = metrics.boundingRect(QRect(0, 0, max(1, text), 0),
                                      Qt.TextWordWrap, message).height()
        return QSize(room, max(height, self._mark) + self._pad * 2)

    def paint(self, painter, option, index) -> None:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        line = style_option.text
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        # Taken before the text is cleared: with nothing to lay out, the style
        # answers with a rect of no width, and every line would then be drawn
        # at the left edge of the row underneath its own marker.
        box = style.subElementRect(QStyle.SE_ItemViewItemText, style_option,
                                   widget)
        style_option.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, style_option, painter, widget)

        stamp, message = self._split(line)
        metrics = QFontMetrics(style_option.font)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setFont(style_option.font)
        left = box.left()
        if stamp:
            painter.setPen(self._muted)
            painter.drawText(QRect(left, box.top(), self._gutter(metrics),
                                   box.height()),
                             int(Qt.AlignLeft | Qt.AlignTop), stamp)
            left += self._gutter(metrics)
        painter.setPen(self._ink(index, style_option))
        painter.drawText(QRect(left, box.top(), max(1, box.right() - left + 1),
                               box.height()),
                         int(Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap),
                         message)
        painter.restore()


# ── Per-day bars ─────────────────────────────────────────────────────────────

class _DayBars(QWidget):
    """Sent and still-queued volume per day, painted directly.

    A bar row is a handful of rectangles. Pulling in a charting dependency to
    draw them would cost more than it saves, and painted here they take the
    accent and the rule straight out of the theme, so the chart changes palette
    with everything else.
    """

    # How many days fit before the row stops being readable, and how wide a bar
    # and its slot may get. Counts and token multiples, so a dense theme draws a
    # denser chart.
    _DAYS = 14

    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._t = t
        self._days: list[tuple[str, int, int]] = []
        self.setMinimumHeight(t.space["9"] * 2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_days(self, days) -> None:
        self._days = list(days or [])[-self._DAYS:]
        self.update()

    def paintEvent(self, event) -> None:
        if not self._days:
            return
        t = self._t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = painter.font()
        font.setPixelSize(t.font["caption"][0])
        painter.setFont(font)

        label_h, value_h = t.space["4"], t.space["3"]
        top = value_h
        floor = self.height() - label_h
        span = max(1, floor - top)
        peak = max(1, max(sent + queued for _, sent, queued in self._days))

        # Bars keep a sane width and the row is centred, so a three-day campaign
        # reads as a short row rather than three lonely bars flung across the
        # full width of the card.
        slot = min(float(t.space["9"] + t.space["6"]),
                   self.width() / float(len(self._days)))
        width = max(float(t.space["2"]),
                    min(float(t.space["8"] + t.space["2"]), slot - t.space["3"]))
        origin = max(0.0, (self.width() - slot * len(self._days)) / 2.0)
        radius = t.radius["sm"]
        painter.setPen(Qt.NoPen)

        for index, (label, sent, queued) in enumerate(self._days):
            base = origin + index * slot
            left = base + (slot - width) / 2.0
            total = sent + queued
            height = span * (total / float(peak))
            sent_h = height * (sent / float(total)) if total else 0.0

            painter.setBrush(QColor(t.color["border.subtle"]))
            painter.drawRoundedRect(int(left), int(floor - height), int(width),
                                    int(max(float(t.space["hair"]), height)),
                                    radius, radius)
            if sent_h >= t.space["hair"]:
                painter.setBrush(QColor(t.color["accent.default"]))
                painter.drawRoundedRect(int(left), int(floor - sent_h), int(width),
                                        int(sent_h), radius, radius)

            painter.setPen(QColor(t.color["text.tertiary"]))
            painter.drawText(int(base), floor + t.space["hair"], int(slot), label_h,
                             Qt.AlignHCenter | Qt.AlignVCenter, label)
            if total and width >= t.space["5"]:
                painter.setPen(QColor(t.color["text.secondary"]))
                painter.drawText(int(base), int(floor - height) - value_h,
                                 int(slot), value_h,
                                 Qt.AlignHCenter | Qt.AlignBottom, str(total))
            painter.setPen(Qt.NoPen)


# ── Naming a view ────────────────────────────────────────────────────────────

def _menu_text(name: str) -> str:
    """A user's own words as a menu entry, clipped and with its ampersands kept.

    Qt reads a single `&` as the mnemonic marker, so a view called "Smith &
    Sons" arrives on the menu as "Smith _Sons" with no way to press it.
    """
    return _clip(name, _VIEW_NAME_CH).replace("&", "&&")


def _view_sentence(view: dict) -> str:
    """What one saved view actually shows, in a line a menu can hold.

    Written out rather than reduced to its name, because a list of five names
    the user typed in a hurry six weeks ago is a list of five guesses. The
    tooltip says what pressing it will do.
    """
    parts = []
    search = _text_of(view.get("search")).strip()
    if search:
        parts.append("matching “%s”" % search)
    wanted = _text_of(view.get("status"))
    label = next((text for text, key in _STATUS_FILTERS if key == wanted), "")
    if wanted and label:
        parts.append(label.lower())
    sort = list(view.get("sort") or [])
    if sort and sort[0] in _FIELD_OF_KEY:
        field = _FIELD_OF_KEY[sort[0]]
        down = not (len(sort) > 1 and sort[1] == "asc")
        parts.append("by %s%s" % (
            _LEAD_COLUMNS[field].title.lower(),
            "" if not down else
            ", highest first" if field == _COL_SCORE else ", reversed"))
    columns = [key for key in (view.get("columns") or []) if key in _FIELD_OF_KEY]
    if columns and len(columns) != len(_LEAD_COLUMNS):
        parts.append("%d of %d columns" % (len(columns), len(_LEAD_COLUMNS)))
    return "every lead" if not parts else ", ".join(parts)


class _CampaignReviewDialog(QDialog):
    """The last look at a campaign before it is committed to the queue.

    This is the gate, not a receipt: rejecting it deletes every message the
    plan wrote. So the number that decides the answer has to be readable here
    and not only afterwards on the Campaign card — "Generic Copies: 31" is a
    figure nobody can act on, because it says neither what a generic copy is
    nor which of four things went wrong to produce one. `_generic_sentence`
    reads the plan's own per-reason tally, which is where `core.campaign`
    counts them, and the same words appear on the card underneath.
    """

    def __init__(self, plan: dict, parent=None):
        super().__init__(parent)
        t = components.active_theme()
        self.setWindowTitle("Review Campaign Plan")
        self.setModal(True)
        box = _rows(self, margin="4", spacing="3", t=t)
        
        box.addWidget(components.heading("Campaign Summary", level="h3"))
        
        stats_layout = _rows(margin="0", spacing="2", t=t)
        
        def add_stat(label, val):
            row = _cols(margin="0", spacing="1", t=t)
            row.addWidget(components.body_label(label + ":", max_chars=40))
            row.addWidget(components.heading(str(val), level="h4"))
            stats_layout.addLayout(row)
            
        queued = _int_of(plan.get("queued"))
        generic = _int_of(plan.get("generic"))
        add_stat("Queued Messages", queued)
        add_stat("Follow-ups", plan.get("followups", 0))
        add_stat("Skipped Leads", plan.get("skipped", 0))
        add_stat("Form letters", generic)

        accounts_text = ", ".join(plan.get("accounts", [])) or "None"
        add_stat("Accounts", accounts_text)
        add_stat("Sending Days", plan.get("days", 0))

        box.addLayout(stats_layout)

        if generic:
            # The one line that changes the answer. A form letter is not a
            # smaller email, it is one that says nothing about the business it
            # is addressed to — and a user who can see that 31 of 40 are that
            # before approving can go back and crawl the sites again.
            box.addWidget(components.body_label(
                "%d of %s will say nothing about the business they are "
                "addressed to — %s. Discard this plan if you would rather fix "
                "those first."
                % (generic, _plural(queued, "email"),
                   _generic_sentence(plan.get("generic_reasons"))),
                tone="warning", max_chars=80))
        if _int_of(plan.get("skipped")):
            box.addWidget(components.body_label(
                "%d lead%s left out — %s."
                % (_int_of(plan.get("skipped")),
                   "" if _int_of(plan.get("skipped")) == 1 else "s",
                   _reason_list(plan.get("skip_reasons"))),
                tone="secondary", max_chars=80))

        if plan.get("warnings"):
            box.addWidget(components.heading("Warnings", level="h4"))
            for w in plan.get("warnings", []):
                box.addWidget(components.body_label("• " + w, tone="warning", max_chars=80))
                
        row = _cols(margin="0", spacing="2", t=t)
        row.addStretch()
        row.addWidget(components.button("Discard Plan", kind="secondary", on_click=self.reject))
        row.addWidget(components.button("Approve and Schedule", kind="primary", on_click=self.accept))
        box.addLayout(row)


class _NameViewDialog(QDialog):
    """Ask for a name for the current filter. One field and two buttons.

    A dialog and not `QInputDialog`, for the same reason `components.confirm`
    is not `QMessageBox.question`: the box Qt builds wears Qt's palette and its
    own button labels, and this app has two palettes and says what a button
    will do rather than "OK".
    """

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        t = components.active_theme()
        self.setWindowTitle("Save this view")
        self.setModal(True)
        box = _rows(self, margin="4", spacing="3", t=t)
        box.addWidget(components.heading("Save this view", level="h3"))
        self.field = components.text_field(
            placeholder="Toronto roofers worth calling",
            label="Name",
            help="The filter, the sort and the columns on screen, kept under "
                 "this name. Saving over a name replaces it.")
        self.field.setText(current)
        box.addWidget(self.field)

        row = _cols(margin="0", spacing="2", t=t)
        row.addStretch()
        row.addWidget(components.button("Cancel", kind="secondary",
                                        on_click=self.reject))
        self.save_btn = components.button("Save view", kind="primary",
                                          on_click=self.accept)
        row.addWidget(self.save_btn)
        box.addLayout(row)
        # Return does what the button does, including refusing: a dialog whose
        # Save is disabled and whose Return saves anyway has two answers to the
        # same question.
        self.field.edit.returnPressed.connect(self._on_return)
        self.field.edit.textChanged.connect(self._on_typed)
        self._on_typed(current)

    def _on_return(self) -> None:
        if self.save_btn.isEnabled():
            self.accept()

    def _on_typed(self, text: str) -> None:
        self.save_btn.setEnabled(bool(_text_of(text).strip()))
        self.save_btn.setToolTip("" if _text_of(text).strip()
                                 else "A view needs a name to be found by")

    def name(self) -> str:
        return _text_of(self.field.text()).strip()[:_VIEW_NAME_CH]


def _ask_name(parent, current: str = "") -> str:
    """The dialog, run. "" when the user backed out."""
    dialog = _NameViewDialog(current, parent)
    try:
        return dialog.name() if dialog.exec_() == QDialog.Accepted else ""
    finally:
        dialog.deleteLater()


# ── What was actually sent ───────────────────────────────────────────────────

class _SentMailDialog(QDialog):
    """One message that has already gone, exactly as the server received it.

    The user could not see this before. Everything on the Sending and Stats tabs
    counted messages; nothing showed one, so the only way to find out what had
    reached a stranger was to open the Gmail account's Sent folder — which is
    the answer to "did it go", not to "what did it say".

    It reads the stored wire form and nothing else. Re-rendering from the
    template would be easier and would be wrong: the template store is editable,
    the sender profile changes mid-campaign, and a February email redrawn from
    today's copy is a message that was never sent, presented with the authority
    of a record.

    Two views, because the two questions are different. *Message* is what the
    recipient read, footer included, on the light paper every preview in this
    app uses. *Source* is the bytes — the headers a deliverability problem lives
    in, and the HTML part underneath them.
    """

    # In characters, like every other measure in this file: what is being sized
    # is text, and a dialog written down in pixels stops fitting the moment the
    # user's font scale is not the developer's.
    _BODY_CH = 84
    _ROWS = 22

    def __init__(self, row: dict, raw: str, parent=None):
        super().__init__(parent)
        self._row = row if isinstance(row, dict) else {}
        self._wire = _mailer.read_wire(raw)
        self._raw = _text_of(raw)
        self.setWindowTitle("Sent message")
        self.setModal(True)
        self._build()

    def _build(self) -> None:
        t = components.active_theme()
        box = QVBoxLayout(self)
        box.setContentsMargins(t.space["5"], t.space["5"], t.space["5"], t.space["5"])
        box.setSpacing(t.space["3"])

        box.addWidget(components.heading(self._subject() or "(no subject)", "h2"))
        box.addWidget(components.body_label(self._provenance(), tone="secondary",
                                            max_chars=self._BODY_CH))

        switch = _cols(margin="0", spacing="2", t=t)
        switch.addWidget(components.section_label("What left"))
        switch.addStretch()
        self._view = QButtonGroup(self)
        self._view.setExclusive(True)
        for index, label in enumerate(("Message", "Source")):
            tab = components.button(label, kind="tab", size="sm")
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            self._view.addButton(tab, index)
            switch.addWidget(tab)
        self._view.idClicked.connect(lambda _index: self._paint())
        box.addLayout(switch)

        self._paper = QTextBrowser()
        self._paper.setObjectName("email_paper")
        self._paper.setOpenExternalLinks(False)
        self._paper.setOpenLinks(False)
        self._paper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box.addWidget(self._paper, stretch=1)

        box.addWidget(components.hint(
            "This is the copy that was handed to Gmail, not the template it came "
            "from. Editing the template later does not change it.",
            max_chars=self._BODY_CH))

        close = _cols(margin="0", spacing="2", t=t)
        close.addStretch()
        close.addWidget(components.button("Close", kind="secondary",
                                          on_click=self.accept))
        box.addLayout(close)

        metrics = QFontMetrics(self.font())
        self.setMinimumSize(QSize(_measure(metrics, self._BODY_CH) + t.space["5"] * 2,
                                  metrics.lineSpacing() * self._ROWS))
        self._paint()

    def _subject(self) -> str:
        for name, value in self._wire["headers"]:
            if name == "Subject":
                return value
        return _text_of(self._row.get("subject"))

    def _provenance(self) -> str:
        """Who it went to, from which account, and when — off the row, not the copy."""
        step = _int_of(self._row.get("step"))
        parts = ["To %s" % (_text_of(self._row.get("to_email")) or "an unknown address"),
                 "from %s" % (_text_of(self._row.get("account_email")) or "an unknown account"),
                 _clock(_float_of(self._row.get("sent_at"))) if
                 _float_of(self._row.get("sent_at")) else "at an unrecorded time",
                 "follow-up %d" % step if step else "first touch"]
        error = _text_of(self._row.get("error")).strip()
        if error:
            parts.append(error)
        return "  ·  ".join(parts)

    def _paint(self) -> None:
        if self._view.checkedId() == 1:
            self._paper.setHtml(_paper_html(html.escape(self._raw or
                                            "Nothing was stored for this message."),
                                            _PAPER.font["caption"][0],
                                            _PAPER.color["text.secondary"],
                                            monospace=True))
        else:
            self._paper.setHtml(self._message_html())
        _paint_paper(self._paper)

    def _message_html(self) -> str:
        rows = "".join(
            '<tr><td style="padding:0 %dpx %dpx 0;color:%s;white-space:nowrap;'
            'vertical-align:top;">%s</td><td style="padding:0 0 %dpx 0;color:%s;">'
            '%s</td></tr>'
            % (_PAPER.space["3"], _PAPER.space["1"], _PAPER.color["text.tertiary"],
               html.escape(name), _PAPER.space["1"], _PAPER.color["text.secondary"],
               html.escape(value))
            for name, value in self._wire["headers"])
        head = ('<table style="border-collapse:collapse;margin:0 0 %dpx 0;">%s</table>'
                '<hr style="border:none;border-top:%dpx solid %s;margin:0 0 %dpx 0;">'
                % (_PAPER.space["4"], rows, components.BORDER,
                   _PAPER.color["border.subtle"], _PAPER.space["4"])) if rows else ""

        body = self._wire["text"] or _text_of(self._row.get("body_text"))
        blocks = []
        for para in re.split(r"\n\s*\n", body.strip()):
            lines = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
            if lines:
                blocks.append('<p style="margin:0 0 %dpx 0;">%s</p>'
                              % (_PAPER.space["3"], "<br>".join(lines)))
        if not blocks:
            blocks.append('<p style="margin:0;">%s</p>' % html.escape(
                "Nothing was stored for this message. It was sent before this "
                "version of MapHarvest, or the store was unwritable at the time."))
        return _paper_html(head + "".join(blocks), _PAPER.font["h3"][0],
                           _PAPER.color["text.primary"])


# ── Planning worker ──────────────────────────────────────────────────────────

class _PlanWorker(QThread):
    """Runs `plan_campaign` off the GUI thread.

    Planning audits every lead that has not been audited yet — a site crawl and
    possibly a model call each — so on a real list it is minutes of network
    work. `core.campaign` ships a worker for auditing and one for sending but
    none for planning, so the thread lives here. It holds no state beyond its
    arguments and exists only to keep the window painting.

    Stopping is co-operative, because quitting the app mid-plan is the normal
    way a plan ends rather than an edge case. `plan_campaign` is handed
    `should_stop` and checks it between leads, so `ui.app._stop_thread` gets its
    thread back at the next lead boundary instead of sitting out `wait(5000)`
    and calling `terminate()` in the middle of a crawl. What was already queued
    is committed and survives in the database either way.
    """

    plan_signal = pyqtSignal(dict)
    progress_signal = pyqtSignal(int, int, str)

    def __init__(self, campaign_id: int, leads: list, template_id: str,
                 settings: dict, channel: str = ""):
        super().__init__()
        self.campaign_id = _int_of(campaign_id)
        self.leads = list(leads or [])
        self.template_id = _text_of(template_id)
        self._settings = settings if isinstance(settings, dict) else {}
        # Which transport this queue is being written for. `plan_campaign`
        # takes it from here and nothing else about this worker changes: the
        # audit, the personalisation and the scheduling are one implementation
        # on both channels.
        self.channel = _campaign._channel(channel or _campaign.EMAIL)
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            ai = AIClient(self._settings)
        except Exception:
            ai = None
        try:
            plan = plan_campaign(
                None, campaign_id=self.campaign_id, leads=self.leads,
                template_id=self.template_id,
                profile=self._settings.get("sender_profile") or {},
                settings=self._settings, ai=ai, progress=self._progress,
                should_stop=self._should_stop, channel=self.channel,
            )
        except Exception as exc:
            plan = {"error": "%s: %s" % (type(exc).__name__, exc), "queued": 0,
                    "channel": self.channel}
        self.plan_signal.emit(plan if isinstance(plan, dict) else {})

    def _should_stop(self) -> bool:
        return not self._running

    def _progress(self, done, total, message) -> None:
        self.progress_signal.emit(_int_of(done), _int_of(total), _text_of(message))


# ── The screen ───────────────────────────────────────────────────────────────

class OutreachScreen(QWidget):
    """Leads, campaign, sending and stats for one outreach run."""

    home_signal = pyqtSignal()
    settings_signal = pyqtSignal()

    TABS = ("Leads", "Campaign", "Sending", "Stats")

    def __init__(self):
        super().__init__()
        self.settings = _settings.load_settings()
        try:
            self.conn = _db.connect()
        except Exception:                    # a dead store must not block the GUI
            self.conn = None

        # Workers are kept referenced after they finish rather than cleared in
        # the done handler. `done_signal` is emitted from inside `run()`, so
        # dropping the last reference there can destroy a QThread that has not
        # unwound yet; the flags below track "busy" instead, and a worker is
        # only replaced once its thread has actually stopped. They stay plain
        # attributes because `ui.app` finds a screen's threads by inspecting
        # them at shutdown.
        self.audit_worker = None
        self.plan_worker = None
        self.send_worker = None
        self._auditing = False
        self._planning = False
        self._leads_dirty = False
        self._stats_dirty = False
        # lead id -> the Site words it carried when the crawl now running was
        # started, and whether that crawl was a retry. Kept because "did that
        # do anything?" can only be answered against the state before it ran,
        # and after `_reload_leads` that state is gone.
        self._audit_before: dict[int, str] = {}
        self._audit_retry = False
        # The second line under "Queues N leads", carried rather than
        # recomputed: working it out is a walk of the target and the label it
        # sits in is rewritten once per audited lead.
        self._form_letter_note = ""
        # Set between Stop being pressed and the worker actually unwinding. The
        # one-second tick repaints the status line, so without this the
        # sentence Stop writes lives for under a second and the user is left
        # watching a run that says it is still sending.
        self._stopping = False
        # Which accounts the run has taken out of service today, and when that
        # was last read off the event log. See `_benched_today`.
        self._benched: set = set()
        self._benched_at = 0.0

        self._leads: list[dict] = []
        # lead id -> why that lead's email would be a form letter, "" when it
        # would not. Filled row by row as the table is built, because the answer
        # comes out of `core.templates` and the table is the only place that
        # walks every lead.
        self._generic: dict[int, str] = {}
        # The three derived facts about a lead that cost real time to work out,
        # kept against its id so no pass has to work them out twice. `_gaps` is
        # the Headline gap cell and whether it is a form letter — one JSON
        # decode and one `core.templates` call; `_blobs` is everything the
        # filter box searches, lowercased once instead of once per keystroke
        # per row. Both are dropped for a lead the moment an audit rewrites it,
        # and wholesale when the list is re-read.
        self._gaps: dict[int, tuple] = {}
        self._blobs: dict[int, str] = {}
        # lead id -> whether that lead's number can be messaged at all, and the
        # default region the answer was worked out under. Cached for the reason
        # the two above are: the Phone filter asks it of every row on every
        # keystroke. Thrown away wholesale when the region moves, because the
        # region is half the question — a number with no country code becomes
        # usable the moment one is set.
        self._phones: dict[int, bool] = {}
        self._phone_region = None
        # lead id -> the fields those three are derived from, as they were when
        # the answers were worked out, and the same for the settings the
        # personalisation call reads. A reload compares these and forgets only
        # the leads whose record actually moved.
        self._stamps: dict[int, tuple] = {}
        self._rules_stamp = ""
        # lead id -> the row it is painted on. Row `n` holds `self._leads[n]`,
        # so this is also the index of the record, and it is what lets an
        # audited lead find itself without a search.
        self._row_of: dict[int, int] = {}
        # What the line above the table says, carried rather than recounted.
        # `_buckets` is leads per status, `_generic_count` how many would send a
        # form letter, `_visible` how many rows the filters leave showing.
        self._buckets: dict[str, int] = {}
        self._generic_count = 0
        self._visible = 0
        # (field, value) -> the words the badge in that cell reads.
        self._badge_text: dict[tuple, str] = {}
        # Which rows are currently made of real cells. The table holds a row per
        # lead so the scrollbar is honest about the size of the list, but only
        # the band around the viewport is built; see `_paint_window`.
        self._painted: set = set()
        # Set when a theme change reaches this screen while it is off screen,
        # and spent by `showEvent`.
        self._stale = False
        self._search = ""
        self._terms: list = []
        # Sorted by a *field*, not by a column: a sort has to survive the column
        # it sorts on being switched off, and after that the two are not the
        # same number.
        self._sort = (_COL_SCORE, Qt.DescendingOrder)
        # What the leads tab remembered from last time: the chosen columns, the
        # named views, and the filter that was live when the app last closed.
        self._stored = _read_views()
        self._fields = _fields_wanted(self._stored, range(len(_LEAD_COLUMNS)))
        self._col_of: dict[int, int] = {}
        self._views: list = list(self._stored.get("views") or [])
        self._view_name = _text_of(self._stored.get("current"))
        # True only while this screen is putting a remembered filter back into
        # its own widgets. Setting the search box fires `textChanged`, which is
        # how the user says "this is not that view any more" — and a restore
        # that let that through would delete the name it had just restored.
        self._restoring = False
        self._campaign_id = 0
        self._plan: dict = {}
        # Two channels and they are not the same question. `_channel` is what
        # the Campaign tab is composing — which templates, which preview, which
        # limits, which transport a Prepare writes for. `_send_channel` is the
        # channel of the campaign selected on the Sending tab, read off its own
        # queued rows, because that tab describes a campaign that may have been
        # prepared days ago on the other one.
        self._channel = EMAIL
        self._send_channel = EMAIL
        # The WhatsApp restriction this screen has already told the user about,
        # so a standing one is announced once rather than on every tick.
        self._ban_told = ""
        self._sending = False
        self._paused = False
        # (stamped line, level, message id) for every activity line, so a theme
        # change can put the log back rather than emptying it mid-run, and a
        # line about a real send can be opened from where it was read.
        self._log_lines: list[tuple[str, str, int]] = []
        # Which stat tiles the dashboard is currently laid out for. A tile with
        # nothing to say is not drawn, and the grid re-flows when that changes.
        self._tiles_shown: tuple = ()
        # Per-account rehearsal tally, keyed by lowercased address. A dry run
        # keeps its sends out of the `sends` table on purpose so a rehearsal
        # cannot spend an account's real quota, which left the Accounts card
        # reading "0 / 10 today" throughout the one operation it exists to
        # report on. Counted here instead, and shown next to — never inside —
        # the real number.
        self._rehearsed: dict[str, int] = {}

        # Shell bookkeeping. `_handed_over` is set once the tabs have reached a
        # shell; `_echoing` is true only while the shell is calling back into
        # this screen, and it is what stops the answer from deleting the button
        # whose click is still on the stack.
        self._handed_over = False
        self._echoing = False
        self._context_line = ""

        # The filter box writes what it is showing on every keystroke, and the
        # write is disk. Started by `_save_view_state` and never restarted by a
        # rebuild, because `_restyle_now` reparents this screen's *widgets* and
        # a QTimer is not one.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_view_state)

        self._build()
        self._restore_filter()
        self.refresh()

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ── The shell ────────────────────────────────────────────────────────────

    def subtabs(self) -> tuple:
        """The four steps, for whatever chrome is drawing them.

        The screens no longer carry a top bar of their own — the audit found
        four screens with four different ones, each with its own Home and
        Settings button — so the tabs are handed back rather than drawn here.
        `(labels, on_change, current)` is the shape `AppShell.set_subtabs` takes.
        """
        return tuple(self.TABS), self._on_shell_tab, self.pages.currentIndex()

    def _host(self) -> tuple:
        """The shell this screen is sitting in, and the key it is filed under.

        Found by asking rather than by being told, because the window registers
        a *factory*: a screen is built on its first visit, from inside the call
        that is about to show it, so there is no moment before that at which
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

    def showEvent(self, event) -> None:
        """Hand the tabs over the first time this screen lands in a shell.

        Once, not on every visit: from here on the shell owns which of the four
        is checked — its own row moves it when the user clicks and `_goto_tab`
        moves it when this screen does — and re-publishing the strip on every
        visit would overwrite the shell's record of it with this screen's stale
        copy.
        """
        super().showEvent(event)
        if self._stale:
            # A theme change that landed while this screen was behind another
            # one. It is paid for here, where the user is about to look at the
            # result, rather than inside the click that changed the setting.
            self._stale = False
            self._restyle_now()
        if self._handed_over:
            return
        host, key = self._host()
        if host is None:
            return
        host.set_subtabs(key, self.TABS, self._on_shell_tab,
                         self.pages.currentIndex())
        self._handed_over = True

    def _on_shell_tab(self, index: int) -> None:
        """The shell moved the tab. Change the page, and do not answer back."""
        self._echoing = True
        try:
            self._goto_tab(index)
        finally:
            self._echoing = False

    def _tell_shell(self, index: int) -> None:
        if self._echoing or not self._handed_over:
            return
        host, key = self._host()
        if host is not None:
            host.set_subtabs(key, self.TABS, self._on_shell_tab, index)

    def _publish_state(self) -> None:
        """One line of live state for the shell, and only while there is any.

        The bar's context line is for what is happening right now on a screen
        the user may have walked away from — a send loop that is still mailing
        strangers is the case it exists for. At rest it says nothing, and it is
        only written when it changes: `set_context` rebuilds the whole second
        row, and rebuilding it on a one-second timer would delete the sub-tab
        buttons under the user's pointer once a second.
        """
        line = ""
        if self._sending:
            # The headline the Sending tab is showing, and not a second opinion
            # about the same run. This used to count `sent`, which a rehearsal
            # never writes, so a dry run reported "Sending — 0 of 500" on the
            # one bar that is visible from every screen — the same frozen
            # number `_send_health` was fixed for, one level up. It also said
            # "Sending" while the loop was holding outside the window, which is
            # the sentence this whole pass exists to stop printing.
            line = self._send_health()[0]
        elif self._auditing:
            line = "Auditing sites"
        elif self._planning:
            line = "Preparing a campaign"
        if line == self._context_line:
            return
        self._context_line = line
        host, key = self._host()
        if host is not None:
            host.set_context(key, line, tone="warning" if line else "info")

    def _tell_shell_mode(self) -> None:
        """The dry-run pill is the shell's; this screen only reports the switch."""
        host, key = self._host()
        if host is not None:
            host.set_dry_run(bool(self.settings.get("dry_run", True)))

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        t = components.active_theme()
        root = _rows(self, margin="5", spacing="3", t=t)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_leads_page(t))
        self.pages.addWidget(self._build_campaign_page(t))
        self.pages.addWidget(self._build_sending_page(t))
        self.pages.addWidget(self._build_stats_page(t))
        root.addWidget(self.pages, stretch=1)

        self.toaster = components.Toaster(self)
        root.addWidget(self.toaster.widget)

    # ── Leads tab ────────────────────────────────────────────────────────────

    def _build_leads_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)

        # The count line rides in the header rather than in the toolbar, and
        # that is what left room for one toolbar where there were two —
        # `_build_lead_toolbar` carries the measurement.
        self.lead_counts = components.body_label("", tone="tertiary")
        self.lead_counts.setWordWrap(False)
        box.addWidget(_section_header("Leads", _SECTION_NOTES["Leads"],
                                      actions=(self.lead_counts,), t=t))
        box.addLayout(self._build_lead_toolbar(t))

        self.lead_stack = QStackedWidget()
        self.lead_table = self._make_table(t)
        self.lead_stack.addWidget(self.lead_table)
        self.lead_stack.addWidget(components.empty_state(
            title="No leads yet",
            body="Scrape a city and press Start Outreach on the results, or "
                 "import a CSV that has an email column. A business with no "
                 "email address cannot be contacted and is left out.",
            action="Scrape a city", on_action=self.home_signal.emit))
        box.addWidget(self.lead_stack, stretch=1)

        self.lead_progress = _thin_bar(t)
        self.lead_progress.hide()
        box.addWidget(self.lead_progress)

        box.addLayout(self._build_lead_actions(t))
        box.addWidget(components.hint(
            "Double-click a lead to open its website, right-click one for more, "
            "click a column header to sort. Columns picks what is on screen and "
            "Views keeps a filter under a name. Auditing is what fills the Score "
            "and Headline gap columns, and what the email copy is built from."))
        return page

    def _build_lead_toolbar(self, t):
        """One toolbar: what the table shows, and what it is showing it of.

        Two rows once, and for a measured reason. At the 880px window minimum
        the leads tab has 840px of usable width; the filter bar wanted 735 of
        it for a heading, a search box, a status picker and the count line, and
        adding Views, Columns and Import took it to 1,136 — so Qt shrank the
        search box to 34px and cut "Import CSV…" mid-word. The heading and the
        count line are the header's now, and with those 283px back the five
        controls left measure 625 of the 840: nothing is squeezed at the
        minimum, and the search box keeps the whole 162px its own character cap
        allows it.

        Everything here changes what the table *shows*; everything on the row
        under the table acts on what is selected in it. That was the honest
        half of the old split and it survives — as two rows of controls with a
        table between them rather than as two rows stacked on top of one.
        """
        bar = _cols(margin="0", spacing="2", t=t)

        self.lead_search = components.search_field("Filter, or city:toronto…")
        self.lead_search.setToolTip(_SEARCH_HELP)
        self.lead_search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.lead_search)

        self.status_filter = _combo(t)
        for label, _key in _STATUS_FILTERS:
            self.status_filter.addItem(label)
        self.status_filter.currentIndexChanged.connect(self._on_status_filter)
        bar.addWidget(self.status_filter)

        self.view_btn = components.button("Views", kind="secondary", size="sm",
                                          icon="filter",
                                          on_click=self._show_views_menu)
        bar.addWidget(self.view_btn)

        self.columns_btn = components.button("Columns", kind="secondary",
                                             size="sm", icon="columns",
                                             on_click=self._show_columns_menu)
        self.columns_btn.setToolTip(
            "Choose which columns the table shows. The choice is remembered.")
        bar.addWidget(self.columns_btn)

        import_csv = components.button("Import CSV…", kind="secondary",
                                       size="sm", icon="document",
                                       on_click=self._on_import_csv)
        import_csv.setToolTip("Load leads from a spreadsheet export")
        bar.addWidget(import_csv)

        bar.addStretch()
        return bar

    def _build_lead_actions(self, t):
        """The bulk row: what a selection of five hundred leads can be told to do.

        There was one action on this screen and it was Audit, so a list of five
        hundred could be crawled in a batch and then had to be suppressed,
        copied or reviewed one right-click at a time. Every button here reads
        the selection, and says how many rows it is about to act on.

        Four of them now, which is the brief's list: audit, suppress, export,
        remove. Suppress and Remove are the two that end a lead and they are
        deliberately not the same action — suppressing keeps the address on
        file so it can never be mailed again, which is what an unsubscribe
        means; removing forgets the row, which is what a bad import means. Both
        ask first, and both are `kind="danger"` because both are irreversible
        in the sense that matters: nothing on this screen can put the crawl
        back.
        """
        actions = _cols(margin="0", spacing="2", t=t)

        self.copy_btn = components.button("Copy", kind="secondary", size="sm",
                                          icon="copy",
                                          on_click=self._on_copy_emails)
        actions.addWidget(self.copy_btn)

        self.export_btn = components.button("Export…", kind="secondary",
                                            size="sm", icon="external",
                                            on_click=self._on_export_clicked)
        actions.addWidget(self.export_btn)

        self.suppress_btn = components.button("Suppress…", kind="danger",
                                              size="sm", icon="minus",
                                              on_click=self._on_suppress_clicked)
        actions.addWidget(self.suppress_btn)

        self.remove_btn = components.button("Remove…", kind="danger", size="sm",
                                            icon="trash",
                                            on_click=self._on_remove_clicked)
        actions.addWidget(self.remove_btn)
        actions.addStretch()

        # Beside the buttons that write it. "Copied 3 addresses" and "Auditing
        # 40 sites…" are answers to something that was just pressed here, and
        # they used to be reported at the far end of a row above the table.
        self.lead_status = components.body_label("", tone="tertiary")
        self.lead_status.setWordWrap(False)
        actions.addWidget(self.lead_status)

        self.audit_btn = components.button("Audit all", kind="primary",
                                           size="lg", icon="search",
                                           on_click=self._on_audit_clicked)
        actions.addWidget(self.audit_btn)
        return actions

    def _make_table(self, t):
        """The lead table, from the spec at the top of this file.

        `sortable=False` and the header driven by hand, because the two badge
        columns are painted by a delegate from data on the item rather than
        from its text, and `QTableWidget.sortItems` reorders items under a
        painted row. Sorting the records and rebuilding is one pass over a list
        this screen already holds.

        Built from the fields the user has left switched on, not from the whole
        spec with the unwanted ones hidden afterwards. `_take_widths` shares
        the window out between the columns it was handed, so a hidden column
        that is still in the spec keeps its share and leaves a dead band beside
        the last one. Measured at 1280 with City, Category and Status off:
        hidden in place the four remaining columns paint 780px of a 1278px
        viewport, leaving 498px of empty table; handed `components.table()` as
        a four-column spec they paint 1276px and leave 2.
        """
        self._col_of = {field: index for index, field in enumerate(self._fields)}
        table = components.table([_LEAD_COLUMNS[field] for field in self._fields],
                                 density=t.density, sortable=False)
        # New table, new delegates, so the labels cached off the old ones are
        # answers about widgets that no longer exist.
        self._badge_text = {}
        self._painted = set()
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # One ground and a hairline between rows rather than a zebra.
        # `components.table()` alternates the row colour for every table it
        # builds, and on this one that lands on top of the bottom border the
        # sheet already gives `QTableWidget::item` — two separations per row,
        # and the louder of the two moves the ground under the status pill and
        # the score badge, which are the two cells on the row that carry a
        # ground of their own.
        table.setAlternatingRowColors(False)
        for field, build in ((_COL_SCORE, components.score_badge),
                             (_COL_STATUS, components.status_pill)):
            column = self._col_of.get(field, -1)
            if column >= 0:
                table.setItemDelegateForColumn(column, _BadgeDelegate(build, table))
        table.customContextMenuRequested.connect(self._show_lead_menu)
        table.cellDoubleClicked.connect(self._on_lead_double_clicked)
        table.itemSelectionChanged.connect(self._on_lead_selection_changed)
        # Both halves of "which rows exist as cells": the scrollbar moving is
        # the user asking for rows that are not built yet, and the viewport
        # growing is the same question asked by a drag of the window edge.
        table.verticalScrollBar().valueChanged.connect(self._on_lead_scrolled)
        table.viewport().installEventFilter(self)

        head = table.horizontalHeader()
        head.setSectionsClickable(True)
        head.setSortIndicatorShown(True)
        head.setSortIndicator(self._col_of.get(self._sort[0], 0), self._sort[1])
        head.sectionClicked.connect(self._on_header_clicked)
        return table

    # ── Campaign tab ─────────────────────────────────────────────────────────

    def _build_campaign_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)
        box.addWidget(_section_header("Campaign", _SECTION_NOTES["Campaign"],
                                      t=t))
        columns = _cols(margin="0", spacing="4", t=t)
        columns.addWidget(self._build_campaign_column(t))
        columns.addWidget(self._build_preview_column(t), stretch=1)
        box.addLayout(columns, stretch=1)
        return page

    def _build_campaign_column(self, t) -> QWidget:
        left = _rows(margin="0", spacing="3", t=t)

        # First, because it decides what every card under it is about: the
        # template list, the preview, the limits in the Schedule card and the
        # transport a Prepare writes for all follow it. A campaign is
        # single-channel — a lead reached by email is not also messaged on
        # WhatsApp unless the user starts a WhatsApp campaign for it by name,
        # because being contacted twice on two channels inside a week is what
        # gets a sender reported.
        channel_card = components.card(title="Channel")
        picker = _cols(margin="0", spacing="2", t=t)
        self.channel_group = QButtonGroup(self)
        self.channel_group.setExclusive(True)
        for index, (key, label) in enumerate(_CHANNELS):
            tab = components.button(label, kind="tab", size="sm")
            tab.setCheckable(True)
            tab.setChecked(key == self._channel)
            self.channel_group.addButton(tab, index)
            picker.addWidget(tab)
        picker.addStretch()
        self.channel_group.idClicked.connect(self._on_channel_picked)
        channel_card.body_layout.addLayout(picker)
        self.channel_note = components.hint("", max_chars=_COLUMN_CH)
        channel_card.body_layout.addWidget(self.channel_note)
        self.channel_warning = components.body_label("", tone="danger",
                                                     max_chars=_COLUMN_CH)
        self.channel_warning.hide()
        channel_card.body_layout.addWidget(self.channel_warning)
        left.addWidget(channel_card)

        template_card = components.card(title="Template")
        self.template_combo = _combo(t)
        self._refresh_templates()
        self.template_combo.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        template_card.body_layout.addWidget(self.template_combo)
        self.followup_hint = components.hint("")
        template_card.body_layout.addWidget(self.followup_hint)
        left.addWidget(template_card)

        who_card = components.card(title="Previewing")
        self.preview_combo = _combo(t)
        self.preview_combo.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        who_card.body_layout.addWidget(self.preview_combo)
        who_card.body_layout.addWidget(components.hint(
            "Selecting a lead on the Leads tab previews that one."))
        left.addWidget(who_card)

        profile_card = components.card(title="Sender profile")
        self.profile_summary = components.body_label("", tone="secondary",
                                                     max_chars=_COLUMN_CH)
        profile_card.body_layout.addWidget(self.profile_summary)
        self.profile_problem = components.body_label("", tone="danger",
                                                     max_chars=_COLUMN_CH)
        self.profile_problem.hide()
        profile_card.body_layout.addWidget(self.profile_problem)
        self.profile_fix_btn = components.button(
            "Fix in Settings", kind="danger", size="sm", icon="gear",
            on_click=self.settings_signal.emit)
        profile_card.body_layout.addWidget(self.profile_fix_btn,
                                           alignment=Qt.AlignLeft)
        left.addWidget(profile_card)

        plan_card = components.card(title="Schedule")
        self.plan_summary = components.body_label("", tone="secondary",
                                                  max_chars=_COLUMN_CH)
        plan_card.body_layout.addWidget(self.plan_summary)
        self.plan_warning = components.body_label("", tone="danger",
                                                  max_chars=_COLUMN_CH)
        self.plan_warning.hide()
        plan_card.body_layout.addWidget(self.plan_warning)
        # Who "prepare" is about to queue, on screen before it is pressed. The
        # button acts on the selection when there is one and on everything the
        # filters show when there is not, and it looked identical either way.
        self.plan_targets = components.hint("", max_chars=_COLUMN_CH)
        plan_card.body_layout.addWidget(self.plan_targets)
        self.plan_progress = _thin_bar(t)
        self.plan_progress.hide()
        plan_card.body_layout.addWidget(self.plan_progress)

        # Stacked, not side by side: the two labels want more than the column
        # is wide, so a row clipped the primary button mid-glyph. Full width
        # also stops the label ever deciding the layout — a wider font or a
        # translation cannot squeeze either button again.
        #
        # A calendar on it and not a paper plane: preparing a campaign queues
        # it against a clock and sends nothing, and the one control on this
        # screen that mails a stranger is the only one allowed to look like it.
        self.prepare_btn = components.button("Prepare campaign", kind="primary",
                                             size="lg", icon="calendar",
                                             on_click=self._on_prepare_clicked)
        plan_card.body_layout.addWidget(self.prepare_btn)
        self.cancel_prepare_btn = components.button("Cancel preparation", kind="secondary",
                                                    size="lg", icon="x-circle",
                                                    on_click=self._on_cancel_prepare_clicked)
        self.cancel_prepare_btn.hide()
        plan_card.body_layout.addWidget(self.cancel_prepare_btn)
        self.goto_sending_btn = components.button(
            "Open Sending", kind="secondary", size="lg", icon="chevron-right",
            on_click=lambda: self._goto_tab(2))
        self.goto_sending_btn.hide()
        plan_card.body_layout.addWidget(self.goto_sending_btn)
        left.addWidget(plan_card)
        left.addStretch()

        # The four cards want more height than the window's own minimum leaves
        # this column, so at 880x620 the Schedule card used to be handed less
        # than its *minimum* — the plan's last line vanished and the two buttons
        # were pushed into each other. Nothing here can be made shorter without
        # dropping something the user has to read, so the column scrolls
        # instead: full height when there is room, a scrollbar when there is
        # not, and never a silent clip.
        holder = QWidget()
        holder.setLayout(left)
        width = max(_measure(QFontMetrics(holder.font()), _COLUMN_CH)
                    + t.space["4"] * 2,
                    self.prepare_btn.sizeHint().width(),
                    self.goto_sending_btn.sizeHint().width())
        holder.setFixedWidth(width)

        self.campaign_scroll = QScrollArea()
        self.campaign_scroll.setWidget(holder)
        self.campaign_scroll.setWidgetResizable(True)
        self.campaign_scroll.setFrameShape(QFrame.NoFrame)
        self.campaign_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.campaign_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # The bar overlays nothing in Qt, so the viewport has to be wide enough
        # for the column plus the bar or the cards lose those pixels the moment
        # it appears.
        self.campaign_scroll.setFixedWidth(
            width + self.campaign_scroll.verticalScrollBar().sizeHint().width())
        self.campaign_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        return self.campaign_scroll

    def _build_preview_column(self, t) -> QWidget:
        """The message as it will be delivered, at the width an inbox shows it.

        Capped, and that is the finding: on a 2560px window this pane measured
        2154px, so the copy being checked before it goes to a stranger was laid
        out at three and a half times the measure it will actually be read at.
        The holder takes the cap and the row round it takes the slack, so the
        page still fills the window and the paper does not.
        """
        holder = QWidget()
        right = _rows(holder, margin="0", spacing="2", t=t)

        subject_row = _cols(margin="0", spacing="2", t=t)
        # Named, because on WhatsApp there is no subject to caption. The counter
        # beside it changes units with the channel too — an inbox measures a
        # subject in characters and a chat bubble measures a message in words.
        self.preview_kind = components.section_label("Subject")
        subject_row.addWidget(self.preview_kind)
        subject_row.addStretch()
        self.subject_count = components.body_label("", tone="tertiary")
        self.subject_count.setWordWrap(False)
        subject_row.addWidget(self.subject_count)
        right.addLayout(subject_row)

        self.subject_label = components.heading("—", "h2")
        self.subject_label.setWordWrap(True)
        right.addWidget(self.subject_label)

        meta_row = _cols(margin="0", spacing="2", t=t)
        self.preview_meta = _ElidedLabel()
        meta_row.addWidget(self.preview_meta, stretch=1)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        # Kept as a list as well as in the group: a WhatsApp message has no HTML
        # alternative, so the pair is hidden whole rather than left offering a
        # view of something that does not exist.
        self.view_tabs = []
        for index, label in enumerate(("Text", "HTML")):
            tab = components.button(label, kind="tab", size="sm")
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            self.view_group.addButton(tab, index)
            self.view_tabs.append(tab)
            meta_row.addWidget(tab)
        self.view_group.idClicked.connect(lambda _i: self._refresh_preview())
        right.addLayout(meta_row)

        self.preview = QTextBrowser()
        self.preview.setObjectName("email_paper")
        self.preview.setOpenExternalLinks(False)
        self.preview.setOpenLinks(False)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.preview, stretch=1)
        self.preview_hint = components.hint("")
        right.addWidget(self.preview_hint)

        # Held, because the measure is the channel's. An inbox renders a message
        # at roughly 76 characters and a phone at roughly 40, and previewing
        # either at the other's width is previewing something nobody is sent.
        self.preview_holder = holder
        self._cap_preview()
        return holder

    def _cap_preview(self) -> None:
        """Cap the preview at the measure the current channel is read at."""
        holder = self.preview_holder
        chars = _BUBBLE_CH if self._channel == WHATSAPP else _PAPER_CH
        holder.setMaximumWidth(_measure(QFontMetrics(holder.font()), chars)
                               + _PAPER.space["5"] * 2)

    # ── Sending tab ──────────────────────────────────────────────────────────

    def _build_sending_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)
        box.addWidget(_section_header("Sending", _SECTION_NOTES["Sending"],
                                      t=t))

        pick = _cols(margin="0", spacing="2", t=t)
        pick.addWidget(components.section_label("Campaign"))
        self.campaign_combo = _combo(t)
        self.campaign_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.campaign_combo.setMaximumWidth(
            _measure(QFontMetrics(page.font()), _COLUMN_CH))
        self.campaign_combo.currentIndexChanged.connect(self._on_campaign_changed)
        pick.addWidget(self.campaign_combo)
        pick.addStretch()

        # Built here and replaced by `_refresh_mode`: the one control that mails
        # real strangers has to be the filled danger button and nothing else,
        # and in a dry run it is not that control at all.
        self.start_btn = components.button("Start sending", kind="primary",
                                           size="lg",
                                           icon=_START_ICONS["primary"],
                                           on_click=self._on_start_clicked)
        self.start_row = pick
        self.start_at = pick.count()
        pick.addWidget(self.start_btn)
        # A clock, because the clock is the whole of what this button waives:
        # the window and the gap between messages go, the daily caps stay.
        self.send_now_btn = components.button(
            "Send now", kind="secondary", size="lg", icon="clock",
            on_click=self._on_send_now_clicked)
        self.send_now_btn.setToolTip(
            "Ignore the sending window and the gap between messages. Daily caps "
            "still apply.")
        pick.addWidget(self.send_now_btn)
        self.pause_btn = components.button("Pause", kind="secondary", size="lg",
                                           icon="pause",
                                           on_click=self._on_pause_clicked)
        self.pause_btn.setEnabled(False)
        pick.addWidget(self.pause_btn)
        self.stop_btn = components.button("Stop", kind="danger", size="lg",
                                          icon="stop",
                                          on_click=self._on_stop_clicked)
        self.stop_btn.setEnabled(False)
        pick.addWidget(self.stop_btn)
        box.addLayout(pick)

        self.send_note = components.hint("")
        box.addWidget(self.send_note)

        progress_row = _cols(margin="0", spacing="3", t=t)
        self.send_status = components.body_label("Nothing queued yet",
                                                 tone="secondary")
        self.send_status.setWordWrap(False)
        progress_row.addWidget(self.send_status)
        progress_row.addStretch()
        self.next_send_label = components.body_label("", tone="tertiary")
        self.next_send_label.setWordWrap(False)
        progress_row.addWidget(self.next_send_label)
        box.addLayout(progress_row)

        # Why the queue is not moving, when it is not moving. A run that has
        # stalled used to report itself as "ready to send" over a frozen queue,
        # which is the one sentence that keeps a user waiting for something that
        # was never going to happen.
        self.send_reason = components.body_label("", tone="warning")
        self.send_reason.hide()
        box.addWidget(self.send_reason)

        self.send_progress = _thin_bar(t)
        box.addWidget(self.send_progress)

        middle = _cols(margin="0", spacing="4", t=t)
        accounts_card = components.card(title="Accounts today")
        self.accounts_holder = _rows(margin="0", spacing="3", t=t)
        accounts_card.body_layout.addLayout(self.accounts_holder)
        accounts_card.body_layout.addStretch()
        accounts_card.setFixedWidth(
            _measure(QFontMetrics(page.font()), _COLUMN_CH) + t.space["4"] * 2)
        middle.addWidget(accounts_card)

        middle.addWidget(self._build_console(t), stretch=1)
        box.addLayout(middle, stretch=1)
        self._repaint_log()

        box.addWidget(components.hint(
            "Sends are spaced by a random gap inside your sending window and "
            "stop at each account's daily cap. Closing the app pauses the run; "
            "the queue survives and picks up where it left off."))
        return page

    def _build_console(self, t) -> QWidget:
        """The activity log, as the console `ui/components.py` already draws.

        What it replaces: a bare QListWidget wearing `saved_list`, four hundred
        proportional 13px lines tinted three ways through `setForeground`, the
        stamp in the same ink as the message it stamps, and no way to get any
        of it off the screen and into a bug report. `log_console()` is
        monospace, marks every line with its level's own drawn glyph in that
        level's family, and carries the two verbs anybody actually does with a
        log. `_LogLineDelegate` adds the one thing it leaves to a caller.

        Three of its settings are this panel's own. The console elides and this
        one wraps, because at the 880px window minimum the panel is 375px wide
        and a business name is wider than that — elided, the end of a failure
        is off the right-hand edge of a list with no horizontal scrollbar to
        reach it with. Uniform item sizes go with the wrap, since a wrapped
        line is two rows tall and the one under it is one. And selection stays
        off: a log line is read and double-clicked, never picked, and Copy
        takes the whole log rather than a selection of it.
        """
        console = components.log_console(
            title="Activity", limit=_LOG_LIMIT,
            placeholder="Nothing sent yet. Press Start and each message "
                        "appears here as it goes out.")
        listing = console.list
        listing.setSelectionMode(QAbstractItemView.NoSelection)
        listing.setWordWrap(True)
        listing.setUniformItemSizes(False)
        listing.setTextElideMode(Qt.ElideNone)
        listing.setItemDelegate(_LogLineDelegate(t, listing))
        console.activated.connect(self._on_log_activated)
        # Clear empties the console, and the screen keeps the record the
        # console is restored from after a rebuild — so it has to hear about
        # it. Without this the next palette change puts back, line for line,
        # the log the user had just cleared.
        console.clear_button.clicked.connect(self._on_log_cleared)
        self.log_panel = console
        # The list keeps a name of its own because everything that reads a line
        # back asks the list — the console is the chrome around it.
        self.log_list = listing
        return console

    # ── Stats tab ────────────────────────────────────────────────────────────

    # What each tile counts, and the sentence that says what the number means.
    # Six numbers with six one-word captions were six numbers nobody could act
    # on; the hint is what turns a count into a finding.
    _TILES = (
        ("queued", "Queued", "info", "Built and waiting for its slot."),
        ("sent", "Sent", "accent", "Delivered to the address, not yet answered."),
        # Hidden at zero, which is every campaign that has never been rehearsed.
        # It exists because a dry run moved no tile at all: five hundred
        # messages were built, Sent stayed on 0, and the screen's answer to
        # "did that work?" was six zeros.
        ("rehearsed", "Rehearsed", "warning",
         "Built by a dry run and not sent. Still owed to the lead."),
        ("failed", "Failed", "danger", "The server refused it. Check the account."),
        ("replied", "Replied", "accent", "Someone wrote back. This is the number that matters."),
        ("bounced", "Bounced", "danger", "The address does not exist. It is suppressed."),
        ("skipped", "Skipped", "warning", "Not sent: suppressed, already contacted, or unusable."),
    )

    def _build_stats_page(self, t) -> QWidget:
        page = QWidget()
        box = _rows(page, margin="0", spacing="3", t=t)

        self.stats_campaign = components.body_label("", tone="tertiary")
        self.stats_campaign.setWordWrap(False)
        box.addWidget(_section_header("Stats", _SECTION_NOTES["Stats"],
                                      actions=(self.stats_campaign,), t=t))

        # One width for every tile, four to a row, and the slack outside them
        # rather than inside. Sharing the row between the tiles makes each one
        # a 400px box round a two-digit number at 2560; letting each size
        # itself to its own sentence made them 81, 160, 146, 216, 161 and 216px
        # at 1080, which is six shapes for six numbers that are read as a set.
        self.tiles_grid = QGridLayout()
        self.tiles_grid.setContentsMargins(t.space["0"], t.space["0"],
                                           t.space["0"], t.space["0"])
        self.tiles_grid.setSpacing(t.space["3"])
        self.tiles_grid.setColumnStretch(_TILES_PER_ROW, 1)
        box.addLayout(self.tiles_grid)

        width = (_measure(QFontMetrics(page.font()), _TILE_CH)
                 + t.space["3"] * 2 + components.BORDER * 2)
        self.tiles: dict[str, QFrame] = {}
        self._tiles_shown = ()
        for key, caption, tone, note in self._TILES:
            tile = components.stat_tile(caption, "0", tone=tone, hint=note)
            # A cap and not a fixed width. Capped, four tiles and their gaps
            # want 828px and the pane is 824 at the smallest window this app
            # opens at with the rail collapsed, so a fixed width would put the
            # fourth number four pixels past the edge of the page; a cap gives
            # every tile the same 198px wherever there is room for it and lets
            # the row narrow evenly where there is not.
            tile.setMaximumWidth(width)
            self.tiles[key] = tile
        # Every tile onto the grid once, whatever it is about to say, because
        # the grid is what parents them: a tile that has never been in a layout
        # has no parent widget at all, and showing one later would put a stat
        # tile on screen as a window of its own. The pass under it takes the
        # ones with nothing to say back off, now that they have a parent to be
        # hidden inside.
        self._flow_tiles(tuple(self.tiles))
        self._layout_tiles()

        # Not a tile, because it is not a count of messages: it is how many of
        # them say anything about the business they are addressed to. A user
        # reading six numbers has no way to tell two hundred personalised
        # emails from two hundred form letters, and that is the difference
        # between outreach and spam.
        self.personal_note = components.body_label("", tone="secondary")
        box.addWidget(self.personal_note)

        bars_card = components.card(title="Volume by day")
        self.day_bars = _DayBars(t)
        bars_card.body_layout.addWidget(self.day_bars)
        self.bars_empty = components.hint(
            "Nothing scheduled yet. Prepare a campaign and the days it spans "
            "appear here, filling in as messages go out.")
        bars_card.body_layout.addWidget(self.bars_empty)
        box.addWidget(bars_card)

        # The one surface in the app that shows a message rather than counting
        # one. Six tiles could say two hundred were sent and none of them could
        # say what any of them said, so "did that go out with the right footer?"
        # had no answer inside MapHarvest at all.
        sent_head = _cols(margin="0", spacing="2", t=t)
        sent_head.addWidget(components.section_label("Sent mail"))
        self.sent_count = components.body_label("", tone="tertiary")
        self.sent_count.setWordWrap(False)
        sent_head.addWidget(self.sent_count)
        sent_head.addStretch()
        self.open_sent_btn = components.button(
            "Open message", kind="secondary", size="sm", icon="mail",
            on_click=self._on_open_sent_clicked)
        sent_head.addWidget(self.open_sent_btn)
        box.addLayout(sent_head)

        self.sent_list = QListWidget()
        self.sent_list.setObjectName("saved_list")
        self.sent_list.setWordWrap(True)
        self.sent_list.itemDoubleClicked.connect(self._on_sent_item_activated)
        self.sent_list.currentItemChanged.connect(
            lambda *_a: self._refresh_sent_actions())
        sent_row = _cols(margin="0", spacing="3", t=t)
        sent_row.addWidget(self.sent_list, stretch=1)
        sent_row.addWidget(components.hint(
            "Open one to read exactly what left this machine — the subject, the "
            "body, the footer and the headers, as they were handed to Gmail. "
            "Stored at the moment of sending, so editing a template afterwards "
            "does not rewrite the record."), stretch=1)
        box.addLayout(sent_row, stretch=1)

        supp_head = _cols(margin="0", spacing="2", t=t)
        supp_head.addWidget(components.section_label("Suppression list"))
        self.supp_count = components.body_label("", tone="tertiary")
        self.supp_count.setWordWrap(False)
        supp_head.addWidget(self.supp_count)
        supp_head.addStretch()
        self.unsuppress_btn = components.button(
            "Remove selected", kind="secondary", size="sm", icon="minus",
            on_click=self._on_unsuppress_clicked)
        supp_head.addWidget(self.unsuppress_btn)
        box.addLayout(supp_head)

        # An address and a reason, and no more room than those need: the list
        # used to take every pixel the window had for a column of 30-character
        # strings.
        self.supp_list = QListWidget()
        self.supp_list.setObjectName("saved_list")
        self.supp_list.setWordWrap(True)
        self.supp_list.setMaximumWidth(
            _measure(QFontMetrics(page.font()), _SUPPRESSION_CH))
        supp_row = _cols(margin="0", spacing="3", t=t)
        supp_row.addWidget(self.supp_list)
        supp_row.addWidget(components.hint(
            "Every address here is permanently excluded from planning and "
            "sending, follow-ups included. Remove one only if the person asked "
            "to be contacted again."), stretch=1)
        box.addLayout(supp_row, stretch=1)
        return page

    # ── The theme, live ──────────────────────────────────────────────────────

    def restyle(self) -> None:
        """Wear the palette the app is in now.

        Every component resolves its colours in Python at build time and writes
        them into its own stylesheet, which beats the application sheet — so a
        repolish alone leaves this screen in the palette it was constructed in.
        The state worth carrying across is small: which tab is open, what is in
        the filter box, which campaign is picked, and the activity log, which is
        the one thing on this screen that cannot be read back off the database.

        `_redraw` and not `refresh`, and that distinction is the whole reason
        the two are separate: a theme change is not a moment to go back to the
        settings file. A campaign half set up in memory — an account being
        tried, a switch thrown from a dialog and not yet saved — would be
        rolled back by a repaint, which is the last thing a repaint may do.

        Deferred while this screen is off screen, and that is the second
        finding. Picking a theme or a density asks every built screen to do
        this, so the click paid for four rebuilds and got one back: 913ms of it
        was this screen, rebuilding four tab pages and re-reading the store for
        a window nobody was looking at. `showEvent` spends the debt at the one
        moment it buys the user anything.
        """
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        self._restyle_now()

    def _restyle_now(self) -> None:
        """The rebuild itself, once there is somebody to show it to.

        Painting is held off across the whole of it. Every `addWidget` into a
        visible tree schedules a repaint of what it lands on, and a rebuild is
        two hundred of them. That, the leads coming from memory rather than
        from the store, and the tab not being refreshed twice took a rebuild on
        a 500-lead store from 1,543ms to 541ms.

        The tab is restored by moving the stack rather than through `_goto_tab`,
        because `_redraw` has just refreshed all four pages and
        `_on_tab_changed` would run the whole of one of them a second time.
        """
        tab = self.pages.currentIndex()
        search, status = self._search, self.status_filter.currentIndex()
        view = self.view_group.checkedId()

        self.setUpdatesEnabled(False)
        self._restoring = True
        try:
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
            self.status_filter.setCurrentIndex(max(0, status))
            for button in self.view_group.buttons():
                button.setChecked(self.view_group.id(button) == max(0, view))
            # `reload=False`: the records are already in hand and a palette has
            # nothing to say about them, so the new widgets are filled from the
            # list this screen is holding rather than from a fresh query.
            self._redraw(reload=False)
            if search:
                self.lead_search.setText(search)
            self.pages.setCurrentIndex(max(0, min(len(self.TABS) - 1, tab)))
        finally:
            self._restoring = False
            self.setUpdatesEnabled(True)
        self._refresh_view_button()
        self._tell_shell(self.pages.currentIndex())

    # ── Public API ───────────────────────────────────────────────────────────

    def load_from_results(self, records: list[dict]) -> None:
        """Take the rows the Results screen just scraped into the lead pool."""
        try:
            leads = [self._lead_from_record(r, "scrape") for r in (records or [])
                     if isinstance(r, dict)]
            self._import_leads(leads, "the last scrape")
        except Exception:
            self._toast("Could not read those results.", tone="danger")

    def absorb_scrape(self, records: list) -> None:
        """Keep a finished scrape without stealing the screen.

        `load_from_results` is the hand-off the user asked for and says so in a
        toast; this is the one nobody asked for, so it saves and stays quiet.
        Same upsert underneath, so a business scraped twice is still one lead
        and an audit already done for it survives.
        """
        try:
            leads = [self._lead_from_record(r, "scrape") for r in (records or [])
                     if isinstance(r, dict)]
            leads = [lead for lead in leads if _text_of(lead.get("email")).strip()]
            if not leads:
                return
            for lead in leads:
                _db.upsert_lead(self.conn, lead)
            if self.isVisible():
                self._redraw()
        except Exception:
            pass

    def refresh(self) -> None:
        """Re-read settings and the database and redraw everything."""
        try:
            self.settings = _settings.load_settings()
            self._redraw()
        except Exception:
            pass

    def _redraw(self, *, reload: bool = True) -> None:
        """Everything `refresh` does except going back to the settings file.

        `reload=False` keeps the leads this screen is already holding. It is
        for a rebuild that changed nothing about the data — a palette, a
        density — where a fresh `list_leads` and a discarded per-lead cache buy
        the same rows at the cost of the query and the audit-derived work
        behind every Headline gap cell.
        """
        self._refresh_mode()
        self._refresh_templates()
        self._refresh_campaigns()
        if reload:
            self._reload_leads()
        else:
            self._repaint_leads()
        self._refresh_profile()
        self._refresh_accounts()
        self._refresh_stats()
        self._refresh_plan_summary()
        self._publish_state()

    # ── Leads: import ────────────────────────────────────────────────────────

    def _lead_from_record(self, record: dict, source: str) -> dict:
        """One scrape row mapped onto the `leads` columns."""
        return {
            "email": record.get("email"),
            "name": record.get("name"),
            "website": record.get("website"),
            "phone": record.get("phone"),
            "city": record.get("city") or record.get("area") or record.get("_area"),
            "category": record.get("category") or record.get("domain") or record.get("_domain"),
            "rating": record.get("rating"),
            "maps_link": record.get("maps_link") or record.get("_href"),
            "source": source,
        }

    def _import_leads(self, leads: list, origin: str, dropped: int = 0) -> None:
        """Take a batch in, and account for every row of it.

        Every count is reported, because the one that was not is the one that
        mattered: a spreadsheet whose email column is half empty imported
        silently, and the campaign built from it was quietly a third the size
        the user thought they had bought.
        """
        known = {_text_of(lead.get("email")).strip().lower()
                 for lead in self._leads}
        added = merged = 0
        for lead in leads:
            email = _text_of(lead.get("email")).strip().lower()
            if "@" not in email:
                dropped += 1
                continue
            if not _db.upsert_lead(self.conn, lead):
                dropped += 1
                continue
            if email in known:
                merged += 1
            else:
                added += 1
                known.add(email)

        self._reload_leads()
        self._goto_tab(0)
        if not added and not merged:
            self._toast("Nothing in %s had an email address, so there is nobody "
                        "to contact yet." % origin, tone="warning")
            return

        parts = ["Imported %s from %s." % (_plural(added, "lead"), origin)]
        if merged:
            parts.append("%s already on the list %s updated."
                         % (_plural(merged, "lead"), "was" if merged == 1 else "were"))
        if dropped:
            parts.append("%d %s no email address and %s left out."
                         % (dropped, "row had" if dropped == 1 else "rows had",
                            "was" if dropped == 1 else "were"))
        parts.append("Audit them next to score the opportunity.")
        self._toast("  ".join(parts),
                    tone="warning" if dropped else "success")

    def _on_import_csv(self) -> None:
        start = self.settings.get("export_dir") or os.path.expanduser("~")
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import leads", start, "CSV files (*.csv);;All files (*)")
        if not path:
            return
        leads, dropped, problem = self._read_csv(path)
        if problem:
            self._toast(problem, tone="danger")
            return
        self._import_leads(leads, os.path.basename(path), dropped)

    def _read_csv(self, path: str) -> tuple:
        """Rows from a CSV, mapped onto lead columns. (leads, dropped, problem).

        `dropped` is the count this used to throw away without a word: a row
        with no address in the email column cannot be contacted, and until now
        the only evidence of it was a lead count that did not match the file.
        """
        dropped = 0
        try:
            # utf-8-sig because a spreadsheet export leads with a BOM, which
            # would otherwise turn the first header into "﻿email".
            with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
                reader = csv.DictReader(handle)
                headers = {_norm_key(h): h for h in (reader.fieldnames or []) if h}
                mapping = {_CSV_ALIASES[key]: raw for key, raw in headers.items()
                           if key in _CSV_ALIASES}
                if "email" not in mapping:
                    return [], 0, ("That file has no email column, so none of it "
                                   "can be contacted. Expected a header called "
                                   "email.")
                leads = []
                for index, row in enumerate(reader):
                    if index >= _CSV_MAX_ROWS:
                        break
                    lead = {field: _text_of(row.get(column)).strip()
                            for field, column in mapping.items()}
                    lead["source"] = "csv"
                    if "@" in lead.get("email", ""):
                        leads.append(lead)
                    else:
                        dropped += 1
        except OSError as exc:
            return [], 0, "Could not read that file: %s" % exc
        except (csv.Error, UnicodeError):
            return [], 0, "That file is not readable as CSV."
        if not leads:
            return [], dropped, ("None of the %s in that file had an email "
                                 "address, so there is nobody to contact."
                                 % _plural(dropped, "row"))
        return leads, dropped, ""

    # ── Leads: table ─────────────────────────────────────────────────────────

    def _reload_leads(self) -> None:
        """Re-read the store, and keep what the store did not change.

        The three derived caches used to be emptied wholesale here, so
        suppressing one lead out of five thousand re-decoded five thousand
        audit blobs and re-asked `core.templates` five thousand times: 76ms of
        the reload, for one row that moved, and it is most of why the end of an
        audit run still cost 246ms after the table itself stopped being
        rebuilt. Every lead now carries a stamp of
        the fields those caches are derived from, and only the leads whose
        stamp moved are forgotten.

        The stamp includes what the answers depend on beside the record — the
        sender profile and the settings `core.templates.personalisation` reads
        — because a user who fills in their company name and comes back has
        changed what every one of those answers is.
        """
        self._leads = _db.list_leads(self.conn)
        rules = _rules_stamp(self.settings)
        stamps = {}
        if rules != self._rules_stamp:
            self._generic.clear()
            self._gaps.clear()
            self._blobs.clear()
            self._rules_stamp = rules
            for lead in self._leads:
                stamps[_int_of(lead.get("id"))] = _lead_stamp(lead)
        else:
            was = self._stamps
            for lead in self._leads:
                lead_id = _int_of(lead.get("id"))
                stamp = _lead_stamp(lead)
                stamps[lead_id] = stamp
                if was.get(lead_id) != stamp:
                    self._forget_lead(lead_id)
            for gone in self._generic.keys() - stamps.keys():
                self._forget_lead(gone)
        self._stamps = stamps
        self._repaint_leads()

    def _repaint_leads(self) -> None:
        """Draw the table from the leads in hand, without going to the store.

        Split out of `_reload_leads` for the one caller that has not changed
        the data: a theme rebuild needs new widgets holding the same records,
        and re-reading them would also throw away every Headline gap this
        screen has already worked out.
        """
        self._fill_table()
        self.lead_stack.setCurrentIndex(0 if self._leads else 1)
        self._refresh_preview_choices()

    def _fill_table(self) -> None:
        """Put the table back in the order the header says. One row per lead.

        Sorted here rather than by `QTableWidget.sortItems`, because the Score
        and Status columns are painted from data on the item rather than from
        its text and Qt's own sort reorders the items under a painted row. A
        re-sort is one pass over a list this screen is already holding.

        What it no longer does is build a cell for every lead, and that is the
        second performance finding closed. The table has a row per lead so the
        scrollbar tells the truth about the size of the list, but the cells are
        built only for the band around the viewport — see `_paint_window`. At
        5,000 leads the old pass spent 2,379ms of its 2,648ms inside
        `components._Table.add_row` building 35,000 `QTableWidgetItem`s, one
        insertRow at a time, for the roughly 20 rows an 800px-high window can
        show. Measured on the same store, back to back, median of nine runs in
        milliseconds:

            _fill_table    677 -> 29      a column-header sort    781 ->  32
            _reload_leads  806 -> 103     the end-of-run reload  1008 -> 104

        What is left of the reload is not here either: 49ms of its 103 is
        `core.outreach_db.list_leads` reading 5,000 rows with their audit blobs.

        One pass, and it still leaves behind everything the rest of the screen
        would otherwise walk the table for: which row each lead is on, how many
        leads each status holds, and how many would send a form letter. Those
        three are the difference between a button label costing a table walk
        and costing a dictionary lookup.
        """
        table = self.lead_table
        scroll_bar = table.verticalScrollBar()
        scroll_val = scroll_bar.value()
        field, order = self._sort
        self._leads.sort(key=lambda lead: self._sort_key(lead, field),
                         reverse=order == Qt.DescendingOrder)
        # Held off for the same reason `_restyle_now` holds it off: resizing
        # the table and re-deciding five thousand rows' visibility is five
        # thousand repaint requests against a viewport that is going to be
        # repainted once at the end anyway.
        table.setUpdatesEnabled(False)
        try:
            self._release_all()
            table.setRowCount(len(self._leads))
            rows: dict[int, int] = {}
            buckets: dict[str, int] = {}
            generic = 0
            for index, lead in enumerate(self._leads):
                status = _text_of(lead.get("status")).strip() or "new"
                buckets[status] = buckets.get(status, 0) + 1
                lead_id = _int_of(lead.get("id"))
                rows[lead_id] = index
                if self._gap_text(lead)[1]:
                    generic += 1
            self._row_of = rows
            self._buckets = buckets
            self._generic_count = generic
            table.horizontalHeader().setSortIndicator(
                self._col_of.get(field, 0), order)
        finally:
            table.setUpdatesEnabled(True)
        self._apply_filters()
        # Retaking the widths is also what clears a horizontal scrollbar left
        # behind by a hand-dragged column: the width the user set survives
        # until the next reload, and a reload is when the table gets its
        # geometry back from the spec. After the window is painted, not before
        # — `relayout` sizes a `fit` column against the rows it can see, and
        # before the paint there are none.
        table.relayout()
        scroll_bar.setValue(scroll_val)

    # ── Leads: the painted window ────────────────────────────────────────────

    def eventFilter(self, watched, event) -> bool:
        """A taller viewport is a request for rows that are not built yet."""
        if (event.type() == QEvent.Resize and hasattr(self, "lead_table")
                and watched is self.lead_table.viewport()):
            self._paint_window()
        return super().eventFilter(watched, event)

    def _on_lead_scrolled(self, _value: int) -> None:
        self._paint_window()

    def _band(self) -> tuple:
        """(first row, last row) of the model the window covers.

        Walked forward from the row Qt says is at the top of the viewport
        rather than computed from a row height, because rows the filter has
        hidden take no space and `rowAt` already knows that. The walk starts at
        the first row on screen, so a filter that leaves four leads out of five
        thousand does not cost a scan of the 4,996 above them.

        A band is not a list of rows to build: the rows inside it that the
        filter is hiding are not on screen and are not built. That distinction
        is the difference between a search that matches nothing costing 5,000
        `isRowHidden` calls and costing 35,000 items.
        """
        table = self.lead_table
        total = table.rowCount()
        if total <= 0:
            return 0, -1
        first = table.rowAt(0)
        if first < 0:
            first = 0
        wanted = max(1, table.viewport().height()
                     // max(1, table.verticalHeader().defaultSectionSize())) \
            + _WINDOW_PAD
        last, shown = first, 0
        for row in range(first, total):
            last = row
            if not table.isRowHidden(row):
                shown += 1
                if shown >= wanted:
                    break
        return max(0, first - _WINDOW_PAD), last

    def _paint_window(self) -> None:
        """Build the cells for the band, and give back the ones far outside it.

        The give-back half is what keeps a scroll from end to end of five
        thousand leads from quietly costing the same 35,000 items the old
        rebuild did — it would just have spread them over a minute of wheel.
        """
        table = self.lead_table
        if table.rowCount() <= 0:
            self._release_all()
            return
        top, bottom = self._band()
        for row in range(top, bottom + 1):
            if row not in self._painted and not table.isRowHidden(row):
                self._paint_row(row)
        far = [row for row in self._painted
               if row < top - _WINDOW_KEEP or row > bottom + _WINDOW_KEEP]
        for row in far:
            self._release_row(row)

    def _paint_row(self, row: int) -> None:
        """Build one row's cells, from the same `Cell`s `components` would take.

        The items are built here rather than through `components._Table.add_row`
        for one reason: `add_row` appends, and a window that starts at row 3,900
        needs to write row 3,900. Everything a cell carries still comes from
        `components` — `FULL_ROLE` and `SORT_ROLE` are its roles, the alignment
        is the `Column`'s own, and no colour is set here at all, because the
        two columns that carry one are painted by `_BadgeDelegate` from
        `status_pill()` and `score_badge()`. A `set_row(row, cells)` beside
        `add_row` is where the whole of this belongs; see the handover note.
        """
        table = self.lead_table
        lead = self._leads[row] if 0 <= row < len(self._leads) else None
        if lead is None:
            return
        for column, cell in enumerate(self._lead_cells(lead)):
            item = QTableWidgetItem(cell.text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(
                _ALIGNMENT.get(_LEAD_COLUMNS[self._fields[column]].align,
                               Qt.AlignLeft) | Qt.AlignVCenter)
            item.setData(components.FULL_ROLE, cell.text)
            if cell.sort is not None:
                item.setData(components.SORT_ROLE, cell.sort)
            if cell.tip:
                item.setToolTip(cell.tip)
            table.setItem(row, column, item)
        self._painted.add(row)
        self._set_badge(row, _COL_SCORE, _int_of(lead.get("opportunity_score")))
        self._set_badge(row, _COL_STATUS,
                        _text_of(lead.get("status")).strip() or "new")

    def _release_row(self, row: int) -> None:
        table = self.lead_table
        for column in range(table.columnCount()):
            table.takeItem(row, column)
        self._painted.discard(row)

    def _release_all(self) -> None:
        if self._painted:
            self.lead_table.clearContents()
            self._painted = set()

    def _repaint_row(self, row: int) -> None:
        """Rebuild a row that is on screen; forget one that is not.

        A row outside the band has no cells to correct, and it will be built
        from the record when it is scrolled to — so an audit landing on a lead
        four thousand rows down costs nothing at all.
        """
        if row in self._painted:
            self._paint_row(row)

    def _sort_key(self, lead: dict, field: int):
        if field == _COL_SCORE:
            return _int_of(lead.get("opportunity_score"))
        if field == _COL_GAP:
            return self._gap_text(lead)[0].lower()
        if field == _COL_SITE:
            # Blank is what a site that was read says, so ascending puts the
            # readable ones first and one click brings every failure to the
            # top, grouped by the way it failed.
            return self._site_text(lead)[0].lower()
        return _text_of(lead.get(_COL_KEYS.get(field, "name"))).strip().lower()

    def _on_header_clicked(self, column: int) -> None:
        field = self._fields[column] if 0 <= column < len(self._fields) \
            else _COL_NAME
        current, order = self._sort
        if field == current:
            order = (Qt.AscendingOrder if order == Qt.DescendingOrder
                     else Qt.DescendingOrder)
        else:
            order = Qt.DescendingOrder if field == _COL_SCORE else Qt.AscendingOrder
        self._sort = (field, order)
        self._fill_table()
        self._forget_view()

    def _gap_text(self, lead: dict, audit=None) -> tuple:
        """(Headline gap, form letter?, Site words, the crawl's sentence, its line).

        Five answers out of one decode, and cached against the lead's id,
        because working them out is a JSON decode and a
        `core.templates.personalisation` call and every pass over the table
        wanted some of them: the sort key, the row, the filter box and the
        tooltip. `audit` is the already-decoded blob when a caller has one, so
        the row build does not decode it twice.

        The last three ride here rather than in a cache of their own for the
        same reason: `_hidden_by` asks whether a lead's site could be read once
        per row on every filter pass, and a second `json.loads` per row per
        keystroke is the cost this cache exists to refuse.
        """
        lead_id = _int_of(lead.get("id"))
        answer = self._gaps.get(lead_id)
        if answer is None:
            audit = _loads(lead.get("audit_json")) if audit is None else audit
            gaps = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)]
            reason = self._generic_reason(lead, audit)
            site, phrase, raw = _site_failure(audit)
            gap = _text_of(gaps[0].get("title")).strip() if gaps else ""
            if not gap and reason:
                # This lead's email is three paragraphs that could have been
                # written before the crawl. That is what the column has to say,
                # because "no clear gap" reads as a thin prospect rather than
                # as a form letter — and when the crawl knows which way the
                # site failed, it says that rather than "could not be reached",
                # which is the difference between a row the user can act on and
                # a row they have to open something to understand.
                gap = "form letter — " + (
                    phrase if phrase and reason == "unreachable"
                    else _templates.generic_reason(reason))
            answer = (gap or ("not audited yet" if not audit else "no clear gap"),
                      bool(reason), site, phrase, raw)
            self._gaps[lead_id] = answer
        return answer

    def _site_text(self, lead: dict) -> tuple:
        """(four words, the crawl's sentence, its own line) for one lead."""
        return self._gap_text(lead)[2:]

    def _forget_lead(self, lead_id: int) -> None:
        """Drop everything derived from one lead's record, after it changed."""
        lead_id = _int_of(lead_id)
        self._generic.pop(lead_id, None)
        self._gaps.pop(lead_id, None)
        self._blobs.pop(lead_id, None)

    def _lead_cells(self, lead: dict) -> tuple:
        """One lead as `components.Cell`s, in the order the columns stand in.

        The whole spec is built and then cut down to the shown fields rather
        than being built per field, because six of the eight cost nothing and
        the two that do — the Headline gap and the score's gap count — are
        cached against the lead anyway.

        The record is deliberately not attached to the row through
        `Qt.UserRole`. Qt marshals a dict through a QVariant on every read, at
        50µs a call measured, and row `n` is `self._leads[n]` by construction —
        so `_lead_at` can answer from the list instead.
        """
        score = _int_of(lead.get("opportunity_score"))
        status = _text_of(lead.get("status")).strip() or "new"
        audit = _loads(lead.get("audit_json"))
        gaps = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)]
        gap, generic, site, phrase, raw = self._gap_text(lead, audit)

        every = (
            Cell(text=_text_of(lead.get("name")).strip() or "—",
                 sort=_text_of(lead.get("name")).strip().lower()),
            Cell(text=_text_of(lead.get("email")).strip()),
            Cell(text=_text_of(lead.get("city")).strip()),
            Cell(text=_text_of(lead.get("category")).strip()),
            Cell(text=site, sort=site,
                 tip=self._site_tip(lead, phrase, raw)),
            Cell(text="", sort=score,
                 tip="Opportunity %d of 100 — %s, %s" % (
                     score, components.score_band(score)[1],
                     _plural(len(gaps), "gap"))
                 if score > 0 else
                 # A lead whose site could not be read *was* audited. Saying
                 # "not audited yet" over its zero is the badge and the
                 # tooltip agreeing on something that did not happen, and it
                 # sends the user back to press Audit on a row that has
                 # already been crawled twice.
                 "Crawled, but %s — there is nothing to score until the "
                 "site can be read." % phrase if site
                 else "Not audited yet"),
            Cell(text=gap,
                 tip="Nothing is known about this business, so the email says "
                     "nothing about it. Filter to Generic email to review or "
                     "exclude these before sending." if generic else ""),
            Cell(text="", sort=status, tip=self._status_tip(status)),
        )
        return tuple(every[field] for field in self._fields)

    def _site_tip(self, lead: dict, phrase: str, raw: str) -> str:
        """What the Site cell says on hover: the address, the line, the cost.

        The raw line the crawl recorded is kept whole and shown here and
        nowhere else. Four words is what a column of forty rows can be scanned
        for; `URLError: <urlopen error [Errno 11001] getaddrinfo failed>` is
        what somebody chasing one site actually needs, and throwing it away to
        keep the cell short would mean the app knows something it will not say.
        """
        if not phrase:
            return ""
        where = _text_of(lead.get("website")).strip()
        lines = ["The crawl could not read %s — %s."
                 % (where or "this lead's site", phrase)]
        if raw and raw.lower() != phrase.lower():
            lines.append("It recorded: %s" % _clip(raw, 160))
        lines.append("Its email will say nothing about the business. Right-click "
                     "to retry the crawl, open the site, or take the lead out.")
        return "\n".join(lines)

    def _set_badge(self, row: int, field: int, key) -> None:
        """Put the value a badge column paints onto its cell.

        The two badge columns carry their value rather than their words: the
        delegate paints `components.status_pill()` and `score_badge()` from
        this, and the item text is the badge's own label so a screen reader and
        the filter box are told what the pill says. A field the user has
        switched off has no cell, and nothing to say.
        """
        column = self._col_of.get(field, -1)
        item = self.lead_table.item(row, column) if column >= 0 else None
        if item is None:
            return
        item.setData(_BADGE_ROLE, key)
        label = self._badge_label(field, key)
        item.setText(label)
        item.setData(components.FULL_ROLE, label)

    def _badge_label(self, field: int, key) -> str:
        """What the pill in `field`'s column reads for `key`, once per value.

        The delegate already keeps one widget per distinct value; this keeps
        the string it renders, so ten thousand cells do not each cross into Qt
        to read back a label that is one of fifteen.
        """
        cached = self._badge_text.get((field, key))
        if cached is None:
            delegate = self.lead_table.itemDelegateForColumn(
                self._col_of.get(field, -1))
            cached = delegate.badge(key).text() \
                if isinstance(delegate, _BadgeDelegate) else _text_of(key)
            self._badge_text[(field, key)] = cached
        return cached

    @staticmethod
    def _status_tip(status: str) -> str:
        spec = components.STATUS_PILLS.get(status)
        if spec is None:
            return "Status: %s" % status
        return {"outline": "Chosen, not sent: %s.",
                "subtle": "In flight: %s.",
                "solid": "Ended badly: %s."}.get(spec.fill, "%s.") % status

    def _generic_reason(self, lead: dict, audit: dict) -> str:
        """Why this lead's email would be a form letter, "" when it would not.

        Cached per lead: the answer comes from `core.templates`, so it is the
        same answer the send loop renders, and asking it once per row keeps the
        status filter free.

        A lead nobody has audited yet is not answered at all. Its email would be
        generic today, but the plan audits it first, and calling it a form
        letter before the crawl has run is a guess dressed as a fact.
        """
        lead_id = _int_of(lead.get("id"))
        if lead_id in self._generic:
            return self._generic[lead_id]
        reason = ""
        if audit:
            try:
                ok, reason = _templates.personalisation(
                    lead, audit, _loads(lead.get("ai_json")), self._profile(),
                    self.settings)
                reason = "" if ok else reason
            except Exception:
                reason = ""
        self._generic[lead_id] = reason
        return reason

    def _on_search_changed(self, text: str) -> None:
        self._search = _text_of(text).strip().lower()
        self._terms = _parse_query(self._search)
        self._apply_filters()
        self._forget_view()

    def _on_status_filter(self, _index: int) -> None:
        self._apply_filters()
        self._forget_view()

    def _wanted_status(self) -> str:
        return _STATUS_FILTERS[max(0, self.status_filter.currentIndex())][1]

    def _hidden_by(self, lead: dict, wanted: str) -> bool:
        """Would the filters as they stand hide this lead? One row's worth.

        `~failed` reads the same cached answer the Site column paints, and that
        is the whole of its change. It used to decode the audit blob per row
        per pass and ask only whether `error` was non-empty — one `json.loads`
        a row on every keystroke, and a predicate that missed a site marked
        unreachable with nothing written in `error`. `_gap_text` has already
        decoded the blob for the table; this asks it.
        """
        if wanted.startswith("~"):
            if wanted == "~failed":
                # Straight off the cache rather than through `_site_text`,
                # which is a call and a tuple slice per row. This runs once per
                # row on every keystroke: measured over 5,000 leads with this
                # filter on, a pass costs 6.1ms read this way and 27.3ms read
                # through the accessor.
                answer = self._gaps.get(_int_of(lead.get("id")))
                hide = not (answer[2] if answer else self._site_text(lead)[0])
            else:
                generic = bool(self._generic.get(_int_of(lead.get("id"))))
                hide = generic != (wanted == "~generic")
        else:
            status = _text_of(lead.get("status")).strip() or "new"
            hide = bool(wanted) and status != wanted
        return bool(hide or (self._terms and not self._matches(lead)))

    def _apply_filters(self) -> None:
        """Re-decide every row's visibility. The whole-table pass.

        Every row, and cheaply: `setRowHidden` on a row that holds no cells is
        the same call it is on a row that does, so the pass costs what it costs
        whether twenty rows are built or five thousand. Broken up at 5,000
        leads it is 17-19ms of `_hidden_by`, 2ms of `setRowHidden` to narrow to
        a quarter of the list and 16-19ms to widen back out, and under 1ms of
        window paint. What used to sit on top of it was the rebuild that
        produced the rows.

        For a change that reaches one row — an audit landing — `_refilter_row`
        does the same work for that row and adjusts the count, which is what
        turns an audit run from O(N²) back into O(N).
        """
        wanted = self._wanted_status()
        table = self.lead_table
        visible = 0
        table.setUpdatesEnabled(False)
        try:
            for row in range(table.rowCount()):
                hide = self._hidden_by(self._lead_at(row), wanted)
                table.setRowHidden(row, hide)
                visible += 0 if hide else 1
        finally:
            table.setUpdatesEnabled(True)

        self._visible = visible
        # The band moved: rows that were hidden a moment ago are now on screen
        # and have no cells yet.
        self._paint_window()
        self._refresh_lead_counts()
        self._refresh_lead_actions()

    def _refilter_row(self, row: int, lead: dict) -> None:
        """Re-decide one row, and keep the shown count in step with it."""
        table = self.lead_table
        if not 0 <= row < table.rowCount():
            return
        hide = self._hidden_by(lead, self._wanted_status())
        if hide == table.isRowHidden(row):
            return
        table.setRowHidden(row, hide)
        self._visible += -1 if hide else 1

    def _matches(self, lead: dict) -> bool:
        """Does everything in the filter box land somewhere in this lead?

        Every term, not the whole box as one substring, and that is the third
        half of the search finding. `toronto roofing` used to match nothing at
        all: the record says "Toronto" in one field and "Roofing contractor" in
        another, the two are joined by a newline so a needle cannot span them,
        and a single-substring match therefore had no way to ask for both. Two
        terms are two needles and each has to land somewhere.

        A term with a field in front of it is asked of that field alone, read
        off the record — which is also what lets the city and the category be
        searched at all, and what keeps the two badge columns searchable now
        that what they paint is a pill rather than a word.
        """
        blob = None
        for field, needle in self._terms:
            if field:
                if needle not in _text_of(lead.get(field)).lower():
                    return False
                continue
            if blob is None:
                blob = self._haystack(lead)
            if needle not in blob:
                return False
        return True

    def _haystack(self, lead: dict) -> str:
        """Everything about one lead the filter box reads, lowercased once.

        Kept against the lead's id because the alternative is rebuilding ten
        strings and lowercasing them for every row on every keystroke.
        Newline-joined rather than space-joined so one *term* still has to sit
        inside one field: `"roofing contractor"` in quotes must not be answered
        by a business called Roofing whose category is Contractor.
        """
        lead_id = _int_of(lead.get("id"))
        blob = self._blobs.get(lead_id)
        if blob is None:
            parts = [_text_of(lead.get(field))
                     for field in ("name", "email", "city", "category", "phone",
                                   "website", "status", "source")]
            parts.append(self._gap_text(lead)[0])
            # Both registers of the crawl's failure: "timed out" is what the
            # column shows and what a user types, and the line underneath is
            # what they paste out of a bug report.
            site, _phrase, raw = self._site_text(lead)
            parts.append(site)
            parts.append(raw)
            parts.append(components.score_band(
                _int_of(lead.get("opportunity_score")))[1])
            blob = "\n".join(part for part in parts if part).lower()
            self._blobs[lead_id] = blob
        return blob

    def _refresh_lead_counts(self) -> None:
        """The line above the table, from counts that were kept as they moved.

        It used to walk every lead for the status buckets and every cached
        reason for the generic tally, on every filter pass — and every audited
        lead caused one. `_fill_table` counts them once and `_on_lead_audited`
        moves one lead between two buckets.
        """
        total = len(self._leads)
        audited = total - self._buckets.get("new", 0)

        parts = [_plural(total, "lead")]
        if self._visible != total:
            parts = ["%d of %d shown" % (self._visible, total)]
        parts.append("%d audited" % audited)
        if self._generic_count:
            parts.append("%d generic" % self._generic_count)
        for key, label in (("queued", "queued"), ("sent", "sent"),
                           ("replied", "replied"), ("suppressed", "suppressed")):
            if self._buckets.get(key):
                parts.append("%d %s" % (self._buckets[key], label))
        self.lead_counts.setText(" · ".join(parts))

    def _lead_at(self, row: int) -> dict:
        """The record painted on `row`.

        From the list rather than from the cell. `_fill_table` appends
        `self._leads` in order, so row `n` is `self._leads[n]` — and asking Qt
        instead meant a dict marshalled out of a QVariant at 50µs a row, which
        was more than half the cost of every pass over this table.
        """
        return self._leads[row] if 0 <= row < len(self._leads) else {}

    def _selected_rows(self) -> list:
        """The rows the user has picked that the filters are still showing.

        `selectedRows()` and not `selectedIndexes()`, which is one index per
        *cell*: a Ctrl+A over 5,000 leads handed this 35,000 `QModelIndex`
        objects to build a set of 5,000 numbers out of, once per selection
        change and again for every button label. Seven times the work for the
        same answer, and it is asked for on the keystroke.
        """
        table = self.lead_table
        picked = table.selectionModel()
        if picked is None:
            return []
        return [index.row() for index in picked.selectedRows()
                if not table.isRowHidden(index.row())]

    def _selected_count(self) -> int:
        """How many rows are picked, without building a row list for them.

        `selectedRows()` is one object per selected row; a range is two
        numbers. The button labels want the count far more often than the rows,
        and at 5,000 selected the two together are the difference between a
        Ctrl+A costing 196ms and costing 8ms.
        """
        table = self.lead_table
        total = 0
        for span in table.selectedRanges():
            for row in range(span.topRow(), span.bottomRow() + 1):
                total += 0 if table.isRowHidden(row) else 1
        return total

    def _selected_leads(self) -> list[dict]:
        return [self._lead_at(row) for row in self._selected_rows()]

    def _shown_leads(self) -> list[dict]:
        """Every lead the filters are leaving on screen, selection ignored."""
        table = self.lead_table
        return [self._lead_at(row) for row in range(table.rowCount())
                if not table.isRowHidden(row)]

    def _target_leads(self) -> list[dict]:
        """Selection if there is one, otherwise everything the filters show.

        The whole list, for the three callers that are about to act on it.
        Anything that only needs to *describe* the target asks `_target_head`,
        which is the same answer without materialising five thousand records.
        """
        rows = self._selected_rows()
        return [self._lead_at(row) for row in rows] if rows \
            else self._shown_leads()

    def _target_head(self, limit: int = _NAMED_IN_SUMMARY) -> tuple:
        """(how many an action would touch, the first few of them, from a pick?)

        What every button label on this screen actually needs. The labels used
        to get it by building two whole lists — `_target_leads` walked the
        table, and `_target_sentence` walked it twice more — three passes over
        five thousand rows to write "Audit all (5000)". The count is carried by
        `_apply_filters`; the names stop at three.
        """
        table = self.lead_table
        chosen = self._selected_count()
        if chosen:
            head: list = []
            for span in table.selectedRanges():
                for row in range(span.topRow(), span.bottomRow() + 1):
                    if table.isRowHidden(row):
                        continue
                    head.append(self._lead_at(row))
                    if len(head) >= limit:
                        return chosen, head, True
            return chosen, head, True

        head = []
        if self._visible > 0:
            for row in range(table.rowCount()):
                if table.isRowHidden(row):
                    continue
                head.append(self._lead_at(row))
                if len(head) >= limit or len(head) >= self._visible:
                    break
        return self._visible, head, False

    def _target_sentence(self, verb: str, target=None) -> str:
        count, head, chosen = self._target_head() if target is None else target
        scope = "selected" if chosen else "shown by the filter"
        return "%s %s (%s) — %s." % (verb, _plural(count, "lead"), scope,
                                     _named(count, head))

    def _on_lead_selection_changed(self) -> None:
        """One selected lead follows the preview; five thousand do not.

        The list was built to ask its length. A Ctrl+A over 5,000 rows built
        5,000 records to decide it was not one of them, on the keystroke, and
        the whole selection change cost 196ms; it costs 8ms.
        """
        self._refresh_lead_actions()
        if self._selected_count() != 1:
            return
        chosen = self._selected_leads()
        if chosen:
            self._select_preview_lead(_int_of(chosen[0].get("id")))

    def _refresh_lead_actions(self) -> None:
        """Every bulk control says what it is about to act on, and how many.

        A disabled one says what would enable it: the audit found controls that
        simply stopped responding, with nothing on screen saying why.

        One look at the target for the whole row. This used to take three —
        `_selected_leads`, `_target_leads`, then `_target_sentence` re-running
        both, twice over — which at 5,000 leads was 1,048ms to write four
        strings, and an audit run asked for them once per lead.
        """
        if not hasattr(self, "audit_btn"):
            return
        target = self._target_head()
        targets, head, from_selection = target
        count = targets if from_selection else 0

        self.audit_btn.setText("Audit selected (%d)" % count if count
                               else "Audit all (%d)" % targets)
        self.audit_btn.setEnabled(bool(targets) and not self._auditing)
        if self._auditing:
            self.audit_btn.setToolTip("A crawl is already running")
        elif not targets:
            self.audit_btn.setToolTip(
                "Nothing to crawl yet — import a CSV or scrape a city first")
        else:
            # Says which model will write the personalised lines, because that
            # choice lives in Settings and shows up nowhere else on this screen.
            self.audit_btn.setToolTip(
                "%s Crawl each website, score the automation gaps and write "
                "the personalised lines (%s). Nothing is sent."
                % (self._target_sentence("Audits", target), self._ai_summary()))

        self.suppress_btn.setText("Suppress…" if count <= 1
                                  else "Suppress %d…" % count)
        self.suppress_btn.setEnabled(bool(count))
        self.suppress_btn.setToolTip(
            "Never contact %s again. You will be asked first."
            % _named(count, head) if count else
            "Select the rows to exclude — this acts on the whole selection")

        self.remove_btn.setText("Remove…" if count <= 1
                                else "Remove %d…" % count)
        self.remove_btn.setEnabled(bool(count))
        self.remove_btn.setToolTip(
            "Forget %s entirely — the row, the crawl and the score. You will "
            "be asked first." % _named(count, head) if count else
            "Select the rows to forget — this acts on the whole selection")

        self.copy_btn.setText("Copy" if count <= 1 else "Copy %d" % count)
        self.copy_btn.setEnabled(bool(targets))
        self.copy_btn.setToolTip(
            "Copy every address %s to the clipboard, one per line"
            % ("selected" if count else "shown") if targets else
            "There are no addresses on screen to copy")

        self.export_btn.setText("Export…" if count <= 1
                                else "Export %d…" % count)
        self.export_btn.setEnabled(bool(targets))
        self.export_btn.setToolTip(
            "%s Writes a CSV of every column — the ones switched off included "
            "— plus the phone, the website and the source."
            % self._target_sentence("Exports", target) if targets else
            "There is nothing on screen to export")

        if hasattr(self, "plan_targets"):
            self.plan_targets.setText(self._target_sentence("Queues", target)
                                      + self._form_letter_warning())
            self.prepare_btn.setText("Prepare campaign (%d)" % targets
                                     if targets else "Prepare campaign")

    def _form_letter_warning(self) -> str:
        """How much of what Prepare is about to queue would be a form letter.

        The number existed already — the plan reports it — but only after the
        campaign had been built, which is after the decision. A user who can
        see "31 of these have a site nothing could read" before pressing
        Prepare can narrow the filter, retry the crawl or take the rows out;
        the same sentence afterwards is an explanation of something that has
        already happened.

        Worked out only while the card is the page in front of the user, and
        that is the whole of what keeps it free. The label lives on the
        Campaign tab; the selection and the filter it describes are changed on
        the Leads tab, where nobody can see it. Counting on every one of those
        changes cost a walk of the target for a string on a hidden page —
        measured at 5,000 leads, +42ms on a Ctrl+A (55ms -> 97ms) and +8ms on
        every keystroke in the filter box. Counted on the way into the tab
        instead it is one walk per visit, 11.5-15.5ms at 5,000 leads over four
        runs, and the number is fresh at the only moment it is read.

        Not recomputed while a crawl is running either. `_refresh_lead_actions`
        is called once per audited lead, so a walk here is the O(N²) this
        screen spent a whole pass closing; `_on_audit_done` asks for it once
        when the run ends, which is when it changed.
        """
        if not hasattr(self, "lead_table") or self._auditing:
            return self._form_letter_note
        if self.pages.currentIndex() != 1:
            return self._form_letter_note
        unreadable, generic = self._form_letter_counts(
            self._selected_leads() if self._selected_count()
            else self._shown_leads())
        note = ""
        if unreadable:
            note = ("\n%d of them have a site nothing could read, so %s go out "
                    "as a form letter."
                    % (unreadable, "that one" if unreadable == 1 else "those"))
            if generic:
                note += (" %d more say nothing about the business for other "
                         "reasons." % generic)
        elif generic:
            note = ("\n%d of them would go out as a form letter — the crawl "
                    "found nothing to say about the business." % generic)
        self._form_letter_note = note
        return note

    def _form_letter_counts(self, leads) -> tuple:
        """(sites nothing could read, other form letters) over `leads`.

        Five thousand shown rows land here on every visit to the Campaign tab,
        so it is two dictionary reads a lead and no function calls inside the
        loop.
        """
        gaps, known = self._gaps, self._generic
        unreadable = generic = 0
        for lead in leads:
            lead_id = _int_of(lead.get("id"))
            answer = gaps.get(lead_id)
            if (answer[2] if answer else self._site_text(lead)[0]):
                unreadable += 1
            elif known.get(lead_id):
                generic += 1
        return unreadable, generic

    def _on_lead_double_clicked(self, row: int, _column: int) -> None:
        lead = self._lead_at(row)
        url = _text_of(lead.get("website")).strip() or _text_of(lead.get("maps_link")).strip()
        if url:
            webbrowser.open(url if "://" in url else "https://" + url)

    def _show_lead_menu(self, pos) -> None:
        index = self.lead_table.indexAt(pos)
        if not index.isValid():
            return
        lead = self._lead_at(index.row())
        if not lead:
            return
        # A right-click inside the selection acts on the selection; one outside
        # it acts on the row under the pointer, which is what the pointer said
        # it would do. Asked of the selection model rather than of a list of
        # the selected records: "is this row in it" is a question the model
        # answers, and the list was five thousand dicts compared by value.
        picked = self.lead_table.selectionModel()
        chosen = self._selected_leads() \
            if picked is not None and picked.isSelected(index) else [lead]

        # The same drawings the toolbar carries, so a row's actions and the
        # bulk row's are one vocabulary: two ways to reach one verb, marked the
        # same way. Both openers take the arrow out of the box, because both
        # leave this app for a browser.
        menu = QMenu(self)
        website = _text_of(lead.get("website")).strip()
        email = _text_of(lead.get("email")).strip()
        if website:
            menu.addAction(_icons.icon("external"), "Open website",
                           lambda: webbrowser.open(
                               website if "://" in website
                               else "https://" + website))
        if lead.get("maps_link"):
            menu.addAction(_icons.icon("external"), "Open in Google Maps",
                           lambda: webbrowser.open(_text_of(lead.get("maps_link"))))
        menu.addSeparator()
        if email:
            menu.addAction(_icons.icon("copy"), "Copy email",
                           lambda: self._copy(email))
        if lead.get("name"):
            menu.addAction(_icons.icon("copy"), "Copy business name",
                           lambda: self._copy(_text_of(lead.get("name"))))
        menu.addAction(_icons.icon("eye"), "Preview this email",
                       lambda: self._preview_lead(lead))
        menu.addSeparator()
        # The crawl, again, on exactly these rows. It is the same worker the
        # Audit button starts and deliberately not a second path: what is
        # different is that it says what it changed, and that it is reachable
        # from the row the user is pointing at rather than only from a button
        # that acts on the whole filter. The label names which of the two it
        # is, because "audit" over a row that has already been crawled twice
        # reads as a no-op.
        failing = [row for row in chosen if self._site_text(row)[0]]
        retrying = bool(failing) and len(failing) == len(chosen)
        again = menu.addAction(
            _icons.icon("search"),
            "Retry the crawl on %s" % _plural(len(chosen), "site") if retrying
            else "Crawl %s again" % _plural(len(chosen), "site"),
            lambda: self._on_audit_clicked(chosen, retry=retrying))
        again.setEnabled(not self._auditing)
        if self._auditing:
            again.setToolTip("A crawl is already running")
        menu.addSeparator()
        menu.addAction(_icons.icon("minus"),
                       "Suppress %s (never contact)" % _plural(len(chosen), "lead"),
                       lambda: self._suppress(chosen))
        menu.addAction(_icons.icon("trash"),
                       "Remove %s from the list" % _plural(len(chosen), "lead"),
                       lambda: self._remove(chosen))
        menu.exec_(self.lead_table.viewport().mapToGlobal(pos))

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(_text_of(text))
        self.lead_status.setText("Copied to clipboard")

    def _on_copy_emails(self) -> None:
        addresses = [_text_of(lead.get("email")).strip()
                     for lead in self._target_leads()
                     if _text_of(lead.get("email")).strip()]
        if not addresses:
            self._toast("There are no addresses on screen to copy.",
                        tone="warning")
            return
        QApplication.clipboard().setText("\n".join(addresses))
        self.lead_status.setText("Copied %s" % _plural(len(addresses), "address"))

    def _on_suppress_clicked(self) -> None:
        chosen = self._selected_leads()
        if not chosen:
            self._toast("Select the leads to exclude first — Suppress acts on "
                        "the whole selection.", tone="warning")
            return
        self._suppress(chosen)

    def _suppress(self, leads: list) -> None:
        """Exclude a whole selection, after asking, and offer the way back.

        Three findings in one action. It was permanent and unconfirmed, so a
        misplaced right-click ended a lead for good; it read `currentItem` and
        so acted on one row of a selection of fifty without saying which; and
        the only feedback was a six-second grey line that could not carry an
        undo. It asks first, it names who it is about to exclude, and the toast
        that reports it carries the way back.
        """
        wanted = [(lead, _text_of(lead.get("email")).strip(),
                   _text_of(lead.get("status")).strip() or "new")
                  for lead in leads
                  if "@" in _text_of(lead.get("email"))]
        if not wanted:
            self._toast("Those leads have no address to exclude.", tone="warning")
            return

        if not components.confirm(
                self,
                title="Never contact %s again?" % _plural(len(wanted), "lead"),
                body="%s will be excluded from every campaign from now on, "
                     "follow-ups included, and anything already queued for "
                     "them is cancelled. This is what an unsubscribe does, so "
                     "use it when someone has asked."
                     % _names_of([lead for lead, _e, _s in wanted]),
                confirm_text="Suppress %s" % _plural(len(wanted), "lead"),
                danger=True):
            return

        for _lead, email, _status in wanted:
            _db.suppress(self.conn, email, "suppressed from the leads table")
        self._reload_leads()
        self._refresh_stats()

        restore = [(email, status) for _lead, email, status in wanted]
        self._toast(
            "%s will never be contacted again, and anything queued for them "
            "has been cancelled." % _names_of([l for l, _e, _s in wanted]),
            tone="warning", action="Undo",
            on_action=lambda: self._undo_suppress(restore))

    def _undo_suppress(self, restore: list) -> None:
        """Put a suppression back, and say plainly what it cannot put back.

        The addresses become contactable again and each lead returns to the
        status it was on. What stays cancelled is what was in the queue: those
        rows carry a send time that has since passed, and re-queueing them would
        mail the whole batch at once.
        """
        back = 0
        for email, status in restore:
            if self._unsuppress(email):
                _db.upsert_lead(self.conn, {"email": email, "status": status})
                _db.log_event(self.conn, "unsuppressed", email)
                back += 1
        self._reload_leads()
        self._refresh_stats()
        if not back:
            self._toast("Could not undo that.", tone="danger")
            return
        self._toast("%s can be contacted again. Nothing was re-queued — "
                    "prepare a new campaign to include them."
                    % _plural(back, "address"), tone="success")

    def _preview_lead(self, lead: dict) -> None:
        self._select_preview_lead(_int_of(lead.get("id")))
        self._goto_tab(1)

    # ── Leads: removing ──────────────────────────────────────────────────────

    def _on_remove_clicked(self) -> None:
        chosen = self._selected_leads()
        if not chosen:
            self._toast("Select the leads to forget first — Remove acts on the "
                        "whole selection.", tone="warning")
            return
        self._remove(chosen)

    def _remove(self, leads: list) -> None:
        """Forget a selection outright, and say what that is not.

        The other half of the pair Suppress opens, and the difference between
        them is the sentence in the dialog. Suppressing keeps the address on
        file precisely so it can never be mailed again — it is what an
        unsubscribe means, and forgetting the row would lose that promise.
        Removing is for the rows that should never have been imported: a
        spreadsheet with the wrong column mapped, a scrape of the wrong city.
        A removed lead can be imported again tomorrow; a suppressed one cannot,
        and must not.

        Anything already queued for those addresses is left alone on purpose.
        A message that has been planned has a send time and an account against
        it, and deleting the lead under a running campaign would leave the send
        loop holding a row with nothing behind it.
        """
        wanted = [lead for lead in leads if _int_of(lead.get("id")) > 0]
        if not wanted:
            self._toast("Those rows are not in the store yet.", tone="warning")
            return
        if not components.confirm(
                self,
                title="Forget %s?" % _plural(len(wanted), "lead"),
                body="%s will be deleted from the lead list, along with the "
                     "crawl and the score. This does not stop them being "
                     "contacted — use Suppress for that — and it does not "
                     "cancel anything already queued. They can be imported "
                     "again." % _names_of(wanted),
                confirm_text="Remove %s" % _plural(len(wanted), "lead"),
                danger=True):
            return

        ids = [_int_of(lead.get("id")) for lead in wanted]
        gone = 0
        try:
            # In batches, because `IN (?, ?, …)` is one bound variable per id
            # and SQLite refuses past its own limit — measured here at 32,766,
            # but it is a compile-time number and this app ships its own
            # interpreter. A Ctrl+A over a 40,000-lead store must not come back
            # as "could not remove those leads".
            for at in range(0, len(ids), _DELETE_BATCH):
                batch = ids[at:at + _DELETE_BATCH]
                cursor = self.conn.execute(
                    "DELETE FROM leads WHERE id IN (%s)"
                    % ",".join("?" * len(batch)), batch)
                gone += _int_of(cursor.rowcount)
            self.conn.commit()
        except Exception:
            self._toast("Could not remove those leads.", tone="danger")
            return
        for lead_id in ids:
            self._forget_lead(lead_id)
        _db.log_event(self.conn, "leads_removed",
                      "%d removed from the leads table" % gone)
        self._reload_leads()
        self._toast("%s removed. Nothing was unsubscribed and nothing already "
                    "queued was cancelled." % _plural(gone, "lead"),
                    tone="warning")

    # ── Leads: export ────────────────────────────────────────────────────────

    def _on_export_clicked(self) -> None:
        """Write the target out as a CSV the user can open in a spreadsheet.

        The *rows* are the ones on screen and that is the point: a filter
        narrowed to the audited roofers in Toronto is the list the user built,
        and an export that quietly widened it back to five thousand would be a
        different list under the same name.

        The *columns* are all of them, plus the three no column shows — the
        phone, the website and where the lead came from — and the headline gap
        whole rather than as the elided cell. Switching a column off is about
        what is worth reading on screen; it is not an instruction to throw the
        email addresses away on the way out of the app.
        """
        leads = self._target_leads()
        if not leads:
            self._toast("There is nothing on screen to export.", tone="warning")
            return
        start = self.settings.get("export_dir") or os.path.expanduser("~")
        suggested = os.path.join(start, "leads-%s.csv"
                                 % datetime.now().strftime("%Y-%m-%d"))
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export leads", suggested, "CSV files (*.csv);;All files (*)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        fields = list(range(len(_LEAD_COLUMNS)))
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow([_LEAD_COLUMNS[field].title for field in fields]
                                + ["Phone", "Website", "Source"])
                for lead in leads:
                    writer.writerow(
                        [self._export_value(lead, field) for field in fields]
                        + [_text_of(lead.get("phone")).strip(),
                           _text_of(lead.get("website")).strip(),
                           _text_of(lead.get("source")).strip()])
        except OSError as exc:
            self._toast("Could not write that file: %s" % exc, tone="danger")
            return
        self._toast("Exported %s to %s." % (_plural(len(leads), "lead"),
                                            os.path.basename(path)),
                    tone="success", action="Open folder",
                    on_action=lambda: webbrowser.open(os.path.dirname(path)))

    def _export_value(self, lead: dict, field: int) -> str:
        """One field of one lead as a spreadsheet should read it.

        The score goes out as the number and not as the badge's words, because
        a column of "88 · strong" cannot be sorted by a spreadsheet, and the
        headline gap goes out whole rather than as the elided cell.
        """
        if field == _COL_SCORE:
            return str(_int_of(lead.get("opportunity_score")))
        if field == _COL_GAP:
            return self._gap_text(lead)[0]
        if field == _COL_SITE:
            # The words and the line, in one cell: a spreadsheet is where a
            # user sorts forty unreachable sites into "fix the URL" and "the
            # business is gone", and four words cannot tell them apart. The
            # line is left off when it says the same thing twice — a crawl
            # whose error is literally "timed out" must not export
            # "timed out — timed out".
            site, phrase, raw = self._site_text(lead)
            if not site or not raw or raw.lower() in (site.lower(), phrase.lower()):
                return site
            return "%s — %s" % (site, raw)
        if field == _COL_STATUS:
            return _text_of(lead.get("status")).strip() or "new"
        return _text_of(lead.get(_COL_KEYS.get(field, "name"))).strip()

    # ── Leads: which columns, and which view ─────────────────────────────────

    def _show_columns_menu(self) -> None:
        """Switch a column off, and have it stay off next time the app opens.

        Business has no entry. A lead table whose every column can be turned
        off has a state in which it paints five thousand empty rows, and the
        one column that says which business a row is cannot be the one the
        user loses.
        """
        menu = QMenu(self)
        # A QMenu swallows its actions' tooltips unless it is told not to, and
        # the one entry that cannot be pressed is the one that has to say why.
        menu.setToolTipsVisible(True)
        for field in range(len(_LEAD_COLUMNS)):
            action = menu.addAction(_LEAD_COLUMNS[field].title)
            action.setCheckable(True)
            action.setChecked(field in self._fields)
            if field in _FIXED_FIELDS:
                action.setEnabled(False)
                action.setToolTip("A row with no business name on it cannot be "
                                  "told from the row above it")
                continue
            action.triggered.connect(
                lambda checked, f=field: self._toggle_column(f, checked))
        menu.addSeparator()
        every = menu.addAction("Show every column")
        every.setEnabled(len(self._fields) != len(_LEAD_COLUMNS))
        every.triggered.connect(
            lambda: self._set_columns(range(len(_LEAD_COLUMNS))))
        menu.exec_(self.columns_btn.mapToGlobal(
            self.columns_btn.rect().bottomLeft()))

    def _toggle_column(self, field: int, shown: bool) -> None:
        wanted = set(self._fields)
        if shown:
            wanted.add(field)
        else:
            wanted.discard(field)
        self._set_columns(wanted)

    def _set_columns(self, fields) -> None:
        wanted = set(fields) | set(_FIXED_FIELDS)
        chosen = tuple(field for field in range(len(_LEAD_COLUMNS))
                       if field in wanted)
        if chosen == tuple(self._fields):
            return
        self._fields = chosen
        self._rebuild_table()
        self._forget_view()
        self._save_view_state(now=True)

    def _rebuild_table(self) -> None:
        """Swap in a table built from the columns that are wanted now.

        A new table and not a reconfigured one, because the column spec is what
        `components.table()` takes and the widths come out of it: there is no
        way to tell an existing `_Table` that it has four columns now that is
        not a rebuild of everything the spec decided.
        """
        if not hasattr(self, "lead_table"):
            return
        t = components.active_theme()
        old = self.lead_table
        self.lead_table = self._make_table(t)
        self.lead_stack.insertWidget(0, self.lead_table)
        self.lead_stack.setCurrentIndex(0 if self._leads else 1)
        old.setParent(None)
        old.deleteLater()
        self._fill_table()

    def _show_views_menu(self) -> None:
        """The saved views, and what can be done to them.

        A view is a filter, a sort and a set of columns under a name. The
        filters already survived a tab switch, which is worth exactly one
        session; this is what makes «Toronto roofers worth calling» a thing the
        user can come back to on Monday.

        Every entry carries the sentence that says what it actually shows,
        because a list of five names typed in a hurry six weeks ago is a list
        of five guesses.
        """
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if self._views:
            for view in self._views:
                name = _text_of(view.get("name"))
                action = menu.addAction(_menu_text(name))
                action.setCheckable(True)
                action.setChecked(name == self._view_name)
                action.setToolTip(_view_sentence(view))
                action.triggered.connect(lambda _c, n=name: self._apply_view(n))
            menu.addSeparator()
        else:
            empty = menu.addAction("No saved views yet")
            empty.setEnabled(False)
            menu.addSeparator()

        menu.addAction("Save this view…", self._on_save_view)
        if self._view_name:
            menu.addAction("Update “%s”" % _menu_text(self._view_name),
                           lambda: self._store_view(self._view_name))
            menu.addAction("Delete “%s”" % _menu_text(self._view_name),
                           lambda: self._delete_view(self._view_name))
        menu.addSeparator()
        menu.addAction("Clear the filter", self._clear_view)
        menu.exec_(self.view_btn.mapToGlobal(self.view_btn.rect().bottomLeft()))

    def _current_view(self, name: str) -> dict:
        return {
            "name": name,
            "search": self._search,
            "status": self._wanted_status(),
            "sort": [_FIELD_KEYS[self._sort[0]],
                     "desc" if self._sort[1] == Qt.DescendingOrder else "asc"],
            "columns": _keys_of_fields(self._fields),
            "known": list(_FIELD_KEYS),
        }

    def _on_save_view(self) -> None:
        name = _ask_name(self, self._view_name)
        if not name:
            return
        self._store_view(name)

    def _store_view(self, name: str) -> None:
        name = _text_of(name).strip()[:_VIEW_NAME_CH]
        if not name:
            return
        entry = self._current_view(name)
        kept = [view for view in self._views
                if _text_of(view.get("name")).lower() != name.lower()]
        replaced = len(kept) != len(self._views)
        if len(kept) >= _MAX_VIEWS:
            self._toast("There is room for %d saved views. Delete one first."
                        % _MAX_VIEWS, tone="warning")
            return
        self._views = kept + [entry]
        self._view_name = name
        self._save_view_state(now=True)
        self._refresh_view_button()
        self._toast("%s “%s” — %s" % ("Updated" if replaced else "Saved",
                                      name, _view_sentence(entry)),
                    tone="success")

    def _delete_view(self, name: str) -> None:
        before = len(self._views)
        self._views = [view for view in self._views
                       if _text_of(view.get("name")) != name]
        if len(self._views) == before:
            return
        self._view_name = ""
        self._save_view_state(now=True)
        self._refresh_view_button()
        self._toast("Deleted the view “%s”. The leads are untouched." % name,
                    tone="info")

    def _apply_view(self, name: str) -> None:
        view = next((v for v in self._views
                     if _text_of(v.get("name")) == name), None)
        if view is None:
            return
        self._set_view(view)
        self._view_name = name
        self._save_view_state(now=True)
        self._refresh_view_button()
        self._toast("Showing “%s” — %s" % (name, _view_sentence(view)))

    def _clear_view(self) -> None:
        self._set_view({"search": "", "status": "",
                        "sort": ["score", "desc"],
                        "columns": _keys_of_fields(self._fields),
                        "known": list(_FIELD_KEYS)})
        self._view_name = ""
        self._save_view_state(now=True)
        self._refresh_view_button()

    def _set_view(self, view: dict) -> None:
        """Put the whole of one view on: columns, sort, status, search.

        Every widget is set with its signals blocked and the state it would
        have announced is set by hand, so applying a view is one pass over the
        table rather than three — the status picker, the filter box and the
        column set each start one on their own. The order matters for the same
        reason: the sort and the terms are in place before the single
        `_fill_table` or `_rebuild_table` that reads them.
        """
        fields = _fields_wanted(view, self._fields)
        sort = list(view.get("sort") or [])
        field = _FIELD_OF_KEY.get(sort[0] if sort else "", _COL_SCORE)
        order = Qt.AscendingOrder if len(sort) > 1 and sort[1] == "asc" \
            else Qt.DescendingOrder

        blocked = self.status_filter.blockSignals(True)
        try:
            wanted = _text_of(view.get("status"))
            self.status_filter.setCurrentIndex(
                _STATUS_KEYS.index(wanted) if wanted in _STATUS_KEYS else 0)
        finally:
            self.status_filter.blockSignals(blocked)

        self._sort = (field, order)
        search = _text_of(view.get("search"))
        self._search = search.strip().lower()
        self._terms = _parse_query(self._search)

        if fields != tuple(self._fields):
            self._fields = fields
            self._rebuild_table()
        else:
            self._fill_table()

        blocked = self.lead_search.blockSignals(True)
        try:
            self.lead_search.setText(search)
        finally:
            self.lead_search.blockSignals(blocked)

    def _forget_view(self) -> None:
        """The user has just changed something the saved view did not say.

        The name comes off rather than the view being rewritten under them: a
        saved view is only worth anything if it stays what it was saved as.
        """
        if self._restoring:
            return
        if self._view_name:
            self._view_name = ""
            self._refresh_view_button()
        self._save_view_state()

    def _refresh_view_button(self) -> None:
        if not hasattr(self, "view_btn"):
            return
        self.view_btn.setText("View: %s" % _clip(self._view_name, _VIEW_NAME_CH)
                              if self._view_name else "Views")
        self.view_btn.setToolTip(
            "Showing the saved view “%s”. Change a filter and it becomes an "
            "unsaved one." % self._view_name if self._view_name else
            "Save the filter, the sort and the columns under a name, and come "
            "back to it another day")

    def _restore_filter(self) -> None:
        """Put back the filter, the sort and the view the app closed on.

        Read once at build time from the file `_read_views` opened. A lead
        table that opens on «all leads, by score» every morning is a table the
        user has to re-narrow every morning, and the brief's word for what this
        makes the existing per-session filters is durable.
        """
        stored = self._stored
        self._restoring = True
        try:
            sort = list(stored.get("sort") or [])
            if sort and sort[0] in _FIELD_OF_KEY:
                self._sort = (_FIELD_OF_KEY[sort[0]],
                              Qt.AscendingOrder
                              if len(sort) > 1 and sort[1] == "asc"
                              else Qt.DescendingOrder)
                self.lead_table.horizontalHeader().setSortIndicator(
                    self._col_of.get(self._sort[0], 0), self._sort[1])
            wanted = _text_of(stored.get("status"))
            if wanted in _STATUS_KEYS:
                self.status_filter.setCurrentIndex(_STATUS_KEYS.index(wanted))
            search = _text_of(stored.get("search"))
            if search:
                self.lead_search.setText(search)
        finally:
            self._restoring = False
        self._refresh_view_button()

    def _save_view_state(self, *, now: bool = False) -> None:
        """Write what the leads tab is showing, and what it has been told to keep.

        `now` for a decision — a view saved, deleted or chosen, a column
        switched off — because a user who presses Save is owed the file. Off
        for the churn: the filter box calls this on every keystroke, and a
        write-then-rename per character is 3.6ms of GUI-thread disk measured
        here for a value the next keystroke replaces.

        Failure is silent on purpose and this is the one place it is: a
        read-only profile directory must cost the user their saved views and
        not their lead table.
        """
        self._stored = {
            "columns": _keys_of_fields(self._fields),
            # Which keys this build could have named. `_fields_wanted` reads it
            # back to tell a column the user switched off from one that did not
            # exist when they chose.
            "known": list(_FIELD_KEYS),
            "views": self._views,
            "current": self._view_name,
            "search": self._search,
            "status": self._wanted_status() if hasattr(self, "status_filter") else "",
            "sort": [_FIELD_KEYS[self._sort[0]],
                     "desc" if self._sort[1] == Qt.DescendingOrder else "asc"],
        }
        if now:
            self._save_timer.stop()
            _write_views(self._stored)
        else:
            self._save_timer.start(_SAVE_AFTER_MS)

    def _flush_view_state(self) -> None:
        """The write the last keystroke put off. Whatever `_stored` says now."""
        _write_views(self._stored)

    # ── Leads: auditing ──────────────────────────────────────────────────────

    def _on_audit_clicked(self, leads=None, *, retry: bool = False) -> None:
        """Crawl the target, and remember enough to say what changed.

        `leads` is the rows a caller has already chosen — the row menu's Retry
        hands it the unreachable ones — and `None` means the button's own
        target: the selection when there is one, everything the filter shows
        when there is not.

        The Site words every one of them carries *now* are kept before the
        worker starts, because that is the only moment they exist. Without them
        `_on_audit_done` can say "Audit finished" and nothing else, and a retry
        that changed nothing at all looks exactly like one that fixed
        everything — which is the worse of the two, because the user goes on
        to send four hundred form letters believing the crawl worked.
        """
        if self._auditing:
            return
        if not self._retire(self.audit_worker):
            self._toast("The last crawl is still finishing. Press Audit again "
                        "in a moment.", tone="warning")
            return
        leads = self._target_leads() if leads is None else [
            lead for lead in leads if lead]
        if not leads:
            self._toast("There are no leads to audit yet. Import a CSV, or "
                        "scrape a city on the Scrape screen.", tone="warning")
            return

        self._auditing = True
        self._audit_retry = bool(retry)
        self._audit_before = {_int_of(lead.get("id")): self._site_text(lead)[0]
                              for lead in leads}
        self.audit_btn.setEnabled(False)
        self.lead_progress.setRange(0, max(1, len(leads)))
        self.lead_progress.setValue(0)
        self.lead_progress.show()
        self.lead_status.setText(
            "Retrying %s…" % _plural(len(leads), "site") if retry else
            "Auditing %s…" % _plural(len(leads), "site"))

        worker = AuditWorker(leads, self.settings, self._template_id())
        worker.progress_signal.connect(self._on_audit_progress)
        worker.lead_signal.connect(self._on_lead_audited)
        worker.log_signal.connect(self._on_audit_log)
        worker.done_signal.connect(self._on_audit_done)
        self.audit_worker = worker
        worker.start()
        self._publish_state()

    def _on_audit_progress(self, done: int, total: int) -> None:
        self.lead_progress.setRange(0, max(1, total))
        self.lead_progress.setValue(done)

    def _on_audit_log(self, message: str, level: str) -> None:
        if level in ("info", "error"):
            self.lead_status.setText(message)

    def _on_lead_audited(self, lead: dict) -> None:
        """Rewrite one row where it stands so scores land while the run runs.

        In place, and not re-sorted: a table that reorders itself under the
        pointer while five hundred crawls come back is a table nobody can read
        a row of. The order is retaken when the run ends.

        One row's worth of work, and that is the first finding closed. This
        used to search the record list and then the table for the lead, and
        then hand the whole screen to `_apply_filters`, which re-walked every
        row and had `_refresh_lead_actions` walk it three more times — so an
        audit of N leads cost N² row visits. Measured at 77.1ms a lead over 500
        leads (38.6 seconds of frozen window for one run) and 796.4ms a lead
        over 5,000 (66 minutes). The row is found in a map, the two counts it
        moves are adjusted rather than recomputed, and only its own visibility
        is re-decided.
        """
        if not isinstance(lead, dict):
            return
        lead_id = _int_of(lead.get("id"))
        row = self._row_of.get(lead_id, -1)
        if not 0 <= row < len(self._leads):
            return

        was_status = _text_of(self._leads[row].get("status")).strip() or "new"
        was_generic = bool(self._generic.get(lead_id))
        self._leads[row] = lead
        self._forget_lead(lead_id)
        self._stamps[lead_id] = _lead_stamp(lead)

        status = _text_of(lead.get("status")).strip() or "new"
        generic = self._gap_text(lead)[1]
        # A row inside the painted band is rebuilt from the new record; one
        # outside it has no cells to correct and will be built from the record
        # when it is scrolled to, so a lead four thousand rows down costs the
        # dictionary lookup above and nothing else.
        self._repaint_row(row)

        self._buckets[was_status] = max(0, self._buckets.get(was_status, 0) - 1)
        self._buckets[status] = self._buckets.get(status, 0) + 1
        self._generic_count += int(bool(generic)) - int(was_generic)
        self._refilter_row(row, lead)
        self._refresh_lead_counts()
        self._refresh_lead_actions()

    def _retire(self, worker) -> bool:
        """True once `worker` is safe to drop — finished, or never started."""
        if worker is None or not worker.isRunning():
            return True
        return bool(worker.wait(2000))

    def _on_audit_done(self) -> None:
        self._auditing = False
        self.lead_progress.hide()
        self._reload_leads()
        # After the reload, so every answer below is read off the records the
        # run actually wrote rather than off the ones it started from.
        self.lead_status.setText(self._audit_report())
        self._refresh_lead_actions()
        self._refresh_preview()
        self._publish_state()

    def _audit_report(self) -> str:
        """What the crawl that has just finished actually changed.

        "Audit finished" was true of every run and told the user nothing about
        any of them. Three questions it could not answer and this does: how
        many sites could not be read, which way they failed, and — for a
        retry — whether pressing it moved anything at all.

        A retry that changed nothing says so in its own sentence and puts it in
        a warning toast, because that is the one outcome the user has to act
        on: the sites are not coming back, and the leads behind them are going
        to be mailed a form letter or taken out.
        """
        asked = dict(self._audit_before)
        self._audit_before = {}
        retry, self._audit_retry = self._audit_retry, False
        if not asked:
            return "Audit finished"

        by_id = {_int_of(lead.get("id")): lead for lead in self._leads}
        fixed, broke, still = 0, 0, {}
        for lead_id, was in asked.items():
            lead = by_id.get(lead_id)
            now = self._site_text(lead)[0] if lead else ""
            if now:
                still[now] = still.get(now, 0) + 1
                if not was:
                    broke += 1
            elif was:
                fixed += 1

        total = len(asked)
        failing = sum(still.values())
        if retry:
            if not failing:
                return ("Retried %s — every one of them answered this time."
                        % _plural(total, "site"))
            if not fixed:
                self._toast(
                    "Retried %s and not one of them answered — %s. Those leads "
                    "will be sent a form letter unless you take them out."
                    % (_plural(total, "site"), _site_tally(still)),
                    tone="warning")
                return ("Retried %s — nothing changed (%s)."
                        % (_plural(total, "site"), _site_tally(still)))
            return ("Retried %s — %d answered this time, %d still unreachable "
                    "(%s)." % (_plural(total, "site"), fixed, failing,
                               _site_tally(still)))

        if not failing:
            return "Audited %s — every site was read." % _plural(total, "site")
        line = ("Audited %s — %d could not be read (%s)."
                % (_plural(total, "site"), failing, _site_tally(still)))
        if broke:
            line += " %s that worked before did not this time." % _plural(
                broke, "site").capitalize()
        return line

    # ── Campaign: profile and preview ────────────────────────────────────────

    def _profile(self) -> dict:
        profile = self.settings.get("sender_profile")
        return profile if isinstance(profile, dict) else {}

    # ── Which channel, and what that changes ─────────────────────────────────

    def _channel_settings(self, channel: str = "") -> dict:
        """`self.settings` with one channel's numbers under the scheduler's names.

        `core.campaign.channel_settings` is the whole of "one scheduler, two
        channels": every wa_* limit arrives under the key the email path already
        used, so every sentence this screen writes about a window, a cap, a gap
        or a ramp is written once and is right on both. Email is handed its own
        dict back unchanged, so nothing about the email path shifts by having
        been routed through here.
        """
        return _campaign.channel_settings(self.settings, channel or self._channel)

    def _copy(self, channel: str = ""):
        """The template source for a channel — the planner's own adapter, or None.

        `plan_campaign` renders through exactly this object, so the preview and
        the send are one call apart rather than two implementations of "what
        does this lead receive". None means the build has no copy for that
        channel at all, which is something to print rather than to crash on.
        """
        return _campaign.copy_for(channel or self._channel)

    def _accounts(self, channel: str = "") -> list[dict]:
        """Who may send on a channel, in the shape the cap rules read.

        Gmail has as many rows as the user has added; WhatsApp has exactly one,
        described the same way so that nothing downstream — the partition, the
        cap check, the Accounts card — needs to know which channel it is on. An
        empty list for WhatsApp is the channel being switched off in Settings.
        """
        try:
            return _campaign.channel_accounts(self.settings, channel or self._channel)
        except Exception:
            return []

    def _sender_noun(self, channel: str = "") -> str:
        return _CHANNEL_SENDER.get(channel or self._channel, "account")

    def _message_noun(self, channel: str = "") -> str:
        return _CHANNEL_NOUN.get(channel or self._channel, "message")

    def _wa_link(self):
        """The one WhatsApp connection this process may have, or None.

        Imported here rather than at the top of the file for one reason and it
        is not the import cycle — there is none. The connection belongs to the
        Settings screen, which is where a transport is set up; this screen only
        borrows it, and a build that has somehow lost that module should carry
        on sending email rather than fail to open the outreach screen.
        """
        try:
            from ui.screen_settings import wa_link
        except Exception:                            # noqa: BLE001 — email still works
            return None
        try:
            return wa_link()
        except Exception:                            # noqa: BLE001
            return None

    def _wa_ban_notice(self) -> str:
        """The unacknowledged WhatsApp restriction, "" when there is none."""
        if self.conn is None:
            return ""
        try:
            return _text_of(_campaign.wa_ban_notice(self.conn))
        except Exception:
            return ""

    def _on_channel_picked(self, index: int) -> None:
        """Swap the channel this campaign is being composed for.

        The plan is dropped rather than carried across, and that is deliberate:
        a plan is a summary of a queue written for one transport, and showing it
        under the other channel's heading would describe a campaign that does
        not exist. Whatever was actually queued is untouched and is still on the
        Sending tab, which reads each campaign's channel off its own rows.
        """
        key = _CHANNELS[index][0] if 0 <= index < len(_CHANNELS) else EMAIL
        if key == self._channel:
            return
        self._channel = key
        self._plan = {}
        self.goto_sending_btn.hide()
        self._cap_preview()
        self._refresh_templates()
        self._refresh_profile()
        self._refresh_preview()
        self._refresh_plan_summary()
        self._refresh_channel_card()
        self._refresh_lead_actions()

    def _refresh_channel_card(self) -> None:
        """What this channel is, and what is standing in the way of using it.

        The warning half is the point. Every reason a WhatsApp campaign will not
        send — the channel switched off, no connection, a standing restriction,
        no copy in the build — is invisible from the Campaign tab otherwise, and
        the user finds out by pressing Prepare and reading a skip count.
        """
        if not hasattr(self, "channel_note"):
            return
        index = _CHANNEL_INDEX.get(self._channel, 0)
        button = self.channel_group.button(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)

        if self._channel == WHATSAPP:
            self.channel_note.setText(
                "Messages go to the lead's phone from the number connected in "
                "Settings. Same leads and the same crawl as the email side, in "
                "a chat register and under much tighter limits. A lead already "
                "reached by email is left out of this campaign.")
        else:
            self.channel_note.setText(
                "Mail goes from the Gmail accounts set up in Settings, with the "
                "footer and the unsubscribe line every message is required to "
                "carry. A lead already messaged on WhatsApp is left out of this "
                "campaign.")

        problems = self._channel_problems()
        self.channel_warning.setText(" ".join(problems))
        self.channel_warning.setVisible(bool(problems))

    def _channel_problems(self) -> list:
        """Everything that would stop this channel sending, in plain sentences."""
        if self._channel != WHATSAPP:
            return []
        problems = []
        if not self.settings.get("wa_enabled", False):
            problems.append("WhatsApp is switched off in Settings, so nothing "
                            "can be queued against the number.")
        elif self._copy(WHATSAPP) is None:
            problems.append("This build carries no WhatsApp copy, so there is "
                            "nothing to write from.")
        notice = self._wa_ban_notice()
        if notice:
            problems.append("%s Nothing will be sent on this channel until you "
                            "acknowledge it in Settings." % notice)
        link = self._wa_link()
        if not problems and link is not None and link.session_for_send() is None:
            problems.append("No WhatsApp connection is open. Preparing works "
                            "either way, but Settings has to be connected and "
                            "the QR scanned before a live run can send.")
        problems.extend(_campaign.wa_warnings(self.settings))
        return problems

    def _ai_summary(self) -> str:
        """Which provider writes the personalised lines, in a few words."""
        provider = _text_of(self.settings.get("ai_provider") or "auto").strip().lower()
        return _AI_PROVIDERS.get(provider, "AI: " + provider if provider else "AI: auto")

    def _rules_summary(self, channel: str = "") -> str:
        """The pacing rules in force, read off the settings themselves.

        Every one of these lives on the Settings screen and nowhere else, so a
        run that behaves unexpectedly — nothing going out on a Saturday, forty
        messages and then silence — has its explanation on the screen where it
        is happening rather than two screens away.

        One function for both channels, because it reads the numbers through
        `channel_settings` rather than by name: WhatsApp's window, caps, gaps,
        ramp and single chaser arrive under the keys the email path already
        used. Only the noun changes — Gmail has accounts and WhatsApp has one
        number, and "per account" over a set of one reads as a bug.
        """
        channel = channel or self._channel
        settings = self._channel_settings(channel)
        noun = self._sender_noun(channel)
        days = sorted({_int_of(d, -1) % 7 for d in (settings.get("send_days") or [])
                       if isinstance(d, (int, float))})
        when = ", ".join(_DAY_NAMES[d] for d in days) if days else "no days chosen"
        start = _int_of(settings.get("send_start_hour"), 9)
        end = _int_of(settings.get("send_end_hour"), 17)
        zone = _text_of(settings.get("send_timezone") or "local").strip()

        daily = _int_of(settings.get("daily_cap_per_account"), 40)
        hourly = _int_of(settings.get("hourly_cap_per_account"), 12)
        caps = "up to %d a day" % daily if daily > 0 else "no daily cap"
        caps += " and %d an hour" % hourly if hourly > 0 else " and no hourly cap"

        parts = ["%s, %d:00–%d:00 %s" % (when, start, end, zone),
                 "%s per %s" % (caps, noun)]
        if settings.get("warmup_enabled", True):
            parts.append("a new %s ramping from %d a day"
                         % (noun, _int_of(settings.get("warmup_start"), 10)))
        steps = _int_of(settings.get("followup_max_steps"), 2)
        if settings.get("followup_enabled", True) and steps > 0:
            parts.append("%s %d days apart" % (_plural(steps, "follow-up"),
                                               _int_of(settings.get("followup_gap_days"), 4)))
        else:
            parts.append("no follow-ups")
        return " · ".join(parts)

    def _profile_problems(self) -> list:
        """(what is missing, what it costs) for every gap in the sender profile.

        Both halves, because a warning without its consequence is either ignored
        or obeyed for the wrong reason. The first entry is the only one that can
        end a campaign on its own — a message needs an address to leave from —
        and it says so rather than being dressed the same as the other two.
        """
        profile = self._profile()
        problems = []
        if not self._accounts():
            problems.append(
                ("WhatsApp is switched off in Settings"
                 if self._channel == WHATSAPP else
                 "no Gmail account is set up to send from",
                 "nothing can be queued until that is changed in Settings"))
        if not _text_of(profile.get("sender_name")).strip():
            problems.append(("your name is missing from the sign-off",
                             "the message says who it is from and names nobody"
                             if self._channel == WHATSAPP else
                             "the email closes with a company and no person behind it"))
        if self._channel != WHATSAPP and not _text_of(profile.get("postal_address")).strip():
            # Only on the email side, and that is not an oversight. The postal
            # address is CAN-SPAM's requirement of a commercial email; a
            # WhatsApp message carries its opt-out in the body instead, and a
            # street address pasted into a chat bubble is four of its sixty
            # words spent saying nothing to the reader.
            problems.append(("no postal address for the footer",
                             "CAN-SPAM requires one, and filters read a missing "
                             "address as bulk mail"))
        return problems

    def _profile_gate(self, verb: str) -> bool:
        """May this run go ahead? Asks first when the profile is unfinished.

        `require_profile_complete` decides whether the question is asked at all,
        never whether the answer may be yes: on, the user is shown what is
        missing and what it costs and can still choose `<verb> anyway`; off, the
        same list is printed as advice and the run starts. The Sender profile
        card carries the identical problems either way, so nothing about a
        campaign is decided in a dialog the user cannot get back to.
        """
        problems = self._profile_problems()
        if not problems:
            return True

        listed = "; ".join(problem for problem, _cost in problems)
        if not self.settings.get("require_profile_complete", True):
            self._toast("%s with an unfinished profile: %s. Warnings only — "
                        "switch the check back on in Settings."
                        % (verb.capitalize(), listed), tone="warning")
            return True

        tail = ("You can go ahead. Every one of these makes a stranger likelier "
                "to report the message, and a reported number is usually gone "
                "for good." if self._channel == WHATSAPP else
                "You can go ahead. Every one of these makes the spam folder more "
                "likely.") if self._accounts() else (
            "You can go ahead, but with no %s to send from there is nothing to "
            "queue this on." % self._sender_noun())
        go, stop_asking = self._ask(
            "%s with an unfinished profile?" % verb.capitalize(),
            "The sender profile is missing:\n\n%s\n\n%s"
            % ("\n".join("  •  %s — %s" % (problem, cost) for problem, cost in problems),
               tail),
            "%s anyway" % verb.capitalize())
        if stop_asking:
            self._remember("require_profile_complete", False)
            self._toast("This screen will warn about the sender profile from now "
                        "on and never stop you. Settings can put the check back.",
                        tone="warning")
        if not go:
            self.settings_signal.emit()
        return go

    def _ask(self, title: str, body: str, proceed: str) -> tuple:
        """A blocking question with a way through. (go ahead, stop asking).

        Its own dialog rather than `components.confirm()`, and the difference is
        the checkbox. `confirm()` remembers an answer in this process; this one
        writes `require_profile_complete` to the settings file, so the switch a
        user throws here is the same switch they find on the Settings screen.
        The shared dialog is what the destructive actions on this screen use —
        suppressing a selection, and the one button that mails real people.

        Two buttons and never one: a dialog whose only option is "OK" is a wall
        with a courtesy label on it. The checkbox is here because a user who
        overrides the same warning twice has told us what they want, and hunting
        for the setting that turns it off is a worse use of their afternoon than
        the warning ever saved them.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(body)
        # Bound to a name, not passed as a temporary: setCheckBox does not take
        # ownership on the Python side, so a temporary is freed the moment the
        # call returns and exec_() then dereferences it. That is an access
        # violation, not an exception -- the process disappears with no
        # traceback, on the first Prepare of a fresh install.
        remember = QCheckBox("Do not ask again — warn me, never stop me")
        box.setCheckBox(remember)
        settings_btn = box.addButton("Open Settings", QMessageBox.AcceptRole)
        proceed_btn = box.addButton(proceed, QMessageBox.DestructiveRole)
        box.setDefaultButton(settings_btn)
        box.exec_()
        checkbox = box.checkBox()
        return (box.clickedButton() is proceed_btn,
                bool(checkbox is not None and checkbox.isChecked()))

    def _remember(self, key: str, value) -> None:
        """Write one setting straight to disk from here.

        The Settings screen re-reads the file every time it opens, so a switch
        thrown from this screen is the same switch when the user goes looking
        for it. A store that will not take it leaves the choice live for this
        session rather than taking the screen down.
        """
        self.settings[key] = value
        try:
            _settings.save_settings(self.settings)
        except Exception:
            pass

    def _refresh_profile(self) -> None:
        profile = self._profile()
        lines = []
        company = _text_of(profile.get("company")).strip()
        who = ", ".join(p for p in (_text_of(profile.get("sender_name")).strip(),
                                    _text_of(profile.get("sender_title")).strip()) if p)
        if who:
            lines.append(who)
        if company:
            lines.append(company)
        accounts = self._accounts()
        if accounts and self._channel == WHATSAPP:
            # The number itself, when the connection can say what it is. It
            # cannot be read without a browser and planning must never open one,
            # so the ledger is keyed on the channel rather than on the number —
            # which is why this line asks the live connection instead of the
            # account row, and says so plainly when nothing is connected.
            link = self._wa_link()
            number = link.me() if link is not None else ""
            lines.append("Messaging from %s" % (number or "the WhatsApp number "
                                                "connected in Settings"))
        elif accounts:
            lines.append("Sending from "
                         + ", ".join(a["email"] for a in accounts[:_NAMED_IN_SUMMARY]))

        # Both halves of every problem, on the card and not only in the dialog:
        # a warning without its consequence is either ignored or obeyed for the
        # wrong reason, and the dialog is the one place the user cannot get
        # back to. The line above them is what says which of the three is the
        # one that can end a campaign on its own.
        problems = self._profile_problems()
        for problem, cost in problems:
            lines.append("Needs attention: %s — %s." % (problem, cost))
        self.profile_summary.setText("\n".join(lines) or "Nothing set up yet.")
        blocked = ("Nothing can be queued until WhatsApp is switched on in "
                   "Settings." if self._channel == WHATSAPP else
                   "Nothing can be queued until a Gmail account is added in "
                   "Settings.")
        self.profile_problem.setText(
            blocked if not self._accounts() else
            "%s will get more of this campaign reported."
            % _plural(len(problems), "thing").capitalize()
            if self._channel == WHATSAPP else
            "%s will push more of this campaign into spam folders."
            % _plural(len(problems), "thing").capitalize())
        self.profile_problem.setVisible(bool(problems))
        self.profile_fix_btn.setVisible(bool(problems))
        self.profile_fix_btn.setText(
            "Fix %s in Settings" % _plural(len(problems), "thing"))

        rules = self._channel_settings()
        steps = _int_of(rules.get("followup_max_steps"), 2)
        gap = _int_of(rules.get("followup_gap_days"), 4)
        if rules.get("followup_enabled", True) and steps > 0:
            self.followup_hint.setText(
                "%s queued alongside it, %d days apart, in the same %s."
                % (_plural(steps, "follow-up"), gap,
                   "chat" if self._channel == WHATSAPP else "thread"))
        else:
            self.followup_hint.setText("Follow-ups are switched off in Settings.")

    def _refresh_templates(self) -> None:
        """Rebuild the template list, keeping the chosen one where it survives.

        The catalogue is edited elsewhere in the app, so a template written a
        minute ago has to be pickable here without restarting — a screen holding
        the list it was built with makes the copy the user just wrote the one
        campaign they cannot send.
        """
        combo = self.template_combo
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        copy = self._copy()
        for tpl in (copy.for_step(0) if copy is not None else ()):
            combo.addItem(_text_of(getattr(tpl, "name", "")).strip()
                          or _text_of(getattr(tpl, "id", "")),
                          getattr(tpl, "id", ""))
        index = combo.findData(previous) if previous is not None else -1
        if index < 0 and combo.count():
            index = 0
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setEnabled(combo.count() > 0)
        combo.setToolTip(
            ("The first message each lead receives; the chaser is chosen for you"
             if self._channel == WHATSAPP else
             "The first email each lead receives; follow-ups are chosen for you")
            if combo.count() else "This build has no first-touch template")

    def _refresh_preview_choices(self) -> None:
        combo = self.preview_combo
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for lead in self._leads[:_PREVIEW_CAP]:
            combo.addItem(self._preview_label(lead), _int_of(lead.get("id")))
        if previous is not None:
            index = combo.findData(previous)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.setEnabled(combo.count() > 0)
        combo.setToolTip("" if combo.count() else
                         "Import leads on the Leads tab and they appear here")
        self._refresh_preview()

    def _preview_label(self, lead: dict) -> str:
        """The lead as the picker names it — by whatever this channel reaches it at.

        An address under a WhatsApp campaign is the wrong half of the record:
        the thing that decides whether this lead can be in the campaign at all
        is the number, and a picker that hides it is a picker whose selection
        cannot be checked.
        """
        whatsapp = self._channel == WHATSAPP
        reached = _text_of(lead.get("phone" if whatsapp else "email")).strip()
        if whatsapp and not reached:
            reached = "no number"
        return "%s — %s" % (_text_of(lead.get("name")).strip() or "Unnamed",
                            reached)

    def _select_preview_lead(self, lead_id: int) -> None:
        lead_id = _int_of(lead_id)
        index = self.preview_combo.findData(lead_id)
        if index < 0:
            # Past the combo's cap. Selecting a lead on the Leads tab must still
            # preview that lead rather than silently previewing someone else.
            lead = next((l for l in self._leads if _int_of(l.get("id")) == lead_id), None)
            if lead is None:
                return
            self.preview_combo.insertItem(0, self._preview_label(lead), lead_id)
            index = 0
        if index != self.preview_combo.currentIndex():
            self.preview_combo.setCurrentIndex(index)

    def _template_id(self) -> str:
        return _text_of(self.template_combo.currentData())

    def _first_touch_template(self):
        """The chosen template, or any first touch, or None.

        The combo is filled from this channel's own `for_step(0)`, so a chosen
        id that no longer resolves means either the catalogue moved under a
        screen that was already open or the channel just changed under it.
        Falling back to a real first touch keeps the preview drawing and the
        campaign preparable; None means there is no copy in the build for this
        channel at all, which is not something the user did.
        """
        copy = self._copy()
        if copy is None:
            return None
        template = copy.get(self._template_id())
        if template is not None:
            return template
        options = copy.for_step(0)
        return options[0] if options else None

    def _preview_target(self) -> dict:
        lead_id = _int_of(self.preview_combo.currentData())
        for lead in self._leads:
            if _int_of(lead.get("id")) == lead_id:
                return lead
        return self._leads[0] if self._leads else {}

    def _refresh_footer_hint(self) -> None:
        """Say what the footer under the preview does and does not carry.

        The sentence used to promise an unsubscribe line and an address in every
        message. Both are switches now, so the promise is read off the switches:
        a preview that shows a shorter footer than the reader expects has to be
        the preview saying so, not a surprise found in a spam folder later.
        """
        if self._channel == WHATSAPP:
            # No footer to describe and no switch that can take it off. The
            # opt-out is a line in the body, `render_wa` appends one to any
            # message that does not already teach it, and it is named after the
            # user's own `wa_opt_out_words` — so a reply matching it is one the
            # reply watcher will actually honour.
            words = [w for w in (self.settings.get("wa_opt_out_words") or []) if w]
            self.preview_hint.setText(
                "This is the message as it arrives, opt-out line included — "
                "every first touch carries one, and a reply matching “%s” "
                "suppresses that lead on both channels and cancels its chaser. "
                "No subject, no footer, no HTML." % (words[0] if words else "stop"))
            return
        missing = [name for name, key in (("unsubscribe line", "append_unsubscribe"),
                                          ("postal address", "append_postal_address"))
                   if not self.settings.get(key, True)]
        note = ("This is the message as it will be delivered, footer and "
                "unsubscribe line included. The follow-ups reuse the same details.")
        if missing:
            note = ("This is the message as it will be delivered. The %s %s switched "
                    "off in Settings, so %s left out of the footer — more of this "
                    "campaign will land in spam. Every message still carries the "
                    "invisible List-Unsubscribe header."
                    % (" and the ".join(missing), "is" if len(missing) == 1 else "are",
                       "it is" if len(missing) == 1 else "they are"))
        self.preview_hint.setText(note)

    def _dress_preview(self) -> None:
        """Put the pane into the shape the channel's message actually has.

        A WhatsApp message has no subject to caption and no HTML alternative to
        switch to, so neither control is left on screen offering a view of
        something that does not exist. The counter changes units with it: an
        inbox measures a subject in characters, and a chat bubble measures the
        whole message in words, which is the number that decides whether it
        reads as a note or as a wall.
        """
        whatsapp = self._channel == WHATSAPP
        self.preview_kind.setText("Message" if whatsapp else "Subject")
        self.subject_label.setVisible(not whatsapp)
        for tab in self.view_tabs:
            tab.setVisible(not whatsapp)
        if whatsapp and self.view_group.checkedId() != 0:
            self.view_group.button(0).setChecked(True)
        self._cap_preview()

    def _refresh_preview(self) -> None:
        self._refresh_footer_hint()
        self._dress_preview()
        whatsapp = self._channel == WHATSAPP
        lead = self._preview_target()
        if not lead:
            self.subject_label.setText("—")
            self.subject_count.setText("")
            self.preview_meta.setText("")
            self._show_paper(
                "Import some leads and this pane shows the exact message each "
                "one would receive, rendered with their own business name, "
                "their own headline gap and %s."
                % ("their own opt-out line" if whatsapp else "your footer"))
            return

        copy = self._copy()
        template = self._first_touch_template()
        if copy is None or template is None:
            self._show_paper(
                "This build has no first-touch WhatsApp message to preview."
                if whatsapp and copy is None else
                "This build has no first-touch template to preview. Reinstalling "
                "restores them; nothing you did caused this.")
            return

        audit = _loads(lead.get("audit_json"))
        site, phrase, raw = _site_failure(audit)
        if site:
            # Not "audit error". The audit ran; the site is what failed, and
            # the difference decides what the user does next — there is no
            # audit to re-run against a domain that does not resolve. The
            # consequence is on screen with it, because this pane is where the
            # user is deciding whether the message is worth sending.
            self.subject_label.setText("—")
            self.subject_count.setText(site)
            self.preview_meta.setText("To %s  ·  Website: %s" % (
                _text_of(lead.get("name")).strip() or "there",
                _text_of(lead.get("website")).strip() or "no website"))
            self._show_paper(
                "There is no message to preview: the crawl could not read this "
                "site — %s.\n\n%s\n"
                "This lead would still be %s, but with a form letter that "
                "says nothing about the business. Retry the crawl from the "
                "row's right-click menu, correct the address in the record, or "
                "take the lead out before you prepare the campaign."
                % (phrase, ("The crawl recorded: %s\n" % raw) if raw else "",
                   "messaged" if whatsapp else "mailed"))
            return
        ai = _loads(lead.get("ai_json"))
        ctx = _templates.build_context(lead, audit, ai, self._profile(),
                                       self.settings)
        if not whatsapp:
            # The same order the planner renders in, and the same condition: the
            # compliance footers are an email's unsubscribe sentence and postal
            # address, and a WhatsApp message carries its opt-out in the body
            # where the reader will actually see it.
            ctx = _campaign.apply_compliance(ctx, self.settings)
        subject, body_text, body_html = copy.render(template, ctx)

        if not copy.usable(subject, body_text):
            self.subject_label.setText("—")
            self.subject_count.setText("")
            self._show_paper(
                "This lead produced no usable copy. Audit it first — the "
                "template needs at least a business name to write %s."
                % ("an opening line" if whatsapp else "a subject"))
            return

        # The renderer already strips surviving tokens; this is the second lock
        # on the same door, because the cost of being wrong once is a stranger
        # reading "Hi {{first_name}}".
        leak = _TOKEN_RE.search(subject) or _TOKEN_RE.search(body_text) or \
            _TOKEN_RE.search(body_html)
        if leak:
            self.subject_label.setText("—")
            self.subject_count.setText("unresolved field")
            self._show_paper(
                "This message still contains an unresolved merge field (%s), so "
                "it is not being shown and would not be sent. Fill in the sender "
                "profile in Settings, or audit this lead, and check again."
                % leak.group(0))
            return

        if whatsapp:
            self._show_bubble(lead, body_text)
            return

        self.subject_label.setText(subject)
        over = len(subject) > _templates.SUBJECT_MAX
        self.subject_count.setText(
            "%d / %d characters%s" % (len(subject), _templates.SUBJECT_MAX,
                                      " — too long for an inbox" if over else ""))

        accounts = self._accounts()
        account = accounts[0] if accounts else {}
        sender = _text_of(account.get("display_name")).strip() or \
            _text_of(self._profile().get("sender_name")).strip()
        from_line = "%s <%s>" % (sender, account.get("email", "no account yet")) \
            if sender else _text_of(account.get("email")) or "no account yet"
        self.preview_meta.setText("To %s <%s>  ·  From %s" % (
            _text_of(lead.get("name")).strip() or "there",
            _text_of(lead.get("email")).strip(), from_line))

        if self.view_group.checkedId() == 1:
            self.preview.setHtml(body_html)
        else:
            self.preview.setHtml(self._as_paper(body_text))
        self._paint_paper()

    def _show_bubble(self, lead: dict, body_text: str) -> None:
        """The WhatsApp message, in the bubble and with the two facts about it.

        The word count is not decoration. Sixty words is the ceiling the shipped
        copy is written to and the number the whole register rests on: the same
        pitch at an email's hundred and twenty arrives on a phone as a wall from
        a number the reader does not recognise, which is what gets it reported.
        So the count is stated against its budget and says when it is over.

        The number is stated the way the transport will dial it, resolved
        through the same `to_wa_id` the planner and the send loop use — a
        preview showing the scraped text while the run refuses it would be the
        preview lying about the one thing that decides whether this lead is in
        the campaign at all.
        """
        words = (_wa_templates.word_count(body_text) if _wa_templates is not None
                 else len(_text_of(body_text).split()))
        budget = getattr(_wa_templates, "WA_MAX_WORDS", 0) if _wa_templates else 0
        self.subject_count.setText(
            "%s / %d%s" % (_plural(words, "word"), budget,
                           " — long for a chat message" if words > budget else "")
            if budget else _plural(words, "word"))

        phone = _text_of(lead.get("phone")).strip()
        region = _campaign.wa_region(self.settings)
        wa_id = _wa.to_wa_id(phone, region)
        if wa_id:
            to = "+%s" % wa_id
        elif phone:
            to = "%s — no country code%s" % (
                phone, "" if region else ", and no default region is set")
        else:
            to = "no number on this lead"
        link = self._wa_link()
        number = link.me() if link is not None else ""
        self.preview_meta.setText("To %s  ·  %s  ·  From %s" % (
            _text_of(lead.get("name")).strip() or "there", to,
            number or "the number connected in Settings"))

        lines = [html.escape(line.strip()) if line.strip() else "&nbsp;"
                 for line in _text_of(body_text).strip().splitlines()]
        self.preview.setHtml(_bubble_html(lines, "delivered"))
        self._paint_paper()

    def _as_paper(self, body_text: str) -> str:
        """Plain text laid out as the recipient's mail client would show it.

        Every value is inline and every one of them comes out of the **light**
        theme: a mail client is not wearing this app's palette, and an email
        preview that does not look like an email is not a preview.
        """
        blocks = []
        for para in re.split(r"\n\s*\n", _text_of(body_text).strip()):
            lines = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
            if lines:
                blocks.append('<p style="margin:0 0 %dpx 0;">%s</p>'
                              % (_PAPER.space["3"], "<br>".join(lines)))
        return _paper_html("".join(blocks), _PAPER.font["h3"][0],
                           _PAPER.color["text.primary"])

    def _show_paper(self, message: str) -> None:
        self.preview.setHtml(_paper_html(
            html.escape(message), _PAPER.font["body"][0],
            _PAPER.color["text.secondary"]))
        self._paint_paper()

    def _paint_paper(self) -> None:
        """The page behind the message, on the ground its channel is read on.

        White paper for an email, because that is what a mail client draws. The
        chat ground for WhatsApp, because the bubble is the paper there and a
        bubble on the same value as the page behind it stops being a bubble.
        """
        _paint_paper(self.preview,
                     "inset" if self._channel == WHATSAPP else "raised")

    def _on_prepare_clicked(self) -> None:
        if self._planning or self._auditing or not self._retire(self.plan_worker):
            self._toast("A crawl is still running — this starts the moment it "
                        "finishes, and nothing is lost by waiting.",
                        tone="warning")
            return

        leads = self._target_leads()
        if not leads:
            self._goto_tab(0)
            self._toast("Import some leads before preparing a campaign.",
                        tone="warning")
            return

        # Before the profile gate, because a standing restriction is not a
        # warning the user can choose past: `OutreachWorker` refuses to start a
        # WhatsApp run while one stands, so a campaign prepared now would sit in
        # the queue unable to move and the schedule this screen printed for it
        # would be fiction.
        notice = self._wa_ban_notice() if self._channel == WHATSAPP else ""
        if notice:
            self._toast("%s Nothing can be queued for WhatsApp until you open "
                        "Settings and acknowledge it." % notice, tone="danger",
                        action="Open Settings",
                        on_action=self.settings_signal.emit)
            return

        if not self._profile_gate("prepare"):
            return

        template = self._first_touch_template()
        if template is None:
            self._toast(
                "This build carries no first-touch WhatsApp message, so there "
                "is nothing to write from." if self._channel == WHATSAPP else
                "There is no first-touch template to write from, so this "
                "install has no copy. Reinstalling restores them.",
                tone="danger")
            return

        # The channel is first in the name because the campaign picker on the
        # Sending tab is the one place the two are listed together, and a run
        # started on the wrong one is refused by the worker rather than sent.
        name = "%s · %s · %s · %s" % (
            _CHANNEL_LABEL.get(self._channel, self._channel), template.name,
            _plural(len(leads), "lead"), datetime.now().strftime("%d %b %H:%M"))
        campaign_id = _db.create_campaign(self.conn, name, template.id,
                                          self._profile(), self.settings, status="preparing")
        if not campaign_id:
            self._toast("Could not create the campaign — the database is "
                        "unavailable.", tone="danger")
            return

        self._campaign_id = campaign_id
        self._planning = True
        self.prepare_btn.setEnabled(False)
        self.prepare_btn.hide()
        self.cancel_prepare_btn.setEnabled(True)
        self.cancel_prepare_btn.setText("Cancel preparation")
        self.cancel_prepare_btn.show()
        self.goto_sending_btn.hide()
        self.plan_progress.setRange(0, max(1, len(leads)))
        self.plan_progress.setValue(0)
        self.plan_progress.show()
        self.plan_warning.hide()
        self.plan_summary.setText(
            "Auditing and queueing %s. This crawls each website, so it takes a "
            "while." % _plural(len(leads), "lead"))

        worker = _PlanWorker(campaign_id, leads, template.id, self.settings,
                             channel=self._channel)
        worker.progress_signal.connect(self._on_plan_progress)
        worker.plan_signal.connect(self._on_plan_ready)
        worker.finished.connect(self._on_plan_finished)
        self.plan_worker = worker
        worker.start()
        self._publish_state()

    def _on_cancel_prepare_clicked(self) -> None:
        if self.plan_worker and self.plan_worker.isRunning():
            self.cancel_prepare_btn.setEnabled(False)
            self.cancel_prepare_btn.setText("Cancelling...")
            self.plan_worker.stop()

    def _on_plan_progress(self, done: int, total: int, message: str) -> None:
        self.plan_progress.setRange(0, max(1, total))
        self.plan_progress.setValue(done)
        self.plan_summary.setText("%d of %d — %s" % (done, total, message))

    def _on_plan_ready(self, plan: dict) -> None:
        """The plan came back. Show it, and let the user refuse it.

        The review is a gate rather than a receipt — refusing it deletes the
        messages the plan wrote — so it opens before anything on this screen
        starts describing the campaign as prepared.

        A plan with nothing in it does not open one. A dialog whose whole
        content is six zeros is a click the user has to make to get back to the
        screen that would have told them the same thing, and `plan_warning`
        already carries the reason.
        """
        self._plan = plan if isinstance(plan, dict) else {}
        self.plan_progress.hide()

        if (self._plan and _int_of(self._plan.get("queued"))
                and not self._plan.get("error")
                and not self._plan.get("cancelled")):
            dialog = _CampaignReviewDialog(self._plan, self)
            try:
                if dialog.exec_() != QDialog.Accepted:
                    _db.delete_campaign_messages(self.conn, self._campaign_id)
                    _db.set_campaign_status(self.conn, self._campaign_id, "failed")
                    self._campaign_id = 0
                    self._plan = {}
                    self._toast("Campaign plan discarded. Nothing is queued and "
                                "no lead was changed.", tone="warning")
            finally:
                dialog.deleteLater()

        self._refresh_plan_summary()
        if self._plan and _int_of(self._plan.get("queued")):
            self.goto_sending_btn.show()
        else:
            self.goto_sending_btn.hide()
        self._refresh_campaigns()
        if self.pages.currentIndex() == 0:
            self._reload_leads()
            self._leads_dirty = False
        else:
            self._leads_dirty = True

        if self.pages.currentIndex() == 3:
            self._refresh_stats()
            self._stats_dirty = False
        else:
            self._stats_dirty = True

    def _refresh_plan_summary(self) -> None:
        """What the card says about the schedule, at every stage it has one.

        Three stages and not one. Before a campaign is prepared the card used
        to be blank, which is the one moment the user is deciding whether to
        prepare it — so the window, the caps and the follow-up spacing were
        invisible exactly when they were being agreed to. After a plan there is
        the plan. After a failure there is the reason, and it is in the warning
        label rather than mixed into the summary, because "nothing was queued"
        must not read as a schedule.
        """
        warning = ""
        if not self._plan:
            summary = ("Nothing queued yet. When you prepare a campaign it "
                       "will send %s." % self._rules_summary())
            if self._channel == WHATSAPP:
                summary = ("Nothing queued yet. When you prepare a WhatsApp "
                           "campaign it will message %s." % self._rules_summary())
        elif _text_of(self._plan.get("error")) and not _int_of(self._plan.get("queued")):
            summary = ""
            warning = "Nothing was queued: %s." % _text_of(self._plan.get("error"))
        else:
            summary, warning = self._plan_sentence(self._plan)
            if self._plan.get("cancelled"):
                # Stopped part-way. What is queued is real, so say so rather
                # than presenting a partial plan as the whole campaign.
                summary = "Stopped before the whole list was planned.\n" + summary
        self.plan_summary.setText(summary)
        self.plan_warning.setText(warning)
        self.plan_warning.setVisible(bool(warning))

    def _plan_sentence(self, plan: dict) -> tuple:
        """(what was queued, what is wrong with it) — never one paragraph."""
        queued = _int_of(plan.get("queued"))
        days = max(1, _int_of(plan.get("days")))
        accounts = [a for a in (plan.get("accounts") or []) if a]
        cap = _int_of(plan.get("daily_cap"))
        # The plan's own channel and not the picker's: a summary is about the
        # queue that was written, and the picker can have moved since.
        channel = _campaign._channel(plan.get("channel"))
        noun = _CHANNEL_NOUN.get(channel, "message")

        head = "%s across %s" % (_plural(queued, noun), _plural(days, "day"))
        if cap and accounts:
            head += ", %d/day from %s" % (cap, _plural(len(accounts),
                                                       _CHANNEL_SENDER.get(
                                                           channel, "account")))
        first = plan.get("first_send")
        if first:
            head += ", first send %s" % _clock(first)

        lines = [head + "."]
        followups = _int_of(plan.get("followups"))
        if followups:
            lines.append("Plus %s on the same threads." % _plural(followups, "follow-up"))
        skipped = _int_of(plan.get("skipped"))
        if skipped:
            lines.append("%d skipped — %s."
                         % (skipped, _reason_list(plan.get("skip_reasons"))))
        last = plan.get("last_send")
        if last:
            lines.append("Last message lands %s." % _clock(last))

        warning = ""
        generic = _int_of(plan.get("generic"))
        if generic:
            # The one number a user cannot get anywhere else. Two hundred form
            # letters sent in the belief that they are personal is the failure
            # the accept-list in `core.templates` trades personalisation to
            # avoid, and it is only a good trade if it is visible.
            warning = ("%d of %s could not be personalised — %s."
                       % (generic, _plural(queued, "email"),
                          _generic_sentence(plan.get("generic_reasons"))))
        return "\n".join(lines), warning

    def _on_plan_finished(self) -> None:
        self._planning = False
        self.prepare_btn.setEnabled(True)
        self.prepare_btn.show()
        self.cancel_prepare_btn.hide()
        self._publish_state()

    # ── Sending ──────────────────────────────────────────────────────────────

    def _refresh_mode(self) -> None:
        """Say which mode the next run is in, and dress Start to match it.

        The banner this replaces was a full-width 44px QPushButton with a dashed
        border — the same component as the 28px badge in the header, at a
        different height, so the app's loudest safety statement was also its
        clearest example of one control with six sizes. The pill on the shell's
        bar says which mode this is on every screen; what belongs here is what
        the mode costs, and a Start button that cannot be mistaken for the other
        one.
        """
        dry = bool(self.settings.get("dry_run", True))
        self._tell_shell_mode()
        self._set_start_kind("primary" if dry else "danger_primary")

        if dry:
            self.start_btn.setText("Start rehearsal")
            self.send_note.setText(
                "Dry run: messages are rendered and logged, no SMTP connection "
                "is opened, nothing leaves this machine and no quota is spent. "
                "The queue goes back as it was, so the campaign is still ready "
                "to send for real. Turn dry run off in Settings when you are.\n"
                "%s. All of it is editable in Settings." % self._rules_summary())
        else:
            self.start_btn.setText("Start sending")
            self.send_note.setText(
                "Live: this mails real businesses from %s.\n%s. All of it is "
                "editable in Settings."
                % (_plural(len(self._accounts()), "account"),
                   self._rules_summary()))

    def _set_start_kind(self, kind: str) -> None:
        """Rebuild Start in the kind the mode calls for, in place.

        Rebuilt rather than repainted because `components.button` decides the
        colour from the kind at build time and that is the whole point of the
        kind: one green currently means Save, Start Scraping and "mail twenty
        real strangers", and `danger_primary` is the only filled red in the app
        precisely so the last of those cannot wear the first one's clothes.

        The glyph goes with the kind for the same reason. A rehearsal is a play
        button — something starts and nothing leaves — and a live run is the
        paper plane the shell's own LIVE badge carries, so the two states of
        this one control differ by colour, by word and by shape.
        """
        if self.start_btn.property("kind") == kind:
            return
        made = components.button(self.start_btn.text(), kind=kind, size="lg",
                                 icon=_START_ICONS.get(kind, "play"),
                                 on_click=self._on_start_clicked)
        made.setEnabled(self.start_btn.isEnabled())
        made.setToolTip(self.start_btn.toolTip())
        self.start_row.insertWidget(self.start_at, made)
        self.start_row.removeWidget(self.start_btn)
        self.start_btn.setParent(None)
        self.start_btn.deleteLater()
        self.start_btn = made

    def _refresh_campaigns(self) -> None:
        rows = _db.list_campaigns(self.conn)
        combo = self.campaign_combo
        previous = self._campaign_id or _int_of(combo.currentData())
        combo.blockSignals(True)
        combo.clear()
        for row in rows:
            combo.addItem("%s  ·  %s" % (_text_of(row.get("name")),
                                         _text_of(row.get("status")) or "draft"),
                          _int_of(row.get("id")))
        index = combo.findData(previous) if previous else -1
        if index < 0 and combo.count():
            index = 0
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._campaign_id = _int_of(combo.currentData())
        self._refresh_send_controls()

    def _on_campaign_changed(self, _index: int) -> None:
        self._campaign_id = _int_of(self.campaign_combo.currentData())
        self._refresh_stats()
        self._refresh_send_controls()

    def _ramp_start(self):
        """The warm-up origin the send loop is using for this campaign, or None.

        Asked for in one place because three callers needed it and one of them
        was not asking: `_room_today` computed a cap with no ramp while the
        account partition computed one with it, so Send now offered room on a
        day-one account that the loop would refuse.
        """
        if not self._campaign_id:
            return None
        try:
            return _campaign.campaign_start_day(self.conn, self._campaign_id,
                                                self.settings)
        except Exception:
            return None

    def _benched_today(self) -> set:
        """Accounts the run took out of service today, lowercased.

        A quota or auth failure retires an account for the calendar day, and
        the worker keeps that in a dict of its own that this screen has no
        business reaching into — but it also writes an `account_stopped` event
        before it does, which is the store's own record of the same fact and
        survives the worker, the run and the app being closed.

        Without this the screen counted a benched account as one with room and
        said "Sending" over a queue that could not move: the loop had nothing
        left to send from and the Accounts card showed 3 / 40 today, which
        reads as plenty.
        """
        now = time.time()
        if now - self._benched_at < _BENCH_TTL:
            return self._benched
        if self.conn is None:
            return set()
        try:
            midnight = _db._day_start(now, self.settings.get("send_timezone"))
            events = _db.recent_events(self.conn, limit=_BENCH_SCAN)
        except Exception:
            return set()
        benched = set()
        for event in events:
            if _float_of(event.get("ts")) < midnight:
                break
            if _text_of(event.get("kind")).strip() != "account_stopped":
                continue
            benched.add(_text_of(event.get("detail")).split(":", 1)[0]
                        .strip().lower())
        self._benched = {email for email in benched if email}
        self._benched_at = now
        return self._benched

    def _account_room(self) -> tuple:
        """(with room today, at their cap, benched by the run) — all lowercased.

        Three buckets and not two, because "no account can take this" has three
        causes and they need three different sentences: raise the cap, wait for
        midnight, or go and fix a password.
        """
        zone = self.settings.get("send_timezone")
        ramp = self._ramp_start()
        benched_today = self._benched_today()
        free, spent, benched = [], [], []
        for account in self._accounts():
            email = _text_of(account.get("email")).strip()
            if email.lower() in benched_today:
                benched.append(email)
                continue
            cap = max(1, _campaign.account_daily_cap(account, self.settings,
                                                     ramp_start=ramp))
            used = _db.sent_today(self.conn, email, zone)
            (spent if used >= cap else free).append(email)
        return free, spent, benched

    def _rehearsing(self) -> bool:
        """Whether what is running (or about to) is a dry run.

        Asked of the worker while one exists, and of the settings otherwise, so
        the screen cannot describe a live run in a rehearsal's words after the
        user flips the switch mid-campaign.
        """
        if self._sending and self.send_worker is not None:
            return bool(getattr(self.send_worker, "dry_run", False))
        return bool(self.settings.get("dry_run", True))

    def _send_health(self, stats=None) -> tuple:
        """(what the queue is doing, why it is not moving) — never "ready".

        The finding this closes is the worst kind: a campaign that had stalled
        reported itself as "N messages ready to send" while the queue sat
        frozen, so the screen's most reassuring sentence was printed at exactly
        the moment nothing was going to happen. Every branch below either
        describes movement or names what is stopping it, and every one of them
        also says *when it changes*, because "held" with no clock on it is a
        second way of saying nothing.

        The states, driven one at a time and each checked against what the
        screen paints (see the handover for the measurements):

          no campaign · stopping · paused · running and holding outside the
          window · running with every account at its cap · running with every
          account benched · running with no account at all · running and
          waiting on the pacing gap · running normally · stopped by the run
          itself · nothing queued · queued with no account configured · queued
          with every account benched · queued but nothing due · queued and due
          later · queued and overdue outside the window · queued and overdue
          with every account at its cap · queued, overdue and only waiting to
          be started.

        Three of them are new and each closes a measured lie. With no Gmail
        account set up at all a running campaign read "Every account has hit
        today's cap", which is a sentence about a set that is empty. An account
        the run had benched for the day after an AUTH failure was counted as
        one with room, so the screen said "Sending — 0 of 3" over a queue with
        nothing left to send from. And a campaign the run had stopped for that
        reason read "Not sending — press Start sending", which starts a run
        that stops again immediately.

        `stats` is the tally the caller already has. `_refresh_send_controls`
        reads it and then asked for it again through here, so every second of
        every run counted the same campaign twice.
        """
        stats = self._stats() if stats is None else stats
        queued, total = _int_of(stats.get("queued")), _int_of(stats.get("total"))
        # What the run has got through, counted the way this run counts. A
        # rehearsal writes 'rehearsed' and never 'sent', so reading `sent`
        # alone printed "Sending — 0 of 500 done" for the whole of a dry run:
        # the one number the user watches, frozen through the one operation it
        # was there to report on.
        rehearsing = self._rehearsing()
        verb, past = ("Rehearsing", "built") if rehearsing else ("Sending", "done")
        sent = _int_of(stats.get("rehearsed" if rehearsing else "sent"))
        done = "%d of %d %s" % (sent, total, past)

        if not self._campaign_id:
            return "No campaign yet", ("Prepare one on the Campaign tab and it "
                                       "appears here.")
        if self._sending:
            if self._stopping:
                return ("Stopping — %s" % done,
                        "Finishing the message in flight. Whatever is still "
                        "queued stays queued and keeps its times.")
            if self._paused:
                return ("Paused after %s" % done,
                        "The queue keeps its times. Press Resume to carry on.")
            # A running worker is not the same as a moving queue. Outside the
            # window the loop naps and the log says so exactly once, so the
            # screen read "Sending" while nothing left for hours -- the same
            # reassuring-at-the-worst-moment failure this function exists to
            # close, one branch further in.
            now = time.time()
            if not _campaign.in_send_window(now, self.settings):
                return ("Holding — %s" % done,
                        "Outside your sending window. The queue restarts at %s; "
                        "widen the window in Settings if that is too late."
                        % _clock(_campaign.next_window_open(now, self.settings)))
            free, spent, benched = self._account_room()
            if not free:
                return ("Holding — %s" % done, self._no_account_reason(
                    spent, benched, "This run cannot send another message "
                                    "until that changes."))
            due = self._next_due_ts()
            if due > now:
                # The pacing gap. The loop is awake and the account is free;
                # what is holding the queue is the random wait that keeps the
                # mailbox looking like a person typing. Saying only "Sending"
                # over a five-minute gap is how a working run gets stopped.
                return ("%s — %s, next at %s" % (verb, done, _clock(due)),
                        "Waiting out the gap between messages. Send now drops "
                        "the gap and the window; the daily caps stay.")
            return "%s — %s" % (verb, done), ""

        if queued <= 0:
            if _text_of(self._campaign_status()) == "failed":
                # Discarded at the review, or abandoned by a plan that threw.
                # It is still in the picker because the record of it is worth
                # keeping, but "nothing left in this campaign's queue" reads as
                # a campaign that finished, and this one never started.
                return ("This campaign was discarded — nothing was queued "
                        "from it",
                        "Prepare a new one on the Campaign tab. The leads it "
                        "would have gone to are untouched.")
            return ("Nothing left in this campaign's queue",
                    "Prepare another campaign on the Campaign tab to queue more.")

        free, spent, benched = self._account_room()
        if not free:
            return (self._no_account_headline(queued, spent, benched),
                    self._no_account_reason(
                        spent, benched,
                        "Until then nothing in this queue can leave."))

        if _text_of(self._campaign_status()) == "stopped":
            # The run took itself down — `_handle_failure` does that when the
            # last account is benched. Pressing Start again from here does the
            # same thing again, so the button is not the answer and the screen
            # must not imply it is.
            return ("Stopped — %s still queued" % _plural(queued, "message"),
                    "The run stopped itself because it had no account left to "
                    "send from. Fix the account in Settings, then press Start "
                    "sending; the queue kept its place.")

        due = self._next_due_ts()
        if not due:
            return self._nothing_due(queued)

        now = time.time()
        if due > now:
            return ("%s queued — the next one is due %s"
                    % (_plural(queued, "message"), _clock(due)),
                    "Nothing goes out before then. Press Start sending to have "
                    "the run waiting when it does.")
        if not _campaign.in_send_window(now, self.settings):
            return ("Held — %s overdue, outside the sending window"
                    % _plural(queued, "message"),
                    "The window reopens %s. Widen the days or the hours in "
                    "Settings to send sooner."
                    % _clock(_campaign.next_window_open(now, self.settings)))
        return ("Not sending — %s overdue since %s"
                % (_plural(queued, "message"), _clock(due)),
                "The window is open and there is room on the account. Nothing "
                "will leave until you press Start sending.")

    def _no_account_headline(self, queued: int, spent, benched) -> str:
        """The three ways "no account can take this" reads on one line.

        One headline used to cover all three and it named the rarest: a user
        whose single account had simply hit its cap was told there was "no
        account to send from", which sends them to Settings to add a second
        Gmail rather than to wait until midnight or raise a number.
        """
        if not spent and not benched:
            return ("Stalled — %s queued and no account to send from"
                    % _plural(queued, "message"))
        if benched and not spent:
            return ("Stalled — %s queued, %s out of action today"
                    % (_plural(queued, "message"),
                       "every account is" if len(benched) > 1
                       else "the account is"))
        if benched:
            return "Held — no account has room left today"
        return "Held — every account has hit today's cap"

    def _no_account_reason(self, spent, benched, tail: str) -> str:
        """Why nothing can send, when nothing can — by which of three it is.

        One sentence used to cover all three and it was written for the middle
        one, so a user with no Gmail account at all was told that "every
        account has hit today's cap" and a user whose password had just been
        rejected was told to raise the cap.
        """
        if benched and not spent:
            return ("%s stopped for today after the server refused it — an app "
                    "password or a quota. Fix it in Settings and start again. %s"
                    % (_listed(benched), tail))
        if benched:
            return ("%s is at today's cap and %s stopped after the server "
                    "refused it. Sending resumes at midnight, or sooner if you "
                    "raise the cap and fix the account in Settings. %s"
                    % (_plural(len(spent), "account").capitalize(),
                       _listed(benched), tail))
        if spent:
            return ("Every account has hit today's cap. Sending resumes at "
                    "midnight, or raise the cap in Settings. %s" % tail)
        return ("There is no Gmail account set up to send from. Add one, with "
                "a Google App Password, in Settings. %s" % tail)

    def _campaign_status(self) -> str:
        """What the store says this campaign is doing, or "" if it cannot say."""
        if not self._campaign_id or self.conn is None:
            return ""
        try:
            return _text_of(_db.get_campaign(self.conn,
                                             self._campaign_id).get("status"))
        except Exception:
            return ""

    def _nothing_due(self, queued: int) -> tuple:
        """Queued, with an account free, and `due_messages` returns nothing.

        Verified rather than asserted, and that is the change. The old branch
        said "every queued message is addressed to a suppressed address" — the
        one cause it could think of — without ever counting them, so a queue
        scheduled past the year `_next_due_ts` looks ahead was reported as a
        list of unsubscribes. Both causes are now counted, and a third answer
        exists for the case where it is neither.
        """
        suppressed = 0
        if self.conn is not None:
            try:
                suppressed = _db._scalar(
                    self.conn,
                    "SELECT COUNT(*) FROM messages "
                    "JOIN leads ON leads.id = messages.lead_id "
                    "JOIN suppression ON suppression.email = LOWER(leads.email) "
                    "WHERE messages.campaign_id = ? AND messages.status = 'queued'",
                    (self._campaign_id,))
            except Exception:
                suppressed = 0
        head = ("Stalled — %s queued, none of it can go out"
                % _plural(queued, "message"))
        if suppressed >= queued:
            return head, ("Every queued message is addressed to a suppressed "
                          "address, so the send loop skips all of them. Prepare "
                          "a new campaign from the leads you can still contact.")
        if suppressed:
            return head, ("%d of them are addressed to suppressed addresses; "
                          "the rest are scheduled further out than a year, "
                          "which the send loop will not reach. Prepare a "
                          "smaller campaign." % suppressed)
        return head, ("Nothing in the queue is due inside the next year — the "
                      "schedule has spread it further than the run will look. "
                      "Raise the daily cap in Settings, or prepare a smaller "
                      "campaign.")

    def _refresh_send_controls(self) -> None:
        stats = self._stats()
        queued = _int_of(stats.get("queued"))
        total = _int_of(stats.get("total"))

        # A control that cannot be pressed has to say what would let it be. The
        # status line underneath says it once for the screen; the tooltip says
        # it where the pointer already is.
        if self._sending:
            blocked = "This campaign is already sending — Pause or Stop first"
        elif not self._campaign_id:
            blocked = "Prepare a campaign on the Campaign tab and it appears here"
        elif queued <= 0:
            blocked = "Nothing is queued — prepare a campaign to put messages in the queue"
        else:
            blocked = ""
        self.start_btn.setEnabled(not blocked)
        self.start_btn.setToolTip(blocked or (
            "Rehearse this campaign's queue — nothing is sent"
            if self.settings.get("dry_run", True) else
            "Mail this campaign's queue to real businesses, on the schedule in "
            "Settings. You will be asked to confirm."))
        # Neither survives Stop being pressed, and that is not cosmetic:
        # this runs on a one-second timer, so `_on_stop_clicked` disabling them
        # was undone within a second and the user could press Stop again on a
        # run that was already stopping.
        stopping = self._sending and self._stopping
        self.pause_btn.setEnabled(self._sending and not stopping)
        self.pause_btn.setToolTip(
            "The run is already stopping" if stopping else
            "Hold the run where it is; the queue keeps its times"
            if self._sending else "Nothing is running")
        self.stop_btn.setEnabled(self._sending and not stopping)
        self.stop_btn.setToolTip(
            "Already stopping — it ends when the message in flight is done"
            if stopping else
            "Finish the message in flight and stop; whatever is queued stays queued"
            if self._sending else "Nothing is running")
        self.campaign_combo.setEnabled(not self._sending)
        self.campaign_combo.setToolTip(
            "Stop the run to switch campaigns" if self._sending else "")

        # Send now was the one control on this tab nothing ever disabled. It
        # sat lit with a tooltip promising to ignore the sending window over a
        # campaign that did not exist, and answered the press with a toast —
        # so the screen's answer to "why did nothing happen" arrived only after
        # the click, and only for the six seconds a toast lives.
        room = self._room_today() if self._campaign_id else 0
        if self._sending:
            waived = "The run is already going — Pause or Stop it first"
        elif not self._campaign_id:
            waived = "There is no campaign to send. Prepare one on the Campaign tab"
        elif queued <= 0:
            waived = "Nothing is queued — prepare a campaign first"
        elif room <= 0:
            waived = ("Every account has already sent its allowance for today. "
                      "The caps are what keep Google from closing the account, "
                      "so this cannot waive them")
        else:
            waived = ""
        self.send_now_btn.setEnabled(not waived)
        self.send_now_btn.setToolTip(waived or (
            "Ignore the sending window and the gap between messages, and send "
            "the next %s now. Daily caps still apply."
            % _plural(min(room, queued), "message")))

        self.send_progress.setRange(0, max(1, total))
        self.send_progress.setValue(total - queued if total else 0)

        headline, why = self._send_health(stats)
        self.send_status.setText(headline)
        self.send_reason.setText(why)
        self.send_reason.setVisible(bool(why))

    def _on_send_now_clicked(self) -> None:
        """Go now: waive the clock, keep the caps.

        The window and the random gap are there to keep a mailbox looking like
        a person typing, and a user who has decided to send anyway can waive
        that for themselves. The daily and hourly caps are not the same kind of
        rule -- they are what keeps Google from shutting the account -- so this
        cannot lift them, and it says how many it can actually take.
        """
        room = self._room_today()
        if room <= 0:
            self._toast("Every account has already sent its allowance for today. "
                        "Raise the daily cap in Settings, or add another account.",
                        tone="warning")
            return
        queued = _int_of(self._stats().get("queued"))
        going = min(room, queued)
        if going <= 0:
            self._toast("This campaign has nothing queued. Prepare one first.",
                        tone="warning")
            return
        dry = bool(self.settings.get("dry_run", True))
        if not dry and not components.confirm(
                self,
                title="Send %s now?" % _plural(going, "message"),
                body="This ignores your sending window and the gap between "
                     "messages, so %s leaves as fast as the server accepts it. "
                     "Daily caps still apply.\n\nThere is no way to recall a "
                     "message once it has left." % _plural(going, "message"),
                confirm_text="Send now",
                danger=True, remember_key=""):
            return
        moved = release_now(self.conn, self._campaign_id, going)
        if moved <= 0:
            self._toast("Nothing moved forward. The queue may have just emptied.",
                        tone="warning")
            return
        # The claim comes after the run, not before it. `_on_start_clicked` has
        # a gate of its own — the sender profile — and a user who answered it
        # with Open Settings used to be told "Sending 40 messages now" over a
        # run that never started, with forty rows already pulled forward.
        self._on_start_clicked(ignore_schedule=True, confirmed=True)
        if self._sending:
            self._toast("Sending %s now." % _plural(moved, "message"), tone="info")
        else:
            self._toast("%s moved to the front of the queue, but the run did "
                        "not start. Press Start sending when you are ready."
                        % _plural(moved, "message").capitalize(), tone="warning")

    def _room_today(self) -> int:
        """How many this campaign could still send today across every account.

        The same three things `_send_health` reads, and that is the fix: this
        computed a cap with no warm-up ramp while `_account_room` computed one
        with it, and it counted an account the run had benched for the day as
        one with room. Both made Send now offer a number the loop would not
        honour — on a day-one account with `warmup_started` unset the cap is 10
        rather than 40, so "Sending 40 messages now" moved forty rows forward
        and thirty of them sat there.
        """
        ramp = self._ramp_start()
        zone = _campaign._zone(self.settings)
        benched = self._benched_today()
        total = 0
        for account in self._accounts():
            email = _text_of(account.get("email")).strip()
            if email.lower() in benched:
                continue
            try:
                cap = account_daily_cap(account, self.settings, ramp_start=ramp)
                used = _db.sent_today(self.conn, email, zone)
                total += max(0, _int_of(cap) - _int_of(used))
            except Exception:
                continue
        return total

    def _on_start_clicked(self, ignore_schedule: bool = False,
                          confirmed: bool = False) -> None:
        if self._sending:
            return
        if self._planning:
            self._toast("Cannot start sending while campaign preparation is active.",
                        tone="warning")
            return
        if not self._retire(self.send_worker):
            self._toast("The last run is still closing its connection. Press "
                        "Start again in a moment.", tone="warning")
            return
        if not self._campaign_id:
            self._goto_tab(1)
            self._toast("There is no campaign yet. Prepare one here and it "
                        "appears on the Sending tab.", tone="warning")
            return
        queued = _int_of(self._stats().get("queued"))
        if queued <= 0:
            self._toast("This campaign has nothing queued. Prepare one first.",
                        tone="warning")
            return
        if not self._profile_gate("send"):
            return

        dry = bool(self.settings.get("dry_run", True))
        if not dry:
            warnings = _campaign.optout_warnings(self.settings, self._profile())
            if warnings:
                body = (
                    "Live sending blocked: The following opt-out routes are not monitored, "
                    "which violates compliance standards:\n\n"
                    + "\n".join("• " + w for w in warnings)
                    + "\n\nTo comply with opt-out safety, please enable IMAP for these accounts. "
                    "If you are sure and wish to proceed anyway, confirm below."
                )
                if not components.confirm(
                        self,
                        title="Opt-out routes not monitored!",
                        body=body,
                        confirm_text="Override & Send anyway",
                        danger=True,
                        remember_key=""):
                    return

        if not dry and not confirmed and not components.confirm(
                self,
                title="Mail %s to real businesses?" % _plural(queued, "message"),
                body="Dry run is off, so this campaign sends for real, from %s, "
                     "on the schedule in Settings: %s.\n\nThere is no way to "
                     "recall a message once it has left."
                     % (_plural(len(self._accounts()), "account"),
                        self._rules_summary()),
                confirm_text="Send for real",
                danger=True, remember_key=""):
            return

        self._clear_log()
        self._rehearsed.clear()
        self._stopping = False
        worker = OutreachWorker(self._campaign_id, self.settings, dry_run=dry,
                                ignore_schedule=bool(ignore_schedule))
        worker.log_signal.connect(self._append_log)
        worker.progress_signal.connect(self._on_send_progress)
        worker.message_sent_signal.connect(self._on_message_sent)
        worker.stats_signal.connect(self._on_stats_signal)
        worker.error_signal.connect(lambda msg: self._append_log(msg, "error"))
        worker.done_signal.connect(self._on_send_done)
        self.send_worker = worker

        self._sending = True
        self._paused = False
        self.pause_btn.setText("Pause")
        self._refresh_send_controls()
        worker.start()
        self._publish_state()

    def _on_pause_clicked(self) -> None:
        if self.send_worker is None or not self._sending:
            return
        if self._paused:
            self.send_worker.resume()
            self._paused = False
            self.pause_btn.setText("Pause")
        else:
            self.send_worker.pause()
            self._paused = True
            self.pause_btn.setText("Resume")
        self._refresh_send_controls()
        self._publish_state()

    def _on_stop_clicked(self) -> None:
        """Ask the run to finish what it is holding and come back.

        The flag rather than only the label, because the label did not survive:
        `_on_tick` repaints the status line every second off `_send_health`, so
        the sentence this used to write lived for under a second and the screen
        went back to saying "Sending" over a run that was on its way down. A
        stop can take as long as an SMTP hand-off, and for the whole of it the
        screen has to say so.
        """
        if self.send_worker is None or not self._sending:
            return
        self._stopping = True
        self.send_worker.stop()
        self._refresh_send_controls()
        self._publish_state()

    def _on_send_progress(self, done: int, total: int) -> None:
        self.send_progress.setRange(0, max(1, total))
        self.send_progress.setValue(done)

    def _on_message_sent(self, row: dict) -> None:
        if not isinstance(row, dict):
            return
        rehearsal = getattr(self.send_worker, "dry_run", False)
        if rehearsal:
            key = _text_of(row.get("account_email")).strip().lower()
            if key:
                self._rehearsed[key] = self._rehearsed.get(key, 0) + 1
                self._refresh_accounts()
        lead = _db.get_lead(self.conn, _int_of(row.get("lead_id")))
        who = _text_of(lead.get("name")).strip() or _text_of(lead.get("email")).strip()
        step = _int_of(row.get("step"))
        label = "follow-up %d" % step if step else "first touch"
        # The log line carries the row it is about, so the message can be
        # opened from the tab the user is already watching. A rehearsal carries
        # nothing: nothing left, so there is nothing to read back.
        self._append_log("%s — %s" % (who or "lead", label), "done",
                         message_id=0 if rehearsal else _int_of(row.get("id")))

    def _on_stats_signal(self, stats: dict) -> None:
        if isinstance(stats, dict):
            self._paint_stats(stats)
        self._refresh_send_controls()
        self._refresh_accounts()
        self._publish_state()

    def _on_send_done(self) -> None:
        self._sending = False
        self._paused = False
        self._stopping = False
        self.pause_btn.setText("Pause")
        self._reload_leads()
        self._refresh_stats()
        self._refresh_campaigns()
        self._publish_state()

    # ── Sending: the activity log ────────────────────────────────────────────

    def _clear_log(self) -> None:
        self._log_lines = []
        self._repaint_log()

    def _on_log_cleared(self) -> None:
        """The console's own Clear, reaching the record behind it.

        The console has already emptied itself by the time this runs; what is
        left is the screen's copy, which is what a rebuild reads.
        """
        self._log_lines = []

    def _append_log(self, message: str, level: str = "info",
                    message_id: int = 0) -> None:
        """One stamped line, without pulling the reader off what they are on.

        The console puts each new line at the top, which is the right end for a
        panel nobody scrolls: the line being waited for is the one on screen.
        It is the wrong end for the one moment somebody *is* scrolling — a run
        that logs a line a second used to walk whatever they were reading one
        row further down every second. Moving the bar by the height of the line
        that arrived keeps that line where it was; at the top, which is where
        the panel sits unless it has been scrolled, nothing moves at all.
        """
        level = _text_of(level)
        if level == "error":
            # An account benched by an AUTH or QUOTA refusal arrives here, and
            # `_benched_today` is memoised for two seconds so the once-a-second
            # status line does not read the event log three times. Spending the
            # memo on the one signal that can change it means the Accounts card
            # and the status line say "stopped for today" on the same tick the
            # log does, rather than a second or two behind it.
            self._benched_at = 0.0
        line = "%s  %s" % (datetime.now().strftime(components.STAMP),
                           _text_of(message))
        self._log_lines.insert(0, (line, level, _int_of(message_id)))
        del self._log_lines[_LOG_LIMIT:]
        bar = self.log_list.verticalScrollBar()
        held = bar.value()
        self._push_log(line, level, _int_of(message_id))
        if held > 0:
            first = self.log_list.item(0)
            bar.setValue(held + (self.log_list.visualItemRect(first).height()
                                 if first is not None else 0))

    def _repaint_log(self) -> None:
        """Put every line the screen holds into a console that has none.

        Called with a console that has just been built — at startup, and again
        when a palette change rebuilds every widget on this screen — because
        the log is the one thing here that cannot be read back out of the
        database.

        Written into the console's line store in one pass rather than appended
        line by line, and that is a measurement rather than a preference.
        `_LogConsole.append` redraws the whole list per line, which is the
        right shape for a line arriving off a worker and quadratic for a
        restore: a full 400-line log costs **5,424ms** of a palette change that
        way and **9ms** this way, measured inside the rebuild both times, on
        the one screen the user is looking at while it happens — which takes
        the whole change from 5,658ms to 425. The console publishes `lines()`
        and no way to put them back: the shape written here is the shape
        `lines()` reads, and a `set_lines()` beside it is where this belongs.
        See the handover note.

        The stamp travels inside the line rather than being taken from the
        clock on the way in, which is the whole reason the lines are kept as
        text: re-stamped on a rebuild, a campaign that had been running for
        three days would come back claiming every message left at the moment
        the user changed theme.
        """
        panel = self.log_panel
        panel._lines = [("", line, level, message_id or None,
                         self._log_tip(message_id))
                        for line, level, message_id in self._log_lines]
        panel._repaint()

    def _push_log(self, line: str, level: str, message_id: int) -> None:
        """One line into the console, carrying the message it can open."""
        self.log_panel.append(line, level=level, stamp=False,
                              data=message_id or None,
                              tooltip=self._log_tip(message_id))

    @staticmethod
    def _log_tip(message_id: int) -> str:
        """What hovering a line says, and only for a line that opens onto one."""
        return "Double-click to read exactly what was sent" if message_id else ""

    def _on_log_activated(self, message_id) -> None:
        """A double-clicked line opens the message it is about.

        The console hands back what the line was given rather than a row
        number, which is what makes this survive four hundred inserts above it.
        A rehearsal's line carries nothing, because nothing left to read.
        """
        self._open_sent_message(_int_of(message_id))

    # ── Sending: accounts and countdown ──────────────────────────────────────

    def _refresh_accounts(self) -> None:
        t = components.active_theme()
        _clear_layout(self.accounts_holder)
        accounts = self._accounts()
        if not accounts:
            self.accounts_holder.addWidget(components.hint(
                "No Gmail account yet. Add one in Settings — outreach needs an "
                "address and a Google App Password to send from."))
            return

        zone = self.settings.get("send_timezone")
        # The same warm-up origin the send loop uses. Without it an account with
        # no `warmup_started` would be shown its first-day cap on day nine of the
        # campaign, and the counter would read as stuck.
        ramp = self._ramp_start()
        benched = self._benched_today()

        for account in accounts:
            email = _text_of(account.get("email"))
            used = _db.sent_today(self.conn, email, zone)
            rehearsed = self._rehearsed.get(email.strip().lower(), 0)
            cap = max(1, _campaign.account_daily_cap(account, self.settings, ramp_start=ramp))
            out = email.strip().lower() in benched

            holder = QWidget()
            row = _rows(holder, margin="0", spacing="1", t=t)
            head = _cols(margin="0", spacing="2", t=t)
            address = _ElidedLabel(email)
            head.addWidget(address, stretch=1)

            # Two separate numbers, never one sum: a rehearsal has spent none of
            # this account's real quota and the card must not imply that it has.
            counter = components.body_label(
                "%d / %d today" % (used, cap),
                tone="danger" if out or used >= cap else "secondary")
            counter.setWordWrap(False)
            tip = "Daily cap for this account, warm-up included"
            if rehearsed:
                counter.setText("%d rehearsed  ·  %d / %d today"
                                % (rehearsed, used, cap))
                tip += ".  %s rehearsed in this dry run — no real quota spent." \
                    % _plural(rehearsed, "message")
            if out:
                # An account with quota left that the run will not use is the
                # card's worst reading: "3 / 40 today" says there is plenty of
                # room on an address the server has already refused, which is
                # exactly the screen looking healthy while the queue is dead.
                counter.setText("stopped for today")
                tip = ("The server refused this account today — an app password "
                       "or a quota — so the run will not use it again until "
                       "midnight. Fix it in Settings and start again.")
            counter.setToolTip(tip)
            head.addWidget(counter)
            row.addLayout(head)

            bar = _thin_bar(t)
            bar.setRange(0, cap)
            # The rehearsal moves the bar because the bar is what the user
            # watches while the run goes; `used` is what governs the cap. A
            # benched account's bar is full whatever its count says, because
            # what it measures is how much of this account is available and the
            # answer is none of it.
            bar.setValue(cap if out else min(used + rehearsed, cap))
            row.addWidget(bar)
            self.accounts_holder.addWidget(holder)

    def _next_due_ts(self) -> float:
        """When this campaign's next queued message is due. 0.0 if none.

        Read through `due_messages` with a far horizon rather than a query of
        its own, so the countdown and the send loop agree about which row is
        next — including the suppression filter that view applies, and the
        campaign filter, which has to be part of the query. Narrowing a fixed
        window of rows afterwards found none of this campaign's whenever
        another campaign had that many queued ahead of it, and `_send_health`
        reads an empty answer as "every address here is suppressed".
        """
        if not self._campaign_id:
            return 0.0
        horizon = time.time() + 366 * _DAY_SEC
        rows = _db.due_messages(self.conn, horizon, limit=1,
                                campaign_id=self._campaign_id)
        return _float_of(rows[0].get("scheduled_at")) if rows else 0.0

    def _on_tick(self) -> None:
        """The clock on the right of the progress line, once a second.

        Every branch names a time, because this label is the only thing on the
        screen that counts down and a queue with no clock on it is a queue the
        user has no way to distinguish from a broken one. The two that used to
        say nothing are the two the brief asked about: a run that is stopping,
        and a queue with nothing due at all.
        """
        if self.pages.currentIndex() != 2 and not self._sending:
            return
        due = self._next_due_ts()
        now = time.time()
        if self._sending and self._stopping:
            self.next_send_label.setText("Stopping…")
        elif not due:
            self.next_send_label.setText(
                "Nothing due" if self._campaign_id else "")
        elif self._paused:
            self.next_send_label.setText("Paused · next was due %s" % _clock(due))
        elif due > now:
            self.next_send_label.setText("Next send in %s · %s"
                                         % (_countdown(due - now), _clock(due)))
        elif self._sending:
            self.next_send_label.setText("Sending now")
        else:
            self.next_send_label.setText("Overdue since %s" % _clock(due))
        self._refresh_send_controls()

    # ── Stats ────────────────────────────────────────────────────────────────

    def _stats(self) -> dict:
        if not self._campaign_id:
            return {}
        return _db.campaign_stats(self.conn, self._campaign_id)

    def _refresh_stats(self) -> None:
        self._paint_stats(self._stats())
        self._refresh_personalisation()
        self._refresh_days()
        self._refresh_sent_mail()
        self._refresh_suppression()
        row = _db.get_campaign(self.conn, self._campaign_id) if self._campaign_id else {}
        self.stats_campaign.setText(_text_of(row.get("name")) or "No campaign yet")

    # Tiles that earn their place only when they have something to say. A zero
    # here is not a finding, and six digits the user has to read past to reach
    # the one that moved is worse than five.
    _TILES_WHEN_NONZERO = ("rehearsed",)

    def _paint_stats(self, stats: dict) -> None:
        stats = stats if isinstance(stats, dict) else {}
        for key, tile in self.tiles.items():
            tile.value_label.setText(str(_int_of(stats.get(key))))
        self._layout_tiles()

    def _layout_tiles(self) -> None:
        """Lay out the tiles that have something to say, four to a row.

        Which those are is read off the numbers the tiles are carrying rather
        than off their visibility, and both halves of that matter. A widget
        that has never been on screen reports itself hidden, so a dashboard
        laid out from `isHidden()` at build time is a dashboard with nothing in
        it; and a tile's number is the same thing its visibility was decided
        from anyway, so asking the number is asking one question instead of
        keeping two answers in step.
        """
        self._flow_tiles(tuple(
            key for key, _caption, _tone, _note in self._TILES
            if key not in self._TILES_WHEN_NONZERO
            or _int_of(self.tiles[key].value_label.text())))

    def _flow_tiles(self, shown: tuple) -> None:
        """Put `shown` on the grid in order, and take the rest off it.

        Re-flowed rather than left with a hole in it. Rehearsed is hidden on
        every campaign nobody has rehearsed, and a dashboard with a gap in the
        middle of it reads as a number that failed to arrive rather than as one
        that was never asked for.

        A tile that comes off the grid is hidden with it, because a widget
        removed from a layout keeps its parent: left alone it would go on
        painting itself wherever the layout last put it, which for a tile that
        has never been laid out at all is the top-left corner of the page.

        Nothing happens when the same tiles are showing as last time, which is
        every call but two in a campaign's life: this runs on every stats
        signal, and a running send loop emits one of those per message.
        """
        if shown == self._tiles_shown:
            return
        self._tiles_shown = shown
        while self.tiles_grid.count():
            self.tiles_grid.takeAt(0)
        for at, key in enumerate(shown):
            self.tiles_grid.addWidget(self.tiles[key], at // _TILES_PER_ROW,
                                      at % _TILES_PER_ROW)
        for key, tile in self.tiles.items():
            tile.setVisible(key in shown)

    # A campaign of five hundred is a long list to walk on a tab change, and the
    # answer past a couple of thousand would not change what the user does about
    # it. Each lead past the cap is a rendered context, at a third of a
    # millisecond each.
    _PERSONAL_SCAN_MAX = 2000

    def _personalisation_counts(self) -> tuple:
        """(generic, first touches, why) for the campaign on screen.

        Worked out from the leads rather than stored against the messages:
        `core.outreach_db` has no column for it, and the answer is a property of
        the audit the message was rendered from, which is still on the lead.
        Read through `core.templates` so this and the Campaign tab cannot come
        to different conclusions about the same email.
        """
        if not self._campaign_id or self.conn is None:
            return 0, 0, {}
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT leads.* FROM messages JOIN leads "
                "ON leads.id = messages.lead_id "
                "WHERE messages.campaign_id = ? AND messages.step = 0 LIMIT ?",
                (self._campaign_id, self._PERSONAL_SCAN_MAX)).fetchall()
        except Exception:
            return 0, 0, {}

        profile = self._profile()
        counts: dict = {}
        for row in rows:
            lead = dict(row)
            try:
                ok, reason = _templates.personalisation(
                    lead, _loads(lead.get("audit_json")), _loads(lead.get("ai_json")),
                    profile, self.settings)
            except Exception:
                continue
            if not ok:
                counts[reason] = counts.get(reason, 0) + 1
        return sum(counts.values()), len(rows), counts

    def _refresh_personalisation(self) -> None:
        generic, total, why = self._personalisation_counts()
        if not total:
            self.personal_note.setText("")
        elif generic:
            self.personal_note.setText(
                "%d of %s could not be personalised — %s. Those say nothing "
                "about the business they are addressed to beyond its name."
                % (generic, _plural(total, "email"), _generic_sentence(why)))
        else:
            self.personal_note.setText(
                "All %s in this campaign say something about the business they "
                "are addressed to." % _plural(total, "email"))
        self.personal_note.setVisible(bool(total))

    def _per_day(self) -> list:
        """(day label, sent, still queued) per day for this campaign.

        Bucketed here rather than in SQL because the day boundary is the user's
        local one, and because `core.outreach_db` exposes no per-day rollup —
        see the note in the handover about adding one.
        """
        if not self._campaign_id or self.conn is None:
            return []
        try:
            rows = self.conn.execute(
                "SELECT status, scheduled_at, sent_at FROM messages "
                "WHERE campaign_id = ?", (self._campaign_id,)).fetchall()
        except Exception:
            return []

        buckets: dict = {}
        for row in rows:
            status = _text_of(row["status"])
            when = row["sent_at"] if status == "sent" and row["sent_at"] else row["scheduled_at"]
            try:
                day = datetime.fromtimestamp(float(when or 0.0)).date()
            except (OSError, OverflowError, TypeError, ValueError):
                continue
            slot = buckets.setdefault(day.isoformat(), [0, 0])
            slot[0 if status == "sent" else 1] += 1

        return [(datetime.fromisoformat(day).strftime("%a %d"), sent, queued)
                for day, (sent, queued) in sorted(buckets.items())]

    def _refresh_days(self) -> None:
        days = self._per_day()
        self.day_bars.set_days(days)
        self.day_bars.setVisible(bool(days))
        self.bars_empty.setVisible(not days)

    # How many sent messages the list holds. A campaign of five hundred with
    # two chasers each is fifteen hundred rows, and nobody scrolls to the
    # bottom of that; the recent ones are the ones a question is ever about.
    _SENT_SHOWN = 200

    def _refresh_sent_mail(self) -> None:
        rows = _db.sent_messages(self.conn, self._campaign_id,
                                 limit=self._SENT_SHOWN) if self._campaign_id else []
        self.sent_list.clear()
        for row in rows:
            step = _int_of(row.get("step"))
            item = QListWidgetItem("%s  ·  %s  ·  %s" % (
                _clock(_float_of(row.get("sent_at"))),
                _text_of(row.get("to_email")) or "unknown address",
                _clip(_text_of(row.get("subject")), _SUPPRESSION_CH)))
            item.setData(Qt.UserRole, _int_of(row.get("id")))
            # Whether the full message can be opened is a fact about the row,
            # not a guess made after the click: messages sent by a build that
            # kept no transcript have none, and saying so up front beats an
            # empty dialog.
            item.setData(Qt.UserRole + 1, bool(row.get("has_transcript")))
            item.setToolTip("%s from %s%s" % (
                "Follow-up %d" % step if step else "First touch",
                _text_of(row.get("account_email")) or "an unknown account",
                "" if row.get("has_transcript") else
                " — no copy was kept of this one"))
            self.sent_list.addItem(item)
        if not rows:
            item = QListWidgetItem(
                "Nothing has been sent from this campaign yet. Each message "
                "appears here once it reaches the server."
                if self._campaign_id else
                "No campaign yet. Prepare one on the Campaign tab.")
            item.setFlags(Qt.NoItemFlags)
            self.sent_list.addItem(item)
        self.sent_count.setText(_plural(len(rows), "message"))
        self._refresh_sent_actions()

    def _refresh_sent_actions(self) -> None:
        item = self.sent_list.currentItem()
        message_id = _int_of(item.data(Qt.UserRole)) if item is not None else 0
        self.open_sent_btn.setEnabled(bool(message_id))
        self.open_sent_btn.setToolTip(
            "Read the message exactly as it was handed to Gmail"
            if message_id else "Select a sent message first")

    def _on_open_sent_clicked(self) -> None:
        item = self.sent_list.currentItem()
        if item is None or not _int_of(item.data(Qt.UserRole)):
            self._toast("Select a sent message first.", tone="warning")
            return
        self._on_sent_item_activated(item)

    def _on_sent_item_activated(self, item) -> None:
        message_id = _int_of(item.data(Qt.UserRole)) if item is not None else 0
        if not message_id:
            return
        if item.data(Qt.UserRole + 1) is False:
            self._toast("No copy was kept of that message — it went out before "
                        "MapHarvest started storing them.", tone="warning")
            return
        self._open_sent_message(message_id)

    def _open_sent_message(self, message_id: int) -> None:
        """Show one already-sent message, or say why it cannot be shown.

        The row and the transcript are read separately on purpose: the row is
        always there and carries who, when and from where, so a message whose
        bytes were never stored still opens and still answers three of the four
        questions rather than refusing.
        """
        message_id = _int_of(message_id)
        if not message_id or self.conn is None:
            return
        row = _db._one(self.conn, "SELECT messages.*, leads.email AS to_email, "
                                  "leads.name AS to_name FROM messages "
                                  "LEFT JOIN leads ON leads.id = messages.lead_id "
                                  "WHERE messages.id = ?", (message_id,))
        if not row:
            self._toast("That message is no longer in the store.", tone="warning")
            return
        _SentMailDialog(row, _db.transcript(self.conn, message_id), self).exec_()

    def _refresh_suppression(self) -> None:
        rows = _db.suppression_list(self.conn)
        self.supp_list.clear()
        for row in rows:
            email = _text_of(row.get("email"))
            reason = _text_of(row.get("reason")).strip()
            item = QListWidgetItem("%s%s" % (email, "  ·  %s" % reason if reason else ""))
            item.setData(Qt.UserRole, email)
            item.setToolTip("%s — %s" % (email, reason or "no reason recorded"))
            self.supp_list.addItem(item)
        if not rows:
            item = QListWidgetItem("Nobody has unsubscribed or been suppressed yet.")
            item.setFlags(Qt.NoItemFlags)
            self.supp_list.addItem(item)
        self.supp_count.setText(_plural(len(rows), "address"))
        self.unsuppress_btn.setEnabled(bool(rows))
        self.unsuppress_btn.setToolTip(
            "Let a suppressed address be contacted again; nothing is re-queued"
            if rows else "Nobody is on the do-not-contact list")

    def _on_unsuppress_clicked(self) -> None:
        item = self.supp_list.currentItem()
        email = _text_of(item.data(Qt.UserRole)) if item is not None else ""
        if not email:
            self._toast("Select an address to remove first.", tone="warning")
            return
        self._undo_suppress([(email, "new")])

    def _unsuppress(self, email: str) -> bool:
        """Drop one address from the do-not-contact list.

        Written against the connection because `core.outreach_db` has
        `suppress` but no inverse; the handover asks for one.
        """
        if self.conn is None:
            return False
        try:
            self.conn.execute("DELETE FROM suppression WHERE email = ?",
                              (_text_of(email).strip().lower(),))
            self.conn.commit()
            return True
        except Exception:
            return False

    # ── Chrome ───────────────────────────────────────────────────────────────

    def _goto_tab(self, index: int) -> None:
        index = max(0, min(len(self.TABS) - 1, _int_of(index)))
        self.pages.setCurrentIndex(index)
        self._on_tab_changed(index)
        self._tell_shell(index)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self._refresh_lead_actions()
            if self._leads_dirty:
                self._reload_leads()
                self._leads_dirty = False
        elif index == 1:
            self._refresh_templates()
            self._refresh_profile()
            self._refresh_preview()
            # The one place "how many of these would be a form letter" is
            # counted, because this is the one place it is read. See
            # `_form_letter_warning`.
            self._refresh_lead_actions()
        elif index == 2:
            self._refresh_accounts()
            self._refresh_send_controls()
        elif index == 3:
            if self._stats_dirty:
                self._refresh_stats()
                self._stats_dirty = False
            else:
                self._refresh_stats()

    def _toast(self, message: str, *, tone: str = "info", action=None,
               on_action=None):
        """One message, in a channel that says whether it is good news.

        The six-second QLabel this replaces said everything in one voice and
        took itself off screen whatever it said, so a suppression that could not
        be undone read exactly like an import that worked, and both were gone
        before they were read. A danger toast waits for the user, and one
        carrying an action waits long enough to reach it.
        """
        return self.toaster.show(message, tone=tone, action=action,
                                 on_action=on_action)
