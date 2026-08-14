import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models/view_models.dart';
import '../theme/va_theme.dart';
import '../widgets/common_widgets.dart';
import 'tasks_page.dart';
import 'va_operations_page.dart';

class WorkPage extends StatelessWidget {
  const WorkPage({this.onOpenBills, this.onOpenPayments, super.key});

  final VoidCallback? onOpenBills;
  final VoidCallback? onOpenPayments;

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 10,
        child: Column(
          children: [
            const TabBar(
              isScrollable: true,
              tabs: [
                Tab(text: 'Operations'),
                Tab(text: 'Tasks'),
                Tab(text: 'Calendar'),
                Tab(text: 'Portals'),
                Tab(text: 'Documents'),
                Tab(text: 'Orders'),
                Tab(text: 'Subscriptions'),
                Tab(text: 'Support'),
                Tab(text: 'Relationships'),
                Tab(text: 'Projects'),
              ],
            ),
            Expanded(
              child: TabBarView(
                children: [
                  const VaOperationsPage(),
                  TasksPage(onOpenBills: onOpenBills, onOpenPayments: onOpenPayments),
                  const _CalendarView(),
                  const _PortalsView(),
                  const _DocumentsView(),
                  const _OrdersView(),
                  const _SubscriptionsView(),
                  const _SupportView(),
                  const _RelationshipsView(),
                  const _ProjectsView(),
                ],
              ),
            ),
          ],
        ),
      );
}

class _CalendarView extends StatelessWidget {
  const _CalendarView();

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final status = state.calendarStatus;
    final rows = state.calendarEvents;
    final lastSync = _formatSync(status['last_sync_at']);
    final awaiting = (status['awaiting_attendee_response'] as num?)?.toInt() ?? 0;
    final upcoming = (status['upcoming_events'] as num?)?.toInt() ?? rows.length;
    final lastError = '${status['last_error'] ?? ''}'.trim();

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        children: [
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(18),
              color: VaTheme.surface,
              border: Border.all(color: VaTheme.primary.withValues(alpha: .24)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.calendar_month_rounded, color: VaTheme.primary),
                    const SizedBox(width: 9),
                    const Expanded(
                      child: Text('Calendar ownership', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 16)),
                    ),
                    IconButton.filledTonal(
                      tooltip: 'Sync Google Calendar now',
                      onPressed: state.busy ? null : () => _sync(context),
                      icon: const Icon(Icons.sync_rounded),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  '$upcoming upcoming · $awaiting awaiting response · Last sync $lastSync',
                  style: const TextStyle(color: VaTheme.textMuted),
                ),
                if (lastError.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(lastError, style: const TextStyle(color: VaTheme.warning, fontWeight: FontWeight.w700)),
                ],
              ],
            ),
          ),
          const SizedBox(height: 12),
          if (rows.isEmpty)
            SizedBox(
              height: MediaQuery.sizeOf(context).height * .48,
              child: const EmptyState(
                icon: Icons.event_available_outlined,
                title: 'No upcoming calendar events',
                message: 'The VA mirrors Google Calendar and will place verified scheduling work here automatically.',
              ),
            )
          else
            for (final row in rows) ...[
              _CalendarEventCard(row: row),
              const SizedBox(height: 9),
            ],
        ],
      ),
    );
  }

  static String _formatSync(dynamic value) {
    final raw = '$value'.trim();
    if (raw.isEmpty || raw == 'null') return 'not yet';
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return raw;
    return DateFormat('d MMM HH:mm').format(parsed.toLocal());
  }

  Future<void> _sync(BuildContext context) async {
    try {
      final result = await context.read<AppState>().syncCalendarNow();
      if (!context.mounted) return;
      final events = (result['events'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Calendar synchronized · $events event${events == 1 ? '' : 's'} observed.')),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}


class _CalendarEventCard extends StatelessWidget {
  const _CalendarEventCard({required this.row});

  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final summary = '${row['summary'] ?? 'Untitled event'}';
    final location = '${row['location'] ?? ''}'.trim();
    final start = _formatTime('${row['start'] ?? ''}');
    final end = _formatTime('${row['end'] ?? ''}', compact: true);
    final attendees = row['attendees'] is List ? List<dynamic>.from(row['attendees'] as List) : const <dynamic>[];
    final pending = attendees.where((item) => item is Map && '${item['responseStatus'] ?? ''}' == 'needsAction').length;
    final link = '${row['html_link'] ?? ''}'.trim();
    final owned = row['owned_objective_id'] != null;

    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: link.isEmpty ? null : () => launchUrl(Uri.parse(link), mode: LaunchMode.externalApplication),
        child: Ink(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            color: VaTheme.surface,
            border: Border.all(color: owned ? VaTheme.primary.withValues(alpha: .34) : VaTheme.border),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(14),
                  color: VaTheme.primary.withValues(alpha: .12),
                ),
                child: const Icon(Icons.event_rounded, color: VaTheme.primary),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(child: Text(summary, style: const TextStyle(fontWeight: FontWeight.w900))),
                        if (owned)
                          const Tooltip(
                            message: 'Owned by the VA objective engine',
                            child: Icon(Icons.verified_rounded, size: 17, color: VaTheme.primary),
                          ),
                      ],
                    ),
                    const SizedBox(height: 5),
                    Text('$start${end.isEmpty ? '' : ' – $end'}', style: const TextStyle(fontWeight: FontWeight.w700)),
                    if (location.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(location, style: const TextStyle(color: VaTheme.textMuted)),
                    ],
                    if (attendees.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Text(
                        '${attendees.length} attendee${attendees.length == 1 ? '' : 's'}${pending > 0 ? ' · $pending awaiting response' : ' · responses received'}',
                        style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
                      ),
                    ],
                  ],
                ),
              ),
              if (link.isNotEmpty) const Icon(Icons.open_in_new_rounded, size: 17, color: VaTheme.textMuted),
            ],
          ),
        ),
      ),
    );
  }

  static String _formatTime(String raw, {bool compact = false}) {
    if (raw.trim().isEmpty) return '';
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return raw;
    final local = parsed.toLocal();
    if (raw.length == 10) return DateFormat('EEE d MMM').format(local);
    return DateFormat(compact ? 'HH:mm' : 'EEE d MMM · HH:mm').format(local);
  }
}


