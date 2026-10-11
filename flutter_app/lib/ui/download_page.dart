import 'package:flutter/material.dart';

import '../data/api_client.dart';
import '../data/models.dart';
import '../state/app_scope.dart';

class DownloadPage extends StatefulWidget {
  const DownloadPage({super.key, required this.onJobStarted});

  final VoidCallback onJobStarted;

  @override
  State<DownloadPage> createState() => _DownloadPageState();
}

class _DownloadPageState extends State<DownloadPage> {
  final TextEditingController _url = TextEditingController();
  final TextEditingController _start = TextEditingController(text: '1');
  final TextEditingController _end = TextEditingController();
  final TextEditingController _title = TextEditingController();

  int? _workers; // null = dùng mặc định trong Cài đặt
  double? _delay;
  bool _starting = false;

  @override
  void dispose() {
    _url.dispose();
    _start.dispose();
    _end.dispose();
    _title.dispose();
    super.dispose();
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _analyze() async {
    final app = AppScope.read(context);
    final url = _url.text.trim();
    if (url.isEmpty) {
      _toast('Dán URL truyện trước.');
      return;
    }
    await app.analyze(url);
    if (!mounted) return;
    final info = app.info;
    if (info != null) {
      setState(() {
        _start.text = '1';
        _end.text = '${info.maxChapterNumber}';
        _title.text = info.title;
      });
    }
  }

  Future<void> _startJob() async {
    final app = AppScope.read(context);
    final url = _url.text.trim();
    final start = int.tryParse(_start.text.trim());
    final end = int.tryParse(_end.text.trim());

    if (url.isEmpty) {
      _toast('Dán URL truyện trước.');
      return;
    }
    if (start == null || start < 1) {
      _toast('"Từ chương" phải là số nguyên >= 1.');
      return;
    }
    if (end == null || end < start) {
      _toast('"Đến chương" phải là số nguyên >= "Từ chương".');
      return;
    }

    setState(() => _starting = true);
    try {
      await app.startJob(
        url: url,
        start: start,
        end: end,
        workers: _workers ?? app.settings.workers,
        delay: _delay ?? app.settings.delay,
        outDir: app.settings.outDir,
        title: _title.text.trim(),
      );
      if (mounted) widget.onJobStarted();
    } on ApiException catch (e) {
      _toast(e.message);
    } catch (e) {
      _toast('Không bắt đầu được: $e');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final info = app.info;
    final workers = _workers ?? app.settings.workers;
    final delay = _delay ?? app.settings.delay;

    return ListView(
      padding: const EdgeInsets.all(24),
      children: <Widget>[
        Text('Tải truyện', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: TextField(
                controller: _url,
                decoration: const InputDecoration(
                  labelText: 'URL trang truyện',
                  hintText: 'https://toctruyen.net/truyen/…',
                  border: OutlineInputBorder(),
                ),
                onSubmitted: (_) => _analyze(),
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              height: 56,
              child: FilledButton.icon(
                onPressed: (app.ready && !app.analyzing) ? _analyze : null,
                icon: app.analyzing
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.search),
                label: Text(app.analyzing ? 'Đang phân tích…' : 'Phân tích'),
              ),
            ),
          ],
        ),
        if (app.analyzing)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text('Có thể mất tới ~30 giây nếu mạng chậm.'),
          ),
        if (app.analyzeError != null) _ErrorBox(message: app.analyzeError!),
        if (info != null) ...<Widget>[
          const SizedBox(height: 16),
          _InfoCard(info: info),
        ],
        const SizedBox(height: 24),
        TextField(
          controller: _title,
          decoration: const InputDecoration(
            labelText: 'Tên hiển thị (tuỳ chọn)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 16),
        Row(
          children: <Widget>[
            Expanded(
              child: TextField(
                controller: _start,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Từ chương', border: OutlineInputBorder()),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: TextField(
                controller: _end,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Đến chương', border: OutlineInputBorder()),
              ),
            ),
          ],
        ),
        if (info != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Wrap(
              spacing: 8,
              children: <Widget>[
                ActionChip(
                  label: const Text('Tất cả'),
                  onPressed: () => setState(() {
                    _start.text = '1';
                    _end.text = '${info.maxChapterNumber}';
                  }),
                ),
                ActionChip(
                  label: const Text('50 chương đầu'),
                  onPressed: () => setState(() {
                    _start.text = '1';
                    _end.text = '${info.maxChapterNumber < 50 ? info.maxChapterNumber : 50}';
                  }),
                ),
              ],
            ),
          ),
        const SizedBox(height: 16),
        Text('Số luồng tải song song: $workers'),
        Slider(
          value: workers.toDouble(),
          min: 1,
          max: 8,
          divisions: 7,
          label: '$workers',
          onChanged: (v) => setState(() => _workers = v.round()),
        ),
        Text('Nghỉ giữa các request: tối đa ${delay.toStringAsFixed(2)} giây'),
        Slider(
          value: delay.clamp(0.0, 2.0).toDouble(),
          min: 0,
          max: 2,
          divisions: 20,
          label: delay.toStringAsFixed(2),
          onChanged: (v) => setState(() => _delay = double.parse(v.toStringAsFixed(2))),
        ),
        const SizedBox(height: 8),
        Text('Lưu vào: ${app.settings.outDir}  (đổi trong Cài đặt)', style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 16),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton.icon(
            onPressed: (app.ready && !_starting) ? _startJob : null,
            icon: _starting
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.play_arrow),
            label: const Text('Bắt đầu tải'),
          ),
        ),
      ],
    );
  }
}

class _InfoCard extends StatelessWidget {
  const _InfoCard({required this.info});

  final StoryInfo info;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final announced = info.announcedTotal;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(info.title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('Site: ${info.site}'),
            Text('Tìm thấy ${info.chaptersFound} chương (số chương cao nhất: ${info.maxChapterNumber})'
                '${announced != null ? '  •  site công bố: $announced' : ''}'),
            for (final w in info.warnings)
              Container(
                width: double.infinity,
                margin: const EdgeInsets.only(top: 8),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: scheme.tertiaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text('⚠ $w', style: TextStyle(color: scheme.onTertiaryContainer)),
              ),
          ],
        ),
      ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  const _ErrorBox({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: scheme.errorContainer, borderRadius: BorderRadius.circular(8)),
      child: Text(message, style: TextStyle(color: scheme.onErrorContainer)),
    );
  }
}
