import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../release_contract.dart';

class ProductStatusPage extends StatelessWidget {
  const ProductStatusPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final backendVersion = '${state.systemInfo['version'] ?? ''}';
    final compatible = _versionAtLeast(backendVersion, minimumBackendVersion);
    final automationEnabled = state.configuration['automation_enabled'] == true;
    final healthStatus = '${state.autopilotHealth['status'] ?? 'unknown'}';
    final capabilities = _capabilities(state.vaCapabilities);
    final available = capabilities.where((item) => item['available'] == true).toList();
    final setupGaps = capabilities.where((item) => item['available'] != true).toList();
    final needsYou = (state.dailyBriefing['needs_you'] as List? ?? const []).length;
    final endpointErrors = state.endpointErrors.length;
    final coreReady = compatible && automationEnabled && endpointErrors == 0 && healthStatus == 'healthy' && capabilities.isNotEmpty;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Product status'),
        actions: [
          IconButton(
            tooltip: 'Refresh live status',
            onPressed: state.busy ? null : () => context.read<AppState>().refreshAll(),
            icon: state.busy
                ? const SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () => context.read<AppState>().refreshAll(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
          children: [
            _ReleaseCard(
              coreReady: coreReady,
              backendVersion: backendVersion,
              compatible: compatible,
              automationEnabled: automationEnabled,
              healthStatus: healthStatus,
              endpointErrors: endpointErrors,
              available: available.length,
              total: capabilities.length,
              needsYou: needsYou,
            ),
            if (state.serverWarning != null) ...[
              const SizedBox(height: 12),
              Card(
                child: ListTile(
                  leading: const Icon(Icons.warning_amber_rounded),
                  title: const Text('Server attention required'),
                  subtitle: Text(state.serverWarning!),
                ),
              ),
            ],
            const SizedBox(height: 12),
            _ActionCard(state: state),
            const SizedBox(height: 18),
            Text('Verified executors', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
            const SizedBox(height: 8),
            Card(
              child: available.isEmpty
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: Text('No external executor is currently verified as available.'),
                    )
                  : Column(
                      children: [
                        for (var i = 0; i < available.length; i++) ...[
                          _CapabilityTile(item: available[i], available: true),
                          if (i != available.length - 1) const Divider(height: 1),
                        ],
                      ],
                    ),
            ),
            const SizedBox(height: 18),
            Text('Setup gaps', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
            const SizedBox(height: 6),
            Text(
              setupGaps.isEmpty
                  ? 'Every shipped executor currently reports available.'
                  : 'These are capability/setup gaps, not completed work and not automatic user obligations.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            Card(
              child: setupGaps.isEmpty
                  ? const ListTile(
                      leading: Icon(Icons.check_circle_outline_rounded),
                      title: Text('No capability gaps reported'),
                    )
                  : Column(
                      children: [
                        for (var i = 0; i < setupGaps.length; i++) ...[
                          _CapabilityTile(item: setupGaps[i], available: false),
                          if (i != setupGaps.length - 1) const Divider(height: 1),
                        ],
                      ],
                    ),
            ),
            const SizedBox(height: 18),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('v1.0 completion contract', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
                    const SizedBox(height: 8),
                    const Text(
                      'This screen reports release compatibility, live executor availability, recovery health and setup gaps. '
                      'It never treats a configured provider, dispatched browser action, placed call, initiated payment, or local UI state as proof that an external objective completed.',
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'External work is complete only when its domain ledger has independent provider/source evidence for the required postcondition.',
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  static List<Map<String, dynamic>> _capabilities(Map<String, dynamic> matrix) =>
      (matrix['capabilities'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();

  static bool _versionAtLeast(String actual, String required) {
    List<int> parts(String value) => value
        .split(RegExp(r'[^0-9]+'))
        .where((part) => part.isNotEmpty)
        .take(3)
        .map(int.parse)
        .toList();
    final a = parts(actual);
    final r = parts(required);
    if (a.isEmpty) return false;
    for (var i = 0; i < 3; i++) {
      final av = i < a.length ? a[i] : 0;
      final rv = i < r.length ? r[i] : 0;
      if (av != rv) return av > rv;
    }
    return true;
  }
}

class _ReleaseCard extends StatelessWidget {
  const _ReleaseCard({
    required this.coreReady,
    required this.backendVersion,
    required this.compatible,
    required this.automationEnabled,
    required this.healthStatus,
    required this.endpointErrors,
    required this.available,
    required this.total,
    required this.needsYou,
  });

  final bool coreReady;
  final String backendVersion;
  final bool compatible;
  final bool automationEnabled;
  final String healthStatus;
  final int endpointErrors;
  final int available;
  final int total;
  final int needsYou;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(coreReady ? Icons.verified_rounded : Icons.shield_outlined, size: 34),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          coreReady ? 'v1.0 core ready' : 'v1.0 needs attention',
                          style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
                        ),
                        Text('Android ${appRelease} · backend ${backendVersion.isEmpty ? 'not reported' : backendVersion}'),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  _StatusChip(label: compatible ? 'Backend compatible' : 'Backend upgrade required', ok: compatible),
                  _StatusChip(label: automationEnabled ? 'Automation active' : 'Automation paused', ok: automationEnabled),
                  _StatusChip(label: 'Autopilot $healthStatus', ok: healthStatus == 'healthy'),
                  _StatusChip(label: '$available/$total executors available', ok: total > 0 && available > 0),
                  _StatusChip(label: '$endpointErrors endpoint errors', ok: endpointErrors == 0),
                  _StatusChip(label: '$needsYou needs you', ok: needsYou == 0),
                ],
              ),
            ],
          ),
        ),
      );
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.label, required this.ok});

