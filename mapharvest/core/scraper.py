import time
import urllib.parse

import undetected_chromedriver as uc
from PyQt5.QtCore import QThread, pyqtSignal
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def get_driver(headless: bool = False):
    options = uc.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    driver = uc.Chrome(options=options)
    driver.set_window_size(1200, 800)
    return driver


def search_google_maps(driver, domain: str, area: str):
    query = f"{domain} in {area}"
    encoded = urllib.parse.quote(query)
    url = f"https://www.google.com/maps/search/{encoded}"
    driver.get(url)
    time.sleep(3.5)


def scroll_to_bottom(driver):
    try:
        feed = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"]'))
        )
    except Exception:
        return

    last_height = 0
    same_count = 0

    while True:
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", feed)
        time.sleep(2)

        # end-of-list message
        try:
            driver.find_element(By.XPATH, "//*[contains(text(), 'You've reached the end')]")
            break
        except Exception:
            pass

        new_height = driver.execute_script("return arguments[0].scrollHeight", feed)

        if new_height == last_height:
            same_count += 1
            if same_count >= 3:
                break
        else:
            same_count = 0

        last_height = new_height


def get_listings(driver) -> list:
    time.sleep(1)

    try:
        all_anchors = driver.find_elements(
            By.CSS_SELECTOR,
            'div[role="feed"] a[href*="google.com/maps/place"]'
        )
    except Exception:
        return []

    results = []
    for anchor in all_anchors:
        try:
            grandparent = anchor.find_element(By.XPATH, "../..")
            text = grandparent.text or ""
            aria = grandparent.get_attribute("aria-label") or ""
            if "Sponsored" in text or "Sponsored" in aria:
                continue
            results.append(anchor)
        except Exception:
            results.append(anchor)

    return results


def extract_business_data(driver, listing_el, fields: list) -> dict:
    data = {}

    try:
        driver.execute_script("arguments[0].click();", listing_el)
        time.sleep(2.5)

        wait = WebDriverWait(driver, 8)

        if "name" in fields:
            try:
                el = wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "h1.DUwDvf, h1.fontHeadlineLarge")
                    )
                )
                data["name"] = el.text.strip()
            except Exception:
                data["name"] = ""

        if "rating" in fields:
            try:
                el = driver.find_element(By.CSS_SELECTOR, "div.F7nice span[aria-hidden='true']")
                data["rating"] = el.text.strip()
            except Exception:
                data["rating"] = ""

        if "address" in fields:
            try:
                el = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[data-item-id='address'] div.Io6YTe, button[data-tooltip='Copy address'] .Io6YTe",
                )
                data["address"] = el.text.strip()
            except Exception:
                data["address"] = ""

        if "website" in fields:
            try:
                el = driver.find_element(
                    By.CSS_SELECTOR,
                    "a[data-item-id='authority'], a[aria-label*='website']",
                )
                data["website"] = el.get_attribute("href") or ""
            except Exception:
                data["website"] = ""

        if "phone" in fields:
            try:
                el = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[data-item-id^='phone'] .Io6YTe, button[data-tooltip='Copy phone number'] .Io6YTe",
                )
                data["phone"] = el.text.strip()
            except Exception:
                data["phone"] = ""

        if "maps_link" in fields:
            data["maps_link"] = driver.current_url

    except Exception as e:
        data["_error"] = str(e)

    return data


class ScrapeWorker(QThread):
    log_signal = pyqtSignal(str, str)  # (message, status)
    progress_signal = pyqtSignal(int, int)
    result_signal = pyqtSignal(dict)
    done_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, domains: list, area: str, fields: list):
        super().__init__()
        self.domains = domains
        self.area = area
        self.fields = fields
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        driver = get_driver(headless=False)
        try:
            for domain in self.domains:
                if not self._running:
                    break

                self.log_signal.emit(f'Searching "{domain} in {self.area}"...', "active")
                search_google_maps(driver, domain, self.area)

                self.log_signal.emit("Scrolling to load all results...", "active")
                scroll_to_bottom(driver)

                listings = get_listings(driver)
                total = len(listings)

                if total == 0:
                    self.log_signal.emit(
                        f'No results found for "{domain} in {self.area}"', "done"
                    )
                    continue

                self.log_signal.emit(f"Found {total} listings (sponsored skipped)", "done")

                for i, listing in enumerate(listings):
                    if not self._running:
                        break

                    self.log_signal.emit(f"Extracting {i + 1} / {total}...", "active")

                    data = extract_business_data(driver, listing, self.fields)
                    data["_domain"] = domain
                    data["_area"] = self.area

                    self.result_signal.emit(data)
                    self.progress_signal.emit(i + 1, total)

                    # go back to results list
                    driver.execute_script("window.history.go(-1)")
                    time.sleep(1.5)

                self.log_signal.emit(
                    f'Done — {total} businesses from "{domain} in {self.area}"', "done"
                )

        except Exception as e:
            self.error_signal.emit(str(e))

        finally:
            try:
                driver.quit()
            except Exception:
                pass
            self.done_signal.emit()