class _PortalsView extends StatelessWidget {
  const _PortalsView();

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final status = state.browserStatus;
    final portals = state.browserPortals;
    final operations = state.browserOperations;
    final configured = (status['configured_portals'] as num?)?.toInt() ?? portals.length;
    final authRequired = (status['needs_user_auth'] as num?)?.toInt() ?? 0;
    final ambiguous = (status['ambiguous_outcomes'] as num?)?.toInt() ?? 0;
    final verified = (status['verified'] as num?)?.toInt() ?? 0;

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Material(
            color: VaTheme.surface,
            borderRadius: BorderRadius.circular(20),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.public_rounded, color: VaTheme.primary),
                      const SizedBox(width: 10),
                      const Expanded(
                        child: Text('Secure portal operator', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 16)),
                      ),
                      FilledButton.icon(
                        onPressed: state.busy ? null : () => _showAddPortalDialog(context),
                        icon: const Icon(Icons.add_rounded, size: 18),
                        label: const Text('Add portal'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  const Text(
                    'VAAPP uses a real Chromium session for allowlisted portals. Credentials and cookies stay encrypted on the backend; CAPTCHA and MFA are never bypassed.',
                    style: TextStyle(color: VaTheme.textMuted),
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      _PortalMetric(label: 'Portals', value: '$configured'),
                      _PortalMetric(label: 'Verified runs', value: '$verified'),
                      _PortalMetric(label: 'Auth needed', value: '$authRequired'),
                      _PortalMetric(label: 'Ambiguous', value: '$ambiguous'),
                    ],
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          const Text('Configured portals', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 15)),
          const SizedBox(height: 8),
          if (portals.isEmpty)
            const _PortalEmpty(
              icon: Icons.vpn_key_off_rounded,
              message: 'No browser portals are configured yet. Add a portal so the VA can own routine web workflows instead of handing them back to you.',
            )
          else
            ...portals.map((row) => Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: _PortalCard(row: row, onEdit: () => _showAddPortalDialog(context, row)),
                )),
          const SizedBox(height: 18),
          const Text('Portal operations', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 15)),
          const SizedBox(height: 8),
          if (operations.isEmpty)
            const _PortalEmpty(
              icon: Icons.travel_explore_rounded,
              message: 'No portal work has been queued yet. Browser operations created by the VA objective engine will appear here with real provider evidence.',
            )
          else
            ...operations.map((row) => Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: _BrowserOperationCard(row: row),
                )),
        ],
      ),
    );
  }

  Future<void> _showAddPortalDialog(BuildContext context, [Map<String, dynamic>? existing]) async {
    final editing = existing != null;
    final name = TextEditingController(text: '${existing?['name'] ?? ''}');
    final baseUrl = TextEditingController(text: '${existing?['base_url'] ?? ''}');
    final loginUrl = TextEditingController(text: '${existing?['login_url'] ?? ''}');
    final allowedHosts = TextEditingController(
      text: existing?['allowed_hosts'] is List
          ? (existing!['allowed_hosts'] as List).map((value) => '$value').join(', ')
          : '',
    );
    final username = TextEditingController();
    final password = TextEditingController();
    var accountScope = '${existing?['account_scope'] ?? 'personal'}';
    var enabled = existing?['enabled'] != false;
    final formKey = GlobalKey<FormState>();
    final result = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: Text(editing ? 'Edit secure portal' : 'Add secure portal'),
          content: Form(
            key: formKey,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextFormField(
                    controller: name,
                    decoration: const InputDecoration(labelText: 'Portal name'),
                    validator: (value) => (value ?? '').trim().isEmpty ? 'Enter a portal name' : null,
                  ),
                  TextFormField(
                    controller: baseUrl,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(labelText: 'Base URL', hintText: 'https://portal.example.com'),
                    validator: (value) {
                      final uri = Uri.tryParse((value ?? '').trim());
                      if (uri == null || uri.scheme != 'https' || uri.host.isEmpty) return 'Enter a valid HTTPS portal URL';
                      return null;
                    },
                  ),
                  TextField(
                    controller: loginUrl,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(labelText: 'Login URL (optional)'),
                  ),
                  TextField(
                    controller: allowedHosts,
                    decoration: const InputDecoration(
                      labelText: 'Allowed / extra login hosts (optional)',
                      hintText: 'login.microsoftonline.com, accounts.example.com',
                    ),
                  ),
                  const SizedBox(height: 10),
                  DropdownButtonFormField<String>(
                    initialValue: accountScope,
                    decoration: const InputDecoration(labelText: 'Scope'),
                    items: const [
                      DropdownMenuItem(value: 'personal', child: Text('Personal')),
                      DropdownMenuItem(value: 'pro', child: Text('Pro')),
                    ],
                    onChanged: (value) => setDialogState(() => accountScope = value ?? accountScope),
                  ),
                  SwitchListTile.adaptive(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Portal enabled'),
                    subtitle: const Text('Disabled portals remain saved but cannot execute browser work.'),
                    value: enabled,
                    onChanged: (value) => setDialogState(() => enabled = value),
                  ),
                  const SizedBox(height: 6),
                  TextField(
                    controller: username,
                    decoration: InputDecoration(
                      labelText: editing ? 'Replace username/email (optional)' : 'Username/email (optional)',
                    ),
                    autocorrect: false,
                  ),
                  TextField(
                    controller: password,
                    decoration: InputDecoration(
                      labelText: editing ? 'Replace password (optional)' : 'Password (optional)',
                    ),
                    obscureText: true,
                    enableSuggestions: false,
                    autocorrect: false,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    editing
                        ? 'Leave login fields blank to keep the existing encrypted credentials. One-time codes are never stored.'
                        : 'Only username/password can be stored. One-time codes remain one-time and are cleared after use.',
                    style: const TextStyle(fontSize: 12, color: VaTheme.textMuted),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(
              onPressed: () {
                if (formKey.currentState?.validate() != true) return;
                Navigator.pop(dialogContext, true);
              },
              child: Text(editing ? 'Save changes' : 'Save portal'),
            ),
          ],
        ),
      ),
    );
    if (result != true || !context.mounted) return;
    final rawName = name.text.trim();
    var slug = '${existing?['slug'] ?? ''}'.trim();
    if (!editing) {
      slug = rawName.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '-');
      slug = slug.replaceAll(RegExp(r'^-+|-+$'), '');
      if (slug.length < 2) slug = 'portal-${DateTime.now().millisecondsSinceEpoch}';
    }
    try {
      await context.read<AppState>().addBrowserPortal(
            slug: slug,
            name: rawName,
            baseUrl: baseUrl.text.trim(),
            loginUrl: loginUrl.text.trim(),
            allowedHosts: allowedHosts.text
                .split(',')
                .map((value) => value.trim())
                .where((value) => value.isNotEmpty)
                .toList(),
            username: username.text,
            password: password.text,
            accountScope: accountScope,
            enabled: enabled,
          );
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(editing ? 'Secure portal updated.' : 'Secure portal configured.')),
        );
      }
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Portal setup failed: $error')));
      }
    }
  }

}

