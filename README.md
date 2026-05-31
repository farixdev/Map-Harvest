<div align="center">

# 🗺️ MapHarvest

> *Every business. One click.*

**A modern desktop tool for scraping Google Maps business data — search, extract, and export to CSV with a clean minimal UI.**

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-41CD52?style=flat-square&logo=qt&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Status](https://img.shields.io/badge/status-active-brightgreen?style=flat-square)

</div>

---

## 📖 Overview

MapHarvest is a clean, minimal desktop application that automates Google Maps business data extraction. Enter a location and one or more business categories, hit start, and watch it scrape — live logs, real-time results table, and a one-click CSV export. Built to be fast, undetected, and dead simple to use.

---

## ✨ Features

- 🔍 Search any business category in any city or neighborhood
- 📋 Single domain or multi-domain list mode
- ☑️ Choose exactly which fields to extract
- 📊 Live results table — rows populate as each business is extracted
- 🪵 Real-time log with step-by-step status updates
- 📁 One-click CSV export — one file per domain
- 🚫 Sponsored listings automatically skipped
- 🛑 Graceful stop button — safely kills the scraper mid-run
- 🤖 Bot detection bypass via `undetected-chromedriver`

---

## 🖥️ Application Flow

### Screen 1 — Input

Configure your search before starting:

- **Domain(s)** — enter a single category or switch to list mode for multiple (one per line)
- **Area** — city, region, or neighborhood (e.g. `Lahore`, `DHA Phase 5`)
- **Data fields** — choose what to extract:

| Field | Default |
|-------|---------|
| Business Name | ✅ |
| Rating | ✅ |
| Address | ✅ |
| Website | ✅ |
| Phone Number | ✅ |
| Maps Link | ✅ |

### Screen 2 — Live Results

Once started, everything happens in real time:

```
● Searching "restaurants in Lahore"...
● Scrolling to load all results...
● Found 47 listings (sponsored skipped)
● Extracting 1/47...

████████████░░░░░░░  24 / 47

┌──────────────┬────────┬───────────┬─────────┬───────────┐
│ Name         │ Rating │ Address   │ Website │ Phone     │
├──────────────┼────────┼───────────┼─────────┼───────────┤
│ Cafe Aylanto │ 4.6 ★  │ MM Alam Rd│ ...     │ 0304-...  │
└──────────────┴────────┴───────────┴─────────┴───────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| UI | PyQt5 |
| Scraping | Selenium + undetected-chromedriver |
| Threading | QThread + pyqtSignal |
| Export | Python `csv` (built-in) |
| Browser | Google Chrome |

---

## 🚀 Installation

### Prerequisites

- Python `3.9+`
- Google Chrome (latest version)
- Internet connection

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/farixdev/mapharvest.git
cd mapharvest

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python main.py
```

### requirements.txt

```
PyQt5>=5.15.0
selenium>=4.18.0
undetected-chromedriver>=3.5.0
```

---

## 📁 Project Structure

```
mapharvest/
├── main.py                  # Entry point
├── ui/
│   ├── app.py               # Main window
│   ├── screen_input.py      # Screen 1: domain, area, checkboxes
│   └── screen_results.py    # Screen 2: live log + results table
├── core/
│   ├── scraper.py           # Selenium scraper + QThread worker
│   └── exporter.py          # CSV export logic
├── requirements.txt
└── README.md
```

---

## 📋 CSV Output

Results are exported as clean CSV files, one per domain:

**Filename format:** `restaurants_in_lahore.csv`

```
Business Name,Rating,Address,Website,Phone,Maps Link
Cafe Aylanto,4.6,MM Alam Rd Lahore,https://aylanto.com,0304-1234567,https://maps.google.com/...
Burning Brownie,4.4,DHA Phase 5,https://burningbrownie.com,0300-9876543,https://maps.google.com/...
```

Only the fields you selected on Screen 1 will appear as columns.

---

## ⚠️ Notes & Limitations

- Google Maps DOM selectors can change over time — if extraction breaks, inspect the element in Chrome and update the selector in `core/scraper.py`
- Run the browser **visible** during development — only switch to headless after everything works
- A delay of `1.5s` between each business click is built in to avoid CAPTCHA triggers
- Results depend on Google Maps data availability for the searched area
- Use responsibly and in accordance with Google's Terms of Service

---

## 📜 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 👤 Author

Made with 🖤 by **[Farisxdev](https://github.com/farixdev)**

---

<div align="center">

Found this useful? Drop a ⭐ on GitHub — it helps a lot!

</div>
