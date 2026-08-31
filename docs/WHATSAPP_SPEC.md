# WhatsApp Outreach — Implementation Contract

Second channel alongside email. Same leads, same audit, same gap→service pitch — a
different transport, with tighter limits because the platform is less forgiving.

## 0. Why this shape

The reference (`D:\Projects\wa-bot`) uses `whatsapp-web.js`: Puppeteer drives
`web.whatsapp.com`, `LocalAuth` persists the session, login is a QR scan. That is the
right technique and the wrong runtime — it is Node, and this app is Python packaged into
a single Windows `.exe`. Bundling Node would add a second runtime to the build for a
capability the app already has.

**We drive `web.whatsapp.com` with Selenium + `undetected-chromedriver`, which
`core/scraper.py` already uses for Google Maps.** Same browser automation, same session
persistence via a Chrome user-data-dir, same QR login — no new runtime, no new dependency.

### The risk this design answers

WhatsApp bans numbers for bulk outreach far faster than Gmail suspends accounts, and
unlike email there is no CAN-SPAM equivalent permitting cold contact. Every limit below
is deliberately tighter than the email side, and the defaults assume the user would
rather send slowly than lose the number.

---

## 1. `core/whatsapp.py` (new) — the transport

```python
WA_STATE_DIR: str                      # <profile>/wa-session, a Chrome user-data-dir

class WhatsAppSession:
    def __init__(self, profile: str = "default", headless: bool = False)
    def start(self) -> tuple[bool, str]          # (ok, error)
    def status(self) -> str                      # "offline"|"qr"|"loading"|"ready"|"banned"
    def qr_png(self) -> bytes                    # b"" unless status == "qr"
    def me(self) -> str                          # the logged-in number, "" if unknown
    def send(self, phone: str, text: str) -> tuple[bool, str]
    def unread_replies(self, since_ts: float) -> list[dict]
    def close(self) -> None
    def __enter__/__exit__
```

- **Session persists.** `LocalAuth` in the reference is a data directory; here it is a
  Chrome user-data-dir under the profile. Scanning the QR once must survive a restart.
- **`status()` never blocks.** The UI polls it; a method that waits on the browser
  freezes the GUI.
- **`send` classifies its failures** the way `core/mailer.py` does, with the same prefixes
  so the campaign loop can reuse its logic: `AUTH:` (logged out, QR needed),
  `RECIPIENT:` (not on WhatsApp, invalid number), `RATE:` (throttled by the platform),
  `BANNED:` (account restricted — stop everything, this is not retryable),
  `CONN:` (browser or network), `OTHER:`.
- **`BANNED:` halts the whole run immediately** and says so. Continuing after a
  restriction is how a temporary block becomes permanent.
- Never raises across the boundary. Every wait has a timeout.

### Phone numbers

```python
def to_wa_id(phone: str, default_region: str = "") -> str
def is_plausible(phone: str) -> bool
```

Scraped Maps numbers arrive as `+1 416-555-0142`, `(416) 555-0142`, `0416 555 142`. A
number without a country code is **not** guessed into one silently — an unqualified
number that resolves to the wrong country messages a stranger abroad. `default_region`
comes from settings and the UI says which region it is applying.

---

## 2. `core/settings.py` — new keys

```python
"wa_enabled": False,
"wa_default_region": "",             # ISO code, e.g. "CA". Blank = require + prefix
"wa_headless": False,                # the QR needs a visible window the first time
"wa_daily_cap": 30,                  # deliberately lower than email's 40
"wa_hourly_cap": 8,
"wa_min_gap_sec": 90,                # slower than email's 60
"wa_max_gap_sec": 300,
"wa_warmup_enabled": True,
"wa_warmup_start": 5,
"wa_warmup_step": 3,
"wa_warmup_max": 30,
"wa_send_days": [0, 1, 2, 3, 4],
"wa_send_start_hour": 10,            # a message at 08:00 reads worse than an email
"wa_send_end_hour": 19,
"wa_followup_enabled": True,
"wa_followup_gap_days": 3,
"wa_followup_max_steps": 1,          # one chaser, not two
"wa_dry_run": True,                  # never surprise-send, same as email
"wa_opt_out_words": ["stop", "unsubscribe", "remove me", "do not message"],
```

---

## 3. `core/outreach_db.py` — channel-aware

The existing tables carry email. Add a `channel` column (`"email"` | `"whatsapp"`,
defaulting to `"email"`) to `messages` and `sends`, and migrate in place — an existing
database must open and keep working, with its rows reading as email.

