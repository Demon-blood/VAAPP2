import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme/va_theme.dart';
import 'relationship_preferences_page.dart';

class PeopleDirectoryPage extends StatefulWidget {
  const PeopleDirectoryPage({super.key});

  @override
  State<PeopleDirectoryPage> createState() => _PeopleDirectoryPageState();
}

class _PeopleDirectoryPageState extends State<PeopleDirectoryPage> {
  static const _device = MethodChannel('full_time_va/device');

  final _search = TextEditingController();
  Map<String, dynamic> _directory = const {};
  String _filter = 'all';
  bool _loading = true;
  bool _syncing = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    if (!mounted) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final query = Uri.encodeQueryComponent(_search.text.trim());
      final filter = Uri.encodeQueryComponent(_filter);
      final raw = await context.read<AppState>().api.getJson(
        '/api/relationships/directory?query=$query&filter=$filter&limit=2000',
      );
      if (!mounted) return;
      setState(() => _directory = Map<String, dynamic>.from(raw as Map));
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _syncContacts() async {
    if (_syncing) return;
    setState(() {
      _syncing = true;
      _error = null;
    });
    var phoneProcessed = 0;
    var googleProcessed = 0;
    String googleNote = '';
    try {
      final raw = await _device.invokeMapMethod<String, dynamic>('readPhoneContacts');
      final phone = Map<String, dynamic>.from(raw ?? const {});
      if (phone['granted'] != true) {
        await context.read<AppState>().requestCommunicationPermissions();
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Contacts permission is required. Grant Contacts access, then tap Sync contacts again.',
            ),
          ),
        );
        return;
      }

      final contacts = (phone['contacts'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
      phoneProcessed = contacts.length;
      final snapshotId = 'android-${DateTime.now().microsecondsSinceEpoch}';
      const chunkSize = 100;
      if (contacts.isEmpty) {
        await context.read<AppState>().api.postJson(
          '/api/relationships/directory/device-contacts',
          {
            'snapshot_id': snapshotId,
            'contacts': <Map<String, dynamic>>[],
            'snapshot_complete': true,
          },
        );
      } else {
        for (var start = 0; start < contacts.length; start += chunkSize) {
          final end = (start + chunkSize < contacts.length)
              ? start + chunkSize
              : contacts.length;
          await context.read<AppState>().api.postJson(
            '/api/relationships/directory/device-contacts',
            {
              'snapshot_id': snapshotId,
              'contacts': contacts.sublist(start, end),
              'snapshot_complete': end == contacts.length,
            },
          );
        }
      }

      try {
        final google = await context.read<AppState>().api.postJson(
          '/api/relationships/directory/sync-google',
        );
        if (google is Map) {
          googleProcessed = (google['processed'] as num?)?.toInt() ?? 0;
        }
      } catch (error) {
        googleNote = ' · Google Contacts not refreshed: $error';
      }
      await _load();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'People updated · $phoneProcessed phone contact${phoneProcessed == 1 ? '' : 's'}'
            ' · $googleProcessed Google contact${googleProcessed == 1 ? '' : 's'}$googleNote',
          ),
          duration: const Duration(seconds: 6),
        ),
      );
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final people = (_directory['people'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
    return Scaffold(
      appBar: AppBar(
        title: const Text('People'),
        actions: [
          IconButton(
            tooltip: 'Sync phone book and Google Contacts',
            onPressed: _syncing ? null : _syncContacts,
            icon: _syncing
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.sync_rounded),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 28),
          children: [
            _SummaryCard(
              total: (_directory['total'] as num?)?.toInt() ?? people.length,
              configured: (_directory['configured'] as num?)?.toInt() ?? 0,
              phoneBook: (_directory['phone_book'] as num?)?.toInt() ?? 0,
              google: (_directory['google_contacts'] as num?)?.toInt() ?? 0,
              syncing: _syncing,
              onSync: _syncContacts,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _search,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => _load(),
              onChanged: (value) {
                if (value.isEmpty) _load();
              },
              decoration: InputDecoration(
                hintText: 'Search name, phone, email, company or group…',
                prefixIcon: const Icon(Icons.search_rounded),
                suffixIcon: _search.text.isEmpty
                    ? null
                    : IconButton(
                        tooltip: 'Clear search',
                        onPressed: () {
                          _search.clear();
                          _load();
                        },
                        icon: const Icon(Icons.close_rounded),
                      ),
              ),
            ),
            const SizedBox(height: 10),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  _filterChip('all', 'All'),
                  _filterChip('configured', 'Personalized'),
                  _filterChip('unconfigured', 'Not configured'),
                  _filterChip('favorites', 'Favorites'),
                ],
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 10),
              Card(
                child: ListTile(
                  leading: const Icon(Icons.error_outline, color: VaTheme.warning),
                  title: const Text(
                    'People directory needs attention',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                  subtitle: Text(_error!),
                  trailing: TextButton(onPressed: _load, child: const Text('Retry')),
                ),
              ),
            ],
            const SizedBox(height: 10),
            if (_loading && people.isEmpty)
              const Padding(
                padding: EdgeInsets.all(28),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (people.isEmpty)
              const Card(
                child: ListTile(
                  leading: Icon(Icons.person_search_outlined),
                  title: Text('No people match this view'),
                  subtitle: Text(
                    'Sync contacts or choose another filter. Names are never merged automatically without a matching phone number or email address.',
                  ),
                ),
              )
            else
              ..._buildAlphabeticalList(people),
          ],
        ),
      ),
    );
  }

  Widget _filterChip(String value, String label) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: ChoiceChip(
        label: Text(label),
        selected: _filter == value,
        onSelected: (_) {
          setState(() => _filter = value);
          _load();
        },
      ),
    );
  }

  List<Widget> _buildAlphabeticalList(List<Map<String, dynamic>> people) {
    final widgets = <Widget>[];
    String previousLetter = '';
    for (final person in people) {
      final name = _name(person);
      final first = name.trim().isEmpty ? '#' : name.trim()[0].toUpperCase();
      final letter = RegExp(r'[A-ZÀ-ÖØ-Þ]').hasMatch(first) ? first : '#';
      if (letter != previousLetter) {
        previousLetter = letter;
        widgets.add(
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 10, 4, 5),
            child: Text(
              letter,
              style: const TextStyle(
                color: VaTheme.textMuted,
                fontWeight: FontWeight.w900,
                fontSize: 13,
              ),
            ),
          ),
        );
      }
      widgets.add(_PersonCard(person: person, onOpen: () => _openPerson(person)));
      widgets.add(const SizedBox(height: 7));
    }
    return widgets;
  }

  Future<void> _openPerson(Map<String, dynamic> person) async {
    final id = (person['relationship_id'] as num?)?.toInt();
    if (id == null || person['configurable'] != true) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'This contact has no stable phone number or email identity yet, so personalized replies cannot be bound safely.',
          ),
        ),
      );
      return;
    }
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => RelationshipPreferencesPage(
          relationshipId: id,
          relationshipName: _name(person),
        ),
      ),
    );
    if (mounted) await _load();
  }

  String _name(Map<String, dynamic> person) {
    final display = '${person['display_name'] ?? ''}'.trim();
    if (display.isNotEmpty) return display;
    final email = '${person['primary_email'] ?? ''}'.trim();
    if (email.isNotEmpty) return email;
    final phone = '${person['primary_phone'] ?? ''}'.trim();
    return phone.isEmpty ? 'Unnamed contact' : phone;
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({
    required this.total,
    required this.configured,
    required this.phoneBook,
    required this.google,
    required this.syncing,
    required this.onSync,
  });

  final int total;
  final int configured;
  final int phoneBook;
  final int google;
  final bool syncing;
  final VoidCallback onSync;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(15),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.people_alt_rounded, color: VaTheme.primary),
                  const SizedBox(width: 9),
                  const Expanded(
                    child: Text(
                      'People & personalized replies',
                      style: TextStyle(fontSize: 17, fontWeight: FontWeight.w900),
                    ),
                  ),
                  FilledButton.tonalIcon(
                    onPressed: syncing ? null : onSync,
                    icon: const Icon(Icons.contacts_outlined, size: 18),
                    label: const Text('Sync contacts'),
                  ),
                ],
              ),
              const SizedBox(height: 7),
              const Text(
                'A name-first directory combining the Android phone book, Google Contacts and communication identities. Exact phone/email identity controls merging; group and relationship labels are context only.',
                style: TextStyle(color: VaTheme.textMuted, height: 1.35),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 14,
                runSpacing: 8,
                children: [
                  _metric('$total', 'people'),
                  _metric('$configured', 'personalized'),
                  _metric('$phoneBook', 'phone book'),
                  _metric('$google', 'Google'),
                ],
              ),
            ],
          ),
        ),
      );

  Widget _metric(String value, String label) => Text.rich(
        TextSpan(
          children: [
            TextSpan(
              text: value,
              style: const TextStyle(fontWeight: FontWeight.w900),
            ),
            TextSpan(
              text: ' $label',
              style: const TextStyle(color: VaTheme.textMuted),
            ),
          ],
        ),
      );
}

