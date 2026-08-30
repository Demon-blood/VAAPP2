from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "4b3b38903545c8598695660c666c3080aff171e2"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256 = {
    "preview/backend/tests/test_v111_fulfillment_side_effect_recovery.py": "b8739819a98ceb984b509ec08d70ed750730331cef87ecb9af3991b3004ce60e",
    "preview/backend/tests/test_v111_fulfillment_side_effect_recovery_contract.py": "5cee097b7db744e738726b4f2fc5a90add095c2a91ff13b8f7f476d62b2333c4",
    "preview/docs/V1.0.11_FULFILLMENT_SIDE_EFFECT_RECOVERY.md": "519cee2698ead1d75af826f97bad7b10ce6b5d8a958f64748b6cba0d276b0416",
}


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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.10 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.10"\nREQUIRED_ANDROID_VERSION = "1.0.10"\n'
    ):
        raise RuntimeError("v1.0.10 backend baseline identity mismatch")
    if "version: 1.0.10+53" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.10 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_browser_operator(root: Path) -> None:
    path = root / "backend/app/services/browser_operator.py"

    replace_once(
        path,
        '''def _as_list(value: Any) -> list[str]:\n    if value is None:\n        return []\n    if isinstance(value, list):\n        return [str(item) for item in value if str(item)]\n    return [str(value)] if str(value) else []\n\n\nasync def verify_page''',
        '''def _as_list(value: Any) -> list[str]:\n    if value is None:\n        return []\n    if isinstance(value, list):\n        return [str(item) for item in value if str(item)]\n    return [str(value)] if str(value) else []\n\n\ndef operation_requires_postcondition_reconciliation(operation: BrowserOperation) -> bool:\n    \"\"\"Return True when a non-replay-safe provider side effect still lacks proof.\"\"\"\n    return (\n        operation.side_effect_step is not None\n        and operation.current_step >= operation.side_effect_step\n    )\n\n\nasync def verify_page''',
    )

    replace_once(
        path,
        '''                # If a worker died after a potentially mutating click/submit, reconcile\n                # against the explicit postcondition before doing anything else.\n                if operation.status == "dispatching" or (\n                    operation.side_effect_step is not None and operation.current_step == operation.side_effect_step\n                ):\n                    verified, verification_details = await verify_page(page, portal, verification)\n''',
        '''                # Any unverified non-replay-safe side effect is reconciliation-only.\n                # Authenticate if necessary, then inspect the provider postcondition before\n                # any original business recipe step is allowed to run again.\n                if operation_requires_postcondition_reconciliation(operation):\n                    await _auto_login_if_needed(\n                        db, operation, portal, page, context, session, credential\n                    )\n                    verified, verification_details = await verify_page(page, portal, verification)\n''',
    )

    replace_once(
        path,
        '''                    operation.current_step = index + 1\n                    operation.side_effect_step = None\n                    operation.side_effect_started_at = None\n                    operation.last_url = _safe_url_for_log(page.url)\n''',
        '''                    operation.current_step = index + 1\n                    # Keep the durable side-effect marker until the provider postcondition\n                    # is verified. Clearing it here would permit a later duplicate replay.\n                    operation.last_url = _safe_url_for_log(page.url)\n''',
    )

    replace_once(
        path,
        '''                        details={"kind": step.get("kind"), "result": result, "side_effect": side_effect},\n''',
        '''                        details={\n                            "kind": step.get("kind"),\n                            "result": result,\n                            "side_effect": side_effect,\n                            "replay_safe": replay_safe,\n                        },\n''',
    )

    replace_once(
        path,
        '''                if verified:\n                    operation.status = "verified"\n                    operation.verified_at = utcnow()\n                    operation.last_error = ""\n                    await _add_evidence(\n''',
        '''                if verified:\n                    operation.status = "verified"\n                    operation.verified_at = utcnow()\n                    operation.last_error = ""\n                    operation.side_effect_step = None\n                    operation.side_effect_started_at = None\n                    await _add_evidence(\n''',
    )

    replace_once(
        path,
        '''                else:\n                    operation.status = "failed"\n                    operation.last_error = "Browser plan ran, but the required provider postcondition was not verified"\n                    await _add_evidence(\n                        db,\n                        operation,\n                        page,\n                        evidence_type="browser_postcondition_failed",\n                        step_index=len(steps),\n                        details=verification_details,\n                        screenshot=True,\n                    )\n                    await _save_storage_state(db, context, session, status="ready", error=operation.last_error)\n''',
        '''                else:\n                    if operation.side_effect_step is not None:\n                        operation.status = "creation_uncertain"\n                        operation.last_error = (\n                            "Provider side effect may already have occurred, but the required postcondition "\n                            "is not yet verified. Automatic replay is blocked; VAAPP will reconcile only."\n                        )\n                        await _add_evidence(\n                            db,\n                            operation,\n                            page,\n                            evidence_type="browser_ambiguous_outcome",\n                            step_index=operation.side_effect_step,\n                            details={\n                                "postcondition_verified": False,\n                                "final_verification": verification_details,\n                                "automatic_replay": False,\n                            },\n                            screenshot=True,\n                        )\n                        await _save_storage_state(\n                            db, context, session, status="ambiguous", error=operation.last_error\n                        )\n                    else:\n                        operation.status = "failed"\n                        operation.last_error = (\n                            "Browser plan ran, but the required provider postcondition was not verified"\n                        )\n                        await _add_evidence(\n                            db,\n                            operation,\n                            page,\n                            evidence_type="browser_postcondition_failed",\n                            step_index=len(steps),\n                            details=verification_details,\n                            screenshot=True,\n                        )\n                        await _save_storage_state(\n                            db, context, session, status="ready", error=operation.last_error\n                        )\n''',
    )

    replace_once(
        path,
        '''    if operation.status not in {"needs_user_auth", "retry", "failed"}:\n        raise ValueError("Browser operation is not resumable in its current state")\n    if operation.status == "failed" and operation.side_effect_step is not None:\n        raise ValueError("A failed operation with an ambiguous side effect cannot be blindly retried")\n''',
        '''    if operation.status not in {"needs_user_auth", "retry", "failed", "creation_uncertain"}:\n        raise ValueError("Browser operation is not resumable in its current state")\n    if operation.status == "failed" and operation.side_effect_step is not None:\n        raise ValueError("A failed operation with an ambiguous side effect cannot be blindly retried")\n    if operation.status == "creation_uncertain" and operation.side_effect_step is None:\n        raise ValueError(\n            "An uncertain browser operation without a durable side-effect marker cannot be resumed safely"\n        )\n''',
    )

    replace_once(
        path,
        '''    except BrowserSecurityError as exc:\n        operation.status = "failed"\n        operation.last_error = str(exc)[:4000]\n        await db.commit()\n        return {"operation_id": operation.id, "status": operation.status}\n''',
        '''    except BrowserSecurityError as exc:\n        error = str(exc)[:3000]\n        if operation_requires_postcondition_reconciliation(operation):\n            operation.status = "creation_uncertain"\n            operation.last_error = (\n                f"Provider verification hit a browser security boundary: {error}. "\n                "Automatic replay remains blocked."\n            )[:4000]\n            operation.verify_after = utcnow() + timedelta(minutes=60)\n        else:\n            operation.status = "failed"\n            operation.last_error = error\n        await db.commit()\n        return {"operation_id": operation.id, "status": operation.status}\n''',
    )

    replace_once(
        path,
        '''    except PlaywrightTimeoutError as exc:\n        operation.last_error = f"Browser provider timeout: {exc}"[:4000]\n        operation.status = "retry" if operation.attempts < operation.max_attempts else "failed"\n        operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))\n        await db.commit()\n        if operation.status == "retry":\n            raise\n        return {"operation_id": operation.id, "status": operation.status}\n''',
        '''    except PlaywrightTimeoutError as exc:\n        operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))\n        if operation_requires_postcondition_reconciliation(operation):\n            operation.status = "creation_uncertain"\n            operation.last_error = (\n                f"Provider timeout during side-effect reconciliation: {exc}. "\n                "Automatic replay remains blocked."\n            )[:4000]\n            await db.commit()\n            return {"operation_id": operation.id, "status": operation.status}\n        operation.last_error = f"Browser provider timeout: {exc}"[:4000]\n        operation.status = "retry" if operation.attempts < operation.max_attempts else "failed"\n        await db.commit()\n        if operation.status == "retry":\n            raise\n        return {"operation_id": operation.id, "status": operation.status}\n''',
    )

    replace_once(
        path,
        '''    except Exception as exc:\n        message = str(exc)\n        if "Executable doesn't exist" in message or "playwright install" in message.lower():\n            operation.status = "blocked_capability"\n            operation.last_error = "Chromium browser runtime is not installed in the backend deployment"\n            await db.commit()\n            return {"operation_id": operation.id, "status": operation.status}\n        operation.last_error = message[:4000]\n        operation.status = "retry" if operation.attempts < operation.max_attempts else "failed"\n        operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))\n        await db.commit()\n        if operation.status == "retry":\n            raise\n        return {"operation_id": operation.id, "status": operation.status}\n''',
        '''    except Exception as exc:\n        message = str(exc)\n        if operation_requires_postcondition_reconciliation(operation):\n            operation.status = "creation_uncertain"\n            operation.last_error = (\n                f"Provider/runtime error during side-effect reconciliation: {message}. "\n                "Automatic replay remains blocked."\n            )[:4000]\n            operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))\n            await db.commit()\n            return {"operation_id": operation.id, "status": operation.status}\n        if "Executable doesn't exist" in message or "playwright install" in message.lower():\n            operation.status = "blocked_capability"\n            operation.last_error = "Chromium browser runtime is not installed in the backend deployment"\n            await db.commit()\n            return {"operation_id": operation.id, "status": operation.status}\n        operation.last_error = message[:4000]\n        operation.status = "retry" if operation.attempts < operation.max_attempts else "failed"\n        operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))\n        await db.commit()\n        if operation.status == "retry":\n            raise\n        return {"operation_id": operation.id, "status": operation.status}\n''',
    )


