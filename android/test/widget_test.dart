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

  test('money helpers accept API Decimal values encoded as strings', () {
    expect(numericValue('89.99'), closeTo(89.99, 0.0001));
    expect(numericValue('1.248,75'), closeTo(1248.75, 0.0001));
    expect(numericValue(54.5), closeTo(54.5, 0.0001));
    expect(money('89.99'), 'EUR 89.99');
  });
}
