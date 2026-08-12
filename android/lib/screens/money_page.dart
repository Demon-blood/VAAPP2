import 'package:flutter/material.dart';

import 'accounts_page.dart';
import 'bills_page.dart';
import 'finance_autopilot_page.dart';
import 'investments_page.dart';
import 'payments_page.dart';
import 'receipts_page.dart';

class MoneyPage extends StatelessWidget {
  const MoneyPage({this.initialIndex = 0, super.key});

  final int initialIndex;

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 6,
        initialIndex: initialIndex < 0 ? 0 : (initialIndex > 5 ? 5 : initialIndex),
        child: const Column(
          children: [
            TabBar(isScrollable: true, tabs: [Tab(text: 'Bills'), Tab(text: 'Payments'), Tab(text: 'Accounts'), Tab(text: 'Budget'), Tab(text: 'Investments'), Tab(text: 'Receipts')]),
            Expanded(child: TabBarView(children: [BillsPage(), PaymentsPage(), AccountsPage(), FinanceAutopilotPage(), InvestmentsPage(), ReceiptsPage()])),
          ],
        ),
      );
}
