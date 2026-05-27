import os
import csv
import time
import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QLineEdit, QPushButton,
                             QTextEdit, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout)
from PyQt5.QtGui import QIcon, QTextCursor
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC




class Worker(QThread):
    log_signal = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, area):
        super().__init__()
        self.area = area
        self._abort = False

    def run(self):
        try:
            options = Options()
            options.add_argument("--headless")
            driver = webdriver.Chrome(service=Service(), options=options)
            original_window = driver.current_window_handle

            area_folder = self.area.replace(" ", "_")
            os.makedirs(area_folder, exist_ok=True)

            fileCSV = os.path.join(area_folder, f"{self.area}.csv")
            self.log(f"\n🔍 Searching address: {self.area}")

# Step 1: Go to Google Maps
            driver.get("https://www.google.com/maps")
            time.sleep(5)

            # Step 2: Search for the address
            search_box = driver.find_element(By.ID, "searchboxinput")
            search_box.clear()
            search_box.send_keys(self.area)
            search_box.send_keys(Keys.ENTER)
            time.sleep(5)

            # Step 3: Click the "Nearby" button
            nearby_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Nearby']"))
            )
            nearby_button.click()
            time.sleep(2)

            # Step 4: Enter "businesses" into the nearby search box
            nearby_input = driver.find_element(By.ID, "searchboxinput")
            nearby_input.clear()
            nearby_input.send_keys("businesses")
            nearby_input.send_keys(Keys.ENTER)
            time.sleep(5)

            scrollable_xpath = '//div[contains(@aria-label, "Results for") or contains(@class, "Nv2PK")]'
            last_count = 0

            for _ in range(4):
                if self._abort:
                    break
                try:
                    results_box = driver.find_element(By.XPATH, scrollable_xpath)
                    driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", results_box)
                    time.sleep(3)
                    cards = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
                    if len(cards) == last_count:
                        break
                    last_count = len(cards)
                except Exception as e:
                    self.log(f"❌ Scrolling failed: {e}")
                    break

            business_cards = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
            self.log(f"Found {len(business_cards)} businesses in {self.area}")

            with open(fileCSV, "w", newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["Business Index", "Business Name", "Map Link", "Phone Number", "Website", "Address"])

                processed = set()
                index = 0
                while index < len(business_cards):
                    if self._abort:
                        break
                    try:
                        business_cards = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
                        card = business_cards[index]
                        name = card.get_attribute("aria-label")
                        link = card.get_attribute("href")

                        if not link or link in processed:
                            index += 1
                            continue

                        processed.add(link)
                        driver.execute_script("window.open(arguments[0]);", link)
                        time.sleep(3)
                        driver.switch_to.window(driver.window_handles[-1])
                        time.sleep(5)

                        

                        phone = ""
                        try:
                            phone_el = driver.find_element(By.XPATH, '//button[contains(@aria-label, "Phone:") or contains(@data-item-id, "phone:")]')
                            phone = phone_el.get_attribute("aria-label").replace("Phone: ", "").strip()
                        except:
                            phone = "Not found"

                        
                        try:
                            website_el = driver.find_element(By.XPATH, '//a[contains(@data-item-id, "authority")]')
                            website = website_el.get_attribute("href").strip()
                        except:
                            website = "Not found"
                        
                        try:
                            address_el = driver.find_element(By.XPATH, '//button[contains(@aria-label, "Address:")]')
                            address = address_el.get_attribute("aria-label").replace("Address: ", "").strip()
                        except:
                            address = "Not found"
                        
                        
    
                        self.log(f"[{index + 1}] {name} | Phone: {phone} | Website: {website} | Address: {address}")
                 
                        writer.writerow([index + 1, name, link, phone, website, address])

                        driver.close()
                        driver.switch_to.window(original_window)
                        index += 1
                        time.sleep(2)
                    except Exception as e:
                        self.log(f"⚠️ Error at business #{index + 1}: {e}")
                        for handle in driver.window_handles:
                            if handle != original_window:
                                driver.switch_to.window(handle)
                                driver.close()
                        driver.switch_to.window(original_window)
                        index += 1

            self.log(f"\n✅ Done scraping for area: {self.area}\n📁 Saved to: {fileCSV}")
            driver.quit()
        except Exception as e:
            self.log(f"❌ Critical error: {e}")

        self.finished.emit()

    def stop(self):
        self._abort = True

    def log(self, message):
        self.log_signal.emit(str(message))


class CustomExitDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm Exit")
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedSize(320, 140)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 8px;
            }
            QLabel {
                color: white;
                font-size: 14px;
            }
            QPushButton {
                padding: 6px 12px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton#yes {
                background-color: #cc3333;
                color: white;
            }
            QPushButton#yes:hover {
                background-color: #a80000;
            }
            QPushButton#no {
                background-color: #444;
                color: white;
            }
            QPushButton#no:hover {
                background-color: #666;
            }
        """)

        layout = QVBoxLayout()
        label = QLabel("Are you sure you want to exit?")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        buttons = QHBoxLayout()
        self.yes_button = QPushButton("Yes")
        self.yes_button.setObjectName("yes")
        self.no_button = QPushButton("No")
        self.no_button.setObjectName("no")
        buttons.addStretch()
        buttons.addWidget(self.yes_button)
        buttons.addWidget(self.no_button)
        buttons.addStretch()

        layout.addLayout(buttons)
        self.setLayout(layout)


class MapScraperGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setGeometry(800, 200, 600, 600)
        self.setWindowTitle("gm Scrapper")
        self.setWindowIcon(QIcon("app_icon.png"))
        self.init_ui()
        self.worker = None

    def init_ui(self):
        self.label = QLabel("Enter Area:", self)
        self.label.move(40, 40)

        self.area_input = QLineEdit(self)
        self.area_input.setGeometry(150, 35, 300, 30)

        self.start_button = QPushButton("Start", self)
        self.start_button.setGeometry(250, 80, 100, 40)
        self.start_button.clicked.connect(self.toggle_scraping)

        self.log_output = QTextEdit(self)
        self.log_output.setGeometry(40, 140, 520, 400)
        self.log_output.setReadOnly(True)

        self.setStyleSheet("""
    QMainWindow {
        background-color: #121212;
        color: #ffffff;
        font-family: 'Segoe UI';
        font-size: 14px;
    }

    QLabel {
        color: #ffffff;
    }

    QLineEdit {
        background-color: #1e1e1e;
        border: 1px solid #3a3a3a;
        border-radius: 4px;
        color: #ffffff;
        padding: 6px;
    }

    QTextEdit {
        background-color: #1e1e1e;
        border: 1px solid #3a3a3a;
        border-radius: 4px;
        color: #00ffcc;
        font-family: Consolas, monospace;
        font-size: 13px;
    }

    QPushButton {
        background-color: #007acc;
        color: white;
        border-radius: 6px;
        padding: 6px 12px;
        font-weight: bold;
    }

    QPushButton:hover {
        background-color: #005fa3;
    }

    QPushButton:pressed {
        background-color: #004b82;
    }

    QPushButton:disabled {
        background-color: #3a3a3a;
        color: #888888;
        border: 1px solid #555;
    }
                           
    QScrollBar:vertical {
    border: none;
    background: #171717;
    width: 10px;
    margin: 0px 0px 0px 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #292929;
    min-height: 20px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #888;
}

QScrollBar::handle:vertical:pressed {
    background: #aaa;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    subcontrol-origin: margin;
}

QScrollBar:horizontal {
    border: none;
    background: #1e1e1e;
    height: 10px;
    margin: 0px 0px 0px 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background: #555;
    min-width: 20px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #888;
}

QScrollBar::handle:horizontal:pressed {
    background: #aaa;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    subcontrol-origin: margin;
}

""")

    def toggle_scraping(self):
        if self.worker and self.worker.isRunning():
            self.start_button.setText("Stopping...")
            self.worker.stop()
            return

        area = self.area_input.text().strip()
        if not area:
            QMessageBox.warning(self, "Missing Area", "Please enter an area to scrape.")
            return

        self.log_output.clear()
        self.worker = Worker(area)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.start_button.setText("Stop")

    def append_log(self, text):
        self.log_output.insertHtml(text + "<br>")
        self.log_output.moveCursor(QTextCursor.End)

    def on_finished(self):
        self.start_button.setText("Start")
        QMessageBox.information(self, "Done", "Scraping completed.")

    def closeEvent(self, event):
        dialog = CustomExitDialog(self)
        dialog.yes_button.clicked.connect(dialog.accept)
        dialog.no_button.clicked.connect(dialog.reject)

        if dialog.exec_() == QDialog.Accepted:
            if self.worker and self.worker.isRunning():
                print("🛑 Stopping background thread...")
                self.worker.stop()
                self.worker.wait()
            event.accept()
        else:
            event.ignore()


if __name__ == '__main__':
    from mapharvest.ui.app import run
    run()

