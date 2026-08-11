import 'package:flutter/services.dart';

class DeviceBridge {
  static const MethodChannel _channel = MethodChannel('full_time_va/device');

  static Future<void> syncCredentials({required String? serverUrl, required String? deviceToken}) async {
    await _channel.invokeMethod<void>('syncCredentials', {
      'serverUrl': serverUrl,
      'deviceToken': deviceToken,
    });
  }

  static Future<void> clearCredentials() async {
    await _channel.invokeMethod<void>('clearCredentials');
  }

  static Future<Map<String, dynamic>> communicationStatus() async {
    final raw = await _channel.invokeMapMethod<String, dynamic>('getCommunicationStatus');
    return Map<String, dynamic>.from(raw ?? const {});
  }

  static Future<void> requestRuntimePermissions() => _channel.invokeMethod<void>('requestRuntimePermissions');
  static Future<void> requestSmsRole() => _channel.invokeMethod<void>('requestSmsRole');
  static Future<void> requestCallScreeningRole() => _channel.invokeMethod<void>('requestCallScreeningRole');
  static Future<void> openNotificationAccess() => _channel.invokeMethod<void>('openNotificationAccess');

  static Future<bool> sendSms({required String target, required String text}) async =>
      await _channel.invokeMethod<bool>('sendSms', {'target': target, 'text': text}) ?? false;

  static Future<Map<String, dynamic>> syncRecentCommunications() async {
    final raw = await _channel.invokeMapMethod<String, dynamic>('syncRecentCommunications');
    return Map<String, dynamic>.from(raw ?? const {});
  }

  static Future<bool> syncCallPolicy() async {
    return (await _channel.invokeMethod<bool>('syncCallPolicy')) ?? false;
  }
}
