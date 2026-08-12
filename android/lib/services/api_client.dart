import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

class ApiException implements Exception {
  ApiException(this.message, {this.statusCode, this.method, this.path});

  final String message;
  final int? statusCode;
  final String? method;
  final String? path;

  @override
  String toString() => message;
}

class ApiClient {
  ApiClient({FlutterSecureStorage? storage})
      : storage = storage ?? const FlutterSecureStorage(aOptions: AndroidOptions());

  final FlutterSecureStorage storage;

  Future<String?> get serverUrl => storage.read(key: 'server_url');
  Future<String?> get deviceToken => storage.read(key: 'device_token');

  Future<void> pair({
    required String serverUrl,
    required String pairingSecret,
    required String deviceName,
  }) async {
    final normalized = serverUrl.trim().replaceAll(RegExp(r'/+$'), '');
    final response = await http
        .post(
          Uri.parse('$normalized/api/pair'),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            'device_name': deviceName.trim(),
            'pairing_secret': pairingSecret,
          }),
        )
        .timeout(const Duration(seconds: 30));
    final body = _decode(response);
    if (response.statusCode >= 300) {
      throw ApiException(
        _errorMessage(body, fallback: 'Pairing failed.'),
        statusCode: response.statusCode,
        method: 'POST',
        path: '/api/pair',
      );
    }
    await storage.write(key: 'server_url', value: normalized);
    await storage.write(key: 'device_token', value: body['device_token'] as String);
  }

  Future<void> disconnect() async {
    await storage.delete(key: 'device_token');
    await storage.delete(key: 'server_url');
  }

  Future<dynamic> getJson(String path) => _request('GET', path);

  Future<dynamic> getPublicJson(String path) => _request('GET', path, authenticated: false);

  Future<dynamic> postJson(String path, [Map<String, dynamic>? body]) =>
      _request('POST', path, body: body);

  Future<dynamic> putJson(String path, Map<String, dynamic> body) =>
      _request('PUT', path, body: body);

  Future<dynamic> patchJson(String path, [Map<String, dynamic>? body]) =>
      _request('PATCH', path, body: body);

  Future<dynamic> deleteJson(String path) => _request('DELETE', path);

  Future<dynamic> postFiles(
    String path,
    List<String> filePaths, {
    Map<String, String> fields = const {},
    String fieldName = 'files',
  }) async {
    final base = await serverUrl;
    final token = await deviceToken;
    if (base == null || token == null) {
      throw ApiException('This device is not paired with a VA server.', method: 'POST', path: path);
    }
    final request = http.MultipartRequest('POST', Uri.parse('$base$path'))
      ..headers['Authorization'] = 'Bearer $token'
      ..headers['Accept'] = 'application/json'
      ..fields.addAll(fields);
    for (final filePath in filePaths) {
      request.files.add(await http.MultipartFile.fromPath(fieldName, filePath));
    }
    late http.Response response;
    try {
      final streamed = await request.send().timeout(const Duration(minutes: 3));
      response = await http.Response.fromStream(streamed);
    } catch (requestError) {
      throw ApiException(
        'Could not upload financial history to the VA server: $requestError',
        method: 'POST',
        path: path,
      );
    }
    final decoded = _decode(response);
    if (response.statusCode == 401) {
      await disconnect();
    }
    if (response.statusCode >= 300) {
      throw ApiException(
        _errorMessage(decoded, fallback: 'The server rejected the statement import.'),
        statusCode: response.statusCode,
        method: 'POST',
        path: path,
      );
    }
    return decoded;
  }


  Future<dynamic> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool authenticated = true,
  }) async {
    final base = await serverUrl;
    final token = authenticated ? await deviceToken : null;
    if (base == null || (authenticated && token == null)) {
      throw ApiException('This device is not paired with a VA server.', method: method, path: path);
    }
    final uri = Uri.parse('$base$path');
    final headers = <String, String>{
      if (authenticated) 'Authorization': 'Bearer $token',
      'Accept': 'application/json',
      if (body != null) 'Content-Type': 'application/json',
    };
    late http.Response response;
    try {
      switch (method) {
        case 'GET':
          response = await http.get(uri, headers: headers).timeout(const Duration(seconds: 40));
          break;
        case 'POST':
          response = await http
              .post(uri, headers: headers, body: body == null ? null : jsonEncode(body))
              .timeout(const Duration(seconds: 90));
          break;
        case 'PUT':
          response = await http
              .put(uri, headers: headers, body: jsonEncode(body))
              .timeout(const Duration(seconds: 60));
          break;
        case 'PATCH':
          response = await http
              .patch(uri, headers: headers, body: body == null ? null : jsonEncode(body))
              .timeout(const Duration(seconds: 60));
          break;
        case 'DELETE':
          response = await http.delete(uri, headers: headers).timeout(const Duration(seconds: 60));
          break;
        default:
          throw ApiException('Unsupported HTTP method: $method', method: method, path: path);
      }
    } on ApiException {
      rethrow;
    } catch (requestError) {
      throw ApiException(
        'Could not reach the VA server for $method $path: $requestError',
        method: method,
        path: path,
      );
    }
    final decoded = _decode(response);
    if (response.statusCode == 401 && authenticated) {
      await disconnect();
    }
    if (response.statusCode >= 300) {
      final fallback = response.statusCode == 404
          ? 'The connected backend does not provide $method $path. Redeploy the current backend version from the repository.'
          : 'The server rejected $method $path.';
      throw ApiException(
        _errorMessage(decoded, fallback: fallback),
        statusCode: response.statusCode,
        method: method,
        path: path,
      );
    }
    return decoded;
  }

  dynamic _decode(http.Response response) {
    if (response.body.trim().isEmpty) return <String, dynamic>{};
    try {
      return jsonDecode(utf8.decode(response.bodyBytes));
    } catch (_) {
      return {'detail': utf8.decode(response.bodyBytes)};
    }
  }

  String _errorMessage(dynamic body, {required String fallback}) {
    if (body is Map<String, dynamic>) {
      final detail = body['detail'];
      if (detail is String && detail.isNotEmpty && detail != 'Not Found') return detail;
      if (detail is List) return detail.map((item) => item.toString()).join('\n');
      final message = body['message'];
      if (message is String && message.isNotEmpty) return message;
    }
    return fallback;
  }
}
