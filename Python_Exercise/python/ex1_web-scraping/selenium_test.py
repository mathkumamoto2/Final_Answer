
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

url = "https://r.gnavi.co.jp/area/kumamoto/rs/"

options = webdriver.ChromeOptions()
options.add_argument("--user-agent=ExerciseForPool-Student/1.0")

driver = webdriver.Chrome(options=options)

try:
    # 最初のページを開く
    time.sleep(3)
    driver.get(url)

    print("ChromeDriver:", driver.service.path)
    print("現在のURL:", driver.current_url)

    # 2ページ目へ移動するリンクを探す
    next_button = WebDriverWait(driver, 20).until(
        lambda d: next(
            (
                element
                for element in d.find_elements(
                    By.CSS_SELECTOR,
                    'a[href*="p=2"]'
                )
                if element.is_displayed()
            ),
            None
        )
    )

    # ページ下部のボタンへ移動する
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        next_button
    )

    # クリックして2ページ目へ
    time.sleep(3)
    next_button.click()

    WebDriverWait(driver, 20).until(
        lambda d: "p=2" in d.current_url
    )

    print("移動後のURL:", driver.current_url)
    print("ページ送り成功")

finally:
    driver.quit()
