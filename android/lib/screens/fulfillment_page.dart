import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class FulfillmentPage extends StatefulWidget {
  const FulfillmentPage({super.key});

  @override
  State<FulfillmentPage> createState() => _FulfillmentPageState();
}

class _FulfillmentPageState extends State<FulfillmentPage> {
  bool loading = true;
  String? error;
  Map<String, dynamic> status = {};
  List<Map<String, dynamic>> providers = [];
  List<Map<String, dynamic>> providerTemplates = [];
  List<Map<String, dynamic>> portals = [];
  List<Map<String, dynamic>> requests = [];

  @override
  void initState() {
    super.initState();
    Future.microtask(_refresh);
  }

  List<Map<String, dynamic>> _rows(dynamic value) => value is List
      ? value.whereType<Map>().map((row) => Map<String, dynamic>.from(row)).toList()
      : <Map<String, dynamic>>[];

  Future<void> _refresh() async {
    if (!mounted) return;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final api = context.read<AppState>().api;
      final result = await Future.wait<dynamic>([
        api.getJson('/api/fulfillment/status'),
        api.getJson('/api/fulfillment/providers'),
        api.getJson('/api/fulfillment/provider-templates'),
        api.getJson('/api/browser/portals'),
        api.getJson('/api/fulfillment/requests?limit=200'),
      ]);
      if (!mounted) return;
      setState(() {
        status = result[0] is Map ? Map<String, dynamic>.from(result[0] as Map) : {};
        providers = _rows(result[1]);
        providerTemplates = _rows(result[2]);
        portals = _rows(result[3]);
        requests = _rows(result[4]);
      });
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _reconcile() async {
    try {
      await context.read<AppState>().api.postJson('/api/fulfillment/reconcile');
      await _refresh();
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    }
  }

  Future<void> _run(int id) async {
    try {
      await context.read<AppState>().api.postJson('/api/fulfillment/requests/$id/run');
      await _refresh();
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    }
  }

  Future<void> _authorize(int id) async {
    final approved = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('Authorize this payment commitment?'),
            content: const Text(
              'This authorizes the specific purchase or travel booking already shown in the fulfillment request. Provider authentication may still be required. VAAPP will not treat a browser click as complete until the provider postcondition is verified.',
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Authorize')),
            ],
          ),
        ) ??
        false;
    if (!approved || !mounted) return;
    try {
      await context.read<AppState>().api.postJson('/api/fulfillment/requests/$id/authorize-payment');
      await _refresh();
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    }
  }

  Future<void> _dismissOrder(int orderId) async {
    final approved = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('This is not an order?'),
            content: const Text(
              'VAAPP will stop treating this source record as a parcel/order and dismiss its logistics objective. The original email/payment receipt remains preserved as source evidence.',
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Not an order')),
            ],
          ),
        ) ??
        false;
    if (!approved || !mounted) return;
    try {
      await context.read<AppState>().dismissOrder(orderId);
      await _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Dismissed false order tracking; source receipt/payment evidence was kept.')),
        );
      }
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    }
  }

  Future<void> _createRequest() async {
    final title = TextEditingController();
    final goal = TextEditingController();
    final amount = TextEditingController();
    final details = TextEditingController(text: '{}');
    var requestType = 'customer_service';
    var accountScope = 'personal';
    final enabledProviders = providers.where((row) => row['enabled'] == true).toList();
    int? providerId = enabledProviders.isEmpty ? null : enabledProviders.first['id'] as int?;
    final create = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('New fulfillment objective'),
          content: SizedBox(
            width: 560,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  DropdownButtonFormField<String>(
                    initialValue: requestType,
                    decoration: const InputDecoration(labelText: 'Type'),
                    items: const [
                      DropdownMenuItem(value: 'purchase', child: Text('Purchase')),
                      DropdownMenuItem(value: 'travel', child: Text('Travel booking')),
                      DropdownMenuItem(value: 'logistics', child: Text('Logistics / tracking')),
                      DropdownMenuItem(value: 'return', child: Text('Return')),
                      DropdownMenuItem(value: 'refund', child: Text('Refund')),
                      DropdownMenuItem(value: 'cancel', child: Text('Provider cancellation')),
                      DropdownMenuItem(value: 'customer_service', child: Text('Customer service')),
                    ],
                    onChanged: (value) => setDialogState(() => requestType = value ?? requestType),
                  ),
                  const SizedBox(height: 10),
                  DropdownButtonFormField<int?>(
                    initialValue: providerId,
                    decoration: const InputDecoration(labelText: 'Configured provider'),
                    items: [
                      const DropdownMenuItem<int?>(value: null, child: Text('No provider yet')),
                      for (final provider in providers.where((row) => row['enabled'] == true))
                        DropdownMenuItem<int?>(value: provider['id'] as int?, child: Text('${provider['name']}')),
                    ],
                    onChanged: (value) => setDialogState(() => providerId = value),
                  ),
                  const SizedBox(height: 10),
                  DropdownButtonFormField<String>(
                    initialValue: accountScope,
                    decoration: const InputDecoration(labelText: 'Scope'),
                    items: const [
                      DropdownMenuItem(value: 'personal', child: Text('Personal')),
                      DropdownMenuItem(value: 'pro', child: Text('Pro')),
                    ],
                    onChanged: (value) => setDialogState(() => accountScope = value ?? accountScope),
                  ),
                  const SizedBox(height: 10),
                  TextField(controller: title, decoration: const InputDecoration(labelText: 'Title')),
                  const SizedBox(height: 10),
                  TextField(controller: goal, minLines: 2, maxLines: 5, decoration: const InputDecoration(labelText: 'Objective / required outcome')),
                  const SizedBox(height: 10),
                  TextField(controller: amount, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Known amount in EUR (optional)')),
                  const SizedBox(height: 10),
                  TextField(controller: details, minLines: 3, maxLines: 8, decoration: const InputDecoration(labelText: 'Provider variables as JSON')),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Create & run')),
          ],
        ),
      ),
    );
    if (create != true || !mounted || title.text.trim().isEmpty || goal.text.trim().isEmpty) return;
    try {
      final decoded = jsonDecode(details.text.trim().isEmpty ? '{}' : details.text.trim());
      if (decoded is! Map) throw const FormatException('Details JSON must be an object.');
      final parsedAmount = double.tryParse(amount.text.trim());
      final body = <String, dynamic>{
        'idempotency_key': 'android:${DateTime.now().microsecondsSinceEpoch}:$requestType',
        'request_type': requestType,
        'title': title.text.trim(),
        'goal': goal.text.trim(),
        'provider_id': providerId,
        'account_scope': accountScope,
        if (parsedAmount != null) 'amount': parsedAmount,
        'currency': 'EUR',
        'details': Map<String, dynamic>.from(decoded),
      };
      await context.read<AppState>().api.postJson('/api/fulfillment/requests', body);
      await _refresh();
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    }
  }

  Future<void> _configureProvider([Map<String, dynamic>? existing]) async {
    final editing = existing != null;
    final slug = TextEditingController(text: '${existing?['slug'] ?? ''}');
    final name = TextEditingController(text: '${existing?['name'] ?? ''}');
    final supportPhone = TextEditingController(text: '${existing?['support_phone'] ?? ''}');
    final existingRecipe = existing?['recipe'];
    final recipe = TextEditingController(
      text: const JsonEncoder.withIndent('  ').convert(existingRecipe is Map ? existingRecipe : <String, dynamic>{}),
    );
    var providerType = '${existing?['provider_type'] ?? 'merchant'}';
    var accountScope = '${existing?['account_scope'] ?? 'personal'}';
    int? portalId = (existing?['browser_portal_id'] as num?)?.toInt();
    var enabled = existing?['enabled'] != false;
    String? templateKey;

    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) {
          final matchingPortals = portals
              .where((row) => '${row['account_scope'] ?? 'personal'}' == accountScope)
              .toList();
          if (portalId != null && !matchingPortals.any((row) => (row['id'] as num?)?.toInt() == portalId)) {
            portalId = null;
          }
          Map<String, dynamic>? selectedTemplate;
          if (templateKey != null) {
            for (final row in providerTemplates) {
              if ('${row['key']}' == templateKey) {
                selectedTemplate = row;
                break;
              }
            }
          }
          final selectedTemplateNotes = '${selectedTemplate?['notes'] ?? ''}';
          return AlertDialog(
            title: Text(editing ? 'Edit fulfillment provider' : 'Configure fulfillment provider'),
            content: SizedBox(
              width: 620,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'Link the provider to an allowlisted Secure Browser portal and/or a verified support phone. Provider recipes define the real steps and required provider evidence.',
                    ),
                    const SizedBox(height: 12),
                    if (!editing && providerTemplates.isNotEmpty) ...[
                      DropdownButtonFormField<String?>(
                        initialValue: templateKey,
                        decoration: const InputDecoration(
                          labelText: 'Starter template (optional)',
                          helperText: 'Templates provide conservative real-provider recipes; you can edit every field afterwards.',
                        ),
                        items: [
                          const DropdownMenuItem<String?>(value: null, child: Text('Start from scratch')),
                          for (final template in providerTemplates)
                            DropdownMenuItem<String?>(
                              value: '${template['key']}',
                              child: Text('${template['name']}'),
                            ),
                        ],
                        onChanged: (value) => setDialogState(() {
                          templateKey = value;
                          if (value == null) return;
                          final template = providerTemplates.firstWhere((row) => '${row['key']}' == value);
                          name.text = '${template['provider_name'] ?? template['name'] ?? ''}';
                          slug.text = '${template['slug'] ?? ''}';
                          providerType = '${template['provider_type'] ?? 'carrier'}';
                          final templateRecipe = template['recipe'];
                          recipe.text = const JsonEncoder.withIndent('  ').convert(
                            templateRecipe is Map ? templateRecipe : <String, dynamic>{},
                          );
                          final baseUrl = '${template['portal_base_url'] ?? ''}'.trim();
                          if (baseUrl.isNotEmpty) {
                            for (final portal in portals) {
                              if ('${portal['account_scope'] ?? 'personal'}' != accountScope) continue;
                              final portalUrl = '${portal['base_url'] ?? ''}';
                              if (portalUrl.startsWith(baseUrl) || baseUrl.startsWith(portalUrl)) {
                                portalId = (portal['id'] as num?)?.toInt();
                                break;
                              }
                            }
                          }
                        }),
                      ),
                      const SizedBox(height: 10),
                    ],
                    TextField(controller: name, decoration: const InputDecoration(labelText: 'Provider name')),
                    const SizedBox(height: 10),
                    TextField(
                      controller: slug,
                      enabled: !editing,
                      decoration: InputDecoration(
                        labelText: 'Slug',
                        helperText: editing ? 'The stable slug is kept when editing.' : null,
                      ),
                    ),
                    const SizedBox(height: 10),
                    DropdownButtonFormField<String>(
                      initialValue: providerType,
                      decoration: const InputDecoration(labelText: 'Provider type'),
                      items: const [
                        DropdownMenuItem(value: 'merchant', child: Text('Merchant')),
                        DropdownMenuItem(value: 'airline', child: Text('Airline')),
                        DropdownMenuItem(value: 'hotel', child: Text('Hotel')),
                        DropdownMenuItem(value: 'travel', child: Text('Travel')),
                        DropdownMenuItem(value: 'carrier', child: Text('Carrier')),
                        DropdownMenuItem(value: 'service', child: Text('Service')),
                        DropdownMenuItem(value: 'general', child: Text('General')),
                      ],
                      onChanged: (value) => setDialogState(() => providerType = value ?? providerType),
                    ),
                    const SizedBox(height: 10),
                    DropdownButtonFormField<String>(
                      initialValue: accountScope,
                      decoration: const InputDecoration(labelText: 'Scope'),
                      items: const [
                        DropdownMenuItem(value: 'personal', child: Text('Personal')),
                        DropdownMenuItem(value: 'pro', child: Text('Pro')),
                      ],
                      onChanged: (value) => setDialogState(() {
                        accountScope = value ?? accountScope;
                        portalId = null;
                      }),
                    ),
                    const SizedBox(height: 10),
                    DropdownButtonFormField<int?>(
                      initialValue: portalId,
                      decoration: const InputDecoration(labelText: 'Secure Browser portal'),
                      items: [
                        const DropdownMenuItem<int?>(value: null, child: Text('No browser portal')),
                        for (final portal in matchingPortals)
                          DropdownMenuItem<int?>(
                            value: (portal['id'] as num?)?.toInt(),
                            child: Text('${portal['name']}'),
                          ),
                      ],
                      onChanged: (value) => setDialogState(() => portalId = value),
                    ),
                    if (matchingPortals.isEmpty) ...[
                      const SizedBox(height: 6),
                      const Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          'No matching portal exists yet. Add it under Work → Portals first.',
                          style: TextStyle(fontSize: 12, color: VaTheme.textMuted),
                        ),
                      ),
                    ],
                    const SizedBox(height: 10),
                    TextField(
                      controller: supportPhone,
                      keyboardType: TextInputType.phone,
                      decoration: const InputDecoration(labelText: 'Verified support phone (optional)'),
                    ),
                    const SizedBox(height: 10),
                    SwitchListTile.adaptive(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Provider enabled'),
                      subtitle: const Text('Disabled providers remain saved but cannot execute new fulfillment work.'),
                      value: enabled,
                      onChanged: (value) => setDialogState(() => enabled = value),
                    ),
                    if (templateKey != null) ...[
                      const SizedBox(height: 6),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          selectedTemplateNotes,
                          style: const TextStyle(fontSize: 12, color: VaTheme.textMuted),
                        ),
                      ),
                    ],
                    const SizedBox(height: 6),
                    TextField(
                      controller: recipe,
                      minLines: 6,
                      maxLines: 14,
                      decoration: const InputDecoration(
                        labelText: 'Provider recipe JSON',
                        helperText: 'Keys may include purchase, travel, track, return, refund, cancel, support.',
                      ),
                    ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: Text(editing ? 'Save changes' : 'Save')),
            ],
          );
        },
      ),
    );
    if (save != true || !mounted || name.text.trim().isEmpty || slug.text.trim().isEmpty) return;
    try {
      final decoded = jsonDecode(recipe.text.trim().isEmpty ? '{}' : recipe.text.trim());
      if (decoded is! Map) throw const FormatException('Recipe JSON must be an object.');
      await context.read<AppState>().api.postJson('/api/fulfillment/providers', {
        'slug': slug.text.trim(),
        'name': name.text.trim(),
        'provider_type': providerType,
        'browser_portal_id': portalId,
        'account_scope': accountScope,
        'support_phone': supportPhone.text.trim(),
        'recipe': Map<String, dynamic>.from(decoded),
        'enabled': enabled,
      });
      await _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(editing ? 'Provider updated.' : 'Provider configured.')),
        );
      }
    } catch (requestError) {
      if (mounted) setState(() => error = '$requestError');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Fulfillment'),
        actions: [
          IconButton(tooltip: 'Refresh', onPressed: loading ? null : _refresh, icon: const Icon(Icons.refresh_rounded)),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 30),
          children: [
            _StatusCard(status: status),
            if (error != null) ...[
              const SizedBox(height: 10),
              Card(child: ListTile(leading: const Icon(Icons.error_outline, color: VaTheme.danger), title: Text(error!))),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(onPressed: loading ? null : _createRequest, icon: const Icon(Icons.add_task_rounded), label: const Text('New objective')),
                FilledButton.tonalIcon(onPressed: loading ? null : _configureProvider, icon: const Icon(Icons.storefront_outlined), label: const Text('Add provider')),
                FilledButton.tonalIcon(onPressed: loading ? null : _reconcile, icon: const Icon(Icons.sync_rounded), label: const Text('Reconcile now')),
              ],
            ),
            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(child: Text('Configured providers', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900))),
                Text('${providers.length}'),
              ],
            ),
            const SizedBox(height: 8),
            if (providers.isEmpty)
              const Card(child: ListTile(title: Text('No provider configured.'), subtitle: Text('Orders and support cases can still be owned, but external execution remains blocked_capability until a real executor is configured.')))
            else
              for (final provider in providers) _ProviderTile(provider: provider, onEdit: () => _configureProvider(provider)),
            const Divider(height: 32),
            Row(
              children: [
                Expanded(child: Text('Owned fulfillment', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900))),
                Text('${requests.length}'),
              ],
            ),
            const SizedBox(height: 8),
            if (loading && requests.isEmpty)
              const Center(child: Padding(padding: EdgeInsets.all(30), child: CircularProgressIndicator()))
            else if (requests.isEmpty)
              const Card(child: ListTile(title: Text('No fulfillment objectives yet.')))
            else
              for (final request in requests) _RequestCard(request: request, onRun: _run, onAuthorize: _authorize, onDismissOrder: _dismissOrder),
          ],
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.status});
  final Map<String, dynamic> status;

  @override
  Widget build(BuildContext context) {
    final needs = (status['needs_user'] as num?)?.toInt() ?? 0;
    final blocked = (status['blocked'] as num?)?.toInt() ?? 0;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.local_shipping_outlined, color: VaTheme.secondary),
                SizedBox(width: 8),
                Expanded(child: Text('Purchasing · Travel · Logistics · Customer Service', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w900))),
              ],
            ),
            const SizedBox(height: 8),
            const Text('VAAPP owns the objective through real provider execution and postcondition verification. Browser/payment intent is never treated as proof of completion.'),
            const SizedBox(height: 10),
            Wrap(
              spacing: 16,
              runSpacing: 8,
              children: [
                Text('${status['open'] ?? 0} open', style: const TextStyle(fontWeight: FontWeight.w800)),
                Text('$needs need you', style: TextStyle(fontWeight: FontWeight.w800, color: needs > 0 ? VaTheme.warning : null)),
                Text('$blocked blocked', style: TextStyle(fontWeight: FontWeight.w800, color: blocked > 0 ? VaTheme.warning : null)),
                Text('${status['enabled_providers'] ?? 0} providers', style: const TextStyle(fontWeight: FontWeight.w800)),
                Text('${status['tracking_waiting'] ?? 0} actively tracked', style: const TextStyle(fontWeight: FontWeight.w800)),
              ],
            ),
            const SizedBox(height: 8),
            Text('Standing purchase authorization: ${status['auto_purchase_enabled'] == true ? 'enabled' : 'off'} · Travel: ${status['auto_travel_enabled'] == true ? 'enabled' : 'off'} · Tracking: ${status['tracking_enabled'] == true ? 'enabled' : 'off'}'),
          ],
        ),
      ),
    );
  }
}

