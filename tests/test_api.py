"""Test tầng API (token, job, SSE, huỷ, lịch sử) với site toctruyen giả. Không cần mạng."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

try:
    from tests.test_toctruyen import PID, Handler as FakeSite, item
except ImportError:  # chạy bằng `unittest discover -s tests`
    from test_toctruyen import PID, Handler as FakeSite, item

from backend.core.http import make_session
from backend.jobs import JobManager, sanitize_filename
from backend.server import make_server
from backend.sites.toctruyen import TocTruyenCrawler

TOKEN = "test-token-123"


class LocalToc(TocTruyenCrawler):
    """Bỏ qua proxy của môi trường khi gọi 127.0.0.1."""

    def session(self, workers: int = 4):
        if self._session is None:
            s = make_session(pool_size=workers + 2)
            s.trust_env = False
            self._session = s
        return self._session


def fake_detect(url: str):
    return LocalToc if "127.0.0.1" in url else None


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        data = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


class ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.story = f"http://127.0.0.1:{cls.site.server_address[1]}/truyen/{PID}/ten-truyen"

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()

    def setUp(self):
        p = mock.patch("backend.core.http.BACKOFF_BASE", 0.0)
        p.start()
        self.addCleanup(p.stop)
        FakeSite.api_items = [item(n) for n in range(1, 7)]
        FakeSite.api_ok, FakeSite.announced = True, 6
        FakeSite.missing_pages, FakeSite.slow, FakeSite.hits = set(), {}, []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.start_backend()

    def start_backend(self):
        self.manager = JobManager(self.data, detect=fake_detect)
        self.api = make_server(self.manager, TOKEN)
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.port = self.api.server_address[1]

        def stop():
            self.manager.shutdown()
            self.api.shutdown()
            self.api.server_close()
        self.addCleanup(stop)

    # ---- client nhỏ ----
    def call(self, method, path, body=None, token=TOKEN, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        return resp.status, raw

    def jcall(self, *a, **kw):
        status, raw = self.call(*a, **kw)
        return status, json.loads(raw)

    def make_job(self, **over):
        body = {"url": self.story, "start": 1, "end": 6, "delay": 0, "out_dir": str(Path(self.tmp.name) / "out")}
        body.update(over)
        status, job = self.jcall("POST", "/jobs", body)
        self.assertEqual(status, 201, job)
        return job

    def wait_status(self, job_id, statuses, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            _, job = self.jcall("GET", f"/jobs/{job_id}")
            if job["status"] in statuses:
                return job
            time.sleep(0.05)
        self.fail(f"job {job_id} không đạt {statuses}, đang là {job['status']}")


class TestAuth(ApiCase):
    def test_health_is_open(self):
        status, body = self.jcall("GET", "/health", token=None)
        self.assertEqual((status, body["ok"]), (200, True))

    def test_everything_else_needs_token(self):
        for token in (None, "sai-token", ""):
            status, body = self.jcall("GET", "/sites", token=token)
            self.assertEqual(status, 401)
            self.assertEqual(body["error"]["code"], "unauthorized")
        self.assertEqual(self.call("GET", "/jobs", token=None)[0], 401)
        self.assertEqual(self.call("POST", "/jobs", {"url": self.story}, token=None)[0], 401)

    def test_sites_and_404(self):
        status, body = self.jcall("GET", "/sites")
        self.assertEqual((status, body["sites"][0]["name"]), (200, "toctruyen"))
        self.assertEqual(self.jcall("GET", "/khong-co")[0], 404)


class TestStoryInfo(ApiCase):
    def test_info_ok(self):
        status, info = self.jcall("POST", "/story/info", {"url": self.story})
        self.assertEqual(status, 200)
        self.assertEqual((info["title"], info["chapters_found"], info["announced_total"]), ("Tên Truyện", 6, 6))

    def test_unsupported_and_invalid_url(self):
        s, b = self.jcall("POST", "/story/info", {"url": "https://example.com/x"})
        self.assertEqual((s, b["error"]["code"]), (400, "unsupported_site"))
        s, b = self.jcall("POST", "/story/info", {"url": "<url>"})
        self.assertEqual((s, b["error"]["code"]), (400, "invalid_url"))

    def test_upstream_failure_is_502(self):
        FakeSite.api_items = []
        bad = self.story.replace("ten-truyen", "khong-co")
        s, b = self.jcall("POST", "/story/info", {"url": bad})
        self.assertEqual((s, b["error"]["code"]), (502, "upstream_error"))

    def test_bad_json_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/story/info", body=b"{khong json", headers={"Authorization": f"Bearer {TOKEN}"})
        r = conn.getresponse()
        self.assertEqual((r.status, json.loads(r.read())["error"]["code"]), (400, "invalid_json"))


class TestJobs(ApiCase):
    def test_full_job_with_sse(self):
        FakeSite.slow = {1: 0.2}
        job = self.make_job()
        self.assertIn(job["status"], ("queued", "running"))
        status, raw = self.call("GET", f"/jobs/{job['id']}/events")
        self.assertEqual(status, 200)
        events = parse_sse(raw)
        types = [e["type"] for e in events]
        self.assertEqual(types[-1], "end")
        self.assertEqual(types.count("progress"), 6)
        self.assertEqual(types.count("start"), 1)
        self.assertEqual([e["seq"] for e in events], list(range(len(events))))
        self.assertEqual(events[-1]["status"], "done")
        self.assertEqual(events[-1]["result"]["saved"], 6)

        done = self.wait_status(job["id"], ("done",))
        self.assertEqual((done["done"], done["total"], done["error"]), (6, 6, None))
        text = Path(done["output_file"]).read_text(encoding="utf-8")
        self.assertEqual(text.count("Đoạn một của chương"), 6)
        self.assertLess(text.index("chương 1 "), text.index("chương 6 "))

    def test_sse_resume_after_seq(self):
        job = self.make_job()
        self.wait_status(job["id"], ("done",))
        _, all_raw = self.call("GET", f"/jobs/{job['id']}/events")
        everything = parse_sse(all_raw)
        _, part_raw = self.call("GET", f"/jobs/{job['id']}/events?after=3")
        part = parse_sse(part_raw)
        self.assertEqual([e["seq"] for e in part], [e["seq"] for e in everything if e["seq"] > 3])
        _, hdr_raw = self.call("GET", f"/jobs/{job['id']}/events", headers={"Last-Event-ID": "3"})
        self.assertEqual(parse_sse(hdr_raw), part)

    def test_partial_failure_is_done_with_failed_numbers(self):
        FakeSite.missing_pages = {2, 5}
        job = self.make_job()
        done = self.wait_status(job["id"], ("done",))
        self.assertEqual(done["result"]["failed_numbers"], [2, 5])
        self.assertEqual(done["result"]["saved"], 4)

    def test_all_chapters_fail_marks_failed(self):
        FakeSite.missing_pages = set(range(1, 7))
        job = self.make_job()
        failed = self.wait_status(job["id"], ("failed",))
        self.assertIn("Không tải được chương nào", failed["error"])

    def test_range_outside_story_marks_failed(self):
        job = self.make_job(start=100, end=110)
        failed = self.wait_status(job["id"], ("failed",))
        self.assertIn("100", failed["error"])

    def test_cancel_running_job(self):
        FakeSite.slow = {n: 0.3 for n in range(1, 7)}
        job = self.make_job(workers=1)
        end = time.time() + 5
        while time.time() < end and self.jcall("GET", f"/jobs/{job['id']}")[1]["done"] < 1:
            time.sleep(0.05)
        status, _ = self.jcall("POST", f"/jobs/{job['id']}/cancel")
        self.assertEqual(status, 200)
        final = self.wait_status(job["id"], ("cancelled",))
        self.assertGreaterEqual(final["result"]["saved"], 1)
        self.assertLess(final["result"]["saved"], 6)

    def test_cancel_queued_job_finishes_immediately(self):
        FakeSite.slow = {n: 0.4 for n in range(1, 7)}
        self.manager._pool.shutdown(wait=False)  # không còn ai chạy -> job ở lại 'queued'
        from concurrent.futures import ThreadPoolExecutor
        self.manager._pool = ThreadPoolExecutor(max_workers=1)
        blockers = [self.make_job(workers=1) for _ in range(2)]
        queued = blockers[1]
        s, j = self.jcall("POST", f"/jobs/{queued['id']}/cancel")
        self.assertEqual((s, j["status"]), (200, "cancelled"))
        self.jcall("POST", f"/jobs/{blockers[0]['id']}/cancel")

    def test_validation(self):
        out = str(Path(self.tmp.name) / "o")
        cases = [
            ({"url": self.story, "start": 0, "end": 5}, "invalid_range"),
            ({"url": self.story, "start": 5, "end": 2}, "invalid_range"),
            ({"url": self.story, "start": "1", "end": 2}, "invalid_field"),
            ({"url": self.story, "start": 1}, "invalid_field"),            # thiếu end
            ({"url": self.story, "start": 1, "end": 2, "out_dir": "relative/dir"}, "invalid_out_dir"),
            ({"url": "https://example.com/x", "start": 1, "end": 2}, "unsupported_site"),
            ({"url": self.story, "start": 1, "end": 2, "delay": "abc"}, "invalid_field"),
        ]
        for body, code in cases:
            body.setdefault("out_dir", out)
            s, b = self.jcall("POST", "/jobs", body)
            self.assertEqual((s, b["error"]["code"]), (400, code), body)

    def test_workers_and_delay_are_clamped(self):
        job = self.make_job(workers=999, delay=999)
        self.assertEqual((job["workers"], job["delay"]), (8, 5.0))
        self.manager.cancel(job["id"])

    def test_filename_sanitized_and_never_overwrites(self):
        a = self.make_job(filename="../../evil:name?.txt")
        self.wait_status(a["id"], ("done",))
        self.assertEqual(Path(a["output_file"]).parent, Path(a["out_dir"]))   # không thoát khỏi out_dir
        self.assertNotIn(":", Path(a["output_file"]).name)
        b = self.make_job(filename="evil_name_.txt")
        self.assertNotEqual(a["output_file"], b["output_file"])
        self.assertTrue(b["output_file"].endswith("_2.txt"))

    def test_sanitize_filename_unit(self):
        self.assertEqual(sanitize_filename("a/b\\c.txt"), "c.txt")
        self.assertEqual(sanitize_filename("truyen"), "truyen.txt")
        self.assertEqual(sanitize_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_filename('a<b>c|d.md'), "a_b_c_d.md")

    def test_delete(self):
        FakeSite.slow = {n: 0.3 for n in range(1, 7)}
        job = self.make_job(workers=1)
        s, b = self.jcall("DELETE", f"/jobs/{job['id']}")
        self.assertEqual((s, b["error"]["code"]), (409, "job_active"))
        self.jcall("POST", f"/jobs/{job['id']}/cancel")
        self.wait_status(job["id"], ("cancelled",))
        self.assertEqual(self.jcall("DELETE", f"/jobs/{job['id']}")[0], 200)
        self.assertEqual(self.jcall("GET", f"/jobs/{job['id']}")[0], 404)

    def test_list_newest_first(self):
        a = self.make_job()
        self.wait_status(a["id"], ("done",))
        time.sleep(1.1)  # created_at có độ phân giải 1 giây
        b = self.make_job()
        _, body = self.jcall("GET", "/jobs")
        self.assertEqual([j["id"] for j in body["jobs"][:2]], [b["id"], a["id"]])


class TestHistory(ApiCase):
    def test_history_survives_restart(self):
        job = self.make_job()
        self.wait_status(job["id"], ("done",))
        again = JobManager(self.data, detect=fake_detect)
        try:
            restored = again.get(job["id"]).to_dict()
            self.assertEqual((restored["status"], restored["result"]["saved"]), ("done", 6))
            self.assertEqual(again.get(job["id"]).events[-1]["type"], "end")   # SSE cũ đóng gọn
        finally:
            again.shutdown()

    def test_running_job_becomes_interrupted_after_crash(self):
        rows = [{"id": "abc123", "url": self.story, "site": "toctruyen", "start": 1, "end": 6, "workers": 4,
                 "delay": 0.1, "out_dir": str(self.data), "output_file": str(self.data / "x.txt"),
                 "status": "running", "done": 2, "total": 6}]
        (self.data / "jobs.json").write_text(json.dumps(rows), encoding="utf-8")
        again = JobManager(self.data, detect=fake_detect)
        try:
            j = again.get("abc123")
            self.assertEqual(j.status, "interrupted")
            self.assertTrue(j.error)
        finally:
            again.shutdown()

    def test_corrupt_history_is_kept_not_lost(self):
        (self.data / "jobs.json").write_text("{khong phai json", encoding="utf-8")
        again = JobManager(self.data, detect=fake_detect)
        try:
            self.assertEqual(again.list(), [])
            self.assertTrue((self.data / "jobs.corrupt").exists())
        finally:
            again.shutdown()


class TestProcessLifecycle(unittest.TestCase):
    """Chạy backend như một process thật, đúng cách Flutter sẽ chạy."""

    def spawn(self, *extra):
        import subprocess
        import sys
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = subprocess.Popen([sys.executable, "-m", "backend", "--port", "0", "--data-dir", d, *extra],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=str(Path(__file__).resolve().parent.parent))
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdin.close(), p.stdout.close(), p.stderr.close()))
        ready = json.loads(p.stdout.readline())
        self.assertEqual(ready["event"], "ready")
        return p, ready

    def test_exits_when_stdin_closes(self):
        p, ready = self.spawn("--exit-on-stdin-eof")
        conn = http.client.HTTPConnection("127.0.0.1", ready["port"], timeout=5)
        conn.request("GET", "/health")
        self.assertEqual(conn.getresponse().status, 200)       # đang sống bình thường khi stdin còn mở
        p.stdin.close()                                         # giả lập app Flutter chết
        self.assertEqual(p.wait(timeout=15), 0)

    def test_keeps_running_without_the_flag_even_if_stdin_closes(self):
        p, ready = self.spawn()
        p.stdin.close()
        time.sleep(1.0)
        self.assertIsNone(p.poll())


if __name__ == "__main__":
    unittest.main()
