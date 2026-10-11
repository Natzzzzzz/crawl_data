"""Nhận diện site từ URL. Thêm site mới = viết 1 file trong sites/ rồi thêm vào CRAWLERS."""
from __future__ import annotations

from .core.base import BaseCrawler
from .sites.toctruyen import TocTruyenCrawler

CRAWLERS: list[type[BaseCrawler]] = [
    TocTruyenCrawler,
]


def detect(url: str) -> type[BaseCrawler] | None:
    for cls in CRAWLERS:
        if cls.matches(url):
            return cls
    return None


def list_sites() -> list[dict]:
    return [{"name": c.name, "host_keywords": list(c.host_keywords)} for c in CRAWLERS]
