"""Test xử lý văn bản (backend/core/clean.py). Chạy: python -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.core.clean import (
    AutoCleaner, CleanConfig, CleanReport, _validate_llm_output, clean_file, clean_text,
    fix_obfuscated, is_chapter_marker, is_promo, looks_vietnamese, refine_with_llm, strip_links,
)


def lines(text: str) -> list[str]:
    return [ln for ln in clean_text(text).split("\n") if ln]


class TestLinks(unittest.TestCase):
    def test_removes_all_link_kinds(self):
        for src in ("Xem tại https://toctruyen.net/truyen/abc/x-y nhé bạn.",
                    "Xem tại www.truyenfull.vision/abc nhé bạn.",
                    "Xem tại toctruyen.net nhé bạn.",
                    "Xem tại t.me/kenh_truyen nhé bạn.",
                    "Xem tại admin@gmail.com nhé bạn."):
            out, n = strip_links(src)
            self.assertEqual(n, 1, src)
            self.assertEqual(out, "Xem tại nhé bạn.", src)

    def test_trailing_punctuation_is_kept(self):
        out, _ = strip_links("Link đây (https://a.com/x), xem đi.")
        self.assertEqual(out, "Link đây, xem đi.")

    def test_line_that_is_only_a_link_is_dropped(self):
        self.assertEqual(lines("Nguồn: https://x.com/abc\nAnh bước vào phòng."), ["Anh bước vào phòng."])
        self.assertEqual(lines("https://shopee.vn/abc\nAnh bước vào phòng."), ["Anh bước vào phòng."])

    def test_story_line_with_link_keeps_its_text(self):
        self.assertEqual(lines("Cô nhìn dòng chữ www.abc.com trên màn hình và thấy lạnh sống lưng."),
                         ["Cô nhìn dòng chữ trên màn hình và thấy lạnh sống lưng."])

    def test_markdown_link(self):
        self.assertEqual(lines("Đọc tiếp [tại đây](https://a.com/x) đi rồi anh đi ngủ ngay."),
                         ["Đọc tiếp tại đây đi rồi anh đi ngủ ngay."])

    def test_plain_dotted_text_is_not_a_domain(self):
        out, n = strip_links("Anh ta nói v.v. và 3.5 lần.")
        self.assertEqual(n, 0)


class TestPromos(unittest.TestCase):
    def test_channel_intros_and_credits_removed(self):
        for line in ("Bạn đang xem truyện Ta Sinh Con tại Toc Truyen",
                     "Bạn đang đọc truyện Hoa Nở Muộn trên kênh ABC",
                     "Truyện làm bởi Nhóm Dịch XYZ, cấm đăng lại",
                     "TRUYỆN LÀM... đăng lại",
                     "Nguồn: Nhóm dịch ABC",
                     "Dịch giả: Tiểu Minh",
                     "Cảm ơn các bạn đã đọc truyện",
                     "Hãy theo dõi kênh của chúng tôi để ủng hộ nhóm dịch bộ truyện này nhé",
                     "Đọc sách tâm linh",
                     "Không có dữ liệu",
                     "Đọc truyện tại Truyenfull mới nhất"):
            self.assertTrue(is_promo(line), line)

    def test_story_lines_are_not_removed(self):
        for line in ("Cô ấy theo dõi anh trên facebook suốt mấy tháng trời.",
                     "Anh đang xem truyện tranh trong phòng.",
                     "Hắn dịch lại bức thư cho cô nghe.",
                     "Nguồn nước trong làng đã cạn từ lâu.",
                     "Cô nói: \"Em sẽ đăng lại bài viết đó.\"",
                     "Một dòng tin nhắn hiện lên: ủng hộ anh nhé."):
            self.assertFalse(is_promo(line), line)

    def test_long_paragraph_is_never_treated_as_promo(self):
        long_line = "Bạn đang xem truyện " + "rất dài " * 60
        self.assertFalse(is_promo(long_line))

    def test_extra_patterns(self):
        self.assertTrue(is_promo("Mời ghé nhà ABC", extra_patterns=(r"nhà ABC",)))


class TestChapterMarkers(unittest.TestCase):
    def test_markers_removed(self):
        for line in ("Chương 12", "Chương 12: Gặp gỡ", "CHƯƠNG MỘT", "Chapter 5 - The Start", "[Chương 3]",
                     "Hồi 4", "第12章 初见", "Chương 7.", "Chương 21 - Hết", "Hết chương 5", "(Hết)", "Hết.",
                     "Còn tiếp", "5", "— 12 —", "Chương trước Chương sau", "Mục lục"):
            self.assertTrue(is_chapter_marker(line), line)

    def test_story_lines_kept(self):
        for line in ("Chương trình học rất nặng.", "Chương 5 là chương cuối cùng của cuốn sách.",
                     "Phần lớn mọi người đều không biết.", "Năm ngoái anh ấy đã đi.",
                     "Tập thể dục mỗi sáng là thói quen của cô."):
            self.assertFalse(is_chapter_marker(line), line)

    def test_end_to_end_no_markers_left(self):
        text = "Chương 1: Mở đầu\nTrời đã tối.\n\nChương 2\nAnh đi ngủ.\n\n3\nHết."
        self.assertEqual(lines(text), ["Trời đã tối.", "Anh đi ngủ."])


class TestObfuscation(unittest.TestCase):
    def test_dotted_words_are_joined(self):
        cases = {"đ.á.nh": "đánh", "ch.ết": "chết", "gi.ết": "giết", "chế độ.t": "chế đột", "gi.ết": "giết", "qu.ỷ": "quỷ",
                 "m.á.u": "máu", "g.i.ết": "giết", "t.anh": "tanh", "đ.ịt.m.ẹ": "địt mẹ",
                 "địt.mẹ.mày": "địt mẹ mày", "đánh.nhau": "đánh nhau", "ng.ực": "ngực",
                 "địt.mẹ": "địt mẹ", "xong.Rồi": "xong. Rồi", "đ*ịt": "địt"}
        for src, want in cases.items():
            self.assertEqual(fix_obfuscated(src), want, src)

    def test_brackets_next_to_quotes_leave_no_stray_space(self):
        self.assertEqual(lines('Tin nhắn: "Gặp ở tầng 2 (tòa nhà ABC)". Rồi cô đi.'),
                         ['Tin nhắn: "Gặp ở tầng 2 tòa nhà ABC". Rồi cô đi.'])

    def test_trailing_period_and_sentence_context(self):
        self.assertEqual(fix_obfuscated("Hắn đ.á.nh nó, rồi ch.ết."), "Hắn đánh nó, rồi chết.")

    def test_abbreviations_numbers_and_ellipsis_untouched(self):
        for s in ("v.v", "TP.HCM", "3.5 triệu", "v1.2", "Anh ta... đi.", "Ông nói. Rồi đi."):
            self.assertEqual(fix_obfuscated(s), s, s)

    def test_counts_reported(self):
        rep = CleanReport()
        fix_obfuscated("đ.á.nh và ch.ết", rep)
        self.assertEqual(rep.obfuscated_fixed, 2)


class TestCharacters(unittest.TestCase):
    def test_tilde_becomes_period(self):
        self.assertEqual(lines("Anh đi đây ơi~"), ["Anh đi đây ơi."])
        self.assertEqual(lines("Anh đi đây ơi~~~"), ["Anh đi đây ơi."])
        self.assertEqual(lines("Anh đi rồi.~"), ["Anh đi rồi."])
        self.assertNotIn("~", clean_text("a~b~"))

    def test_emoji_stickers_and_private_use_removed(self):
        src = "Anh cười 😀 rồi đi ❤️ về \ue000 nhà ⚠️ xa 👍🏽."
        out = clean_text(src)
        self.assertEqual(out.strip(), "Anh cười rồi đi về nhà xa.")

    def test_ascii_emotions_removed_but_real_text_kept(self):
        self.assertEqual(lines("Vui quá :)) haha =)) ^_^ T_T :v"), ["Vui quá haha."])
        for keep in ("Lúc 10:30 anh đến.", "Tỉ số 1:3 cho đội khách.", "Nhiệt độ 30°C hôm nay."):
            self.assertEqual(lines(keep), [keep], keep)

    def test_colon_before_capital_p_is_not_an_emoticon(self):
        self.assertEqual(lines("Ghi chú:Phong đến."), ["Ghi chú: Phong đến."])   # "Phong" không bị ăn mất chữ P

    def test_kaomoji(self):
        self.assertEqual(lines("Vui quá (^_^) hôm nay."), ["Vui quá hôm nay."])

    def test_separator_lines_removed_but_ellipsis_pause_kept(self):
        self.assertEqual(lines("Một.\n=====\n-----\n***\n……\nHai."), ["Một.", "...", "Hai."])


class TestTTS(unittest.TestCase):
    def test_symbols_and_brackets(self):
        self.assertEqual(lines("Anh (khẽ cười) nói *nhỏ* #1 & em."), ["Anh khẽ cười nói nhỏ 1 và em."])

    def test_dashes_and_dialogue_markers(self):
        self.assertEqual(lines("- Anh đi đâu?\n— Anh đi làm."), ["Anh đi đâu?", "Anh đi làm."])
        self.assertEqual(lines("Anh — người đó — đã đi."), ["Anh, người đó, đã đi."])

    def test_repeated_punctuation_and_spacing(self):
        self.assertEqual(lines("Sao thế ??? Không biết !!! Ừ ,đi."), ["Sao thế? Không biết! Ừ, đi."])
        self.assertEqual(lines("Đợi chút....Rồi anh đi"), ["Đợi chút... Rồi anh đi."])

    def test_ellipsis_char_and_curly_quotes(self):
        self.assertEqual(lines("Anh… đi. “Về nhé”"), ['Anh... đi. "Về nhé"'])

    def test_every_line_ends_with_punctuation(self):
        for line in lines("Anh đi\nEm ở lại,\nCô nói:"):
            self.assertTrue(line.endswith((".", "!", "?", '"')), line)

    def test_numbers_untouched(self):
        self.assertEqual(lines("Giá 3,5 triệu lúc 10:30 ngày 1.000.000."), ["Giá 3,5 triệu lúc 10:30 ngày 1.000.000."])

    def test_long_paragraph_is_split_on_sentences(self):
        para = " ".join(f"Đây là câu số {i} của đoạn văn rất dài." for i in range(60))
        out = lines(para)
        self.assertGreater(len(out), 3)
        self.assertTrue(all(len(x) <= 500 for x in out))
        self.assertEqual(" ".join(out), para)                    # không mất chữ nào

    def test_giant_sentence_without_punctuation_is_hard_split(self):
        out = lines("từ " * 400)
        self.assertTrue(all(len(x) <= 500 for x in out))
        self.assertEqual(" ".join(out).replace(".", "").split(), ["từ"] * 400)

    def test_paragraph_separator_option(self):
        out = clean_text("Một.\nHai.", CleanConfig(paragraph_sep="\n\n"))
        self.assertEqual(out, "Một.\n\nHai.\n")


class TestWholePipeline(unittest.TestCase):
    RAW = (
        "Bạn đang xem truyện Hoa Nở Muộn tại https://toctruyen.net/truyen/a/b\n\n"
        "Chương 1: Gặp gỡ\n\n"
        "Trời tối rồi~ Anh đ.á.nh cô ấy 😀 :))\n\n"
        "……\n\n"
        "Truyện làm bởi nhóm ABC, cấm đăng lại\n\n"
        "1\n\n"
        "Chương 2\n"
        "- Em ch.ết rồi sao?!!\n"
        "=========\n"
        "Hết chương 2\n"
    )

    def test_result(self):
        self.assertEqual(lines(self.RAW), ["Trời tối rồi. Anh đánh cô ấy.", "...", "Em chết rồi sao?!"])

    def test_idempotent(self):
        once = clean_text(self.RAW)
        self.assertEqual(clean_text(once), once)

    def test_no_meta_commentary_added(self):
        out = clean_text(self.RAW).lower()
        for bad in ("tuyệt", "sau đây", "dưới đây", "đã chỉnh sửa"):
            self.assertNotIn(bad, out)

    def test_empty_and_garbage_input(self):
        self.assertEqual(clean_text(""), "")
        self.assertEqual(clean_text("\n\n   \n"), "")
        self.assertEqual(clean_text("😀😀 ~~ ===\n1\n"), "")

    def test_crawler_placeholder_for_failed_chapter_removed(self):
        self.assertEqual(lines("Chương 5\n⚠️ Không có dữ liệu\nTrời mưa."), ["Trời mưa."])

    def test_switches_off(self):
        cfg = CleanConfig(remove_links=False, remove_promos=False, remove_chapter_markers=False,
                          fix_obfuscated_words=False, tilde_to_period=False, remove_emoji=False, tts_normalize=False)
        out = clean_text("Chương 1\nđ.á.nh ơi~ 😀", cfg)
        self.assertEqual(out, "Chương 1\nđ.á.nh ơi~ 😀\n")

    def test_report_counts(self):
        rep = CleanReport()
        clean_text(self.RAW, report=rep)
        self.assertEqual((rep.links_removed, rep.obfuscated_fixed), (1, 2))
        self.assertGreaterEqual(len(rep.removed_markers), 4)
        self.assertEqual(len(rep.removed_promos), 2)
        self.assertGreaterEqual(rep.emoji_removed, 2)


class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_clean_file_rewrites_and_backs_up(self):
        p = self.dir / "a.txt"
        raw = "Chương 1\nTrời tối rồi~ 😀\n"
        p.write_text(raw, encoding="utf-8")
        rep = clean_file(p)
        self.assertEqual(p.read_text(encoding="utf-8"), "Trời tối rồi.\n")
        self.assertEqual((self.dir / "raw_backup" / "a.txt").read_text(encoding="utf-8"), raw)
        self.assertFalse((self.dir / "a.txt.tmp").exists())
        self.assertGreater(rep.chars_before, rep.chars_after)

    def test_rerun_keeps_original_backup(self):
        p = self.dir / "a.txt"
        raw = "Chương 1\nTrời tối rồi~\n"
        p.write_text(raw, encoding="utf-8")
        clean_file(p)
        clean_file(p)                       # đã sạch: không được đè bản gốc bằng bản đã xử lý
        self.assertEqual((self.dir / "raw_backup" / "a.txt").read_text(encoding="utf-8"), raw)

    def test_keep_raw_false(self):
        p = self.dir / "a.txt"
        p.write_text("Trời tối rồi~\n", encoding="utf-8")
        clean_file(p, keep_raw=False)
        self.assertFalse((self.dir / "raw_backup").exists())

    def test_utf8_no_bom_and_lf(self):
        p = self.dir / "a.txt"
        p.write_text("Một.\r\nHai.\r\n", encoding="utf-8")
        clean_file(p)
        data = p.read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", data)

    def test_crlf_file_that_is_otherwise_clean_is_still_rewritten_with_lf(self):
        p = self.dir / "a.txt"
        p.write_bytes("Một.\r\nHai.\r\n".encode("utf-8"))
        clean_file(p)
        self.assertEqual(p.read_bytes(), "Một.\nHai.\n".encode("utf-8"))

    def test_bom_is_removed(self):
        p = self.dir / "a.txt"
        p.write_bytes(b"\xef\xbb\xbf" + "Một.\n".encode("utf-8"))
        clean_file(p)
        self.assertEqual(p.read_bytes(), "Một.\n".encode("utf-8"))

    def test_looks_vietnamese(self):
        self.assertTrue(looks_vietnamese("Trời tối rồi, anh đi ngủ đây. " * 10))
        self.assertFalse(looks_vietnamese("La noche es oscura, vamos a dormir ahora mismo. " * 10))
        self.assertFalse(looks_vietnamese("short"))


class FakeEvents:
    def __init__(self):
        self.handlers = {}

    def register(self, name, fn):
        self.handlers.setdefault(name, []).append(fn)

    def unregister(self, name, fn):
        self.handlers[name].remove(fn)

    def fire(self, name):
        for fn in list(self.handlers.get(name, [])):
            fn()


class FakeIPython:
    def __init__(self):
        self.events = FakeEvents()


VI = "Chương 1\nTrời tối rồi~ Anh đ.á.nh cô ấy 😀 và đi về nhà ngủ một giấc thật dài.\n" * 3
ES = "La noche es oscura, vamos a dormir ahora mismo y mañana será otro día. " * 5


class TestAutoCleaner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ip = FakeIPython()
        self.auto = AutoCleaner(self.dir, ipython=self.ip).install()
        self.addCleanup(self.auto.uninstall)

    def run_cell(self, writer):
        self.ip.events.fire("pre_run_cell")
        writer()
        self.ip.events.fire("post_run_cell")

    def test_install_alone_touches_nothing(self):
        (self.dir / "old.txt").write_text(VI, encoding="utf-8")
        self.ip.events.fire("post_run_cell")     # cell cài đặt vừa chạy xong
        self.assertEqual((self.dir / "old.txt").read_text(encoding="utf-8"), VI)

    def test_file_written_during_cell_is_cleaned(self):
        self.run_cell(lambda: (self.dir / "truyen.txt").write_text(VI, encoding="utf-8"))
        out = (self.dir / "truyen.txt").read_text(encoding="utf-8")
        self.assertNotIn("Chương", out)
        self.assertNotIn("😀", out)
        self.assertIn("Anh đánh cô ấy", out)
        self.assertTrue((self.dir / "raw_backup" / "truyen.txt").exists())

    def test_old_files_and_non_vietnamese_are_left_alone(self):
        old = self.dir / "old.txt"
        old.write_text(VI, encoding="utf-8")
        past = time.time() - 3600
        os.utime(old, (past, past))
        self.run_cell(lambda: (self.dir / "es.txt").write_text(ES, encoding="utf-8"))
        self.assertEqual(old.read_text(encoding="utf-8"), VI)
        self.assertEqual((self.dir / "es.txt").read_text(encoding="utf-8"), ES)

    def test_same_file_not_reprocessed_in_next_cell(self):
        self.run_cell(lambda: (self.dir / "truyen.txt").write_text(VI, encoding="utf-8"))
        backup = self.dir / "raw_backup" / "truyen.txt"
        first = backup.stat().st_mtime_ns
        self.run_cell(lambda: None)
        self.assertEqual(backup.stat().st_mtime_ns, first)

    def test_disabled_and_uninstall(self):
        self.auto.enabled = False
        self.run_cell(lambda: (self.dir / "a.txt").write_text(VI, encoding="utf-8"))
        self.assertEqual((self.dir / "a.txt").read_text(encoding="utf-8"), VI)
        self.auto.uninstall()
        self.assertEqual(self.ip.events.handlers["post_run_cell"], [])

    def test_reinstall_replaces_previous_hook(self):
        AutoCleaner(self.dir, ipython=self.ip).install()
        self.assertEqual(len(self.ip.events.handlers["post_run_cell"]), 1)

    def test_hook_error_does_not_break_cell(self):
        self.auto.directory = self.dir / "khong-ton-tai-\0"
        self.run_cell(lambda: None)    # không được ném exception


class TestLLMStep(unittest.TestCase):
    def test_wiring_and_chunking(self):
        text = "\n".join(f"Tôi đi đường {i} cùng anh ấy ngày hôm nay." for i in range(200)) + "\n"
        calls = []

        def fake(chunk: str) -> str:
            calls.append(len(chunk))
            return chunk.replace("Tôi", "Ta")

        rep = CleanReport()
        out = refine_with_llm(text, fake, chunk_chars=1000, report=rep)
        self.assertGreater(len(calls), 3)
        self.assertTrue(all(c <= 1000 for c in calls))
        self.assertEqual(out.count("Ta đi đường"), 200)
        self.assertEqual((rep.llm_chunks, rep.llm_fallbacks), (len(calls), 0))

    def test_meta_commentary_is_stripped(self):
        src = "Tôi đi học.\nTôi về nhà."
        out = _validate_llm_output(src, "Tuyệt! Sau đây là văn bản đã chỉnh sửa:\nTa đi học.\nTa về nhà.")
        self.assertEqual(out, "Ta đi học.\nTa về nhà.")
        out = _validate_llm_output(src, "```\nTa đi học.\nTa về nhà.\n```")
        self.assertEqual(out, "Ta đi học.\nTa về nhà.")

    def test_story_line_that_looks_like_meta_is_kept(self):
        src = "Được rồi, anh đi.\nTôi về nhà."
        out = _validate_llm_output(src, "Được rồi, anh đi.\nTa về nhà.")
        self.assertEqual(out, "Được rồi, anh đi.\nTa về nhà.")

    def test_refusal_and_bad_length_fall_back_to_original(self):
        src = "Tôi đi học. " * 20
        self.assertIsNone(_validate_llm_output(src, "Xin lỗi, tôi không thể giúp với yêu cầu này."))
        self.assertIsNone(_validate_llm_output(src, "Ta đi học."))               # bị cắt ngắn
        self.assertIsNone(_validate_llm_output(src, "Ta đi học. " * 60))          # bị phình ra
        self.assertIsNone(_validate_llm_output(src, ""))

    def test_fallback_keeps_original_chunk_and_counts_it(self):
        text = "Tôi đi học hôm nay cùng bạn.\n" * 5
        rep = CleanReport()
        out = refine_with_llm(text, lambda c: "Xin lỗi, tôi không thể giúp việc này.", report=rep)
        self.assertEqual(out, text)
        self.assertEqual((rep.llm_chunks, rep.llm_fallbacks), (1, 1))

    def test_api_exception_does_not_lose_text(self):
        def boom(_):
            raise RuntimeError("HTTP 529")
        text = "Tôi đi học hôm nay cùng bạn.\n" * 5
        self.assertEqual(refine_with_llm(text, boom), text)

    def test_clean_file_with_llm_and_final_normalisation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("Chương 1\nTôi đi học hôm nay cùng bạn thân của tôi.\n", encoding="utf-8")
            clean_file(p, llm=lambda c: "Dưới đây là bản chỉnh sửa:\n" + c.replace("Tôi", "Ta").replace("tôi", "ta"))
            self.assertEqual(p.read_text(encoding="utf-8"), "Ta đi học hôm nay cùng bạn thân của ta.\n")


if __name__ == "__main__":
    unittest.main()
