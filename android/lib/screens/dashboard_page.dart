import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';

class DashboardPage extends StatelessWidget {
  const DashboardPage({
    required this.onOpenTasks,
    required this.onOpenEmails,
    required this.onOpenBills,
    required this.onOpenPayments,
    required this.onOpenServices,
    super.key,
  });

  final VoidCallback onOpenTasks;
  final VoidCallback onOpenEmails;
  final VoidCallback onOpenBills;
  final VoidCallback onOpenPayments;
  final VoidCallback onOpenServices;

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final data = state.dashboard;
    if (data == null) {
      if (!state.refreshComplete || state.busy) {
        return const Center(child: CircularProgressIndicator());
      }
      return RefreshIndicator(
        onRefresh: () => context.read<AppState>().refreshAll(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            SizedBox(
              height: MediaQuery.sizeOf(context).height * .65,
              child: EmptyState(
                icon: Icons.cloud_off_outlined,
                title: 'VA server data unavailable',
                message: state.serverWarning ??
                    'The phone is paired, but the dashboard endpoint did not return live data. Refresh or repair the backend deployment.',
              ),
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
        children: [
          _AutomationHero(state: state),
          const SizedBox(height: 18),
          Text('Action centre', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
          const SizedBox(height: 5),
          const Text(
            'Safe actions run automatically. Tap any card to inspect, execute, approve, or resolve what remains.',
            style: TextStyle(color: VaTheme.textMuted),
          ),
          const SizedBox(height: 14),
          CountCard(
            label: 'Emails needing action',
            value: data.actionEmails,
            icon: Icons.mark_email_unread_rounded,
            accent: VaTheme.primary,
            subtitle: data.actionEmails == 0 ? 'Inbox automation is caught up' : 'Open the unresolved email action queue',
            onTap: onOpenEmails,
          ),
          const SizedBox(height: 10),
          CountCard(
            label: 'Unpaid bills',
            value: data.unpaidBills,
            icon: Icons.receipt_long_rounded,
            accent: VaTheme.success,
            subtitle: data.unpaidBills == 0 ? 'No outstanding bills detected' : 'Review bills or run eligible automatic payments',
            onTap: onOpenBills,
          ),
          const SizedBox(height: 10),
          CountCard(
            label: 'Open tasks',
            value: data.openTasks,
            icon: Icons.task_alt_rounded,
            accent: VaTheme.secondary,
            subtitle: data.openTasks == 0 ? 'No manual follow-ups are waiting' : 'Review approvals, follow-ups, and exceptions',
            onTap: onOpenTasks,
          ),
          const SizedBox(height: 10),
          CountCard(
            label: 'Payment approvals',
            value: data.paymentActions,
            icon: Icons.verified_user_rounded,
            accent: VaTheme.warning,
            subtitle: data.paymentActions == 0 ? 'No bank authorization is waiting' : 'Bank or SCA authorization is required',
            onTap: onOpenPayments,
          ),
          const SizedBox(height: 22),
          Row(
            children: [
              Expanded(
                child: Text('Live connections', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
              ),
              TextButton(onPressed: onOpenServices, child: const Text('Manage')),
            ],
          ),
          const SizedBox(height: 6),
          VaSectionCard(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (final entry in data.connectedServices.entries)
                  ListTile(
                    leading: Container(
                      width: 38,
                      height: 38,
                      decoration: BoxDecoration(
                        color: (entry.value == true ? VaTheme.success : VaTheme.danger).withValues(alpha: .14),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(
                        entry.value == true ? Icons.check_rounded : Icons.link_off_rounded,
                        color: entry.value == true ? VaTheme.success : VaTheme.danger,
                      ),
                    ),
                    title: Text(_serviceName(entry.key), style: const TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: Text(entry.value == true ? 'Connected and available' : 'Connection required'),
                    trailing: Container(
                      width: 9,
                      height: 9,
                      decoration: BoxDecoration(
                        color: entry.value == true ? VaTheme.success : VaTheme.danger,
                        shape: BoxShape.circle,
                      ),
                    ),
                    onTap: onOpenServices,
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _serviceName(String key) => switch (key) {
        'google' => 'Google Gmail & Calendar',
        'drive' => 'Google Drive archive',
        'contacts' => 'Google Contacts',
        'ai' => 'AI decision engine',
        'banking' => 'Open Banking',
        'github' => 'GitHub',
        'cloudflare' => 'Cloudflare',
        'discord' => 'Discord',
        _ => key,
      };
}

class _AutomationHero extends StatelessWidget {
  const _AutomationHero({required this.state});

  final AppState state;

  @override
  Widget build(BuildContext context) {
    final running = state.configuration['automation_enabled'] == true;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(26),
        gradient: const LinearGradient(
          colors: [Color(0xFF25154B), Color(0xFF102A55), Color(0xFF0B1B31)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        border: Border.all(color: const Color(0xFF563D94)),
        boxShadow: [
          BoxShadow(color: VaTheme.primary.withValues(alpha: .12), blurRadius: 30, offset: const Offset(0, 14)),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(18),
                child: Image.asset('assets/app_icon.png', width: 62, height: 62, fit: BoxFit.cover),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Full-Time VA', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
                    const SizedBox(height: 3),
                    Text(
                      running ? 'Automation is active' : 'Automation is paused',
                      style: TextStyle(color: running ? VaTheme.success : VaTheme.warning, fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(
                  color: VaTheme.primary.withValues(alpha: .18),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.auto_awesome, size: 16, color: Color(0xFFC9B5FF)),
                    SizedBox(width: 5),
                    Text('AI', style: TextStyle(fontWeight: FontWeight.w800)),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          const Text(
            'Run the complete VA workflow now: Gmail decisions, action reconciliation, bank synchronization, eligible auto-pay, payment status checks, contacts, and connector rules.',
            style: TextStyle(color: Color(0xFFD6DDF0), height: 1.4),
          ),
          const SizedBox(height: 14),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: state.busy ? null : () => _runNow(context),
              icon: const Icon(Icons.bolt_rounded),
              label: const Text('Run VA now'),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _runNow(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runAutomationNow();
      if (!context.mounted) return;
      final errors = (result['errors'] as Map?)?.length ?? 0;
      final message = errors == 0
          ? 'VA run completed. Safe actions were executed and remaining exceptions were queued.'
          : 'VA run completed with $errors exception${errors == 1 ? '' : 's'}. Open the action cards for details.';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}
