"""Local ChromeDriver smoke test: opens about:blank, no website scraping."""
from __future__ import annotations

import runpy
from pathlib import Path


def main():
    script = Path(__file__).resolve().parent / "ex1_web-scraping" / "1-2.py"
    namespace = runpy.run_path(str(script), run_name="__smoke_test__")
    driver = None
    try:
        driver = namespace["create_driver"]()
        driver.get("about:blank")
        capabilities = driver.capabilities
        print("[PASS] ChromeDriver + Chrome launched without Selenium Manager")
        print("[INFO] Chrome browser:", capabilities.get("browserVersion", "unknown"))
        print("[INFO] ChromeDriver:", capabilities.get("chrome", {}).get("chromedriverVersion", "unknown"))
    finally:
        namespace["safe_quit"](driver)


if __name__ == "__main__":
    main()
