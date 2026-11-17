from inspect_gnavi_links import analyze_html


def test_diagnostic_does_not_keep_lignosa_hardcoded():
    result = analyze_html(
        '<a href="https://uokura-kumamoto.com/">公式サイト 最新情報やブログなど多数掲載</a>',
        'https://r.gnavi.co.jp/example/',
    )
    assert result['target_fragments'] == []
    assert result['resolved_site_links'][0]['url'] == 'https://uokura-kumamoto.com/'


def test_diagnostic_explicit_needle():
    result = analyze_html('<p>uokura-kumamoto.com</p>', 'https://r.gnavi.co.jp/example/', needles=['uokura-kumamoto.com'])
    assert result['target_fragments'][0]['count'] == 1
