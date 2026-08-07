import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../widgets/common_widgets.dart';

class AccountsPage extends StatelessWidget {
  const AccountsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final accounts = context.watch<AppState>().accounts;
    if (accounts.isEmpty) {
      return const EmptyState(
        icon: Icons.account_balance_outlined,
        title: 'No bank accounts connected',
        message: 'Connect Beobank or Revolut in Settings. Only bank-returned accounts are shown.',
      );
    }
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().syncBanks(),
      child: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: accounts.length,
        itemBuilder: (context, index) {
          final account = accounts[index];
          final synced = DateTime.tryParse('${account['last_synced_at'] ?? ''}');
          return Card(
            child: ListTile(
              leading: const Icon(Icons.account_balance),
              title: Text('${account['name']}'),
              subtitle: Text([
                '${account['iban'] ?? ''}',
                'Scope: ${account['account_scope']}',
                'Reserve: ${money(account['safety_reserve'], '${account['currency'] ?? 'EUR'}')}',
                if (synced != null) 'Synced ${DateFormat('dd/MM HH:mm').format(synced)}',
              ].join('\n')),
              trailing: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(money(account['available_balance'] ?? account['current_balance'], '${account['currency'] ?? 'EUR'}')),
                  Icon(account['enabled_for_payments'] == true ? Icons.payments : Icons.lock_outline),
                ],
              ),
              isThreeLine: true,
              onTap: () => _editPolicy(context, account),
            ),
          );
        },
      ),
    );
  }

  Future<void> _editPolicy(BuildContext context, Map<String, dynamic> account) async {
    String scope = '${account['account_scope'] ?? 'personal'}';
    bool enabled = account['enabled_for_payments'] == true;
    final reserveController = TextEditingController(text: '${account['safety_reserve'] ?? 0}');
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Account automation policy'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: scope,
                  decoration: const InputDecoration(labelText: 'Account scope'),
                  items: const [
                    DropdownMenuItem(value: 'personal', child: Text('Personal')),
                    DropdownMenuItem(value: 'pro', child: Text('Revolut Pro')),
                    DropdownMenuItem(value: 'reserve', child: Text('Reserve only')),
                  ],
                  onChanged: (value) => setState(() => scope = value ?? scope),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: reserveController,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(labelText: 'Minimum safety reserve'),
                ),
                const SizedBox(height: 8),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Allow approved automatic payments'),
                  value: enabled,
                  onChanged: (value) => setState(() => enabled = value),
                ),
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
    final reserve = double.tryParse(reserveController.text.replaceAll(',', '.'));
    if (reserve == null || reserve < 0) return;
    try {
      await context.read<AppState>().updateAccountPolicy(
            accountId: account['id'] as int,
            scope: scope,
            reserve: reserve,
            enabled: enabled,
          );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}
