"""Test crawler toctruyen bằng server HTTP giả (không cần mạng). Chạy: python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.core.http import make_session, response_text
from backend.core.text import paragraphs_from_block, parse_html
from backend.registry import detect
from backend.sites.toctruyen import TocTruyenCrawler, chapter_number, parse_story_url

PID = "abc123"


def chapter_html(n: int, title: str = "") -> str:
    # class của khối text ngẫu nhiên + 1 div "rác" ít <p> hơn
    return f"""<html><body><section class="container novel-reading-content">
    <div class="ads"><p>quảng cáo</p></div>
    <div class="t{n}f9a8"><h2>Chương {n}</h2><p>1</p>
      <p>Đoạn một của chương {n} {title}.</p><p>……</p><p> , Đoạn hai.</p><p>HẾT.</p><p>2</p>
    </div></section></body></html>"""


class Handler(BaseHTTPRequestHandler):
    # cấu hình theo test
    api_items: list | None = None
    api_ok = True
    announced = 3
    missing_pages: set = set()
    slow: dict = {}
    hits: list = []
    require_headers = True

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlsplit(self.path)
        Handler.hits.append(u.path)
        if u.path == "/content/subitems":
            if not Handler.api_ok:
                return self._send(500, "boom")
            if Handler.require_headers and self.headers.get("X-Requested-With") != "XMLHttpRequest":
                return self._send(200, json.dumps({"error": 1, "msg": "Missing ID"}), "application/json")
            assert parse_qs(u.query)["pid"] == [PID]
            return self._send(200, json.dumps({"error": 0, "data": {"e": Handler.api_items}}), "application/json")
        if u.path == f"/truyen/{PID}/ten-truyen":
            return self._send(200, f'<html><head><meta property="og:title" content="Tên Truyện"></head>'
                                   f'<body><span class="subitem-list-num">{Handler.announced} chương</span>'
                                   f'<div class="subitem-item"><div class="name"><a href="/doc/ten-truyen-chuong-1" title="Chương 1">c1</a></div></div>'
                                   f'</body></html>')
        if u.path.startswith("/doc/"):
            n = int(u.path.rsplit("-", 1)[1])
            if n in Handler.missing_pages:
                return self._send(404, "nope")
            time.sleep(Handler.slow.get(n, 0))
            return self._send(200, chapter_html(n))  # cố ý: không khai charset
        self._send(404, "nope")


def item(n, title=None, slug=None):
    return [n, 1, title or f"Chương {n}", f"/doc/{slug or 'ten-truyen'}-chuong-{n}", "2026", 0, 0]


class FakeSiteCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/truyen/{PID}/ten-truyen"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        p = mock.patch("backend.core.http.BACKOFF_BASE", 0.0)  # backoff thật là cố ý; chỉ tắt trong test
        p.start()
        self.addCleanup(p.stop)
        Handler.api_items = [item(3), item(1), item(2)]   # API trả lộn thứ tự
        Handler.api_ok, Handler.announced = True, 3
        Handler.missing_pages, Handler.slow, Handler.hits = set(), {}, []
        s = make_session()
        s.trust_env = False   # bỏ qua proxy của môi trường khi gọi 127.0.0.1
        self.crawler = TocTruyenCrawler(session=s)
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "o.txt"

    def tearDown(self):
        self.tmp.cleanup()


class TestCrawl(FakeSiteCase):
    def test_content_is_clean_and_sorted(self):
        Handler.slow = {1: 0.3, 2: 0.1}     # chương 1 xong SAU chương 3 -> phải sort lại
        r = self.crawler.crawl(self.url, 1, 3, self.out, delay=0)
        self.assertEqual((r.requested, r.saved, r.failed_numbers), (3, 3, []))
        text = self.out.read_text(encoding="utf-8")
        self.assertLess(text.index("chương 1 "), text.index("chương 2 "))
        self.assertLess(text.index("chương 2 "), text.index("chương 3 "))
        self.assertIn("……", text)                       # nhịp im lặng = nội dung thật
        self.assertNotIn("HẾT.", text)                  # dòng kết truyện bị bỏ
        self.assertNotIn("quảng cáo", text)             # chọn div có nhiều <p> nhất
        self.assertNotIn("\n1\n", text)                 # số trang bị bỏ
        self.assertIn("Đoạn hai.", text)
        self.assertNotRegex(text, "Ã|â€|ï»¿")            # không mojibake dù server không khai charset

    def test_range_filter(self):
        r = self.crawler.crawl(self.url, 2, 3, self.out, delay=0)
        self.assertEqual(r.requested, 2)
        text = self.out.read_text(encoding="utf-8")
        self.assertNotIn("chương 1 ", text)

    def test_one_failed_chapter_does_not_kill_job(self):
        Handler.missing_pages = {2}
        events = []
        r = self.crawler.crawl(self.url, 1, 3, self.out, delay=0, on_progress=events.append)
        self.assertEqual(r.failed_numbers, [2])
        self.assertEqual(r.saved, 2)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("Chương 2\n⚠️ Không tải được trang", text)
        self.assertIn("chương 3 ", text)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1].done, 3)

    def test_duplicate_chapter_numbers_do_not_overwrite(self):
        Handler.api_items = [item(1), item(1, "Chương 1: Ngoại truyện", slug="ngoai-truyen"), item(2)]
        r = self.crawler.crawl(self.url, 1, 2, self.out, delay=0)
        self.assertEqual(r.requested, 3)   # cả 2 mục "chương 1" đều được giữ
        self.assertEqual(self.out.read_text(encoding="utf-8").count("Đoạn một của chương 1"), 2)

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        r = self.crawler.crawl(self.url, 1, 3, self.out, delay=0, cancel=cancel)
        self.assertTrue(r.cancelled)
        self.assertEqual(r.saved, 0)

    def test_cancel_midway_keeps_finished_chapters(self):
        Handler.api_items = [item(n) for n in range(1, 7)]
        Handler.slow = {n: 0.05 for n in range(1, 7)}
        cancel = threading.Event()
        r = self.crawler.crawl(self.url, 1, 6, self.out, workers=1, delay=0, cancel=cancel,
                               on_progress=lambda e: cancel.set() if e.done == 2 else None)
        self.assertTrue(r.cancelled)
        self.assertGreaterEqual(r.saved, 2)
        self.assertLess(r.saved, 6)
        self.assertTrue(self.out.exists())


class TestInfoAndFallback(FakeSiteCase):
    def test_info_ok(self):
        info = self.crawler.get_story_info(self.url)
        self.assertEqual((info.title, info.chapters_found, info.max_chapter_number, info.announced_total),
                         ("Tên Truyện", 3, 3, 3))
        self.assertEqual(info.warnings, [])

    def test_info_warns_on_count_mismatch(self):
        Handler.announced = 50
        info = self.crawler.get_story_info(self.url)
        self.assertTrue(any("50" in w for w in info.warnings))

    def test_api_down_falls_back_to_html_with_warning(self):
        Handler.api_ok = False
        info = self.crawler.get_story_info(self.url)
        self.assertEqual(info.chapters_found, 1)   # chỉ chương render sẵn trong trang
        self.assertTrue(any("API" in w for w in info.warnings))


class TestUnits(unittest.TestCase):
    def test_parse_story_url(self):
        self.assertEqual(parse_story_url("https://toctruyen.net/truyen/avlBQwkA/ban-trai/?x=1#y"), ("avlBQwkA", "ban-trai"))

    def test_chapter_number(self):
        self.assertEqual(chapter_number("/doc/x-chuong-12", "Chương 99"), 12)
        self.assertEqual(chapter_number("/doc/x", "Chương 7: abc"), 7)
        self.assertIsNone(chapter_number("/doc/x", "Ngoại truyện"))

    def test_registry_detects_mirrors_not_hardcoded(self):
        self.assertIs(detect("https://toctruyen.net/truyen/a/b"), TocTruyenCrawler)
        self.assertIs(detect("https://m.toctruyen.xyz/truyen/a/b"), TocTruyenCrawler)
        self.assertIsNone(detect("https://example.com/x"))

    def test_br_fallback_and_no_punct_normalization(self):
        soup = parse_html("<div>Dòng một<br>5<br>đ.ạ.m<br>HẾT</div>")
        self.assertEqual(paragraphs_from_block(soup.div), ["Dòng một", "đ.ạ.m"])  # không xoá dấu chấm trong từ

    def test_response_text_no_charset_header_is_utf8(self):
        class R:  # giả Response tối thiểu
            headers = {"Content-Type": "text/html"}
            content = "Tiếng Việt: đ ạ".encode("utf-8")
            url = "x"
            apparent_encoding = "ISO-8859-1"
        self.assertEqual(response_text(R), "Tiếng Việt: đ ạ")


if __name__ == "__main__":
    unittest.main()
