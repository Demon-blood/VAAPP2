import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import 'communications_page.dart';
import '../widgets/automation_rules_section.dart';

class ServicesPage extends StatelessWidget {
  const ServicesPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: ListTile(
            leading: const Icon(Icons.forum_outlined),
            title: const Text('Communications Autopilot', style: TextStyle(fontWeight: FontWeight.w900)),
            subtitle: const Text('SMS, WhatsApp, Signal, Telegram, Messenger and incoming call screening'),
            trailing: const Icon(Icons.chevron_right_rounded),
            onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const CommunicationsPage())),
          ),
        ),
        const SizedBox(height: 12),
        Text('Built-in services', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 8),
        const Text('Configure, connect, test and disconnect every live service from this phone.'),
        const SizedBox(height: 12),
        for (final section in state.setupSections)
          _BuiltInServiceCard(section: section),
        const Divider(height: 32),
        Row(
          children: [
            Expanded(child: Text('Service catalog', style: Theme.of(context).textTheme.headlineSmall)),
            FilledButton.icon(
              onPressed: state.busy || state.connectorPresets.isEmpty ? null : () => _addPreset(context),
              icon: const Icon(Icons.apps),
              label: const Text('Choose service'),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          '${state.connectorPresets.length} preconfigured service integrations are available. Provider credentials and approval are still obtained from the official provider.',
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            Expanded(child: Text('Universal connectors', style: Theme.of(context).textTheme.headlineSmall)),
            OutlinedButton.icon(
              onPressed: state.busy ? null : () => _addConnector(context),
              icon: const Icon(Icons.add_link),
              label: const Text('Add custom'),
            ),
          ],
        ),
        const SizedBox(height: 8),
        const Text(
          'Use OAuth 2.0, client credentials, REST/XML, webhook, IMAP/SMTP, WebDAV, SFTP, Browserless, or RSS when no dedicated preset fits.',
        ),
        const SizedBox(height: 12),
        if (state.connectors.isEmpty)
          const Card(child: ListTile(title: Text('No custom services connected.')))
        else
          for (final connector in state.connectors)
            _ConnectorCard(connector: connector),
        const Divider(height: 32),
        const AutomationRulesSection(),
      ],
    );
  }

  static Future<void> _addPreset(BuildContext context) async {
    final state = context.read<AppState>();
    if (state.connectorPresets.isEmpty) return;
    Map<String, dynamic> selected = state.connectorPresets.first;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Choose a service'),
          content: SizedBox(
            width: 560,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: selected['id'] as String,
                  decoration: const InputDecoration(labelText: 'Service'),
                  isExpanded: true,
                  items: state.connectorPresets
                      .map(
                        (item) => DropdownMenuItem<String>(
                          value: item['id'] as String,
                          child: Text('${item['title']} · ${item['category']}', overflow: TextOverflow.ellipsis),
                        ),
                      )
                      .toList(),
                  onChanged: (value) {
                    if (value == null) return;
                    setState(() => selected = state.connectorPresets.firstWhere((item) => item['id'] == value));
                  },
                ),
                const SizedBox(height: 12),
                Text('${selected['description']}'),
                const SizedBox(height: 8),
                Text('Connection method: ${selected['connector_type']}'),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () => launchUrl(Uri.parse(selected['setup_url'] as String), mode: LaunchMode.externalApplication),
                  icon: const Icon(Icons.open_in_browser),
                  label: const Text('Open official provider setup'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Configure service')),
          ],
        ),
      ),
    );
    if (accepted != true || !context.mounted) return;
    final connectorType = selected['connector_type'] as String;
    final template = state.connectorTemplates.firstWhere((item) => item['type'] == connectorType);
    await _configureConnector(
      context,
      connector: {
        'slug': selected['id'],
        'display_name': selected['title'],
        'connector_type': connectorType,
        'category': selected['category'],
        'fields': template['fields'],
        'configured_fields': const <String>[],
        'current_values': Map<String, dynamic>.from((selected['defaults'] as Map?) ?? const <String, dynamic>{}),
      },
    );
  }

  static Future<void> _addConnector(BuildContext context) async {
    final state = context.read<AppState>();
    if (state.connectorTemplates.isEmpty) return;
    Map<String, dynamic> selected = state.connectorTemplates.first;
    final nameController = TextEditingController();
    final slugController = TextEditingController();
    final created = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Add service connector'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: selected['type'] as String,
                  decoration: const InputDecoration(labelText: 'Connector type'),
                  items: state.connectorTemplates
                      .map(
                        (item) => DropdownMenuItem<String>(
                          value: item['type'] as String,
                          child: Text(item['title'] as String),
                        ),
                      )
                      .toList(),
                  onChanged: (value) {
                    if (value == null) return;
                    setState(() => selected = state.connectorTemplates.firstWhere((item) => item['type'] == value));
                  },
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: nameController,
                  decoration: const InputDecoration(labelText: 'Service name'),
                  onChanged: (value) {
                    if (slugController.text.isEmpty) {
                      slugController.text = _slugify(value);
                    }
                  },
                ),
                const SizedBox(height: 12),
                TextField(controller: slugController, decoration: const InputDecoration(labelText: 'Service ID')),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Continue')),
          ],
        ),
      ),
    );
    if (created != true || !context.mounted) return;
    final name = nameController.text.trim();
    final slug = _slugify(slugController.text);
    if (name.isEmpty || slug.isEmpty) return;
    await _configureConnector(
      context,
      connector: {
        'slug': slug,
        'display_name': name,
        'connector_type': selected['type'],
        'category': selected['category'],
        'fields': selected['fields'],
        'configured_fields': const <String>[],
      },
    );
  }

  static String _slugify(String value) => value
      .trim()
      .toLowerCase()
      .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
      .replaceAll(RegExp(r'^-+|-+$'), '');

  static Future<void> _configureConnector(
    BuildContext context, {
    required Map<String, dynamic> connector,
  }) async {
    final fields = (connector['fields'] as List? ?? const []).cast<Map>();
    final configured = Set<String>.from((connector['configured_fields'] as List? ?? const []).map((value) => '$value'));
    final currentValues = Map<String, dynamic>.from((connector['current_values'] as Map?) ?? const <String, dynamic>{});
    final controllers = <String, TextEditingController>{};
    for (final raw in fields) {
      final field = Map<String, dynamic>.from(raw);
      controllers[field['key'] as String] = TextEditingController(text: '${currentValues[field['key']] ?? field['default'] ?? ''}');
    }
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Configure ${connector['display_name']}'),
        content: SizedBox(
          width: 560,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final raw in fields) ...[
                  _DynamicField(
                    field: Map<String, dynamic>.from(raw),
                    controller: controllers[raw['key']]!,
                    alreadyConfigured: configured.contains(raw['key']),
                  ),
                  const SizedBox(height: 12),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
        ],
      ),
    );
    if (save != true || !context.mounted) return;
    final config = <String, dynamic>{};
    for (final entry in controllers.entries) {
      final value = entry.value.text.trim();
      if (value.isNotEmpty) config[entry.key] = value;
    }
    try {
      await context.read<AppState>().configureConnector(
            slug: connector['slug'] as String,
            displayName: connector['display_name'] as String,
            connectorType: connector['connector_type'] as String,
            category: connector['category'] as String?,
            config: config,
          );
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Connector configuration saved.')));
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}