class _ProviderTile extends StatelessWidget {
  const _ProviderTile({required this.provider, required this.onEdit});
  final Map<String, dynamic> provider;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final portalName = '${provider['browser_portal_name'] ?? ''}'.trim();
    final portalLabel = portalName.isEmpty ? 'no browser portal' : portalName;
    final actions = (provider['recipe_actions'] as List?)?.join(', ') ?? 'none';
    return Card(
      child: ListTile(
        onTap: onEdit,
        leading: Icon(provider['enabled'] == true ? Icons.verified_outlined : Icons.pause_circle_outline),
        title: Text('${provider['name']}', style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text(
          '${provider['provider_type']} · ${provider['account_scope']} · $portalLabel · recipes $actions'
          '${provider['support_phone_configured'] == true ? ' · support phone configured' : ''}\nTap to edit',
        ),
        trailing: IconButton(
          tooltip: 'Edit provider',
          onPressed: onEdit,
          icon: const Icon(Icons.edit_outlined),
        ),
      ),
    );
  }
}

class _RequestCard extends StatelessWidget {
  const _RequestCard({required this.request, required this.onRun, required this.onAuthorize, required this.onDismissOrder});
  final Map<String, dynamic> request;
  final Future<void> Function(int id) onRun;
  final Future<void> Function(int id) onAuthorize;
  final Future<void> Function(int orderId) onDismissOrder;

