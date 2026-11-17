import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.site_verifier import RobotsDecision

ROOT = Path(__file__).resolve().parents[1]


def _load_scraper():
    import importlib.util
    path = ROOT / "ex1_web-scraping" / "1-2.py"
    spec = importlib.util.spec_from_file_location("scraper_12_safety", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scraper = _load_scraper()


class FakeTimeoutException(Exception):
    pass


def _install_fake_selenium(monkeypatch, webdriver_module=None):
    """Selenium未導入のテスト環境でもWebDriver境界をモックできるようにする。"""
    selenium_module = types.ModuleType("selenium")
    common_module = types.ModuleType("selenium.common")
    exceptions_module = types.ModuleType("selenium.common.exceptions")
    exceptions_module.TimeoutException = FakeTimeoutException

    if webdriver_module is None:
        webdriver_module = types.ModuleType("selenium.webdriver")

    support_module = types.ModuleType("selenium.webdriver.support")
    ui_module = types.ModuleType("selenium.webdriver.support.ui")
    ui_module.WebDriverWait = object

    chrome_module = types.ModuleType("selenium.webdriver.chrome")
    service_module = types.ModuleType("selenium.webdriver.chrome.service")

    selenium_module.webdriver = webdriver_module
    monkeypatch.setitem(sys.modules, "selenium", selenium_module)
    monkeypatch.setitem(sys.modules, "selenium.common", common_module)
    monkeypatch.setitem(sys.modules, "selenium.common.exceptions", exceptions_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver", webdriver_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.support", support_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.support.ui", ui_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome", chrome_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome.service", service_module)
    return webdriver_module, service_module


def test_create_driver_uses_selenium_manager_not_bundled_driver(monkeypatch, tmp_path):
    project_dir = Path(scraper.__file__).resolve().parent
    captured = {}

    class FakeOptions:
        def __init__(self):
            self.arguments = []
            self.page_load_strategy = "normal"
        def add_argument(self, value):
            self.arguments.append(value)

    class FakeService:
        def __init__(self):
            self.path = None
            self.process = None
        def stop(self):
            captured["stopped"] = True

    class FakeDriver:
        def __init__(self):
            self.service = SimpleNamespace(
                path=str(tmp_path / "selenium-cache" / "chromedriver.exe"),
                process=None,
            )
            self.command_executor = SimpleNamespace(
                client_config=SimpleNamespace(timeout=120)
            )
            self.page_timeout = None
            self.script_timeout = None
        def set_page_load_timeout(self, value):
            self.page_timeout = value
        def set_script_timeout(self, value):
            self.script_timeout = value

    fake_driver = FakeDriver()
    webdriver_module, service_module = _install_fake_selenium(monkeypatch)
    webdriver_module.ChromeOptions = FakeOptions
    service_module.Service = FakeService

    original_path = str(project_dir) + os.pathsep + str(tmp_path / "other")
    monkeypatch.setenv("PATH", original_path)
    monkeypatch.setenv("SE_CHROMEDRIVER", str(project_dir / "chromedriver.exe"))

    def fake_chrome(*, service, options):
        captured["service"] = service
        captured["options"] = options
        captured["path_during_start"] = os.environ.get("PATH", "")
        captured["driver_override_during_start"] = os.environ.get("SE_CHROMEDRIVER")
        return fake_driver

    webdriver_module.Chrome = fake_chrome

    result = scraper.create_driver()

    assert result is fake_driver
    assert captured["service"].path is None
    assert captured["driver_override_during_start"] is None
    assert str(project_dir) not in captured["path_during_start"]
    assert os.environ["PATH"] == original_path
    assert "SE_CHROMEDRIVER" not in os.environ
    assert captured["options"].page_load_strategy == "normal"
    assert fake_driver.page_timeout == scraper.PAGE_LOAD_TIMEOUT
    assert fake_driver.script_timeout == scraper.SCRIPT_TIMEOUT
    assert fake_driver.command_executor.client_config.timeout == scraper.WEBDRIVER_COMMAND_TIMEOUT


def test_safe_quit_suppresses_remote_quit_failure_and_stops_service():
    class FakeService:
        def __init__(self):
            self.process = None
            self.stopped = False
        def stop(self):
            self.stopped = True

    service = FakeService()
    driver = SimpleNamespace(
        service=service,
        command_executor=SimpleNamespace(client_config=SimpleNamespace(timeout=120)),
        quit=lambda: (_ for _ in ()).throw(RuntimeError("driver already exited")),
    )

    scraper.safe_quit(driver)

    assert service.stopped is True
    assert driver.command_executor.client_config.timeout == 5


def test_safe_quit_skips_quit_when_driver_process_already_exited():
    class FakeProcess:
        def poll(self):
            return 1
    driver = SimpleNamespace(
        service=SimpleNamespace(process=FakeProcess(), stop=lambda: pytest.fail("stop should not run")),
        quit=lambda: pytest.fail("quit should not run"),
    )
    scraper.safe_quit(driver)


def test_navigate_converts_page_timeout_and_stops_loading(monkeypatch):
    _install_fake_selenium(monkeypatch)

    class FakeVerifier:
        def check_robots(self, url):
            return RobotsDecision(True, "ALLOWED", "allowed by test")
    class FakeDriver:
        def __init__(self):
            self.stopped = False
        def get(self, url):
            raise FakeTimeoutException("page load timeout")
        def execute_script(self, script):
            if script == "window.stop();":
                self.stopped = True
                return None
            raise AssertionError(script)

    monkeypatch.setattr(scraper.time, "sleep", lambda _: None)
    driver = FakeDriver()
    with pytest.raises(scraper.PageNavigationTimeout):
        scraper.navigate(driver, scraper.START_URL, FakeVerifier())
    assert driver.stopped is True


def test_navigate_classifies_local_driver_transport_failure(monkeypatch):
    _install_fake_selenium(monkeypatch)

    class FakeVerifier:
        def check_robots(self, url):
            return RobotsDecision(True, "ALLOWED", "allowed by test")
    class FakeDriver:
        def get(self, url):
            raise ConnectionError("local ChromeDriver connection reset")

    monkeypatch.setattr(scraper.time, "sleep", lambda _: None)
    with pytest.raises(scraper.DriverCommunicationError, match="ChromeDriverとの通信"):
        scraper.navigate(FakeDriver(), scraper.START_URL, FakeVerifier())
