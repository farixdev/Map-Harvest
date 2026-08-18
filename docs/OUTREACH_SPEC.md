# MapHarvest Outreach — Implementation Contract

This is the **binding interface spec**. Every module below is written by a separate
implementer. Do not invent signatures, settings keys, DB columns or dict keys that
are not in this document — other modules import them by name and integration breaks
silently otherwise.

## 0. Ground rules

- **Python 3.14**, Windows-first, but no OS-specific code outside `core/secrets.py`.
- **No new hard dependencies.** Standard library only (`urllib`, `smtplib`, `imaplib`,
  `sqlite3`, `ssl`, `email`, `json`, `re`, `concurrent.futures`, `ctypes`, `zoneinfo`).
  `lxml` is already a dependency and may be used. `dnspython` may be used **only**
  behind a `try: import dns.resolver except ImportError:` guard with a stdlib fallback.
- **Nothing raises across a module boundary.** Public functions return empty/degraded
  values on failure. The GUI must never die because a third-party site was weird.
- Every network call takes a timeout. No unbounded reads — cap response bodies.
- Pure logic lives in module-level functions taking already-fetched HTML, so it is
  unit-testable offline. Network I/O sits in thin wrappers around those.
- Match the existing house style: `from __future__ import annotations`, module
  docstring explaining the *why*, `# ── Section ──` comment banners, short helper
  functions prefixed `_`.

---

## 1. `core/secrets.py` (new)

Encrypts credentials at rest so `settings.json` never contains a live Gmail app
password or API key in plaintext.

```python
def encrypt(plaintext: str) -> str
def decrypt(token: str) -> str
def is_encrypted(value: str) -> bool
```

- Stored form is `"enc:v1:<base64>"`. `is_encrypted` tests that prefix.
- On Windows use DPAPI via `ctypes` (`CryptProtectData` / `CryptUnprotectData` from
  `crypt32.dll`, `CRYPTPROTECT_UI_FORBIDDEN = 0x1`), scoped to the current user.
- On any other platform, or if DPAPI fails, fall back to base64 with a fixed XOR key
  derived from the machine name. Obfuscation, not security — document that honestly
  in the docstring.
- `decrypt` on a non-encrypted value returns it unchanged (migration path).
- `encrypt("")` returns `""`.

---

## 2. `core/settings.py` (extend — keep existing API working)

Keep `load_settings`, `save_settings`, `add_saved_search`, `SETTINGS_DIR`,
`SETTINGS_PATH`, `MAX_SAVED_SEARCHES` exactly as they are today. The existing
`DEFAULT_SETTINGS` keys (`headless`, `max_limit_cap`, `default_max_results`,
`export_dir`, `saved_searches`) must not change meaning.

Note the current `load_settings` drops unknown keys and `save_settings` rebuilds
from `DEFAULT_SETTINGS` — that behaviour is fine and must be preserved for nested
dicts too (deep-merge nested defaults so a new sub-key added later still appears).

### New keys added to `DEFAULT_SETTINGS`

```python
# ── AI ──
"ai_provider": "auto",            # auto | groq | openrouter | off
"groq_api_key": "",               # stored via core.secrets
"groq_model": "llama-3.3-70b-versatile",
"openrouter_api_key": "",         # stored via core.secrets
"openrouter_model": "meta-llama/llama-3.3-70b-instruct",
"ai_max_tokens_per_lead": 220,
"ai_monthly_token_cap": 2000000,
"ai_tokens_used": 0,
"ai_tokens_month": "",            # "YYYY-MM"; resets ai_tokens_used on rollover

# ── Enrichment ──
"enrich_max_pages": 4,
"enrich_workers": 6,
"enrich_timeout": 8.0,
"enrich_verify_dns": True,
"enrich_accept_free_mail": True,  # keep gmail.com/yahoo.com addresses

# ── Audit ──
"audit_enabled": True,
"audit_max_pages": 6,
"audit_timeout": 8.0,

# ── Sender profile (drives all copy) ──
"sender_profile": {
    "company": "Auto Army",
    "sender_name": "",
    "sender_title": "",
    "website": "",
    "reply_to": "",
    "phone": "",
    "postal_address": "",         # required by CAN-SPAM, rendered in footer
    "calendar_link": "",
    "services": [],               # list[str]; defaults seeded from AUTO_ARMY_SERVICES
    "proof_points": [],           # list[str]
    "tone": "direct",             # direct | friendly | consultative
},

# ── Gmail accounts ──
"smtp_accounts": [],
#   each: {"email": str, "app_password": str (encrypted), "display_name": str,
#          "daily_cap": int, "enabled": bool, "warmup_started": "YYYY-MM-DD",
#          "imap_enabled": bool}

# ── Sending schedule ──
"send_days": [0, 1, 2, 3, 4],     # Mon=0 .. Sun=6
"send_start_hour": 9,
"send_end_hour": 17,
"send_timezone": "local",         # "local" or an IANA name
"send_min_gap_sec": 60,
"send_max_gap_sec": 240,
"daily_cap_per_account": 40,
"hourly_cap_per_account": 12,
"warmup_enabled": True,
"warmup_start": 10,
"warmup_step": 5,
"warmup_max": 40,

# ── Follow-ups ──
"followup_enabled": True,
"followup_gap_days": 4,
"followup_max_steps": 2,

# ── Compliance ──
"unsubscribe_mailto": "",         # blank = use the sending account address
"dry_run": True,                  # NEW installs default to True — never surprise-send
```

