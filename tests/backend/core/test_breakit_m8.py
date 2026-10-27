"""M8 break-it: damaged local stores (vault, SQLite), a force-pushed sync remote, big queues."""
import json
import os
import sqlite3
import threading
import time

import pytest

from core.credential_vault import CredentialVault


# ── malformed credentials file ──
@pytest.mark.parametrize("content", [
    b"{\"version\": 1, \"sites\": {\"a.example.com\": {\"pass",       # truncated write
    b"",                                                              # zero bytes (crash during create)
    b"\xff\xfe\x00garbage",                                           # wrong encoding
    b"[1, 2, 3]",                                                     # wrong shape
    b"{\"sites\": \"oops\"}",
], ids=["truncated", "empty", "encoding", "list", "sites-str"])
def test_malformed_vault_is_quarantined_not_overwritten(tmp_path, content):
    p = tmp_path / "vault" / "site_credentials.json"
    p.parent.mkdir()
    p.write_bytes(content)
    v = CredentialVault(p)
    assert v.list_sites() == []                      # no crash
    corrupt = list(p.parent.glob("site_credentials.json.corrupt-*"))
    assert len(corrupt) == 1 and corrupt[0].read_bytes() == content   # the original is kept as-is
    entry, created = v.get_or_create("jobs.example.com", "alex@example.com")
    assert created and v.password_for_sign_in("jobs.example.com") == entry["password"]


def test_vault_with_one_damaged_entry_keeps_the_others(tmp_path):
    p = tmp_path / "site_credentials.json"
    p.write_text(json.dumps({"version": 1, "sites": {
        "good.example.com": {"username": "a", "password": "Good-Pass-1!"},
        "bad.example.com": "not-an-object", "worse.example.com": {"username": "a"}},
        "_legacy_shared": "oops"}), "utf-8")
    v = CredentialVault(p)
    assert [s["host"] for s in v.list_sites()] == ["good.example.com"]
    assert v.password_for_sign_in("good.example.com") == "Good-Pass-1!"
    assert v.password_for_sign_in("bad.example.com") is None


def test_vault_bom_is_tolerated(tmp_path):
    p = tmp_path / "site_credentials.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"sites": {"x.example.com": {"password": "P-1!aaaaaaaaaaaa"}}}).encode())
    assert CredentialVault(p).password_for_sign_in("x.example.com") == "P-1!aaaaaaaaaaaa"


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a read-only dir that root ignores")
def test_unmovable_corrupt_vault_is_never_overwritten(tmp_path):
    d = tmp_path / "vault"
    d.mkdir()
    p = d / "site_credentials.json"
    p.write_bytes(b"{broken")
    os.chmod(d, 0o500)
    try:
        v = CredentialVault(p)
        with pytest.raises(OSError):
            v.get_or_create("jobs.example.com", "alex@example.com")
        assert p.read_bytes() == b"{broken"
    finally:
        os.chmod(d, 0o700)


# ── SQLite: corrupt and locked memory_store.db ──
def _stores():
    from memory.metrics import MetricsStore
    from memory.store import MemoryStore
    return MemoryStore, MetricsStore


@pytest.mark.parametrize("garbage", [b"this is not sqlite at all" * 100,
                                     b"SQLite format 3\x00" + b"\x00" * 50 + b"\xff" * 4000], ids=["text", "bad-header"])
def test_corrupt_memory_db_is_quarantined_and_recreated(tmp_path, garbage):
    MemoryStore, MetricsStore = _stores()
    db = tmp_path / "memory_store.db"
    db.write_bytes(garbage)
    (tmp_path / "memory_store.db-wal").write_bytes(b"junk")
    m = MemoryStore(db)
    m.qa_add(question="Are you authorized to work in the US?", answer="Yes", source_domain="example.com")
    assert m.qa_get_all_for_prompt()
    MetricsStore(db)  # the second store on the same (now healthy) file
    moved = sorted(x.name for x in tmp_path.glob("*.corrupt-*"))
    assert any(x.startswith("memory_store.db.corrupt-") for x in moved)
    assert any(x.startswith("memory_store.db-wal.corrupt-") for x in moved)


