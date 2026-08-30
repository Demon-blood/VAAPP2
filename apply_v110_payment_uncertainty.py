from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "12afd780fdeb83fe89f0a6c3010d268dde683103"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256 = {'preview/backend/app/services/payment_recovery.py': '5760339b63c36777c87d499873c15a0640308b4473d1a9243e722715b4d93182', 'preview/backend/tests/test_v110_payment_uncertainty.py': '0d578f1033cf44772c49eb307dd49c8be9948e0896d895ff9d2935f5a081dafc', 'preview/backend/tests/test_v110_payment_uncertainty_contract.py': 'e08cfae9b991a26ee50259f6349e00e7db0dd743d835c466ab855e35ffcd9251', 'preview/docs/V1.0.10_PAYMENT_UNCERTAINTY_RECOVERY.md': '629ec59ee4eda0dc3d31ff48b05857972faa2a2a0f4c1d96b177fe2d3d9b0f39'}


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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.9 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.9"\nREQUIRED_ANDROID_VERSION = "1.0.9"\n'
    ):
        raise RuntimeError("v1.0.9 backend baseline identity mismatch")
    if "version: 1.0.9+52" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.9 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_entities(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    marker = '''class AutomationRule(Base):\n'''
    insertion = '''class PaymentRecoveryEvidence(Base):\n    __tablename__ = "payment_recovery_evidence"\n\n    id: Mapped[int] = mapped_column(primary_key=True)\n    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)\n    bank_account_id: Mapped[int | None] = mapped_column(\n        ForeignKey("bank_accounts.id", ondelete="SET NULL"), nullable=True, index=True\n    )\n    transaction_id: Mapped[str] = mapped_column(String(255))\n    match_basis: Mapped[str] = mapped_column(String(80))\n    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)\n    details_json: Mapped[str] = mapped_column(Text, default="{}")\n    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)\n\n    __table_args__ = (\n        UniqueConstraint(\n            "payment_id",\n            "transaction_id",\n            name="uq_payment_recovery_payment_transaction",\n        ),\n    )\n\n\nclass AutomationRule(Base):\n'''
    replace_once(path, marker, insertion)


def patch_banking_service(root: Path) -> None:
    path = root / "backend/app/services/banking_service.py"
    old_network = '''    except (httpx.RequestError, TimeoutError) as exc:\n        payment.status = "creation_uncertain"\n        payment.requires_user_action = True\n        payment.failure_reason = (\n            f"Payment creation outcome is uncertain; automatic retry is blocked until the bank is checked: {exc}"\n        )[:2000]\n        bill.status = "payment_initiated"\n        db.add(\n            Task(\n                title=f"Check bank before retrying {bill.creditor_name}",\n                description=payment.failure_reason,\n                source_type="payment_creation_uncertain",\n                source_id=str(payment.id),\n                priority="urgent",\n                requires_approval=True,\n            )\n        )\n        await write_audit(\n            db,\n            "payment_creation_uncertain",\n            entity_type="payment",\n            entity_id=str(payment.id),\n            result="blocked",\n            details={"bill_id": bill.id, "retry_suppressed": True, "error": str(exc)},\n        )\n        await db.commit()\n        return payment\n\n'''
    new_network = '''    except (httpx.RequestError, TimeoutError) as exc:\n        payment.status = "creation_uncertain"\n        payment.requires_user_action = False\n        payment.failure_reason = (\n            "Payment creation outcome is uncertain; automatic retry is blocked while "\n            f"the VA reconciles independent bank evidence: {exc}"\n        )[:2000]\n        bill.status = "payment_initiated"\n        await write_audit(\n            db,\n            "payment_creation_uncertain",\n            entity_type="payment",\n            entity_id=str(payment.id),\n            result="blocked",\n            details={\n                "bill_id": bill.id,\n                "retry_suppressed": True,\n                "ownership": "va",\n                "requires_user_action": False,\n                "error": str(exc),\n            },\n        )\n        await db.commit()\n        return payment\n\n'''
    replace_once(path, old_network, new_network)

    old_provider = '''    external_id = str(response.get("payment_id") or response.get("id") or "").strip() or None\n    payment.external_payment_id = external_id\n    payment.authorization_url = str(response.get("url") or "").strip() or None\n    payment.requires_user_action = bool(payment.authorization_url)\n    if external_id is None:\n        payment.status = "creation_uncertain"\n        payment.requires_user_action = True\n        payment.failure_reason = "Payment provider returned success without a payment identifier; automatic retry is blocked."\n    else:\n        payment.status = "authorization_required" if payment.authorization_url else str(response.get("status") or "received").lower()\n    bill.status = "payment_initiated"\n    state_row.payload_json = json.dumps(\n        {"bill_id": bill.id, "bank_account_id": account.id, "payment_id": payment.id, "external_payment_id": external_id}\n    )\n    if payment.status == "creation_uncertain":\n        db.add(\n            Task(\n                title=f"Check bank before retrying {bill.creditor_name}",\n                description=payment.failure_reason,\n                source_type="payment_creation_uncertain",\n                source_id=str(payment.id),\n                priority="urgent",\n                requires_approval=True,\n            )\n        )\n'''
    new_provider = '''    external_id = str(response.get("payment_id") or response.get("id") or "").strip() or None\n    payment.external_payment_id = external_id\n    payment.authorization_url = str(response.get("url") or "").strip() or None\n    if external_id is None:\n        payment.status = "creation_uncertain"\n        payment.requires_user_action = False\n        payment.authorization_url = None\n        payment.failure_reason = (\n            "Payment provider returned success without a payment identifier; automatic retry is "\n            "blocked while the VA reconciles independent bank evidence."\n        )\n    else:\n        payment.requires_user_action = bool(payment.authorization_url)\n        payment.status = "authorization_required" if payment.authorization_url else str(\n            response.get("status") or "received"\n        ).lower()\n    bill.status = "payment_initiated"\n    state_row.payload_json = json.dumps(\n        {"bill_id": bill.id, "bank_account_id": account.id, "payment_id": payment.id, "external_payment_id": external_id}\n    )\n'''
    replace_once(path, old_provider, new_provider)


def patch_workflow_engine(root: Path) -> None:
    path = root / "backend/app/services/workflow_engine.py"
    replace_once(
        path,
        '''    from app.services.financial_reconciliation import reconcile_receipts_with_bank_transactions\n''',
        '''    from app.services.financial_reconciliation import reconcile_receipts_with_bank_transactions\n    from app.services.payment_recovery import reconcile_all_uncertain_payments\n''',
    )
    replace_once(
        path,
        '''    transaction_sync = await sync_bank_transactions(db)\n    statement_reconciliation = await reconcile_statement_transactions_with_bank(db)\n''',
        '''    transaction_sync = await sync_bank_transactions(db)\n    uncertain_payment_recovery = await reconcile_all_uncertain_payments(db)\n    statement_reconciliation = await reconcile_statement_transactions_with_bank(db)\n''',
    )
    replace_once(
        path,
        '''        "transaction_sync": transaction_sync,\n        "statement_reconciliation": statement_reconciliation,\n''',
        '''        "transaction_sync": transaction_sync,\n        "payment_uncertainty_recovery": uncertain_payment_recovery,\n        "statement_reconciliation": statement_reconciliation,\n''',
    )
    replace_once(
        path,
        '''    from app.services.banking_service import create_payment_for_bill, refresh_payment\n''',
        '''    from app.services.banking_service import create_payment_for_bill, refresh_payment\n    from app.services.payment_recovery import reconcile_uncertain_payment\n''',
    )
    old_existing = '''    if payment is not None:\n        if payment.external_payment_id:\n            await refresh_payment(db, payment)\n        return {\n            "bill_id": bill.id,\n            "state": "settled" if payment.status == "completed" else ("authorization_required" if payment.requires_user_action else "payment_pending"),\n            "payment_id": payment.id,\n            "payment_status": payment.status,\n            "requires_user_action": payment.requires_user_action,\n            "authorization_url": payment.authorization_url,\n        }\n\n'''
    new_existing = '''    if payment is not None:\n        recovery = None\n        if payment.status == "creation_uncertain" and not payment.external_payment_id:\n            recovery = await reconcile_uncertain_payment(db, payment)\n            await db.refresh(payment)\n        elif payment.external_payment_id:\n            await refresh_payment(db, payment)\n        human_required = bool(payment.requires_user_action and payment.authorization_url)\n        if payment.status == "completed":\n            state = "settled"\n        elif human_required:\n            state = "authorization_required"\n        elif payment.status == "creation_uncertain":\n            state = "payment_reconciling"\n        else:\n            state = "payment_pending"\n        return {\n            "bill_id": bill.id,\n            "state": state,\n            "payment_id": payment.id,\n            "payment_status": payment.status,\n            "requires_user_action": human_required,\n            "authorization_url": payment.authorization_url if human_required else None,\n            "recovery": recovery,\n        }\n\n'''
    replace_once(path, old_existing, new_existing)

    old_created = '''    return {\n        "bill_id": bill.id,\n        "state": "authorization_required" if payment.requires_user_action else "payment_initiated",\n        "payment_id": payment.id,\n        "payment_status": payment.status,\n        "requires_user_action": payment.requires_user_action,\n        "authorization_url": payment.authorization_url,\n    }\n\n\n@job_handler("autopilot.provider_health")\n'''
    new_created = '''    human_required = bool(payment.requires_user_action and payment.authorization_url)\n    state = (\n        "authorization_required"\n        if human_required\n        else "payment_reconciling"\n        if payment.status == "creation_uncertain"\n        else "payment_initiated"\n    )\n    return {\n        "bill_id": bill.id,\n        "state": state,\n        "payment_id": payment.id,\n        "payment_status": payment.status,\n        "requires_user_action": human_required,\n        "authorization_url": payment.authorization_url if human_required else None,\n    }\n\n\n@job_handler("autopilot.provider_health")\n'''
    replace_once(path, old_created, new_created)


def patch_operational_guardian(root: Path) -> None:
    path = root / "backend/app/services/operational_guardian.py"
    old = '''async def _payment_state(db: AsyncSession) -> dict[str, Any]:\n    cutoff = _now() - timedelta(days=7)\n    rows = list(\n        (\n            await db.execute(\n                select(Payment).where(\n                    Payment.updated_at >= cutoff,\n                    Payment.status.in_(["failed", "rejected", "declined"]),\n                )\n            )\n        ).scalars()\n    )\n    return {\n        "recent_rejections": len(rows),\n        "provider_user_action_required": sum(1 for row in rows if row.requires_user_action),\n    }\n\n\n'''
    new = '''async def _payment_state(db: AsyncSession) -> dict[str, Any]:\n    cutoff = _now() - timedelta(days=7)\n    rejected = list(\n        (\n            await db.execute(\n                select(Payment).where(\n                    Payment.updated_at >= cutoff,\n                    Payment.status.in_(["failed", "rejected", "declined"]),\n                )\n            )\n        ).scalars()\n    )\n    uncertain = list(\n        (\n            await db.execute(\n                select(Payment).where(\n                    Payment.updated_at >= cutoff,\n                    Payment.status == "creation_uncertain",\n                    Payment.external_payment_id.is_(None),\n                )\n            )\n        ).scalars()\n    )\n    return {\n        "recent_rejections": len(rejected),\n        "provider_user_action_required": sum(1 for row in rejected if row.requires_user_action),\n        "creation_uncertain": len(uncertain),\n        "system_owned_uncertainty": len(uncertain),\n    }\n\n\n'''
    replace_once(path, old, new)
    replace_once(
        path,
        '    system_issues = len(workflow["stale"])\n',
        '    system_issues = len(workflow["stale"]) + payments["system_owned_uncertainty"]\n',
    )
    replace_once(
        path,
        '    system_issues = len(workflow["stale"]) - len(workflow["self_healed"])\n',
        '    system_issues = (\n        len(workflow["stale"])\n        - len(workflow["self_healed"])\n        + payments["system_owned_uncertainty"]\n    )\n',
    )


def patch_project_metadata(root: Path) -> None:
    status = '''# VAAPP v1.0.10 — Payment Recovery & Human Boundary Integrity

Updated: 2026-08-30

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.9 source baseline: `12afd780fdeb83fe89f0a6c3010d268dde683103`
- Verified v1.0.9 GitHub Actions run: `33323619938` — success
- Verified v1.0.9 prerelease tag: `va-android-109-4-1`
- v1.0.9 release identity: backend `1.0.9`, Android `1.0.9+52`

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.9.

## v1.0.10 maintenance scope

v1.0.10 keeps ambiguous payment-creation outcomes VA-owned instead of manufacturing a human approval boundary.

- Network/timeout uncertainty after a bank payment POST remains `creation_uncertain` with automatic duplicate retry suppressed.
- A provider response without a payment identifier also remains VA-owned; an unbound authorization URL is not surfaced as a valid SCA action.
- The VA reconciles uncertain payment creation against booked transactions from the exact source bank account.
- Completion requires exactly one provider-backed transaction matching amount, currency, timing, and strong creditor/reference evidence.
- Zero or multiple candidate transactions remain unresolved and VA-owned.
- Recovery creates durable `PaymentRecoveryEvidence` before treating the bill as paid.
- Legacy `payment_creation_uncertain` human tasks are closed by reconciliation rather than kept in Needs You.
- Genuine bank authorization remains human-bound only when a real provider authorization URL is attached to a provider payment identifier.
- The Operational Guardian counts unresolved payment creation uncertainty as a system issue, not Needs You.
- Anti-double-payment behavior is unchanged: an uncertain payment stays active, so a second automatic payment is not submitted while evidence is unresolved.

## Release identity

- Backend: `1.0.10`
- Required Android: `1.0.10`
- Android: `1.0.10+53`

This status file is committed only by the guarded v1.0.10 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
'''
    (root / "STATUS.md").write_text(status, encoding="utf-8")

    project_state = '''{
  "updated": "2026-08-30",
  "repository": "Demon-blood/VAAPP2",
  "branch": "main",
  "verified_baseline_commit": "12afd780fdeb83fe89f0a6c3010d268dde683103",
  "verified_baseline_version": "1.0.9",
  "verified_baseline_android_version": "1.0.9+52",
  "verified_baseline_actions_run": 41,
  "verified_baseline_actions_conclusion": "success",
  "verified_maintenance_actions_run_id": 33323619938,
  "verified_baseline_release_tag": "va-android-109-4-1",
  "current_phase": "maintenance",
  "current_phase_name": "v1.0.10 Payment Recovery & Human Boundary Integrity",
  "current_version": "1.0.10",
  "current_android_version": "1.0.10+53",
  "phase_status": "source commit is gated by full GitHub Actions validation before publication",
  "next_phase": "v1.x maintenance and real-world hardening",
  "v110_features": [
    "uncertain payment creation remains VA-owned instead of generating fake Needs You",
    "duplicate payment retry remains suppressed while creation outcome is unresolved",
    "unique booked bank transaction evidence can recover an uncertain payment",
    "ambiguous or absent transaction evidence remains unresolved without human escalation",
    "durable payment recovery evidence records the independent bank postcondition",
    "genuine provider SCA remains human-bound only with a valid authorization URL",
    "operational guardian reports unresolved payment uncertainty as a system issue"
  ],
  "invariants": [
    "provider and system defects remain VA-owned",
    "Needs You is reserved for genuine human boundaries",
    "terminal payment completion requires independent provider or bank evidence",
    "uncertain payment creation never triggers an automatic duplicate retry"
  ]
}
'''
    (root / "VAAPP_PROJECT_STATE.json").write_text(project_state, encoding="utf-8")

    handoff = root / "VAAPP_PROJECT_HANDOFF.md"
    old_prefix = '''# VAAPP project handoff

Updated: 2026-08-15  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–10 and production v1.0 are complete on GitHub. The current verified maintenance release is commit `752d69b224bb8b0bfb96663e1df5089a30442bfb` (`v1.0.5 — Human Boundary & Relationship-Aware Communications`). GitHub Actions run #49 completed successfully end-to-end, including the backend suite, Flutter analysis/tests, persistent-signing Android release build, and GitHub prerelease publication.

Verified maintenance release: backend `1.0.5` / Android `1.0.5+47`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current local candidate

Backend `1.0.5` / Android `1.0.5+47`.

Current maintenance candidate: **v1.0.5 — Human Boundary & Relationship-Aware Communications**.
Status: **verified by canonical GitHub Actions run #49; backend, Flutter, signed APK, and prerelease publication are green.**

The candidate adds objective-bound Authorize/Decline decisions, repairs duplicate communication ownership/cross-transport SMS mirrors, adds user-explicit Phase-4 relationship reply preferences, and adds opt-in learned relationship writing style from device-observed non-VA sent history. Authorization remains distinct from completion evidence, and relationship style never grants financial/legal/security/browser/banking or other material execution authority.

Next work after the v1.0.5 gate is green: **v1.x maintenance and real-world hardening**.

## Product objective
'''
    new_prefix = '''# VAAPP project handoff

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
    replace_once(handoff, old_prefix, new_prefix)


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.9"\nREQUIRED_ANDROID_VERSION = "1.0.9"\n',
        'APP_VERSION = "1.0.10"\nREQUIRED_ANDROID_VERSION = "1.0.10"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.9"', 'version = "1.0.10"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.9+52", "version: 1.0.10+53")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.9';\nconst String minimumBackendVersion = '1.0.9';\n",
        "const String appRelease = '1.0.10';\nconst String minimumBackendVersion = '1.0.10';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.9"', 'APP_VERSION = "1.0.10"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.9"', 'REQUIRED_ANDROID_VERSION = "1.0.10"'),
        ('version = "1.0.9"', 'version = "1.0.10"'),
        ('version: 1.0.9+52', 'version: 1.0.10+53'),
        ("appRelease = '1.0.9'", "appRelease = '1.0.10'"),
        ("minimumBackendVersion = '1.0.9'", "minimumBackendVersion = '1.0.10'"),
        ('APP_VERSION == "1.0.9"', 'APP_VERSION == "1.0.10"'),
    )
    updated = 0
    for path in sorted((root / "backend/tests").glob("test_*.py")):
        if path.name.startswith("test_v110_"):
            continue
        text = read_text(path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.10")


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/app/services/payment_recovery.py",
        "backend/app/services/payment_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v110_payment_uncertainty.py",
        "backend/tests/test_v110_payment_uncertainty.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v110_payment_uncertainty_contract.py",
        "backend/tests/test_v110_payment_uncertainty_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.10_PAYMENT_UNCERTAINTY_RECOVERY.md",
        "docs/V1.0.10_PAYMENT_UNCERTAINTY_RECOVERY.md",
    )


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
    forbidden = [path for path in changed if path.startswith(".github/workflows/")]
    if forbidden:
        raise RuntimeError(f"workflow files changed unexpectedly: {forbidden}")
    required = {
        "backend/app/models/entities.py",
        "backend/app/services/banking_service.py",
        "backend/app/services/operational_guardian.py",
        "backend/app/services/payment_recovery.py",
        "backend/app/services/workflow_engine.py",
        "backend/tests/test_v110_payment_uncertainty.py",
        "backend/tests/test_v110_payment_uncertainty_contract.py",
        "docs/V1.0.10_PAYMENT_UNCERTAINTY_RECOVERY.md",
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
        raise RuntimeError(f"required v1.0.10 changes missing from diff: {missing}")
    if 'APP_VERSION = "1.0.10"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.10 backend version guard failed")
    if "version: 1.0.10+53" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.10 Android version guard failed")
    print("v1.0.10 source patch prepared. Changed files:")
    for path in changed:
        print(f"  {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply VAAPP v1.0.10 Payment Recovery & Human Boundary Integrity"
    )
    parser.add_argument("repo", nargs="?", default=".", help="path to a clean VAAPP2 checkout")
    args = parser.parse_args()
    root = Path(args.repo).resolve()

    verify_bundle()
    verify_repo(root)
    patch_entities(root)
    write_new_files(root)
    patch_banking_service(root)
    patch_workflow_engine(root)
    patch_operational_guardian(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)


if __name__ == "__main__":
    main()
