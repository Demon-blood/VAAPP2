import 'package:flutter/material.dart';

import '../theme/va_theme.dart';
import 'common_widgets.dart';

class DailyBriefingCard extends StatelessWidget {
  const DailyBriefingCard({required this.briefing, super.key});

  final Map<String, dynamic> briefing;

  int _int(String key, {int fallback = 0}) {
    final stats = briefing['stats'];
    if (stats is Map) {
      final value = stats[key];
      if (value is num) return value.toInt();
      return int.tryParse('$value') ?? fallback;
    }
    return fallback;
  }

  @override
  Widget build(BuildContext context) {
    final mailFallback = briefing['mail'] is List
        ? (briefing['mail'] as List).length
        : (briefing['important_mail'] as List? ?? const []).length;
    final emails = _int('emails_received', fallback: mailFallback);
    final payments = _int(
      'payments_changed',
      fallback: (briefing['payment_activity'] as List? ?? const []).length,
    );
    final actions = _int(
      'va_actions',
      fallback: (briefing['activity'] as List? ?? const []).length,
    );
    final needs = _int(
      'needs_you',
      fallback: (briefing['needs_you'] as List? ?? const []).length,
    );
    final headline = '${briefing['headline'] ?? ''}'.trim();
    final summary = '${briefing['summary_text'] ?? ''}'.trim();

    return VaSectionCard(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: briefing.isEmpty ? null : () => _showBriefing(context),
        child: Padding(
          padding: const EdgeInsets.all(2),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      gradient: const LinearGradient(
                        colors: [Color(0xFF8B3DFF), Color(0xFF2C66FF)],
                      ),
                      borderRadius: BorderRadius.circular(14),
                      boxShadow: [
                        BoxShadow(
                          color: VaTheme.primary.withValues(alpha: .22),
                          blurRadius: 18,
                        ),
                      ],
                    ),
                    child: const Icon(Icons.auto_awesome_rounded, color: Colors.white),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Daily briefing',
                          style: TextStyle(fontWeight: FontWeight.w900, fontSize: 16),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          '${briefing['briefing_date'] ?? 'Last 24 hours'}',
                          style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
                        ),
                      ],
                    ),
                  ),
                  const Icon(Icons.chevron_right_rounded, color: VaTheme.textMuted),
                ],
              ),
              const SizedBox(height: 12),
              Text(
                headline.isNotEmpty
                    ? headline
                    : summary.isNotEmpty
                        ? summary
                        : 'Your mail, money, calendar and VA activity will be summarized here.',
                style: const TextStyle(fontWeight: FontWeight.w700, height: 1.35),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 7,
                runSpacing: 7,
                children: [
                  _MetricChip(icon: Icons.mail_outline_rounded, label: '$emails mail'),
                  _MetricChip(icon: Icons.payments_outlined, label: '$payments money'),
                  _MetricChip(icon: Icons.bolt_rounded, label: '$actions VA actions'),
                  _MetricChip(
                    icon: needs == 0 ? Icons.done_all_rounded : Icons.priority_high_rounded,
                    label: needs == 0 ? 'Nothing needs you' : '$needs need you',
                    accent: needs == 0 ? VaTheme.success : VaTheme.warning,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _showBriefing(BuildContext context) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: const Color(0xFF080D1B),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(26)),
      ),
      builder: (sheetContext) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .9,
        minChildSize: .55,
        maxChildSize: .96,
        builder: (context, controller) => _BriefingSheet(
          briefing: briefing,
          controller: controller,
        ),
      ),
    );
  }
}

class _MetricChip extends StatelessWidget {
  const _MetricChip({required this.icon, required this.label, this.accent = VaTheme.secondary});

  final IconData icon;
  final String label;
  final Color accent;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
        decoration: BoxDecoration(
          color: accent.withValues(alpha: .12),
          border: Border.all(color: accent.withValues(alpha: .32)),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14, color: accent),
            const SizedBox(width: 5),
            Text(label, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 11)),
          ],
        ),
      );
}

class _BriefingSheet extends StatelessWidget {
  const _BriefingSheet({required this.briefing, required this.controller});

