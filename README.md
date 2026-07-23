<div align="center">

# 🗺️ MapHarvest

> *Every business. One click.*

**A local desktop app that scrapes Google Maps business listings into clean CSV files — no paid API, no cloud, everything runs on your own machine.**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-41CD52?style=flat-square&logo=qt&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white)
![Chrome](https://img.shields.io/badge/Chrome-required-4285F4?style=flat-square&logo=googlechrome&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

</div>

---

## 📑 Table of Contents

1. [TL;DR](#-tldr)
2. [Why this exists](#-why-this-exists)
3. [The core idea: card-first, detail-on-demand](#-the-core-idea-card-first-detail-on-demand)
4. [Architecture at a glance](#-architecture-at-a-glance)
5. [End-to-end: what happens when you click **Start**](#-end-to-end-what-happens-when-you-click-start)
6. [Deep dive — `core/parse.py` (the extraction core)](#-deep-dive--coreparsepy-the-extraction-core)
7. [Deep dive — `core/scraper.py` (the engine)](#-deep-dive--corescraperpy-the-engine)
8. [Deep dive — `core/enrich.py` (website enrichment)](#-deep-dive--coreenrichpy-website-enrichment)
9. [Deep dive — `core/filters.py` (result filtering)](#-deep-dive--corefilterspy-result-filtering)
10. [Deep dive — `core/exporter.py` & `core/settings.py`](#-deep-dive--coreexporterpy--coresettingspy)
11. [The UI layer (`ui/`)](#-the-ui-layer-ui)
12. [Every field, explained](#-every-field-explained)
13. [Installation](#-installation)
14. [Usage walkthrough](#-usage-walkthrough)
15. [Configuration file](#-configuration-file)
16. [Performance: what's fast, what's slow, and why](#-performance-what-is-fast-what-is-slow-and-why)
17. [Testing & verification philosophy](#-testing--verification-philosophy)
18. [Troubleshooting](#-troubleshooting)
19. [Limitations, reliability & legal](#-limitations-reliability--legal)
20. [Project structure](#-project-structure)
21. [Roadmap](#-roadmap)
22. [Glossary](#-glossary)

---

## 🎯 TL;DR

You type a **business type** (e.g. `roofing company`) and one or more **cities** (e.g. `Toronto`). MapHarvest opens a real Chrome browser, runs the Google Maps search, scrolls the results, and reads each business's **name, category, rating, address, phone, website, coordinates and Google Place ID directly from the results list** — then optionally opens each listing for **hours/reviews**, or fetches each business **website for email + social links**. You can **filter** to only keep businesses that match rules (min rating, has/no website, must-have phone/email, etc.), watch results fill a **searchable, sortable table**, and everything is auto-saved to **CSV**.

No Google Maps API key. No monthly fee. No data leaves your computer except the requests Chrome makes to load the pages you asked for.

---

## 🧭 Why this exists

The official Google Places API costs money per request and rate-limits hard. For local lead-generation and market research, people just want *"give me every roofer in these 5 cities with their phone and website."* MapHarvest does that by **driving a normal browser the way a human would** and reading the page — which is free, but comes with one responsibility: Google's HTML changes over time, so the extraction code is written to be resilient and easy to re-point (see [Reliability](#-limitations-reliability--legal)).

Two design constraints shaped everything:

- **It must be fast.** Opening a separate page for every business is the naive approach and it's painfully slow. MapHarvest avoids that wherever possible.
- **It must be honest.** When Google blocks the browser, or a business genuinely has no phone/email, the app says so rather than silently returning garbage.

---

## 💡 The core idea: card-first, detail-on-demand

This is the single most important thing to understand about the project.

When you search Google Maps, the left-hand **results feed** is a scrolling list of **cards**. Each card is not just a title — it already contains a surprising amount of structured data, and the card's link (`href`) encodes even more:

```
https://www.google.com/maps/place/Alpine+Roofing/data=!4m7!3m6
  !1s0x89d4cc9b150c8a9f:0xb0887b40788685bd   ← CID (hex place id pair)
  !8m2!3d43.656728!4d-79.338035              ← latitude / longitude
  !16s%2Fg%2F1tk68nss                        ← feature id
  !19sChIJn4oMFZvM1IkRvYWGeEB7iLA            ← ChIJ Place ID
```

So from the **feed alone**, without opening anything, we can extract: **name, rating, category, address, phone, website, latitude, longitude, and a stable Place ID.**

**The old approach** (before the rewrite) opened a brand-new browser tab for *every single business*, waited for a full page load, scraped obfuscated CSS classes, then closed the tab. For 50 results that's ~50 page loads — minutes of wall-clock time and a failure point at every step.

**The current approach:**

| Step | What it does | Cost |
|------|--------------|------|
| 1. **Card pass** | Scroll the feed, parse all cards in one shot per scroll | Milliseconds per card, no navigation |
| 2. **Detail fallback** | Open a listing's page **only if** a requested field isn't on the card (Hours, Reviews — or Phone/Website for the minority of categories that hide them) | One page load, only when needed |
| 3. **Enrichment** | Fetch the business **website** over plain HTTP (not the browser) for email + socials | One HTTP request, only when requested |

For a service-business search (roofers, plumbers, dentists — which show phone + website on the card), a typical run does **zero** per-listing navigations. That's the difference between **~2 seconds** and **several minutes** for the same 4 results.

> **Rule of thumb:** everything except **Hours**, **Reviews**, and **Email/socials** is free (feed-level). Those three are the "slow path" and are **off by default**.

---

## 🏗️ Architecture at a glance

```
main.py  ─►  ui.app.run()  ─►  MainWindow
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                        ▼
      ui/screen_input.py                       ui/screen_results.py
   (Scrape / Filters / Settings)          (live table, progress, export)
              │  start_signal(...)                     ▲  Qt signals
              └───────────────────────────────────────┘
                                  │ creates
                                  ▼
                         core/scraper.py
                     ScrapeWorker (QThread)
                                  │ per (domain, area)
                                  ▼
                 scrape_domain_progressive(...)
                    │            │            │
            core/parse.py   core/enrich.py  core/filters.py
          (parse feed +     (email/social   (keep/skip a
           place URLs)       from website)   business)
                                  │
                                  ▼
                         core/exporter.py  ──►  CSV
```

**Separation of concerns** (this is deliberate and makes the risky parts testable without a browser):

| Layer | Files | Responsibility | Browser needed? |
|-------|-------|----------------|-----------------|
| **Pure logic** | `core/parse.py`, `core/enrich.py`, `core/filters.py` | Turn HTML/URLs into data; decide keep/skip | ❌ No — fully unit-testable offline |
| **Orchestration** | `core/scraper.py` | Drive Chrome, scroll, decide when to open pages, run the pure logic, thread it off the UI | ✅ Yes |
| **Presentation** | `ui/*.py`, `core/exporter.py`, `core/settings.py` | Collect input, show results, save CSV, persist prefs | ❌ (Qt only) |

Because the extraction rules live in **pure functions**, the whole "did we parse this right?" question is answered by tests that run against a **saved copy of a real Google Maps page** — no flaky live browser required (see [Testing](#-testing--verification-philosophy)).

---

## 🔄 End-to-end: what happens when you click **Start**

1. **`ui/screen_input.py` → `_on_start()`** validates that you entered at least one domain, one area, an export folder, and one field. It reads the checked fields, the filters, headless flag, and max-results, then emits:
   ```python
   start_signal(domains, areas, fields, headless, max_results, export_dir, filters)
   ```
2. **`ui/app.py → MainWindow.on_start()`** hands those to the results screen, resizes the window, switches to the results view, and starts the worker.
3. **`ui/screen_results.py → setup()`** builds the table columns (prepending a **Search Domain** column if you gave multiple domains, and a **Search Area** column if you gave multiple areas), resets counters, and wires up the live table.
4. **`start_worker()`** constructs a **`ScrapeWorker`** (a `QThread`) and connects its signals to table/progress/toast slots. The scrape now runs **on a background thread**, so the UI never freezes.
5. **`ScrapeWorker.run()`** launches Chrome once, then builds the task list = **every (domain × area) pair** and loops over them.
6. For each pair it calls **`scrape_domain_progressive(...)`**, which does the actual work: navigate → dismiss consent → detect the feed → scroll & parse cards → (optionally) open detail pages → (optionally) enrich websites → apply filters → **emit each matching business** back to the UI via a Qt signal.
7. As each business is emitted, **`add_table_row()`** appends it to the live table and the progress counter ticks up.
8. When a (domain, area) finishes, its rows are **auto-exported to a CSV** named `domain_in_area.csv`, a toast confirms the path, and (in multi-search mode) the table clears for the next pair.
9. When everything finishes, **`done_signal`** flips the UI to idle, enables header-click sorting, and shows the final summary.

Every arrow between the worker and the UI is a **Qt signal**, which is the thread-safe way to talk from a background thread to the GUI thread.

---

## 🔬 Deep dive — `core/parse.py` (the extraction core)

This module is **pure**: give it a string of HTML (or a URL) and it returns Python dicts. No Selenium, no network. That's why it can be tested against a saved page.

### `parse_place_url(href)`
Regex-extracts, from a Maps place URL: `latitude`, `longitude` (`!3d…!4d…`, with an `@lat,lng` fallback), `place_id` (`!19sChIJ…`), `cid` (the `!1s0x…:0x…` hex pair), and a cleaned `maps_link`. **Why:** these identifiers are stable and free — the Place ID in particular is the same ID Google's official API uses, so your CSV can later be joined against other data sources.

### `place_key(href)`
Returns the best available stable ID (Place ID → CID → URL path) used for **de-duplication**. Google's feed sometimes shows the same business twice with slightly different URLs; keying on the Place ID guarantees each business is collected once.

### `parse_card(card_element)`
Given one feed card (an `lxml` element), it reads:

- **name** — from the card link's `aria-label` (falls back to the `.qBF1Pd` headline). *Why aria-label?* It's a semantic attribute Google keeps stable for accessibility, unlike the obfuscated class names.
- **sponsored flag** — presence of a "Sponsored" label. Sponsored/ad cards are skipped entirely.
- **rating** — `.MW4etd`, with a fallback to the `aria-label="4.5 stars"` on the rating widget.
- **review_count** — best-effort from the card (many searches don't show it on the card, in which case it's left empty and can be filled by the detail pass).
- **phone** — `span.UsdlK`, with a regex fallback that scans the card's info rows.
- **website** — `a[data-value="Website"]`, but **only if it's a real URL** (ad-click `/aclk` redirects are rejected).
- **category + address** — parsed out of the card's `.W4Efsd` "info rows" by splitting on the middot (`·`) separator and dropping status text like *"Open 24 hours"*.
- **coordinates + Place ID** — via `parse_place_url` on the card's `href`.

### `parse_feed_html(html)`
Finds every card in a feed's HTML and returns a list of parsed dicts. This is what the scraper calls each scroll iteration.

> **Design choice:** the parser prefers **semantic signals** (`aria-label`, `data-value`, `role`) and only uses obfuscated class names (`Nv2PK`, `hfpxzc`, `qBF1Pd`, `MW4etd`, `UsdlK`, `W4Efsd`) as anchors. Semantic signals change far less often. When Google *does* rotate its markup, the fix is localized to this one file — and you can validate the fix offline against a captured page.

---

## ⚙️ Deep dive — `core/scraper.py` (the engine)

This is the only module that touches the browser. Roughly top-to-bottom:

### Browser setup — `get_driver(headless)`
Creates an **undetected-chromedriver** Chrome. `undetected-chromedriver` patches the automation flags that make vanilla Selenium easy for Google to detect. Notable options: `--lang=en-US` and an `Accept-Language: en-US` preference (so selectors and the *"You've reached the end of the list"* text stay in English), and `page_load_strategy = "eager"` (don't wait for every last image). `_chrome_major_version()` sniffs your installed Chrome version so the driver matches it.

### Getting to the results — consent & blocking
- **`_dismiss_consent_screen(driver)`** clicks through Google's cookie/consent wall. **Why it matters:** on many non-US connections Google shows `consent.google.com` *before* the map. If you don't click it, the feed never appears and everything downstream silently finds zero results. This was historically the #1 cause of "scrolls forever, finds nothing."
- **`_page_blocked_reason(driver)`** detects the three "you've been flagged" states — the consent page, the *"unusual traffic"* / CAPTCHA page, and a reCAPTCHA challenge — and returns a human-readable reason so the UI can tell you exactly what happened (and a screenshot + HTML are dumped to `debug/`).

### Finding & reading the feed
- **`_get_feed(driver)`** locates the scrollable results container (`div[role="feed"]`, with `aria-label` fallbacks).
- **`_feed_html(driver)`** grabs that container's `outerHTML` so `core/parse.py` can parse it. **Why snapshot the HTML instead of walking the DOM with Selenium?** One `outerHTML` read + one fast `lxml` parse beats hundreds of individual Selenium round-trips, and it's immune to "stale element" errors mid-scroll.

### The scroll fix (the important part) — `_nudge_feed` / `_scroll_for_more`
Google Maps lazy-loads more results only when it detects a **real scroll gesture**. This is subtle and was the cause of a "caps out at ~7 results" bug:

- Setting `element.scrollTop = scrollHeight` **does nothing** — Maps ignores programmatic scroll-position changes.
- Calling `scrollIntoView()` on the last card **fights** the scroll and freezes it.
- Picking "whatever div has the most links" as the scroll target **latches onto an inner wrapper** once results load and stalls.

The method that actually works (`_nudge_feed`): target `div[role="feed"]` specifically, set `scrollTop = scrollHeight` **and dispatch a `WheelEvent({deltaY: 1500, bubbles: true})`** on it. `_scroll_for_more` then polls the card count for up to ~5 s, re-nudging until the feed grows or the end-of-list appears. Verified live: this pulls **50 unique results in ~12 s** where the old code stopped at 6.

### The collection loop — `scrape_domain_progressive(...)`
This is the heart. Given one `(domain, area)`, a field list, a max, and a filter spec, it:

1. Navigates to the search URL, dismisses consent, checks for blocking, waits for the feed.
2. Computes a **collection cap**. If your filters can only be decided *after* extra work (must-have phone/email, review-count thresholds), it **over-collects** (up to `target × 4`) so it can still reach your requested count after some candidates are rejected.
3. **Loops:** parse the current feed → for each new, non-sponsored, deduplicated card:
   - Apply **cheap filters** (rating, has/no website, name/category text) — these are decidable from the card, so rejects cost nothing.
   - If the card needs a **detail page** (`_detail_wants` returns non-empty), queue it for the detail pass.
   - Otherwise, **enrich** the website over HTTP if requested, apply **full filters**, and if it passes, **emit it live** and count it.
   - Checkpoints for **Pause/Stop** are inside this loop, so a long enrichment batch can be interrupted promptly.
4. Between rounds, it scrolls (`_scroll_for_more`) and watches for the **end of list**, a **stall** (no new cards for several rounds), or a **watchdog timeout** — so it can never loop forever.
5. **Detail pass:** for every queued card, open its Maps page (`_extract_detail`) to fill Hours / Reviews / a missing Phone·Website / an exact review count, enrich if needed, apply full filters, and emit if it passes.
6. Returns `(matched_count, completed_naturally)`.

Helper decisions:
- **`_detail_wants(card, fields, spec)`** — returns exactly which detail-page fields to fetch for this card, i.e. *only the gaps*: Hours/Reviews always; Phone/Website/review_count/rating only if requested-and-missing; and the website URL when enrichment needs it even if you didn't ask for the Website column. It's empty for a card that already has everything → no page load.
- **`_enrich_wants(card, fields, spec)`** — which enrichment fields to fetch, and only if the card actually has a website to fetch from.
- **`_extract_detail(driver, href, want)`** — opens the place page and reads only the requested detail fields using Google's stable `data-item-id` attributes (`address`, `phone`, `authority`=website) plus Hours/Reviews widgets.

### Threading — `ScrapeWorker(QThread)`
The scrape runs on a background thread so the GUI stays responsive. It exposes Qt **signals** (`log_signal`, `progress_signal`, `result_signal`, `domain_finished_signal`, `done_signal`, `error_signal`, `paused_signal`) — the thread-safe way to push updates to the UI thread. **Pause** uses a `threading.Event`; **Stop** flips a flag checked throughout the loop; **multi-search** pausing between tasks uses a second event so the UI can export each search's CSV before continuing.

**Multi-city:** `run()` builds `tasks = [(d, a) for d in domains for a in areas]` and processes each pair like an independent search, emitting a `domain_finished_signal(domain, area, …)` after each so the UI can save that pair's CSV.

---

## 🌐 Deep dive — `core/enrich.py` (website enrichment)

Turns a business **website URL** into `email` + social profiles (Facebook, Instagram, LinkedIn, Twitter/X, YouTube). **Uses only the Python standard library (`urllib`)** — deliberately, so there's no extra dependency to install and it works anywhere the app runs.

- **`_fetch(url)`** downloads the page (with a real browser User-Agent, gzip support, and a 1.5 MB cap) with an 8-second timeout.
- **`extract_contacts(html, base_url)`** (pure, tested): pulls emails (preferring `mailto:` links, then anything matching an email pattern) and social URLs.
  - **Email hygiene** — rejects asset artifacts (`logo@2x.png`), tracking/CDN/platform junk (Sentry, Wix, Cloudflare, etc. — matched *anywhere* in the domain, and long hex "tracking id" local parts), and prefers an email on the **site's own domain** (using an exact / subdomain match, so `wisdom.com` isn't confused with `notwisdom.com`).
  - **Social hygiene** — rejects share/intent/plugin widget URLs but *keeps* legitimate profiles that carry a query string (e.g. `facebook.com/profile.php?id=…`).
- **`enrich_website(url, fields)`** fetches the homepage, and if no email is found there, follows one likely **contact/about** link and tries again. Never raises — a failure just yields empty strings.

> **Honest by design:** if a business genuinely doesn't publish an email in its HTML (many use a contact form or load it via JavaScript), the field comes back blank. That's a *real* "no email," not a scraper failure.

---

## 🧮 Deep dive — `core/filters.py` (result filtering)

Decides **keep or skip** for each business, from a plain-dict spec (easy to pass across a Qt signal and to persist). The key idea is a **cost split**:

- **`cheap_pass(record, spec)`** — checks that are decidable from **card-level data**: min rating, has/no website, name include/exclude, category include/exclude. Applied **during collection**, so a business that fails is dropped *before* any expensive detail visit or website fetch.
- **`full_pass(record, spec)`** — checks that need **fully-populated fields**: must-have phone, must-have email, min/max review count. Applied **just before a row is emitted**, after any detail visit / enrichment.
- **`needs_phone` / `needs_email` / `needs_reviews`** — tell the scraper which *extra work* a filter forces (e.g. a review-count filter makes the engine open the listing to read the exact count, even if you didn't tick the Reviews field).
- **`normalize_spec`** coerces raw UI values (strings, spin-box numbers) into a clean, typed spec; **`is_active`** reports whether any filter is set.

**Why the split matters:** it means filtering is *cheap when it can be* and only pays for detail work when the filter genuinely requires it. A "min rating 4.5 + has a website" search stays fully feed-level and fast; a "must have an email" search accepts the cost of fetching each website because that's the only way to know.

---

## 🗃️ Deep dive — `core/exporter.py` & `core/settings.py`

- **`core/exporter.py`** — `FIELD_LABELS` maps internal field keys (`name`, `phone`, `latitude`, `email`, …) to human CSV headers (`Business Name`, `Phone`, `Latitude`, `Email`, …). `export_csv()` writes UTF-8 **with a BOM** (`utf-8-sig`) so Excel opens accented characters correctly, and only writes the columns you selected, in order.
- **`core/settings.py`** — loads/saves `~/.mapharvest/settings.json` (headless flag, slider cap, default max, export folder, and your last 12 searches). It merges saved values over defaults defensively, so a corrupt or partial settings file never crashes the app.

---

## 🖥️ The UI layer (`ui/`)

Built with **PyQt5**, styled by a single dark stylesheet (`QSS` in `ui/app.py`). Everything the user sees lives here; none of it blocks on the network because scraping is on the worker thread.

### `ui/app.py`
- **`MainWindow`** owns a `QStackedWidget` with two screens (input ↔ results), wires `start_signal → on_start`, `stop_signal`, `home_signal`, and resizes the window between the compact home view and the wider results view.
- **`QSS`** — the entire dark theme (inputs, buttons, table, tabs, sliders, spinboxes, the new search box, combo boxes, and right-click menus) in one place.
- **`run()`** — the entry point `main.py` calls: creates the `QApplication`, applies the Fusion style + font + stylesheet, shows the window.

### `ui/screen_input.py` — three tabs

**Scrape tab**
- **Domain** + **List** button — one business type, or many (`ListDialog`) to run several categories in one session.
- **Area** + **List** button — one city, or many. Each domain is run against **every** area (multi-city).
- **Max Results** slider — how many businesses to collect per search (cap raised in Settings).
- **Export Folder** — where CSVs are auto-saved.
- **Recent Searches** — your last 12 searches, click to refill.
- **Data to Scrape** — a scrollable checklist of every field, with **All / Fast only / None** quick-select buttons. Slow fields (Hours, Reviews, Email + socials) are **off by default** and tool-tipped as slower.

**Filters tab** — only collect businesses that match: min rating, min/max reviews, website (Any / Has / None), must-have phone, must-have email, and name/category include-exclude (comma-separated). A **Reset filters** button clears them.

**Settings tab** — headless toggle and the max-results slider cap (up to 1000).

`get_filters()` packages the Filters tab into the spec dict; `_get_domains()` / `_get_areas()` de-duplicate and combine the single input with its list; `validate()` guards the required inputs (and shakes the offending field if something's missing).

### `ui/screen_results.py` — the live power table
- **Live search box** — hides rows that don't contain your text (matches the **full** value, even for cells truncated in the display).
- **Sortable columns** — click a header to sort; `SortableItem` sorts **numerically** when the column is numeric (rating, reviews, coordinates) and alphabetically otherwise. Sorting is enabled once a run finishes (so live inserts don't jump around).
- **Row actions** — **double-click** a row to open it in Google Maps; **right-click** for: open in Maps, open website, copy phone, copy email, copy name, copy the whole row (tab-separated). The full record is stashed on each row so actions still work after you sort.
- **Progress** — a live count, a status line echoing the current business, and a thin progress bar.
- **Buttons** — **Pause/Resume**, **Stop** (keeps what's collected), an on-demand **Export CSV**, and after a run, **Scrape Another**.
- **Auto-export** — each `(domain, area)` search saves its own CSV automatically; a toast shows the saved path.

### `ui/domain_list_dialog.py`
A small `ListDialog` (one item per line) reused for both the **domain list** and the **area list**; `DomainListDialog` is a thin backward-compatible wrapper.

---

## 🧾 Every field, explained

| Field | CSV Column | Source | Speed |
|-------|-----------|--------|-------|
| Business Name | Business Name | Feed card | ⚡ instant |
| Category | Category | Feed card | ⚡ instant |
| Rating | Rating | Feed card | ⚡ instant |
| Review Count | Review Count | Feed card if shown, else the listing page | ⚡ / 🐢 |
| Address | Address | Feed card (street) → full on detail visit | ⚡ / 🐢 |
| Website | Website | Feed card (services) → listing page (POI) | ⚡ / 🐢 |
| Phone | Phone | Feed card (services) → listing page (POI) | ⚡ / 🐢 |
| Maps Link | Maps Link | Feed card URL | ⚡ instant |
| Latitude / Longitude | Latitude / Longitude | Decoded from the card URL | ⚡ instant |
| Place ID | Place ID | Decoded from the card URL | ⚡ instant |
| Hours | Hours | **Opens the listing page** | 🐢 slow |
| Review 1–3 | Review 1–3 | **Opens the listing page** | 🐢 slow |
| Email | Email | **Fetches the business website** | 🐢 slow |
| Facebook / Instagram / LinkedIn / Twitter-X / YouTube | (same) | **Fetches the business website** | 🐢 slow |
| Search Domain | Search Domain | Auto-added when using multiple domains | ⚡ |
| Search Area | Search Area | Auto-added when using multiple areas | ⚡ |

⚡ = read from the results feed with no extra navigation. 🐢 = requires opening a listing page or fetching a website (off by default).

---

## 🚀 Installation

**Prerequisites:** Python **3.10+**, **Google Chrome** installed, Windows/macOS/Linux.

```bash
# 1. Clone
git clone <your-repo-url> "Map Harvest"
cd "Map Harvest"

# 2. Virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 3. Dependencies
pip install -r requirements.txt

# 4. Run
python main.py
```

`requirements.txt`: `PyQt5`, `selenium`, `undetected-chromedriver`, `lxml`. (Enrichment and filtering use only the standard library.)

---

## 🖱️ Usage walkthrough

1. **Domain** — type a business type (`roofing company`). Use **List** to add more categories.
2. **Area** — type a city (`Toronto`). Use **List** to add more cities; every domain runs against every city.
3. **Max Results** — set how many businesses per search.
4. **Data to Scrape** — leave the defaults for a fast run, or tick Hours/Reviews/Email/socials for a richer (slower) run.
5. **Filters** (optional) — e.g. *min rating 4.5*, *No website* (great for selling web design), *Must have email*.
6. **Export Folder** — pick where CSVs land.
7. Click **Start Scraping** and watch the table fill. **Pause/Stop** any time. Sort, search, and right-click rows. CSVs save automatically per search.

---

## 🗂️ Configuration file

`~/.mapharvest/settings.json`:

```json
{
  "headless": false,
  "max_limit_cap": 100,
  "default_max_results": 50,
  "export_dir": "C:/Users/you/Desktop/leads",
  "saved_searches": [
    { "domains": ["roofing company"], "area": "Toronto", "max_results": 50 }
  ]
}
```

---

## ⏱️ Performance: what is fast, what is slow, and why

| Scenario | Navigations | Typical time (per ~5 results) |
|----------|-------------|-------------------------------|
| Service search, feed-level fields (name, rating, phone, website, coords) | **0** | ~2–3 s |
| POI search (café/restaurant) needing phone/website | 1 per result | ~2–3 s each |
| Any search + **Hours/Reviews** | 1 per result | ~2–3 s each |
| Any search + **Email/socials** | 0 browser, 1 HTTP fetch per result | ~1–8 s each |

**Why the variance:** Google puts phone/website on the card for **service businesses** but hides them for many **points of interest**; enrichment always costs one website fetch; Hours/Reviews always cost one listing open. The engine only pays these costs for the exact fields you asked for, on the exact businesses that need them.

**Google's hard ceiling:** a single Maps search returns at most ~120 results no matter what. For a big city, split by neighbourhood or use multiple areas.

---

## ✅ Testing & verification philosophy

Because the extraction logic is **pure**, it's tested without a browser:

- **`tests/test_parse.py`** — runs the card/URL parser against a **saved real Google Maps feed** (`debug/initial_*.html`) and asserts every business's name, rating, phone, website, coordinates and Place ID come out correctly, and that sponsored cards are flagged.
- **`tests/test_enrich_filters.py`** — asserts email/social extraction (including the junk-email and query-string edge cases) and every filter predicate.

Run them:
```bash
venv/Scripts/python.exe -m tests.test_parse
venv/Scripts/python.exe -m tests.test_enrich_filters
```

Beyond unit tests, changes are verified **live** (small headless scrapes against real Google) and the **PyQt UI is exercised off-screen** (`QT_QPA_PLATFORM=offscreen`) so wiring bugs surface without a display.

---

## 🔧 Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| "Stuck on cookie-consent page" | A consent wall the click didn't clear — run **non-headless** once so you can see/accept it. Check `debug/`. |
| "Unusual traffic / CAPTCHA" | The browser got flagged — slow down, lower Max Results, try again later, run non-headless. |
| Only ~7 results | (Fixed) If it ever recurs, the feed scroll selector changed — see `_nudge_feed`; re-capture a feed and adjust. |
| Empty category/hours, or 0 listings | Google changed its markup — a `debug/*.html` + `.png` snapshot is saved; update the selectors in `core/parse.py` (validate offline against the snapshot). |
| Phone/website blank for cafés | Normal — those categories hide contact info on the card; the engine falls back to the listing page, but some genuinely list none. |
| Email blank | The site doesn't expose one in its HTML (contact form or JS-loaded). Not a bug. |
| Chrome won't start | Update Google Chrome; delete any cached mismatched chromedriver. |

---

## ⚠️ Limitations, reliability & legal

- **Google's HTML changes.** No Maps scraper can be *permanently* guaranteed. MapHarvest defends against this with semantic selectors, offline tests, and automatic `debug/` snapshots so a break is quick to diagnose and re-point — but expect occasional selector maintenance in `core/parse.py`.
- **The "No website" filter is card-level.** It reflects what Maps shows on the card — accurate for service businesses, less precise for POIs (where a website may only appear on the listing page). On the roadmap to make detail-accurate.
- **Rate limits.** Heavy/rapid scraping can trigger CAPTCHAs. Use reasonable limits.
- **~120-result cap per search** is Google's, not the app's.
- **Terms of Service.** Scraping Google Maps may violate [Google's ToS](https://policies.google.com/terms). Use responsibly, for personal or permitted purposes. The author is not responsible for misuse.

---

## 📁 Project structure

```
Map Harvest/
├── main.py                     # Entry point → ui.app.run()
├── requirements.txt            # PyQt5, selenium, undetected-chromedriver, lxml
├── main.spec                   # PyInstaller spec (optional packaging)
│
├── core/
│   ├── parse.py                # PURE: parse feed HTML + place URLs  → data
│   ├── enrich.py               # PURE: website → email + social links (stdlib only)
│   ├── filters.py              # PURE: keep/skip a business (cheap vs full)
│   ├── scraper.py              # Selenium engine + ScrapeWorker (QThread)
│   ├── exporter.py             # CSV writing + field labels
│   ├── settings.py             # ~/.mapharvest/settings.json load/save
│   └── distutils_compat.py     # Python 3.12+ shim (undetected-chromedriver)
│
├── ui/
│   ├── app.py                  # MainWindow, dark theme (QSS), run()
│   ├── screen_input.py         # Scrape / Filters / Settings tabs
│   ├── screen_results.py       # Live searchable/sortable table + row actions
│   └── domain_list_dialog.py   # Generic multi-line list dialog (domains, areas)
│
├── tests/
│   ├── test_parse.py           # Offline parser tests vs a captured feed
│   └── test_enrich_filters.py  # Offline enrichment + filter tests
│
└── debug/                      # Auto-saved page snapshots on failure (gitignored)
```

---

## 🛣️ Roadmap

- Concurrent detail/enrichment for POI- or email-heavy runs.
- Parse `window.APP_INITIALIZATION_STATE` to recover phone/website without any detail visit.
- Auto-split big cities into sub-areas to beat Google's ~120 cap.
- SQLite persistence + resume/append across sessions; Excel/JSON export.
- Make the "No website" filter detail-accurate for POIs.

---

## 📖 Glossary

- **Feed / card** — the scrolling results list on the left of Maps, and one business entry in it.
- **Detail page** — an individual business's Maps page (opened only when needed).
- **Enrichment** — fetching a business's own website (over HTTP) to find email + socials.
- **Place ID** — Google's stable identifier for a place (same one the official API uses).
- **Cheap vs full filter** — filters decidable from the card vs filters needing extra fetched data.
- **Card-first** — read everything possible from the feed before opening anything.

---

<div align="center">

Made for local lead generation and business research — without a paid Maps API.

Found it useful? A ⭐ helps.

</div>
