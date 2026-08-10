import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class ReceiptsPage extends StatelessWidget {
  const ReceiptsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final rows = state.financialRecords;
    final error = state.endpointErrors['/api/financial-records'];

    if (error != null && rows.isEmpty) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(18),
        children: [
          const SizedBox(height: 70),
          EmptyState(
            icon: Icons.receipt_long_outlined,
            title: 'Receipts could not be loaded',
            message: error,
          ),
        ],
      );
    }

    if (rows.isEmpty) {
      return RefreshIndicator(
        onRefresh: () => context.read<AppState>().refreshMoneyData(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: const [
            SizedBox(height: 90),
            EmptyState(
              icon: Icons.receipt_long_outlined,
              title: 'No receipts recorded yet',
              message: 'Paid confirmations and financial notices appear here instead of becoming payable bills.',
            ),
          ],
        ),
      );
    }

    final paid = rows.where((row) => row['record_type'] == 'paid_receipt').length;
    final reconciled = rows.where((row) => row['status'] == 'reconciled').length;

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshMoneyData(),
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        itemCount: rows.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: VaSectionCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Receipts & notices', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 18)),
                    const SizedBox(height: 6),
                    Text('$paid paid receipt${paid == 1 ? '' : 's'} · $reconciled matched to bank activity', style: const TextStyle(color: VaTheme.textMuted)),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        onPressed: () => _reconcile(context),
                        icon: const Icon(Icons.sync_rounded),
                        label: const Text('Reconcile receipts now'),
                      ),
                    ),
                    const SizedBox(height: 7),
                    const Text(
                      'These records are informational. They cannot be sent to the payment engine.',
                      style: TextStyle(color: VaTheme.textMuted, fontSize: 12),
                    ),
                  ],
                ),
              ),
            );
          }
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _ReceiptCard(row: rows[index - 1]),
          );
        },
      ),
    );
  }

  Future<void> _reconcile(BuildContext context) async {
    try {
      final result = await context.read<AppState>().reconcileFinancialRecords();
      if (!context.mounted) return;
      final reclassified = result['reclassified_bills'] as Map?;
      final bankMatches = result['bank_matches'] as Map?;
      final fixed = (reclassified?['reclassified'] as num?)?.toInt() ?? 0;
      final matched = (bankMatches?['matched'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Financial reconciliation: $fixed false bill${fixed == 1 ? '' : 's'} corrected, $matched receipt${matched == 1 ? '' : 's'} matched.')),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}

class _ReceiptCard extends StatelessWidget {
  const _ReceiptCard({required this.row});

  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final type = '${row['record_type'] ?? ''}';
    final status = '${row['status'] ?? ''}';
    final occurredAt = DateTime.tryParse('${row['occurred_at'] ?? ''}');
    final paid = type == 'paid_receipt';
    final reconciled = status == 'reconciled';
    final color = reconciled ? VaTheme.success : (paid ? VaTheme.secondary : VaTheme.textMuted);

    return VaSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: color.withValues(alpha: .14),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Icon(paid ? Icons.receipt_rounded : Icons.description_outlined, color: color),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${row['provider_name'] ?? 'Financial record'}', style: const TextStyle(fontWeight: FontWeight.w800)),
                    Text(
                      reconciled ? 'paid · reconciled' : type.replaceAll('_', ' '),
                      style: TextStyle(color: color, fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
              ),
              if (row['amount'] != null)
                Text(money(row['amount'], '${row['currency'] ?? 'EUR'}'), style: const TextStyle(fontWeight: FontWeight.w900)),
            ],
          ),
          if ('${row['description'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('${row['description']}'),
          ],
          if ('${row['order_number'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text('Order: ${row['order_number']}', style: const TextStyle(color: VaTheme.textMuted)),
          ],
          if (occurredAt != null) ...[
            const SizedBox(height: 5),
            Text('Recorded: ${DateFormat('dd MMM yyyy').format(occurredAt)}', style: const TextStyle(color: VaTheme.textMuted)),
          ],
          const SizedBox(height: 9),
          Row(
            children: [
              Icon(reconciled ? Icons.verified_rounded : Icons.check_circle_outline_rounded, size: 18, color: color),
              const SizedBox(width: 7),
              Expanded(
                child: Text(
                  reconciled ? 'Matched to a real bank transaction. No payment action required.' : 'No payment action required.',
                  style: TextStyle(color: color, fontWeight: FontWeight.w700, fontSize: 12),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
