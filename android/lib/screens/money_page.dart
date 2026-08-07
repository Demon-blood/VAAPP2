import 'package:flutter/material.dart';

import 'accounts_page.dart';
import 'bills_page.dart';
import 'payments_page.dart';

class MoneyPage extends StatelessWidget {
  const MoneyPage({super.key});

  @override
  Widget build(BuildContext context) => const DefaultTabController(
        length: 3,
        child: Column(
          children: [
            TabBar(tabs: [Tab(text: 'Bills'), Tab(text: 'Payments'), Tab(text: 'Accounts')]),
            Expanded(child: TabBarView(children: [BillsPage(), PaymentsPage(), AccountsPage()])),
          ],
        ),
      );
}
