import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message, {this.status, this.code = 'error'});

  final String message;
  final int? status;
  final String code;

  @override
  String toString() => message;
}

/// Tách luồng dòng của SSE thành các JSON object (mỗi sự kiện kết thúc bằng 1 dòng trống).
/// Bỏ qua dòng chú thích (": ping") và các trường id/event (JSON đã chứa seq và type).
Stream<Map<String, dynamic>> parseSse(Stream<String> lines) async* {
  final dataLines = <String>[];
  await for (final line in lines) {
    if (line.isEmpty) {
      if (dataLines.isNotEmpty) {
        final text = dataLines.join('\n');
        dataLines.clear();
        try {
          final obj = jsonDecode(text);
          if (obj is Map<String, dynamic>) yield obj;
        } on FormatException {
          // sự kiện hỏng: bỏ qua, không làm đứt luồng
        }
      }
      continue;
    }
    if (line.startsWith(':')) continue;
    if (line.startsWith('data:')) {
      var value = line.substring(5);
      if (value.startsWith(' ')) value = value.substring(1);
      dataLines.add(value);
    }
  }
}

class ApiClient {
  ApiClient({required this.host, required this.port, required this.token});

  final String host;
  final int port;
  final String token;
  final HttpClient _http = HttpClient()..connectionTimeout = const Duration(seconds: 10);

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri(scheme: 'http', host: host, port: port, path: path, queryParameters: query);

  void close() => _http.close(force: true);

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Object? body,
    Duration timeout = const Duration(seconds: 20),
    bool auth = true,
  }) async {
    try {
      final req = await _http.openUrl(method, _uri(path)).timeout(timeout);
      if (auth) req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      if (body != null) {
        final bytes = utf8.encode(jsonEncode(body));
        req.headers.contentType = ContentType.json;
        req.contentLength = bytes.length;
        req.add(bytes);
      }
      final res = await req.close().timeout(timeout);
      final text = await res.transform(utf8.decoder).join().timeout(timeout);

      Map<String, dynamic>? data;
      try {
        final decoded = jsonDecode(text);
        if (decoded is Map<String, dynamic>) data = decoded;
      } on FormatException {
        data = null;
      }

      if (res.statusCode >= 200 && res.statusCode < 300) return data ?? <String, dynamic>{};

      final err = data == null ? null : data['error'];
      if (err is Map) {
        throw ApiException(
          (err['message'] ?? 'Lỗi ${res.statusCode}').toString(),
          status: res.statusCode,
          code: (err['code'] ?? 'error').toString(),
        );
      }
      throw ApiException('Lỗi ${res.statusCode} từ backend.', status: res.statusCode);
    } on ApiException {
      rethrow;
    } on TimeoutException {
      throw ApiException('Hết thời gian chờ backend.', code: 'timeout');
    } on SocketException catch (e) {
      throw ApiException('Không kết nối được backend (${e.message}).', code: 'connection');
    } on HttpException catch (e) {
      throw ApiException('Lỗi HTTP: ${e.message}', code: 'connection');
    }
  }

  Future<HttpClientRequest> _openGet(Uri uri) async {
    try {
      return await _http.getUrl(uri);
    } on SocketException catch (e) {
      throw ApiException('Không kết nối được backend (${e.message}).', code: 'connection');
    }
  }

  Future<void> health() => _request('GET', '/health', auth: false);

  Future<StoryInfo> storyInfo(String url) async {
    // Backend có thể retry vài lần khi mạng chậm/mất: cho phép chờ lâu.
    final data = await _request('POST', '/story/info', body: {'url': url}, timeout: const Duration(seconds: 90));
    return StoryInfo.fromJson(data);
  }

  Future<List<Job>> listJobs() async {
    final data = await _request('GET', '/jobs');
    final raw = data['jobs'];
    if (raw is! List) return <Job>[];
    return raw.whereType<Map<String, dynamic>>().map(Job.fromJson).toList();
  }

  Future<Job> getJob(String id) async => Job.fromJson(await _request('GET', '/jobs/$id'));

  Future<Job> createJob({
    required String url,
    required int start,
    required int end,
    required int workers,
    required double delay,
    required String outDir,
    String title = '',
    String? filename,
  }) async {
    final body = <String, dynamic>{
      'url': url,
      'start': start,
      'end': end,
      'workers': workers,
      'delay': delay,
      'out_dir': outDir,
      'title': title,
    };
    if (filename != null && filename.isNotEmpty) body['filename'] = filename;
    return Job.fromJson(await _request('POST', '/jobs', body: body));
  }

  Future<Job> cancelJob(String id) async => Job.fromJson(await _request('POST', '/jobs/$id/cancel'));

  Future<void> deleteJob(String id) => _request('DELETE', '/jobs/$id');

  /// Luồng SSE của một job. Kết thúc (đóng luồng) sau sự kiện `end`.
  /// `after`: seq cuối cùng đã nhận, để nối lại mà không nhận trùng.
  Stream<JobEvent> events(String jobId, {int? after}) async* {
    final req = await _openGet(_uri('/jobs/$jobId/events', after == null ? null : {'after': '$after'}));
    req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    req.headers.set(HttpHeaders.acceptHeader, 'text/event-stream');
    final res = await req.close();
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw ApiException('Không mở được luồng tiến độ (HTTP ${res.statusCode}).', status: res.statusCode);
    }
    final lines = res.transform(utf8.decoder).transform(const LineSplitter());
    await for (final m in parseSse(lines)) {
      yield JobEvent.fromJson(m);
    }
  }
}
