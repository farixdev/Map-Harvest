<div align="center">

# 🗺️ MapHarvest

> *Every business. One click.*

**A local desktop app that scrapes Google Maps business listings into clean CSV files — and, when you ask it to, turns that list into a paced, personalised cold-email campaign sent from your own Gmail. No paid API, no cloud, everything runs on your own machine.**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-41CD52?style=flat-square&logo=qt&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white)
![Chrome](https://img.shields.io/badge/Chrome-required-4285F4?style=flat-square&logo=googlechrome&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

</div>

---

> **Engineers:** for a flow-by-flow walkthrough of the runtime (19 numbered flows), a selector
> registry, the threading/shutdown model, the build pipeline and a verified defect audit, see
> **[docs/TECHNICAL_REFERENCE.md](docs/TECHNICAL_REFERENCE.md)** (also available as
> [PDF](docs/MapHarvest-Technical-Reference.pdf)). The outreach half has its own binding
> interface spec — module signatures, settings keys, database columns — in
> **[docs/OUTREACH_SPEC.md](docs/OUTREACH_SPEC.md)**. This README stays the product-level
> overview.

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
13. [The outreach pipeline, end to end](#-the-outreach-pipeline-end-to-end)
14. [Deep dive — the outreach modules](#-deep-dive--the-outreach-modules)
15. [The outreach UI](#-the-outreach-ui)
16. [Installation](#-installation)
17. [Setup you must do yourself](#-setup-you-must-do-yourself)
18. [Sending limits, warm-up, and quota errors](#-sending-limits-warm-up-and-quota-errors)
19. [Dry run is on by default](#-dry-run-is-on-by-default)
20. [Compliance: unsubscribe, suppression, one first touch](#-compliance-unsubscribe-suppression-one-first-touch)
21. [Usage walkthrough](#-usage-walkthrough)
22. [Configuration files](#-configuration-files)
23. [Performance: what's fast, what's slow, and why](#-performance-what-is-fast-what-is-slow-and-why)
24. [Testing & verification philosophy](#-testing--verification-philosophy)
25. [Troubleshooting](#-troubleshooting)
26. [Limitations, reliability & legal](#-limitations-reliability--legal)
27. [Project structure](#-project-structure)
28. [Roadmap](#-roadmap)
29. [Glossary](#-glossary)

---

## 🎯 TL;DR

You type a **business type** (e.g. `roofing company`) and one or more **cities** (e.g. `Toronto`). MapHarvest opens a real Chrome browser, runs the Google Maps search, scrolls the results, and reads each business's **name, category, rating, address, phone, website, coordinates and Google Place ID directly from the results list** — then optionally opens each listing for **hours/reviews**, or fetches each business **website for email + social links**. You can **filter** to only keep businesses that match rules (min rating, has/no website, must-have phone/email, etc.), watch results fill a **searchable, sortable table**, and everything is auto-saved to **CSV**.

No Google Maps API key. No monthly fee. No data leaves your computer except the requests Chrome makes to load the pages you asked for.

**And then, optionally, the second half.** Press **Start Outreach** on the results and the same list becomes a cold-email campaign: each business's website is crawled for a real contact address, audited offline for the automation gaps worth pitching, personalised by a language model in three short lines, rendered into a plain-text-first email with an unsubscribe footer, and queued to leave your own Gmail a few dozen a day inside your working hours. It is **dry-run by default** — the first run rehearses the whole schedule and sends nothing. See [The outreach pipeline](#-the-outreach-pipeline-end-to-end) and, before you send anything at all, [Setup you must do yourself](#-setup-you-must-do-yourself).

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

The outreach half hangs off `ui/screen_results.py` at the far right of that diagram and follows the
same shape — pure rules, thin network wrappers, work on `QThread`s, Qt signals back to the GUI. Its
own diagram is in [The outreach pipeline](#-the-outreach-pipeline-end-to-end).

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

## 📮 The outreach pipeline, end to end

A scraped CSV is a list of businesses. Outreach turns it into a list of **leads** and then into a
**schedule**. Eight steps, in this order, and each one is a place where a lead can honestly drop out:

```
scrape ─► enrich ─► audit ─► personalise ─► queue ─► send ─► follow up ─► suppress
  │         │         │           │           │        │         │           │
Maps    the site's  offline    Groq /      messages   Gmail   same thread   never
feed    own pages   heuristics OpenRouter  table +    SMTP    same account  again
        (email)     (gaps)     (3 lines)   timestamps
```

**1 · Scrape.** The Results screen grows one button, **Start Outreach**, which hands the rows
straight to the Outreach screen. You can also **Import CSV…** from any spreadsheet that has an
email column. Either way each row is upserted into `~/.mapharvest/outreach.db`, **deduplicated on
the lowercased email address**. Businesses with no email cannot be contacted and are counted and
skipped rather than queued. Re-scraping the same city is additive: a second sighting that only
knows the phone number cannot blank out the website and the audit the first pass paid for.

**2 · Enrich.** `core/enrich.harvest_site` fetches the business's own site — the home page plus the
best few of its own links, ranked *contact > about > team > support > impressum*, fetched
**concurrently** because one extra sequential page per lead is what turns a five-minute run into an
hour. It defeats the four things that actually hide an address on a small-business site:
Cloudflare's `data-cfemail` XOR blobs, `info (at) acme (dot) ca` style obfuscation,
`String.fromCharCode` assembly, and JSON-LD / microdata where the address is markup rather than
text. Candidates are **scored, not first-one-wins** — a page routinely carries a webmaster alias, a
careers inbox, a privacy contact and the owner's address, and picking `postmaster@` costs a lead
*and* some sender reputation.

**3 · Audit.** `core/audit` runs over the HTML enrichment already downloaded — **one crawl per
lead, not two** — and produces, entirely offline and for zero tokens: a technology fingerprint (CMS,
ecommerce, chat, booking, CRM, analytics), roughly thirty boolean signals, an `opportunity_score`
from 0 to 100, and the thing that earns its keep, a **`gaps` list ordered severity-first**. Each gap
names the services that close it, resolved through `core/templates.AUTO_ARMY_SERVICES`, so the
email can never pitch a capability the seller does not actually offer. `gaps[0]` is the headline the
copy leads with.

**4 · Personalise.** The model never sees a web page. It receives `audit.digest()` — five lines,
under 1200 characters, roughly 300 tokens — plus one line naming at most six services, and it
returns exactly three short strings: **subject, opener, ps**. The rest of the email comes from
`core/templates`, which is free. Answers are cached on disk at `~/.mapharvest/ai_cache.json` keyed
by domain + template + model, so re-running a 500-lead campaign after a copy tweak costs nothing.
Groq is tried first, OpenRouter second. **Every failure path falls back to the plain template** —
see [AI personalisation is optional](#-limitations-reliability--legal).

**5 · Queue.** `plan_campaign` renders each lead through `core/templates.render` and hands the count
to `next_send_times`, which assigns every message an exact second and a specific sending account.
Follow-ups are planned at the same time, on the same account. Everything lands in the `messages`
table with a `scheduled_at`, so the plan survives the app being closed — a 500-lead campaign at
forty a day is a fortnight of wall-clock time.

**6 · Send.** `OutreachWorker` walks the queue and treats the stored plan as a *proposal*: it
re-checks the sending window, the daily and hourly caps, the warm-up ceiling and the suppression
list **at send time**, against the settings as they are now. A plan made on Friday afternoon is
still in the queue on Monday and the user will have changed two settings in between.

**7 · Follow up.** Up to `followup_max_steps` (default 2) follow-ups per lead, `followup_gap_days`
(default 4) apart, jittered by up to half an hour either way, sent from **the same account** as the
first touch — a reply-chaser from a different address reads as a different sender and loses the
thread.

**8 · Suppress.** An unsubscribe puts the address in the `suppression` table, cancels every queued
and in-flight message for that lead **including the follow-ups already scheduled days out**, marks
the lead `suppressed` and logs an event. Suppression is checked twice: `queue_message` refuses a
suppressed lead when planning, and `due_messages` filters again when reading — so an unsubscribe
that arrives *after* the plan was built still takes effect. See
[Compliance](#-compliance-unsubscribe-suppression-one-first-touch) for how an unsubscribe gets
recorded in the first place.

---

## 🧰 Deep dive — the outreach modules

Nine modules, same separation of concerns as the scraper half: the rules are pure functions over
already-fetched data, and the network and Qt live in thin wrappers around them.

| Module | What it owns | Network? |
|--------|--------------|----------|
| `core/secrets.py` | Encrypting credentials at rest | ❌ |
| `core/settings.py` | The whole settings schema, deep-merged; the secret accessors | ❌ |
| `core/enrich.py` | Website → the address a business actually answers | ✅ |
| `core/audit.py` | HTML → gaps, signals, score, digest | ✅ (thin wrapper) |
| `core/ai.py` | Digest → subject / opener / ps, cached and budgeted | ✅ |
| `core/templates.py` | The service catalogue, the templates, rendering | ❌ |
| `core/outreach_db.py` | SQLite: leads, campaigns, messages, suppression, sends, events | ❌ |
| `core/mailer.py` | Building the message; Gmail SMTP and IMAP | ✅ |
| `core/campaign.py` | The schedule (pure) and the two worker threads | ✅ |

### `core/secrets.py` — credentials at rest
`settings.json` sits in your home directory in plain sight and now carries Gmail App Passwords and
API keys, so everything sensitive goes through here first. On Windows the payload is sealed with
**DPAPI** (`CryptProtectData`) scoped to the logged-in user: Windows holds the key material and the
ciphertext is inert on another machine or under another account. Everywhere else — and on any DPAPI
failure — it falls back to XOR against a key derived from the machine name, which is **obfuscation,
not security**, and is documented as such in the module. Stored form is `enc:v1:<base64>`; a value
without that prefix passes through `decrypt` untouched, which is the migration path for files
written before encryption existed. A token this machine cannot open yields `""`, so you get an empty
credential and a re-prompt rather than binary garbage handed to a mail server.

### `core/settings.py` — one schema, deep-merged
`DEFAULT_SETTINGS` **is** the schema. Load and save are deliberately lossy — keys outside the schema
are dropped on the next save, so the file cannot accumulate junk from dead releases. The merge is
*deep*, so a file written by an older build gains sub-keys added since (a new `sender_profile`
field, a new per-account flag) instead of being flattened back. Saves are write-then-rename: a crash
mid-write would otherwise cost every saved search and every App Password at once. `get_secret` /
`set_secret` are the only sanctioned way to touch a credential.

### `core/enrich.py` — finding the address
Covered in [step 2](#-the-outreach-pipeline-end-to-end) above. Three details worth knowing:

- **Encoding matters.** A hardcoded UTF-8 decode mangles the Latin-1, cp1252 and Shift-JIS sites
  that are exactly the neglected businesses worth pitching, so the charset is sniffed from the
  header, the `<meta>` tag and the bytes.
- **A bad certificate is a signal, not a reason to stop.** An expired or self-signed cert usually
  means a neglected site, which is the target market; the fetch retries without verification rather
  than throwing the lead away.
- **`harvest_site` returns the HTML it fetched** under `"html"`, which is what lets the audit run
  off the same download.

The old `extract_contacts` / `enrich_website` API is unchanged and still used by `core/scraper.py`
for the Email/socials columns in the scrape half.

### `core/audit.py` — the evidence the email stands on
Detection is deliberately conservative: a wrong *"no online booking"* in a live cold email is worse
than a missed gap, because it tells the reader you did not look. So every signal fires on a concrete
marker — a script host, a link, a schema type, a printed date — and staleness is never claimed
without a date to point at. `audit_from_html(pages, base_url)` is the pure core and holds every
rule, which is why the whole catalogue is testable from handwritten fixtures.

### `core/ai.py` — as few tokens as possible
One system prompt of ~120 fixed words, one user message of ~300 tokens, three strings back. Groq and
OpenRouter both speak the OpenAI chat-completions dialect, so there is one request builder and a
small table of per-provider URL, key and headers. A monthly token budget (`ai_monthly_token_cap`,
default 2,000,000) is counted in settings and resets on the calendar month. Tokens are charged
**before** the reply is parsed — a reply that turns out unusable was still billed, and pretending
otherwise would let a broken model spend the month for free. Nothing here raises and nothing here is
required.

### `core/templates.py` — the copy
`AUTO_ARMY_SERVICES` is the real service catalogue, written the way the seller writes it, and a
detected gap is turned into service names taken **verbatim** from it. Three first-touch templates
(*Headline gap*, *Hours back*, *One question*) and two follow-ups (*bump*, *close*).

The rule that shapes the whole renderer: **a leaked merge field is the worst bug in this system.** A
live cold email reading `Hi {{first_name}},` burns the domain and the prospect. So `render` never
emits `{{…}}` — every unresolved token becomes an invisible gap marker that is deleted *along with
the punctuation and whitespace that belonged to it*, and a missing value degrades to a shorter
sentence rather than to visible machinery. Copy rules are structural, not advisory: under 120 words
on a first touch, exactly one link, a real sign-off, a subject under 55 characters with no shouting
and **no fake `Re:`**. `to_html` is a deliberately dumb renderer — system fonts, paragraphs, one
anchor, a grey footer. No images, no tables, nothing that makes a filter look twice.

### `core/outreach_db.py` — why a database
A campaign is a *schedule*, not a document. It has to survive the app being closed, and two threads
(the worker sending, the GUI redrawing counters) read and write it at once. One connection is
opened lazily per path and shared, WAL journalling so a reader is never blocked behind the writer,
and a module-level re-entrant lock around every write. Rows come back as plain **dicts**, never
`sqlite3.Row`, because they cross a `pyqtSignal` boundary. Six tables: `leads`, `campaigns`,
`messages`, `suppression`, `sends`, `events`. Nothing raises — a locked or corrupt database degrades
to an empty list rather than a traceback out of a worker thread.

### `core/mailer.py` — looking like a person, not a bulk sender
One asymmetry drives this file: a lead who never sees the email costs nothing to fix, and a Gmail
account that gets flagged costs the user their inbox. Concretely:

- **No tracking pixel, no open tracking, no click wrapping, no images.** A 1×1 image from a domain
  nobody has heard of is one of the cheapest bulk-mail signals there is. This is a decision, not a
  gap.
- Real `Message-ID` on the sender's own domain, `Date`, `Reply-To`, `List-Unsubscribe` and
  `List-Unsubscribe-Post` on every send.
- `multipart/alternative` with `text/plain` **first**, and the plain part is actually the same words
  as the HTML rather than a stub. Even the MIME boundary is randomised — the stdlib's
  `===============N==` is a legible Python fingerprint.
- Authentication is **App Password only**. Gmail has not accepted an ordinary account password over
  SMTP since 2022, so the auth error spells out the fix instead of echoing the server's terse reply.

Failures come back as strings prefixed `AUTH:`, `QUOTA:`, `RECIPIENT:`, `CONN:` or `OTHER:`. The
prefix is a control signal, not a label — see
[quota errors](#-sending-limits-warm-up-and-quota-errors).

### `core/campaign.py` — the schedule and the workers
Two very different things live here on purpose.

**`next_send_times` is a pure function.** Given a count, the accounts, the settings and a start
instant it returns the exact second each message leaves, with no clock, no database and no Qt
anywhere near it. That matters because this function is the only thing standing between the user and
a suspended Gmail account, and the only way to know it is right is to be able to test it: a fixed
seed produces a fixed schedule, and every rule is asserted offline in `tests/test_schedule.py`.

The rules are all one idea — **do not look like software**. Sends stay inside working hours on
working days; no account passes its daily or hourly cap; a new account ramps up instead of opening
at full rate; and the gap between two sends is *drawn from a range* rather than being a constant. A
message every 180 seconds on the dot is the clearest automation fingerprint a mail provider can
read, and it costs nothing at all to avoid.

**`OutreachWorker` then does as little thinking as possible.** It waits in quarter-second slices so
**Stop** lands immediately, and `abort()` closes the SMTP socket from the outside for the same
reason `ScrapeWorker.abort()` quits the browser: a thread blocked in a network call cannot check a
flag. **`AuditWorker`** is the other thread — it enriches, audits and personalises a batch across a
pool without sending anything, keeping every database call on its own thread and the pool on the
network only.

---

## 📬 The outreach UI

Two new screens, both styled from the same dark stylesheet in `ui/app.py`.

### `ui/screen_outreach.py` — four tabs that are steps, not categories
**Leads** (who) → **Campaign** (what they receive) → **Sending** (the run) → **Stats** (what came
back). Each tab ends by pointing at the next.

- **Leads** — the lead table with a live filter and a status filter, an **Import CSV…** button, and
  **Audit all**, which runs `AuditWorker` and fills the *Score* and *Headline gap* columns. Nothing
  is sent. Double-click a lead to open its site; right-click to copy fields or **Suppress**.
- **Campaign** — pick the first-touch template, pick a lead to preview, check the sender profile,
  and press **Prepare campaign** to audit, personalise, render and queue. The preview goes through
  `core.templates.render` — *the same call the send loop makes* — and refuses to draw anything still
  carrying a `{{token}}`. This screen is the last place a human sees the copy before it leaves.
- **Sending** — a **DRY RUN / LIVE** badge, the live log, Start / Pause / Stop.
- **Stats** — per-status tiles, a volume-by-day bar chart, and the **suppression list** with a
  *Remove selected* button.

Nothing slow runs on the GUI thread: crawling a site, calling a model and opening an SMTP session
are all minutes of network time and all happen in the workers in `core/campaign.py`. This file
starts them, draws what they emit, and reads a local SQLite file.

### `ui/screen_settings.py` — everything needed before the app may send
The old settings were a tab of `InputScreen` sized for two controls. Cold email needs an order of
magnitude more, so it gets a full screen with five pages: **AI**, **Sender**, **Gmail**, **Sending**
(days, window, pacing, warm-up and follow-ups) and **Compliance**. (`InputScreen` keeps `headless`
and the result-limit cap where you already know to find them.) Three things here are safety features
rather than decoration:

- **Secrets are never on screen in plaintext by default.** Every credential field is a password
  field with an explicit reveal toggle, and is written through `settings.set_secret`.
- **Verify and Test run off the GUI thread.** An SMTP handshake takes seconds and an unreachable
  host takes the full timeout. Both report the provider's *real* error string, because "wrong
  password" and "app passwords are disabled for this account" need completely different fixes.
- **Turning dry-run off is a decision, not a click.** It asks first, and shows a standing red
  warning while live sending is on.

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

`requirements.txt` is the **runtime** set and is deliberately short: `PyQt5`, `selenium`,
`undetected-chromedriver`, `lxml`. Everything else — enrichment, the audit, the AI client, SMTP,
IMAP, SQLite, the scheduler, credential encryption — is **standard library only**.

`requirements-dev.txt` adds what you need to run the test suite (`pytest`) on top of the runtime
set:

```bash
pip install -r requirements-dev.txt
```

**Two optional accelerators**, both imported behind a `try: … except ImportError:` guard in
`core/enrich.py`, both with a working fallback, and neither declared as a dependency:

| Package | What it adds | Without it |
|---------|--------------|------------|
| `dnspython` | An MX lookup as the deliverability check | Falls back to `socket.getaddrinfo` (an A-record check) |
| `brotli` / `brotlicffi` | Advertises `br` in `Accept-Encoding` and decodes it | `br` is simply not advertised; sites serve gzip or deflate |

Install either only if you want it. Nothing breaks if you never do.

> **Windows and timezones:** Windows ships no IANA timezone database, so a named
> `send_timezone` (`America/Toronto`) resolves only when the `tzdata` package happens to be
> installed. An unresolvable zone silently falls back to this machine's clock rather than taking the
> scheduler down. Leave the setting on `local` unless you have a reason not to.

---

## 🔑 Setup you must do yourself

The scraper half needs nothing but Chrome. The outreach half cannot work until **you** create
credentials in three places that are not this app, and fill in who you are. None of it can be
automated — Google will not hand out a mail password to a program, and no API provider issues keys
to anything but a human in a browser.

Do these in order. Everything below lives in the full **Settings** screen — reach it from the Home
screen's **Settings** tab → **Open full settings**, or from **Fix in Settings** on the Outreach
Campaign tab.

### 1 · A Gmail App Password  *(required — nothing sends without one)*

Gmail has not accepted an ordinary account password over SMTP since 2022. You need a 16-character
**App Password**, and Google will not offer you one until 2-Step Verification is on.

1. Go to **[myaccount.google.com/security](https://myaccount.google.com/security)** — that is
   *Google Account → Security*.
2. Turn on **2-Step Verification**. If it is already on, skip to step 3. **This is not optional:**
   the App passwords page does not exist for an account without 2FA, and this is the single most
   common reason people cannot find it.
3. Still under Security, open **2-Step Verification → App passwords**, or go straight to
   **[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)**.
4. Give it a name you will recognise later — `MapHarvest` — and create it.
5. Google shows a 16-character password **once**, printed in four groups of four
   (`abcd efgh ijkl mnop`). Copy it now. If you lose it, you cannot look it up; delete that entry
   and make a new one.
6. In **Settings → Gmail**, add the account: the Gmail address, the App Password, and a display name
   (the name a recipient sees in their From column). The spaces Google prints are presentation only
   — paste it with or without them, the app strips them.
7. Press **Verify**. That opens a real SMTP session to `smtp.gmail.com` and reports Gmail's own
   words back to you. A green result means the credentials work; nothing has been sent.

If Verify fails, the message tells you which problem you have — a rejected password, an account
where an administrator has disabled App Passwords, or an account Google wants you to log into from
a browser first. Those need different fixes, which is why the raw server reply is shown rather than
a generic "login failed".

> A Google Workspace account may have App Passwords switched off by its administrator. If your
> organisation has done that, this app cannot send from that address, and there is no workaround on
> this side.

### 2 · A Groq API key  *(optional — personalisation)*

Groq is the first-choice model provider: it is the faster and cheaper of the two.

1. Sign in at **[console.groq.com](https://console.groq.com)**.
2. Open **API Keys** and create one. The key is displayed **once** — copy it immediately.
3. In **Settings → AI**, paste it into **Groq API key** and leave the model as
   `llama-3.3-70b-versatile` unless you have a reason to change it.
4. Press **Test**. That makes one five-token call and reports the round-trip latency, or the
   provider's own error — `unknown model name` and `the API key was rejected` need different fixes.

### 3 · An OpenRouter key  *(optional — the fallback)*

OpenRouter covers the case where Groq has no key, no quota, or no pulse. With the provider set to
**Auto** the app tries Groq first and falls through to OpenRouter automatically.

1. Sign in at **[openrouter.ai](https://openrouter.ai)**.
2. Open **Keys** and create one. Copy it.
3. In **Settings → AI**, paste it into **OpenRouter API key**; the default model is
   `meta-llama/llama-3.3-70b-instruct`.
4. Press **Test**. OpenRouter bills per call against credit on your account — if the account has
   none, the test comes back `HTTP 402: the provider account is out of credit`.

> **Both keys are genuinely optional.** With the provider set to **Off**, or with no key at all,
> every campaign still renders and sends — it just uses the plain templates, and the `{{ai_opener}}`
> and `{{ai_ps}}` lines disappear cleanly instead of leaving holes. Set a monthly ceiling in
> **Settings → AI → monthly token cap** (default 2,000,000); it resets on the calendar month and the
> screen shows what is left.

### 4 · The sender profile  *(required — this is who the email is from)*

**Settings → Sender.** Every line of copy is built from this, so a blank profile produces a blank
signature.

| Field | Why it matters |
|-------|----------------|
| **Company** | Signs the email and appears in the footer identity line |
| **Sender name** | The human name on the sign-off. Leave it blank and the email is unsigned |
| **Sender title** | Renders as `{{sender_title}}, {{company}}` under the name |
| **Website** | Also the fallback for the single link when no calendar is set |
| **Reply-to** | Where replies land, if not the sending account |
| **Phone** | Optional |
| **Calendar link** | The one link in the body. Falls back to the website, so the "exactly one link" copy rule still holds |
| **Services** | Seeded from the full catalogue. **Narrow it** — a profile still holding everything is not a choice, and the app treats it as one |
| **Proof points** | One per line; the copy picks one deterministically per lead |
| **Tone** | `direct` / `friendly` / `consultative`, passed to the model |

### 5 · A postal address  *(required by law, not by the app)*

**Settings → Sender → Postal address.** A real physical mailing address for your business.

CAN-SPAM — and the equivalent rules in most other jurisdictions — requires a valid physical postal
address in every commercial email. The app renders it into the footer of every message, plain-text
and HTML, next to the company name and above the unsubscribe line.

**The app will not stop you leaving it blank.** If you do, the footer simply carries the company
name and the unsubscribe line, and you are sending a non-compliant commercial email. A PO box or a
registered agent's address is acceptable in most jurisdictions; a fake one is not. This is the one
setup step where getting it wrong has a legal consequence rather than a deliverability one.

### 6 · Check the unsubscribe address  *(optional)*

**Settings → Compliance → Unsubscribe.** Leave it blank and the sending account's own address is
used, which always works. Set it if you would rather route opt-outs somewhere specific.

### 7 · Leave dry run on for the first campaign

It already is. See [Dry run is on by default](#-dry-run-is-on-by-default).

---

## 📉 Sending limits, warm-up, and quota errors

### The limits that actually apply

These are **Google's**, not the app's, and no setting in this app can raise them:

| Account type | Roughly how many messages a day |
|--------------|---------------------------------|
| Free Gmail (`@gmail.com`) | **~500** |
| Google Workspace (paid, custom domain) | **2000** |

Two things people get wrong about these numbers. First, they count **everything the account sends**
— mail you write by hand in the web UI and on your phone comes out of the same budget. Second, they
are a ceiling, not a target: hitting 500 cold emails a day from a single Gmail address is how an
account gets suspended, not how a campaign gets delivered.

Which is why **the app's own defaults sit an order of magnitude below the ceiling**:

| Setting | Default | Why |
|---------|---------|-----|
| `daily_cap_per_account` | 40 | Well under any provider limit, and a volume a real person could plausibly send |
| `hourly_cap_per_account` | 12 | Flattens bursts; a rolling window, not a clock hour |
| `send_min_gap_sec` / `send_max_gap_sec` | 60 / 240 | Each gap is drawn at random from the range. A fixed interval is the clearest automation fingerprint there is |
| `send_days` | Mon–Fri | |
| `send_start_hour` / `send_end_hour` | 09:00–17:00 | Business mail arriving at 3 a.m. is a signal |

The three daily limits compose as a **minimum and never as an override**: the global cap, the
per-account cap and the warm-up ceiling each have to allow the send.

### The warm-up ramp, and why starting slow matters

`warmup_enabled` is on, and the ramp is `warmup_start` = 10, `warmup_step` = 5, `warmup_max` = 40.
So an account's ceiling walks up day by day from the date its ramp starts:

```
day 1: 10    day 2: 15    day 3: 20    day 4: 25    day 5: 30    day 6: 35    day 7+: 40
```

The ramp counts from each account's **warm-up date** (Settings → Gmail). An account with no date set
is treated as *starting its ramp on the campaign's first day* — not as fully warmed. That guess is
deliberately pessimistic, because a brand-new Gmail account sending forty cold emails on its first
morning is the precise failure the ramp exists to prevent, and guessing safe costs a few days.

**Why this matters more than any other setting here.** Mail providers score a sending identity on
its *history*. A mailbox with no record of ever sending to strangers that suddenly emits forty
messages to forty unrelated domains looks exactly like a compromised account, and the response is
throttling, a spam-folder placement that never recovers, or a suspension. History takes days to
build and cannot be bought or faked. The ramp is the cheapest insurance in this application: it
costs you a week of lower volume and it is the difference between an account that keeps working and
one that does not.

Replanning a running campaign is anchored to the day the campaign began, not to today, so a replan
on day three does not drop an undated account back to its first-day rate.

### What the app does when it hits a quota error

`core/mailer` classifies every SMTP failure into one of five prefixes, and the prefix is a control
signal for the send loop rather than a label:

| Prefix | What it means | What the run does |
|--------|---------------|-------------------|
| `QUOTA:` | Daily limit, rate limit, or a Gmail reputation refusal (`5.4.5`, `4.2.2`, `5.7.1`, or wording like *"daily user sending limit exceeded"*) | **Retires that account for the rest of the calendar day** and puts the message back on the queue for whichever account is still standing |
| `AUTH:` | Credentials rejected, App Password required, web login required | Same as QUOTA — the account is benched for the day. Never retried, because repeating a failed login is exactly the pattern that gets an account locked |
| `RECIPIENT:` | The address does not exist | Skips that one lead, marks the message failed and the lead bounced, moves on |
| `CONN:` | Socket dropped, connect failed, temporary 4xx | Requeues in two minutes. Five consecutive connection failures and the run gives up |
| `OTHER:` | Anything unrecognised | Marks the message failed and continues |

QUOTA deliberately covers reputation blocks as well as the literal daily limit, because the correct
response to both is the same and pushing through either is how an account gets suspended. The bench
is **for the calendar day, not for the run** — a fortnight-long campaign would otherwise finish with
every account permanently benched over one bad afternoon.

If a quota error benches the last standing account, the run **stops** and says so, rather than
retrying. Fix the account in Settings and start again.

When today's caps are simply spent — or the clock has left the sending window — the whole overdue
backlog goes **back through the scheduler** into the next window that has room, rather than being
released as it comes free. A hundred messages leaving in one burst at nine the next morning is a
worse signal than the stale plan ever was.

---

## 🛑 Dry run is on by default

`dry_run` is `True` in `DEFAULT_SETTINGS`, so **a fresh install cannot surprise-send.** The flag is
also or-ed rather than replaced when the worker is constructed: a caller that forgets to pass it
cannot turn a rehearsal into a live send.

**What a dry run actually does.** Everything except the SMTP connection. It plans the real schedule,
renders every message including the footer and the unsubscribe line, walks the queue at the real
pace, and marks each message `sent` with `error = "DRY-RUN"`. Rehearsed sends count towards the caps
so the pacing is realistic, but are never written to the `sends` table — pacing a rehearsal is worth
having; spending a live account's real daily quota on messages nobody received is not. No socket is
opened and nothing leaves the machine.

You can see which mode you are in without hunting for it: the Outreach header carries a **DRY RUN**
or **LIVE** badge, the Sending tab repeats it with a full sentence underneath, and the log's first
line during a rehearsal says so explicitly.

**Turning it off, deliberately:**

1. **Settings → Compliance → Dry run.**
2. Untick *"Dry run — build and log every email, send none"*.
3. Confirm the prompt. It is the one toggle in this app that converts a rehearsal into mail landing
   in strangers' inboxes, so it asks first.
4. A standing red warning stays on the page while live sending is on, and the Outreach badge flips
   to **LIVE**.

Run a dry run once before every new campaign. It costs a few seconds and it is the only way to read
the actual copy, the actual schedule and the actual recipient list before any of it is irreversible.

---

## ⚖️ Compliance: unsubscribe, suppression, one first touch

Cold B2B email is lawful in most jurisdictions **only** with these present, so they are built in
rather than bolted on.

### Every message carries an unsubscribe

Two of them, in fact:

- **`List-Unsubscribe: <mailto:…?subject=unsubscribe>`** plus `List-Unsubscribe-Post:
  List-Unsubscribe=One-Click` in the headers. Gmail and Outlook both weight this and surface a
  one-click unsubscribe button for it.
- **A visible footer line** in the body — *"Not the right person? Reply "unsubscribe" or write to
  … and I will stop."* — rendered in both the plain-text and the HTML part, alongside the company
  name and the postal address.

The address used is `unsubscribe_mailto` if set, then the profile's reply-to, then the sending
account's own address. There is always a working route.

> RFC 8058 one-click is formally defined over HTTPS, and this pairing is a `mailto:` one. That is a
> deliberate stretch of the spec: both major providers accept it and show the button, and hosting an
> HTTPS endpoint would mean running a server this desktop app does not have.

### Suppression is permanent and retroactive

Recording an unsubscribe is a **manual step** — see the limitation below. When you do it (right-click
a lead → **Suppress**, or the Stats tab's suppression list), one call does all of this:

1. Adds the address to the `suppression` table with a reason and a timestamp.
2. **Cancels every queued and in-flight message for that lead, follow-ups included.** This is the
   whole point: by the time somebody opts out, their follow-ups are already scheduled days out, and
   leaving them queued would mail a person who has explicitly said no.
3. Marks the lead `suppressed` so it is excluded from every future plan.
4. Logs an event.

And it is checked **twice**, at planning time and again at send time, so an unsubscribe that arrives
after a plan was built still takes effect on messages that plan already created.

Removing somebody from the suppression list is possible (Stats → *Remove selected*) and should be
done only if that person asked to be contacted again.

### One first touch per address, ever

`plan_campaign` refuses to queue a second step-0 message to any lead that already has one queued,
sending, sent, replied or bounced. Crucially it reads this off the **`messages`** table rather than
off `leads.status`: a lead whose status was reset by a re-import is still a person who has had one
cold email, and a second one is exactly the failure this guards against. Re-importing the same CSV,
re-scraping the same city, or resetting a status cannot produce a duplicate first touch.

### And a few things that are absent on purpose

- **No tracking pixel, no open tracking, no click wrapping, no images.**
- **No fake `Re:` or `Fwd:`.** The renderer strips such a prefix from any subject and the AI prompt
  forbids it. Misrepresenting a subject line is both a CAN-SPAM violation and the fastest way to
  train a reader to distrust you.
- **No purchased or guessed addresses.** Every address is one the business published on its own
  website; anything the scorer could not justify is dropped rather than guessed at.

None of this is legal advice. Rules differ by jurisdiction — CASL in Canada and GDPR in the EU are
materially stricter than CAN-SPAM, and consent requirements vary. Check what applies to you and to
the people you are mailing.

---

## 🖱️ Usage walkthrough

1. **Domain** — type a business type (`roofing company`). Use **List** to add more categories.
2. **Area** — type a city (`Toronto`). Use **List** to add more cities; every domain runs against every city.
3. **Max Results** — set how many businesses per search.
4. **Data to Scrape** — leave the defaults for a fast run, or tick Hours/Reviews/Email/socials for a richer (slower) run.
5. **Filters** (optional) — e.g. *min rating 4.5*, *No website* (great for selling web design), *Must have email*.
6. **Export Folder** — pick where CSVs land.
7. Click **Start Scraping** and watch the table fill. **Pause/Stop** any time. Sort, search, and right-click rows. CSVs save automatically per search.

### …and then, if you are sending

Do [Setup you must do yourself](#-setup-you-must-do-yourself) once, first. Then:

8. On the results, click **Start Outreach** (or open Outreach and **Import CSV…**). Rows without an
   email are skipped and counted.
9. **Leads tab → Audit all.** Each site is crawled, scored and personalised. Nothing is sent. Watch
   the *Score* and *Headline gap* columns fill.
10. **Campaign tab.** Pick a template, pick a lead in **Previewing**, and read the actual email —
    subject, body, footer, unsubscribe line. Switch between **Text** and **HTML**. Fix anything the
    sender-profile card flags.
11. **Prepare campaign.** This audits anything still missing, renders, and queues every message with
    a timestamp and an account. The Schedule card then tells you how many messages, over how many
    days, starting when, plus how many were skipped and why.
12. **Sending tab.** Confirm the badge says **DRY RUN**, press **Start**, and let it run. Read the
    log. This is your rehearsal.
13. Happy? **Settings → Compliance**, turn dry run off, confirm, come back, and start again for
    real. **Pause** and **Stop** work at any point and land within a quarter of a second.
14. **Stats tab** for the counters, the volume-by-day chart and the suppression list. Suppress
    anybody who asks to be left alone — see
    [Compliance](#-compliance-unsubscribe-suppression-one-first-touch).

---

## 🗂️ Configuration files

Everything lives in `~/.mapharvest/`:

| File | What it holds |
|------|---------------|
| `settings.json` | Every setting, including credentials as ciphertext |
| `outreach.db` | SQLite: leads, campaigns, messages, suppression, the send log, events |
| `ai_cache.json` | Model replies keyed by domain + template + model, capped at 5000 entries |

`settings.json`, abridged — the schema in `core/settings.py` is the authority, and the Settings
screen writes all of it, so you should never need to edit this by hand:

```json
{
  "headless": false,
  "max_limit_cap": 100,
  "default_max_results": 50,
  "export_dir": "C:/Users/you/Desktop/leads",
  "saved_searches": [
    { "domains": ["roofing company"], "area": "Toronto", "max_results": 50 }
  ],

  "ai_provider": "auto",
  "groq_api_key": "enc:v1:…",
  "groq_model": "llama-3.3-70b-versatile",
  "openrouter_api_key": "enc:v1:…",
  "openrouter_model": "meta-llama/llama-3.3-70b-instruct",
  "ai_monthly_token_cap": 2000000,

  "sender_profile": {
    "company": "Auto Army",
    "sender_name": "",
    "sender_title": "",
    "website": "",
    "reply_to": "",
    "postal_address": "",
    "calendar_link": "",
    "services": ["…"],
    "proof_points": [],
    "tone": "direct"
  },

  "smtp_accounts": [
    { "email": "you@gmail.com", "app_password": "enc:v1:…", "display_name": "",
      "daily_cap": 40, "enabled": true, "warmup_started": "2026-08-17" }
  ],

  "send_days": [0, 1, 2, 3, 4],
  "send_start_hour": 9,
  "send_end_hour": 17,
  "send_timezone": "local",
  "send_min_gap_sec": 60,
  "send_max_gap_sec": 240,
  "daily_cap_per_account": 40,
  "hourly_cap_per_account": 12,
  "warmup_enabled": true,
  "warmup_start": 10,
  "warmup_step": 5,
  "warmup_max": 40,

  "followup_enabled": true,
  "followup_gap_days": 4,
  "followup_max_steps": 2,

  "unsubscribe_mailto": "",
  "dry_run": true
}
```

Every `enc:v1:…` value is sealed by `core/secrets.py`. On Windows that is DPAPI scoped to your user
account, so copying this file to another machine yields empty credentials rather than live ones —
which is the intent. Elsewhere it is XOR obfuscation and should be treated as *unreadable at a
glance*, not as encrypted.

Loading is defensive throughout: unknown keys are dropped, missing sub-keys are filled from the
defaults, and a corrupt file falls back to defaults instead of crashing. Saves are written to a temp
file and renamed.

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

Because the risky logic is **pure**, it is tested without a browser and without a network — and the
one file that does need Qt runs it offscreen. Every test is offline:

| File | What it pins down |
|------|-------------------|
| `tests/test_parse.py` | The card/URL parser against a **saved real Google Maps feed** (`debug/initial_*.html`) — every business's name, rating, phone, website, coordinates and Place ID, and that sponsored cards are flagged |
| `tests/test_enrich_filters.py` | The legacy `extract_contacts` shape and every filter predicate |
| `tests/test_enrich_email.py` | Cloudflare `data-cfemail` decoding, `(at)`/`[dot]` deobfuscation, JSON-LD and microdata addresses, `mailto:` priority, junk rejection, scoring order, same-domain preference |
| `tests/test_audit.py` | `audit_from_html` against handwritten fixtures, that every gap names a service that exists **verbatim** in the catalogue, and that `digest()` stays inside its character budget |
| `tests/test_templates.py` | That no `{{` survives `render` for a context with missing keys, subject length, and that the HTML has exactly one link plus the unsubscribe |
| `tests/test_schedule.py` | `next_send_times` against the window, the send days, all three caps and the warm-up ramp; determinism for a fixed seed; gaps inside the configured range; 500 messages against a 40/day cap spilling across days |
| `tests/test_outreach_db.py` | Upsert dedupe, suppression blocking a queue, `due_messages` ordering, `sent_today` at the local-midnight boundary |
| `tests/test_mailer.py` | Message construction (headers, parts, encoding) and the five-way error classification |
| `tests/test_settings.py` | Deep merge across schema versions, the secrets round-trip, and a corrupt settings file falling back to defaults |
| `tests/test_outreach_screen.py` | The Outreach screen's appearance and shutdown contracts, asserted against sampled pixels under `QT_QPA_PLATFORM=offscreen` |

Run the whole suite:
```bash
pip install -r requirements-dev.txt
venv/Scripts/python.exe -m pytest tests/ -q
```

The suite is well past 150 tests and grows with each change; the command above is the authority on
the current number. Most files are also runnable on their own without pytest, which is useful when
bisecting one area:
```bash
venv/Scripts/python.exe -m tests.test_schedule
```

`tests/test_schedule.py` deserves a note. `next_send_times` is the only thing standing between the
user and a suspended Gmail account, and it was written as a pure function *specifically* so it could
be asserted this way — a fixed seed produces a fixed schedule, so "does the warm-up ramp actually
hold on day three" is a test rather than a fortnight of watching.

Beyond unit tests, changes are verified **live** (small headless scrapes against real Google, and
dry-run campaigns end to end) and the **PyQt UI is exercised off-screen**
(`QT_QPA_PLATFORM=offscreen`) so wiring bugs surface without a display.

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

### Outreach

| Symptom | Likely cause & fix |
|---------|--------------------|
| "I can't find App passwords in my Google account" | 2-Step Verification is off. The page does not exist until it is on — see [step 1](#-setup-you-must-do-yourself). |
| Verify says the password was rejected | You pasted your Google account password. Gmail refuses those over SMTP; it must be the 16-character App Password. |
| Verify says App Passwords are disabled | A Workspace administrator has switched them off for the domain. Nothing on this side can work around it. |
| Nothing sends, and the log says DRY RUN | Working as intended. **Settings → Compliance** to turn it off deliberately. |
| Campaign prepared but everything is "skipped" | Those leads already had a first touch, are suppressed, or produced no usable copy. The Schedule card breaks the number down. |
| The plan spans far more days than expected | The caps and the warm-up ramp compose as a minimum. A fresh account starts at 10/day and needs a week to reach 40. |
| "Every account is at its cap for today" | Expected. The backlog is re-spaced into the next window with room, not dropped. |
| An account stopped mid-run | An `AUTH:` or `QUOTA:` failure benched it for the calendar day. The log carries Gmail's own words. Fix it in Settings; it is available again tomorrow. |
| The emails read generically | No AI provider is configured or reachable, so the plain template is being used. **Settings → AI**, then **Test**. |
| Replied / Bounced tiles are always zero | Known limitation — nothing reads the mailbox yet. See [Limitations](#-limitations-reliability--legal). |
| A named timezone seems to be ignored | Windows ships no IANA database; install `tzdata` or leave `send_timezone` on `local`. |
| Credentials empty after copying `settings.json` to another PC | Intended. DPAPI ciphertext is scoped to the Windows account that wrote it. Re-enter them. |

---

## ⚠️ Limitations, reliability & legal

- **Google's HTML changes.** No Maps scraper can be *permanently* guaranteed. MapHarvest defends against this with semantic selectors, offline tests, and automatic `debug/` snapshots so a break is quick to diagnose and re-point — but expect occasional selector maintenance in `core/parse.py`.
- **The "No website" filter is card-level.** It reflects what Maps shows on the card — accurate for service businesses, less precise for POIs (where a website may only appear on the listing page). On the roadmap to make detail-accurate.
- **Rate limits.** Heavy/rapid scraping can trigger CAPTCHAs. Use reasonable limits.
- **~120-result cap per search** is Google's, not the app's.
- **Terms of Service.** Scraping Google Maps may violate [Google's ToS](https://policies.google.com/terms). Use responsibly, for personal or permitted purposes. The author is not responsible for misuse.

### Outreach — what this half genuinely cannot do

- **Email extraction is best-effort, and a blank is often the true answer.** A business that only
  publishes a contact form, or that assembles its address in JavaScript the crawler does not run,
  yields nothing. The scorer drops any candidate it cannot justify rather than guessing — no address
  is an honest result, a wrong address is not. Expect a real share of any scraped list to be
  uncontactable, and treat that as the list telling you the truth.
- **The audit is heuristic and will sometimes mislabel a site.** It fires on concrete markers — a
  script host, a link, a schema type, a printed date — which keeps it conservative, but a booking
  widget behind a login, a chat script loaded lazily, or a CRM the crawler never sees all read as
  *absent*. That produces a confidently wrong "no online booking" in a live email, which tells the
  reader you did not look. **Read the preview before you send.** The Campaign tab shows exactly what
  the prospect receives, and it is there for this reason.
- **AI personalisation falls back to the templates, quietly.** No key, a spent monthly budget, both
  providers down, a truncated JSON reply, or an answer containing a placeholder — every one of those
  paths returns "not ok" and the campaign sends the pure template instead. That is by design: a bad
  model reply must never reach a prospect. But it does mean a campaign can be markedly less
  personalised than you expected without anything looking broken. The Audit log names the reason,
  and the Campaign preview shows the result.
- **Replies and bounces are not detected.** `core/mailer` has `check_replies` and `check_bounces`
  and each account carries an `imap_enabled` flag, but **nothing in the app calls them yet**. In
  practice: the *Replied* and *Bounced* tiles on the Stats tab stay at zero, and a follow-up will go
  out to somebody who has already replied unless you stop it. Watch the mailbox yourself, and
  suppress by hand. (A hard SMTP rejection at send time *is* caught and marks that lead bounced —
  but it does not cancel that lead's already-queued follow-ups; suppressing does.)
- **Unsubscribes are recorded by hand.** The email offers a working `List-Unsubscribe` header and a
  reply-to-unsubscribe footer line, and suppressing an address does everything correctly and
  retroactively — but somebody has to read the reply and press **Suppress**. Nothing automates that
  step. If you are not going to watch the mailbox daily, do not run a live campaign.
- **Deliverability depends on the sending domain's reputation, and no amount of app-side care can
  fully control it.** Plain text first, a real `Message-ID` on your own domain, `List-Unsubscribe`,
  no images, no tracking, randomised gaps, working-hours only, a warm-up ramp — all of it reduces
  the chance of being filtered. **None of it decides whether you land in the inbox.** A young
  domain, a Gmail address with no sending history, a handful of spam complaints, or a bad list will
  outweigh every precaution in this codebase. Cold email works on the strength of the list and the
  copy; this app can only make sure the mechanics are not what sinks you.
- **The XOR fallback in `core/secrets.py` is obfuscation, not security.** On Windows you get real
  per-user DPAPI. Everywhere else — and on any DPAPI failure — anyone with the file and this source
  can recover the credential. It stops a password being grep-able or shoulder-surfable, and nothing
  more; the module says so itself.
- **Named timezones need `tzdata` on Windows.** An unresolvable zone falls back to the machine
  clock, silently and on purpose, because taking the scheduler down over a timezone name would be
  worse.
- **Nothing here is legal advice.** CAN-SPAM, CASL and GDPR impose materially different obligations,
  and consent requirements vary by jurisdiction and by whether the recipient is a business or an
  individual. The compliance machinery in this app is the floor, not a guarantee that what you send
  is lawful where you are sending it.

---

## 📁 Project structure

```
Map Harvest/
├── main.py                     # Entry point → ui.app.run()
├── requirements.txt            # RUNTIME: PyQt5, selenium, undetected-chromedriver, lxml
├── requirements-dev.txt        # The above + pytest, for the test suite
├── main.spec                   # PyInstaller spec (optional packaging)
│
├── core/
│   │  ── scrape half ──
│   ├── parse.py                # PURE: parse feed HTML + place URLs  → data
│   ├── filters.py              # PURE: keep/skip a business (cheap vs full)
│   ├── scraper.py              # Selenium engine + ScrapeWorker (QThread)
│   ├── exporter.py             # CSV writing + field labels
│   ├── distutils_compat.py     # Python 3.12+ shim (undetected-chromedriver)
│   │
│   │  ── shared ──
│   ├── settings.py             # ~/.mapharvest/settings.json: schema, deep merge, secrets
│   ├── secrets.py              # Credential encryption at rest (DPAPI / XOR fallback)
│   ├── enrich.py               # Website → the address a business actually answers
│   │
│   │  ── outreach half ──
│   ├── audit.py                # PURE: HTML → gaps, signals, score, digest (zero tokens)
│   ├── templates.py            # PURE: service catalogue, templates, rendering
│   ├── ai.py                   # Groq / OpenRouter → subject, opener, ps (cached, budgeted)
│   ├── outreach_db.py          # SQLite: leads, campaigns, messages, suppression, sends
│   ├── mailer.py               # Message building, Gmail SMTP + IMAP, error classification
│   └── campaign.py             # PURE scheduler + OutreachWorker / AuditWorker (QThreads)
│
├── ui/
│   ├── app.py                  # MainWindow, dark theme (QSS), run()
│   ├── screen_input.py         # Scrape / Filters / Settings tabs
│   ├── screen_results.py       # Live searchable/sortable table + row actions
│   ├── screen_outreach.py      # Leads / Campaign / Sending / Stats
│   ├── screen_settings.py      # AI / Sender / Gmail / Sending / Compliance
│   └── domain_list_dialog.py   # Generic multi-line list dialog (domains, areas)
│
├── tests/                      # Offline — no network, no browser, Qt only offscreen
│   ├── test_parse.py           ├── test_audit.py       ├── test_outreach_db.py
│   ├── test_enrich_filters.py  ├── test_templates.py   ├── test_mailer.py
│   ├── test_enrich_email.py    ├── test_schedule.py    ├── test_settings.py
│   └── test_outreach_screen.py
│
├── docs/
│   ├── TECHNICAL_REFERENCE.md  # Flow-by-flow runtime walkthrough (scrape half)
│   └── OUTREACH_SPEC.md        # Binding interface contract for the outreach modules
│
└── debug/                      # Auto-saved page snapshots on failure (gitignored)
```

Not in the repo, created on first run:

```
~/.mapharvest/
├── settings.json               # All settings; credentials as enc:v1:… ciphertext
├── outreach.db                 # SQLite campaign store (WAL)
└── ai_cache.json               # Model replies, keyed by domain + template + model
```

---

## 🛣️ Roadmap

- Concurrent detail/enrichment for POI- or email-heavy runs.
- Parse `window.APP_INITIALIZATION_STATE` to recover phone/website without any detail visit.
- Auto-split big cities into sub-areas to beat Google's ~120 cap.
- SQLite persistence + resume/append across sessions; Excel/JSON export.
- Make the "No website" filter detail-accurate for POIs.
- **Wire up `check_replies` / `check_bounces`.** Both exist in `core/mailer.py` and the per-account
  `imap_enabled` flag is already in settings; nothing calls them. This is the single largest gap in
  the outreach half — it would fill the Replied and Bounced tiles, auto-suppress hard bounces, and
  cancel follow-ups to people who already answered.

---

## 📖 Glossary

**Scrape half**

- **Feed / card** — the scrolling results list on the left of Maps, and one business entry in it.
- **Detail page** — an individual business's Maps page (opened only when needed).
- **Enrichment** — fetching a business's own website (over HTTP) to find email + socials.
- **Place ID** — Google's stable identifier for a place (same one the official API uses).
- **Cheap vs full filter** — filters decidable from the card vs filters needing extra fetched data.
- **Card-first** — read everything possible from the feed before opening anything.

**Outreach half**

- **Lead** — a business with a usable email address, stored in `outreach.db` and deduplicated on
  that address.
- **Audit** — the offline, zero-token analysis of a business's website that produces its gaps,
  signals and score.
- **Gap** — one automatable weakness found by the audit (*no online booking*, *no CRM behind the
  form*), carrying the services from the catalogue that close it. `gaps[0]` is what the email leads
  with.
- **Digest** — the five-line, sub-1200-character brief that is the *only* thing the model ever sees.
- **Merge field** — a `{{token}}` in a template. An unresolved one is deleted along with its
  punctuation rather than being printed.
- **First touch** — a step-0 message. Exactly one is ever queued per address.
- **Warm-up ramp** — the per-account daily ceiling that walks up over the first days of sending.
- **Send window** — the days and hours a campaign is allowed to send in.
- **Suppression** — the permanent do-not-contact list. Adding to it cancels queued follow-ups.
- **Dry run** — a full rehearsal that renders, schedules and logs everything but opens no SMTP
  connection. On by default.

---

<div align="center">

Made for local lead generation and business research — without a paid Maps API,
and without a paid sending platform.

Found it useful? A ⭐ helps.

</div>
