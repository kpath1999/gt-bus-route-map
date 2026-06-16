"""Single Vercel entrypoint — dispatches to chat or health logic.

Routes:
  GET  /api/health  → health check (inline)
  POST /api/chat    → delegates to chat.handler
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Import chat handler at module level so Vercel bundles chat.py.
# Vercel sets /var/task = project root (chat/), so api.chat is the correct path.
from api.chat import handler as _ChatHandler  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_local_env() -> None:
    if load_dotenv is None:
        return
    chat_root = Path(__file__).resolve().parents[1]
    for env_name in (".env.local", ".env"):
        env_path = chat_root / env_name
        if env_path.exists():
            load_dotenv(env_path, override=False)


_load_local_env()


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "has_api_key": bool(os.environ.get("GROQ_API_KEY")),
            }).encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def do_POST(self) -> None:
        _ChatHandler.do_POST(self)
