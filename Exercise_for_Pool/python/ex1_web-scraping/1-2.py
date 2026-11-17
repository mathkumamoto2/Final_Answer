"""課題1-2：Seleniumでぐるなびの50店舗を収集する。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.gnavi_rules import extract_direct_email, extract_site_candidates
from shared.site_verifier import URLVerifier, resolve_site

START_URL = "https://r.gnavi.co.jp/area/kumamoto/rs/"
COLUMNS = ["店舗名", "電話番号", "メールアドレス", "都道府県", "市区町村", "番地", "建物名", "URL", "SSL"]
PREFECTURES = "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
PREF_RE = re.compile("^(?:" + "|".join(map(re.escape, PREFECTURES)) + ")")
CITY_RE = re.compile(r"^(.+?市.+?区|.+?郡.+?[町村]|.+?[市区町村])")
SHOP_RE = re.compile(r"^/(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{6,}/?$")
PHONE_RE = re.compile(r"0\d{1,4}[-－]?\d{1,4}[-－]?\d{3,4}")
CHALLENGE_RE = re.compile(r"captcha|access denied|verify you are human|ロボットではない|アクセスが制限", re.I)
WAIT_SECONDS = 3.0
USER_AGENT = "ExerciseForPool-Student/1.0"
PAGE_LOAD_TIMEOUT = 45
WEBDRIVER_COMMAND_TIMEOUT = 60
SCRIPT_TIMEOUT = 15


class PageNavigationTimeout(Exception):
    """ページ読み込み期限を超過したときの例外。再試行は行わない。"""


class DriverCommunicationError(RuntimeError):
    """ChromeDriverとのローカル通信が成立しない場合の致命的エラー。"""


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def valid_public_url(value: str) -> bool:
    return URLVerifier.is_public_url(value)


def is_gnavi(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host in {"gnavi.co.jp", "gurunavi.com"} or host.endswith((".gnavi.co.jp", ".gurunavi.com"))


def split_address(value: str) -> tuple[str, str, str, str]:
    address = re.sub(r"^〒?\s*\d{3}-?\d{4}\s*", "", clean(value))
    address = re.split(r"大きな地図で見る|地図印刷|Googleマップ", address)[0].strip()
    pref = city = building = ""
    match = PREF_RE.match(address)
    if match:
        pref, address = match.group(), address[match.end():].strip()
    match = CITY_RE.match(address)
    if match:
        city, address = match.group(), address[match.end():].strip()
    town_match = re.match(r"^([^0-9０-９]+)(?=[0-9０-９])", address)
    if town_match:
        city += town_match.group(1).strip()
        address = address[town_match.end():].strip()
    separated = re.match(r"^(?P<block>.+?\d(?:[-－−]\d+)*)\s+(?P<building>.+)$", address)
    if separated:
        return pref, city, separated["block"], separated["building"]
    floor = re.match(r"^(?P<block>.+\d)(?P<building>B?\d+(?:[〜～~-]\d+)?F)$", address, re.I)
    if floor:
        return pref, city, floor["block"], floor["building"]
    joined = re.match(r"^(?P<block>.+?\d+(?:[-－−]\d+)*)(?P<building>[A-Za-zぁ-んァ-ヶ一-龥].+)$", address)
    if joined:
        return pref, city, joined["block"], joined["building"]
    return pref, city, address, building


def table_value(soup: BeautifulSoup, label: str) -> str:
    for node in soup.select("th, dt"):
        if clean(node.get_text(" ", strip=True)) == label:
            sibling = node.find_next_sibling("td" if node.name == "th" else "dd")
            if sibling:
                return clean(sibling.get_text(" ", strip=True))
    return ""


def restaurant_jsonld(soup: BeautifulSoup) -> dict:
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            yield value
            if "@graph" in value:
                yield from walk(value["@graph"])
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in walk(data):
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if isinstance(kinds, list) and any(str(k).rsplit("/", 1)[-1] in {"Restaurant", "FoodEstablishment"} for k in kinds):
                return node
    return {}


def listing_urls(html: str, base_url: str) -> list[str]:
    seen, urls = set(), []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        u = urljoin(base_url, a.get("href", ""))
        parts = urlsplit(u)
        if parts.hostname != "r.gnavi.co.jp" or not SHOP_RE.fullmatch(parts.path):
            continue
        u = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        if u not in seen:
            urls.append(u)
            seen.add(u)
    return urls


def parse_store(html: str, base_url: str) -> tuple[dict[str, str], list[str]] | None:
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if CHALLENGE_RE.search(title):
        raise RuntimeError("アクセス制限が表示されました。処理を中止します。")
    ld = restaurant_jsonld(soup)
    h1 = soup.select_one("h1")
    name = clean(ld.get("name") or (h1.get_text(" ", strip=True) if h1 else ""))
    if not name:
        return None
    phone = clean(ld.get("telephone"))
    if not phone:
        a = soup.select_one('a[href^="tel:"]')
        phone = clean(a["href"][4:]) if a else ""
    if not phone:
        match = PHONE_RE.search(table_value(soup, "電話番号") or soup.get_text(" ", strip=True))
        phone = match.group() if match else ""
    email = extract_direct_email(html)
    address = table_value(soup, "住所")
    if not address:
        structured = ld.get("address", "")
        address = (" ".join(clean(structured.get(k)) for k in ("addressRegion", "addressLocality", "streetAddress"))
                   if isinstance(structured, dict) else clean(structured))
    if not address:
        a = soup.select_one('[itemprop="streetAddress"], address')
        address = a.get_text(" ", strip=True) if a else ""
    pref, city, block, building = split_address(address)
    if not pref or not city or not block:
        return None
    candidates = extract_site_candidates(html, base_url)
    record = dict(zip(COLUMNS, (name, phone, email, pref, city, block, building, "", "")))
    return record, candidates


def navigate(driver: webdriver.Chrome, url: str, verifier: URLVerifier) -> str:
    """robots.txtを確認し、3秒待機してぐるなびのページを開く。"""
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    if not valid_public_url(url) or not is_gnavi(url):
        raise ValueError("ぐるなびの公開URLではありません: {}".format(url))

    decision = verifier.check_robots(url)
    if not decision.allowed:
        raise PermissionError(
            "{}: {}".format(decision.status, decision.reason)
        )

    time.sleep(WAIT_SECONDS)
    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            lambda current: current.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException as exc:
        # ページ読み込みだけを停止する。CAPTCHA等の回避や再試行はしない。
        try:
            driver.execute_script("window.stop();")
        except Exception as stop_exc:
            raise DriverCommunicationError(
                "ページ読み込みタイムアウト後、ChromeDriverが応答しません: {}".format(url)
            ) from stop_exc
        raise PageNavigationTimeout(
            "ページ読み込みが{}秒を超過しました: {}".format(PAGE_LOAD_TIMEOUT, url)
        ) from exc
    except Exception as exc:
        # urllib3 ReadTimeoutError等もここで明示的に致命エラーへ変換する。
        raise DriverCommunicationError(
            "ChromeDriverとの通信に失敗しました。プロセス停止/Nortonの検出履歴を確認してください: {} ({})".format(
                url, type(exc).__name__
            )
        ) from exc

    actual_url = driver.current_url
    if not valid_public_url(actual_url) or not is_gnavi(actual_url):
        raise RuntimeError(
            "ぐるなび外へ転送されたため停止: {}".format(actual_url)
        )
    final_decision = verifier.check_robots(actual_url)
    if not final_decision.allowed:
        raise PermissionError(
            "転送先のrobots.txtを確認できません: {}".format(final_decision.reason)
        )

    title = driver.title or ""
    source = driver.page_source or ""
    if CHALLENGE_RE.search(title + " " + source[:8192]):
        raise RuntimeError("CAPTCHA/アクセス制限を検知しました: {}".format(actual_url))
    return source


def page_number(url: str) -> int:
    value = parse_qs(urlsplit(url).query).get("p", ["1"])[0]
    return int(value) if value.isdigit() else 1


def click_next(driver: webdriver.Chrome, current_page: int, verifier: URLVerifier) -> bool:
    """robots.txtを確認し、一覧下部の「>」ボタンをクリックする。"""
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        StaleElementReferenceException,
        TimeoutException,
    )
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    current_path = urlsplit(driver.current_url).path.rstrip("/")
    candidates = []
    for anchor in driver.find_elements(By.CSS_SELECTOR, 'a[href*="p="]'):
        href = anchor.get_attribute("href") or ""
        parts = urlsplit(href)
        if parts.path.rstrip("/") != current_path or page_number(href) != current_page + 1:
            continue
        if not anchor.is_displayed():
            continue
        label = (
            anchor.text
            + " "
            + (anchor.get_attribute("aria-label") or "")
            + " "
            + (anchor.get_attribute("title") or "")
        ).strip()
        is_arrow = bool(re.search(r"次|next|[>＞›»→]", label, re.I)) or "next" in (anchor.get_attribute("rel") or "").lower()
        candidates.append((is_arrow, anchor, href))

    if not candidates:
        return False

    selected = next(
        (item for item in reversed(candidates) if item[0]),
        candidates[-1],
    )
    next_url = selected[2]
    if not is_gnavi(next_url):
        raise RuntimeError("次ページURLがぐるなび外です: {}".format(next_url))
    decision = verifier.check_robots(next_url)
    if not decision.allowed:
        raise PermissionError(
            "次ページのrobots.txt確認に失敗: {}".format(decision.reason)
        )

    next_anchor = selected[1]
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_anchor)
    time.sleep(WAIT_SECONDS)
    try:
        next_anchor.click()
    except (ElementClickInterceptedException, StaleElementReferenceException) as exc:
        raise RuntimeError("次ページボタンをクリックできませんでした") from exc

    try:
        WebDriverWait(driver, 20).until(
            lambda current: page_number(current.current_url) == current_page + 1
        )
    except TimeoutException as exc:
        try:
            driver.execute_script("window.stop();")
        except Exception as stop_exc:
            raise DriverCommunicationError(
                "次ページ遷移後にChromeDriverが応答しません。"
            ) from stop_exc
        raise PageNavigationTimeout(
            "次ページへの遷移を20秒以内に確認できませんでした。"
        ) from exc
    return True


def create_driver() -> webdriver.Chrome:
    """Start Chrome using the explicitly bundled, SHA-256-pinned driver.

    Never let Selenium Manager silently obtain or select a different binary.
    The binary and its sha256 sidecar are retrieved from Chrome for Testing
    using DOWNLOAD_CHROMEDRIVER.ps1 and committed together with this source.
    """
    import hashlib
    driver_path = Path(__file__).resolve().with_name("chromedriver.exe")
    digest_path = driver_path.with_suffix(".sha256")
    if not driver_path.is_file():
        raise FileNotFoundError(
            "同梱chromedriver.exeがありません。DOWNLOAD_CHROMEDRIVER.ps1を実行してください: {}".format(driver_path)
        )
    if not digest_path.is_file():
        raise RuntimeError("ChromeDriverの検証ハッシュがありません: {}".format(digest_path))
    expected = digest_path.read_text(encoding="ascii").strip().split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("ChromeDriverのSHA-256ファイルが不正です")
    digest = hashlib.sha256()
    with driver_path.open("rb") as binary:
        for chunk in iter(lambda: binary.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("ChromeDriverのSHA-256が一致しません。実行を中止します")

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.add_argument("--user-agent={}".format(USER_AGENT))
    options.page_load_strategy = "normal"

    # Selenium Service may prioritize SE_CHROMEDRIVER over executable_path.
    # Temporarily remove the override, and restore the caller's environment.
    external_override = os.environ.pop("SE_CHROMEDRIVER", None)
    service = None
    try:
        service = Service(executable_path=str(driver_path))
        actual = os.path.normcase(os.path.realpath(str(service.path)))
        expected_path = os.path.normcase(os.path.realpath(str(driver_path)))
        if actual != expected_path:
            raise RuntimeError("指定外のChromeDriverが選択されました: {}".format(service.path))
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as exc:
        try:
            if service is not None:
                service.stop()
        except Exception:
            pass
        raise RuntimeError(
            "同梱ChromeDriverの起動に失敗しました。ChromeとDriverのバージョン、"
            "セキュリティソフトの警告を確認し、警告時は停止してください"
        ) from exc
    finally:
        if external_override is not None:
            os.environ["SE_CHROMEDRIVER"] = external_override

    try:
        config = getattr(driver.command_executor, "client_config", None)
        if config is not None and hasattr(config, "timeout"):
            config.timeout = WEBDRIVER_COMMAND_TIMEOUT
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.set_script_timeout(SCRIPT_TIMEOUT)
    except Exception:
        safe_quit(driver)
        raise

    print("[INFO] 同梱ChromeDriver:", str(driver_path))
    print("[INFO] SHA-256:", expected)
    return driver


def safe_quit(driver) -> None:
    """ChromeDriverが停止済み/応答不能でも終了処理の二次例外を抑える。"""
    if driver is None:
        return

    service = getattr(driver, "service", None)
    process = getattr(service, "process", None)
    try:
        if process is not None and process.poll() is not None:
            print("[WARN] ChromeDriverプロセスは既に終了しています。")
            return
    except Exception:
        process = None

    try:
        config = getattr(getattr(driver, "command_executor", None), "client_config", None)
        if config is not None and hasattr(config, "timeout"):
            config.timeout = 5
        driver.quit()
        return
    except Exception as exc:
        print("[WARN] driver.quit()に失敗しました: {}".format(type(exc).__name__))

    try:
        if service is not None:
            service.stop()
    except Exception as exc:
        print("[WARN] ChromeDriver service.stop()に失敗しました: {}".format(type(exc).__name__))

    process = getattr(service, "process", None)
    try:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    except Exception as exc:
        print("[WARN] ChromeDriverプロセスの終了確認に失敗しました: {}".format(type(exc).__name__))


def collect(count: int, max_pages: int) -> pd.DataFrame:
    """Seleniumで店舗ページを取得し、URL判定は共有Requests実装に委ねる。"""
    from selenium.common.exceptions import WebDriverException

    verifier = URLVerifier(user_agent=USER_AGENT)
    driver = None
    rows, visited, names = [], set(), set()
    try:
        driver = create_driver()
        navigate(driver, START_URL, verifier)
        list_handle = driver.current_window_handle
        driver.switch_to.new_window("tab")
        detail_handle = driver.current_window_handle
        driver.switch_to.window(list_handle)

        for page in range(1, max_pages + 1):
            if len(rows) >= count:
                break
            urls = listing_urls(driver.page_source, driver.current_url)
            unvisited = [url for url in urls if url not in visited]
            print(
                "[INFO] {}ページ目: 候補{}件（未処理{}件）".format(
                    page, len(urls), len(unvisited)
                )
            )

            for url in unvisited:
                if len(rows) >= count:
                    break
                visited.add(url)
                driver.switch_to.window(detail_handle)
                try:
                    html = navigate(driver, url, verifier)
                    parsed = parse_store(html, driver.current_url)
                    if parsed is None:
                        print("[WARN] 店舗情報不足: {}".format(url))
                        continue

                    record, candidates = parsed
                    if record["店舗名"] in names:
                        continue

                    checked = resolve_site(
                        verifier,
                        candidates,
                        record["店舗名"],
                        record["市区町村"],
                        record["番地"],
                    )
                    record["URL"] = checked.url
                    record["SSL"] = checked.ssl
                    if checked.status not in ("VERIFIED", "NO_LINK"):
                        print(
                            "[WARN] URL検証 {}: {} | URL={} | SSL={} | {}".format(
                                checked.status, record["店舗名"],
                                checked.url, checked.ssl, checked.reason,
                            )
                        )

                    names.add(record["店舗名"])
                    rows.append(record)
                    print("[{}/{}] {}".format(len(rows), count, record["店舗名"]))
                except PageNavigationTimeout as exc:
                    # 読み込み失敗したページは再試行せずスキップする。
                    print("[WARN] 店舗ページの読み込みを中止: {}: {}".format(url, exc))
                except RuntimeError:
                    # CAPTCHA/アクセス制限/Driver通信断は回避せず停止する。
                    raise
                except (WebDriverException, PermissionError, ValueError) as exc:
                    print("[WARN] 店舗ページ取得失敗: {}: {}".format(url, str(exc)[:180]))
                finally:
                    try:
                        driver.switch_to.window(list_handle)
                    except Exception as exc:
                        raise DriverCommunicationError(
                            "一覧タブへ戻れません。ChromeDriverの応答を確認してください。"
                        ) from exc

            if len(rows) >= count:
                break
            if not click_next(driver, page, verifier):
                print("[INFO] 次ページボタンが見つかりません。")
                break

        return pd.DataFrame(rows, columns=COLUMNS)
    finally:
        try:
            verifier.close()
        except Exception as exc:
            print("[WARN] URLVerifierの終了処理に失敗しました: {}".format(type(exc).__name__))
        finally:
            safe_quit(driver)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorized", action="store_true", help="サイトの利用条件と取得許可を確認済みの場合に指定")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("1-2.csv"))
    args = parser.parse_args()
    if not args.authorized:
        parser.error("利用条件と取得許可を確認してから --authorized を指定してください")
    if args.count < 1 or args.max_pages < 1:
        parser.error("--count と --max-pages は1以上を指定してください")
    df = collect(args.count, args.max_pages)
    if len(df) != args.count:
        raise RuntimeError(f"取得数不足: {len(df)}/{args.count}件。CSVは生成しません。")
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"[OK] {args.output.resolve()}: {len(df)}件、{len(df.columns)}列、公式URLあり{df['URL'].ne('').sum()}件")


if __name__ == "__main__":
    main()
