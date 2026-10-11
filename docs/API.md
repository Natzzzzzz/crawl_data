# Backend API (cho Flutter)

Chạy: `python -m backend [--port 8765|0] [--data-dir DIR] [--max-concurrent 2]`

Cờ `--exit-on-stdin-eof`: backend tự thoát khi stdin bị đóng (Flutter bật cờ này để không bỏ mồ côi backend khi app chết).

Khi sẵn sàng, backend in **một dòng JSON** ra stdout (các log khác ra stderr):

```json
{"event":"ready","host":"127.0.0.1","port":8765,"token":"...","data_dir":"C:\\Users\\...\\crawl_backend"}
```
Lỗi cổng bận: `{"event":"error","code":"bind_failed",...}` và mã thoát `3`.

Token: `--token`, hoặc biến môi trường `CRAWL_BACKEND_TOKEN`, hoặc tự sinh (đọc từ dòng `ready`).

Mọi route trừ `/health` cần header `Authorization: Bearer <token>`.
Chỉ lắng nghe `127.0.0.1`. Không có CORS (dùng cho Flutter desktop, không dùng từ trình duyệt).

## Lỗi
```json
{"error":{"code":"invalid_range","message":"'end' phải >= 'start'."}}
```
Mã: `unauthorized`(401) `invalid_json` `invalid_url` `unsupported_site` `invalid_field` `invalid_range`
`invalid_out_dir` `invalid_filename`(400) `job_not_found` `not_found`(404) `job_active`(409)
`body_too_large`(413) `upstream_error`(502) `internal`(500).

## Routes
| Method | Path | Mô tả |
|---|---|---|
| GET | `/health` | `{"ok":true,"version":"0.1.0"}` (không cần token) |
| GET | `/sites` | `{"sites":[{"name","host_keywords"}]}` |
| POST | `/story/info` `{url}` | Thông tin truyện: `title, chapters_found, max_chapter_number, announced_total, warnings[]`. **Có thể chậm (retry) — UI cần spinner + timeout.** |
| POST | `/jobs` | Tạo job → 201 + job |
| GET | `/jobs` | `{"jobs":[...]}` mới nhất trước |
| GET | `/jobs/{id}` | 1 job |
| GET | `/jobs/{id}/events` | SSE (xem dưới) |
| POST | `/jobs/{id}/cancel` | Huỷ (giữ lại các chương đã tải xong) |
| DELETE | `/jobs/{id}` | Xoá khỏi lịch sử (409 nếu đang chạy). Không xoá file kết quả. |

### POST /jobs
```json
{"url":"https://toctruyen.net/truyen/ID/slug","start":1,"end":50,
 "workers":4,"delay":0.15,"out_dir":"C:\\Users\\me\\Truyen","filename":"ten.txt","title":"Tên hiển thị"}
```
- Bắt buộc: `url`, `end`. `start` mặc định 1. `start`/`end` là **số chương** (không phải vị trí trong danh sách).
- `workers` (1–8, mặc định 4) và `delay` (0–5 giây, mặc định 0.15) bị kẹp vào khoảng cho phép.
- `out_dir` phải là đường dẫn **tuyệt đối**; bỏ trống = `<data_dir>/downloads`.
- `filename` bỏ trống = lấy từ slug URL. Tên được làm sạch; **không bao giờ ghi đè**: trùng thì thành `ten_2.txt`.

### Job
```json
{"id":"a1b2c3d4e5f6","url":"...","site":"toctruyen","title":"","start":1,"end":50,"workers":4,"delay":0.15,
 "out_dir":"...","output_file":"...","status":"running","done":12,"total":50,
 "result":null,"error":null,"created_at":"2026-10-11T09:00:00+00:00","started_at":"...","finished_at":null}
```
`status`: `queued` → `running` → `done` | `failed` | `cancelled`; `interrupted` = backend chết khi job đang chạy.
`done` có thể kèm `result.failed_numbers` khác rỗng (thiếu vài chương): UI nên cảnh báo.
`result`: `{output_file, requested, saved, failed_numbers[], chars, seconds, cancelled}`.

### SSE `/jobs/{id}/events`
Mỗi sự kiện có `id` (= `seq`, tăng dần từ 0), `event` và `data` (JSON):
```
event: status    {"seq":0,"type":"status","status":"running"}
event: start     {"seq":1,"type":"start","total":50}
event: progress  {"seq":2,"type":"progress","done":1,"total":50,"number":3,"ok":true,"message":""}
event: end       {"seq":52,"type":"end","status":"done","result":{...},"error":null}
```
- Chương hoàn thành **không theo thứ tự** (song song); `done` mới là số đếm tiến độ.
- Server đóng kết nối sau `end`. Có dòng `: ping` mỗi 15 giây khi im lặng.
- Nối lại sau khi rớt: header `Last-Event-ID: <seq cuối đã nhận>` hoặc `?after=<seq>`.
- Job lấy từ lịch sử (sau khi restart backend) chỉ còn đúng 1 sự kiện `end`.
