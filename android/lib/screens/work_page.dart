import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../widgets/common_widgets.dart';
import 'tasks_page.dart';

class WorkPage extends StatelessWidget {
  const WorkPage({this.onOpenBills, this.onOpenPayments, super.key});

  final VoidCallback? onOpenBills;
  final VoidCallback? onOpenPayments;

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 7,
        child: Column(
          children: [
            const TabBar(
              isScrollable: true,
              tabs: [
                Tab(text: 'Tasks'),
                Tab(text: 'Documents'),
                Tab(text: 'Orders'),
                Tab(text: 'Subscriptions'),
                Tab(text: 'Support'),
                Tab(text: 'Contacts'),
                Tab(text: 'Projects'),
              ],
            ),
            Expanded(
              child: TabBarView(
                children: [
                  TasksPage(onOpenBills: onOpenBills, onOpenPayments: onOpenPayments),
                  const _DocumentsView(),
                  const _OrdersView(),
                  const _SubscriptionsView(),
                  const _SupportView(),
                  const _ContactsView(),
                  const _ProjectsView(),
                ],
              ),
            ),
          ],
        ),
      );
}

class _DocumentsView extends StatelessWidget {
  const _DocumentsView();

  @override
  Widget build(BuildContext context) {
    final rows = context.watch<AppState>().documents;
    if (rows.isEmpty) {
      return const EmptyState(
        icon: Icons.folder_outlined,
        title: 'No archived documents',
        message: 'Durable Gmail attachments are uploaded to the connected Google Drive archive.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.all(8),
      itemCount: rows.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final row = rows[index];
        return ListTile(
          leading: const Icon(Icons.description_outlined),
          title: Text('${row['name']}'),
          subtitle: Text('${row['category']} · ${row['account_scope']}'),
          trailing: Text(_size(row['size_bytes'])),
          onTap: '${row['drive_web_url'] ?? ''}'.isEmpty
              ? null
              : () => launchUrl(Uri.parse('${row['drive_web_url']}'), mode: LaunchMode.externalApplication),
        );
      },
    );
  }

  String _size(dynamic value) {
    final bytes = (value as num?)?.toInt() ?? 0;
    if (bytes >= 1024 * 1024) return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    if (bytes >= 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
    return '$bytes B';
  }
}

class _OrdersView extends StatelessWidget {
  const _OrdersView();

  @override
  Widget build(BuildContext context) {
    final rows = context.watch<AppState>().orders;
    if (rows.isEmpty) {
      return const EmptyState(
        icon: Icons.local_shipping_outlined,
        title: 'No tracked orders',
        message: 'Order confirmations and delivery updates are extracted from live Gmail messages.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: rows.length,
      itemBuilder: (context, index) {
        final row = rows[index];
        final delivery = DateTime.tryParse('${row['expected_delivery_at'] ?? ''}');
        return Card(
          child: ListTile(
            leading: const Icon(Icons.inventory_2_outlined),
            title: Text('${row['merchant']} · ${row['order_number']}'),
            subtitle: Text([
              'Status: ${row['status']}',
              if (row['total_amount'] != null) money(row['total_amount'], '${row['currency'] ?? 'EUR'}'),
              if (delivery != null) 'Expected ${DateFormat('dd MMM yyyy').format(delivery)}',
            ].join('\n')),
            onTap: '${row['tracking_url'] ?? ''}'.isEmpty
                ? null
                : () => launchUrl(Uri.parse('${row['tracking_url']}'), mode: LaunchMode.externalApplication),
          ),
        );
      },
    );
  }
}

class _SubscriptionsView extends StatelessWidget {
  const _SubscriptionsView();

  @override
  Widget build(BuildContext context) {
    final rows = context.watch<AppState>().subscriptions;
    if (rows.isEmpty) {
      return const EmptyState(
        icon: Icons.autorenew,
        title: 'No subscriptions detected',
        message: 'Renewals and recurring charges are extracted from real messages and receipts.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.all(8),
      itemCount: rows.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final row = rows[index];
        final next = DateTime.tryParse('${row['next_charge_at'] ?? ''}');
        return ListTile(
          leading: const Icon(Icons.repeat),
          title: Text('${row['provider_name']}'),
          subtitle: Text([
            '${row['description']}',
            '${row['billing_cycle']} · ${row['account_scope']} · ${row['status']}',
            if (next != null) 'Next charge ${DateFormat('dd MMM yyyy').format(next)}',
          ].join('\n')),
          trailing: row['amount'] == null ? null : Text(money(row['amount'], '${row['currency'] ?? 'EUR'}')),
        );
      },
    );
  }
}

class _SupportView extends StatelessWidget {
  const _SupportView();