### New functions

```python
def get_secret(settings: dict, key: str) -> str      # decrypts on read
def set_secret(settings: dict, key: str, value: str) -> None   # encrypts on write
def smtp_accounts(settings: dict) -> list[dict]      # decrypted app_password, enabled only
def note_ai_tokens(settings: dict, tokens: int) -> None  # rolls month, adds, persists
def ai_budget_left(settings: dict) -> int
```

`get_secret`/`set_secret` handle `groq_api_key`, `openrouter_api_key`, and the
`app_password` of each entry in `smtp_accounts` (use dotted key
`"smtp_accounts.<email>.app_password"`).

---

## 3. `core/enrich.py` (rewrite — keep the old API intact)

**Must keep working, byte-identical signatures**, because `core/scraper.py` imports
them and `tests/test_enrich_filters.py` asserts on them:

```python
ENRICH_KEYS = ("email", "facebook", "instagram", "linkedin", "twitter", "youtube")
def extract_contacts(html: str, base_url: str = "") -> dict   # returns dict with exactly ENRICH_KEYS
def enrich_website(url: str, timeout: float = 8.0, fields: tuple = ENRICH_KEYS) -> dict
```

### New public API

```python
EMAIL_ROLE_PREFIXES = frozenset({...})       # info, contact, hello, sales, office, admin, ...
EMAIL_JUNK_PREFIXES = frozenset({...})       # noreply, no-reply, postmaster, abuse, ...

def harvest_site(url: str, *, max_pages: int = 4, timeout: float = 8.0,
                 workers: int = 6, verify_dns: bool = True,
                 accept_free_mail: bool = True) -> dict
```

Returns:

```python
{
  "url": str, "final_url": str, "reachable": bool,
  "emails": [ {"email": str, "score": int, "kind": "role"|"personal"|"generic"|"free",
               "source": str,           # page URL it came from
               "method": str,           # mailto | jsonld | cfemail | deobfuscated | text | js
               "deliverable": bool | None} ],
  "best_email": str,
  "socials": {"facebook": str, "instagram": str, "linkedin": str,
              "twitter": str, "youtube": str},
  "phones": [str],
  "pages": [str],                        # URLs actually fetched
  "html": {url: str},                    # fetched HTML, reused by core/audit.py
  "error": str,                          # "" when fine
}
```

### Extraction improvements required

Each of these is a real-world miss in the current implementation. Implement all:

1. **`mailto:` links** — highest confidence. Strip `?subject=`/`?body=`, URL-decode.
2. **JSON-LD / microdata** — `"email"` fields inside `<script type="application/ld+json">`,
   and `itemprop="email"`. Very common on business sites and currently missed entirely.
3. **Cloudflare `data-cfemail`** — decode the hex string: first byte is the XOR key,
   each subsequent byte XORed with it yields the address. Cloudflare's email
   obfuscation is extremely common and currently yields *zero* emails.
4. **Textual obfuscation** — normalise before regexing:
   `(at)`, `[at]`, `{at}`, ` at `, `&#64;`, `&commat;`, `%40` → `@`;
   `(dot)`, `[dot]`, `{dot}`, ` dot ` → `.`. Only apply the spaced ` at `/` dot `
   forms when the surrounding token already looks address-shaped, to avoid
   turning ordinary prose into fake emails.
5. **`String.fromCharCode(...)` blobs** — decode and re-scan the result.
6. **Reversed text** (`unicode-bidi: bidi-override` trick) — scan `html[::-1]` too.
7. **Multi-page crawl** — home plus up to `max_pages-1` of the best candidate links,
   ranked by href/anchor text: `contact` > `about` > `team`/`staff`/`people` >
   `support` > `impressum`/`legal`. Fetch them **concurrently** with a
   `ThreadPoolExecutor(max_workers=workers)`. Same-host links only; skip
   `mailto:`/`tel:`/`#`/asset extensions; cap at `max_pages` total fetches.
8. **Robust decoding** — honour the `charset` from the `Content-Type` header and
   from a `<meta charset>` in the first 2 KB, not a hardcoded utf-8. Handle
   `Content-Encoding: gzip` **and** `deflate` **and** `br` (skip br if
   `brotli` is unavailable — send an `Accept-Encoding` that omits it).
9. **Follow redirects** and record `final_url`; treat `http→https` upgrades and
   `www` variants as the same host for the same-domain email bonus.

### Scoring (`score`, higher is better; the top scorer becomes `best_email`)

