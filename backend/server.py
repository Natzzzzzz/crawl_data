"""Tầng HTTP (thư viện chuẩn). Chỉ bind 127.0.0.1, mọi route (trừ /health) cần Bearer token."""
from __future__ import annotations

import hmac
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .jobs import ApiError, JobManager
from .registry import list_sites

log = logging.getLogger(__name__)

MAX_BODY = 1_000_000
JOB_RE = re.compile(r"^/jobs/([0-9a-f]{1,32})$")
JOB_SUB_RE = re.compile(r"^/jobs/([0-9a-f]{1,32})/(events|cancel)$")


class Handler(BaseHTTPRequestHandler):
    server_version = f"CrawlBackend/{__version__}"
    manager: JobManager          # gắn ở make_server
    token: str
    heartbeat = 15.0             # giây giữa các dòng ": ping" của SSE

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---------------------------------------------------------------- io
    def _json(self, status: int, payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("Client ngắt kết nối trước khi nhận phản hồi %s", status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "invalid_json", "Content-Length không hợp lệ.")
        if length > MAX_BODY:
            raise ApiError(413, "body_too_large", "Body quá lớn.")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid_json", "Body không phải JSON hợp lệ.")
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_json", "Body phải là JSON object.")
        return data

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, given = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(given.encode(), self.token.encode())

    # ---------------------------------------------------------------- dispatch
    def do_GET(self):    self._dispatch("GET")      # noqa: E704
    def do_POST(self):   self._dispatch("POST")     # noqa: E704
    def do_DELETE(self): self._dispatch("DELETE")   # noqa: E704

    def _dispatch(self, method: str) -> None:
        try:
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            if method == "GET" and path == "/health":
                return self._json(200, {"ok": True, "version": __version__})
            if not self._authorized():
                raise ApiError(401, "unauthorized", "Thiếu hoặc sai token.")
            self._route(method, path, parse_qs(parts.query))
        except ApiError as e:
            self._json(e.status, {"error": {"code": e.code, "message": e.message}})
        except (BrokenPipeError, ConnectionResetError):
            pass  # client ngắt kết nối (thường là SSE)
        except Exception:
            log.exception("Lỗi không mong đợi: %s %s", method, self.path)
            try:
                self._json(500, {"error": {"code": "internal", "message": "Lỗi nội bộ của backend."}})
            except OSError:
                pass

    def _route(self, method: str, path: str, query: dict) -> None:
        m = self.manager
        if method == "GET" and path == "/sites":
            return self._json(200, {"sites": list_sites()})
        if method == "POST" and path == "/story/info":
            return self._story_info()
        if path == "/jobs":
            if method == "GET":
                return self._json(200, {"jobs": [j.to_dict() for j in m.list()]})
            if method == "POST":
                return self._json(201, m.create(self._body()).to_dict())
        mt = JOB_RE.match(path)
        if mt:
            if method == "GET":
                return self._json(200, m.get(mt.group(1)).to_dict())
            if method == "DELETE":
                m.delete(mt.group(1))
                return self._json(200, {"deleted": mt.group(1)})
        mt = JOB_SUB_RE.match(path)
        if mt:
            if mt.group(2) == "cancel" and method == "POST":
                return self._json(200, m.cancel(mt.group(1)).to_dict())
            if mt.group(2) == "events" and method == "GET":
                return self._events(mt.group(1), query)
        raise ApiError(404, "not_found", "Không có route này.")

    # ---------------------------------------------------------------- handlers
    def _story_info(self) -> None:
        url = str(self._body().get("url") or "").strip()
        cls = self.manager.resolve_crawler(url)
        try:
            info = cls().get_story_info(url)
        except RuntimeError as e:
            raise ApiError(502, "upstream_error", str(e))
        except Exception:
            log.exception("get_story_info lỗi: %s", url)
            raise ApiError(502, "upstream_error", "Không tải được thông tin truyện từ site.")
        self._json(200, info.to_dict())

    def _events(self, job_id: str, query: dict) -> None:
        """SSE. Resume bằng header Last-Event-ID hoặc ?after=<seq> (nhận các sự kiện có seq lớn hơn)."""
        job = self.manager.get(job_id)
        raw = self.headers.get("Last-Event-ID") or (query.get("after") or [""])[0]
        try:
            idx = int(raw) + 1 if raw != "" else 0
        except ValueError:
            raise ApiError(400, "invalid_field", "Last-Event-ID/after phải là số.")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        while True:
            with job.cond:
                if idx >= len(job.events) and not job.finished:
                    job.cond.wait(timeout=self.heartbeat)
                batch = job.events[idx:]
                finished = job.finished
            if batch:
                for ev in batch:
                    chunk = f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
                idx += len(batch)
            elif finished:
                return
            else:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()


def make_server(manager: JobManager, token: str, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("token không được rỗng")
    handler = type("BoundHandler", (Handler,), {"manager": manager, "token": token})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True   # kết nối SSE đang treo không được chặn việc tắt
    return server
