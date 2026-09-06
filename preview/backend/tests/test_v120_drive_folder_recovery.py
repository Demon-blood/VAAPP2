import base64
import os

os.environ.setdefault("PUBLIC_BASE_URL", "https://va.example.test")
os.environ.setdefault("PAIRING_SECRET", "x" * 32)
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"1" * 32).decode(),
)

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import DocumentArchiveUploadIntent, DriveArchiveFolderIntent
from app.services.document_archive_recovery import (
    DriveArchiveCreationUncertainError,
    ensure_document_archive_upload,
)
from app.services.drive_folder_recovery import (
    DriveFolderCreationUncertainError,
    drive_archive_folder_path_key,
    ensure_drive_archive_folder_path,
)


async def _sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _folder_row(
    *,
    folder_id: str,
    name: str,
    path_key: str = "",
    created: str = "2026-09-05T10:00:00Z",
) -> dict:
    properties = {"va_managed_folder": "true"}
    if path_key:
        properties["va_folder_path_key"] = path_key
    return {
        "id": folder_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "createdTime": created,
        "appProperties": properties,
    }


@pytest.mark.asyncio
async def test_lost_folder_create_response_recovers_without_second_create() -> None:
    engine, sessions = await _sessions()
    provider_folders: list[dict] = []
    create_calls = 0

    async def fake_find(db, *, folder_name, parent_id, path_key):
        return list(provider_folders)

    async def fake_create(
        db,
        *,
        folder_name,
        parent_id,
        path_key,
        parent_path_key,
    ):
        nonlocal create_calls
        create_calls += 1
        provider_folders.append(
            _folder_row(
                folder_id="folder-1",
                name=folder_name,
                path_key=path_key,
            )
        )
        raise TimeoutError("Drive accepted folder but response was lost")

    async with sessions() as db:
        folder_id = await ensure_drive_archive_folder_path(
            db,
            folder_path=["Full-Time VA"],
            find_folders=fake_find,
            create_folder=fake_create,
        )
        assert folder_id == "folder-1"
        assert create_calls == 1
        rows = list(
            (
                await db.execute(
                    select(DriveArchiveFolderIntent).order_by(DriveArchiveFolderIntent.id)
                )
            ).scalars()
        )
        assert rows[0].status == "verified"
        assert rows[0].drive_folder_id == "folder-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_delayed_folder_evidence_never_replays_ambiguous_create() -> None:
    engine, sessions = await _sessions()
    provider_folders: list[dict] = []
    create_calls = 0
    reveal = False

    async def fake_find(db, *, folder_name, parent_id, path_key):
        return list(provider_folders) if reveal else []

    async def fake_create(
        db,
        *,
        folder_name,
        parent_id,
        path_key,
        parent_path_key,
    ):
        nonlocal create_calls
        create_calls += 1
        provider_folders.append(
            _folder_row(
                folder_id="folder-late",
                name=folder_name,
                path_key=path_key,
            )
        )
        raise TimeoutError("provider response lost")

    async with sessions() as db:
        with pytest.raises(DriveFolderCreationUncertainError):
            await ensure_drive_archive_folder_path(
                db,
                folder_path=["Archive"],
                find_folders=fake_find,
                create_folder=fake_create,
            )
        intent = (await db.execute(select(DriveArchiveFolderIntent))).scalar_one()
        assert intent.status == "creation_uncertain"
        assert intent.attempts == 1
        assert create_calls == 1

        with pytest.raises(DriveFolderCreationUncertainError):
            await ensure_drive_archive_folder_path(
                db,
                folder_path=["Archive"],
                find_folders=fake_find,
                create_folder=fake_create,
            )
        assert create_calls == 1

        reveal = True
        folder_id = await ensure_drive_archive_folder_path(
            db,
            folder_path=["Archive"],
            find_folders=fake_find,
            create_folder=fake_create,
        )
        assert folder_id == "folder-late"
        assert create_calls == 1
        await db.refresh(intent)
        assert intent.status == "verified"
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_folder_without_path_properties_is_bound_not_duplicated() -> None:
    engine, sessions = await _sessions()
    create_calls = 0

    async def fake_find(db, *, folder_name, parent_id, path_key):
        return [
            {
                "id": "legacy-folder",
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "createdTime": "2026-08-01T08:00:00Z",
                "appProperties": {},
            }
        ]

    async def fake_create(*args, **kwargs):
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("historical folder must be reused")

    async with sessions() as db:
        folder_id = await ensure_drive_archive_folder_path(
            db,
            folder_path=["Full-Time VA"],
            find_folders=fake_find,
            create_folder=fake_create,
        )
        assert folder_id == "legacy-folder"
        assert create_calls == 0
        intent = (await db.execute(select(DriveArchiveFolderIntent))).scalar_one()
        assert intent.status == "verified"
    await engine.dispose()


