import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

from core.distutils_compat import *  # noqa: F401,F403 — must load before uc

import undetected_chromedriver as uc
from PyQt5.QtCore import QThread, pyqtSignal
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── Timing (seconds) ─────────────────────────────────────────────────────────
T_SEARCH_LOAD = 1.0
T_DETAIL_LOAD = 0.35
T_AFTER_BACK = 0.25
T_SCROLL = 0.3


def _chrome_major_version() -> int:
    version_re = re.compile(r"^(\d+)\.\d+\.\d+\.\d+$")
    if sys.platform.startswith("win"):
        for app_dir in (
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application"),
        ):
            if not os.path.isdir(app_dir):
                continue
            for name in os.listdir(app_dir):
                m = version_re.match(name)
                if m:
                    return int(m.group(1))
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
            version, _ = winreg.QueryValueEx(key, "version")
            m = re.match(r"(\d+)\.", str(version))
            if m:
                return int(m.group(1))
        except Exception:
            pass
    return 0


def get_driver(headless: bool = False):
    options = uc.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-popup-blocking")
    options.page_load_strategy = "eager"

    major = _chrome_major_version()
    kwargs = {"options": options, "use_subprocess": True}
    if major:
        kwargs["version_main"] = major

    driver = uc.Chrome(**kwargs)
    driver.set_window_size(1200, 800)
    driver.set_page_load_timeout(25)
    return driver


def _search_url(domain: str, area: str) -> str:
    query = f"{domain} in {area}"
    return f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"


def _place_key(href: str) -> str:
    """Stable ID for deduplication across URL variants."""
    m = re.search(r"/place/[^/]+/([^/@?]+)", href)
    if m:
        return m.group(1)
    m = re.search(r"!1s([^!]+)", href)
    if m:
        return m.group(1)
    return href.split("?")[0]


def _get_feed(driver):
    try:
        return WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"]'))
        )
    except Exception:
        return None


def _wait_for_feed(driver) -> bool:
    return _get_feed(driver) is not None


def _is_end_of_list(driver) -> bool:
    try:
        driver.find_element(By.XPATH, "//*[contains(text(), \"You've reached the end\")]")
        return True
    except NoSuchElementException:
        return False


def _scroll_feed_step(driver, feed) -> None:
    driver.execute_script(
        "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight * 0.8",
        feed,
    )
    time.sleep(T_SCROLL)


def _return_to_results(driver, search_url: str) -> None:
    """Go back to the search-results list so the feed is visible again."""
    back_selectors = (
        'button[aria-label="Back"]',
        'button[aria-label*="Back to results"]',
        'button[jsaction*="pane.backSection"]',
        'button[jsaction*="back"]',
    )
    for sel in back_selectors:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].click();", btn)
            if _wait_for_feed(driver):
                time.sleep(T_AFTER_BACK)
                return
        except Exception:
            continue

    try:
        driver.back()
        if _wait_for_feed(driver):
            time.sleep(T_AFTER_BACK)
            return
    except Exception:
        pass

    driver.get(search_url)
    time.sleep(T_SEARCH_LOAD)
    _wait_for_feed(driver)


def get_visible_listings(driver) -> list[str]:
    if not _get_feed(driver):
        return []

    try:
        anchors = driver.find_elements(
            By.CSS_SELECTOR,
            'div[role="feed"] a[href*="google.com/maps/place"]',
        )
    except Exception:
        return []

    results = []
    seen = set()
    for anchor in anchors:
        try:
            href = anchor.get_attribute("href") or ""
            if not href:
                continue
            key = _place_key(href)
            if key in seen:
                continue

            sponsored = False
            node = anchor
            for _ in range(4):
                try:
                    node = node.find_element(By.XPATH, "..")
                    blob = (node.text or "") + (node.get_attribute("aria-label") or "")
                    if "Sponsored" in blob:
                        sponsored = True
                        break
                except Exception:
                    break

            if not sponsored:
                seen.add(key)
                results.append(href)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    return results