  final String label;
  final bool ok;

  @override
  Widget build(BuildContext context) => Chip(
        avatar: Icon(ok ? Icons.check_rounded : Icons.info_outline_rounded, size: 18),
        label: Text(label),
      );
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({required this.state});

  final AppState state;

  @override
  Widget build(BuildContext context) {
    final actionable = (state.autopilotHealth['actionable_dead_letters'] as num?)?.toInt() ?? 0;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Operations', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
            const SizedBox(height: 10),
            FilledButton.icon(
              onPressed: state.busy ? null : () => _runNow(context),
              icon: const Icon(Icons.bolt_rounded),
              label: const Text('Run complete VA workflow now'),
            ),
            if (actionable > 0) ...[
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: state.busy ? null : () => _recover(context),
                icon: const Icon(Icons.healing_rounded),
                label: Text('Recover $actionable active exception${actionable == 1 ? '' : 's'}'),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _runNow(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runAutomationNow();
      if (!context.mounted) return;
      final errors = (result['errors'] as Map?)?.length ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(errors == 0 ? 'VA workflow completed successfully.' : 'VA workflow completed with $errors exception${errors == 1 ? '' : 's'}.' )),
      );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _recover(BuildContext context) async {
    try {
      final result = await context.read<AppState>().recoverAutopilot();
      if (!context.mounted) return;
      final requeued = (result['requeued'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Recovery requeued $requeued item${requeued == 1 ? '' : 's'}.' )),
      );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}

class _CapabilityTile extends StatelessWidget {
  const _CapabilityTile({required this.item, required this.available});

  final Map<String, dynamic> item;
  final bool available;

  @override
  Widget build(BuildContext context) {
    final title = '${item['title'] ?? item['key'] ?? 'Capability'}';
    final executor = '${item['executor'] ?? ''}';
    final detail = '${item['detail'] ?? ''}';
    final resolution = '${item['resolution'] ?? ''}';
    return ListTile(
      leading: Icon(available ? Icons.check_circle_outline_rounded : Icons.add_link_rounded),
      title: Text(title),
      subtitle: Text([
        if (executor.isNotEmpty) executor,
        if (detail.isNotEmpty) detail,
        if (!available && resolution.isNotEmpty) 'Resolution: $resolution',
      ].join('\n')),
      isThreeLine: detail.isNotEmpty,
    );
  }
}
