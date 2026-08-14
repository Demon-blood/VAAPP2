import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import 'communications_page.dart';
import 'fulfillment_page.dart';
import 'services_page.dart';
import 'telephony_page.dart';

class VaOperationsPage extends StatelessWidget {
  const VaOperationsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final overview = state.vaOverview;
    final metrics = Map<String, dynamic>.from((overview['metrics'] as Map?) ?? const {});
    final totals = Map<String, dynamic>.from((metrics['totals'] as Map?) ?? const {});
    final needsUser = (overview['needs_user'] as List? ?? const []).cast<Map>();
    final recent = (overview['recent_objectives'] as List? ?? const []).cast<Map>();
    final capabilities = (state.vaCapabilities['capabilities'] as List? ?? const []).cast<Map>();
    final rate = (metrics['autonomous_completion_rate'] as num?)?.toDouble();
    final active = (metrics['active_objectives'] as num?)?.toInt() ?? 0;
    final waiting = (metrics['waiting_external'] as num?)?.toInt() ?? 0;
    final completed = (totals['objectives_completed'] as num?)?.toInt() ?? 0;

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 28),
        children: [
          _OperatorHero(
            rate: rate,
            active: active,
            waiting: waiting,
            completed: completed,
            backlog: (overview['event_backlog'] as num?)?.toInt() ?? 0,
          ),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: state.busy ? null : () => _runNow(context),
              icon: const Icon(Icons.play_arrow_rounded),
              label: const Text('Run VA now'),
            ),
          ),
          const SizedBox(height: 20),
          _SectionHeader(
            title: 'Needs You',
            subtitle: needsUser.isEmpty
                ? 'No unavoidable human blockers.'
                : 'Only provider authentication or material decisions belong here.',
          ),
          const SizedBox(height: 8),
          if (needsUser.isEmpty)
            const _StateCard(
              icon: Icons.check_circle_outline_rounded,
              title: 'Nothing is waiting on you',
              detail: 'Routine work remains owned by the VA.',
              accent: VaTheme.success,
            )
          else
            for (final raw in needsUser) ...[
              _ObjectiveCard(
                row: Map<String, dynamic>.from(raw),
                onRecheck: () => _recheck(context, raw['id']),
                needsUser: true,
              ),
              const SizedBox(height: 8),
            ],
          const SizedBox(height: 20),
          const _SectionHeader(
            title: 'Execution capabilities',
            subtitle: 'Real executors only. READY means configured but not yet proven by end-to-end provider delivery.',
          ),
          const SizedBox(height: 8),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                children: [
                  for (final raw in capabilities) ...[
                    _CapabilityRow(
                      row: Map<String, dynamic>.from(raw),
                      onTap: () => _showCapabilitySetup(context, Map<String, dynamic>.from(raw)),
                    ),
                    if (raw != capabilities.last) const Divider(height: 18),
                  ],
                  if (capabilities.isEmpty)
                    const Text('Capability status has not loaded yet.', style: TextStyle(color: VaTheme.textMuted)),
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          const _SectionHeader(
            title: 'VA-owned work',
            subtitle: 'Durable objectives remain here until their real outcome is verified.',
          ),
          const SizedBox(height: 8),
          if (recent.isEmpty)
            const _StateCard(
              icon: Icons.inbox_outlined,
              title: 'No objectives yet',
              detail: 'The autonomous core will create objectives from actionable events.',
              accent: VaTheme.secondary,
            )
          else
            for (final raw in recent.take(30)) ...[
              _ObjectiveCard(row: Map<String, dynamic>.from(raw)),
              const SizedBox(height: 8),
            ],
        ],
      ),
    );
  }

  Future<void> _runNow(BuildContext context) async {
    try {
      await context.read<AppState>().runAutonomousCoreNow();
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Autonomous VA cycle dispatched. Outcomes remain tracked until verified.')),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }

  Future<void> _recheck(BuildContext context, dynamic id) async {
    final objectiveId = (id as num?)?.toInt() ?? int.tryParse('$id');
    if (objectiveId == null) return;
    try {
      await context.read<AppState>().recheckVaObjective(objectiveId);
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }

  Future<void> _showCapabilitySetup(BuildContext context, Map<String, dynamic> row) async {
    final setup = row['setup'] is Map
        ? Map<String, dynamic>.from(row['setup'] as Map)
        : <String, dynamic>{};
    final backend = ((await context.read<AppState>().api.serverUrl) ?? '').replaceAll(RegExp(r'/+$'), '');
    if (!context.mounted) return;

    final steps = (setup['steps'] as List? ?? const [])
        .map((step) => '$step'.replaceAll('{{backend}}', backend.isEmpty ? '<your VA backend>' : backend))
        .where((step) => step.trim().isNotEmpty)
        .toList();
    final action = '${setup['action'] ?? ''}'.trim();
    final destination = '${setup['destination'] ?? ''}'.trim();
    final available = row['available'] == true;
    final readiness = '${row['readiness'] ?? (available ? 'live' : 'offline')}';
    final readyOnly = readiness.toLowerCase() == 'ready';
    final verified = row['verified'];
    final detail = '${row['detail'] ?? ''}'.trim();

    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => SafeArea(
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            20,
            18,
            20,
            20 + MediaQuery.viewInsetsOf(sheetContext).bottom,
          ),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      readyOnly
                          ? Icons.pending_outlined
                          : available
                              ? Icons.check_circle_rounded
                              : Icons.build_circle_outlined,
                      color: readyOnly
                          ? VaTheme.cyan
                          : available
                              ? VaTheme.success
                              : VaTheme.warning,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        '${row['title'] ?? row['key'] ?? 'Capability'}',
                        style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w900),
                      ),
                    ),
                    _CapabilityStateBadge(readiness: readiness, verified: verified == true),
                  ],
                ),
                const SizedBox(height: 10),
                Text(
                  detail.isEmpty ? '${row['executor'] ?? ''}' : detail,
                  style: const TextStyle(color: VaTheme.textMuted),
                ),
                if (available && '${row['executor'] ?? ''}'.trim().isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(
                    'Executor: ${row['executor']}',
                    style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
                  ),
                ],
                if (destination.isNotEmpty) ...[
                  const SizedBox(height: 18),
                  const Text('Setup location', style: TextStyle(fontWeight: FontWeight.w900)),
                  const SizedBox(height: 4),
                  Text(destination),
                ],
                if (steps.isNotEmpty) ...[
                  const SizedBox(height: 18),
                  const Text('What to configure', style: TextStyle(fontWeight: FontWeight.w900)),
                  const SizedBox(height: 8),
                  for (var index = 0; index < steps.length; index++) ...[
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          width: 24,
                          child: Text(
                            '${index + 1}.',
                            style: const TextStyle(fontWeight: FontWeight.w900, color: VaTheme.primaryBright),
                          ),
                        ),
                        Expanded(child: Text(steps[index])),
                      ],
                    ),
                    if (index != steps.length - 1) const SizedBox(height: 8),
                  ],
                ],
                if (action.isNotEmpty) ...[
                  const SizedBox(height: 20),
                  if (action == 'gmail_push')
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: () {
                              Navigator.pop(sheetContext);
                              _openCapabilitySetup(context, 'services');
                            },
                            icon: const Icon(Icons.settings_outlined),
                            label: const Text('Open Services'),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: FilledButton.icon(
                            onPressed: () {
                              Navigator.pop(sheetContext);
                              _activateGmailWatch(context);
                            },
                            icon: const Icon(Icons.sync_rounded),
                            label: const Text('Activate watch'),
                          ),
                        ),
                      ],
                    )
                  else
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton.icon(
                        onPressed: () {
                          Navigator.pop(sheetContext);
                          _openCapabilitySetup(context, action);
                        },
                        icon: const Icon(Icons.open_in_new_rounded),
                        label: const Text('Open setup'),
                      ),
                    ),
                ],
                const SizedBox(height: 10),
                const Text(
                  'A configured executor is not completion evidence. VAAPP still requires the provider/source postcondition before an objective is marked complete.',
                  style: TextStyle(color: VaTheme.textMuted, fontSize: 11),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _activateGmailWatch(BuildContext context) async {
    try {
      final result = await context.read<AppState>().activateGmailWatch();
      if (!context.mounted) return;
      final expiration = (result['expiration'] as num?)?.toInt();
      final expiresAt = expiration == null
          ? null
          : DateTime.fromMillisecondsSinceEpoch(expiration).toLocal();
      final suffix = expiresAt == null
          ? ''
          : ' until ${expiresAt.day.toString().padLeft(2, '0')}/${expiresAt.month.toString().padLeft(2, '0')} '
              '${expiresAt.hour.toString().padLeft(2, '0')}:${expiresAt.minute.toString().padLeft(2, '0')}';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Gmail watch accepted$suffix. The capability becomes delivery-verified after a real Pub/Sub notification reaches VAAPP.',
          ),
        ),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not activate Gmail watch: $error')),
        );
      }
    }
  }

  Future<void> _openCapabilitySetup(BuildContext context, String action) async {
    if (!context.mounted) return;
    switch (action) {
      case 'services':
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => Scaffold(
              appBar: AppBar(title: const Text('Services')),
              body: const ServicesPage(),
            ),
          ),
        );
        break;
      case 'browser_portals':
        DefaultTabController.of(context).animateTo(3);
        break;
      case 'fulfillment':
        await Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const FulfillmentPage()),
        );
        break;
      case 'telephony':
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => Scaffold(
              appBar: AppBar(title: const Text('Calls')),
              body: const TelephonyPage(),
            ),
          ),
        );
        break;
      case 'communications':
        await Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const CommunicationsPage()),
        );
        break;
    }
    if (context.mounted) {
      await context.read<AppState>().refreshAll(showBusy: false);
    }
  }
}