class _PortalMetric extends StatelessWidget {
  const _PortalMetric({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: VaTheme.border),
        ),
        child: Text('$label · $value', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w800)),
      );
}

class _PortalEmpty extends StatelessWidget {
  const _PortalEmpty({required this.icon, required this.message});
  final IconData icon;
  final String message;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(18),
          color: VaTheme.surface,
          border: Border.all(color: VaTheme.border),
        ),
        child: Row(
          children: [
            Icon(icon, color: VaTheme.textMuted),
            const SizedBox(width: 12),
            Expanded(child: Text(message, style: const TextStyle(color: VaTheme.textMuted))),
          ],
        ),
      );
}

class _PortalCard extends StatelessWidget {
  const _PortalCard({required this.row, required this.onEdit});
  final Map<String, dynamic> row;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final id = (row['id'] as num?)?.toInt() ?? 0;
    final name = '${row['name'] ?? 'Portal'}';
    final baseUrl = '${row['base_url'] ?? ''}';
    final session = '${row['session_status'] ?? 'empty'}';
    final credentials = row['credentials_configured'] == true;
    final error = '${row['last_error'] ?? ''}'.trim();
    return Material(
      color: VaTheme.surface,
      borderRadius: BorderRadius.circular(18),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.language_rounded, color: VaTheme.primary),
                const SizedBox(width: 10),
                Expanded(child: Text(name, style: const TextStyle(fontWeight: FontWeight.w900))),
                IconButton(
                  tooltip: 'Edit portal',
                  onPressed: onEdit,
                  icon: const Icon(Icons.edit_outlined, size: 19),
                ),
                Text(session, style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
              ],
            ),
            if (baseUrl.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(baseUrl, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: VaTheme.textMuted)),
            ],
            const SizedBox(height: 8),
            Row(
              children: [
                Icon(credentials ? Icons.lock_rounded : Icons.lock_open_rounded, size: 16, color: credentials ? VaTheme.primary : VaTheme.textMuted),
                const SizedBox(width: 6),
                Expanded(child: Text(credentials ? 'Encrypted credentials configured' : 'No stored credentials', style: const TextStyle(fontSize: 12))),
                TextButton(
                  onPressed: id <= 0 ? null : () => _showCredentials(context, id, name),
                  child: Text(credentials ? 'Replace' : 'Add login'),
                ),
              ],
            ),
            if (error.isNotEmpty)
              Text(error, style: const TextStyle(fontSize: 12, color: Colors.orangeAccent)),
          ],
        ),
      ),
    );
  }

  Future<void> _showCredentials(BuildContext context, int portalId, String portalName) async {
    final username = TextEditingController();
    final password = TextEditingController();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Login for $portalName'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: username, decoration: const InputDecoration(labelText: 'Username/email'), autocorrect: false),
            TextField(controller: password, decoration: const InputDecoration(labelText: 'Password'), obscureText: true, enableSuggestions: false, autocorrect: false),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save encrypted login')),
        ],
      ),
    );
    if (accepted != true || !context.mounted) return;
    try {
      await context.read<AppState>().updateBrowserCredentials(portalId, username.text, password.text);
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Portal login updated.')));
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Credential update failed: $error')));
    }
  }
}

