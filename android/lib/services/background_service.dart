import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:workmanager/workmanager.dart';

const vaBackgroundTask = 'full_time_va_priority_check';
const _dailyBriefingDayKey = 'last_va_daily_briefing_day';
const _prioritySignatureKey = 'last_va_priority_signature';

final FlutterLocalNotificationsPlugin notifications = FlutterLocalNotificationsPlugin();

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

      // Priority alerts are event-driven rather than repeated every 15 minutes. The same
      // unresolved exception remains visible in Today and the daily briefing, but it does
      // not keep buzzing the phone unless the exception set changes.
      final signature = _prioritySignature(needsYou);
      final previousSignature = await storage.read(key: _prioritySignatureKey) ?? '';
      if (needsYou.isEmpty) {
        if (previousSignature.isNotEmpty) {
          await storage.delete(key: _prioritySignatureKey);
        }
      } else if (signature.isNotEmpty && signature != previousSignature) {
        final paymentApprovals = needsYou
            .where((item) => item is Map && item['type'] == 'payment_authorization')
            .length;
        final taskApprovals = needsYou
            .where((item) => item is Map && item['type'] == 'task_approval')
            .length;
        final failures = needsYou
            .where((item) => item is Map && item['type'] == 'autopilot_exception')
            .length;
        final providerAuth = needsYou
            .where((item) => item is Map && item['type'] == 'provider_authorization')
            .length;
        final funding = needsYou
            .where((item) => item is Map && item['type'] == 'funding_required')
            .length;
        final summary = <String>[
          if (paymentApprovals > 0)
            '$paymentApprovals payment authorization${paymentApprovals == 1 ? '' : 's'}',
          if (taskApprovals > 0)
            '$taskApprovals approval${taskApprovals == 1 ? '' : 's'}',
          if (providerAuth > 0)
            '$providerAuth provider authorization${providerAuth == 1 ? '' : 's'}',
          if (funding > 0)
            '$funding funding issue${funding == 1 ? '' : 's'}',
          if (failures > 0)
            '$failures Autopilot exception${failures == 1 ? '' : 's'}',
        ];
        await notifications.show(
          id: 1001,
          title: 'Full-Time VA needs you',
          body: summary.isEmpty
              ? '${needsYou.length} exception${needsYou.length == 1 ? '' : 's'} require attention.'
              : summary.join(' · '),
          notificationDetails: const NotificationDetails(
            android: AndroidNotificationDetails(
              'va_priority',
              'VA priority alerts',
              channelDescription: 'Only new or changed exceptions that require human attention',
              importance: Importance.high,
              priority: Priority.high,
            ),
          ),
        );
        await storage.write(key: _prioritySignatureKey, value: signature);
      }

      // Daily briefing: exactly once per server-local day after the configured delivery
      // hour. The backend decides readiness in Europe/Brussels (or the configured VA
      // timezone), so a phone timezone mismatch cannot shift the briefing window.
      final briefingDate = '${data['briefing_date'] ?? ''}';
      final notification = data['notification'];
      if (briefingDate.isNotEmpty && notification is Map) {
        final enabled = notification['enabled'] == true;
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
                'VA daily briefing',
                channelDescription: 'One concise daily summary of mail, money, calendar and VA activity',
                importance: Importance.defaultImportance,
                priority: Priority.defaultPriority,
              ),
            ),
          );
          await storage.write(key: _dailyBriefingDayKey, value: briefingDate);
        }
      }
    } catch (_) {
      // Background delivery is best-effort. The next WorkManager run retries without
      // converting a phone notification failure into a backend/Autopilot failure.
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
