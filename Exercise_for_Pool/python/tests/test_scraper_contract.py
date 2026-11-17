import importlib.util
from pathlib import Path

from shared.site_verifier import RobotsDecision

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scraper_11 = load_module("scraper_11", ROOT / "ex1_web-scraping" / "1-1.py")
scraper_12 = load_module("scraper_12", ROOT / "ex1_web-scraping" / "1-2.py")
scraper_22 = load_module("scraper_22", ROOT / "ex2_docker_and_db" / "2-2.py")

STORE_HTML = '''
<html lang="ja">
<head><meta charset="UTF-8"><title>Test Store</title></head>
<body>
  <h1>Test Store</h1>
  <table>
    <tr><th>電話番号</th><td>096-123-4567</td></tr>
    <tr><th>住所</th><td>熊本県熊本市中央区下通1-2-3</td></tr>
    <tr><th>ホームページ</th><td>
      <a href="#" class="go-off" data-o='{"a":"shop.example\\/","b":"https"}'>お店のホームページ</a>
      <a href="https://official.example/">オフィシャルページ</a>
    </td></tr>
  </table>
  <a href="mailto:wrong@example.com">運営会社へメール</a>
  <a href="mailto:info@shop.example">お店に直接メールする</a>
</body></html>
'''


def test_1_1_parser_uses_homepage_and_direct_email():
    parsed = scraper_11.parse_store(STORE_HTML, "https://r.gnavi.co.jp/test123/")
    assert parsed is not None
    record, candidates = parsed
    assert record["店舗名"] == "Test Store"
    assert record["メールアドレス"] == "info@shop.example"
    assert candidates[0].kind == "shop_homepage"
    assert candidates[0].url == "https://shop.example/"


def test_1_2_parser_uses_same_shared_rules():
    parsed = scraper_12.parse_store(STORE_HTML, "https://r.gnavi.co.jp/test123/")
    assert parsed is not None
    record, candidates = parsed
    assert record["メールアドレス"] == "info@shop.example"
    assert candidates[0].kind == "shop_homepage"
    assert candidates[0].url == "https://shop.example/"


def test_both_scrapers_produce_identical_extracted_store_fields():
    left = scraper_11.parse_store(STORE_HTML, "https://r.gnavi.co.jp/test123/")
    right = scraper_12.parse_store(STORE_HTML, "https://r.gnavi.co.jp/test123/")
    assert left is not None and right is not None
    assert left[0] == right[0]
    assert left[1] == right[1]


def test_address_split_matches_expected_sample_layout():
    assert scraper_11.split_address("熊本県熊本市中央区下通1-2-3") == (
        "熊本県", "熊本市中央区下通", "1-2-3", ""
    )


def test_requests_fetcher_stops_when_robots_is_unverified(monkeypatch):
    class FakeVerifier:
        @staticmethod
        def is_public_url(url):
            return True

        def __init__(self, **kwargs):
            self.session = object()

        def check_robots(self, url):
            return RobotsDecision(False, "UNVERIFIED", "robots.txt is not text/plain")

        def close(self):
            pass

    monkeypatch.setattr(scraper_11, "URLVerifier", FakeVerifier)
    fetcher = scraper_11.Fetcher()
    with pytest.raises(PermissionError, match="robots.txt is not text/plain"):
        fetcher.get("https://r.gnavi.co.jp/n814504/")
    fetcher.close()


def test_listing_urls_filters_other_domains_and_deduplicates():
    html = '''
      <a href="https://r.gnavi.co.jp/n814504/">one</a>
      <a href="https://r.gnavi.co.jp/n814504/?from=top">duplicate</a>
      <a href="https://evil.example/123456/">external</a>
      <a href="https://r.gnavi.co.jp/not-a-store/">not-store</a>
    '''
    expected = ["https://r.gnavi.co.jp/n814504/"]
    assert scraper_11.listing_urls(html, "https://r.gnavi.co.jp/area/kumamoto/rs/") == expected
    assert scraper_12.listing_urls(html, "https://r.gnavi.co.jp/area/kumamoto/rs/") == expected


def make_frame(rows=50):
    return pd.DataFrame(
        [
            {
                "店舗名": "店{}".format(i),
                "電話番号": "096-123-{:04d}".format(i),
                "メールアドレス": "",
                "都道府県": "熊本県",
                "市区町村": "熊本市中央区下通",
                "番地": "1-{}-3".format(i),
                "建物名": "",
                "URL": "https://shop{}.example/".format(i) if i < 49 else "",
                "SSL": "TRUE" if i < 49 else "",
            }
            for i in range(rows)
        ],
        columns=scraper_22.COLUMNS,
    )


def test_2_2_normalize_addresses_preserves_existing_town_split():
    frame = make_frame()
    normalized = scraper_22.normalize_addresses(frame)
    assert normalized.loc[0, "市区町村"] == "熊本市中央区下通"
    assert normalized.loc[0, "番地"] == "1-0-3"


def test_2_2_normalize_addresses_moves_town_from_block_column():
    frame = make_frame()
    frame.loc[0, "市区町村"] = "熊本市中央区"
    frame.loc[0, "番地"] = "下通1-2-3"
    normalized = scraper_22.normalize_addresses(frame)
    assert normalized.loc[0, "市区町村"] == "熊本市中央区下通"
    assert normalized.loc[0, "番地"] == "1-2-3"


def test_2_2_frame_contract_accepts_50_rows_and_49_urls():
    frame = make_frame()
    scraper_22.validate_frame(frame)


def test_2_2_frame_contract_rejects_url_ssl_mismatch():
    frame = make_frame()
    frame.loc[0, "SSL"] = ""
    with pytest.raises(ValueError, match="URLとSSL"):
        scraper_22.validate_frame(frame)


def test_2_2_frame_contract_rejects_duplicate_store_names():
    frame = make_frame()
    frame.loc[1, "店舗名"] = frame.loc[0, "店舗名"]
    with pytest.raises(ValueError, match="重複"):
        scraper_22.validate_frame(frame)


def test_2_2_frame_contract_rejects_wrong_column_order():
    frame = make_frame()
    frame = frame[list(reversed(frame.columns))]
    with pytest.raises(ValueError, match="指定9列"):
        scraper_22.validate_frame(frame)


def test_2_2_frame_contract_rejects_non_boolean_ssl():
    frame = make_frame()
    frame.loc[0, "SSL"] = "UNKNOWN"
    with pytest.raises(ValueError, match="SSL"):
        scraper_22.validate_frame(frame)