| Signal | Delta |
|---|---|
| domain matches the site's registrable domain | `+50` |
| found via `mailto:` | `+25` |
| found via JSON-LD or `data-cfemail` | `+20` |
| local part is a role prefix (`info`, `contact`, `hello`, `sales`, `office`, `enquiries`, `admin`, `bookings`, `reception`) | `+18` |
| local part looks like a person (`first.last`, `first_last`, or a known-name shape) | `+30` |
| free-mail domain (gmail/yahoo/hotmail/outlook/aol/icloud) but no other candidate | `+8` |
| found on a `/contact` page | `+10` |
| junk prefix (`noreply`, `no-reply`, `donotreply`, `postmaster`, `abuse`, `webmaster`, `hostmaster`, `mailer-daemon`) | `-100` |
| `careers`/`jobs`/`recruit` | `-25` |
| `privacy`/`legal`/`dpo`/`gdpr`/`compliance` | `-20` |
| domain fails DNS (`deliverable is False`) | `-60` |
| disposable-domain list hit | `-100` |

Anything scoring `<= 0` is dropped from `emails` entirely.

### Deliverability check (`verify_dns=True`)

- Resolve MX for the domain via `dns.resolver` if importable; else fall back to
  `socket.getaddrinfo(domain, None)` (an A record means mail *may* work).
- Cache results per-domain in a module-level dict for the process lifetime — a
  scrape of 200 leads in one city hits the same handful of domains repeatedly.
- Timeout 3 s; on timeout set `deliverable = None` (unknown, not a penalty).

### Keeping `extract_contacts` / `enrich_website` compatible

Re-implement them as thin wrappers over the new machinery, returning the same
flat dict shape as today. `enrich_website` keeps returning `{field: str}` for
exactly the requested `fields`. This keeps `core/scraper.py` untouched.

---

## 4. `core/audit.py` (new)

Local, deterministic, **zero-token** website audit. This is what makes the AI
prompt cheap — the model receives a digest, never raw HTML.

```python
def audit_site(url: str, *, max_pages: int = 6, timeout: float = 8.0,
               prefetched: dict | None = None) -> dict
def audit_from_html(pages: dict, base_url: str) -> dict   # pure, offline-testable
def digest(audit: dict, max_chars: int = 1200) -> str
```

`prefetched` is the `html` dict from `harvest_site` — when the enricher already
fetched pages, the audit must reuse them and not re-download.

`audit_from_html(pages, base_url)` takes `{url: html}` and is the pure core.

### Returned dict

```python
{
  "url": str, "final_url": str, "reachable": bool, "status": int,
  "load_ms": int, "pages": [str], "page_count": int,
  "title": str, "description": str, "h1": str, "brand": str,
  "tech": {
      "cms": str,            # wordpress | wix | squarespace | shopify | webflow | joomla | drupal | godaddy | duda | weebly | custom | ""
      "ecommerce": str,      # shopify | woocommerce | bigcommerce | magento | ""
      "analytics": [str],    # ga4, gtm, meta_pixel, hotjar, clarity, ...
      "chat": str,           # intercom | tawk | drift | crisp | tidio | livechat | messenger | ""
      "booking": str,        # calendly | acuity | setmore | square | opentable | resy | housecallpro | ""
      "crm": str,            # hubspot | salesforce | zoho | pipedrive | activecampaign | mailchimp | klaviyo | ""
      "forms": int,          # count of <form> elements across pages
      "frameworks": [str],   # react, vue, angular, nextjs, ...
  },
  "services": [str],         # up to 12, from nav links + service-y H2/H3 + <title>
  "signals": {
      "has_ssl": bool, "mobile_viewport": bool, "has_schema": bool,
      "has_localbusiness_schema": bool, "has_blog": bool, "blog_stale": bool,
      "has_online_booking": bool, "has_live_chat": bool, "has_contact_form": bool,
      "has_phone": bool, "has_email": bool, "has_social": bool,
      "has_pricing": bool, "has_testimonials": bool, "has_gallery": bool,
      "has_careers": bool, "has_newsletter": bool, "has_pdf_forms": bool,
      "has_multiple_locations": bool, "has_quote_form": bool,
      "copyright_year": int,   # 0 if not found
      "stale_copyright": bool, # copyright_year < current_year - 1
      "avg_page_kb": int, "slow": bool,   # slow = load_ms > 3000
  },
  "gaps": [ {"code": str, "title": str, "severity": 1|2|3,
             "evidence": str, "services": [str]} ],
  "opportunity_score": int,   # 0-100
  "error": str,
}
```

### Gap catalogue — the gap→service mapping is the core of the pitch

Detect these; `services` on each gap must name **Auto Army services verbatim**
(see `AUTO_ARMY_SERVICES` in `core/templates.py`):

