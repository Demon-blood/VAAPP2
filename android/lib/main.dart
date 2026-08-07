import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';
import 'screens/home_shell.dart';
import 'screens/onboarding_page.dart';
import 'services/api_client.dart';
import 'services/background_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
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
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF4B5FFF)),
          useMaterial3: true,
          inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder()),
        ),
        darkTheme: ThemeData(
          colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF8994FF),
            brightness: Brightness.dark,
          ),
          useMaterial3: true,
          inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder()),
        ),
        themeMode: ThemeMode.system,
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
