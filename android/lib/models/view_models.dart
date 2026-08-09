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

double numericValue(dynamic value) {
  if (value is num) return value.toDouble();
  final text = '${value ?? ''}'.trim().replaceAll('\u00A0', '').replaceAll(' ', '');
  if (text.isEmpty) return 0;
  // API decimals may be JSON strings. Support both 1234.56 and common 1.234,56 / 1,234.56 forms.
  var normalized = text;
  if (normalized.contains(',') && normalized.contains('.')) {
    final comma = normalized.lastIndexOf(',');
    final dot = normalized.lastIndexOf('.');
    if (comma > dot) {
      normalized = normalized.replaceAll('.', '').replaceAll(',', '.');
    } else {
      normalized = normalized.replaceAll(',', '');
    }
  } else if (normalized.contains(',')) {
    normalized = normalized.replaceAll(',', '.');
  }
  return double.tryParse(normalized) ?? 0;
}

String money(dynamic value, [String currency = 'EUR']) {
  final number = numericValue(value);
  return '$currency ${number.toStringAsFixed(2)}';
}
