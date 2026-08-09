import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;

class RenderWorkspace {
  const RenderWorkspace({required this.id, required this.name, this.email = ''});

  final String id;
  final String name;
  final String email;
}

class DeploymentResult {
  const DeploymentResult({
    required this.serverUrl,
    required this.pairingSecret,
    required this.serviceId,
    this.databaseId = '',
    this.deployId = '',
  });

  final String serverUrl;
  final String pairingSecret;
  final String serviceId;
  final String databaseId;
  final String deployId;
}

class MobileDeploymentService {
  static const _renderApi = 'https://api.render.com/v1';

  Future<List<RenderWorkspace>> listWorkspaces(String apiToken) async {
    final response = await http.get(
      Uri.parse('$_renderApi/owners?limit=100'),
      headers: _headers(apiToken),
    ).timeout(const Duration(seconds: 45));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception(_renderError(decoded, response.statusCode));
    }

    final rawItems = decoded is List
        ? decoded
        : decoded is Map
            ? (decoded['items'] ?? decoded['owners'] ?? decoded['data'] ?? const [])
            : const [];
    if (rawItems is! List) throw Exception('Render returned an unexpected workspace response.');

    final result = <RenderWorkspace>[];
    for (final rawItem in rawItems) {
      if (rawItem is! Map) continue;
      final wrapper = Map<String, dynamic>.from(rawItem);
      final source = wrapper['owner'] is Map
          ? Map<String, dynamic>.from(wrapper['owner'] as Map)
          : wrapper;
      final id = '${source['id'] ?? source['ownerId'] ?? source['owner_id'] ?? ''}'.trim();
      if (id.isEmpty) continue;
      result.add(
        RenderWorkspace(
          id: id,
          name: '${source['name'] ?? source['displayName'] ?? source['email'] ?? id}',
          email: '${source['email'] ?? ''}',
        ),
      );
    }
    if (result.isEmpty) throw Exception('No Render workspace was available to this API key.');
    return result;
  }

  Future<DeploymentResult> deployRender({
    required String apiToken,
    required String ownerId,
    required String repositoryUrl,
    required String serviceName,
    required String databaseUrl,
    required String deploymentMode,
    String branch = 'main',
  }) async {
    final normalizedName = serviceName
        .trim()
        .toLowerCase()
        .replaceAll(RegExp(r'[^a-z0-9-]+'), '-')
        .replaceAll(RegExp(r'^-+|-+$'), '');
    if (normalizedName.isEmpty) throw Exception('Enter a valid Render service name.');
    if (!{'production', 'testing'}.contains(deploymentMode)) {
      throw Exception('Unknown deployment mode.');
    }

    final normalizedRepository = repositoryUrl.trim();
    final normalizedBranch = branch.trim().isEmpty ? 'main' : branch.trim();
    final production = deploymentMode == 'production';
    final servicePlan = production ? 'starter' : 'free';
    final databasePlan = production ? 'basic_256mb' : 'free';
    final expectedUrl = 'https://$normalizedName.onrender.com';
    final pairingSecret = _randomToken(36);

    final existingService = await _findServiceByName(
      apiToken: apiToken,
      ownerId: ownerId,
      name: normalizedName,
    );

    if (existingService != null) {
      final serviceId = existingService.$1;
      final service = existingService.$2;
      final details = Map<String, dynamic>.from(
        (service['serviceDetails'] as Map?) ?? const <String, dynamic>{},
      );
      final serverUrl = (details['url'] ?? service['url'] ?? expectedUrl)
          .toString()
          .replaceAll(RegExp(r'/+$'), '');
      final currentEnv = await _getServiceEnvironment(apiToken, serviceId);
      final preserveExistingDatabase = databaseUrl.trim().isEmpty && currentEnv.containsKey('DATABASE_URL');
      var effectiveDatabaseUrl = databaseUrl.trim();
      var databaseId = '';
      if (!preserveExistingDatabase && effectiveDatabaseUrl.isEmpty) {
        final databaseName = '$normalizedName-db';
        final existingDatabase = await _findPostgresByName(
          apiToken: apiToken,
          ownerId: ownerId,
          name: databaseName,
        );
        final database = existingDatabase ??
            await _createPostgres(
              apiToken: apiToken,
              ownerId: ownerId,
              name: databaseName,
              plan: databasePlan,
            );
        databaseId = database.$1;
        effectiveDatabaseUrl = await _waitForDatabaseUrl(apiToken, databaseId);
      }

      await _updateServiceSource(
        apiToken: apiToken,
        serviceId: serviceId,
        repositoryUrl: normalizedRepository,
        branch: normalizedBranch,
      );
      final requiredEnv = <String, String>{
        'APP_NAME': 'Full-Time VA',
        'ENVIRONMENT': 'production',
        'PUBLIC_BASE_URL': serverUrl,
        'PAIRING_SECRET': pairingSecret,
        'PYTHON_VERSION': '3.13.5',
        if (!preserveExistingDatabase) 'DATABASE_URL': _asyncDatabaseUrl(effectiveDatabaseUrl),
        if (!currentEnv.containsKey('TOKEN_ENCRYPTION_KEY'))
          'TOKEN_ENCRYPTION_KEY': base64Url.encode(_randomBytes(32)),
      };
      for (final entry in requiredEnv.entries) {
        await _putServiceEnvironmentVariable(
          apiToken: apiToken,
          serviceId: serviceId,
          key: entry.key,
          value: entry.value,
        );
      }
      final deployId = await _triggerDeploy(apiToken: apiToken, serviceId: serviceId);
      return DeploymentResult(
        serverUrl: serverUrl,
        pairingSecret: pairingSecret,
        serviceId: serviceId,
        databaseId: databaseId,
        deployId: deployId,
      );
    }

    var effectiveDatabaseUrl = databaseUrl.trim();
    var databaseId = '';
    if (effectiveDatabaseUrl.isEmpty) {
      final databaseName = '$normalizedName-db';
      final existingDatabase = await _findPostgresByName(
        apiToken: apiToken,
        ownerId: ownerId,
        name: databaseName,
      );
      final database = existingDatabase ??
          await _createPostgres(
            apiToken: apiToken,
            ownerId: ownerId,
            name: databaseName,
            plan: databasePlan,
          );
      databaseId = database.$1;
      effectiveDatabaseUrl = await _waitForDatabaseUrl(apiToken, databaseId);
    }

    final encryptionKey = base64Url.encode(_randomBytes(32));
    final env = <Map<String, String>>[
      {'key': 'APP_NAME', 'value': 'Full-Time VA'},
      {'key': 'ENVIRONMENT', 'value': 'production'},
      {'key': 'PUBLIC_BASE_URL', 'value': expectedUrl},
      {'key': 'PAIRING_SECRET', 'value': pairingSecret},
      {'key': 'TOKEN_ENCRYPTION_KEY', 'value': encryptionKey},
      {'key': 'DATABASE_URL', 'value': _asyncDatabaseUrl(effectiveDatabaseUrl)},
      {'key': 'PYTHON_VERSION', 'value': '3.13.5'},
    ];
    final response = await http.post(
      Uri.parse('$_renderApi/services'),
      headers: _headers(apiToken, json: true),
      body: jsonEncode({
        'type': 'web_service',
        'name': normalizedName,
        'ownerId': ownerId.trim(),
        'repo': normalizedRepository,
        'branch': normalizedBranch,
        'rootDir': 'backend',
        'autoDeploy': 'yes',
        'envVars': env,
        'serviceDetails': {
          'runtime': 'python',
          'plan': servicePlan,
          'region': 'frankfurt',
          'healthCheckPath': '/health',
          'numInstances': 1,
          'envSpecificDetails': {
            'buildCommand': 'pip install .',
            'startCommand': 'uvicorn app.main:app --host 0.0.0.0 --port \$PORT',
          },
        },
      }),
    ).timeout(const Duration(seconds: 60));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception(_renderError(decoded, response.statusCode));
    }
    if (decoded is! Map) throw Exception('Render returned an unexpected service response.');
    final map = Map<String, dynamic>.from(decoded);
    final service = Map<String, dynamic>.from((map['service'] as Map?) ?? map);
    final details = Map<String, dynamic>.from((service['serviceDetails'] as Map?) ?? const <String, dynamic>{});
    final serverUrl = (details['url'] ?? service['url'] ?? expectedUrl).toString().replaceAll(RegExp(r'/+$'), '');
    final serviceId = (service['id'] ?? map['id'] ?? '').toString();
    return DeploymentResult(
      serverUrl: serverUrl,
      pairingSecret: pairingSecret,
      serviceId: serviceId,
      databaseId: databaseId,
    );
  }

  Future<(String, Map<String, dynamic>)?> _findServiceByName({
    required String apiToken,
    required String ownerId,
    required String name,
  }) async {
    final uri = Uri.parse('$_renderApi/services').replace(
      queryParameters: {
        'name': name,
        'ownerId': ownerId.trim(),
        'type': 'web_service',
        'limit': '20',
      },
    );
    final response = await http.get(uri, headers: _headers(apiToken)).timeout(const Duration(seconds: 45));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception('Service lookup failed: ${_renderError(decoded, response.statusCode)}');
    }
    if (decoded is! List) return null;
    for (final rawItem in decoded) {
      if (rawItem is! Map) continue;
      final wrapper = Map<String, dynamic>.from(rawItem);
      final service = wrapper['service'] is Map
          ? Map<String, dynamic>.from(wrapper['service'] as Map)
          : wrapper;
      final serviceName = '${service['name'] ?? ''}'.trim();
      final id = '${service['id'] ?? ''}'.trim();
      if (serviceName == name && id.isNotEmpty) return (id, service);
    }
    return null;
  }

  Future<Map<String, String>> _getServiceEnvironment(String apiToken, String serviceId) async {
    final response = await http.get(
      Uri.parse('$_renderApi/services/$serviceId/env-vars?limit=100'),
      headers: _headers(apiToken),
    ).timeout(const Duration(seconds: 45));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception('Environment lookup failed: ${_renderError(decoded, response.statusCode)}');
    }
    final result = <String, String>{};
    if (decoded is List) {
      for (final raw in decoded) {
        if (raw is! Map) continue;
        final wrapper = Map<String, dynamic>.from(raw);
        final envVar = wrapper['envVar'] is Map
            ? Map<String, dynamic>.from(wrapper['envVar'] as Map)
            : wrapper;
        final key = '${envVar['key'] ?? ''}'.trim();
        final value = '${envVar['value'] ?? ''}';
        if (key.isNotEmpty) result[key] = value;
      }
    }
    return result;
  }

  Future<void> _updateServiceSource({
    required String apiToken,
    required String serviceId,
    required String repositoryUrl,
    required String branch,
  }) async {
    final response = await http.patch(
      Uri.parse('$_renderApi/services/$serviceId'),
      headers: _headers(apiToken, json: true),
      body: jsonEncode({
        'repo': repositoryUrl,
        'branch': branch,
        'rootDir': 'backend',
        'autoDeploy': 'yes',
      }),
    ).timeout(const Duration(seconds: 60));
    if (response.statusCode >= 300) {
      throw Exception('Service update failed: ${_renderError(_decode(response), response.statusCode)}');
    }
  }

  Future<void> _putServiceEnvironmentVariable({
    required String apiToken,
    required String serviceId,
    required String key,
    required String value,
  }) async {
    final response = await http.put(
      Uri.parse('$_renderApi/services/$serviceId/env-vars/${Uri.encodeComponent(key)}'),
      headers: _headers(apiToken, json: true),
      body: jsonEncode({'value': value}),
    ).timeout(const Duration(seconds: 45));
    if (response.statusCode >= 300) {
      throw Exception('Could not update $key: ${_renderError(_decode(response), response.statusCode)}');
    }
  }

  Future<String> _triggerDeploy({required String apiToken, required String serviceId}) async {
    final response = await http.post(
      Uri.parse('$_renderApi/services/$serviceId/deploys'),
      headers: _headers(apiToken, json: true),
      body: jsonEncode({'clearCache': 'clear'}),
    ).timeout(const Duration(seconds: 60));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception('Deploy trigger failed: ${_renderError(decoded, response.statusCode)}');
    }
    if (decoded is Map) {
      final wrapper = Map<String, dynamic>.from(decoded);
      final deploy = wrapper['deploy'] is Map
          ? Map<String, dynamic>.from(wrapper['deploy'] as Map)
          : wrapper;
      return '${deploy['id'] ?? wrapper['id'] ?? ''}'.trim();
    }
    return '';
  }

  Future<void> waitForDeployLive({
    required String apiToken,
    required String serviceId,
    required String deployId,
  }) async {
    if (deployId.trim().isEmpty) return;
    const failedStatuses = {
      'build_failed',
      'update_failed',
      'pre_deploy_failed',
      'canceled',
      'deactivated',
    };
    for (var attempt = 0; attempt < 120; attempt++) {
      try {
        final response = await http.get(
          Uri.parse('$_renderApi/services/$serviceId/deploys/$deployId'),
          headers: _headers(apiToken),
        ).timeout(const Duration(seconds: 30));
        final decoded = _decode(response);
        if (response.statusCode >= 300) {
          if (response.statusCode != 503 && response.statusCode != 429) {
            throw Exception('Deploy status check failed: ${_renderError(decoded, response.statusCode)}');
          }
        } else if (decoded is Map) {
          final wrapper = Map<String, dynamic>.from(decoded);
          final deploy = wrapper['deploy'] is Map
              ? Map<String, dynamic>.from(wrapper['deploy'] as Map)
              : wrapper;
          final status = '${deploy['status'] ?? ''}'.trim().toLowerCase();
          if (status == 'live') return;
          if (failedStatuses.contains(status)) {
            final reason = deploy['errorMessage'] ?? deploy['error_message'] ?? status;
            throw Exception('Render deployment failed: $reason');
          }
        }
      } catch (error) {
        final message = error.toString();
        if (message.contains('Render deployment failed:') ||
            message.contains('Deploy status check failed:')) {
          rethrow;
        }
        // Timeouts and transient network failures are retried.
      }
      await Future<void>.delayed(const Duration(seconds: 8));
    }
    throw Exception('Timed out waiting for the new Render deployment to become Live. Open Render > Deploys to inspect the latest deployment.');
  }

  Future<(String, Map<String, dynamic>)?> _findPostgresByName({
    required String apiToken,
    required String ownerId,
    required String name,
  }) async {
    final uri = Uri.parse('$_renderApi/postgres').replace(
      queryParameters: {
        'name': name,
        'ownerId': ownerId.trim(),
        'limit': '20',
      },
    );
    final response = await http.get(uri, headers: _headers(apiToken)).timeout(const Duration(seconds: 45));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception('Database lookup failed: ${_renderError(decoded, response.statusCode)}');
    }
    if (decoded is! List) return null;
    for (final rawItem in decoded) {
      if (rawItem is! Map) continue;
      final wrapper = Map<String, dynamic>.from(rawItem);
      final database = wrapper['postgres'] is Map
          ? Map<String, dynamic>.from(wrapper['postgres'] as Map)
          : wrapper;
      final databaseName = '${database['name'] ?? ''}'.trim();
      final id = '${database['id'] ?? ''}'.trim();
      if (databaseName == name && id.isNotEmpty) return (id, database);
    }
    return null;
  }

  Future<(String, Map<String, dynamic>)> _createPostgres({
    required String apiToken,
    required String ownerId,
    required String name,
    required String plan,
  }) async {
    final response = await http.post(
      Uri.parse('$_renderApi/postgres'),
      headers: _headers(apiToken, json: true),
      body: jsonEncode({
        'name': name,
        'ownerId': ownerId.trim(),
        'plan': plan,
        'region': 'frankfurt',
        'version': '18',
        'connectionPool': 'none',
      }),
    ).timeout(const Duration(seconds: 60));
    final decoded = _decode(response);
    if (response.statusCode >= 300) {
      throw Exception('Database creation failed: ${_renderError(decoded, response.statusCode)}');
    }
    if (decoded is! Map) throw Exception('Render returned an unexpected database response.');
    final map = Map<String, dynamic>.from(decoded);
    final database = Map<String, dynamic>.from(
      (map['postgres'] as Map?) ?? (map['database'] as Map?) ?? map,
    );
    final id = '${database['id'] ?? map['id'] ?? ''}'.trim();
    if (id.isEmpty) throw Exception('Render created a database but did not return its ID.');
    return (id, database);
  }

  Future<String> _waitForDatabaseUrl(String apiToken, String databaseId) async {
    for (var attempt = 0; attempt < 90; attempt++) {
      try {
        final response = await http.get(
          Uri.parse('$_renderApi/postgres/$databaseId/connection-info'),
          headers: _headers(apiToken),
        ).timeout(const Duration(seconds: 30));
        if (response.statusCode == 200) {
          final decoded = _decode(response);
          final value = _findConnectionString(decoded);
          if (value != null && value.isNotEmpty) return value;
        } else if (response.statusCode >= 400 && response.statusCode != 404) {
          throw Exception(_renderError(_decode(response), response.statusCode));
        }
      } catch (_) {
        // A new database can return temporary errors while it is being provisioned.
      }
      await Future<void>.delayed(const Duration(seconds: 10));
    }
    throw Exception('The Render database was created, but its connection details did not become available.');
  }

  String? _findConnectionString(dynamic value) {
    if (value is Map) {
      const preferred = [
        'internalConnectionString',
        'internal_connection_string',
        'connectionString',
        'connection_string',
        'externalConnectionString',
        'external_connection_string',
        'url',
      ];
      for (final key in preferred) {
        final candidate = value[key];
        if (candidate is String && (candidate.startsWith('postgres://') || candidate.startsWith('postgresql://'))) {
          return candidate;
        }
      }
      for (final nested in value.values) {
        final found = _findConnectionString(nested);
        if (found != null) return found;
      }
    } else if (value is List) {
      for (final nested in value) {
        final found = _findConnectionString(nested);
        if (found != null) return found;
      }
    }
    return null;
  }

  String _asyncDatabaseUrl(String value) {
    if (value.startsWith('postgresql+asyncpg://')) return value;
    if (value.startsWith('postgresql://')) return value.replaceFirst('postgresql://', 'postgresql+asyncpg://');
    if (value.startsWith('postgres://')) return value.replaceFirst('postgres://', 'postgresql+asyncpg://');
    return value;
  }

  Future<bool> waitUntilHealthy(String serverUrl, {String requiredVersion = '0.4.15'}) async {
    for (var attempt = 0; attempt < 90; attempt++) {
      try {
        final health = await http.get(Uri.parse('$serverUrl/health')).timeout(const Duration(seconds: 20));
        if (health.statusCode == 200) {
          final info = await http.get(Uri.parse('$serverUrl/api/system/info')).timeout(const Duration(seconds: 20));
          if (info.statusCode == 200) {
            final decoded = _decode(info);
            final version = decoded is Map ? '${decoded['version'] ?? ''}' : '';
            if (_versionAtLeast(version, requiredVersion)) return true;
          }
        }
      } catch (_) {}
      await Future<void>.delayed(const Duration(seconds: 10));
    }
    return false;
  }

  bool _versionAtLeast(String actual, String required) {
    List<int> parts(String value) => value
        .split(RegExp(r'[^0-9]+'))
        .where((part) => part.isNotEmpty)
        .take(3)
        .map(int.parse)
        .toList();
    final a = parts(actual);
    final r = parts(required);
    if (a.isEmpty) return false;
    for (var index = 0; index < 3; index++) {
      final av = index < a.length ? a[index] : 0;
      final rv = index < r.length ? r[index] : 0;
      if (av != rv) return av > rv;
    }
    return true;
  }

  Map<String, String> _headers(String apiToken, {bool json = false}) => {
        'Authorization': 'Bearer ${apiToken.trim()}',
        'Accept': 'application/json',
        if (json) 'Content-Type': 'application/json',
      };

  dynamic _decode(http.Response response) {
    if (response.body.trim().isEmpty) return <String, dynamic>{};
    try {
      return jsonDecode(response.body);
    } catch (_) {
      return <String, dynamic>{'message': response.body};
    }
  }

  String _renderError(dynamic body, int status) {
    if (body is Map) {
      final message = body['message'] ?? body['error'] ?? body['detail'];
      if (message != null) return '$message';
    }
    return 'Render rejected the request (HTTP $status).';
  }

  List<int> _randomBytes(int length) {
    final random = Random.secure();
    return List<int>.generate(length, (_) => random.nextInt(256));
  }

  String _randomToken(int length) => base64Url.encode(_randomBytes(length)).replaceAll('=', '');
}
