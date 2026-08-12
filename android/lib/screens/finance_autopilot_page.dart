import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class FinanceAutopilotPage extends StatelessWidget {
  const FinanceAutopilotPage({super.key});

  String _money(dynamic value, [String currency = 'EUR']) {
    final amount = double.tryParse('$value') ?? 0;
    return '${amount.toStringAsFixed(2)} $currency';
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final overview = state.financeOverview;
    final history = Map<String, dynamic>.from((overview['statement_history'] as Map?) ?? const {});
    final envelopes = (overview['envelopes'] as List? ?? const []).cast<Map>();
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshMoneyData(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 30),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Financial Autopilot', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900)),
                  const SizedBox(height: 8),
                  Text('Available across connected accounts: ${_money(overview['total_available'])}'),
                  Text('Internal transfers in progress: ${overview['pending_internal_transfers'] ?? 0}'),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: state.busy ? null : () => _runNow(context),
                    icon: const Icon(Icons.auto_awesome_rounded),
                    label: const Text('Run budgeting & rebalancing now'),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: state.busy ? null : () => _importHistory(context),
                    icon: const Icon(Icons.upload_file_rounded),
                    label: const Text('Import financial history'),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          if ((history['statements'] as num? ?? 0) > 0) ...[
            _StatementHistoryCard(history: history),
            const SizedBox(height: 12),
          ],
          Text('Budget envelopes', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
          const SizedBox(height: 6),
          const Text('Limits default to learned spending when configured limit is 0. Tap an envelope to override it.'),
          const SizedBox(height: 10),
          for (final raw in envelopes) _BudgetCard(envelope: Map<String, dynamic>.from(raw)),
          const SizedBox(height: 18),
          Text('Account roles & cash reserves', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
          const SizedBox(height: 8),
          for (final account in state.accounts) _PolicyCard(account: account),
          const SizedBox(height: 18),
          Text('Own-account transfers', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
          const SizedBox(height: 8),
          if (state.internalTransfers.isEmpty)
            const Card(child: ListTile(title: Text('No automatic own-account transfers yet.')))
          else
            for (final transfer in state.internalTransfers.take(30)) _TransferCard(transfer: transfer),
        ],
      ),
    );
  }

  Future<void> _runNow(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runFinancialAutopilotNow();
      if (!context.mounted) return;
      final planned = (result['budget'] as Map?)?['initiated'] ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Financial Autopilot completed. Transfers planned: $planned')));
    } catch (_) {}
  }

  Future<void> _importHistory(BuildContext context) async {
    var scope = 'personal';
    final selectedScope = await showDialog<String>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Import financial history'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Import Beobank PDFs or Revolut XLSX/PDF exports. When both Revolut formats are selected, XLSX is used for the ledger and PDF enriches it without double counting. Choose the ownership scope only as a fallback for accounts that are not connected yet.'),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: scope,
                decoration: const InputDecoration(labelText: 'Account scope'),
                items: const [
                  DropdownMenuItem(value: 'personal', child: Text('Personal')),
                  DropdownMenuItem(value: 'pro', child: Text('Pro / business')),
                ],
                onChanged: (value) => setState(() => scope = value ?? scope),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, scope), child: const Text('Choose bank files')),
          ],
        ),
      ),
    );
    if (selectedScope == null || !context.mounted) return;
    final picked = await FilePicker.pickFiles(
      allowMultiple: true,
      type: FileType.custom,
      allowedExtensions: const ['pdf', 'xlsx'],
    );
    if (picked == null || !context.mounted) return;
    final paths = picked.files.map((file) => file.path).whereType<String>().toList();
    if (paths.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('The selected bank files could not be opened.')));
      return;
    }
    try {
      final result = await context.read<AppState>().importFinancialHistory(paths, accountScope: selectedScope);
      if (!context.mounted) return;
      final imported = result['imported'] ?? 0;
      final duplicates = result['duplicates'] ?? 0;
      final errors = (result['errors'] as List? ?? const []).length;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Financial history imported: $imported new · $duplicates duplicate · $errors rejected')),
      );
    } catch (_) {}
  }
}

