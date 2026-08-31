from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "ecaa113d4461a550cb49c6046a42ecf880729346"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256 = {
    "preview/backend/app/services/own_transfer_recovery.py": "8edc089b777b447e55c60e2600ee260157dd01c45ec60eb8e46c224b479a4f75",
    "preview/backend/tests/test_v114_own_transfer_recovery.py": "a0d815c6e89db8db5ff13c7a9e42a6fa2817a4007fd8fa7a8de547c7567902da",
    "preview/backend/tests/test_v114_own_transfer_recovery_contract.py": "30d747077915df39c6373d708b27a8d1fb9172604b3ed5f572aee779159d1357",
    "preview/docs/V1.0.14_OWN_TRANSFER_RECOVERY.md": "ad0d348a007cfd441831e75b839a57c1a1b07f1dfae69944ef81b081da581c95",
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


def replace_count(path: Path, old: str, new: str, expected: int) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != expected:
        raise RuntimeError(
            f"expected {expected} anchors in {path} for {old!r}: found {count}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def regex_replace_count(path: Path, pattern: str, replacement: str, expected: int) -> None:
    text = read_text(path)
    updated, count = re.subn(pattern, replacement, text, flags=re.DOTALL)
    if count != expected:
        raise RuntimeError(f"expected {expected} regex anchors in {path}: found {count}")
    path.write_text(updated, encoding="utf-8")


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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.13 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.13"\nREQUIRED_ANDROID_VERSION = "1.0.13"\n'
    ):
        raise RuntimeError("v1.0.13 backend baseline identity mismatch")
    if "version: 1.0.13+56" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.13 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_entities(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    evidence_class = '''

class OwnAccountTransferRecoveryEvidence(Base):
    __tablename__ = "own_account_transfer_recovery_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_id: Mapped[int] = mapped_column(
        ForeignKey("own_account_transfers.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        index=True,
    )
    transaction_id: Mapped[str] = mapped_column(String(255))
    match_basis: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bank_account_id",
            "transaction_id",
            name="uq_own_transfer_recovery_account_transaction",
        ),
    )
'''
    regex_replace_count(
        path,
        r"(class OwnAccountTransfer\(Base\):.*?)(\n\nclass [A-Za-z])",
        r"\1" + evidence_class + r"\2",
        1,
    )

def patch_financial_autopilot(root: Path) -> None:
    path = root / "backend/app/services/financial_autopilot.py"

    replace_count(
        path,
        'transfer.status = "creation_uncertain"\n        transfer.requires_user_action = True',
        'transfer.status = "creation_uncertain"\n        transfer.requires_user_action = False',
        2,
    )

    regex_replace_count(
        path,
        r'''\n        db\.add\(\n            Task\(\n                title="Check bank before retrying own-account transfer",\n.*?                requires_approval=True,\n            \)\n        \)''',
        "",
        2,
    )

    replace_once(
        path,
        '''    if transfer.external_payment_id is None:\n        transfer.status = "creation_uncertain"\n''',
        '''    if transfer.external_payment_id is None:\n        # An authorization URL without a provider payment ID cannot be bound to\n        # a specific transfer safely. Keep the uncertainty VA-owned and reconcile\n        # independent booked-bank evidence instead of exposing a fake user boundary.\n        transfer.authorization_url = None\n        transfer.status = "creation_uncertain"\n''',
    )

    replace_once(
        path,
        '''async def refresh_own_account_transfer(db: AsyncSession, transfer: OwnAccountTransfer) -> OwnAccountTransfer:\n    if not transfer.external_payment_id or transfer.status == "creation_uncertain":\n        return transfer\n''',
        '''async def refresh_own_account_transfer(db: AsyncSession, transfer: OwnAccountTransfer) -> OwnAccountTransfer:\n    if transfer.status == "creation_uncertain" and not transfer.external_payment_id:\n        from app.services.own_transfer_recovery import reconcile_uncertain_own_account_transfer\n\n        await reconcile_uncertain_own_account_transfer(db, transfer)\n        return transfer\n    if not transfer.external_payment_id:\n        return transfer\n''',
    )

    replace_once(
        path,
        '''async def run_budget_autopilot(db: AsyncSession, *, redirect_url: str) -> dict[str, Any]:\n    await ensure_default_budget_envelopes(db, "personal")\n''',
        '''async def run_budget_autopilot(db: AsyncSession, *, redirect_url: str) -> dict[str, Any]:\n    # Recovery of an already-dispatched transfer is independent of whether new\n    # automatic budgeting is currently enabled. It must never require a redial/recreate.\n    from app.services.own_transfer_recovery import reconcile_all_uncertain_own_account_transfers\n\n    await reconcile_all_uncertain_own_account_transfers(db)\n    await ensure_default_budget_envelopes(db, "personal")\n''',
    )


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/app/services/own_transfer_recovery.py",
        "backend/app/services/own_transfer_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v114_own_transfer_recovery.py",
        "backend/tests/test_v114_own_transfer_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v114_own_transfer_recovery_contract.py",
        "backend/tests/test_v114_own_transfer_recovery_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.14_OWN_TRANSFER_RECOVERY.md",
        "docs/V1.0.14_OWN_TRANSFER_RECOVERY.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    baseline_status = read_text(status_path)
    if "# VAAPP v1.0.13 — Gmail Late-Evidence Recovery & Delivery Continuity" not in baseline_status:
        raise RuntimeError("unexpected STATUS.md baseline")
    status = '''# VAAPP v1.0.14 — Own-Account Transfer Recovery & Human Boundary Integrity\n\nUpdated: 2026-08-31\n\n## Source of truth\n\n- Repository: `Demon-blood/VAAPP2`\n- Branch: `main`\n- Verified v1.0.13 source baseline: `ecaa113d4461a550cb49c6046a42ecf880729346`\n- Verified v1.0.13 GitHub Actions run: `33434347111` — success\n- Verified v1.0.13 prerelease tag: `va-android-113-4-1`\n- v1.0.13 release identity: backend `1.0.13`, Android `1.0.13+56`\n- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.\n- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.\n\nThe operator subsequently reported production deployment and phone smoke testing complete for v1.0.13.\n\n## v1.0.14 maintenance scope\n\nv1.0.14 makes ambiguous own-account transfer creation a VA-owned reconciliation problem instead of fake user work.\n\n- Network loss after provider submission does not create an approval task.\n- A provider response without a payment ID does not expose an unbound authorization URL.\n- `creation_uncertain` keeps `requires_user_action = false`.\n- The original transfer and idempotency key remain active; automatic creation is never repeated.\n- VAAPP checks booked transactions on the exact source account for independent evidence.\n- Recovery requires debit direction, exact amount/currency, exact destination IBAN, a booking date within four days, a provider transaction ID, and exactly one candidate.\n- One unique candidate marks the existing transfer completed from booked-bank evidence.\n- Zero or multiple candidates stay VA-owned and unresolved.\n- Provider reconciliation outages stay VA-owned.\n- Historical `bank_transfer_uncertain` tasks are closed automatically.\n- Genuine authorization remains a human boundary only when a real provider payment ID and authorization URL are bound together.\n- No database schema migration is required.\n\n## Release identity\n\n- Backend: `1.0.14`\n- Required Android: `1.0.14`\n- Android: `1.0.14+57`\n\nThis status file is committed only by the guarded v1.0.14 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.\n'''
    status_path.write_text(status, encoding="utf-8")

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.13":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-08-31",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.13",
            "verified_baseline_android_version": "1.0.13+56",
            "verified_maintenance_actions_run_id": 33434347111,
            "verified_baseline_release_tag": "va-android-113-4-1",
            "current_phase": "maintenance",
            "current_phase_name": "v1.0.14 Own-Account Transfer Recovery & Human Boundary Integrity",
            "current_version": "1.0.14",
            "current_android_version": "1.0.14+57",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "next_phase": "v1.x maintenance and real-world hardening",
            "v114_features": [
                "uncertain own-account transfer creation remains VA-owned and creates no fake Needs You",
                "unbound bank authorization URLs are suppressed when the provider payment ID is missing",
                "the existing transfer and idempotency key remain active so creation is never repeated blindly",
                "booked source-account transactions provide independent recovery evidence",
                "recovery requires exact amount currency destination IBAN date and one unique provider transaction",
                "zero multiple or unavailable provider evidence remains system-owned",
                "historical bank_transfer_uncertain tasks are closed automatically",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    new_invariant = (
        "uncertain own-account transfers remain VA-owned and are recovered only from unique booked-bank evidence"
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
    if "Current candidate: **v1.0.13 — Gmail Late-Evidence Recovery & Delivery Continuity**." not in handoff:
        raise RuntimeError("unexpected VAAPP_PROJECT_HANDOFF.md baseline")
    marker = "## Product objective\n"
    if marker not in handoff:
        raise RuntimeError("handoff product-objective marker is missing")
    suffix = marker + handoff.split(marker, 1)[1]
    prefix = '''# VAAPP project handoff\n\nUpdated: 2026-08-31\nRepository: `Demon-blood/VAAPP2`  \nBranch: `main`\n\n## Verified source of truth\n\nPhases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `ecaa113d4461a550cb49c6046a42ecf880729346` (`v1.0.13 — Gmail Late-Evidence Recovery & Delivery Continuity`). GitHub Actions run `33434347111` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-113-4-1`.\n\nVerified v1.0.13 release identity: backend `1.0.13` / Android `1.0.13+56`. The operator subsequently reported production deployment and phone smoke testing complete.\n\nHistorical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current maintenance candidate\n\nBackend `1.0.14` / Android `1.0.14+57`.\n\nCurrent candidate: **v1.0.14 — Own-Account Transfer Recovery & Human Boundary Integrity**.\n\nv1.0.14 removes the remaining fake-human boundary from Financial Autopilot transfer creation uncertainty. A timeout or missing provider payment ID keeps the original transfer VA-owned, suppresses any unbound authorization URL, and never permits blind recreation. VAAPP reconciles independent booked transactions on the exact source account and completes the transfer only when one unique debit matches the exact amount, currency, destination IBAN, booking window, and provider transaction identity. Zero, multiple, or unavailable evidence remains VA-owned. Genuine bank authorization remains unchanged when it is bound to a real provider payment ID.\n\nThe guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.\n\nNext work after the v1.0.14 gate is green: **v1.x maintenance and real-world hardening**.\n\n'''
    handoff_path.write_text(prefix + suffix, encoding="utf-8")


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.13"\nREQUIRED_ANDROID_VERSION = "1.0.13"\n',
        'APP_VERSION = "1.0.14"\nREQUIRED_ANDROID_VERSION = "1.0.14"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.13"', 'version = "1.0.14"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.13+56", "version: 1.0.14+57")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.13';\nconst String minimumBackendVersion = '1.0.13';\n",
        "const String appRelease = '1.0.14';\nconst String minimumBackendVersion = '1.0.14';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.13"', 'APP_VERSION = "1.0.14"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.13"', 'REQUIRED_ANDROID_VERSION = "1.0.14"'),
        ('version = "1.0.13"', 'version = "1.0.14"'),
        ('version: 1.0.13+56', 'version: 1.0.14+57'),
        ("appRelease = '1.0.13'", "appRelease = '1.0.14'"),
        ("minimumBackendVersion = '1.0.13'", "minimumBackendVersion = '1.0.14'"),
        ('APP_VERSION == "1.0.13"', 'APP_VERSION == "1.0.14"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v114_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.14")


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
        "backend/app/models/entities.py",
        "backend/app/services/financial_autopilot.py",
        "backend/app/services/own_transfer_recovery.py",
        "backend/tests/test_v114_own_transfer_recovery.py",
        "backend/tests/test_v114_own_transfer_recovery_contract.py",
        "docs/V1.0.14_OWN_TRANSFER_RECOVERY.md",
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
        raise RuntimeError(f"required v1.0.14 changes missing from diff: {missing}")

    entities = read_text(root / "backend/app/models/entities.py")
    finance = read_text(root / "backend/app/services/financial_autopilot.py")
    recovery = read_text(root / "backend/app/services/own_transfer_recovery.py")
    if "class OwnAccountTransferRecoveryEvidence(Base):" not in entities:
        raise RuntimeError("v1.0.14 transfer recovery evidence model missing")
    if "uq_own_transfer_recovery_account_transaction" not in entities:
        raise RuntimeError("v1.0.14 recovery transaction uniqueness constraint missing")
    if 'title="Check bank before retrying own-account transfer"' in finance:
        raise RuntimeError("v1.0.14 must not create a fake uncertainty approval task")
    if 'source_type="bank_transfer_uncertain"' in finance:
        raise RuntimeError("legacy uncertainty task creation remains in Financial Autopilot")
    for marker in (
        'transfer.requires_user_action = False',
        'transfer.authorization_url = None',
        'await reconcile_uncertain_own_account_transfer(db, transfer)',
        'await reconcile_all_uncertain_own_account_transfers(db)',
    ):
        if marker not in finance:
            raise RuntimeError(f"v1.0.14 Financial Autopilot marker missing: {marker}")
    for marker in (
        "reconcile_uncertain_own_account_transfer",
        "reconcile_all_uncertain_own_account_transfers",
        'if len(candidates) != 1:',
        '"automatic_retry": False',
        '"completion_evidence": "booked_bank_transaction"',
    ):
        if marker not in recovery:
            raise RuntimeError(f"v1.0.14 recovery marker missing: {marker}")
    if 'APP_VERSION = "1.0.14"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.14 backend version missing")
    if "version: 1.0.14+57" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.14 Android version missing")
    status = read_text(root / "STATUS.md")
    state = read_text(root / "VAAPP_PROJECT_STATE.json")
    handoff = read_text(root / "VAAPP_PROJECT_HANDOFF.md")
    for historical in ("v1.0.13", "v1.0.12", "v1.0.11"):
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
    patch_entities(root)
    patch_financial_autopilot(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.14 source patch prepared. Changed files:")
    tracked = run_git(root, "diff", "--name-only").splitlines()
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    for name in sorted(set(tracked + untracked)):
        if name:
            print(f"  {name}")


if __name__ == "__main__":
    main()
