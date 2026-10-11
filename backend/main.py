"""Khởi động backend:  python -m backend [--port 8765] [--data-dir DIR]

In ĐÚNG 1 dòng JSON ra stdout khi sẵn sàng (Flutter đọc dòng này để lấy port/token):
    {"event": "ready", "host": "127.0.0.1", "port": 8765, "token": "...", "data_dir": "..."}
Token: --token, hoặc biến môi trường CRAWL_BACKEND_TOKEN, hoặc tự sinh ngẫu nhiên.

--exit-on-stdin-eof: backend tự thoát khi stdin bị đóng. Flutter bật cờ này: nếu app bị tắt đột ngột (kể cả
bị kill), hệ điều hành đóng pipe stdin và backend không bị bỏ mồ côi chạy mãi.
(Không truyền token qua argv nếu tránh được: argv hiện trong danh sách process.)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import sys
import threading
from pathlib import Path

from .jobs import JobManager
from .registry import detect
from .server import make_server


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / ("crawl_backend" if os.environ.get("LOCALAPPDATA") else ".crawl_backend")


def _watch_stdin(on_eof) -> None:
    """Chặn đọc stdin; khi EOF (tiến trình cha chết hoặc đóng pipe) thì gọi on_eof."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:      # chạy không có stdin (vd pythonw): không có gì để theo dõi
        return

    def run() -> None:
        try:
            while stream.read(4096):
                pass
        except (OSError, ValueError):
            pass
        on_eof()

    threading.Thread(target=run, name="stdin-watch", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backend crawler (chỉ lắng nghe 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="0 = chọn cổng trống bất kỳ")
    ap.add_argument("--token")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--max-concurrent", type=int, default=2, help="số job chạy cùng lúc")
    ap.add_argument("--exit-on-stdin-eof", action="store_true", help="thoát khi tiến trình cha đóng stdin")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    token = args.token or os.environ.get("CRAWL_BACKEND_TOKEN") or secrets.token_urlsafe(24)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    manager = JobManager(data_dir, detect=detect, max_concurrent=max(1, args.max_concurrent))
    try:
        server = make_server(manager, token, port=args.port)
    except OSError as e:
        print(json.dumps({"event": "error", "code": "bind_failed", "message": str(e)}), flush=True)
        return 3

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):   # SIGBREAK chỉ có trên Windows
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, stop)
            except (ValueError, OSError):
                pass

    if args.exit_on_stdin_eof:
        _watch_stdin(stop)

    # ensure_ascii=True (mặc định) để console Windows cp1252 không lỗi với đường dẫn tiếng Việt
    print(json.dumps({"event": "ready", "host": "127.0.0.1", "port": server.server_address[1],
                      "token": token, "data_dir": str(data_dir)}), flush=True)
    try:
        server.serve_forever()
    finally:
        manager.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
