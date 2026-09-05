"""Single Vercel entrypoint — ASGI app dispatching to chat or health logic.

Routes:
  GET  /api/health  → health check (inline)
  POST /api/chat    → delegates to chat handler
"""

from __future__ import annotations

import json
import os
import socket
from io import BytesIO
from pathlib import Path
from typing import Any, TYPE_CHECKING

from asgiref.wsgi import WsgiToAsgi

if TYPE_CHECKING:
    from api.chat import handler as _ChatHandler

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


class _MockConnection:
    """Minimal socket-like object that BaseHTTPRequestHandler.setup() expects."""

    def __init__(self):
        self._rfile = BytesIO(b"")

    def makefile(self, mode: str, *args, **kwargs) -> BytesIO:
        return self._rfile

    def recv(self, bufsize: int) -> bytes:
        return b""

    def sendall(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self._rfile.close()

    def getsockname(self):
        return ("127.0.0.1", 0)

    def getpeername(self):
        return ("127.0.0.1", 0)


def app(environ: dict, start_response: callable):
    """WSGI app that wraps the BaseHTTPRequestHandler for Vercel/Python dev."""
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "") or "/"
    query = environ.get("QUERY_STRING", "")

    # Health endpoint
    if method == "GET" and path.rstrip("/").endswith("/health"):
        body = json.dumps({
            "status": "ok",
            "has_api_key": bool(os.environ.get("OPENROUTER_PRODUCTION")),
        }).encode()
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
        ])
        return [body]

    # OPTIONS preflight
    if method == "OPTIONS":
        start_response("200 OK", [
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type"),
        ])
        return [b""]

    # Load analysis dependencies only for a chat request. Health remains a fast
    # deployment and OpenRouter-secret probe if an analysis dependency fails.
    from api.chat import handler as chat_handler

    # Delegate everything else to the chat handler
    body = _read_body(environ)
    mock_conn = _MockConnection()
    handler_instance = chat_handler(
        request=mock_conn,
        client_address=(environ.get("REMOTE_ADDR", "0.0.0.0"), 0),
        server=None,
    )
    # Override rfile/wfile after setup() uses the mock connection
    handler_instance.rfile = BytesIO(body)
    handler_instance.wfile = BytesIO()
    # Populate BaseHTTPRequestHandler fields from environ
    handler_instance.command = method
    handler_instance.path = f"{path}?{query}" if query else path
    handler_instance.request_version = environ.get("SERVER_PROTOCOL", "HTTP/1.1")
    handler_instance.headers = _environ_to_headers(environ)
    handler_instance.requestline = f"{method} {handler_instance.path} {handler_instance.request_version}"

    method_name = f"do_{method}"
    if hasattr(handler_instance, method_name):
        try:
            getattr(handler_instance, method_name)()
        except Exception as exc:
            handler_instance.send_response(500)
            handler_instance.send_header("Content-Type", "application/json")
            handler_instance.end_headers()
            handler_instance.wfile.write(json.dumps({"error": str(exc)}).encode())
    else:
        handler_instance.send_response(405)
        handler_instance.send_header("Content-Type", "application/json")
        handler_instance.end_headers()
        handler_instance.wfile.write(json.dumps({"error": "Method not allowed"}).encode())

    response_bytes = handler_instance.wfile.getvalue()
    status_line, header_block, response_body = _split_response(response_bytes)
    status = status_line.decode().split(" ", 1)[1].strip()
    headers = _parse_headers(header_block)
    start_response(status, headers)
    return [response_body]


app = WsgiToAsgi(app)


def _read_body(environ: dict) -> bytes:
    try:
        content_length = int(environ.get("CONTENT_LENGTH", 0))
    except ValueError:
        content_length = 0
    if content_length:
        return environ["wsgi.input"].read(content_length)
    return b""


def _environ_to_headers(environ: dict):
    """Build a mock HTTPMessage from WSGI environ for handler.headers."""
    from email.message import Message
    msg = Message()
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            msg[name] = value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            name = key.replace("_", "-").title()
            msg[name] = value
    return msg


def _split_response(data: bytes) -> tuple[bytes, bytes, bytes]:
    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        header_end = data.find(b"\n\n")
        if header_end == -1:
            return b"HTTP/1.1 200 OK", b"", data
        raw = data[:header_end]
        body = data[header_end + 2:]
    else:
        raw = data[:header_end]
        body = data[header_end + 4:]
    lines = raw.split(b"\r\n") if b"\r\n" in raw else raw.split(b"\n")
    status = lines[0]
    headers = b"\r\n".join(lines[1:])
    return status, headers, body


def _parse_headers(header_block: bytes) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for line in header_block.decode("latin-1").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            headers.append((name.strip(), value.strip()))
    return headers
