# MapHarvest

Google Maps business scraper — desktop app built with PyQt5, Selenium, and undetected-chromedriver.

## What it does

Enter a business domain (e.g. `restaurants`) and a location (e.g. `Lahore`). MapHarvest searches Google Maps, scrolls through all results, skips sponsored listings, clicks each card, and extracts business data — exporting it as a clean CSV.

**No external APIs. No AI. No cloud. Runs fully local.**

## Requirements

- Python 3.10+
- Google Chrome (for Selenium)

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## Project structure

```
├── main.py                   # launches the app
├── ui/
│   ├── app.py                # QMainWindow + QStackedWidget (screen manager)
│   ├── screen_input.py       # Screen 1: inputs + checkboxes + start button
│   └── screen_results.py     # Screen 2: live log + progress bar + results table
├── core/
│   ├── scraper.py            # Selenium logic + QThread worker
│   └── exporter.py           # CSV export
├── requirements.txt
└── README.md
```

## Usage

1. Enter a domain (single or list mode) and an area.
2. Choose which fields to scrape (name, rating, address, website, phone, maps link).
3. Click **Start Scraping** — the window expands to show live progress.
4. When done, click **Export CSV** to save results.

## Stack

- **PyQt5** — desktop UI
- **Selenium** + **undetected-chromedriver** — browser automation
- **Python 3.10+**
