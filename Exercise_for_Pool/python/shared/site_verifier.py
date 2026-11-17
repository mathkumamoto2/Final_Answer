"""Respectful URL verification for published Gurunavi shop-homepage links.

The published URL is kept separately from connection, TLS and HTTP results.
All requests use certificate verification, a descriptive User-Agent, robots.txt
checks and a minimum delay. CAPTCHA/access controls are never bypassed.
Compatible with Python 3.8+.
"""

from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "ExerciseForPool-Student/1.0"
WAIT_SECONDS = 3.0
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_CHALLENGE_RE = re.compile(
    r"captcha|verify you are human|access denied|"
    r"robot check|ロボットではない|アクセスが制限|"
    r"セキュリティ確認|reCAPTCHA",
    re.I,
)
_INTERSTITIAL_RE = re.compile(
    r"redirecting|please wait|click here to continue|"
    r"external site|外部サイトへ移動|転送しています|"
    r"リダイレクトしています|ページへ移動します",
    re.I,
)


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    status: str
    reason: str


@dataclass(frozen=True)
class VerificationResult:
    url: str
    ssl: str
    status: str
    reason: str
    http_status: Optional[int] = None
    final_url: str = ""


class URLVerifier:
    """Check robots.txt, TLS, HTTP status and redirected-site identity."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        delay: float = WAIT_SECONDS,
        timeout: Tuple[float, float] = (8.0, 20.0),
        user_agent: str = USER_AGENT,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "ja,en;q=0.8",
            }
        )
        self.delay = max(float(delay), WAIT_SECONDS)
        self.timeout = timeout
        self.user_agent = user_agent
        self._robots_cache: Dict[str, RobotsDecision] = {}
        self._robots_parsers: Dict[str, RobotFileParser] = {}

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def is_public_url(url: str) -> bool:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower().rstrip(".")
            if parts.scheme.lower() not in ("http", "https") or not host:
                return False
            if parts.username or parts.password:
                return False
            if host in ("localhost", "host.docker.internal"):
                return False
            if host.endswith((".local", ".localhost", ".internal", ".test")):
                return False
            try:
                ip = ipaddress.ip_address(host)
                return ip.is_global
            except ValueError:
                return True
        except ValueError:
            return False

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return "{}://{}".format(parts.scheme.lower(), parts.netloc)

    @staticmethod
    def read_limited(response: requests.Response, limit: int) -> bytes:
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise ValueError("response body exceeds {} bytes".format(limit))
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def decode_body(body: bytes, content_type: str = "") -> str:
        charset_match = re.search(r"charset\s*=\s*['\"]?([^; '\"]+)", content_type, re.I)
        meta_match = re.search(
            br"<meta[^>]+charset\s*=\s*['\"]?([^\s'\"/>;]+)",
            body[:8192],
            re.I,
        )
        encodings = []
        header_encoding = charset_match.group(1).strip() if charset_match else ""
        meta_encoding = meta_match.group(1).decode("ascii", errors="ignore") if meta_match else ""
        # Some older Japanese sites send an ISO-8859-1 HTTP header while the
        # actual HTML declares UTF-8. Prefer the explicit HTML declaration in
        # that known-misconfigured case to avoid mojibake in Japanese labels.
        if header_encoding.lower() in ("iso-8859-1", "latin-1") and meta_encoding:
            encodings.append(meta_encoding)
        if header_encoding:
            encodings.append(header_encoding)
        if meta_encoding:
            encodings.append(meta_encoding)
        encodings.extend(["utf-8-sig", "cp932", "utf-8"])
        for encoding in encodings:
            try:
                return body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return body.decode("utf-8", errors="replace")

    def _wait(self) -> None:
        time.sleep(self.delay)

    def _load_robots(self, origin: str) -> RobotsDecision:
        cached = self._robots_cache.get(origin)
        if cached is not None:
            return cached

        current = origin + "/robots.txt"
        parser = RobotFileParser()

        for redirect_count in range(MAX_REDIRECTS + 1):
            if not self.is_public_url(current):
                decision = RobotsDecision(False, "UNVERIFIED", "robots.txt redirect is not a public HTTP(S) URL")
                self._robots_cache[origin] = decision
                return decision

            self._wait()
            try:
                response = self.session.get(
                    current,
                    timeout=self.timeout,
                    allow_redirects=False,
                    stream=True,
                    verify=True,
                )
            except requests.exceptions.SSLError as exc:
                decision = RobotsDecision(False, "UNVERIFIED", "robots TLS_ERROR: {}".format(exc))
                self._robots_cache[origin] = decision
                return decision
            except requests.exceptions.Timeout as exc:
                decision = RobotsDecision(False, "UNVERIFIED", "robots TIMEOUT: {}".format(exc))
                self._robots_cache[origin] = decision
                return decision
            except requests.exceptions.RequestException as exc:
                decision = RobotsDecision(False, "UNVERIFIED", "robots request failed: {}".format(exc))
                self._robots_cache[origin] = decision
                return decision

            try:
                status = int(response.status_code)
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        decision = RobotsDecision(False, "UNVERIFIED", "robots redirect has no Location")
                        self._robots_cache[origin] = decision
                        return decision
                    if redirect_count >= MAX_REDIRECTS:
                        decision = RobotsDecision(False, "UNVERIFIED", "robots redirect limit exceeded")
                        self._robots_cache[origin] = decision
                        return decision
                    current = urljoin(current, location)
                    continue

                if status in (404, 410):
                    parser.parse([])
                    self._robots_parsers[origin] = parser
                    decision = RobotsDecision(True, "ROBOTS_MISSING", "robots.txt returned HTTP {}".format(status))
                    self._robots_cache[origin] = decision
                    return decision

                if status in (401, 403):
                    body = self.read_limited(response, 128 * 1024)
                    text = self.decode_body(body, response.headers.get("Content-Type", ""))
                    if _CHALLENGE_RE.search(text[:8192]):
                        decision = RobotsDecision(False, "CAPTCHA", "robots.txt endpoint returned CAPTCHA/access-control content")
                    else:
                        decision = RobotsDecision(False, "ROBOTS_DISALLOWED", "robots.txt returned HTTP {}".format(status))
                    self._robots_cache[origin] = decision
                    return decision

                if status != 200:
                    decision = RobotsDecision(False, "UNVERIFIED", "robots.txt returned HTTP {}".format(status))
                    self._robots_cache[origin] = decision
                    return decision

                content_type = response.headers.get("Content-Type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                body = self.read_limited(response, 512 * 1024)
                text = self.decode_body(body, content_type)
                if _CHALLENGE_RE.search(text[:8192]):
                    decision = RobotsDecision(False, "CAPTCHA", "robots.txt endpoint returned CAPTCHA/access-control content")
                    self._robots_cache[origin] = decision
                    return decision
                if media_type != "text/plain":
                    decision = RobotsDecision(
                        False,
                        "UNVERIFIED",
                        "robots.txt is not text/plain: {}".format(content_type or "missing Content-Type"),
                    )
                    self._robots_cache[origin] = decision
                    return decision

                parser.parse(text.splitlines())
                self._robots_parsers[origin] = parser
                decision = RobotsDecision(True, "ROBOTS_OK", "robots.txt parsed successfully")
                self._robots_cache[origin] = decision
                return decision
            except (requests.RequestException, ValueError) as exc:
                decision = RobotsDecision(False, "UNVERIFIED", "robots.txt could not be read: {}".format(exc))
                self._robots_cache[origin] = decision
                return decision
            finally:
                response.close()

        decision = RobotsDecision(False, "UNVERIFIED", "robots.txt redirect limit exceeded")
        self._robots_cache[origin] = decision
        return decision

    def check_robots(self, url: str) -> RobotsDecision:
        if not self.is_public_url(url):
            return RobotsDecision(False, "INVALID_URL", "URL is not a public HTTP(S) address")
        origin = self._origin(url)
        decision = self._load_robots(origin)
        if not decision.allowed:
            return decision
        parser = self._robots_parsers.get(origin)
        if parser is None:
            return RobotsDecision(False, "UNVERIFIED", "robots parser is unavailable")
        try:
            allowed = parser.can_fetch(self.user_agent, url)
        except Exception as exc:  # RobotFileParser may reject malformed rules.
            return RobotsDecision(False, "UNVERIFIED", "robots rule evaluation failed: {}".format(exc))
        if not allowed:
            return RobotsDecision(False, "ROBOTS_DISALLOWED", "robots.txt disallows this URL")
        return RobotsDecision(True, decision.status, decision.reason)

    def can_fetch(self, url: str) -> bool:
        return self.check_robots(url).allowed

    @staticmethod
    def _is_gnavi_host(host: str) -> bool:
        host = host.lower().rstrip(".")
        return (
            host in ("gnavi.co.jp", "gurunavi.com")
            or host.endswith((".gnavi.co.jp", ".gurunavi.com"))
        )

    @staticmethod
    def _normalise_identity(value: str) -> str:
        return re.sub(r"[\s\W_]+", "", value or "", flags=re.UNICODE).casefold()

    @classmethod
    def identity_matches(cls, html: str, store_name: str, city: str, block: str) -> Tuple[bool, str]:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        headings = " ".join(node.get_text(" ", strip=True) for node in soup.select("h1, h2"))
        visible = " ".join([title, headings, soup.get_text(" ", strip=True)])
        normal_visible = cls._normalise_identity(visible)

        # Store names often include a parenthetical reading or a branch suffix.
        # Accept a meaningful explicit segment (e.g. "LIGNOSA CAFE" from
        # "LIGNOSA CAFE(リグノーサカフェ)") rather than requiring every alias
        # to appear on the destination page.
        name_variants = [
            cls._normalise_identity(part)
            for part in re.split(r"[()（）\[\]【】/／|・]", store_name or "")
        ]
        name_variants.append(cls._normalise_identity(store_name))
        if any(len(variant) >= 4 and variant in normal_visible for variant in name_variants):
            return True, "store name matched"

        normal_city = cls._normalise_identity(city)
        normal_block = cls._normalise_identity(block)
        if len(normal_city) >= 2 and len(re.sub(r"\D", "", normal_block)) >= 3:
            if normal_city in normal_visible and normal_block in normal_visible:
                return True, "specific address matched"

        return False, "store name and specific address did not match"

    @staticmethod
    def _looks_interstitial(url: str, html: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if URLVerifier._is_gnavi_host(host):
            return True
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        return bool(_INTERSTITIAL_RE.search(title + " " + soup.get_text(" ", strip=True)[:4000]))

    def verify_url(self, published_url: str, store_name: str = "", city: str = "", block: str = "") -> VerificationResult:
        """Verify a published URL without losing it when the check fails."""
        original = (published_url or "").strip()
        if not original:
            return VerificationResult("", "", "NO_LINK", "No homepage/official-page link was published")
        if not self.is_public_url(original):
            return VerificationResult(original, "FALSE", "INVALID_URL", "Published URL is not a safe public HTTP(S) URL")

        # A Gurunavi URL is an intermediary, not the shop's own published URL.
        # Reject it BEFORE any network request; never persist it into CSV/MySQL.
        original_host = (urlsplit(original).hostname or "").lower().rstrip(".")
        if self._is_gnavi_host(original_host):
            return VerificationResult(
                "", "", "INTERMEDIARY",
                "Gurunavi intermediary URL is not a published external shop website",
                final_url=original,
            )

        original_tls_verified = False
        current = original

        for hop in range(MAX_REDIRECTS + 1):
            robots = self.check_robots(current)
            if not robots.allowed:
                return VerificationResult(
                    original,
                    "TRUE" if original_tls_verified else "FALSE",
                    robots.status,
                    robots.reason,
                    final_url=current,
                )

            self._wait()
            try:
                response = self.session.get(
                    current,
                    timeout=self.timeout,
                    allow_redirects=False,
                    stream=True,
                    verify=True,
                )
            except requests.exceptions.SSLError as exc:
                return VerificationResult(
                    original,
                    "TRUE" if original_tls_verified else "FALSE",
                    "TLS_ERROR",
                    "TLS handshake/certificate verification failed: {}".format(exc),
                    final_url=current,
                )
            except requests.exceptions.Timeout as exc:
                return VerificationResult(
                    original,
                    "FALSE",
                    "UNVERIFIED",
                    "TIMEOUT: {}".format(exc),
                    final_url=current,
                )
            except requests.exceptions.RequestException as exc:
                return VerificationResult(
                    original,
                    "FALSE",
                    "UNVERIFIED",
                    "HTTP connection failed: {}".format(exc),
                    final_url=current,
                )

            try:
                status = int(response.status_code)
                if hop == 0 and urlsplit(original).scheme.lower() == "https":
                    # A returned HTTPS response proves that TLS certificate
                    # verification succeeded, even when the HTTP status is 404.
                    original_tls_verified = True

                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            "REDIRECT_ERROR",
                            "HTTP {} without Location".format(status),
                            http_status=status,
                            final_url=current,
                        )
                    if hop >= MAX_REDIRECTS:
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            "REDIRECT_ERROR",
                            "redirect limit exceeded",
                            http_status=status,
                            final_url=current,
                        )
                    target = urljoin(current, location)
                    if not self.is_public_url(target):
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            "REDIRECT_ERROR",
                            "redirect target is not a public HTTP(S) URL",
                            http_status=status,
                            final_url=target,
                        )
                    if re.search(r"captcha|challenge|verify", target, re.I):
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            "CAPTCHA",
                            "redirect target appears to be a CAPTCHA/challenge URL",
                            http_status=status,
                            final_url=target,
                        )
                    current = target
                    continue

                if status >= 400:
                    try:
                        error_body = self.read_limited(response, 512 * 1024)
                        error_text = self.decode_body(
                            error_body, response.headers.get("Content-Type", "")
                        )
                    except requests.RequestException as exc:
                        # The status code is already known. A broken error
                        # body must not erase a verified TLS result or HTTP status.
                        error_text = ""
                        body_note = "; error body could not be read: {}".format(exc)
                    else:
                        body_note = ""
                    if _CHALLENGE_RE.search(error_text[:8192]):
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            "CAPTCHA",
                            "HTTP {} response contains CAPTCHA/access-control content".format(status),
                            http_status=status,
                            final_url=current,
                        )
                    return VerificationResult(
                        original,
                        "TRUE" if original_tls_verified else "FALSE",
                        "HTTP_ERROR",
                        "HTTP {} at {}{}".format(status, current, body_note),
                        http_status=status,
                        final_url=current,
                    )

                body = self.read_limited(response, MAX_RESPONSE_BYTES)
                content_type = response.headers.get("Content-Type", "")
                page = self.decode_body(body, content_type)
                soup = BeautifulSoup(page, "html.parser")
                title = soup.title.get_text(" ", strip=True) if soup.title else ""
                if _CHALLENGE_RE.search(title + " " + soup.get_text(" ", strip=True)[:4000]):
                    return VerificationResult(
                        original,
                        "TRUE" if original_tls_verified else "FALSE",
                        "CAPTCHA",
                        "CAPTCHA/access-control page detected; published URL retained",
                        http_status=status,
                        final_url=current,
                    )

                original_host = (urlsplit(original).hostname or "").lower().rstrip(".")
                final_host = (urlsplit(current).hostname or "").lower().rstrip(".")
                cross_domain = original_host != final_host
                if cross_domain:
                    if self._is_gnavi_host(final_host):
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            "INTERMEDIARY",
                            "redirect ended on a Gurunavi intermediary page",
                            http_status=status,
                            final_url=current,
                        )
                    matched, evidence = self.identity_matches(page, store_name, city, block)
                    if not matched:
                        status_name = "INTERMEDIARY" if self._looks_interstitial(current, page) else "IDENTITY_UNVERIFIED"
                        return VerificationResult(
                            original,
                            "TRUE" if original_tls_verified else "FALSE",
                            status_name,
                            evidence,
                            http_status=status,
                            final_url=current,
                        )

                final_ssl = "TRUE" if urlsplit(current).scheme.lower() == "https" else "FALSE"
                return VerificationResult(
                    current,
                    final_ssl,
                    "VERIFIED",
                    "TLS verified" if final_ssl == "TRUE" else "HTTP site; no TLS certificate",
                    http_status=status,
                    final_url=current,
                )
            except (requests.RequestException, ValueError) as exc:
                return VerificationResult(
                    original,
                    "TRUE" if original_tls_verified else "FALSE",
                    "UNVERIFIED",
                    "response could not be read: {}".format(exc),
                    http_status=getattr(response, "status_code", None),
                    final_url=current,
                )
            finally:
                response.close()

        return VerificationResult(
            original,
            "TRUE" if original_tls_verified else "FALSE",
            "REDIRECT_ERROR",
            "redirect limit exceeded",
            final_url=current,
        )


def resolve_site(verifier: URLVerifier, candidates, store_name: str = "", city: str = "", block: str = "") -> VerificationResult:
    """Apply homepage-first priority and verify only the selected candidate."""
    from shared.gnavi_rules import select_priority

    selected = select_priority(list(candidates or []))
    if selected is None:
        return VerificationResult("", "", "NO_LINK", "No recognised homepage/official-page link")
    if not selected.url:
        return VerificationResult(
            "",
            "",
            "UNRESOLVED_LINK",
            "{} link exists but no URL could be statically resolved ({})".format(selected.label, selected.source),
        )
    result = verifier.verify_url(selected.url, store_name, city, block)
    return result
