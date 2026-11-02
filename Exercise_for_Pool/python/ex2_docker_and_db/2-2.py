
from __future__ import annotations

import argparse
import re
import runpy
from getpass import getpass
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.types import Unicode, UnicodeText


DB_HOST = "finalanswer-mysql"
DB_NAME = "ex2"
TABLE_NAME = "ex2_2"
TARGET_COUNT = 50

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

DEFAULT_SCRAPER = (
    Path(__file__).resolve().parent.parent
    / "ex1_web-scraping"
    / "1-1.py"
)

# 例: 下通2-1-31 -> 下通 / 2-1-31
TOWN_BLOCK_RE = re.compile(
    r"^([^0-9０-９]+)([0-9０-９].*)$"
)

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


def normalize_addresses(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """町名を市区町村列に結合し、番地を数字から始まる形にする。"""

    result = frame.copy()

    parts = (
        result["番地"]
        .fillna("")
        .astype(str)
        .str.extract(TOWN_BLOCK_RE, expand=True)
    )

    mask = parts[0].notna()

    result.loc[mask, "市区町村"] = (
        result.loc[mask, "市区町村"]
        .fillna("")
        .astype(str)
        + parts.loc[mask, 0].str.strip()
    )

    result.loc[mask, "番地"] = (
        parts.loc[mask, 1].str.strip()
    )

    # 住所分割に失敗した店舗を検出する。
    invalid = (
        ~result["番地"]
        .fillna("")
        .astype(str)
        .str.match(r"^[0-9０-９]")
    )

    if invalid.any():
        bad_names = result.loc[
            invalid, "店舗名"
        ].tolist()

        raise ValueError(
            "住所の分割を確認できない店舗があります: "
            + ", ".join(map(str, bad_names))
        )

    print(
        f"[INFO] 住所を補正した店舗: "
        f"{int(mask.sum())}件"
    )

    return result


def scrape(
    scraper_path: Path,
) -> pd.DataFrame:
    """課題1-1のcollect()を使い、実サイトから50店舗を再取得する。"""

    path = scraper_path.expanduser().resolve()

    if not path.is_file():
        raise FileNotFoundError(
            f"1-1.pyが見つかりません: {path}"
        )

    # 1-1.pyのmain()は実行せず、collect()だけを再利用する。
    scraper = runpy.run_path(
        str(path),
        run_name="__scraper__",
    )

    collect = scraper.get("collect")
    start_url = scraper.get("START_URL")

    if (
        not callable(collect)
        or not isinstance(start_url, str)
    ):
        raise RuntimeError(
            "1-1.pyにcollect()またはSTART_URLがありません"
        )

    frame = collect(
        start_url,
        TARGET_COUNT,
    )

    if not isinstance(frame, pd.DataFrame):
        raise TypeError(
            "collect()の戻り値がDataFrameではありません"
        )

    if (
        list(frame.columns) != COLUMNS
        or len(frame) != TARGET_COUNT
    ):
        raise ValueError(
            "取得データが50件・指定9列の条件を満たしません"
        )

    if (
        frame["店舗名"].isna().any()
        or frame["店舗名"].eq("").any()
        or frame["店舗名"].duplicated().any()
    ):
        raise ValueError(
            "店舗名に空欄または重複があります"
        )

    return normalize_addresses(frame)


def save_to_mysql(
    frame: pd.DataFrame,
    username: str,
    password: str,
) -> None:
    """住所補正後の50件をMySQLに保存し、保存結果を照合する。"""

    connection_url = URL.create(
        drivername="mysql+pymysql",
        username=username,
        password=password,
        host=DB_HOST,
        port=3306,
        database=DB_NAME,
        query={
            "charset": "utf8mb4",
        },
    )

    engine = create_engine(
        connection_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 10,
        },
    )

    # 空文字列はSQLのNULLとして保存する。
    data = frame.copy().replace({
        "": None,
    })

    try:
        # 正常終了時にCOMMIT、例外発生時にROLLBACKする。
        with engine.begin() as conn:

            # 未終了トランザクションによるロック待ちを制限する。
            conn.execute(
                text(
                    "SET SESSION innodb_lock_wait_timeout = 15"
                )
            )

            conn.execute(
                text(
                    "SET SESSION lock_wait_timeout = 15"
                )
            )

            existing = inspect(conn).has_table(
                TABLE_NAME
            )

            if existing:

                actual_columns = [
                    column["name"]
                    for column in inspect(conn).get_columns(
                        TABLE_NAME
                    )
                ]

                if actual_columns != COLUMNS:
                    raise RuntimeError(
                        "既存テーブルの列構成が異なります: "
                        f"{actual_columns}"
                    )

                # テーブル自体は削除せず、既存データを入れ替える。
                conn.execute(
                    text("DELETE FROM `ex2_2`")
                )

            # スクレイピングした50件をMySQLへ保存する。
            data.to_sql(
                TABLE_NAME,
                con=conn,
                if_exists=(
                    "append" if existing else "fail"
                ),
                index=False,
                dtype=SQL_TYPES,
                method="multi",
            )

            # 保存されたデータをMySQLから再取得する。
            saved = pd.read_sql_query(
                text(
                    "SELECT "
                    "`店舗名`, "
                    "`市区町村`, "
                    "`番地`, "
                    "`URL` "
                    "FROM `ex2_2`"
                ),
                conn,
            )

            if len(saved) != TARGET_COUNT:
                raise RuntimeError(
                    "DB登録件数が不正です: "
                    f"{len(saved)}/{TARGET_COUNT}"
                )

            # Python側のデータとMySQLの保存結果を比較する。
            compare_columns = [
                "店舗名",
                "市区町村",
                "番地",
                "URL",
            ]

            expected = (
                data[compare_columns]
                .fillna("")
                .sort_values("店舗名")
                .reset_index(drop=True)
            )

            actual = (
                saved[compare_columns]
                .fillna("")
                .sort_values("店舗名")
                .reset_index(drop=True)
            )

            if not expected.equals(actual):
                raise RuntimeError(
                    "MySQLに保存した住所・URLが"
                    "取得データと一致しません"
                )

            url_count = int(
                saved["URL"].notna().sum()
            )

        # トランザクションが正常にコミットされた場合のみ表示。
        print(
            f"[OK] MySQL {DB_NAME}.{TABLE_NAME}: "
            f"全{TARGET_COUNT}件、"
            f"公式URL{url_count}件、"
            "住所照合OK"
        )

    finally:
        engine.dispose()


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "課題2-2: "
            "スクレイピング → 住所補正 → MySQL登録"
        )
    )

    parser.add_argument(
        "--authorized",
        action="store_true",
        help="サイトの利用条件・取得許可を確認済みの場合に指定",
    )

    parser.add_argument(
        "--scraper",
        type=Path,
        default=DEFAULT_SCRAPER,
        help="課題1-1の1-1.pyへのパス",
    )

    parser.add_argument(
        "--mysql-user",
        default="root",
        help="MySQLの接続ユーザー名",
    )

    args = parser.parse_args()

    if not args.authorized:
        parser.error(
            "サイトの利用条件と取得許可を確認してから"
            " --authorized を指定してください"
        )

    frame = scrape(args.scraper)

    password = getpass(
        "MySQLパスワード: "
    )

    save_to_mysql(
        frame,
        args.mysql_user,
        password,
    )


if __name__ == "__main__":
    main()