class _BrowserOperationCard extends StatelessWidget {
  const _BrowserOperationCard({required this.row});
  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final id = (row['id'] as num?)?.toInt() ?? 0;
    final title = '${row['title'] ?? 'Portal operation'}';
    final status = '${row['status'] ?? 'pending'}';
    final challenge = '${row['challenge_type'] ?? ''}';
    final prompt = '${row['challenge_prompt'] ?? row['last_error'] ?? ''}'.trim();
    final approval = row['material_approval_required'] == true;
    final url = '${row['last_url'] ?? ''}';
    final verified = status == 'verified';

    return Material(
      color: VaTheme.surface,
      borderRadius: BorderRadius.circular(18),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(verified ? Icons.verified_rounded : Icons.web_asset_rounded, color: verified ? VaTheme.primary : VaTheme.textMuted),
                const SizedBox(width: 10),
                Expanded(child: Text(title, style: const TextStyle(fontWeight: FontWeight.w900))),
                Text(status.replaceAll('_', ' '), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12)),
              ],
            ),
            if (url.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(url, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
            ],
            if (prompt.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(prompt, style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
            ],
            if (id > 0 && (approval || status == 'needs_user_auth')) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (approval)
                    FilledButton.icon(
                      onPressed: () => _approve(context, id),
                      icon: const Icon(Icons.verified_user_rounded, size: 18),
                      label: const Text('Approve material action'),
                    ),
                  if (status == 'needs_user_auth' && challenge == 'otp')
                    OutlinedButton.icon(
                      onPressed: () => _enterCode(context, id),
                      icon: const Icon(Icons.password_rounded, size: 18),
                      label: const Text('Enter code'),
                    ),
                  if (status == 'needs_user_auth' && challenge != 'otp' && challenge != 'captcha')
                    OutlinedButton.icon(
                      onPressed: () => _resume(context, id),
                      icon: const Icon(Icons.refresh_rounded, size: 18),
                      label: const Text('Recheck authentication'),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _enterCode(BuildContext context, int id) async {
    final code = TextEditingController();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('One-time authentication code'),
        content: TextField(
          controller: code,
          keyboardType: TextInputType.number,
          autofocus: true,
          obscureText: true,
          decoration: const InputDecoration(labelText: 'Code'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, code.text.trim().isNotEmpty), child: const Text('Continue')),
        ],
      ),
    );
    if (accepted != true || !context.mounted) return;
    try {
      await context.read<AppState>().submitBrowserAuthCode(id, code.text.trim());
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Authentication failed: $error')));
    }
  }

  Future<void> _resume(BuildContext context, int id) async {
    try {
      await context.read<AppState>().resumeBrowserOperation(id);
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Portal resume failed: $error')));
    }
  }

  Future<void> _approve(BuildContext context, int id) async {
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Approve material portal action?'),
        content: const Text('This action may create a payment, contractual commitment, or security/account change. Approval is intentionally required before the browser can continue.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Do not approve')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Approve once')),
        ],
      ),
    );
    if (accepted != true || !context.mounted) return;
    try {
      await context.read<AppState>().approveBrowserOperation(id);
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Approval failed: $error')));
    }
  }
}


class _DocumentsView extends StatefulWidget {
  const _DocumentsView();

  @override
  State<_DocumentsView> createState() => _DocumentsViewState();
}

class _DocumentsViewState extends State<_DocumentsView> {
  final searchController = TextEditingController();
  String filter = 'All';

