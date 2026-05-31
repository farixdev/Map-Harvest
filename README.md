# MapHarvest

A local desktop app for scraping business listings from Google Maps and exporting them to CSV. Built with **PyQt5**, **Selenium**, and **undetected-chromedriver**.

No external APIs. No cloud. Everything runs on your machine with a visible (or headless) Chrome browser.

---

## Features

### Scraping
- Search by **business type** (domain) and **location** (area)
- **Multi-domain** searches — run several categories in one session via the List dialog
- **Configurable fields** — choose exactly which data points to collect
- **Sponsored listings skipped** automatically
- **Max results limit** — horizontal slider caps how many businesses are collected per run
- **Pause / Resume** — pause mid-scrape without losing progress
- Progressive scrolling — loads more Maps results as needed until the limit or end of list is reached

### Data export
- Live results table during scraping
- **Export CSV** with UTF-8 BOM (Excel-friendly)
- Custom save path and filename

### Settings & convenience
- **Headless mode** — hide Chrome while scraping (Settings tab)
- **Adjustable result cap** — raise the slider maximum beyond 100 (up to 1000) in Settings
- **Recent searches** — last 12 domain + area + limit combos saved and reloadable with one click
- Settings persist in `~/.mapharvest/settings.json`

### UI
- Two-screen flow: input → live results
- **Scrape Another** returns to the home screen after a run finishes or is stopped
- Dark, minimal interface

---

## Requirements

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

## Project structure

```
Map Harvest/
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
│
└── README.md
```

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

## Tech stack

- **[PyQt5](https://www.riverbankcomputing.com/software/pyqt/)** — Desktop GUI
- **[Selenium](https://www.selenium.dev/)** — Browser automation
- **[undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver)** — Chrome driver with reduced automation detection
- **Python 3.10+**

---

## Building a standalone executable (optional)

A PyInstaller spec file is included. Example:

```bash
pip install pyinstaller
pyinstaller main.spec
```

Adjust `main.spec` for your platform and icon as needed.

---

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
| App feels slow | Lower max results; uncheck Review fields; enable headless mode |

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

This project is provided as-is. Add a `LICENSE` file (e.g. MIT) if you plan to open-source it publicly.

---

## Acknowledgments

Built for local lead generation and business research workflows without relying on paid Maps APIs.
