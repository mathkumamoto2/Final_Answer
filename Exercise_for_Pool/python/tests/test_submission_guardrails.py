"""Regression tests for Homma review: real evidence format and intermediary URLs."""

import pytest

from shared.gnavi_rules import SiteCandidate, inspect_labels
from shared.site_verifier import URLVerifier, resolve_site
from verify_example import format_label_evidence

BASE = "https://r.gnavi.co.jp/n814504/"


def test_lignosa_live_verifier_can_print_evidence_without_keyerror():
    html = r"""<a href="#" data-o='{"a":"lignosa.com\/","b":"http"}'>お店のホームページ</a>"""
    lines = format_label_evidence(inspect_labels(html, BASE))
    assert len(lines) == 1
    assert "お店のホームページ" in lines[0]
    assert "http://lignosa.com/" in lines[0]
    assert "source=data-o" in lines[0]


class NoNetworkSession:
    def __init__(self):
        self.headers = {}
    def get(self, *args, **kwargs):
        raise AssertionError("No network request is allowed for intermediary URL")
    def close(self):
        pass


@pytest.mark.parametrize(
    "intermediary",
    [
        "https://r.gnavi.co.jp/redirect/",
        "https://gnavi.co.jp/somewhere",
        "https://www.gurunavi.com/redirect/",
        "http://foo.gnavi.co.jp/redirect/?next=shop",
    ],
)
def test_gnavi_published_intermediary_is_not_saved(intermediary):
    verifier = URLVerifier(session=NoNetworkSession())
    result = verifier.verify_url(intermediary, "Test Store")
    assert result.status == "INTERMEDIARY"
    assert result.url == ""
    assert result.ssl == ""
    assert result.final_url == intermediary  # diagnostics only; NOT CSV output


def test_gnavi_intermediary_does_not_fall_back_to_unrelated_official_page():
    verifier = URLVerifier(session=NoNetworkSession())
    candidates = [
        SiteCandidate("shop_homepage", "https://r.gnavi.co.jp/redirect/", "お店のホームページ", "href"),
        SiteCandidate("official_page", "https://official.example/", "オフィシャルページ", "href"),
    ]
    result = resolve_site(verifier, candidates, "Test Store")
    assert result.status == "INTERMEDIARY"
    assert (result.url, result.ssl) == ("", "")


def test_gorp_official_page_is_not_mistaken_for_gnavi_intermediary(monkeypatch):
    from shared.site_verifier import RobotsDecision
    verifier = URLVerifier(session=NoNetworkSession())
    monkeypatch.setattr(verifier, "check_robots", lambda url: RobotsDecision(False, "UNVERIFIED", "simulated robots error"))
    result = verifier.verify_url("https://store.gorp.jp/", "Test Store")
    assert result.url == "https://store.gorp.jp/"
    assert result.ssl == "FALSE"
    assert result.status == "UNVERIFIED"
