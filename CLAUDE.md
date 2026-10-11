# Rules cho dự án crawl truyện (toctruyen, metruyen, monkeydtruyen, truyenfull, ...)

Rule này rút từ lỗi đã thật sự gặp trong repo này. Mục tiêu: code chạy được ngay,
nhanh, không mất chương, không làm bẩn text, và không phá môi trường.

## 1. Môi trường máy này (đọc trước khi chạy bất cứ gì)

- **Không có Xcode Command Line Tools, không có Homebrew.** `/usr/bin/python3` và `git`
  là shim: gọi vào là in `xcode-select: note: No developer tools were found` rồi fail.
  → **Không bao giờ gọi `python3` / `pip` / `git` trần.** Dùng `uv`.
- Python + venv đến từ `~/.local/bin/uv`. App venv hiện tại: `.venv` (Python 3.15).
- Cài đặt: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`
  (đọc `requirements.txt` là nguồn sự thật về phiên bản).
- Code đọc file tải về từ web: chạy với `-I` và mỗi truyện một thư mục riêng,
  vì interpreter có thể load code lạ từ thư mục script (vd `json.py` cài sẵn).

## 2. Notebook VS Code: quy tắc khi sửa `.ipynb` bằng lệnh

Editor giữ model trong RAM và **không reload** khi file bị sửa từ ngoài. Hệ quả đã xảy ra:
user bấm Save và **revert ngược lại** thay đổi của tôi (cell 0 bị trả về `pip install` thô 2 lần).

- Mặc định: **sửa file `.py` riêng**, notebook chỉ là chỗ chạy. Module `toctruyen_crawler.py`
  là bản mẫu cho các crawler khác — chạy được bằng `python toctruyen_crawler.py`, không phụ thuộc notebook.
- Nếu buộc phải sửa `.ipynb` khi user đang mở: nói rõ là phải
  `Cmd+Shift+P → File: Revert File`, và **cấm user Save trước khi revert** (Save sẽ ghi đè).
- Kiểm tra hiện trạng đĩa **trước khi sửa**: `jq -r '.cells[0].source|join("")' Crawl_Data.ipynb` —
  đừng tin rằng lần sửa trước còn nguyên.
- Trước khi user save, giữ 1 bản backup để phục hồi thứ tự cell:
  `cp Crawl_Data.ipynb /tmp/nb_before_<mục đích>.ipynb`.

## 3. Cell cài đặt

Viết `%pip install ...` — **KHÔNG viết `pip install ...`** trong code cell:
shell không có `pip` trên máy này (`zsh: command not found: pip`), và `%pip` cài đúng vào
interpreter của kernel đang chạy. Liệt kê đủ deps thật dùng (`lxml`, `deep-translator`),
không chỉ 4 cái đầu tiên.

Kernel: chọn **"Python 3 (crawl_data venv)"** (`Cmd+Shift+P → Notebook: Select Notebook Kernel`).
Bẫy đã gặp: user tự tạo venv tên `.venvv` (2 chữ v) rồi kernel trỏ vào nó →
`Could not find a suitable TLS CA certificate bundle` (thiếu `certifi`) và
`ModuleNotFoundError: stack_data` (IPython không render được traceback). Venv sai tên đó đã bị xoá.

## 4. Quy trình viết một crawler cho site mới (theo đúng thứ tự)

1. **Probe bằng `requests` trước, chưa động tới Selenium.** Test:
   `GET` trang truyện + 1 trang chương, in `HTTP status`, số byte, và
   `select_one(<selector nội dung>)` có tồn tại trong HTML tĩnh hay không.
   Với toctruyen: có đủ ⇒ bỏ 100% Selenium, nhanh gấp ~55 lần (47.02s → 0.85s/6 chương).
2. **Tìm JSON endpoint nội bộ của site** (đây là nguồn dữ liệu tốt nhất):
   - List `<script src>` trong trang, mở JS widget, tìm `ajaxData: {url: ...}`.
   - toctruyen: `GET /content/subitems?pid=<id>` với header `X-Requested-With: XMLHttpRequest`
     + `Referer` = URL truyện. (POST bị từ chối `Missing ID` — phải GET.)
   - **Bẫy chết người:** scroll DOM chỉ render ~10 chương ⇒ `end_chap=50` mất chương
     **không báo lỗi**. Luôn in số chương lấy được và đối chiếu con số site công bố
     (`span.subitem-list-num`).
3. **Một `requests.Session` dùng chung** + `HTTPAdapter(pool_connections, pool_maxsize)`
   để reuse TLS. `User-Agent` + `Accept-Language` đặt ở session, không đặt rải rác.
4. **Tải song song** bằng `ThreadPoolExecutor` (I/O-bound), mặc định `workers=4`.
   Đã đo trên 15 chương: 1→2.49s, 2→0.72s, 4→0.42s, 6→0.34s, 8→0.41s ⇒ 4–6 là điểm tốt,
   đừng tăng vô hạn (site dội). **Luôn sort lại theo số chương khi ghi file.**
5. **Verify bằng cách chạy thật**, không chỉ import: chạy cell qua `nbclient` với kernel
   `crawl_data`, redirect output sang `/tmp/bench/` để không đè file user đang mở.

## 5. Chất lượng text (đây là phần làm bẩn dữ liệu nhiều nhất)

- **Lấy `<p>` làm đoạn văn.** site truyện đã đánh dấu đoạn bằng `<p>` rồi;
  heuristic gộp câu theo dấu chấm than/hỏi trong bản cũ **nhồi cả chương vào 1 dòng**
  (12 dòng cho 6 chương) và ăn mất chữ.
- **Không hard-code class chứa nội dung.** toctruyen sinh class ngẫu nhiên
  (`div.t4a458058`). Thay vào đó: chọn `div` con trực tiếp có nhiều `<p>` nhất.
- **Blacklist phải hẹp và test trên output thật.** Đã suýt xoá `……`/`…` — trong truyện
  đó là nhịp im lặng của nhân vật, là **nội dung thật**. Thứ đáng xoá là
  `<p>1</p>`, `<p>2</p>` (số trang), `Đọc sách tâm linh`, link Shopee, dòng `HẾT.`.
- **Đừng tự "chuẩn hoá" dấu câu quá đà.** `re.sub(r'(?<=\w)[^\w\s](?=\w)', '', text)`
  (dùng trong bản cũ) xoá cả ký tự vô vọng như `đ.ạ.m` — quyết định này phải của user,
  không phải của crawler.
- **Assert encoding thật:** sau khi GET, kiểm không có mojibake (`Ã`, `â€`, `ï»¿`)
  và in ra `Content-Type`. Đừng `res.encoding='utf-8'` mù nếu header đã khai charset khác.

## 6. Robustness — các nhánh lỗi

- Retry có **backoff + jitter**, không `time.sleep(5)` mù. Bắt cả `OSError`, không chỉ
  `RequestException` — lỗi CA/SSL context là `OSError`.
- **CA bundle:** kiểm tra file có tồn tại & đủ lớn, không thì rơi về
  `/etc/ssl/cert.pem` (xem `find_ca_bundle()`), rồi cuối cùng `verify=True`.
- **Fallback parser:** `lxml` → `html.parser` (`_parse()` trả `None` khi thiếu parser),
  không để `FeatureNotFound` giết cả run.
- **Không ném exception từ 1 chương** ⇒ trả `f"Chương {n}\n⚠️ ..."` để các chương còn lại
  vẫn ghi được; in danh sách chương thiếu ở cuối.
- **Key kết quả theo index của danh sách đã chọn, không theo số chương** —
  "Chương 1" và "Chương 1: Ngoại truyện" trùng số sẽ đè mất nhau.
- **Không hard-code host:** dùng origin từ chính URL user dán (`urljoin(ref.origin, link)`)
  vì các site này đổi tên miền/mirror liên tục.
- API đổi schema ⇒ **fallback về parsing HTML trang truyện**, in cảnh báo rõ là
  chỉ lấy được số chương render sẵn.

## 7. Văn hoá làm việc trong repo này

- **Đo trước khi tối ưu, nói số.** Giữ baseline (`47.02s`) cạnh kết quả (`0.85s`),
  đo từng thành phần tách riêng (parse 6 chương: `lxml` 53ms vs `html.parser` 49ms ⇒
  gần bằng nhau, nên **không** khoe "nhanh hơn nhờ lxml").
- **Đừng sửa ngoài phạm vi yêu cầu.** User yêu cầu sắp xếp cell thì chỉ sắp xếp cell;
  token rác (`vzzzimport`, `:zzz`) phát hiện được thì **báo** để user quyết.
- **Không im lặng khi mất dữ liệu.** Nếu file backup/origin không còn như đã nói,
  báo ngay và nêu đường dẫn phục hồi còn lại (`/tmp/pre_fix.ipynb`, `/tmp/nb_dump.txt`).
- **Crawl lịch sự:** delay nhỏ giữa request, `workers` vừa phải, giờ thấp điểm,
  tôn trọng nội dung có bản quyền — chỉ dùng cho việc của chính mình.

## 8. Audit hiện trạng 10 section (số đo thật, ngày 2026-10-11)

| cell | section | engine | parser | session | sleep chờ DOM | throttle |
|---|---|---|---|---|---|---|
| 3 | TocTruyen | requests | lxml | Session | 0 | ✅ |
| 5 | **Metruyen.mobi** ⚠️ | requests | lxml | Session | 0 | ⚠️ *sai nội dung — xem 8.1* |
| 7 | monkeydtruyen | selenium | html.parser | – | 2 | ✅ |
| 9 | Truyenfull.me | requests | html.parser | `requests.get` | 0 | ✅ |
| 11 | Truyenfull.vision | requests | html.parser | `requests.get` | 0 | ✅ |
| 13 | Tàng Thư Viện | cloudscraper | lxml | – | 0 | ✅ |
| 15 | Truyendecu.net | requests | html.parser | `requests.get` | 1 | ✅ |
| 17 | Otruyen.vn | selenium | html.parser | – | 2 | ❌ |
| 19 | Daotruyen.me | selenium | html.parser | – | 2 | ✅ |
| 21 | Truyenmacothat.net | selenium | html.parser | `requests.get` | 2 | ❌ |

Việc cần làm theo thứ tự ích: (a) **9 chỗ `time.sleep` cứng để chờ DOM** trải trên 5 cell
(7,15,17,19,21) — 4 cell selenium vẫn mở Chrome không cần thiết; (b) **7 cell dùng
`html.parser` dù `lxml` đã cài**; (c) **4 cell gọi `requests.get()` trần** (9,11,15,21)
⇒ không reuse TLS.

Phân biệt khi đọc code (kẻo tối ưu nhầm cái đúng):
`time.sleep` **cố định để chờ DOM** = bug tốc độ, thay bằng polling/`WebDriverWait` có điều kiện.
`time.sleep` **trong retry backoff hoặc throttle giữa request** = **cố ý, giữ lại** —
đó là tiền sử server và lịch sự với site, đừng xoá để đổi lấy tốc độ.

### 8.1 Sự cố đã xảy ra: section này bị ghi đè bằng section khác

Cell **5 dưới heading "Metruyen.mobi" hiện chứa code toctruyen** (bản tối ưu, đã đổi URL sang
`ta-sinh-con-cho-long-ngao-thien`), không còn `metruyen.mobi` nào trong notebook nữa.
Code Metruyen gốc (120 dòng: `format_paragraphs`, `get_chapter_list`, `get_chapter_content`,
`crawl_truyen_range`) **chỉ còn trong backup** `.backups/Crawl_Data_before_fix_2026-10-10.ipynb`
cell 5. Hệ quả: 2 heading liên tiếp trỏ cùng 1 crawler, chạy cell 5 là ghi đè file toctruyen.

Rule rút ra:
- **Một heading = một site.** Trước khi sửa/sao chép cell, kiểm tra marker của site
  (`grep -c "metruyen"` trong chính cell đó) chứ không tin heading.
- **Không dán code của site A vào ô của site B.** Muốn tái dùng bản tối ưu: tách hàm chung,
  tham số hoá `BASE_URL` + selector + endpoint, mỗi cell gọi với config của riêng nó.
- **Backup vào repo, không vào `/tmp`** (tmp bị purge): `.backups/` như ở đây.
  Mỗi lần sửa notebook: `cp Crawl_Data.ipynb .backups/Crawl_Data_<ngày>_<mục đích>.ipynb`.
- **Báo ngay khi phát hiện mất dữ liệu** và nêu đường dẫn phục hồi — không im lặng.

Quy trình chuẩn cho mỗi crawler còn lại: probe endpoint → bỏ sleep chờ DOM (giữ throttle) →
`Session` dùng chung + `lxml` → song song `workers=4` → chạy qua kernel với output về `/tmp/bench/`.

## 9. Lệnh kiểm tra nhanh

```bash
# env lành?
.venv/bin/python -c "import requests,bs4,lxml,selenium,cloudscraper,deep_translator,certifi,stack_data;print('OK')"
# TLS + CA?
.venv/bin/python -c "import certifi,os;print(os.path.isfile(certifi.where()))"
# 1 trang truyện sống?
.venv/bin/python -c "import requests;print(requests.get('https://toctruyen.net/truyen/avlBQwkA/ban-trai-than-tien-ho-tong',timeout=20).status_code)"
# chạy crawler
.venv/bin/python -I toctruyen_crawler.py
```
