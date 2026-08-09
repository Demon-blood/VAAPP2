import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';
import 'screens/home_shell.dart';
import 'screens/onboarding_page.dart';
import 'services/api_client.dart';
import 'services/background_service.dart';
import 'theme/va_theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
    systemNavigationBarColor: VaTheme.background,
    systemNavigationBarIconBrightness: Brightness.light,
  ));
  await initializeBackgroundService();
  ErrorWidget.builder = (details) => Material(
        color: VaTheme.background,
        child: SafeArea(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: const [
                  Icon(Icons.error_outline_rounded, color: VaTheme.warning, size: 48),
                  SizedBox(height: 14),
                  Text('This screen could not be rendered', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900)),
                  SizedBox(height: 8),
                  Text(
                    'Return to another tab and tap Refresh. The VA will keep the rest of the app available instead of showing a blank grey screen.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: VaTheme.textMuted, height: 1.4),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
  final state = AppState(ApiClient());
  await state.initialize();
  runApp(
    ChangeNotifierProvider.value(
      value: state,
      child: const FullTimeVaApp(),
    ),
  );
}

class FullTimeVaApp extends StatelessWidget {
  const FullTimeVaApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Full-Time VA',
        debugShowCheckedModeBanner: false,
        theme: VaTheme.dark,
        darkTheme: VaTheme.dark,
        themeMode: ThemeMode.dark,
        home: Consumer<AppState>(
          builder: (context, state, _) {
            if (!state.initialized) {
              return const Scaffold(body: Center(child: CircularProgressIndicator()));
            }
            return state.paired ? const HomeShell() : const OnboardingPage();
          },
        ),
      );
}
