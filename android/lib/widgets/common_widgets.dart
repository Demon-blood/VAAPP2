import 'package:flutter/material.dart';

import '../theme/va_theme.dart';

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
          borderRadius: BorderRadius.circular(22),
          child: Ink(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(22),
              border: Border.all(color: accent.withValues(alpha: .42)),
              gradient: LinearGradient(
                colors: [
                  accent.withValues(alpha: .16),
                  VaTheme.surfaceRaised.withValues(alpha: .96),
                ],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: .18),
                  blurRadius: 18,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Row(
                children: [
                  Container(
                    width: 50,
                    height: 50,
                    decoration: BoxDecoration(
                      color: accent.withValues(alpha: .2),
                      borderRadius: BorderRadius.circular(15),
                    ),
                    child: Icon(icon, size: 28, color: accent),
                  ),
                  const SizedBox(width: 15),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Text('$value', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text(
                                label,
                                style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                              ),
                            ),
                          ],
                        ),
                        if (subtitle != null) ...[
                          const SizedBox(height: 3),
                          Text(
                            subtitle!,
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: VaTheme.textMuted),
                          ),
                        ],
                      ],
                    ),
                  ),
                  if (onTap != null) ...[
                    const SizedBox(width: 8),
                    Icon(Icons.chevron_right_rounded, color: accent),
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
          color: VaTheme.surface,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFF20324D)),
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
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 76,
                height: 76,
                decoration: BoxDecoration(
                  color: VaTheme.primary.withValues(alpha: .14),
                  borderRadius: BorderRadius.circular(24),
                ),
                child: const Icon(Icons.auto_awesome_rounded, size: 38, color: VaTheme.primary),
              ),
              const SizedBox(height: 16),
              Text(title, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 8),
              Text(message, textAlign: TextAlign.center, style: const TextStyle(color: VaTheme.textMuted)),
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
        content: Text(message),
        leading: Icon(Icons.error_outline, color: Theme.of(context).colorScheme.error),
        actions: const [SizedBox.shrink()],
      );
}
