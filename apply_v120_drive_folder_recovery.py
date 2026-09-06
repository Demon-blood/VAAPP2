from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "41ecf4ae69f9750cac821688df9fb9b9ee1213c6"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256: dict[str, str] = {'preview/backend/app/services/drive_folder_recovery.py': '975178ab4e2f9785145044c1a48e8d22cf187c51f89c955ad9102fe3fba2a693',
 'preview/backend/tests/test_v120_drive_folder_recovery.py': '971d0f9316338bd8b7efd9c805e8d020d46ffe56b9255b4c1015bdf9b04eb996',
 'preview/backend/tests/test_v120_drive_folder_recovery_contract.py': '7eef65fc19761d2ecdb49235399461013f3669ee4d9ec9efb3204c42fcabba47',
 'preview/docs/V1.0.20_DRIVE_FOLDER_RECOVERY.md': '770e450480cd64b583c038cc044465f16af2a4aca3f706874511eaad528e4a6a'}
STATUS_TEXT = '# VAAPP v1.0.20 — Drive Archive Folder Creation Recovery & Staged Upload Continuity\n\nUpdated: 2026-09-05\n\n## Source of truth\n\n- Repository: `Demon-blood/VAAPP2`\n- Branch: `main`\n- Verified v1.0.19 source baseline: `41ecf4ae69f9750cac821688df9fb9b9ee1213c6`\n- Verified v1.0.19 GitHub Actions run: `33989785817` — success\n- Verified v1.0.19 prerelease tag: `va-android-119-1-1`\n- v1.0.19 release identity: backend `1.0.19`, Android `1.0.19+62`\n- v1.0.19 APK SHA-256: `dec470cc1deacdb17cd1758191b984f1e17f194c5098a929d229efaee4d7a9d8`\n- Historical v1.0.18 evidence: source `b0005392a799bc5466a5e77febfd34035fb26ce3`, GitHub Actions run `33986405236`, tag `va-android-118-3-1`.\n- Historical v1.0.17 evidence: source `251e2e5a67ba137d2ac7b445a719d4be487df9fc`, GitHub Actions run `33981261146`, tag `va-android-117-2-1`.\n- Historical v1.0.16 evidence: source `830c2c87b89972bc0735028584285f2827ac4bf9`, GitHub Actions run `33975481668`, tag `va-android-116-3-1`.\n- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.\n- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.\n- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.\n- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.\n- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.\n\nThe operator subsequently reported production deployment and phone smoke testing complete for v1.0.19.\n\n## v1.0.20 maintenance scope\n\n- Drive archive folder creation has a durable path-keyed intent ledger.\n- Each cumulative archive folder path is reconciled before a new folder create is claimed.\n- Folder creates carry stable VA path properties for read-only provider recovery.\n- Legacy same-name folders under the exact parent can be adopted without another provider mutation.\n- A folder create is one-shot after an atomic `prepared -> submitting` claim.\n- `submitting` and `creation_uncertain` folder intents are reconciliation-only and never replay automatically.\n- The full folder path resolves before the exact-byte file intent can enter `submitting`.\n- Folder ambiguity leaves the file intent `prepared` with zero file-dispatch attempts.\n- Existing v1.0.18 file ambiguity remains fail-closed and is never blindly reopened.\n- Drive provider ambiguity remains VA-owned and creates no fake Needs You work.\n\n## Release identity\n\n- Backend: `1.0.20`\n- Required Android: `1.0.20`\n- Android: `1.0.20+63`\n\nSource publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.\n'
HANDOFF_PREFIX = '# VAAPP project handoff\n\nUpdated: 2026-09-05\nRepository: `Demon-blood/VAAPP2`\nBranch: `main`\n\n## Verified source of truth\n\nThe verified maintenance baseline for this release is commit `41ecf4ae69f9750cac821688df9fb9b9ee1213c6` (`v1.0.19 — Scheduled Connector Mutation Claim & Retry Integrity`). GitHub Actions run `33989785817` completed successfully end-to-end with 443 backend tests, Ruff gates, Flutter analysis/tests, Android signing, signed APK build, source verification, and prerelease publication under tag `va-android-119-1-1`.\n\nVerified v1.0.19 release identity: backend `1.0.19` / Android `1.0.19+62`. APK SHA-256: `dec470cc1deacdb17cd1758191b984f1e17f194c5098a929d229efaee4d7a9d8`. The operator subsequently reported production deployment and phone smoke testing complete.\n\nHistorical v1.0.18 source remains `b0005392a799bc5466a5e77febfd34035fb26ce3` with successful Actions run `33986405236` and tag `va-android-118-3-1`. Historical v1.0.17 source remains `251e2e5a67ba137d2ac7b445a719d4be487df9fc` with successful Actions run `33981261146` and tag `va-android-117-2-1`. Historical v1.0.16 source remains `830c2c87b89972bc0735028584285f2827ac4bf9` with successful Actions run `33975481668` and tag `va-android-116-3-1`. Historical v1.0.15 source remains `2b48b72e720a2e515e346fed253e24c131ae078a` with successful Actions run `33967944880` and tag `va-android-115-3-1`. Historical v1.0.14 source remains `8557dd449db554528ab7e111d0029faf784c996f` with successful Actions run `33961135886` and tag `va-android-114-3-1`. Historical v1.0.13 source remains `ecaa113d4461a550cb49c6046a42ecf880729346` with successful Actions run `33434347111` and tag `va-android-113-4-1`. Historical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current maintenance candidate\n\nBackend `1.0.20` / Android `1.0.20+63`.\n\nCurrent candidate: **v1.0.20 — Drive Archive Folder Creation Recovery & Staged Upload Continuity**.\n\nv1.0.20 closes the remaining Drive archive setup response-loss window. Every cumulative archive folder path receives a durable provider-reconcilable identity, and the complete folder path must be verified before the v1.0.18 exact-byte file intent can be claimed. Folder response loss therefore stays folder-owned and cannot falsely strand a file intent as creation-uncertain. Existing historical file ambiguity remains fail-closed.\n\nThe guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass.\n\nNext work after the v1.0.20 gate is green: **v1.x maintenance and real-world hardening**.\n\n'


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


