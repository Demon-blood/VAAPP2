from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "8557dd449db554528ab7e111d0029faf784c996f"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256 = {
    "preview/backend/tests/test_v115_browser_late_evidence_recovery.py": "458cebabf8c6ee77f43306605968b6b9c63a3dd56c6115b50d5b10d8d5c00c90",
    "preview/backend/tests/test_v115_browser_late_evidence_recovery_contract.py": "5394095059ac3e524f9644b1a90a49abf1b6e082538725d6e5cc5ce242ea1bfe",
    "preview/docs/V1.0.15_BROWSER_LATE_EVIDENCE_RECOVERY.md": "96cecbbf7346917a8a76a67072328ee4c3925c87931b73d3c8f2d4aca4e0b34e",
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
        if len(expected) != 64:
            raise RuntimeError("bundle hashes were not finalized")
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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.14 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.14"\nREQUIRED_ANDROID_VERSION = "1.0.14"\n'
    ):
        raise RuntimeError("v1.0.14 backend baseline identity mismatch")
    if "version: 1.0.14+57" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.14 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_autonomous_core(root: Path) -> None:
    path = root / "backend/app/services/autonomous_core.py"

    execute_old = '''            elif operation.status == "creation_uncertain":
                step.status = "failed"
                step.finished_at = utcnow()
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=operation.last_error or "Browser side-effect outcome is ambiguous; VAAPP will not risk a duplicate submission",
                )
'''
    execute_new = '''            elif operation.status == "creation_uncertain":
                if not await _resume_browser_reconciliation(
                    db,
                    objective=objective,
                    step=step,
                    operation=operation,
                    now=utcnow(),
                ):
                    step.status = "failed"
                    step.finished_at = utcnow()
                    step.last_error = operation.last_error
                    await _transition_objective(
                        db,
                        objective,
                        "blocked_system",
                        reason=(
                            operation.last_error
                            or "Uncertain browser operation lacks a durable side-effect marker; safe replay cannot be proven"
                        ),
                    )
'''
    replace_once(path, execute_old, execute_new)

    verify_old = '''            if operation.status == "creation_uncertain":
                step.status = "failed"
                step.finished_at = now
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=operation.last_error or "Browser side-effect outcome is ambiguous; VAAPP will not blindly replay it",
                )
                continue
'''
    verify_new = '''            if operation.status == "creation_uncertain":
                if await _resume_browser_reconciliation(
                    db,
                    objective=objective,
                    step=step,
                    operation=operation,
                    now=now,
                ):
                    continue
                step.status = "failed"
                step.finished_at = now
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=(
                        operation.last_error
                        or "Uncertain browser operation lacks a durable side-effect marker; safe replay cannot be proven"
                    ),
                )
                continue
'''
    replace_once(path, verify_old, verify_new)

    helper = '''async def _resume_browser_reconciliation(
    db: AsyncSession,
    *,
    objective: VAObjective,
    step: VAObjectiveStep,
    operation: BrowserOperation,
    now: datetime,
) -> bool:
    """Resume one ambiguous browser side effect in provider-postcondition-only mode."""
    from app.services.browser_operator import (
        operation_requires_postcondition_reconciliation,
        resume_browser_operation,
    )

    if not operation_requires_postcondition_reconciliation(operation):
        return False
    try:
        await resume_browser_operation(db, operation.id)
    except (LookupError, ValueError):
        return False

    step.status = "verifying"
    step.finished_at = None
    step.last_error = ""
    step.run_after = max(operation.verify_after, now + timedelta(seconds=10))
    objective.last_error = ""
    await _transition_objective(db, objective, "verifying")
    return True


async def _recover_legacy_browser_uncertainty(db: AsyncSession, now: datetime) -> int:
    """Reopen historically failed generic browser steps when reconciliation is provably safe."""
    rows = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(
                    VAObjectiveStep.status == "failed",
                    VAObjectiveStep.verification_type == "browser_operation_verified",
                )
                .order_by(VAObjectiveStep.id.asc())
                .limit(100)
            )
        ).scalars()
    )
    recovered = 0
    for step in rows:
        params = _loads(step.parameters_json, {})
        params = params if isinstance(params, dict) else {}
        operation_id = int(params.get("browser_operation_id") or 0)
        operation = await db.get(BrowserOperation, operation_id) if operation_id > 0 else None
        if operation is None or operation.status != "creation_uncertain":
            continue
        objective = await db.get(VAObjective, step.objective_id)
        if (
            objective is None
            or objective.status in TERMINAL_OBJECTIVE_STATES
            or objective.status == "needs_user"
        ):
            continue
        if not await _resume_browser_reconciliation(
            db,
            objective=objective,
            step=step,
            operation=operation,
            now=now,
        ):
            continue
        await write_audit(
            db,
            "browser_operation_legacy_uncertainty_reopened",
            entity_type="browser_operation",
            entity_id=str(operation.id),
            result="deferred",
            details={
                "objective_id": objective.id,
                "step_id": step.id,
                "automatic_replay": False,
                "recovery": "provider_postcondition_only",
            },
        )
        recovered += 1
    if recovered:
        await db.commit()
    return recovered


'''
    replace_once(
        path,
        "async def _recover_legacy_gmail_uncertainty(db: AsyncSession, now: datetime) -> int:\n",
        helper + "async def _recover_legacy_gmail_uncertainty(db: AsyncSession, now: datetime) -> int:\n",
    )

    replace_once(
        path,
        '''async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:
    now = utcnow()
    await _recover_legacy_gmail_uncertainty(db, now)
''',
        '''async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:
    now = utcnow()
    await _recover_legacy_browser_uncertainty(db, now)
    await _recover_legacy_gmail_uncertainty(db, now)
''',
    )