@pytest.mark.asyncio
async def test_matching_path_key_beats_legacy_same_name_candidate() -> None:
    engine, sessions = await _sessions()

    async def fake_find(db, *, folder_name, parent_id, path_key):
        return [
            _folder_row(
                folder_id="legacy-oldest",
                name=folder_name,
                created="2026-08-01T08:00:00Z",
            ),
            _folder_row(
                folder_id="path-key-match",
                name=folder_name,
                path_key=path_key,
                created="2026-09-01T08:00:00Z",
            ),
        ]

    async def fake_create(*args, **kwargs):
        raise AssertionError("provider create must not run when a path-key match exists")

    async with sessions() as db:
        folder_id = await ensure_drive_archive_folder_path(
            db,
            folder_path=["Archive"],
            find_folders=fake_find,
            create_folder=fake_create,
        )
        assert folder_id == "path-key-match"
    await engine.dispose()


@pytest.mark.asyncio
async def test_folder_uncertainty_occurs_before_file_dispatch_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    folder_ready = False
    upload_calls = 0

    async def fake_folder_path(*args, **kwargs):
        if not folder_ready:
            raise DriveFolderCreationUncertainError("folder response lost")
        return "folder-final"

    async def fake_find_files(db, *, app_properties, page_size=100):
        return []

    async def fake_upload(*args, **kwargs):
        nonlocal upload_calls
        upload_calls += 1
        assert kwargs["parent_id"] == "folder-final"
        assert kwargs["folder_path"] == []
        return {
            "id": "file-1",
            "name": kwargs["name"],
            "mimeType": kwargs["mime_type"],
            "size": len(kwargs["content"]),
            "webViewLink": "https://drive.example.test/file-1",
        }

    monkeypatch.setattr(
        "app.services.document_archive_recovery.ensure_drive_archive_folder_path",
        fake_folder_path,
    )

    async with sessions() as db:
        kwargs = {
            "checksum_sha256": "a" * 64,
            "account_scope": "personal",
            "source_type": "portal",
            "source_id": "portal:1",
            "name": "archive.txt",
            "mime_type": "text/plain",
            "content": b"archive",
            "folder_path": ["Archive", "Personal", "General", "2026"],
            "app_properties": {
                "va_managed": "true",
                "checksum_sha256": "a" * 64,
                "account_scope": "personal",
            },
            "upload_file": fake_upload,
            "find_files": fake_find_files,
        }
        with pytest.raises(DriveArchiveCreationUncertainError):
            await ensure_document_archive_upload(db, **kwargs)
        file_intent = (await db.execute(select(DocumentArchiveUploadIntent))).scalar_one()
        assert file_intent.status == "prepared"
        assert file_intent.attempts == 0
        assert upload_calls == 0

        folder_ready = True
        result = await ensure_document_archive_upload(db, **kwargs)
        assert result["id"] == "file-1"
        assert upload_calls == 1
        await db.refresh(file_intent)
        assert file_intent.status == "verified"
        assert file_intent.attempts == 1
    await engine.dispose()


def test_path_key_is_stable_and_hierarchy_sensitive() -> None:
    first = drive_archive_folder_path_key(["Archive", "Personal"])
    second = drive_archive_folder_path_key(["Archive", "Personal"])
    sibling = drive_archive_folder_path_key(["Archive", "Professional"])
    nested = drive_archive_folder_path_key(["Archive", "Personal", "General"])
    assert first == second
    assert first != sibling
    assert first != nested
