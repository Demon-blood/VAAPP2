import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class InvestmentsPage extends StatelessWidget {
  const InvestmentsPage({super.key});

  double _number(dynamic value) => double.tryParse('$value') ?? 0;

  String _money(dynamic value, [String currency = 'EUR']) {
    final amount = _number(value);
    final symbol = switch (currency.toUpperCase()) {
      'EUR' => '€',
      'USD' => r'$',
      'GBP' => '£',
      _ => '${currency.toUpperCase()} ',
    };
    return '$symbol${amount.toStringAsFixed(2)}';
  }

  String _signedMoney(dynamic value, String currency) {
    final amount = _number(value);
    final prefix = amount > 0 ? '+' : '';
    return '$prefix${_money(amount, currency)}';
  }

  String _shortDate(dynamic value) {
    final parsed = DateTime.tryParse('$value');
    if (parsed == null) return '—';
    return '${parsed.day.toString().padLeft(2, '0')}/${parsed.month.toString().padLeft(2, '0')}/${parsed.year}';
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final data = state.financeInvestments;
    final portfolios = (data['portfolios'] as List? ?? const []).cast<Map>();
    final totals = Map<String, dynamic>.from((data['total_value_by_currency'] as Map?) ?? const {});
    final kraken = Map<String, dynamic>.from((data['kraken'] as Map?) ?? const {});
    final autopilot = Map<String, dynamic>.from((data['autopilot'] as Map?) ?? const {});
    final funding = Map<String, dynamic>.from((data['funding_transfers'] as Map?) ?? const {});
    final krakenFunding = (funding['kraken'] as List? ?? const []).cast<Map>();

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshMoneyData(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 30),
        children: [
          _HeroCard(
            totals: totals,
            kraken: kraken,
            portfolioCount: (data['portfolio_count'] as num?)?.toInt() ?? 0,
            positionCount: (data['position_count'] as num?)?.toInt() ?? 0,
            money: _money,
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: state.busy ? null : () => _importStatements(context),
                  icon: const Icon(Icons.upload_file_rounded),
                  label: const Text('Import statements'),
                ),
              ),
              const SizedBox(width: 10),
              IconButton.filledTonal(
                tooltip: 'Refresh investments',
                onPressed: state.busy ? null : () => context.read<AppState>().refreshMoneyData(),
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          const SizedBox(height: 18),
          _SectionTitle(
            title: 'Portfolios',
            subtitle: portfolios.isEmpty
                ? 'Import Revolut Securities Account and P&L statements to populate holdings.'
                : 'Statement-backed holdings. Revolut Brokerage and Robo stay separate.',
          ),
          const SizedBox(height: 8),
          if (portfolios.isEmpty)
            const _EmptyCard(
              icon: Icons.show_chart_rounded,
              title: 'No Revolut investment portfolio imported yet',
              detail: 'Use Import statements and select the Revolut Securities Account/P&L XLSX or PDF exports.',
            )
          else
            for (final raw in portfolios) ...[
              _PortfolioCard(
                portfolio: Map<String, dynamic>.from(raw),
                money: _money,
                signedMoney: _signedMoney,
                shortDate: _shortDate,
              ),
              const SizedBox(height: 10),
            ],
          const SizedBox(height: 8),
          const _SectionTitle(
            title: 'Kraken',
            subtitle: 'Live API balances when connected. EUR values are estimates from Kraken market prices.',
          ),
          const SizedBox(height: 8),
          _KrakenCard(
            data: kraken,
            fundingRows: krakenFunding,
            autopilot: autopilot,
            money: _money,
            shortDate: _shortDate,
          ),
          const SizedBox(height: 18),
          _PerformanceCard(
            realised: Map<String, dynamic>.from((data['realised_pnl_by_currency'] as Map?) ?? const {}),
            income: Map<String, dynamic>.from((data['net_investment_income_by_currency'] as Map?) ?? const {}),
            tax: Map<String, dynamic>.from((data['withholding_tax_by_currency'] as Map?) ?? const {}),
            signedMoney: _signedMoney,
            money: _money,
          ),
          const SizedBox(height: 18),
          _ContributionCard(
            portfolios: portfolios,
            learnedByScope: Map<String, dynamic>.from((data['learned_monthly_cash_topup_by_scope'] as Map?) ?? const {}),
            autopilot: autopilot,
            krakenFunding: krakenFunding,
            money: _money,
            shortDate: _shortDate,
          ),
        ],
      ),
    );
  }

  Future<void> _importStatements(BuildContext context) async {
    var scope = 'personal';
    final selectedScope = await showDialog<String>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Import investment statements'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Select Revolut Securities Account/P&L XLSX or PDF files. XLSX is used as the structured ledger and PDF adds statement validation/current holdings.',
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: scope,
                decoration: const InputDecoration(labelText: 'Ownership scope'),
                items: const [
                  DropdownMenuItem(value: 'personal', child: Text('Personal')),
                  DropdownMenuItem(value: 'pro', child: Text('Pro / business')),
                ],
                onChanged: (value) => setState(() => scope = value ?? scope),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, scope), child: const Text('Choose files')),
          ],
        ),
      ),
    );
    if (selectedScope == null || !context.mounted) return;
    final picked = await FilePicker.pickFiles(
      allowMultiple: true,
      type: FileType.custom,
      allowedExtensions: const ['pdf', 'xlsx'],
    );
    if (picked == null || !context.mounted) return;
    final paths = picked.files.map((file) => file.path).whereType<String>().toList();
    if (paths.isEmpty) return;
    try {
      final result = await context.read<AppState>().importFinancialHistory(paths, accountScope: selectedScope);
      if (!context.mounted) return;
      final imported = result['imported'] ?? 0;
      final duplicates = result['duplicates'] ?? 0;
      final errors = (result['errors'] as List? ?? const []).length;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Investment history: $imported new · $duplicates duplicate · $errors rejected')),
      );
    } catch (_) {}
  }
}

