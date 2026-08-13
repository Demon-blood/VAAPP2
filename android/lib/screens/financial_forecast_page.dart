import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class FinancialForecastPage extends StatelessWidget {
  const FinancialForecastPage({super.key});

  String _date(dynamic value) {
    final parsed = DateTime.tryParse('$value');
    if (parsed == null) return '—';
    return '${parsed.day.toString().padLeft(2, '0')}/${parsed.month.toString().padLeft(2, '0')}/${parsed.year}';
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final forecast = state.financeForecast;
    final scopes = (forecast['scopes'] as List? ?? const []).cast<Map>();
    final plans = (forecast['allocation_plans'] as List? ?? const []).cast<Map>();
    final generatedAt = forecast['generated_at'];
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshMoneyData(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 30),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        forecast['status'] == 'at_risk' ? Icons.warning_amber_rounded : Icons.insights_rounded,
                        color: forecast['status'] == 'at_risk' ? VaTheme.warning : VaTheme.secondary,
                      ),
                      const SizedBox(width: 9),
                      const Expanded(
                        child: Text('Financial Allocation & Forecasting', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text('Status: ${forecast['status'] ?? 'not generated'}'),
                  Text('Horizon: ${forecast['horizon_days'] ?? 90} days'),
                  Text('Generated: ${generatedAt == null ? '—' : _date(generatedAt)}'),
                  const SizedBox(height: 8),
                  const Text(
                    'Only cash that remains surplus in the conservative scenario can be allocated. Safety reserves and protected investment funding are never treated as investable surplus.',
                  ),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: state.busy ? null : () => _runNow(context),
                    icon: const Icon(Icons.auto_graph_rounded),
                    label: const Text('Refresh forecast & allocate safe surplus'),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          if (scopes.isEmpty)
            const Card(child: ListTile(title: Text('No forecast is available yet.')))
          else
            for (final raw in scopes) ...[
              _ScopeForecastCard(scope: Map<String, dynamic>.from(raw)),
              const SizedBox(height: 10),
            ],
          const SizedBox(height: 8),
          Text('Allocation plans', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
          const SizedBox(height: 6),
          const Text('Transfers remain subject to the configured account policy, provider limits and bank SCA. A plan is not complete until the bank transfer is verified.'),
          const SizedBox(height: 10),
          if (plans.isEmpty)
            const Card(child: ListTile(title: Text('No allocation plan has been created for this forecast.')))
          else
            for (final raw in plans) _AllocationPlanCard(plan: Map<String, dynamic>.from(raw)),
        ],
      ),
    );
  }

  Future<void> _runNow(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runFinancialForecastNow();
      if (!context.mounted) return;
      final plans = (result['allocation_plans'] as List? ?? const []).length;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Forecast refreshed. Allocation plans: $plans')),
      );
    } catch (_) {}
  }
}

class _ScopeForecastCard extends StatelessWidget {
  const _ScopeForecastCard({required this.scope});

  final Map<String, dynamic> scope;

  String _money(dynamic value) {
    final amount = double.tryParse('$value') ?? 0;
    return '${amount.toStringAsFixed(2)} EUR';
  }

  @override
  Widget build(BuildContext context) {
    final atRisk = scope['status'] == 'at_risk';
    final series = (scope['series'] as List? ?? const []).cast<Map>();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(atRisk ? Icons.warning_amber_rounded : Icons.savings_outlined, color: atRisk ? VaTheme.warning : VaTheme.secondary),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('${scope['scope'] ?? 'personal'} · ${scope['status'] ?? ''}', style: const TextStyle(fontWeight: FontWeight.w900, fontSize: 17)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text('Starting managed cash: ${_money(scope['starting_cash'])}'),
            Text('Protected cash floor: ${_money(scope['protected_floor'])}'),
            Text('Lowest base forecast: ${_money(scope['base_min_cash'])}'),
            Text('Lowest conservative forecast: ${_money(scope['conservative_min_cash'])}'),
            Text('Safe allocatable surplus: ${_money(scope['allocatable_surplus'])}'),
            if (scope['days_until_floor'] != null)
              Text('Protected floor breached in conservative case after ${scope['days_until_floor']} days', style: const TextStyle(fontWeight: FontWeight.w700)),
            const Divider(height: 22),
            Text('Baseline monthly spend: ${_money(scope['monthly_baseline_spend'])}'),
            Text('Protected recurring: ${_money(scope['monthly_protected_recurring'])}'),
            Text('Known bills next 30 days: ${_money(scope['known_bills_next_30_days'])}'),
            Text('Protected investment funding: ${_money(scope['monthly_investment_funding'])}'),
            if (series.isNotEmpty) ...[
              const SizedBox(height: 10),
              const Text('Cash checkpoints', style: TextStyle(fontWeight: FontWeight.w800)),
              const SizedBox(height: 4),
              for (final raw in series.take(8))
                Text('Day ${raw['day']}: base ${_money(raw['base_cash'])} · conservative ${_money(raw['conservative_cash'])}'),
            ],
          ],
        ),
      ),
    );
  }
}

class _AllocationPlanCard extends StatelessWidget {
  const _AllocationPlanCard({required this.plan});

  final Map<String, dynamic> plan;

  String _money(dynamic value, [String currency = 'EUR']) {
    final amount = double.tryParse('$value') ?? 0;
    return '${amount.toStringAsFixed(2)} $currency';
  }

  @override
  Widget build(BuildContext context) {
    final actions = (plan['actions'] as List? ?? const []).cast<Map>();
    final details = Map<String, dynamic>.from((plan['details'] as Map?) ?? const {});
    final reasons = (details['reasons'] as List? ?? const []).map((value) => '$value').toList();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${plan['scope'] ?? 'personal'} · ${plan['status'] ?? ''}', style: const TextStyle(fontWeight: FontWeight.w900, fontSize: 17)),
            const SizedBox(height: 6),
            Text('Forecast-safe surplus: ${_money(plan['allocatable_surplus'])}'),
            Text('Conservative minimum: ${_money(plan['conservative_min_cash'])}'),
            if (reasons.isNotEmpty) ...[
              const SizedBox(height: 6),
              for (final reason in reasons.take(3)) Text(reason),
            ],
            if (actions.isNotEmpty) ...[
              const Divider(height: 22),
              for (final raw in actions) _AllocationActionTile(action: Map<String, dynamic>.from(raw)),
            ],
          ],
        ),
      ),
    );
  }
}

class _AllocationActionTile extends StatelessWidget {
  const _AllocationActionTile({required this.action});

  final Map<String, dynamic> action;

  @override
  Widget build(BuildContext context) {
    final authorization = '${action['authorization_url'] ?? ''}';
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: const Icon(Icons.compare_arrows_rounded),
      title: Text('${action['amount']} ${action['currency'] ?? 'EUR'} · ${action['destination_role'] ?? ''}'),
      subtitle: Text('${action['source_account'] ?? ''} → ${action['destination_account'] ?? ''}\n${action['status'] ?? ''}'),
      isThreeLine: true,
      trailing: authorization.isNotEmpty
          ? FilledButton(
              onPressed: () => launchUrl(Uri.parse(authorization), mode: LaunchMode.externalApplication),
              child: const Text('Authorize'),
            )
          : null,
    );
  }
}
