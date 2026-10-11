"""Kiểu dữ liệu dùng chung giữa crawler, API (FastAPI) và Flutter.

Mọi dataclass có `to_dict()` để serialize thẳng ra JSON ở Phase 3.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ChapterRef:
    """Một mục trong danh sách chương của truyện."""

    number: int          # số chương đọc từ URL/tiêu đề (có thể trùng nhau: "Chương 1", "Chương 1: Ngoại truyện")
    title: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StoryInfo:
    """Thông tin để UI hiển thị trước khi crawl."""

    site: str                         # tên site, vd "toctruyen"
    url: str
    title: str
    chapters_found: int               # số chương THỰC SỰ lấy được
    max_chapter_number: int
    announced_total: int | None = None  # số chương site công bố (nếu đọc được)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChapterResult:
    """Kết quả tải 1 chương. Lỗi KHÔNG ném exception, mà ok=False + text báo lỗi."""

    number: int
    text: str
    ok: bool = True
    error: str | None = None


@dataclass
class ProgressEvent:
    """Sự kiện tiến độ; ở Phase 3 sẽ được đẩy ra SSE."""

    done: int
    total: int
    number: int          # số chương vừa xong
    ok: bool
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CrawlResult:
    output_file: str | None
    requested: int                    # số chương nằm trong khoảng đã chọn
    saved: int                        # số chương ghi được nội dung thật
    failed_numbers: list[int] = field(default_factory=list)
    chars: int = 0
    seconds: float = 0.0
    cancelled: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
