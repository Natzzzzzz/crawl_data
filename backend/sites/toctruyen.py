"""toctruyen.net — không Selenium.

- Danh sách chương: `GET /content/subitems?pid=<id>` (JSON nội bộ, trả TOÀN BỘ chương;
  scroll DOM chỉ thấy ~10 chương). Phải GET + header X-Requested-With + Referer.
- Nội dung chương: có sẵn trong HTML tĩnh.
- Host lấy từ chính URL user dán (site đổi mirror liên tục), không hard-code.
"""
from __future__ import annotations

import logging
import re
import threading
from urllib.parse import urljoin

from ..core.base import BaseCrawler, error_text, origin_of
from ..core.http import http_get, response_text
from ..core.models import ChapterRef, ChapterResult, StoryInfo
from ..core.text import densest_div, paragraphs_from_block, parse_html

log = logging.getLogger(__name__)

CHAP_NUM_RE = re.compile(r"chuong-(\d+)", re.I)
ANY_NUM_RE = re.compile(r"(\d+)")

CONTENT_SELECTORS = (
    "section.container.novel-reading-content",
    "#chapter-content, .chapter-content, #content-full",
)


def parse_story_url(url: str) -> tuple[str, str]:
    """'https://toctruyen.net/truyen/avlBQwkA/ban-trai-...' -> (pid, slug)."""
    path = url.split("#")[0].split("?")[0].strip("/")
    parts = path.split("/")
    slug = parts[-1]
    pid = parts[-2] if len(parts) >= 2 else ""
    return pid, slug


def chapter_number(link: str, title: str) -> int | None:
    """Ưu tiên '...-chuong-12' trong URL, fallback số đầu tiên trong title."""
    m = CHAP_NUM_RE.search(link or "")
    if m:
        return int(m.group(1))
    m = ANY_NUM_RE.search(title or "")
    return int(m.group(1)) if m else None


class TocTruyenCrawler(BaseCrawler):
    name = "toctruyen"
    host_keywords = ("toctruyen",)

    # ---------- danh sách chương ----------
    def _load_chapters(self, url: str, cancel: threading.Event | None) -> tuple[list[ChapterRef], bool]:
        """-> (chapters sort tăng dần, used_fallback)."""
        session = self.session()
        origin = origin_of(url)
        pid, _ = parse_story_url(url)
        headers = {
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        chapters: list[ChapterRef] = []
        seen: set[str] = set()

        res = http_get(session, f"{origin}/content/subitems", params={"pid": pid},
                       headers=headers, cancel=cancel)
        if res is not None:
            try:
                payload = res.json()
            except ValueError:
                payload = None
            if payload and payload.get("error") in (0, None):
                for item in (payload.get("data") or {}).get("e") or []:
                    # item = [id, type, title, url, date, views, locked, ...]
                    if len(item) < 4:
                        continue
                    title, link = item[2], item[3]
                    if not link or link in seen:
                        continue
                    num = chapter_number(link, title)
                    if num is None:
                        continue
                    seen.add(link)
                    chapters.append(ChapterRef(num, str(title or "").strip(), urljoin(origin, link)))

        used_fallback = False
        if not chapters:
            used_fallback = True
            log.warning("API danh sách chương không dùng được, fallback về parsing HTML "
                        "(chỉ lấy được các chương render sẵn trong trang).")
            page = http_get(session, url, cancel=cancel)
            soup = parse_html(response_text(page)) if page is not None else None
            if soup is not None:
                for a in soup.select("div.subitem-item div.name a[href]"):
                    link = a["href"]
                    if link in seen:
                        continue
                    title = a.get("title") or a.get_text(strip=True)
                    num = chapter_number(link, title)
                    if num is None:
                        continue
                    seen.add(link)
                    chapters.append(ChapterRef(num, str(title).strip(), urljoin(origin, link)))

        if not chapters:
            raise RuntimeError("Không lấy được danh sách chương — kiểm tra lại URL truyện.")

        chapters.sort(key=lambda c: c.number)  # stable: số trùng giữ nguyên thứ tự gặp
        return chapters, used_fallback

    def get_chapter_list(self, url: str, cancel: threading.Event | None = None) -> list[ChapterRef]:
        return self._load_chapters(url, cancel)[0]

    # ---------- thông tin truyện ----------
    def get_story_info(self, url: str) -> StoryInfo:
        chapters, used_fallback = self._load_chapters(url, None)
        max_num = max(c.number for c in chapters)
        warnings: list[str] = []
        if used_fallback:
            warnings.append("API danh sách chương hỏng/đổi schema: chỉ lấy được số chương render sẵn trong trang.")

        _, slug = parse_story_url(url)
        title, announced = slug, None
        page = http_get(self.session(), url)
        soup = parse_html(response_text(page)) if page is not None else None
        if soup is not None:
            og = soup.find("meta", property="og:title")
            h1 = soup.find("h1")
            title = ((og.get("content") if og else None) or (h1.get_text(strip=True) if h1 else None) or slug).strip()
            num_tag = soup.select_one("span.subitem-list-num")
            if num_tag:
                m = ANY_NUM_RE.search(num_tag.get_text())
                announced = int(m.group(1)) if m else None
        else:
            warnings.append("Không tải được trang truyện: thiếu tên truyện và số chương site công bố.")

        # Đối chiếu số chương lấy được với số site công bố: chống mất chương im lặng.
        if announced is not None and announced != len(chapters):
            warnings.append(f"Lấy được {len(chapters)} chương nhưng site công bố {announced} — có thể thiếu chương.")

        return StoryInfo(site=self.name, url=url, title=title, chapters_found=len(chapters),
                         max_chapter_number=max_num, announced_total=announced, warnings=warnings)

    # ---------- nội dung chương ----------
    def fetch_chapter(self, ref: ChapterRef, cancel: threading.Event | None = None) -> ChapterResult:
        res = http_get(self.session(), ref.url, cancel=cancel)
        if res is None:
            return ChapterResult(ref.number, error_text(ref.number, "Không tải được trang"),
                                 ok=False, error="fetch_failed")
        soup = parse_html(response_text(res))
        block = None
        if soup is not None:
            for sel in CONTENT_SELECTORS:
                block = soup.select_one(sel)
                if block is not None:
                    break
        if block is None:
            return ChapterResult(ref.number, error_text(ref.number, "Không lấy được nội dung"),
                                 ok=False, error="no_content_block")
        paragraphs = paragraphs_from_block(densest_div(block))
        if not paragraphs:
            return ChapterResult(ref.number, error_text(ref.number, "Không lấy được nội dung"),
                                 ok=False, error="no_paragraphs")
        return ChapterResult(ref.number, "\n\n".join(paragraphs))
