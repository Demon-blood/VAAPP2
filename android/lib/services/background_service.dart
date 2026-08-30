import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:workmanager/workmanager.dart';

const vaBackgroundTask = 'full_time_va_priority_check';
const _dailyBriefingDayKey = 'last_va_daily_briefing_day'; // legacy migration key
const _briefingPeriodKey = 'last_va_briefing_period';
const _briefingPendingAckKey = 'pending_va_briefing_ack_key';
const _briefingPendingAckTokenKey = 'pending_va_briefing_ack_token';
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

Future<bool> _ackBriefingDelivery({
  required String base,
  required String token,
  required String deliveryKey,
  required String deliveryToken,
}) async {
  if (deliveryKey.isEmpty || deliveryToken.isEmpty) return false;
  try {
    final response = await http.post(
      Uri.parse('$base/api/autopilot/briefing/deliveries'),
      headers: {
        'Authorization': 'Bearer $token',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
      },
      body: jsonEncode({
        'delivery_key': deliveryKey,
        'delivery_token': deliveryToken,
      }),
    ).timeout(const Duration(seconds: 15));
    return response.statusCode >= 200 && response.statusCode < 300;
  } catch (_) {
    // The notification was shown, but delivery could not be proven to the backend.
    // Keep the durable local proof pending so a later poll can retry silently.
    return false;
  }
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
      // A successful OS notification can outlive a transient backend/network failure.
      // Retry that proof before fetching the next briefing so its window can advance.
      final pendingAckKey = await storage.read(key: _briefingPendingAckKey) ?? '';
      final pendingAckToken = await storage.read(key: _briefingPendingAckTokenKey) ?? '';
      if (pendingAckKey.isNotEmpty && pendingAckToken.isNotEmpty) {
        final acknowledged = await _ackBriefingDelivery(
          base: base,
          token: token,
          deliveryKey: pendingAckKey,
          deliveryToken: pendingAckToken,
        );
        if (acknowledged) {
          await storage.delete(key: _briefingPendingAckKey);
          await storage.delete(key: _briefingPendingAckTokenKey);
        }
      }

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
            final deliveryToken = '${period['delivery_token'] ?? ''}';
            if (deliveryToken.isNotEmpty) {
              // Persist proof-of-show intent before the network ACK. If the ACK fails, the
              // next poll retries silently without showing the same notification again.
              await storage.write(key: _briefingPendingAckKey, value: key);
              await storage.write(key: _briefingPendingAckTokenKey, value: deliveryToken);
              final acknowledged = await _ackBriefingDelivery(
                base: base,
                token: token,
                deliveryKey: key,
                deliveryToken: deliveryToken,
              );
              if (acknowledged) {
                await storage.delete(key: _briefingPendingAckKey);
                await storage.delete(key: _briefingPendingAckTokenKey);
              }
            }
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
