"""Lớp HTTP dùng chung: Session keep-alive, CA bundle, retry backoff + jitter, kiểm encoding."""
from __future__ import annotations

import logging
import os
import random
import re
import threading
import time

import requests
from requests.adapters import HTTPAdapter

try:
    import certifi
except ImportError:  # certifi tuỳ chọn
    certifi = None

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0  # giây; lần n nghỉ BACKOFF_BASE * n + jitter

# Dấu hiệu UTF-8 bị decode nhầm bằng latin-1/cp1252
_MOJIBAKE_RE = re.compile("Ã|â€|ï»¿|Ä\u0091|áº|á»")
_CHARSET_RE = re.compile(r"charset=([\w\-]+)", re.I)


def find_ca_bundle() -> str | bool:
    """CA bundle dùng được; không thì rơi về bundle hệ thống; cuối cùng True.

    Kiểm file tồn tại & đủ lớn vì certifi hỏng khi venv bị đổi tên/di chuyển
    (`OSError: Could not find a suitable TLS CA certificate bundle`).
    """
    candidates: list[str] = []
    if certifi is not None:
        try:
            candidates.append(certifi.where())
        except Exception:
            pass
    candidates += [
        "/etc/ssl/cert.pem",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/usr/share/ca-certificates/mozilla/provider.crt",
    ]
    for path in candidates:
        try:
            if path and os.path.isfile(path) and os.path.getsize(path) > 1024:
                return path
        except OSError:
            continue
    return True


def make_session(pool_size: int = 6, user_agent: str = DEFAULT_UA,
                 cookies: dict | None = None) -> requests.Session:
    """Một Session dùng chung cho mọi request của 1 job (reuse TLS)."""
    session = requests.Session()
    # UA + Accept-Language đặt ở session, không rải rác từng request
    session.headers.update({
        "User-Agent": user_agent,
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    session.verify = find_ca_bundle()
    if cookies:
        session.cookies.update(cookies)
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def backoff_sleep(attempt: int, cancel: threading.Event | None = None) -> None:
    """Backoff tuyến tính + jitter. Đây là sleep CỐ Ý (tiền sử server), đừng xoá."""
    delay = BACKOFF_BASE * (attempt + 1) + random.random()
    if cancel is not None:
        cancel.wait(delay)   # thoát sớm nếu bị huỷ
    else:
        time.sleep(delay)


def http_get(session: requests.Session, url: str, *,
             cancel: threading.Event | None = None, **kw) -> requests.Response | None:
    """GET có retry + backoff/jitter. Trả None nếu vẫn thất bại.

    Bắt cả OSError (lỗi CA/SSL context là OSError, không phải RequestException).
    """
    for attempt in range(MAX_ATTEMPTS):
        if cancel is not None and cancel.is_set():
            return None
        try:
            res = session.get(url, timeout=REQUEST_TIMEOUT, **kw)
            if res.status_code == 200:
                return res
            if res.status_code in RETRY_STATUS:
                log.warning("HTTP %s cho %s (lần %d)", res.status_code, url, attempt + 1)
                backoff_sleep(attempt, cancel)
                continue
            log.warning("HTTP %s cho %s, không retry", res.status_code, url)
            return None
        except (requests.RequestException, OSError) as e:
            if isinstance(e, OSError) and not isinstance(e, requests.RequestException):
                session.verify = True  # để urllib3 tự lấy CA hệ thống
            log.warning("Lỗi mạng %s cho %s (lần %d)", type(e).__name__, url, attempt + 1)
            backoff_sleep(attempt, cancel)
    return None


def response_text(res: requests.Response) -> str:
    """Decode theo charset THẬT, không `res.encoding = 'utf-8'` mù.

    - Header khai charset -> tôn trọng.
    - Không khai (requests mặc định latin-1 cho text/*) -> thử UTF-8 strict, rồi apparent_encoding.
    Sau đó cảnh báo nếu text có dấu hiệu mojibake.
    """
    ctype = res.headers.get("Content-Type", "")
    m = _CHARSET_RE.search(ctype)
    if m:
        encoding = m.group(1)
        text = res.content.decode(encoding, errors="replace")
    else:
        try:
            text, encoding = res.content.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            encoding = res.apparent_encoding or "utf-8"
            text = res.content.decode(encoding, errors="replace")
    if _MOJIBAKE_RE.search(text):
        log.warning("Nghi mojibake ở %s (Content-Type=%r, decode=%s)", res.url, ctype, encoding)
    log.debug("%s Content-Type=%r decode=%s bytes=%d", res.url, ctype, encoding, len(res.content))
    return text