class _OperatorHero extends StatelessWidget {
  const _OperatorHero({
    required this.rate,
    required this.active,
    required this.waiting,
    required this.completed,
    required this.backlog,
  });

  final double? rate;
  final int active;
  final int waiting;
  final int completed;
  final int backlog;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF25164C), Color(0xFF0B284E)],
          ),
          borderRadius: BorderRadius.circular(24),
          border: Border.all(color: const Color(0xFF4D3188)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.hub_rounded, color: VaTheme.primaryBright),
                SizedBox(width: 9),
                Expanded(
                  child: Text('Full-Time VA operator', style: TextStyle(fontSize: 21, fontWeight: FontWeight.w900)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Observe → own → execute → verify → follow up',
              style: TextStyle(color: VaTheme.textMuted),
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(child: _Metric(label: 'Autonomy', value: rate == null ? '—' : '${rate!.toStringAsFixed(0)}%')),
                const SizedBox(width: 8),
                Expanded(child: _Metric(label: 'Active', value: '$active')),
                const SizedBox(width: 8),
                Expanded(child: _Metric(label: 'Waiting', value: '$waiting')),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(child: _Metric(label: 'Completed', value: '$completed')),
                const SizedBox(width: 8),
                Expanded(child: _Metric(label: 'Event backlog', value: '$backlog')),
                const Spacer(),
              ],
            ),
          ],
        ),
      );
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 10),
        decoration: BoxDecoration(
          color: VaTheme.background.withValues(alpha: .35),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: VaTheme.border.withValues(alpha: .65)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w900)),
            const SizedBox(height: 2),
            Text(label, style: const TextStyle(color: VaTheme.textMuted, fontSize: 11)),
          ],
        ),
      );
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, required this.subtitle});
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w900)),
          const SizedBox(height: 3),
          Text(subtitle, style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
        ],
      );
}

