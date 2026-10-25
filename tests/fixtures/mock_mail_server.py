"""In-process mock of Google OAuth + Gmail API and Microsoft identity + Graph mail (M4 tests).

Only the endpoints ApplyQueue calls are implemented. Fictional data (Alex Example, example.com).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class MockMail:
    def __init__(self):
        self.lock = threading.Lock()
        self.challenges: dict[str, str] = {}        # code -> code_challenge
        self.refresh_tokens: dict[str, str] = {}    # refresh token -> provider
        self.revoked: set[str] = set()
        self.access: dict[str, str] = {}            # access token -> provider
        self.token_requests: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.gmail_msgs: dict[str, dict] = {}
        self.gmail_labels: dict[str, str] = {"INBOX": "INBOX"}
        self.graph_msgs: dict[str, dict] = {}
        self.n = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # ── fixtures ──
    def authorize(self, auth_url: str, code: str = "code-1") -> str:
        """What the user's browser would do: approve and return the state."""
        q = parse_qs(urlparse(auth_url).query)
        assert q["code_challenge_method"] == ["S256"]
        self.challenges[code] = q["code_challenge"][0]
        return q["state"][0]

    def _id(self) -> str:
        self.n += 1
        return f"m{self.n:04d}"

    def add_gmail(self, *, sender: str, subject: str, body: str, minutes_ago: float = 1, html: bool = False) -> str:
        mid = self._id()
        mime = "text/html" if html else "text/plain"
        self.gmail_msgs[mid] = {
            "id": mid, "labelIds": ["INBOX", "UNREAD"], "snippet": body[:80],
            "internalDate": str(int((time.time() - minutes_ago * 60) * 1000)),
            "payload": {"mimeType": "multipart/alternative", "headers": [
                {"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
                "parts": [{"mimeType": mime, "body": {"data": _b64(body)}}]},
        }
        return mid

    def add_graph(self, *, name: str, address: str, subject: str, body: str, minutes_ago: float = 1) -> str:
        mid = "AAMk" + self._id()
        when = datetime.fromtimestamp(time.time() - minutes_ago * 60, timezone.utc)
        self.graph_msgs[mid] = {
            "id": mid, "subject": subject, "folder": "inbox", "categories": [],
            "from": {"emailAddress": {"name": name, "address": address}},
            "receivedDateTime": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": {"contentType": "text", "content": body}, "bodyPreview": body[:80],
        }
        return mid

    # ── HTTP ──
    def _handler(mock):  # noqa: N805
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj=None):
                data = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode() if n else ""
                if "json" in (self.headers.get("Content-Type") or ""):
                    return json.loads(raw or "{}")
                return {k: v[0] for k, v in parse_qs(raw).items()}

            def _authed(self, provider: str) -> bool:
                tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                if mock.access.get(tok) != provider:
                    self._send(401, {"error": {"code": 401}})
                    return False
                return True

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PATCH(self):
                self._route("PATCH")

            def _route(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                path = u.path
                body = self._body() if method in ("POST", "PATCH") else {}
                with mock.lock:
                    mock.calls.append((method, path))
                    if path in ("/google/token",) or re.fullmatch(r"/ms/[^/]+/oauth2/v2.0/token", path):
                        return self._token("gmail" if path.startswith("/google") else "outlook", body)
                    if path == "/google/revoke":
                        mock.revoked.add(body.get("token", ""))
                        return self._send(200, {})
                    if path.startswith("/gmail/"):
                        if not self._authed("gmail"):
                            return
                        return self._gmail(method, path.removeprefix("/gmail/v1/users/me"), q, body)
                    if path.startswith("/graph/"):
                        if not self._authed("outlook"):
                            return
                        return self._graph(method, path.removeprefix("/graph/v1.0/me"), q, body)
                return self._send(404, {"error": "not found"})

            def _token(self, provider, form):
                mock.token_requests.append({"provider": provider, **form})
                if provider == "outlook" and "client_secret" in form:
                    return self._send(400, {"error": "invalid_request"})
                if form.get("grant_type") == "authorization_code":
                    ch = mock.challenges.pop(form.get("code", ""), None)
                    digest = base64.urlsafe_b64encode(
                        hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                    if not ch or ch != digest or "/email/oauth/callback" not in form.get("redirect_uri", ""):
                        return self._send(400, {"error": "invalid_grant"})
                    rt = f"rt-{provider}-{mock._id()}"
                    mock.refresh_tokens[rt] = provider
                elif form.get("grant_type") == "refresh_token":
                    rt = form.get("refresh_token", "")
                    if rt in mock.revoked or rt not in mock.refresh_tokens:
                        return self._send(400, {"error": "invalid_grant", "error_description": "Token expired"})
                    if provider == "outlook":  # Microsoft rotates refresh tokens
                        mock.refresh_tokens.pop(rt)
                        rt = f"rt-{provider}-{mock._id()}"
                        mock.refresh_tokens[rt] = provider
                    else:
                        rt = None
                else:
                    return self._send(400, {"error": "unsupported_grant_type"})
                at = f"at-{provider}-{mock._id()}"
                mock.access[at] = provider
                out = {"access_token": at, "expires_in": 3599, "token_type": "Bearer"}
                if rt:
                    out["refresh_token"] = rt
                return self._send(200, out)

            def _gmail(self, method, path, q, body):
                if path == "/profile":
                    return self._send(200, {"emailAddress": "alex@example.com"})
                if path == "/messages" and method == "GET":
                    qs = q.get("q", "")
                    after = int((re.search(r"after:(\d+)", qs) or [0, 0])[1])
                    ids = [m["id"] for m in mock.gmail_msgs.values()
                           if int(m["internalDate"]) / 1000 >= after
                           and ("in:inbox" not in qs or "INBOX" in m["labelIds"])]
                    return self._send(200, {"messages": [{"id": i} for i in ids], "resultSizeEstimate": len(ids)})
                m = re.fullmatch(r"/messages/([^/]+)(/modify)?", path)
                if m and m.group(1) in mock.gmail_msgs:
                    msg = mock.gmail_msgs[m.group(1)]
                    if m.group(2) and method == "POST":
                        for lid in body.get("addLabelIds") or []:
                            if lid not in msg["labelIds"]:
                                msg["labelIds"].append(lid)
                        msg["labelIds"] = [x for x in msg["labelIds"] if x not in (body.get("removeLabelIds") or [])]
                        return self._send(200, {"id": msg["id"], "labelIds": msg["labelIds"]})
                    return self._send(200, msg)
                if path == "/labels" and method == "GET":
                    return self._send(200, {"labels": [{"id": i, "name": n} for n, i in mock.gmail_labels.items()]})
                if path == "/labels" and method == "POST":
                    lid = f"Label_{len(mock.gmail_labels)}"
                    mock.gmail_labels[body["name"]] = lid
                    return self._send(200, {"id": lid, "name": body["name"]})
                return self._send(404, {"error": {"code": 404}})

            def _graph(self, method, path, q, body):
                if path == "":
                    return self._send(200, {"mail": "alex@example.com", "userPrincipalName": "alex@example.com"})
                if path in ("/messages", "/mailFolders/inbox/messages") and method == "GET":
                    since = re.search(r"receivedDateTime ge (\S+)", q.get("$filter", ""))
                    rows = [m for m in mock.graph_msgs.values()
                            if (not since or m["receivedDateTime"] >= since.group(1))
                            and (path == "/messages" or m["folder"] == "inbox")]
                    rows.sort(key=lambda m: m["receivedDateTime"], reverse=True)
                    return self._send(200, {"value": [{k: v for k, v in m.items() if k != "folder"} for m in rows]})
                m = re.fullmatch(r"/messages/([^/]+)(/move)?", path)
                if m and m.group(1) in mock.graph_msgs:
                    msg = mock.graph_msgs[m.group(1)]
                    if m.group(2) and method == "POST":
                        assert body == {"destinationId": "archive"}
                        mock.graph_msgs.pop(msg["id"])
                        msg = {**msg, "id": msg["id"] + "-arch", "folder": "archive"}
                        mock.graph_msgs[msg["id"]] = msg
                        return self._send(201, msg)
                    if method == "PATCH":
                        msg["categories"] = list(body.get("categories") or [])
                        return self._send(200, msg)
                    return self._send(200, msg)
                return self._send(404, {"error": {"code": "ErrorItemNotFound"}})

        return H

    def point(self, monkeypatch, oauth, providers):
        monkeypatch.setitem(oauth.ENDPOINTS, "gmail", {"auth": self.base + "/google/auth",
                                                       "token": self.base + "/google/token",
                                                       "revoke": self.base + "/google/revoke"})
        monkeypatch.setitem(oauth.ENDPOINTS, "outlook", {"auth": self.base + "/ms/{tenant}/oauth2/v2.0/authorize",
                                                         "token": self.base + "/ms/{tenant}/oauth2/v2.0/token",
                                                         "revoke": ""})
        monkeypatch.setitem(providers.API_BASE, "gmail", self.base + "/gmail/v1/users/me")
        monkeypatch.setitem(providers.API_BASE, "outlook", self.base + "/graph/v1.0/me")
