import 'package:flutter/material.dart';

import '../state/app_scope.dart';
import '../state/app_state.dart';
import 'download_page.dart';
import 'jobs_page.dart';
import 'settings_page.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final active = app.activeJobs;

    return Scaffold(
      body: Row(
        children: <Widget>[
          NavigationRail(
            selectedIndex: _index,
            labelType: NavigationRailLabelType.all,
            onDestinationSelected: (i) => setState(() => _index = i),
            destinations: <NavigationRailDestination>[
              const NavigationRailDestination(
                icon: Icon(Icons.download_outlined),
                selectedIcon: Icon(Icons.download),
                label: Text('Tải truyện'),
              ),
              NavigationRailDestination(
                icon: Badge(
                  isLabelVisible: active > 0,
                  label: Text('$active'),
                  child: const Icon(Icons.list_alt_outlined),
                ),
                selectedIcon: Badge(
                  isLabelVisible: active > 0,
                  label: Text('$active'),
                  child: const Icon(Icons.list_alt),
                ),
                label: const Text('Công việc'),
              ),
              const NavigationRailDestination(
                icon: Icon(Icons.settings_outlined),
                selectedIcon: Icon(Icons.settings),
                label: Text('Cài đặt'),
              ),
            ],
          ),
          const VerticalDivider(width: 1),
          Expanded(
            child: Column(
              children: <Widget>[
                _BackendBanner(onOpenSettings: () => setState(() => _index = 2)),
                Expanded(
                  child: IndexedStack(
                    index: _index,
                    children: <Widget>[
                      DownloadPage(onJobStarted: () => setState(() => _index = 1)),
                      const JobsPage(),
                      const SettingsPage(),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _BackendBanner extends StatelessWidget {
  const _BackendBanner({required this.onOpenSettings});

  final VoidCallback onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final scheme = Theme.of(context).colorScheme;

    if (app.ready) return const SizedBox.shrink();

    if (app.backendState == BackendState.starting) {
      return Container(
        width: double.infinity,
        color: scheme.secondaryContainer,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          children: <Widget>[
            const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: 12),
            Text('Đang khởi động backend…', style: TextStyle(color: scheme.onSecondaryContainer)),
          ],
        ),
      );
    }

    final isError = app.backendState == BackendState.error;
    final message = app.backendMessage.isEmpty ? 'Backend chưa chạy.' : app.backendMessage;
    return Container(
      width: double.infinity,
      color: isError ? scheme.errorContainer : scheme.secondaryContainer,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: isError ? scheme.onErrorContainer : scheme.onSecondaryContainer),
            ),
          ),
          const SizedBox(width: 12),
          if (app.settings.backendDir.isNotEmpty)
            FilledButton(onPressed: app.startBackend, child: const Text('Khởi động backend')),
          const SizedBox(width: 8),
          TextButton(onPressed: onOpenSettings, child: const Text('Cài đặt')),
        ],
      ),
    );
  }
}
