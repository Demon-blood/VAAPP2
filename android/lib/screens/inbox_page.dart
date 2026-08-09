import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class InboxPage extends StatefulWidget {
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
  State<InboxPage> createState() => _InboxPageState();
}

class _InboxPageState extends State<InboxPage> {
  final searchController = TextEditingController();
  String priorityFilter = 'All';

  @override
  void dispose() {
    searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final query = searchController.text.trim().toLowerCase();
    final base = widget.actionOnly
        ? state.emails.where((email) => email['action_required'] == true)
        : state.emails;
    final emails = base.where((email) {
      final priority = '${email['priority'] ?? 'normal'}'.toLowerCase();
      final priorityMatch = switch (priorityFilter) {
        'High' => priority == 'high' || priority == 'urgent',
        'Medium' => priority == 'medium' || priority == 'normal',
        'Low' => priority == 'low',
        _ => true,
      };
      final haystack = '${email['subject'] ?? ''} ${email['sender'] ?? ''} ${email['category'] ?? ''} ${email['snippet'] ?? ''}'.toLowerCase();
      return priorityMatch && (query.isEmpty || haystack.contains(query));
    }).toList();

    final sourceCount = widget.actionOnly
        ? state.emails.where((email) => email['action_required'] == true).length
        : state.emails.length;
    final highCount = base.where((email) {
      final priority = '${email['priority'] ?? ''}'.toLowerCase();
      return priority == 'high' || priority == 'urgent';
    }).length;
    final lowCount = base.where((email) => '${email['priority'] ?? ''}'.toLowerCase() == 'low').length;
    final mediumCount = sourceCount - highCount - lowCount;

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().syncGmail(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
        children: [
          if (widget.actionOnly)
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Emails needing action',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
                  ),
                ),
                if (widget.onShowAll != null)
                  TextButton.icon(
                    onPressed: widget.onShowAll,
                    icon: const Icon(Icons.all_inbox_rounded),
                    label: const Text('Show all'),
                  ),
              ],
            ),
          SizedBox(
            height: 40,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                _priorityChip('All', sourceCount),
                _priorityChip('High', highCount),
                _priorityChip('Medium', mediumCount < 0 ? 0 : mediumCount),
                _priorityChip('Low', lowCount),
              ],
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: searchController,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              hintText: 'Search emails…',
              prefixIcon: Icon(Icons.search_rounded),
              suffixIcon: Icon(Icons.tune_rounded),
              isDense: true,
            ),
          ),
          const SizedBox(height: 12),
          if (emails.isEmpty)
            SizedBox(
              height: MediaQuery.sizeOf(context).height * .50,
              child: EmptyState(
                icon: Icons.inbox_outlined,
                title: widget.actionOnly && sourceCount == 0
                    ? 'No unresolved email actions'
                    : 'No matching messages',
                message: widget.actionOnly && sourceCount == 0
                    ? 'The VA has executed or resolved every current email action.'
                    : query.isNotEmpty || priorityFilter != 'All'
                        ? 'Try another search or priority filter.'
                        : 'Connect Google and run Gmail sync. This screen never displays fabricated mail.',
              ),
            )
          else
            for (final email in emails) ...[
              _EmailActionCard(
                email: email,
                onOpenTasks: widget.onOpenTasks,
                onOpenBills: widget.onOpenBills,
              ),
              const SizedBox(height: 9),
            ],
        ],
      ),
    );
  }

  Widget _priorityChip(String label, int count) => Padding(
        padding: const EdgeInsets.only(right: 7),
        child: ChoiceChip(
          selected: priorityFilter == label,
          onSelected: (_) => setState(() => priorityFilter = label),
          label: Text('$label $count'),
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
