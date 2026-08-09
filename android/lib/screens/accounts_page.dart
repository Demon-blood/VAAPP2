import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class AccountsPage extends StatelessWidget {
  const AccountsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final accounts = state.accounts;
    if (accounts.isEmpty) {
      return RefreshIndicator(
        onRefresh: () => context.read<AppState>().refreshMoneyData(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: const [
            SizedBox(height: 80),
            EmptyState(
              icon: Icons.account_balance_outlined,
              title: 'No bank accounts connected',
              message: 'Connect Beobank or Revolut in Services. Only bank-returned accounts are shown.',
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().syncBanks(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        children: [
          _AccountsSummary(accounts: accounts),
          const SizedBox(height: 12),
          for (final account in accounts) ...[
            _AccountCard(account: account),
            const SizedBox(height: 9),
          ],
        ],
      ),
    );
  }
}

class _AccountsSummary extends StatelessWidget {
  const _AccountsSummary({required this.accounts});

  final List<Map<String, dynamic>> accounts;

  @override
  Widget build(BuildContext context) {
    final enabled = accounts.where((account) => account['enabled_for_payments'] == true).length;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: const LinearGradient(colors: [Color(0xFF172157), Color(0xFF0A1D3B)]),
        border: Border.all(color: VaTheme.secondary.withValues(alpha: .42)),
      ),
      child: Row(
        children: [
          Container(
            width: 54,
            height: 54,
            decoration: BoxDecoration(
              color: VaTheme.secondary.withValues(alpha: .18),
              borderRadius: BorderRadius.circular(16),
            ),
            child: const Icon(Icons.account_balance_wallet_rounded, color: VaTheme.secondary, size: 30),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${accounts.length} connected account${accounts.length == 1 ? '' : 's'}', style: const TextStyle(fontWeight: FontWeight.w900, fontSize: 18)),
                const SizedBox(height: 3),
                Text('$enabled enabled for approved payments', style: const TextStyle(color: VaTheme.textMuted)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _AccountCard extends StatelessWidget {
  const _AccountCard({required this.account});

  final Map<String, dynamic> account;

  @override
  Widget build(BuildContext context) {
    final synced = DateTime.tryParse('${account['last_synced_at'] ?? ''}');
    final enabled = account['enabled_for_payments'] == true;
    final currency = '${account['currency'] ?? 'EUR'}';
    return VaSectionCard(
      child: InkWell(
        onTap: () => _editPolicy(context),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 46,
                  height: 46,
                  decoration: BoxDecoration(
                    color: VaTheme.primary.withValues(alpha: .18),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: const Icon(Icons.account_balance_rounded, color: VaTheme.primaryBright),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${account['name']}', style: const TextStyle(fontWeight: FontWeight.w900)),
                      Text('${account['iban'] ?? ''}', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: VaTheme.textMuted)),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(money(account['available_balance'] ?? account['current_balance'], currency), style: const TextStyle(fontWeight: FontWeight.w900, fontSize: 16)),
                    const SizedBox(height: 3),
                    Icon(enabled ? Icons.check_circle_rounded : Icons.lock_outline_rounded, color: enabled ? VaTheme.success : VaTheme.textMuted, size: 19),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 11),
            Wrap(
              spacing: 7,
              runSpacing: 7,
              children: [
                Chip(label: Text('${account['account_scope'] ?? 'personal'}')),
                Chip(label: Text('Reserve ${money(account['safety_reserve'], currency)}')),
                Chip(label: Text(enabled ? 'Payments enabled' : 'Payments locked')),
              ],
            ),
            if (synced != null) ...[
              const SizedBox(height: 7),
              Text('Synced ${DateFormat('dd/MM HH:mm').format(synced)}', style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _editPolicy(BuildContext context) async {
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
