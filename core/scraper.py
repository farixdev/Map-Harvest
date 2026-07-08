"""Google Maps scraping engine.

Design: **card-first, detail-on-demand.**

The Google Maps results feed already carries name, rating, category, address,
phone, website and (encoded in each place URL) coordinates + a stable Place ID.
We parse all of that straight from the feed HTML in a single pass per scroll
— no per-business browser tab, no page load per row. That is the whole speed
and reliability win over the old "open every listing in a new tab" approach.

We only open individual place pages when the user explicitly asks for a field
that the feed cannot provide (weekly Hours, or Review snippets). That detail
pass also upgrades address/category/rating to their full-detail values.
"""

import os
import re
import sys
import threading
import time
import urllib.parse

from core.distutils_compat import *  # noqa: F401,F403 — must load before uc

import undetected_chromedriver as uc
from PyQt5.QtCore import QThread, pyqtSignal
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.parse import parse_feed_html, parse_place_url, place_key

# ── Timing (seconds) ─────────────────────────────────────────────────────────
T_SEARCH_LOAD = 2.0
T_DETAIL_LOAD = 0.5
T_AFTER_SCROLL = 0.5

# Fields that cannot be read from a feed card — they force a detail-page visit.
DETAIL_ONLY_FIELDS = ("hours", "review_1", "review_2", "review_3")
# Card-derived fields that a detail visit can improve when one happens anyway.
UPGRADEABLE_FIELDS = ("address", "category", "rating", "review_count")


# ── Chrome / driver ──────────────────────────────────────────────────────────
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
    # Force English UI so our selectors and status strings stay predictable.
    options.add_experimental_option("prefs", {"intl.accept_languages": "en-US,en"})
    options.page_load_strategy = "eager"

    major = _chrome_major_version()
    kwargs = {"options": options, "use_subprocess": True}
    if major:
        kwargs["version_main"] = major

    driver = uc.Chrome(**kwargs)
    driver.set_window_size(1280, 900)
    driver.set_page_load_timeout(30)
    return driver


def _search_url(domain: str, area: str) -> str:
    query = f"{domain} in {area}"
    # hl=en keeps the UI/end-of-list strings in English.
    return f"https://www.google.com/maps/search/{urllib.parse.quote(query)}?hl=en"


# ── Consent / blocking ───────────────────────────────────────────────────────
def _dismiss_consent_screen(driver) -> bool:
    """Click through Google's cookie/consent interstitial if it appears.

    On non-US IPs Google shows a consent page *before* the Maps feed, so the
    feed selectors otherwise find nothing because the feed isn't there yet.
    """
    clicked = False
    xpaths = (
        "//button[.//div[contains(text(),'Accept all')]]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'Reject all')]",
        "//button[contains(., 'I agree')]",
        "//form//button[contains(@aria-label, 'Accept')]",
    )
    for xp in xpaths:
        try:
            btn = driver.find_element(By.XPATH, xp)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                time.sleep(0.8)
                break
        except Exception:
            continue
    return clicked


def _page_blocked_reason(driver) -> str:
    """Human-readable reason if Google is blocking/challenging us, else ''."""
    try:
        url = driver.current_url or ""
        src = (driver.page_source or "")[:20000]
    except Exception:
        return ""

    if "consent.google.com" in url:
        return "Stuck on Google's cookie-consent page — the consent click did not go through."
    if "sorry/index" in url or "unusual traffic" in src.lower():
        return "Google is showing an 'unusual traffic' / CAPTCHA page — the automated browser got flagged."
    if "recaptcha" in src.lower() and "maps" not in url:
        return "Google is showing a reCAPTCHA challenge instead of Maps."
    return ""


# ── Feed detection / scrolling ───────────────────────────────────────────────
def _get_feed(driver):
    selectors = (
        'div[role="feed"]',
        'div[aria-label*="Results for"]',
        'div[aria-label*="Search results"]',
    )
    for sel in selectors:
        try:
            for feed in driver.find_elements(By.CSS_SELECTOR, sel):
                if feed.is_displayed():
                    return feed
        except Exception:
            continue
    try:
        return WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//div[@role='feed' or contains(@aria-label, 'Results for') "
                "or contains(@aria-label, 'Search results')]",
            ))
        )
    except Exception:
        return None