class _BuiltInServiceCard extends StatelessWidget {
  const _BuiltInServiceCard({required this.section});

  final Map<String, dynamic> section;

  @override
  Widget build(BuildContext context) {
    final configured = section['configured'] == true;
    final slug = section['slug'] as String;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(configured ? Icons.check_circle : Icons.settings_outlined),
              title: Text(section['title'] as String),
              subtitle: Text(section['description'] as String),
              trailing: Text(configured ? 'Configured' : 'Setup required'),
            ),
            if (slug == 'ai') _AiUsageSummary(usage: context.watch<AppState>().aiUsage),
            if ('${section['callback_url'] ?? ''}'.isNotEmpty)
              Row(
                children: [
                  Expanded(child: SelectableText('Callback: ${section['callback_url']}')),
                  IconButton(
                    tooltip: 'Copy callback URL',
                    onPressed: () => _copyText(context, '${section['callback_url']}', 'Callback URL copied.'),
                    icon: const Icon(Icons.copy),
                  ),
                ],
              ),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if ('${section['setup_url'] ?? ''}'.isNotEmpty)
                  OutlinedButton.icon(
                    onPressed: () => launchUrl(
                      Uri.parse('${section['setup_url']}'),
                      mode: LaunchMode.externalApplication,
                    ),
                    icon: const Icon(Icons.open_in_browser),
                    label: const Text('Provider setup'),
                  ),
                FilledButton.tonalIcon(
                  onPressed: () => _configure(context),
                  icon: const Icon(Icons.tune),
                  label: const Text('Configure'),
                ),
                OutlinedButton.icon(
                  onPressed: configured ? () => _test(context) : null,
                  icon: const Icon(Icons.network_check),
                  label: const Text('Test'),
                ),
                if (slug == 'google')
                  OutlinedButton.icon(
                    onPressed: configured ? () => _connectGoogle(context) : null,
                    icon: const Icon(Icons.login),
                    label: const Text('Connect account'),
                  ),
                if (slug == 'enable_banking') ...[
                  OutlinedButton.icon(
                    onPressed: () => _generateBankCertificate(context),
                    icon: const Icon(Icons.vpn_key_outlined),
                    label: const Text('Generate key + certificate'),
                  ),
                  OutlinedButton.icon(
                    onPressed: configured ? () => _connectBank(context, 'Beobank') : null,
                    icon: const Icon(Icons.account_balance),
                    label: const Text('Connect Beobank'),
                  ),
                  OutlinedButton.icon(
                    onPressed: configured ? () => _connectBank(context, 'Revolut') : null,
                    icon: const Icon(Icons.account_balance_wallet_outlined),
                    label: const Text('Connect Revolut Personal / Pro'),
                  ),
                ],
                TextButton.icon(
                  onPressed: configured ? () => _disconnect(context) : null,
                  icon: const Icon(Icons.link_off),
                  label: const Text('Disconnect'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _copyText(BuildContext context, String value, String message) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
    }
  }

