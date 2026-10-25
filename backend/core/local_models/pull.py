"""One-click model download through Ollama's /api/pull (streamed progress)."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
import uuid

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,80}(:[a-z0-9._-]{1,60})?$")


def valid_model(name: str) -> bool:
    return bool(MODEL_RE.match(name or ""))


def _run(job_id: str, base: str, model: str):
    job = _JOBS[job_id]
    try:
        req = urllib.request.Request(f"{base}/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (local Ollama)
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                ev = json.loads(line)
                with _LOCK:
                    if ev.get("error"):
                        job.update(state="error", error=str(ev["error"])[:300])
                        return
                    job["status"] = ev.get("status", job["status"])
                    if ev.get("total"):
                        job["total"] = int(ev["total"])
                        job["completed"] = int(ev.get("completed") or 0)
                    job["updated"] = time.time()
                    if ev.get("status") == "success":
                        job["state"] = "done"
        with _LOCK:
            if job["state"] == "running":
                job["state"] = "done"
    except Exception as e:
        with _LOCK:
            job.update(state="error", error=f"{type(e).__name__}: {e}"[:300])


def start(base: str, model: str, *, background: bool = True) -> dict:
    if not valid_model(model):
        raise ValueError("Invalid model name")
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "model": model, "state": "running", "status": "starting",
                         "completed": 0, "total": 0, "error": "", "updated": time.time()}
    if background:
        threading.Thread(target=_run, args=(job_id, base.rstrip("/"), model), daemon=True).start()
    else:
        _run(job_id, base.rstrip("/"), model)
    return get(job_id)


def get(job_id: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None
