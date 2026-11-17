import requests
import pytest

from shared.gnavi_rules import SiteCandidate
from shared.site_verifier import URLVerifier, resolve_site


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, url=""):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/plain; charset=utf-8"}
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.url = url

    def iter_content(self, chunk_size=65536):
        if self._body:
            yield self._body

    def close(self):
        pass


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.results:
            raise AssertionError("unexpected request: " + url)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        result.url = url
        return result

    def close(self):
        self.closed = True


def robots(body="User-agent: *\nAllow: /\n", status=200, content_type="text/plain; charset=utf-8"):
    return FakeResponse(status, body, {"Content-Type": content_type})


def html_page(title="Shop", body="Shop"):
    return FakeResponse(
        200,
        "<html><head><title>{}</title></head><body><h1>{}</h1></body></html>".format(title, body),
        {"Content-Type": "text/html; charset=utf-8"},
    )


def make_verifier(monkeypatch, results):
    monkeypatch.setattr("shared.site_verifier.time.sleep", lambda seconds: None)
    session = FakeSession(results)
    return URLVerifier(session=session), session


def test_no_link_returns_empty_url_and_ssl(monkeypatch):
    verifier, session = make_verifier(monkeypatch, [])
    result = resolve_site(verifier, [], "Shop", "Chuo", "1-2-3")
    assert (result.url, result.ssl, result.status) == ("", "", "NO_LINK")
    assert session.calls == []


def test_failed_homepage_keeps_its_url_and_never_checks_official_fallback(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [robots(), requests.exceptions.Timeout("homepage timeout")],
    )
    candidates = [
        SiteCandidate("official_page", "https://official.example/", "オフィシャルページ", "href"),
        SiteCandidate("shop_homepage", "https://homepage.example/", "お店のホームページ", "href"),
    ]
    result = resolve_site(verifier, candidates, "Shop", "Chuo", "1-2-3")
    assert result.url == "https://homepage.example/"
    assert result.status == "UNVERIFIED"
    assert result.ssl == "FALSE"
    assert len(session.calls) == 2  # robots + homepage only; no official fallback


def test_unresolved_homepage_is_not_replaced_by_official_page(monkeypatch):
    verifier, session = make_verifier(monkeypatch, [])
    candidates = [
        SiteCandidate("official_page", "https://official.example/", "オフィシャルページ", "href"),
        SiteCandidate("shop_homepage", "", "お店のホームページ", "unresolved"),
    ]
    result = resolve_site(verifier, candidates, "Shop", "Chuo", "1-2-3")
    assert result.status == "UNRESOLVED_LINK"
    assert result.url == ""
    assert session.calls == []


def test_https_404_keeps_url_and_ssl_true(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [robots(), FakeResponse(404, "Not Found", {"Content-Type": "text/html"})],
    )
    result = verifier.verify_url("https://shop.example/missing", "Shop")
    assert result.url == "https://shop.example/missing"
    assert result.ssl == "TRUE"
    assert result.status == "HTTP_ERROR"
    assert result.http_status == 404
    assert all(call[1].get("verify") is True for call in session.calls)


def test_http_404_has_ssl_false(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [robots(), FakeResponse(404, "Not Found", {"Content-Type": "text/html"})],
    )
    result = verifier.verify_url("http://shop.example/missing", "Shop")
    assert result.ssl == "FALSE"
    assert result.status == "HTTP_ERROR"


