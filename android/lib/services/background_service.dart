import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:workmanager/workmanager.dart';

const vaBackgroundTask = 'full_time_va_priority_check';

final FlutterLocalNotificationsPlugin notifications = FlutterLocalNotificationsPlugin();

@pragma('vm:entry-point')
void callbackDispatcher() {
  Workmanager().executeTask((taskName, inputData) async {
    if (taskName != vaBackgroundTask) return true;
    const storage = FlutterSecureStorage(aOptions: AndroidOptions());
    final base = await storage.read(key: 'server_url');
    final token = await storage.read(key: 'device_token');
    if (base == null || token == null) return true;
    try {
      final response = await http.get(
        Uri.parse('$base/api/autopilot/briefing'),
        headers: {'Authorization': 'Bearer $token', 'Accept': 'application/json'},
      ).timeout(const Duration(seconds: 30));
      if (response.statusCode != 200) return true;
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final needsYou = (data['needs_you'] as List? ?? const []);
      if (needsYou.isEmpty) return true;

      final paymentApprovals = needsYou.where((item) => item is Map && item['type'] == 'payment_authorization').length;
      final taskApprovals = needsYou.where((item) => item is Map && item['type'] == 'task_approval').length;
      final failures = needsYou.where((item) => item is Map && item['type'] == 'autopilot_exception').length;
      final summary = <String>[
        if (paymentApprovals > 0) '$paymentApprovals payment authorization${paymentApprovals == 1 ? '' : 's'}',
        if (taskApprovals > 0) '$taskApprovals approval${taskApprovals == 1 ? '' : 's'}',
        if (failures > 0) '$failures Autopilot exception${failures == 1 ? '' : 's'}',
      ];
      await notifications.show(
        id: 1001,
        title: 'Full-Time VA needs you',
        body: summary.isEmpty ? '${needsYou.length} exception${needsYou.length == 1 ? '' : 's'} require attention.' : summary.join(' · '),
        notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails(
            'va_priority',
            'VA priority alerts',
            channelDescription: 'Only exceptions that require human attention',
            importance: Importance.high,
            priority: Priority.high,
          ),
        ),
      );
    } catch (_) {
      return true;
    }
    return true;
  });
}

Future<void> initializeBackgroundService() async {
  const android = AndroidInitializationSettings('@mipmap/ic_launcher');
  await notifications.initialize(
    settings: const InitializationSettings(android: android),
  );
  final androidPlugin = notifications.resolvePlatformSpecificImplementation<
      AndroidFlutterLocalNotificationsPlugin>();
  await androidPlugin?.requestNotificationsPermission();
  await Workmanager().initialize(callbackDispatcher);
  await Workmanager().registerPeriodicTask(
    'full_time_va_priority_check_unique',
    vaBackgroundTask,
    frequency: const Duration(minutes: 15),
    existingWorkPolicy: ExistingPeriodicWorkPolicy.update,
    constraints: Constraints(networkType: NetworkType.connected),
  );
}
