"""线路 B 独立 HTTP 服务。"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from apps.line_b_web.control import LineBControl

INDEX = Path(__file__).with_name("index.html")


def make_handler(control: LineBControl, token: str, logs=None, index_html: bytes | None = None):
    page = INDEX.read_bytes() if index_html is None else bytes(index_html)
    class Handler(BaseHTTPRequestHandler):
        server_version = "Q1LineBWeb/1"

        def _authorized(self) -> bool:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            supplied = self.headers.get("X-Control-Token") or query.get("token", [""])[0]
            return bool(token) and supplied == token

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, value) -> None:
            self._send(code, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _download(self, filename: str, body: bytes) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("请求体长度不合法")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return value

        def do_GET(self):  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if not self._authorized():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "控制令牌无效"})
            if path == "/api/status":
                return self._json(HTTPStatus.OK, control.get_status())
            if path == "/api/logs":
                return self._json(HTTPStatus.OK, logs.web_status() if logs is not None else {"enabled": False})
            if path == "/api/logs/download":
                if logs is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "运行记录未启用"})
                mode = control.get_status().get("mode")
                if mode not in ("LYING", "DAMPED"):
                    return self._json(HTTPStatus.CONFLICT, {"error": "为避免日志打包影响实时控制，请先正常趴下再下载"})
                try:
                    filename, body = logs.web_download()
                except RuntimeError as e:
                    return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(e)})
                return self._download(filename, body)
            if path == "/":
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if not self._authorized():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "控制令牌无效"})
            try:
                data = self._read_json()
                if path == "/api/drive":
                    control.drive(**data)
                elif path == "/api/action":
                    control.action(data.get("action", ""))
                elif path == "/api/session":
                    control.session(data.get("active"))
                elif path == "/api/settings":
                    control.settings(**data)
                else:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            self._json(HTTPStatus.OK, {"ok": True})

        def log_message(self, fmt, *args):
            return

    return Handler


def open_server(control: LineBControl, host: str, port: int, token: str, logs=None,
                index_html: bytes | None = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(control, token, logs, index_html))
    threading.Thread(target=server.serve_forever, name="q1-line-b-web", daemon=True).start()
    return server
