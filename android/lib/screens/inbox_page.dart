import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../widgets/common_widgets.dart';

class InboxPage extends StatelessWidget {
  const InboxPage({super.key});

  @override
  Widget build(BuildContext context) {
    final emails = context.watch<AppState>().emails;
    if (emails.isEmpty) {
      return const EmptyState(
        icon: Icons.inbox_outlined,
        title: 'No processed messages',
        message: 'Connect Google and run the Gmail sync. This screen never displays fabricated mail.',
      );
    }
    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().syncGmail(),
      child: ListView.separated(
        padding: const EdgeInsets.all(8),
        itemCount: emails.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final email = emails[index];
          final date = DateTime.tryParse('${email['received_at'] ?? ''}');
          return ListTile(
            leading: Icon(email['action_required'] == true ? Icons.priority_high : Icons.mail_outline),
            title: Text('${email['subject'] ?? '(No subject)'}', maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text(
              '${email['sender'] ?? ''}\n${email['category'] ?? 'unclassified'} · ${email['status'] ?? ''}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: date == null ? null : Text(DateFormat('dd/MM').format(date)),
            isThreeLine: true,
          );
        },
      ),
    );
  }
}
