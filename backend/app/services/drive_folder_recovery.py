from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from googleapiclient.errors import HttpError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.google_api import GoogleConfigurationError
from app.models.entities import DriveArchiveFolderIntent


class DriveFolderCreationUncertainError(RuntimeError):
    """A Drive folder create may have succeeded; provider reconciliation must continue."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


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


def _provider_exception_types() -> tuple[type[BaseException], ...]:
    return (
        HttpError,
        httpx.HTTPError,
        GoogleConfigurationError,
        TimeoutError,
        OSError,
    )


def normalize_drive_archive_path(folder_path: list[str]) -> list[str]:
    normalized = [str(value or "").strip() for value in folder_path]
    normalized = [value for value in normalized if value]
    if not normalized:
        raise ValueError("Drive archive folder path cannot be empty")
    return normalized


def drive_archive_folder_path_key(parts: list[str]) -> str:
    normalized = normalize_drive_archive_path(parts)
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def prepare_drive_archive_folder_intent(
    db: AsyncSession,
    *,
    parts: list[str],
    parent_path_key: str,
) -> DriveArchiveFolderIntent:
    normalized = normalize_drive_archive_path(parts)
    path_key = drive_archive_folder_path_key(normalized)
    existing = (
        await db.execute(
            select(DriveArchiveFolderIntent)
            .where(DriveArchiveFolderIntent.path_key == path_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = DriveArchiveFolderIntent(
        path_key=path_key,
        parent_path_key=parent_path_key,
        folder_name=normalized[-1],
        logical_path_json=_dump(normalized),
        status="prepared",
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(DriveArchiveFolderIntent)
                .where(DriveArchiveFolderIntent.path_key == path_key)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    await db.refresh(row)
    return row


def _candidate_sort_key(
    candidate: dict[str, Any],
    *,
    path_key: str,
) -> tuple[int, str, str]:
    properties = candidate.get("appProperties")
    properties = properties if isinstance(properties, dict) else {}
    exact_path_key = str(properties.get("va_folder_path_key") or "") == path_key
    return (
        0 if exact_path_key else 1,
        str(candidate.get("createdTime") or "9999-12-31T23:59:59Z"),
        str(candidate.get("id") or ""),
    )


async def _bind_folder(
    db: AsyncSession,
    intent: DriveArchiveFolderIntent,
    candidate: dict[str, Any],
    *,
    match_count: int,
) -> str:
    folder_id = str(candidate.get("id") or "").strip()
    if not folder_id:
        raise DriveFolderCreationUncertainError(
            "Drive folder recovery candidate is missing an immutable folder id"
        )
    intent.drive_folder_id = folder_id[:255]
    intent.observed_folder_json = _dump(candidate)
    intent.status = "verified"
    intent.verified_at = intent.verified_at or utcnow()
    intent.last_error = (
        ""
        if match_count == 1
        else (
            f"Observed {match_count} matching Drive folders from historical state; "
            "bound the preferred existing folder without creating another."
        )
    )
    await db.commit()
    return folder_id


async def reconcile_drive_archive_folder(
    db: AsyncSession,
    *,
    intent: DriveArchiveFolderIntent,
    parent_id: str | None,
    find_folders: Any,
) -> str | None:
    if intent.status == "verified" and intent.drive_folder_id:
        return str(intent.drive_folder_id)

    candidates = await find_folders(
        db,
        folder_name=intent.folder_name,
        parent_id=parent_id,
        path_key=intent.path_key,
    )
    usable = [
        dict(candidate)
        for candidate in candidates or []
        if isinstance(candidate, dict) and str(candidate.get("id") or "").strip()
    ]
    if not usable:
        return None
    usable.sort(
        key=lambda candidate: _candidate_sort_key(
            candidate,
            path_key=intent.path_key,
        )
    )
    return await _bind_folder(db, intent, usable[0], match_count=len(usable))


async def _claim_drive_archive_folder(
    db: AsyncSession,
    *,
    intent: DriveArchiveFolderIntent,
    parent_id: str | None,
) -> bool:
    now = utcnow()
    result = await db.execute(
        update(DriveArchiveFolderIntent)
        .where(
            DriveArchiveFolderIntent.id == intent.id,
            DriveArchiveFolderIntent.status == "prepared",
        )
        .values(
            status="submitting",
            attempts=DriveArchiveFolderIntent.attempts + 1,
            parent_drive_folder_id=parent_id,
            last_error="",
            updated_at=now,
        )
    )
    await db.commit()
    await db.refresh(intent)
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def ensure_drive_archive_folder(
    db: AsyncSession,
    *,
    parts: list[str],
    parent_id: str | None,
    parent_path_key: str,
    find_folders: Any,
    create_folder: Any,
) -> str:
    intent = await prepare_drive_archive_folder_intent(
        db,
        parts=parts,
        parent_path_key=parent_path_key,
    )
    provider_errors = _provider_exception_types()
    try:
        recovered = await reconcile_drive_archive_folder(
            db,
            intent=intent,
            parent_id=parent_id,
            find_folders=find_folders,
        )
    except provider_errors as exc:
        if intent.status in {"submitting", "creation_uncertain"}:
            raise DriveFolderCreationUncertainError(
                "Drive folder outcome remains uncertain while provider evidence is unavailable"
            ) from exc
        raise
    if recovered is not None:
        return recovered

    if intent.status in {"submitting", "creation_uncertain"}:
        raise DriveFolderCreationUncertainError(
            "Drive folder creation is reconciliation-only after an ambiguous provider outcome"
        )
    if intent.status != "prepared":
        raise RuntimeError(f"unsupported Drive folder intent state: {intent.status}")

    claimed = await _claim_drive_archive_folder(
        db,
        intent=intent,
        parent_id=parent_id,
    )
    if not claimed:
        try:
            recovered = await reconcile_drive_archive_folder(
                db,
                intent=intent,
                parent_id=parent_id,
                find_folders=find_folders,
            )
        except provider_errors as exc:
            raise DriveFolderCreationUncertainError(
                "Another worker owns Drive folder dispatch; reconciliation is pending"
            ) from exc
        if recovered is not None:
            return recovered
        raise DriveFolderCreationUncertainError(
            "Another worker owns Drive folder dispatch; duplicate folder creation is suppressed"
        )

    try:
        created = await create_folder(
            db,
            folder_name=intent.folder_name,
            parent_id=parent_id,
            path_key=intent.path_key,
            parent_path_key=parent_path_key,
        )
    except provider_errors as exc:
        if _definitive_no_create(exc):
            intent.status = "prepared"
            intent.last_error = _safe_error(exc)
            await db.commit()
            raise
        intent.status = "creation_uncertain"
        intent.last_error = (
            "Drive folder provider outcome is uncertain; automatic folder replay is disabled: "
            + _safe_error(exc)
        )[:4000]
        await db.commit()
        try:
            recovered = await reconcile_drive_archive_folder(
                db,
                intent=intent,
                parent_id=parent_id,
                find_folders=find_folders,
            )
        except provider_errors:
            recovered = None
        if recovered is not None:
            return recovered
        raise DriveFolderCreationUncertainError(intent.last_error) from exc

    if not isinstance(created, dict) or not str(created.get("id") or "").strip():
        intent.status = "creation_uncertain"
        intent.last_error = (
            "Drive accepted the folder request without an immutable folder id; "
            "automatic folder replay is disabled."
        )
        await db.commit()
        try:
            recovered = await reconcile_drive_archive_folder(
                db,
                intent=intent,
                parent_id=parent_id,
                find_folders=find_folders,
            )
        except provider_errors:
            recovered = None
        if recovered is not None:
            return recovered
        raise DriveFolderCreationUncertainError(intent.last_error)

    return await _bind_folder(db, intent, dict(created), match_count=1)


async def ensure_drive_archive_folder_path(
    db: AsyncSession,
    *,
    folder_path: list[str],
    find_folders: Any,
    create_folder: Any,
) -> str:
    normalized = normalize_drive_archive_path(folder_path)
    parent_id: str | None = None
    parent_path_key = ""
    for index in range(len(normalized)):
        parts = normalized[: index + 1]
        parent_id = await ensure_drive_archive_folder(
            db,
            parts=parts,
            parent_id=parent_id,
            parent_path_key=parent_path_key,
            find_folders=find_folders,
            create_folder=create_folder,
        )
        parent_path_key = drive_archive_folder_path_key(parts)
    if not parent_id:
        raise DriveFolderCreationUncertainError(
            "Drive archive path did not resolve to a final folder id"
        )
    return parent_id
