# MapHarvest — Setup & User Manual

**Start the app:** double-click `MapHarvest.exe`, or `RUN.bat` if you are running from source.

Everything below is one-time setup. Two steps have to be done on Google's website
rather than in the app.

---

## 1. What you need

| | For | Cost | Time |
|---|---|---|---|
| **Google Chrome** | scraping Google Maps | free | already installed |
| **Gmail App Password** | sending | free | ~3 min |
| **Groq API key** | the personalised line | free tier | ~2 min |
| **OpenRouter key** | fallback when Groq fails | optional | ~2 min |
| **A postal address** | required in the footer by law | — | — |

Only the App Password is strictly required. Without AI keys the app still sends —
it uses your templates without a personalised opening line.

---

## 2. Gmail App Password  *(required)*

Gmail rejects your normal password over SMTP. You need a 16-character App Password,
and Google only offers them once 2-Step Verification is on.

1. Turn on 2-Step Verification: <https://myaccount.google.com/security>
2. Create the password: <https://myaccount.google.com/apppasswords> — name it anything.
3. **Settings → Gmail** → paste it → **Verify**. It tells you immediately if Google refuses.
4. Tick **Read replies and bounces (IMAP)**.
5. **Add account** to rotate across several mailboxes and spread the volume.

Passwords are encrypted with Windows DPAPI before touching disk. Never stored in plain text.

### Turn IMAP on. This one matters.

With IMAP off the app can **send** but not **read**, which means:

- someone replying "unsubscribe" is never suppressed, and still gets both follow-ups
- a hard bounce is never recorded, and that address is re-contacted next campaign

Your footer tells recipients to reply to opt out. If nothing reads that mailbox, you are
ignoring opt-outs — the thing that gets a sender reported.

---

## 3. AI keys  *(optional, recommended)*

All website auditing is local and free. AI writes only the personalised subject and
opening line — roughly 500–900 tokens per lead, cached per domain so a re-run costs nothing.

- **Groq** (primary): <https://console.groq.com/keys>
- **OpenRouter** (fallback): <https://openrouter.ai/keys>

**Settings → AI** → paste → **Test**. Set Provider to *Auto* to try Groq then fall back.
If both fail the email still goes out from the template — it is never blocked.

---

## 4. Sender profile  *(required)*

**Settings → Sender.** This drives every email.

- **Your name** and **title** — the sign-off
- **Postal address** — a real one. CAN-SPAM requires it, and filters treat a missing
  address as a spam signal on its own.
- **Calendar link** — the single link in the email
- **Proof points**, one per line — the **only** claims the app makes about your track
  record. Leave it empty and it claims nothing, which is correct if you have nothing to
  point at yet.

### Services you sell

The list ships with 63 services grouped by category. **Only ticked services are ever
offered in an email**, in exactly that wording.

- **Add service** — add your own; it is saved with your profile
- **Rename** / **Remove** — for services you added
- **All** / **None** — bulk tick

Shipped services can be unticked but not reworded: their exact wording is what the
gap-to-service mapping is written against, so renaming one would quietly break the link
between a finding and the offer that answers it. Add your own instead.

---

## 5. Sending rules

**Settings → Sending.** Defaults are deliberately conservative:

- Your chosen days and hours, in a timezone you pick
- 40/day and 12/hour per account
- New accounts ramp from 10/day
- A random 60–240s gap — an even cadence is the clearest automation fingerprint there is

Gmail's real ceiling is about **500/day** free, **2000/day** Workspace. Staying well
under it is the point.

**If nothing sends**, check this screen first. Outside the window the app holds
everything and the Sending tab says so, naming the time the queue restarts.

---

## 6. Your first campaign

1. **Scrape** — business type, city, results folder, **Start**.
2. **Outreach → Leads** — **Audit all** crawls each site, finds the gaps, writes the line.
3. **Campaign** — pick a template, read the preview, **Prepare campaign**. It tells you
   how many emails across how many days, and how many could not be personalised.
4. **Sending** — **Start**. Dry run is ON by default.

### Dry run

Renders and logs every message, opens no connection, spends none of your quota, and
**puts the queue back exactly as it was** — the campaign is still ready to send for real.
Turn it off in **Settings → Compliance**. It asks first.

### Before your first real send

Send to **five leads, not five hundred**, then open a real inbox and check what arrived.

---

## 7. Editing the emails

**Settings → Templates.** Three first-touch angles and two follow-ups ship.

- **New**, **Duplicate**, **Delete** — your templates are yours
- **Reset** — restores a shipped template you edited
- Merge-field chips insert at your cursor; the preview renders the real email as you type
- Warnings (subject too long, misspelled merge field, too many links) never block saving

---

## 8. Working faster

| | |
|---|---|
| **Ctrl+K** | command palette — every action and destination by typing |
| **Escape** | back out of a screen, close a dialog |
| **Enter** | submit the focused form |

**Leads tab:** multi-select for bulk audit, suppress, export or remove; column visibility;
saved views (a named filter + sort + columns you can return to); search across business,
email, city and category.

**Settings → Appearance:** dark or light theme, comfortable or compact density. Compact
fits noticeably more leads on screen. Both apply instantly.

---

## 9. What the app will not do for you

- **It cannot make mail land in the inbox.** It gets the technical parts right — matching
  plain-text and HTML parts, a real unsubscribe header, no tracking pixels, paced sending
  — but reputation is earned by your domain over time.
- **It cannot read a mailbox you have not connected.** See the IMAP warning.
- **Email extraction is best-effort.** A missing email usually means the site never
  published one in a machine-readable way. It will not guess: an address belonging to the
  web designer who built the site is refused rather than mailed.
- **The website audit is heuristic.** It will occasionally mislabel a site. The preview
  exists so you can see what it concluded before anything is sent.

---

## 10. Where your data lives

```
%USERPROFILE%\.mapharvest\
    settings.json      settings; credentials encrypted
    templates.json     your edited and custom templates
    outreach.db        leads, campaigns, send queue, suppressions
    ai_cache.json      cached AI lines, so a re-run costs nothing
```

Deleting `templates.json` restores the shipped templates. Deleting `outreach.db` erases
every lead, campaign and suppression — **including the record of who asked not to be
contacted**, so keep it.

---

## 11. If something goes wrong

| Symptom | Cause |
|---|---|
| "Gmail rejected the sign-in" | Normal password instead of an App Password, or 2FA is off |
| Scrape finds nothing | Google is showing a CAPTCHA — check `debug\` for a screenshot |
| No leads after a scrape | Nothing found had an email; try another category or city |
| AI test fails | Wrong key or free tier exhausted. Sending still works. |
| Nothing sends | Outside the send window, or a daily cap is spent — the Sending tab says which |
| Follow-ups later than expected | The gap is a floor; capacity pushes them out on a long list |

---

## 12. Building from source

```
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
venv\Scripts\python.exe -m pytest tests/ -q
venv\Scripts\python.exe -m PyInstaller main.spec --noconfirm
```

The build lands in `dist\MapHarvest.exe`. `tools\make_icon.py` regenerates the icon,
reading the brand green from `ui/theme.py` so the launcher and the app never drift apart.

Contracts worth reading before changing anything: `docs/DESIGN_SYSTEM.md` (every value the
interface paints) and `docs/OUTREACH_SPEC.md` (module interfaces and the compliance rules).