  @override
  void dispose() {
    searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final obligations = state.documentObligations;
    final ownership = state.documentOwnershipStatus;
    final query = searchController.text.trim().toLowerCase();
    final rows = state.documents.where((row) {
      final name = '${row['name'] ?? ''}'.toLowerCase();
      final category = '${row['category'] ?? ''}'.toLowerCase();
      final matchesQuery = query.isEmpty || name.contains(query) || category.contains(query);
      final matchesFilter = switch (filter) {
        'Finance' => category.contains('financ') || category.contains('geld') || category.contains('bill'),
        'Purchase' => category.contains('purchase') || category.contains('order') || category.contains('receipt'),
        'Important' => category.contains('important') || category.contains('legal') || category.contains('contract') || category.contains('tax') || category.contains('medical'),
        _ => true,
      };
      return matchesQuery && matchesFilter;
    }).toList();

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: searchController,
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                    hintText: 'Search documents…',
                    prefixIcon: Icon(Icons.search_rounded),
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                tooltip: 'Clean document archive',
                onPressed: state.busy ? null : () => _cleanup(context),
                icon: const Icon(Icons.auto_delete_outlined),
              ),
            ],
          ),
          const SizedBox(height: 10),
          SizedBox(
            height: 38,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                for (final option in const ['All', 'Finance', 'Purchase', 'Important']) ...[
                  ChoiceChip(
                    label: Text(option),
                    selected: filter == option,
                    onSelected: (_) => setState(() => filter = option),
                  ),
                  const SizedBox(width: 7),
                ],
              ],
            ),
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(18),
              color: VaTheme.surface,
              border: Border.all(color: VaTheme.primary.withValues(alpha: .24)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.fact_check_outlined, color: VaTheme.primary),
                    const SizedBox(width: 9),
                    const Expanded(child: Text('Document ownership', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 16))),
                    IconButton.filledTonal(
                      tooltip: 'Reconcile documents, forms and deadlines now',
                      onPressed: state.busy ? null : () => _reconcileOwnership(context),
                      icon: const Icon(Icons.sync_rounded),
                    ),
                    IconButton.filledTonal(
                      tooltip: 'Add verified profile fact for form filling',
                      onPressed: state.busy ? null : () => _addProfileFact(context),
                      icon: const Icon(Icons.person_add_alt_1_rounded),
                    ),
                  ],
                ),
                const SizedBox(height: 7),
                Text(
                  '${ownership['total_obligations'] ?? obligations.length} obligation${(ownership['total_obligations'] ?? obligations.length) == 1 ? '' : 's'} · ${state.documentProfileFacts.length} verified profile fact${state.documentProfileFacts.length == 1 ? '' : 's'}',
                  style: const TextStyle(color: VaTheme.textMuted),
                ),
                if ('${ownership['next_due_at'] ?? ''}'.trim().isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text('Next due ${_formatDocumentDue('${ownership['next_due_at']}')}', style: const TextStyle(fontWeight: FontWeight.w700)),
                ],
              ],
            ),
          ),
          if (obligations.isNotEmpty) ...[
            const SizedBox(height: 10),
            for (final obligation in obligations.take(8)) ...[
              _DocumentObligationCard(row: obligation),
              const SizedBox(height: 8),
            ],
          ],
          const SizedBox(height: 12),
          if (rows.isEmpty)
            SizedBox(
              height: MediaQuery.sizeOf(context).height * .48,
              child: EmptyState(
                icon: Icons.folder_outlined,
                title: state.documents.isEmpty ? 'No saved documents' : 'No matching documents',
                message: state.documents.isEmpty
                    ? 'Only useful invoices, receipts, contracts, statements and other durable records are archived. Boilerplate Terms of Service and policy attachments are ignored.'
                    : 'Try another search or filter.',
              ),
            )
          else
            for (final row in rows) ...[
              _DocumentCard(row: row),
              const SizedBox(height: 9),
            ],
        ],
      ),
    );
  }

  static String _formatDocumentDue(String raw) {
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return raw;
    return DateFormat('d MMM yyyy').format(parsed.toLocal());
  }

  Future<void> _reconcileOwnership(BuildContext context) async {
    try {
      final result = await context.read<AppState>().reconcileDocumentsNow();
      if (!context.mounted) return;
      final analyzed = (result['documents_analyzed'] as num?)?.toInt() ?? 0;
      final completed = (result['completed'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Document ownership reconciled · $analyzed analyzed · $completed completed.')),
      );
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _addProfileFact(BuildContext context) async {
    final keyController = TextEditingController();
    final valueController = TextEditingController();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Add verified profile fact'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Used only for form filling. Examples: phone, address, postal_code, city, date_of_birth.'),
            const SizedBox(height: 12),
            TextField(controller: keyController, decoration: const InputDecoration(labelText: 'Fact key')),
            const SizedBox(height: 8),
            TextField(controller: valueController, decoration: const InputDecoration(labelText: 'Value')),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save')),
        ],
      ),
    );
    if (accepted != true || !context.mounted) return;
    final key = keyController.text.trim();
    final value = valueController.text.trim();
    keyController.dispose();
    valueController.dispose();
    if (key.isEmpty || value.isEmpty) return;
    try {
      await context.read<AppState>().setDocumentProfileFact(key, value);
    } catch (error) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _cleanup(BuildContext context) async {
    try {
      final result = await context.read<AppState>().cleanupDocuments();
      if (!context.mounted) return;
      final removed = (result['removed'] as num?)?.toInt() ?? 0;
      final failed = (result['failed'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            removed == 0 && failed == 0
                ? 'Document archive is already clean.'
                : 'Removed $removed low-value document${removed == 1 ? '' : 's'}${failed > 0 ? ' · $failed could not be removed yet' : ''}.',
          ),
        ),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}

class _DocumentObligationCard extends StatelessWidget {
  const _DocumentObligationCard({required this.row});

  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final status = '${row['status'] ?? 'detected'}';
    final dueRaw = '${row['due_at'] ?? ''}'.trim();
    final due = DateTime.tryParse(dueRaw);
    final dueText = due == null ? '' : DateFormat('d MMM yyyy').format(due.toLocal());
    final error = '${row['last_error'] ?? ''}'.trim();
    final form = '${row['obligation_type'] ?? ''}' == 'form';
    final material = row['material_commitment'] == true;
    final protected = row['protected'] == true;
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        color: VaTheme.surface,
        border: Border.all(color: status == 'completed' ? VaTheme.success.withValues(alpha: .35) : VaTheme.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(form ? Icons.assignment_turned_in_outlined : Icons.event_note_outlined, color: status == 'completed' ? VaTheme.success : VaTheme.primary),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${row['title'] ?? 'Document obligation'}', style: const TextStyle(fontWeight: FontWeight.w900)),
                const SizedBox(height: 4),
                Text(
                  '${form ? 'Form' : 'Deadline'} · ${status.replaceAll('_', ' ')}${dueText.isEmpty ? '' : ' · due $dueText'}',
                  style: const TextStyle(color: VaTheme.textMuted),
                ),
                if (material || protected) ...[
                  const SizedBox(height: 5),
                  Text(
                    [if (protected) 'Protected', if (material) 'Material decision'].join(' · '),
                    style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                  ),
                ],
                if (error.isNotEmpty) ...[
                  const SizedBox(height: 5),
                  Text(error, maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(color: VaTheme.warning, fontSize: 12)),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}


class _DocumentCard extends StatelessWidget {
  const _DocumentCard({required this.row});

  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final name = '${row['name'] ?? ''}';
    final category = '${row['category'] ?? 'General'}';
    final url = '${row['drive_web_url'] ?? ''}';
    final extension = name.contains('.') ? name.split('.').last.toUpperCase() : 'FILE';
    final accent = _accent(category);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: url.isEmpty ? null : () => launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication),
        child: Ink(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            gradient: LinearGradient(
              colors: [accent.withValues(alpha: .12), VaTheme.surface],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
            border: Border.all(color: accent.withValues(alpha: .30)),
          ),
          child: Row(
            children: [
              Container(
                width: 46,
                height: 52,
                decoration: BoxDecoration(
                  color: accent.withValues(alpha: .22),
                  borderRadius: BorderRadius.circular(13),
                ),
                child: Stack(
                  alignment: Alignment.center,
                  children: [
                    Icon(Icons.description_rounded, color: accent, size: 30),
                    Positioned(
                      bottom: 5,
                      child: Text(extension.length > 4 ? extension.substring(0, 4) : extension, style: const TextStyle(fontSize: 7, fontWeight: FontWeight.w900)),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 13),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(name, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w800)),
                    const SizedBox(height: 4),
                    Text('$category · ${row['account_scope'] ?? 'personal'}', style: const TextStyle(color: VaTheme.textMuted)),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(_size(row['size_bytes']), style: const TextStyle(fontWeight: FontWeight.w700)),
                  if (url.isNotEmpty) ...[
                    const SizedBox(height: 5),
                    const Icon(Icons.open_in_new_rounded, size: 16, color: VaTheme.textMuted),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Color _accent(String category) {
    final value = category.toLowerCase();
    if (value.contains('financ') || value.contains('bill')) return VaTheme.success;
    if (value.contains('purchase') || value.contains('order') || value.contains('receipt')) return VaTheme.warning;
    if (value.contains('important') || value.contains('legal') || value.contains('contract') || value.contains('tax')) return VaTheme.primary;
    return VaTheme.secondary;
  }

  String _size(dynamic value) {
    final bytes = (value as num?)?.toInt() ?? int.tryParse('$value') ?? 0;
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
        message: 'Only source-backed physical orders and delivery updates are shown here. Payment receipts stay in the financial/email ledgers instead.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: rows.length,
      itemBuilder: (context, index) {
        final row = rows[index];
        final id = (row['id'] as num?)?.toInt() ?? 0;
        final delivery = DateTime.tryParse('${row['expected_delivery_at'] ?? ''}');
        final reason = '${row['classification_reason'] ?? ''}'.trim();
        return Card(
          child: ListTile(
            leading: const Icon(Icons.inventory_2_outlined),
            title: Text('${row['merchant']} · ${row['order_number']}'),
            subtitle: Text([
              'Status: ${row['status']}',
              if (row['total_amount'] != null) money(row['total_amount'], '${row['currency'] ?? 'EUR'}'),
              if (delivery != null) 'Expected ${DateFormat('dd MMM yyyy').format(delivery)}',
              if (reason.isNotEmpty) 'Evidence: $reason',
            ].join('\n')),
            onTap: '${row['tracking_url'] ?? ''}'.isEmpty
                ? null
                : () => launchUrl(Uri.parse('${row['tracking_url']}'), mode: LaunchMode.externalApplication),
            trailing: id <= 0
                ? null
                : PopupMenuButton<String>(
                    tooltip: 'Order actions',
                    onSelected: (value) {
                      if (value == 'not_order') _dismissOrder(context, id, row);
                    },
                    itemBuilder: (_) => const [
                      PopupMenuItem(
                        value: 'not_order',
                        child: ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(Icons.receipt_long_outlined),
                          title: Text('Not an order'),
                          subtitle: Text('Keep as receipt/payment evidence'),
                        ),
                      ),
                    ],
                  ),
          ),
        );
      },
    );
  }

  Future<void> _dismissOrder(BuildContext context, int id, Map<String, dynamic> row) async {
    final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('Mark this as not an order?'),
            content: Text(
              '${row['merchant']} · ${row['order_number']} will stop being treated as a parcel/order. '
              'The original Gmail/payment evidence is kept; only the fulfillment/tracking classification is dismissed.',
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Not an order')),
            ],
          ),
        ) ??
        false;
    if (!confirmed || !context.mounted) return;
    try {
      await context.read<AppState>().dismissOrder(id);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Removed from order tracking. Receipt/payment evidence was kept.')),
        );
      }
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Could not reclassify order: $error')));
      }
    }
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

class _RelationshipsView extends StatelessWidget {
  const _RelationshipsView();

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final rows = state.relationships;
    final status = state.relationshipStatus;
    final waiting = (status['waiting_on_counterparty'] as num?)?.toInt() ?? 0;
    final due = (status['followups_due'] as num?)?.toInt() ?? 0;
    final identities = (status['identities'] as num?)?.toInt() ?? 0;
    final interactions = (status['interactions'] as num?)?.toInt() ?? 0;
    final lastError = '${status['last_error'] ?? ''}'.trim();

    return RefreshIndicator(
      onRefresh: () => context.read<AppState>().refreshAll(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 24),
        children: [
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(18),
              color: VaTheme.surface,
              border: Border.all(color: VaTheme.primary.withValues(alpha: .24)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.people_alt_rounded, color: VaTheme.primary),
                    const SizedBox(width: 9),
                    const Expanded(
                      child: Text('Relationship memory', style: TextStyle(fontWeight: FontWeight.w900, fontSize: 16)),
                    ),
                    IconButton.filledTonal(
                      tooltip: 'Reconcile relationship memory now',
                      onPressed: state.busy ? null : () => _reconcile(context),
                      icon: const Icon(Icons.sync_rounded),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  '${rows.length} people · $identities identities · $interactions interactions',
                  style: const TextStyle(color: VaTheme.textMuted),
                ),
                const SizedBox(height: 4),
                Text(
                  '$waiting waiting on someone · $due follow-up${due == 1 ? '' : 's'} due',
                  style: TextStyle(
                    color: due > 0 ? VaTheme.warning : VaTheme.textMuted,
                    fontWeight: due > 0 ? FontWeight.w800 : FontWeight.normal,
                  ),
                ),
                if (lastError.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(lastError, style: const TextStyle(color: VaTheme.warning, fontWeight: FontWeight.w700)),
                ],
              ],
            ),
          ),
          const SizedBox(height: 12),
          if (rows.isEmpty)
            SizedBox(
              height: MediaQuery.sizeOf(context).height * .48,
              child: const EmptyState(
                icon: Icons.person_search_rounded,
                title: 'No relationship memory yet',
                message: 'The VA builds this automatically from verified Google Contacts, Gmail, device communications, and Calendar identities.',
              ),
            )
          else
            for (final row in rows) ...[
              _RelationshipCard(row: row),
              const SizedBox(height: 9),
            ],
        ],
      ),
    );
  }

  Future<void> _reconcile(BuildContext context) async {
    try {
      final result = await context.read<AppState>().reconcileRelationshipsNow();
      if (!context.mounted) return;
      final profiles = (result['profiles'] as num?)?.toInt() ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Relationship memory reconciled · $profiles profile${profiles == 1 ? '' : 's'} verified.')),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}

class _RelationshipCard extends StatelessWidget {
  const _RelationshipCard({required this.row});

  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final name = '${row['display_name'] ?? ''}'.trim();
    final email = '${row['primary_email'] ?? ''}'.trim();
    final phone = '${row['primary_phone'] ?? ''}'.trim();
    final organization = '${row['organization'] ?? ''}'.trim();
    final channel = '${row['preferred_channel'] ?? ''}'.trim();
    final summary = '${row['memory_summary'] ?? ''}'.trim();
    final count = (row['interaction_count'] as num?)?.toInt() ?? 0;
    final score = (row['engagement_score'] as num?)?.toInt() ?? 0;
    final waiting = row['waiting_on_counterparty'] == true;
    final nextFollowUp = _formatDate('${row['next_follow_up_at'] ?? ''}');
    final lastInteraction = _formatDate('${row['last_interaction_at'] ?? ''}');
    final title = name.isNotEmpty ? name : (email.isNotEmpty ? email : phone);

    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => _openDetail(context),
        child: Ink(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            color: VaTheme.surface,
            border: Border.all(color: waiting ? VaTheme.primary.withValues(alpha: .36) : VaTheme.border),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              CircleAvatar(
                backgroundColor: VaTheme.primary.withValues(alpha: .12),
                child: Text(
                  title.isEmpty ? '?' : title.substring(0, 1).toUpperCase(),
                  style: const TextStyle(color: VaTheme.primary, fontWeight: FontWeight.w900),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(child: Text(title.isEmpty ? 'Unnamed relationship' : title, style: const TextStyle(fontWeight: FontWeight.w900))),
                        if (waiting)
                          const Tooltip(
                            message: 'The VA is waiting on this person',
                            child: Icon(Icons.hourglass_top_rounded, size: 17, color: VaTheme.primary),
                          ),
                      ],
                    ),
                    if (organization.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(organization, style: const TextStyle(color: VaTheme.textMuted)),
                    ],
                    if (email.isNotEmpty || phone.isNotEmpty) ...[
                      const SizedBox(height: 5),
                      Text(
                        [if (email.isNotEmpty) email, if (phone.isNotEmpty) phone].join(' · '),
                        style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
                      ),
                    ],
                    const SizedBox(height: 7),
                    Text(
                      '$count interaction${count == 1 ? '' : 's'}${channel.isEmpty ? '' : ' · $channel'} · activity $score/100',
                      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                    ),
                    if (lastInteraction.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text('Last contact $lastInteraction', style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
                    ],
                    if (nextFollowUp.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text('Next follow-up $nextFollowUp', style: const TextStyle(color: VaTheme.warning, fontSize: 12, fontWeight: FontWeight.w800)),
                    ],
                    if (summary.isNotEmpty) ...[
                      const SizedBox(height: 7),
                      Text(summary, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(color: VaTheme.textMuted)),
                    ],
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: VaTheme.textMuted),
            ],
          ),
        ),
      ),
    );
  }

  static String _formatDate(String raw) {
    if (raw.isEmpty || raw == 'null') return '';
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return raw;
    return DateFormat('d MMM · HH:mm').format(parsed.toLocal());
  }

  Future<void> _openDetail(BuildContext context) async {
    final id = (row['id'] as num?)?.toInt();
    if (id == null) return;
    try {
      final detail = await context.read<AppState>().relationshipDetail(id);
      if (!context.mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (sheetContext) => _RelationshipDetailSheet(detail: detail),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }
}

class _RelationshipDetailSheet extends StatelessWidget {
  const _RelationshipDetailSheet({required this.detail});

  final Map<String, dynamic> detail;

  @override
  Widget build(BuildContext context) {
    final identities = detail['identities'] is List ? List<dynamic>.from(detail['identities'] as List) : const <dynamic>[];
    final interactions = detail['recent_interactions'] is List ? List<dynamic>.from(detail['recent_interactions'] as List) : const <dynamic>[];
    final facts = detail['facts'] is List ? List<dynamic>.from(detail['facts'] as List) : const <dynamic>[];
    final name = '${detail['display_name'] ?? ''}'.trim();
    final email = '${detail['primary_email'] ?? ''}'.trim();
    final title = name.isNotEmpty ? name : (email.isNotEmpty ? email : 'Relationship');

    return SafeArea(
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: .78,
        maxChildSize: .94,
        minChildSize: .45,
        builder: (context, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 4, 18, 28),
          children: [
            Text(title, style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w900)),
            if ('${detail['organization'] ?? ''}'.trim().isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('${detail['organization']}', style: const TextStyle(color: VaTheme.textMuted)),
              ),
            const SizedBox(height: 18),
            Text('Verified identities', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
            const SizedBox(height: 6),
            if (identities.isEmpty)
              const Text('No verified identities stored.', style: TextStyle(color: VaTheme.textMuted))
            else
              for (final item in identities.whereType<Map>())
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  leading: Icon('${item['type']}' == 'phone' ? Icons.phone_outlined : Icons.alternate_email_rounded),
                  title: Text('${item['value'] ?? ''}'),
                  subtitle: Text('Source: ${item['source'] ?? 'observed'}'),
                ),
            if (facts.isNotEmpty) ...[
              const SizedBox(height: 14),
              Text('Source-backed facts', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
              const SizedBox(height: 6),
              for (final item in facts.whereType<Map>())
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  title: Text('${item['key'] ?? ''}'.replaceAll('_', ' ')),
                  subtitle: Text('${item['value'] ?? ''}\n${item['source_type'] ?? ''} · ${item['source_ref'] ?? ''}'),
                ),
            ],
            const SizedBox(height: 14),
            Text('Recent interactions', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
            const SizedBox(height: 6),
            if (interactions.isEmpty)
              const Text('No interaction history stored.', style: TextStyle(color: VaTheme.textMuted))
            else
              for (final item in interactions.whereType<Map>())
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(_channelIcon('${item['channel'] ?? ''}')),
                  title: Text('${item['subject'] ?? ''}'.trim().isEmpty ? '${item['channel'] ?? 'Interaction'}' : '${item['subject']}'),
                  subtitle: Text(
                    [
                      _formatTimestamp('${item['occurred_at'] ?? ''}'),
                      '${item['direction'] ?? ''}',
                      if ('${item['summary'] ?? ''}'.trim().isNotEmpty) '${item['summary']}',
                    ].where((value) => value.isNotEmpty).join(' · '),
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
          ],
        ),
      ),
    );
  }

  static IconData _channelIcon(String channel) => switch (channel) {
        'email' => Icons.email_outlined,
        'sms' => Icons.sms_outlined,
        'calendar' => Icons.event_outlined,
        _ => Icons.chat_bubble_outline_rounded,
      };

  static String _formatTimestamp(String raw) {
    final parsed = DateTime.tryParse(raw);
    return parsed == null ? '' : DateFormat('d MMM yyyy · HH:mm').format(parsed.toLocal());
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