class _CapabilityRow extends StatelessWidget {
  const _CapabilityRow({required this.row, required this.onTap});
  final Map<String, dynamic> row;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final available = row['available'] == true;
    final readiness = '${row['readiness'] ?? (available ? 'live' : 'offline')}';
    final verified = row['verified'] == true;
    final readyOnly = readiness.toLowerCase() == 'ready';
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(
              readyOnly
                  ? Icons.pending_outlined
                  : available
                      ? Icons.check_circle_rounded
                      : Icons.link_off_rounded,
              color: readyOnly
                  ? VaTheme.cyan
                  : available
                      ? VaTheme.success
                      : VaTheme.warning,
              size: 20,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${row['title'] ?? row['key'] ?? 'Capability'}', style: const TextStyle(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 2),
                  Text(
                    '${row['detail'] ?? (available ? row['executor'] ?? '' : 'Connection unavailable')}',
                    style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            _CapabilityStateBadge(readiness: readiness, verified: verified),
            const SizedBox(width: 2),
            const Icon(Icons.chevron_right_rounded, size: 17, color: VaTheme.textMuted),
          ],
        ),
      ),
    );
  }
}

class _CapabilityStateBadge extends StatelessWidget {
  const _CapabilityStateBadge({required this.readiness, required this.verified});

