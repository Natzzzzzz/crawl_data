"""Quản lý job crawl: tạo, chạy nền, huỷ, log sự kiện (cho SSE), lịch sử lưu đĩa.

Không phụ thuộc framework HTTP: tầng `server.py` chỉ gọi các hàm ở đây.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .core.base import DEFAULT_DELAY, DEFAULT_WORKERS, MAX_WORKERS_CAP, BaseCrawler
from .core.models import ProgressEvent

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "cancelled", "interrupted")
MAX_HISTORY = 200

_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


class ApiError(Exception):
    """Lỗi có mã HTTP + code máy đọc được + thông điệp cho người dùng."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sanitize_filename(name: str) -> str:
    """Bỏ thư mục, ký tự cấm trên Windows, tên dành riêng; đảm bảo có đuôi."""
    name = Path(str(name).replace("\\", "/")).name
    name = _BAD_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name:
        raise ApiError(400, "invalid_filename", "Tên file không hợp lệ.")
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, "txt"
    stem = stem[:120]
    if stem.upper() in _WINDOWS_RESERVED:
        stem = "_" + stem
    return f"{stem}.{suffix[:10]}"


def _int_field(req: dict, key: str, default: int | None = None) -> int:
    v = req.get(key, default)
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ApiError(400, "invalid_field", f"'{key}' phải là số nguyên.")
    return int(v)


@dataclass
class Job:
    id: str
    url: str
    site: str
    start: int
    end: int
    workers: int
    delay: float
    out_dir: str
    output_file: str
    title: str = ""
    status: str = "queued"
    done: int = 0
    total: int = 0
    result: dict | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    # --- chỉ tồn tại trong RAM ---
    events: list = field(default_factory=list, repr=False)
    cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    PUBLIC = ("id", "url", "site", "title", "start", "end", "workers", "delay", "out_dir",
              "output_file", "status", "done", "total", "result", "error",
              "created_at", "started_at", "finished_at")

    @property
    def finished(self) -> bool:
        return self.status in FINISHED

    def to_dict(self) -> dict:
        with self.cond:
            return {k: getattr(self, k) for k in self.PUBLIC}

    # ---- sự kiện (SSE đọc từ list này) ----
    def emit(self, type_: str, **data) -> None:
        with self.cond:
            self.events.append({"seq": len(self.events), "type": type_, **data})
            self.cond.notify_all()

    def try_start(self) -> bool:
        with self.cond:
            if self.status != "queued":
                return False
            self.status, self.started_at = "running", _now()
            self.events.append({"seq": len(self.events), "type": "status", "status": "running"})
            self.cond.notify_all()
            return True

    def finish(self, status: str, result: dict | None = None, error: str | None = None) -> bool:
        """Chuyển sang trạng thái cuối + sự kiện `end` trong cùng một lần khoá."""
        with self.cond:
            if self.finished:
                return False
            self.status, self.result, self.error, self.finished_at = status, result, error, _now()
            self.events.append({"seq": len(self.events), "type": "end", "status": status,
                                "result": result, "error": error})
            self.cond.notify_all()
            return True


