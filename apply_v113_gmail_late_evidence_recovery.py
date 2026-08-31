from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "22a392f1341ef19caf8a761cd7bfa44000fdc08c"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256 = {
    "preview/backend/tests/test_v113_gmail_late_evidence_recovery.py": "017b58ee325ace6954c99043279b49697ad4624648cf9e065c5921d5c5bdbac2",
    "preview/backend/tests/test_v113_gmail_late_evidence_recovery_contract.py": "c6d0852af96b043248cd1ddcf56b183b9f0e8d5bee15647f11684dd00f4574bb",
    "preview/docs/V1.0.13_GMAIL_LATE_EVIDENCE_RECOVERY.md": "c520d084fa719857a3fe91ab01eb2a70d935f3ec3fd8d0b3e4c79aa6382d76d2",
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
    if not EXPECTED_PREVIEW_SHA256:
        raise RuntimeError("bundle hashes were not finalized")
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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.12 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.12"\nREQUIRED_ANDROID_VERSION = "1.0.12"\n'
    ):
        raise RuntimeError("v1.0.12 backend baseline identity mismatch")
    if "version: 1.0.12+55" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.12 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_gmail_delivery(root: Path) -> None:
    path = root / "backend/app/services/gmail_delivery.py"

    replace_once(
        path,
        '''    if row.status in {"creation_uncertain", "sent_unverified"}:\n        row.verify_after = max(row.verify_after, now + timedelta(minutes=2))\n        await db.commit()\n        return row\n    if row.attempts >= row.max_attempts:\n''',
        '''    if row.status in {"creation_uncertain", "sent_unverified"}:\n        row.verify_after = max(row.verify_after, now + timedelta(minutes=2))\n        await db.commit()\n        return row\n    if row.status == "failed_uncertain":\n        # Historical v1.0.12 rows are reconciliation-only. Never turn this legacy\n        # state into permission to submit the same provider intent again.\n        row.status = "creation_uncertain"\n        row.verify_after = max(row.verify_after, now + timedelta(minutes=2))\n        row.last_error = (\n            "Historical Gmail delivery ambiguity returned to provider reconciliation; "\n            "automatic resend remains disabled"\n        )\n        await db.commit()\n        return row\n    if row.attempts >= row.max_attempts:\n''',
    )

    old_ensure = '''async def ensure_gmail_outbound_verified(db: AsyncSession, row: GmailOutboundMessage) -> bool:\n    if row.status == "verified":\n        return True\n    now = utcnow()\n    try:\n        if await reconcile_gmail_outbound(db, row):\n            return True\n    except Exception as exc:\n        row.last_error = f"verification failed: {exc}"[:8000]\n        row.verify_after = now + timedelta(minutes=2)\n        await db.commit()\n        return False\n\n    if row.status in {"creation_uncertain", "sent_unverified"}:\n        # Provider execution may already have happened. Never re-POST. Keep checking\n        # for up to thirty minutes, then fail closed so the objective is surfaced as\n        # a system/provider ambiguity rather than risking a duplicate communication.\n        if now - row.created_at >= timedelta(minutes=30):\n            row.status = "failed_uncertain"\n            row.last_error = (\n                "Gmail send outcome remained ambiguous for 30 minutes; automatic resend is disabled "\n                "until provider state can be independently established"\n            )\n        else:\n            row.verify_after = now + timedelta(minutes=2)\n        await db.commit()\n        return False\n\n    if row.status == "prepared" and row.verify_after <= now:\n        await send_or_reconcile_gmail_outbound(db, row)\n        return row.status == "verified"\n    return False\n'''

    new_ensure = '''def _gmail_uncertain_verify_delay(row: GmailOutboundMessage, now: datetime) -> timedelta:\n    age = now - row.created_at\n    if age < timedelta(minutes=30):\n        return timedelta(minutes=2)\n    if age < timedelta(hours=24):\n        return timedelta(minutes=15)\n    if age < timedelta(days=7):\n        return timedelta(hours=1)\n    return timedelta(hours=6)\n\n\nasync def ensure_gmail_outbound_verified(db: AsyncSession, row: GmailOutboundMessage) -> bool:\n    if row.status == "verified":\n        return True\n    now = utcnow()\n\n    # v1.0.12 used failed_uncertain as a terminal timeout after thirty minutes.\n    # It is historical input only now: provider ambiguity stays VA-owned and the\n    # stable RFC Message-ID remains the sole recovery key. No provider re-POST.\n    if row.status == "failed_uncertain":\n        row.status = "creation_uncertain"\n        row.last_error = (\n            "Historical Gmail delivery ambiguity returned to provider reconciliation; "\n            "automatic resend remains disabled"\n        )\n\n    try:\n        if await reconcile_gmail_outbound(db, row):\n            return True\n    except Exception as exc:\n        row.last_error = f"verification failed: {exc}"[:8000]\n        delay = (\n            _gmail_uncertain_verify_delay(row, now)\n            if row.status in {"creation_uncertain", "sent_unverified"}\n            else timedelta(minutes=2)\n        )\n        row.verify_after = now + delay\n        await db.commit()\n        return False\n\n    if row.status in {"creation_uncertain", "sent_unverified"}:\n        # Provider execution may already have happened. Never re-POST. The fast\n        # verification window becomes a bounded long-term reconciliation cadence\n        # instead of an arbitrary terminal failure. Late Sent evidence can still\n        # complete the original durable objective days later.\n        row.verify_after = now + _gmail_uncertain_verify_delay(row, now)\n        if now - row.created_at >= timedelta(minutes=30):\n            row.last_error = (\n                "Gmail send outcome remains ambiguous; VAAPP will continue provider reconciliation "\n                "using the stable RFC Message-ID without resending this provider intent"\n            )\n        await db.commit()\n        return False\n\n    if row.status == "prepared" and row.verify_after <= now:\n        await send_or_reconcile_gmail_outbound(db, row)\n        return row.status == "verified"\n    return False\n'''
    replace_once(path, old_ensure, new_ensure)

    replace_once(
        path,
        '''        # Timeouts, connection resets, 429 and 5xx can happen after Gmail accepted\n        # the request. Never blind-retry this provider intent. Later cycles only\n        # reconcile Sent by the stable RFC Message-ID; unresolved ambiguity fails\n        # closed instead of creating a duplicate message.\n''',
        '''        # Timeouts, connection resets, 429 and 5xx can happen after Gmail accepted\n        # the request. Never blind-retry this provider intent. Later cycles only\n        # reconcile Sent by the stable RFC Message-ID; unresolved ambiguity remains\n        # VA-owned instead of creating a duplicate message.\n''',
    )


