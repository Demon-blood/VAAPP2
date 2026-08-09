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
        Uri.parse('$base/api/dashboard'),
        headers: {'Authorization': 'Bearer $token', 'Accept': 'application/json'},
      ).timeout(const Duration(seconds: 30));
      if (response.statusCode != 200) return true;
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final actions = (data['action_emails'] as num?)?.toInt() ?? 0;
      final tasks = (data['open_tasks'] as num?)?.toInt() ?? 0;
      final bills = (data['unpaid_bills'] as num?)?.toInt() ?? 0;
      final paymentActions = (data['payments_requiring_action'] as num?)?.toInt() ?? 0;
      if (actions + tasks + bills + paymentActions > 0) {
        await notifications.show(
          id: 1001,
          title: 'Full-Time VA action centre',
          body: '$actions email actions · $tasks tasks · $bills unpaid bills · $paymentActions payment approvals.',
          notificationDetails: const NotificationDetails(
            android: AndroidNotificationDetails(
              'va_priority',
              'VA priority alerts',
              channelDescription: 'Urgent exceptions found by Full-Time VA',
              importance: Importance.high,
              priority: Priority.high,
            ),
          ),
        );
      }
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
