from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "53b3191c7e7e46af425ed7cefa51bcb6c971ab51"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256: dict[str, str] = {
    "preview/backend/tests/test_v121_gmail_cursor_continuity.py":
        "290d0112cde54f40cbebb4bf970c50f3c97a969af25e010c74b2f90a97529dcb",
    "preview/backend/tests/test_v121_gmail_cursor_continuity_contract.py":
        "6f42d11b5069d749b99a319a161f07de489e8a8e2eb6fddf05c4171509811f67",
    "preview/docs/V1.0.21_GMAIL_CURSOR_CONTINUITY.md":
        "5903d2291c4844aa5e6a4c30c87583c4c46416b84b15e55c939a67fcb48773ba",
}

STATUS_PREFIX = """# VAAPP v1.0.21 — Gmail Cursor Continuity & Watch Renewal Integrity

Updated: 2026-09-06

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.20 source baseline: `53b3191c7e7e46af425ed7cefa51bcb6c971ab51`
- Verified v1.0.20 prerelease tag: `va-android-120-1-1`
- Verified v1.0.20 release ID: `383522599`
- v1.0.20 release identity: backend `1.0.20`, Android `1.0.20+63`
- v1.0.20 APK SHA-256: `0a4956bf11c6e72b27cacad3410a9c3601773e701f808005032097d125741cee`

## v1.0.21 maintenance scope

- Gmail watch renewal no longer advances the durable processing history cursor.
- Watch-response history is exposed separately from the consumed processing cursor.
- Profile cursor refresh is observational unless a caller explicitly proves the scan is complete.
- Full recovery captures a pre-scan history checkpoint and catches up from it after the scan.
- Scheduled Gmail polling consumes durable history when a cursor exists instead of jumping to the profile head.
- Incomplete Gmail history pagination fails closed without committing an advanced cursor.
- Gmail watch transport runs off the event loop with no blind inner retry.
- Provider delay and cursor recovery remain VA-owned; no fake Needs You work is created.

## Release identity

- Backend: `1.0.21`
- Required Android: `1.0.21`
- Android: `1.0.21+64`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.

---

"""

