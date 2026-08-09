import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class PaymentsPage extends StatelessWidget {
  const PaymentsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    if (state.payments.isEmpty) {
      return const EmptyState(
        icon: Icons.payments_outlined,
        title: 'No payment attempts',
        message: 'Verified payment attempts appear here after the VA or you initiates a real bank payment.',
      );
    }
    final payments = [...state.payments]
      ..sort((a, b) {
        final aAction = a['requires_user_action'] == true ? 0 : 1;
        final bAction = b['requires_user_action'] == true ? 0 : 1;
        if (aAction != bAction) return aAction.compareTo(bAction);
        return ((b['id'] as num?)?.toInt() ?? 0).compareTo((a['id'] as num?)?.toInt() ?? 0);
      });
    final approvals = payments.where((payment) => payment['requires_user_action'] == true).length;
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        itemCount: payments.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: _PaymentSummary(approvals: approvals),
            );
          }
          final payment = payments[index - 1];
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _PaymentCard(payment: payment),
          );
        },
      ),
    );
  }
}

class _PaymentSummary extends StatelessWidget {
  const _PaymentSummary({required this.approvals});

  final int approvals;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(22),
          gradient: const LinearGradient(colors: [Color(0xFF392310), Color(0xFF17233A)]),
          border: Border.all(color: VaTheme.warning.withValues(alpha: .5)),
        ),
        child: Row(
          children: [
            Container(
              width: 54,
              height: 54,
              decoration: BoxDecoration(color: VaTheme.warning.withValues(alpha: .16), borderRadius: BorderRadius.circular(16)),
              child: const Icon(Icons.verified_user_rounded, color: VaTheme.warning, size: 30),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('$approvals payment approval${approvals == 1 ? '' : 's'}', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
                  const SizedBox(height: 3),
                  Text(
                    approvals == 0 ? 'No bank authorization is waiting.' : 'Open the highlighted payment and authorize it with your bank.',
                    style: const TextStyle(color: VaTheme.textMuted),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}

class _PaymentCard extends StatelessWidget {
  const _PaymentCard({required this.payment});

  final Map<String, dynamic> payment;

  @override
  Widget build(BuildContext context) {
    final created = DateTime.tryParse('${payment['created_at'] ?? ''}');
    final authorizationUrl = '${payment['authorization_url'] ?? ''}';
    final requiresAction = payment['requires_user_action'] == true && authorizationUrl.isNotEmpty;
    final status = '${payment['status']}';
    final statusColor = requiresAction
        ? VaTheme.warning
        : switch (status.toLowerCase()) {
            'completed' => VaTheme.success,
            'failed' || 'cancelled' || 'rejected' => VaTheme.danger,
            _ => VaTheme.secondary,
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
                child: Icon(requiresAction ? Icons.touch_app_rounded : _statusIcon(status), color: statusColor),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Payment #${payment['id']}', style: const TextStyle(fontWeight: FontWeight.w800)),
                    Text(status.replaceAll('_', ' '), style: TextStyle(color: statusColor, fontWeight: FontWeight.w700)),
                  ],
                ),
              ),
              Text(money(payment['amount'], '${payment['currency'] ?? 'EUR'}'), style: const TextStyle(fontWeight: FontWeight.w900)),
            ],
          ),
          const SizedBox(height: 8),
          Text('Bill #${payment['bill_id']}'),
          if (created != null) Text('Created: ${DateFormat('dd/MM/yyyy HH:mm').format(created)}'),
          if ('${payment['failure_reason'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('${payment['failure_reason']}', style: const TextStyle(color: VaTheme.danger, fontWeight: FontWeight.w600)),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (requiresAction)
                FilledButton.icon(
                  onPressed: () => launchUrl(Uri.parse(authorizationUrl), mode: LaunchMode.externalApplication),
                  icon: const Icon(Icons.verified_user_outlined),
                  label: const Text('Authorize with bank'),
                ),
              OutlinedButton.icon(
                onPressed: () => context.read<AppState>().refreshPayment(payment['id'] as int),
                icon: const Icon(Icons.refresh_rounded),
                label: const Text('Check bank status'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  IconData _statusIcon(String status) {
    switch (status.toLowerCase()) {
      case 'completed':
        return Icons.check_circle_outline;
      case 'failed':
      case 'cancelled':
      case 'rejected':
        return Icons.error_outline;
      default:
        return Icons.schedule_outlined;
    }
  }
}
