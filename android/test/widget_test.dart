import 'package:flutter_test/flutter_test.dart';
import 'package:full_time_va/models/view_models.dart';

void main() {
  test('dashboard parses live API response', () {
    final data = DashboardData.fromJson({
      'open_tasks': 2,
      'action_emails': 3,
      'unpaid_bills': 4,
      'payments_requiring_action': 1,
      'connected_services': {'google': true, 'ai': true, 'banking': false},
    });
    expect(data.openTasks, 2);
    expect(data.paymentActions, 1);
    expect(data.connectedServices['google'], true);
  });
}
