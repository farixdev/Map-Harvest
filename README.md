<div align="center">

# 🗺️ MapHarvest

> *Every business. One click.*

**A local desktop app for scraping Google Maps business listings and exporting them to CSV — no APIs, no cloud, everything runs on your machine.**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-41CD52?style=flat-square&logo=qt&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white)
![Chrome](https://img.shields.io/badge/Chrome-required-4285F4?style=flat-square&logo=googlechrome&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)

</div>

---

## 📖 Overview

MapHarvest automates Google Maps business data extraction from your desktop. Search any business category in any city, watch results populate live, and export clean CSV files — all without touching a paid API. Built with PyQt5 and Selenium, it runs a real Chrome browser on your machine with bot detection bypassed via `undetected-chromedriver`.

---

## ✨ Features

### 🔍 Scraping
- Search by **business type** and **location**
- **Card-first extraction** — most fields (name, rating, category, address, phone, website, coordinates, Place ID) are read straight from the results feed in a single pass, with **no page load per business**. This is dramatically faster and more reliable than opening every listing.
- **Automatic detail fallback** — when the feed card omits a requested field (some categories hide phone/website), and only then, that one listing is opened to fill it. You never pay for navigation you don't need.
- **Multi-domain mode** — scrape several categories in one session
- **Configurable fields** — choose exactly which data points to collect
- **Max results slider** — cap how many businesses are collected per run
- **Pause / Resume** — freeze mid-scrape without losing progress
- **Sponsored / ad listings skipped** automatically (detected, not guessed)
- **Dedup by Place ID** — the same business is never collected twice
- Progressive scrolling — loads more results until the limit or end of list

### 📊 Data & Export
- Live results table as scraping runs
- **Export CSV** with UTF-8 BOM (Excel-friendly)
- Custom save path and filename per export

### ⚙️ Settings & Convenience
- **Headless mode** — hide Chrome while scraping
- **Adjustable result cap** — raise the slider maximum up to 1000 in Settings
- **Recent searches** — last 12 domain + area + limit combos saved and reloadable in one click
- Settings persist in `~/.mapharvest/settings.json`

---

## 📋 Scrapable Fields

| Field | CSV Column | Description |
|-------|-----------|-------------|
| Business Name | Business Name | Place title |
| Category | Category | e.g. Car dealer, Restaurant |
| Rating | Rating | Star rating |
| Review Count | Review Count | Total number of reviews |
| Hours | Hours | Weekly opening hours |
| Address | Address | Street address |
| Website | Website | Business website URL |
| Phone Number | Phone | Contact number |
| Maps Link | Maps Link | Direct Google Maps URL |
| Latitude | Latitude | Location latitude (from the place URL) |
| Longitude | Longitude | Location longitude (from the place URL) |
| Place ID | Place ID | Stable Google Place ID — ideal for de-duping / joining with the Places API |
| Review 1–3 | Review 1–3 | Sample review snippets (author, rating, date, text) — *opens each listing, slower* |
| Search Domain | Search Domain | Auto-added when using multiple domains |

> **Fast vs. detailed fields.** Everything except **Hours** and **Reviews** comes from the results feed with no per-listing navigation. Hours and Reviews (and phone/website for the minority of categories that hide them in the card) require opening each listing, so leave them unticked for the fastest runs. Hours and Reviews are **off by default**.

---

## 🚀 Installation

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python | `3.10` or newer |
| Google Chrome | Must be installed — used by undetected-chromedriver |
| OS | Windows, macOS, or Linux |

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/map-harvest.git
cd map-harvest

# 2. Create and activate a virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python main.py
```

---

## 🖱️ Usage

### Scrape Tab — Set Up a Run

| Field | Example | Description |
|-------|---------|-------------|
| **Domain** | `car dealers` | Business type or search term |
| **Area** | `Lahore` | City or region |
| **Max Results** | `50` | Stops after this many businesses |
| **List** | — | Add extra domains for a multi-category session |
| **Data to Scrape** | checkboxes | Pick which columns appear in the table and CSV |

Click **Start Scraping** — the window expands to the results screen.

### Results Screen — Monitor and Export

- Watch rows appear in the live table in real time
- **Pause / Resume** — freeze or continue without losing progress
- **Stop** — end the run early, results collected so far are kept
- **Export CSV** — save to a file of your choice
- **Scrape Another** — return to the home screen when done

### Settings Tab

| Option | Description |
|--------|-------------|
| **Run headless** | Scrape without showing the Chrome window |
| **Slider maximum** | Upper bound for the Max Results slider (default 100, max 1000) |

### Recent Searches

Click any saved entry under **Recent Searches** to instantly refill domain, area, and max results from a previous run.

---

## 📁 Project Structure

```
Map Harvest/
├── main.py                       # Application entry point
├── requirements.txt              # Python dependencies
├── main.spec                     # PyInstaller spec (optional packaging)
│
├── ui/
│   ├── app.py                    # Main window, global styles, screen routing
│   ├── screen_input.py           # Home screen: Scrape + Settings tabs
│   ├── screen_results.py         # Results screen: table, progress, pause/stop/export
│   └── domain_list_dialog.py     # Dialog for multi-domain input
│
├── core/
│   ├── parse.py                  # Pure feed/URL parsing (no browser) — the extraction core
│   ├── scraper.py                # Selenium orchestration, browser driver, QThread worker
│   ├── exporter.py               # CSV export logic and field labels
│   ├── settings.py               # Load/save user settings and recent searches
│   └── distutils_compat.py       # Python 3.12+ compatibility shim
│
├── tests/
│   └── test_parse.py             # Offline parser tests against a captured feed
│
└── README.md
```

---

## ⚙️ How It Works

1. **Search** — Opens `https://www.google.com/maps/search/{domain}+in+{area}?hl=en` in Chrome and clicks through any cookie/consent wall
2. **Scroll & parse** — Scrolls the results feed and parses **every card in one pass** (`core/parse.py`): name, rating, category, address, phone, website — plus coordinates and Place ID decoded from each place URL. Sponsored/ad cards are detected and skipped; businesses are de-duplicated by Place ID
3. **Stream** — Complete rows are emitted to the live table as they're found
4. **Detail fallback** — *Only* for rows still missing a requested field (Hours, Reviews, or phone/website when a card hides them), the individual listing is opened and the gap is filled
5. **Export** — Automatic CSV export per domain to your chosen folder

Because the common lead-gen fields come straight from the feed, a typical run does **zero** per-listing page loads — the slow path is used only when Google genuinely withholds the data from the card.

Scraping runs on a background `QThread` so the UI stays responsive. Pause uses a thread event; resume continues from the same browser session. The parser is a pure, browser-free module (`core/parse.py`) with offline tests (`tests/test_parse.py`), so extraction can be verified without launching Chrome.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| UI | PyQt5 |
| Browser Automation | Selenium |
| Bot Detection Bypass | undetected-chromedriver |
| Feed / URL parsing | lxml (fast, browser-free) |
| Threading | QThread + pyqtSignal |
| Export | Python `csv` (built-in) |
| Config | JSON (`~/.mapharvest/settings.json`) |

---

## 🗂️ Configuration File

Settings are stored at `~/.mapharvest/settings.json`:

```json
{
  "headless": false,
  "max_limit_cap": 100,
  "default_max_results": 50,
  "saved_searches": [
    {
      "domains": ["car dealers"],
      "area": "Lahore",
      "max_results": 50
    }
  ]
}
```

---

## 📦 Build Standalone Executable *(Optional)*

A PyInstaller spec file is included for packaging into a standalone `.exe`:

```bash
pip install pyinstaller
pyinstaller main.spec
```

Adjust `main.spec` for your platform and icon as needed.

---

## 🔧 Troubleshooting

| Issue | Fix |
|-------|-----|
| Chrome won't start | Install or update Google Chrome; delete cached chromedriver if version mismatch |
| Empty category or hours | Maps UI may have changed; try a visible (non-headless) run to inspect the page |
| No results found | Check domain/area spelling; try plural terms (e.g. `car dealers`) |
| App feels slow | Lower max results; uncheck Review fields; enable headless mode |

---

## ⚠️ Limitations & Notes

- **Google Maps DOM changes** — Selectors may break if Google updates their UI; inspect in Chrome and update `core/scraper.py` accordingly
- **Rate limiting** — Heavy or rapid scraping can trigger CAPTCHAs or temporary blocks; use reasonable limits
- **Reviews** — Enabling review fields slows scraping as each listing must open the Reviews tab
- **Legal / ToS** — Scraping Google Maps may violate [Google's Terms of Service](https://policies.google.com/terms); use responsibly for personal or permitted use only. The author is not responsible for misuse.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch — `git checkout -b feature/my-feature`
3. Commit your changes — `git commit -m 'Add my feature'`
4. Push to the branch — `git push origin feature/my-feature`
5. Open a Pull Request

---

## 👤 Author

Made with 🖤 by **[Farisxdev](https://github.com/farixdev)**

> Built for local lead generation and business research workflows — without relying on paid Maps APIs.

---

<div align="center">

Found this useful? Drop a ⭐ on GitHub — it helps a lot!

</div>
