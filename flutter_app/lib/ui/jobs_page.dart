import 'package:flutter/material.dart';

import '../data/api_client.dart';
import '../data/models.dart';
import '../state/app_scope.dart';
import '../state/app_state.dart';

class JobsPage extends StatelessWidget {
  const JobsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final jobs = app.jobs;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 24, 24, 8),
          child: Row(
            children: <Widget>[
              Expanded(child: Text('Công việc', style: Theme.of(context).textTheme.headlineSmall)),
              IconButton(
                tooltip: 'Tải lại danh sách',
                onPressed: app.ready ? () => app.refreshJobs() : null,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
        Expanded(
          child: jobs.isEmpty
              ? const Center(child: Text('Chưa có công việc nào.'))
              : ListView.separated(
                  padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
                  itemCount: jobs.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 12),
                  itemBuilder: (context, i) => _JobCard(job: jobs[i], app: app),
                ),
        ),
      ],
    );
  }
}

String _statusLabel(String status) {
  switch (status) {
    case 'queued':
      return 'Đang chờ';
    case 'running':
      return 'Đang tải';
    case 'done':
      return 'Hoàn tất';
    case 'failed':
      return 'Thất bại';
    case 'cancelled':
      return 'Đã huỷ';
    case 'interrupted':
      return 'Bị gián đoạn';
    default:
      return status;
  }
}

Color _statusColor(String status) {
  switch (status) {
    case 'done':
      return Colors.green.shade700;
    case 'failed':
      return Colors.red.shade700;
    case 'cancelled':
    case 'interrupted':
      return Colors.orange.shade800;
    default:
      return Colors.indigo.shade400;
  }
}

String _shortList(List<int> numbers) {
  const maxShown = 20;
  if (numbers.length <= maxShown) return numbers.join(', ');
  return '${numbers.take(maxShown).join(', ')}… (+${numbers.length - maxShown})';
}

class _JobCard extends StatelessWidget {
  const _JobCard({required this.job, required this.app});

  final Job job;
  final AppController app;

  Future<void> _run(BuildContext context, Future<void> Function() action) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Lỗi: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final result = job.result;
    final failed = result?.failedNumbers ?? const <int>[];
    final color = _statusColor(job.status);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(job.displayName, style: theme.textTheme.titleMedium, overflow: TextOverflow.ellipsis),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    border: Border.all(color: color),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(_statusLabel(job.status), style: TextStyle(color: color, fontWeight: FontWeight.w600)),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text('Chương ${job.start} → ${job.end}  •  ${job.workers} luồng', style: theme.textTheme.bodySmall),
            const SizedBox(height: 12),
            if (job.isActive) ...<Widget>[
              LinearProgressIndicator(value: job.progress),
              const SizedBox(height: 6),
              Text(job.total > 0 ? '${job.done}/${job.total} chương' : 'Đang chuẩn bị…'),
            ],
            if (job.isFinished && result != null) ...<Widget>[
              Text('Đã lưu ${result.saved}/${result.requested} chương  •  '
                  '${result.chars} ký tự  •  ${result.seconds.toStringAsFixed(1)} giây'),
              if (failed.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    'Thiếu ${failed.length} chương: ${_shortList(failed)}',
                    style: TextStyle(color: Colors.orange.shade800),
                  ),
                ),
            ],
            if (job.error != null && job.error!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(job.error!, style: TextStyle(color: Colors.red.shade700)),
              ),
            const SizedBox(height: 8),
            SelectableText(job.outputFile, style: theme.textTheme.bodySmall),
            const SizedBox(height: 8),
            Row(
              children: <Widget>[
                if (job.isActive)
                  OutlinedButton.icon(
                    onPressed: () => _run(context, () => app.cancelJob(job.id)),
                    icon: const Icon(Icons.stop),
                    label: const Text('Huỷ'),
                  ),
                if (job.isFinished) ...<Widget>[
                  OutlinedButton.icon(
                    onPressed: () => app.openFolder(job.outDir),
                    icon: const Icon(Icons.folder_open),
                    label: const Text('Mở thư mục'),
                  ),
                  const SizedBox(width: 8),
                  TextButton.icon(
                    onPressed: () => _run(context, () => app.deleteJob(job.id)),
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('Xoá khỏi danh sách'),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}