typedef _MoneyFormatter = String Function(dynamic value, [String currency]);
typedef _SignedMoneyFormatter = String Function(dynamic value, String currency);
typedef _DateFormatter = String Function(dynamic value);

class _HeroCard extends StatelessWidget {
  const _HeroCard({
    required this.totals,
    required this.kraken,
    required this.portfolioCount,
    required this.positionCount,
    required this.money,
  });

  final Map<String, dynamic> totals;
  final Map<String, dynamic> kraken;
  final int portfolioCount;
  final int positionCount;
  final _MoneyFormatter money;

  @override
  Widget build(BuildContext context) {
    final krakenConnected = kraken['status'] == 'connected';
    final krakenValue = double.tryParse('${kraken['estimated_total_eur'] ?? 0}') ?? 0;
    final revolutEur = double.tryParse('${totals['EUR'] ?? 0}') ?? 0;
    final visibleEur = revolutEur + (krakenConnected ? krakenValue : 0);
    final otherCurrencies = totals.entries.where((entry) => entry.key.toUpperCase() != 'EUR').toList();

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF241449), Color(0xFF0A2450)],
        ),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: const Color(0xFF4D3188)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.trending_up_rounded, color: VaTheme.primaryBright),
              SizedBox(width: 9),
              Text('Investment portfolio', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900)),
            ],
          ),
          const SizedBox(height: 16),
          Text(money(visibleEur), style: const TextStyle(fontSize: 36, fontWeight: FontWeight.w900, letterSpacing: -1.2)),
          const SizedBox(height: 4),
          Text(
            krakenConnected
                ? 'EUR-visible value · Revolut statements + Kraken live estimate'
                : 'Revolut statement value · connect Kraken to include live crypto',
            style: const TextStyle(color: VaTheme.textMuted),
          ),
          if (otherCurrencies.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final entry in otherCurrencies)
                  Chip(label: Text('${entry.key}: ${money(entry.value, entry.key)}')),
              ],
            ),
          ],
          const SizedBox(height: 14),
          Text('$portfolioCount Revolut portfolios · $positionCount current positions${krakenConnected ? ' · ${kraken['asset_count'] ?? 0} Kraken assets' : ''}'),
        ],
      ),
    );
  }
}

class _PortfolioCard extends StatelessWidget {
  const _PortfolioCard({
    required this.portfolio,
    required this.money,
    required this.signedMoney,
    required this.shortDate,
  });

  final Map<String, dynamic> portfolio;
  final _MoneyFormatter money;
  final _SignedMoneyFormatter signedMoney;
  final _DateFormatter shortDate;

