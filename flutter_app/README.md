# Flutter app (Windows desktop)

Giao diện cho backend Python. Không dùng package bên thứ ba: chỉ Flutter SDK + `dart:io`.

## Chuẩn bị (một lần)

1. Cài Flutter SDK, rồi kiểm tra: `flutter doctor`. Để build Windows cần **Visual Studio 2022** với workload
   **"Desktop development with C++"** (`flutter doctor` sẽ báo nếu thiếu).
2. Backend Python đã chạy được: có `.venv` ở thư mục `crawl_project` (xem hướng dẫn cài ở đầu dự án).

## Dựng project Windows

Thư mục này mới có `lib/`, `test/`, `pubspec.yaml`. Cần sinh thêm thư mục `windows/`:

```powershell
cd crawl_project\flutter_app
flutter create --platforms=windows .
```

Lệnh này giữ nguyên `lib/main.dart` và `pubspec.yaml` có sẵn. Nếu nó tạo ra `test\widget_test.dart`
thì **xoá file đó đi** (nó tham chiếu một widget mẫu không còn tồn tại và sẽ làm `flutter test` lỗi).

## Chạy

```powershell
flutter pub get
flutter test                 # test logic thuần (parser SSE, models)
flutter run -d windows
```

App tự tìm thư mục chứa `backend\` bằng cách đi ngược lên từ thư mục chạy, nên đặt app trong
`crawl_project\flutter_app` là tự nhận. Nếu không nhận: vào **Cài đặt**, điền
"Thư mục backend" và đường dẫn `...\.venv\Scripts\python.exe`, bấm **Lưu và khởi động lại backend**.

Build bản chạy độc lập: `flutter build windows` (file .exe ở `build\windows\x64\runner\Release`).

## Cách hoạt động

- App chạy `python -X utf8 -m backend --port 0 --exit-on-stdin-eof`, đọc dòng JSON `ready` (port + token) từ stdout.
- Mọi request kèm token `Bearer`. Tiến độ job lấy qua SSE; mất kết nối thì tự nối lại, và luôn chốt lại bằng `GET /jobs/{id}`.
- Đóng app thì backend được dừng. App bị kill đột ngột thì backend tự thoát vì pipe stdin bị OS đóng.
- Cài đặt lưu ở `%APPDATA%\crawl_app\settings.json`.

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| Banner đỏ "Không chạy được Python" | Sai đường dẫn Python trong Cài đặt |
| "Backend thoát sớm" + log có `No module named 'backend'` | "Thư mục backend" phải là thư mục **chứa** `backend`, không phải chính `backend` |
| "Backend không phản hồi sau 25 giây" | Xem tab Cài đặt → Nhật ký backend |
| Phân tích truyện báo lỗi sau ~30s | Mất mạng hoặc site chặn: backend retry vài lần rồi mới báo |
