"""CLI kiểm tra nhanh, không phụ thuộc notebook.

    .venv/bin/python -m backend.cli <url> --start 1 --end 6 [--out /tmp/bench/x.txt] [--info]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .registry import detect, list_sites


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crawl truyện (backend dùng chung với Flutter)")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=6)
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--info", action="store_true", help="chỉ in thông tin truyện, không crawl")
    ap.add_argument("--sites", action="store_true", help="liệt kê site được hỗ trợ")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    if args.sites:
        print(json.dumps(list_sites(), ensure_ascii=False, indent=2))
        return 0
    if not args.url:
        ap.error("thiếu url")

    cls = detect(args.url)
    if cls is None:
        print("❌ Site chưa được hỗ trợ. Dùng --sites để xem danh sách.", file=sys.stderr)
        return 2
    crawler = cls()

    if args.info:
        print(json.dumps(crawler.get_story_info(args.url).to_dict(), ensure_ascii=False, indent=2))
        return 0

    result = crawler.crawl(
        args.url, args.start, args.end, args.out, workers=args.workers, delay=args.delay,
        on_progress=lambda e: print(f"  [{e.done}/{e.total}] chương {e.number} {'✅' if e.ok else '⚠️ ' + e.message}"),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if not result.failed_numbers else 1


if __name__ == "__main__":
    raise SystemExit(main())