  bool get _terminal => const {'completed', 'cancelled', 'failed'}.contains('${request['status']}');

  @override
  Widget build(BuildContext context) {
    final id = (request['id'] as num).toInt();
    final needsUser = request['requires_user_action'] == true || request['status'] == 'needs_user';
    final paymentType = request['request_type'] == 'purchase' || request['request_type'] == 'travel';
    final sourceOrderId = (request['order_id'] as num?)?.toInt();
    final amount = request['amount'] == null ? '' : ' · ${request['amount']} ${request['currency'] ?? 'EUR'}';
    final tracking = request['tracking'] is Map ? Map<String, dynamic>.from(request['tracking'] as Map) : <String, dynamic>{};
    final trackingState = '${tracking['state'] ?? ''}';
    final trackingObserved = _shortTime('${tracking['observed_at'] ?? ''}');
    final nextCheck = _shortTime('${tracking['next_check_at'] ?? ''}');
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(_icon('${request['request_type']}'), color: needsUser ? VaTheme.warning : VaTheme.secondary),
                const SizedBox(width: 8),
                Expanded(child: Text('${request['title']}', style: const TextStyle(fontWeight: FontWeight.w900, fontSize: 16))),
                Text('#$id', style: const TextStyle(color: VaTheme.textMuted)),
              ],
            ),
            const SizedBox(height: 6),
            Text('${request['request_type']} · ${request['status']} · ${request['account_scope']}$amount'),
            if ('${request['provider_name'] ?? ''}'.isNotEmpty) Text('Provider: ${request['provider_name']}'),
            if (request['request_type'] == 'logistics' && trackingState.isNotEmpty) ...[
              const SizedBox(height: 6),
              Row(
                children: [
                  Icon(
                    trackingState == 'delivered'
                        ? Icons.check_circle_outline_rounded
                        : trackingState == 'available_for_pickup'
                            ? Icons.store_mall_directory_outlined
                            : Icons.radar_rounded,
                    size: 18,
                    color: tracking['stalled'] == true ? VaTheme.warning : VaTheme.secondary,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      'Tracking: ${trackingState.replaceAll('_', ' ')}'
                      '${trackingObserved.isEmpty ? '' : ' · observed $trackingObserved'}'
                      '${nextCheck.isEmpty || trackingState == 'delivered' ? '' : ' · next check $nextCheck'}',
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                  ),
                ],
              ),
            ],
            if ('${request['needs_user_reason'] ?? ''}'.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text('${request['needs_user_reason']}', style: const TextStyle(color: VaTheme.warning, fontWeight: FontWeight.w700)),
            ],
            if ('${request['last_error'] ?? ''}'.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text('${request['last_error']}', style: const TextStyle(color: VaTheme.danger)),
            ],
            if ('${request['authorization_basis'] ?? ''}'.isNotEmpty)
              Text('Authorization: ${request['authorization_basis']}', style: const TextStyle(color: VaTheme.textMuted)),
            if (!_terminal) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FilledButton.tonal(onPressed: () => onRun(id), child: const Text('Run / reconcile')),
                  if (needsUser && paymentType)
                    FilledButton(onPressed: () => onAuthorize(id), child: const Text('Authorize payment')),
                  if (sourceOrderId != null && sourceOrderId > 0)
                    TextButton.icon(
                      onPressed: () => onDismissOrder(sourceOrderId),
                      icon: const Icon(Icons.receipt_long_outlined),
                      label: const Text('Not an order'),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  static String _shortTime(String raw) {
    if (raw.trim().isEmpty || raw == 'null') return '';
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return raw;
    final local = parsed.toLocal();
    final day = local.day.toString().padLeft(2, '0');
    final month = local.month.toString().padLeft(2, '0');
    final hour = local.hour.toString().padLeft(2, '0');
    final minute = local.minute.toString().padLeft(2, '0');
    return '$day/$month $hour:$minute';
  }

  IconData _icon(String type) => switch (type) {
        'purchase' => Icons.shopping_bag_outlined,
        'travel' => Icons.flight_takeoff_rounded,
        'logistics' => Icons.local_shipping_outlined,
        'return' => Icons.assignment_return_outlined,
        'refund' => Icons.currency_exchange_rounded,
        'cancel' => Icons.cancel_outlined,
        _ => Icons.support_agent_rounded,
      };
}
