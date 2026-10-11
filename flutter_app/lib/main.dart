import 'dart:ui';

import 'package:flutter/material.dart';

import 'state/app_scope.dart';
import 'state/app_state.dart';
import 'ui/home_shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const CrawlApp());
}

class CrawlApp extends StatefulWidget {
  const CrawlApp({super.key});

  @override
  State<CrawlApp> createState() => _CrawlAppState();
}

class _CrawlAppState extends State<CrawlApp> {
  final AppController _controller = AppController();
  late final AppLifecycleListener _lifecycle;

  @override
  void initState() {
    super.initState();
    _controller.init();
    // Đóng app thì dừng backend. Nếu app bị kill đột ngột, backend tự thoát nhờ --exit-on-stdin-eof.
    _lifecycle = AppLifecycleListener(
      onExitRequested: () async {
        await _controller.shutdown();
        return AppExitResponse.exit;
      },
      onDetach: () {
        _controller.shutdown();
      },
    );
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _controller.shutdown();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppScope(
      controller: _controller,
      child: MaterialApp(
        title: 'Tải truyện',
        debugShowCheckedModeBanner: false,
        themeMode: ThemeMode.system,
        theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
        darkTheme: ThemeData(
            colorSchemeSeed: Colors.indigo,
            brightness: Brightness.dark,
            useMaterial3: true),
        home: const HomeShell(),
      ),
    );
  }
}
