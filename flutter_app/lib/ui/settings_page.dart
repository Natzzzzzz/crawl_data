import 'package:flutter/material.dart';

import '../data/settings_store.dart';
import '../state/app_scope.dart';
import '../state/app_state.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final TextEditingController _backendDir = TextEditingController();
  final TextEditingController _python = TextEditingController();
  final TextEditingController _outDir = TextEditingController();
  int _workers = 4;
  double _delay = 0.15;
  bool _autoStart = true;
  bool _synced = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final app = AppScope.of(context);
    if (!_synced && app.settingsLoaded) {
      final s = app.settings;
      _backendDir.text = s.backendDir;
      _python.text = s.pythonPath;
      _outDir.text = s.outDir;
      _workers = s.workers;
      _delay = s.delay;
      _autoStart = s.autoStart;
      _synced = true;
    }
  }

  @override
  void dispose() {
    _backendDir.dispose();
    _python.dispose();
    _outDir.dispose();
    super.dispose();
  }

  AppSettings _collect(AppSettings base) => base.copyWith(
        backendDir: _backendDir.text.trim(),
        pythonPath: _python.text.trim(),
        outDir: _outDir.text.trim(),
        workers: _workers,
        delay: _delay,
        autoStart: _autoStart,
      );

  Future<void> _save({bool restart = false}) async {
    final app = AppScope.read(context);
    final messenger = ScaffoldMessenger.of(context);
    final s = _collect(app.settings);
    if (s.outDir.isEmpty || s.pythonPath.isEmpty) {
      messenger.showSnackBar(const SnackBar(content: Text('Đường dẫn Python và thư mục lưu không được để trống.')));
      return;
    }
    await app.updateSettings(s);
    if (restart) await app.restartBackend();
    messenger.showSnackBar(SnackBar(content: Text(restart ? 'Đã lưu và khởi động lại backend.' : 'Đã lưu cài đặt.')));
  }

  String _stateLabel(BackendState state) {
    switch (state) {
      case BackendState.stopped:
        return 'Đã dừng';
      case BackendState.starting:
        return 'Đang khởi động…';
      case BackendState.ready:
        return 'Đang chạy';
      case BackendState.error:
        return 'Lỗi';
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final theme = Theme.of(context);
    final logText = app.backendLog.isEmpty ? '(chưa có nhật ký)' : app.backendLog.join('\n');

    return ListView(
      padding: const EdgeInsets.all(24),
      children: <Widget>[
        Text('Cài đặt', style: theme.textTheme.headlineSmall),
        const SizedBox(height: 16),
        TextField(
          controller: _backendDir,
          decoration: const InputDecoration(
            labelText: 'Thư mục backend (thư mục chứa "backend")',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _python,
          decoration: const InputDecoration(
            labelText: 'Python (vd: …\\.venv\\Scripts\\python.exe)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _outDir,
          decoration: const InputDecoration(
            labelText: 'Thư mục lưu truyện (đường dẫn tuyệt đối)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 16),
        Text('Số luồng mặc định: $_workers'),
        Slider(
          value: _workers.toDouble(),
          min: 1,
          max: 8,
          divisions: 7,
          label: '$_workers',
          onChanged: (v) => setState(() => _workers = v.round()),
        ),
        Text('Nghỉ mặc định giữa các request: tối đa ${_delay.toStringAsFixed(2)} giây'),
        Slider(
          value: _delay.clamp(0.0, 2.0).toDouble(),
          min: 0,
          max: 2,
          divisions: 20,
          label: _delay.toStringAsFixed(2),
          onChanged: (v) => setState(() => _delay = double.parse(v.toStringAsFixed(2))),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Tự khởi động backend khi mở app'),
          value: _autoStart,
          onChanged: (v) => setState(() => _autoStart = v),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 12,
          runSpacing: 8,
          children: <Widget>[
            FilledButton(onPressed: () => _save(), child: const Text('Lưu')),
            OutlinedButton(onPressed: () => _save(restart: true), child: const Text('Lưu và khởi động lại backend')),
          ],
        ),
        const Divider(height: 40),
        Text('Backend', style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Text('Trạng thái: ${_stateLabel(app.backendState)}'),
        if (app.backendMessage.isNotEmpty)
          Padding(padding: const EdgeInsets.only(top: 4), child: Text(app.backendMessage)),
        const SizedBox(height: 12),
        Wrap(
          spacing: 12,
          children: <Widget>[
            FilledButton.tonal(
              onPressed: app.backendState == BackendState.starting ? null : () => app.restartBackend(),
              child: Text(app.ready ? 'Khởi động lại' : 'Khởi động'),
            ),
            OutlinedButton(
              onPressed: (app.backendState == BackendState.ready || app.backendState == BackendState.starting)
                  ? () => app.stopBackend()
                  : null,
              child: const Text('Dừng'),
            ),
          ],
        ),
        const SizedBox(height: 16),
        Text('Nhật ký backend', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        Container(
          width: double.infinity,
          constraints: const BoxConstraints(maxHeight: 260),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(8),
          ),
          child: SingleChildScrollView(
            child: SelectableText(
              logText,
              style: const TextStyle(fontFamily: 'Consolas', fontFamilyFallback: <String>['Menlo', 'monospace'], fontSize: 12),
            ),
          ),
        ),
      ],
    );
  }
}
