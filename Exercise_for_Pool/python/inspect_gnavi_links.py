"""Inspect the static HTML evidence for a Gurunavi restaurant link.

This is a diagnostic tool, not a scraper. It does not execute JavaScript or
attempt to bypass robots.txt, CAPTCHA, or other access restrictions.
Compatible with Python 3.8+.
"""

import argparse
import html as html_lib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from shared.site_verifier import URLVerifier
from shared.gnavi_rules import inspect_labels, extract_direct_email

USER_AGENT = 'ExerciseForPool-Student/1.0'
TARGET_PAGE = 'https://r.gnavi.co.jp/n814504/'
TARGET_LABELS = ('お店のホームページ', 'オフィシャルページ', '公式サイト', 'お店に直接メールする')
FULL_URL_RE = re.compile(r'https?:(?:\\?/\\?/|%2f%2f)[^\s\'"<>]+', re.I)


def flat_text(value):
    return re.sub(r'\s+', '', value or '')


def node_attributes(node):
    """Show the evidence needed to identify an href=# target."""
    if not node:
        return {}
    # Keep attribute evidence. Don't execute content in onclick or data-*.
    result = {}
    for key, val in node.attrs.items():
        if key in ('href', 'onclick', 'id', 'class', 'title', 'aria-label', 'target', 'rel') or key.startswith('data-'):
            result[key] = ' '.join(val) if isinstance(val, list) else str(val)
    return result


def analyze_html(markup, source_url, needles=()):
    soup = BeautifulSoup(markup, 'html.parser')
    evidence = []
    for anchor in soup.find_all(['a', 'button']):
        label = anchor.get_text(' ', strip=True)
        label_compact = flat_text(label)
        heading = anchor.find_parent('tr') or anchor.find_parent('dl')
        header_text = heading.get_text(' ', strip=True) if heading else ''
        if not any(flat_text(needle) in flat_text(label + ' ' + header_text) for needle in TARGET_LABELS):
            continue
        parent = anchor.find_parent(['td', 'dd']) or anchor.parent
        row = anchor.find_parent(['tr', 'dl'])
        sample = str(parent)[:2200] if parent else ''
        candidate_attrs = [
            {'tag': tag.name, 'attrs': node_attributes(tag)}
            for tag in (anchor, anchor.parent, parent)
            if tag and tag.attrs
        ]
        # No assumption that a URL on a different element belongs to this link.
        evidence.append({
            'label': label,
            'tag': anchor.name,
            'attributes': node_attributes(anchor),
            'nearby_attributes': candidate_attrs,
            'parent_text': (parent.get_text(' ', strip=True) if parent else '')[:500],
            'row_label': (row.find(['th', 'dt']).get_text(' ', strip=True) if row and row.find(['th', 'dt']) else '')[:150],
            'nearby_html': sample,
        })
    # Find references to the known illustrative domain in raw HTML. This helps
    # distinguish actual static evidence from a guessed URL.
    target_fragments = []
    for needle in needles:
        found = [m.start() for m in re.finditer(re.escape(needle), markup, re.I)]
        target_fragments.append({
            'needle': needle,
            'count': len(found),
            'examples': [html_lib.unescape(markup[max(0, i - 180): i + 220]) for i in found[:5]],
        })
    return {'source': source_url, 'target_links': evidence, 'resolved_site_links': inspect_labels(markup, source_url), 'direct_email': extract_direct_email(markup), 'target_fragments': target_fragments}


def fetch_html_respecting_robots(url):
    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'r.gnavi.co.jp':
        raise ValueError('For this diagnostic, only https://r.gnavi.co.jp/ URLs are allowed')

    verifier = URLVerifier()
    try:
        decision = verifier.check_robots(url)
        if not decision.allowed:
            raise PermissionError('{}: {}'.format(decision.status, decision.reason))
        time.sleep(3)
        response = verifier.session.get(
            url,
            timeout=(8, 20),
            allow_redirects=False,
            verify=True,
        )
        try:
            if response.is_redirect:
                raise RuntimeError('Unexpected redirect; inspect manually without following it.')
            response.raise_for_status()
            # The page declares UTF-8. Decode the response bytes directly so
            # requests' ISO-8859-1 fallback cannot corrupt Japanese labels.
            return response.content.decode('utf-8-sig', errors='replace')
        finally:
            response.close()
    finally:
        verifier.close()


def main():
    parser = argparse.ArgumentParser(description='Inspect actual static link evidence, including href=#')
    parser.add_argument('--url', default=TARGET_PAGE)
    parser.add_argument('--html-file', type=Path, help='Optional locally saved HTML; no requests will be sent')
    parser.add_argument('--output', default='gnavi_link_evidence.json')
    parser.add_argument('--needle', action='append', default=[], help='Optional search term; repeatable. No restaurant-specific default.')
    args = parser.parse_args()
    markup = args.html_file.read_text(encoding='utf-8') if args.html_file else fetch_html_respecting_robots(args.url)
    result = analyze_html(markup, args.url, needles=args.needle)
    output = Path(args.output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Inspection saved to:', output.resolve())
    print('Matched labels:', [x['label'] for x in result['target_links']])
    print('Resolved candidate URLs:', [x['url'] for x in result['resolved_site_links']])
    print('Domain evidence counts:', [(x['needle'], x['count']) for x in result['target_fragments']])
    print('Inspect nearby_html, attributes, and target_fragments before changing the scraper.')
    print('Do not commit the raw site HTML or diagnostic file without reviewing its content.')


if __name__ == '__main__':
    main()
