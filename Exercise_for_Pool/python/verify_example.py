"""Live acceptance check for the reviewer-provided LIGNOSA CAFE page.

The expected website and mail are test assertions only; the scraper itself
never hard-codes them or uses them as extraction fallbacks.
"""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path

from shared.gnavi_rules import extract_direct_email, extract_site_candidates, inspect_labels, select_priority

SOURCE_URL = "https://r.gnavi.co.jp/n814504/"
EXPECTED_PUBLISHED = "http://lignosa.com/"
EXPECTED_EMAIL = "info@lignosa.com"


def format_label_evidence(rows):
    """Format actual inspect_labels() records using their public keys."""
    return [
        "  {} -> {} source={}".format(row["label"], row["url"], row["source"])
        for row in rows
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorized", action="store_true")
    args = parser.parse_args()
    if not args.authorized:
        parser.error("Confirm permission and site terms, then use --authorized")
    scraper = runpy.run_path(
        str(Path(__file__).resolve().parent / "ex1_web-scraping" / "1-1.py"),
        run_name="__example_test__",
    )
    fetcher = scraper["Fetcher"]()
    try:
        _, markup = fetcher.get(SOURCE_URL)
        chosen = select_priority(extract_site_candidates(markup, SOURCE_URL))
        email = extract_direct_email(markup)
        print("Label evidence:")
        for line in format_label_evidence(inspect_labels(markup, SOURCE_URL)):
            print(line)
        print("Chosen published URL:", chosen.url if chosen else None)
        print("Shop email:", email)
        assert chosen is not None, "Missing site link"
        assert chosen.kind == "shop_homepage", "Homepage must take precedence"
        assert chosen.url == EXPECTED_PUBLISHED, (
            "Expected published URL not statically resolved from actual HTML. "
            "Inspect gnavi_link_evidence.json and modify the parser from evidence."
        )
        assert email == EXPECTED_EMAIL, "Did not extract the direct-shop mail only"
        print("[PASS] LIGNOSA CAFE: published homepage and direct email extraction")
    finally:
        fetcher.session.close()


if __name__ == "__main__":
    main()
