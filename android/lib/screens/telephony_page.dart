import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../services/api_client.dart';
import '../theme/va_theme.dart';

class TelephonyPage extends StatefulWidget {
  const TelephonyPage({super.key});

  @override
  State<TelephonyPage> createState() => _TelephonyPageState();
}

class _TelephonyPageState extends State<TelephonyPage> {
  Map<String, dynamic> _status = const {};
  List<Map<String, dynamic>> _calls = const [];
  final Map<String, String> _draftKeys = {};
  static const FlutterSecureStorage _draftStorage = FlutterSecureStorage();
  bool _loading = true;
  String? _error;



  String _stableDraftHash(String value) {
    var hash = 0;
    for (final unit in value.codeUnits) {
      hash = ((hash * 31) + unit) & 0x7fffffff;
    }
    return '${hash.toRadixString(16)}-${value.length}';
  }

  Future<String> _draftIdempotencyKey(String fingerprint) async {
    final cached = _draftKeys[fingerprint];
    if (cached != null) return cached;
    final storageKey = 'telephony_draft_${_stableDraftHash(fingerprint)}';
    try {
      final raw = await _draftStorage.read(key: storageKey);
      if (raw != null && raw.isNotEmpty) {
        final decoded = jsonDecode(raw);
        if (decoded is Map &&
            decoded['fingerprint'] == fingerprint &&
            '${decoded['idempotency_key'] ?? ''}'.isNotEmpty) {
          final key = '${decoded['idempotency_key']}';
          _draftKeys[fingerprint] = key;
          return key;
        }
      }
    } catch (_) {}
    final key = 'android-call:${DateTime.now().microsecondsSinceEpoch}:${_stableDraftHash(fingerprint)}';
    _draftKeys[fingerprint] = key;
    try {
      await _draftStorage.write(
        key: storageKey,
        value: jsonEncode({'fingerprint': fingerprint, 'idempotency_key': key}),
      );
    } catch (_) {}
    return key;
  }

  Future<void> _clearDraftIdempotencyKey(String fingerprint) async {
    _draftKeys.remove(fingerprint);
    try {
      await _draftStorage.delete(key: 'telephony_draft_${_stableDraftHash(fingerprint)}');
    } catch (_) {}
  }

  @override
  void initState() {
    super.initState();
    Future<void>.microtask(_refresh);
  }

  Future<void> _refresh() async {
    if (mounted) setState(() => _loading = true);
    try {
      final api = context.read<AppState>().api;
      final results = await Future.wait<dynamic>([
        api.getJson('/api/telephony/status'),
        api.getJson('/api/telephony/calls?limit=100'),
      ]);
      if (!mounted) return;
      setState(() {
        _status = Map<String, dynamic>.from((results[0] as Map?) ?? const {});
        _calls = (results[1] as List? ?? const [])
            .map((row) => Map<String, dynamic>.from(row as Map))
            .toList();
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '$error';
        _loading = false;
      });
    }
  }

