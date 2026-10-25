"""Break-it suite (M8) against the real sidecar process (packaged binary in CI).

Corrupt / partial / future-schema config, read-only data dir, missing or corrupt resume,
empty / huge / unicode / RTL / emoji input, a second instance on the same port, no network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.ui_e2e.harness import PORT, ROOT, Stack

RTL = "\u05de\u05d4\u05e0\u05d3\u05e1 \u0646\u0638\u0645 \U0001F469\u200d\U0001F4BB \u202eevil"


def _err(st, method, path, body=None):
    try:
        st.api(method, path, body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    return 200, ""


@pytest.fixture()
def sidecar(tmp_path):
    stacks = []

    def start(seed=None, chmod=None):
        data = tmp_path / "Alex Example ü data dir"
        data.mkdir(exist_ok=True)
        for name, raw in (seed or {}).items():
            (data / name).write_bytes(raw if isinstance(raw, bytes) else raw.encode())
        st = Stack(data)
        stacks.append(st)
        st.start_sidecar()
        return st
    yield start
    for st in stacks:
        st.stop_sidecar()


def test_corrupt_partial_and_future_schema_config_still_boots(sidecar):
    st = sidecar({
        "settings.json": '{"resume_path": "/x", "auto',                        # truncated mid-write
        "llm_settings.json": b"\xef\xbb\xbf\x00\x00garbage",                   # BOM + NULs
        "candidate_profile.json": '["not", "an", "object"]',                    # wrong top-level type
        "searches.json": json.dumps({"version": 999, "searches": [{"id": "s_x", "name": 5,
                                     "titles": "Data Engineer", "mode": "turbo", "future_field": {"a": 1}}]}),
        "jobs.json": '{"https://example.com/j/1": "not-a-dict", "https://example.com/j/2": {"status": "bogus"}}',
        "schedule_state.json": "null",
    })
    assert st.api("GET", "/settings") is not None
    assert isinstance(st.api("GET", "/profile"), dict)
    assert isinstance(st.api("GET", "/settings/llm"), dict)
    searches = st.api("GET", "/searches")["searches"]
    assert isinstance(searches, list)
    assert st.api("GET", "/schedules") is not None
    # corrupt files were moved aside, never deleted
    names = os.listdir(st.data_dir)
    assert any(n.startswith("settings.json.corrupt-") for n in names), names
    # and the app can write fresh state afterwards
    st.api("PUT", "/profile", {"name": "Alex Example", "email": "alex@example.com"})
    assert st.api("GET", "/profile")["name"] == "Alex Example"


def test_hostile_text_inputs(sidecar):
    st = sidecar()
    # empty
    code, body = _err(st, "POST", "/searches", {"name": "", "titles": []})
    assert code == 400 and "title" in body.lower()
    code, _ = _err(st, "POST", "/searches", {"titles": ["   ", "\u200b"]})
    assert code == 400
    # unicode / RTL override / emoji / ZWJ round-trip
    s = st.api("POST", "/searches", {"name": RTL, "titles": [RTL, "Data Engineer"], "locations": ["Zürich", "東京"]})["search"]
    got = next(x for x in st.api("GET", "/searches")["searches"] if x["id"] == s["id"])
    assert got["titles"][0] == RTL and "東京" in got["locations"]
    # huge: bounded, never a 500
    huge = "A" * 200_000
    code, _ = _err(st, "POST", "/searches", {"name": huge, "titles": [huge] * 500})
    assert code in (200, 400, 413)
    for x in st.api("GET", "/searches")["searches"]:
        assert len(x["name"]) <= 200 and len(x["titles"]) <= 50
    prof = {"name": RTL, "email": "alex@example.com", "cover_letter": "é" * 100_000}
    st.api("PUT", "/profile", prof)
    assert st.api("GET", "/profile")["name"] == RTL
    # malformed JSON body / wrong types: 4xx, not 500
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/searches", data=b"{not json", method="POST",
                                 headers={"Authorization": "Bearer ui-e2e-token-not-a-secret",
                                          "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert 400 <= e.value.code < 500
    code, _ = _err(st, "PUT", f"/searches/{s['id']}/schedule", {"kind": "daily", "time": "25:99"})
    assert code == 400
    code, _ = _err(st, "PUT", f"/searches/{s['id']}/schedule", {"kind": "daily", "tz": "../../etc"})
    assert code == 400


def test_missing_and_corrupt_resume(sidecar, tmp_path):
    st = sidecar()
    st.api("PUT", "/settings/llm", {"provider": "openai_compatible",
                                    "openai_compatible": {"base_url": "http://127.0.0.1:9/v1", "api_key": "x",
                                                          "model": "m"}})
    settings = st.api("GET", "/settings")
    st.api("PUT", "/settings", {**settings, "resume_path": str(tmp_path / "gone" / "Alex résumé.pdf")})
    r = st.api("POST", "/profile/parse-resume")
    assert r["success"] is False and "not found" in r["message"].lower()
    bad = tmp_path / "Alex corrupt résumé.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + os.urandom(4096))
    st.api("PUT", "/settings", {**settings, "resume_path": str(bad)})
    code, body = _err(st, "POST", "/profile/parse-resume")
    if code == 200:
        r = st.api("POST", "/profile/parse-resume")
        assert r["success"] is False, r
    else:
        assert code < 500, body
    # quick start with a CV path that does not exist: refused with a clear message, nothing half-created
    code, body = _err(st, "POST", "/searches/quickstart",
                      {"resume_path": str(tmp_path / "nope.pdf"), "titles": "Data Engineer", "locations": "Remote"})
    assert code == 400 and ("not found" in body.lower() or "exist" in body.lower()), (code, body)
    assert st.api("GET", "/searches")["searches"] == []


def test_second_instance_on_same_port_exits_and_first_survives(sidecar):
    st = sidecar()
    exe = os.environ.get("APPLYQUEUE_SIDECAR")
    cmd = [exe, str(PORT)] if exe else [sys.executable, str(ROOT / "backend" / "main.py"), str(PORT)]
    env = dict(os.environ, APPLYQUEUE_DATA_DIR=str(st.data_dir), JOB_APPLICANT_TOKEN="second-instance")
    p = subprocess.run(cmd, cwd=None if exe else str(ROOT / "backend"), env=env, capture_output=True, timeout=120)
    assert p.returncode != 0
    # the first instance still answers with ITS token (the loser must not overwrite .api_token)
    assert st.api("GET", "/settings") is not None
    assert (st.data_dir / ".api_token").read_text().strip() == "ui-e2e-token-not-a-secret"


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions; root ignores them")
def test_read_only_data_dir_fails_loudly_not_silently(tmp_path):
    data = tmp_path / "ro data"
    data.mkdir()
    data.chmod(0o555)
    try:
        exe = os.environ.get("APPLYQUEUE_SIDECAR")
        cmd = [exe, str(PORT)] if exe else [sys.executable, str(ROOT / "backend" / "main.py"), str(PORT)]
        env = dict(os.environ, APPLYQUEUE_DATA_DIR=str(data), JOB_APPLICANT_TOKEN="ro-token")
        p = subprocess.run(cmd, cwd=None if exe else str(ROOT / "backend"), env=env, capture_output=True,
                           timeout=120)
        out = (p.stdout + p.stderr).decode(errors="replace")
        assert p.returncode != 0
        assert "not writable" in out and str(data) in out, out[-2000:]
        assert "Traceback" not in out, out[-2000:]
    finally:
        data.chmod(0o755)


def test_no_network_paths_report_offline_not_500(sidecar):
    st = sidecar()
    # LLM endpoint unreachable
    st.api("PUT", "/settings/llm", {"provider": "openai_compatible",
                                    "openai_compatible": {"base_url": "http://127.0.0.1:9/v1", "api_key": "x",
                                                          "model": "m"}})
    code, body = _err(st, "POST", "/llm/test", {})
    assert code < 500 or "connect" in body.lower(), (code, body)
    # GitHub device flow unreachable (the env-gated override points at a dead loopback port)
    st.stop_sidecar()
    st.extra_env = {"APPLYQUEUE_E2E_MOCK_GITHUB": "http://127.0.0.1:9", "APPLYQUEUE_E2E_MOCK_MAIL": "http://127.0.0.1:9"}
    st.start_sidecar()
    code, body = _err(st, "POST", "/sync/github/device", {"client_id": "Iv1.example"})
    assert code < 500 or "offline" in body.lower() or "connect" in body.lower(), (code, body)
    st.api("PUT", "/email/outlook/config", {"client_id": "00000000-0000-4000-8000-000000000001", "tenant": "common"})
    code, body = _err(st, "POST", "/email/sweep", {})
    assert code < 500, (code, body)
    r = st.api("GET", "/email/status")
    assert "outlook" in r["providers"]


@pytest.mark.skipif(os.name == "nt", reason="re-parenting an orphan is POSIX behaviour")
def test_orphaned_backend_is_replaced_by_the_next_launch(sidecar, tmp_path, monkeypatch):
    from tests.ui_e2e import harness
    data = tmp_path / "orphan data"
    exe = os.environ.get("APPLYQUEUE_SIDECAR")
    cmd = [exe, str(PORT)] if exe else [sys.executable, str(ROOT / "backend" / "main.py"), str(PORT)]
    env = dict(os.environ, APPLYQUEUE_DATA_DIR=str(data), JOB_APPLICANT_TOKEN="orphan-token")
    # a "window" process that launches the backend and then dies -> the backend is orphaned
    launcher = ("import subprocess,sys,json; p=subprocess.Popen(json.loads(sys.argv[1]), cwd=sys.argv[2] or None, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True); print(p.pid, flush=True); import time; time.sleep(3)")
    out = subprocess.run([sys.executable, "-c", launcher, json.dumps(cmd), "" if exe else str(ROOT / "backend")],
                         env=env, capture_output=True, text=True, timeout=60)
    orphan_pid = int(out.stdout.split()[-1])
    try:
        h = None
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    h = json.loads(r.read())
                    if h.get("app") == "applyqueue":
                        break
            except OSError:
                time.sleep(0.5)
        assert h and h["orphaned"] is True, h
        real_free = harness._free
        calls = iter([True])
        monkeypatch.setattr(harness, "_free", lambda port: next(calls, None) or real_free(port))
        st = sidecar()  # the next launch takes over
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if json.loads(r.read()).get("pid") not in (orphan_pid, None):
                        break
            except OSError:
                pass
            time.sleep(0.5)
        assert st.api("GET", "/settings") is not None
        assert st.proc.poll() is None
    finally:
        try:
            os.kill(orphan_pid, 9)
        except OSError:
            pass


def test_orphaned_backend_exits_on_its_own(tmp_path):
    """The window crashed/was killed: the backend (incl. the PyInstaller bootloader pair) must not
    linger holding the port, browsers and the data dir."""
    exe = os.environ.get("APPLYQUEUE_SIDECAR")
    cmd = [exe, str(PORT)] if exe else [sys.executable, str(ROOT / "backend" / "main.py"), str(PORT)]
    env = dict(os.environ, APPLYQUEUE_DATA_DIR=str(tmp_path / "orphan 2"), JOB_APPLICANT_TOKEN="orphan-2")
    launcher = ("import subprocess,sys,json,time; p=subprocess.Popen(json.loads(sys.argv[1]), cwd=sys.argv[2] or None, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
                + ("creationflags=0x00000200|0x00000008" if os.name == "nt" else "start_new_session=True")
                + "); print(p.pid, flush=True); time.sleep(4)")
    out = subprocess.run([sys.executable, "-c", launcher, json.dumps(cmd), "" if exe else str(ROOT / "backend")],
                         env=env, capture_output=True, text=True, timeout=60)
    pid = int(out.stdout.split()[-1])
    from tests.ui_e2e.harness import _free
    try:
        saw_up = False
        for _ in range(180):
            up = not _free(PORT)
            saw_up = saw_up or up
            if saw_up and not up:
                break
            time.sleep(0.5)
        assert saw_up, "backend never came up"
        assert _free(PORT), "orphaned backend still holds the port 90 s after its window died"
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