```python
def suppress(conn, *, email: str = "", phone: str = "", reason: str) -> None
def is_suppressed(conn, *, email: str = "", phone: str = "") -> bool
```

**Suppression is shared across channels.** Someone who says stop on WhatsApp must not
receive the email sequence, and the reverse. That is the whole point of one lead pool.

Caps, `sent_today` and `sent_last_hour` are all per-channel: WhatsApp volume must not
consume the email allowance.

---

## 4. `core/wa_templates.py` (new) — the pitch, in WhatsApp register

Not a reuse of the email templates. A WhatsApp message is read on a phone, in a chat
thread, from an unknown number — the register is completely different:

- **Under 60 words.** An email of 120 words is normal; the same text on WhatsApp is a wall.
- **No subject, no signature block, no footer, no HTML.**
- **One question, one link at most.** No calendar link in the first message.
- Opens by saying who is writing and why *this* business, using the same audit gap the
  email uses — the specificity is the entire value.
- **Opt-out in the first message**, in plain words: a line telling them to reply STOP.
  Legally safer and, on WhatsApp, materially reduces being reported as spam.

```python
WA_TEMPLATES: list[Template]         # reuse the Template shape from core/templates.py
def render_wa(template, ctx) -> str  # one string; no subject, no HTML
```

Same merge fields and the same `_observed` guard on model output, so an AI line that
claims a track record is dropped here too. Same store shape as `core/templates.py`, so
the settings editor can edit these with the component it already has.

---

## 5. `core/campaign.py` — one scheduler, two channels

Do **not** fork the scheduler. `next_send_times` already enforces window, days, caps,
warm-up and jitter; it takes the numbers as arguments. Give it the channel's settings and
it schedules WhatsApp correctly for free, including the spill across days.

`OutreachWorker` gains a `channel` and picks its transport accordingly. Everything the
email path earned applies unchanged: pacing consumed on every attempt, `BANNED:`/`AUTH:`
stopping the run, crash-resume without double-sending, dry run that opens nothing and
restores the queue, and a campaign that says what is holding it.

A campaign is single-channel. A lead reached by email is **not** also messaged on
WhatsApp unless the user explicitly starts a WhatsApp campaign for it — being contacted
twice on two channels in one week is what gets a sender reported.

---

## 6. `ui/screen_outreach.py` — the channel is a choice, not a second screen

WhatsApp is a channel toggle on the existing Campaign tab, not a parallel screen. Same
leads, same audit, same preview, same Sending tab.

- **Channel selector** on Campaign: Email or WhatsApp. Choosing WhatsApp swaps the
  template list to `WA_TEMPLATES`, shows the message preview as a phone bubble rather
  than an email, and shows the WhatsApp limits rather than the email ones.
- **Connection card** — the QR login, in Settings under a new WhatsApp section: current
  status, the QR image while it is waiting, the connected number when ready, and a
  Disconnect. The QR must render inside the app; sending the user to a terminal is the
  reference's constraint, not ours.
- The Leads table gains a **Phone** column and a filter for "has a usable number",
  because a lead with no number cannot be in a WhatsApp campaign.
- The plan summary says how many leads have no usable phone number **before** the user
  commits, the same way it reports unpersonalised emails.

---

## 7. Non-negotiables

1. `wa_dry_run` defaults **True**. A dry run renders every message, opens no browser
   session, and leaves the queue sendable.
2. Every first message carries an opt-out line. A reply matching `wa_opt_out_words`
   suppresses that lead **on both channels**, immediately, and cancels its queued
   follow-up.
3. `BANNED:` stops the run and every future run until the user acknowledges it.
4. One first-touch per number, ever, per campaign.
5. The number's own limits are never overridable from the UI the way the email window is.
   Send-now may waive the *clock*; it may not waive the cap.

---

## 8. Tests

Offline, no browser, no network. `WhatsAppSession` is injected so the campaign tests
drive a stub, exactly as `SmtpSender` is stubbed today.

- `to_wa_id` across real Maps formats, and that an unqualified number without a
  `default_region` is refused rather than guessed.
- Suppression crossing channels, in both directions.
- The scheduler honouring WhatsApp's own caps and window, and not spending email's.
- `BANNED:` halting a run; `RATE:` backing off; `RECIPIENT:` skipping one lead.
- Dry run opening no session and restoring the queue.
- Every `WA_TEMPLATES` entry under 60 words, carrying an opt-out line, with no unresolved
  merge token and no fabricated track record.
- A migrated pre-channel database opening and reading as email.
