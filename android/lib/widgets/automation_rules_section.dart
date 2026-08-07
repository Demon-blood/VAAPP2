import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';

class AutomationRulesSection extends StatelessWidget {
  const AutomationRulesSection({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(child: Text('Automation rules', style: Theme.of(context).textTheme.headlineSmall)),
            FilledButton.icon(
              onPressed: state.busy ? null : () => _addRule(context),
              icon: const Icon(Icons.add_task),
              label: const Text('Add rule'),
            ),
          ],
        ),
        const SizedBox(height: 8),
        const Text('Rules run on the backend even when the phone app is closed.'),
        const SizedBox(height: 12),
        if (state.automationRules.isEmpty)
          const Card(child: ListTile(title: Text('No automation rules configured.')))
        else
          for (final rule in state.automationRules)
            GestureDetector(
              onLongPress: () => _deleteRule(context, rule),
              child: Card(
                child: SwitchListTile(
                  value: rule['enabled'] == true,
                  onChanged: (value) => state.setAutomationRuleEnabled(rule['id'] as int, value),
                  secondary: Icon(rule['rule_type'] == 'auto_reply' ? Icons.reply_all : Icons.schedule_send),
                  title: Text('${rule['name']}'),
                  subtitle: Text([
                    '${rule['rule_type']}',
                    if ('${rule['last_run_at'] ?? ''}'.isNotEmpty) 'Last run: ${rule['last_run_at']}',
                    if ('${rule['last_result'] ?? ''}'.isNotEmpty) 'Result: ${rule['last_result']}',
                  ].join('\n')),
                  isThreeLine: '${rule['last_result'] ?? ''}'.isNotEmpty,
                  controlAffinity: ListTileControlAffinity.trailing,
                ),
              ),
            ),
        if (state.automationRules.isNotEmpty)
          const Text('Long-press a rule to delete it.'),
      ],
    );
  }

  Future<void> _deleteRule(BuildContext context, Map<String, dynamic> rule) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Delete automation rule?'),
        content: Text('${rule['name']} will stop running.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Delete')),
        ],
      ),
    );
    if (confirmed == true && context.mounted) {
      await context.read<AppState>().deleteAutomationRule(rule['id'] as int);
    }
  }

  Future<void> _addRule(BuildContext context) async {
    final type = await showDialog<String>(
      context: context,
      builder: (dialogContext) => SimpleDialog(
        title: const Text('Choose automation'),
        children: [
          SimpleDialogOption(
            onPressed: () => Navigator.pop(dialogContext, 'connector_schedule'),
            child: const ListTile(
              leading: Icon(Icons.schedule_send),
              title: Text('Scheduled service action'),
              subtitle: Text('Run a connected service automatically at an interval.'),
            ),
          ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(dialogContext, 'auto_reply'),
            child: const ListTile(
              leading: Icon(Icons.reply_all),
              title: Text('Automatic email reply'),
              subtitle: Text('Authorize AI-drafted replies for a known sender or category.'),
            ),
          ),
        ],
      ),
    );
    if (type == null || !context.mounted) return;
    if (type == 'connector_schedule') {
      await _addConnectorRule(context);
    } else {
      await _addAutoReplyRule(context);
    }
  }

  Future<void> _addAutoReplyRule(BuildContext context) async {
    final name = TextEditingController();
    final sender = TextEditingController();
    final category = TextEditingController();
    var sendAutomatically = true;
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Automatic email reply'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(controller: name, decoration: const InputDecoration(labelText: 'Rule name')),
                const SizedBox(height: 12),
                TextField(
                  controller: sender,
                  decoration: const InputDecoration(
                    labelText: 'Sender contains',
                    helperText: 'Example: @trusted-company.be. Leave empty only when a category is supplied.',
                  ),
                ),
                const SizedBox(height: 12),
                TextField(controller: category, decoration: const InputDecoration(labelText: 'Exact VA category (optional)')),
                const SizedBox(height: 8),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Send without approval'),
                  subtitle: const Text('The VA still uses the analyzed reply content and keeps an audit record.'),
                  value: sendAutomatically,
                  onChanged: (value) => setState(() => sendAutomatically = value),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Create rule')),
          ],
        ),
      ),
    );
    if (save != true || !context.mounted) return;
    if (name.text.trim().isEmpty || (sender.text.trim().isEmpty && category.text.trim().isEmpty)) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Enter a rule name and at least one sender or category condition.')),
      );
      return;
    }
    await context.read<AppState>().createAutomationRule(
          ruleType: 'auto_reply',
          name: name.text.trim(),
          conditions: {
            if (sender.text.trim().isNotEmpty) 'sender_contains': sender.text.trim(),
            if (category.text.trim().isNotEmpty) 'category': category.text.trim(),
          },
          actions: {'send': sendAutomatically},
        );
  }

  Future<void> _addConnectorRule(BuildContext context) async {
    final state = context.read<AppState>();
    final connectors = state.connectors.where((item) => (item['operations'] as List? ?? const []).isNotEmpty).toList();
    if (connectors.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Connect a service with executable actions first.')),
      );
      return;
    }
    Map<String, dynamic> connector = connectors.first;
    Map<String, dynamic> operation = Map<String, dynamic>.from((connector['operations'] as List).first as Map);
    Map<String, TextEditingController> parameterControllers = _controllers(operation);
    final name = TextEditingController(text: 'Run ${connector['display_name']}');
    final interval = TextEditingController(text: '60');

    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Scheduled service action'),
          content: SizedBox(
            width: 560,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(controller: name, decoration: const InputDecoration(labelText: 'Rule name')),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: connector['slug'] as String,
                    decoration: const InputDecoration(labelText: 'Connected service'),
                    items: connectors
                        .map((item) => DropdownMenuItem<String>(value: item['slug'] as String, child: Text('${item['display_name']}')))
                        .toList(),
                    onChanged: (value) {
                      if (value == null) return;
                      for (final controller in parameterControllers.values) {
                        controller.dispose();
                      }
                      setState(() {
                        connector = connectors.firstWhere((item) => item['slug'] == value);
                        operation = Map<String, dynamic>.from((connector['operations'] as List).first as Map);
                        parameterControllers = _controllers(operation);
                      });
                    },
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    key: ValueKey('${connector['slug']}:${operation['key']}'),
                    initialValue: operation['key'] as String,
                    decoration: const InputDecoration(labelText: 'Action'),
                    items: (connector['operations'] as List)
                        .map((raw) => Map<String, dynamic>.from(raw as Map))
                        .map((item) => DropdownMenuItem<String>(value: item['key'] as String, child: Text('${item['label']}')))
                        .toList(),
                    onChanged: (value) {
                      if (value == null) return;
                      for (final controller in parameterControllers.values) {
                        controller.dispose();
                      }
                      setState(() {
                        operation = Map<String, dynamic>.from(
                          (connector['operations'] as List).firstWhere((raw) => (raw as Map)['key'] == value) as Map,
                        );
                        parameterControllers = _controllers(operation);
                      });
                    },
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: interval,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'Repeat every (minutes)'),
                  ),
                  const SizedBox(height: 12),
                  for (final raw in (operation['fields'] as List? ?? const [])) ...[
                    _RuleField(
                      field: Map<String, dynamic>.from(raw as Map),
                      controller: parameterControllers[raw['key']]!,
                    ),
                    const SizedBox(height: 12),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Create rule')),
          ],
        ),
      ),
    );
    if (save != true || !context.mounted) {
      for (final controller in parameterControllers.values) {
        controller.dispose();
      }
      return;
    }
    try {
      final minutes = int.tryParse(interval.text.trim());
      if (name.text.trim().isEmpty || minutes == null || minutes < 1) {
        throw const FormatException('Enter a rule name and a valid interval.');
      }
      final parameters = <String, dynamic>{};
      for (final raw in (operation['fields'] as List? ?? const [])) {
        final field = Map<String, dynamic>.from(raw as Map);
        final key = field['key'] as String;
        final value = parameterControllers[key]!.text.trim();
        if (field['required'] == true && value.isEmpty) throw FormatException('${field['label']} is required.');
        if (value.isEmpty) continue;
        if (field['type'] == 'json') {
          parameters[key] = jsonDecode(value);
        } else if (field['type'] == 'number') {
          final number = num.tryParse(value.replaceAll(',', '.'));
          if (number == null) throw FormatException('${field['label']} must be a number.');
          parameters[key] = number;
        } else {
          parameters[key] = value;
        }
      }
      await context.read<AppState>().createAutomationRule(
            ruleType: 'connector_schedule',
            name: name.text.trim(),
            conditions: {'interval_minutes': minutes},
            actions: {
              'connector_slug': connector['slug'],
              'operation': operation['key'],
              'parameters': parameters,
            },
          );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    } finally {
      for (final controller in parameterControllers.values) {
        controller.dispose();
      }
    }
  }

  Map<String, TextEditingController> _controllers(Map<String, dynamic> operation) {
    final result = <String, TextEditingController>{};
    for (final raw in (operation['fields'] as List? ?? const [])) {
      final field = Map<String, dynamic>.from(raw as Map);
      result[field['key'] as String] = TextEditingController(text: '${field['default'] ?? ''}');
    }
    return result;
  }
}

class _RuleField extends StatelessWidget {
  const _RuleField({required this.field, required this.controller});

  final Map<String, dynamic> field;
  final TextEditingController controller;

  @override
  Widget build(BuildContext context) {
    final type = '${field['type'] ?? 'text'}';
    final label = '${field['label'] ?? field['key']}';
    if (type == 'choice') {
      final choices = (field['choices'] as List? ?? const []).map((value) => '$value').toList();
      final current = choices.contains(controller.text) ? controller.text : null;
      return DropdownButtonFormField<String>(
        initialValue: current,
        decoration: InputDecoration(labelText: label),
        items: choices.map((value) => DropdownMenuItem(value: value, child: Text(value))).toList(),
        onChanged: (value) => controller.text = value ?? '',
      );
    }
    return TextField(
      controller: controller,
      maxLines: type == 'json' || type.contains('multiline') ? 7 : 1,
      keyboardType: type == 'number' ? TextInputType.number : type == 'url' ? TextInputType.url : TextInputType.text,
      decoration: InputDecoration(labelText: label),
    );
  }
}
