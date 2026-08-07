import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models/view_models.dart';
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
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: state.payments.length,
        itemBuilder: (context, index) {
          final payment = state.payments[index];
          final created = DateTime.tryParse('${payment['created_at'] ?? ''}');
          final authorizationUrl = '${payment['authorization_url'] ?? ''}';
          final requiresAction = payment['requires_user_action'] == true && authorizationUrl.isNotEmpty;
          return Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(requiresAction ? Icons.touch_app_outlined : _statusIcon('${payment['status']}')),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'Payment #${payment['id']} · ${payment['status']}',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      Text(money(payment['amount'], '${payment['currency'] ?? 'EUR'}')),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text('Bill #${payment['bill_id']}'),
                  if (created != null) Text('Created: ${DateFormat('dd/MM/yyyy HH:mm').format(created)}'),
                  if ('${payment['failure_reason'] ?? ''}'.isNotEmpty)
                    Text(
                      '${payment['failure_reason']}',
                      style: TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
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
                        icon: const Icon(Icons.refresh),
                        label: const Text('Check bank status'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          );
        },
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
