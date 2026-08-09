import 'package:flutter/material.dart';

class VaTheme {
  static const background = Color(0xFF050D1C);
  static const backgroundRaised = Color(0xFF081326);
  static const surface = Color(0xFF0A1730);
  static const surfaceRaised = Color(0xFF102344);
  static const surfaceSoft = Color(0xFF0D1C35);
  static const primary = Color(0xFF8C4CFF);
  static const primaryBright = Color(0xFFB16CFF);
  static const secondary = Color(0xFF2686FF);
  static const cyan = Color(0xFF2CD5FF);
  static const success = Color(0xFF22DF72);
  static const warning = Color(0xFFFFA439);
  static const danger = Color(0xFFFF4D67);
  static const textMuted = Color(0xFF9EACC6);
  static const border = Color(0xFF1C3A67);

  static ThemeData get dark {
    const scheme = ColorScheme.dark(
      primary: primary,
      onPrimary: Colors.white,
      secondary: secondary,
      onSecondary: Colors.white,
      error: danger,
      onError: Colors.white,
      surface: surface,
      onSurface: Color(0xFFF8FAFF),
    );
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: scheme,
      scaffoldBackgroundColor: background,
      canvasColor: background,
      splashColor: primary.withValues(alpha: .10),
      highlightColor: primary.withValues(alpha: .05),
      textTheme: const TextTheme(
        headlineLarge: TextStyle(fontWeight: FontWeight.w900, letterSpacing: -.5),
        headlineMedium: TextStyle(fontWeight: FontWeight.w900, letterSpacing: -.4),
        headlineSmall: TextStyle(fontWeight: FontWeight.w900, letterSpacing: -.3),
        titleLarge: TextStyle(fontWeight: FontWeight.w800, letterSpacing: -.2),
        titleMedium: TextStyle(fontWeight: FontWeight.w700),
        bodyLarge: TextStyle(height: 1.35),
        bodyMedium: TextStyle(height: 1.35),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: backgroundRaised,
        foregroundColor: Color(0xFFF8FAFF),
        elevation: 0,
        centerTitle: false,
        scrolledUnderElevation: 0,
      ),
      tabBarTheme: const TabBarThemeData(
        labelColor: Color(0xFFC99BFF),
        unselectedLabelColor: Color(0xFFD7DCE8),
        indicatorColor: primary,
        dividerColor: border,
        labelStyle: TextStyle(fontWeight: FontWeight.w800),
        unselectedLabelStyle: TextStyle(fontWeight: FontWeight.w600),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surfaceSoft,
        hintStyle: const TextStyle(color: Color(0xFF7486A6)),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(15),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(15),
          borderSide: const BorderSide(color: border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(15),
          borderSide: const BorderSide(color: primary, width: 1.3),
        ),
      ),
      dividerColor: border,
      navigationBarTheme: const NavigationBarThemeData(
        height: 72,
        backgroundColor: Color(0xFF071225),
        indicatorColor: Color(0xFF342060),
        iconTheme: WidgetStatePropertyAll(IconThemeData(size: 24)),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(fontWeight: FontWeight.w700, fontSize: 12)),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: surfaceRaised,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(0, 48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontWeight: FontWeight.w800),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(0, 46),
          side: const BorderSide(color: Color(0xFF294A75)),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      chipTheme: const ChipThemeData(
        backgroundColor: Color(0xFF112544),
        selectedColor: Color(0xFF5128A4),
        side: BorderSide(color: Color(0xFF294A75)),
        labelStyle: TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
        shape: StadiumBorder(),
        padding: EdgeInsets.symmetric(horizontal: 3),
      ),
      cardTheme: const CardThemeData(
        color: surface,
        elevation: 0,
        margin: EdgeInsets.zero,
      ),
    );
  }
}
