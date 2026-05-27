import os
import csv
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- Read domains from file ---
with open("Domains.txt", 'r', encoding='utf-8') as f:
    Domains = [line.strip() for line in f if line.strip()]

# --- Set up Chrome driver ---
options = Options()
options.add_argument("--headless")
driver = webdriver.Chrome(service=Service(), options=options)
original_window = driver.current_window_handle

# --- Loop through each domain ---
for domain in Domains:
    search_query = f"{domain} in West Vancouver"
    fileCSV = f"{domain}.csv"

    print(f"\nSearching: {search_query}")

    driver.get("https://www.google.com/maps")
    time.sleep(5)

    # Enter search query
    search_box = driver.find_element(By.ID, "searchboxinput")
    search_box.clear()
    search_box.send_keys(search_query)
    search_box.send_keys(Keys.ENTER)
    time.sleep(5)

    # --- Scroll until all results load ---
    scrollable_xpath = '//div[contains(@aria-label, "Results for") or contains(@class, "m6QErb DxyBCb kA9KIf dS8AEf ecceSd") or contains(@class, "Nv2PK")]'
    last_count = 0

    for _ in range(30):
        try:
            results_box = driver.find_element(By.XPATH, scrollable_xpath)
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", results_box)
            time.sleep(3)
            cards = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
            if len(cards) == last_count:
                break
            last_count = len(cards)
        except Exception as e:
            print("Scrolling failed:", e)
            break

    business_cards = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
    print(f"Found {len(business_cards)} businesses for: {domain}")

    # --- Open CSV file for current domain ---
    with open(fileCSV, "w", newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Business Index", "Business Name", "Map Link", "Phone Number", "Claimed"])

        processed = set()
        index = 0

        while True:
            try:
                business_cards = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
                if index >= len(business_cards):
                    break

                card = business_cards[index]
                business_name = card.get_attribute("aria-label")
                business_link = card.get_attribute("href")

                if not business_link or business_link in processed:
                    index += 1
                    continue

                processed.add(business_link)

                driver.execute_script("window.open(arguments[0]);", business_link)
                time.sleep(3)
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(5)

                # Check if business is claimed
                claimed = "No"
                try:
                    driver.find_element(By.CLASS_NAME, "AeaXub")
                    claimed = "Yes"
                except:
                    pass

                # Extract phone number
                phone = ""
                try:
                    phone_element = driver.find_element(By.XPATH, '//button[contains(@aria-label, "Phone:") or contains(@data-item-id, "phone:")]')
                    phone = phone_element.get_attribute("aria-label").replace("Phone: ", "").strip()
                except:
                    phone = "Not found"

                print(f"[{index + 1}] {business_name} | Phone: {phone} | Claimed: {claimed}")
                writer.writerow([index + 1, business_name, business_link, phone, claimed])

                driver.close()
                driver.switch_to.window(original_window)
                time.sleep(2)
                index += 1

            except Exception as e:
                print(f"Error at business #{index + 1}:", e)
                for handle in driver.window_handles:
                    if handle != original_window:
                        driver.switch_to.window(handle)
                        driver.close()
                driver.switch_to.window(original_window)
                index += 1
                continue

        print(f"\n✅ Done scraping for domain: {domain}")
        print(f"📁 Saved to: {fileCSV}")

driver.quit()
print("\n🎉 All domains completed.")
