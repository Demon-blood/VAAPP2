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
