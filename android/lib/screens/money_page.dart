import 'package:flutter/material.dart';

import 'accounts_page.dart';
import 'bills_page.dart';
import 'payments_page.dart';

class MoneyPage extends StatelessWidget {
  const MoneyPage({this.initialIndex = 0, super.key});

  final int initialIndex;

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 3,
        initialIndex: initialIndex < 0 ? 0 : (initialIndex > 2 ? 2 : initialIndex),
        child: const Column(
          children: [
            TabBar(tabs: [Tab(text: 'Bills'), Tab(text: 'Payments'), Tab(text: 'Accounts')]),
            Expanded(child: TabBarView(children: [BillsPage(), PaymentsPage(), AccountsPage()])),
          ],
        ),
      );
}