def patch_fulfillment_service(root: Path) -> None:
    path = root / "backend/app/services/fulfillment_service.py"
    replace_once(
        path,
        '''from app.services.browser_operator import enqueue_browser_operation, prepare_browser_operation\n''',
        '''from app.services.browser_operator import (\n    enqueue_browser_operation,\n    prepare_browser_operation,\n    resume_browser_operation,\n)\n''',
    )

    replace_once(
        path,
        '''        if operation.status in _BROWSER_FAILURE:\n            if request.request_type == "logistics":\n                await _tracking_browser_failure(db, request, action, operation)\n                return\n            action.status = "blocked_system" if operation.status == "creation_uncertain" else "failed"\n            action.last_error = operation.last_error or operation.status\n            request.status = "blocked_system" if operation.status == "creation_uncertain" else "failed"\n            request.last_error = action.last_error\n            await _sync_va_state(db, request)\n            return\n''',
        '''        if operation.status in _BROWSER_FAILURE:\n            if request.request_type == "logistics":\n                await _tracking_browser_failure(db, request, action, operation)\n                return\n            if operation.status == "creation_uncertain":\n                request.requires_user_action = False\n                request.needs_user_reason = ""\n                try:\n                    await resume_browser_operation(db, operation.id)\n                except ValueError as exc:\n                    action.status = "blocked_system"\n                    action.last_error = str(exc)[:8000]\n                    request.status = "blocked_system"\n                    request.last_error = action.last_error\n                    request.next_action_at = utcnow() + timedelta(minutes=60)\n                else:\n                    action.status = "waiting_provider"\n                    action.last_error = operation.last_error or (\n                        "Provider side effect is awaiting postcondition verification"\n                    )\n                    request.status = "waiting_provider"\n                    request.last_error = (\n                        "Provider side effect has an uncertain outcome; the original action is retained "\n                        "and only its postcondition will be rechecked. Automatic replay is blocked."\n                    )\n                    request.next_action_at = utcnow() + timedelta(minutes=30)\n                await _sync_va_state(db, request)\n                return\n            action.status = "failed"\n            action.last_error = operation.last_error or operation.status\n            request.status = "failed"\n            request.last_error = action.last_error\n            await _sync_va_state(db, request)\n            return\n''',
    )


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/tests/test_v111_fulfillment_side_effect_recovery.py",
        "backend/tests/test_v111_fulfillment_side_effect_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v111_fulfillment_side_effect_recovery_contract.py",
        "backend/tests/test_v111_fulfillment_side_effect_recovery_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.11_FULFILLMENT_SIDE_EFFECT_RECOVERY.md",
        "docs/V1.0.11_FULFILLMENT_SIDE_EFFECT_RECOVERY.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    status_text = read_text(status_path)
    if "# VAAPP v1.0.10 — Payment Recovery & Human Boundary Integrity" not in status_text:
        raise RuntimeError("unexpected STATUS.md baseline")
    status = '''# VAAPP v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression

Updated: 2026-08-30

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.10 source baseline: `4b3b38903545c8598695660c666c3080aff171e2`
- Verified v1.0.10 GitHub Actions run: `33328116694` — success
- Verified v1.0.10 prerelease tag: `va-android-110-4-1`
- v1.0.10 release identity: backend `1.0.10`, Android `1.0.10+53`

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.10.

## v1.0.11 maintenance scope

v1.0.11 prevents duplicate provider mutations when a browser action succeeds but its confirmation/postcondition is delayed or temporarily unverifiable.

- A non-replay-safe side-effect marker persists until explicit provider postcondition verification succeeds.
- Final postcondition failure after a possible side effect becomes `creation_uncertain`, never ordinary `failed`.
- The original FulfillmentAction and browser operation are retained; no new business idempotency key is created while the outcome is uncertain.
- Uncertain operations resume with a new workflow resume sequence in verification-only mode.
- Verification-only recovery may authenticate to the provider, but it checks the postcondition before any original business recipe step can execute.
- A still-missing postcondition remains VA-owned with another verification check scheduled.
- Security boundaries, provider timeouts, and runtime errors during verification-only recovery preserve uncertainty and cannot reopen recipe replay.
- Unsafe or structurally unrecoverable uncertainty becomes `blocked_system`, not Needs You.
- Genuine CAPTCHA/OTP/security authentication remains an explicit human boundary through the existing browser auth path.
- No database schema migration is required.

## Release identity

- Backend: `1.0.11`
- Required Android: `1.0.11`
- Android: `1.0.11+54`

This status file is committed only by the guarded v1.0.11 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
'''
    status_path.write_text(status, encoding="utf-8")

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.10":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-08-30",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.10",
            "verified_baseline_android_version": "1.0.10+53",
            "verified_maintenance_actions_run_id": 33328116694,
            "verified_baseline_release_tag": "va-android-110-4-1",
            "current_phase": "maintenance",
            "current_phase_name": "v1.0.11 Fulfillment Side-Effect Recovery & Duplicate Suppression",
            "current_version": "1.0.11",
            "current_android_version": "1.0.11+54",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "next_phase": "v1.x maintenance and real-world hardening",
            "v111_features": [
                "non-replay-safe browser side effects retain a durable ambiguity marker until provider verification",
                "missing postconditions after provider mutation become creation_uncertain instead of terminal failed",
                "uncertain fulfillment reuses the same action and browser operation instead of creating a duplicate action",
                "recovery resumes in provider-postcondition verification mode before any business recipe step can run",
                "unresolved ambiguity remains VA-owned and schedules another verification check",
                "verification-time security, timeout, and runtime failures cannot downgrade ambiguity to ordinary failed",
                "genuine portal authentication remains the only user boundary in this recovery path",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    new_invariant = "a non-replay-safe provider side effect is never repeated solely because its postcondition is missing"
    if new_invariant not in invariants:
        invariants.append(new_invariant)
    state["invariants"] = invariants
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff = root / "VAAPP_PROJECT_HANDOFF.md"
    old_prefix = '''# VAAPP project handoff

Updated: 2026-08-30
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `12afd780fdeb83fe89f0a6c3010d268dde683103` (`v1.0.9 — Briefing Ledger & Quiet Operations`). GitHub Actions run `33323619938` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-109-4-1`.

Verified v1.0.9 release identity: backend `1.0.9` / Android `1.0.9+52`. The operator subsequently reported production deployment and phone smoke testing complete.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.10` / Android `1.0.10+53`.

Current candidate: **v1.0.10 — Payment Recovery & Human Boundary Integrity**.

v1.0.10 repairs a human-boundary violation in payment creation recovery. A network drop or provider response without a payment identifier no longer fabricates a Needs You approval. The uncertain payment remains active and duplicate retry stays suppressed while the VA reconciles the exact source bank account for independent booked-transaction evidence. Exactly one strong match can prove completion; zero or multiple matches remain VA-owned and unresolved. Genuine bank SCA/authorization remains human-bound only when the provider supplied a real authorization URL tied to a provider payment identifier.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.

Next work after the v1.0.10 gate is green: **v1.x maintenance and real-world hardening**.

## Product objective
'''
    new_prefix = '''# VAAPP project handoff

Updated: 2026-08-30
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `4b3b38903545c8598695660c666c3080aff171e2` (`v1.0.10 — Payment Recovery & Human Boundary Integrity`). GitHub Actions run `33328116694` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-110-4-1`.

Verified v1.0.10 release identity: backend `1.0.10` / Android `1.0.10+53`. The operator subsequently reported production deployment and phone smoke testing complete.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.11` / Android `1.0.11+54`.

Current candidate: **v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression**.

v1.0.11 closes a duplicate-execution boundary in browser-backed Fulfillment. If a non-replay-safe provider action may already have happened but its postcondition is not yet visible, VAAPP retains the original action and browser operation, marks the outcome `creation_uncertain`, and performs verification-only revisits. Security boundaries, provider timeouts, and runtime errors during those revisits preserve the uncertainty state rather than reopening replay. It never creates a replacement business action merely because confirmation was delayed. Provider/system ambiguity remains VA-owned; genuine portal authentication remains a separate human boundary.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.

Next work after the v1.0.11 gate is green: **v1.x maintenance and real-world hardening**.

## Product objective
'''
    replace_once(handoff, old_prefix, new_prefix)


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.10"\nREQUIRED_ANDROID_VERSION = "1.0.10"\n',
        'APP_VERSION = "1.0.11"\nREQUIRED_ANDROID_VERSION = "1.0.11"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.10"', 'version = "1.0.11"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.10+53", "version: 1.0.11+54")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.10';\nconst String minimumBackendVersion = '1.0.10';\n",
        "const String appRelease = '1.0.11';\nconst String minimumBackendVersion = '1.0.11';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.10"', 'APP_VERSION = "1.0.11"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.10"', 'REQUIRED_ANDROID_VERSION = "1.0.11"'),
        ('version = "1.0.10"', 'version = "1.0.11"'),
        ('version: 1.0.10+53', 'version: 1.0.11+54'),
        ("appRelease = '1.0.10'", "appRelease = '1.0.11'"),
        ("minimumBackendVersion = '1.0.10'", "minimumBackendVersion = '1.0.11'"),
        ('APP_VERSION == "1.0.10"', 'APP_VERSION == "1.0.11"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v111_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.11")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    tracked = [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
    untracked = [
        line
        for line in run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
        if line
    ]
    changed = sorted(set(tracked + untracked))
    if not changed:
        raise RuntimeError("patch produced no changes")
    forbidden = [name for name in changed if name.startswith(".github/workflows/")]
    if forbidden:
        raise RuntimeError(f"workflow files changed unexpectedly: {forbidden}")
    required = {
        "backend/app/services/browser_operator.py",
        "backend/app/services/fulfillment_service.py",
        "backend/tests/test_v111_fulfillment_side_effect_recovery.py",
        "backend/tests/test_v111_fulfillment_side_effect_recovery_contract.py",
        "docs/V1.0.11_FULFILLMENT_SIDE_EFFECT_RECOVERY.md",
        "backend/app/core/version.py",
        "backend/pyproject.toml",
        "android/pubspec.yaml",
        "android/lib/release_contract.dart",
        "STATUS.md",
        "VAAPP_PROJECT_STATE.json",
        "VAAPP_PROJECT_HANDOFF.md",
    }
    missing = sorted(required.difference(changed))
    if missing:
        raise RuntimeError(f"required v1.0.11 changes missing from diff: {missing}")
    browser = read_text(root / "backend/app/services/browser_operator.py")
    fulfillment = read_text(root / "backend/app/services/fulfillment_service.py")
    if "operation_requires_postcondition_reconciliation" not in browser:
        raise RuntimeError("v1.0.11 browser reconciliation guard missing")
    if "Provider side effect may already have occurred" not in browser:
        raise RuntimeError("v1.0.11 ambiguous postcondition handling missing")
    if "Provider timeout during side-effect reconciliation" not in browser:
        raise RuntimeError("v1.0.11 timeout uncertainty preservation missing")
    if "Provider/runtime error during side-effect reconciliation" not in browser:
        raise RuntimeError("v1.0.11 runtime uncertainty preservation missing")
    if "await resume_browser_operation(db, operation.id)" not in fulfillment:
        raise RuntimeError("v1.0.11 fulfillment same-operation resume missing")
    if 'APP_VERSION = "1.0.11"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.11 backend version guard failed")
    if "version: 1.0.11+54" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.11 Android version guard failed")
    print("v1.0.11 source patch prepared. Changed files:")
    for name in changed:
        print(f"  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply VAAPP v1.0.11 Fulfillment Side-Effect Recovery & Duplicate Suppression"
    )
    parser.add_argument("repo", nargs="?", default=".", help="path to a clean VAAPP2 checkout")
    args = parser.parse_args()
    root = Path(args.repo).resolve()

    verify_bundle()
    verify_repo(root)
    write_new_files(root)
    patch_browser_operator(root)
    patch_fulfillment_service(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)


if __name__ == "__main__":
    main()
