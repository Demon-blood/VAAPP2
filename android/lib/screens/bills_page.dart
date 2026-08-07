import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../widgets/common_widgets.dart';

class BillsPage extends StatelessWidget {
  const BillsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    if (state.bills.isEmpty) {
      return const EmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No detected bills',
        message: 'Bills appear only after the live Gmail processor validates invoice information.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: state.bills.length,
      itemBuilder: (context, index) {
        final bill = state.bills[index];
        final due = DateTime.tryParse('${bill['due_at'] ?? ''}');
        final status = '${bill['status'] ?? ''}';
        final needsCreditor = status == 'requires_review';
        final canPay = !['paid', 'cancelled', 'payment_initiated'].contains(status);
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${bill['creditor_name']}',
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                    Text(
                      money(bill['amount'], '${bill['currency'] ?? 'EUR'}'),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text('Status: $status'),
                if (due != null) Text('Due: ${DateFormat('dd MMM yyyy').format(due)}'),
                if ('${bill['invoice_number'] ?? ''}'.isNotEmpty)
                  Text('Invoice: ${bill['invoice_number']}'),
                if ('${bill['reference'] ?? ''}'.isNotEmpty)
                  Text('Reference: ${bill['reference']}'),
                if ('${bill['iban'] ?? ''}'.isNotEmpty) Text('IBAN: ${bill['iban']}'),
                if ('${bill['risk_reason'] ?? ''}'.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(
                    '${bill['risk_reason']}',
                    style: TextStyle(color: Theme.of(context).colorScheme.error),
                  ),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  children: [
                    if (needsCreditor)
                      OutlinedButton.icon(
                        onPressed: () => _approveCreditor(context, bill),
                        icon: const Icon(Icons.verified_outlined),
                        label: const Text('Approve creditor rules'),
                      ),
                    if (canPay)
                      FilledButton.icon(
                        onPressed: state.accounts.isEmpty ? null : () => _pay(context, bill, state.accounts),
                        icon: const Icon(Icons.payment),
                        label: const Text('Process payment'),
                      ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Future<void> _approveCreditor(BuildContext context, Map<String, dynamic> bill) async {
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
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text('${bill['creditor_name']}'),
                  subtitle: Text('${bill['iban'] ?? 'No IBAN'}'),
                ),
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
                const Text(
                  'Approval stores this exact IBAN and limit. A changed IBAN is not accepted automatically.',
                ),
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
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }

  Future<void> _pay(
    BuildContext context,
    Map<String, dynamic> bill,
    List<Map<String, dynamic>> accounts,
  ) async {
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
                .map(
                  (account) => DropdownMenuItem<int>(
                    value: account['id'] as int,
                    child: Text('${account['name']} · ${account['account_scope']}'),
                  ),
                )
                .toList(),
            onChanged: (value) => setState(() => selectedId = value),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Cancel')),
            FilledButton(
              onPressed: selectedId == null ? null : () => Navigator.pop(dialogContext, selectedId),
              child: const Text('Continue'),
            ),
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
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Payment status: ${payment['status']}')),
        );
      }
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}

extension FirstOrNull<E> on Iterable<E> {
  E? get firstOrNull => isEmpty ? null : first;
}
