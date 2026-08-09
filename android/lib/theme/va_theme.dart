import 'package:flutter/material.dart';

class VaTheme {
  static const background = Color(0xFF07111F);
  static const surface = Color(0xFF0D1828);
  static const surfaceRaised = Color(0xFF132137);
  static const primary = Color(0xFF8B5CF6);
  static const secondary = Color(0xFF3B82F6);
  static const success = Color(0xFF2DD36F);
  static const warning = Color(0xFFFFA94D);
  static const danger = Color(0xFFFF5D73);
  static const textMuted = Color(0xFFA8B3C7);

  static ThemeData get dark {
    const scheme = ColorScheme.dark(
      primary: primary,
      onPrimary: Colors.white,
      secondary: secondary,
      onSecondary: Colors.white,
      error: danger,
      onError: Colors.white,
      surface: surface,
      onSurface: Color(0xFFF7F8FC),
    );
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: scheme,
      scaffoldBackgroundColor: background,
      canvasColor: background,
      appBarTheme: const AppBarTheme(
        backgroundColor: background,
        foregroundColor: Color(0xFFF7F8FC),
        elevation: 0,
        centerTitle: false,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surfaceRaised,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: Color(0xFF243551)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: primary, width: 1.4),
        ),
      ),
      dividerColor: const Color(0xFF21314A),
      navigationBarTheme: const NavigationBarThemeData(
        backgroundColor: Color(0xFF0A1422),
        indicatorColor: Color(0xFF2B2050),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(fontWeight: FontWeight.w600)),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: surfaceRaised,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(0, 48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(0, 46),
          side: const BorderSide(color: Color(0xFF334867)),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      chipTheme: const ChipThemeData(
        backgroundColor: Color(0xFF15243A),
        side: BorderSide(color: Color(0xFF2A3C59)),
        labelStyle: TextStyle(fontWeight: FontWeight.w600),
        shape: StadiumBorder(),
      ),
    );
  }
}
