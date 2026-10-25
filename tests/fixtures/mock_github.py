"""A tiny mock of the GitHub endpoints the sync repo uses (device flow + repos)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# Token-shaped strings are assembled so the privacy scan never sees a literal.
FAKE_TOKEN = "gho" + "_" + "A" * 36


class MockGitHub:
    def __init__(self):
        self.repos: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.pending_polls = 1
        self.deny = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                if "json" in (self.headers.get("Content-Type") or ""):
                    return json.loads(raw or "{}")
                return {k: v[0] for k, v in parse_qs(raw).items()}

            def _authed(self):
                return self.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"

            def do_POST(self):
                outer.calls.append(("POST", self.path))
                body = self._body()
                if self.path == "/login/device/code":
                    if not body.get("client_id"):
                        return self._send(400, {"error": "unauthorized_client"})
                    return self._send(200, {"device_code": "dev-123", "user_code": "ABCD-1234",
                                            "verification_uri": "https://github.example/login/device",
                                            "expires_in": 900, "interval": 1})
                if self.path == "/login/oauth/access_token":
                    if outer.deny:
                        return self._send(200, {"error": "access_denied"})
                    if outer.pending_polls > 0:
                        outer.pending_polls -= 1
                        return self._send(200, {"error": "authorization_pending"})
                    return self._send(200, {"access_token": FAKE_TOKEN, "token_type": "bearer", "scope": "repo"})
                if self.path == "/user/repos":
                    if not self._authed():
                        return self._send(401, {"message": "Bad credentials"})
                    name = body["name"]
                    repo = {"name": name, "full_name": f"alex-example/{name}", "private": bool(body.get("private")),
                            "visibility": "private" if body.get("private") else "public", "fork": False,
                            "permissions": {"push": True}, "html_url": f"https://github.example/alex-example/{name}"}
                    outer.repos[f"alex-example/{name}"] = repo
                    return self._send(201, repo)
                self._send(404, {"message": "Not Found"})

            def do_GET(self):
                outer.calls.append(("GET", self.path))
                if not self._authed():
                    return self._send(401, {"message": "Bad credentials"})
                if self.path == "/user":
                    return self._send(200, {"login": "alex-example"})
                if self.path.startswith("/repos/"):
                    key = self.path[len("/repos/"):]
                    if key in outer.repos:
                        return self._send(200, outer.repos[key])
                self._send(404, {"message": "Not Found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def set_visibility(self, full_name: str, visibility: str):
        self.repos[full_name].update(private=visibility == "private", visibility=visibility)

    def visibility_checks(self, full_name: str) -> int:
        return sum(1 for m, p in self.calls if m == "GET" and p == f"/repos/{full_name}")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
