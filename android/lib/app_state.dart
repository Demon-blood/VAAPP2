import 'package:flutter/foundation.dart';

import 'models/view_models.dart';
import 'services/api_client.dart';
import 'services/device_bridge.dart';
import 'services/local_connector_catalog.dart';

class AppState extends ChangeNotifier {
  AppState(this.api);

  final ApiClient api;
  bool initialized = false;
  bool paired = false;
  bool busy = false;
  bool refreshComplete = false;
  String? error;
  String? serverWarning;
  bool repairRecommended = false;
  Map<String, String> endpointErrors = {};
  Map<String, dynamic> systemInfo = {};
  DashboardData? dashboard;
  Map<String, dynamic> configuration = {};
  List<Map<String, dynamic>> emails = [];
  List<Map<String, dynamic>> tasks = [];
  List<Map<String, dynamic>> bills = [];
  List<Map<String, dynamic>> financialRecords = [];
  List<Map<String, dynamic>> accounts = [];
  List<Map<String, dynamic>> payments = [];
  List<Map<String, dynamic>> documents = [];
  List<Map<String, dynamic>> contacts = [];
  List<Map<String, dynamic>> orders = [];
  List<Map<String, dynamic>> subscriptions = [];
  List<Map<String, dynamic>> supportCases = [];
  Map<String, dynamic> serviceStatus = {};
  List<Map<String, dynamic>> githubRepositories = [];
  List<Map<String, dynamic>> githubNotifications = [];
  Map<String, dynamic> cloudflareResources = {};
  Map<String, dynamic> aiUsage = {};
  List<Map<String, dynamic>> setupSections = [];
  List<Map<String, dynamic>> connectorTemplates = [];
  List<Map<String, dynamic>> connectorPresets = [];
  List<Map<String, dynamic>> connectors = [];
  List<Map<String, dynamic>> automationRules = [];
  Map<String, dynamic> autopilotHealth = {};
  Map<String, dynamic> dailyBriefing = {};
  List<Map<String, dynamic>> autopilotJobs = [];
  List<Map<String, dynamic>> communications = [];
  List<Map<String, dynamic>> communicationRules = [];
  Map<String, dynamic> communicationStatus = {};
  Map<String, dynamic> financeOverview = {};
  Map<String, dynamic> financeInvestments = {};
  List<Map<String, dynamic>> financeAccountPolicies = [];
  List<Map<String, dynamic>> budgetEnvelopes = [];
  List<Map<String, dynamic>> internalTransfers = [];
  Map<String, dynamic> vaOverview = {};
  Map<String, dynamic> vaCapabilities = {};
  List<Map<String, dynamic>> vaObjectives = [];

  Future<void> initialize() async {
    try {
      final localCatalog = await LocalConnectorCatalog.load();
      connectorTemplates = localCatalog.templates;
      connectorPresets = localCatalog.presets;
    } catch (catalogError) {
      serverWarning = 'The built-in connector catalog could not be loaded: $catalogError';
    }
    paired = await api.deviceToken != null && await api.serverUrl != null;
    if (paired) await _syncDeviceLink();
    initialized = true;
    notifyListeners();
    if (paired) await refreshAll();
  }

  Future<void> pair({
    required String serverUrl,
    required String pairingSecret,
    required String deviceName,
  }) async {
    await _run(() async {
      await api.pair(
        serverUrl: serverUrl,
        pairingSecret: pairingSecret,
        deviceName: deviceName,
      );
      paired = true;
      await _syncDeviceLink();
      await refreshAll(showBusy: false);
    });
  }

  Future<void> disconnect() async {
    await api.disconnect();
    try {
      await DeviceBridge.clearCredentials();
    } catch (_) {}
    paired = false;
    dashboard = null;
    configuration = {};
    emails = [];
    tasks = [];
    bills = [];
    financialRecords = [];
    accounts = [];
    payments = [];
    documents = [];
    contacts = [];
    orders = [];
    subscriptions = [];
    supportCases = [];
    serviceStatus = {};
    githubRepositories = [];
    githubNotifications = [];
    cloudflareResources = {};
    aiUsage = {};
    setupSections = [];
    connectors = [];
    automationRules = [];
    autopilotHealth = {};
    dailyBriefing = {};
    autopilotJobs = [];
    communications = [];
    communicationRules = [];
    communicationStatus = {};
    financeOverview = {};
    financeInvestments = {};
    financeAccountPolicies = [];
    budgetEnvelopes = [];
    internalTransfers = [];
    vaOverview = {};
    vaCapabilities = {};
    vaObjectives = [];
    systemInfo = {};
    endpointErrors = {};
    serverWarning = null;
    repairRecommended = false;
    refreshComplete = false;
    notifyListeners();
  }