def _is_loading(driver) -> bool:
    """True if a *visible* loading/progress indicator is on the page."""
    try:
        for sel in ('div[role="progressbar"]', 'div.section-loading', 'div.loading'):
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
                        continue
                    style = el.get_attribute("style") or ""
                    if "opacity: 0" in style or "opacity:0" in style:
                        continue
                    size = el.size
                    if size and (size.get("width", 0) == 0 or size.get("height", 0) == 0):
                        continue
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _is_end_of_list(driver) -> bool:
    try:
        if _is_loading(driver):
            return False
        return bool(driver.find_elements(
            By.XPATH,
            "//*[contains(text(), \"You've reached the end\") "
            "or contains(text(), 'reached the end') "
            "or contains(text(), 'end of the list')]",
        ))
    except Exception:
        return False


def _feed_html(driver) -> str:
    feed = _get_feed(driver)
    if feed is None:
        return ""
    try:
        return feed.get_attribute("outerHTML") or ""
    except StaleElementReferenceException:
        feed = _get_feed(driver)
        return (feed.get_attribute("outerHTML") or "") if feed else ""
    except Exception:
        return ""


def _scroll_feed_step(driver, feed) -> None:
    """Nudge the feed so Maps' lazy-load fires, then wait for it to settle."""
    try:
        driver.execute_script(
            """
            const el = arguments[0];
            el.scrollTop = el.scrollTop + el.clientHeight * 0.9;
            el.dispatchEvent(new WheelEvent('wheel', {deltaY: 1000, bubbles: true}));
            const links = el.querySelectorAll('a[href*="/maps/place"]');
            if (links.length) links[links.length - 1].scrollIntoView({block: 'end'});
            """,
            feed,
        )
    except Exception:
        pass

    start = time.time()
    while _is_loading(driver) and time.time() - start < 6.0:
        time.sleep(0.25)
    time.sleep(T_AFTER_SCROLL)


# ── Detail-page extraction (only used when Hours/Reviews are requested) ───────
def _first_text(scope, selectors: str) -> str:
    for sel in selectors.split(", "):
        try:
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


def _xpath_text(driver, xpaths: list) -> str:
    for xp in xpaths:
        try:
            el = driver.find_element(By.XPATH, xp)
            text = (el.text or el.get_attribute("textContent") or "").strip()
            if text:
                return " ".join(text.split())
        except Exception:
            continue
    return ""


def _element_text(el) -> str:
    text = (el.text or el.get_attribute("textContent") or "").strip()
    return " ".join(text.split()) if text else ""


def _extract_category(driver) -> str:
    for sel in ("button[jsaction*='category']", "button.DkEaL[jsaction*='category']"):
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                text = _element_text(btn)
                if text and len(text) < 80:
                    return text
        except Exception:
            continue
    return _xpath_text(driver, ["//button[contains(@jsaction,'category')]"])


def _find_hours_button(driver):
    for sel in (
        "button[data-item-id='oh']",
        "button[aria-label*='Hours']",
        "button[data-tooltip*='hours']",
        "button[data-tooltip*='Hours']",
    ):
        try:
            return driver.find_element(By.CSS_SELECTOR, sel)
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
        return _first_text(driver, "button[data-item-id='oh'] div.Io6YTe, div[aria-label*='Hours']")

    aria = (btn.get_attribute("aria-label") or "").strip()
    hours = _element_text(btn) or _hours_from_label(aria)
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
    except Exception:
        pass
    return hours


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


def _extract_reviews(driver, count: int = 3) -> list:
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


def _extract_rating_and_count(driver) -> tuple:
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
    if not count:
        for el in driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='review'], span[aria-label*='review']"):
            aria = el.get_attribute("aria-label") or ""
            cm = re.search(r"([\d,]+)\s*review", aria, re.I)
            if cm:
                count = cm.group(1).replace(",", "")
                break
    return rating, count


