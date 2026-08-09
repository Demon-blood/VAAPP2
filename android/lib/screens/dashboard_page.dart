import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';
import '../widgets/va_mascot.dart';

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
        padding: const EdgeInsets.fromLTRB(14, 8, 14, 28),
        children: [
          _GreetingHero(state: state),
          const SizedBox(height: 14),
          CountCard(
            label: 'Emails needing action',
            value: data.actionEmails,
            icon: Icons.mail_rounded,
            accent: VaTheme.primary,
            subtitle: data.actionEmails == 0
                ? 'Inbox automation is caught up'
                : 'Open unresolved messages and suggested actions',
            onTap: onOpenEmails,
          ),
          const SizedBox(height: 9),
          CountCard(
            label: 'Unpaid bills',
            value: data.unpaidBills,
            icon: Icons.receipt_long_rounded,
            accent: VaTheme.success,
            subtitle: data.unpaidBills == 0
                ? 'No outstanding bills detected'
                : 'Review due bills or run eligible auto-pay',
            onTap: onOpenBills,
          ),
          const SizedBox(height: 9),
          CountCard(
            label: 'Open tasks',
            value: data.openTasks,
            icon: Icons.task_alt_rounded,
            accent: VaTheme.secondary,
            subtitle: data.openTasks == 0
                ? 'You’re all caught up'
                : 'Approvals, follow-ups and exceptions',
            onTap: onOpenTasks,
          ),
          const SizedBox(height: 9),
          CountCard(
            label: 'Payment approvals',
            value: data.paymentActions,
            icon: Icons.verified_user_rounded,
            accent: VaTheme.warning,
            subtitle: data.paymentActions == 0
                ? 'No bank authorization is waiting'
                : 'Bank or SCA authorization is required',
            onTap: onOpenPayments,
          ),
          const SizedBox(height: 12),
          _RunVaButton(state: state),
          const SizedBox(height: 22),
          Row(
            children: [
              Expanded(
                child: Text(
                  'Live connections',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900),
                ),
              ),
              TextButton(onPressed: onOpenServices, child: const Text('View all')),
            ],
          ),
          const SizedBox(height: 6),
          VaSectionCard(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (final entry in data.connectedServices.entries)
                  _ConnectionTile(
                    name: _serviceName(entry.key),
                    connected: entry.value == true,
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
        'drive' => 'Google Drive',
        'contacts' => 'Google Contacts',
        'ai' => 'AI Decision Engine',
        'banking' => 'Open Banking',
        'github' => 'GitHub',
        'cloudflare' => 'Cloudflare',
        'discord' => 'Discord',
        _ => key,
      };
}

class _GreetingHero extends StatelessWidget {
  const _GreetingHero({required this.state});

  final AppState state;

  @override
  Widget build(BuildContext context) {
    final hour = DateTime.now().hour;
    final greeting = hour < 12
        ? 'Good morning'
        : hour < 18
            ? 'Good afternoon'
            : 'Good evening';
    final running = state.configuration['automation_enabled'] == true;

    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(24),
        gradient: const LinearGradient(
          colors: [Color(0xFF28135B), Color(0xFF152A69), Color(0xFF0A1733)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        border: Border.all(color: VaTheme.primary.withValues(alpha: .52)),
        boxShadow: [
          BoxShadow(
            color: VaTheme.primary.withValues(alpha: .15),
            blurRadius: 34,
            offset: const Offset(0, 14),
          ),
        ],
      ),
      child: Stack(
        children: [
          Positioned(
            right: -22,
            top: -38,
            child: Container(
              width: 180,
              height: 180,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  colors: [VaTheme.primary.withValues(alpha: .25), Colors.transparent],
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 17, 10, 15),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '$greeting 👋',
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
                      ),
                      const SizedBox(height: 5),
                      Text(
                        running
                            ? 'Everything is running smoothly. Here’s what needs attention.'
                            : 'Automation is paused. You can still inspect and run actions manually.',
                        style: const TextStyle(color: Color(0xFFD9DDF3), height: 1.35),
                      ),
                      const SizedBox(height: 11),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: (running ? VaTheme.success : VaTheme.warning).withValues(alpha: .16),
                          borderRadius: BorderRadius.circular(999),
                          border: Border.all(
                            color: (running ? VaTheme.success : VaTheme.warning).withValues(alpha: .45),
                          ),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              running ? Icons.bolt_rounded : Icons.pause_rounded,
                              size: 15,
                              color: running ? VaTheme.success : VaTheme.warning,
                            ),
                            const SizedBox(width: 5),
                            Text(
                              running ? 'AUTOMATION ACTIVE' : 'AUTOMATION PAUSED',
                              style: TextStyle(
                                fontSize: 11,
                                letterSpacing: .5,
                                fontWeight: FontWeight.w900,
                                color: running ? VaTheme.success : VaTheme.warning,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 4),
                const VaAssistantMascot(size: 112),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _RunVaButton extends StatelessWidget {
  const _RunVaButton({required this.state});

  final AppState state;

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(17),
          gradient: const LinearGradient(
            colors: [Color(0xFF8B3DFF), Color(0xFF5C2EFF), Color(0xFF2C66FF)],
          ),
          boxShadow: [
            BoxShadow(color: VaTheme.primary.withValues(alpha: .24), blurRadius: 22, offset: const Offset(0, 9)),
          ],
        ),
        child: FilledButton.icon(
          style: FilledButton.styleFrom(
            backgroundColor: Colors.transparent,
            shadowColor: Colors.transparent,
            minimumSize: const Size(double.infinity, 54),
          ),
          onPressed: state.busy ? null : () => _runNow(context),
          icon: const Icon(Icons.play_arrow_rounded),
          label: const Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Run VA now', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w900)),
              Text('Process emails, bills, tasks & more', style: TextStyle(fontSize: 11, color: Color(0xFFE7E2FF))),
            ],
          ),
        ),
      );

  Future<void> _runNow(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runAutomationNow();
      if (!context.mounted) return;
      final errors = (result['errors'] as Map?)?.length ?? 0;
      final cleanup = result['document_cleanup'] as Map?;
      final removed = (cleanup?['removed'] as num?)?.toInt() ?? 0;
      final message = errors == 0
          ? 'VA run completed${removed > 0 ? ' · $removed low-value document${removed == 1 ? '' : 's'} removed' : ''}.'
          : 'VA run completed with $errors exception${errors == 1 ? '' : 's'}. Open the action cards for details.';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}

class _ConnectionTile extends StatelessWidget {
  const _ConnectionTile({required this.name, required this.connected, required this.onTap});

  final String name;
  final bool connected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => ListTile(
        dense: true,
        leading: Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: (connected ? VaTheme.success : VaTheme.danger).withValues(alpha: .13),
            borderRadius: BorderRadius.circular(11),
          ),
          child: Icon(
            connected ? Icons.check_rounded : Icons.link_off_rounded,
            color: connected ? VaTheme.success : VaTheme.danger,
            size: 20,
          ),
        ),
        title: Text(name, style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle: Text(
          connected ? 'Connected' : 'Connection required',
          style: TextStyle(color: connected ? VaTheme.success : VaTheme.danger, fontSize: 12),
        ),
        trailing: Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            color: connected ? VaTheme.success : VaTheme.danger,
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(
                color: (connected ? VaTheme.success : VaTheme.danger).withValues(alpha: .4),
                blurRadius: 8,
              ),
            ],
          ),
        ),
        onTap: onTap,
      );
}
