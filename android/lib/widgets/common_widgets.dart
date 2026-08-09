import 'package:flutter/material.dart';

import '../theme/va_theme.dart';
import 'va_mascot.dart';

class CountCard extends StatelessWidget {
  const CountCard({
    required this.label,
    required this.value,
    required this.icon,
    this.subtitle,
    this.accent = VaTheme.primary,
    this.onTap,
    super.key,
  });

  final String label;
  final int value;
  final IconData icon;
  final String? subtitle;
  final Color accent;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(19),
          child: Ink(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(19),
              border: Border.all(color: accent.withValues(alpha: .40)),
              gradient: LinearGradient(
                colors: [
                  accent.withValues(alpha: .15),
                  VaTheme.surface.withValues(alpha: .98),
                  VaTheme.surfaceSoft,
                ],
                stops: const [0, .42, 1],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              boxShadow: [
                BoxShadow(
                  color: accent.withValues(alpha: .07),
                  blurRadius: 20,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(14, 13, 13, 13),
              child: Row(
                children: [
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        colors: [accent.withValues(alpha: .40), accent.withValues(alpha: .14)],
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                      ),
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: accent.withValues(alpha: .25)),
                    ),
                    child: Icon(icon, size: 27, color: Color.lerp(accent, Colors.white, .22)),
                  ),
                  const SizedBox(width: 13),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.center,
                          children: [
                            Text(
                              '$value',
                              style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w900),
                            ),
                            const SizedBox(width: 9),
                            Expanded(
                              child: Text(
                                label,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
                              ),
                            ),
                          ],
                        ),
                        if (subtitle != null) ...[
                          const SizedBox(height: 3),
                          Text(
                            subtitle!,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: VaTheme.textMuted),
                          ),
                        ],
                      ],
                    ),
                  ),
                  if (onTap != null) ...[
                    const SizedBox(width: 6),
                    Icon(Icons.chevron_right_rounded, color: accent, size: 26),
                  ],
                ],
              ),
            ),
          ),
        ),
      );
}

class VaSectionCard extends StatelessWidget {
  const VaSectionCard({required this.child, this.padding = const EdgeInsets.all(16), super.key});

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding,
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [Color(0xFF0C1B36), Color(0xFF09162C)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: BorderRadius.circular(19),
          border: Border.all(color: VaTheme.border),
          boxShadow: [
            BoxShadow(color: Colors.black.withValues(alpha: .18), blurRadius: 18, offset: const Offset(0, 8)),
          ],
        ),
        child: child,
      );
}

class EmptyState extends StatelessWidget {
  const EmptyState({required this.icon, required this.title, required this.message, super.key});

  final IconData icon;
  final String title;
  final String message;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const VaAssistantMascot(size: 96, wave: false),
              const SizedBox(height: 10),
              Text(title, textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
              const SizedBox(height: 7),
              Text(message, textAlign: TextAlign.center, style: const TextStyle(color: VaTheme.textMuted, height: 1.4)),
            ],
          ),
        ),
      );
}

class ErrorBanner extends StatelessWidget {
  const ErrorBanner(this.message, {super.key});

  final String message;

  @override
  Widget build(BuildContext context) => MaterialBanner(
        backgroundColor: const Color(0xFF261425),
        content: Text(message),
        leading: Icon(Icons.error_outline, color: Theme.of(context).colorScheme.error),
        actions: const [SizedBox.shrink()],
      );
}
