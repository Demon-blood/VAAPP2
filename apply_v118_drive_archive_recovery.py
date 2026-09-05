from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "251e2e5a67ba137d2ac7b445a719d4be487df9fc"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256: dict[str, str] = {'preview/backend/app/services/document_archive_recovery.py': '41df085b3f2524605e9d49098ebcc1ec251af8eb546fd4640f0fa04f64dc4e6c',
 'preview/backend/tests/test_v118_drive_archive_recovery.py': 'c66e8bdffcacb1d2a8b1bfef4952d1d1daa810834ca5dfa2c249899da806f141',
 'preview/backend/tests/test_v118_drive_archive_recovery_contract.py': '7471f3621617981514a241d703ea346a26a160c2230eee09a283dcf4510d0cec',
 'preview/docs/V1.0.18_DRIVE_ARCHIVE_RECOVERY.md': 'cac90ca8f740492456f0c4170502ea6f9819b6b4f5e2670141b9da3f957a5a91'}


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
            f"refusing to patch unexpected HEAD {head}; expected v1.0.17 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.17"\nREQUIRED_ANDROID_VERSION = "1.0.17"\n'
    ):
        raise RuntimeError("v1.0.17 backend baseline identity mismatch")
    if "version: 1.0.17+60" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.17 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_models(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    old = '''class DocumentRecord(Base):
    __tablename__ = "documents"
'''
    new = '''class DocumentArchiveUploadIntent(Base):
    __tablename__ = "document_archive_upload_intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    account_scope: Mapped[str] = mapped_column(String(30), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="")
    source_id: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(160))
    folder_path_json: Mapped[str] = mapped_column(Text, default="[]")
    app_properties_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), default="prepared", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    drive_file_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    observed_file_json: Mapped[str] = mapped_column(Text, default="{}")
    last_error: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "checksum_sha256", "account_scope",
            name="uq_document_archive_upload_checksum_scope",
        ),
    )


class DocumentRecord(Base):
    __tablename__ = "documents"
'''
    replace_once(path, old, new)


def patch_google_api(root: Path) -> None:
    path = root / "backend/app/integrations/google_api.py"
    anchor = '''async def upload_drive_file(
    db: AsyncSession,
'''
    helper = r'''def _drive_query_literal(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


async def find_drive_files_by_app_properties(
    db: AsyncSession,
    *,
    app_properties: dict[str, str],
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Read VA-managed Drive files matching every requested app property."""
    if not app_properties:
        raise ValueError("Drive app-property reconciliation requires at least one property")
    service = await drive_service(db)
    clauses = ["trashed=false"]
    for key, value in sorted(app_properties.items()):
        escaped_key = _drive_query_literal(key)
        escaped_value = _drive_query_literal(value)
        clauses.append(
            "appProperties has { "
            f"key='{escaped_key}' and value='{escaped_value}'"
            " }"
        )
    query = " and ".join(clauses)
    rows: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        response = await _execute_google_request(
            lambda page_token=page_token: service.files().list(
                q=query,
                spaces="drive",
                fields=(
                    "nextPageToken,files("
                    "id,name,mimeType,size,webViewLink,createdTime,appProperties,parents)"
                ),
                orderBy="createdTime asc",
                pageSize=max(1, min(page_size, 1000)),
                pageToken=page_token,
            ),
            attempts=4,
        )
        rows.extend(
            dict(item) for item in response.get("files", []) or []
            if isinstance(item, dict)
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return rows


'''
    replace_once(path, anchor, helper + anchor)


def patch_document_ingestion(root: Path) -> None:
    path = root / "backend/app/services/document_ingestion.py"
    replace_once(
        path,
        "from sqlalchemy import select\n",
        "from sqlalchemy import select\nfrom sqlalchemy.exc import IntegrityError\n",
    )
    replace_once(
        path,
        "from app.integrations.google_api import upload_drive_file\n",
        '''from app.integrations.google_api import (
    find_drive_files_by_app_properties,
    upload_drive_file,
)
''',
    )
    replace_once(
        path,
        "from app.services.document_policy import document_category_decision, document_retention_decision\n",
        '''from app.services.document_archive_recovery import ensure_document_archive_upload
from app.services.document_policy import document_category_decision, document_retention_decision
''',
    )
    old = '''        uploaded = await upload_drive_file(
            db,
            name=safe_name,
            mime_type=normalized_mime,
            content=content,
            folder_path=[
                settings.google_drive_archive_folder,
                "Professional" if account_scope == "pro" else "Personal",
                resolved_category.replace("/", "-")[:80] or "General",
                str(date.year),
            ],
            app_properties={
                "va_managed": "true",
                "source_type": source_type[:40],
                "source_id": source_id[:255],
                "category": resolved_category[:120],
                "account_scope": account_scope,
                "checksum_sha256": checksum,
            },
        )
        record = DocumentRecord(
            source_type=source_type[:40],
            source_id=source_id[:255],
            name=str(uploaded.get("name") or safe_name),
            mime_type=str(uploaded.get("mimeType") or normalized_mime),
            size_bytes=int(uploaded.get("size") or len(content)),
            category=resolved_category,
            account_scope=account_scope,
            checksum_sha256=checksum,
            drive_file_id=str(uploaded["id"]),
            drive_web_url=str(uploaded.get("webViewLink") or ""),
        )
        db.add(record)
        await db.flush()
'''
    new = '''        folder_path = [
            settings.google_drive_archive_folder,
            "Professional" if account_scope == "pro" else "Personal",
            resolved_category.replace("/", "-")[:80] or "General",
            str(date.year),
        ]
        app_properties = {
            "va_managed": "true",
            "source_type": source_type[:40],
            "source_id": source_id[:255],
            "category": resolved_category[:120],
            "account_scope": account_scope,
            "checksum_sha256": checksum,
        }
        uploaded = await ensure_document_archive_upload(
            db,
            checksum_sha256=checksum,
            account_scope=account_scope,
            source_type=source_type,
            source_id=source_id,
            name=safe_name,
            mime_type=normalized_mime,
            content=content,
            folder_path=folder_path,
            app_properties=app_properties,
            upload_file=upload_drive_file,
            find_files=find_drive_files_by_app_properties,
        )

        record = (
            await db.execute(
                select(DocumentRecord)
                .where(
                    DocumentRecord.checksum_sha256 == checksum,
                    DocumentRecord.account_scope == account_scope,
                )
                .order_by(DocumentRecord.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if record is not None:
            created = False
        else:
            record = DocumentRecord(
                source_type=source_type[:40],
                source_id=source_id[:255],
                name=str(uploaded.get("name") or safe_name),
                mime_type=str(uploaded.get("mimeType") or normalized_mime),
                size_bytes=int(uploaded.get("size") or len(content)),
                category=resolved_category,
                account_scope=account_scope,
                checksum_sha256=checksum,
                drive_file_id=str(uploaded["id"]),
                drive_web_url=str(uploaded.get("webViewLink") or ""),
            )
            db.add(record)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                record = (
                    await db.execute(
                        select(DocumentRecord)
                        .where(
                            DocumentRecord.checksum_sha256 == checksum,
                            DocumentRecord.account_scope == account_scope,
                        )
                        .order_by(DocumentRecord.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if record is None:
                    raise
                created = False
'''
    replace_once(path, old, new)


def patch_legacy_portal_test(root: Path) -> None:
    path = root / "backend/tests/test_portal_document_sync.py"
    replace_once(
        path,
        '''    async def fake_analyze(db, record):
        return {"document_id": record.id, "status": "analyzed"}

    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
''',
        '''    async def fake_find(*args, **kwargs):
        return []

    async def fake_analyze(db, record):
        return {"document_id": record.id, "status": "analyzed"}

    monkeypatch.setattr(
        "app.services.document_ingestion.find_drive_files_by_app_properties",
        fake_find,
    )
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
''',
    )


def write_new_files(root: Path) -> None:
    for source, destination in (
        (
            "preview/backend/app/services/document_archive_recovery.py",
            "backend/app/services/document_archive_recovery.py",
        ),
        (
            "preview/backend/tests/test_v118_drive_archive_recovery.py",
            "backend/tests/test_v118_drive_archive_recovery.py",
        ),
        (
            "preview/backend/tests/test_v118_drive_archive_recovery_contract.py",
            "backend/tests/test_v118_drive_archive_recovery_contract.py",
        ),
        (
            "preview/docs/V1.0.18_DRIVE_ARCHIVE_RECOVERY.md",
            "docs/V1.0.18_DRIVE_ARCHIVE_RECOVERY.md",
        ),
    ):
        copy_prepared(root, source, destination)


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.17 — Investment Side-Effect Recovery & Human Boundary Integrity" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status_path.write_text(
        '''# VAAPP v1.0.18 — Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.17 source baseline: `251e2e5a67ba137d2ac7b445a719d4be487df9fc`
- Verified v1.0.17 GitHub Actions run: `33981261146` — success
- Verified v1.0.17 prerelease tag: `va-android-117-2-1`
- v1.0.17 release identity: backend `1.0.17`, Android `1.0.17+60`
- v1.0.17 APK SHA-256: `caf1c2e41efe1abccd96dd6699efa0f7b50323093f1c09ed1a1397e1da9832fc`
- Historical v1.0.16 evidence: source `830c2c87b89972bc0735028584285f2827ac4bf9`, GitHub Actions run `33975481668`, tag `va-android-116-3-1`.
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.17.

## v1.0.18 maintenance scope

- Exact-byte Drive archive upload intent is durable before provider mutation.
- One intent exists per SHA-256 and account scope.
- Fresh upload dispatch requires an atomic `prepared -> submitting` claim.
- Drive app properties provide independent checksum/scope recovery evidence.
- `submitting` and `creation_uncertain` uploads are reconciliation-only.
- Retry, restart, or elapsed time never authorizes another Drive create.
- Historical orphan Drive files bind without another upload.
- Historical exact-byte duplicates bind the oldest observed copy without a new mutation.
- Drive ambiguity remains VA-owned and creates no fake Needs You work.

## Release identity

- Backend: `1.0.18`
- Required Android: `1.0.18`
- Android: `1.0.18+61`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
''',
        encoding="utf-8",
    )

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.17":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-09-05",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.17",
            "verified_baseline_android_version": "1.0.17+60",
            "verified_maintenance_actions_run_id": 33981261146,
            "verified_baseline_release_tag": "va-android-117-2-1",
            "current_phase_name": "v1.0.18 Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression",
            "current_version": "1.0.18",
            "current_android_version": "1.0.18+61",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "v118_features": [
                "exact-byte Drive archive intent is durable before provider mutation",
                "one archive upload intent exists per checksum and account scope",
                "fresh Drive create requires an atomic prepared-to-submitting claim",
                "provider app properties reconcile orphaned or late upload evidence",
                "submitting and creation_uncertain uploads are reconciliation-only",
                "retries and restarts never authorize duplicate Drive create",
                "historical exact-byte duplicates bind without another provider mutation",
                "Drive archive ambiguity remains VA-owned without fake Needs You work",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    invariant = (
        "ambiguous Drive archive creation remains reconciliation-only and never "
        "authorizes a duplicate exact-byte upload"
    )
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
    current = "Current candidate: **v1.0.17 — Investment Side-Effect Recovery & Human Boundary Integrity**."
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

The verified maintenance baseline for this release is commit `251e2e5a67ba137d2ac7b445a719d4be487df9fc` (`v1.0.17 — Investment Side-Effect Recovery & Human Boundary Integrity`). GitHub Actions run `33981261146` completed successfully end-to-end with 420 backend tests, Ruff gates, Flutter analysis/tests, Android signing, signed APK build, source verification, and prerelease publication under tag `va-android-117-2-1`.

Verified v1.0.17 release identity: backend `1.0.17` / Android `1.0.17+60`. APK SHA-256: `caf1c2e41efe1abccd96dd6699efa0f7b50323093f1c09ed1a1397e1da9832fc`. The operator subsequently reported production deployment and phone smoke testing complete.

Historical v1.0.16 source remains `830c2c87b89972bc0735028584285f2827ac4bf9` with successful Actions run `33975481668` and tag `va-android-116-3-1`. Historical v1.0.15 source remains `2b48b72e720a2e515e346fed253e24c131ae078a` with successful Actions run `33967944880` and tag `va-android-115-3-1`. Historical v1.0.14 source remains `8557dd449db554528ab7e111d0029faf784c996f` with successful Actions run `33961135886` and tag `va-android-114-3-1`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.18` / Android `1.0.18+61`.

Current candidate: **v1.0.18 — Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression**.

v1.0.18 closes the archive-upload response-loss window. Exact-byte content gets one durable upload intent per account scope before Drive mutation. A fresh worker must atomically claim that intent before `files.create()`. Once dispatch is ambiguous, all later work is read-only reconciliation by the existing Drive checksum/account-scope app properties; retries, restarts, and elapsed time never authorize another upload. Provider ambiguity remains VA-owned.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass.

Next work after the v1.0.18 gate is green: **v1.x maintenance and real-world hardening**.

'''
    handoff_path.write_text(prefix + suffix, encoding="utf-8")


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.17"\nREQUIRED_ANDROID_VERSION = "1.0.17"\n',
        'APP_VERSION = "1.0.18"\nREQUIRED_ANDROID_VERSION = "1.0.18"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.17"', 'version = "1.0.18"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.17+60", "version: 1.0.18+61")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.17';\nconst String minimumBackendVersion = '1.0.17';\n",
        "const String appRelease = '1.0.18';\nconst String minimumBackendVersion = '1.0.18';\n",
    )
    replacements = (
        ('APP_VERSION = "1.0.17"', 'APP_VERSION = "1.0.18"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.17"', 'REQUIRED_ANDROID_VERSION = "1.0.18"'),
        ('version = "1.0.17"', 'version = "1.0.18"'),
        ('version: 1.0.17+60', 'version: 1.0.18+61'),
        ("appRelease = '1.0.17'", "appRelease = '1.0.18'"),
        ("minimumBackendVersion = '1.0.17'", "minimumBackendVersion = '1.0.18'"),
        ('APP_VERSION == "1.0.17"', 'APP_VERSION == "1.0.18"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v118_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected living release contracts to advance to v1.0.18")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    tracked = [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
    untracked = [
        line for line in run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
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
        "backend/app/integrations/google_api.py",
        "backend/app/services/document_ingestion.py",
        "backend/app/services/document_archive_recovery.py",
        "backend/tests/test_portal_document_sync.py",
        "backend/tests/test_v118_drive_archive_recovery.py",
        "backend/tests/test_v118_drive_archive_recovery_contract.py",
        "docs/V1.0.18_DRIVE_ARCHIVE_RECOVERY.md",
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
        raise RuntimeError(f"required v1.0.18 changes missing from diff: {missing}")
    checks = {
        "backend/app/models/entities.py": [
            "class DocumentArchiveUploadIntent", "uq_document_archive_upload_checksum_scope",
        ],
        "backend/app/integrations/google_api.py": [
            "async def find_drive_files_by_app_properties", "appProperties has", "createdTime",
        ],
        "backend/app/services/document_ingestion.py": [
            "ensure_document_archive_upload", "upload_file=upload_drive_file",
            "find_files=find_drive_files_by_app_properties", "except IntegrityError",
        ],
        "backend/app/services/document_archive_recovery.py": [
            "DriveArchiveCreationUncertainError", "duplicate upload is suppressed",
            "automatic upload replay is disabled", "reconcile_document_archive_upload",
        ],
    }
    for relative, markers in checks.items():
        text = read_text(root / relative)
        for marker in markers:
            if marker not in text:
                raise RuntimeError(f"v1.0.18 marker missing in {relative}: {marker}")


def apply(root: Path) -> None:
    verify_bundle()
    verify_repo(root)
    patch_models(root)
    patch_google_api(root)
    patch_document_ingestion(root)
    patch_legacy_portal_test(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.18 source patch prepared. Changed files:")
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
        print("v1.0.18 bundle integrity verified")
        return
    apply(Path(args.root).resolve())


if __name__ == "__main__":
    main()
