// Kiểu dữ liệu khớp với docs/API.md của backend.

int _int(Object? v, [int fallback = 0]) => v is num ? v.toInt() : fallback;
double _double(Object? v, [double fallback = 0]) => v is num ? v.toDouble() : fallback;
String _str(Object? v, [String fallback = '']) => v is String ? v : fallback;
DateTime? _date(Object? v) => v is String ? DateTime.tryParse(v)?.toLocal() : null;

class StoryInfo {
  const StoryInfo({
    required this.site,
    required this.url,
    required this.title,
    required this.chaptersFound,
    required this.maxChapterNumber,
    required this.announcedTotal,
    required this.warnings,
  });

  final String site;
  final String url;
  final String title;
  final int chaptersFound;
  final int maxChapterNumber;
  final int? announcedTotal;
  final List<String> warnings;

  factory StoryInfo.fromJson(Map<String, dynamic> j) => StoryInfo(
        site: _str(j['site']),
        url: _str(j['url']),
        title: _str(j['title']),
        chaptersFound: _int(j['chapters_found']),
        maxChapterNumber: _int(j['max_chapter_number']),
        announcedTotal: j['announced_total'] is num ? _int(j['announced_total']) : null,
        warnings: j['warnings'] is List
            ? (j['warnings'] as List).map((e) => e.toString()).toList()
            : const <String>[],
      );
}

class JobResult {
  const JobResult({
    required this.outputFile,
    required this.requested,
    required this.saved,
    required this.failedNumbers,
    required this.chars,
    required this.seconds,
    required this.cancelled,
  });

  final String? outputFile;
  final int requested;
  final int saved;
  final List<int> failedNumbers;
  final int chars;
  final double seconds;
  final bool cancelled;

  factory JobResult.fromJson(Map<String, dynamic> j) => JobResult(
        outputFile: j['output_file'] is String ? j['output_file'] as String : null,
        requested: _int(j['requested']),
        saved: _int(j['saved']),
        failedNumbers: j['failed_numbers'] is List
            ? (j['failed_numbers'] as List).whereType<num>().map((e) => e.toInt()).toList()
            : const <int>[],
        chars: _int(j['chars']),
        seconds: _double(j['seconds']),
        cancelled: j['cancelled'] == true,
      );
}

class Job {
  const Job({
    required this.id,
    required this.url,
    required this.site,
    required this.title,
    required this.start,
    required this.end,
    required this.workers,
    required this.delay,
    required this.outDir,
    required this.outputFile,
    required this.status,
    required this.done,
    required this.total,
    required this.result,
    required this.error,
    required this.createdAt,
    required this.startedAt,
    required this.finishedAt,
  });

  final String id;
  final String url;
  final String site;
  final String title;
  final int start;
  final int end;
  final int workers;
  final double delay;
  final String outDir;
  final String outputFile;
  final String status; // queued | running | done | failed | cancelled | interrupted
  final int done;
  final int total;
  final JobResult? result;
  final String? error;
  final DateTime? createdAt;
  final DateTime? startedAt;
  final DateTime? finishedAt;

  bool get isActive => status == 'queued' || status == 'running';
  bool get isFinished => !isActive;

  /// null = chưa biết tổng số chương (thanh tiến độ chạy vô định).
  double? get progress => total > 0 ? done / total : null;

  String get displayName {
    final t = title.trim();
    if (t.isNotEmpty) return t;
    final segments = (Uri.tryParse(url)?.pathSegments ?? const <String>[]).where((s) => s.isNotEmpty).toList();
    return segments.isNotEmpty ? segments.last : url;
  }

  factory Job.fromJson(Map<String, dynamic> j) => Job(
        id: _str(j['id']),
        url: _str(j['url']),
        site: _str(j['site']),
        title: _str(j['title']),
        start: _int(j['start']),
        end: _int(j['end']),
        workers: _int(j['workers']),
        delay: _double(j['delay']),
        outDir: _str(j['out_dir']),
        outputFile: _str(j['output_file']),
        status: _str(j['status'], 'queued'),
        done: _int(j['done']),
        total: _int(j['total']),
        result: j['result'] is Map<String, dynamic> ? JobResult.fromJson(j['result'] as Map<String, dynamic>) : null,
        error: j['error'] is String ? j['error'] as String : null,
        createdAt: _date(j['created_at']),
        startedAt: _date(j['started_at']),
        finishedAt: _date(j['finished_at']),
      );

  Job copyWith({String? status, int? done, int? total, JobResult? result, String? error}) => Job(
        id: id,
        url: url,
        site: site,
        title: title,
        start: start,
        end: end,
        workers: workers,
        delay: delay,
        outDir: outDir,
        outputFile: outputFile,
        status: status ?? this.status,
        done: done ?? this.done,
        total: total ?? this.total,
        result: result ?? this.result,
        error: error ?? this.error,
        createdAt: createdAt,
        startedAt: startedAt,
        finishedAt: finishedAt,
      );
}

/// Sự kiện SSE: status | start | progress | end.
class JobEvent {
  const JobEvent({required this.seq, required this.type, required this.data});

  final int seq;
  final String type;
  final Map<String, dynamic> data;

  factory JobEvent.fromJson(Map<String, dynamic> j) =>
      JobEvent(seq: _int(j['seq'], -1), type: _str(j['type']), data: j);

  int get done => _int(data['done']);
  int get total => _int(data['total']);
  String get status => _str(data['status']);
  String? get error => data['error'] is String ? data['error'] as String : null;
  JobResult? get result =>
      data['result'] is Map<String, dynamic> ? JobResult.fromJson(data['result'] as Map<String, dynamic>) : null;
}