class JobManager:
    def __init__(self, data_dir: str | Path, *, detect: Callable[[str], type[BaseCrawler] | None],
                 max_concurrent: int = 2):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.default_out_dir = self.data_dir / "downloads"
        self._detect = detect
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._history_path = self.data_dir / "jobs.json"
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="job")
        self._load()

    # ------------------------------------------------------------ site
    def resolve_crawler(self, url: object) -> type[BaseCrawler]:
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ApiError(400, "invalid_url", "URL phải bắt đầu bằng http:// hoặc https://")
        cls = self._detect(url)
        if cls is None:
            raise ApiError(400, "unsupported_site", "Site này chưa được hỗ trợ.")
        return cls

    # ------------------------------------------------------------ CRUD
    def create(self, req: dict) -> Job:
        url = str(req.get("url") or "").strip()
        cls = self.resolve_crawler(url)

        start = _int_field(req, "start", 1)
        end = _int_field(req, "end")
        if start < 1:
            raise ApiError(400, "invalid_range", "'start' phải >= 1.")
        if end < start:
            raise ApiError(400, "invalid_range", "'end' phải >= 'start'.")
        workers = max(1, min(_int_field(req, "workers", DEFAULT_WORKERS), MAX_WORKERS_CAP))
        try:
            delay = float(req.get("delay", DEFAULT_DELAY))
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_field", "'delay' phải là số.")
        delay = max(0.0, min(delay, 5.0))

        out_dir_raw = req.get("out_dir")
        if out_dir_raw:
            out_dir = Path(str(out_dir_raw))
            if not out_dir.is_absolute():
                raise ApiError(400, "invalid_out_dir", "'out_dir' phải là đường dẫn tuyệt đối.")
        else:
            out_dir = self.default_out_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ApiError(400, "invalid_out_dir", f"Không tạo được thư mục lưu: {e.strerror or e}")

        filename = sanitize_filename(req["filename"]) if req.get("filename") else cls().default_filename(url)
        title = str(req.get("title") or "")[:200]

        with self._lock:  # đặt chỗ tên file + đăng ký job phải nguyên tử
            path = self._reserve_path(out_dir, filename)
            job = Job(id=uuid.uuid4().hex[:12], url=url, site=cls.name, start=start, end=end,
                      workers=workers, delay=delay, out_dir=str(out_dir), output_file=str(path), title=title)
            self._jobs[job.id] = job
            self._trim_history()
            self._save()
        self._pool.submit(self._run, job, cls)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(404, "job_not_found", "Không tìm thấy job.")
        return job

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        job.cancel.set()
        with job.cond:
            queued = job.status == "queued"
        if queued and job.finish("cancelled"):  # chưa chạy thì kết thúc ngay, khỏi chờ slot
            self._save()
        return job

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job.finished:
            raise ApiError(409, "job_active", "Job đang chạy, hãy huỷ trước khi xoá.")
        with self._lock:
            self._jobs.pop(job_id, None)
            self._save()

    def shutdown(self) -> None:
        for job in self.list():
            if not job.finished:
                job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._save()

    # ------------------------------------------------------------ chạy nền
    def _run(self, job: Job, cls: type[BaseCrawler]) -> None:
        if job.cancel.is_set():
            if job.finish("cancelled"):
                self._save()
            return
        if not job.try_start():  # đã bị huỷ lúc còn trong hàng đợi
            return
        try:
            def on_start(total: int) -> None:
                with job.cond:
                    job.total = total
                job.emit("start", total=total)

            def on_progress(ev: ProgressEvent) -> None:
                with job.cond:
                    job.done, job.total = ev.done, ev.total
                job.emit("progress", **ev.to_dict())

            res = cls().crawl(job.url, job.start, job.end, job.output_file, workers=job.workers,
                              delay=job.delay, on_progress=on_progress, on_start=on_start, cancel=job.cancel)
            rd = res.to_dict()
            if res.cancelled:
                job.finish("cancelled", rd)
            elif res.requested == 0:
                job.finish("failed", rd, f"Không có chương nào trong khoảng {job.start}→{job.end}.")
            elif res.saved == 0:
                job.finish("failed", rd, "Không tải được chương nào.")
            else:  # có thể thiếu một vài chương: UI đọc result.failed_numbers
                job.finish("done", rd)
        except Exception as e:
            log.exception("Job %s lỗi", job.id)
            msg = str(e) if isinstance(e, RuntimeError) else f"Lỗi không mong đợi: {type(e).__name__}"
            job.finish("failed", None, msg)
        self._save()

    # ------------------------------------------------------------ file tên
    def _reserve_path(self, out_dir: Path, filename: str) -> Path:
        norm = lambda p: os.path.normcase(str(p))  # noqa: E731  (Windows không phân biệt hoa/thường)
        taken = {norm(j.output_file) for j in self._jobs.values() if not j.finished}
        stem, _, suffix = filename.rpartition(".")
        candidate, n = out_dir / filename, 2
        while candidate.exists() or norm(candidate) in taken:  # không ghi đè im lặng
            candidate = out_dir / f"{stem}_{n}.{suffix}"
            n += 1
        return candidate

    # ------------------------------------------------------------ lịch sử
    def _trim_history(self) -> None:
        extra = len(self._jobs) - MAX_HISTORY
        if extra > 0:
            old = sorted((j for j in self._jobs.values() if j.finished), key=lambda j: j.created_at)[:extra]
            for j in old:
                self._jobs.pop(j.id, None)

    def _save(self) -> None:
        with self._lock:
            data = [j.to_dict() for j in self._jobs.values()]
            tmp = self._history_path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self._history_path)
            except OSError:
                log.exception("Không ghi được lịch sử job (%s)", self._history_path)

    def _load(self) -> None:
        if not self._history_path.exists():
            return
        try:
            rows = json.loads(self._history_path.read_text(encoding="utf-8"))
            assert isinstance(rows, list)
        except (OSError, ValueError, AssertionError):
            bad = self._history_path.with_suffix(".corrupt")
            log.exception("jobs.json hỏng, đổi tên thành %s và bắt đầu lại", bad)
            try:
                os.replace(self._history_path, bad)
            except OSError:
                pass
            return
        for row in rows:
            try:
                job = Job(**{k: row[k] for k in Job.PUBLIC if k in row})
            except TypeError:
                log.warning("Bỏ qua dòng lịch sử không đọc được: %r", row)
                continue
            if job.status in ACTIVE:  # process trước đó chết giữa chừng
                job.status, job.error = "interrupted", "Backend đã dừng khi job đang chạy."
                job.finished_at = job.finished_at or _now()
            # job cũ không còn log tiến độ: chỉ giữ 1 sự kiện `end` để SSE đóng gọn
            job.events.append({"seq": 0, "type": "end", "status": job.status,
                               "result": job.result, "error": job.error})
            self._jobs[job.id] = job