class _StatementHistoryCard extends StatelessWidget {
  const _StatementHistoryCard({required this.history});
  final Map<String, dynamic> history;

  String _shortDate(dynamic value) {
    final parsed = DateTime.tryParse('$value');
    if (parsed == null) return '—';
    return '${parsed.day.toString().padLeft(2, '0')}/${parsed.month.toString().padLeft(2, '0')}/${parsed.year}';
  }

  @override
  Widget build(BuildContext context) {
    final verified = history['balance_chain_verified'] == true;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(verified ? Icons.verified_rounded : Icons.warning_amber_rounded, color: verified ? VaTheme.success : VaTheme.warning),
                const SizedBox(width: 9),
                const Expanded(child: Text('Imported financial history', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 17))),
              ],
            ),
            const SizedBox(height: 9),
            Text('${history['statements'] ?? 0} statements · ${history['transactions'] ?? 0} transactions'),
            Text('${_shortDate(history['period_start'])} → ${_shortDate(history['period_end'])}'),
            Text('${history['matched_to_bank'] ?? 0} matched to Enable Banking · ${history['historical_only'] ?? 0} historical-only'),
            Text('${history['internal_transfers'] ?? 0} own-account transfers excluded from spending'),
            Text(verified ? 'Statement balance chain verified' : 'Statement balance chain is incomplete or does not reconcile'),
          ],
        ),
      ),
    );
  }
}

class _BudgetCard extends StatelessWidget {
  const _BudgetCard({required this.envelope});
  final Map<String, dynamic> envelope;

  @override
  Widget build(BuildContext context) {
    final overspent = envelope['overspent'] == true;
    final learned = (double.tryParse('${envelope['monthly_limit'] ?? 0}') ?? 0) <= 0;
    return Card(
      child: ListTile(
        onTap: () => _edit(context),
        leading: Icon(overspent ? Icons.warning_amber_rounded : Icons.pie_chart_outline_rounded, color: overspent ? VaTheme.warning : VaTheme.secondary),
        title: Text('${envelope['category'] ?? ''}'.replaceAll('_', ' '), style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text('Spent ${envelope['spent'] ?? '0'} / ${envelope['effective_limit'] ?? '0'} EUR · ${envelope['scope'] ?? 'personal'}${learned ? ' · learned' : ''}'),
        trailing: const Icon(Icons.tune_rounded),
      ),
    );
  }

  Future<void> _edit(BuildContext context) async {
    final limit = TextEditingController(text: '${envelope['monthly_limit'] ?? '0'}');
    final reserve = TextEditingController(text: '${envelope['reserve_target'] ?? '0'}');
    final incomePercent = TextEditingController(text: '${envelope['income_allocation_percent'] ?? '0'}');
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Budget · ${envelope['category']}'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: limit, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Monthly limit (0 = learned)')),
            const SizedBox(height: 12),
            TextField(controller: reserve, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Reserve target')),
            if (envelope['category'] == 'tax') ...[
              const SizedBox(height: 12),
              TextField(
                controller: incomePercent,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: const InputDecoration(labelText: 'Income allocation % for tax reserve'),
              ),
            ],
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
        ],
      ),
    );
    if (save != true || !context.mounted) return;
    await context.read<AppState>().saveBudgetEnvelope({
      'account_scope': '${envelope['scope'] ?? 'personal'}',
      'category': '${envelope['category']}',
      'monthly_limit': limit.text.trim().isEmpty ? '0' : limit.text.trim(),
      'reserve_target': reserve.text.trim().isEmpty ? '0' : reserve.text.trim(),
      'income_allocation_percent': incomePercent.text.trim().isEmpty ? '0' : incomePercent.text.trim(),
      'priority': 50,
      'enabled': true,
    });
  }
}

