import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--disable-gpu')
opts.add_argument('--no-sandbox')
try:
    driver = webdriver.Chrome(options=opts)
except Exception as e:
    print('CHROME FAIL', e)
    raise

driver.set_window_size(1200, 900)
search = 'coffee in seattle'
try:
    driver.get('https://www.google.com/maps/search/' + search.replace(' ', '+'))
    time.sleep(10)
    feed = driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')
    for i in range(10):
        cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place"], a[href*="google.com/maps/place"], a.hfpxzc')
        print('iteration', i, 'cards', len(cards), 'scrollHeight', driver.execute_script('return arguments[0].scrollHeight', feed), 'clientHeight', driver.execute_script('return arguments[0].clientHeight', feed), 'scrollTop', driver.execute_script('return arguments[0].scrollTop', feed))
        driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', feed)
        time.sleep(4)
    print('final cards', len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place"], a[href*="google.com/maps/place"], a.hfpxzc')))
finally:
    driver.quit()