| code | fires when | severity | services |
|---|---|---|---|
| `no_online_booking` | no booking widget/link and the business is appointment-shaped (has contact form + service words) | 3 | Appointment booking, Lead Automation |
| `no_live_chat` | `tech.chat == ""` | 2 | AI customer-support agents, AI lead qualification |
| `contact_form_only` | has a form but no chat, no booking, no phone-first CTA | 3 | AI lead qualification, Automatic follow-ups |
| `no_crm_signals` | `tech.crm == ""` and a form exists | 3 | CRM & Sales Automation, Automatically add leads to CRM |
| `no_lead_capture` | no form and no newsletter anywhere | 3 | Lead Generation, Lead Automation |
| `quote_by_form` | a "request a quote"/"get an estimate" form exists | 2 | AI lead scoring, AI decision/triage systems |
| `stale_blog` | `has_blog and blog_stale` | 1 | AI content generation, Content pipelines |
| `no_analytics` | `tech.analytics == []` | 2 | Automated reports, Reporting |
| `careers_manual` | `has_careers` | 2 | HR processes, Employee onboarding |
| `ecommerce_manual` | `tech.ecommerce != ""` | 2 | Purchase/order workflows, Order automation |
| `pdf_forms` | `has_pdf_forms` | 2 | Document Automation, PDF/document data extraction |
| `multi_location` | `has_multiple_locations` | 2 | Automated reports, Business Process Automation |
| `no_social_presence` | `not has_social` | 1 | Social media workflows, Marketing Automation |
| `stale_site` | `stale_copyright` | 1 | Business Process Automation |
| `slow_site` | `signals.slow` | 1 | Business Process Automation |
| `no_mobile` | `not mobile_viewport` | 2 | Business Process Automation |
| `no_schema` | `not has_schema` | 1 | SEO automation |
| `price_opaque` | `not has_pricing` and ecommerce is empty | 1 | AI lead qualification |

`opportunity_score` = clamp to 0-100 of `sum(severity * 9 for gaps)` plus `+10` if
reachable and `+5` if an email was found. Sort `gaps` by severity desc, then by
catalogue order, so `gaps[0]` is always the headline gap.

### `digest(audit)` — the only thing the AI ever sees

Compact, plain-text, **≤ 1200 characters** (~300 tokens). Exactly this shape:

```
SITE: acmeplumbing.com | Acme Plumbing & Heating
WHAT: Emergency plumbing, boiler installs, bathroom fitting, drain clearing
STACK: wordpress | no chat | no booking | no crm | ga4
SIGNALS: contact form yes, phone yes, blog 2021, careers page yes, 3 locations
TOP GAPS: no online booking (3); no CRM behind the form (3); careers handled manually (2)
```

No HTML, no URLs beyond the domain, no boilerplate. Truncate service lists rather
than exceeding the cap.

---

## 5. `core/templates.py` (new)

Owns the service catalogue and all copy that does not come from the model.

```python
AUTO_ARMY_SERVICES: dict[str, list[str]]     # category -> list of service names
DEFAULT_SERVICES: list[str]                  # flat list seeded into sender_profile

@dataclass
class Template:
    id: str
    name: str
    step: int              # 0 = first touch, 1..n = follow-ups
    subject: str
    body: str              # plain text with {{merge_fields}}

TEMPLATES: list[Template]

def render(template: Template, ctx: dict) -> tuple[str, str, str]
    """Returns (subject, body_text, body_html)."""

def build_context(lead: dict, audit: dict, ai: dict, profile: dict,
                  settings: dict) -> dict

def to_html(body_text: str, ctx: dict) -> str
```

### Merge fields available in `ctx`

`business_name`, `first_name`, `city`, `category`, `website_domain`,
`gap_1`, `gap_2`, `gap_1_evidence`, `service_1`, `service_2`, `service_3`,
`ai_subject`, `ai_opener`, `ai_ps`, `sender_name`, `sender_title`, `company`,
`company_website`, `calendar_link`, `phone`, `postal_address`, `unsubscribe_line`,
`proof_point`.

Unknown fields render as `""`, never as a literal `{{token}}` — a leaked merge
token in a live cold email is the single most damaging bug in this system, so
`render` must strip any `{{...}}` that survives substitution.

### Required templates

Ship at least 3 first-touch angles + 2 follow-ups:

- `gap_direct` — names the headline gap, one sentence on the fix, soft CTA.
- `time_saved` — leads with hours-per-week reclaimed by automating the gap.
- `question` — short, single question, lowest-pressure.
- `followup_bump` (step 1) — 3 lines, references the first email, no new pitch.
- `followup_close` (step 2) — permission-to-close-the-file, one line.

Copy rules baked into the templates: under 120 words for first touch, no
attachments, no tracking pixels, no link shorteners, exactly one link (the
calendar or company site), no ALL-CAPS or `!!` in subjects, subject under 55
chars, a real sign-off.

### `to_html`

Minimal, deliverability-safe HTML: system font stack, `<p>` per paragraph, one
`<a>`, a `<hr>`, then a small grey footer with `postal_address` and the
unsubscribe line. **No images, no external CSS, no tables, no web fonts.**
Plain text is always sent alongside as the `text/plain` alternative.

---

## 6. `core/ai.py` (new)

```python
class AIClient:
    def __init__(self, settings: dict) -> None
    def available(self) -> bool
    def personalize(self, *, business_name: str, digest: str, profile: dict,
                    tone: str = "direct") -> dict
    @property
    def last_error(self) -> str
```

`personalize` returns:

```python
{"subject": str, "opener": str, "ps": str,
 "provider": str, "model": str, "tokens": int, "cached": bool, "ok": bool}
```

- Groq: `POST https://api.groq.com/openai/v1/chat/completions`
- OpenRouter: `POST https://openrouter.ai/api/v1/chat/completions`
  (send `HTTP-Referer` and `X-Title` headers — OpenRouter wants them).
- Both are OpenAI-compatible: **one request builder, two base URLs**.
- `ai_provider == "auto"`: try Groq, fall back to OpenRouter on any failure,
  then return `{"ok": False}` so the caller uses the pure template.