def _extract_detail(driver, href: str, fields: list) -> dict:
    """Open a place page and pull the detail-only + upgradeable fields."""
    out = {}
    try:
        driver.get(href)
        WebDriverWait(driver, 8).until(EC.presence_of_element_located((
            By.CSS_SELECTOR, "h1.DUwDvf, h1.fontHeadlineLarge, h1[class*='fontHeadline']",
        )))
        time.sleep(T_DETAIL_LOAD)

        if "address" in fields:
            out["address"] = _first_text(
                driver,
                "button[data-item-id='address'] div.Io6YTe, "
                "button[data-tooltip='Copy address'] .Io6YTe",
            )
        if "phone" in fields:
            phone = _first_text(
                driver,
                "button[data-item-id^='phone'] .Io6YTe, "
                "button[data-tooltip='Copy phone number'] .Io6YTe",
            )
            if not phone:
                tel = _first_attr(driver, "a[href^='tel:']", "href")
                phone = tel.replace("tel:", "").strip() if tel else ""
            out["phone"] = phone
        if "website" in fields:
            out["website"] = _first_attr(
                driver, "a[data-item-id='authority'], a[aria-label*='website']", "href",
            )
        if "category" in fields:
            out["category"] = _extract_category(driver)
        if "rating" in fields or "review_count" in fields:
            rating, count = _extract_rating_and_count(driver)
            if rating:
                out["rating"] = rating
            if count:
                out["review_count"] = count
        if "hours" in fields:
            out["hours"] = _extract_hours(driver)
        if any(f in fields for f in ("review_1", "review_2", "review_3")):
            reviews = _extract_reviews(driver, count=3)
            for i, key in enumerate(("review_1", "review_2", "review_3")):
                if key in fields:
                    out[key] = reviews[i] if i < len(reviews) else ""
    except Exception as e:
        out["_error"] = str(e)
    return {k: v for k, v in out.items() if v}


# ── Record assembly ──────────────────────────────────────────────────────────
def _card_to_record(card: dict, fields: list, domain: str, area: str) -> dict:
    record = {f: card.get(f, "") for f in fields}
    record["_domain"] = domain
    record["_area"] = area
    record["_href"] = card.get("_href", "")
    if "domain" in fields:
        record["domain"] = domain
    return record


