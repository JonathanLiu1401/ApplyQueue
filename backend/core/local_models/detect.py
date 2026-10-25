"""Detect local model runtimes: Ollama, LM Studio, llama.cpp server, other OpenAI-compatible
servers (vLLM etc.), and GGUF files from Hugging Face / LM Studio downloads."""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 1.5
VISION_HINTS = ("vl", "vision", "llava", "gemma3", "gemma-3", "minicpm-v", "moondream", "pixtral",
                "mllama", "bakllava", "qwen2.5vl", "qwen3-vl", "smolvlm", "internvl", "granite3.2-vision")


def _ollama_base() -> str:
    host = (os.environ.get("OLLAMA_HOST") or "").strip()
    if host and not host.startswith("http"):
        host = "http://" + host
    host = host.replace("://0.0.0.0", "://127.0.0.1")
    return (host or "http://127.0.0.1:11434").rstrip("/")


def default_endpoints() -> dict:
    return {
        "ollama": _ollama_base(),
        "lmstudio": "http://127.0.0.1:1234",
        "llamacpp": "http://127.0.0.1:8080",
        "openai": "http://127.0.0.1:8000",
    }


def _get(url: str, data: dict | None = None, timeout: float = TIMEOUT):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (local endpoints only)
        return json.loads(r.read().decode("utf-8") or "null")


def looks_vision(name: str) -> bool:
    n = name.lower()
    return any(h in n for h in VISION_HINTS)


def probe_ollama(base: str) -> dict | None:
    try:
        ver = _get(f"{base}/api/version")
        tags = _get(f"{base}/api/tags") or {}
    except Exception:
        return None
    models = []
    for m in (tags.get("models") or [])[:40]:
        name = m.get("name") or m.get("model") or ""
        vision = None
        try:
            show = _get(f"{base}/api/show", {"model": name})
            caps = show.get("capabilities") if isinstance(show, dict) else None
            if isinstance(caps, list):
                vision = "vision" in caps
        except Exception:
            pass
        if vision is None:
            fams = " ".join((m.get("details") or {}).get("families") or [])
            vision = looks_vision(name) or "clip" in fams or "mllama" in fams
        models.append({"id": name, "vision": bool(vision), "size_bytes": int(m.get("size") or 0)})
    return {"id": "ollama", "name": "Ollama", "base_url": base, "running": True,
            "version": (ver or {}).get("version", ""), "models": models, "can_pull": True}


def _openai_models(base: str) -> list[dict]:
    data = _get(f"{base}/v1/models") or {}
    return [{"id": m.get("id", ""), "vision": looks_vision(m.get("id", ""))} for m in data.get("data") or []]


def probe_lmstudio(base: str) -> dict | None:
    models = None
    try:  # LM Studio's own REST API reports model type ("vlm" = vision)
        data = _get(f"{base}/api/v0/models") or {}
        models = [{"id": m.get("id", ""), "vision": m.get("type") == "vlm" or looks_vision(m.get("id", "")),
                   "loaded": m.get("state") == "loaded"}
                  for m in data.get("data") or [] if m.get("type") != "embeddings"]
    except Exception:
        try:
            models = _openai_models(base)
        except Exception:
            return None
    return {"id": "lmstudio", "name": "LM Studio", "base_url": base, "running": True, "models": models,
            "can_pull": bool(shutil.which("lms"))}


def probe_llamacpp(base: str) -> dict | None:
    try:
        props = _get(f"{base}/props")
    except Exception:
        return None
    if not isinstance(props, dict) or "default_generation_settings" not in props and "model_path" not in props:
        return None
    vision = bool((props.get("modalities") or {}).get("vision"))
    try:
        models = _openai_models(base)
    except Exception:
        models = [{"id": Path(str(props.get("model_path") or "model")).name}]
    for m in models:
        m["vision"] = vision or looks_vision(m["id"])
    return {"id": "llamacpp", "name": "llama.cpp server", "base_url": base, "running": True,
            "models": models, "can_pull": False}


def probe_openai(base: str) -> dict | None:
    try:
        models = _openai_models(base)
    except Exception:
        return None
    return {"id": "openai", "name": "OpenAI-compatible server (vLLM, etc.)", "base_url": base,
            "running": True, "models": models, "can_pull": False}


def gguf_roots() -> list[Path]:
    env = os.environ.get("APPLYQUEUE_GGUF_DIRS")
    if env is not None:
        return [Path(p) for p in env.split(os.pathsep) if p]
    home = Path.home()
    hf = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME") or home / ".cache" / "huggingface") / "hub")
    return [hf, home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models",
            home / ".cache" / "llama.cpp", home / "Library" / "Caches" / "llama.cpp",
            Path(os.environ.get("LOCALAPPDATA") or home) / "llama.cpp"]


def scan_gguf(limit: int = 200) -> list[dict]:
    found: list[dict] = []
    for root in gguf_roots():
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*.gguf"):
                if len(found) >= limit:
                    return found
                if p.name.lower().startswith("mmproj"):
                    continue
                proj = any(q.name.lower().startswith("mmproj") for q in p.parent.glob("*.gguf"))
                repo = next((part[len("models--"):].replace("--", "/") for part in p.parts
                             if part.startswith("models--")), "")
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                found.append({"file": p.name, "repo": repo, "vision": proj, "size_bytes": size,
                              "source": "huggingface" if repo else "local"})
        except OSError:
            continue
    return found


def binaries() -> dict:
    return {name: bool(shutil.which(exe)) for name, exe in
            (("ollama", "ollama"), ("lms", "lms"), ("llama-server", "llama-server"))}


def detect(endpoints: dict | None = None) -> dict:
    eps = {**default_endpoints(), **(endpoints or {})}
    runtimes = []
    for key, fn in (("ollama", probe_ollama), ("lmstudio", probe_lmstudio), ("llamacpp", probe_llamacpp),
                    ("openai", probe_openai)):
        base = eps.get(key)
        if not base:
            continue
        r = fn(base.rstrip("/"))
        if r:
            runtimes.append(r)
    return {"runtimes": runtimes, "gguf": scan_gguf(), "binaries": binaries()}