  Future<void> _placeCall() async {
    final phone = TextEditingController();
    final purpose = TextEditingController();
    final expected = TextEditingController();
    var attempts = 3;
    final submit = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => StatefulBuilder(
            builder: (context, setDialogState) => AlertDialog(
              title: const Text('Place autonomous call'),
              content: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'The VA identifies itself as an automated assistant. It can handle routine coordination and information gathering, but it will stop for material payments, binding commitments, security/authentication, or medical/legal decisions.',
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      controller: phone,
                      keyboardType: TextInputType.phone,
                      decoration: const InputDecoration(labelText: 'Phone number · E.164 (+32…)'),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: purpose,
                      minLines: 2,
                      maxLines: 4,
                      decoration: const InputDecoration(labelText: 'Purpose of the call'),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: expected,
                      minLines: 2,
                      maxLines: 4,
                      decoration: const InputDecoration(labelText: 'Expected outcome / proof of completion'),
                    ),
                    const SizedBox(height: 10),
                    DropdownButtonFormField<int>(
                      initialValue: attempts,
                      decoration: const InputDecoration(labelText: 'Maximum bounded attempts'),
                      items: const [1, 2, 3, 4, 5]
                          .map((value) => DropdownMenuItem(value: value, child: Text('$value')))
                          .toList(),
                      onChanged: (value) => setDialogState(() => attempts = value ?? attempts),
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
                FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Call')),
              ],
            ),
          ),
        ) ??
        false;
    final target = phone.text.trim();
    final callPurpose = purpose.text.trim();
    final outcome = expected.text.trim();
    if (!submit || !mounted || target.isEmpty || callPurpose.isEmpty) return;

    final draftFingerprint = '$target\n$callPurpose\n$outcome\n$attempts';
    final idempotencyKey = await _draftIdempotencyKey(draftFingerprint);
    if (!mounted) return;
    try {
      await context.read<AppState>().api.postJson('/api/telephony/calls', {
        'target': target,
        'purpose': callPurpose,
        'expected_outcome': outcome,
        'idempotency_key': idempotencyKey,
        'max_attempts': attempts,
      });
      await _clearDraftIdempotencyKey(draftFingerprint);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Call intent persisted and dispatched through the configured telephony provider.')),
      );
      await _refresh();
    } on ApiException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('${error.message}\nRetrying this draft reuses the same persisted idempotency key.')),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('$error\nRetrying this draft reuses the same persisted idempotency key.')),
      );
    }
  }

  Future<void> _showCall(Map<String, dynamic> summary) async {
    final id = summary['id'];
    if (id is! int) return;
    try {
      final raw = await context.read<AppState>().api.getJson('/api/telephony/calls/$id');
      final call = Map<String, dynamic>.from((raw as Map?) ?? const {});
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: Text('${call['target_masked'] ?? 'Call'} · ${call['status'] ?? ''}'),
          content: SizedBox(
            width: 620,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${call['purpose'] ?? ''}', style: const TextStyle(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 8),
                  Text('Provider: ${call['provider_status'] ?? '—'}'),
                  Text('Objective verification: ${call['verification_status'] ?? 'unverified'}'),
                  Text('Attempt ${call['attempt'] ?? 1} / ${call['max_attempts'] ?? 1}'),
                  if ('${call['result_summary'] ?? ''}'.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text('Outcome: ${call['result_summary']}'),
                  ],
                  if (call['provider_completed'] == true && call['objective_verified'] != true) ...[
                    const SizedBox(height: 8),
                    const Text(
                      'Provider completed ≠ objective verified. A connected call can be a person, IVR, or voicemail.',
                      style: TextStyle(color: VaTheme.warning, fontWeight: FontWeight.w800),
                    ),
                  ],
                  if (call['needs_user'] == true) ...[
                    const SizedBox(height: 8),
                    Text('Needs you: ${call['needs_user_reason'] ?? ''}', style: const TextStyle(color: VaTheme.warning)),
                  ],
                  if ('${call['failure_reason'] ?? ''}'.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text('${call['failure_reason']}', style: const TextStyle(color: VaTheme.warning)),
                  ],
                  const Divider(height: 28),
                  const Text('Transcript', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w900)),
                  const SizedBox(height: 6),
                  for (final rawTurn in (call['turns'] as List? ?? const []))
                    _TranscriptTurn(turn: Map<String, dynamic>.from(rawTurn as Map)),
                  const Divider(height: 28),
                  const Text('Provider evidence', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w900)),
                  const SizedBox(height: 6),
                  for (final rawEvidence in (call['evidence'] as List? ?? const []))
                    _EvidenceRow(evidence: Map<String, dynamic>.from(rawEvidence as Map)),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () async {
                try {
                  await context.read<AppState>().api.postJson('/api/telephony/calls/$id/reconcile');
                  if (!dialogContext.mounted) return;
                  Navigator.pop(dialogContext);
                  await _refresh();
                } catch (_) {}
              },
              child: const Text('Reconcile provider'),
            ),
            FilledButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('Close')),
          ],
        ),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final configured = _status['configured'] == true;
    final available = _status['available'] == true;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 30),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(available ? Icons.phone_in_talk_rounded : Icons.phone_disabled_outlined,
                          color: available ? VaTheme.success : VaTheme.warning),
                      const SizedBox(width: 10),
                      const Expanded(child: Text('Calls / Telephony', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900))),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text('Provider: ${_status['provider'] ?? 'twilio'} · ${available ? 'ready' : configured ? 'disabled/incomplete' : 'not configured'}'),
                  if ('${_status['from_number'] ?? ''}'.isNotEmpty) Text('Caller number: ${_status['from_number']}'),
                  Text('Active calls: ${_status['active_calls'] ?? 0}'),
                  const SizedBox(height: 8),
                  const Text(
                    'Call recording is off. Speech transcripts are encrypted at rest. Signed provider callbacks are retained as evidence, and a completed PSTN call is never treated as task completion by itself.',
                  ),
                  if (!available) ...[
                    const SizedBox(height: 8),
                    const Text('Configure Twilio and the AI decision engine in the provider settings to enable real autonomous calls.', style: TextStyle(color: VaTheme.warning)),
                  ],
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      FilledButton.icon(
                        onPressed: available ? _placeCall : null,
                        icon: const Icon(Icons.add_call),
                        label: const Text('Place call'),
                      ),
                      OutlinedButton.icon(
                        onPressed: _loading
                            ? null
                            : () async {
                                try {
                                  await context.read<AppState>().api.postJson('/api/telephony/reconcile');
                                  await _refresh();
                                } catch (_) {}
                              },
                        icon: const Icon(Icons.sync_rounded),
                        label: const Text('Reconcile calls'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Card(
              child: ListTile(
                leading: const Icon(Icons.error_outline, color: VaTheme.warning),
                title: const Text('Telephony status could not be loaded'),
                subtitle: Text(_error!),
                trailing: IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh_rounded)),
              ),
            ),
          ],
          const SizedBox(height: 18),
          Row(
            children: [
              Expanded(child: Text('Call ownership', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900))),
              if (_loading) const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2)) else Text('${_calls.length}'),
            ],
          ),
          const SizedBox(height: 6),
          const Text('VAAPP keeps the objective open across ringing, conversation, retries, reconciliation, and counterparty verification.'),
          const SizedBox(height: 10),
          if (!_loading && _calls.isEmpty)
            const Card(child: ListTile(title: Text('No provider-backed telephone calls yet.')))
          else
            for (final call in _calls) _CallTile(call: call, onTap: () => _showCall(call)),
        ],
      ),
    );
  }
}

