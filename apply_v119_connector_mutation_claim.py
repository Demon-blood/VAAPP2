from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "b0005392a799bc5466a5e77febfd34035fb26ce3"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256: dict[str, str] = {
    "preview/backend/app/services/connector_mutation_recovery.py": "873ff757d759c1aeedb23c85b300a565c15ac2100b7c8f5bcd0ed16294fb4d74",
    "preview/backend/tests/test_v119_connector_mutation_claim.py": "eb16848d849d24102b9b119e4f18aee043cd10d72de5aa9eb33be2e6efd9cfe2",
    "preview/backend/tests/test_v119_connector_mutation_claim_contract.py": "aa65e04a1dbd39bc658672f3905983a8e671a8a43a6dd860cdb00ebdc6844d7b",
    "preview/docs/V1.0.19_CONNECTOR_MUTATION_CLAIM.md": "7c3761efd668a58ec5058c285beff3693bbcf603e684f52244a3bfd33da709ed",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.18 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.18"\nREQUIRED_ANDROID_VERSION = "1.0.18"\n'
    ):
        raise RuntimeError("v1.0.18 backend baseline identity mismatch")
    if "version: 1.0.18+61" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.18 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_models(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    anchor = '''class ServiceConnector(Base):
    __tablename__ = "service_connectors"
'''
    addition = '''class ScheduledConnectorMutationIntent(Base):
    __tablename__ = "scheduled_connector_mutation_intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_rule_id: Mapped[int] = mapped_column(
        ForeignKey("automation_rules.id", ondelete="CASCADE"), index=True
    )
    service_connector_id: Mapped[int] = mapped_column(
        ForeignKey("service_connectors.id", ondelete="CASCADE"), index=True
    )
    occurrence_key: Mapped[str] = mapped_column(String(255), index=True)
    connector_slug: Mapped[str] = mapped_column(String(120), index=True)
    connector_type: Mapped[str] = mapped_column(String(80), index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="prepared", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    last_error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "automation_rule_id",
            "occurrence_key",
            name="uq_scheduled_connector_mutation_rule_occurrence",
        ),
    )


class ServiceConnector(Base):
    __tablename__ = "service_connectors"
'''
    replace_once(path, anchor, addition)


def patch_automation_engine(root: Path) -> None:
    path = root / "backend/app/services/automation_engine.py"
    replace_once(
        path,
        "from app.services.connector_service import execute_connector\n",
        '''from app.services.connector_mutation_recovery import (
    claim_scheduled_connector_mutation,
    complete_scheduled_connector_mutation,
    connector_operation_is_mutating,
    mark_scheduled_connector_mutation_uncertain,
    mutation_replay_status,
    prepare_scheduled_connector_mutation,
)
from app.services.connector_service import execute_connector
''',
    )
    old = '''        connector_slug = str(actions.get("connector_slug") or "")
        operation = str(actions.get("operation") or "")
        connector = (
            await db.execute(select(ServiceConnector).where(ServiceConnector.slug == connector_slug))
        ).scalar_one_or_none()
        rule.last_run_at = now
        if connector is None or not operation:
            rule.last_result = "Connector or operation is missing"
            outcome["failed"] += 1
            await _ensure_rule_exception_task(
                db,
                rule,
                description="The connector or requested operation is missing. Connect/configure it before this rule can continue.",
            )
            continue
        try:
            result = await execute_connector(
                db,
                connector,
                operation,
                dict(actions.get("parameters") or {}),
            )
            rule.last_result = json.dumps(result, ensure_ascii=False)[:4000]
            outcome["executed"] += 1
            await write_audit(
                db,
                "scheduled_connector_rule_executed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                details={"connector": connector_slug, "operation": operation},
            )
            stale_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "automation_rule",
                        Task.source_id == str(rule.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalar_one_or_none()
            if stale_task is not None:
                stale_task.status = "completed"
        except Exception as exc:
            rule.last_result = str(exc)[:4000]
            outcome["failed"] += 1
            recovery_class = failure_recovery_class("connectors.rules.run", str(exc))
            if recovery_class in {"transient", "user_required"}:
                # The durable workflow engine owns provider backoff/recovery and will surface
                # OAuth/security setup only after retries cannot resolve it.
                raise
            await _ensure_rule_exception_task(
                db,
                rule,
                description=f"Autopilot cannot safely infer how to repair this connector rule: {exc}",
            )
            await write_audit(
                db,
                "scheduled_connector_rule_failed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                result="needs_user",
                details={
                    "connector": connector_slug,
                    "operation": operation,
                    "error": str(exc),
                    "recovery_class": recovery_class,
                },
            )
'''
    new = '''        connector_slug = str(actions.get("connector_slug") or "")
        operation = str(actions.get("operation") or "")
        connector = (
            await db.execute(select(ServiceConnector).where(ServiceConnector.slug == connector_slug))
        ).scalar_one_or_none()
        if connector is None or not operation:
            rule.last_run_at = now
            rule.last_result = "Connector or operation is missing"
            outcome["failed"] += 1
            await _ensure_rule_exception_task(
                db,
                rule,
                description="The connector or requested operation is missing. Connect/configure it before this rule can continue.",
            )
            continue

        parameters = dict(actions.get("parameters") or {})
        try:
            is_mutating = connector_operation_is_mutating(
                connector.connector_type, operation, parameters
            )
        except ValueError as exc:
            rule.last_run_at = now
            rule.last_result = str(exc)[:4000]
            outcome["failed"] += 1
            await _ensure_rule_exception_task(
                db,
                rule,
                description=f"The scheduled connector operation is invalid and cannot run unattended: {exc}",
            )
            continue

        if is_mutating:
            intent = await prepare_scheduled_connector_mutation(
                db,
                rule=rule,
                connector=connector,
                operation=operation,
                parameters=parameters,
                interval_minutes=interval,
                now=now,
            )
            if intent.status != "prepared":
                rule.last_run_at = rule.last_run_at or now
                rule.last_result = mutation_replay_status(intent)
                outcome["skipped"] += 1
                if intent.status in {"submitting", "execution_uncertain"}:
                    await write_audit(
                        db,
                        "scheduled_connector_mutation_replay_suppressed",
                        entity_type="automation_rule",
                        entity_id=str(rule.id),
                        result="system_owned",
                        details={
                            "connector": connector_slug,
                            "operation": operation,
                            "intent_id": intent.id,
                            "intent_status": intent.status,
                            "automatic_replay": False,
                        },
                    )
                await db.commit()
                continue

            claimed = await claim_scheduled_connector_mutation(
                db, intent=intent, rule=rule, claimed_at=now
            )
            if not claimed:
                rule.last_result = mutation_replay_status(intent)
                outcome["skipped"] += 1
                await write_audit(
                    db,
                    "scheduled_connector_mutation_replay_suppressed",
                    entity_type="automation_rule",
                    entity_id=str(rule.id),
                    result="system_owned",
                    details={
                        "connector": connector_slug,
                        "operation": operation,
                        "intent_id": intent.id,
                        "intent_status": intent.status,
                        "automatic_replay": False,
                    },
                )
                await db.commit()
                continue

            try:
                result = await execute_connector(db, connector, operation, parameters)
            except Exception as exc:
                await mark_scheduled_connector_mutation_uncertain(
                    db, intent=intent, error=exc
                )
                rule.last_result = mutation_replay_status(intent)
                outcome["failed"] += 1
                stale_task = (
                    await db.execute(
                        select(Task).where(
                            Task.source_type == "automation_rule",
                            Task.source_id == str(rule.id),
                            Task.status.in_(["open", "waiting"]),
                        )
                    )
                ).scalar_one_or_none()
                if stale_task is not None:
                    stale_task.status = "completed"
                await write_audit(
                    db,
                    "scheduled_connector_mutation_uncertain",
                    entity_type="automation_rule",
                    entity_id=str(rule.id),
                    result="system_owned",
                    details={
                        "connector": connector_slug,
                        "operation": operation,
                        "intent_id": intent.id,
                        "error": str(exc),
                        "automatic_replay": False,
                    },
                )
                await db.commit()
                continue

            await complete_scheduled_connector_mutation(db, intent=intent, result=result)
            rule.last_result = json.dumps(result, ensure_ascii=False)[:4000]
            outcome["executed"] += 1
            await write_audit(
                db,
                "scheduled_connector_rule_executed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                details={
                    "connector": connector_slug,
                    "operation": operation,
                    "mutation_intent_id": intent.id,
                },
            )
            stale_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "automation_rule",
                        Task.source_id == str(rule.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalar_one_or_none()
            if stale_task is not None:
                stale_task.status = "completed"
            await db.commit()
            continue

        # Read-only connector operations retain bounded workflow retry semantics.
        rule.last_run_at = now
        try:
            result = await execute_connector(db, connector, operation, parameters)
            rule.last_result = json.dumps(result, ensure_ascii=False)[:4000]
            outcome["executed"] += 1
            await write_audit(
                db,
                "scheduled_connector_rule_executed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                details={"connector": connector_slug, "operation": operation},
            )
            stale_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "automation_rule",
                        Task.source_id == str(rule.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalar_one_or_none()
            if stale_task is not None:
                stale_task.status = "completed"
        except Exception as exc:
            rule.last_result = str(exc)[:4000]
            outcome["failed"] += 1
            recovery_class = failure_recovery_class("connectors.rules.run", str(exc))
            if recovery_class in {"transient", "user_required"}:
                # Read-only provider work remains safe to re-run.
                raise
            await _ensure_rule_exception_task(
                db,
                rule,
                description=f"Autopilot cannot safely infer how to repair this connector rule: {exc}",
            )
            await write_audit(
                db,
                "scheduled_connector_rule_failed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                result="needs_user",
                details={
                    "connector": connector_slug,
                    "operation": operation,
                    "error": str(exc),
                    "recovery_class": recovery_class,
                },
            )
'''
    replace_once(path, old, new)


def patch_workflow_engine(root: Path) -> None:
    path = root / "backend/app/services/workflow_engine.py"
    anchor = '''async def recover_autopilot_exceptions(db: AsyncSession, *, limit: int = 50) -> dict[str, int]:
'''
    helper = '''async def repair_v119_connector_rule_retry_backlog(
    db: AsyncSession,
) -> dict[str, int]:
    marker = (
        await db.execute(
            select(AuditLog.id)
            .where(AuditLog.event_type == "v119_connector_rule_retry_backlog_repaired")
            .limit(1)
        )
    ).scalar_one_or_none()
    if marker is not None:
        return {"superseded": 0, "already_repaired": 1}

    rows = list(
        (
            await db.execute(
                select(WorkflowJob).where(
                    WorkflowJob.job_type == "connectors.rules.run",
                    WorkflowJob.status.in_(["running", "retry", "dead_letter"]),
                )
            )
        ).scalars()
    )
    now = utcnow()
    run_ids: set[int] = set()
    for job in rows:
        job.status = "superseded"
        job.result_json = json.dumps(
            {
                "reason": "v1.0.19_preclaim_connector_retry_quarantine",
                "previous_error": job.last_error,
                "automatic_replay": False,
            },
            ensure_ascii=False,
            default=str,
        )
        job.lease_owner = ""
        job.lease_expires_at = None
        job.finished_at = job.finished_at or now
        if job.workflow_run_id is not None:
            run_ids.add(job.workflow_run_id)

    for run_id in run_ids:
        await refresh_workflow_status(db, run_id)

    await write_audit(
        db,
        "v119_connector_rule_retry_backlog_repaired",
        entity_type="workflow",
        entity_id="connectors.rules.run",
        details={"superseded": len(rows), "automatic_replay": False},
    )
    await db.commit()
    return {"superseded": len(rows), "already_repaired": 0}


'''
    replace_once(path, anchor, helper + anchor)


def patch_main(root: Path) -> None:
    path = root / "backend/app/main.py"
    replace_once(
        path,
        '''    repair_v052_gmail_conflict_backlog,
    repair_v062_gmail_label_conflict_backlog,
)
''',
        '''    repair_v052_gmail_conflict_backlog,
    repair_v062_gmail_label_conflict_backlog,
    repair_v119_connector_rule_retry_backlog,
)
''',
    )
    replace_once(
        path,
        '''            label_backlog = await repair_v062_gmail_label_conflict_backlog(db)
            compacted = await compact_duplicate_dead_letters(db)
            if legacy_backlog["superseded"] or label_backlog["superseded"] or compacted["superseded"]:
                logger.warning(
                    "Initial Autopilot exception repair: legacy_gmail_409=%s label_conflicts=%s duplicates=%s",
                    legacy_backlog,
                    label_backlog,
                    compacted,
                )
''',
        '''            label_backlog = await repair_v062_gmail_label_conflict_backlog(db)
            connector_backlog = await repair_v119_connector_rule_retry_backlog(db)
            compacted = await compact_duplicate_dead_letters(db)
            if (
                legacy_backlog["superseded"]
                or label_backlog["superseded"]
                or connector_backlog["superseded"]
                or compacted["superseded"]
            ):
                logger.warning(
                    "Initial Autopilot exception repair: legacy_gmail_409=%s "
                    "label_conflicts=%s connector_retries=%s duplicates=%s",
                    legacy_backlog,
                    label_backlog,
                    connector_backlog,
                    compacted,
                )
''',
    )


def write_new_files(root: Path) -> None:
    for source, destination in (
        ("preview/backend/app/services/connector_mutation_recovery.py", "backend/app/services/connector_mutation_recovery.py"),
        ("preview/backend/tests/test_v119_connector_mutation_claim.py", "backend/tests/test_v119_connector_mutation_claim.py"),
        ("preview/backend/tests/test_v119_connector_mutation_claim_contract.py", "backend/tests/test_v119_connector_mutation_claim_contract.py"),
        ("preview/docs/V1.0.19_CONNECTOR_MUTATION_CLAIM.md", "docs/V1.0.19_CONNECTOR_MUTATION_CLAIM.md"),
    ):
        copy_prepared(root, source, destination)


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.18 — Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status_path.write_text(
        '''# VAAPP v1.0.19 — Scheduled Connector Mutation Claim & Retry Integrity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.18 source baseline: `b0005392a799bc5466a5e77febfd34035fb26ce3`
- Verified v1.0.18 GitHub Actions run: `33986405236` — success
- Verified v1.0.18 prerelease tag: `va-android-118-3-1`
- v1.0.18 release identity: backend `1.0.18`, Android `1.0.18+61`
- v1.0.18 APK SHA-256: `93aeddafa680ed4cf4b729fadd6401cad6af2f5240ec4cddaa8a355d3e862558`
- Historical v1.0.17 evidence: source `251e2e5a67ba137d2ac7b445a719d4be487df9fc`, GitHub Actions run `33981261146`, tag `va-android-117-2-1`.
- Historical v1.0.16 evidence: source `830c2c87b89972bc0735028584285f2827ac4bf9`, GitHub Actions run `33975481668`, tag `va-android-116-3-1`.
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.18.

## v1.0.19 maintenance scope

- Scheduled connector writes have a durable per-occurrence mutation ledger.
- REST/OAuth writes, webhooks, Telegram sends, SMTP sends, uploads, and arbitrary Browserless work are non-replay-safe.
- One worker must win an atomic `prepared -> submitting` claim before provider dispatch.
- The claim and scheduled timestamp are committed before the external mutation.
- Any post-claim provider exception becomes `execution_uncertain`.
- `execution_uncertain` occurrences never enter ordinary transient workflow replay.
- Ambiguous connector writes remain VA-owned and create no fake Needs You work.
- Read-only connector rules retain normal bounded transient retry behavior.
- Pre-v1.0.19 connector-rule retry/dead-letter/running jobs are quarantined once at startup.
- Later scheduled interval buckets remain independent occurrences and continue normally.

## Release identity

- Backend: `1.0.19`
- Required Android: `1.0.19`
- Android: `1.0.19+62`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
''',
        encoding="utf-8",
    )

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.18":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update({
        "updated": "2026-09-05",
        "verified_baseline_commit": EXPECTED_BASELINE,
        "verified_baseline_version": "1.0.18",
        "verified_baseline_android_version": "1.0.18+61",
        "verified_maintenance_actions_run_id": 33986405236,
        "verified_baseline_release_tag": "va-android-118-3-1",
        "current_phase_name": "v1.0.19 Scheduled Connector Mutation Claim & Retry Integrity",
        "current_version": "1.0.19",
        "current_android_version": "1.0.19+62",
        "phase_status": "source commit is gated by full GitHub Actions validation before publication",
        "v119_features": [
            "scheduled connector writes persist a durable per-occurrence mutation intent",
            "one worker atomically claims a scheduled connector mutation before provider dispatch",
            "claim ownership and last_run_at are committed before any external mutation",
            "post-claim connector exceptions become execution_uncertain instead of transient replay",
            "ambiguous scheduled connector writes remain VA-owned without fake Needs You work",
            "read-only connector rules retain ordinary bounded retry behavior",
            "pre-v1.0.19 connector-rule retries are quarantined once without provider replay",
            "later interval buckets remain independent scheduled occurrences",
        ],
    })
    invariants = list(state.get("invariants") or [])
    invariant = "an ambiguous scheduled connector mutation never enters generic transient workflow replay for the same occurrence"
    if invariant not in invariants:
        invariants.append(invariant)
    state["invariants"] = invariants
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff_path = root / "VAAPP_PROJECT_HANDOFF.md"
    handoff = read_text(handoff_path)
    current = "Current candidate: **v1.0.18 — Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression**."
    if current not in handoff:
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