- `response_format={"type": "json_object"}`, `max_tokens` from
  `ai_max_tokens_per_lead`, `temperature=0.7`.
- **Token frugality is a hard requirement.** System prompt ≤ 150 tokens. User
  message is the digest plus a one-line offer summary built from at most 6
  services. Never send raw HTML or full page text.
- Parse defensively: strip ``` fences, find the first `{`..last `}`, `json.loads`,
  and validate the three string fields. On any parse failure return `ok=False`.
- **Disk cache** at `~/.mapharvest/ai_cache.json`, keyed
  `sha1(domain + "|" + template_id + "|" + model)`, capped at 5000 entries
  (drop oldest). A re-run over the same leads must cost zero tokens.
- Call `settings.note_ai_tokens(...)` after each live call; refuse to call when
  `ai_budget_left(settings) <= 0` and return `ok=False`.
- Timeout 20 s, one retry on 429/5xx with a 2 s backoff, then give up.

### The system prompt (use this, do not improvise)

> You write one-line cold-email personalisation for a business automation agency.
> Given a compressed website audit, return JSON with keys "subject", "opener", "ps".
> subject: under 55 characters, lowercase-ish, specific to their business, no
> hype, no exclamation marks. opener: ONE sentence, max 25 words, states a
> concrete thing you noticed on their site — never "I came across your website".
> ps: ONE short sentence offering a specific automation tied to the gap. Plain
> language. No em dashes. No placeholders.

---

## 7. `core/outreach_db.py` (new)

SQLite at `os.path.join(SETTINGS_DIR, "outreach.db")`. WAL mode. All functions take
an explicit connection or use a module-level lazily-opened one guarded by a
`threading.Lock` — the Qt worker thread and the GUI thread both touch it.

```python
def connect(path: str = "") -> sqlite3.Connection
def init_db(conn) -> None

def upsert_lead(conn, lead: dict) -> int          # returns lead id; dedupes on email
def get_lead(conn, lead_id: int) -> dict
def list_leads(conn, *, status: str = "", campaign_id: int = 0,
               limit: int = 0, offset: int = 0) -> list[dict]
def count_leads(conn, **filters) -> int
def set_lead_audit(conn, lead_id: int, audit: dict, ai: dict) -> None

def create_campaign(conn, name: str, template_id: str, profile: dict,
                    settings_snapshot: dict) -> int
def get_campaign(conn, campaign_id: int) -> dict
def list_campaigns(conn) -> list[dict]
def set_campaign_status(conn, campaign_id: int, status: str) -> None

def queue_message(conn, msg: dict) -> int
def due_messages(conn, now_ts: float, limit: int = 50) -> list[dict]
def mark_message(conn, message_id: int, status: str, **fields) -> None
def campaign_stats(conn, campaign_id: int) -> dict

def suppress(conn, email: str, reason: str) -> None
def is_suppressed(conn, email: str) -> bool
def suppression_list(conn) -> list[dict]

def record_send(conn, account_email: str, ts: float) -> None
def sent_today(conn, account_email: str, tz) -> int
def sent_last_hour(conn, account_email: str) -> int

def log_event(conn, kind: str, detail: str, lead_id: int = 0) -> None
def recent_events(conn, limit: int = 200) -> list[dict]
```

### Schema

```sql
CREATE TABLE leads (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, domain TEXT,
  website TEXT, phone TEXT, city TEXT, category TEXT, rating TEXT,
  maps_link TEXT, source TEXT, audit_json TEXT, ai_json TEXT,
  opportunity_score INTEGER DEFAULT 0, status TEXT DEFAULT 'new',
  created_at REAL, updated_at REAL);
CREATE TABLE campaigns (
  id INTEGER PRIMARY KEY, name TEXT, template_id TEXT, profile_json TEXT,
  settings_json TEXT, status TEXT DEFAULT 'draft', created_at REAL);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY, campaign_id INTEGER, lead_id INTEGER, step INTEGER DEFAULT 0,
  subject TEXT, body_text TEXT, body_html TEXT, account_email TEXT,
  status TEXT DEFAULT 'queued', scheduled_at REAL, sent_at REAL,
  error TEXT, message_id TEXT, created_at REAL);
CREATE TABLE suppression (
  email TEXT PRIMARY KEY, reason TEXT, added_at REAL);
CREATE TABLE sends (
  id INTEGER PRIMARY KEY, account_email TEXT, ts REAL);
CREATE TABLE events (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, detail TEXT, lead_id INTEGER);
```

Indexes on `messages(status, scheduled_at)`, `messages(campaign_id)`,
`sends(account_email, ts)`, `leads(status)`, `leads(domain)`.

`lead["status"]`: `new | audited | queued | sent | replied | bounced | skipped | suppressed`.
`message["status"]`: `queued | sending | sent | failed | skipped | bounced | replied`.

`upsert_lead` dedupes on lowercase email; on conflict it updates non-empty fields
only and never downgrades an existing `audit_json` to null.

---

## 8. `core/mailer.py` (new)

```python
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
GMAIL_IMAP_HOST = "imap.gmail.com"