  @override
  Widget build(BuildContext context) {
    final rows = context.watch<AppState>().supportCases;
    if (rows.isEmpty) {
      return const EmptyState(
        icon: Icons.support_agent,
        title: 'No support cases',
        message: 'Requests that need a response or follow-up are tracked from Gmail.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: rows.length,
      itemBuilder: (context, index) {
        final row = rows[index];
        final followUp = DateTime.tryParse('${row['next_follow_up_at'] ?? ''}');
        return Card(
          child: ListTile(
            leading: const Icon(Icons.support_agent),
            title: Text('${row['subject']}'),
            subtitle: Text([
              '${row['requester']}',
              '${row['priority']} · ${row['status']} · ${row['category']}',
              if ('${row['last_action'] ?? ''}'.isNotEmpty) '${row['last_action']}',
              if (followUp != null) 'Follow up ${DateFormat('dd MMM yyyy HH:mm').format(followUp)}',
            ].join('\n')),
            trailing: PopupMenuButton<String>(
              onSelected: (value) => context.read<AppState>().setSupportCaseStatus(row['id'] as int, value),
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'open', child: Text('Open')),
                PopupMenuItem(value: 'waiting', child: Text('Waiting')),
                PopupMenuItem(value: 'resolved', child: Text('Resolved')),
                PopupMenuItem(value: 'closed', child: Text('Closed')),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _ContactsView extends StatelessWidget {
  const _ContactsView();

  @override
  Widget build(BuildContext context) {
    final rows = context.watch<AppState>().contacts;
    if (rows.isEmpty) {
      return const EmptyState(
        icon: Icons.contacts_outlined,
        title: 'No contacts synchronized',
        message: 'Google Contacts data appears after the Google account is authorized and synchronized.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.all(8),
      itemCount: rows.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final row = rows[index];
        final emails = (row['emails'] as List? ?? const []).join(', ');
        final phones = (row['phones'] as List? ?? const []).join(', ');
        return ListTile(
          leading: const CircleAvatar(child: Icon(Icons.person_outline)),
          title: Text('${row['display_name']}'.isEmpty ? emails : '${row['display_name']}'),
          subtitle: Text([if (emails.isNotEmpty) emails, if (phones.isNotEmpty) phones, if ('${row['organization'] ?? ''}'.isNotEmpty) '${row['organization']}'].join('\n')),
        );
      },
    );
  }
}

class _ProjectsView extends StatelessWidget {
  const _ProjectsView();

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final cloudflare = state.cloudflareResources;
    final repositories = state.githubRepositories;
    final notifications = state.githubNotifications;
    if (repositories.isEmpty && cloudflare.isEmpty) {
      return const EmptyState(
        icon: Icons.hub_outlined,
        title: 'No project services connected',
        message: 'Configure GitHub or Cloudflare on the backend to load live repositories and infrastructure.',
      );
    }
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        if (repositories.isNotEmpty) ...[
          Row(
            children: [
              Expanded(child: Text('GitHub repositories', style: Theme.of(context).textTheme.titleLarge)),
              FilledButton.tonalIcon(
                onPressed: () => _createIssue(context, repositories),
                icon: const Icon(Icons.add_task),
                label: const Text('Issue'),
              ),
            ],
          ),
          ...repositories.take(50).map((repo) => ListTile(
                leading: Icon(repo['private'] == true ? Icons.lock_outline : Icons.public),
                title: Text('${repo['full_name']}'),
                subtitle: Text('${repo['description'] ?? ''}'),
                trailing: const Icon(Icons.open_in_new),
                onTap: () => launchUrl(Uri.parse('${repo['html_url']}'), mode: LaunchMode.externalApplication),
              )),
          if (notifications.isNotEmpty) Text('${notifications.length} unread GitHub notifications'),
          const Divider(height: 32),
        ],
        if (cloudflare.isNotEmpty) ...[
          Text('Cloudflare resources', style: Theme.of(context).textTheme.titleLarge),
          ListTile(title: const Text('Workers'), trailing: Text('${(cloudflare['workers'] as List? ?? const []).length}')),
          ListTile(title: const Text('D1 databases'), trailing: Text('${(cloudflare['d1_databases'] as List? ?? const []).length}')),
          ListTile(title: const Text('R2 buckets'), trailing: Text('${(cloudflare['r2_buckets'] as List? ?? const []).length}')),
          ListTile(title: const Text('Zones'), trailing: Text('${(cloudflare['zones'] as List? ?? const []).length}')),
        ],
      ],
    );
  }

  Future<void> _createIssue(BuildContext context, List<Map<String, dynamic>> repositories) async {
    String repository = '${repositories.first['full_name']}';
    final title = TextEditingController();
    final body = TextEditingController();
    final submit = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Create GitHub issue'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: repository,
                  isExpanded: true,
                  items: repositories.map((repo) => DropdownMenuItem(value: '${repo['full_name']}', child: Text('${repo['full_name']}'))).toList(),
                  onChanged: (value) => setState(() => repository = value ?? repository),
                ),
                TextField(controller: title, decoration: const InputDecoration(labelText: 'Title')),
                TextField(controller: body, maxLines: 6, decoration: const InputDecoration(labelText: 'Details')),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Create')),
          ],
        ),
      ),
    );
    if (submit != true || title.text.trim().isEmpty || !context.mounted) return;
    await context.read<AppState>().createGitHubIssue(repository: repository, title: title.text.trim(), body: body.text.trim());
  }
}
