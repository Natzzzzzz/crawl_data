import 'dart:convert';
import 'dart:io';

class AppSettings {
  const AppSettings({
    required this.backendDir,
    required this.pythonPath,
    required this.outDir,
    required this.workers,
    required this.delay,
    required this.autoStart,
  });

  final String backendDir; // thư mục chứa backend/ (crawl_project)
  final String pythonPath; // python.exe trong .venv
  final String outDir; // nơi lưu file .txt
  final int workers;
  final double delay;
  final bool autoStart;

  AppSettings copyWith({
    String? backendDir,
    String? pythonPath,
    String? outDir,
    int? workers,
    double? delay,
    bool? autoStart,
  }) =>
      AppSettings(
        backendDir: backendDir ?? this.backendDir,
        pythonPath: pythonPath ?? this.pythonPath,
        outDir: outDir ?? this.outDir,
        workers: workers ?? this.workers,
        delay: delay ?? this.delay,
        autoStart: autoStart ?? this.autoStart,
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'backendDir': backendDir,
        'pythonPath': pythonPath,
        'outDir': outDir,
        'workers': workers,
        'delay': delay,
        'autoStart': autoStart,
      };

  static AppSettings defaults() {
    final dir = detectBackendDir() ?? '';
    return AppSettings(
      backendDir: dir,
      pythonPath: dir.isEmpty ? (Platform.isWindows ? 'python' : 'python3') : defaultPython(dir),
      outDir: defaultOutDir(),
      workers: 4,
      delay: 0.15,
      autoStart: true,
    );
  }

  factory AppSettings.fromJson(Map<String, dynamic> j) {
    final d = AppSettings.defaults();
    String str(String key, String fallback) => j[key] is String && (j[key] as String).isNotEmpty ? j[key] as String : fallback;
    final w = j['workers'];
    final dl = j['delay'];
    return AppSettings(
      backendDir: str('backendDir', d.backendDir),
      pythonPath: str('pythonPath', d.pythonPath),
      outDir: str('outDir', d.outDir),
      workers: w is num ? w.toInt().clamp(1, 8).toInt() : d.workers,
      delay: dl is num ? dl.toDouble().clamp(0.0, 5.0).toDouble() : d.delay,
      autoStart: j['autoStart'] is bool ? j['autoStart'] as bool : d.autoStart,
    );
  }
}

String get _sep => Platform.pathSeparator;

String _home() => Platform.environment['USERPROFILE'] ?? Platform.environment['HOME'] ?? '.';

String defaultOutDir() => '${_home()}${_sep}Documents${_sep}TruyenCrawl';

/// Đi ngược từ thư mục chạy app và thư mục file .exe để tìm thư mục có backend/__main__.py.
String? detectBackendDir() {
  final starts = <Directory>[Directory.current, File(Platform.resolvedExecutable).parent];
  for (final start in starts) {
    var dir = start.absolute;
    for (var i = 0; i < 8; i++) {
      if (File('${dir.path}${_sep}backend$_sep' '__main__.py').existsSync()) return dir.path;
      final parent = dir.parent;
      if (parent.path == dir.path) break;
      dir = parent;
    }
  }
  return null;
}

String defaultPython(String backendDir) {
  final venv = Platform.isWindows
      ? '$backendDir$_sep.venv${_sep}Scripts${_sep}python.exe'
      : '$backendDir$_sep.venv${_sep}bin${_sep}python';
  if (File(venv).existsSync()) return venv;
  return Platform.isWindows ? 'python' : 'python3';
}

class SettingsStore {
  static File _file() {
    final base = Platform.isWindows
        ? (Platform.environment['APPDATA'] ?? _home())
        : '${_home()}${_sep}.config';
    return File('$base${_sep}crawl_app$_sep' 'settings.json');
  }

  static Future<AppSettings> load() async {
    try {
      final f = _file();
      if (await f.exists()) {
        final j = jsonDecode(await f.readAsString());
        if (j is Map<String, dynamic>) return AppSettings.fromJson(j);
      }
    } catch (_) {
      // file hỏng: dùng mặc định, không làm app không mở được
    }
    return AppSettings.defaults();
  }

  static Future<void> save(AppSettings s) async {
    final f = _file();
    await f.parent.create(recursive: true);
    await f.writeAsString(const JsonEncoder.withIndent('  ').convert(s.toJson()));
  }
}