def patch_document_ownership(root: Path) -> None:
    path = root / "backend/app/services/document_ownership.py"
    old = '''                if operation.status in {"creation_uncertain", "failed"}:
                    row.status = "blocked_system"
                    row.last_error = operation.last_error
                    if submission is not None:
                        submission.status = row.status
                        submission.last_error = row.last_error
                    result["blocked"] += 1
                    continue
'''
    new = '''                if operation.status == "creation_uncertain":
                    from app.services.browser_operator import operation_requires_postcondition_reconciliation

                    row.last_error = operation.last_error
                    if operation_requires_postcondition_reconciliation(operation):
                        # The same BrowserOperation is still owned by the Autonomous Core and
                        # may only perform provider-postcondition reconciliation. Do not project
                        # active duplicate-safe recovery as a terminal document failure.
                        row.status = "in_progress"
                        if submission is not None:
                            submission.status = "in_progress"
                            submission.last_error = row.last_error
                        result["in_progress"] += 1
                    else:
                        # Without the durable marker, the browser runtime cannot prove that
                        # reconciliation-only resume is safe. Keep the obligation system-blocked.
                        row.status = "blocked_system"
                        if submission is not None:
                            submission.status = row.status
                            submission.last_error = row.last_error
                        result["blocked"] += 1
                    continue
                if operation.status == "failed":
                    row.status = "blocked_system"
                    row.last_error = operation.last_error
                    if submission is not None:
                        submission.status = row.status
                        submission.last_error = row.last_error
                    result["blocked"] += 1
                    continue
'''
    replace_once(path, old, new)


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/tests/test_v115_browser_late_evidence_recovery.py",
        "backend/tests/test_v115_browser_late_evidence_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v115_browser_late_evidence_recovery_contract.py",
        "backend/tests/test_v115_browser_late_evidence_recovery_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.15_BROWSER_LATE_EVIDENCE_RECOVERY.md",
        "docs/V1.0.15_BROWSER_LATE_EVIDENCE_RECOVERY.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    baseline_status = read_text(status_path)
    if "# VAAPP v1.0.14 — Own-Account Transfer Recovery & Human Boundary Integrity" not in baseline_status:
        raise RuntimeError("unexpected STATUS.md baseline")
    status = '''# VAAPP v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.14 source baseline: `8557dd449db554528ab7e111d0029faf784c996f`
- Verified v1.0.14 GitHub Actions run: `33961135886` — success
- Verified v1.0.14 prerelease tag: `va-android-114-3-1`
- v1.0.14 release identity: backend `1.0.14`, Android `1.0.14+57`
- v1.0.14 APK SHA-256: `1fae494cb449c48a65997f80709b9404a533b76297144a713f0b574f05d2d4c2`
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.14.

## v1.0.15 maintenance scope

v1.0.15 keeps generic secure-browser side-effect uncertainty under active VA-owned provider reconciliation instead of terminalizing the objective step.

- `creation_uncertain` is resumed only when the durable v1.0.11 side-effect marker proves postcondition-only reconciliation is safe.
- The same BrowserOperation is reused; no second operation or provider mutation is created.
- Provider postconditions are checked before any original business recipe step can execute again.
- Safe uncertainty keeps the objective step `verifying` and clears its historical `finished_at` marker.
- Historical failed generic browser steps are reopened automatically when their linked operation is still safely reconcilable.
- Markerless uncertainty remains `blocked_system`; replay safety is never guessed.
- Genuine portal authentication remains a real Needs You boundary.
- Definitive browser failure remains system-owned.
- Document/form obligations show active uncertainty as `in_progress` and complete only from verified browser evidence.
- No database schema migration is required.

## Release identity

- Backend: `1.0.15`
- Required Android: `1.0.15`
- Android: `1.0.15+58`

This status file is committed only by the guarded v1.0.15 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
'''
    status_path.write_text(status, encoding="utf-8")

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.14":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-09-05",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.14",
            "verified_baseline_android_version": "1.0.14+57",
            "verified_maintenance_actions_run_id": 33961135886,
            "verified_baseline_release_tag": "va-android-114-3-1",
            "current_phase": "maintenance",
            "current_phase_name": "v1.0.15 Generic Browser Late-Evidence Recovery & Objective Continuity",
            "current_version": "1.0.15",
            "current_android_version": "1.0.15+58",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "next_phase": "v1.x maintenance and real-world hardening",
            "v115_features": [
                "generic browser creation uncertainty remains actively provider-reconciliation-owned",
                "safe recovery reuses the same BrowserOperation and durable side-effect marker",
                "provider postcondition verification runs before any original business recipe step",
                "historical failed browser verification steps reopen automatically when safe",
                "markerless ambiguity remains blocked_system and never authorizes replay",
                "document form obligations remain in_progress while duplicate-safe browser reconciliation runs",
                "genuine portal authentication and definitive system failure remain distinct boundaries",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    new_invariant = (
        "ambiguous generic browser side effects remain provider-reconciliation-owned and never authorize replay"
    )
    if new_invariant not in invariants:
        invariants.append(new_invariant)
    state["invariants"] = invariants
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff_path = root / "VAAPP_PROJECT_HANDOFF.md"
    handoff = read_text(handoff_path)
    if "Current candidate: **v1.0.14 — Own-Account Transfer Recovery & Human Boundary Integrity**." not in handoff:
        raise RuntimeError("unexpected VAAPP_PROJECT_HANDOFF.md baseline")
    marker = "## Product objective\n"
    if marker not in handoff:
        raise RuntimeError("handoff product-objective marker is missing")
    suffix = marker + handoff.split(marker, 1)[1]
    prefix = '''# VAAPP project handoff

Updated: 2026-09-05
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `8557dd449db554528ab7e111d0029faf784c996f` (`v1.0.14 — Own-Account Transfer Recovery & Human Boundary Integrity`). GitHub Actions run `33961135886` completed successfully end-to-end, including 380 backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-114-3-1`.

Verified v1.0.14 release identity: backend `1.0.14` / Android `1.0.14+57`. APK SHA-256: `1fae494cb449c48a65997f80709b9404a533b76297144a713f0b574f05d2d4c2`. The operator subsequently reported production deployment and phone smoke testing complete.

Historical v1.0.13 source remains `ecaa113d4461a550cb49c6046a42ecf880729346` with successful Actions run `33434347111` and tag `va-android-113-4-1`. Historical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.15` / Android `1.0.15+58`.

Current candidate: **v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity**.

v1.0.15 closes a continuity gap above the v1.0.11 browser side-effect guard. A generic browser objective whose provider mutation may already have happened no longer terminalizes merely because the immediate postcondition is missing. When the durable side-effect marker proves reconciliation-only resume is safe, VAAPP reuses the same BrowserOperation, verifies the provider postcondition before any original business step, keeps the objective in `verifying`, and reopens historical failed verification steps. Markerless uncertainty still fails closed, genuine portal authentication still remains a user boundary, and definitive provider failure remains system-owned. Document/form obligations project active uncertainty as `in_progress` until the linked browser operation is independently verified.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.

Next work after the v1.0.15 gate is green: **v1.x maintenance and real-world hardening**.

'''
    handoff_path.write_text(prefix + suffix, encoding="utf-8")



def patch_legacy_v095_form_contract(root: Path) -> None:
    """Advance the historical form contract to the v1.0.15 uncertainty semantics."""
    path = root / "backend/tests/test_v095_documents_forms_deadlines_contract.py"
    old = '''def test_form_completion_requires_verified_browser_operation() -> None:
    source = (_root() / "backend/app/services/document_ownership.py").read_text()
    assert 'if operation.status == "verified":' in source
    assert 'row.status = "completed"' in source
    assert 'submission.status = "verified"' in source
    assert 'row.completed_at = operation.verified_at or now' in source
    assert 'if operation.status in {"creation_uncertain", "failed"}:' in source
    assert 'row.status = "blocked_system"' in source
    assert 'operation.status == "needs_user_auth"' in source
    assert 'operation.challenge_type == "form_input"' in source
'''
    new = '''def test_form_completion_requires_verified_browser_operation() -> None:
    source = (_root() / "backend/app/services/document_ownership.py").read_text()
    assert 'if operation.status == "verified":' in source
    assert 'row.status = "completed"' in source
    assert 'submission.status = "verified"' in source
    assert 'row.completed_at = operation.verified_at or now' in source
    assert 'if operation.status == "creation_uncertain":' in source
    assert 'operation_requires_postcondition_reconciliation(operation)' in source
    assert 'row.status = "in_progress"' in source
    assert 'if operation.status == "failed":' in source
    assert 'row.status = "blocked_system"' in source
    assert 'operation.status == "needs_user_auth"' in source
    assert 'operation.challenge_type == "form_input"' in source
'''
    replace_once(path, old, new)

def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.14"\nREQUIRED_ANDROID_VERSION = "1.0.14"\n',
        'APP_VERSION = "1.0.15"\nREQUIRED_ANDROID_VERSION = "1.0.15"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.14"', 'version = "1.0.15"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.14+57", "version: 1.0.15+58")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.14';\nconst String minimumBackendVersion = '1.0.14';\n",
        "const String appRelease = '1.0.15';\nconst String minimumBackendVersion = '1.0.15';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.14"', 'APP_VERSION = "1.0.15"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.14"', 'REQUIRED_ANDROID_VERSION = "1.0.15"'),
        ('version = "1.0.14"', 'version = "1.0.15"'),
        ('version: 1.0.14+57', 'version: 1.0.15+58'),
        ("appRelease = '1.0.14'", "appRelease = '1.0.15'"),
        ("minimumBackendVersion = '1.0.14'", "minimumBackendVersion = '1.0.15'"),
        ('APP_VERSION == "1.0.14"', 'APP_VERSION == "1.0.15"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v115_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.15")


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
        "backend/app/services/autonomous_core.py",
        "backend/app/services/document_ownership.py",
        "backend/tests/test_v115_browser_late_evidence_recovery.py",
        "backend/tests/test_v115_browser_late_evidence_recovery_contract.py",
        "docs/V1.0.15_BROWSER_LATE_EVIDENCE_RECOVERY.md",
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
        raise RuntimeError(f"required v1.0.15 changes missing from diff: {missing}")

    core = read_text(root / "backend/app/services/autonomous_core.py")
    documents = read_text(root / "backend/app/services/document_ownership.py")
    browser = read_text(root / "backend/app/services/browser_operator.py")
    for marker in (
        "async def _resume_browser_reconciliation",
        "operation_requires_postcondition_reconciliation",
        "resume_browser_operation",
        "async def _recover_legacy_browser_uncertainty",
        "browser_operation_legacy_uncertainty_reopened",
        "await _recover_legacy_browser_uncertainty(db, now)",
        '"recovery": "provider_postcondition_only"',
    ):
        if marker not in core:
            raise RuntimeError(f"v1.0.15 Autonomous Core marker missing: {marker}")
    if core.count("await _resume_browser_reconciliation(") < 3:
        raise RuntimeError("v1.0.15 must cover execution, verification, and historical browser uncertainty")
    for marker in (
        'if operation.status == "creation_uncertain":',
        'operation_requires_postcondition_reconciliation(operation)',
        'row.status = "in_progress"',
        'submission.status = "in_progress"',
        'row.status = "blocked_system"',
        'if operation.status == "failed":',
    ):
        if marker not in documents:
            raise RuntimeError(f"v1.0.15 document projection marker missing: {marker}")
    if "def operation_requires_postcondition_reconciliation" not in browser:
        raise RuntimeError("v1.0.11 browser reconciliation safety primitive was lost")
    if 'operation.status == "creation_uncertain" and operation.side_effect_step is None' not in browser:
        raise RuntimeError("markerless browser uncertainty must remain non-resumable")
    if 'APP_VERSION = "1.0.15"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.15 backend version missing")
    if "version: 1.0.15+58" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.15 Android version missing")
    status = read_text(root / "STATUS.md")
    state = read_text(root / "VAAPP_PROJECT_STATE.json")
    handoff = read_text(root / "VAAPP_PROJECT_HANDOFF.md")
    for historical in ("v1.0.14", "v1.0.13", "v1.0.12", "v1.0.11"):
        if historical not in status:
            raise RuntimeError(f"historical status evidence missing: {historical}")
    if '"verified_baseline_actions_run": 41' not in state:
        raise RuntimeError("original production v1 baseline run was not preserved")
    if '"verified_baseline_actions_conclusion": "success"' not in state:
        raise RuntimeError("original production v1 baseline conclusion was not preserved")
    if "GitHub Actions run #41" not in handoff:
        raise RuntimeError("original production v1 handoff evidence was not preserved")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    verify_bundle()
    verify_repo(root)
    patch_autonomous_core(root)
    patch_document_ownership(root)
    write_new_files(root)
    patch_project_metadata(root)
    patch_legacy_v095_form_contract(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.15 source patch prepared. Changed files:")
    tracked = run_git(root, "diff", "--name-only").splitlines()
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    for name in sorted(set(tracked + untracked)):
        if name:
            print(f"  {name}")


if __name__ == "__main__":
    main()