  @override
  Widget build(BuildContext context) {
    final values = Map<String, dynamic>.from((portfolio['total_value_by_currency'] as Map?) ?? const {});
    final cash = Map<String, dynamic>.from((portfolio['cash_value_by_currency'] as Map?) ?? const {});
    final realised = Map<String, dynamic>.from((portfolio['realised_pnl_by_currency'] as Map?) ?? const {});
    final income = Map<String, dynamic>.from((portfolio['net_investment_income_by_currency'] as Map?) ?? const {});
    final positions = (portfolio['top_positions'] as List? ?? const []).cast<Map>();
    final kind = '${portfolio['portfolio_kind'] ?? 'brokerage'}';
    final icon = kind == 'robo' ? Icons.auto_awesome_rounded : Icons.candlestick_chart_rounded;

    return Card(
      child: ExpansionTile(
        leading: CircleAvatar(
          backgroundColor: VaTheme.surfaceRaised,
          child: Icon(icon, color: kind == 'robo' ? VaTheme.primaryBright : VaTheme.cyan),
        ),
        title: Text('${portfolio['display_name'] ?? 'Revolut investments'}', style: const TextStyle(fontWeight: FontWeight.w900)),
        subtitle: Text('${portfolio['positions'] ?? 0} positions · ${portfolio['transactions'] ?? 0} transactions'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: Wrap(
              spacing: 12,
              runSpacing: 6,
              children: [
                for (final entry in values.entries) _Metric(label: 'Value ${entry.key}', value: money(entry.value, entry.key)),
                for (final entry in cash.entries) _Metric(label: 'Cash ${entry.key}', value: money(entry.value, entry.key)),
                for (final entry in realised.entries) _Metric(label: 'Realised P&L ${entry.key}', value: signedMoney(entry.value, entry.key)),
                for (final entry in income.entries) _Metric(label: 'Net income ${entry.key}', value: signedMoney(entry.value, entry.key)),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
              'Statement ${shortDate(portfolio['period_start'])} → ${shortDate(portfolio['period_end'])} · '
              'learned contribution ${money(portfolio['learned_monthly_cash_topup'] ?? 0)}/month',
              style: const TextStyle(color: VaTheme.textMuted),
            ),
          ),
          if (positions.isNotEmpty) ...[
            const Divider(height: 26),
            const Align(
              alignment: Alignment.centerLeft,
              child: Text('Largest positions', style: TextStyle(fontWeight: FontWeight.w900)),
            ),
            const SizedBox(height: 6),
            for (final raw in positions.take(10))
              _PositionRow(position: Map<String, dynamic>.from(raw), money: money),
          ],
        ],
      ),
    );
  }
}

class _PositionRow extends StatelessWidget {
  const _PositionRow({required this.position, required this.money});
  final Map<String, dynamic> position;
  final _MoneyFormatter money;

  @override
  Widget build(BuildContext context) {
    final allocation = double.tryParse('${position['allocation_percent'] ?? 0}') ?? 0;
    final symbol = '${position['symbol'] ?? ''}';
    final company = '${position['company'] ?? ''}'.trim();
    final currency = '${position['currency'] ?? 'EUR'}';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(company.isEmpty ? symbol : company, style: const TextStyle(fontWeight: FontWeight.w800)),
                Text(
                  '$symbol · ${position['quantity'] ?? '0'} units${allocation > 0 ? ' · ${allocation.toStringAsFixed(1)}%' : ''}',
                  style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
                ),
              ],
            ),
          ),
          Text(money(position['market_value'], currency), style: const TextStyle(fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }
}

class _KrakenCard extends StatelessWidget {
  const _KrakenCard({
    required this.data,
    required this.fundingRows,
    required this.autopilot,
    required this.money,
    required this.shortDate,
  });

  final Map<String, dynamic> data;
  final List<Map> fundingRows;
  final Map<String, dynamic> autopilot;
  final _MoneyFormatter money;
  final _DateFormatter shortDate;

  @override
  Widget build(BuildContext context) {
    final status = '${data['status'] ?? 'configuration_required'}';
    final assets = (data['assets'] as List? ?? const []).cast<Map>();
    final connected = status == 'connected';
    final latest = fundingRows.isEmpty ? null : fundingRows.first;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.currency_bitcoin_rounded, color: connected ? VaTheme.warning : VaTheme.textMuted),
                const SizedBox(width: 9),
                const Expanded(child: Text('Kraken crypto', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 17))),
                Chip(label: Text(connected ? 'LIVE' : status.replaceAll('_', ' '))),
              ],
            ),
            const SizedBox(height: 10),
            if (!connected)
              Text('${data['detail'] ?? 'Configure Kraken to show live holdings.'}')
            else ...[
              Text(money(data['estimated_total_eur'] ?? 0), style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w900)),
              Text(
                '${data['asset_count'] ?? 0} assets${(data['unvalued_asset_count'] as num? ?? 0) > 0 ? ' · ${data['unvalued_asset_count']} without EUR market valuation' : ''}',
                style: const TextStyle(color: VaTheme.textMuted),
              ),
              const SizedBox(height: 8),
              for (final raw in assets.take(12))
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(
                    children: [
                      Expanded(child: Text('${raw['asset']} · ${raw['quantity']}')),
                      Text(raw['estimated_value_eur'] == null ? '—' : money(raw['estimated_value_eur']), style: const TextStyle(fontWeight: FontWeight.w800)),
                    ],
                  ),
                ),
            ],
            const Divider(height: 24),
            Text('Monthly target: ${money(autopilot['kraken_monthly_target_eur'] ?? 0)}'),
            Text('Automatic funding: ${autopilot['kraken_auto_fund_enabled'] == true ? 'ON' : 'OFF'} · automatic trading: ${autopilot['kraken_auto_trade_enabled'] == true ? 'ON' : 'OFF'}'),
            Text('Trade policy: ${autopilot['kraken_default_pair'] ?? 'XBTEUR'} · max ${money(autopilot['kraken_max_auto_trade_eur'] ?? 0)}'),
            if (latest != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('Latest funding: ${money(latest['amount'], '${latest['currency'] ?? 'EUR'}')} · ${latest['status']} · ${shortDate(latest['created_at'])}'),
              ),
          ],
        ),
      ),
    );
  }
}

