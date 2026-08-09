import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final config = state.configuration;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Operations', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: state.busy ? null : () => _runVaNow(context),
          icon: const Icon(Icons.bolt_rounded),
          label: const Text('Run complete VA workflow now'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: config['google_connected'] == true ? () => state.syncGmail() : null,
          icon: const Icon(Icons.mark_email_read_outlined),
          label: const Text('Process Gmail now'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: (config['bank_accounts_connected'] as num? ?? 0) > 0 ? () => state.syncBanks() : null,
          icon: const Icon(Icons.account_balance_outlined),
          label: const Text('Synchronize bank accounts'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: config['google_connected'] == true ? () => state.syncExternalServices() : null,
          icon: const Icon(Icons.sync),
          label: const Text('Synchronize contacts and external services'),
        ),
        const Divider(height: 32),
        Text('Build and update from phone', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 8),
        const Text(
          'The GitHub connector can start the included Android build workflow. GitHub performs the Flutter build in the cloud; Android still requires confirmation before installing the resulting APK.',
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: config['github_configured'] == true ? () => _openSigningSetup(context) : null,
          icon: const Icon(Icons.key_outlined),
          label: const Text('Set up persistent update signing'),
        ),
        const SizedBox(height: 8),
        const Text(
          'Android only accepts an APK as an update when it is signed by the same release key as the installed app. Initialize this once before building the first stable-signed APK; never rotate that key afterward.',
        ),
        const SizedBox(height: 12),
        FilledButton.tonalIcon(
          onPressed: config['github_configured'] == true ? () => _triggerBuild(context) : null,
          icon: const Icon(Icons.build_circle_outlined),
          label: const Text('Build Android update'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: config['github_configured'] == true ? () => _showBuildRuns(context) : null,
          icon: const Icon(Icons.history),
          label: const Text('View build runs'),
        ),
        const Divider(height: 32),
        Text('Device', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: () => context.read<AppState>().disconnect(),
          icon: const Icon(Icons.link_off),
          label: const Text('Unpair this phone'),
        ),
      ],
    );
  }



  Future<void> _runVaNow(BuildContext context) async {
    try {
      final result = await context.read<AppState>().runAutomationNow();
      if (!context.mounted) return;
      final errors = (result['errors'] as Map?)?.length ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(errors == 0
              ? 'VA workflow completed successfully.'
              : 'VA workflow completed with $errors exception${errors == 1 ? '' : 's'}.'),
        ),
      );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _openSigningSetup(BuildContext context) async {
    try {
      final serverUrl = await context.read<AppState>().api.serverUrl;
      if (serverUrl == null || serverUrl.isEmpty) {
        throw Exception('This phone is not paired with a VA server.');
      }
      final uri = Uri.parse('${serverUrl.replaceAll(RegExp(r'/+$'), '')}/setup/android-signing');
      if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
        throw Exception('Could not open the Android signing setup page.');
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _triggerBuild(BuildContext context) async {
    try {
      final state = context.read<AppState>();
      final signing = await state.androidSigningStatus();
      if (signing['configured'] != true) {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Persistent Android signing must be initialized before building an update.')),
          );
          await _openSigningSetup(context);
        }
        return;
      }
      final result = await state.triggerAndroidBuild();
      final url = result['actions_url']?.toString();
      if (url != null && url.isNotEmpty) {
        await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      }
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('GitHub accepted the Android build request.')),
        );
      }
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _showBuildRuns(BuildContext context) async {
    try {
      final runs = await context.read<AppState>().loadAndroidBuildRuns();
      if (!context.mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (sheetContext) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('Android build runs', style: Theme.of(sheetContext).textTheme.titleLarge),
            const SizedBox(height: 8),
            if (runs.isEmpty) const ListTile(title: Text('No workflow runs found.')),
            for (final run in runs)
              ListTile(
                leading: Icon(run['conclusion'] == 'success' ? Icons.check_circle : Icons.pending_outlined),
                title: Text('${run['name'] ?? 'Android build'} · ${run['status'] ?? 'unknown'}'),
                subtitle: Text('${run['created_at'] ?? ''}'),
                trailing: const Icon(Icons.open_in_new),
                onTap: () {
                  final url = run['html_url']?.toString();
                  if (url != null && url.isNotEmpty) launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
                },
              ),
          ],
        ),
      );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}
