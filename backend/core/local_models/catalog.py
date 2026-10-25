"""Vision-capable models known to work with browser screenshots, smallest first.

Sizes are the download sizes of the default 4-bit builds (Ollama library, checked 2026-10-25).
`hf` is a GGUF repo for llama.cpp (`llama-server -hf <repo>`) / LM Studio."""
from __future__ import annotations

from .hardware import GB

CATALOG = [
    {"id": "qwen3-vl:2b", "family": "Qwen3-VL", "params": "2B", "size_gb": 1.9,
     "hf": "Qwen/Qwen3-VL-2B-Instruct-GGUF", "quality": "basic"},
    {"id": "gemma3:4b", "family": "Gemma 3", "params": "4B", "size_gb": 3.4,
     "hf": "ggml-org/gemma-3-4b-it-GGUF", "quality": "basic"},
    {"id": "qwen3-vl:4b", "family": "Qwen3-VL", "params": "4B", "size_gb": 3.3,
     "hf": "Qwen/Qwen3-VL-4B-Instruct-GGUF", "quality": "fair"},
    {"id": "qwen3-vl:8b", "family": "Qwen3-VL", "params": "8B", "size_gb": 6.2,
     "hf": "Qwen/Qwen3-VL-8B-Instruct-GGUF", "quality": "good"},
    {"id": "gemma3:12b", "family": "Gemma 3", "params": "12B", "size_gb": 8.2,
     "hf": "ggml-org/gemma-3-12b-it-GGUF", "quality": "good"},
    {"id": "gemma3:27b", "family": "Gemma 3", "params": "27B", "size_gb": 17.0,
     "hf": "ggml-org/gemma-3-27b-it-GGUF", "quality": "very good"},
    {"id": "qwen3-vl:30b", "family": "Qwen3-VL", "params": "30B-A3B (MoE)", "size_gb": 20.0,
     "hf": "Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF", "quality": "very good"},
    {"id": "qwen3-vl:32b", "family": "Qwen3-VL", "params": "32B", "size_gb": 21.0,
     "hf": "Qwen/Qwen3-VL-32B-Instruct-GGUF", "quality": "best local"},
]

# The model's weights plus KV cache for ~32k tokens of page context and screenshots.
OVERHEAD_GB = 1.5
# CPU-only machines: larger models are too slow to finish an application in reasonable time.
CPU_MAX_GB = 6.5

SPEED_NOTE = {
    "fast": "Roughly 2-6 minutes per application on a recent GPU.",
    "good": "Roughly 4-12 minutes per application on Apple silicon.",
    "slow": "CPU only: roughly 15-45 minutes per application. Leave it running overnight.",
}


def need_bytes(m: dict) -> int:
    return int((m["size_gb"] * 1.1 + OVERHEAD_GB) * GB)


def recommend(budget: dict) -> dict:
    fits = [m for m in CATALOG if need_bytes(m) <= budget["bytes"]]
    if budget["speed"] == "slow":
        fits = [m for m in fits if m["size_gb"] <= CPU_MAX_GB]
    pick = max(fits, key=lambda m: (m["size_gb"], m["quality"] == "best local")) if fits else None
    # Prefer Qwen3-VL at equal footprint (better at reading forms).
    if pick:
        same = [m for m in fits if abs(m["size_gb"] - pick["size_gb"]) < 1.5 and m["family"] == "Qwen3-VL"]
        pick = same[-1] if same else pick
    warnings = []
    if pick is None:
        warnings.append("This computer does not have enough free memory for a vision model. Use a "
                        "subscription CLI or an API key instead, or close other apps and retry.")
    elif pick["size_gb"] < 3.0:
        warnings.append("Small models make more mistakes on long or unusual forms. Use review-first "
                        "mode and check answers before submitting.")
    if budget["speed"] == "slow":
        warnings.append("No supported GPU was found, so the model runs on the CPU.")
    return {
        "model": pick,
        "alternatives": [m for m in fits if m is not pick][-3:],
        "speed": budget["speed"],
        "speed_note": SPEED_NOTE[budget["speed"]],
        "warnings": warnings,
    }


EXPECTATIONS = [
    "Free and private: nothing leaves your computer except the job sites you apply to.",
    "Slower than cloud AI. Expect fewer applications per hour; overnight runs work well.",
    "Smaller models misread more forms. ApplyQueue still verifies every submission and marks "
    "anything uncertain as needs-review instead of applied.",
    "Your computer must stay awake while it runs, and it will use a lot of memory and power.",
]
