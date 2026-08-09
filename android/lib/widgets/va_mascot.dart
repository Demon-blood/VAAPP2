import 'dart:math' as math;

import 'package:flutter/material.dart';

/// A native Flutter recreation of the assistant mascot used by the VA visual system.
/// It is intentionally drawn at runtime rather than cropped from a mockup image.
class VaAssistantMascot extends StatelessWidget {
  const VaAssistantMascot({this.size = 118, this.wave = true, super.key});

  final double size;
  final bool wave;

  @override
  Widget build(BuildContext context) => RepaintBoundary(
        child: CustomPaint(
          size: Size.square(size),
          painter: _VaAssistantPainter(wave: wave),
        ),
      );
}

class _VaAssistantPainter extends CustomPainter {
  const _VaAssistantPainter({required this.wave});

  final bool wave;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.shortestSide;
    final center = Offset(size.width * .5, size.height * .52);

    final glow = Paint()
      ..shader = RadialGradient(
        colors: [
          const Color(0xFF9B5CFF).withValues(alpha: .38),
          const Color(0xFF4D8CFF).withValues(alpha: .16),
          Colors.transparent,
        ],
      ).createShader(Rect.fromCircle(center: center, radius: s * .5));
    canvas.drawCircle(center, s * .49, glow);

    final shadow = Paint()
      ..color = Colors.black.withValues(alpha: .28)
      ..maskFilter = MaskFilter.blur(BlurStyle.normal, s * .045);
    canvas.drawOval(
      Rect.fromCenter(center: Offset(s * .51, s * .83), width: s * .55, height: s * .13),
      shadow,
    );

    final bodyRect = Rect.fromCenter(
      center: Offset(s * .50, s * .61),
      width: s * .56,
      height: s * .56,
    );
    final body = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Color(0xFFF7F4FF), Color(0xFFC7C6FF), Color(0xFF8268E8)],
        stops: [0, .56, 1],
      ).createShader(bodyRect);
    canvas.drawOval(bodyRect, body);

    final lowerShade = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [Colors.transparent, const Color(0xFF4E37A6).withValues(alpha: .5)],
      ).createShader(bodyRect);
    canvas.drawOval(bodyRect, lowerShade);

    final headRect = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: Offset(s * .50, s * .43),
        width: s * .52,
        height: s * .36,
      ),
      Radius.circular(s * .18),
    );
    final head = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Color(0xFFF9F8FF), Color(0xFFD7D6FF), Color(0xFF9277EA)],
      ).createShader(headRect.outerRect);
    canvas.drawRRect(headRect, head);

    final faceRect = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: Offset(s * .50, s * .435),
        width: s * .38,
        height: s * .20,
      ),
      Radius.circular(s * .10),
    );
    final face = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [Color(0xFF161239), Color(0xFF090D26)],
      ).createShader(faceRect.outerRect);
    canvas.drawRRect(faceRect, face);

    final faceGlow = Paint()
      ..color = const Color(0xFF8B5CF6).withValues(alpha: .25)
      ..maskFilter = MaskFilter.blur(BlurStyle.normal, s * .035);
    canvas.drawRRect(faceRect, faceGlow);
    canvas.drawRRect(faceRect, face);

    final eyeGlow = Paint()
      ..color = const Color(0xFFB98CFF).withValues(alpha: .75)
      ..maskFilter = MaskFilter.blur(BlurStyle.normal, s * .028);
    final eye = Paint()..color = const Color(0xFFD5B7FF);
    for (final x in [s * .43, s * .57]) {
      canvas.drawCircle(Offset(x, s * .435), s * .033, eyeGlow);
      canvas.drawCircle(Offset(x, s * .435), s * .020, eye);
    }

    final chest = Paint()
      ..shader = RadialGradient(
        colors: [const Color(0xFFB88CFF), const Color(0xFF5E3DC2).withValues(alpha: .35)],
      ).createShader(Rect.fromCircle(center: Offset(s * .50, s * .66), radius: s * .10));
    canvas.drawCircle(Offset(s * .50, s * .66), s * .065, chest);

    _drawArm(canvas, s, left: true, raised: false);
    _drawArm(canvas, s, left: false, raised: wave);

    final highlight = Paint()
      ..color = Colors.white.withValues(alpha: .60)
      ..strokeWidth = s * .012
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(
      Rect.fromCenter(center: Offset(s * .44, s * .52), width: s * .35, height: s * .35),
      math.pi * 1.05,
      math.pi * .42,
      false,
      highlight,
    );
  }

  void _drawArm(Canvas canvas, double s, {required bool left, required bool raised}) {
    final direction = left ? -1.0 : 1.0;
    final shoulder = Offset(s * (.50 + .23 * direction), s * .58);
    final hand = raised
        ? Offset(s * (.50 + .40 * direction), s * .31)
        : Offset(s * (.50 + .37 * direction), s * .67);
    final armPaint = Paint()
      ..shader = const LinearGradient(
        colors: [Color(0xFFE8E6FF), Color(0xFF8069DA)],
      ).createShader(Rect.fromPoints(shoulder, hand))
      ..strokeWidth = s * .105
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(shoulder, hand, armPaint);

    final handPaint = Paint()
      ..shader = const RadialGradient(
        colors: [Color(0xFFF8F7FF), Color(0xFF8E78E2)],
      ).createShader(Rect.fromCircle(center: hand, radius: s * .075));
    canvas.drawCircle(hand, s * .067, handPaint);

    if (raised) {
      final finger = Paint()
        ..color = const Color(0xFFD9D5FF)
        ..strokeWidth = s * .025
        ..strokeCap = StrokeCap.round;
      for (final offset in [-.034, 0.0, .034]) {
        canvas.drawLine(
          hand + Offset(s * offset, -s * .02),
          hand + Offset(s * (offset + .012 * direction), -s * .105),
          finger,
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant _VaAssistantPainter oldDelegate) => oldDelegate.wave != wave;
}
