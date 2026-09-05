import base64
import os

os.environ.setdefault("PUBLIC_BASE_URL", "https://va.example.test")
os.environ.setdefault("PAIRING_SECRET", "x" * 32)
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"1" * 32).decode(),
)

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    DocumentArchiveUploadIntent,
    DocumentRecord,
    DocumentSourceReference,
)
from app.services.document_archive_recovery import DriveArchiveCreationUncertainError
from app.services.document_ingestion import ingest_document_bytes


async def _sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _fake_analyze(db, record):
    return {"document_id": record.id, "status": "analyzed"}


def _content() -> bytes:
    return ("Durable provider-backed archive record. " * 50).encode()


def _provider_file(kwargs, *, file_id: str = "drive-1", created: str = "2026-09-05T10:00:00Z"):
    return {
        "id": file_id,
        "name": kwargs["name"],
        "mimeType": kwargs["mime_type"],
        "size": len(kwargs["content"]),
        "webViewLink": f"https://drive.example.test/{file_id}",
        "createdTime": created,
        "appProperties": dict(kwargs["app_properties"]),
    }


@pytest.mark.asyncio
async def test_lost_drive_response_recovers_same_file_without_second_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    provider_files: list[dict] = []
    upload_calls = 0
    observed_queries: list[dict[str, str]] = []

    async def fake_find(db, *, app_properties, page_size=100):
        observed_queries.append(dict(app_properties))
        return list(provider_files)

    async def fake_upload(*args, **kwargs):
        nonlocal upload_calls
        upload_calls += 1
        provider_files.append(_provider_file(kwargs))
        raise TimeoutError("Drive response lost after provider acceptance")

    monkeypatch.setattr("app.services.document_ingestion.find_drive_files_by_app_properties", fake_find)
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)

    async with sessions() as db:
        result = await ingest_document_bytes(
            db,
            content=_content(),
            filename="archive.txt",
            mime_type="text/plain",
            source_type="portal",
            source_id="portal:doc-1",
            extracted_text=_content().decode(),
            financial_ownership=False,
        )
        assert result.created is True
        assert result.document.drive_file_id == "drive-1"
        assert upload_calls == 1
        assert observed_queries
        query = observed_queries[-1]
        assert query["va_managed"] == "true"
        assert query["checksum_sha256"] == result.document.checksum_sha256
        assert query["account_scope"] == "personal"
        intent = (await db.execute(select(DocumentArchiveUploadIntent))).scalar_one()
        assert intent.status == "verified"
        assert intent.drive_file_id == "drive-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_unresolved_drive_ambiguity_never_replays_provider_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    upload_calls = 0

    async def fake_find(db, *, app_properties, page_size=100):
        return []

    async def fake_upload(*args, **kwargs):
        nonlocal upload_calls
        upload_calls += 1
        raise TimeoutError("unknown Drive create outcome")

    monkeypatch.setattr("app.services.document_ingestion.find_drive_files_by_app_properties", fake_find)
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)

    async with sessions() as db:
        for _ in range(2):
            with pytest.raises(DriveArchiveCreationUncertainError):
                await ingest_document_bytes(
                    db,
                    content=_content(),
                    filename="archive.txt",
                    mime_type="text/plain",
                    source_type="portal",
                    source_id="portal:doc-2",
                    extracted_text=_content().decode(),
                    financial_ownership=False,
                )
        assert upload_calls == 1
        intent = (await db.execute(select(DocumentArchiveUploadIntent))).scalar_one()
        assert intent.status == "creation_uncertain"
        assert intent.attempts == 1
        assert (await db.execute(select(func.count(DocumentRecord.id)))).scalar_one() == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_orphan_drive_file_is_bound_without_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    upload_calls = 0

    async def fake_find(db, *, app_properties, page_size=100):
        return [
            {
                "id": "drive-orphan",
                "name": "orphan.txt",
                "mimeType": "text/plain",
                "size": len(_content()),
                "webViewLink": "https://drive.example.test/drive-orphan",
                "createdTime": "2026-09-01T08:00:00Z",
                "appProperties": dict(app_properties),
            }
        ]

    async def fake_upload(*args, **kwargs):
        nonlocal upload_calls
        upload_calls += 1
        raise AssertionError("provider create must not run when exact evidence already exists")

    monkeypatch.setattr("app.services.document_ingestion.find_drive_files_by_app_properties", fake_find)
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)

    async with sessions() as db:
        result = await ingest_document_bytes(
            db,
            content=_content(),
            filename="orphan.txt",
            mime_type="text/plain",
            source_type="email",
            source_id="gmail-orphan",
            extracted_text=_content().decode(),
            financial_ownership=False,
        )
        assert result.document.drive_file_id == "drive-orphan"
        assert upload_calls == 0
        intent = (await db.execute(select(DocumentArchiveUploadIntent))).scalar_one()
        assert intent.status == "verified"
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_multiple_exact_files_bind_oldest_without_more_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    upload_calls = 0

    async def fake_find(db, *, app_properties, page_size=100):
        common = {
            "name": "same.txt",
            "mimeType": "text/plain",
            "size": len(_content()),
            "appProperties": dict(app_properties),
        }
        return [
            {
                **common,
                "id": "drive-newer",
                "createdTime": "2026-09-04T10:00:00Z",
            },
            {
                **common,
                "id": "drive-older",
                "createdTime": "2026-09-03T10:00:00Z",
            },
        ]

    async def fake_upload(*args, **kwargs):
        nonlocal upload_calls
        upload_calls += 1
        raise AssertionError("historical duplicate evidence must never trigger another upload")

    monkeypatch.setattr("app.services.document_ingestion.find_drive_files_by_app_properties", fake_find)
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)

    async with sessions() as db:
        result = await ingest_document_bytes(
            db,
            content=_content(),
            filename="same.txt",
            mime_type="text/plain",
            source_type="portal",
            source_id="portal:historical",
            extracted_text=_content().decode(),
            financial_ownership=False,
        )
        assert result.document.drive_file_id == "drive-older"
        assert upload_calls == 0
        intent = (await db.execute(select(DocumentArchiveUploadIntent))).scalar_one()
        assert "2 exact-byte" in intent.last_error
    await engine.dispose()


@pytest.mark.asyncio
async def test_exact_bytes_still_share_one_document_and_keep_both_provenance_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    provider_files: list[dict] = []
    uploads = 0

    async def fake_find(db, *, app_properties, page_size=100):
        return list(provider_files)

    async def fake_upload(*args, **kwargs):
        nonlocal uploads
        uploads += 1
        row = _provider_file(kwargs)
        provider_files.append(row)
        return row

    monkeypatch.setattr("app.services.document_ingestion.find_drive_files_by_app_properties", fake_find)
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", _fake_analyze)

    async with sessions() as db:
        first = await ingest_document_bytes(
            db,
            content=_content(),
            filename="first.txt",
            mime_type="text/plain",
            source_type="email",
            source_id="gmail-1",
            extracted_text=_content().decode(),
            financial_ownership=False,
        )
        second = await ingest_document_bytes(
            db,
            content=_content(),
            filename="second.txt",
            mime_type="text/plain",
            source_type="portal",
            source_id="portal:1",
            extracted_text=_content().decode(),
            financial_ownership=False,
        )
        assert first.created is True
        assert second.created is False
        assert first.document.id == second.document.id
        assert uploads == 1
        assert (await db.execute(select(func.count(DocumentRecord.id)))).scalar_one() == 1
        assert (await db.execute(select(func.count(DocumentSourceReference.id)))).scalar_one() == 2
    await engine.dispose()
