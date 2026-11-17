"""Shared, static HTML extraction rules for Gurunavi store pages.

No JavaScript is executed. Only attributes attached to the labelled link
(or a URL explicitly referenced by that link) are considered evidence.
Compatible with Python 3.8+.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

SHOP_HOME_LABEL = "お店のホームページ"
OFFICIAL_LABEL = "オフィシャルページ"
OFFICIAL_SITE_LABEL = "公式サイト"
DIRECT_EMAIL_LABEL = "お店に直接メールする"

_SPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+", re.I)
_JS_URL_RE = re.compile(
    r"(?:window\s*\.\s*open|location\s*\.\s*(?:assign|replace)|location\s*=)\s*\(?\s*['\"]([^'\"]+)['\"]",
    re.I,
)


@dataclass(frozen=True)
class SiteCandidate:
    """A URL tied to a specific labelled Gurunavi field."""

    kind: str
    url: str
    label: str
    source: str

    @property
    def priority(self) -> int:
        return 0 if self.kind == "shop_homepage" else 1


def _compact(value: str) -> str:
    return _SPACE_RE.sub("", value or "").replace("　", "")


def _anchor_label(anchor: Tag) -> str:
    """Read label text from the anchor or its directly associated table row."""
    own = " ".join(
        [
            anchor.get_text(" ", strip=True),
            str(anchor.get("aria-label", "")),
            str(anchor.get("title", "")),
        ]
    )
    cell = anchor.find_parent(["td", "dd"])
    heading_text = ""
    if cell is not None:
        row = cell.find_parent("tr")
        if row is not None:
            heading = row.find("th")
            heading_text = heading.get_text(" ", strip=True) if heading else ""
        else:
            definition = cell.find_parent("dl")
            if definition is not None:
                heading = definition.find("dt")
                heading_text = heading.get_text(" ", strip=True) if heading else ""

    # Combine the anchor label and its directly associated field heading.
    # This supports anchors such as "こちら" inside the homepage table row
    # without matching unrelated links elsewhere on the page.
    return " ".join(part for part in (own, heading_text) if part.strip())


def _kind_for_label(label: str) -> Optional[str]:
    compact = _compact(label)
    if _compact(SHOP_HOME_LABEL) in compact:
        return "shop_homepage"
    if any(_compact(label) in compact for label in (OFFICIAL_LABEL, OFFICIAL_SITE_LABEL)):
        return "official_page"
    return None


def _valid_http_url(value: str, base_url: str) -> str:
    value = html_lib.unescape(unquote(str(value or "").strip()))
    if not value or value.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    absolute = urljoin(base_url, value)
    try:
        parts = urlsplit(absolute)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    if parts.username or parts.password:
        return ""
    return absolute


def url_from_gnavi_data_o(raw_value: str, base_url: str) -> str:
    """Decode Gurunavi's data-o JSON without executing its JavaScript.

    Example: {"a":"lignosa.com\\/","b":"http"} -> http://lignosa.com/
    """
    if not raw_value:
        return ""
    try:
        payload = json.loads(html_lib.unescape(raw_value))
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""

    address = payload.get("a")
    scheme = payload.get("b")
    if not isinstance(address, str) or not address.strip():
        return ""
    address = html_lib.unescape(address.strip()).replace("\\/", "/")

    if re.match(r"^https?://", address, re.I):
        candidate = address
    elif address.startswith("//"):
        candidate = (str(scheme or urlsplit(base_url).scheme or "https") + ":" + address)
    else:
        scheme_text = str(scheme or "").strip().lower().rstrip(":")
        if scheme_text not in ("http", "https"):
            return ""
        # data-o.a is a host/path pair, not an arbitrary relative URL.
        if address.startswith("/") or not re.match(r"^[A-Za-z0-9.-]+(?::\d+)?(?:/|$)", address):
            return ""
        candidate = scheme_text + "://" + address

    return _valid_http_url(candidate, base_url)


def _url_from_explicit_id_script(soup: BeautifulSoup, anchor_id: str, base_url: str) -> str:
    """Resolve only literal URLs assigned by a script to this exact element ID."""
    if not anchor_id:
        return ""
    quoted_id = re.escape(anchor_id)
    patterns = [
        re.compile(
            r"getElementById\s*\(\s*['\"]" + quoted_id
            + r"['\"]\s*\)[\s\S]{0,240}?\.href\s*=\s*['\"]([^'\"]+)['\"]",
            re.I,
        ),
        re.compile(
            r"getElementById\s*\(\s*['\"]" + quoted_id
            + r"['\"]\s*\)[\s\S]{0,240}?setAttribute\s*\(\s*['\"]href['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            re.I,
        ),
        re.compile(
            r"(?:\$|jQuery)\s*\(\s*['\"]#" + quoted_id
            + r"['\"]\s*\)\s*\.attr\s*\(\s*['\"]href['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            re.I,
        ),
        re.compile(
            r"querySelector\s*\(\s*['\"]#" + quoted_id
            + r"['\"]\s*\)[\s\S]{0,240}?\.href\s*=\s*['\"]([^'\"]+)['\"]",
            re.I,
        ),
    ]
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text()
        for pattern in patterns:
            match = pattern.search(script_text or "")
            if match:
                resolved = _valid_http_url(match.group(1), base_url)
                if resolved:
                    return resolved
    return ""


def _url_from_anchor(anchor: Tag, base_url: str, soup: Optional[BeautifulSoup] = None) -> tuple[str, str]:
    href = str(anchor.get("href", "")).strip()
    direct = _valid_http_url(href, base_url)
    if direct:
        return direct, "href"

    data_o = anchor.get("data-o")
    if isinstance(data_o, str):
        decoded = url_from_gnavi_data_o(data_o, base_url)
        if decoded:
            return decoded, "data-o"

    for attribute in ("data-url", "data-href", "data-link", "data-target-url"):
        raw = anchor.get(attribute)
        if isinstance(raw, str):
            decoded = _valid_http_url(raw, base_url)
            if decoded:
                return decoded, attribute

    onclick = str(anchor.get("onclick", ""))
    match = _JS_URL_RE.search(onclick)
    if match:
        decoded = _valid_http_url(match.group(1), base_url)
        if decoded:
            return decoded, "onclick-literal"

    if soup is not None:
        script_url = _url_from_explicit_id_script(soup, str(anchor.get("id", "")), base_url)
        if script_url:
            return script_url, "script-id-reference"

    return "", "unresolved"


def inspect_labels(markup: str, base_url: str) -> List[Dict[str, str]]:
    """Return auditable evidence for homepage/official-page anchors."""
    soup = BeautifulSoup(markup, "html.parser")
    evidence: List[Dict[str, str]] = []
    for anchor in soup.find_all("a"):
        label = _anchor_label(anchor)
        kind = _kind_for_label(label)
        if kind is None:
            continue
        resolved, source = _url_from_anchor(anchor, base_url, soup)
        evidence.append(
            {
                "kind": kind,
                "label": label.strip(),
                "url": resolved,
                "source": source,
                "href": str(anchor.get("href", "")),
                "data_o": str(anchor.get("data-o", "")),
            }
        )
    return evidence


def extract_site_candidates(markup: str, base_url: str) -> List[SiteCandidate]:
    """Extract URLs tied to recognized Gurunavi homepage/official labels.

    An unresolved homepage link is retained as a candidate so it still blocks
    fallback to a lower-priority official-page link.
    """
    return [
        SiteCandidate(
            kind=item["kind"],
            url=item["url"],
            label=item["label"],
            source=item["source"],
        )
        for item in inspect_labels(markup, base_url)
    ]


def select_priority(candidates: List[SiteCandidate]) -> Optional[SiteCandidate]:
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.priority)[0]


def _direct_email_label(anchor: Tag) -> bool:
    label = _compact(_anchor_label(anchor))
    return _compact(DIRECT_EMAIL_LABEL) in label


def extract_direct_email(markup: str) -> str:
    """Return only an email explicitly attached to 'お店に直接メールする'."""
    soup = BeautifulSoup(markup, "html.parser")
    for anchor in soup.find_all("a"):
        if not _direct_email_label(anchor):
            continue

        href = str(anchor.get("href", "")).strip()
        if href.lower().startswith("mailto:"):
            address = unquote(href[7:].split("?", 1)[0]).strip()
            match = _EMAIL_RE.search(address)
            if match:
                return match.group(0)

        # Some pages render the address as text rather than mailto:.
        match = _EMAIL_RE.search(anchor.get_text(" ", strip=True))
        if match:
            return match.group(0)

    return ""
