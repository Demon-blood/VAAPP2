import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class TasksPage extends StatelessWidget {
  const TasksPage({this.onOpenBills, this.onOpenPayments, super.key});

  final VoidCallback? onOpenBills;
  final VoidCallback? onOpenPayments;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final tasks = [...state.tasks]
      ..sort((a, b) {
        final aOpen = ['open', 'waiting'].contains('${a['status']}') ? 0 : 1;
        final bOpen = ['open', 'waiting'].contains('${b['status']}') ? 0 : 1;
        if (aOpen != bOpen) return aOpen.compareTo(bOpen);
        return ('${a['due_at'] ?? ''}').compareTo('${b['due_at'] ?? ''}');
      });
    if (tasks.isEmpty) {
      return const EmptyState(
        icon: Icons.task_alt,
        title: 'Nothing waiting',
        message: 'Tasks appear only when a live email, deadline, payment, or workflow creates one.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
      itemCount: tasks.length,
      itemBuilder: (context, index) {
        final task = tasks[index];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _TaskCard(task: task, onOpenBills: onOpenBills, onOpenPayments: onOpenPayments),
        );
      },
    );
  }
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.task, this.onOpenBills, this.onOpenPayments});

  final Map<String, dynamic> task;
  final VoidCallback? onOpenBills;
  final VoidCallback? onOpenPayments;

  @override
  Widget build(BuildContext context) {
    final due = DateTime.tryParse('${task['due_at'] ?? ''}');
    final status = '${task['status'] ?? 'open'}';
    final completed = status == 'completed';
    final sourceType = '${task['source_type'] ?? ''}';
    final canExecute = ['email_reply', 'calendar_review'].contains(sourceType) && !completed;
    final manualCompletionAllowed = sourceType == 'manual' || sourceType == 'physical' || sourceType.startsWith('physical_');
    final accent = task['requires_approval'] == true ? VaTheme.warning : VaTheme.secondary;
    return VaSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              InkWell(
                onTap: manualCompletionAllowed
                    ? () => context.read<AppState>().setTaskStatus(task['id'] as int, completed ? 'open' : 'completed')
                    : null,
                borderRadius: BorderRadius.circular(14),
                child: Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(color: accent.withValues(alpha: .16), borderRadius: BorderRadius.circular(14)),
                  child: Icon(completed ? Icons.check_circle_rounded : Icons.task_alt_rounded, color: completed ? VaTheme.success : accent),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${task['title']}',
                      style: TextStyle(fontWeight: FontWeight.w800, decoration: completed ? TextDecoration.lineThrough : null),
                    ),
                    if ('${task['description'] ?? ''}'.isNotEmpty) ...[
                      const SizedBox(height: 5),
                      Text('${task['description']}', style: const TextStyle(color: VaTheme.textMuted, height: 1.35)),
                    ],
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              Chip(label: Text(status.toUpperCase())),
              if (sourceType.isNotEmpty) Chip(label: Text(_sourceLabel(sourceType))),
              if (task['requires_approval'] == true) const Chip(label: Text('APPROVAL')),
              if (due != null) Chip(label: Text('Due ${DateFormat('dd MMM HH:mm').format(due)}')),
            ],
          ),
          if (!completed) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                if (canExecute)
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: () => _execute(context),
                      icon: Icon(sourceType == 'email_reply' ? Icons.send_rounded : Icons.calendar_month_rounded),
                      label: Text(sourceType == 'email_reply' ? 'Send approved reply' : 'Create calendar event'),
                    ),
                  ),
                if (canExecute) const SizedBox(width: 8),
                if (sourceType == 'bill_review' && onOpenBills != null)
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: onOpenBills,
                      icon: const Icon(Icons.receipt_long_rounded),
                      label: const Text('Open Bills'),
                    ),
                  ),
                if (sourceType == 'bill_payment' && onOpenPayments != null)
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: onOpenPayments,
                      icon: const Icon(Icons.payments_rounded),
                      label: const Text('Open Payments'),
                    ),
                  ),
                if ((sourceType == 'bill_review' && onOpenBills != null) ||
                    (sourceType == 'bill_payment' && onOpenPayments != null))
                  const SizedBox(width: 8),
                if (manualCompletionAllowed)
                  OutlinedButton.icon(
                    onPressed: () => context.read<AppState>().setTaskStatus(task['id'] as int, 'completed'),
                    icon: const Icon(Icons.check_rounded),
                    label: const Text('I did this'),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  String _sourceLabel(String value) => switch (value) {
        'email_reply' => 'Reply',
        'calendar_review' => 'Calendar',
        'bill_review' => 'Bill review',
        'email_action' => 'Email action',
        'support_followup' => 'Support',
        'bill_payment' => 'Payment',
        _ => value.replaceAll('_', ' '),
      };

  Future<void> _execute(BuildContext context) async {
    try {
      final result = await context.read<AppState>().executeTaskAction(task['id'] as int);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${result['message'] ?? 'Action executed'}')));
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}
