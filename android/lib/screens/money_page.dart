import 'package:flutter/material.dart';

import 'accounts_page.dart';
import 'bills_page.dart';
import 'payments_page.dart';
import 'receipts_page.dart';

class MoneyPage extends StatelessWidget {
  const MoneyPage({this.initialIndex = 0, super.key});

  final int initialIndex;

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 4,
        initialIndex: initialIndex < 0 ? 0 : (initialIndex > 3 ? 3 : initialIndex),
        child: const Column(
          children: [
            TabBar(tabs: [Tab(text: 'Bills'), Tab(text: 'Payments'), Tab(text: 'Accounts'), Tab(text: 'Receipts')]),
            Expanded(child: TabBarView(children: [BillsPage(), PaymentsPage(), AccountsPage(), ReceiptsPage()])),
          ],
        ),
      );
}
