# MapHarvest — Setup & User Manual

Double-click **`RUN.bat`** to start the app.

Everything below is one-time setup. Nothing here needs a developer, but two of the
steps have to be done on Google's website, not in the app.

---

## What you need before the first send

| | Needed for | Cost | Time |
|---|---|---|---|
| **Google Chrome** | scraping Google Maps | free | already installed |
| **A Gmail App Password** | sending the emails | free | ~3 min |
| **A Groq API key** | writing the personalised line | free tier | ~2 min |
| **An OpenRouter key** | fallback when Groq fails | optional | ~2 min |
| **A postal address** | required in the footer by law | — | — |

Only the App Password is strictly required. Without AI keys the app still sends —
it just uses the templates without a personalised opening line.

---

## 1. Gmail App Password  *(required)*

Gmail rejects your normal password over SMTP. You need a 16-character App Password,
and Google only offers them once 2-Step Verification is on.

1. Turn on 2-Step Verification: <https://myaccount.google.com/security>
2. Create the password: <https://myaccount.google.com/apppasswords>
   - Name it anything — "MapHarvest" is fine.
   - Google shows 16 characters in four blocks. Copy them.
3. In the app: **Settings → Gmail**
   - Gmail address, then paste the App Password (spaces don't matter)
   - Click **Verify**. It tells you immediately if Google refuses it.
   - Tick **Read replies and bounces (IMAP)** — see the warning below.
4. **Add account** to rotate across several mailboxes and spread the volume.

The password is encrypted with Windows DPAPI before it touches disk. It is never
stored in plain text.

### Turn IMAP on. This one matters.

If IMAP is off, the app can still *send* but cannot *read*. That means:

- someone who replies "unsubscribe" is never suppressed, and still gets both follow-ups
- a hard bounce is never recorded, and the address is re-contacted next campaign

The footer tells recipients to reply to opt out. If nothing reads that mailbox, you
are ignoring opt-outs — which is the thing that gets a sender reported.

---

## 2. AI keys  *(optional but recommended)*

The app does all the website auditing locally, for free. AI is used only to write
the personalised subject and opening line — roughly 500–900 tokens per lead.

**Groq** (primary, fast, generous free tier)
1. <https://console.groq.com/keys> → sign in → **Create API Key**
2. **Settings → AI** → paste into Groq → click **Test**

**OpenRouter** (fallback, pay-as-you-go)
1. <https://openrouter.ai/keys> → **Create Key**
2. Paste into OpenRouter → **Test**

Set **Provider** to *Auto* to try Groq first and fall back to OpenRouter. If both
fail, the email still goes out using the template alone — it is never blocked.

Results are cached per domain, so re-running the same leads costs nothing.

---

## 3. Sender profile  *(required)*

**Settings → Sender.** This drives every email.

- **Your name** and **title** — the sign-off
- **Postal address** — a real one. CAN-SPAM requires it in every commercial email,
  and filters treat a missing address as a spam signal on its own.
- **Calendar link** — the single link in the email
- **Proof points** — one per line. These are the *only* claims the app will make
  about your track record. Leave it empty and it claims nothing, which is correct
  if you have nothing to point at yet.

---

## 4. Check the sending rules

**Settings → Sending.** The defaults are deliberately conservative:

- Mon–Fri, 9:00–17:00, in the timezone you pick
- 40/day and 12/hour per account
- New accounts ramp from 10/day
- A random 60–240s gap between sends — an even cadence is the clearest automation
  fingerprint a mail provider can see

Gmail's real ceiling is about **500/day** on a free account and **2000/day** on
Workspace. Staying well under it is the point.

---

## 5. Your first campaign

1. **Scrape** — enter a business type and a city, pick a results folder, **Start**.
2. **Outreach → Leads** — the scrape lands here. **Audit all** crawls each website,
   finds the gaps, and writes the personalised line.
3. **Campaign** — pick a template, read the preview, **Prepare campaign**. It tells
   you how many emails across how many days, and how many could not be personalised.
4. **Sending** — **Start**. Dry run is ON by default.

### Dry run

A dry run renders and logs every message, opens no connection, and spends none of
your quota. **The queue goes back exactly as it was**, so the campaign is still
ready to send for real afterwards.

Turn it off in **Settings → Compliance** when you are ready. It asks first.

### Before your first *real* send

Send to **five leads, not five hundred.** Then open a real inbox and check what
actually arrived — the footer, the unsubscribe line, how it reads. Deliverability
depends on your sending domain's reputation, which no app can control for you.

---

## Editing the emails

**Settings → Templates.** Five templates ship: three first-touch angles and two
follow-ups.

- Click a merge field chip to insert it at your cursor
- The preview shows the real email as you type, footer and all
- Editing a built-in creates an override — **Reset** always brings the original back
- Warnings (subject too long, a misspelled merge field, too many links) never block
  saving; they just tell you

Templates are stored in `%USERPROFILE%\.mapharvest\templates.json`.

---

## Appearance

**Settings → Appearance** — dark or light theme, comfortable or compact density.
Compact fits noticeably more leads on screen. Both apply instantly.

---

## What the app will not do for you

- **It cannot make mail land in the inbox.** It gets the technical parts right —
  matching plain-text and HTML parts, a real unsubscribe header, no tracking pixels,
  paced sending — but reputation is earned by your domain over time.
- **It cannot read a mailbox you have not connected.** See the IMAP warning above.
- **Email extraction is best-effort.** A missing email usually means the site never
  published one in a machine-readable way.
- **The website audit is heuristic.** It will occasionally mislabel a site. The
  preview is there so you can see what it concluded before anything is sent.

---

## Where your data lives

```
%USERPROFILE%\.mapharvest\
    settings.json      your settings; credentials encrypted
    templates.json     your edited templates
    outreach.db        leads, campaigns, the send queue, suppressions
```

Deleting `templates.json` restores the five shipped templates. Deleting
`outreach.db` erases every lead, campaign and suppression — including the record of
who asked not to be contacted, so keep it.

---

## If something goes wrong

| Symptom | Cause |
|---|---|
| "Gmail rejected the sign-in" | Normal password instead of an App Password, or 2FA is off |
| Scrape finds nothing | Google is showing a CAPTCHA — check the `debug\` folder for a screenshot |
| No leads after a scrape | Nothing found had an email; try a different category or city |
| AI test fails | Wrong key, or the free tier is exhausted. Sending still works. |
| Nothing sends | Check the send window and daily caps in **Settings → Sending** |
