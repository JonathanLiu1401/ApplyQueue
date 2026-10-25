"""M6: local runtime detection, hardware-based recommendation, one-click pull (mocked runtimes)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.local_models import catalog, detect, hardware, pull  # noqa: E402
from tests.fixtures.mock_local_llm import MockRuntime, dead_url  # noqa: E402

GB = hardware.GB


@pytest.fixture
def runtimes():
    rts = {
        "ollama": MockRuntime("ollama", ["qwen3-vl:8b", "llama3.1:8b"], vision={"qwen3-vl:8b"}),
        "lmstudio": MockRuntime("lmstudio", ["google/gemma-3-12b", "qwen2.5-coder"], vision={"google/gemma-3-12b"}),
        "llamacpp": MockRuntime("llamacpp", ["model.gguf"], vision={"model.gguf"}),
        "openai": MockRuntime("openai", ["Qwen/Qwen3-VL-8B-Instruct"]),
    }
    yield rts
    for r in rts.values():
        r.close()


def test_detects_all_runtimes_with_vision_flags(runtimes, tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYQUEUE_GGUF_DIRS", str(tmp_path))
    out = detect.detect({k: r.base for k, r in runtimes.items()})
    by = {r["id"]: r for r in out["runtimes"]}
    assert set(by) == {"ollama", "lmstudio", "llamacpp", "openai"}
    ol = {m["id"]: m["vision"] for m in by["ollama"]["models"]}
    assert ol == {"qwen3-vl:8b": True, "llama3.1:8b": False}
    assert by["ollama"]["version"] == "0.12.0" and by["ollama"]["can_pull"]
    lm = {m["id"]: m["vision"] for m in by["lmstudio"]["models"]}
    assert lm == {"google/gemma-3-12b": True, "qwen2.5-coder": False}  # embeddings model dropped
    assert by["llamacpp"]["models"][0]["vision"] is True
    assert by["openai"]["models"][0]["vision"] is True  # name heuristic


def test_nothing_running_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYQUEUE_GGUF_DIRS", str(tmp_path))
    d = dead_url()
    out = detect.detect({"ollama": d, "lmstudio": d, "llamacpp": d, "openai": d})
    assert out["runtimes"] == [] and out["gguf"] == []


def test_llamacpp_probe_ignores_other_servers(runtimes):
    # An Ollama server has no /props: must not be reported as llama.cpp
    assert detect.probe_llamacpp(runtimes["ollama"].base) is None


def test_ollama_host_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11500")
    assert detect.default_endpoints()["ollama"] == "http://127.0.0.1:11500"


def test_scan_hf_gguf_with_mmproj(tmp_path, monkeypatch):
    snap = tmp_path / "models--ggml-org--gemma-3-4b-it-GGUF" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "gemma-3-4b-it-Q4_K_M.gguf").write_bytes(b"x" * 10)
    (snap / "mmproj-model-f16.gguf").write_bytes(b"x")
    other = tmp_path / "lms" / "text-only"
    other.mkdir(parents=True)
    (other / "llama.gguf").write_bytes(b"x")
    monkeypatch.setenv("APPLYQUEUE_GGUF_DIRS", str(tmp_path))
    found = {f["file"]: f for f in detect.scan_gguf()}
    assert set(found) == {"gemma-3-4b-it-Q4_K_M.gguf", "llama.gguf"}
    assert found["gemma-3-4b-it-Q4_K_M.gguf"]["vision"] and found["gemma-3-4b-it-Q4_K_M.gguf"]["repo"] == "ggml-org/gemma-3-4b-it-GGUF"
    assert not found["llama.gguf"]["vision"]


@pytest.mark.parametrize("hw,expect,speed", [
    ({"ram_bytes": 64 * GB, "gpus": [{"vram_bytes": 24 * GB}]}, "qwen3-vl:30b", "fast"),
    ({"ram_bytes": 32 * GB, "gpus": [{"vram_bytes": 12 * GB}]}, "gemma3:12b", "fast"),
    ({"ram_bytes": 16 * GB, "gpus": [{"vram_bytes": 8 * GB}]}, "qwen3-vl:4b", "fast"),
    ({"ram_bytes": 16 * GB, "apple_silicon": True, "gpus": []}, "gemma3:12b", "good"),
    ({"ram_bytes": 8 * GB, "apple_silicon": True, "gpus": []}, "qwen3-vl:4b", "good"),
    ({"ram_bytes": 64 * GB, "apple_silicon": True, "gpus": []}, "qwen3-vl:32b", "good"),
    # Intel Mac / CPU only: capped to a model that finishes in reasonable time
    ({"ram_bytes": 64 * GB, "intel_mac": True, "gpus": []}, "qwen3-vl:8b", "slow"),
    ({"ram_bytes": 16 * GB, "intel_mac": True, "gpus": []}, "qwen3-vl:4b", "slow"),
    ({"ram_bytes": 8 * GB, "gpus": []}, "qwen3-vl:2b", "slow"),
])
def test_recommendation_by_memory(hw, expect, speed):
    rec = catalog.recommend(hardware.memory_budget(hw))
    assert rec["model"]["id"] == expect
    assert rec["speed"] == speed
    if speed == "slow":
        assert any("CPU" in w for w in rec["warnings"]) and "overnight" in rec["speed_note"]


def test_recommendation_none_when_too_small():
    rec = catalog.recommend(hardware.memory_budget({"ram_bytes": 4 * GB, "gpus": []}))
    assert rec["model"] is None and "not have enough" in rec["warnings"][0]


def test_small_model_warns_review_first():
    rec = catalog.recommend(hardware.memory_budget({"ram_bytes": 8 * GB, "gpus": []}))
    assert any("review-first" in w for w in rec["warnings"])


def test_catalog_is_all_vision_and_sorted():
    assert all(detect.looks_vision(m["id"]) for m in catalog.CATALOG)
    assert catalog.EXPECTATIONS and all(len(e) < 200 for e in catalog.EXPECTATIONS)


def test_hardware_probe_never_raises():
    hw = hardware.probe()
    assert hw["ram_bytes"] >= 0 and isinstance(hw["gpus"], list)
    if sys.platform.startswith("linux"):
        assert hw["ram_bytes"] > GB


def test_pull_streams_progress(runtimes):
    job = pull.start(runtimes["ollama"].base, "qwen3-vl:4b", background=False)
    assert job["state"] == "done" and job["total"] == 1000 and job["completed"] == 1000
    assert runtimes["ollama"].pulls == ["qwen3-vl:4b"]


def test_pull_error_reported(runtimes):
    runtimes["ollama"].fail_pull = True
    job = pull.start(runtimes["ollama"].base, "nope:1b", background=False)
    assert job["state"] == "error" and "does not exist" in job["error"]


def test_pull_rejects_bad_names():
    for bad in ("", "../etc", "a b", "x;rm -rf", "A" * 300):
        assert not pull.valid_model(bad)
        with pytest.raises(ValueError):
            pull.start("http://127.0.0.1:1", bad, background=False)


def test_pull_offline_is_error():
    job = pull.start(dead_url(), "qwen3-vl:2b", background=False)
    assert job["state"] == "error"