  Future<void> refreshAll({bool showBusy = true}) async {
    if (showBusy) busy = true;
    error = null;
    endpointErrors = {};
    repairRecommended = false;
    notifyListeners();

    try {
      final info = await _safeGet('/api/system/info', public: true);
      if (info is Map) {
        systemInfo = Map<String, dynamic>.from(info);
        final backendVersion = systemInfo['version']?.toString() ?? '';
        if (!_versionAtLeast(backendVersion, '0.9.0')) {
          repairRecommended = true;
          serverWarning = backendVersion.isEmpty
              ? 'The connected server is missing version information and must be redeployed from the current repository.'
              : 'The connected server is running backend $backendVersion. App 0.9.0 requires backend 0.9.0 or newer.';
        } else {
          serverWarning = null;
          repairRecommended = false;
        }
      } else {
        repairRecommended = true;
        serverWarning = 'The connected VA server is running an older or incomplete backend. Redeploy the backend from the current repository, then refresh.';
      }

      final results = await Future.wait<dynamic>([
        _safeGet('/api/dashboard'),
        _safeGet('/api/configuration'),
        _safeGet('/api/emails'),
        _safeGet('/api/tasks'),
        _safeGet('/api/bills'),
        _safeGet('/api/financial-records'),
        _safeGet('/api/accounts'),
        _safeGet('/api/payments'),
        _safeGet('/api/documents'),
        _safeGet('/api/contacts'),
        _safeGet('/api/orders'),
        _safeGet('/api/subscriptions'),
        _safeGet('/api/support-cases'),
        _safeGet('/api/services/status'),
        _safeGet('/api/setup/sections'),
        _safeGet('/api/connectors/templates'),
        _safeGet('/api/connectors/presets'),
        _safeGet('/api/connectors'),
        _safeGet('/api/rules'),
        _safeGet('/api/autopilot/health'),
        _safeGet('/api/autopilot/briefing'),
        _safeGet('/api/autopilot/jobs?limit=30'),
        _safeGet('/api/communications/events?limit=200'),
        _safeGet('/api/finance/overview'),
        _safeGet('/api/finance/account-policies'),
        _safeGet('/api/finance/budgets'),
        _safeGet('/api/finance/transfers'),
        _safeGet('/api/communications/rules'),
        _safeGet('/api/finance/investments'),
        _safeGet('/api/va/overview'),
        _safeGet('/api/va/capabilities'),
        _safeGet('/api/va/objectives?limit=100'),
      ]);

      if (results[0] is Map) dashboard = DashboardData.fromJson(Map<String, dynamic>.from(results[0] as Map));
      if (results[1] is Map) configuration = Map<String, dynamic>.from(results[1] as Map);
      if (results[2] is List) emails = _list(results[2]);
      if (results[3] is List) tasks = _list(results[3]);
      if (results[4] is List) bills = _list(results[4]);
      if (results[5] is List) financialRecords = _list(results[5]);
      if (results[6] is List) accounts = _list(results[6]);
      if (results[7] is List) payments = _list(results[7]);
      if (results[8] is List) documents = _list(results[8]);
      if (results[9] is List) contacts = _list(results[9]);
      if (results[10] is List) orders = _list(results[10]);
      if (results[11] is List) subscriptions = _list(results[11]);
      if (results[12] is List) supportCases = _list(results[12]);
      if (results[13] is Map) serviceStatus = Map<String, dynamic>.from(results[13] as Map);
      if (results[14] is List) setupSections = _list(results[14]);
      if (results[15] is List && (results[15] as List).isNotEmpty) connectorTemplates = _list(results[15]);
      if (results[16] is List && (results[16] as List).isNotEmpty) connectorPresets = _list(results[16]);
      if (results[17] is List) connectors = _list(results[17]);
      if (results[18] is List) automationRules = _list(results[18]);
      if (results[19] is Map) autopilotHealth = Map<String, dynamic>.from(results[19] as Map);
      if (results[20] is Map) dailyBriefing = Map<String, dynamic>.from(results[20] as Map);
      if (results[21] is List) autopilotJobs = _list(results[21]);
      if (results[22] is List) communications = _list(results[22]);
      if (results[23] is Map) financeOverview = Map<String, dynamic>.from(results[23] as Map);
      if (results[24] is List) financeAccountPolicies = _list(results[24]);
      if (results[25] is List) budgetEnvelopes = _list(results[25]);
      if (results[26] is List) internalTransfers = _list(results[26]);
      if (results[27] is List) communicationRules = _list(results[27]);
      if (results[28] is Map) financeInvestments = Map<String, dynamic>.from(results[28] as Map);
      if (results[29] is Map) vaOverview = Map<String, dynamic>.from(results[29] as Map);
      if (results[30] is Map) vaCapabilities = Map<String, dynamic>.from(results[30] as Map);
      if (results[31] is List) vaObjectives = _list(results[31]);
      await _refreshNativeCommunicationState();

      githubRepositories = [];
      githubNotifications = [];
      cloudflareResources = {};
      aiUsage = {};
      if (configuration['ai_configured'] == true) {
        final usage = await _safeGet('/api/ai/status', optional: true);
        if (usage is Map) aiUsage = Map<String, dynamic>.from(usage);
      }
      if (configuration['github_configured'] == true) {
        final repositories = await _safeGet('/api/github/repositories');
        final notifications = await _safeGet('/api/github/notifications', optional: true);
        if (repositories is List) githubRepositories = _list(repositories);
        if (notifications is List) githubNotifications = _list(notifications);
      }
      if (configuration['cloudflare_configured'] == true) {
        final resources = await _safeGet('/api/cloudflare/resources');
        if (resources is Map) cloudflareResources = Map<String, dynamic>.from(resources);
      }

      if (endpointErrors.isNotEmpty && serverWarning == null) {
        final first = endpointErrors.entries.first;
        serverWarning = 'Some VA server functions are unavailable. ${first.key}: ${first.value}';
      }
    } finally {
      refreshComplete = true;
      if (showBusy) busy = false;
      notifyListeners();
    }
  }

