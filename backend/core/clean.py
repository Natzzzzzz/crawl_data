"""Làm sạch văn bản truyện sau khi cào và chuẩn hoá cho đọc TTS.

Chỉ dùng thư viện chuẩn (bước LLM tuỳ chọn cần `requests`). Module độc lập: có thể dán nguyên
vào một cell notebook (không import tương đối).

Quy tắc xử lý (theo thứ tự):
  1. Chuẩn hoá Unicode, bỏ ký tự vô hình.
  2. Bỏ emoji / nhãn dán / ký tự private-use / emoticon (:)) =)) ^_^ T_T ...).
  3. Bỏ dòng phân cách (=====, -----). Đổi "~" thành ".".
  4. Từng dòng: bỏ link -> sửa chữ bị che (đ.á.nh -> đánh) -> bỏ dấu hiệu chương -> bỏ dòng quảng cáo/credit.
  5. Chuẩn hoá cho TTS: bỏ ký hiệu đọc thành tiếng, gộp dấu câu lặp, mỗi đoạn 1 dòng, đoạn dài tách theo câu.
  6. (Tuỳ chọn) Chỉnh xưng hô theo bối cảnh bằng LLM: xem `refine_with_llm`.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ----------------------------------------------------------------------------- cấu hình / báo cáo


@dataclass
class CleanConfig:
    remove_links: bool = True
    remove_promos: bool = True
    remove_chapter_markers: bool = True
    fix_obfuscated_words: bool = True      # đ.á.nh -> đánh
    tilde_to_period: bool = True           # ~ -> .
    remove_emoji: bool = True
    tts_normalize: bool = True
    max_line_chars: int = 500              # tách đoạn dài theo câu; 0 = không tách
    paragraph_sep: str = "\n"              # "\n" = mỗi đoạn một dòng; "\n\n" = cách một dòng trống
    extra_promo_patterns: tuple = ()       # regex bổ sung cho dòng quảng cáo riêng của site


@dataclass
class CleanReport:
    removed_promos: list = field(default_factory=list)
    removed_markers: list = field(default_factory=list)
    links_removed: int = 0
    obfuscated_fixed: int = 0
    emoji_removed: int = 0
    chars_before: int = 0
    chars_after: int = 0
    lines_after: int = 0
    llm_chunks: int = 0
    llm_fallbacks: int = 0

    def summary(self) -> str:
        parts = [f"{self.chars_before:,} → {self.chars_after:,} ký tự, {self.lines_after:,} dòng"]
        if self.removed_promos:
            parts.append(f"{len(self.removed_promos)} dòng quảng cáo/credit")
        if self.removed_markers:
            parts.append(f"{len(self.removed_markers)} dòng dấu hiệu chương")
        if self.links_removed:
            parts.append(f"{self.links_removed} link")
        if self.obfuscated_fixed:
            parts.append(f"{self.obfuscated_fixed} từ bị che chữ")
        if self.emoji_removed:
            parts.append(f"{self.emoji_removed} emoji/nhãn dán")
        if self.llm_chunks:
            parts.append(f"LLM {self.llm_chunks - self.llm_fallbacks}/{self.llm_chunks} đoạn")
        return " | ".join(parts)


# ----------------------------------------------------------------------------- bước 1-3: ký tự

_KEEP_SYMBOLS = {"°"}   # "30°C" là nội dung thật


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)          # …→..., ，→, NBSP→space, ghép dấu tiếng Việt
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u2028\u2029\x0b\x0c\x85]", "\n", text)
    text = text.replace("\t", " ")
    return "".join(ch for ch in text
                   if unicodedata.category(ch) != "Cf" and (ch >= " " or ch == "\n")
                   and not (0xFE00 <= ord(ch) <= 0xFE0F or 0xE0100 <= ord(ch) <= 0xE01EF or ord(ch) == 0x20E3))


def _drop_char(ch: str) -> bool:
    if ch < "\u0080" or ch in _KEEP_SYMBOLS:
        return False
    cat = unicodedata.category(ch)
    # So: emoji, dingbat, nhãn dán | Co: private-use (nhãn dán web) | Cs/Cn: lỗi/chưa gán
    # Sk > U+02FF: bộ chỉnh màu da của emoji
    return cat in ("So", "Co", "Cs", "Cn") or (cat == "Sk" and ord(ch) > 0x2FF)


_EMOTICON_RE = re.compile(
    r"(?<!\S)(?:"
    r"[:;=][\-o']?[)(DPpOo3vV/|$@*\\]+"        # :) :(( :D =)) :v :3
    r"|\^[_.\-]?\^"                            # ^^  ^_^
    r"|[TQ][_.\-][TQ]"                         # T_T  Q_Q
    r"|[\-=~][_.][\-=~]|>[_.]<|[oO][_.][oO]"   # -_-  >_<  o_o
    r"|<3+|</3"
    r")(?=\s|$|[.,!?])"
)
_KAOMOJI_RE = re.compile(r"\(\s*[^\w\s()]{1,8}\s*\)")      # (^^)  (>_<)  sau khi đã bỏ ký hiệu lạ


def _strip_emoji(text: str, rep: CleanReport) -> str:
    kept = []
    for ch in text:
        if _drop_char(ch):
            rep.emoji_removed += 1
        else:
            kept.append(ch)
    text = "".join(kept)
    text, n1 = _EMOTICON_RE.subn("", text)
    text, n2 = _KAOMOJI_RE.subn("", text)
    rep.emoji_removed += n1 + n2
    return text


_SEPARATOR_LINE_RE = re.compile(r"(?m)^[ \t]*[=\-_*#~•·+<>—–]{3,}[ \t]*$")


def _fix_tilde(text: str) -> str:
    text = re.sub(r"(?<=[.!?,;:])~+", "", text)    # "xong.~" -> "xong."
    return re.sub(r"~+", ".", text)               # "ơi~" -> "ơi."


# ----------------------------------------------------------------------------- bước 4: từng dòng

_TLDS = "com|net|org|vn|me|mobi|vision|info|xyz|top|tv|io|co|cc|app|site|online|club|pro|biz|us|fm|ly|gl"
_URL_RE = re.compile(r"(?:https?://|ftp://|www\.)\S+?(?=[.,;:!?)\]\"'»”]*(?:\s|$))", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DOMAIN_RE = re.compile(
    rf"(?<![\w@.\-])(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+(?:{_TLDS})\b(?:[/?#]\S*)?", re.I)
_EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]|<\s*>")


def strip_links(line: str) -> tuple[str, int]:
    total = 0
    for rx in (_URL_RE, _EMAIL_RE, _DOMAIN_RE):
        line, n = rx.subn("", line)
        total += n
    if total:
        line = _EMPTY_BRACKETS_RE.sub("", line)
        line = re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", line)).strip()
    return line, total


# --- chữ bị che: đ.á.nh -> đánh
_L = r"[^\W\d_]"
_OBF_RE = re.compile(rf"(?<![\w.*·]){_L}+(?:[.*_·]{_L}+)+(?!\w)")
_ABBREVIATIONS = {"v.v", "tp.hcm", "a.m", "p.m", "u.s", "u.s.a", "ph.d", "e.g", "i.e", "t.p"}


def _is_vowel(ch: str) -> bool:
    base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c)).lower()
    return base[:1] in ("a", "e", "i", "o", "u", "y")


def _join_fragments(frags: list[str]) -> str:
    """Nối các mảnh của một chữ bị che. Ghép cả chuỗi rồi đếm cụm nguyên âm:
    một cụm = một âm tiết ("m.á.u" -> "máu", "g.i.ết" -> "giết", "đ.á.nh" -> "đánh");
    chỉ chèn dấu cách ở chỗ chấm nằm giữa hai cụm ("địt.mẹ", "đ.ịt.m.ẹ" -> "địt mẹ")."""
    joined = "".join(frags)
    groups, i = [], 0
    while i < len(joined):
        if _is_vowel(joined[i]):
            j = i
            while j < len(joined) and _is_vowel(joined[j]):
                j += 1
            groups.append((i, j))
            i = j
        else:
            i += 1
    bounds, pos = [], 0
    for f in frags[:-1]:
        pos += len(f)
        bounds.append(pos)
    spaces = set()
    for (_, end1), (start2, _) in zip(groups, groups[1:]):
        cands = [b for b in bounds if end1 <= b <= start2]
        if cands:
            spaces.add(cands[0])
    return "".join((" " if idx in spaces else "") + ch for idx, ch in enumerate(joined))


def _fix_chain(m: re.Match) -> str:
    token = m.group(0)
    if token.lower() in _ABBREVIATIONS:
        return token
    parts = re.split(r"([.*_·])", token)       # [mảnh, dấu, mảnh, dấu, mảnh...]
    # "xong.Rồi": chấm + chữ hoa = thiếu dấu cách sau câu, không phải chữ bị che -> cắt chuỗi tại đó
    pieces, current = [], [parts[0]]
    for i in range(1, len(parts), 2):
        sep, a, b = parts[i], parts[i - 1], parts[i + 1]
        if sep == "." and len(a) >= 2 and b[:1].isupper() and not a.isupper():
            pieces.append(current)
            current = [b]
        else:
            current.append(b)
    pieces.append(current)
    return ". ".join(_join_fragments(p) for p in pieces)


def fix_obfuscated(line: str, rep: CleanReport | None = None) -> str:
    def repl(m: re.Match) -> str:
        new = _fix_chain(m)
        if rep is not None and new != m.group(0):
            rep.obfuscated_fixed += 1
        return new
    return _OBF_RE.sub(repl, line)


# --- dấu hiệu chương
_MARKER_WORD = r"(?:chương|chuong|chapter|chap|hồi|tập|phần|quyển|volume|vol)"
_NUMW = r"(?:một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười|mươi|trăm|nghìn|linh|lẻ)"
_MARKER_RE = re.compile(
    rf"^[\W_]*{_MARKER_WORD}\s*(?:số\s*)?"
    rf"(?:\d{{1,5}}(?!\d)|[ivxlc]{{1,6}}(?![^\W\d_])|(?:{_NUMW}(?![^\W\d_])\s*)+)"
    rf"(?:\s*[:：.\-–—)\]】》」』>]+\s*.{{0,100}})?[\W_]*$", re.I)
_CJK_MARKER_RE = re.compile(r"^\W*第[\d一二三四五六七八九十百千零〇两]{1,6}[章回节節集卷部].{0,60}$")
_END_MARKER_RE = re.compile(
    r"^[\W_]*(?:hết(?:\s+(?:chương|phần|tập|truyện))?(?:\s*\d+)?|còn tiếp|chưa hết|to be continued|tbc|"
    r"the end|hoàn(?:\s+thành)?|full|end)[\W_]*$", re.I)
_NAV_RE = re.compile(
    r"^(?:[\W_]*(?:chương\s+(?:trước|sau|tiếp(?:\s+theo)?)|mục\s+lục|danh\s+sách\s+chương|báo\s+lỗi|bình\s+luận|trang\s+chủ)[\W_]*)+$",
    re.I)
_NUMBER_ONLY_RE = re.compile(r"^[\W_]*\d{1,4}[\W_]*$")


def is_chapter_marker(line: str) -> bool:
    return len(line) <= 140 and bool(
        _MARKER_RE.match(line) or _CJK_MARKER_RE.match(line) or _END_MARKER_RE.match(line)
        or _NAV_RE.match(line) or _NUMBER_ONLY_RE.match(line))


# --- quảng cáo / credit (chỉ xoá dòng NGẮN để tránh xoá nhầm lời truyện)
_PROMO_PATTERNS = [
    r"^[\W_]*(?:(?:bạn|các bạn|quý (?:vị|độc giả)|độc giả)\s+)?(?:đang|vừa)\s+(?:xem|đọc|nghe|theo dõi)\s+(?:truyện|bộ truyện|tại|trên|kênh|video)",
    r"(?:đọc|xem|nghe)\s+truyện\s+(?:tại|trên|full|mới nhất|hay nhất|miễn phí|online)",
    r"^[\W_]*(?:nguồn|source|sưu tầm|dịch giả|người dịch|converter|convert|editor|edit|biên tập|beta|cv)\s*(?:bởi|by)?\s*:",
    r"(?:truyện|bản dịch|bản convert|nội dung)\s+(?:này\s+)?(?:được\s+)?(?:làm|dịch|convert|chuyển ngữ|biên tập|đăng|đăng tải|sưu tầm|thực hiện|cung cấp)\s+(?:bởi|bằng|tại|trên|lại|by)\b",
    r"^[\W_]*truyện\s+(?:làm|dịch|convert|đăng|sưu tầm)\b",
    r"(?:truyện|bản dịch|chương).{0,60}đăng\s+lại|đăng\s+lại.{0,40}(?:truyện|bản dịch|chương)",
    r"\b(?:repost|re-?up|reup)\b",
    r"(?:cấm|không được|vui lòng không|xin đừng)\s+(?:copy|sao chép|re-?up|reup|đăng lại|mang đi)",
    r"cảm ơn\s+(?:các\s+)?(?:bạn|quý|độc giả|mọi người|anh chị)[^.!?]{0,40}(?:đã|đang)\s+(?:đọc|xem|nghe|theo dõi|ủng hộ)",
    r"(?:hãy|xin|vui lòng|nhớ|đừng quên)[^.!?]{0,30}(?:like|share|subscribe|đăng ký|theo dõi|bình luận|comment|ủng hộ|donate|vote|đánh giá|chia sẻ)[^.!?]{0,40}(?:kênh|truyện|channel|page|fanpage|website|bộ truyện|video)",
    r"đọc sách tâm linh",
    r"^[\W_]*(?:không có dữ liệu|không có nội dung|không lấy được nội dung|không tải được trang)[\W_]*$",  # chương lỗi của crawler
]
_PROMO_RE = re.compile("|".join(f"(?:{p})" for p in _PROMO_PATTERNS), re.I)
_PLATFORM_RE = re.compile(r"\b(?:fanpage|facebook|fb|youtube|kênh|channel|group|nhóm|zalo|telegram|tiktok|shopee|lazada|website|trang web|web|app|ứng dụng|admin)\b", re.I)
_CTA_RE = re.compile(r"(?:theo dõi|đăng ký|subscribe|ghé|truy cập|tham gia|join|ủng hộ|donate|liên hệ|inbox|nhắn tin|link|tải|cài đặt|bình luận|đọc truyện|xem truyện)", re.I)
_READER_RE = re.compile(r"\b(?:bạn|các bạn|quý|độc giả|mọi người|anh chị|hãy|mời|xin|vui lòng|nhớ)\b", re.I)
_PROMO_MAX_LEN = 250


@functools.lru_cache(maxsize=16)
def _compile_extra(patterns: tuple) -> re.Pattern | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I) if patterns else None


def is_promo(line: str, extra_patterns: tuple = ()) -> bool:
    if len(line) > _PROMO_MAX_LEN:
        return False
    if _PROMO_RE.search(line):
        return True
    extra = _compile_extra(tuple(extra_patterns))
    if extra is not None and extra.search(line):
        return True
    # "Hãy theo dõi kênh ..." = nền tảng + lời kêu gọi + gọi người đọc. Thiếu 1 trong 3 thì KHÔNG xoá
    # (vd "Cô ấy theo dõi anh trên facebook" là lời truyện).
    return bool(_PLATFORM_RE.search(line) and _CTA_RE.search(line) and _READER_RE.search(line))


# ----------------------------------------------------------------------------- bước 5: TTS

_QUOTE_MAP = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "「": '"', "」": '"', "『": '"', "』": '"', "《": '"', "》": '"', "〈": '"', "〉": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
})
_BRACKETS_RE = re.compile(r"[()\[\]{}<>【】]")
_READ_ALOUD_RE = re.compile(r"[*#_^|\\`@•·●■◆▪→←↑↓⇒⇐]")
_END_OK = (".", "!", "?", '"', "'", "…")


def _tts_paragraph(line: str, max_len: int) -> list[str]:
    s = line.translate(_QUOTE_MAP)
    s = re.sub(r"^[\-–—•·*]+\s*", "", s)                  # gạch đầu dòng lời thoại: TTS đọc thành "gạch"
    s = s.replace("—", ", ").replace("–", ", ")
    s = _BRACKETS_RE.sub(" ", s)
    s = _READ_ALOUD_RE.sub(" ", s)
    s = re.sub(r"(?<=\D)/(?=\D)", " ", s)
    s = s.replace("&", " và ")
    s = re.sub(r"\.{3,}", "...", s)
    s = re.sub(r"(?<!\.)\.\.(?!\.)", ".", s)
    s = re.sub(r"!{2,}", "!", s)
    s = re.sub(r"\?{2,}", "?", s)
    s = re.sub(r"[!?]{3,}", "?!", s)
    s = re.sub(r",{2,}", ",", s)
    s = re.sub(r"(?:\s*,)+\s*(?=[.!?])", "", s)           # ", ." sinh ra từ gạch ngang
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r'\s+"(?=[.,;:!?]|$)', '"', s)             # 'ABC ".' (sau khi bỏ ngoặc đơn) -> 'ABC".'
    s = re.sub(r"([,;:!?])(?=[^\s\d,.;:!?\"'\)])", r"\1 ", s)   # thiếu dấu cách sau dấu câu (không đụng 3,5 và 10:30)
    s = re.sub(r"\.{3}(?=[^\W\d_])", "... ", s)
    s = re.sub(r"([^\W\d_]{2,})\.([^\W\d_])",
               lambda m: f"{m.group(1)}. {m.group(2)}" if m.group(2).isupper() else m.group(0), s)
    s = re.sub(r"^[,;:\s]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return []
    if not re.search(r"[^\W_]", s) and s != "...":
        return []                                         # chỉ còn dấu câu (trừ nhịp "...") -> bỏ
    if s != "..." and not s.endswith(_END_OK):
        s = s.rstrip(",;:") + "."
    return _split_long(s, max_len) if max_len else [s]


def _pack(pieces: list[str], max_len: int, joiner: str = " ") -> list[str]:
    out, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(joiner) + len(p) > max_len:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}{joiner}{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def _split_long(s: str, max_len: int) -> list[str]:
    if len(s) <= max_len:
        return [s]
    sentences = re.split(r'(?<=[.!?])\s+(?=\S)|(?<=[.!?]")\s+(?=\S)', s)
    pieces: list[str] = []
    for sent in sentences:
        if len(sent) <= max_len:
            pieces.append(sent)
            continue
        clauses = re.split(r"(?<=[,;:])\s+", sent)         # câu quá dài: cắt ở dấu phẩy
        for c in clauses:
            while len(c) > max_len:                         # không có dấu phẩy: cắt cứng ở khoảng trắng
                cut = c.rfind(" ", 0, max_len)
                cut = cut if cut > 0 else max_len
                pieces.append(c[:cut].rstrip())
                c = c[cut:].lstrip()
            if c:
                pieces.append(c)
    return _pack(pieces, max_len)


# ----------------------------------------------------------------------------- pipeline

def clean_text(text: str, cfg: CleanConfig | None = None, report: CleanReport | None = None) -> str:
    cfg = cfg or CleanConfig()
    rep = report if report is not None else CleanReport()
    rep.chars_before += len(text)

    t = _normalize(text)
    if cfg.remove_emoji:
        t = _strip_emoji(t, rep)
    t = _SEPARATOR_LINE_RE.sub("", t)
    if cfg.tilde_to_period:
        t = _fix_tilde(t)

    lines: list[str] = []
    for raw in t.split("\n"):
        line = re.sub(r"[ ]+", " ", raw).strip()
        if not line:
            continue
        if cfg.remove_links:
            stripped, n = strip_links(line)
            rep.links_removed += n
            if n and len(re.findall(r"\w+", stripped)) <= 4:    # "Nguồn: <link>", "Xem thêm: <link>"
                rep.removed_promos.append(raw.strip())
                continue
            line = stripped
        if cfg.fix_obfuscated_words:
            line = fix_obfuscated(line, rep)
        if cfg.remove_chapter_markers and is_chapter_marker(line):
            rep.removed_markers.append(line)
            continue
        if cfg.remove_promos and is_promo(line, cfg.extra_promo_patterns):
            rep.removed_promos.append(line)
            continue
        lines.append(line)

    if cfg.tts_normalize:
        lines = [p for line in lines for p in _tts_paragraph(line, cfg.max_line_chars)]

    out = cfg.paragraph_sep.join(lines)
    out = out + "\n" if out else ""
    rep.chars_after += len(out)
    rep.lines_after += len(lines)
    return out


# ----------------------------------------------------------------------------- file

_VI_ONLY = set("ăâđêôơưĂÂĐÊÔƠƯạảãàằẳẵắặầẩẫấậẹẻẽèềểễếệịỉĩìọỏõòồổỗốộờởỡớợụủũùừửữứựỳỷỹỵ"
               "ẠẢÃÀẰẲẴẮẶẦẨẪẤẬẸẺẼÈỀỂỄẾỆỊỈĨÌỌỎÕÒỒỔỖỐỘỜỞỠỚỢỤỦŨÙỪỬỮỨỰỲỶỸỴ")


def looks_vietnamese(text: str, sample: int = 20000) -> bool:
    """Có phải tiếng Việt không? (để không động vào file dịch sang ngôn ngữ khác)."""
    letters = [c for c in text[:sample] if c.isalpha()]
    if len(letters) < 50:
        return False
    return sum(c in _VI_ONLY for c in letters) / len(letters) >= 0.05


def clean_file(path: str | Path, cfg: CleanConfig | None = None, *, keep_raw: bool = True,
               backup_dir: str = "raw_backup", llm: Callable[[str], str] | None = None) -> CleanReport:
    """Ghi đè `path` bằng bản đã xử lý. Bản gốc lưu ở `<thư mục file>/raw_backup/<tên file>`.

    `llm`: hàm nhận 1 đoạn văn bản, trả đoạn đã chỉnh xưng hô (xem make_llm_refiner). None = bỏ qua bước LLM.
    """
    path = Path(path)
    cfg = cfg or CleanConfig()
    raw_bytes = path.read_bytes()                     # đọc byte: không để Python đổi \r\n trước khi so sánh
    raw = raw_bytes.decode("utf-8", errors="replace")
    rep = CleanReport()
    cleaned = clean_text(raw, cfg, rep)
    if llm is not None and cleaned:
        cleaned = refine_with_llm(cleaned, llm, report=rep)
        cleaned = clean_text(cleaned, cfg)          # LLM có thể làm lệch dấu câu: chuẩn hoá lại (idempotent)
    if cleaned.encode("utf-8") == raw_bytes:
        return rep                                  # đã sạch sẵn: không động vào file, không đè bản gốc
    if keep_raw:
        backup = path.parent / backup_dir / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(cleaned.encode("utf-8"))          # UTF-8 không BOM, xuống dòng LF
    os.replace(tmp, path)
    return rep


# ----------------------------------------------------------------------------- LLM (tuỳ chọn)

_SETTINGS = {
    "co_dai": "Bối cảnh CỔ ĐẠI / cổ trang: dùng cách xưng hô hợp bối cảnh (ta, ngươi, ngài, huynh, đệ, tỷ, muội, công tử, "
              "tiểu thư, bệ hạ, nương nương, nô tì...), bỏ cách gọi và từ ngữ hiện đại.",
    "hien_dai": "Bối cảnh HIỆN ĐẠI: dùng xưng hô thuần Việt tự nhiên nhất có thể (tôi, anh, em, chị, cậu, tớ, bố, mẹ...); "
                "đổi lối convert cứng như 'hắn', 'nàng', 'ngươi', 'ta' thành cách nói đời thường, trừ khi hợp tính cách nhân vật.",
    "auto": "Tự xác định bối cảnh (cổ đại hay hiện đại) từ nội dung rồi chỉnh xưng hô cho nhất quán với bối cảnh đó.",
}
_LLM_SYSTEM = (
    "Bạn là biên tập viên truyện tiếng Việt. Nhiệm vụ DUY NHẤT: chỉnh cách xưng hô và các chi tiết gọi tên nhân vật "
    "cho phù hợp bối cảnh câu chuyện, đọc tự nhiên khi chuyển thành giọng nói.\n{setting}\n{notes}\n"
    "Quy tắc bắt buộc:\n"
    "- Giữ nguyên cốt truyện, thứ tự, số đoạn (mỗi đoạn một dòng) và độ dài gần như cũ. Không tóm tắt, không thêm, không bớt, không kiểm duyệt.\n"
    "- Chỉ trả về đúng phần văn bản đã chỉnh sửa. KHÔNG có lời dẫn, lời kết, giải thích, ghi chú, markdown hay dấu ```.\n"
    "- Không viết những câu như 'Tuyệt!', 'Dưới đây là văn bản đã chỉnh sửa'."
)
_META_LINE_RE = re.compile(
    r"^\s*(?:tuyệt(?:\s+vời)?|được rồi|chắc chắn|tất nhiên|dưới đây|sau đây|đây là|here(?:'s| is)|sure|certainly|of course)\b[^\n]{0,200}$"
    r"|^\s*[^\n]{0,80}(?:văn bản|bản)\s+(?:đã\s+)?(?:chỉnh sửa|biên tập|hiệu đính)[^\n]{0,80}$|^\s*```", re.I)
_REFUSAL_RE = re.compile(
    r"^\s*(?:xin lỗi|rất tiếc|tôi\s+(?:không thể|xin lỗi))[^\n]{0,80}(?:không thể|không được|từ chối)|"
    r"\bI\s+(?:can(?:'|no)t|can not|won't|am unable)\b", re.I)


def _validate_llm_output(src: str, out: str) -> str | None:
    """Trả văn bản hợp lệ (đã bỏ lời dẫn/kết nếu có) hoặc None để rơi về bản gốc của đoạn."""
    out = out.strip()
    lines = out.split("\n")
    while lines and _META_LINE_RE.match(lines[0]) and lines[0].strip() not in src:
        lines.pop(0)
    while lines and _META_LINE_RE.match(lines[-1]) and lines[-1].strip() not in src:
        lines.pop()
    out = "\n".join(lines).strip()
    if not out or _REFUSAL_RE.search(out[:300]) and not _REFUSAL_RE.search(src[:300]):
        return None
    ratio = len(out) / max(1, len(src.strip()))
    return out if 0.75 <= ratio <= 1.3 else None


def refine_with_llm(text: str, call: Callable[[str], str], *, chunk_chars: int = 3500,
                    report: CleanReport | None = None, progress: Callable[[int, int], None] | None = None) -> str:
    """Chia văn bản theo đoạn thành các khối <= chunk_chars, gọi `call` cho từng khối.

    Khối nào bị LLM từ chối / trả lời lệch độ dài / kèm lời dẫn không bỏ được -> GIỮ NGUYÊN bản gốc của khối đó.
    """
    paragraphs = [p for p in text.split("\n") if p.strip()]
    chunks = _pack(paragraphs, chunk_chars, joiner="\n")
    out_chunks = []
    for i, chunk in enumerate(chunks, 1):
        fixed = None
        try:
            fixed = _validate_llm_output(chunk, call(chunk))
        except Exception as e:       # mạng/API lỗi: không làm hỏng cả file
            print(f"⚠️ LLM lỗi ở khối {i}/{len(chunks)}: {type(e).__name__}: {e}")
        if report is not None:
            report.llm_chunks += 1
            report.llm_fallbacks += fixed is None
        out_chunks.append(fixed if fixed is not None else chunk)
        if progress:
            progress(i, len(chunks))
    return "\n".join(out_chunks) + "\n"


def make_llm_refiner(setting: str = "auto", *, model: str = "claude-sonnet-5-5", api_key: str | None = None,
                     notes: str = "", max_tokens: int = 4096, retries: int = 3) -> Callable[[str], str]:
    """Hàm gọi Anthropic Messages API (cần `requests` và ANTHROPIC_API_KEY). setting: co_dai | hien_dai | auto."""
    import requests

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Thiếu ANTHROPIC_API_KEY (đặt biến môi trường hoặc truyền api_key=...).")
    system = _LLM_SYSTEM.format(setting=_SETTINGS.get(setting, _SETTINGS["auto"]),
                                notes=f"Ghi chú nhân vật/quan hệ: {notes}" if notes else "")

    def call(chunk: str) -> str:
        last = None
        for attempt in range(retries):
            try:
                r = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens, "system": system,
                          "messages": [{"role": "user", "content": chunk}]},
                    timeout=120)
                if r.status_code == 200:
                    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code not in (429, 500, 502, 503, 529):
                    break
            except requests.RequestException as e:
                last = str(e)
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(last or "lỗi không rõ")
    return call


# ----------------------------------------------------------------------------- tự động sau mỗi cell


class AutoCleaner:
    """Sau mỗi cell chạy xong, tự xử lý các file .txt tiếng Việt vừa được ghi trong `directory`."""

    _active: "AutoCleaner | None" = None

    def __init__(self, directory: str | Path = ".", cfg: CleanConfig | None = None, *, keep_raw: bool = True,
                 llm: Callable[[str], str] | None = None, enabled: bool = True, ipython=None):
        self.directory = Path(directory)
        self.cfg = cfg or CleanConfig()
        self.keep_raw = keep_raw
        self.llm = llm
        self.enabled = enabled
        self._ip = ipython
        self._t0 = float("inf")          # chưa có cell nào chạy -> không xử lý gì (tránh quét cả thư mục lúc cài)
        self._done: dict[Path, tuple[int, int]] = {}

    def install(self) -> "AutoCleaner":
        ip = self._ip
        if ip is None:
            try:
                ip = get_ipython()  # type: ignore[name-defined]  # noqa: F821
            except NameError:
                ip = None
        if ip is None:
            print("⚠️ Không chạy trong IPython/Jupyter: dùng clean_file(path) thủ công.")
            return self
        if AutoCleaner._active is not None:
            AutoCleaner._active.uninstall()
        self._ip = ip
        ip.events.register("pre_run_cell", self._pre)
        ip.events.register("post_run_cell", self._post)
        AutoCleaner._active = self
        return self

    def uninstall(self) -> None:
        if self._ip is not None:
            for name, fn in (("pre_run_cell", self._pre), ("post_run_cell", self._post)):
                try:
                    self._ip.events.unregister(name, fn)
                except ValueError:
                    pass
        if AutoCleaner._active is self:
            AutoCleaner._active = None

    def _pre(self, *args, **kwargs) -> None:
        self._t0 = time.time() - 2.0

    def _post(self, *args, **kwargs) -> None:
        if self.enabled:
            try:
                self.run_once()
            except Exception as e:       # hook không bao giờ được làm hỏng cell của người dùng
                print(f"⚠️ Xử lý văn bản tự động lỗi: {type(e).__name__}: {e}")

    def run_once(self) -> list[Path]:
        processed = []
        for p in sorted(self.directory.glob("*.txt")):
            st = p.stat()
            sig = (st.st_mtime_ns, st.st_size)
            if st.st_mtime < self._t0 or self._done.get(p) == sig:
                continue
            head = p.read_text(encoding="utf-8", errors="replace")
            if not looks_vietnamese(head):
                continue
            rep = clean_file(p, self.cfg, keep_raw=self.keep_raw, llm=self.llm)
            st = p.stat()
            self._done[p] = (st.st_mtime_ns, st.st_size)
            processed.append(p)
            print(f"🧹 {p.name}: {rep.summary()}")
            for sample in rep.removed_promos[:5]:
                print(f"     ✂ đã xoá: {sample[:100]}")
            if self.keep_raw and rep.chars_before != rep.chars_after:
                print(f"     (bản gốc: {p.parent / 'raw_backup' / p.name})")
        return processed
