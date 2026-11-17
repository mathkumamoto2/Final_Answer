"""課題2-2: 実店舗50件をスクレイピングし、住所を補正してMySQLに登録する。

フォルダ構成:
    python/ex1_web-scraping/1-1.py
    python/ex2_docker_and_db/2-2.py

実行例（Dockerコンテナ内）:
    python3 2-2.py --authorized
"""

from __future__ import annotations

import argparse
import os
import re
import runpy
from getpass import getpass
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.types import Unicode, UnicodeText


DB_HOST = os.environ.get("MYSQL_HOST", "finalanswer-mysql")
DB_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DB_NAME = os.environ.get("MYSQL_DATABASE", "ex2")
TABLE_NAME = os.environ.get("MYSQL_TABLE", "ex2_2")
TARGET_COUNT = 50

COLUMNS = [
    "店舗名", "電話番号", "メールアドレス", "都道府県",
    "市区町村", "番地", "建物名", "URL", "SSL",
]

DEFAULT_SCRAPER = (
    Path(__file__).resolve().parent.parent
    / "ex1_web-scraping" / "1-1.py"
)

# 例: 下通2-1-31 -> 下通 / 2-1-31
TOWN_BLOCK_RE = re.compile(r"^([^0-9０-９]+)([0-9０-９].*)$")

SQL_TYPES = {
    "店舗名": Unicode(512),
    "電話番号": Unicode(64),
    "メールアドレス": Unicode(320),
    "都道府県": Unicode(16),
    "市区町村": Unicode(255),
    "番地": Unicode(255),
    "建物名": Unicode(512),
    "URL": UnicodeText(),
    "SSL": Unicode(5),
}


def normalize_addresses(frame: pd.DataFrame) -> pd.DataFrame:
    """町名を市区町村列に結合し、番地列を数字から始まる形にする。"""
    result = frame.copy()
    parts = result["番地"].fillna("").astype(str).str.extract(
        TOWN_BLOCK_RE, expand=True
    )
    mask = parts[0].notna()

    result.loc[mask, "市区町村"] = (
        result.loc[mask, "市区町村"].fillna("").astype(str)
        + parts.loc[mask, 0].str.strip()
    )
    result.loc[mask, "番地"] = parts.loc[mask, 1].str.strip()

    # 本課題の50店舗では番地が数字で始まることを検証する。
    invalid = ~result["番地"].fillna("").astype(str).str.match(
        r"^[0-9０-９]"
    )
    if invalid.any():
        bad_names = result.loc[invalid, "店舗名"].tolist()
        raise ValueError(
            "住所の分割を確認できない店舗があります: "
            + ", ".join(map(str, bad_names))
        )

    print(f"[INFO] 住所を補正した店舗: {int(mask.sum())}件")
    return result



def validate_frame(frame: pd.DataFrame) -> None:
    """Validate the nine-column CSV/DB contract before touching MySQL."""
    if list(frame.columns) != COLUMNS:
        raise ValueError("列構成が指定9列と一致しません: {}".format(list(frame.columns)))
    if len(frame) != TARGET_COUNT:
        raise ValueError("店舗数が50件ではありません: {}".format(len(frame)))
    if frame["店舗名"].isna().any() or frame["店舗名"].astype(str).str.strip().eq("").any():
        raise ValueError("店舗名に空欄があります")
    if frame["店舗名"].astype(str).duplicated().any():
        raise ValueError("店舗名に重複があります")
    if not frame["SSL"].fillna("").astype(str).isin(["TRUE", "FALSE", ""]).all():
        raise ValueError("SSLはTRUE/FALSE/空欄のみ許可されます")
    url_present = frame["URL"].fillna("").astype(str).ne("")
    ssl_present = frame["SSL"].fillna("").astype(str).ne("")
    if not url_present.equals(ssl_present):
        raise ValueError("URLとSSLの空欄状態が一致しません")
    if (url_present & ~frame["SSL"].astype(str).isin(["TRUE", "FALSE"])).any():
        raise ValueError("URLありの行にSSL判定がありません")

def scrape(scraper_path: Path) -> pd.DataFrame:
    """課題1-1のcollect()を使って、CSVではなく実サイトから再取得する。"""
    path = scraper_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"1-1.pyが見つかりません: {path}")

    scraper = runpy.run_path(str(path), run_name="__scraper__")
    collect = scraper.get("collect")
    start_url = scraper.get("START_URL")
    if not callable(collect) or not isinstance(start_url, str):
        raise RuntimeError("1-1.pyにcollect()またはSTART_URLがありません")

    frame = collect(start_url, TARGET_COUNT)
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("collect()の戻り値がDataFrameではありません")
    if list(frame.columns) != COLUMNS or len(frame) != TARGET_COUNT:
        raise ValueError("取得したデータが50件・指定9列の条件を満たしません")
    if (
        frame["店舗名"].isna().any()
        or frame["店舗名"].eq("").any()
        or frame["店舗名"].duplicated().any()
    ):
        raise ValueError("店舗名に空欄または重複があります")

    normalized = normalize_addresses(frame)
    validate_frame(normalized)
    return normalized