def test_tls_error_is_distinguished_and_url_is_retained(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [robots(), requests.exceptions.SSLError("certificate verify failed")],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.url == "https://shop.example/"
    assert result.ssl == "FALSE"
    assert result.status == "TLS_ERROR"
    assert "certificate" in result.reason


@pytest.mark.parametrize("error", [requests.exceptions.Timeout("timed out"), requests.exceptions.ConnectTimeout("connect timed out")])
def test_timeout_is_unverified_and_url_is_retained(monkeypatch, error):
    verifier, _ = make_verifier(monkeypatch, [robots(), error])
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.url == "https://shop.example/"
    assert result.ssl == "FALSE"
    assert result.status == "UNVERIFIED"
    assert "TIMEOUT" in result.reason


def test_html_robots_response_fails_closed(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [robots("<html>Unexpected response</html>", content_type="text/html; charset=UTF-8")],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "UNVERIFIED"
    assert result.ssl == "FALSE"
    assert "not text/plain" in result.reason
    assert len(session.calls) == 1  # Never request the store page.


def test_captcha_in_robots_response_is_classified_separately(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [robots("<html>Access denied - verify you are human</html>", content_type="text/html; charset=UTF-8")],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "CAPTCHA"
    assert result.ssl == "FALSE"
    assert len(session.calls) == 1


def test_robots_disallow_prevents_store_request(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [robots("User-agent: *\nDisallow: /private\n")],
    )
    result = verifier.verify_url("https://shop.example/private", "Shop")
    assert result.status == "ROBOTS_DISALLOWED"
    assert result.url == "https://shop.example/private"
    assert len(session.calls) == 1


def test_robots_404_means_no_published_rules_and_http_can_be_checked(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [FakeResponse(404, "", {"Content-Type": "text/html"}), html_page()],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "VERIFIED"
    assert result.ssl == "TRUE"


def test_same_domain_redirect_adopts_final_url(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [
            robots(),
            FakeResponse(301, "", {"Location": "/new-home", "Content-Type": "text/plain"}),
            html_page("Shop", "Shop"),
        ],
    )
    result = verifier.verify_url("https://shop.example/old", "Shop")
    assert result.status == "VERIFIED"
    assert result.url == "https://shop.example/new-home"
    assert result.ssl == "TRUE"


def test_cross_domain_redirect_requires_store_name_match(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [
            robots(),
            FakeResponse(302, "", {"Location": "https://new.example/shop", "Content-Type": "text/plain"}),
            robots(),
            html_page("Shop Official", "Welcome to Shop"),
        ],
    )
    result = verifier.verify_url("https://old.example/", "Shop")
    assert result.status == "VERIFIED"
    assert result.url == "https://new.example/shop"
    assert result.ssl == "TRUE"


def test_cross_domain_redirect_without_identity_keeps_published_url(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [
            robots(),
            FakeResponse(302, "", {"Location": "https://unrelated.example/", "Content-Type": "text/plain"}),
            robots(),
            html_page("Unrelated Company", "Unrelated Company"),
        ],
    )
    result = verifier.verify_url("https://old.example/", "Shop")
    assert result.status == "IDENTITY_UNVERIFIED"
    assert result.url == "https://old.example/"


def test_cross_domain_redirect_matches_store_name_variant():
    matched, reason = URLVerifier.identity_matches(
        "<html><title>LIGNOSA CAFE</title><body>Welcome</body></html>",
        "LIGNOSA CAFE(リグノーサカフェ)",
        "熊本市中央区",
        "下通1-10-3",
    )
    assert matched is True
    assert reason == "store name matched"


def test_cross_domain_redirect_can_match_specific_address(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [
            robots(),
            FakeResponse(302, "", {"Location": "https://new.example/location", "Content-Type": "text/plain"}),
            robots(),
            html_page("Official", "熊本市中央区下通1-10-3"),
        ],
    )
    result = verifier.verify_url("https://old.example/", "Different Store", "熊本市中央区", "下通1-10-3")
    assert result.status == "VERIFIED"


def test_captcha_redirect_never_saves_captcha_url(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [robots(), FakeResponse(302, "", {"Location": "https://challenge.example/captcha", "Content-Type": "text/plain"})],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "CAPTCHA"
    assert result.url == "https://shop.example/"
    assert "challenge.example" not in result.url
    assert len(session.calls) == 2


def test_http_403_captcha_body_is_not_misreported_as_plain_http_error(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [robots(), FakeResponse(403, "<title>Access denied</title>", {"Content-Type": "text/html"})],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "CAPTCHA"
    assert result.url == "https://shop.example/"
    assert result.ssl == "TRUE"


def test_captcha_body_never_replaces_published_url(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [robots(), FakeResponse(200, "<title>Access denied</title>", {"Content-Type": "text/html"})],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "CAPTCHA"
    assert result.url == "https://shop.example/"
    assert result.ssl == "TRUE"


def test_gnavi_intermediary_redirect_is_not_adopted(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [
            robots(),
            FakeResponse(302, "", {"Location": "https://r.gnavi.co.jp/redirect/", "Content-Type": "text/plain"}),
            robots(),
            html_page("Gurunavi", "Gurunavi redirect"),
        ],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "INTERMEDIARY"
    assert result.url == "https://shop.example/"


def test_http_to_https_redirect_sets_ssl_true_on_final_url(monkeypatch):
    verifier, _ = make_verifier(
        monkeypatch,
        [
            robots(),
            FakeResponse(301, "", {"Location": "https://shop.example/home", "Content-Type": "text/plain"}),
            robots(),
            html_page("Shop", "Shop"),
        ],
    )
    result = verifier.verify_url("http://shop.example/", "Shop")
    assert result.status == "VERIFIED"
    assert result.url == "https://shop.example/home"
    assert result.ssl == "TRUE"


def test_user_agent_and_minimum_three_second_wait_are_applied(monkeypatch):
    waits = []
    monkeypatch.setattr("shared.site_verifier.time.sleep", waits.append)
    verifier, session = make_verifier(monkeypatch, [robots(), html_page()])
    # make_verifier patches sleep too; restore the collector after it returns.
    monkeypatch.setattr("shared.site_verifier.time.sleep", waits.append)
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "VERIFIED"
    assert session.headers["User-Agent"] == "ExerciseForPool-Student/1.0"
    assert len(waits) >= 2 and all(seconds >= 3 for seconds in waits)


def test_robots_redirect_is_followed_with_five_hop_limit(monkeypatch):
    verifier, session = make_verifier(
        monkeypatch,
        [
            FakeResponse(302, "", {"Location": "https://cdn.example/robots.txt", "Content-Type": "text/plain"}),
            robots("User-agent: *\nAllow: /\n"),
            html_page("Shop", "Shop"),
        ],
    )
    result = verifier.verify_url("https://shop.example/", "Shop")
    assert result.status == "VERIFIED"
    assert session.calls[0][0] == "https://shop.example/robots.txt"
    assert session.calls[1][0] == "https://cdn.example/robots.txt"


def test_html_meta_charset_overrides_misconfigured_latin1_header():
    raw = '<html><head><meta charset="UTF-8"></head><body>お店のホームページ</body></html>'.encode("utf-8")
    assert "お店のホームページ" in URLVerifier.decode_body(raw, "text/html; charset=iso-8859-1")


def test_close_closes_shared_session(monkeypatch):
    verifier, session = make_verifier(monkeypatch, [])
    verifier.close()
    assert session.closed is True
