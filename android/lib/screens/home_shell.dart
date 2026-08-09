import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
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
  bool inboxActionOnly = false;
  int moneyTab = 0;

  static const labels = ['Today', 'Inbox', 'Work', 'Money', 'Services', 'Settings'];

  void _openTasks() {
    context.read<AppState>().clearTransientError();
    setState(() => index = 2);
  }

  void _openEmails() {
    context.read<AppState>().clearTransientError();
    setState(() {
      inboxActionOnly = true;
      index = 1;
    });
  }

  void _openBills() {
    final state = context.read<AppState>();
    state.clearTransientError();
    setState(() {
      moneyTab = 0;
      index = 3;
    });
    state.refreshMoneyData();
  }

  void _openPayments() {
    final state = context.read<AppState>();
    state.clearTransientError();
    setState(() {
      moneyTab = 1;
      index = 3;
    });
    state.refreshMoneyData();
  }

  void _openServices() {
    context.read<AppState>().clearTransientError();
    setState(() => index = 4);
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final pages = [
      DashboardPage(
        onOpenTasks: _openTasks,
        onOpenEmails: _openEmails,
        onOpenBills: _openBills,
        onOpenPayments: _openPayments,
        onOpenServices: _openServices,
      ),
      InboxPage(
        actionOnly: inboxActionOnly,
        onShowAll: () => setState(() => inboxActionOnly = false),
        onOpenTasks: _openTasks,
        onOpenBills: _openBills,
      ),
      WorkPage(onOpenBills: _openBills, onOpenPayments: _openPayments),
      MoneyPage(key: ValueKey('money-$moneyTab'), initialIndex: moneyTab),
      const ServicesPage(),
      const SettingsPage(),
    ];
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: Row(
          children: [
            if (index != 0) ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.asset('assets/app_icon.png', width: 34, height: 34, fit: BoxFit.cover),
              ),
              const SizedBox(width: 10),
            ],
            Text(labels[index], style: const TextStyle(fontWeight: FontWeight.w800)),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Refresh live data',
            onPressed: state.busy ? null : () => context.read<AppState>().refreshAll(),
            icon: state.busy
                ? const SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh_rounded),
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(
        children: [
          if (state.serverWarning != null)
            MaterialBanner(
              backgroundColor: const Color(0xFF241A31),
              content: Text(state.serverWarning!),
              leading: const Icon(Icons.cloud_off_outlined, color: VaTheme.warning),
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
              backgroundColor: const Color(0xFF2B1720),
              content: Text(state.error!),
              leading: const Icon(Icons.error_outline, color: VaTheme.danger),
              actions: [
                TextButton(onPressed: () => context.read<AppState>().refreshAll(), child: const Text('Refresh')),
              ],
            ),
          Expanded(child: IndexedStack(index: index, children: pages)),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        height: 72,
        selectedIndex: index,
        onDestinationSelected: (value) {
          final appState = context.read<AppState>();
          appState.clearTransientError();
          setState(() {
            index = value;
            if (value == 1) inboxActionOnly = false;
            if (value == 3) moneyTab = 0;
          });
          if (value == 3) appState.refreshMoneyData();
        },
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home_rounded), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.inbox_outlined), selectedIcon: Icon(Icons.inbox_rounded), label: 'Inbox'),
          NavigationDestination(icon: Icon(Icons.work_outline), selectedIcon: Icon(Icons.work_rounded), label: 'Work'),
          NavigationDestination(icon: Icon(Icons.account_balance_wallet_outlined), selectedIcon: Icon(Icons.account_balance_wallet_rounded), label: 'Money'),
          NavigationDestination(icon: Icon(Icons.hub_outlined), selectedIcon: Icon(Icons.hub_rounded), label: 'Services'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings_rounded), label: 'Settings'),
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