  Future<void> _configure(BuildContext context) async {
    final fields = (section['fields'] as List).cast<Map>();
    final configuredFields = Set<String>.from((section['configured_fields'] as List? ?? const []).map((value) => '$value'));
    final currentValues = Map<String, dynamic>.from((section['current_values'] as Map?) ?? const <String, dynamic>{});
    final controllers = <String, TextEditingController>{};
    for (final raw in fields) {
      controllers[raw['key'] as String] = TextEditingController(text: '${currentValues[raw['key']] ?? raw['default'] ?? ''}');
    }
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Configure ${section['title']}'),
        content: SizedBox(
          width: 560,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final raw in fields) ...[
                  _DynamicField(
                    field: Map<String, dynamic>.from(raw),
                    controller: controllers[raw['key']]!,
                    alreadyConfigured: configuredFields.contains(raw['key']),
                  ),
                  const SizedBox(height: 12),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
        ],
      ),
    );
    if (save != true || !context.mounted) return;
    final values = <String, dynamic>{};
    for (final entry in controllers.entries) {
      if (entry.value.text.trim().isNotEmpty) values[entry.key] = entry.value.text.trim();
    }
    try {
      await context.read<AppState>().configureSetupSection(section['slug'] as String, values);
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Configuration saved.')));
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _test(BuildContext context) async {
    try {
      final result = await context.read<AppState>().testSetupSection(section['slug'] as String);
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Live test passed: $result')));
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _connectGoogle(BuildContext context) async {
    final url = await context.read<AppState>().startGoogleConnection();
    await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
  }

  Future<void> _generateBankCertificate(BuildContext context) async {
    final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('Generate a new Open Banking key?'),
            content: const Text(
              'The backend will generate a new 4096-bit private key, store it encrypted, and return only the public certificate. Replacing an existing key requires the new certificate to be uploaded in Enable Banking.',
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Generate')),
            ],
          ),
        ) ??
        false;
    if (!confirmed || !context.mounted) return;
    try {
      final result = await context.read<AppState>().generateEnableBankingCertificate();
      if (!context.mounted) return;
      final certificate = result['certificate_pem']?.toString() ?? '';
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Enable Banking public certificate'),
          content: SizedBox(
            width: 600,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('SHA-256: ${result['sha256_fingerprint'] ?? ''}'),
                  Text('Valid until: ${result['valid_until'] ?? ''}'),
                  const SizedBox(height: 12),
                  SelectableText(certificate),
                ],
              ),
            ),
          ),
          actions: [
            TextButton.icon(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: certificate));
                if (dialogContext.mounted) {
                  ScaffoldMessenger.of(dialogContext).showSnackBar(
                    const SnackBar(content: Text('Public certificate copied.')),
                  );
                }
              },
              icon: const Icon(Icons.copy),
              label: const Text('Copy certificate'),
            ),
            FilledButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Done')),
          ],
        ),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }

  Future<void> _connectBank(BuildContext context, String name) async {
    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(SnackBar(content: Text('Starting $name authorization…')));
    try {
      final url = await context.read<AppState>().startBankConnection(institutionName: name);
      final uri = Uri.tryParse(url);
      if (uri == null || !uri.hasScheme || !(uri.scheme == 'https' || uri.scheme == 'http')) {
        throw StateError('The banking provider returned an invalid authorization URL: $url');
      }
      final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!opened) {
        throw StateError('Android could not open the banking authorization page.');
      }
      if (context.mounted) {
        messenger.hideCurrentSnackBar();
      }
    } catch (error) {
      if (context.mounted) {
        messenger.hideCurrentSnackBar();
        messenger.showSnackBar(
          SnackBar(
            content: Text('Could not start $name connection: $error'),
            duration: const Duration(seconds: 12),
          ),
        );
      }
    }
  }

  Future<void> _disconnect(BuildContext context) async {
    await context.read<AppState>().disconnectSetupSection(section['slug'] as String);
  }
}

