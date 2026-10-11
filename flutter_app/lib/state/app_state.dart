import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../data/api_client.dart';
import '../data/backend_launcher.dart';
import '../data/models.dart';
import '../data/settings_store.dart';

enum BackendState { stopped, starting, ready, error }

/// Trạng thái toàn app: backend, danh sách job, kết quả phân tích truyện, cài đặt.
class AppController extends ChangeNotifier {
  AppController() {
    _launcher.onExit = _handleBackendExit;
  }

  AppSettings settings = AppSettings.defaults();
  bool settingsLoaded = false;

  BackendState backendState = BackendState.stopped;
  String backendMessage = '';
  ApiClient? api;

  List<Job> jobs = <Job>[];

  bool analyzing = false;
  StoryInfo? info;
  String? analyzeError;

  final BackendLauncher _launcher = BackendLauncher();
  final Set<String> _watching = <String>{};
  bool _disposed = false;

  bool get ready => backendState == BackendState.ready && api != null;
  int get activeJobs => jobs.where((j) => j.isActive).length;
  List<String> get backendLog => _launcher.log;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  // ------------------------------------------------------------------ khởi động
  Future<void> init() async {
    settings = await SettingsStore.load();
    settingsLoaded = true;
    _notify();
    if (settings.backendDir.isEmpty) {
      backendMessage = 'Chưa tìm thấy thư mục backend. Vào Cài đặt để chọn thư mục chứa "backend".';
      _notify();
      return;
    }
    if (settings.autoStart) await startBackend();
  }

  Future<void> startBackend() async {
    if (backendState == BackendState.starting) return;
    backendState = BackendState.starting;
    backendMessage = '';
    _notify();
    try {
      final r = await _launcher.start(python: settings.pythonPath, workingDir: settings.backendDir);
      api?.close();
      final client = ApiClient(host: r.host, port: r.port, token: r.token);
      await client.health();
      api = client;
      backendState = BackendState.ready;
      await refreshJobs();
    } catch (e) {
      await _launcher.stop();
      api?.close();
      api = null;
      backendState = BackendState.error;
      backendMessage = e.toString();
    }
    _notify();
  }

  Future<void> stopBackend() async {
    await _launcher.stop();
    api?.close();
    api = null;
    backendState = BackendState.stopped;
    backendMessage = 'Backend đã dừng.';
    _notify();
  }

  Future<void> restartBackend() async {
    await stopBackend();
    await startBackend();
  }

  void _handleBackendExit(int code) {
    if (backendState != BackendState.ready) return; // lỗi lúc khởi động đã được báo ở startBackend
    api?.close();
    api = null;
    backendState = BackendState.error;
    backendMessage = 'Backend đã dừng bất ngờ (mã thoát $code). Xem nhật ký ở tab Cài đặt.';
    _notify();
  }

  /// Gọi khi đóng app.
  Future<void> shutdown() => _launcher.stop();

  // ------------------------------------------------------------------ cài đặt
  Future<void> updateSettings(AppSettings s) async {
    settings = s;
    _notify();
    await SettingsStore.save(s);
  }

  // ------------------------------------------------------------------ phân tích truyện
  Future<void> analyze(String url) async {
    final client = api;
    if (!ready || client == null) {
      analyzeError = 'Backend chưa sẵn sàng.';
      _notify();
      return;
    }
    analyzing = true;
    analyzeError = null;
    info = null;
    _notify();
    try {
      info = await client.storyInfo(url);
    } on ApiException catch (e) {
      analyzeError = e.message;
    } catch (e) {
      analyzeError = e.toString();
    }
    analyzing = false;
    _notify();
  }

  // ------------------------------------------------------------------ job
  Future<void> refreshJobs() async {
    final client = api;
    if (client == null) return;
    jobs = await client.listJobs();
    for (final j in jobs) {
      if (j.isActive) _watch(j.id);
    }
    _notify();
  }

  Future<Job> startJob({
    required String url,
    required int start,
    required int end,
    required int workers,
    required double delay,
    required String outDir,
    String title = '',
  }) async {
    final client = api;
    if (!ready || client == null) throw ApiException('Backend chưa sẵn sàng.', code: 'not_ready');
    final job = await client.createJob(
      url: url,
      start: start,
      end: end,
      workers: workers,
      delay: delay,
      outDir: outDir,
      title: title,
    );
    jobs = <Job>[job, ...jobs];
    _notify();
    _watch(job.id);
    return job;
  }

  Future<void> cancelJob(String id) async {
    final client = api;
    if (client == null) return;
    _replaceJob(await client.cancelJob(id));
  }

  Future<void> deleteJob(String id) async {
    final client = api;
    if (client == null) return;
    await client.deleteJob(id);
    jobs = jobs.where((j) => j.id != id).toList();
    _notify();
  }

  Future<void> openFolder(String path) async {
    try {
      if (Platform.isWindows) {
        await Process.run('explorer.exe', <String>[path]); // explorer trả mã 1 dù mở thành công: bỏ qua mã thoát
      } else if (Platform.isMacOS) {
        await Process.run('open', <String>[path]);
      } else {
        await Process.run('xdg-open', <String>[path]);
      }
    } catch (_) {
      // không mở được thư mục: không có gì nghiêm trọng
    }
  }

  void _replaceJob(Job job) {
    jobs = jobs.map((j) => j.id == job.id ? job : j).toList();
    _notify();
  }

  void _applyEvent(String id, JobEvent ev) {
    final index = jobs.indexWhere((j) => j.id == id);
    if (index < 0) return;
    final job = jobs[index];
    Job next = job;
    switch (ev.type) {
      case 'status':
        next = job.copyWith(status: ev.status);
        break;
      case 'start':
        next = job.copyWith(total: ev.total);
        break;
      case 'progress':
        next = job.copyWith(done: ev.done, total: ev.total);
        break;
      case 'end':
        next = job.copyWith(status: ev.status, result: ev.result, error: ev.error);
        break;
    }
    if (!identical(next, job)) {
      jobs = List<Job>.of(jobs)..[index] = next;
      _notify();
    }
  }

  /// Theo dõi tiến độ 1 job bằng SSE; mất kết nối thì nối lại, và luôn chốt lại bằng GET /jobs/{id}.
  void _watch(String id) {
    if (!_watching.add(id)) return;
    final client = api;
    unawaited(() async {
      var after = -1;
      var failures = 0;
      while (client != null && identical(client, api) && !_disposed) {
        try {
          await for (final ev in client.events(id, after: after >= 0 ? after : null)) {
            after = ev.seq;
            failures = 0;
            _applyEvent(id, ev);
            if (ev.type == 'end') break;
          }
        } catch (_) {
          failures++;
        }
        try {
          final fresh = await client.getJob(id); // nguồn sự thật: tránh kẹt "đang chạy" khi lỡ sự kiện
          _replaceJob(fresh);
          if (fresh.isFinished) break;
        } catch (_) {
          failures++;
        }
        if (failures > 5) break;
        await Future<void>.delayed(const Duration(seconds: 2));
      }
      _watching.remove(id);
    }());
  }

  @override
  void dispose() {
    _disposed = true;
    api?.close();
    super.dispose();
  }
}
