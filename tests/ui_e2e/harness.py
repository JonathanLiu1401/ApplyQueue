"""Drive the built frontend (dist/) against a real backend sidecar with Playwright.

The sidecar is the packaged binary when APPLYQUEUE_SIDECAR points at it (CI, after
PyInstaller), otherwise `python backend/main.py`. The frontend is the production Vite
build served as static files. The only stand-in is the Tauri bridge: an init script
answers `get_api_token` with the sidecar's token and the dialog plugin with a file path,
exactly what the Rust shell would return. Everything the UI does goes over HTTP to the
real sidecar, which writes to a data dir whose path has spaces and non-ASCII characters.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
PORT = 8743  # the frontend's fixed backend port (src/lib/api.ts DEFAULT_PORT)
FRONT_PORT = 1420
TOKEN = "ui-e2e-token-not-a-secret"

TAURI_MOCK = """
(() => {
  const cfg = %s;
  let cb = 0;
  window.__AQ_INVOKES = [];
  window.__TAURI_INTERNALS__ = {
    metadata: { currentWindow: { label: 'main' }, currentWebview: { windowLabel: 'main', label: 'main' } },
    transformCallback: (fn) => { const id = ++cb; window['_' + id] = fn; return id; },
    unregisterCallback: () => {},
    convertFileSrc: (p) => p,
    invoke: async (cmd, args) => {
      window.__AQ_INVOKES.push(cmd);
      if (cmd === 'get_api_token') return cfg.token;
      if (cmd === 'plugin:dialog|open') return cfg.pick;
      if (cmd === 'plugin:dialog|save') return cfg.pick;
      if (cmd.startsWith('plugin:event|')) return 0;
      if (cmd.startsWith('plugin:shell|')) { (window.__AQ_OPENED = window.__AQ_OPENED || []).push(args && (args.path || args.with || JSON.stringify(args))); return null; }
      return null;
    },
  };
  window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
})();
"""


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class _SPA(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Stack:
    """Sidecar + static frontend. Use as a context manager."""

    def __init__(self, data_dir: Path, pick: str = "", env: dict | None = None):
        self.data_dir = data_dir
        self.extra_env = env or {}
        self.pick = pick
        self.proc = None
        self.httpd = None
        self.log = data_dir.parent / "sidecar.log"

    # ── sidecar ────────────────────────────────────────────────
    def start_sidecar(self):
        if not _free(PORT):
            raise RuntimeError(f"port {PORT} is busy; stop the running ApplyQueue first")
        exe = os.environ.get("APPLYQUEUE_SIDECAR")
        cmd = [exe, str(PORT)] if exe else [sys.executable, str(ROOT / "backend" / "main.py"), str(PORT)]
        env = dict(os.environ, APPLYQUEUE_DATA_DIR=str(self.data_dir), JOB_APPLICANT_TOKEN=TOKEN,
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8", **self.extra_env)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._logf = open(self.log, "wb")
        self.proc = subprocess.Popen(cmd, cwd=str(ROOT / "backend") if not exe else None, env=env,
                                     stdout=self._logf, stderr=subprocess.STDOUT)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("sidecar exited early:\n" + self.log.read_text(errors="replace")[-3000:])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("sidecar never answered /health:\n" + self.log.read_text(errors="replace")[-3000:])

    def stop_sidecar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        if getattr(self, "_logf", None):
            self._logf.close()
        # PyInstaller onefile leaves a child; wait until the port is free again
        for _ in range(60):
            if _free(PORT):
                return
            time.sleep(0.5)

    # ── API helper (what the UI should have written) ───────────
    def api(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode()
            return json.loads(t) if t else {}

    # ── frontend ───────────────────────────────────────────────
    def start_frontend(self):
        if not (DIST / "index.html").exists():
            raise RuntimeError("dist/ is missing; run `npm run build` first")
        handler = functools.partial(_SPA, directory=str(DIST))
        # 127.0.0.1:1420 is an origin the sidecar allows (the Vite dev origin); any other is 403
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", FRONT_PORT), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/index.html"

    def init_script(self) -> str:
        return TAURI_MOCK % json.dumps({"token": TOKEN, "pick": self.pick})

    def __enter__(self):
        self.start_sidecar()
        self.start_frontend()
        return self

    def __exit__(self, *exc):
        if self.httpd:
            self.httpd.shutdown()
        self.stop_sidecar()


class FakeOpenAI:
    """OpenAI-compatible endpoint for the resume auto-fill step.

    It answers with the persona's fields only when the prompt really contains the resume
    text (proving the sidecar read the PDF), otherwise with an empty object."""

    def __init__(self, reply: dict, must_contain: str):
        self.prompts: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._send({"data": [{"id": "fake-model", "object": "model"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                text = json.dumps(body.get("messages", []), ensure_ascii=False)
                outer.prompts.append(text)
                content = json.dumps(reply if must_contain in text else {})
                self._send({"id": "x", "object": "chat.completion", "created": 0, "model": "fake-model",
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": content}}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
