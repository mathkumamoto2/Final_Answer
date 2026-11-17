import pytest

from shared.gnavi_rules import (
    extract_direct_email,
    extract_site_candidates,
    inspect_labels,
    select_priority,
    url_from_gnavi_data_o,
)

BASE = "https://r.gnavi.co.jp/n814504/"


def test_data_o_static_url_is_decoded_without_javascript():
    raw = r'{"a":"lignosa.com\/","b":"http"}'
    assert url_from_gnavi_data_o(raw, BASE) == "http://lignosa.com/"


def test_reviewer_provided_lignosa_html_fragment_extracts_expected_values():
    # The attributes and labels are taken from the uploaded HTML evidence.
    html = r'''<li><a href="#" class="url go-off" target="_blank" data-o='{"a":"lignosa.com\/","b":"http"}'>お店のホームページ</a></li>
    <li><a href="mailto:info@lignosa.com">お店に直接メールする</a></li>'''
    candidates = extract_site_candidates(html, BASE)
    selected = select_priority(candidates)
    assert selected is not None and selected.url == "http://lignosa.com/"
    assert extract_direct_email(html) == "info@lignosa.com"


def test_homepage_has_priority_over_official_page_even_if_listed_later():
    html = '''<ul>
      <li><a href="https://official.example/">オフィシャルページ</a></li>
      <li><a href="#" data-o='{"a":"lignosa.com\\/","b":"http"}'>お店のホームページ</a></li>
    </ul>'''
    selected = select_priority(extract_site_candidates(html, BASE))
    assert selected is not None
    assert selected.kind == "shop_homepage"
    assert selected.url == "http://lignosa.com/"


def test_official_page_is_fallback_when_homepage_field_is_absent():
    html = '<a href="https://official.example/">オフィシャルページ</a>'
    selected = select_priority(extract_site_candidates(html, BASE))
    assert selected is not None
    assert selected.kind == "official_page"
    assert selected.url == "https://official.example/"


def test_unresolved_homepage_blocks_official_page_fallback():
    html = '''<a href="#" class="go-off">お店のホームページ</a>
              <a href="https://official.example/">オフィシャルページ</a>'''
    selected = select_priority(extract_site_candidates(html, BASE))
    assert selected is not None
    assert selected.kind == "shop_homepage"
    assert selected.url == ""


def test_data_href_is_used_only_on_labelled_anchor():
    html = '''<a href="#" data-href="https://shop.example/">お店のホームページ</a>
              <a href="#" data-href="https://unrelated.example/">予約はこちら</a>'''
    candidates = extract_site_candidates(html, BASE)
    assert len(candidates) == 1
    assert candidates[0].url == "https://shop.example/"


def test_onclick_literal_url_is_staticly_extracted():
    html = '''<a href="#" onclick="window.open('https://shop.example/path')">お店のホームページ</a>'''
    candidates = extract_site_candidates(html, BASE)
    assert candidates[0].url == "https://shop.example/path"
    assert candidates[0].source == "onclick-literal"


def test_explicit_id_reference_script_is_parsed_without_execution():
    html = '''<a id="shop-home" href="#">お店のホームページ</a>
      <script>document.getElementById("shop-home").href = "https://shop.example/";</script>'''
    candidates = extract_site_candidates(html, BASE)
    assert candidates[0].url == "https://shop.example/"
    assert candidates[0].source == "script-id-reference"


def test_table_heading_is_associated_with_anchor_label():
    html = '''<table><tr><th>お店のホームページ</th>
              <td><a href="https://shop.example/">ホームページを開く</a></td></tr></table>'''
    candidates = extract_site_candidates(html, BASE)
    assert len(candidates) == 1
    assert candidates[0].kind == "shop_homepage"


def test_data_o_invalid_json_remains_unresolved():
    html = '''<a href="#" data-o="not-json">お店のホームページ</a>'''
    candidates = extract_site_candidates(html, BASE)
    assert len(candidates) == 1
    assert candidates[0].url == ""


@pytest.mark.parametrize(
    "href",
    ["javascript:void(0)", "mailto:shop@example.com", "tel:0123456789", "#"],
)
def test_non_http_href_is_not_used_as_site_url(href):
    html = '<a href="{}">お店のホームページ</a>'.format(href)
    candidates = extract_site_candidates(html, BASE)
    assert candidates
    assert candidates[0].url == ""


def test_direct_shop_email_is_extracted():
    html = '''<a href="mailto:info@shop.example">お店に直接メールする</a>'''
    assert extract_direct_email(html) == "info@shop.example"


def test_unrelated_mailto_before_direct_shop_email_is_ignored():
    html = '''<a href="mailto:admin@example.com">運営会社へメール</a>
              <a href="mailto:info@shop.example">お店に直接メールする</a>'''
    assert extract_direct_email(html) == "info@shop.example"


@pytest.mark.parametrize(
    "html",
    [
        '<a href="mailto:admin@example.com">運営会社へメール</a>',
        '<script type="application/ld+json">{"email":"json@example.com"}</script>',
        '<a href="mailto:info@shop.example">お問い合わせ</a>',
        '<a href="mailto:info@shop.example">メールする</a>',
        '<p>info@shop.example</p>',
    ],
)
def test_non_direct_email_sources_are_rejected(html):
    assert extract_direct_email(html) == ""


def test_direct_email_visible_text_without_mailto_is_allowed():
    html = '<a>お店に直接メールする info@shop.example</a>'
    assert extract_direct_email(html) == "info@shop.example"


def test_inspect_labels_exposes_source_evidence():
    html = '''<a href="#" data-o='{"a":"shop.example/","b":"https"}'>お店のホームページ</a>'''
    evidence = inspect_labels(html, BASE)
    assert evidence[0]["source"] == "data-o"
    assert evidence[0]["url"] == "https://shop.example/"


def test_official_site_label_with_description_is_recognized():
    html = '<a href="https://uokura-kumamoto.com/">公式サイト 最新情報やブログなど多数掲載</a>'
    candidates = extract_site_candidates(html, BASE)
    assert len(candidates) == 1
    assert candidates[0].kind == "official_page"
    assert candidates[0].url == "https://uokura-kumamoto.com/"


def test_shop_homepage_still_beats_official_site():
    html = (
        '<a href="https://official.example/">公式サイト</a>'
        '<a href="#" data-o=\'{"a":"shop.example/","b":"https"}\'>お店のホームページ</a>'
    )
    candidate = select_priority(extract_site_candidates(html, BASE))
    assert candidate is not None
    assert candidate.kind == "shop_homepage"
    assert candidate.url == "https://shop.example/"


def test_unrelated_generic_site_link_not_accepted():
    html = '<a href="https://ads.example/">おすすめサイト</a>'
    assert extract_site_candidates(html, BASE) == []