def build_message(*, to_email: str, to_name: str, from_email: str,
                  from_name: str, reply_to: str, subject: str,
                  body_text: str, body_html: str,
                  unsubscribe_mailto: str, message_id: str = "") -> tuple[EmailMessage, str]
    """Returns (message, message_id)."""

class SmtpSender:
    def __init__(self, email: str, app_password: str, display_name: str = "",
                 timeout: float = 30.0) -> None
    def connect(self) -> tuple[bool, str]      # (ok, error)
    def send(self, message) -> tuple[bool, str]
    def close(self) -> None
    def __enter__/__exit__

def verify_account(email: str, app_password: str) -> tuple[bool, str]
def check_replies(email: str, app_password: str, since_days: int = 14) -> list[str]
def check_bounces(email: str, app_password: str, since_days: int = 14) -> list[str]
```

- `starttls` with a default `ssl.create_default_context()`. Login with the app
  password (Gmail rejects normal passwords — say so in the error string when
  auth fails, that is the #1 support question).
- Reuse one connection across sends; reconnect transparently on
  `SMTPServerDisconnected`. Retry a send once on `SMTPConnectError`/`disconnected`.
- Classify errors so the caller can decide to stop the whole run vs skip one lead:
  return error strings prefixed `AUTH:`, `QUOTA:`, `RECIPIENT:`, `CONN:`, `OTHER:`.
  Gmail signals quota with `550 5.4.5` / "Daily user sending limit exceeded" and
  `452 4.2.2`.
- Headers on every message: `Message-ID` (a real one, `<uuid@sender-domain>`),
  `Date`, `Reply-To`, `List-Unsubscribe: <mailto:...?subject=unsubscribe>`,
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, `Auto-Submitted: auto-generated`.
- Body is `multipart/alternative` — `text/plain` first, then `text/html`.
- **No tracking pixel, no open tracking, no click wrapping.** Deliverability over
  analytics; that is a deliberate product decision, not an oversight.
- `check_replies` scans INBOX via IMAP for messages whose `In-Reply-To`/`References`
  match sent `Message-ID`s, returning the matched message-ids. `check_bounces`
  looks for `mailer-daemon`/`postmaster` senders and extracts the failed recipient
  from the body. Both return `[]` and never raise if IMAP is off or fails.

---

## 9. `core/campaign.py` (new)

The scheduling brain plus the Qt worker. **The scheduler must be a pure function**
so it can be unit-tested without clocks or Qt.

```python
def next_send_times(*, count: int, accounts: list[dict], settings: dict,
                    start_ts: float, sent_today_by_account: dict[str, int],
                    seed: int = 0) -> list[tuple[float, str]]
    """Returns [(timestamp, account_email)] of length <= count, ascending."""
```

Rules `next_send_times` enforces:

1. Only within `send_days` and the `[send_start_hour, send_end_hour)` window in
   `send_timezone` (use `zoneinfo`; `"local"` means the machine's tz).
2. Round-robin across enabled accounts, skipping any that has hit its cap.
3. Per-account daily cap = `min(daily_cap_per_account, account["daily_cap"], warmup_cap)`
   where `warmup_cap = warmup_start + warmup_step * days_since(account["warmup_started"])`,
   clamped to `warmup_max`, when `warmup_enabled`.
4. Per-account hourly cap = `hourly_cap_per_account`.
5. Gap between consecutive sends on the *same* account is a pseudo-random value in
   `[send_min_gap_sec, send_max_gap_sec]`, derived from `seed` so the function is
   deterministic under test. Never a fixed interval — a metronome is the clearest
   automation fingerprint there is.
6. Spill into subsequent days when the count exceeds today's remaining capacity.

```python
def plan_campaign(conn, *, campaign_id: int, leads: list[dict], template_id: str,
                  profile: dict, settings: dict, ai: AIClient | None,
                  progress=None) -> dict
```

Audits each lead's website, personalises, renders, and queues messages with the
schedule from `next_send_times`. Skips suppressed, invalid, and already-contacted
addresses. `progress(done, total, message)` is called if provided.

```python
class OutreachWorker(QThread):
    log_signal = pyqtSignal(str, str)          # message, level (info|active|done|error)
    progress_signal = pyqtSignal(int, int)     # done, total
    message_sent_signal = pyqtSignal(dict)     # the message row
    stats_signal = pyqtSignal(dict)            # campaign_stats()
    done_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, campaign_id: int, settings: dict, dry_run: bool = False)
    def stop(self) -> None
    def pause(self) / def resume(self) -> None
```

Worker loop: poll `due_messages`, re-check caps and window at send time (the plan
can be hours old), send, `mark_message`, `record_send`, emit. Sleep in **0.25 s
slices** so Stop is responsive — never one long `time.sleep` until the next send.
Honour `dry_run`: render and mark `sent` with `error="DRY-RUN"` but open no SMTP
connection. `PreparedCampaign`/plan must be resumable across app restarts, since
a 500-lead campaign spans days.

```python
class AuditWorker(QThread):
    """Enrich + audit + personalise a batch of leads without sending."""
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int, int)
    lead_signal = pyqtSignal(dict)
    done_signal = pyqtSignal()