HANDOFF_PREFIX = """# VAAPP v1.0.21 handoff addendum

Updated: 2026-09-06
Repository: `Demon-blood/VAAPP2`
Branch: `main`

The verified maintenance source entering this candidate is v1.0.20 commit `53b3191c7e7e46af425ed7cefa51bcb6c971ab51`, published under prerelease tag `va-android-120-1-1`. Its signed APK digest is `0a4956bf11c6e72b27cacad3410a9c3601773e701f808005032097d125741cee`.

Current candidate: **v1.0.21 — Gmail Cursor Continuity & Watch Renewal Integrity**.

The safety invariant is that `GmailMailboxState.history_id` means **consumed mailbox history**, not merely a provider-reported current position. `users.watch` renewal metadata therefore cannot advance it. Scheduled polling consumes `history.list` from the durable cursor, full recovery brackets the scan with a pre-scan history checkpoint and a post-scan catch-up, and pagination truncation fails closed. Google OAuth/reauthorization remains the genuine human boundary; provider/network delay stays VA-owned.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

---

"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def replace_once(path: Path, old: str, new: str) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one anchor in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def verify_bundle() -> None:
    for relative, expected in EXPECTED_PREVIEW_SHA256.items():
        path = BUNDLE_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"missing prepared bundle file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"bundle integrity mismatch for {relative}: {actual}")


def verify_repo(root: Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"{root} is not a git working tree")
    head = run_git(root, "rev-parse", "HEAD")
    if head != EXPECTED_BASELINE:
        raise RuntimeError(
            f"refusing to patch unexpected HEAD {head}; expected v1.0.20 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.20"\nREQUIRED_ANDROID_VERSION = "1.0.20"\n'
    ):
        raise RuntimeError("v1.0.20 backend baseline identity mismatch")
    if "version: 1.0.20+63" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.20 Android baseline identity mismatch")
    if (root / "backend/tests/test_v121_gmail_cursor_continuity.py").exists():
        raise RuntimeError("v1.0.21 runtime test already exists")
    if (root / "docs/V1.0.21_GMAIL_CURSOR_CONTINUITY.md").exists():
        raise RuntimeError("v1.0.21 release document already exists")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_gmail_sync_service(root: Path) -> None:
    path = root / "backend/app/services/gmail_sync_service.py"

    replace_once(
        path,
        '''async def refresh_mailbox_cursor_from_profile(\n    db: AsyncSession,\n    *,\n    mark_full_sync: bool = False,\n) -> GmailMailboxState:\n''',
        '''async def refresh_mailbox_cursor_from_profile(\n    db: AsyncSession,\n    *,\n    mark_full_sync: bool = False,\n    advance_processing_cursor: bool = False,\n) -> GmailMailboxState:\n''',
    )
    replace_once(
        path,
        '''    history_id = str(profile.get("historyId") or "")\n    if history_id:\n        row.history_id = history_id\n''',
        '''    history_id = str(profile.get("historyId") or "")\n    # A profile historyId is Gmail's current mailbox position, not evidence that\n    # VAAPP consumed all changes up to that position. Cursor advancement is opt-in.\n    if history_id and advance_processing_cursor:\n        row.history_id = history_id\n''',
    )
    replace_once(
        path,
        '''    row.history_id = history_id\n    row.watch_topic = topic\n''',
        '''    # users.watch returns Gmail's current history record. It is watch metadata,\n    # not a consumed-processing checkpoint, so it must never advance row.history_id.\n    row.watch_topic = topic\n''',
    )
    replace_once(
        path,
        '''        details={"history_id": history_id, "expiration": expiration, "topic": topic},\n''',
        '''        details={\n            "watch_history_id": history_id,\n            "processing_history_id": row.history_id,\n            "expiration": expiration,\n            "topic": topic,\n        },\n''',
    )
    replace_once(
        path,
        '''        "history_id": row.history_id,\n        "expiration": int(expiration.timestamp() * 1000),\n''',
        '''        "history_id": row.history_id,\n        "watch_history_id": history_id,\n        "expiration": int(expiration.timestamp() * 1000),\n''',
    )

    old_full_recovery = '''async def full_recovery_sync(db: AsyncSession, *, max_messages: int = 500) -> dict[str, Any]:\n    processed = await sync_gmail(db, max_messages=max_messages)\n    row = await refresh_mailbox_cursor_from_profile(db, mark_full_sync=True)\n    await write_audit(\n        db,\n        "gmail_recovery_sync_completed",\n        entity_type="gmail_mailbox",\n        entity_id=row.account_key,\n        details={"processed": processed, "history_id": row.history_id},\n    )\n    await db.commit()\n    return {"mode": "full_recovery", "processed": processed, "history_id": row.history_id}\n'''
    new_full_recovery = '''async def full_recovery_sync(db: AsyncSession, *, max_messages: int = 500) -> dict[str, Any]:\n    row = await mailbox_state(db)\n    profile = await get_gmail_profile(db)\n    profile_email = str(profile.get("emailAddress") or row.account_key).lower()\n    if profile_email and profile_email != row.account_key:\n        raise RuntimeError("Gmail profile does not match the connected Google account")\n    bootstrap_history_id = str(profile.get("historyId") or "").strip()\n    if not bootstrap_history_id:\n        raise RuntimeError("Gmail profile did not provide a recovery history cursor")\n\n    # Capture the provider cursor before the bounded recovery scan. Only after the\n    # scan completes do we checkpoint that pre-scan boundary, then consume every\n    # change that happened while the scan was running. A crash before checkpointing\n    # leaves the old cursor untouched; a crash after checkpointing resumes from it.\n    bootstrap_processed = await sync_gmail(db, max_messages=max_messages)\n    now = utcnow()\n    row.history_id = bootstrap_history_id\n    row.last_full_sync_at = now\n    row.last_history_sync_at = now\n    row.last_error = ""\n    await write_audit(\n        db,\n        "gmail_recovery_scan_checkpointed",\n        entity_type="gmail_mailbox",\n        entity_id=row.account_key,\n        details={\n            "bootstrap_processed": bootstrap_processed,\n            "bootstrap_history_id": bootstrap_history_id,\n        },\n    )\n    await db.commit()\n\n    catchup = await history_sync(db)\n    catchup_processed = int(catchup.get("processed") or 0)\n    final_history_id = str(catchup.get("history_id") or row.history_id)\n    processed = bootstrap_processed + catchup_processed\n    await write_audit(\n        db,\n        "gmail_recovery_sync_completed",\n        entity_type="gmail_mailbox",\n        entity_id=row.account_key,\n        details={\n            "processed": processed,\n            "bootstrap_processed": bootstrap_processed,\n            "catchup_processed": catchup_processed,\n            "bootstrap_history_id": bootstrap_history_id,\n            "history_id": final_history_id,\n        },\n    )\n    await db.commit()\n    return {\n        "mode": "full_recovery",\n        "processed": processed,\n        "bootstrap_processed": bootstrap_processed,\n        "catchup_processed": catchup_processed,\n        "history_id": final_history_id,\n    }\n'''
    replace_once(path, old_full_recovery, new_full_recovery)


def patch_google_api(root: Path) -> None:
    path = root / "backend/app/integrations/google_api.py"
    replace_once(path, "    max_pages: int = 25,\n", "    max_pages: int = 1000,\n")
    replace_once(
        path,
        '''        page_token = response.get("nextPageToken")\n        if not page_token or pages >= max(1, max_pages):\n            break\n    return message_ids, newest_history_id\n''',
        '''        page_token = response.get("nextPageToken")\n        if not page_token:\n            break\n        if pages >= max(1, max_pages):\n            raise RuntimeError(\n                "Gmail history pagination limit reached before cursor exhaustion"\n            )\n    return message_ids, newest_history_id\n''',
    )
    replace_once(
        path,
        '''async def start_gmail_watch(db: AsyncSession, topic_name: str) -> dict[str, Any]:\n    service = await gmail_service(db)\n    return service.users().watch(\n        userId="me",\n        body={"topicName": topic_name, "labelFilterBehavior": "INCLUDE", "labelIds": ["INBOX"]},\n    ).execute()\n''',
        '''async def start_gmail_watch(db: AsyncSession, topic_name: str) -> dict[str, Any]:\n    service = await gmail_service(db)\n    return await _execute_google_request(\n        lambda: service.users().watch(\n            userId="me",\n            body={\n                "topicName": topic_name,\n                "labelFilterBehavior": "INCLUDE",\n                "labelIds": ["INBOX"],\n            },\n        ),\n        attempts=1,\n        retry_statuses=set(),\n    )\n''',
    )


def patch_workflow_engine(root: Path) -> None:
    path = root / "backend/app/services/workflow_engine.py"
    old = '''@job_handler("gmail.sync")\nasync def _gmail_sync(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:\n    from app.services.email_processor import sync_gmail\n    from app.services.gmail_sync_service import refresh_mailbox_cursor_from_profile\n\n    max_messages = max(1, min(int(payload.get("max_messages") or 250), 1000))\n    result = await sync_gmail(db, max_messages=max_messages)\n    state = await refresh_mailbox_cursor_from_profile(db, mark_full_sync=True)\n    return {"result": result, "history_id": state.history_id}\n'''
    new = '''@job_handler("gmail.sync")\nasync def _gmail_sync(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:\n    from app.services.gmail_sync_service import full_recovery_sync, history_sync, mailbox_state\n\n    max_messages = max(1, min(int(payload.get("max_messages") or 250), 1000))\n    state = await mailbox_state(db)\n    if state.history_id:\n        return await history_sync(db)\n    return await full_recovery_sync(db, max_messages=max_messages)\n'''
    replace_once(path, old, new)


def patch_release_identity(root: Path) -> None:
    version = root / "backend/app/core/version.py"
    if read_text(version) != 'APP_VERSION = "1.0.20"\nREQUIRED_ANDROID_VERSION = "1.0.20"\n':
        raise RuntimeError("unexpected backend version file")
    version.write_text(
        'APP_VERSION = "1.0.21"\nREQUIRED_ANDROID_VERSION = "1.0.21"\n',
        encoding="utf-8",
    )

    replace_once(
        root / "backend/pyproject.toml",
        'version = "1.0.20"',
        'version = "1.0.21"',
    )
    replace_once(
        root / "android/pubspec.yaml",
        "version: 1.0.20+63",
        "version: 1.0.21+64",
    )
    contract = root / "android/lib/release_contract.dart"
    replace_once(contract, "const String appRelease = '1.0.20';", "const String appRelease = '1.0.21';")
    replace_once(
        contract,
        "const String minimumBackendVersion = '1.0.20';",
        "const String minimumBackendVersion = '1.0.21';",
    )

    identity_replacements = {
        'APP_VERSION = "1.0.20"': 'APP_VERSION = "1.0.21"',
        'REQUIRED_ANDROID_VERSION = "1.0.20"': 'REQUIRED_ANDROID_VERSION = "1.0.21"',
        'version = "1.0.20"': 'version = "1.0.21"',
        "version: 1.0.20+63": "version: 1.0.21+64",
        "appRelease = '1.0.20'": "appRelease = '1.0.21'",
        "minimumBackendVersion = '1.0.20'": "minimumBackendVersion = '1.0.21'",
        'assert APP_VERSION == "1.0.20"': 'assert APP_VERSION == "1.0.21"',
    }
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        text = read_text(test_path)
        updated = text
        for old, new in identity_replacements.items():
            updated = updated.replace(old, new)
        if updated != text:
            test_path.write_text(updated, encoding="utf-8")


def patch_project_metadata(root: Path) -> None:
    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.20":
        raise RuntimeError("unexpected project-state current_version")
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("verified baseline run metadata changed unexpectedly")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("verified baseline conclusion changed unexpectedly")
    state["updated"] = "2026-09-06"
    state["current_phase"] = "maintenance"
    state["current_phase_name"] = "v1.0.21 Gmail Cursor Continuity & Watch Renewal Integrity"
    state["current_version"] = "1.0.21"
    state["current_android_version"] = "1.0.21+64"
    state["phase_status"] = "source commit is gated by full GitHub Actions validation before publication"
    state["v121_features"] = [
        "Gmail watch renewal never advances the durable consumed-processing history cursor",
        "watch response history is reported separately from the processing cursor",
        "profile history refresh is observational unless cursor advancement is explicitly authorized",
        "full recovery captures a pre-scan history boundary and catches up after the scan",
        "scheduled Gmail polling consumes history from the durable cursor when available",
        "history pagination truncation fails closed instead of committing an advanced cursor",
        "watch transport runs off the event loop without blind provider-operation retries",
        "Gmail provider delay remains VA-owned and creates no fake Needs You work",
    ]
    invariant = (
        "Gmail processing history advances only after the corresponding provider changes have been consumed"
    )
    invariants = list(state.get("invariants") or [])
    if invariant not in invariants:
        invariants.append(invariant)
    state["invariants"] = invariants
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    status_path = root / "STATUS.md"
    status = read_text(status_path)
    if not status.startswith("# VAAPP v1.0.21"):
        status_path.write_text(STATUS_PREFIX + status, encoding="utf-8")

    handoff_path = root / "VAAPP_PROJECT_HANDOFF.md"
    handoff = read_text(handoff_path)
    if not handoff.startswith("# VAAPP v1.0.21 handoff addendum"):
        handoff_path.write_text(HANDOFF_PREFIX + handoff, encoding="utf-8")


def verify_result(root: Path) -> None:
    gmail = read_text(root / "backend/app/services/gmail_sync_service.py")
    google = read_text(root / "backend/app/integrations/google_api.py")
    workflow = read_text(root / "backend/app/services/workflow_engine.py")
    if "watch_history_id" not in gmail:
        raise RuntimeError("watch history separation missing")
    watch_start = gmail.index("async def ensure_gmail_watch")
    watch_end = gmail.index("async def full_recovery_sync", watch_start)
    if "row.history_id = history_id" in gmail[watch_start:watch_end]:
        raise RuntimeError("watch renewal still advances the processing cursor")
    if "row.history_id = bootstrap_history_id" not in gmail:
        raise RuntimeError("full-recovery checkpoint missing")
    if "await history_sync(db)" not in gmail:
        raise RuntimeError("full-recovery catch-up missing")
    if "Gmail history pagination limit reached before cursor exhaustion" not in google:
        raise RuntimeError("history pagination fail-closed guard missing")
    if "max_pages: int = 1000" not in google:
        raise RuntimeError("history page ceiling not updated")
    handler_start = workflow.index('@job_handler("gmail.sync")')
    handler_end = workflow.index('@job_handler("gmail.history.sync")', handler_start)
    handler = workflow[handler_start:handler_end]
    if "return await history_sync(db)" not in handler:
        raise RuntimeError("scheduled Gmail sync does not use durable history")
    if "refresh_mailbox_cursor_from_profile" in handler:
        raise RuntimeError("scheduled Gmail sync still jumps to profile history")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.21"\nREQUIRED_ANDROID_VERSION = "1.0.21"\n'
    ):
        raise RuntimeError("v1.0.21 backend release identity missing")
    if "version: 1.0.21+64" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.21 Android release identity missing")
    state = read_text(root / "VAAPP_PROJECT_STATE.json")
    if '"verified_baseline_actions_run": 41' not in state:
        raise RuntimeError("historical verified baseline run metadata was not preserved")
    if '"verified_baseline_actions_conclusion": "success"' not in state:
        raise RuntimeError("historical verified baseline conclusion was not preserved")
    if "GitHub Actions run #41" not in read_text(root / "VAAPP_PROJECT_HANDOFF.md"):
        raise RuntimeError("historical run #41 handoff phrase was not preserved")


def apply(root: Path) -> None:
    verify_bundle()
    verify_repo(root)
    patch_gmail_sync_service(root)
    patch_google_api(root)
    patch_workflow_engine(root)
    patch_release_identity(root)
    patch_project_metadata(root)
    copy_prepared(
        root,
        "preview/backend/tests/test_v121_gmail_cursor_continuity.py",
        "backend/tests/test_v121_gmail_cursor_continuity.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v121_gmail_cursor_continuity_contract.py",
        "backend/tests/test_v121_gmail_cursor_continuity_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.21_GMAIL_CURSOR_CONTINUITY.md",
        "docs/V1.0.21_GMAIL_CURSOR_CONTINUITY.md",
    )
    verify_result(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    args = parser.parse_args()
    root = args.repo.resolve()
    apply(root)
    print("Applied guarded v1.0.21 Gmail cursor-continuity patch.")


if __name__ == "__main__":
    main()
