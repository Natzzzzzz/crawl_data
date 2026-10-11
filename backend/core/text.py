"""Trích đoạn văn từ HTML. Quy tắc: lấy <p> làm đoạn, blacklist HẸP, không chuẩn hoá dấu câu."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

PAGE_NUM_RE = re.compile(r"^\d{1,3}$")             # <p>1</p>, <p>2</p> lạc vào nội dung
MULTISPACE_RE = re.compile(r"[ \t\u00a0]+")
LEAD_PUNCT_RE = re.compile(r"^\s+([,.!?;:])")
# Chỉ bỏ dòng kết truyện. "……" / "…" KHÔNG nằm ở đây: là nhịp im lặng của nhân vật = nội dung thật.
END_MARKERS = {"hết.", "hết", "hết chương", "hoàn", "hoàn thành", "(hoàn thành)", "full"}

STRIP_TAGS = ["script", "style", "ins", "iframe", "center", "h1", "h2", "h3"]


def parse_html(html: str) -> BeautifulSoup | None:
    """lxml -> html.parser. Trả None nếu không parser nào dùng được (không để FeatureNotFound giết run)."""
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, parser)
        except Exception:
            continue
    return None


def densest_div(block: Tag) -> Tag:
    """Class chứa nội dung có thể sinh ngẫu nhiên -> chọn div con trực tiếp có nhiều <p> nhất."""
    children = block.find_all("div", recursive=False)
    if not children:
        return block
    return max(children, key=lambda d: len(d.find_all("p")))


def _is_junk(line: str) -> bool:
    return not line or bool(PAGE_NUM_RE.fullmatch(line)) or line.lower() in END_MARKERS


def clean_line(text: str) -> str:
    text = MULTISPACE_RE.sub(" ", text).strip()
    return LEAD_PUNCT_RE.sub(r"\1", text)


def paragraphs_from_block(block: Tag, *, extra_junk: set[str] | None = None) -> list[str]:
    """Các đoạn văn của 1 khối nội dung. `extra_junk`: dòng (lowercase) riêng của site cần bỏ."""
    for tag in block(STRIP_TAGS):
        tag.decompose()
    extra = extra_junk or set()

    paragraphs = []
    for p in block.find_all("p"):
        line = clean_line(p.get_text(" ", strip=True))
        if _is_junk(line) or line.lower() in extra:
            continue
        paragraphs.append(line)

    if not paragraphs:  # truyện dùng <br> thay vì <p>
        for raw in block.get_text("\n", strip=True).splitlines():
            line = clean_line(raw)
            if _is_junk(line) or line.lower() in extra:
                continue
            paragraphs.append(line)
    return paragraphs
