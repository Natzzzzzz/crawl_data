"""Giao diện chung cho mọi crawler + vòng crawl song song dùng chung.

Một site chỉ cần cài:  matches(), get_story_info(), get_chapter_list(), fetch_chapter().
Phần song song / sắp xếp / ghi file / huỷ / tiến độ nằm ở `crawl()` và dùng chung.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import requests

from .http import make_session
from .models import ChapterRef, ChapterResult, CrawlResult, ProgressEvent, StoryInfo

log = logging.getLogger(__name__)

DEFAULT_WORKERS = 4   # đo 15 chương: 4–6 là điểm tốt, đừng tăng vô hạn
MAX_WORKERS_CAP = 8
DEFAULT_DELAY = 0.15  # throttle ngẫu nhiên giữa request: CỐ Ý, giữ lại

ProgressCallback = Callable[[ProgressEvent], None]


def origin_of(url: str) -> str:
    """'https://mirror.example/truyen/x' -> 'https://mirror.example'. Không hard-code host (site hay đổi mirror)."""
    parts = urlsplit(url)
    return f"{parts.scheme or 'https'}://{parts.netloc}"


def error_text(number: int, reason: str) -> str:
    return f"Chương {number}\n⚠️ {reason}"


class BaseCrawler(ABC):
    name: str = ""                   # id site, vd "toctruyen"
    host_keywords: tuple[str, ...] = ()  # khớp theo từ khoá trong hostname (mirror đổi TLD liên tục)

    def __init__(self, session: requests.Session | None = None):
        self._session = session

    # ---------- nhận diện ----------
    @classmethod
    def matches(cls, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(k in host for k in cls.host_keywords)

    # ---------- session ----------
    def session(self, workers: int = DEFAULT_WORKERS) -> requests.Session:
        if self._session is None:
            self._session = make_session(pool_size=workers + 2)
        return self._session

    # ---------- phần mỗi site phải cài ----------
    @abstractmethod
    def get_story_info(self, url: str) -> StoryInfo: ...

    @abstractmethod
    def get_chapter_list(self, url: str, cancel: threading.Event | None = None) -> list[ChapterRef]:
        """Toàn bộ chương, sort tăng dần theo `number`. Raise RuntimeError nếu không lấy được gì."""

    @abstractmethod
    def fetch_chapter(self, ref: ChapterRef, cancel: threading.Event | None = None) -> ChapterResult:
        """Tải 1 chương. KHÔNG ném exception: lỗi -> ChapterResult(ok=False, text=error_text(...))."""

    # ---------- đường dẫn mặc định ----------
    def default_filename(self, url: str) -> str:
        slug = urlsplit(url).path.rstrip("/").split("/")[-1] or self.name
        slug = re.sub(r"[^\w\-.]+", "_", slug).strip("._") or self.name  # an toàn cho Windows
        return f"{slug}.txt"

    # ---------- vòng crawl dùng chung ----------
    def crawl(
        self,
        url: str,
        start: int,
        end: int,
        output_file: str | Path | None = None,
        *,
        workers: int = DEFAULT_WORKERS,
        delay: float = DEFAULT_DELAY,
        on_progress: ProgressCallback | None = None,
        on_start: Callable[[int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> CrawlResult:
        workers = max(1, min(int(workers), MAX_WORKERS_CAP))
        cancel = cancel or threading.Event()
        t0 = time.perf_counter()

        try:
            chapters = self.get_chapter_list(url, cancel)
        except RuntimeError:
            if cancel.is_set():  # huỷ lúc đang lấy danh sách chương: không phải lỗi
                return CrawlResult(output_file=None, requested=0, saved=0,
                                   seconds=time.perf_counter() - t0, cancelled=True)
            raise
        max_num = max((c.number for c in chapters), default=0)
        log.info("Truyện có %d chương (số cao nhất: %d)", len(chapters), max_num)

        selected = sorted((c for c in chapters if start <= c.number <= end), key=lambda c: c.number)
        if not selected:
            log.warning("Không có chương nào trong khoảng %d→%d", start, end)
            return CrawlResult(output_file=None, requested=0, saved=0,
                               seconds=time.perf_counter() - t0, cancelled=cancel.is_set())

        out_path = Path(output_file) if output_file else Path(self.default_filename(url))
        if on_start:
            on_start(len(selected))  # UI biết tổng số chương trước khi chương đầu tiên xong

        # Key theo INDEX của danh sách đã chọn, không theo số chương -> số chương trùng không đè nhau.
        results: list[ChapterResult | None] = [None] * len(selected)
        done = 0

        def work(idx: int) -> tuple[int, ChapterResult]:
            ref = selected[idx]
            if cancel.is_set():
                return idx, ChapterResult(ref.number, error_text(ref.number, "Đã huỷ"), ok=False, error="cancelled")
            time.sleep(random.uniform(0, delay))
            try:
                return idx, self.fetch_chapter(ref, cancel)
            except Exception as e:  # lưới an toàn: 1 chương lỗi không được giết cả job
                log.exception("fetch_chapter lỗi bất ngờ ở chương %d", ref.number)
                return idx, ChapterResult(ref.number, error_text(ref.number, f"Lỗi: {type(e).__name__}"),
                                          ok=False, error=str(e))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, i) for i in range(len(selected))]
            for fut in as_completed(futures):
                idx, res = fut.result()
                results[idx] = res
                done += 1
                if on_progress:
                    on_progress(ProgressEvent(done=done, total=len(selected), number=res.number,
                                              ok=res.ok, message=res.error or ""))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for idx, ref in enumerate(selected):
                res = results[idx]
                f.write(res.text if res else error_text(ref.number, "Không có dữ liệu"))
                f.write("\n\n")  # sort theo số chương, các chương cách nhau 1 dòng trống

        failed = [selected[i].number for i, r in enumerate(results) if r is None or not r.ok]
        result = CrawlResult(
            output_file=str(out_path),
            requested=len(selected),
            saved=len(selected) - len(failed),
            failed_numbers=failed,
            chars=sum(len(r.text) for r in results if r and r.ok),
            seconds=time.perf_counter() - t0,
            cancelled=cancel.is_set(),
        )
        log.info("Lưu %d/%d chương (%s ký tự) vào %s trong %.2fs",
                 result.saved, result.requested, f"{result.chars:,}", out_path, result.seconds)
        if failed:
            log.warning("Thiếu chương: %s", failed)
        return result
