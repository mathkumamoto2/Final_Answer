"""Validate the specified nine-column output without depending on the network."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

COLUMNS = [
    "店舗名", "電話番号", "メールアドレス", "都道府県", "市区町村",
    "番地", "建物名", "URL", "SSL",
]


def validate(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    assert list(data.columns) == COLUMNS, f"9列/順序が不一致: {list(data.columns)}"
    assert len(data) == 50, f"行数が50ではない: {len(data)}"
    assert data["店舗名"].ne("").all(), "店舗名の空欄"
    assert not data["店舗名"].duplicated().any(), "店舗名に重複"
    assert data["電話番号"].ne("").all(), "電話番号の空欄"
    assert data["都道府県"].ne("").all(), "都道府県の空欄"
    assert data["市区町村"].ne("").all(), "市区町村の空欄"
    assert data["番地"].str.match(r"^[0-9０-９]").all(), "番地が数字で始まらない"
    assert data["SSL"].isin(["TRUE", "FALSE", ""]).all(), "SSLが不正"
    assert ((data["URL"] != "") == (data["SSL"] != "")).all(), "URLとSSLの片方だけ空欄"
    url_present = data["URL"].ne("")
    assert data.loc[url_present, "URL"].str.match(r"^https?://[^/]+", case=False).all(), "URLはHTTP(S)の絶対URLである必要があります"
    assert (~data["URL"].str.contains(r"(?:captcha|r\.gnavi\.co\.jp|localhost)", case=False, regex=True)).all(), "不適切なURL"
    print(f"[OK] {path}: 50件、9列、住所・重複・URL/SSL形式OK")
    print(f"URLあり: {data['URL'].ne('').sum()} / 50")
    print(f"SSL TRUE: {data['SSL'].eq('TRUE').sum()} / 50")
    print(data[["店舗名", "市区町村", "番地", "URL", "SSL"]].head(5).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        validate(path)


if __name__ == "__main__":
    main()