def _debug_dump(driver, tag: str) -> None:
    try:
        os.makedirs("debug", exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", tag)[:40]
        driver.save_screenshot(f"debug/{safe}.png")
        with open(f"debug/{safe}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("debug dump failed:", e)


# ── Main per-domain scrape ───────────────────────────────────────────────────
def scrape_domain_progressive(
    driver, domain: str, area: str, fields: list, worker, max_results: int = 0,
) -> tuple:
    """Scrape one domain. Returns (collected_count, completed_naturally)."""
    search_url = _search_url(domain, area)
    driver.get(search_url)
    time.sleep(1.0)

    if _dismiss_consent_screen(driver):
        worker.log_signal.emit("Dismissed cookie-consent screen...", "active")
        time.sleep(1.0)
        if "consent.google.com" in driver.current_url:
            driver.get(search_url)
            time.sleep(1.0)
            _dismiss_consent_screen(driver)
            time.sleep(1.0)

    reason = _page_blocked_reason(driver)
    if reason:
        _debug_dump(driver, f"blocked_{domain}")
        worker.log_signal.emit(f'"{domain}" — {reason} See debug/ folder.', "done")
        return 0, True

    try:
        WebDriverWait(driver, 12).until(lambda d: _get_feed(d) is not None)
    except Exception:
        reason = _page_blocked_reason(driver)
        _debug_dump(driver, f"nofeed_{domain}")
        msg = reason or (
            "no results feed appeared. Saved debug/nofeed_*.png and .html — "
            "check what Chrome actually loaded."
        )
        worker.log_signal.emit(f'"{domain}" — {msg}', "done")
        return 0, True

    # Hours/Reviews always need a detail page. Phone/Website need one only when
    # the card omits them (common for cafes/restaurants, rare for services).
    forced_detail = [f for f in fields if f in DETAIL_ONLY_FIELDS]
    fallback_fields = [f for f in ("phone", "website") if f in fields]
    target = max_results if max_results > 0 else 10 ** 9

    collected = []      # every unique non-sponsored record: (record, href)
    pending = []        # records still needing a detail-page visit
    seen = set()
    emitted = 0

    def emit(record: dict) -> None:
        nonlocal emitted
        emitted += 1
        worker.result_signal.emit(record)
        worker.progress_signal.emit(worker._progress_base + emitted)
        name = (record.get("name") or "Unknown")[:50]
        worker.log_signal.emit(f"#{worker._progress_base + emitted}  {name}", "done")

    def needs_detail(record: dict) -> bool:
        if forced_detail:
            return True
        return any(not record.get(f) for f in fallback_fields)

    worker.log_signal.emit(f'Scanning "{domain} in {area}"...', "active")

    stall = 0
    max_stall = 8
    last_growth = time.time()
    no_growth_timeout = 90
    ended = False

    while worker._running and len(collected) < target:
        worker._wait_if_paused()
        if not worker._running:
            return len(collected), False

        cards = parse_feed_html(_feed_html(driver))
        new_this_round = 0
        for card in cards:
            if card.get("_sponsored"):
                continue
            key = card.get("_key")
            if not key or key in seen:
                continue
            seen.add(key)
            record = _card_to_record(card, fields, domain, area)
            href = card.get("_href", "")
            collected.append((record, href))
            new_this_round += 1
            if needs_detail(record):
                pending.append((record, href))
            else:
                emit(record)  # card is complete — stream it live
            if len(collected) >= target:
                break

        if len(collected) >= target:
            break

        if new_this_round:
            last_growth = time.time()
            stall = 0
        elif collected:
            stall += 1
            worker.log_signal.emit(f'Found {len(collected)} businesses so far...', "active")
        else:
            stall += 1

        if _is_end_of_list(driver) and new_this_round == 0:
            ended = True
            break

        if stall >= max_stall or time.time() - last_growth > no_growth_timeout:
            ended = True
            break

        feed = _get_feed(driver)
        if feed is None:
            driver.get(search_url)
            time.sleep(T_SEARCH_LOAD)
            continue
        _scroll_feed_step(driver, feed)

    if len(collected) == 0:
        _debug_dump(driver, f"noresults_{domain}")
        worker.log_signal.emit(
            f'"{domain}" — no listings found in the results feed. See debug/ folder.', "done",
        )
        return 0, True

    if not worker._running:
        return len(collected), False

    # Detail pass — only for records the feed couldn't fully cover.
    if pending:
        want = list(dict.fromkeys(
            forced_detail + fallback_fields + [f for f in UPGRADEABLE_FIELDS if f in fields]
        ))
        total = len(pending)
        for idx, (record, href) in enumerate(pending, 1):
            if not worker._running:
                break
            worker._wait_if_paused()
            worker.log_signal.emit(
                f'Fetching details {idx}/{total}: {record.get("name", "")[:40]}', "active",
            )
            detail = _extract_detail(driver, href, want)
            for f in want:
                if detail.get(f) and (
                    f in forced_detail or f in UPGRADEABLE_FIELDS or not record.get(f)
                ):
                    record[f] = detail[f]
            emit(record)

    completed = ended or (max_results > 0 and len(collected) >= max_results)
    return len(collected), completed


# ── Qt worker thread (interface unchanged) ───────────────────────────────────
class ScrapeWorker(QThread):
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    result_signal = pyqtSignal(dict)
    domain_finished_signal = pyqtSignal(str, int, int, bool)
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
        use_tabs: bool = True,  # kept for backward compatibility (unused)
    ):
        super().__init__()
        self.domains = domains
        self.area = area
        self.fields = fields
        self.headless = headless
        self.max_results = max(0, max_results)
        self.use_tabs = use_tabs
        self._running = True
        self._paused = False
        self._pause_lock = threading.Event()
        self._pause_lock.set()
        self._continue_event = threading.Event()
        self._continue_event.set()
        self._total_collected = 0
        self._progress_base = 0

    def continue_next_domain(self):
        self._continue_event.set()

    def stop(self):
        self._running = False
        self._pause_lock.set()
        self._continue_event.set()

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
            multi_domain = len(self.domains) > 1

            for domain in self.domains:
                if not self._running:
                    break

                if multi_domain:
                    limit = self.max_results
                elif self.max_results > 0:
                    limit = self.max_results - self._total_collected
                    if limit <= 0:
                        break
                else:
                    limit = 0

                self._progress_base = 0 if multi_domain else self._total_collected

                count, completed = scrape_domain_progressive(
                    driver, domain, self.area, self.fields, self, max_results=limit,
                )
                self._total_collected += count

                if not self._running:
                    break

                if not multi_domain:
                    if self.max_results > 0 and self._total_collected >= self.max_results:
                        break
                    continue

                if not completed and count:
                    self.log_signal.emit(f'"{domain}" stopped early — {count} collected', "done")

                if count == 0:
                    self.log_signal.emit(f'No results for "{domain}"', "done")
                elif self.max_results > 0 and count >= self.max_results:
                    self.log_signal.emit(f'"{domain}" done — {count} of {self.max_results}', "done")
                else:
                    self.log_signal.emit(f'"{domain}" done — {count} (no more results)', "done")

                self._continue_event.clear()
                hit_limit = self.max_results > 0 and count >= self.max_results
                self.domain_finished_signal.emit(domain, count, self.max_results, hit_limit)
                while self._running and not self._continue_event.wait(timeout=0.2):
                    pass

        except Exception as e:
            self.error_signal.emit(str(e))

        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            self.done_signal.emit()
