#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

EXPECTED_BASELINE = '1111973780e3a8f3f6028da26f2003ece5d4ac36'
HELPERS = "String _briefingCollapsedBody(String body) {\n  final normalized = body.replaceAll(RegExp(r'\\s+'), ' ').trim();\n  if (normalized.isEmpty) return 'Your VA briefing is ready.';\n  const limit = 140;\n  if (normalized.length <= limit) return normalized;\n\n  var end = normalized.lastIndexOf('. ', limit);\n  if (end < 80) end = normalized.lastIndexOf('; ', limit);\n  if (end < 80) end = normalized.lastIndexOf(', ', limit);\n  if (end < 80) end = limit;\n\n  final includePeriod = end < normalized.length && normalized[end] == '.';\n  final clipped = normalized.substring(0, end + (includePeriod ? 1 : 0)).trim();\n  return '$clipped… Expand to read the full briefing.';\n}\n\nString _briefingExpandedBody(String body) {\n  final normalized = body.replaceAll(RegExp(r'\\s+'), ' ').trim();\n  if (normalized.isEmpty) return 'Your VA briefing is ready.';\n  return normalized\n      .replaceAll('. ', '.\\n\\n')\n      .replaceAll('! ', '!\\n\\n')\n      .replaceAll('? ', '?\\n\\n');\n}\n\nNotificationDetails _briefingNotificationDetails(String title, String body) {\n  final expandedBody = _briefingExpandedBody(body);\n  return NotificationDetails(\n    android: AndroidNotificationDetails(\n      'va_daily_briefing',\n      'VA briefings',\n      channelDescription: 'Scheduled human-style VA briefings',\n      importance: Importance.defaultImportance,\n      priority: Priority.defaultPriority,\n      styleInformation: BigTextStyleInformation(\n        expandedBody,\n        contentTitle: title,\n        summaryText: 'Full-Time VA',\n      ),\n    ),\n  );\n}"
CURRENT_CALL = "            await notifications.show(\n              id: 1002,\n              title: '${name[0].toUpperCase()}${name.substring(1)} VA briefing',\n              body: '${notification['body'] ?? data['summary_text'] ?? 'Your VA briefing is ready.'}',\n              notificationDetails: const NotificationDetails(\n                android: AndroidNotificationDetails(\n                  'va_daily_briefing',\n                  'VA briefings',\n                  channelDescription: 'Scheduled human-style VA briefings',\n                  importance: Importance.defaultImportance,\n                  priority: Priority.defaultPriority,\n                ),\n              ),\n            );"
CURRENT_REPLACEMENT = "            final briefingTitle = '${name[0].toUpperCase()}${name.substring(1)} VA briefing';\n            final briefingBody =\n                '${notification['body'] ?? data['summary_text'] ?? 'Your VA briefing is ready.'}';\n            await notifications.show(\n              id: 1002,\n              title: briefingTitle,\n              body: _briefingCollapsedBody(briefingBody),\n              notificationDetails: _briefingNotificationDetails(briefingTitle, briefingBody),\n            );"
LEGACY_CALL = "            await notifications.show(\n              id: 1002,\n              title: '${notification['title'] ?? 'Your Full-Time VA daily briefing'}',\n              body: '${notification['body'] ?? data['summary_text'] ?? 'Your daily VA briefing is ready.'}',\n              notificationDetails: const NotificationDetails(\n                android: AndroidNotificationDetails(\n                  'va_daily_briefing',\n                  'VA briefings',\n                  channelDescription: 'Scheduled human-style VA briefings',\n                  importance: Importance.defaultImportance,\n                  priority: Priority.defaultPriority,\n                ),\n              ),\n            );"
LEGACY_REPLACEMENT = "            final briefingTitle = '${notification['title'] ?? 'Your Full-Time VA daily briefing'}';\n            final briefingBody =\n                '${notification['body'] ?? data['summary_text'] ?? 'Your daily VA briefing is ready.'}';\n            await notifications.show(\n              id: 1002,\n              title: briefingTitle,\n              body: _briefingCollapsedBody(briefingBody),\n              notificationDetails: _briefingNotificationDetails(briefingTitle, briefingBody),\n            );"
TEST_CONTENT = 'from pathlib import Path\n\n\nROOT = Path(__file__).resolve().parents[2]\nSOURCE = ROOT / "android" / "lib" / "services" / "background_service.dart"\n\n\ndef _source() -> str:\n    return SOURCE.read_text(encoding="utf-8")\n\n\ndef test_v122_briefing_uses_big_text_expansion() -> None:\n    source = _source()\n    assert "BigTextStyleInformation(" in source\n    assert "styleInformation: BigTextStyleInformation(" in source\n    assert "_briefingExpandedBody" in source\n\n\ndef test_v122_collapsed_notification_stays_concise() -> None:\n    source = _source()\n    assert "_briefingCollapsedBody" in source\n    assert source.count("body: _briefingCollapsedBody(briefingBody)") == 2\n    assert "Expand to read the full briefing." in source\n\n\ndef test_v122_covers_current_and_legacy_briefing_paths() -> None:\n    source = _source()\n    assert source.count("id: 1002") == 2\n    assert source.count("_briefingNotificationDetails(briefingTitle, briefingBody)") == 2\n    assert "Your VA briefing is ready." in source\n    assert "Your daily VA briefing is ready." in source\n'
DOC_CONTENT = '# v1.0.22 — Briefing Notification UX\n\n## Problem\n\nThe backend already generates the full human-style VA briefing, but Android was presenting the\nentire briefing as a normal notification `body`. Android collapses ordinary notification text,\nwhich meant most of the briefing appeared hidden even though delivery itself was correct.\n\n## Change\n\nThe Android background service now:\n\n1. Keeps the complete briefing text.\n2. Renders the complete text through `BigTextStyleInformation`.\n3. Uses a concise collapsed body so the lock-screen / notification shade remains readable.\n4. Adds paragraph spacing to the expanded briefing for easier scanning.\n5. Applies the same behavior to both the current morning/afternoon/evening delivery path and\n   the legacy one-daily-briefing fallback.\n6. Leaves briefing delivery acknowledgements, durable delivery windows, retry behavior, and\n   backend ownership semantics unchanged.\n\n## Invariant\n\nA successful notification display may advance the existing delivery-acknowledgement workflow,\nbut presentation changes must never change what the backend considers processed or delivered.\n\n## Expected Android behavior\n\nCollapsed:\n\n    Evening VA briefing\n    Everything is under control and nothing needs your attention right now… Expand to read the full briefing.\n\nExpanded:\n\n    Evening VA briefing\n    Good evening.\n\n    Everything is under control and nothing needs your attention right now.\n\n    I cleared the routine mail and messages...\n\nThe full briefing remains available in the application as before.\n'


