"""Exercise 1-1 static parse against a synthetic restaurant detail page."""
import runpy
from pathlib import Path

import pytest

S1 = Path(__file__).resolve().parents[1] / 'ex1_web-scraping' / '1-1.py'


def test_1_1_parse_store_home_and_mail_without_using_unrelated_jsonld():
    namespace = runpy.run_path(str(S1), run_name='__unit_test__')
    markup = '''<html><head><title>My restaurant</title>
    <script type="application/ld+json">{"@type":"Restaurant","name":"LIGNOSA CAFE","telephone":"052-111-1111","email":"bad@site.example"}</script></head>
    <body><table><tr><th>住所</th><td>〒458-0851 愛知県名古屋市緑区熊の前1-801</td></tr></table>
    <a href="mailto:bad@site.example">運営に問い合わせる</a>
    <a href="#" onclick="window.open('http://lignosa.com/')">お店のホームページ</a>
    <a href="https://n814504.gorp.jp/">オフィシャル ページ</a>
    <a href="mailto:info@lignosa.com">お店に直接メールする</a></body></html>'''
    row, links = namespace['parse_store'](markup, 'https://r.gnavi.co.jp/n814504/')
    assert row['店舗名'] == 'LIGNOSA CAFE'
    assert row['メールアドレス'] == 'info@lignosa.com'
    assert row['市区町村'] == '名古屋市緑区熊の前'
    assert row['番地'] == '1-801'
    assert namespace['extract_site_candidates'](markup, 'https://r.gnavi.co.jp/n814504/') == links
    from sys import path
    if str(S1.parent.parent) not in path:
        path.insert(0, str(S1.parent.parent))
    from shared.gnavi_rules import select_priority
    assert select_priority(links).url == 'http://lignosa.com/'