```

Runs `harvest_site` → `audit_site` → `AIClient.personalize` per lead across a
`ThreadPoolExecutor` (network-bound), writing results with `set_lead_audit`.

---

## 10. `ui/screen_outreach.py` (new)

A `QWidget` styled by the existing global QSS in `ui/app.py` (dark, `#1C1C1E`
background, `#636366` accents, 8 px radii). Do **not** add a second stylesheet;
if a new control needs styling, add an `objectName` and tell the integrator which
QSS rule to append.

```python
class OutreachScreen(QWidget):
    home_signal = pyqtSignal()
    settings_signal = pyqtSignal()
    def load_from_results(self, records: list[dict]) -> None
    def refresh(self) -> None
```

Four sub-tabs reusing the existing `QPushButton#tab` pattern:

1. **Leads** — table (Business, Email, Score, Gap, Status), import from the last
   scrape or a CSV, "Audit selected" button, live count, search box. Colour the
   score cell by band. Right-click: open site, copy email, suppress.
2. **Campaign** — template picker, live preview of the rendered email for the
   selected lead (subject + body, monospace-free, readable), sender-profile
   summary with a link to Settings, "Prepare campaign" button that runs the audit
   pass then shows the schedule ("218 emails across 6 days, 40/day from 1 account,
   first send Mon 9:14 AM").
3. **Sending** — Start/Pause/Stop, live log, progress, per-account counters
   (today/cap), next-send countdown, and a prominent **DRY RUN** badge when
   `settings["dry_run"]` is on.
4. **Stats** — queued/sent/failed/replied/bounced tiles, per-day sparkline-ish bar
   row (plain QWidget painting or coloured labels — no chart dependency),
   suppression list with a remove action.

The preview pane must show exactly what will be sent, including the footer and
unsubscribe line. Never show a template with unresolved `{{...}}`.

---

## 11. `ui/screen_settings.py` (new) + wiring

Move settings out of the cramped `InputScreen` tab into a full screen, and add the
new sections. `InputScreen`'s Settings tab becomes a short summary with an
"Open full settings" button (keep `headless` and the result-limit cap where they
are so nothing the user already knows moves).

```python
class SettingsScreen(QWidget):
    back_signal = pyqtSignal()
    saved_signal = pyqtSignal(dict)
```

Sections: **AI** (provider combo, Groq key + model, OpenRouter key + model, a
"Test" button per provider that does one 5-token call and reports latency, token
budget bar), **Sender profile** (company, name, title, website, reply-to, phone,
postal address, calendar link, services multi-select seeded from
`AUTO_ARMY_SERVICES`, proof points, tone), **Gmail accounts** (add/remove/verify
rows; the verify button calls `mailer.verify_account` and shows a green tick or
the real error; an inline hint on where App Passwords come from), **Sending**
(days, window, gaps, caps, warm-up), **Follow-ups**, **Compliance** (unsubscribe
address, dry-run toggle with an explicit warning when turning it off).

All secret fields use `QLineEdit.Password` echo mode with a reveal toggle, and are
written through `settings.set_secret`.

### Wiring in `ui/app.py`

Add both screens to the `QStackedWidget` (indices 2 = outreach, 3 = settings).
`MainWindow` currently calls `setFixedSize` per screen — replace with `setMinimumSize`
plus a sensible `resize` so the new, denser screens are usable and the window can be
maximised. Add navigation: Input → Settings, Results → "Start Outreach" (passes
`self.results` via `load_from_results`), Outreach → Home/Settings.

`ui/screen_results.py` gets one new button, `Start Outreach`, next to `Export CSV`,
emitting a new `outreach_signal = pyqtSignal(list)` carrying `self.results`. Do not
otherwise restructure that file.

---

## 12. Tests

Offline, no network, no Qt event loop.

`pytest` is a **dev** dependency and is declared in `requirements-dev.txt`, not in
`requirements.txt` — the latter stays the runtime set and the ground rule above still
holds: nothing at runtime imports outside the stdlib plus PyQt5 / selenium /
undetected-chromedriver / lxml.

```bash
pip install -r requirements-dev.txt
venv/Scripts/python.exe -m pytest tests/ -q
```

Every file must **also** stay runnable on its own without pytest, so that one area can be
bisected without the runner:

```bash
venv/Scripts/python.exe -m tests.test_schedule
```

That is why each file ends in an `if __name__ == "__main__":` block that calls its own
tests. There is no `conftest.py`; each file puts the repo root on `sys.path` itself.
Follow the style of the existing `tests/test_parse.py` and `tests/test_enrich_filters.py`.

- `tests/test_enrich_email.py` — Cloudflare `data-cfemail` decode, `(at)`/`[dot]`
  deobfuscation, JSON-LD email, `mailto:` priority, junk rejection, scoring order,
  same-domain preference, and that `extract_contacts`/`enrich_website` keep their
  old shape.
- `tests/test_audit.py` — `audit_from_html` against small handwritten HTML fixtures:
  WordPress + no chat + contact form → expects `no_live_chat` and `no_crm_signals`;
  a Shopify page → `ecommerce_manual`; a careers page → `careers_manual`. Assert
  `digest()` stays under 1200 chars and contains the top gap.
- `tests/test_templates.py` — no `{{` survives `render` for a context with missing
  keys; subject length; the HTML has exactly one `<a>` plus the unsubscribe.
- `tests/test_schedule.py` — `next_send_times` respects window/days/caps/warm-up,
  is deterministic for a fixed seed, gaps fall in range, and 500 messages against a
  40/day cap spill across days.
- `tests/test_outreach_db.py` — upsert dedupe, suppression blocks queueing,
  `due_messages` ordering, `sent_today` boundary at local midnight.

The shipped suite adds files this list did not ask for, covering surfaces that turned out
to need the same treatment: `tests/test_mailer.py` (message construction and the five-way
error classification), `tests/test_settings.py` (deep merge across schema versions, the
secrets round-trip, a corrupt settings file) and `tests/test_outreach_screen.py` (the
Outreach screen's appearance and shutdown contracts, under `QT_QPA_PLATFORM=offscreen`).
That last one is the sole exception to "no Qt event loop" above, and it constructs the
screen against a redirected `SETTINGS_DIR` so it can never touch a real `~/.mapharvest`.

---

## 13. Compliance — non-negotiable, build it in

Cold B2B email is legal in most jurisdictions **only** with these present, and they
are cheap to build now and painful to retrofit:

- Every email carries a working unsubscribe (`List-Unsubscribe` header **and** a
  visible footer line) and the sender's postal address.
- An unsubscribe adds the address to `suppression` and cancels every queued
  message for that lead, including follow-ups.
- One-address-one-campaign: never queue a second first-touch to an address that
  already received one.
- `dry_run` defaults to **True** on a fresh install.
- Subject lines must not misrepresent — no fake `Re:`/`Fwd:` prefixes anywhere in
  the templates or the AI prompt.

---

## 14. Where the implementation deviates from this spec

This spec is the contract; the list below is every place the shipped code knowingly departs
from it, and why. Anything **not** listed here should match the spec — if it does not, that is
a defect rather than a decision.

### Signatures

| Spec | Shipped | Why |
|------|---------|-----|
| `next_send_times(*, count, accounts, settings, start_ts, sent_today_by_account, seed=0)` | plus `ramp_start: date \| None = None` | The warm-up origin for accounts with no `warmup_started` of their own. Without it, every replan of a running campaign re-derives the ramp from *today* and drops those accounts back to their first-day rate, so the campaign never finishes. Keyword-only with a default, so every call written against the spec still works. |
| `mark_message(conn, message_id: int, status: str, **fields)` | `mark_message(conn, message_id, /, status: str, **fields)` | The row id and the RFC 5322 header share the name `message_id`. The positional-only marker is what lets `mark_message(conn, row_id, "sent", message_id="<x@y>")` work instead of raising `TypeError`. Positional callers are unaffected; `message_id=` as a *keyword* now means the header. |
| `sent_today(conn, account_email, tz)`, `sent_last_hour(conn, account_email)` | plus `*, now_ts: float = 0.0` | Lets the scheduler and the tests ask about a fixed instant rather than "now". Keyword-only with a default; existing calls are unaffected. |

Rule 2 of `next_send_times` says "round-robin across enabled accounts". The implementation
orders candidates by *(earliest available, fewest sends placed, account index)*, which produces
the same rotation when accounts are equally free and a better one when they are not — an
account throttled by the hourly cap does not stall the whole rotation behind it.

The spec names a `PreparedCampaign`. No such class exists: the plan is the returned dict plus
the queued `messages` rows, and resumability comes from the rows surviving in SQLite. That
satisfies the requirement ("must be resumable across app restarts") without a second
representation of the same state.

### Dependencies

`core/enrich.py` optionally imports `brotli` / `brotlicffi` alongside the `dnspython` guard
this spec allows, under the same `try: … except ImportError:` pattern and with the same kind
of fallback (`br` is simply not advertised in `Accept-Encoding`). No new hard dependency, so
the ground rule holds, but the spec did not name it.

`pytest` is a dev dependency, declared in `requirements-dev.txt`. See §12.

### Standards

`List-Unsubscribe-Post: List-Unsubscribe=One-Click` is emitted alongside a **`mailto:`**
`List-Unsubscribe`, as §8 requires. RFC 8058 defines one-click over HTTPS, so this pairing
stretches the RFC. It is deliberate: Gmail and Outlook both accept it and surface the
unsubscribe button, and an HTTPS endpoint would mean running a server this desktop app does
not have.

### Not wired up

`check_replies` and `check_bounces` are implemented in `core/mailer.py` exactly as §8
specifies, and `imap_enabled` exists per account as §2 specifies — but **nothing calls them**.
No module in `core/` or `ui/` invokes either function. The consequences are visible to the
user: the *Replied* and *Bounced* tiles on the Stats tab are always zero, hard bounces are not
auto-suppressed, and a follow-up will go out to a lead who has already replied. A hard SMTP
rejection at send time is still caught (`RECIPIENT:` → the lead is marked `bounced`), but that
path does not cancel that lead's already-queued follow-ups the way `suppress()` does.

This is the largest open gap against §13's spirit, and it is called out in the README's
limitations rather than being papered over.