class _PerformanceCard extends StatelessWidget {
  const _PerformanceCard({
    required this.realised,
    required this.income,
    required this.tax,
    required this.signedMoney,
    required this.money,
  });

  final Map<String, dynamic> realised;
  final Map<String, dynamic> income;
  final Map<String, dynamic> tax;
  final _SignedMoneyFormatter signedMoney;
  final _MoneyFormatter money;

  @override
  Widget build(BuildContext context) {
    final currencies = <String>{...realised.keys, ...income.keys, ...tax.keys}.toList()..sort();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionTitle(
          title: 'Performance & income',
          subtitle: 'Statement-derived realised P&L, dividends/income and withholding tax.',
        ),
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: currencies.isEmpty
                ? const Text('Import Revolut P&L statements to populate realised performance and investment income.')
                : Column(
                    children: [
                      for (final currency in currencies)
                        Padding(
                          padding: const EdgeInsets.symmetric(vertical: 6),
                          child: Row(
                            children: [
                              Expanded(child: _Metric(label: 'Realised P&L · $currency', value: signedMoney(realised[currency] ?? 0, currency))),
                              Expanded(child: _Metric(label: 'Net income', value: signedMoney(income[currency] ?? 0, currency))),
                              Expanded(child: _Metric(label: 'Tax withheld', value: money(tax[currency] ?? 0, currency))),
                            ],
                          ),
                        ),
                    ],
                  ),
          ),
        ),
      ],
    );
  }
}

class _ContributionCard extends StatelessWidget {
  const _ContributionCard({
    required this.portfolios,
    required this.learnedByScope,
    required this.autopilot,
    required this.krakenFunding,
    required this.money,
    required this.shortDate,
  });

  final List<Map> portfolios;
  final Map<String, dynamic> learnedByScope;
  final Map<String, dynamic> autopilot;
  final List<Map> krakenFunding;
  final _MoneyFormatter money;
  final _DateFormatter shortDate;

  @override
  Widget build(BuildContext context) {
    final personal = learnedByScope['personal'] ?? '0';
    final pro = learnedByScope['pro'] ?? '0';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionTitle(
          title: 'Contributions & Investment Autopilot',
          subtitle: 'Investment funding is kept separate from lifestyle spending.',
        ),
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Learned Revolut funding · Personal ${money(personal)}/month${double.tryParse('$pro') != null && (double.tryParse('$pro') ?? 0) > 0 ? ' · Pro ${money(pro)}' : ''}'),
                const SizedBox(height: 8),
                for (final raw in portfolios)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 7),
                    child: Text(
                      '${raw['display_name']} · ${money(raw['learned_monthly_cash_topup'] ?? 0)}/month · '
                      'last observed ${shortDate(raw['last_cash_topup_at'])} · Revolut-managed execution',
                    ),
                  ),
                const Divider(height: 22),
                Text('Kraken target · ${money(autopilot['kraken_monthly_target_eur'] ?? 0)}/month'),
                const Text('Kraken funding is restricted to Personal EUR payment-enabled bank accounts; Pro/business sources are blocked.'),
                if (krakenFunding.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text('Most recent Kraken funding state: ${krakenFunding.first['status']}'),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle({required this.title, required this.subtitle});
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
          const SizedBox(height: 3),
          Text(subtitle, style: const TextStyle(color: VaTheme.textMuted)),
        ],
      );
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, style: const TextStyle(color: VaTheme.textMuted, fontSize: 11)),
          Text(value, style: const TextStyle(fontWeight: FontWeight.w900)),
        ],
      );
}

class _EmptyCard extends StatelessWidget {
  const _EmptyCard({required this.icon, required this.title, required this.detail});
  final IconData icon;
  final String title;
  final String detail;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: VaTheme.textMuted),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontWeight: FontWeight.w900)),
                    const SizedBox(height: 4),
                    Text(detail, style: const TextStyle(color: VaTheme.textMuted)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}
