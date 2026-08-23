import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:workmanager/workmanager.dart';

const vaBackgroundTask = 'full_time_va_priority_check';
const _dailyBriefingDayKey = 'last_va_daily_briefing_day'; // legacy migration key
const _briefingPeriodKey = 'last_va_briefing_period';
const _prioritySignatureKey = 'last_va_priority_signature';

final FlutterLocalNotificationsPlugin notifications = FlutterLocalNotificationsPlugin();

bool _isImmediateInterrupt(dynamic raw) {
  if (raw is! Map) return false;
  return raw['interrupt'] == true;
}

String _prioritySignature(List<dynamic> items) {
  final normalized = items
      .whereType<Map>()
      .map((item) => [
            '${item['type'] ?? ''}',
            '${item['id'] ?? ''}',
            '${item['title'] ?? ''}',
            '${item['detail'] ?? ''}',
          ].join('|'))
      .toList()
    ..sort();
  return normalized.join('||');
}

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
      final needsYou = data['needs_you'] as List? ?? const [];
      final interrupts = needsYou.where(_isImmediateInterrupt).toList();

      // The 15-minute check is intentionally silent for routine work. Only a backend
      // item explicitly classified as an immediate interrupt is allowed to buzz.
      final signature = _prioritySignature(interrupts);
      final previousSignature = await storage.read(key: _prioritySignatureKey) ?? '';
      if (interrupts.isEmpty) {
        if (previousSignature.isNotEmpty) await storage.delete(key: _prioritySignatureKey);
      } else if (signature.isNotEmpty && signature != previousSignature) {
        final first = interrupts.first as Map;
        await notifications.show(
          id: 1001,
          title: '${first['title'] ?? 'Full-Time VA needs you now'}',
          body: '${first['detail'] ?? 'A genuine urgent decision is blocking active work.'}',
          notificationDetails: const NotificationDetails(
            android: AndroidNotificationDetails(
              'va_priority',
              'VA priority alerts',
              channelDescription: 'Only genuine urgent human interruptions',
              importance: Importance.high,
              priority: Priority.high,
            ),
          ),
        );
        await storage.write(key: _prioritySignatureKey, value: signature);
      }

      final briefingDate = '${data['briefing_date'] ?? ''}';
      final notification = data['notification'];
      if (briefingDate.isNotEmpty && notification is Map) {
        final enabled = notification['enabled'] == true;
        final periods = (notification['periods'] as List? ?? const []).whereType<Map>().toList();
        if (enabled && periods.isNotEmpty) {
          final ready = periods.where((row) => row['enabled'] == true && row['ready'] == true).toList()
            ..sort((a, b) => ((a['hour_local'] as num?)?.toInt() ?? 0)
                .compareTo((b['hour_local'] as num?)?.toInt() ?? 0));
          final lastKey = await storage.read(key: _briefingPeriodKey) ?? '';
          final due = ready.where((row) => '${row['delivery_key'] ?? ''}' != lastKey).toList();
          if (due.isNotEmpty) {
            final period = due.last;
            final name = '${period['name'] ?? 'daily'}';
            final key = '${period['delivery_key'] ?? '$briefingDate:$name'}';
            await notifications.show(
              id: 1002,
              title: '${name[0].toUpperCase()}${name.substring(1)} VA briefing',
              body: '${notification['body'] ?? data['summary_text'] ?? 'Your VA briefing is ready.'}',
              notificationDetails: const NotificationDetails(
                android: AndroidNotificationDetails(
                  'va_daily_briefing',
                  'VA briefings',
                  channelDescription: 'Scheduled human-style VA briefings',
                  importance: Importance.defaultImportance,
                  priority: Priority.defaultPriority,
                ),
              ),
            );
            await storage.write(key: _briefingPeriodKey, value: key);
            // Keep the legacy key updated so downgrades do not duplicate the evening briefing.
            await storage.write(key: _dailyBriefingDayKey, value: briefingDate);
          }
        } else {
          // Compatibility with an older backend that exposes only one daily ready flag.
          final ready = notification['ready'] == true;
          final lastDay = await storage.read(key: _dailyBriefingDayKey) ?? '';
          if (enabled && ready && lastDay != briefingDate) {
            await notifications.show(
              id: 1002,
              title: '${notification['title'] ?? 'Your Full-Time VA daily briefing'}',
              body: '${notification['body'] ?? data['summary_text'] ?? 'Your daily VA briefing is ready.'}',
              notificationDetails: const NotificationDetails(
                android: AndroidNotificationDetails(
                  'va_daily_briefing',
                  'VA briefings',
                  channelDescription: 'Scheduled human-style VA briefings',
                  importance: Importance.defaultImportance,
                  priority: Priority.defaultPriority,
                ),
              ),
            );
            await storage.write(key: _dailyBriefingDayKey, value: briefingDate);
          }
        }
      }
    } catch (_) {
      // Best-effort phone delivery. Backend ownership/state is unaffected.
      return true;
    }
    return true;
  });
}

Future<void> initializeBackgroundService() async {
  const android = AndroidInitializationSettings('@mipmap/ic_launcher');
  await notifications.initialize(settings: const InitializationSettings(android: android));
  final androidPlugin = notifications.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>();
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