class _AiUsageSummary extends StatelessWidget {
  const _AiUsageSummary({required this.usage});

  final Map<String, dynamic> usage;

  @override
  Widget build(BuildContext context) {
    if (usage.isEmpty) return const SizedBox.shrink();
    final requests = usage['requests'] ?? 0;
    final requestBudget = usage['request_budget'] ?? 0;
    final tokens = usage['total_tokens'] ?? 0;
    final tokenBudget = usage['token_budget'] ?? 0;
    final shortcuts = usage['rule_shortcuts'] ?? 0;
    final fingerprints = usage['fingerprint_hits'] ?? 0;
    final deferred = usage['deferred_count'] ?? 0;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        'Today: $requests / $requestBudget AI requests · $tokens / $tokenBudget tokens\n'
        'Saved AI calls: $shortcuts rule shortcuts + $fingerprints fingerprint hits · Deferred: $deferred',
      ),
    );
  }
}


class _ConnectorCard extends StatelessWidget {
  const _ConnectorCard({required this.connector});

  final Map<String, dynamic> connector;

  @override
  Widget build(BuildContext context) {
    final live = connector['status'] == 'live';
    final oauth = connector['connector_type'] == 'oauth2';
    return Card(
      child: ListTile(
        leading: Icon(live ? Icons.check_circle : Icons.extension_outlined),
        title: Text(connector['display_name'] as String),
        subtitle: Text('${connector['connector_type']} · ${connector['status']}${connector['last_error'] == '' ? '' : '\n${connector['last_error']}'}'),
        isThreeLine: connector['last_error'] != '',
        trailing: PopupMenuButton<String>(
          onSelected: (action) async {
            if (action == 'configure') {
              await ServicesPage._configureConnector(context, connector: connector);
            } else if (action == 'test') {
              try {
                final result = await context.read<AppState>().testConnector(connector['slug'] as String);
                if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Live test passed: $result')));
              } catch (error) {
                if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
              }
            } else if (action == 'run') {
              await _runOperation(context);
            } else if (action == 'oauth') {
              final url = await context.read<AppState>().startConnectorOauth(connector['slug'] as String);
              await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
            } else if (action == 'callback') {
              final callback = connector['oauth_callback_url']?.toString() ?? '';
              if (callback.isNotEmpty) {
                await Clipboard.setData(ClipboardData(text: callback));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('OAuth callback URL copied.')),
                  );
                }
              }
            } else if (action == 'delete') {
              await context.read<AppState>().deleteConnector(connector['slug'] as String);
            }
          },
          itemBuilder: (_) => [
            const PopupMenuItem(value: 'configure', child: Text('Configure')),
            const PopupMenuItem(value: 'test', child: Text('Test live connection')),
            const PopupMenuItem(value: 'run', child: Text('Run operation')),
            if (oauth) const PopupMenuItem(value: 'callback', child: Text('Copy OAuth callback URL')),
            if (oauth) const PopupMenuItem(value: 'oauth', child: Text('Authorize service')),
            const PopupMenuItem(value: 'delete', child: Text('Remove')),
          ],
        ),
      ),
    );
  }

  Future<void> _runOperation(BuildContext context) async {
    final operations = (connector['operations'] as List? ?? const [])
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
    if (operations.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('This connector has no executable operations.')),
      );
      return;
    }

    Map<String, dynamic> selected = operations.first;
    Map<String, TextEditingController> controllers = _controllersForOperation(selected);
    final run = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: Text('Run ${connector['display_name']}'),
          content: SizedBox(
            width: 560,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  DropdownButtonFormField<String>(
                    initialValue: selected['key'] as String,
                    decoration: const InputDecoration(labelText: 'Action'),
                    items: operations
                        .map(
                          (operation) => DropdownMenuItem<String>(
                            value: operation['key'] as String,
                            child: Text(operation['label'] as String),
                          ),
                        )
                        .toList(),
                    onChanged: (value) {
                      if (value == null) return;
                      for (final controller in controllers.values) {
                        controller.dispose();
                      }
                      setState(() {
                        selected = operations.firstWhere((operation) => operation['key'] == value);
                        controllers = _controllersForOperation(selected);
                      });
                    },
                  ),
                  const SizedBox(height: 12),
                  for (final raw in (selected['fields'] as List? ?? const [])) ...[
                    _DynamicField(
                      field: Map<String, dynamic>.from(raw as Map),
                      controller: controllers[raw['key']]!,
                      alreadyConfigured: false,
                    ),
                    const SizedBox(height: 12),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Run live action')),
          ],
        ),
      ),
    );
    if (run != true || !context.mounted) {
      for (final controller in controllers.values) {
        controller.dispose();
      }
      return;
    }

    try {
      final parameters = <String, dynamic>{};
      for (final raw in (selected['fields'] as List? ?? const [])) {
        final field = Map<String, dynamic>.from(raw as Map);
        final key = field['key'] as String;
        final value = controllers[key]!.text.trim();
        if (field['required'] == true && value.isEmpty) {
          throw FormatException('${field['label']} is required.');
        }
        if (value.isEmpty) continue;
        switch (field['type']) {
          case 'json':
            parameters[key] = jsonDecode(value);
            break;
          case 'number':
            final number = num.tryParse(value.replaceAll(',', '.'));
            if (number == null) throw FormatException('${field['label']} must be a number.');
            parameters[key] = number;
            break;
          default:
            parameters[key] = value;
        }
      }
      final result = await context.read<AppState>().executeConnector(
            connector['slug'] as String,
            selected['key'] as String,
            parameters,
          );
      if (!context.mounted) return;
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Verified live result'),
          content: SingleChildScrollView(
            child: SelectableText(const JsonEncoder.withIndent('  ').convert(result)),
          ),
          actions: [TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Close'))],
        ),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    } finally {
      for (final controller in controllers.values) {
        controller.dispose();
      }
    }
  }

  Map<String, TextEditingController> _controllersForOperation(Map<String, dynamic> operation) {
    final controllers = <String, TextEditingController>{};
    for (final raw in (operation['fields'] as List? ?? const [])) {
      final field = Map<String, dynamic>.from(raw as Map);
      controllers[field['key'] as String] = TextEditingController(text: '${field['default'] ?? ''}');
    }
    return controllers;
  }

}