def test_connect_closes_the_handle_when_the_file_is_not_a_database(tmp_path, monkeypatch):
    """Windows can't rename a file with an open handle, so a failed open must not leak one."""
    from backend.memory import sqlite_util
    db = tmp_path / "memory_store.db"
    # A valid header passes the pre-check, so SQLite itself opens it and fails.
    db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4080)
    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*a, **kw):
        conn = real_connect(*a, **kw)
        closed = {"v": False}

        class Tracked:
            row_factory = None

            def execute(self, *x, **y):
                return conn.execute(*x, **y)

            def close(self):
                closed["v"] = True
                conn.close()
        opened.append(closed)
        return Tracked()
    monkeypatch.setattr(sqlite_util.sqlite3, "connect", tracking_connect)
    with pytest.raises(sqlite3.DatabaseError):
        sqlite_util.connect(db)
    assert opened and all(c["v"] for c in opened)


def test_locked_memory_db_waits_then_works(tmp_path, monkeypatch):
    MemoryStore, _ = _stores()
    db = tmp_path / "memory_store.db"
    MemoryStore(db).close()
    holder = sqlite3.connect(str(db), check_same_thread=False)
    holder.execute("BEGIN EXCLUSIVE")
    threading.Timer(1.5, holder.rollback).start()   # another process holds the lock for 1.5 s
    m = MemoryStore(db)
    t0 = time.monotonic()
    m.qa_add(question="Notice period?", answer="Two weeks", source_domain="example.com")
    assert time.monotonic() - t0 >= 1.0
    holder.close()


def test_locked_memory_db_past_timeout_raises_operational_error_not_hang(tmp_path, monkeypatch):
    from memory import sqlite_util
    monkeypatch.setattr(sqlite_util, "BUSY_TIMEOUT_S", 0.5)
    MemoryStore, _ = _stores()
    db = tmp_path / "memory_store.db"
    MemoryStore(db).close()
    holder = sqlite3.connect(str(db))
    holder.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            m = MemoryStore(db)
            m.qa_add(question="Q?", answer="A", source_domain="example.com")
        assert time.monotonic() - t0 < 5
        assert not list(tmp_path.glob("*.corrupt-*"))   # locked is not corrupt: never moved aside
    finally:
        holder.rollback()
        holder.close()


def test_memory_context_survives_an_unusable_store(monkeypatch):
    import core.shared_config as sc

    def boom():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sc, "get_memory_store", boom)
    out = sc.build_memory_context({"name": "Alex Example"}, {"Q?": "A"}, [], job_url="https://jobs.example.com/1")
    assert "Alex Example" in out and "Q: Q?" in out


# ── big queue: claim/update stay fast ──
def test_claims_and_updates_on_a_5000_job_queue(data_dir):
    from core import jobs_store
    jf, _ = jobs_store._paths()
    jf.parent.mkdir(parents=True, exist_ok=True)
    jobs = {f"https://jobs.example.com/{i}": {"url": f"https://jobs.example.com/{i}", "title": f"T{i}",
                                              "company": "Example", "status": "pending"} for i in range(5000)}
    jf.write_text(json.dumps(jobs), "utf-8")
    t0 = time.monotonic()
    claimed = [jobs_store.claim_next_pending() for _ in range(20)]
    for c in claimed:
        jobs_store.update_job(c["url"], status="failed", error="x")
    per_op = (time.monotonic() - t0) / 40
    assert len({c["url"] for c in claimed}) == 20
    assert per_op < 0.25, f"{per_op * 1000:.0f} ms per claim/update"
    assert sum(1 for j in jobs_store.read_jobs().values() if j["status"] == "pending") == 4980


# ── prompt-injection exfiltration guard ──
@pytest.mark.parametrize("url,leak", [
    ("https://evil.example.com/c?d=alex@example.com", True),
    ("https://evil.example.com/c?d=ALEX%40EXAMPLE.COM", True),
    ("https://evil.example.com/c?d=alex%2540example.com", True),          # double-encoded
    ("https://evil.example.com/c?p=555-0123", True),
    ("https://evil.example.com/c?p=%28415%29%20555.0123", True),
    ("https://boards.greenhouse.io/examplerobotics/jobs/4001", False),
    ("https://jobs.example.com/apply?ref=5550199", False),
], ids=["plain", "encoded", "double", "phone", "phone-fmt", "job", "other-digits"])
def test_navigation_guard_spots_personal_data_in_urls(url, leak):
    from core.apply_tools import _protected_values, _url_leaks
    pats = _protected_values({"email": "alex@example.com", "phone": "(415) 555-0123"},
                             {"account_email": "alex@example.com"})
    assert _url_leaks(url, pats) is leak
