import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';

class RelationshipPreferencesPage extends StatefulWidget {
  const RelationshipPreferencesPage({
    required this.relationshipId,
    required this.relationshipName,
    super.key,
  });

  final int relationshipId;
  final String relationshipName;

  @override
  State<RelationshipPreferencesPage> createState() => _RelationshipPreferencesPageState();
}

class _RelationshipPreferencesPageState extends State<RelationshipPreferencesPage> {
  final _instructions = TextEditingController();
  final _approvalTopics = TextEditingController();
  final _examples = TextEditingController();
  final _whatsAppAlias = TextEditingController();
  final _signalAlias = TextEditingController();
  final _telegramAlias = TextEditingController();
  final _messengerAlias = TextEditingController();
  final _messagesAlias = TextEditingController();

  bool _loading = true;
  bool _saving = false;
  bool _relearning = false;
  bool _learnFromHistory = false;
  Map<String, dynamic> _learnedStyle = const {};
  String? _error;
  String _language = 'auto';
  String _tone = 'neutral';
  String _formality = 'auto';
  String _greeting = 'auto';
  String _signoff = 'auto';
  String _verbosity = 'normal';
  String _preferredChannel = 'auto';
  String _relationshipCategory = 'other';
  String _routineAutoSend = 'default';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _instructions.dispose();
    _approvalTopics.dispose();
    _examples.dispose();
    _whatsAppAlias.dispose();
    _signalAlias.dispose();
    _telegramAlias.dispose();
    _messengerAlias.dispose();
    _messagesAlias.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final result = await context.read<AppState>().relationshipCommunicationPreferences(widget.relationshipId);
      final prefs = Map<String, dynamic>.from((result['preferences'] as Map?) ?? const {});
      final learnedStyle = Map<String, dynamic>.from((result['learned_style'] as Map?) ?? const {});
      if (!mounted) return;
      setState(() {
        _language = '${prefs['language'] ?? 'auto'}';
        _tone = '${prefs['tone'] ?? 'neutral'}';
        _formality = '${prefs['formality'] ?? 'auto'}';
        _greeting = '${prefs['greeting_style'] ?? 'auto'}';
        _signoff = '${prefs['signoff_style'] ?? 'auto'}';
        _verbosity = '${prefs['verbosity'] ?? 'normal'}';
        _preferredChannel = '${prefs['preferred_channel'] ?? 'auto'}';
        _relationshipCategory = '${prefs['relationship_category'] ?? 'other'}';
        final autoSend = prefs['routine_auto_send'];
        _routineAutoSend = autoSend == true ? 'allow' : autoSend == false ? 'review' : 'default';
        _learnFromHistory = prefs['learn_from_history'] == true;
        _learnedStyle = learnedStyle;
        _instructions.text = '${prefs['instructions'] ?? ''}';
        _approvalTopics.text = (prefs['approval_topics'] as List? ?? const []).join(', ');
        _examples.text = (prefs['examples'] as List? ?? const []).join('\n');
        final aliases = Map<String, dynamic>.from((prefs['channel_aliases'] as Map?) ?? const {});
        String joined(String channel) => (aliases[channel] as List? ?? const []).join(', ');
        _whatsAppAlias.text = joined('whatsapp');
        _signalAlias.text = joined('signal');
        _telegramAlias.text = joined('telegram');
        _messengerAlias.text = joined('messenger');
        _messagesAlias.text = joined('notification');
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

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final result = await context.read<AppState>().updateRelationshipCommunicationPreferences(
        widget.relationshipId,
        {
          'language': _language,
          'tone': _tone,
          'formality': _formality,
          'greeting_style': _greeting,
          'signoff_style': _signoff,
          'verbosity': _verbosity,
          'preferred_channel': _preferredChannel,
          'relationship_category': _relationshipCategory,
          'routine_auto_send': _routineAutoSend == 'allow'
              ? true
              : _routineAutoSend == 'review'
                  ? false
                  : null,
          'approval_topics': _approvalTopics.text
              .split(',')
              .map((value) => value.trim())
              .where((value) => value.isNotEmpty)
              .toList(),
          'instructions': _instructions.text.trim(),
          'examples': _examples.text
              .split('\n')
              .map((value) => value.trim())
              .where((value) => value.isNotEmpty)
              .toList(),
          'learn_from_history': _learnFromHistory,
          'channel_aliases': {
            'whatsapp': _aliasList(_whatsAppAlias.text),
            'signal': _aliasList(_signalAlias.text),
            'telegram': _aliasList(_telegramAlias.text),
            'messenger': _aliasList(_messengerAlias.text),
            'notification': _aliasList(_messagesAlias.text),
          },
        },
      );
      if (!mounted) return;
      setState(() {
        _learnedStyle = Map<String, dynamic>.from((result['learned_style'] as Map?) ?? const {});
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Reply preferences saved for this relationship.')),
      );
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }


  Future<void> _relearnStyle() async {
    setState(() {
      _relearning = true;
      _error = null;
    });
    try {
      final result = await context.read<AppState>().relearnRelationshipCommunicationStyle(widget.relationshipId);
      if (!mounted) return;
      setState(() => _learnedStyle = result);
      final count = result['sample_count'] ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Writing style refreshed from $count verified sent messages.')),
      );
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _relearning = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Reply preferences · ${widget.relationshipName}')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
              children: [
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: VaTheme.surface,
                    borderRadius: BorderRadius.circular(18),
                    border: Border.all(color: VaTheme.primary.withValues(alpha: .24)),
                  ),
                  child: const Text(
                    'These settings change how the VA communicates with this person. They do not grant payment, legal, browser, banking or other execution authority.',
                    style: TextStyle(color: VaTheme.textMuted),
                  ),
                ),
                const SizedBox(height: 16),
                _dropdown('Preferred language', _language, const {
                  'auto': 'Automatic',
                  'nl': 'Dutch',
                  'fr': 'French',
                  'en': 'English',
                  'de': 'German',
                }, (value) => setState(() => _language = value)),
                _dropdown('Tone', _tone, const {
                  'neutral': 'Neutral',
                  'friendly': 'Friendly',
                  'warm': 'Warm',
                  'direct': 'Direct',
                  'professional': 'Professional',
                }, (value) => setState(() => _tone = value)),
                _dropdown('Formality', _formality, const {
                  'auto': 'Automatic',
                  'informal': 'Informal',
                  'formal': 'Formal',
                }, (value) => setState(() => _formality = value)),
                _dropdown('Greeting style', _greeting, const {
                  'auto': 'Automatic',
                  'first_name': 'First name',
                  'hello': 'Hello / Hi',
                  'none': 'No greeting',
                }, (value) => setState(() => _greeting = value)),
                _dropdown('Sign-off style', _signoff, const {
                  'auto': 'Automatic',
                  'name': 'Name only',
                  'warm': 'Warm',
                  'professional': 'Professional',
                  'none': 'No sign-off',
                }, (value) => setState(() => _signoff = value)),
                _dropdown('Reply length', _verbosity, const {
                  'short': 'Short',
                  'normal': 'Normal',
                  'detailed': 'Detailed',
                }, (value) => setState(() => _verbosity = value)),
                _dropdown('Preferred channel', _preferredChannel, const {
                  'auto': 'Automatic',
                  'email': 'Email',
                  'sms': 'SMS',
                  'whatsapp': 'WhatsApp',
                  'signal': 'Signal',
                  'telegram': 'Telegram',
                  'messenger': 'Messenger',
                }, (value) => setState(() => _preferredChannel = value)),
                _dropdown('Relationship category', _relationshipCategory, const {
                  'partner': 'Partner',
                  'family': 'Family',
                  'friend': 'Friend',
                  'client': 'Client',
                  'provider': 'Provider',
                  'colleague': 'Colleague',
                  'other': 'Other',
                }, (value) => setState(() => _relationshipCategory = value)),
                _dropdown('Routine replies', _routineAutoSend, const {
                  'default': 'Use global safety policy',
                  'allow': 'Allow when otherwise safe',
                  'review': 'Always ask before sending',
                }, (value) => setState(() => _routineAutoSend = value)),
                SwitchListTile.adaptive(
                  contentPadding: EdgeInsets.zero,
                  value: _learnFromHistory,
                  onChanged: (value) => setState(() => _learnFromHistory = value),
                  title: const Text('Learn how I write to this person', style: TextStyle(fontWeight: FontWeight.w800)),
                  subtitle: const Text(
                    'Opt-in. Learns only from device-observed outgoing history after excluding known VA-generated replies; never from incoming messages. Explicit instructions and examples still win.',
                    style: TextStyle(color: VaTheme.textMuted, fontSize: 12),
                  ),
                ),
                if (_learnFromHistory) _learnedStyleCard(),
                const SizedBox(height: 8),
                Text('Messaging-app identity links', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
                const SizedBox(height: 4),
                const Text(
                  'Notification apps may expose only the display name. Add the exact names shown by each app to bind them explicitly to this relationship. The VA never merges people by name automatically.',
                  style: TextStyle(color: VaTheme.textMuted, fontSize: 12),
                ),
                const SizedBox(height: 10),
                _aliasField('WhatsApp displayed name(s)', _whatsAppAlias),
                _aliasField('Signal displayed name(s)', _signalAlias),
                _aliasField('Telegram displayed name(s)', _telegramAlias),
                _aliasField('Messenger displayed name(s)', _messengerAlias),
                _aliasField('Google/Samsung Messages notification name(s)', _messagesAlias),
                const SizedBox(height: 8),
                TextField(
                  controller: _approvalTopics,
                  decoration: const InputDecoration(
                    labelText: 'Topics that always require approval',
                    hintText: 'e.g. contract, school choice, travel dates',
                    helperText: 'Comma-separated. This can only make policy stricter.',
                  ),
                  maxLines: 2,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _instructions,
                  decoration: const InputDecoration(
                    labelText: 'Explicit reply instructions',
                    hintText: 'e.g. Keep messages concise and address them by first name.',
                  ),
                  maxLines: 4,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _examples,
                  decoration: const InputDecoration(
                    labelText: 'Example replies',
                    helperText: 'One example per line, up to 5 examples.',
                  ),
                  maxLines: 5,
                ),
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(_error!, style: const TextStyle(color: VaTheme.warning, fontWeight: FontWeight.w700)),
                ],
                const SizedBox(height: 20),
                FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: _saving
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.save_rounded),
                  label: Text(_saving ? 'Saving…' : 'Save reply preferences'),
                ),
              ],
            ),
    );
  }



  Widget _learnedStyleCard() {
    final savedEnabled = _learnedStyle['enabled'] == true;
    final ready = _learnedStyle['ready'] == true;
    final count = _learnedStyle['sample_count'] ?? 0;
    final style = Map<String, dynamic>.from((_learnedStyle['style'] as Map?) ?? const {});
    final channels = Map<String, dynamic>.from((style['channels'] as Map?) ?? const {});
    final channelText = channels.keys.isEmpty ? 'none yet' : channels.keys.join(', ');
    final summary = '${style['summary'] ?? ''}'.trim();
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: VaTheme.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: VaTheme.primary.withValues(alpha: .18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            ready ? 'Learned style ready · $count verified messages' : 'Learning style · $count verified messages',
            style: const TextStyle(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            !savedEnabled
                ? 'Save these preferences first to enable learning for this relationship.'
                : ready
                    ? 'Sources: $channelText${summary.isEmpty ? '' : ' · $summary'}'
                    : 'At least 3 safe verified sent messages are required. Android currently provides historical user-sent SMS; notification-only Messenger, WhatsApp, Telegram and Signal do not expose sent history, so explicit examples can supplement the learned relationship style.',
            style: const TextStyle(color: VaTheme.textMuted, fontSize: 12),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _relearning || !savedEnabled ? null : _relearnStyle,
            icon: _relearning
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.auto_awesome_rounded),
            label: Text(_relearning ? 'Learning…' : 'Relearn from verified sent messages'),
          ),
        ],
      ),
    );
  }

  List<String> _aliasList(String raw) => raw
      .split(',')
      .map((value) => value.trim())
      .where((value) => value.isNotEmpty)
      .toList();

  Widget _aliasField(String label, TextEditingController controller) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: TextField(
          controller: controller,
          decoration: InputDecoration(
            labelText: label,
            helperText: 'Comma-separated exact display names.',
          ),
          maxLines: 1,
        ),
      );

  Widget _dropdown(
    String label,
    String value,
    Map<String, String> options,
    ValueChanged<String> onChanged,
  ) {
    final safeValue = options.containsKey(value) ? value : options.keys.first;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DropdownButtonFormField<String>(
        initialValue: safeValue,
        decoration: InputDecoration(labelText: label),
        items: options.entries
            .map((entry) => DropdownMenuItem(value: entry.key, child: Text(entry.value)))
            .toList(),
        onChanged: _saving ? null : (next) => onChanged(next ?? safeValue),
      ),
    );
  }
}
