import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class BillsPage extends StatelessWidget {
  const BillsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final outstanding = state.bills.where((bill) => !['paid', 'cancelled'].contains('${bill['status']}')).toList();
    if (state.bills.isEmpty) {
      return const EmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No detected bills',
        message: 'Bills appear only after the live Gmail processor validates invoice information.',
      );
    }
    final total = outstanding.fold<double>(0, (sum, bill) => sum + ((bill['amount'] as num?)?.toDouble() ?? double.tryParse('${bill['amount']}') ?? 0));
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        itemCount: state.bills.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: _BillsSummary(count: outstanding.length, total: total),
            );
          }
          final bill = state.bills[index - 1];
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _BillCard(bill: bill, accounts: state.accounts),
          );
        },
      ),
    );
  }
}

class _BillsSummary extends StatelessWidget {
  const _BillsSummary({required this.count, required this.total});

  final int count;
  final double total;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(22),
          gradient: const LinearGradient(colors: [Color(0xFF123625), Color(0xFF10273B)]),
          border: Border.all(color: VaTheme.success.withValues(alpha: .5)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Outstanding bills', style: TextStyle(color: VaTheme.textMuted, fontWeight: FontWeight.w600)),
            const SizedBox(height: 5),
            Row(
              children: [
                Expanded(child: Text('EUR ${total.toStringAsFixed(2)}', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w900, color: VaTheme.success))),
                Text('$count bill${count == 1 ? '' : 's'}', style: const TextStyle(fontWeight: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: count == 0 ? null : () => _runAutoPay(context),
                icon: const Icon(Icons.bolt_rounded),
                label: const Text('Run eligible auto-pay now'),
              ),
            ),
            const SizedBox(height: 6),
            const Text(
              'Only approved creditors/accounts within your limits and safety reserve can be initiated. Bank-required SCA still appears under Payment approvals.',
              style: TextStyle(color: VaTheme.textMuted, fontSize: 12, height: 1.35),
            ),
          ],
        ),
      );

  Future<void> _runAutoPay(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runAutomaticPaymentsNow();
      if (!context.mounted) return;
      final auto = result['auto_pay'] as Map?;
      final initiated = (auto?['initiated'] as num?)?.toInt() ?? 0;
      final failed = (auto?['failed'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Auto-pay run: $initiated initiated${failed > 0 ? ', $failed blocked/failed' : ''}.')),
      );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}

class _BillCard extends StatelessWidget {
  const _BillCard({required this.bill, required this.accounts});

  final Map<String, dynamic> bill;
  final List<Map<String, dynamic>> accounts;

  @override
  Widget build(BuildContext context) {
    final due = DateTime.tryParse('${bill['due_at'] ?? ''}');
    final status = '${bill['status'] ?? ''}';
    final needsCreditor = status == 'requires_review' || status == 'detected';
    final canPay = !['paid', 'cancelled', 'payment_initiated'].contains(status);
    final statusColor = switch (status) {
      'paid' => VaTheme.success,
      'requires_review' || 'detected' => VaTheme.warning,
      'payment_initiated' => VaTheme.secondary,
      _ => VaTheme.primary,
    };
    return VaSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(color: statusColor.withValues(alpha: .14), borderRadius: BorderRadius.circular(14)),
                child: Icon(Icons.receipt_long_rounded, color: statusColor),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${bill['creditor_name']}', style: const TextStyle(fontWeight: FontWeight.w800)),
                    Text(status.replaceAll('_', ' '), style: TextStyle(color: statusColor, fontWeight: FontWeight.w700)),
                  ],
                ),
              ),
              Text(money(bill['amount'], '${bill['currency'] ?? 'EUR'}'), style: const TextStyle(fontWeight: FontWeight.w900)),
            ],
          ),
          const SizedBox(height: 10),
          if (due != null) Text('Due: ${DateFormat('dd MMM yyyy').format(due)}'),
          if ('${bill['invoice_number'] ?? ''}'.isNotEmpty) Text('Invoice: ${bill['invoice_number']}'),
          if ('${bill['reference'] ?? ''}'.isNotEmpty) Text('Reference: ${bill['reference']}'),
          if ('${bill['iban'] ?? ''}'.isNotEmpty) Text('IBAN: ${bill['iban']}'),
          if ('${bill['risk_reason'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('${bill['risk_reason']}', style: const TextStyle(color: VaTheme.warning, fontWeight: FontWeight.w600)),
          ],
          if (needsCreditor || canPay) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (needsCreditor)
                  OutlinedButton.icon(
                    onPressed: () => _approveCreditor(context),
                    icon: const Icon(Icons.verified_outlined),
                    label: const Text('Approve creditor rules'),
                  ),
                if (canPay)
                  FilledButton.icon(
                    onPressed: accounts.isEmpty ? null : () => _pay(context),
                    icon: const Icon(Icons.payment_rounded),
                    label: const Text('Process payment'),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Future<void> _approveCreditor(BuildContext context) async {
    final maximumController = TextEditingController(text: '${bill['amount']}');
    String scope = '${bill['account_scope'] ?? 'personal'}';
    final approved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Approve creditor'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                ListTile(contentPadding: EdgeInsets.zero, title: Text('${bill['creditor_name']}'), subtitle: Text('${bill['iban'] ?? 'No IBAN'}')),
                TextField(
                  controller: maximumController,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(labelText: 'Maximum automatic payment'),
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  initialValue: scope,
                  decoration: const InputDecoration(labelText: 'Expense scope'),
                  items: const [
                    DropdownMenuItem(value: 'personal', child: Text('Personal')),
                    DropdownMenuItem(value: 'pro', child: Text('Revolut Pro')),
                  ],
                  onChanged: (value) => setState(() => scope = value ?? scope),
                ),
                const SizedBox(height: 12),
                const Text('Approval stores this exact IBAN and limit. A changed IBAN is not accepted automatically.'),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Approve')),
          ],
        ),
      ),
    );
    if (approved != true || !context.mounted) return;
    final maximum = double.tryParse(maximumController.text.replaceAll(',', '.'));
    if (maximum == null || maximum <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Enter a valid payment limit.')));
      return;
    }
    try {
      await context.read<AppState>().approveCreditor(
            name: '${bill['creditor_name']}',
            iban: '${bill['iban']}',
            accountScope: scope,
            maximum: maximum,
          );
      if (context.mounted) {
        await context.read<AppState>().runAutomaticPaymentsNow();
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _pay(BuildContext context) async {
    int? selectedId = accounts
        .where((account) => account['enabled_for_payments'] == true)
        .map((account) => account['id'] as int)
        .firstOrNull;
    final selected = await showDialog<int>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Select payment account'),
          content: DropdownButtonFormField<int>(
            initialValue: selectedId,
            decoration: const InputDecoration(labelText: 'Approved account'),
            items: accounts
                .where((account) => account['enabled_for_payments'] == true)
                .map((account) => DropdownMenuItem<int>(value: account['id'] as int, child: Text('${account['name']} · ${account['account_scope']}')))
                .toList(),
            onChanged: (value) => setState(() => selectedId = value),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Cancel')),
            FilledButton(onPressed: selectedId == null ? null : () => Navigator.pop(dialogContext, selectedId), child: const Text('Continue')),
          ],
        ),
      ),
    );
    if (selected == null || !context.mounted) return;
    try {
      final payment = await context.read<AppState>().createPayment(bill['id'] as int, selected);
      final authorizationUrl = payment['authorization_url'] as String?;
      if (authorizationUrl != null && authorizationUrl.isNotEmpty) {
        await launchUrl(Uri.parse(authorizationUrl), mode: LaunchMode.externalApplication);
      } else if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Payment status: ${payment['status']}')));
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}

extension FirstOrNull<E> on Iterable<E> {
  E? get firstOrNull => isEmpty ? null : first;
}