  final Map<String, dynamic> briefing;
  final ScrollController controller;

  List<Map<String, dynamic>> _maps(String key) => (briefing[key] as List? ?? const [])
      .whereType<Map>()
      .map((value) => Map<String, dynamic>.from(value))
      .toList();

  List<Map<String, dynamic>> _nestedMaps(String parent, String key) {
    final value = briefing[parent];
    if (value is! Map) return const [];
    return (value[key] as List? ?? const [])
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final mail = _maps('mail').isNotEmpty ? _maps('mail') : _maps('important_mail');
    final replies = _maps('reply_activity');
    final completedTasks = _nestedMaps('task_activity', 'completed');
    final upcomingTasks = _nestedMaps('task_activity', 'upcoming');
    final payments = _maps('payment_activity');
    final bills = _maps('bill_activity').isNotEmpty ? _maps('bill_activity') : _maps('upcoming_bills');
    final financial = _maps('financial_records');
    final calendarChanges = _maps('calendar_changes');
    final appointments = _maps('appointments');
    final orders = _maps('orders');
    final subscriptions = _maps('subscriptions');
    final documents = _maps('important_documents');
    final unusualItems = _maps('unusual_items');
    final providerProblems = _maps('provider_problems');
    final activity = _maps('activity_summary');
    final activityTimeline = _maps('activity');
    final communications = _maps('communications');
    final internalTransfers = _maps('internal_transfers');
    final needsYou = _maps('needs_you');

    return ListView(
      controller: controller,
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 34),
      children: [
        Center(
          child: Container(
            width: 44,
            height: 4,
            decoration: BoxDecoration(
              color: Colors.white24,
              borderRadius: BorderRadius.circular(999),
            ),
          ),
        ),
        const SizedBox(height: 18),
        Row(
          children: [
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                gradient: const LinearGradient(colors: [Color(0xFF8B3DFF), Color(0xFF2C66FF)]),
                borderRadius: BorderRadius.circular(15),
              ),
              child: const Icon(Icons.auto_awesome_rounded, color: Colors.white),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Daily VA briefing',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
                  ),
                  Text(
                    '${briefing['briefing_date'] ?? ''} · ${briefing['timezone'] ?? ''}',
                    style: const TextStyle(color: VaTheme.textMuted),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        Text(
          '${briefing['summary_text'] ?? briefing['headline'] ?? ''}',
          style: const TextStyle(fontSize: 15, height: 1.45, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 22),
        _BriefingSection(
          icon: Icons.mark_email_read_outlined,
          title: 'What your mail was about',
          empty: 'No mail was recorded in this briefing window.',
          children: [
            for (final item in mail)
              _MailRow(item: item),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.reply_all_outlined,
          title: 'Replies',
          empty: replies.isEmpty ? 'No replies were sent or left waiting for a decision.' : '',
          children: [
            for (final item in replies)
              _BriefingRow(
                icon: item['status'] == 'sent' ? Icons.send_outlined : Icons.pending_actions_outlined,
                title: '${item['subject'] ?? 'Email reply'}',
                detail: [
                  item['status'] == 'sent' ? 'Sent automatically' : 'Awaiting an unavoidable decision',
                  if ('${item['recipient'] ?? ''}'.isNotEmpty) '${item['recipient']}',
                  if ('${item['detail'] ?? ''}'.isNotEmpty) '${item['detail']}',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: item['status'] == 'sent' ? VaTheme.success : VaTheme.warning,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.task_alt_outlined,
          title: 'Tasks & deadlines',
          empty: completedTasks.isEmpty && upcomingTasks.isEmpty
              ? 'No completed or upcoming task deadlines in this briefing window.'
              : '',
          children: [
            for (final item in completedTasks)
              _BriefingRow(
                icon: Icons.check_circle_outline_rounded,
                title: '${item['title'] ?? 'Completed task'}',
                detail: _completedTaskDetail(item),
                accent: VaTheme.success,
              ),
            for (final item in upcomingTasks)
              _BriefingRow(
                icon: Icons.schedule_outlined,
                title: '${item['title'] ?? 'Upcoming task'}',
                detail: [
                  if ('${item['due_at'] ?? ''}'.isNotEmpty) 'Due ${_shortDate(item['due_at'])}',
                  if ('${item['priority'] ?? ''}'.isNotEmpty) '${item['priority']} priority',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: item['requires_approval'] == true ? VaTheme.warning : VaTheme.secondary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.account_balance_wallet_outlined,
          title: 'Payments & bills',
          empty: payments.isEmpty && bills.isEmpty && financial.isEmpty
              ? 'No bill, payment or receipt activity in this briefing window.'
              : '',
          children: [
            for (final item in payments)
              _BriefingRow(
                icon: item['requires_user_action'] == true
                    ? Icons.verified_user_outlined
                    : Icons.payments_outlined,
                title: '${item['purpose'] ?? 'Payment'}',
                detail: [
                  '${item['amount_text'] ?? ''}',
                  if ('${item['status'] ?? ''}'.isNotEmpty) '${item['status']}',
                  if ('${item['account'] ?? ''}'.isNotEmpty) 'via ${item['account']}',
                  if ('${item['failure_reason'] ?? ''}'.isNotEmpty) '${item['failure_reason']}',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: item['requires_user_action'] == true
                    ? VaTheme.warning
                    : item['status'] == 'completed'
                        ? VaTheme.success
                        : VaTheme.secondary,
              ),
            for (final item in bills.take(10))
              _BriefingRow(
                icon: Icons.receipt_long_outlined,
                title: '${item['creditor'] ?? 'Bill'}',
                detail: [
                  '${item['amount_text'] ?? item['amount'] ?? ''}',
                  if ('${item['due_at'] ?? ''}'.isNotEmpty) 'due ${_shortDate(item['due_at'])}',
                  if ('${item['status'] ?? ''}'.isNotEmpty) '${item['status']}',
                ].where((value) => value.isNotEmpty).join(' · '),
              ),
            for (final item in financial.take(10))
              _BriefingRow(
                icon: Icons.receipt_outlined,
                title: '${item['provider'] ?? item['description'] ?? 'Receipt / notice'}',
                detail: [
                  '${item['description'] ?? ''}',
                  if (item['amount'] != null) '${item['amount']} ${item['currency'] ?? 'EUR'}',
                  'No payment action required',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: VaTheme.secondary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.calendar_month_outlined,
          title: 'Calendar',
          empty: calendarChanges.isEmpty && appointments.isEmpty
              ? 'No calendar changes or upcoming appointments were returned.'
              : '',
          children: [
            for (final item in calendarChanges)
              _BriefingRow(
                icon: Icons.event_available_outlined,
                title: '${item['subject'] ?? 'Calendar item'}',
                detail: '${item['detail'] ?? 'Added to Calendar'}',
                accent: VaTheme.success,
              ),
            for (final item in appointments.take(12))
              _BriefingRow(
                icon: Icons.event_outlined,
                title: '${item['summary'] ?? item['title'] ?? 'Appointment'}',
                detail: _appointmentDetail(item),
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.local_shipping_outlined,
          title: 'Deliveries, orders & subscriptions',
          empty: orders.isEmpty && subscriptions.isEmpty
              ? 'No order, delivery or subscription items are currently tracked.'
              : '',
          children: [
            for (final item in orders.take(12))
              _BriefingRow(
                icon: Icons.inventory_2_outlined,
                title: _orderTitle(item),
                detail: [
                  '${item['status'] ?? ''}',
                  if ('${item['expected_delivery_at'] ?? ''}'.isNotEmpty)
                    'Expected ${_shortDate(item['expected_delivery_at'])}',
                ].where((value) => value.isNotEmpty).join(' · '),
              ),
            for (final item in subscriptions.take(12))
              _BriefingRow(
                icon: Icons.autorenew_outlined,
                title: '${item['provider'] ?? 'Subscription'}',
                detail: [
                  '${item['description'] ?? ''}',
                  if (item['amount'] != null) '${item['amount']} ${item['currency'] ?? 'EUR'}',
                  if ('${item['next_charge_at'] ?? ''}'.isNotEmpty)
                    'Next ${_shortDate(item['next_charge_at'])}',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: VaTheme.secondary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.folder_special_outlined,
          title: 'Important documents',
          empty: documents.isEmpty ? 'No new retained documents in this briefing window.' : '',
          children: [
            for (final item in documents.take(20))
              _BriefingRow(
                icon: Icons.description_outlined,
                title: '${item['name'] ?? 'Document'}',
                detail: [
                  '${item['category'] ?? ''}',
                  if ('${item['account_scope'] ?? ''}'.isNotEmpty) '${item['account_scope']}',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: VaTheme.secondary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.shield_outlined,
          title: 'Unusual, security, legal & financial',
          empty: unusualItems.isEmpty ? 'No unusual or sensitive items were identified.' : '',
          children: [
            for (final item in unusualItems.take(20))
              _BriefingRow(
                icon: Icons.report_outlined,
                title: '${item['subject'] ?? 'Important item'}',
                detail: [
                  '${item['category'] ?? ''}',
                  '${item['summary'] ?? ''}',
                  if ('${item['outcome'] ?? ''}'.isNotEmpty) 'VA: ${item['outcome']}',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: item['action_required'] == true ? VaTheme.warning : VaTheme.primary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: providerProblems.isEmpty ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
          title: 'Automation & provider problems',
          empty: providerProblems.isEmpty ? 'No provider or automation problems are active.' : '',
          children: [
            for (final item in providerProblems)
              _BriefingRow(
                icon: Icons.warning_amber_outlined,
                title: '${item['provider'] ?? 'Autopilot'}',
                detail: '${item['detail'] ?? item['status'] ?? ''}',
                accent: VaTheme.warning,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.bolt_outlined,
          title: 'What the VA did',
          empty: activity.isEmpty ? 'No summarized VA activity was returned.' : '',
          children: [
            for (final item in activity)
              _BriefingRow(
                icon: Icons.check_circle_outline_rounded,
                title: '${item['label'] ?? 'VA action'}',
                detail: '${item['count'] ?? 1} time${('${item['count'] ?? 1}' == '1') ? '' : 's'}',
                accent: VaTheme.success,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.forum_outlined,
          title: 'Calls & messages',
          empty: communications.isEmpty ? 'No phone or messaging activity was synced in this briefing window.' : '',
          children: [
            for (final item in communications.take(20))
              _BriefingRow(
                icon: item['channel'] == 'call' ? Icons.phone_outlined : Icons.chat_bubble_outline_rounded,
                title: '${item['sender'] ?? item['channel'] ?? 'Communication'}',
                detail: [
                  '${item['body'] ?? ''}',
                  '${item['channel'] ?? ''}',
                  if (item['action_required'] == true) 'Needs attention',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: item['action_required'] == true ? VaTheme.warning : VaTheme.secondary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.swap_horiz_rounded,
          title: 'Own-account transfers',
          empty: internalTransfers.isEmpty ? 'No budget rebalancing transfers changed in this briefing window.' : '',
          children: [
            for (final item in internalTransfers.take(12))
              _BriefingRow(
                icon: Icons.account_balance_wallet_outlined,
                title: '${item['amount_text'] ?? 'Transfer'}',
                detail: [
                  '${item['status'] ?? ''}',
                  '${item['reason'] ?? ''}',
                  if (item['requires_user_action'] == true) 'Bank authorization required',
                ].where((value) => value.isNotEmpty).join(' · '),
                accent: item['requires_user_action'] == true ? VaTheme.warning : VaTheme.secondary,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: Icons.history_rounded,
          title: 'VA activity timeline',
          empty: activityTimeline.isEmpty ? 'No detailed VA activity was returned.' : '',
          children: [
            for (final item in activityTimeline.take(40))
              _BriefingRow(
                icon: item['result'] == 'failed'
                    ? Icons.error_outline_rounded
                    : Icons.check_circle_outline_rounded,
                title: '${item['label'] ?? item['event_type'] ?? 'VA action'}',
                detail: [
                  if ('${item['created_at'] ?? ''}'.isNotEmpty) _shortTime(item['created_at']),
                  if ('${item['entity_type'] ?? ''}'.isNotEmpty) '${item['entity_type']}',
                  if (item['result'] == 'failed') 'Failed',
                ].join(' · '),
                accent: item['result'] == 'failed' ? VaTheme.warning : VaTheme.success,
              ),
          ],
        ),
        const SizedBox(height: 18),
        _BriefingSection(
          icon: needsYou.isEmpty ? Icons.done_all_rounded : Icons.priority_high_rounded,
          title: 'Needs you',
          empty: needsYou.isEmpty ? 'Nothing needs you. Autopilot can continue on its own.' : '',
          children: [
            for (final item in needsYou)
              _BriefingRow(
                icon: Icons.priority_high_rounded,
                title: '${item['title'] ?? item['type'] ?? 'Action required'}',
                detail: '${item['detail'] ?? ''}',
                accent: VaTheme.warning,
              ),
          ],
        ),
      ],
    );
  }

  String _shortTime(dynamic value) {
    final parsed = DateTime.tryParse('$value');
    if (parsed == null) return '$value';
    final local = parsed.toLocal();
    return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }

  String _shortDate(dynamic value) {
    final parsed = DateTime.tryParse('$value');
    if (parsed == null) return '$value';
    return '${parsed.day.toString().padLeft(2, '0')}/${parsed.month.toString().padLeft(2, '0')}';
  }

  String _completedTaskDetail(Map<String, dynamic> item) {
    final updatedAt = '${item['updated_at'] ?? ''}';
    return updatedAt.isEmpty ? 'Completed' : 'Completed · ${_shortDate(updatedAt)}';
  }

  String _orderTitle(Map<String, dynamic> item) {
    final merchant = '${item['merchant'] ?? 'Order'}';
    final orderNumber = '${item['order_number'] ?? ''}';
    return orderNumber.isEmpty ? merchant : '$merchant · $orderNumber';
  }

  String _appointmentDetail(Map<String, dynamic> item) {
    final start = item['start'];
    if (start is Map) {
      final value = start['dateTime'] ?? start['date'];
      if (value != null) return '$value';
    }
    return '${item['start_at'] ?? item['start'] ?? ''}';
  }
}

class _BriefingSection extends StatelessWidget {
  const _BriefingSection({
    required this.icon,
    required this.title,
    required this.empty,
    required this.children,
  });

  final IconData icon;
  final String title;
  final String empty;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => VaSectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: VaTheme.secondary),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900),
                  ),
                ),
              ],
            ),
            if (children.isNotEmpty) ...[
              const SizedBox(height: 8),
              ...children,
            ] else if (empty.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(empty, style: const TextStyle(color: VaTheme.textMuted, height: 1.35)),
            ],
          ],
        ),
      );
}

class _MailRow extends StatelessWidget {
  const _MailRow({required this.item});

  final Map<String, dynamic> item;

  @override
  Widget build(BuildContext context) {
    final actionRequired = item['action_required'] == true;
    return _BriefingRow(
      icon: actionRequired ? Icons.mark_email_unread_outlined : Icons.mark_email_read_outlined,
      title: '${item['subject'] ?? '(No subject)'}',
      detail: [
        '${item['sender'] ?? ''}',
        if ('${item['summary'] ?? ''}'.isNotEmpty) '${item['summary']}',
        if ('${item['outcome'] ?? ''}'.isNotEmpty) 'VA: ${item['outcome']}',
      ].where((value) => value.isNotEmpty).join('\n'),
      accent: actionRequired ? VaTheme.warning : VaTheme.primary,
    );
  }
}

class _BriefingRow extends StatelessWidget {
  const _BriefingRow({
    required this.icon,
    required this.title,
    required this.detail,
    this.accent = VaTheme.primary,
  });

  final IconData icon;
  final String title;
  final String detail;
  final Color accent;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: accent.withValues(alpha: .12),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(icon, size: 18, color: accent),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
                  if (detail.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(detail, style: const TextStyle(color: VaTheme.textMuted, height: 1.35)),
                  ],
                ],
              ),
            ),
          ],
        ),
      );
}
