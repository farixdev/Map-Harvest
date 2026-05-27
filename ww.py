import os
import csv
import time
import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QLineEdit, QPushButton,
                             QTextEdit, QMessageBox)
from PyQt5.QtGui import QIcon, QTextCursor
from PyQt5.QtCore import QThread, pyqtSignal
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options


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

            with open("Domains.txt", 'r', encoding='utf-8') as f:
                Domains = [line.strip() for line in f if line.strip()]

            for domain in Domains:
                if self._abort:
                    break

                search_query = f"{domain} in {self.area}"
                fileCSV = os.path.join(area_folder, f"{domain}.csv")
                self.log(f"\n🔍 Searching: {search_query}")

                driver.get("https://www.google.com/maps")
                time.sleep(5)
                search_box = driver.find_element(By.ID, "searchboxinput")
                search_box.clear()
                search_box.send_keys(search_query)
                search_box.send_keys(Keys.ENTER)
                time.sleep(5)

                scrollable_xpath = '//div[contains(@aria-label, "Results for") or contains(@class, "Nv2PK")]'
                last_count = 0

                for _ in range(70):
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
                self.log(f"📦 Found {len(business_cards)} businesses for: {domain}")

                with open(fileCSV, "w", newline='', encoding='utf-8') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(["Business Index", "Business Name", "Map Link", "Phone Number", "Claimed"])

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

                            claimed = "Yes"
                            try:
                                if driver.find_element(By.XPATH, "//*[contains(text(), 'Claim this business')]"):
                                    claimed = "No"
                            except:
                                pass

                            phone = ""
                            try:
                                phone_el = driver.find_element(By.XPATH, '//button[contains(@aria-label, "Phone:") or contains(@data-item-id, "phone:")]')
                                phone = phone_el.get_attribute("aria-label").replace("Phone: ", "").strip()
                            except:
                                phone = "Not found"
                            
                            color = "green" if claimed == "Yes" else "red"
                            claimed_html = f'<span style="color:{color}; font-weight:bold;">{claimed}</span>'
                            self.log(f"[{index + 1}] {name} | Phone: {phone} | Claimed: {claimed_html}")
                            if claimed == "No":
                                writer.writerow([index + 1, name, link, phone, claimed])

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

                self.log(f"\n✅ Done scraping for domain: {domain}\n📁 Saved to: {fileCSV}")

            driver.quit()
        except Exception as e:
            self.log(f"❌ Critical error: {e}")

        self.finished.emit()

    def stop(self):
        self._abort = True

    def log(self, message):
        self.log_signal.emit(str(message))


from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PyQt5.QtCore import Qt

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


class DomainEditor(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Domains")
        self.setFixedSize(400, 300)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 6px;
            }
            QTextEdit {
                background-color: #121212;
                color: #00ffcc;
                border: 1px solid #333;
                font-family: Consolas;
                font-size: 13px;
            }
            QPushButton {
                background-color: #007acc;
                color: white;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005fa3;
            }
        """)

        layout = QVBoxLayout()
        self.text_edit = QTextEdit(self)
        layout.addWidget(self.text_edit)

        save_button = QPushButton("Save", self)
        save_button.clicked.connect(self.save_domains)
        layout.addWidget(save_button)

        self.setLayout(layout)
        self.load_domains()

    def load_domains(self):
        try:
            with open("Domains.txt", 'r', encoding='utf-8') as f:
                self.text_edit.setText(f.read())
        except Exception as e:
            self.text_edit.setText("")

    def save_domains(self):
        try:
            with open("Domains.txt", 'w', encoding='utf-8') as f:
                f.write(self.text_edit.toPlainText())
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save domains: {e}")




class MapScraperGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setGeometry(800, 200, 600, 600)
        self.setWindowTitle("Claimed Business Scraper")
        self.setWindowIcon(QIcon("app_icon.png"))
        self.init_ui()
        self.worker = None

    def open_domain_editor(self):
        editor = DomainEditor(self)
        editor.exec_()


    def init_ui(self):
        self.label = QLabel("Enter Area:", self)
        self.label.move(40, 40)

        self.area_input = QLineEdit(self)
        self.area_input.setGeometry(150, 35, 300, 30)

        self.start_button = QPushButton("Start", self)
        self.start_button.setGeometry(150, 80, 100, 40)
        self.start_button.clicked.connect(self.toggle_scraping)

        self.edit_domains_button = QPushButton("Edit Domains", self)
        self.edit_domains_button.setGeometry(270, 80, 120, 40)
        self.edit_domains_button.clicked.connect(self.open_domain_editor)


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
        background-color: #1e1e1e;
        width: 12px;
        margin: 0px;
        border-radius: 6px;
    }

    QScrollBar::handle:vertical {
        background-color: #3a3a3a;
        min-height: 20px;
        border-radius: 6px;
    }

    QScrollBar::handle:vertical:hover {
        background-color: #555555;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0px;
        subcontrol-origin: margin;
    }

    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {
        background: none;
    }

    QScrollBar:horizontal {
        border: none;
        background-color: #1e1e1e;
        height: 12px;
        margin: 0px;
        border-radius: 6px;
    }

    QScrollBar::handle:horizontal {
        background-color: #3a3a3a;
        min-width: 20px;
        border-radius: 6px;
    }

    QScrollBar::handle:horizontal:hover {
        background-color: #555555;
    }

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        width: 0px;
        subcontrol-origin: margin;
    }

    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {
        background: none;
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
    app = QApplication(sys.argv)
    window = MapScraperGUI()
    window.show()
    sys.exit(app.exec_())
