import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../services/mobile_deployment_service.dart';

class OnboardingPage extends StatefulWidget {
  const OnboardingPage({super.key});

  @override
  State<OnboardingPage> createState() => _OnboardingPageState();
}

class _OnboardingPageState extends State<OnboardingPage> {
  final _pairForm = GlobalKey<FormState>();
  final _deployForm = GlobalKey<FormState>();
  final _server = TextEditingController();
  final _secret = TextEditingController();
  final _device = TextEditingController(text: 'Full-Time VA Android');
  final _renderToken = TextEditingController();
  final _repository = TextEditingController();
  final _serviceName = TextEditingController(text: 'full-time-va');
  final _databaseUrl = TextEditingController();
  bool deploying = false;
  bool loadingWorkspaces = false;
  String? deploymentStatus;
  String deploymentMode = 'production';
  List<RenderWorkspace> workspaces = const [];
  String? selectedWorkspaceId;

  @override
  void dispose() {
    for (final controller in [_server, _secret, _device, _renderToken, _repository, _serviceName, _databaseUrl]) {
      controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Scaffold(
      appBar: AppBar(title: const Text('Full-Time VA setup')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Center(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(26),
                child: Image.asset('assets/app_icon.png', width: 96, height: 96, fit: BoxFit.cover),
              ),
            ),
            const SizedBox(height: 14),
            Text(
              'Your Full-Time VA',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 6),
            const Text(
              'Automated. Intelligent. Secure.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Color(0xFFA8B3C7), fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 10),
            const Text(
              'Deploy the private automation backend from this phone or pair with an existing deployment. External providers still use their official authorization pages.',
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            Card(
              child: ExpansionTile(
                initiallyExpanded: true,
                leading: const Icon(Icons.cloud_upload_outlined),
                title: const Text('Deploy backend from phone'),
                subtitle: const Text('Render API + your GitHub repository'),
                children: [Padding(padding: const EdgeInsets.all(16), child: _deploymentForm(state))],
              ),
            ),
            Card(
              child: ExpansionTile(
                leading: const Icon(Icons.link),
                title: const Text('Pair with existing backend'),
                children: [Padding(padding: const EdgeInsets.all(16), child: _pairingForm(state))],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _deploymentForm(AppState state) => Form(
        key: _deployForm,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Render must already be authorized to read the selected GitHub repository. The API key is used for deployment requests only and is not stored by the app.',
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: deploymentMode,
              decoration: const InputDecoration(labelText: 'Hosting mode'),
              items: const [
                DropdownMenuItem(
                  value: 'production',
                  child: Text('Always-on VA — paid Render service + persistent database'),
                ),
                DropdownMenuItem(
                  value: 'testing',
                  child: Text('Testing only — free service + temporary free database'),
                ),
              ],
              onChanged: deploying ? null : (value) => setState(() => deploymentMode = value ?? 'production'),
            ),
            const SizedBox(height: 8),
            Text(
              deploymentMode == 'production'
                  ? 'Uses Render Starter and Basic-256 MB Postgres. Render can require billing details.'
                  : 'Free hosting can sleep after inactivity and the free database is temporary. It is not suitable for a full-time VA.',
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _renderToken,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Render API key'),
              validator: _required,
              onChanged: (_) {
                if (workspaces.isNotEmpty) {
                  setState(() {
                    workspaces = const [];
                    selectedWorkspaceId = null;
                  });
                }
              },
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: loadingWorkspaces ? null : _loadWorkspaces,
              icon: loadingWorkspaces
                  ? const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.cloud_sync_outlined),
              label: const Text('Verify key and load workspaces'),
            ),
            if (workspaces.isNotEmpty) ...[
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: selectedWorkspaceId,
                decoration: const InputDecoration(labelText: 'Render workspace'),
                items: workspaces
                    .map(
                      (workspace) => DropdownMenuItem<String>(
                        value: workspace.id,
                        child: Text(workspace.email.isEmpty ? workspace.name : '${workspace.name} · ${workspace.email}'),
                      ),
                    )
                    .toList(),
                onChanged: (value) => setState(() => selectedWorkspaceId = value),
                validator: (value) => value == null || value.isEmpty ? 'Load and select a workspace.' : null,
              ),
            ],
            const SizedBox(height: 12),
            TextFormField(
              controller: _repository,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(labelText: 'GitHub repository URL'),
              validator: (value) {
                final uri = Uri.tryParse(value?.trim() ?? '');
                return uri == null || uri.scheme != 'https' || uri.host != 'github.com' ? 'Enter an HTTPS GitHub repository URL.' : null;
              },
            ),
            const SizedBox(height: 12),
            TextFormField(controller: _serviceName, decoration: const InputDecoration(labelText: 'Render service name'), validator: _required),
            const SizedBox(height: 12),
            TextFormField(
              controller: _databaseUrl,
              obscureText: true,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(
                labelText: 'Existing PostgreSQL DATABASE_URL (optional)',
                helperText: 'Leave empty and the app provisions a matching Render PostgreSQL database.',
              ),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  onPressed: () => launchUrl(Uri.parse('https://dashboard.render.com/u/settings#api-keys'), mode: LaunchMode.externalApplication),
                  icon: const Icon(Icons.key_outlined),
                  label: const Text('Open Render API settings'),
                ),
                OutlinedButton.icon(
                  onPressed: () => launchUrl(Uri.parse('https://dashboard.render.com/'), mode: LaunchMode.externalApplication),
                  icon: const Icon(Icons.open_in_browser),
                  label: const Text('Open Render repository access'),
                ),
              ],
            ),
            if (deploymentStatus != null) ...[
              const SizedBox(height: 12),
              Text(deploymentStatus!),
            ],
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: deploying || state.busy ? null : _deployAndPair,
              icon: deploying
                  ? const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.rocket_launch_outlined),
              label: const Text('Provision, deploy, verify and pair'),
            ),
          ],
        ),
      );

  Widget _pairingForm(AppState state) => Form(
        key: _pairForm,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextFormField(
              controller: _server,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(labelText: 'VA server HTTPS address'),
              validator: (value) {
                final uri = Uri.tryParse(value?.trim() ?? '');
                return uri == null || uri.scheme != 'https' || uri.host.isEmpty ? 'Enter a valid HTTPS address.' : null;
              },
            ),
            const SizedBox(height: 12),
            TextFormField(controller: _secret, obscureText: true, decoration: const InputDecoration(labelText: 'Pairing secret'), validator: _required),
            const SizedBox(height: 12),
            TextFormField(controller: _device, decoration: const InputDecoration(labelText: 'Device name'), validator: _required),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: state.busy ? null : _pair,
              icon: const Icon(Icons.link),
              label: const Text('Pair device'),
            ),
          ],
        ),
      );

  String? _required(String? value) => value == null || value.trim().isEmpty ? 'Required.' : null;

  Future<void> _loadWorkspaces() async {
    if (_renderToken.text.trim().isEmpty) {
      setState(() => deploymentStatus = 'Enter a Render API key first.');
      return;
    }
    setState(() {
      loadingWorkspaces = true;
      deploymentStatus = 'Verifying the Render API key…';
    });
    try {
      final loaded = await MobileDeploymentService().listWorkspaces(_renderToken.text);
      if (!mounted) return;
      setState(() {
        workspaces = loaded;
        selectedWorkspaceId = loaded.first.id;
        deploymentStatus = 'Render key verified. Select the workspace and continue.';
      });
    } catch (error) {
      if (mounted) setState(() => deploymentStatus = '$error');
    } finally {
      if (mounted) setState(() => loadingWorkspaces = false);
    }
  }

  Future<void> _pair() async {
    if (!_pairForm.currentState!.validate()) return;
    try {
      await context.read<AppState>().pair(serverUrl: _server.text, pairingSecret: _secret.text, deviceName: _device.text);
    } catch (_) {}
  }

  Future<void> _deployAndPair() async {
    if (!_deployForm.currentState!.validate()) return;
    if (deploymentMode == 'production') {
      final confirmed = await showDialog<bool>(
            context: context,
            builder: (dialogContext) => AlertDialog(
              title: const Text('Create paid always-on resources?'),
              content: const Text(
                'This will ask Render to create an always-on web service and a persistent PostgreSQL database. Render may charge the workspace according to its current pricing. Continue only after reviewing the selected workspace billing settings.',
              ),
              actions: [
                TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
                FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Create resources')),
              ],
            ),
          ) ??
          false;
      if (!confirmed) return;
    }
    if (selectedWorkspaceId == null) {
      setState(() => deploymentStatus = 'Verify the Render key and select a workspace first.');
      return;
    }
    setState(() {
      deploying = true;
      deploymentStatus = _databaseUrl.text.trim().isEmpty
          ? 'Provisioning the live database…'
          : 'Creating the live backend…';
    });
    try {
      final deployment = MobileDeploymentService();
      final result = await deployment.deployRender(
        apiToken: _renderToken.text,
        ownerId: selectedWorkspaceId!,
        repositoryUrl: _repository.text,
        serviceName: _serviceName.text,
        databaseUrl: _databaseUrl.text,
        deploymentMode: deploymentMode,
      );
      if (!mounted) return;
      if (result.deployId.isNotEmpty) {
        setState(() => deploymentStatus = 'Render is deploying the repaired server. Waiting for the new instance to become Live…');
        await deployment.waitForDeployLive(
          apiToken: _renderToken.text,
          serviceId: result.serviceId,
          deployId: result.deployId,
        );
      }
      if (!mounted) return;
      setState(() => deploymentStatus = 'New server instance is Live. Verifying backend 0.4.16…');
      final healthy = await deployment.waitUntilHealthy(result.serverUrl);
      if (!healthy) throw Exception('Render did not expose backend 0.4.16 after deployment. Confirm the repository contains the current backend folder and inspect the latest Render deploy logs.');
      if (!mounted) return;
      setState(() => deploymentStatus = 'Backend verified. Pairing this phone with the newly deployed secret…');
      Object? lastPairingError;
      for (var attempt = 0; attempt < 6; attempt++) {
        try {
          await context.read<AppState>().pair(
                serverUrl: result.serverUrl,
                pairingSecret: result.pairingSecret,
                deviceName: _device.text,
              );
          lastPairingError = null;
          break;
        } catch (error) {
          lastPairingError = error;
          if (!error.toString().toLowerCase().contains('invalid pairing secret') || attempt == 5) {
            rethrow;
          }
          await Future<void>.delayed(const Duration(seconds: 10));
        }
      }
      if (lastPairingError != null) throw lastPairingError;
    } catch (error) {
      if (mounted) setState(() => deploymentStatus = '$error');
    } finally {
      if (mounted) setState(() => deploying = false);
    }
  }
}