def git_head(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply guarded VAAPP v1.0.22 briefing notification UX patch."
    )
    parser.add_argument("--repo", default=".", help="VAAPP2 repository root")
    parser.add_argument(
        "--allow-head-mismatch",
        action="store_true",
        help="Allow applying when HEAD differs from the validated v1.0.21 baseline; source patterns still fail closed.",
    )
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    target = root / "android" / "lib" / "services" / "background_service.dart"
    tests = root / "backend" / "tests" / "test_v122_briefing_notification_contract.py"
    docs = root / "docs" / "V1.0.22_BRIEFING_NOTIFICATION_UX.md"

    if not target.exists():
        raise SystemExit(f"Missing expected file: {target}")

    head = git_head(root)
    if head and head != EXPECTED_BASELINE and not args.allow_head_mismatch:
        raise SystemExit(
            f"Refusing source drift: HEAD is {head}, expected {EXPECTED_BASELINE}. "
            "Use --allow-head-mismatch only after reviewing intervening changes."
        )

    text = target.read_text(encoding="utf-8")

    if "_briefingNotificationDetails(" not in text:
        marker = "Future<bool> _ackBriefingDelivery({"
        if text.count(marker) != 1:
            raise SystemExit("Could not locate briefing-delivery insertion marker exactly once.")
        text = text.replace(marker, HELPERS + "\n\n" + marker, 1)

    if CURRENT_REPLACEMENT not in text:
        text = replace_once(text, CURRENT_CALL, CURRENT_REPLACEMENT, "current briefing notification")
    if LEGACY_REPLACEMENT not in text:
        text = replace_once(text, LEGACY_CALL, LEGACY_REPLACEMENT, "legacy briefing notification")

    required = [
        "BigTextStyleInformation(",
        "styleInformation: BigTextStyleInformation(",
        "body: _briefingCollapsedBody(briefingBody)",
        "_briefingNotificationDetails(briefingTitle, briefingBody)",
        "Expand to read the full briefing.",
    ]
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise SystemExit(f"Patched source is missing required contract markers: {missing}")
    if text.count("body: _briefingCollapsedBody(briefingBody)") != 2:
        raise SystemExit("Expected exactly two briefing notification presentation paths.")
    if text.count("_briefingNotificationDetails(briefingTitle, briefingBody)") != 2:
        raise SystemExit("Expected exactly two expanded briefing notification paths.")

    target.write_text(text, encoding="utf-8")
    tests.write_text(TEST_CONTENT, encoding="utf-8")
    docs.write_text(DOC_CONTENT, encoding="utf-8")

    print("Applied VAAPP v1.0.22 briefing notification UX patch.")
    print(f"Updated: {target.relative_to(root)}")
    print(f"Created/updated: {tests.relative_to(root)}")
    print(f"Created/updated: {docs.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