class _PolicyCard extends StatelessWidget {
  const _PolicyCard({required this.account});
  final Map<String, dynamic> account;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final accountId = account['id'];
    final policy = state.financeAccountPolicies.cast<Map<String, dynamic>?>().firstWhere(
          (item) => item?['bank_account_id'] == accountId,
          orElse: () => null,
        );
    return Card(
      child: ListTile(
        onTap: policy == null ? null : () => _edit(context, policy),
        leading: const Icon(Icons.account_balance_rounded),
        title: Text('${account['name'] ?? 'Account'}', style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text(policy == null
            ? 'Autopilot policy is being initialized'
            : '${policy['role']} · transfers ${policy['internal_transfers_enabled'] == true ? 'enabled' : 'disabled'} · receives surplus ${policy['accept_surplus'] == true ? 'yes' : 'no'}'),
        trailing: const Icon(Icons.tune_rounded),
      ),
    );
  }

  Future<void> _edit(BuildContext context, Map<String, dynamic> policy) async {
    var role = '${policy['role'] ?? 'operating'}';
    var enabled = policy['internal_transfers_enabled'] == true;
    var accept = policy['accept_surplus'] == true;
    final floor = TextEditingController(text: '${policy['target_floor'] ?? '0'}');
    final ceiling = TextEditingController(text: '${policy['target_ceiling'] ?? '0'}');
    final monthly = TextEditingController(text: '${policy['monthly_outbound_limit'] ?? '5000'}');
    final minimum = TextEditingController(text: '${policy['min_transfer_amount'] ?? '50'}');
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: Text('Autopilot · ${account['name']}'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: role,
                  decoration: const InputDecoration(labelText: 'Account role'),
                  items: const ['operating', 'savings', 'reserve', 'tax', 'income', 'disabled'].map((value) => DropdownMenuItem(value: value, child: Text(value))).toList(),
                  onChanged: (value) => setState(() => role = value ?? role),
                ),
                SwitchListTile(
                  value: enabled,
                  onChanged: account['enabled_for_payments'] == true ? (value) => setState(() => enabled = value) : null,
                  title: const Text('May send own-account transfers'),
                  subtitle: account['enabled_for_payments'] == true
                      ? null
                      : const Text('Enable payment execution for this account first.'),
                ),
                SwitchListTile(value: accept, onChanged: (value) => setState(() => accept = value), title: const Text('May receive surplus cash')),
                TextField(controller: floor, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Minimum balance floor')),
                TextField(controller: ceiling, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Target ceiling (0 = dynamic)')),
                TextField(controller: monthly, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Monthly outbound limit')),
                TextField(controller: minimum, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Minimum transfer amount')),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
          ],
        ),
      ),
    );
    if (save != true || !context.mounted) return;
    await context.read<AppState>().updateFinanceAccountPolicy(account['id'] as int, {
      'role': role,
      'internal_transfers_enabled': enabled,
      'target_floor': floor.text.trim().isEmpty ? '0' : floor.text.trim(),
      'target_ceiling': ceiling.text.trim().isEmpty ? '0' : ceiling.text.trim(),
      'accept_surplus': accept,
      'monthly_outbound_limit': monthly.text.trim().isEmpty ? '0' : monthly.text.trim(),
      'min_transfer_amount': minimum.text.trim().isEmpty ? '0' : minimum.text.trim(),
    });
  }
}

class _TransferCard extends StatelessWidget {
  const _TransferCard({required this.transfer});
  final Map<String, dynamic> transfer;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    String accountName(dynamic id) => state.accounts
        .cast<Map<String, dynamic>?>()
        .firstWhere((item) => item?['id'] == id, orElse: () => null)?['name']
        ?.toString() ?? 'Account $id';
    final authorization = '${transfer['authorization_url'] ?? ''}';
    return Card(
      child: ListTile(
        title: Text('${transfer['amount']} ${transfer['currency'] ?? 'EUR'} · ${transfer['status']}', style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text('${accountName(transfer['source_account_id'])} → ${accountName(transfer['destination_account_id'])}\n${transfer['reason'] ?? ''}'),
        isThreeLine: true,
        trailing: authorization.isNotEmpty
            ? FilledButton(onPressed: () => launchUrl(Uri.parse(authorization), mode: LaunchMode.externalApplication), child: const Text('Authorize'))
            : IconButton(onPressed: () => context.read<AppState>().refreshInternalTransfer(transfer['id'] as int), icon: const Icon(Icons.refresh_rounded)),
      ),
    );
  }
}