def patch_autonomous_core(root: Path) -> None:
    path = root / "backend/app/services/autonomous_core.py"

    helper = '''\n\nasync def _recover_legacy_gmail_uncertainty(db: AsyncSession, now: datetime) -> int:\n    rows = list(\n        (\n            await db.execute(\n                select(GmailOutboundMessage)\n                .where(GmailOutboundMessage.status == "failed_uncertain")\n                .order_by(GmailOutboundMessage.id.asc())\n                .limit(100)\n            )\n        ).scalars()\n    )\n    recovered = 0\n    for outbound in rows:\n        outbound.status = "creation_uncertain"\n        outbound.verify_after = now\n        outbound.last_error = (\n            "Historical Gmail delivery ambiguity returned to provider reconciliation; "\n            "automatic resend remains disabled"\n        )\n        if outbound.step_id:\n            step = await db.get(VAObjectiveStep, outbound.step_id)\n            if (\n                step is not None\n                and step.verification_type == "gmail_outbound_verified"\n                and step.status == "failed"\n            ):\n                objective = await db.get(VAObjective, step.objective_id)\n                if (\n                    objective is not None\n                    and objective.status not in TERMINAL_OBJECTIVE_STATES\n                    and objective.status != "needs_user"\n                ):\n                    step.status = "verifying"\n                    step.finished_at = None\n                    step.run_after = now\n                    step.last_error = ""\n        await write_audit(\n            db,\n            "gmail_outbound_legacy_uncertainty_reopened",\n            entity_type="gmail_outbound_message",\n            entity_id=str(outbound.id),\n            result="deferred",\n            details={\n                "rfc_message_id": outbound.rfc_message_id,\n                "automatic_resend": False,\n                "recovery": "provider_reconciliation_only",\n            },\n        )\n        recovered += 1\n    if recovered:\n        await db.commit()\n    return recovered\n'''
    replace_once(
        path,
        "\n\nasync def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:\n",
        helper + "\n\nasync def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:\n",
    )

    replace_once(
        path,
        '''async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:\n    now = utcnow()\n    steps = list(\n''',
        '''async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:\n    now = utcnow()\n    await _recover_legacy_gmail_uncertainty(db, now)\n    steps = list(\n''',
    )

    replace_once(
        path,
        '''            if outbound.status in {"failed", "failed_uncertain"}:\n                step.status = "failed"\n                step.finished_at = now\n                step.last_error = outbound.last_error\n                reason = (\n                    "Gmail provider outcome is ambiguous; automatic duplicate send is disabled"\n                    if outbound.status == "failed_uncertain"\n                    else (outbound.last_error or "Gmail send failed without a verified outcome")\n                )\n                await _transition_objective(db, objective, "blocked_system", reason=reason, error=outbound.last_error)\n                continue\n''',
        '''            if outbound.status == "failed":\n                step.status = "failed"\n                step.finished_at = now\n                step.last_error = outbound.last_error\n                reason = outbound.last_error or "Gmail send failed without a verified outcome"\n                await _transition_objective(db, objective, "blocked_system", reason=reason, error=outbound.last_error)\n                continue\n''',
    )


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/tests/test_v113_gmail_late_evidence_recovery.py",
        "backend/tests/test_v113_gmail_late_evidence_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v113_gmail_late_evidence_recovery_contract.py",
        "backend/tests/test_v113_gmail_late_evidence_recovery_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.13_GMAIL_LATE_EVIDENCE_RECOVERY.md",
        "docs/V1.0.13_GMAIL_LATE_EVIDENCE_RECOVERY.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.12 — Telephony Creation Recovery & Retry Integrity" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status = '''# VAAPP v1.0.13 — Gmail Late-Evidence Recovery & Delivery Continuity\n\nUpdated: 2026-08-30\n\n## Source of truth\n\n- Repository: `Demon-blood/VAAPP2`\n- Branch: `main`\n- Verified v1.0.12 source baseline: `22a392f1341ef19caf8a761cd7bfa44000fdc08c`\n- Verified v1.0.12 GitHub Actions run: `33333446575` — success\n- Verified v1.0.12 prerelease tag: `va-android-112-2-1`\n- v1.0.12 release identity: backend `1.0.12`, Android `1.0.12+55`\n- Historical v1.0.11 evidence remains preserved: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.\n\nThe operator subsequently reported production deployment and phone smoke testing complete for v1.0.12.\n\n## v1.0.13 maintenance scope\n\nv1.0.13 keeps ambiguous Gmail provider delivery under continuous VA ownership instead of abandoning it after an arbitrary thirty-minute window.\n\n- A possibly accepted Gmail send is never automatically submitted a second time.\n- The deterministic RFC Message-ID remains the stable provider evidence and idempotency key.\n- Fresh ambiguity is reconciled every two minutes, then backs off to fifteen minutes, one hour, and six hours for long-lived uncertainty.\n- Elapsed time alone never converts `creation_uncertain` or `sent_unverified` into terminal failure.\n- Late Gmail Sent evidence can complete the original durable objective after the old thirty-minute boundary.\n- Provider verification outages preserve VA-owned uncertainty and do not create Needs You work.\n- Historical `failed_uncertain` rows are migrated back to `creation_uncertain` reconciliation-only state.\n- Historical Gmail objective steps failed solely by the old ambiguity cutoff are reopened as `verifying`.\n- Definitive Gmail request failures remain system failures.\n- Genuine Gmail authentication/authorization remains the existing `failed_user` human boundary.\n- No database schema migration is required.\n\n## Release identity\n\n- Backend: `1.0.13`\n- Required Android: `1.0.13`\n- Android: `1.0.13+56`\n\nThis status file is committed only by the guarded v1.0.13 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.\n'''
    status_path.write_text(status, encoding="utf-8")

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.12":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-08-30",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.12",
            "verified_baseline_android_version": "1.0.12+55",
            "verified_maintenance_actions_run_id": 33333446575,
            "verified_baseline_release_tag": "va-android-112-2-1",
            "current_phase": "maintenance",
            "current_phase_name": "v1.0.13 Gmail Late-Evidence Recovery & Delivery Continuity",
            "current_version": "1.0.13",
            "current_android_version": "1.0.13+56",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "next_phase": "v1.x maintenance and real-world hardening",
            "v113_features": [
                "ambiguous Gmail sends remain reconciliation-owned beyond the former thirty-minute cutoff",
                "the stable RFC Message-ID remains the sole provider-evidence key and the send is never repeated",
                "verification cadence backs off from two minutes to fifteen minutes, one hour, then six hours",
                "late Gmail Sent evidence can complete the original durable objective",
                "historical failed_uncertain rows and their failed verification steps are reopened safely",
                "provider verification outages remain VA-owned and create no fake Needs You",
                "definitive Gmail failures and genuine authentication boundaries remain distinct",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    new_invariant = (
        "ambiguous Gmail delivery remains provider-reconciliation-only and never authorizes a duplicate send"
    )
    if new_invariant not in invariants:
        invariants.append(new_invariant)
    state["invariants"] = invariants
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff = root / "VAAPP_PROJECT_HANDOFF.md"
    old_prefix = '''# VAAPP project handoff\n\nUpdated: 2026-08-30\nRepository: `Demon-blood/VAAPP2`  \nBranch: `main`\n\n## Verified source of truth\n\nPhases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `221205e82444f9c0bff2589cf3ffc015408e664a` (`v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression`). GitHub Actions run `33331650005` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-111-2-1`.\n\nVerified v1.0.11 release identity: backend `1.0.11` / Android `1.0.11+54`. The operator subsequently reported production deployment and phone smoke testing complete.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current maintenance candidate\n\nBackend `1.0.12` / Android `1.0.12+55`.\n\nCurrent candidate: **v1.0.12 — Telephony Creation Recovery & Retry Integrity**.\n\nv1.0.12 closes the remaining ambiguity gap in outbound Twilio call creation. If the irreversible provider create request may have succeeded but VAAPP missed both the response and the normal CallSid callback, VAAPP searches authenticated Twilio call history using exact source/destination numbers and a narrow local creation-time match. Exactly one unbound `outbound-api` candidate is required to recover the existing call intent. Zero, multiple, paginated, or unavailable provider evidence remains VA-owned and cannot trigger a redial. Follow-up retry is permitted only after the previous attempt has a real CallSid and a terminal provider state. Genuine material voice-conversation decisions and authentication remain separate human boundaries.\n\nThe guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.\n\nNext work after the v1.0.12 gate is green: **v1.x maintenance and real-world hardening**.\n\n## Product objective\n'''
    new_prefix = '''# VAAPP project handoff\n\nUpdated: 2026-08-30\nRepository: `Demon-blood/VAAPP2`  \nBranch: `main`\n\n## Verified source of truth\n\nPhases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `22a392f1341ef19caf8a761cd7bfa44000fdc08c` (`v1.0.12 — Telephony Creation Recovery & Retry Integrity`). GitHub Actions run `33333446575` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-112-2-1`.\n\nVerified v1.0.12 release identity: backend `1.0.12` / Android `1.0.12+55`. The operator subsequently reported production deployment and phone smoke testing complete.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current maintenance candidate\n\nBackend `1.0.13` / Android `1.0.13+56`.\n\nCurrent candidate: **v1.0.13 — Gmail Late-Evidence Recovery & Delivery Continuity**.\n\nv1.0.13 removes the arbitrary thirty-minute abandonment boundary from Gmail delivery reconciliation. Once a Gmail provider submission may have happened, VAAPP keeps the original deterministic RFC Message-ID under reconciliation and never re-POSTs that provider intent. Provider checks back off to a bounded long-term cadence, and late Sent evidence can complete the original durable objective. Historical `failed_uncertain` rows and their previously failed Gmail verification steps are safely reopened. Provider/system ambiguity remains VA-owned; genuine Gmail authentication remains a separate human boundary.\n\nThe guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.\n\nNext work after the v1.0.13 gate is green: **v1.x maintenance and real-world hardening**.\n\n## Product objective\n'''
    replace_once(handoff, old_prefix, new_prefix)


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.12"\nREQUIRED_ANDROID_VERSION = "1.0.12"\n',
        'APP_VERSION = "1.0.13"\nREQUIRED_ANDROID_VERSION = "1.0.13"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.12"', 'version = "1.0.13"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.12+55", "version: 1.0.13+56")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.12';\nconst String minimumBackendVersion = '1.0.12';\n",
        "const String appRelease = '1.0.13';\nconst String minimumBackendVersion = '1.0.13';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.12"', 'APP_VERSION = "1.0.13"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.12"', 'REQUIRED_ANDROID_VERSION = "1.0.13"'),
        ('version = "1.0.12"', 'version = "1.0.13"'),
        ('version: 1.0.12+55', 'version: 1.0.13+56'),
        ("appRelease = '1.0.12'", "appRelease = '1.0.13'"),
        ("minimumBackendVersion = '1.0.12'", "minimumBackendVersion = '1.0.13'"),
        ('APP_VERSION == "1.0.12"', 'APP_VERSION == "1.0.13"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v113_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.13")


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
        "backend/app/services/gmail_delivery.py",
        "backend/app/services/autonomous_core.py",
        "backend/tests/test_v113_gmail_late_evidence_recovery.py",
        "backend/tests/test_v113_gmail_late_evidence_recovery_contract.py",
        "docs/V1.0.13_GMAIL_LATE_EVIDENCE_RECOVERY.md",
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
        raise RuntimeError(f"required v1.0.13 changes missing from diff: {missing}")

    delivery = read_text(root / "backend/app/services/gmail_delivery.py")
    core = read_text(root / "backend/app/services/autonomous_core.py")
    for marker in (
        "def _gmail_uncertain_verify_delay",
        'if row.status == "failed_uncertain":',
        "continue provider reconciliation",
        "without resending",
        "timedelta(hours=6)",
    ):
        if marker not in delivery:
            raise RuntimeError(f"v1.0.13 Gmail marker missing: {marker}")
    if 'row.status = "failed_uncertain"' in delivery:
        raise RuntimeError("v1.0.13 must not terminalize Gmail ambiguity as failed_uncertain")
    for marker in (
        "async def _recover_legacy_gmail_uncertainty",
        'GmailOutboundMessage.status == "failed_uncertain"',
        "await _recover_legacy_gmail_uncertainty(db, now)",
        'if outbound.status == "failed":',
        "gmail_outbound_legacy_uncertainty_reopened",
    ):
        if marker not in core:
            raise RuntimeError(f"v1.0.13 autonomous-core marker missing: {marker}")
    if 'if outbound.status in {"failed", "failed_uncertain"}:' in core:
        raise RuntimeError("autonomous core still terminalizes Gmail provider ambiguity")
    if 'APP_VERSION = "1.0.13"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.13 backend version missing")
    if "version: 1.0.13+56" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.13 Android version missing")
    state = read_text(root / "VAAPP_PROJECT_STATE.json")
    handoff = read_text(root / "VAAPP_PROJECT_HANDOFF.md")
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
    patch_gmail_delivery(root)
    patch_autonomous_core(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.13 source patch prepared. Changed files:")
    tracked = run_git(root, "diff", "--name-only").splitlines()
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    for name in sorted(set(tracked + untracked)):
        if name:
            print(f"  {name}")


if __name__ == "__main__":
    main()
