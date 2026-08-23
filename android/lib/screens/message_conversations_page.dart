import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class MessageConversationsPage extends StatefulWidget {
  const MessageConversationsPage({super.key});

  @override
  State<MessageConversationsPage> createState() => _MessageConversationsPageState();
}

class _MessageConversationsPageState extends State<MessageConversationsPage> {
  final _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final conversations = _conversations(state);
    final query = _search.text.trim().toLowerCase();
    final filtered = query.isEmpty
        ? conversations
        : conversations.where((row) {
            final haystack = '${row.name} ${row.address} ${row.preview}'.toLowerCase();
            return haystack.contains(query);
          }).toList();
    return Scaffold(
      appBar: AppBar(title: const Text('SMS/MMS conversations')),
      body: RefreshIndicator(
        onRefresh: () => context.read<AppState>().refreshCommunications(syncDeviceHistory: true),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 28),
          children: [
            TextField(
              controller: _search,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                hintText: 'Search people or message text…',
                prefixIcon: Icon(Icons.search_rounded),
              ),
            ),
            const SizedBox(height: 10),
            if (filtered.isEmpty)
              const Card(
                child: ListTile(
                  leading: Icon(Icons.sms_outlined),
                  title: Text('No SMS/MMS conversations synced yet'),
                  subtitle: Text('Pull to refresh. SMS and MMS history are grouped by person or phone number.'),
                ),
              )
            else
              for (final conversation in filtered)
                Card(
                  child: ListTile(
                    leading: CircleAvatar(child: Text(_initials(conversation.name))),
                    title: Text(
                      conversation.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w800),
                    ),
                    subtitle: Text(
                      conversation.preview,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text(_stamp(conversation.lastAt), style: const TextStyle(fontSize: 11, color: VaTheme.textMuted)),
                        if (conversation.hasMms)
                          const Padding(
                            padding: EdgeInsets.only(top: 4),
                            child: Text('MMS', style: TextStyle(fontSize: 10, color: VaTheme.secondary, fontWeight: FontWeight.w800)),
                          ),
                      ],
                    ),
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => _ConversationPage(conversation: conversation),
                      ),
                    ),
                  ),
                ),
          ],
        ),
      ),
    );
  }

  List<_Conversation> _conversations(AppState state) {
    final byPhone = <String, String>{};
    for (final relationship in state.relationships) {
      final phone = _digits('${relationship['primary_phone'] ?? ''}');
      final name = '${relationship['display_name'] ?? ''}'.trim();
      if (phone.isNotEmpty && name.isNotEmpty) byPhone[phone] = name;
    }

    final groups = <String, List<Map<String, dynamic>>>{};
    for (final event in state.communications) {
      final channel = '${event['channel'] ?? ''}'.toLowerCase();
      final type = '${event['event_type'] ?? ''}'.toLowerCase();
      if (channel != 'sms' || !{'message', 'mms'}.contains(type)) continue;
      final incoming = '${event['direction'] ?? ''}' != 'outgoing';
      final address = '${incoming ? event['sender'] : event['recipient'] ?? ''}'.trim();
      final rawKey = '${event['thread_key'] ?? ''}'.trim();
      final key = rawKey.isNotEmpty ? rawKey : _digits(address);
      if (key.isEmpty) continue;
      groups.putIfAbsent(key, () => <Map<String, dynamic>>[]).add(event);
    }

    final result = <_Conversation>[];
    for (final entry in groups.entries) {
      final messages = entry.value
        ..sort((a, b) => _date(a).compareTo(_date(b)));
      final latest = messages.last;
      final incoming = '${latest['direction'] ?? ''}' != 'outgoing';
      final address = '${incoming ? latest['sender'] : latest['recipient'] ?? ''}'.trim();
      final normalized = _digits(address);
      String? name = byPhone[normalized];
      if (name == null && normalized.length >= 8) {
        final suffix = normalized.substring(normalized.length - 8);
        for (final candidate in byPhone.entries) {
          if (candidate.key.endsWith(suffix)) {
            name = candidate.value;
            break;
          }
        }
      }
      final body = '${latest['body'] ?? ''}'.trim();
      result.add(
        _Conversation(
          address: address,
          name: name ?? (address.isEmpty ? 'Unknown sender' : address),
          messages: messages,
          preview: body.isEmpty ? 'MMS' : body,
          lastAt: _date(latest),
          hasMms: messages.any((row) => '${row['event_type'] ?? ''}'.toLowerCase() == 'mms'),
        ),
      );
    }
    result.sort((a, b) => b.lastAt.compareTo(a.lastAt));
    return result;
  }
}