def _first_text(scope, selectors: str, wait=None) -> str:
    for sel in selectors.split(", "):
        try:
            if wait:
                el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel.strip())))
            else:
                el = scope.find_element(By.CSS_SELECTOR, sel.strip())
            text = (el.text or el.get_attribute("textContent") or "").strip()
            if text:
                return " ".join(text.split())
        except Exception:
            continue
    return ""


def _first_attr(scope, selectors: str, attr: str) -> str:
    for sel in selectors.split(", "):
        try:
            el = scope.find_element(By.CSS_SELECTOR, sel.strip())
            val = (el.get_attribute(attr) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


def _xpath_text(driver, xpaths: list[str]) -> str:
    for xp in xpaths:
        try:
            el = driver.find_element(By.XPATH, xp)
            text = (el.text or el.get_attribute("textContent") or "").strip()
            if text:
                return " ".join(text.split())
        except Exception:
            continue
    return ""


def _open_reviews_tab(driver) -> bool:
    for sel in ('button[aria-label*="Reviews"]', 'button[data-tab-index="1"]'):
        try:
            for tab in driver.find_elements(By.CSS_SELECTOR, sel):
                label = (tab.get_attribute("aria-label") or tab.text or "").lower()
                if "review" in label:
                    driver.execute_script("arguments[0].click();", tab)
                    time.sleep(0.35)
                    return True
        except Exception:
            continue
    return False


def _element_text(el) -> str:
    text = (el.text or el.get_attribute("textContent") or "").strip()
    return " ".join(text.split()) if text else ""


def _extract_category(driver) -> str:
    """Category lives on the button itself (class DkEaL), not a child span."""
    selectors = (
        "button[jsaction*='category']",
        "button.DkEaL[jsaction*='category']",
        "button[jsaction*='.category']",
    )
    for sel in selectors:
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                text = _element_text(btn)
                if text and len(text) < 80:
                    return text
                label = (btn.get_attribute("aria-label") or "").strip()
                if label and len(label) < 80:
                    return label
        except Exception:
            continue

    for xp in (
        "//button[contains(@jsaction,'category')]",
        "//h1[contains(@class,'fontHeadline')]/following::button[1]",
    ):
        text = _xpath_text(driver, [xp])
        if text and len(text) < 80:
            return text
    return ""


def _find_hours_button(driver):
    for sel in (
        "button[data-item-id='oh']",
        "button[aria-label*='Hours']",
        "button[data-tooltip*='hours']",
        "button[data-tooltip*='Hours']",
    ):
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn:
                return btn
        except Exception:
            continue
    return None


def _hours_from_label(label: str) -> str:
    if not label:
        return ""
    label = label.strip()
    for prefix in ("Hours:", "Opening hours:", "Hours "):
        if label.lower().startswith(prefix.lower()):
            return label[len(prefix):].strip()
    return label


def _extract_hours(driver) -> str:
    btn = _find_hours_button(driver)
    if btn is None:
        return _first_text(
            driver, "button[data-item-id='oh'] div.Io6YTe, div[aria-label*='Hours']"
        )

    aria = (btn.get_attribute("aria-label") or "").strip()
    collapsed = _element_text(btn) or _hours_from_label(aria)
    hours = collapsed

    try:
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.25)
        rows = []
        for row in driver.find_elements(By.CSS_SELECTOR, "table.eK4R0e tr, tr.y0skZc"):
            cells = [_element_text(c) for c in row.find_elements(By.CSS_SELECTOR, "td, th")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(": ".join(cells) if len(cells) > 1 else cells[0])
        if rows:
            hours = "; ".join(rows)
        else:
            expanded = _xpath_text(driver, [
                "//div[contains(@aria-label,'Monday')]",
                "//div[contains(@aria-label,'Sunday')]",
            ])
            if expanded:
                hours = expanded
    except Exception:
        pass

    return hours


def _extract_reviews(driver, count: int = 3) -> list[str]:
    reviews = []
    if not _open_reviews_tab(driver):
        return reviews
    try:
        WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.jftiE, div[data-review-id]"))
        )
    except Exception:
        return reviews

    for block in driver.find_elements(By.CSS_SELECTOR, "div.jftiE, div[data-review-id]")[:count]:
        try:
            author = _first_text(block, "div.d4r55, span.dwiWPf")
            rating = _first_attr(block, "span.kvMYJc, span[role='img']", "aria-label")
            date = _first_text(block, "span.rsqaWe, span.dehysf")
            body = _first_text(block, "span.wiI7pd, div.MyEned")
            parts = [p for p in (author, rating, date, body) if p]
            if parts:
                reviews.append(" | ".join(parts))
        except Exception:
            continue
    return reviews


