import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class CommunicationsPage extends StatelessWidget {
  const CommunicationsPage({super.key});

  Future<void> _sendSms(BuildContext context) async {
    final number = TextEditingController();
    final message = TextEditingController();
    final send = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Send SMS'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: number, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Phone number')),
            const SizedBox(height: 12),
            TextField(controller: message, minLines: 2, maxLines: 6, decoration: const InputDecoration(labelText: 'Message')),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Send')),
        ],
      ),
    );
    if (send != true || !context.mounted || number.text.trim().isEmpty || message.text.trim().isEmpty) return;
    await context.read<AppState>().sendSms(target: number.text.trim(), text: message.text.trim());
  }

  Future<void> _addCallRule(BuildContext context) async {
    final number = TextEditingController();
    var disposition = 'silence';
    final save = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Add call rule'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(controller: number, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Phone number')),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: disposition,
                decoration: const InputDecoration(labelText: 'When this number calls'),
                items: const [
                  DropdownMenuItem(value: 'allow', child: Text('Always allow / VIP')),
                  DropdownMenuItem(value: 'silence', child: Text('Silence')),
                  DropdownMenuItem(value: 'block', child: Text('Block')),
                ],
                onChanged: (value) => setState(() => disposition = value ?? disposition),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
          ],
        ),
      ),
    );
    if (save != true || !context.mounted || number.text.trim().isEmpty) return;
    await context.read<AppState>().saveCallRule(phoneNumber: number.text.trim(), disposition: disposition);
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final status = state.communicationStatus;
    bool ok(String key) => status[key] == true;
    return Scaffold(
      appBar: AppBar(title: const Text('Communications Autopilot')),
      body: RefreshIndicator(
        onRefresh: () => context.read<AppState>().refreshCommunications(syncDeviceHistory: true),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text('Phone access', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900)),
            const SizedBox(height: 6),
            const Text('Grant these Android roles once. After that, calls and messages feed the same Autopilot, Needs You and Daily Briefing as email.'),
            const SizedBox(height: 12),
            _AccessTile(title: 'SMS read/send permissions', active: ok('read_sms') && ok('send_sms'), onTap: () => context.read<AppState>().requestCommunicationPermissions()),
            _AccessTile(title: 'Default SMS role', active: ok('sms_role'), onTap: () => context.read<AppState>().requestSmsRole()),
            _AccessTile(title: 'WhatsApp / Signal / Telegram / Messenger access', active: ok('notification_access'), onTap: () => context.read<AppState>().openNotificationAccess()),
            _AccessTile(title: 'Incoming call screening', active: ok('call_screening_role'), onTap: () => context.read<AppState>().requestCallScreeningRole()),
            _AccessTile(title: 'Call-log access', active: ok('read_call_log'), onTap: () => context.read<AppState>().requestCommunicationPermissions()),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: state.busy ? null : () => context.read<AppState>().refreshCommunications(syncDeviceHistory: true),
                  icon: const Icon(Icons.sync_rounded),
                  label: const Text('Sync phone history & policies now'),
                ),
                FilledButton.tonalIcon(
                  onPressed: ok('send_sms') ? () => _sendSms(context) : null,
                  icon: const Icon(Icons.sms_outlined),
                  label: const Text('Send SMS'),
                ),
              ],
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(child: Text('Call rules', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900))),
                FilledButton.tonalIcon(onPressed: () => _addCallRule(context), icon: const Icon(Icons.add_rounded), label: const Text('Add')),
              ],
            ),
            const SizedBox(height: 8),
            if (state.communicationRules.isEmpty)
              const Card(child: ListTile(title: Text('No explicit call rules. Unknown callers are allowed unless you enable “silence unknown callers” in Automation settings.')))
            else
              for (final rule in state.communicationRules)
                Card(
                  child: ListTile(
                    leading: Icon(rule['disposition'] == 'block' ? Icons.block_rounded : rule['disposition'] == 'silence' ? Icons.volume_off_outlined : Icons.star_outline_rounded),
                    title: Text('${rule['contact_key']}', style: const TextStyle(fontWeight: FontWeight.w800)),
                    subtitle: Text('${rule['disposition']}'),
                    trailing: IconButton(onPressed: () => context.read<AppState>().deleteCallRule(rule['id'] as int), icon: const Icon(Icons.delete_outline_rounded)),
                  ),
                ),
            const Divider(height: 36),
            Row(
              children: [
                Expanded(child: Text('Recent communications', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900))),
                Text('${state.communications.length}'),
              ],
            ),
            const SizedBox(height: 8),
            if (state.communications.isEmpty)
              const Card(child: ListTile(title: Text('No phone/message events synced yet.')))
            else
              for (final event in state.communications.take(100)) _CommunicationTile(event: event),
          ],
        ),
      ),
    );
  }
}

class _AccessTile extends StatelessWidget {
  const _AccessTile({required this.title, required this.active, required this.onTap});
  final String title;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Card(
        child: ListTile(
          leading: Icon(active ? Icons.check_circle_rounded : Icons.shield_outlined, color: active ? VaTheme.success : VaTheme.warning),
          title: Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
          subtitle: Text(active ? 'Enabled' : 'Tap to grant/enable'),
          trailing: active ? null : FilledButton(onPressed: onTap, child: const Text('Enable')),
          onTap: active ? null : onTap,
        ),
      );
}

class _CommunicationTile extends StatelessWidget {
  const _CommunicationTile({required this.event});
  final Map<String, dynamic> event;

  @override
  Widget build(BuildContext context) {
    final channel = '${event['channel'] ?? 'message'}';
    final protected = event['protected'] == true;
    final needs = event['action_required'] == true;
    return Card(
      child: ListTile(
        leading: Icon(channel == 'call' ? Icons.phone_outlined : Icons.chat_bubble_outline_rounded, color: protected ? VaTheme.warning : VaTheme.secondary),
        title: Text('${event['sender'] ?? channel}', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text('${event['body'] ?? ''}\n$channel · ${event['category'] ?? ''}${needs ? ' · needs attention' : ''}', maxLines: 4, overflow: TextOverflow.ellipsis),
        isThreeLine: true,
        trailing: protected ? const Icon(Icons.lock_outline_rounded, color: VaTheme.warning) : (needs ? const Icon(Icons.priority_high_rounded, color: VaTheme.warning) : null),
      ),
    );
  }
}