class _ConversationPage extends StatefulWidget {
  const _ConversationPage({required this.conversation});
  final _Conversation conversation;

  @override
  State<_ConversationPage> createState() => _ConversationPageState();
}

class _ConversationPageState extends State<_ConversationPage> {
  final _composer = TextEditingController();
  bool _sending = false;

  @override
  void dispose() {
    _composer.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _composer.text.trim();
    if (text.isEmpty || widget.conversation.address.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await context.read<AppState>().sendSms(target: widget.conversation.address, text: text);
      _composer.clear();
      await context.read<AppState>().refreshCommunications(syncDeviceHistory: true);
      if (mounted) Navigator.of(context).pop();
    } catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final messages = widget.conversation.messages;
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(widget.conversation.name, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
            Text(widget.conversation.address, style: const TextStyle(fontSize: 11, color: VaTheme.textMuted)),
          ],
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(12, 14, 12, 14),
              itemCount: messages.length,
              itemBuilder: (context, index) {
                final message = messages[index];
                final outgoing = '${message['direction'] ?? ''}' == 'outgoing';
                final body = '${message['body'] ?? ''}'.trim();
                final isMms = '${message['event_type'] ?? ''}'.toLowerCase() == 'mms';
                return Align(
                  alignment: outgoing ? Alignment.centerRight : Alignment.centerLeft,
                  child: Container(
                    constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * .78),
                    margin: const EdgeInsets.only(bottom: 8),
                    padding: const EdgeInsets.fromLTRB(12, 9, 12, 8),
                    decoration: BoxDecoration(
                      color: outgoing ? VaTheme.primary.withValues(alpha: .26) : Colors.white.withValues(alpha: .07),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (isMms)
                          const Padding(
                            padding: EdgeInsets.only(bottom: 4),
                            child: Text('MMS', style: TextStyle(fontSize: 10, fontWeight: FontWeight.w900, color: VaTheme.secondary)),
                          ),
                        Text(body.isEmpty ? 'MMS message' : body, style: const TextStyle(height: 1.35)),
                        const SizedBox(height: 4),
                        Text(_stamp(_date(message)), style: const TextStyle(fontSize: 10, color: VaTheme.textMuted)),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(10, 6, 10, 10),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _composer,
                      minLines: 1,
                      maxLines: 5,
                      decoration: const InputDecoration(hintText: 'SMS message…'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    onPressed: _sending ? null : _send,
                    icon: _sending
                        ? const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.send_rounded),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Conversation {
  const _Conversation({
    required this.address,
    required this.name,
    required this.messages,
    required this.preview,
    required this.lastAt,
    required this.hasMms,
  });
  final String address;
  final String name;
  final List<Map<String, dynamic>> messages;
  final String preview;
  final DateTime lastAt;
  final bool hasMms;
}

DateTime _date(Map<String, dynamic> row) =>
    DateTime.tryParse('${row['occurred_at'] ?? row['created_at'] ?? ''}')?.toLocal() ?? DateTime.fromMillisecondsSinceEpoch(0);

String _digits(String value) => value.replaceAll(RegExp(r'\D'), '');

String _initials(String value) {
  final parts = value.trim().split(RegExp(r'\s+')).where((part) => part.isNotEmpty).take(2).toList();
  if (parts.isEmpty) return '?';
  return parts.map((part) => part[0].toUpperCase()).join();
}

String _stamp(DateTime value) {
  if (value.millisecondsSinceEpoch == 0) return '';
  final now = DateTime.now();
  if (value.year == now.year && value.month == now.month && value.day == now.day) {
    return '${value.hour.toString().padLeft(2, '0')}:${value.minute.toString().padLeft(2, '0')}';
  }
  return '${value.day.toString().padLeft(2, '0')}/${value.month.toString().padLeft(2, '0')}';
}