def replace_exact_count(path: Path, old: str, new: str, expected: int) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != expected:
        raise RuntimeError(
            f"expected {expected} anchors in {path}: found {count}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


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
            f"refusing to patch unexpected HEAD {head}; "
            f"expected v1.0.19 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.19"\nREQUIRED_ANDROID_VERSION = "1.0.19"\n'
    ):
        raise RuntimeError("v1.0.19 backend baseline identity mismatch")
    if "version: 1.0.19+62" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.19 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_models(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    anchor = '''class DocumentArchiveUploadIntent(Base):
    __tablename__ = "document_archive_upload_intents"
'''
    addition = '''class DriveArchiveFolderIntent(Base):
    __tablename__ = "drive_archive_folder_intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    path_key: Mapped[str] = mapped_column(String(64), index=True)
    parent_path_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    folder_name: Mapped[str] = mapped_column(Text)
    logical_path_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="prepared", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    parent_drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_folder_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    observed_folder_json: Mapped[str] = mapped_column(Text, default="{}")
    last_error: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "path_key",
            name="uq_drive_archive_folder_path_key",
        ),
    )


class DocumentArchiveUploadIntent(Base):
    __tablename__ = "document_archive_upload_intents"
'''
    replace_once(path, anchor, addition)


def patch_google_api(root: Path) -> None:
    path = root / "backend/app/integrations/google_api.py"
    anchor = '''async def upload_drive_file(
    db: AsyncSession,
'''
    helper = r'''async def find_drive_folder_candidates(
    db: AsyncSession,
    *,
    folder_name: str,
    parent_id: str | None,
    path_key: str,
) -> list[dict[str, Any]]:
    service = await drive_service(db)
    parent_ref = _drive_query_literal(parent_id or "root")
    escaped_name = _drive_query_literal(folder_name)
    escaped_key = _drive_query_literal(path_key)
    base = [
        "mimeType='application/vnd.google-apps.folder'",
        "trashed=false",
        f"'{parent_ref}' in parents",
    ]
    queries = [
        " and ".join(
            base
            + [
                "appProperties has { "
                f"key='va_folder_path_key' and value='{escaped_key}'"
                " }"
            ]
        ),
        " and ".join(base + [f"name='{escaped_name}'"]),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        response = await _execute_google_request(
            lambda query=query: service.files().list(
                q=query,
                spaces="drive",
                fields="files(id,name,mimeType,createdTime,appProperties,parents)",
                orderBy="createdTime asc",
                pageSize=100,
            ),
            attempts=4,
        )
        for item in response.get("files", []) or []:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("id") or "")
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            rows.append(dict(item))
    return rows


async def create_drive_folder_once(
    db: AsyncSession,
    *,
    folder_name: str,
    parent_id: str | None,
    path_key: str,
    parent_path_key: str,
) -> dict[str, Any]:
    service = await drive_service(db)
    metadata: dict[str, Any] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "appProperties": {
            "va_managed_folder": "true",
            "va_folder_path_key": path_key,
            "va_folder_parent_path_key": parent_path_key,
        },
    }
    if parent_id:
        metadata["parents"] = [parent_id]
    result = await asyncio.to_thread(
        lambda: service.files().create(
            body=metadata,
            fields="id,name,mimeType,createdTime,appProperties,parents",
        ).execute()
    )
    return dict(result or {})


'''
    replace_once(path, anchor, helper + anchor)

    old = '''async def upload_drive_file(
    db: AsyncSession,
    *,
    name: str,
    mime_type: str,
    content: bytes,
    folder_path: list[str],
    app_properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    service = await drive_service(db)
    parent_id: str | None = None
    for folder in folder_path:
        parent_id = await ensure_drive_folder(db, folder, parent_id)
    metadata: dict[str, Any] = {
        "name": name,
        "parents": [parent_id] if parent_id else [],
        "appProperties": app_properties or {},
    }
'''
    new = '''async def upload_drive_file(
    db: AsyncSession,
    *,
    name: str,
    mime_type: str,
    content: bytes,
    folder_path: list[str] | None = None,
    app_properties: dict[str, str] | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    service = await drive_service(db)
    resolved_parent_id = parent_id
    if resolved_parent_id is None:
        for folder in folder_path or []:
            resolved_parent_id = await ensure_drive_folder(
                db,
                folder,
                resolved_parent_id,
            )
    metadata: dict[str, Any] = {
        "name": name,
        "parents": [resolved_parent_id] if resolved_parent_id else [],
        "appProperties": app_properties or {},
    }
'''
    replace_once(path, old, new)


def patch_document_archive_recovery(root: Path) -> None:
    path = root / "backend/app/services/document_archive_recovery.py"
    replace_once(
        path,
        "from app.integrations.google_api import GoogleConfigurationError\n",
        '''from app.integrations.google_api import (
    GoogleConfigurationError,
    create_drive_folder_once,
    find_drive_folder_candidates,
)
''',
    )
    replace_once(
        path,
        "from app.models.entities import DocumentArchiveUploadIntent\n",
        '''from app.models.entities import DocumentArchiveUploadIntent
from app.services.drive_folder_recovery import (
    DriveFolderCreationUncertainError,
    ensure_drive_archive_folder_path,
)
''',
    )

    old = '''    if intent.status != "prepared":
        raise RuntimeError(f"unsupported Drive archive intent state: {intent.status}")

    claimed = await _claim_fresh_upload(db, intent)
'''
    new = '''    if intent.status != "prepared":
        raise RuntimeError(f"unsupported Drive archive intent state: {intent.status}")

    folder_path_value = list(_loads(intent.folder_path_json, folder_path))
    try:
        resolved_parent_id = await ensure_drive_archive_folder_path(
            db,
            folder_path=folder_path_value,
            find_folders=find_drive_folder_candidates,
            create_folder=create_drive_folder_once,
        )
    except DriveFolderCreationUncertainError as exc:
        intent.last_error = (
            "Drive folder setup remains reconciliation-owned before file dispatch; "
            f"the file intent is still prepared: {exc}"
        )[:4000]
        await db.commit()
        raise DriveArchiveCreationUncertainError(intent.last_error) from exc

    claimed = await _claim_fresh_upload(db, intent)
'''
    replace_once(path, old, new)

    old_upload = '''            content=content,
            folder_path=list(_loads(intent.folder_path_json, folder_path)),
            app_properties=dict(_loads(intent.app_properties_json, app_properties)),
        )
'''
    new_upload = '''            content=content,
            folder_path=[],
            app_properties=dict(_loads(intent.app_properties_json, app_properties)),
            parent_id=resolved_parent_id,
        )
'''
    replace_once(path, old_upload, new_upload)


def patch_legacy_v118_tests(root: Path) -> None:
    path = root / "backend/tests/test_v118_drive_archive_recovery.py"
    replace_once(
        path,
        '''async def _fake_analyze(db, record):
    return {"document_id": record.id, "status": "analyzed"}


''',
        '''async def _fake_analyze(db, record):
    return {"document_id": record.id, "status": "analyzed"}


async def _fake_folder_path(*args, **kwargs):
    return "folder-v118-test"


''',
    )
    old = '''    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)
'''
    new = '''    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)
    monkeypatch.setattr(
        "app.services.document_archive_recovery.ensure_drive_archive_folder_path",
        _fake_folder_path,
    )
'''
    replace_exact_count(path, old, new, 5)


def patch_portal_document_test(root: Path) -> None:
    path = root / "backend/tests/test_portal_document_sync.py"
    replace_once(
        path,
        '''    async def fake_find(*args, **kwargs):
        return []

    async def fake_analyze(db, record):
''',
        '''    async def fake_find(*args, **kwargs):
        return []

    async def fake_folder_path(*args, **kwargs):
        return "folder-portal-test"

    async def fake_analyze(db, record):
''',
    )
    replace_once(
        path,
        '''    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", fake_analyze)
''',
        '''    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr(
        "app.services.document_archive_recovery.ensure_drive_archive_folder_path",
        fake_folder_path,
    )
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", fake_analyze)
''',
    )


def write_new_files(root: Path) -> None:
    for source, destination in (
        (
            "preview/backend/app/services/drive_folder_recovery.py",
            "backend/app/services/drive_folder_recovery.py",
        ),
        (
            "preview/backend/tests/test_v120_drive_folder_recovery.py",
            "backend/tests/test_v120_drive_folder_recovery.py",
        ),
        (
            "preview/backend/tests/test_v120_drive_folder_recovery_contract.py",
            "backend/tests/test_v120_drive_folder_recovery_contract.py",
        ),
        (
            "preview/docs/V1.0.20_DRIVE_FOLDER_RECOVERY.md",
            "docs/V1.0.20_DRIVE_FOLDER_RECOVERY.md",
        ),
    ):
        copy_prepared(root, source, destination)


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.19 — Scheduled Connector Mutation Claim & Retry Integrity" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status_path.write_text(STATUS_TEXT, encoding="utf-8")

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.19":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-09-05",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.19",
            "verified_baseline_android_version": "1.0.19+62",
            "verified_maintenance_actions_run_id": 33989785817,
            "verified_baseline_release_tag": "va-android-119-1-1",
            "current_phase_name": (
                "v1.0.20 Drive Archive Folder Creation Recovery & Staged Upload Continuity"
            ),
            "current_version": "1.0.20",
            "current_android_version": "1.0.20+63",
            "phase_status": (
                "source commit is gated by full GitHub Actions validation before publication"
            ),
            "v120_features": [
                "Drive archive folder creation uses a durable path-keyed provider intent",
                "each cumulative folder path is reconciled before provider create",
                "folder creates carry stable provider app properties for recovery",
                "legacy exact-parent same-name folders can be adopted without another create",
                "ambiguous folder creates remain reconciliation-only and are never replayed",
                "the complete folder path resolves before exact-byte file dispatch is claimed",
                "folder uncertainty leaves the file intent prepared with zero file attempts",
                "existing historical file uncertainty remains fail-closed without blind replay",
                "Drive folder and file ambiguity remain VA-owned without fake Needs You work",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    invariant = (
        "Drive archive folder uncertainty is resolved before file dispatch and never "
        "authorizes a duplicate folder or file create"
    )
    if invariant not in invariants:
        invariants.append(invariant)
    state["invariants"] = invariants
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    handoff_path = root / "VAAPP_PROJECT_HANDOFF.md"
    handoff = read_text(handoff_path)
    current = (
        "Current candidate: **v1.0.19 — Scheduled Connector Mutation Claim & "
        "Retry Integrity**."
    )
    if current not in handoff:
        raise RuntimeError("unexpected VAAPP_PROJECT_HANDOFF.md baseline")
    marker = "## Product objective\n"
    if marker not in handoff:
        raise RuntimeError("handoff product-objective marker is missing")
    suffix = marker + handoff.split(marker, 1)[1]
    handoff_path.write_text(HANDOFF_PREFIX + suffix, encoding="utf-8")


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.19"\nREQUIRED_ANDROID_VERSION = "1.0.19"\n',
        'APP_VERSION = "1.0.20"\nREQUIRED_ANDROID_VERSION = "1.0.20"\n',
    )
    replace_once(
        root / "backend/pyproject.toml",
        'version = "1.0.19"',
        'version = "1.0.20"',
    )
    replace_once(
        root / "android/pubspec.yaml",
        "version: 1.0.19+62",
        "version: 1.0.20+63",
    )
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.19';\n"
        "const String minimumBackendVersion = '1.0.19';\n",
        "const String appRelease = '1.0.20';\n"
        "const String minimumBackendVersion = '1.0.20';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.19"', 'APP_VERSION = "1.0.20"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.19"', 'REQUIRED_ANDROID_VERSION = "1.0.20"'),
        ('version = "1.0.19"', 'version = "1.0.20"'),
        ('version: 1.0.19+62', 'version: 1.0.20+63'),
        ("appRelease = '1.0.19'", "appRelease = '1.0.20'"),
        (
            "minimumBackendVersion = '1.0.19'",
            "minimumBackendVersion = '1.0.20'",
        ),
        ('APP_VERSION == "1.0.19"', 'APP_VERSION == "1.0.20"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v120_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected living release contracts to advance to v1.0.20")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    tracked = [
        line for line in run_git(root, "diff", "--name-only").splitlines() if line
    ]
    untracked = [
        line
        for line in run_git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
        ).splitlines()
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
        "backend/app/services/document_archive_recovery.py",
        "backend/app/services/drive_folder_recovery.py",
        "backend/tests/test_v118_drive_archive_recovery.py",
        "backend/tests/test_portal_document_sync.py",
        "backend/tests/test_v120_drive_folder_recovery.py",
        "backend/tests/test_v120_drive_folder_recovery_contract.py",
        "docs/V1.0.20_DRIVE_FOLDER_RECOVERY.md",
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
        raise RuntimeError(f"required v1.0.20 changes missing from diff: {missing}")

    checks = {
        "backend/app/models/entities.py": [
            "class DriveArchiveFolderIntent",
            "uq_drive_archive_folder_path_key",
            "drive_folder_id",
        ],
        "backend/app/integrations/google_api.py": [
            "async def find_drive_folder_candidates",
            "async def create_drive_folder_once",
            "va_folder_path_key",
            "parent_id: str | None = None",
        ],
        "backend/app/services/document_archive_recovery.py": [
            "await ensure_drive_archive_folder_path",
            "Drive folder setup remains reconciliation-owned",
            "parent_id=resolved_parent_id",
            "folder_path=[]",
        ],
        "backend/app/services/drive_folder_recovery.py": [
            "DriveFolderCreationUncertainError",
            "creation_uncertain",
            "duplicate folder creation is suppressed",
            "ensure_drive_archive_folder_path",
        ],
    }
    for relative, markers in checks.items():
        text = read_text(root / relative)
        for marker in markers:
            if marker not in text:
                raise RuntimeError(f"v1.0.20 marker missing in {relative}: {marker}")

    v118 = read_text(root / "backend/tests/test_v118_drive_archive_recovery.py")
    portal = read_text(root / "backend/tests/test_portal_document_sync.py")
    if "ensure_drive_archive_folder_path" not in v118:
        raise RuntimeError("v1.0.18 tests do not stub the new folder recovery stage")
    if "ensure_drive_archive_folder_path" not in portal:
        raise RuntimeError("portal exact-byte test does not stub the new folder recovery stage")


def apply(root: Path) -> None:
    verify_bundle()
    verify_repo(root)
    patch_models(root)
    patch_google_api(root)
    patch_document_archive_recovery(root)
    patch_legacy_v118_tests(root)
    patch_portal_document_test(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.20 source patch prepared. Changed files:")
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
        print("v1.0.20 bundle integrity verified")
        return
    apply(Path(args.root).resolve())


if __name__ == "__main__":
    main()
