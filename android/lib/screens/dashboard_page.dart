import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../widgets/common_widgets.dart';

class DashboardPage extends StatelessWidget {
  const DashboardPage({super.key});

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
        padding: const EdgeInsets.all(16),
        children: [
          Text('VA status', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 12),
          CountCard(label: 'Open tasks', value: data.openTasks, icon: Icons.task_alt),
          CountCard(label: 'Emails needing action', value: data.actionEmails, icon: Icons.mark_email_unread),
          CountCard(label: 'Unpaid bills', value: data.unpaidBills, icon: Icons.receipt_long),
          CountCard(label: 'Payment approvals', value: data.paymentActions, icon: Icons.verified_user_outlined),
          const SizedBox(height: 16),
          Text('Live connections', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          ...data.connectedServices.entries.map(
            (entry) => ListTile(
              leading: Icon(entry.value == true ? Icons.check_circle : Icons.cancel_outlined),
              title: Text(_serviceName(entry.key)),
              subtitle: Text(entry.value == true ? 'Connected' : 'Connection required'),
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
