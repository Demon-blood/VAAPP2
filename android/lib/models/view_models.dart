class DashboardData {
  DashboardData.fromJson(Map<String, dynamic> json)
      : openTasks = (json['open_tasks'] as num?)?.toInt() ?? 0,
        actionEmails = (json['action_emails'] as num?)?.toInt() ?? 0,
        unpaidBills = (json['unpaid_bills'] as num?)?.toInt() ?? 0,
        paymentActions = (json['payments_requiring_action'] as num?)?.toInt() ?? 0,
        connectedServices = Map<String, dynamic>.from(
          json['connected_services'] as Map? ?? const {},
        );

  final int openTasks;
  final int actionEmails;
  final int unpaidBills;
  final int paymentActions;
  final Map<String, dynamic> connectedServices;
}

String money(dynamic value, [String currency = 'EUR']) {
  final number = value is num ? value.toDouble() : double.tryParse('$value') ?? 0;
  return '$currency ${number.toStringAsFixed(2)}';
}
