"""Mock local model runtimes (Ollama, LM Studio, llama.cpp server, vLLM) for M6 tests."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockRuntime:
    def __init__(self, kind: str, models: list[str] | None = None, vision: set[str] | None = None):
        self.kind = kind
        self.models = list(models or [])
        self.vision = set(vision or ())
        self.pulls: list[str] = []
        self.fail_pull = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                k, p = outer.kind, self.path
                if k == "ollama" and p == "/api/version":
                    return self._json(200, {"version": "0.12.0"})
                if k == "ollama" and p == "/api/tags":
                    return self._json(200, {"models": [{"name": m, "size": 123, "details": {"families": []}}
                                                       for m in outer.models]})
                if k == "lmstudio" and p == "/api/v0/models":
                    return self._json(200, {"data": [{"id": m, "type": "vlm" if m in outer.vision else "llm",
                                                      "state": "loaded"} for m in outer.models]
                                           + [{"id": "nomic-embed", "type": "embeddings"}]})
                if k == "llamacpp" and p == "/props":
                    return self._json(200, {"default_generation_settings": {}, "model_path": "/m/model.gguf",
                                            "modalities": {"vision": bool(outer.vision)}})
                if k in ("lmstudio", "llamacpp", "openai") and p == "/v1/models":
                    return self._json(200, {"object": "list", "data": [{"id": m} for m in outer.models]})
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                body = self._body()
                if outer.kind == "ollama" and self.path == "/api/show":
                    m = body.get("model")
                    caps = ["completion"] + (["vision"] if m in outer.vision else [])
                    return self._json(200, {"capabilities": caps})
                if outer.kind == "ollama" and self.path == "/api/pull":
                    m = body.get("model")
                    outer.pulls.append(m)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    if outer.fail_pull:
                        self.wfile.write(b'{"error":"pull model manifest: file does not exist"}\n')
                        return
                    for ev in ({"status": "pulling manifest"},
                               {"status": "pulling abc", "total": 1000, "completed": 500},
                               {"status": "pulling abc", "total": 1000, "completed": 1000},
                               {"status": "success"}):
                        self.wfile.write((json.dumps(ev) + "\n").encode())
                    outer.models.append(m)
                    return
                return self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def dead_url() -> str:
    """A localhost URL with nothing listening."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"