  final String readiness;
  final bool verified;

  @override
  Widget build(BuildContext context) {
    final normalized = readiness.toLowerCase();
    final label = normalized == 'ready' ? 'READY' : normalized == 'live' ? 'LIVE' : 'OFFLINE';
    final color = normalized == 'live'
        ? VaTheme.success
        : normalized == 'ready'
            ? VaTheme.cyan
            : VaTheme.warning;
    return Tooltip(
      message: verified
          ? 'Real provider delivery has been observed.'
          : normalized == 'ready'
              ? 'Executor is configured, but end-to-end delivery has not yet been observed.'
              : label,
      child: Text(
        label,
        style: TextStyle(color: color, fontWeight: FontWeight.w900, fontSize: 10),
      ),
    );
  }
}

class _ObjectiveCard extends StatelessWidget {
  const _ObjectiveCard({required this.row, this.onRecheck, this.needsUser = false});
  final Map<String, dynamic> row;
  final VoidCallback? onRecheck;
  final bool needsUser;

  @override
  Widget build(BuildContext context) {
    final status = '${row['status'] ?? 'unknown'}';
    final accent = _statusColor(status);
    final reason = needsUser
        ? '${row['needs_user_reason'] ?? ''}'
        : '${row['blocked_reason'] ?? row['last_error'] ?? ''}';
    final steps = (row['steps'] as List? ?? const []).length;
    final evidence = (row['evidence_count'] as num?)?.toInt() ?? 0;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(width: 4, height: 34, decoration: BoxDecoration(color: accent, borderRadius: BorderRadius.circular(4))),
                const SizedBox(width: 10),
                Expanded(child: Text('${row['title'] ?? 'VA objective'}', style: const TextStyle(fontWeight: FontWeight.w900))),
                _StatusChip(status: status, color: accent),
              ],
            ),
            if ('${row['goal'] ?? ''}'.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('${row['goal']}', style: const TextStyle(color: VaTheme.textMuted)),
            ],
            if (reason.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(reason, style: TextStyle(color: needsUser ? VaTheme.warning : VaTheme.textMuted, fontSize: 12)),
            ],
            const SizedBox(height: 9),
            Text('$steps persisted step${steps == 1 ? '' : 's'} · $evidence verified outcome${evidence == 1 ? '' : 's'}',
                style: const TextStyle(color: VaTheme.textMuted, fontSize: 11)),
            if (onRecheck != null) ...[
              const SizedBox(height: 10),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: onRecheck,
                  icon: const Icon(Icons.refresh_rounded, size: 17),
                  label: const Text('Recheck after authorization'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Color _statusColor(String status) => switch (status) {
        'completed' => VaTheme.success,
        'needs_user' => VaTheme.warning,
        'failed' || 'blocked_system' => VaTheme.danger,
        'verifying' || 'executing' => VaTheme.cyan,
        'waiting' || 'waiting_external' || 'blocked_capability' => VaTheme.secondary,
        _ => VaTheme.primaryBright,
      };
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.status, required this.color});
  final String status;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(color: color.withValues(alpha: .14), borderRadius: BorderRadius.circular(999)),
        child: Text(status.replaceAll('_', ' ').toUpperCase(), style: TextStyle(color: color, fontSize: 9, fontWeight: FontWeight.w900)),
      );
}

class _StateCard extends StatelessWidget {
  const _StateCard({required this.icon, required this.title, required this.detail, required this.accent});
  final IconData icon;
  final String title;
  final String detail;
  final Color accent;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Icon(icon, color: accent),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontWeight: FontWeight.w900)),
                    const SizedBox(height: 3),
                    Text(detail, style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}
