import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import 'dashboard_page.dart';
import 'inbox_page.dart';
import 'money_page.dart';
import 'services_page.dart';
import 'settings_page.dart';
import 'work_page.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int index = 0;

  final pages = const [
    DashboardPage(),
    InboxPage(),
    WorkPage(),
    MoneyPage(),
    ServicesPage(),
    SettingsPage(),
  ];

  final labels = const ['Today', 'Inbox', 'Work', 'Money', 'Services', 'Settings'];

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Scaffold(
      appBar: AppBar(
        title: Text(labels[index]),
        actions: [
          IconButton(
            tooltip: 'Refresh live data',
            onPressed: state.busy ? null : () => context.read<AppState>().refreshAll(),
            icon: state.busy
                ? const SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          if (state.serverWarning != null)
            MaterialBanner(
              content: Text(state.serverWarning!),
              leading: const Icon(Icons.cloud_off_outlined),
              actions: [
                if (state.endpointErrors.isNotEmpty)
                  TextButton(onPressed: () => _showDiagnostics(context), child: const Text('Details')),
                TextButton(onPressed: () => context.read<AppState>().refreshAll(), child: const Text('Retry')),
                if (state.repairRecommended)
                  TextButton(onPressed: () => _repairServer(context), child: const Text('Repair server')),
              ],
            ),
          if (state.error != null)
            MaterialBanner(
              content: Text(state.error!),
              leading: const Icon(Icons.error_outline),
              actions: [
                TextButton(onPressed: () => context.read<AppState>().refreshAll(), child: const Text('Refresh')),
              ],
            ),
          Expanded(child: IndexedStack(index: index, children: pages)),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (value) => setState(() => index = value),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.today_outlined), selectedIcon: Icon(Icons.today), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.inbox_outlined), selectedIcon: Icon(Icons.inbox), label: 'Inbox'),
          NavigationDestination(icon: Icon(Icons.work_outline), selectedIcon: Icon(Icons.work), label: 'Work'),
          NavigationDestination(icon: Icon(Icons.account_balance_wallet_outlined), selectedIcon: Icon(Icons.account_balance_wallet), label: 'Money'),
          NavigationDestination(icon: Icon(Icons.hub_outlined), selectedIcon: Icon(Icons.hub), label: 'Services'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }

  Future<void> _showDiagnostics(BuildContext context) async {
    final state = context.read<AppState>();
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Server diagnostics'),
        content: SizedBox(
          width: 560,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Backend version: ${state.systemInfo['version'] ?? 'not reported'}'),
                const SizedBox(height: 12),
                for (final entry in state.endpointErrors.entries) ...[
                  Text(entry.key, style: Theme.of(dialogContext).textTheme.titleSmall),
                  SelectableText(entry.value),
                  const SizedBox(height: 12),
                ],
              ],
            ),
          ),
        ),
        actions: [TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Close'))],
      ),
    );
  }

  Future<void> _repairServer(BuildContext context) async {
    final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('Repair backend deployment'),
            content: const Text(
              'This unpairs only this phone and returns to the deployment wizard. Enter the same Render workspace, repository, and service name. The wizard will update and redeploy the existing service instead of creating a duplicate.',
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Continue')),
            ],
          ),
        ) ??
        false;
    if (!confirmed || !context.mounted) return;
    await context.read<AppState>().disconnect();
  }
}