The verified maintenance baseline for this release is commit `b0005392a799bc5466a5e77febfd34035fb26ce3` (`v1.0.18 — Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression`). GitHub Actions run `33986405236` completed successfully end-to-end with 431 backend tests, Ruff gates, Flutter analysis/tests, Android signing, signed APK build, source verification, and prerelease publication under tag `va-android-118-3-1`.

Verified v1.0.18 release identity: backend `1.0.18` / Android `1.0.18+61`. APK SHA-256: `93aeddafa680ed4cf4b729fadd6401cad6af2f5240ec4cddaa8a355d3e862558`. The operator subsequently reported production deployment and phone smoke testing complete.

Historical v1.0.17 source remains `251e2e5a67ba137d2ac7b445a719d4be487df9fc` with successful Actions run `33981261146` and tag `va-android-117-2-1`. Historical v1.0.16 source remains `830c2c87b89972bc0735028584285f2827ac4bf9` with successful Actions run `33975481668` and tag `va-android-116-3-1`. Historical v1.0.15 source remains `2b48b72e720a2e515e346fed253e24c131ae078a` with successful Actions run `33967944880` and tag `va-android-115-3-1`. Historical v1.0.14 source remains `8557dd449db554528ab7e111d0029faf784c996f` with successful Actions run `33961135886` and tag `va-android-114-3-1`. Historical v1.0.13 source remains `ecaa113d4461a550cb49c6046a42ecf880729346` with successful Actions run `33434347111` and tag `va-android-113-4-1`. Historical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.19` / Android `1.0.19+62`.

Current candidate: **v1.0.19 — Scheduled Connector Mutation Claim & Retry Integrity**.

v1.0.19 closes the generic scheduled-connector response-loss replay window. Every scheduled external mutation receives one durable occurrence intent and an atomic provider-dispatch claim. Once claimed, any ambiguous provider outcome stays VA-owned and cannot flow through ordinary transient workflow replay. Historical pre-claim connector-rule retries are quarantined once at startup. Read-only rules and later independent schedule occurrences continue normally.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass.

Next work after the v1.0.19 gate is green: **v1.x maintenance and real-world hardening**.

'''
    handoff_path.write_text(prefix + suffix, encoding="utf-8")


def bump_versions(root: Path) -> None:
    replace_once(root / "backend/app/core/version.py", 'APP_VERSION = "1.0.18"\nREQUIRED_ANDROID_VERSION = "1.0.18"\n', 'APP_VERSION = "1.0.19"\nREQUIRED_ANDROID_VERSION = "1.0.19"\n')
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.18"', 'version = "1.0.19"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.18+61", "version: 1.0.19+62")
    replace_once(root / "android/lib/release_contract.dart", "const String appRelease = '1.0.18';\nconst String minimumBackendVersion = '1.0.18';", "const String appRelease = '1.0.19';\nconst String minimumBackendVersion = '1.0.19';")
    replacements = (
        ('APP_VERSION = "1.0.18"', 'APP_VERSION = "1.0.19"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.18"', 'REQUIRED_ANDROID_VERSION = "1.0.19"'),
        ('version = "1.0.18"', 'version = "1.0.19"'),
        ('version: 1.0.18+61', 'version: 1.0.19+62'),
        ("appRelease = '1.0.18'", "appRelease = '1.0.19'"),
        ("minimumBackendVersion = '1.0.18'", "minimumBackendVersion = '1.0.19'"),
        ('APP_VERSION == "1.0.18"', 'APP_VERSION == "1.0.19"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v119_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected living release contracts to advance to v1.0.19")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    changed = sorted(set(
        [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
        + [line for line in run_git(root, "ls-files", "--others", "--exclude-standard").splitlines() if line]
    ))
    if not changed:
        raise RuntimeError("patch produced no changes")
    if any(name.startswith(".github/workflows/") for name in changed):
        raise RuntimeError("source patch attempted to modify a workflow")
    required = {
        "backend/app/models/entities.py", "backend/app/services/automation_engine.py",
        "backend/app/services/workflow_engine.py", "backend/app/services/connector_mutation_recovery.py",
        "backend/app/main.py", "backend/tests/test_v119_connector_mutation_claim.py",
        "backend/tests/test_v119_connector_mutation_claim_contract.py",
        "docs/V1.0.19_CONNECTOR_MUTATION_CLAIM.md", "backend/app/core/version.py",
        "backend/pyproject.toml", "android/pubspec.yaml", "android/lib/release_contract.dart",
        "STATUS.md", "VAAPP_PROJECT_STATE.json", "VAAPP_PROJECT_HANDOFF.md",
    }
    missing = sorted(required.difference(changed))
    if missing:
        raise RuntimeError(f"required v1.0.19 changes missing from diff: {missing}")
    checks = {
        "backend/app/models/entities.py": ["class ScheduledConnectorMutationIntent", "uq_scheduled_connector_mutation_rule_occurrence"],
        "backend/app/services/automation_engine.py": ["await claim_scheduled_connector_mutation", "scheduled_connector_mutation_uncertain", "automatic_replay"],
        "backend/app/services/workflow_engine.py": ["repair_v119_connector_rule_retry_backlog", "v119_connector_rule_retry_backlog_repaired"],
        "backend/app/main.py": ["repair_v119_connector_rule_retry_backlog"],
        "backend/app/services/connector_mutation_recovery.py": ["execution_uncertain", "connector_operation_is_mutating"],
    }
    for relative, markers in checks.items():
        text = read_text(root / relative)
        for marker in markers:
            if marker not in text:
                raise RuntimeError(f"v1.0.19 marker missing in {relative}: {marker}")


def apply(root: Path) -> None:
    verify_bundle()
    verify_repo(root)
    patch_models(root)
    patch_automation_engine(root)
    patch_workflow_engine(root)
    patch_main(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.19 source patch prepared. Changed files:")
    print(run_git(root, "diff", "--name-only"))
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard")
    if untracked:
        print(untracked)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--verify-bundle", action="store_true")
    args = parser.parse_args()
    verify_bundle()
    if args.verify_bundle:
        print("v1.0.19 bundle integrity verified")
        return
    apply(Path(args.root).resolve())


if __name__ == "__main__":
    main()
