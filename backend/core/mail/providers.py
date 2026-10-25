"""Thin Gmail API / Microsoft Graph clients returning normalized messages.

Message dict: {id, provider, from_addr, from_name, from_domain, subject, received_at (epoch), text, labels}
"""
from __future__ import annotations

import base64
import html
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

import httpx

try:
    from core.mail import oauth
except ImportError:  # pragma: no cover
    from backend.core.mail import oauth

# Module-level so tests can point them at mock servers.
API_BASE = {
    "gmail": "https://gmail.googleapis.com/gmail/v1/users/me",
    "outlook": "https://graph.microsoft.com/v1.0/me",
}
MAX_TEXT = 20_000
_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)


class MailApiError(Exception):
    pass


def html_to_text(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", s or "", flags=re.I)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(_TAG_RE.sub(" ", s))).strip()


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower().strip(">") if "@" in addr else ""


class _Base:
    provider = ""

    def __init__(self, timeout: float = 20):
        self.timeout = timeout

    def _req(self, method: str, path: str, **kw) -> dict:
        url = path if path.startswith("http") else API_BASE[self.provider] + path
        for attempt in (1, 2):
            tok = oauth.access_token(self.provider)
            headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json", **kw.pop("headers", {})}
            try:
                r = httpx.request(method, url, headers=headers, timeout=self.timeout, **kw)
            except httpx.HTTPError as e:
                raise MailApiError(f"{oauth.NAMES[self.provider]} unreachable: {type(e).__name__}") from None
            if r.status_code == 401 and attempt == 1:
                oauth._update(self.provider, access_token=None, expires_at=0)  # force one refresh
                continue
            if r.status_code == 401:
                oauth.mark_reauth(self.provider, "unauthorized")
                raise oauth.ReauthRequired("unauthorized")
            if r.status_code >= 400:
                raise MailApiError(f"{oauth.NAMES[self.provider]} API HTTP {r.status_code}")
            if not r.content:
                return {}
            try:
                return r.json()
            except ValueError:
                return {}
        return {}  # pragma: no cover


class Gmail(_Base):
    provider = "gmail"

    def __init__(self, timeout: float = 20):
        super().__init__(timeout)
        self._labels: dict[str, str] = {}

    def profile_email(self) -> str:
        return str(self._req("GET", "/profile").get("emailAddress") or "")

    def list_recent(self, since_ts: float, *, inbox_only: bool = False, query: str = "", limit: int = 50) -> list[dict]:
        q = f"after:{int(since_ts)}" + (" in:inbox" if inbox_only else "") + (f" {query}" if query else "")
        ids = [m["id"] for m in (self._req("GET", "/messages", params={"q": q, "maxResults": limit})
                                 .get("messages") or [])]
        out = []
        for mid in ids:
            try:
                out.append(self.get(mid))
            except MailApiError:
                continue
        out.sort(key=lambda m: m["received_at"], reverse=True)
        return out

    def get(self, mid: str) -> dict:
        raw = self._req("GET", f"/messages/{mid}", params={"format": "full"})
        payload = raw.get("payload") or {}
        headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
        name, addr = parseaddr(headers.get("from", ""))
        try:
            received = int(raw.get("internalDate") or 0) / 1000.0
        except (TypeError, ValueError):
            received = 0.0
        if not received and headers.get("date"):
            try:
                received = parsedate_to_datetime(headers["date"]).timestamp()
            except (TypeError, ValueError):
                received = time.time()
        plain, rich = [], []

        def walk(part: dict):
            mime = (part.get("mimeType") or "").lower()
            data = (part.get("body") or {}).get("data")
            if data and mime in ("text/plain", "text/html"):
                try:
                    txt = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                except (ValueError, TypeError):
                    txt = ""
                (plain if mime == "text/plain" else rich).append(txt)
            for p in part.get("parts") or []:
                walk(p)

        walk(payload)
        text = "\n".join(plain) or html_to_text("\n".join(rich)) or raw.get("snippet", "")
        return {"id": raw.get("id", mid), "provider": "gmail", "from_addr": addr.lower(), "from_name": name,
                "from_domain": _domain(addr), "subject": headers.get("subject", ""), "received_at": received,
                "text": text[:MAX_TEXT], "labels": list(raw.get("labelIds") or [])}

    def _label_id(self, name: str) -> str:
        if name in self._labels:
            return self._labels[name]
        for lab in self._req("GET", "/labels").get("labels") or []:
            self._labels[lab.get("name", "")] = lab.get("id", "")
        if name not in self._labels:
            made = self._req("POST", "/labels", json={"name": name, "labelListVisibility": "labelShow",
                                                      "messageListVisibility": "show"})
            self._labels[name] = made.get("id", "")
        return self._labels[name]

    def label_and_archive(self, mid: str, label: str, archive: bool = True) -> str:
        body: dict = {"addLabelIds": [self._label_id(label)] if label else []}
        if archive:
            body["removeLabelIds"] = ["INBOX"]
        self._req("POST", f"/messages/{mid}/modify", json=body)
        return mid


class Outlook(_Base):
    provider = "outlook"
    _SELECT = "id,subject,from,receivedDateTime,body,bodyPreview,categories"

    def profile_email(self) -> str:
        me = self._req("GET", "", params={"$select": "mail,userPrincipalName"})
        return str(me.get("mail") or me.get("userPrincipalName") or "")

    def _norm(self, m: dict) -> dict:
        frm = ((m.get("from") or {}).get("emailAddress") or {})
        addr = str(frm.get("address") or "").lower()
        try:
            received = datetime.fromisoformat(str(m.get("receivedDateTime")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            received = time.time()
        body = m.get("body") or {}
        text = body.get("content") or m.get("bodyPreview") or ""
        if (body.get("contentType") or "").lower() == "html":
            text = html_to_text(text)
        return {"id": m.get("id", ""), "provider": "outlook", "from_addr": addr, "from_name": frm.get("name", ""),
                "from_domain": _domain(addr), "subject": m.get("subject") or "", "received_at": received,
                "text": text[:MAX_TEXT], "labels": list(m.get("categories") or [])}

    def list_recent(self, since_ts: float, *, inbox_only: bool = False, query: str = "", limit: int = 50) -> list[dict]:
        since = datetime.fromtimestamp(since_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = "/mailFolders/inbox/messages" if inbox_only else "/messages"
        res = self._req("GET", path, params={
            "$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime desc",
            "$top": str(limit), "$select": self._SELECT,
        }, headers={"Prefer": 'outlook.body-content-type="text"'})
        return [self._norm(m) for m in res.get("value") or []]

    def label_and_archive(self, mid: str, label: str, archive: bool = True) -> str:
        if label:
            cur = self._req("GET", f"/messages/{mid}", params={"$select": "categories"}).get("categories") or []
            if label not in cur:
                self._req("PATCH", f"/messages/{mid}", json={"categories": [*cur, label]})
        if archive:
            moved = self._req("POST", f"/messages/{mid}/move", json={"destinationId": "archive"})
            return moved.get("id") or mid
        return mid


def client_for(provider: str):
    return {"gmail": Gmail, "outlook": Outlook}[provider]()
