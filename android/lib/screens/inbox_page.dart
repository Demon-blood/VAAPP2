import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class InboxPage extends StatelessWidget {
  const InboxPage({
    this.actionOnly = false,
    this.onShowAll,
    this.onOpenTasks,
    this.onOpenBills,
    super.key,
  });

  final bool actionOnly;
  final VoidCallback? onShowAll;
  final VoidCallback? onOpenTasks;
  final VoidCallback? onOpenBills;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final emails = actionOnly
        ? state.emails.where((email) => email['action_required'] == true).toList()
        : state.emails;
    if (emails.isEmpty) {
      return EmptyState(
        icon: Icons.inbox_outlined,
        title: actionOnly ? 'No unresolved email actions' : 'No processed messages',
        message: actionOnly
            ? 'The VA has executed or resolved every current email action.'
            : 'Connect Google and run Gmail sync. This screen never displays fabricated mail.',
      );
    }
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().syncGmail(),
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
        itemCount: emails.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) {
            return _InboxHeader(
              actionOnly: actionOnly,
              count: emails.length,
              onShowAll: onShowAll,
            );
          }
          final email = emails[index - 1];
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _EmailActionCard(
              email: email,
              onOpenTasks: onOpenTasks,
              onOpenBills: onOpenBills,
            ),
          );
        },
      ),
    );
  }
}

class _InboxHeader extends StatelessWidget {
  const _InboxHeader({required this.actionOnly, required this.count, this.onShowAll});

  final bool actionOnly;
  final int count;
  final VoidCallback? onShowAll;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    actionOnly ? 'Action queue' : 'Processed inbox',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
                  ),
                  Text(
                    actionOnly ? '$count unresolved message${count == 1 ? '' : 's'}' : '$count recent processed messages',
                    style: const TextStyle(color: VaTheme.textMuted),
                  ),
                ],
              ),
            ),
            if (actionOnly && onShowAll != null)
              TextButton.icon(
                onPressed: onShowAll,
                icon: const Icon(Icons.all_inbox_rounded),
                label: const Text('Show all'),
              ),
          ],
        ),
      );
}

class _EmailActionCard extends StatelessWidget {
  const _EmailActionCard({required this.email, this.onOpenTasks, this.onOpenBills});

  final Map<String, dynamic> email;
  final VoidCallback? onOpenTasks;
  final VoidCallback? onOpenBills;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final date = DateTime.tryParse('${email['received_at'] ?? ''}');
    final actionRequired = email['action_required'] == true;
    final providerId = '${email['provider_message_id'] ?? ''}';
    final relatedTasks = state.tasks.where((task) => '${task['source_id'] ?? ''}' == providerId && ['open', 'waiting'].contains('${task['status']}')).toList();
    final executableTasks = relatedTasks
        .where((task) => ['email_reply', 'calendar_review'].contains('${task['source_type']}'))
        .toList();
    final executableTask = executableTasks.isEmpty ? null : executableTasks.first;
    final hasBillReview = relatedTasks.any((task) => '${task['source_type']}' == 'bill_review');
    final analysis = _analysis(email['analysis_json']);
    final summary = '${analysis['reasoning_summary'] ?? email['snippet'] ?? ''}'.trim();
    final priority = '${email['priority'] ?? 'normal'}';
    final accent = switch (priority.toLowerCase()) {
      'urgent' || 'high' => VaTheme.danger,
      'low' => VaTheme.secondary,
      _ => VaTheme.primary,
    };

    return VaSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(color: accent.withValues(alpha: .16), borderRadius: BorderRadius.circular(14)),
                child: Icon(actionRequired ? Icons.priority_high_rounded : Icons.mail_outline_rounded, color: accent),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${email['subject'] ?? '(No subject)'}', style: const TextStyle(fontWeight: FontWeight.w800)),
                    const SizedBox(height: 2),
                    Text('${email['sender'] ?? ''}', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: VaTheme.textMuted)),
                  ],
                ),
              ),
              if (date != null) Text(DateFormat('dd/MM HH:mm').format(date), style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              Chip(label: Text('${email['category'] ?? 'unclassified'}')),
              Chip(label: Text(priority.toUpperCase())),
              if (relatedTasks.isNotEmpty) Chip(label: Text('${relatedTasks.length} task${relatedTasks.length == 1 ? '' : 's'}')),
              if ('${email['status']}' == 'deferred_ai') const Chip(label: Text('AI deferred')),
            ],
          ),
          if (summary.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(summary, maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Color(0xFFD6DDF0), height: 1.35)),
          ],
          if (actionRequired) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: state.busy
                        ? null
                        : executableTask != null
                            ? () => _executeTask(context, executableTask)
                            : hasBillReview && onOpenBills != null
                                ? onOpenBills
                                : () => _runSafeAction(context),
                    icon: Icon(
                      executableTask == null
                          ? hasBillReview
                              ? Icons.receipt_long_rounded
                              : Icons.bolt_rounded
                          : '${executableTask['source_type']}' == 'email_reply'
                              ? Icons.send_rounded
                              : Icons.calendar_month_rounded,
                    ),
                    label: Text(
                      executableTask == null
                          ? hasBillReview
                              ? 'Open Bills'
                              : 'Run VA now'
                          : '${executableTask['source_type']}' == 'email_reply'
                              ? 'Send approved reply'
                              : 'Create calendar event',
                    ),
                  ),
                ),
                if (relatedTasks.isNotEmpty && onOpenTasks != null) ...[
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    onPressed: onOpenTasks,
                    icon: const Icon(Icons.task_alt_rounded),
                    label: const Text('Tasks'),
                  ),
                ],
              ],
            ),
          ],
        ],
      ),
    );
  }

  Map<String, dynamic> _analysis(dynamic raw) {
    if (raw is Map) return Map<String, dynamic>.from(raw);
    if (raw is! String || raw.isEmpty) return {};
    try {
      final parsed = jsonDecode(raw);
      return parsed is Map ? Map<String, dynamic>.from(parsed) : {};
    } catch (_) {
      return {};
    }
  }

  Future<void> _executeTask(BuildContext context, Map<String, dynamic> task) async {
    try {
      final result = await context.read<AppState>().executeTaskAction(task['id'] as int);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${result['message'] ?? 'Action executed'}')),
        );
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _runSafeAction(BuildContext context) async {
    try {
      await context.read<AppState>().runAutomationNow();
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('The VA ran all safe actions and refreshed the remaining queue.')),
        );
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}
