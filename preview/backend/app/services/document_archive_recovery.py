from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from googleapiclient.errors import HttpError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.google_api import GoogleConfigurationError
from app.models.entities import DocumentArchiveUploadIntent


class DriveArchiveCreationUncertainError(RuntimeError):
    """A Drive create may have succeeded; only provider reconciliation may continue."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return default


def _safe_error(exc: Exception) -> str:
    return str(exc)[:4000]


def _http_status(exc: Exception) -> int:
    if not isinstance(exc, HttpError):
        return 0
    try:
        return int(getattr(exc.resp, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _definitive_no_create(exc: Exception) -> bool:
    if isinstance(exc, GoogleConfigurationError):
        return True
    status = _http_status(exc)
    return 400 <= status < 500 and status not in {408, 409, 425, 429}


async def prepare_document_archive_upload_intent(
    db: AsyncSession,
    *,
    checksum_sha256: str,
    account_scope: str,
    source_type: str,
    source_id: str,
    name: str,
    mime_type: str,
    folder_path: list[str],
    app_properties: dict[str, str],
) -> DocumentArchiveUploadIntent:
    existing = (
        await db.execute(
            select(DocumentArchiveUploadIntent)
            .where(
                DocumentArchiveUploadIntent.checksum_sha256 == checksum_sha256,
                DocumentArchiveUploadIntent.account_scope == account_scope,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = DocumentArchiveUploadIntent(
        checksum_sha256=checksum_sha256,
        account_scope=account_scope,
        source_type=source_type[:40],
        source_id=source_id[:255],
        name=name,
        mime_type=mime_type[:160],
        folder_path_json=_dump(folder_path),
        app_properties_json=_dump(app_properties),
        status="prepared",
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(DocumentArchiveUploadIntent)
                .where(
                    DocumentArchiveUploadIntent.checksum_sha256 == checksum_sha256,
                    DocumentArchiveUploadIntent.account_scope == account_scope,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    await db.refresh(row)
    return row


def _recovery_properties(intent: DocumentArchiveUploadIntent) -> dict[str, str]:
    return {
        "va_managed": "true",
        "checksum_sha256": intent.checksum_sha256,
        "account_scope": intent.account_scope,
    }


def _provider_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("createdTime") or "9999-12-31T23:59:59Z"),
        str(row.get("id") or ""),
    )


async def _bind_provider_file(
    db: AsyncSession,
    intent: DocumentArchiveUploadIntent,
    candidate: dict[str, Any],
    *,
    match_count: int,
) -> dict[str, Any]:
    file_id = str(candidate.get("id") or "").strip()
    if not file_id:
        raise DriveArchiveCreationUncertainError("Drive recovery candidate is missing an immutable file id")
    intent.drive_file_id = file_id[:255]
    intent.observed_file_json = _dump(candidate)
    intent.status = "verified"
    intent.verified_at = intent.verified_at or utcnow()
    intent.last_error = (
        ""
        if match_count == 1
        else (
            f"Observed {match_count} exact-byte Drive archive files from historical state; "
            "bound the oldest file without creating another."
        )
    )
    await db.commit()
    return candidate


async def reconcile_document_archive_upload(
    db: AsyncSession,
    intent: DocumentArchiveUploadIntent,
    *,
    find_files: Any,
) -> dict[str, Any] | None:
    if intent.status == "verified":
        observed = _loads(intent.observed_file_json, {})
        if isinstance(observed, dict) and str(observed.get("id") or "").strip():
            return observed

    candidates = await find_files(
        db,
        app_properties=_recovery_properties(intent),
        page_size=100,
    )
    usable = [
        dict(row)
        for row in candidates or []
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    ]
    if not usable:
        return None
    usable.sort(key=_provider_sort_key)
    return await _bind_provider_file(db, intent, usable[0], match_count=len(usable))


async def _claim_fresh_upload(
    db: AsyncSession,
    intent: DocumentArchiveUploadIntent,
) -> bool:
    result = await db.execute(
        update(DocumentArchiveUploadIntent)
        .where(
            DocumentArchiveUploadIntent.id == intent.id,
            DocumentArchiveUploadIntent.status == "prepared",
        )
        .values(
            status="submitting",
            attempts=DocumentArchiveUploadIntent.attempts + 1,
            last_error="",
            updated_at=utcnow(),
        )
    )
    await db.commit()
    await db.refresh(intent)
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def ensure_document_archive_upload(
    db: AsyncSession,
    *,
    checksum_sha256: str,
    account_scope: str,
    source_type: str,
    source_id: str,
    name: str,
    mime_type: str,
    content: bytes,
    folder_path: list[str],
    app_properties: dict[str, str],
    upload_file: Any,
    find_files: Any,
) -> dict[str, Any]:
    """Resolve or perform one exact-byte Drive archive upload.

    A fresh intent must first pass a read-only Drive reconciliation. After the
    atomic prepared -> submitting transition, every ambiguous state is
    reconciliation-only. No elapsed time or retry counter authorizes another
    provider create.
    """

    intent = await prepare_document_archive_upload_intent(
        db,
        checksum_sha256=checksum_sha256,
        account_scope=account_scope,
        source_type=source_type,
        source_id=source_id,
        name=name,
        mime_type=mime_type,
        folder_path=folder_path,
        app_properties=app_properties,
    )

    try:
        recovered = await reconcile_document_archive_upload(
            db,
            intent,
            find_files=find_files,
        )
    except Exception as exc:
        if intent.status in {"submitting", "creation_uncertain"}:
            raise DriveArchiveCreationUncertainError(
                "Drive archive outcome remains uncertain while provider evidence is unavailable"
            ) from exc
        raise
    if recovered is not None:
        return recovered

    if intent.status in {"submitting", "creation_uncertain"}:
        raise DriveArchiveCreationUncertainError(
            "Drive archive upload is reconciliation-only after an ambiguous provider outcome"
        )
    if intent.status != "prepared":
        raise RuntimeError(f"unsupported Drive archive intent state: {intent.status}")

    claimed = await _claim_fresh_upload(db, intent)
    if not claimed:
        try:
            recovered = await reconcile_document_archive_upload(
                db,
                intent,
                find_files=find_files,
            )
        except Exception as exc:
            raise DriveArchiveCreationUncertainError(
                "Another worker owns Drive archive dispatch; provider reconciliation is pending"
            ) from exc
        if recovered is not None:
            return recovered
        raise DriveArchiveCreationUncertainError(
            "Another worker owns Drive archive dispatch; duplicate upload is suppressed"
        )

    try:
        uploaded = await upload_file(
            db,
            name=intent.name,
            mime_type=intent.mime_type,
            content=content,
            folder_path=list(_loads(intent.folder_path_json, folder_path)),
            app_properties=dict(_loads(intent.app_properties_json, app_properties)),
        )
    except Exception as exc:
        if _definitive_no_create(exc):
            intent.status = "prepared"
            intent.last_error = _safe_error(exc)
            await db.commit()
            raise

        intent.status = "creation_uncertain"
        intent.last_error = (
            "Drive archive provider outcome is uncertain; automatic upload replay is disabled: "
            + _safe_error(exc)
        )[:4000]
        await db.commit()
        try:
            recovered = await reconcile_document_archive_upload(
                db,
                intent,
                find_files=find_files,
            )
        except (
            HttpError,
            httpx.HTTPError,
            GoogleConfigurationError,
            TimeoutError,
            OSError,
        ):
            recovered = None
        if recovered is not None:
            return recovered
        raise DriveArchiveCreationUncertainError(intent.last_error) from exc

    if not isinstance(uploaded, dict) or not str(uploaded.get("id") or "").strip():
        intent.status = "creation_uncertain"
        intent.last_error = (
            "Drive accepted the archive request without an immutable file id; "
            "automatic upload replay is disabled."
        )
        await db.commit()
        try:
            recovered = await reconcile_document_archive_upload(
                db,
                intent,
                find_files=find_files,
            )
        except (
            HttpError,
            httpx.HTTPError,
            GoogleConfigurationError,
            TimeoutError,
            OSError,
        ):
            recovered = None
        if recovered is not None:
            return recovered
        raise DriveArchiveCreationUncertainError(intent.last_error)

    return await _bind_provider_file(db, intent, uploaded, match_count=1)