def _extract_rating_and_count(driver) -> tuple[str, str]:
    rating, count = "", ""
    for el in driver.find_elements(By.CSS_SELECTOR, "div.F7nice, div.fontBodyMedium"):
        aria = el.get_attribute("aria-label") or ""
        if "star" in aria.lower() or "review" in aria.lower():
            rm = re.search(r"([\d.]+)\s*star", aria, re.I)
            if rm and not rating:
                rating = rm.group(1)
            cm = re.search(r"([\d,]+)\s*review", aria, re.I)
            if cm and not count:
                count = cm.group(1).replace(",", "")

    if not rating:
        rating = _first_text(driver, "div.F7nice span[aria-hidden='true'], span.ceNzKf")
        m = re.search(r"[\d.]+", rating)
        rating = m.group(0) if m else rating

    if not count:
        for el in driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='review'], span[aria-label*='review']"):
            aria = el.get_attribute("aria-label") or ""
            cm = re.search(r"([\d,]+)\s*review", aria, re.I)
            if cm:
                count = cm.group(1).replace(",", "")
                break
    return rating, count


def extract_business_data(driver, listing_href: str, fields: list) -> dict:
    """Open place page directly, extract fields, caller returns to search list."""
    data = {f: "" for f in fields}

    try:
        driver.get(listing_href)
        wait = WebDriverWait(driver, 6)
        wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR,
            "h1.DUwDvf, h1.fontHeadlineLarge, h1[class*='fontHeadline']",
        )))
        time.sleep(T_DETAIL_LOAD)

        if "name" in fields:
            data["name"] = _first_text(
                driver, "h1.DUwDvf, h1.fontHeadlineLarge, h1[class*='fontHeadline']",
            ) or _xpath_text(driver, ["//h1[contains(@class,'fontHeadline')]"])

        if "category" in fields:
            data["category"] = _extract_category(driver)

        if "rating" in fields or "review_count" in fields:
            rating, count = _extract_rating_and_count(driver)
            if "rating" in fields:
                data["rating"] = rating
            if "review_count" in fields:
                data["review_count"] = count

        if "hours" in fields:
            data["hours"] = _extract_hours(driver)

        if "address" in fields:
            data["address"] = _first_text(
                driver,
                "button[data-item-id='address'] div.Io6YTe, button[data-tooltip='Copy address'] .Io6YTe",
            )

        if "website" in fields:
            data["website"] = _first_attr(
                driver, "a[data-item-id='authority'], a[aria-label*='website']", "href"
            )

        if "phone" in fields:
            data["phone"] = _first_text(
                driver,
                "button[data-item-id^='phone'] .Io6YTe, button[data-tooltip='Copy phone number'] .Io6YTe",
            )
            if not data["phone"]:
                tel = _first_attr(driver, "a[href^='tel:']", "href")
                data["phone"] = tel.replace("tel:", "") if tel else ""

        if "maps_link" in fields:
            data["maps_link"] = driver.current_url.split("?")[0]

        if any(f in fields for f in ("review_1", "review_2", "review_3")):
            reviews = _extract_reviews(driver, count=3)
            for i, key in enumerate(("review_1", "review_2", "review_3")):
                if key in fields:
                    data[key] = reviews[i] if i < len(reviews) else ""

    except Exception as e:
        data["_error"] = str(e)

    return data


