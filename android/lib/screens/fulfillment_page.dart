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
        api.getJson('/api/fulfillment/requests?limit=200'),
      ]);
      if (!mounted) return;
      setState(() {
        status = result[0] is Map ? Map<String, dynamic>.from(result[0] as Map) : {};
        providers = _rows(result[1]);
        requests = _rows(result[2]);
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

  Future<void> _configureProvider() async {
    final slug = TextEditingController();
    final name = TextEditingController();
    final portalId = TextEditingController();
    final supportPhone = TextEditingController();
    final recipe = TextEditingController(text: '{}');
    var providerType = 'merchant';
    var accountScope = 'personal';
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('Configure fulfillment provider'),
          content: SizedBox(
            width: 620,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'A provider is not a generic web crawler. Link it to an existing allowlisted Secure Browser portal and/or a verified support phone. Recipes define the provider-specific real steps and required postcondition.',
                  ),
                  const SizedBox(height: 12),
                  TextField(controller: name, decoration: const InputDecoration(labelText: 'Provider name')),
                  const SizedBox(height: 10),
                  TextField(controller: slug, decoration: const InputDecoration(labelText: 'Slug')),
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
                    onChanged: (value) => setDialogState(() => accountScope = value ?? accountScope),
                  ),
                  const SizedBox(height: 10),
                  TextField(controller: portalId, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Secure Browser portal ID (optional)')),
                  const SizedBox(height: 10),
                  TextField(controller: supportPhone, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Verified support phone (optional)')),
                  const SizedBox(height: 10),
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
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
          ],
        ),
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
        'browser_portal_id': int.tryParse(portalId.text.trim()),
        'account_scope': accountScope,
        'support_phone': supportPhone.text.trim(),
        'recipe': Map<String, dynamic>.from(decoded),
        'enabled': true,
      });
      await _refresh();
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
              for (final provider in providers) _ProviderTile(provider: provider),
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
              for (final request in requests) _RequestCard(request: request, onRun: _run, onAuthorize: _authorize),
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
  const _ProviderTile({required this.provider});
  final Map<String, dynamic> provider;

  @override
  Widget build(BuildContext context) => Card(
        child: ListTile(
          leading: Icon(provider['enabled'] == true ? Icons.verified_outlined : Icons.pause_circle_outline),
          title: Text('${provider['name']}', style: const TextStyle(fontWeight: FontWeight.w800)),
          subtitle: Text('${provider['provider_type']} · ${provider['account_scope']} · portal ${provider['browser_portal_id'] ?? '—'} · recipes ${(provider['recipe_actions'] as List?)?.join(', ') ?? 'none'}${provider['support_phone_configured'] == true ? ' · support phone configured' : ''}'),
        ),
      );
}

class _RequestCard extends StatelessWidget {
  const _RequestCard({required this.request, required this.onRun, required this.onAuthorize});
  final Map<String, dynamic> request;
  final Future<void> Function(int id) onRun;
  final Future<void> Function(int id) onAuthorize;

  bool get _terminal => const {'completed', 'cancelled', 'failed'}.contains('${request['status']}');

  @override
  Widget build(BuildContext context) {
    final id = (request['id'] as num).toInt();
    final needsUser = request['requires_user_action'] == true || request['status'] == 'needs_user';
    final paymentType = request['request_type'] == 'purchase' || request['request_type'] == 'travel';
    final amount = request['amount'] == null ? '' : ' · ${request['amount']} ${request['currency'] ?? 'EUR'}';
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
                ],
              ),
            ],
          ],
        ),
      ),
    );
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
