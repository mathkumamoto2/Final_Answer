
"""課題1-1: 楽天ぐるなびから実店舗情報を50件取得する。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import pandas as pd
import requests
from bs4 import BeautifulSoup
# Import a shared rule set from the parent python/ directory. The same
# module also works when 1-1.py is loaded with runpy by 2-2.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.gnavi_rules import extract_direct_email, extract_site_candidates
from shared.site_verifier import MAX_REDIRECTS, URLVerifier, resolve_site



START_URL = "https://r.gnavi.co.jp/area/kumamoto/rs/"
TARGET_COUNT = 50
MAX_PAGES = 20
WAIT_SECONDS = 3.0
USER_AGENT = "ExerciseForPool-Student/1.0"

COLUMNS = [
    "店舗名", "電話番号", "メールアドレス",
    "都道府県", "市区町村", "番地", "建物名",
    "URL", "SSL",
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
    r"^(" + "|".join(map(re.escape, PREFECTURES)) + r")"
)

CITY_RE = re.compile(
    r"^(.+?市.+?区|.+?郡.+?[町村]|.+?[市区町村])"
)

POST_RE = re.compile(r"^〒?\s*\d{3}-?\d{4}\s*")

SHOP_RE = re.compile(
    r"^/(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{6,}/?$"
)

PHONE_RE = re.compile(
    r"0\d{1,4}[-－]?\d{1,4}[-－]?\d{3,4}"
)

CHALLENGE_RE = re.compile(
    r"captcha|verify you are human|access denied|"
    r"ロボットではない|アクセスが制限",
    re.I,
)

TOWN_RE = re.compile(r"^([^0-9０-９]+)(?=[0-9０-９])")

FLOOR_RE = re.compile(
    r"^(?P<block>.+\d)"
    r"(?P<floor>B?\d+(?:[〜～~-]\d+)?F|B\d+)$",
    re.I,
)


def clean(value: object) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\u3000", " "),
    ).strip()


def public_url(url: str) -> bool:
    """HTTP(S)かつ明らかなローカルアドレスでないことを確認。"""
    return URLVerifier.is_public_url(url)


def gnavi_domain(url: str) -> bool:
    """ぐるなびのページかどうかを判断する。"""

    host = (urlsplit(url).hostname or "").lower().rstrip(".")

    return (
        host in {"gnavi.co.jp", "gurunavi.com"}
        or host.endswith((".gnavi.co.jp", ".gurunavi.com"))
    )


def page_url(start_url: str, page: int) -> str:
    """一覧ページのページ番号を設定する。"""

    parts = urlsplit(start_url)

    params = [
        (key, value)
        for key, value in parse_qsl(
            parts.query, keep_blank_values=True
        )
        if key != "p"
    ]

    if page > 1:
        params.append(("p", str(page)))

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(params),
        "",
    ))


def listing_urls(html: str, base_url: str) -> list[str]:
    """店舗一覧から店舗詳細URLだけを取得する。"""

    soup = BeautifulSoup(html, "html.parser")

    seen = set()
    urls = []

    for anchor in soup.select("a[href]"):
        url = urljoin(base_url, anchor["href"])
        parts = urlsplit(url)

        if parts.hostname != "r.gnavi.co.jp":
            continue

        if not SHOP_RE.fullmatch(parts.path):
            continue

        normalized = urlunsplit((
            parts.scheme,
            parts.netloc,
            parts.path,
            "",
            "",
        ))

        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    return urls


def restaurant_jsonld(soup: BeautifulSoup) -> dict:
    """JSON-LDに含まれる店舗情報を抽出する。"""

    def walk(value):
        if isinstance(value, list):
            for part in value:
                yield from walk(part)

        elif isinstance(value, dict):
            yield value

            if "@graph" in value:
                yield from walk(value["@graph"])

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            data = json.loads(
                script.string or script.get_text()
            )
        except (TypeError, json.JSONDecodeError):
            continue

        for node in walk(data):
            types = node.get("@type", [])

            if isinstance(types, str):
                types = [types]

            if isinstance(types, list) and any(
                str(item).rsplit("/", 1)[-1]
                in {"Restaurant", "FoodEstablishment"}
                for item in types
            ):
                return node

    return {}


def table_value(soup: BeautifulSoup, label: str) -> str:
    """HTMLの表から指定された情報を取り出す。"""

    for node in soup.select("th, dt"):

        node_text = clean(
            node.get_text(" ", strip=True)
        )

        if node_text != label:
            continue

        sibling_type = (
            "td" if node.name == "th" else "dd"
        )

        sibling = node.find_next_sibling(sibling_type)

        if sibling:
            return clean(
                sibling.get_text(" ", strip=True)
            )

    return ""


def split_address(
    value: str,
) -> tuple[str, str, str, str]:
    """住所を都道府県・市区町村・番地・建物名に分割する。"""

    address = clean(
        POST_RE.sub("", clean(value))
    )

    address = re.split(
        r"大きな地図で見る|地図印刷|Googleマップ",
        address,
    )[0].strip()

    prefecture = ""
    city = ""
    building = ""

    # 都道府県
    match = PREF_RE.match(address)

    if match:
        prefecture = match.group()
        address = address[match.end():].strip()

    # 市区町村
    match = CITY_RE.match(address)

    if match:
        city = match.group()
        address = address[match.end():].strip()

    # The official sample.csv places the town name in 市区町村, not 番地.
    town_match = TOWN_RE.match(address)
    if town_match:
        city += town_match.group(1).strip()
        address = address[town_match.end():].strip()

    # 空白で番地と建物名が区切られている場合
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

    # 末尾の3F、1〜3Fなどを番地から分離する。
    floor = FLOOR_RE.match(address)

    if floor:
        return (
            prefecture,
            city,
            floor["block"],
            floor["floor"],
        )

    # 建物名が空白なしで連結されている場合
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


def parse_store(
    html: str,
    url: str,
) -> tuple[dict[str, str], list[str]] | None:
    """1店舗の情報を取得する。"""

    soup = BeautifulSoup(html, "html.parser")

    title = (
        clean(soup.title.get_text(" ", strip=True))
        if soup.title
        else ""
    )

    if CHALLENGE_RE.search(title):
        raise RuntimeError(
            f"アクセス制限を検出: {url}"
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

    # Only the link explicitly labelled お店に直接メールする is valid.
    # Never substitute JSON-LD or an unrelated mailto link.
    email = extract_direct_email(html)

    # 住所は画面に表示されている情報を優先。
    address = table_value(soup, "住所")

    if not address:
        ld_address = ld.get("address")

        if isinstance(ld_address, dict):
            address = " ".join(
                clean(ld_address.get(key))
                for key in (
                    "addressRegion",
                    "addressLocality",
                    "streetAddress",
                )
            )
        else:
            address = clean(ld_address)

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

    return record, extract_site_candidates(html, url)


class Fetcher:
    """Respect robots.txt and wait before every Gurunavi HTTP request."""

    def __init__(self) -> None:
        self.verifier = URLVerifier(user_agent=USER_AGENT)
        # Share one session so User-Agent, connection pooling and robots cache
        # are consistent between store-page retrieval and homepage checks.
        self.session = self.verifier.session

    def allowed_by_robots(self, url: str) -> bool:
        decision = self.verifier.check_robots(url)
        if not decision.allowed:
            raise PermissionError(
                "{}: {}".format(decision.status, decision.reason)
            )
        return True

    def get(
        self,
        url: str,
        *,
        check_robots: bool = True,
        only_url: bool = False,
    ) -> tuple[str, str]:
        current = url

        for _ in range(MAX_REDIRECTS + 1):
            if not public_url(current):
                raise ValueError("不正なアクセス先: {}".format(current))

            if check_robots:
                self.allowed_by_robots(current)

            # Every actual HTTP request is preceded by at least three seconds.
            time.sleep(WAIT_SECONDS)
            with self.session.get(
                current,
                timeout=(8, 20),
                stream=True,
                allow_redirects=False,
                verify=True,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError(
                            "リダイレクト先なし: {}".format(current)
                        )
                    target = urljoin(current, location)
                    if not gnavi_domain(target):
                        raise RuntimeError(
                            "ぐるなび外への転送を拒否: {}".format(target)
                        )
                    current = target
                    continue

                if response.status_code >= 400:
                    error_body = self.verifier.read_limited(response, 512 * 1024)
                    error_text = self.verifier.decode_body(
                        error_body, response.headers.get("Content-Type", "")
                    )
                    if CHALLENGE_RE.search(error_text[:8192]):
                        raise RuntimeError(
                            "CAPTCHA/アクセス制限を検知しました: {}".format(current)
                        )
                    response.raise_for_status()

                if only_url:
                    return response.url, ""

                content = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError(
                            "HTMLサイズが4MiBを超過: {}".format(current)
                        )

                content_type = response.headers.get("Content-Type", "")
                markup = self.verifier.decode_body(bytes(content), content_type)
                return response.url, markup

        raise RuntimeError("リダイレクト回数が上限を超えました: {}".format(url))

    def close(self) -> None:
        self.verifier.close()


def collect(
    start_url: str,
    count: int,
) -> pd.DataFrame:
    """一覧ページから必要件数の店舗情報を取得する。"""
    if urlsplit(start_url).hostname != "r.gnavi.co.jp":
        raise ValueError("ぐるなびの店舗一覧URLを指定してください")
    if count < 1:
        raise ValueError("countは1以上を指定してください")

    fetcher = Fetcher()
    verifier = fetcher.verifier
    records = []
    processed = set()
    names = set()

    try:
        for page in range(1, MAX_PAGES + 1):
            if len(records) >= count:
                break

            list_url = page_url(start_url, page)
            actual, html = fetcher.get(list_url)
            if CHALLENGE_RE.search(html[:8192]):
                raise RuntimeError("アクセス制限のため停止: {}".format(actual))

            urls = listing_urls(html, actual)
            unprocessed = [store_url for store_url in urls if store_url not in processed]
            print(
                "[INFO] {}ページ目: 店舗候補{}件（未処理{}件）".format(
                    page, len(urls), len(unprocessed)
                )
            )
            if not unprocessed:
                break

            for store_url in unprocessed:
                if len(records) >= count:
                    break
                processed.add(store_url)

                try:
                    actual_store, detail = fetcher.get(store_url)
                    if CHALLENGE_RE.search(detail[:8192]):
                        raise RuntimeError(
                            "アクセス制限のため停止: {}".format(actual_store)
                        )

                    parsed = parse_store(detail, actual_store)
                    if parsed is None:
                        print("[WARN] 店舗情報不足: {}".format(store_url))
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
                                checked.status,
                                record["店舗名"],
                                checked.url,
                                checked.ssl,
                                checked.reason,
                            )
                        )

                    names.add(record["店舗名"])
                    records.append(record)
                    print("[{}/{}] {}".format(len(records), count, record["店舗名"]))

                except RuntimeError:
                    # CAPTCHA/access-control pages are a hard stop, not a retry.
                    raise
                except (requests.RequestException, PermissionError, ValueError) as exc:
                    print("[WARN] 店舗取得失敗: {} ({})".format(store_url, exc))

        return pd.DataFrame(records, columns=COLUMNS)
    finally:
        fetcher.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="課題1-1: ぐるなび店舗情報を取得"
    )

    parser.add_argument(
        "--authorized",
        action="store_true",
        help="取得許可・利用条件を確認済み",
    )

    parser.add_argument(
        "--start-url",
        default=START_URL,
    )

    parser.add_argument(
        "--count",
        type=int,
        default=TARGET_COUNT,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("1-1.csv"),
    )

    args = parser.parse_args()

    if not args.authorized:
        parser.error(
            "サイトの利用条件と取得許可を確認してから"
            " --authorized を指定してください"
        )

    if args.count <= 0:
        parser.error(
            "--countには1以上を指定してください"
        )

    frame = collect(
        args.start_url,
        args.count,
    )

    if len(frame) != args.count:
        raise RuntimeError(
            f"取得件数不足: {len(frame)}/{args.count}件。"
            "CSVは保存しません"
        )

    frame.to_csv(
        args.output,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"[OK] {args.output.resolve()}："
        f"{len(frame)}件、{len(frame.columns)}列"
    )

    print(
        f"[INFO] 公式URLあり: "
        f"{(frame['URL'] != '').sum()}件 / "
        f"{len(frame)}件"
    )


if __name__ == "__main__":
    main()