def scrape_domain_progressive(
    driver, domain: str, area: str, fields: list, worker, max_results: int = 0,
) -> int:
    """
    Open cards one-by-one from the visible feed, extract, return to list, scroll for more.
    max_results: stop after this many listings for this domain pass (0 = no limit).
    """
    search_url = _search_url(domain, area)
    driver.get(search_url)
    time.sleep(T_SEARCH_LOAD)

    feed = _get_feed(driver)
    if feed is None:
        return 0

    processed: set[str] = set()
    collected = 0
    idle_rounds = 0

    worker.log_signal.emit(f'Scanning "{domain} in {area}"...', "active")

    while worker._running and idle_rounds < 10:
        worker._wait_if_paused()
        if not worker._running:
            break

        if max_results > 0 and collected >= max_results:
            break

        hrefs = get_visible_listings(driver)
        next_href = None
        for href in hrefs:
            if _place_key(href) not in processed:
                next_href = href
                break

        if next_href:
            idle_rounds = 0
            processed.add(_place_key(next_href))

            worker._wait_if_paused()
            if not worker._running:
                break

            data = extract_business_data(driver, next_href, fields)
            _return_to_results(driver, search_url)
            feed = _get_feed(driver)

            data["_domain"] = domain
            data["_area"] = area
            if "domain" in fields:
                data["domain"] = domain

            collected += 1
            worker.result_signal.emit(data)
            worker.progress_signal.emit(worker._total_collected + collected)

            name = (data.get("name") or "Unknown")[:50]
            worker.log_signal.emit(f"#{worker._total_collected + collected}  {name}", "done")

            if max_results > 0 and collected >= max_results:
                break
            continue

        if _is_end_of_list(driver):
            break

        if feed is None:
            feed = _get_feed(driver)
        if feed is None:
            driver.get(search_url)
            time.sleep(T_SEARCH_LOAD)
            feed = _get_feed(driver)
            if feed is None:
                break

        worker.log_signal.emit("Loading more results...", "active")
        _scroll_feed_step(driver, feed)
        idle_rounds += 1

    return collected


class ScrapeWorker(QThread):
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    result_signal = pyqtSignal(dict)
    done_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    paused_signal = pyqtSignal(bool)

    def __init__(
        self,
        domains: list,
        area: str,
        fields: list,
        headless: bool = False,
        max_results: int = 0,
    ):
        super().__init__()
        self.domains = domains
        self.area = area
        self.fields = fields
        self.headless = headless
        self.max_results = max(0, max_results)
        self._running = True
        self._paused = False
        self._pause_lock = threading.Event()
        self._pause_lock.set()
        self._total_collected = 0

    def stop(self):
        self._running = False
        self._pause_lock.set()

    def pause(self):
        self._paused = True
        self._pause_lock.clear()
        self.paused_signal.emit(True)

    def resume(self):
        self._paused = False
        self._pause_lock.set()
        self.paused_signal.emit(False)

    def _wait_if_paused(self):
        while self._running and not self._pause_lock.is_set():
            time.sleep(0.15)

    def run(self):
        driver = None
        try:
            driver = get_driver(headless=self.headless)
            self._total_collected = 0

            for domain in self.domains:
                if not self._running:
                    break

                remaining = 0
                if self.max_results > 0:
                    remaining = self.max_results - self._total_collected
                    if remaining <= 0:
                        break

                count = scrape_domain_progressive(
                    driver, domain, self.area, self.fields, self,
                    max_results=remaining,
                )
                self._total_collected += count

                if count == 0:
                    self.log_signal.emit(f'No results for "{domain}"', "done")
                else:
                    self.log_signal.emit(f'"{domain}" done — {count} total', "done")

                if self.max_results > 0 and self._total_collected >= self.max_results:
                    break

        except Exception as e:
            self.error_signal.emit(str(e))

        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            self.done_signal.emit()
