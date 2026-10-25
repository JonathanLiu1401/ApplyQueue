"""The real packaged Tauri app (Rust shell + webview + spawned PyInstaller sidecar),
driven over W3C WebDriver through tauri-driver. Linux (WebKitWebDriver) and Windows
(msedgedriver) only: Apple's WKWebView has no WebDriver, so macOS is covered by
tests/ui_e2e plus scripts/smoke-macos-app.sh.

Set APPLYQUEUE_TAURI_APP to the built binary (target/<triple>/release/app[.exe]) and
have `tauri-driver` on PATH. Plain HTTP, no extra dependency.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

APP = os.environ.get("APPLYQUEUE_TAURI_APP", "")
DRIVER = shutil.which("tauri-driver")
pytestmark = pytest.mark.skipif(not (APP and Path(APP).exists() and DRIVER),
                                reason="needs APPLYQUEUE_TAURI_APP and tauri-driver")
WD = "http://127.0.0.1:4444"
SIDECAR = "http://127.0.0.1:8743"


def _req(method, url, body=None, headers=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Session:
    def __init__(self, sid):
        self.base = f"{WD}/session/{sid}"

    def cmd(self, method, path, body=None):
        return _req(method, self.base + path, body)["value"]

    def find(self, xpath, timeout=60):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                el = self.cmd("POST", "/element", {"using": "xpath", "value": xpath})
                return next(iter(el.values()))
            except urllib.error.HTTPError as e:
                last = e
                time.sleep(0.5)
        try:
            text = self.cmd("POST", "/execute/sync", {"script": "return document.body.innerText", "args": []})
        except Exception as e:  # noqa: BLE001
            text = f"<no page text: {e}>"
        raise AssertionError(f"not found: {xpath} ({last})\n--- page ---\n{str(text)[:3000]}")

    def click(self, xpath, timeout=60):
        self.cmd("POST", f"/element/{self.find(xpath, timeout)}/click", {})

    def type(self, xpath, text):
        eid = self.find(xpath)
        self.cmd("POST", f"/element/{eid}/clear", {})
        self.cmd("POST", f"/element/{eid}/value", {"text": text})

    def js(self, script, *args):
        return self.cmd("POST", "/execute/sync", {"script": script, "args": list(args)})


@pytest.fixture()
def app(tmp_path):
    assert not _port_open(8743), "port 8743 busy (another ApplyQueue running?)"
    data = tmp_path / "Alex Example ü data"
    env = dict(os.environ, APPLYQUEUE_DATA_DIR=str(data))
    log = open(tmp_path / "tauri-driver.log", "wb")
    drv = subprocess.Popen([DRIVER, "--port", "4444"], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            if _port_open(4444):
                break
            time.sleep(0.2)
        caps = {"capabilities": {"alwaysMatch": {"tauri:options": {"application": str(Path(APP).resolve())}}}}
        sid = _req("POST", f"{WD}/session", caps, timeout=120)["value"]["sessionId"]
        s = Session(sid)
        try:
            yield s, data
        finally:
            try:
                _req("DELETE", f"{WD}/session/{sid}", timeout=30)
            except Exception:
                pass
    finally:
        drv.terminate()
        try:
            drv.wait(15)
        except subprocess.TimeoutExpired:
            drv.kill()
        log.close()
        # the sidecar watches its parent and exits; give it a moment so the next test can bind
        for _ in range(60):
            if not _port_open(8743):
                break
            time.sleep(0.5)


def test_packaged_app_wizard_and_quick_start(app, tmp_path):
    s, data = app
    # 1. The Rust shell spawned the bundled sidecar, which wrote its token where the shell reads it.
    s.find("//*[contains(text(), 'Welcome to ApplyQueue')]", timeout=120)
    token = (data / ".api_token").read_text().strip()
    auth = {"Authorization": f"Bearer {token}"}
    assert _req("GET", f"{SIDECAR}/health")["status"] in ("ok", "healthy")
    # 2. Deep wizard: Welcome -> AI provider (local) -> Resume (skip) -> Review -> Finish
    s.click("//button[contains(., \"Let's Go\")]")
    s.click("//*[@data-testid='ai-mode-local']")
    s.click("//button[normalize-space(.)='Next']")
    s.find("//*[contains(text(), 'Resume')]")
    # "Skip for now" closes the whole wizard, so walk the remaining steps with Next.
    for _ in range(6):
        if s.cmd("POST", "/elements", {"using": "xpath", "value": "//button[contains(., 'Finish Setup')]"}):
            break
        s.click("//button[normalize-space(.)='Next']", timeout=10)
    s.click("//button[contains(., 'Finish Setup')]")
    for _ in range(40):
        if _req("GET", f"{SIDECAR}/setup/status", headers=auth).get("onboarding_completed"):
            break
        time.sleep(0.5)
    assert _req("GET", f"{SIDECAR}/setup/status", headers=auth)["onboarding_completed"] is True
    # 3. 3-step quick start through the real webview -> IPC token -> sidecar
    s.js("window.location.hash = '#/searches'")
    resume = tmp_path / "Alex Example résumé.pdf"
    from tests.fixtures.persona import write_resume_pdf
    write_resume_pdf(resume)
    s.type("//input[@aria-label='CV path']", str(resume))
    s.type("//input[@aria-label='Job titles']", "Data Engineer")
    s.type("//input[@aria-label='Locations']", "Remote")
    s.click("//*[@data-testid='quick-start-go']")
    for _ in range(40):
        got = _req("GET", f"{SIDECAR}/searches", headers=auth)["searches"]
        if got:
            break
        time.sleep(0.5)
    assert got and got[0]["titles"] == ["Data Engineer"], got
    errors = s.js("return (window.__aqErrors || []).length")
    assert errors in (0, None)
