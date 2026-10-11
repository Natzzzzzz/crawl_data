import 'dart:async';
import 'dart:convert';
import 'dart:io';

class BackendReady {
  const BackendReady({required this.host, required this.port, required this.token});

  final String host;
  final int port;
  final String token;
}

/// Chạy `python -m backend` như một tiến trình con và đọc dòng JSON `ready` từ stdout.
class BackendLauncher {
  Process? _process;
  bool _stopping = false;
  final List<String> _log = <String>[];

  /// Gọi khi backend thoát mà KHÔNG phải do mình yêu cầu dừng.
  void Function(int exitCode)? onExit;

  bool get running => _process != null;
  List<String> get log => List<String>.unmodifiable(_log);

  void _addLog(String line) {
    _log.add(line);
    if (_log.length > 200) _log.removeRange(0, _log.length - 200);
  }

  String get _tail => _log.length <= 8 ? _log.join('\n') : _log.sublist(_log.length - 8).join('\n');

  Future<BackendReady> start({required String python, required String workingDir, String? dataDir}) async {
    await stop();
    _log.clear();
    _stopping = false;

    final args = <String>[
      '-X', 'utf8',
      '-m', 'backend',
      '--port', '0', // cổng trống bất kỳ: không bao giờ đụng cổng đang dùng
      '--exit-on-stdin-eof', // app chết thì backend tự thoát
      if (dataDir != null && dataDir.isNotEmpty) ...<String>['--data-dir', dataDir],
    ];

    final Process p;
    try {
      p = await Process.start(python, args, workingDirectory: workingDir);
    } on ProcessException catch (e) {
      throw StateError('Không chạy được Python "$python": ${e.message}');
    }
    _process = p;

    final ready = Completer<BackendReady>();

    p.stdout.transform(utf8.decoder).transform(const LineSplitter()).listen((line) {
      _addLog(line);
      if (ready.isCompleted) return;
      try {
        final j = jsonDecode(line);
        if (j is Map && j['event'] == 'ready') {
          ready.complete(BackendReady(
            host: (j['host'] ?? '127.0.0.1').toString(),
            port: (j['port'] as num).toInt(),
            token: j['token'].toString(),
          ));
        } else if (j is Map && j['event'] == 'error') {
          ready.completeError(StateError((j['message'] ?? 'Backend báo lỗi khi khởi động.').toString()));
        }
      } on FormatException {
        // dòng log thường, không phải JSON
      }
    });
    p.stderr.transform(utf8.decoder).transform(const LineSplitter()).listen(_addLog);

    unawaited(p.exitCode.then((code) {
      final wasCurrent = identical(_process, p);
      if (wasCurrent) _process = null;
      if (!ready.isCompleted) {
        ready.completeError(StateError('Backend thoát sớm (mã $code).\n$_tail'));
      } else if (wasCurrent && !_stopping) {
        onExit?.call(code);
      }
    }));

    return ready.future.timeout(const Duration(seconds: 25), onTimeout: () {
      p.kill();
      throw StateError('Backend không phản hồi sau 25 giây.\n$_tail');
    });
  }

  /// Dừng nhẹ nhàng: đóng stdin (backend tự thoát), chờ tối đa 5 giây rồi mới kill.
  Future<void> stop() async {
    final p = _process;
    if (p == null) return;
    _stopping = true;
    _process = null;
    try {
      await p.stdin.close();
    } catch (_) {
      // stdin có thể đã đóng
    }
    try {
      await p.exitCode.timeout(const Duration(seconds: 5));
    } on TimeoutException {
      p.kill();
    }
  }
}
