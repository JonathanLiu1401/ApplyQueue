# Local models

ApplyQueue can run with no AI subscription and no API key. It drives the browser with a
**vision** model on your own computer, because it reads screenshots of application forms.

## Quick start
1. Install [Ollama](https://ollama.com/download) (free) and open it.
2. In ApplyQueue: setup wizard → **Run 100% locally, no subscription** (or Settings → Local models).
3. Click **Download and use** on the recommended model.

Already use LM Studio or llama.cpp? Load a vision model there and start its server; ApplyQueue
detects it on the default port and lists it with a **Use** button.

| Runtime | Detected at | Notes |
|---|---|---|
| Ollama | `OLLAMA_HOST` or `127.0.0.1:11434` | One-click download |
| LM Studio | `127.0.0.1:1234` | Vision models are type `vlm` |
| llama.cpp `llama-server` | `127.0.0.1:8080` | Start with `llama-server -hf <repo>` (the app shows the repo) |
| Other OpenAI-compatible | `127.0.0.1:8000` | e.g. vLLM |
| GGUF files | HF cache, LM Studio and llama.cpp folders | Set `APPLYQUEUE_GGUF_DIRS` to add folders |

## Which model
The app measures RAM and GPU memory and picks the largest Qwen3-VL or Gemma 3 model that fits.

| Hardware | Typical pick | Speed per application |
|---|---|---|
| NVIDIA/AMD GPU, 24 GB | qwen3-vl:30b | 2-6 min |
| GPU, 12 GB | gemma3:12b | 2-6 min |
| Apple silicon, 16 GB | gemma3:12b | 4-12 min |
| CPU only (incl. Intel Mac), 16 GB+ | qwen3-vl:4b or 8b | 15-45 min, run overnight |
| CPU only, 8 GB | qwen3-vl:2b | 15-45 min, review-first |

## Honest expectations
- Local models are slower and make more mistakes than the best cloud models.
- Small models (under ~3 GB) should run in review-first mode.
- ApplyQueue still verifies every application (page + network + email), so a weak model fails
  loudly instead of marking a job applied that was not.
- Everything stays on your computer except the job sites themselves.