  Future<dynamic> _safeGet(
    String path, {
    bool public = false,
    bool optional = false,
  }) async {
    try {
      return public ? await api.getPublicJson(path) : await api.getJson(path);
    } catch (requestError) {
      if (!optional) {
        endpointErrors[path] = requestError.toString();
      }
      return null;
    }
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

  Future<String> startGoogleConnection() async {
    final data = await api.getJson('/api/google/start') as Map;
    return data['authorization_url'] as String;
  }

  Future<String> startBankConnection({
    required String institutionName,
    String psuType = 'personal',
  }) async {
    final data = await api.postJson('/api/banking/start', {
      'institution_country': 'BE',
      'institution_name': institutionName,
      'psu_type': psuType,
    }) as Map;
    return data['authorization_url'] as String;
  }

  void clearTransientError() {
    if (error == null) return;
    error = null;
    notifyListeners();
  }

  Future<void> refreshMoneyData() async {
    busy = true;
    error = null;
    endpointErrors.remove('/api/bills');
    endpointErrors.remove('/api/financial-records');
    endpointErrors.remove('/api/accounts');
    endpointErrors.remove('/api/payments');
    notifyListeners();
    try {
      final results = await Future.wait<dynamic>([
        _safeGet('/api/bills'),
        _safeGet('/api/financial-records'),
        _safeGet('/api/accounts'),
        _safeGet('/api/payments'),
        _safeGet('/api/dashboard'),
        _safeGet('/api/finance/overview'),
        _safeGet('/api/finance/account-policies'),
        _safeGet('/api/finance/budgets'),
        _safeGet('/api/finance/transfers'),
        _safeGet('/api/finance/investments'),
      ]);
      if (results[0] is List) bills = _list(results[0]);
      if (results[1] is List) financialRecords = _list(results[1]);
      if (results[2] is List) accounts = _list(results[2]);
      if (results[3] is List) payments = _list(results[3]);
      if (results[4] is Map) dashboard = DashboardData.fromJson(Map<String, dynamic>.from(results[4] as Map));
      if (results[5] is Map) financeOverview = Map<String, dynamic>.from(results[5] as Map);
      if (results[6] is List) financeAccountPolicies = _list(results[6]);
      if (results[7] is List) budgetEnvelopes = _list(results[7]);
      if (results[8] is List) internalTransfers = _list(results[8]);
      if (results[9] is Map) financeInvestments = Map<String, dynamic>.from(results[9] as Map);
      if (endpointErrors.isNotEmpty && serverWarning == null) {
        final first = endpointErrors.entries.first;
        serverWarning = 'Some VA server functions are unavailable. ${first.key}: ${first.value}';
      }
    } finally {
      busy = false;
      refreshComplete = true;
      notifyListeners();
    }
  }

  Future<void> _syncDeviceLink() async {
    try {
      await DeviceBridge.syncCredentials(
        serverUrl: await api.serverUrl,
        deviceToken: await api.deviceToken,
      );
    } catch (_) {}
  }

  Future<void> _refreshNativeCommunicationState() async {
    await _syncDeviceLink();
    try {
      communicationStatus = await DeviceBridge.communicationStatus();
      await DeviceBridge.syncCallPolicy();
    } catch (_) {
      communicationStatus = {};
    }
  }

  Future<void> refreshCommunications({bool syncDeviceHistory = false}) async {
    if (syncDeviceHistory) {
      await _syncDeviceLink();
      try {
        await DeviceBridge.syncRecentCommunications();
        await DeviceBridge.syncCallPolicy();
      } catch (bridgeError) {
        error = 'Phone communication sync failed: $bridgeError';
      }
    }
    final results = await Future.wait<dynamic>([
      _safeGet('/api/communications/events?limit=200'),
      _safeGet('/api/communications/rules'),
    ]);
    if (results[0] is List) communications = _list(results[0]);
    if (results[1] is List) communicationRules = _list(results[1]);
    await _refreshNativeCommunicationState();
    notifyListeners();
  }

  Future<void> saveCallRule({required String phoneNumber, required String disposition}) async {
    await _run(() async {
      await api.postJson('/api/communications/rules', {
        'channel': 'call',
        'contact_key': phoneNumber,
        'disposition': disposition,
        'auto_reply_enabled': false,
        'source': disposition == 'allow' ? 'vip' : 'manual',
      });
      await refreshCommunications();
      try {
        await DeviceBridge.syncCallPolicy();
      } catch (_) {}
    });
  }

  Future<void> deleteCallRule(int ruleId) async {
    await _run(() async {
      await api.deleteJson('/api/communications/rules/$ruleId');
      await refreshCommunications();
      try {
        await DeviceBridge.syncCallPolicy();
      } catch (_) {}
    });
  }

  Future<void> requestCommunicationPermissions() async {
    await DeviceBridge.requestRuntimePermissions();
    await Future<void>.delayed(const Duration(milliseconds: 350));
    await _refreshNativeCommunicationState();
    notifyListeners();
  }

  Future<void> requestSmsRole() async {
    await DeviceBridge.requestSmsRole();
  }

  Future<void> requestCallScreeningRole() async {
    await DeviceBridge.requestCallScreeningRole();
  }

  Future<void> openNotificationAccess() async {
    await DeviceBridge.openNotificationAccess();
  }

  Future<void> sendSms({required String target, required String text}) async {
    await DeviceBridge.sendSms(target: target, text: text);
    await refreshCommunications(syncDeviceHistory: true);
  }

  Future<Map<String, dynamic>> runFinancialAutopilotNow() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(await api.postJson('/api/finance/autopilot/run') as Map);
      await refreshMoneyData();
    });
    return result;
  }

  Future<void> refreshInternalTransfer(int transferId) async {
    await _run(() async {
      await api.postJson('/api/finance/transfers/$transferId/refresh');
      await refreshMoneyData();
    });
  }

  Future<void> updateFinanceAccountPolicy(int accountId, Map<String, dynamic> values) async {
    await _run(() async {
      await api.putJson('/api/finance/account-policies/$accountId', values);
      await refreshMoneyData();
    });
  }

  Future<void> saveBudgetEnvelope(Map<String, dynamic> values) async {
    await _run(() async {
      await api.postJson('/api/finance/budgets', values);
      await refreshMoneyData();
    });
  }


  Future<Map<String, dynamic>> importFinancialHistory(
    List<String> filePaths, {
    String accountScope = 'personal',
  }) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postFiles(
          '/api/finance/statements/import',
          filePaths,
          fields: {'account_scope': accountScope},
        ) as Map,
      );
      await refreshMoneyData();
    });
    return result;
  }

  Future<Map<String, dynamic>> reconcileFinancialRecords() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/financial-records/reconcile') as Map,
      );
      await refreshMoneyData();
    });
    return result;
  }

  Future<Map<String, dynamic>> cleanupDocuments() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/documents/cleanup') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<Map<String, dynamic>> runAutomationNow() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/actions/run') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<Map<String, dynamic>> runAutonomousCoreNow() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/va/run') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<Map<String, dynamic>> recheckVaObjective(int objectiveId) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/va/objectives/$objectiveId/recheck') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<void> syncGmail() async {
    await _run(() async {
      await api.postJson('/api/sync/gmail');
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> runAutomaticPaymentsNow() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/payments/auto-run') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<void> syncBanks() async {
    await _run(() async {
      await api.postJson('/api/sync/banks');
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> createPayment(int billId, int accountId) async {
    late Map<String, dynamic> payment;
    await _run(() async {
      payment = Map<String, dynamic>.from(
        await api.postJson('/api/payments', {
          'bill_id': billId,
          'bank_account_id': accountId,
        }) as Map,
      );
      await refreshAll(showBusy: false);
    });
    return payment;
  }

  Future<void> refreshPayment(int paymentId) async {
    await _run(() async {
      await api.postJson('/api/payments/$paymentId/refresh');
      await refreshAll(showBusy: false);
    });
  }

  Future<void> approveCreditor({
    required String name,
    required String iban,
    required String accountScope,
    required double maximum,
  }) async {
    await _run(() async {
      await api.postJson('/api/creditors', {
        'name': name,
        'iban': iban,
        'account_scope': accountScope,
        'auto_pay_enabled': true,
        'max_auto_amount': maximum,
      });
      await refreshAll(showBusy: false);
    });
  }

  Future<void> updateAccountPolicy({
    required int accountId,
    required String scope,
    required double reserve,
    required bool enabled,
  }) async {
    await _run(() async {
      await api.putJson('/api/accounts/$accountId/policy', {
        'account_scope': scope,
        'safety_reserve': reserve,
        'enabled_for_payments': enabled,
      });
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> executeTaskAction(int taskId) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/tasks/$taskId/execute') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<void> setTaskStatus(int taskId, String status) async {
    await _run(() async {
      await api.patchJson('/api/tasks/$taskId/status?status=$status');
      await refreshAll(showBusy: false);
    });
  }

  Future<void> syncExternalServices() async {
    await _run(() async {
      await api.postJson('/api/sync/external');
      await refreshAll(showBusy: false);
    });
  }

  Future<void> setSupportCaseStatus(int caseId, String status) async {
    await _run(() async {
      await api.patchJson('/api/support-cases/$caseId/status?status=$status');
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> createGitHubIssue({
    required String repository,
    required String title,
    required String body,
    List<String> labels = const [],
  }) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/github/issues', {
          'repository': repository,
          'title': title,
          'body': body,
          'labels': labels,
        }) as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<void> sendDiscordMessage(String content, {String? channelId}) async {
    await _run(() async {
      await api.postJson('/api/discord/messages', {
        'content': content,
        if (channelId != null && channelId.isNotEmpty) 'channel_id': channelId,
      });
    });
  }

  Future<void> configureSetupSection(String slug, Map<String, dynamic> values) async {
    await _run(() async {
      await api.putJson('/api/setup/sections/$slug', values);
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> generateEnableBankingCertificate() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/setup/enable-banking/generate-key') as Map,
      );
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<Map<String, dynamic>> testSetupSection(String slug) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(await api.postJson('/api/setup/sections/$slug/test') as Map);
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<void> disconnectSetupSection(String slug) async {
    await _run(() async {
      await api.deleteJson('/api/setup/sections/$slug');
      await refreshAll(showBusy: false);
    });
  }

  Future<void> configureConnector({
    required String slug,
    required String displayName,
    required String connectorType,
    required Map<String, dynamic> config,
    String? category,
  }) async {
    await _run(() async {
      await api.putJson('/api/connectors/$slug', {
        'display_name': displayName,
        'connector_type': connectorType,
        'config': config,
        'category': ?category,
      });
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> testConnector(String slug) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(await api.postJson('/api/connectors/$slug/test') as Map);
      await refreshAll(showBusy: false);
    });
    return result;
  }

  Future<String> startConnectorOauth(String slug) async {
    final data = await api.getJson('/api/connectors/$slug/oauth/start') as Map;
    return data['authorization_url'] as String;
  }

  Future<void> deleteConnector(String slug) async {
    await _run(() async {
      await api.deleteJson('/api/connectors/$slug');
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> executeConnector(
    String slug,
    String operation,
    Map<String, dynamic> parameters,
  ) async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/connectors/$slug/execute', {
          'operation': operation,
          'parameters': parameters,
        }) as Map,
      );
    });
    return result;
  }


  Future<void> createAutomationRule({
    required String ruleType,
    required String name,
    required Map<String, dynamic> conditions,
    required Map<String, dynamic> actions,
  }) async {
    await _run(() async {
      await api.postJson('/api/rules', {
        'rule_type': ruleType,
        'name': name,
        'conditions': conditions,
        'actions': actions,
        'enabled': true,
      });
      await refreshAll(showBusy: false);
    });
  }

  Future<void> setAutomationRuleEnabled(int ruleId, bool enabled) async {
    await _run(() async {
      await api.patchJson('/api/rules/$ruleId/enabled?enabled=$enabled');
      await refreshAll(showBusy: false);
    });
  }

  Future<void> deleteAutomationRule(int ruleId) async {
    await _run(() async {
      await api.deleteJson('/api/rules/$ruleId');
      await refreshAll(showBusy: false);
    });
  }

  Future<Map<String, dynamic>> androidSigningStatus() async {
    return Map<String, dynamic>.from(
      await api.getJson('/api/github/android/signing/status') as Map,
    );
  }

  Future<Map<String, dynamic>> triggerAndroidBuild() async {
    late Map<String, dynamic> result;
    await _run(() async {
      result = Map<String, dynamic>.from(
        await api.postJson('/api/github/workflows/android/build', const <String, dynamic>{}) as Map,
      );
    });
    return result;
  }

  Future<List<Map<String, dynamic>>> loadAndroidBuildRuns() async {
    final data = await api.getJson('/api/github/workflows/android/runs');
    return _list(data);
  }

  Future<Map<String, dynamic>> dispatchAutopilotIntent(
    String type, {
    Map<String, dynamic>? payload,
    int? billId,
  }) async {
    final body = <String, dynamic>{'type': type};
    if (payload != null) body['payload'] = payload;
    if (billId != null) body['bill_id'] = billId;
    final result = Map<String, dynamic>.from(await api.postJson('/api/autopilot/intents', body) as Map);
    await refreshAll(showBusy: false);
    return result;
  }

  Future<void> requeueAutopilotJob(int jobId) async {
    await api.postJson('/api/autopilot/jobs/$jobId/requeue');
    await refreshAll(showBusy: false);
  }

  Future<Map<String, dynamic>> recoverAutopilot() async {
    final result = Map<String, dynamic>.from(
      await api.postJson('/api/autopilot/recover') as Map,
    );
    await refreshAll(showBusy: false);
    return result;
  }

  Future<void> _run(Future<void> Function() action, {bool showBusy = true}) async {
    if (showBusy) busy = true;
    error = null;
    notifyListeners();
    try {
      await action();
    } catch (exception) {
      error = exception.toString();
      rethrow;
    } finally {
      if (showBusy) busy = false;
      notifyListeners();
    }
  }

  List<Map<String, dynamic>> _list(dynamic value) =>
      (value as List).map((item) => Map<String, dynamic>.from(item as Map)).toList();
}
