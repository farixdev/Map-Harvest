<div align="center">


=======
# 🗺️ MapHarvest

> *Every business. One click.*

**A local desktop app for scraping Google Maps business listings and exporting them to CSV — no APIs, no cloud, everything runs on your machine.**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-41CD52?style=flat-square&logo=qt&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white)
![Chrome](https://img.shields.io/badge/Chrome-required-4285F4?style=flat-square&logo=googlechrome&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a

</div>

<<<<<<< HEAD
| Requirement | Notes |
|-------------|--------|
| **Python** | 3.10 or newer |
| **Google Chrome** | Must be installed — used by undetected-chromedriver |
| **OS** | Windows, macOS, or Linux |

---

## Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/YOUR_USERNAME/map-harvest.git
   cd map-harvest
   ```

2. **Create a virtual environment** (recommended)

   ```bash
   python -m venv venv

   # Windows
   venv\Scripts\activate

   # macOS / Linux
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Run the app**

   ```bash
   python main.py
   ```

---

## Usage

### 1. Scrape tab — set up a run

| Field | Example | Description |
|-------|---------|-------------|
| **Domain** | `car dealers` | Business type or search term sent to Google Maps |
| **Area** | `Lahore` | City or region |
| **Max Results** | `50` | Slider — stops after this many businesses (total across all domains) |
| **List** | — | Add extra domains (one per line) to scrape in the same session |
| **Data to Scrape** | checkboxes | Pick which columns appear in the table and CSV |

Click **Start Scraping**. The window expands to the results screen.

### 2. Results screen — monitor and export

- Watch rows appear in the live table
- **Pause** / **Resume** — freeze or continue scraping
- **Stop** — end the run early (results collected so far are kept)
- **Export CSV** — save to a file of your choice
- **Scrape Another** — go back to the home screen when the run is done or stopped

### 3. Settings tab

| Option | Description |
|--------|-------------|
| **Run headless** | Scrape without showing the Chrome window |
| **Slider maximum** | Upper bound for the Max Results slider (default 100, max 1000) |

### Recent searches

Under **Recent Searches** on the Scrape tab, click any saved entry to refill domain, area, and max results.

---

## Scrapable fields

| Field | CSV column | Description |
|-------|------------|-------------|
| Business Name | Business Name | Place title |
| Category | Category | e.g. Car dealer, Restaurant |
| Rating | Rating | Star rating |
| Review Count | Review Count | Total reviews |
| Hours | Hours | Opening hours (expanded weekly schedule when available) |
| Address | Address | Street address |
| Website | Website | Business website URL |
| Phone Number | Phone | Phone number |
| Maps Link | Maps Link | Direct Google Maps URL |
| Review 1–3 | Review 1–3 | Sample review snippets (author, rating, date, text) |
| Search Domain | Search Domain | Added automatically when using multiple domains |

---
=======
---

## 📖 Overview

MapHarvest automates Google Maps business data extraction from your desktop. Search any business category in any city, watch results populate live, and export clean CSV files — all without touching a paid API. Built with PyQt5 and Selenium, it runs a real Chrome browser on your machine with bot detection bypassed via `undetected-chromedriver`.

---

## ✨ Features

### 🔍 Scraping
- Search by **business type** and **location**
- **Multi-domain mode** — scrape several categories in one session
- **Configurable fields** — choose exactly which data points to collect
- **Max results slider** — cap how many businesses are collected per run
- **Pause / Resume** — freeze mid-scrape without losing progress
- **Sponsored listings skipped** automatically
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
| Review 1–3 | Review 1–3 | Sample review snippets (author, rating, date, text) |
| Search Domain | Search Domain | Auto-added when using multiple domains |

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
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a

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
<<<<<<< HEAD
├── main.py                      # Application entry point
├── requirements.txt             # Python dependencies
├── main.spec                    # PyInstaller spec (optional packaging)
│
├── ui/
│   ├── app.py                   # Main window, global styles (QSS), screen routing
│   ├── screen_input.py          # Home screen: Scrape + Settings tabs
│   ├── screen_results.py        # Results screen: table, progress, pause/stop/export
│   └── domain_list_dialog.py    # Dialog for multi-domain input
│
├── core/
│   ├── scraper.py               # Selenium scraper, browser driver, QThread worker
│   ├── exporter.py              # CSV export logic and field labels
│   ├── settings.py              # Load/save user settings and recent searches
│   └── distutils_compat.py      # Python 3.12+ compatibility shim for undetected-chromedriver
=======
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
│   ├── scraper.py                # Selenium scraper, browser driver, QThread worker
│   ├── exporter.py               # CSV export logic and field labels
│   ├── settings.py               # Load/save user settings and recent searches
│   └── distutils_compat.py       # Python 3.12+ compatibility shim
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a
│
└── README.md
```

<<<<<<< HEAD
### Module overview

| Module | Role |
|--------|------|
| `ui/app.py` | `QMainWindow` with a stacked widget switching between input and results screens |
| `ui/screen_input.py` | Domain/area inputs, field checkboxes, max-results slider, saved searches, settings |
| `ui/screen_results.py` | Live table, progress bar, pause/resume/stop, CSV export |
| `core/scraper.py` | Maps search URL building, feed scrolling, per-listing extraction, worker thread |
| `core/exporter.py` | Writes selected fields to `.csv` |
| `core/settings.py` | Persists headless preference, limit cap, and recent searches to disk |

---

## How it works

1. **Search** — Opens `https://www.google.com/maps/search/{domain}+in+{area}` in Chrome.
2. **Collect links** — Reads place URLs from the results feed, skipping sponsored entries.
3. **Extract** — Opens each place page, reads the selected fields from the DOM, then returns to the list.
4. **Scroll** — Scrolls the feed to load more results until the max limit, end of list, or stop/pause.
5. **Export** — Optional CSV export from the in-memory results list.

Scraping runs on a background `QThread` so the UI stays responsive. Pause uses a thread event; resume continues from the same browser session and processed set.

---

## Configuration file

Settings are stored at:

```
~/.mapharvest/settings.json
```

Example:
=======
---

## ⚙️ How It Works

1. **Search** — Opens `https://www.google.com/maps/search/{domain}+in+{area}` in Chrome
2. **Collect links** — Reads place URLs from the results feed, skipping sponsored entries
3. **Extract** — Opens each place page, reads selected fields from the DOM, returns to the list
4. **Scroll** — Scrolls the feed to load more results until the max limit or end of list
5. **Export** — Optional CSV export from the in-memory results list

Scraping runs on a background `QThread` so the UI stays fully responsive. Pause uses a thread event; resume continues from the same browser session.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| UI | PyQt5 |
| Browser Automation | Selenium |
| Bot Detection Bypass | undetected-chromedriver |
| Threading | QThread + pyqtSignal |
| Export | Python `csv` (built-in) |
| Config | JSON (`~/.mapharvest/settings.json`) |

---

## 🗂️ Configuration File

Settings are stored at `~/.mapharvest/settings.json`:
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a

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

<<<<<<< HEAD
## Tech stack

- **[PyQt5](https://www.riverbankcomputing.com/software/pyqt/)** — Desktop GUI
- **[Selenium](https://www.selenium.dev/)** — Browser automation
- **[undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver)** — Chrome driver with reduced automation detection
- **Python 3.10+**

---

## Building a standalone executable (optional)

A PyInstaller spec file is included. Example:
=======
## 📦 Build Standalone Executable *(Optional)*

A PyInstaller spec file is included for packaging into a standalone `.exe`:
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a

```bash
pip install pyinstaller
pyinstaller main.spec
```

Adjust `main.spec` for your platform and icon as needed.

---

<<<<<<< HEAD
## Limitations & notes

- **Google Maps DOM changes** — Selectors may break if Google updates their UI; category and hours extraction depend on current page structure.
- **Rate & blocking** — Heavy or rapid scraping can trigger CAPTCHAs or temporary blocks. Use reasonable limits and delays.
- **Reviews** — Enabling review fields slows scraping because each listing opens the Reviews tab.
- **Legal / ToS** — Scraping Google Maps may violate [Google's Terms of Service](https://policies.google.com/terms). Use this tool responsibly, for personal or permitted use only. The authors are not responsible for misuse.

---

## Troubleshooting

| Issue | Things to try |
|-------|----------------|
| Chrome won't start | Install/update Google Chrome; delete cached chromedriver if version mismatch |
| Empty category or hours | Maps UI may have changed; try a visible (non-headless) run to inspect the page |
| No results | Check domain/area spelling; try plural terms (e.g. `car dealers`) |
=======
## 🔧 Troubleshooting

| Issue | Fix |
|-------|-----|
| Chrome won't start | Install or update Google Chrome; delete cached chromedriver if version mismatch |
| Empty category or hours | Maps UI may have changed; try a visible (non-headless) run to inspect the page |
| No results found | Check domain/area spelling; try plural terms (e.g. `car dealers`) |
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a
| App feels slow | Lower max results; uncheck Review fields; enable headless mode |

---

<<<<<<< HEAD
## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
=======
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
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a
5. Open a Pull Request

---

<<<<<<< HEAD
## License

This project is provided as-is. Add a `LICENSE` file (e.g. MIT) if you plan to open-source it publicly.

---

## Acknowledgments

Built for local lead generation and business research workflows without relying on paid Maps APIs.
=======
## 👤 Author

Made with 🖤 by **[Farisxdev](https://github.com/farixdev)**

> Built for local lead generation and business research workflows — without relying on paid Maps APIs.

---

<div align="center">

Found this useful? Drop a ⭐ on GitHub — it helps a lot!

</div>
>>>>>>> 93ced9512f2373f3bc32da39e55243eb275c824a