class _PersonCard extends StatelessWidget {
  const _PersonCard({required this.person, required this.onOpen});

  final Map<String, dynamic> person;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final name = _displayName();
    final configured = person['configured'] == true;
    final favorite = person['favorite'] == true;
    final configurable = person['configurable'] == true;
    final category = '${person['relationship_category'] ?? 'other'}';
    final suggested = '${person['suggested_category'] ?? 'other'}';
    final organization = '${person['organization'] ?? ''}'.trim();
    final title = '${person['job_title'] ?? ''}'.trim();
    final phone = '${person['primary_phone'] ?? ''}'.trim();
    final email = '${person['primary_email'] ?? ''}'.trim();
    final groups = (person['groups'] as List? ?? const []).map((e) => '$e').where((e) => e.isNotEmpty).toList();
    final sources = (person['source_types'] as List? ?? const []).map((e) => '$e').toSet();
    final initials = _initials(name);

    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onOpen,
        child: Padding(
          padding: const EdgeInsets.all(13),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              CircleAvatar(
                child: Text(initials, style: const TextStyle(fontWeight: FontWeight.w900)),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            name,
                            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w900),
                          ),
                        ),
                        if (favorite)
                          const Padding(
                            padding: EdgeInsets.only(left: 5),
                            child: Icon(Icons.star_rounded, size: 18, color: VaTheme.warning),
                          ),
                        const SizedBox(width: 6),
                        _status(configured, configurable),
                      ],
                    ),
                    if (organization.isNotEmpty || title.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(
                        [title, organization].where((value) => value.isNotEmpty).join(' · '),
                        style: const TextStyle(color: VaTheme.textMuted),
                      ),
                    ],
                    if (phone.isNotEmpty || email.isNotEmpty) ...[
                      const SizedBox(height: 5),
                      Text(
                        [phone, email].where((value) => value.isNotEmpty).join(' · '),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12),
                      ),
                    ],
                    const SizedBox(height: 7),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        if (configured)
                          _tag(category == 'other' ? 'Personalized' : category),
                        if (!configured && suggested != 'other')
                          _tag('Suggested: $suggested', muted: true),
                        if (sources.contains('android_contacts'))
                          _tag('Phone', muted: true),
                        if (sources.contains('google_contacts'))
                          _tag('Google', muted: true),
                        for (final group in groups.take(2)) _tag(group, muted: true),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              Icon(
                configurable ? Icons.chevron_right_rounded : Icons.link_off_rounded,
                color: VaTheme.textMuted,
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _status(bool configured, bool configurable) {
    if (!configurable) {
      return const Tooltip(
        message: 'No stable email/phone identity',
        child: Icon(Icons.info_outline_rounded, size: 18, color: VaTheme.textMuted),
      );
    }
    return Icon(
      configured ? Icons.tune_rounded : Icons.tune_outlined,
      size: 18,
      color: configured ? VaTheme.primary : VaTheme.textMuted,
    );
  }

  Widget _tag(String label, {bool muted = false}) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(999),
          color: muted
              ? VaTheme.textMuted.withValues(alpha: .10)
              : VaTheme.primary.withValues(alpha: .12),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w700,
            color: muted ? VaTheme.textMuted : VaTheme.primary,
          ),
        ),
      );

  String _displayName() {
    final name = '${person['display_name'] ?? ''}'.trim();
    if (name.isNotEmpty) return name;
    final email = '${person['primary_email'] ?? ''}'.trim();
    if (email.isNotEmpty) return email;
    final phone = '${person['primary_phone'] ?? ''}'.trim();
    return phone.isEmpty ? 'Unnamed contact' : phone;
  }

  String _initials(String name) {
    final words = name.trim().split(RegExp(r'\s+')).where((part) => part.isNotEmpty).toList();
    if (words.isEmpty) return '?';
    if (words.length == 1) return words.first.substring(0, 1).toUpperCase();
    return '${words.first.substring(0, 1)}${words.last.substring(0, 1)}'.toUpperCase();
  }
}
