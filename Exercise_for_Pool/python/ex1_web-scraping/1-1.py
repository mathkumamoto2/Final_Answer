
"""課題1-1: 楽天ぐるなびから実店舗情報を50件取得する。"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import time
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from bs4 import BeautifulSoup


# ==========================================
# 基本設定
# ==========================================

START_URL = "https://r.gnavi.co.jp/area/kumamoto/rs/"
TARGET_COUNT = 50
MAX_PAGES = 20
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
    r"^(" + "|".join(map(re.escape, PREFECTURES)) + r")"
)

CITY_RE = re.compile(
    r"^(.+?市.+?区|.+?郡.+?[町村]|.+?[市区町村])"
)

POST_RE = re.compile(
    r"^〒?\s*\d{3}-?\d{4}\s*"
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
    r"captcha|verify you are human|access denied|"
    r"ロボットではない|アクセスが制限",
    re.I,
)

FLOOR_RE = re.compile(
    r"^(?P<block>.+\d)"
    r"(?P<floor>B?\d+(?:[〜～~-]\d+)?F|B\d+)$",
    re.I,
)

# 町名と番地を分離するための正規表現
# 例: 下通1-10-3 -> 下通 / 1-10-3
TOWN_RE = re.compile(
    r"^([^0-9０-９]+)(?=[0-9０-９])"
)


# ==========================================
# 文字列・URL処理
# ==========================================

def clean(value: object) -> str:
    """余分な空白や改行を取り除く。"""

    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\u3000", " "),
    ).strip()


def public_url(url: str) -> bool:
    """HTTP(S)かつ明らかなローカルアドレスでないことを確認する。"""

    try:
        parts = urlsplit(url)

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


def gnavi_domain(url: str) -> bool:
    """ぐるなびのページかどうかを判定する。"""

    host = (
        urlsplit(url).hostname or ""
    ).lower().rstrip(".")

    return (
        host in {"gnavi.co.jp", "gurunavi.com"}
        or host.endswith(
            (".gnavi.co.jp", ".gurunavi.com")
        )
    )


def page_url(
    start_url: str,
    page: int,
) -> str:
    """一覧ページのURLにページ番号を設定する。"""

    parts = urlsplit(start_url)

    params = [
        (key, value)
        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
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


# ==========================================
# 店舗一覧からURLを取得
# ==========================================

def listing_urls(
    html: str,
    base_url: str,
) -> list[str]:
    """一覧ページから店舗詳細URLだけを取得する。"""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    seen = set()
    urls = []

    for anchor in soup.select("a[href]"):

        url = urljoin(
            base_url,
            anchor["href"],
        )

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


# ==========================================
# JSON-LDから店舗情報を取得
# ==========================================

def restaurant_jsonld(
    soup: BeautifulSoup,
) -> dict:
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
                in {
                    "Restaurant",
                    "FoodEstablishment",
                }
                for item in types
            ):
                return node

    return {}


def table_value(
    soup: BeautifulSoup,
    label: str,
) -> str:
    """HTMLの表から指定された情報を取り出す。"""

    for node in soup.select("th, dt"):

        node_text = clean(
            node.get_text(
                " ",
                strip=True,
            )
        )

        if node_text != label:
            continue

        sibling_type = (
            "td"
            if node.name == "th"
            else "dd"
        )

        sibling = node.find_next_sibling(
            sibling_type
        )

        if sibling:
            return clean(
                sibling.get_text(
                    " ",
                    strip=True,
                )
            )

    return ""


# ==========================================
# 住所分割
# ==========================================

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

    # 1. 都道府県を取得
    match = PREF_RE.match(address)

    if match:
        prefecture = match.group()
        address = address[match.end():].strip()

    # 2. 市区町村を取得
    match = CITY_RE.match(address)

    if match:
        city = match.group()
        address = address[match.end():].strip()

    # 3. 町名を市区町村側に移動
    # 修正前:
    # 市区町村 = 熊本市中央区
    # 番地     = 下通1-10-3
    #
    # 修正後:
    # 市区町村 = 熊本市中央区下通
    # 番地     = 1-10-3

    town_match = TOWN_RE.match(address)

    if town_match:
        city += town_match.group(1).strip()
        address = address[town_match.end():].strip()

    # 4. 番地と建物名の間に空白がある場合
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

    # 5. 末尾の階数表記を分離
    # 例: 1-4-153F -> 1-4-15 / 3F

    floor = FLOOR_RE.match(address)

    if floor:
        return (
            prefecture,
            city,
            floor["block"],
            floor["floor"],
        )

    # 6. 番地と建物名が連続している場合
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

    return (
        prefecture,
        city,
        address,
        building,
    )


# ==========================================
# 公式サイトURLの候補を取得
# ==========================================

def official_candidates(
    soup: BeautifulSoup,
    store_url: str,
) -> list[str]:
    """店舗ページから公式サイトURL候補を取得する。"""

    high_priority = []
    normal_priority = []
    seen = set()

    for anchor in soup.select("a[href]"):

        label = clean(" ".join((
            anchor.get_text(
                " ",
                strip=True,
            ),
            anchor.get("aria-label", ""),
            anchor.get("title", ""),
        )))

        if not OFFICIAL_RE.search(label):
            continue

        raw = anchor.get(
            "href",
            "",
        ).strip()

        # ページ内移動リンクは除外する。
        if not raw or raw.startswith("#"):
            continue

        url = urljoin(
            store_url,
            raw,
        )

        # ぐるなび自身のページは公式URLとしない。
        if (
            not public_url(url)
            or gnavi_domain(url)
            or url in seen
        ):
            continue

        seen.add(url)

        if re.search(
            r"オフィシャル\s*ページ|"
            r"お店の\s*ホームページ",
            label,
        ):
            high_priority.append(url)

        else:
            normal_priority.append(url)

    return high_priority + normal_priority


# ==========================================
# 1店舗の情報を抽出
# ==========================================

def parse_store(
    html: str,
    url: str,
) -> tuple[dict[str, str], list[str]] | None:
    """店舗詳細ページから9項目のデータを取得する。"""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = (
        clean(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )
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
            h1.get_text(
                " ",
                strip=True,
            )
            if h1
            else ""
        )
    )

    if not name:
        return None

    # 電話番号
    phone = clean(
        ld.get("telephone")
    )

    if not phone:
        anchor = soup.select_one(
            'a[href^="tel:"]'
        )

        if anchor:
            phone = clean(
                anchor["href"][4:]
            )

    if not phone:
        source = (
            table_value(
                soup,
                "電話番号",
            )
            or soup.get_text(
                " ",
                strip=True,
            )
        )

        match = PHONE_RE.search(source)

        phone = (
            match.group()
            if match
            else ""
        )

    # メールアドレス
    email = clean(
        ld.get("email")
    )

    if not email:
        anchor = soup.select_one(
            'a[href^="mailto:"]'
        )

        if anchor:
            email = clean(
                anchor["href"][7:].split(
                    "?",
                    1,
                )[0]
            )

    # 住所
    # HTMLの表に表示されている住所を優先する。
    address = table_value(
        soup,
        "住所",
    )

    if not address:
        ld_address = ld.get("address")

        if isinstance(ld_address, dict):
            address = " ".join(
                clean(
                    ld_address.get(key)
                )
                for key in (
                    "addressRegion",
                    "addressLocality",
                    "streetAddress",
                )
            )

        else:
            address = clean(
                ld_address
            )

    if not address:
        node = soup.select_one(
            '[itemprop="streetAddress"], address'
        )

        address = (
            node.get_text(
                " ",
                strip=True,
            )
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

    return (
        record,
        official_candidates(
            soup,
            url,
        ),
    )


# ==========================================
# HTTP通信
# ==========================================

class Fetcher:
    """HTTPアクセスとrobots.txtの判定を管理する。"""

    def __init__(self) -> None:
        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.8",
        })

        self.robots = {}

    def allowed_by_robots(
        self,
        url: str,
    ) -> bool:
        """robots.txtを確認する。"""

        parts = urlsplit(url)

        origin = (
            f"{parts.scheme}://{parts.netloc}"
        )

        if origin not in self.robots:

            _, content = self.get(
                origin + "/robots.txt",
                check_robots=False,
            )

            parser = RobotFileParser()

            parser.parse(
                content.splitlines()
            )

            self.robots[origin] = parser

        return self.robots[origin].can_fetch(
            USER_AGENT,
            url,
        )

    def get(
        self,
        url: str,
        *,
        check_robots: bool = True,
        only_url: bool = False,
    ) -> tuple[str, str]:
        """アクセス制限・待機時間・リダイレクトを考慮して取得する。"""

        current = url

        for _ in range(9):

            if not public_url(current):
                raise ValueError(
                    f"不正なアクセス先: {current}"
                )

            if (
                check_robots
                and not self.allowed_by_robots(current)
            ):
                raise PermissionError(
                    f"robots.txtによる制限: {current}"
                )

            # リクエストの前に3秒待機する。
            time.sleep(WAIT_SECONDS)

            with self.session.get(
                current,
                timeout=(8, 20),
                stream=True,
                allow_redirects=False,
            ) as response:

                if response.status_code in {
                    301,
                    302,
                    303,
                    307,
                    308,
                }:

                    location = response.headers.get(
                        "Location"
                    )

                    if not location:
                        raise RuntimeError(
                            f"リダイレクト先なし: {current}"
                        )

                    current = urljoin(
                        current,
                        location,
                    )

                    continue

                response.raise_for_status()

                if only_url:
                    return response.url, ""

                content = bytearray()

                for chunk in response.iter_content(
                    chunk_size=65536
                ):

                    content.extend(chunk)

                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError(
                            f"HTMLサイズが4MiBを超過: {current}"
                        )

                response._content = bytes(
                    content
                )

                if (
                    not response.encoding
                    or response.encoding.lower() == "iso-8859-1"
                ):
                    response.encoding = (
                        response.apparent_encoding
                        or "utf-8"
                    )

                return (
                    response.url,
                    response.text,
                )

        raise RuntimeError(
            "リダイレクト回数が上限を超えました"
        )


# ==========================================
# 公式URLとSSLの確認
# ==========================================

def resolve_official(
    fetcher: Fetcher,
    candidates: list[str],
) -> tuple[str, str]:
    """公式URLの最終到達先とSSLを確認する。"""

    for candidate in candidates:

        try:
            final_url, _ = fetcher.get(
                candidate,
                only_url=True,
            )

            if (
                not public_url(final_url)
                or gnavi_domain(final_url)
            ):
                continue

            ssl = (
                "TRUE"
                if urlsplit(final_url).scheme == "https"
                else "FALSE"
            )

            return (
                final_url,
                ssl,
            )

        except requests.exceptions.SSLError:
            print(
                "[WARN] SSL証明書エラー: "
                f"{candidate}"
            )

        except (
            requests.RequestException,
            PermissionError,
            ValueError,
            RuntimeError,
        ) as exc:
            print(
                "[WARN] 公式URL確認失敗: "
                f"{candidate} ({exc})"
            )

    return "", ""


# ==========================================
# 50店舗の収集
# ==========================================

def collect(
    start_url: str,
    count: int,
) -> pd.DataFrame:
    """一覧ページを巡回して必要件数の店舗情報を取得する。"""

    if urlsplit(start_url).hostname != "r.gnavi.co.jp":
        raise ValueError(
            "ぐるなびの店舗一覧URLを指定してください"
        )

    fetcher = Fetcher()

    records = []
    processed = set()

    for page in range(
        1,
        MAX_PAGES + 1,
    ):

        if len(records) >= count:
            break

        list_url = page_url(
            start_url,
            page,
        )

        actual, html = fetcher.get(
            list_url
        )

        if CHALLENGE_RE.search(html[:2000]):
            raise RuntimeError(
                f"アクセス制限のため停止: {actual}"
            )

        urls = listing_urls(
            html,
            actual,
        )

        unprocessed = [
            url
            for url in urls
            if url not in processed
        ]

        print(
            f"[INFO] {page}ページ目: "
            f"店舗候補{len(urls)}件 "
            f"（未処理{len(unprocessed)}件）"
        )

        if not unprocessed:
            break

        for store_url in unprocessed:

            if len(records) >= count:
                break

            processed.add(store_url)

            try:
                actual_store, detail = fetcher.get(
                    store_url
                )

                if CHALLENGE_RE.search(detail[:2000]):
                    raise RuntimeError(
                        "アクセス制限のため停止: "
                        f"{store_url}"
                    )

                result = parse_store(
                    detail,
                    actual_store,
                )

                if result is None:
                    print(
                        f"[WARN] 店舗情報不足: {store_url}"
                    )
                    continue

                record, candidates = result

                record["URL"], record["SSL"] = (
                    resolve_official(
                        fetcher,
                        candidates,
                    )
                )

                records.append(record)

                print(
                    f"[{len(records)}/{count}] "
                    f"{record['店舗名']}"
                )

            except (
                requests.RequestException,
                PermissionError,
                ValueError,
            ) as exc:
                print(
                    f"[WARN] 店舗取得失敗: "
                    f"{store_url} ({exc})"
                )

    return pd.DataFrame(
        records,
        columns=COLUMNS,
    )


# ==========================================
# メイン処理
# ==========================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "課題1-1: ぐるなび店舗情報を取得"
        )
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
            f"取得件数不足: "
            f"{len(frame)}/{args.count}件。"
            "CSVは保存しません"
        )

    frame.to_csv(
        args.output,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"[OK] {args.output.resolve()}："
        f"{len(frame)}件、"
        f"{len(frame.columns)}列"
    )

    print(
        "[INFO] 公式URLあり: "
        f"{(frame['URL'] != '').sum()}件 / "
        f"{len(frame)}件"
    )


if __name__ == "__main__":
    main()
