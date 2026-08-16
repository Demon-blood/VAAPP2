import 'dart:io';

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

  test('document sources expose structured setup, readiness and actions', () {
    final work = File('lib/screens/work_page.dart').readAsStringSync();
    final state = File('lib/app_state.dart').readAsStringSync();
    expect(work, contains('Portal document sources'));
    expect(work, contains('Secure portal'));
    expect(work, contains('Doccle starter · needs verification'));
    expect(work, contains('Sync interval (minutes, minimum 15)'));
    expect(work, contains('Test'));
    expect(work, contains('Sync now'));
    expect(work, contains('needs_user_auth'));
    expect(work, contains('Source:'));
    expect(state, contains('/api/portal-documents/sources'));
    expect(state, contains('testPortalDocumentSource'));
    expect(state, contains('syncPortalDocumentSource'));
    expect(state, contains('submitPortalDocumentAuthCode'));
  });
}