def save_to_mysql(frame: pd.DataFrame, username: str, password: str) -> None:
    """保存と検証を1トランザクションで行う。失敗時はロールバック。"""
    validate_frame(frame)
    if not re.fullmatch(r"[A-Za-z0-9_]+", TABLE_NAME):
        raise ValueError("MYSQL_TABLEに不正な識別子が指定されています")
    quoted_table = "`{}`".format(TABLE_NAME)
    url = URL.create(
        drivername="mysql+pymysql",
        username=username,
        password=password,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        query={"charset": "utf8mb4"},
    )
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    data = frame.replace({"": None})

    try:
        with engine.begin() as conn:
            # DBeaver等に未終了のトランザクションがある場合、無限に待たない。
            conn.execute(text("SET SESSION innodb_lock_wait_timeout = 15"))
            conn.execute(text("SET SESSION lock_wait_timeout = 15"))

            existing = inspect(conn).has_table(TABLE_NAME)
            if existing:
                actual_columns = [
                    col["name"]
                    for col in inspect(conn).get_columns(TABLE_NAME)
                ]
                if actual_columns != COLUMNS:
                    raise RuntimeError(
                        f"既存テーブルの列構成が異なります: {actual_columns}"
                    )

                # DROP TABLEを避け、既存テーブルの50件だけを置き換える。
                # DELETEとINSERTは同一トランザクションなので、失敗時に戻せる。
                conn.execute(text("DELETE FROM " + quoted_table))

            data.to_sql(
                TABLE_NAME,
                con=conn,
                if_exists="append" if existing else "fail",
                index=False,
                dtype=SQL_TYPES,
                method="multi",
            )

            # 件数だけでなく、全店舗の住所データが一致するか確認する。
            quoted = ", ".join("`" + column + "`" for column in COLUMNS)
            saved = pd.read_sql_query(
                text("SELECT " + quoted + " FROM " + quoted_table), conn,
            )
            if len(saved) != TARGET_COUNT:
                raise RuntimeError(
                    f"DB総行数が不正です: {len(saved)}/{TARGET_COUNT}"
                )

            # Compare all 9 fields, not only addresses and URL.
            expected = data[COLUMNS].fillna("").astype(str).sort_values(
                COLUMNS
            ).reset_index(drop=True)
            actual = saved[COLUMNS].fillna("").astype(str).sort_values(
                COLUMNS
            ).reset_index(drop=True)
            if not expected.equals(actual):
                raise RuntimeError("MySQLへの保存結果が9列で一致しません")

            counts = conn.execute(
                text(
                    "SELECT COUNT(*) AS total_rows, "
                    "COUNT(`URL`) AS non_null_urls, "
                    "COUNT(NULLIF(`URL`, '')) AS non_empty_urls "
                    "FROM " + quoted_table
                )
            ).mappings().one()
            total_rows = int(counts["total_rows"])
            url_count = int(counts["non_null_urls"])
            non_empty_urls = int(counts["non_empty_urls"])
            if total_rows != TARGET_COUNT:
                raise RuntimeError(
                    "COUNT(*)が不正です: {}/{}".format(total_rows, TARGET_COUNT)
                )
            if url_count != non_empty_urls:
                raise RuntimeError(
                    "URL列に空文字とNULLが混在しています: COUNT(URL)={}, COUNT(NULLIF)={}".format(
                        url_count, non_empty_urls
                    )
                )

        # with engine.begin()の正常終了でコミット済み。
        print(
            "[OK] MySQL {}.{}: COUNT(*)={}件、COUNT(URL)={}件、"
            "COUNT(NULLIF(URL,''))={}件、9列照合OK".format(
                DB_NAME, TABLE_NAME, total_rows, url_count, non_empty_urls
            )
        )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="課題2-2: 取得→住所補正→DB登録")
    parser.add_argument("--authorized", action="store_true")
    parser.add_argument("--scraper", type=Path, default=DEFAULT_SCRAPER)
    parser.add_argument("--mysql-user", default="root")
    args = parser.parse_args()

    if not args.authorized:
        parser.error("サイトの利用条件と取得許可を確認して--authorizedを指定してください")

    frame = scrape(args.scraper)
    validate_frame(frame)
    password = getpass("MySQLパスワード: ")
    save_to_mysql(frame, args.mysql_user, password)


if __name__ == "__main__":
    main()
