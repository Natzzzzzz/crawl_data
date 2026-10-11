import 'package:flutter/widgets.dart';

import 'app_state.dart';

/// Đưa AppController xuống cây widget; widget gọi `of` sẽ tự rebuild khi state đổi.
class AppScope extends InheritedNotifier<AppController> {
  const AppScope({super.key, required AppController controller, required super.child}) : super(notifier: controller);

  /// Dùng trong build(): đăng ký rebuild khi state đổi.
  static AppController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'Không tìm thấy AppScope trên cây widget');
    return scope!.notifier!;
  }

  /// Dùng trong callback (onPressed...): không đăng ký rebuild.
  static AppController read(BuildContext context) {
    final scope = context.getInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'Không tìm thấy AppScope trên cây widget');
    return scope!.notifier!;
  }
}