class _CallTile extends StatelessWidget {
  const _CallTile({required this.call, required this.onTap});

  final Map<String, dynamic> call;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final verified = call['objective_verified'] == true;
    final needsUser = call['needs_user'] == true;
    final providerOnly = call['provider_completed'] == true && !verified;
    final retryAt = DateTime.tryParse('${call['next_retry_at'] ?? ''}')?.toLocal();
    final retryText = retryAt == null
        ? ''
        : ' · retry ${retryAt.day.toString().padLeft(2, '0')}/${retryAt.month.toString().padLeft(2, '0')} ${retryAt.hour.toString().padLeft(2, '0')}:${retryAt.minute.toString().padLeft(2, '0')}';
    return Card(
      child: ListTile(
        onTap: onTap,
        leading: Icon(
          verified ? Icons.verified_rounded : needsUser || providerOnly ? Icons.warning_amber_rounded : Icons.phone_outlined,
          color: verified ? VaTheme.success : needsUser || providerOnly ? VaTheme.warning : VaTheme.secondary,
        ),
        title: Text('${call['target_masked'] ?? call['target'] ?? 'Call'} · ${call['status'] ?? ''}', style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text(
          '${call['purpose'] ?? ''}\nprovider ${call['provider_status'] ?? '—'} · verification ${call['verification_status'] ?? 'unverified'} · attempt ${call['attempt'] ?? 1}/${call['max_attempts'] ?? 1}$retryText',
          maxLines: 4,
          overflow: TextOverflow.ellipsis,
        ),
        isThreeLine: true,
        trailing: const Icon(Icons.chevron_right_rounded),
      ),
    );
  }
}

class _TranscriptTurn extends StatelessWidget {
  const _TranscriptTurn({required this.turn});

  final Map<String, dynamic> turn;

  @override
  Widget build(BuildContext context) {
    final speaker = '${turn['speaker'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(speaker == 'va' ? 'VA' : speaker == 'counterparty' ? 'Counterparty' : speaker, style: const TextStyle(fontWeight: FontWeight.w800)),
          Text('${turn['text'] ?? ''}'),
        ],
      ),
    );
  }
}

class _EvidenceRow extends StatelessWidget {
  const _EvidenceRow({required this.evidence});

  final Map<String, dynamic> evidence;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Text(
          '${evidence['event_type'] ?? ''} · ${evidence['provider_status'] ?? ''}${evidence['signature_verified'] == true ? ' · signed' : ''}',
        ),
      );
}
