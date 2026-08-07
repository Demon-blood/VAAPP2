import 'dart:convert';

import 'package:flutter/services.dart';

class LocalConnectorCatalog {
  static Future<({List<Map<String, dynamic>> templates, List<Map<String, dynamic>> presets})>
      load() async {
    final raw = await rootBundle.loadString('assets/connector_catalog.json');
    final decoded = Map<String, dynamic>.from(jsonDecode(raw) as Map);
    return (
      templates: _list(decoded['templates']),
      presets: _list(decoded['presets']),
    );
  }

  static List<Map<String, dynamic>> _list(dynamic value) =>
      (value as List? ?? const [])
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList(growable: false);
}
