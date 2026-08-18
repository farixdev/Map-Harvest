# 🗺️ MapHarvest — User Manual & Perpetual Hosting Guide

Welcome to the MapHarvest User Manual and Perpetual Hosting Guide. This document provides a complete guide on how to use MapHarvest (both for scraping and cold-outreach via CSV imports), how to host it on a virtual private server (VPS) to run 24/7, estimated costs, and how to maintain the system indefinitely.

---

## 📑 Table of Contents
1. [App Overview & Interface Layout](#1-app-overview--interface-layout)
2. [Outreach-First Flow (CSV Import Without Scraping)](#2-outreach-first-flow-csv-import-without-scraping)
3. [Running 24/7: Hosting, Setup & Costs](#3-running-247-hosting-setup--costs)
4. [Sender Identity & Compliance Setup](#4-sender-identity--compliance-setup)
5. [SMTP & AI Configuration](#5-smtp--ai-configuration)
6. [System Maintenance & Keeping It Running Forever](#6-system-maintenance--keeping-it-running-forever)

---

## 1. App Overview & Interface Layout

MapHarvest is a hybrid local automation system that consists of two distinct cores:
* **The Scraper:** Scrolls Google Maps progressive feeds via Selenium, reading business details (ratings, websites, phone numbers) free of API costs.
* **The Outreach Campaign Engine:** Crawls the extracted websites, performs zero-token local audits, generates AI-personalized icebreakers using Groq/OpenRouter, and sends paced follow-up email campaigns.

### Main Navigation Header
The header on the home screen allows you to access all core actions:
1. **Scrape Tab:** Enter business categories and target cities.
2. **Filters Tab:** Set conditions (e.g., *Rating > 4.5*, *Must have website*, *No email found yet*).
3. **Settings Tab:** Set local default caps and export folders.
4. **Outreach Button:** Navigate directly to the Outreach Workspace to import leads and launch campaigns.

---

## 2. Outreach-First Flow (CSV Import Without Scraping)

If you already have a list of leads (e.g., from an external database or purchased directory), you can bypass the scraper completely:

1. **Launch Outreach:** Click the **Outreach** button in the top-right header of the main screen.
2. **Import CSV:** Click the **Import CSV…** button at the bottom-left of the **Leads** table.
3. **Column Mapping:** MapHarvest automatically maps columns based on header names (it looks for common variations of `email`, `name`, `website`, `phone`, `city`, and `category`). **At least one email column is required.**
4. **Run Website Audits:**
   * Select the imported leads in the table.
   * Click **Audit all** in the bottom-right.
   * The background thread will crawl each website to find technical gaps (e.g., missing booking widgets, stale blogs, missing CRM signals) and write AI-personalized icebreakers.
5. **Prepare & Launch Campaign:** Select your email template on the **Campaign** tab, click **Prepare campaign**, review the preview, and click **Start** on the **Sending** tab.

---

## 3. Running 24/7: Hosting, Setup & Costs

Because MapHarvest uses **Chrome driven by Selenium** (`undetected-chromedriver`) to scrape Google Maps and crawls target websites for audits, it is designed as a desktop application. To run it 24/7 without keeping your personal computer turned on, you can host it on a Virtual Private Server (VPS).

### Option A: Windows Server VPS (Recommended & Easiest)
Since MapHarvest is built using PyQt5, running it on a Windows Server VPS provides a full GUI desktop out of the box.

* **Recommended VPS Spec:** 2 vCPUs, 4GB RAM, 50GB SSD. (Do not go lower than 4GB RAM, as Chrome and PyQt5 require moderate memory).
* **Estimated Cost:** **$10 to $20 / month**.
* **Suggested Providers:**
  * **Hetzner** (CX22 / CX32)
  * **Contabo** (Cloud VPS S)
  * **Kamatera** (Custom Windows VPS)
* **Setup Process:**
  1. Buy a Windows Server VPS (e.g., Windows Server 2022) from your chosen provider.
  2. Connect to the VPS via **Remote Desktop Connection (RDP)** from your local computer.
  3. Install **Google Chrome** on the VPS.
  4. Download and install **Python 3.10+**.
  5. Clone the MapHarvest repository and install dependencies:
     ```cmd
     python -m venv venv
     call venv\Scripts\activate
     pip install -r requirements.txt
     ```
  6. Run `python main.py`. Keep the command prompt running.
  7. **Crucial:** When closing the Remote Desktop session, do **NOT** shut down or log out the server. Simply click the "X" button to disconnect the RDP client. The virtual desktop session keeps running on the server, allowing MapHarvest to operate 24/7.

### Option B: Linux VPS with Headless Virtual Display (Advanced)
If you want to save on Windows licensing costs, you can run MapHarvest on a Linux VPS (Ubuntu Server) using a virtual framebuffer.

* **Estimated Cost:** **$5 to $10 / month** (no Windows OS license fee).
* **Setup Process:**
  1. Purchase an Ubuntu Server VPS.
  2. Install a virtual frame buffer (`Xvfb`), Chrome, and python requirements:
     ```bash
     sudo apt update
     sudo apt install -y xorg xvfb chromium-browser python3-pip python3-venv
     ```
  3. Set up the virtual display environment variable:
     ```bash
     Xvfb :99 -screen 0 1024x768x24 &
     export DISPLAY=:99
     ```
  4. Run `python main.py` or execute automated scripts using headless virtual display.

---

## 4. Sender Identity & Compliance Setup

To ensure high email deliverability and stay compliant with anti-spam laws (like CAN-SPAM and GDPR), configure your sender profile in **Settings → Sender**:

* **Sender Name & Company:** Input a real human name and registered business name.
* **Postal Address (Required):** Under CAN-SPAM, you must provide a valid physical postal address. This is automatically rendered into the footer of every plain-text and HTML email.
* **Opt-Out Footer:** MapHarvest automatically appends a clear opt-out statement to the footer: *"Not the right person? Reply 'unsubscribe' and I will stop."*
* **Suppression List:** When a recipient replies "unsubscribe", right-click their lead in the **Leads** table and click **Suppress**. This permanently prevents the system from scheduling any future emails or follow-ups to that email address.

---

## 5. SMTP & AI Configuration

### Gmail SMTP Setup
Gmail requires **App Passwords** to allow SMTP connections.
1. Enable **2-Step Verification** on your Google Account ([myaccount.google.com/security](https://myaccount.google.com/security)).
2. Go to **App passwords** ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)) and generate a 16-character code named `MapHarvest`.
3. In **Settings → Gmail**, add your Gmail address and paste the App Password.
4. Press **Verify** to test the connection.

### AI Personalization (Groq or OpenRouter)
* In **Settings → AI**, enter your **Groq** or **OpenRouter** API key.
* The system will automatically fetch available models from their endpoints and populate the **Model** dropdown.
* Choose a fast, cheap model (like `llama-3.3-70b-versatile` on Groq).
* **Note:** AI personalization is optional. If turned off, MapHarvest will send default, clean email templates without AI-generated text.

---

## 6. System Maintenance & Keeping It Running Forever

To keep MapHarvest running continuously without errors:

1. **Auto-Update Chrome & Undetected-Chromedriver:**
   * Undetected-chromedriver automatically downloads the matching driver version for your installed Google Chrome.
   * On your hosting server, ensure Chrome is set to auto-update. If you encounter a driver version mismatch, run:
     ```bash
     pip install --upgrade undetected-chromedriver
     ```
2. **Handle Gmail Limits Safely:**
   * Gmail daily limits are ~500 emails/day for free accounts and 2000 emails/day for Workspace accounts.
   * **Do not exceed 40-50 emails/day per account** to prevent domain warming blocks.
   * Use MapHarvest's built-in **Warm-up Ramp** (Settings → Sending) to automatically scale up daily send limits from 10 to 40 over a week.
3. **Database Maintenance (`outreach.db`):**
   * SQLite databases can grow over time. The database resides in `~/.mapharvest/outreach.db`.
   * Back up this file periodically. If you migrate servers, copying the `~/.mapharvest` folder to the new server will preserve all leads, campaigns, caches, and suppression lists.