class _DynamicField extends StatelessWidget {
  const _DynamicField({required this.field, required this.controller, required this.alreadyConfigured});

  final Map<String, dynamic> field;
  final TextEditingController controller;
  final bool alreadyConfigured;

  @override
  Widget build(BuildContext context) {
    final type = field['type'] as String? ?? 'text';
    final label = field['label'] as String? ?? field['key'] as String;
    if (type == 'choice') {
      final choices = (field['choices'] as List).map((value) => '$value').toList();
      final current = controller.text.isNotEmpty && choices.contains(controller.text) ? controller.text : null;
      return DropdownButtonFormField<String>(
        initialValue: current,
        decoration: InputDecoration(labelText: label),
        items: choices.map((value) => DropdownMenuItem(value: value, child: Text(value))).toList(),
        onChanged: (value) => controller.text = value ?? '',
      );
    }
    final isSecret = type.contains('secret');
    return TextField(
      controller: controller,
      obscureText: isSecret && type != 'multiline_secret',
      maxLines: type.contains('multiline') || type == 'json' ? 8 : 1,
      keyboardType: type == 'number' ? TextInputType.number : type == 'url' ? TextInputType.url : TextInputType.text,
      decoration: InputDecoration(
        labelText: label,
        helperText: alreadyConfigured && isSecret ? 'Leave empty to keep the saved secret.' : null,
      ),
    );
  }
}
