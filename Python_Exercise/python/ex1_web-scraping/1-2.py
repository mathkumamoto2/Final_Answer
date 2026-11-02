
"""課題1-2：Seleniumでぐるなびの50店舗を収集する。"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import (
    parse_qs,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.robotparser import RobotFileParser

os.environ.setdefault("SE_AVOID_STATS", "true")

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


# ==========================================
# 基本設定
# ==========================================

START_URL = "https://r.gnavi.co.jp/area/kumamoto/rs/"
WAIT_SECONDS = 3.0
USER_AGENT = "ExerciseForPool-Student/1.0"

COLUMNS = [
    "店舗名",
    "電話番号",
    "メールアドレス",
    "都道府県",
    "市区町村",
    "番地",
    "建物名",
    "URL",
    "SSL",
]

PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 "
    "茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 "
    "神奈川県 新潟県 富山県 石川県 福井県 山梨県 "
    "長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 "
    "京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 "
    "島根県 岡山県 広島県 山口県 徳島県 香川県 "
    "愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 "
    "大分県 宮崎県 鹿児島県 沖縄県"
).split()

PREF_RE = re.compile(
    "^(?:" + "|".join(map(re.escape, PREFECTURES)) + ")"
)

CITY_RE = re.compile(
    r"^(.+?市.+?区|.+?郡.+?[町村]|.+?[市区町村])"
)

SHOP_RE = re.compile(
    r"^/(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{6,}/?$"
)

OFFICIAL_RE = re.compile(
    r"オフィシャル\s*ページ|"
    r"お店の\s*ホームページ|"
    r"[【〖]?公式[】〗]?\s*"
    r"(?:ホームページ|サイト|Webサイト)"
)

PHONE_RE = re.compile(
    r"0\d{1,4}[-－]?\d{1,4}[-－]?\d{3,4}"
)

CHALLENGE_RE = re.compile(
    r"captcha|access denied|verify you are human|"
    r"ロボットではない|アクセスが制限",
    re.IGNORECASE,
)


# ==========================================
# 文字列処理
# ==========================================

def clean(value: object) -> str:
    """余分な空白や改行を削除する。"""

    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\u3000", " "),
    ).strip()


def valid_public_url(value: str) -> bool:
    """HTTP(S)の公開URLかを簡易判定する。"""

    try:
        parts = urlsplit(value)

        host = (
            parts.hostname or ""
        ).lower().rstrip(".")

        if (
            parts.scheme not in {"http", "https"}
            or not host
        ):
            return False

        if host in {
            "localhost",
            "host.docker.internal",
        }:
            return False

        if host.endswith(
            (".local", ".localhost", ".internal")
        ):
            return False

        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True

    except ValueError:
        return False


def is_gnavi(url: str) -> bool:
    """ぐるなびのドメインか判定する。"""

    host = (
        urlsplit(url).hostname or ""
    ).lower().rstrip(".")

    return (
        host in {"gnavi.co.jp", "gurunavi.com"}
        or host.endswith(
            (".gnavi.co.jp", ".gurunavi.com")
        )
    )


# ==========================================
# 住所の分割
# ==========================================

def split_address(
    value: str,
) -> tuple[str, str, str, str]:
    """住所を都道府県、市区町村、番地、建物名に分割する。"""

    address = re.sub(
        r"^〒?\s*\d{3}-?\d{4}\s*",
        "",
        clean(value),
    )

    address = re.split(
        r"大きな地図で見る|地図印刷|Googleマップ",
        address,
    )[0].strip()

    prefecture = ""
    city = ""
    building = ""

    # 都道府県を取得
    match = PREF_RE.match(address)

    if match:
        prefecture = match.group()
        address = address[match.end():].strip()

    # 市区町村を取得
    match = CITY_RE.match(address)

    if match:
        city = match.group()
        address = address[match.end():].strip()

    # 町名は市区町村側に含める。
    # 例：熊本市中央区下通1-10-3
    # 市区町村：熊本市中央区下通
    # 番地：1-10-3
    town_match = re.match(
        r"^([^0-9０-９]+)(?=[0-9０-９])",
        address,
    )

    if town_match:
        city += town_match.group(1).strip()
        address = address[town_match.end():].strip()

    # 番地と建物名の間に空白がある場合
    separated = re.match(
        r"^(?P<block>.+?\d(?:[-－−]\d+)*)"
        r"\s+(?P<building>.+)$",
        address,
    )

    if separated:
        return (
            prefecture,
            city,
            separated["block"],
            separated["building"],
        )

    # 末尾の階数表記を分離する。
    # 例：1-4-153F -> 1-4-15, 3F
    floor = re.match(
        r"^(?P<block>.+\d)"
        r"(?P<building>B?\d+(?:[〜～~-]\d+)?F)$",
        address,
        re.IGNORECASE,
    )

    if floor:
        return (
            prefecture,
            city,
            floor["block"],
            floor["building"],
        )

    # 建物名が番地と連続している場合
    joined = re.match(
        r"^(?P<block>.+?\d+(?:[-－−]\d+)*)"
        r"(?P<building>[A-Za-zぁ-んァ-ヶ一-龥].+)$",
        address,
    )

    if joined:
        return (
            prefecture,
            city,
            joined["block"],
            joined["building"],
        )

    return prefecture, city, address, building


# ==========================================
# HTML解析
# ==========================================

def table_value(
    soup: BeautifulSoup,
    label: str,
) -> str:
    """店舗情報の表から指定項目を取得する。"""

    for node in soup.select("th, dt"):

        if clean(
            node.get_text(" ", strip=True)
        ) != label:
            continue

        sibling = node.find_next_sibling(
            "td" if node.name == "th" else "dd"
        )

        if sibling:
            return clean(
                sibling.get_text(" ", strip=True)
            )

    return ""


def restaurant_jsonld(soup: BeautifulSoup) -> dict:
    """JSON-LDから店舗情報を取得する。"""

    def walk(value):

        if isinstance(value, list):
            for item in value:
                yield from walk(item)

        elif isinstance(value, dict):
            yield value

            if "@graph" in value:
                yield from walk(value["@graph"])

    scripts = soup.select(
        'script[type="application/ld+json"]'
    )

    for script in scripts:

        try:
            data = json.loads(
                script.string or script.get_text()
            )

        except (json.JSONDecodeError, TypeError):
            continue

        for node in walk(data):

            kinds = node.get("@type", [])

            if isinstance(kinds, str):
                kinds = [kinds]

            if not isinstance(kinds, list):
                continue

            if any(
                str(kind).rsplit("/", 1)[-1]
                in {"Restaurant", "FoodEstablishment"}
                for kind in kinds
            ):
                return node

    return {}


def listing_urls(
    html: str,
    base_url: str,
) -> list[str]:
    """一覧ページから店舗詳細URLを取得する。"""

    soup = BeautifulSoup(html, "html.parser")

    seen = set()
    urls = []

    for anchor in soup.select("a[href]"):

        url = urljoin(
            base_url,
            anchor.get("href", ""),
        )

        parts = urlsplit(url)

        if parts.hostname != "r.gnavi.co.jp":
            continue

        if not SHOP_RE.fullmatch(parts.path):
            continue

        url = urlunsplit((
            parts.scheme,
            parts.netloc,
            parts.path,
            "",
            "",
        ))

        if url not in seen:
            urls.append(url)
            seen.add(url)

    return urls


def parse_store(
    html: str,
    base_url: str,
) -> tuple[dict[str, str], list[str]] | None:
    """店舗詳細ページからデータと公式URL候補を取得する。"""

    soup = BeautifulSoup(html, "html.parser")

    title = (
        clean(soup.title.get_text(" ", strip=True))
        if soup.title
        else ""
    )

    if CHALLENGE_RE.search(title):
        raise RuntimeError(
            "アクセス制限を検知しました。"
        )

    ld = restaurant_jsonld(soup)

    # 店舗名
    h1 = soup.select_one("h1")

    name = clean(
        ld.get("name")
        or (
            h1.get_text(" ", strip=True)
            if h1
            else ""
        )
    )

    if not name:
        return None

    # 電話番号
    phone = clean(ld.get("telephone"))

    if not phone:
        anchor = soup.select_one('a[href^="tel:"]')

        if anchor:
            phone = clean(anchor["href"][4:])

    if not phone:
        source = (
            table_value(soup, "電話番号")
            or soup.get_text(" ", strip=True)
        )

        match = PHONE_RE.search(source)
        phone = match.group() if match else ""

    # メールアドレス
    email = clean(ld.get("email"))

    if not email:
        anchor = soup.select_one(
            'a[href^="mailto:"]'
        )

        if anchor:
            email = clean(
                anchor["href"][7:].split("?", 1)[0]
            )

    # 住所
    address = table_value(soup, "住所")

    if not address:
        structured = ld.get("address", "")

        if isinstance(structured, dict):
            address = " ".join(
                clean(structured.get(key))
                for key in (
                    "addressRegion",
                    "addressLocality",
                    "streetAddress",
                )
            )
        else:
            address = clean(structured)

    if not address:
        node = soup.select_one(
            '[itemprop="streetAddress"], address'
        )

        address = (
            node.get_text(" ", strip=True)
            if node
            else ""
        )

    prefecture, city, block, building = (
        split_address(address)
    )

    if not prefecture or not city or not block:
        return None

    # 公式サイトへのリンク候補を取得
    candidates = []
    seen = set()

    for anchor in soup.select("a[href]"):

        label = clean(" ".join((
            anchor.get_text(" ", strip=True),
            anchor.get("aria-label", ""),
            anchor.get("title", ""),
        )))

        if not OFFICIAL_RE.search(label):
            continue

        raw = anchor.get("href", "").strip()

        # ページ内リンクは公式URLとして採用しない。
        if not raw or raw.startswith("#"):
            continue

        candidate = urljoin(base_url, raw)

        if (
            valid_public_url(candidate)
            and not is_gnavi(candidate)
            and candidate not in seen
        ):
            seen.add(candidate)
            candidates.append(candidate)

    record = dict(zip(
        COLUMNS,
        (
            name,
            phone,
            email,
            prefecture,
            city,
            block,
            building,
            "",
            "",
        ),
    ))

    return record, candidates


# ==========================================
# Seleniumによるアクセス
# ==========================================

def navigate(
    driver: webdriver.Chrome,
    url: str,
) -> str:
    """3秒待機してからページを開く。"""

    if not valid_public_url(url):
        raise ValueError(
            f"不正なURL: {url}"
        )

    time.sleep(WAIT_SECONDS)

    driver.get(url)

    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script(
            "return document.readyState"
        ) == "complete"
    )

    title = driver.title or ""

    if CHALLENGE_RE.search(title):
        raise RuntimeError(
            f"アクセス制限を検知しました: {url}"
        )

    return driver.page_source


# ==========================================
# 公式URLとSSL
# ==========================================

def same_site_redirect(
    candidate: str,
    destination: str,
) -> bool:
    """別ドメインへのリダイレクトを検出する。"""

    original = (
        urlsplit(candidate).hostname or ""
    ).lower().removeprefix("www.")

    actual = (
        urlsplit(destination).hostname or ""
    ).lower().removeprefix("www.")

    return bool(
        original
        and actual
        and (
            actual == original
            or actual.endswith("." + original)
            or original.endswith("." + actual)
        )
    )


def resolve_official(
    driver: webdriver.Chrome,
    candidates: list[str],
) -> tuple[str, str]:
    """公式URLの最終到達先とHTTPSを確認する。"""

    for candidate in candidates:

        try:
            navigate(driver, candidate)

            result = driver.current_url

            if (
                not valid_public_url(result)
                or is_gnavi(result)
            ):
                continue

            title = (driver.title or "").lower()

            if (
                "privacy error" in title
                or "プライバシー エラー" in title
            ):
                print(
                    f"[WARN] 証明書エラー: {candidate}"
                )
                continue

            # 無関係なサイトに転送された場合は除外
            if not same_site_redirect(
                candidate,
                result,
            ):
                print(
                    "[WARN] 別ドメインへの転送を検出: "
                    f"{candidate} -> {result}"
                )
                continue

            ssl = (
                "TRUE"
                if urlsplit(result).scheme == "https"
                else "FALSE"
            )

            return result, ssl

        except (
            WebDriverException,
            RuntimeError,
            ValueError,
        ) as exc:
            print(
                f"[WARN] 公式URL確認失敗: "
                f"{candidate}: {str(exc)[:150]}"
            )

    return "", ""


# ==========================================
# 次ページへの移動
# ==========================================

def page_number(url: str) -> int:
    """URLからページ番号を取得する。"""

    value = parse_qs(
        urlsplit(url).query
    ).get("p", ["1"])[0]

    return int(value) if value.isdigit() else 1


def click_next(
    driver: webdriver.Chrome,
    current_page: int,
) -> bool:
    """次ページへのボタンをSeleniumでクリックする。"""

    current_path = urlsplit(
        driver.current_url
    ).path.rstrip("/")

    candidates = []

    for anchor in driver.find_elements(
        By.CSS_SELECTOR,
        'a[href*="p="]',
    ):
        href = anchor.get_attribute("href") or ""

        parts = urlsplit(href)

        if (
            parts.path.rstrip("/") != current_path
            or page_number(href) != current_page + 1
        ):
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

        is_arrow = bool(
            re.search(
                r"次|next|[>＞›»→]",
                label,
                re.IGNORECASE,
            )
        ) or "next" in (
            anchor.get_attribute("rel") or ""
        ).lower()

        candidates.append(
            (is_arrow, anchor)
        )

    if not candidates:
        return False

    # 「次へ」の表示があるリンクを優先
    next_anchor = next(
        (
            anchor
            for is_arrow, anchor in reversed(candidates)
            if is_arrow
        ),
        candidates[-1][1],
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'});",
        next_anchor,
    )

    time.sleep(WAIT_SECONDS)

    try:
        next_anchor.click()

    except (
        ElementClickInterceptedException,
        StaleElementReferenceException,
    ) as exc:
        raise RuntimeError(
            "次ページボタンをクリックできませんでした。"
        ) from exc

    WebDriverWait(driver, 20).until(
        lambda d: page_number(
            d.current_url
        ) == current_page + 1
    )

    return True


# ==========================================
# 店舗情報の収集
# ==========================================

def collect(
    count: int,
    max_pages: int,
) -> pd.DataFrame:
    """Seleniumで店舗50件を収集する。"""

    options = webdriver.ChromeOptions()

    options.add_argument(
        f"--user-agent={USER_AGENT}"
    )

    driver = webdriver.Chrome(
        options=options
    )

    print(
        "[INFO] ChromeDriver:",
        driver.service.path,
    )

    rows = []
    visited = set()
    names = set()

    try:
        navigate(driver, START_URL)

        # 一覧ページを保持するタブ
        list_handle = driver.current_window_handle

        # 詳細ページ・公式サイトの取得用タブ
        driver.switch_to.new_window("tab")

        detail_handle = driver.current_window_handle

        driver.switch_to.window(list_handle)

        for page in range(1, max_pages + 1):

            if len(rows) >= count:
                break

            urls = listing_urls(
                driver.page_source,
                driver.current_url,
            )

            unvisited = [
                url
                for url in urls
                if url not in visited
            ]

            print(
                f"[INFO] {page}ページ目: "
                f"店舗候補{len(urls)}件 "
                f"（未処理{len(unvisited)}件）"
            )

            for url in unvisited:

                if len(rows) >= count:
                    break

                visited.add(url)

                driver.switch_to.window(detail_handle)

                try:
                    html = navigate(driver, url)

                    parsed = parse_store(
                        html,
                        driver.current_url,
                    )

                    if parsed is None:
                        print(
                            f"[WARN] 店舗情報不足: {url}"
                        )
                        continue

                    record, official_candidates = parsed

                    # 店舗名による重複除外
                    if record["店舗名"] in names:
                        continue

                    record["URL"], record["SSL"] = (
                        resolve_official(
                            driver,
                            official_candidates,
                        )
                    )

                    names.add(record["店舗名"])
                    rows.append(record)

                    print(
                        f"[{len(rows)}/{count}] "
                        f"{record['店舗名']}"
                    )

                except RuntimeError:
                    raise

                except WebDriverException as exc:
                    print(
                        f"[WARN] 店舗取得失敗: "
                        f"{url}: {str(exc)[:130]}"
                    )

                finally:
                    driver.switch_to.window(list_handle)

            if len(rows) >= count:
                break

            # URLを直接作成せず、ボタンをクリックする。
            if not click_next(driver, page):
                print(
                    "[INFO] 次ページボタンがありません。"
                )
                break

        return pd.DataFrame(
            rows,
            columns=COLUMNS,
        )

    finally:
        driver.quit()


# ==========================================
# メイン処理
# ==========================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description="課題1-2：Seleniumスクレイピング"
    )

    parser.add_argument(
        "--authorized",
        action="store_true",
        help="対象サイトの利用条件・取得許可を確認済み",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=50,
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("1-2.csv"),
    )

    args = parser.parse_args()

    if not args.authorized:
        parser.error(
            "サイトの利用条件と取得許可を確認してから"
            " --authorized を指定してください。"
        )

    if args.count < 1 or args.max_pages < 1:
        parser.error(
            "--count と --max-pages は1以上で指定してください。"
        )

    df = collect(
        args.count,
        args.max_pages,
    )

    if len(df) != args.count:
        raise RuntimeError(
            f"取得数不足: {len(df)}/{args.count}件。"
            "CSVは保存しません。"
        )

    df.to_csv(
        args.output,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"[OK] {args.output.resolve()}："
        f"{len(df)}件、{len(df.columns)}列、"
        f"公式URLあり{df['URL'].ne('').sum()}件"
    )


if __name__ == "__main__":
    main()
