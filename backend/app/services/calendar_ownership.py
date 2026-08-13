from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.errors import HttpError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.integrations.google_api import (
    insert_calendar_event,
    delete_calendar_event,
    get_calendar_event,
    list_calendar_events_window,
    query_calendar_freebusy,
    update_calendar_event,
)
from app.models.entities import (
    CalendarEventMirror,
    CalendarMutation,
    CalendarSyncState,
    VAObjectiveStep,
)
from app.services.audit import write_audit

settings = get_settings()


class CalendarConflictError(RuntimeError):
    def __init__(self, message: str, conflicts: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.conflicts = conflicts or []


def utcnow() -> datetime:
    return datetime.utcnow()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return default


def _http_status(exc: Exception) -> int:
    if not isinstance(exc, HttpError):
        return 0
    try:
        return int(getattr(exc.resp, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def deterministic_calendar_event_id(idempotency_key: str) -> str:
    """Google Calendar event IDs accept lower-case hex, so use a stable content hash.

    Supplying the ID on insert makes create retries duplicate-safe: if the first POST
    succeeded but the response was lost, a later insert hits the same provider event
    rather than creating a second meeting.
    """

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"va{digest[:48]}"


def _parse_provider_time(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _event_time(item: dict[str, Any], field: str) -> tuple[str, str]:
    block = item.get(field) or {}
    if not isinstance(block, dict):
        return "", settings.default_timezone
    raw = str(block.get("dateTime") or block.get("date") or "")
    zone = str(block.get("timeZone") or settings.default_timezone)
    return raw, zone


def _normalize_attendees(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in value or []:
        if isinstance(item, str):
            email = item.strip().lower()
            if email:
                rows.append({"email": email, "responseStatus": "needsAction"})
            continue
        if isinstance(item, dict):
            email = str(item.get("email") or "").strip().lower()
            if not email:
                continue
            rows.append(
                {
                    "email": email,
                    "displayName": str(item.get("displayName") or ""),
                    "responseStatus": str(item.get("responseStatus") or "needsAction"),
                    "optional": "true" if bool(item.get("optional")) else "false",
                }
            )
    return rows


def _desired_attendee_emails(event: dict[str, Any]) -> set[str]:
    return {
        str(item.get("email") if isinstance(item, dict) else item).strip().lower()
        for item in event.get("attendees") or []
        if str(item.get("email") if isinstance(item, dict) else item).strip()
    }


def _provider_attendee_emails(item: dict[str, Any]) -> set[str]:
    return {
        str(row.get("email") or "").strip().lower()
        for row in item.get("attendees") or []
        if isinstance(row, dict) and str(row.get("email") or "").strip()
    }


def _times_equal(provider_value: str, desired_value: str) -> bool:
    provider = _parse_provider_time(provider_value)
    desired = _parse_provider_time(desired_value)
    if provider is not None and desired is not None:
        return provider == desired
    return provider_value[:10] == desired_value[:10]


def calendar_event_matches(item: dict[str, Any], desired: dict[str, Any]) -> bool:
    if str(item.get("status") or "confirmed") == "cancelled":
        return False
    if str(desired.get("summary") or "").strip() and str(item.get("summary") or "").strip() != str(
        desired.get("summary") or ""
    ).strip():
        return False
    provider_start, _ = _event_time(item, "start")
    provider_end, _ = _event_time(item, "end")
    if str(desired.get("start") or "").strip() and not _times_equal(
        provider_start, str(desired.get("start") or "")
    ):
        return False
    if str(desired.get("end") or "").strip() and not _times_equal(
        provider_end, str(desired.get("end") or "")
    ):
        return False
    if "location" in desired and str(item.get("location") or "").strip() != str(desired.get("location") or "").strip():
        return False
    if "description" in desired and str(item.get("description") or "").strip() != str(desired.get("description") or "").strip():
        return False
    desired_attendees = _desired_attendee_emails(desired)
    if desired_attendees and not desired_attendees.issubset(_provider_attendee_emails(item)):
        return False
    return True


async def _sync_state(db: AsyncSession) -> CalendarSyncState:
    row = await db.get(CalendarSyncState, 1)
    if row is None:
        row = CalendarSyncState(id=1, calendar_id="primary")
        db.add(row)
        await db.flush()
    return row


async def _upsert_mirror(
    db: AsyncSession,
    item: dict[str, Any],
    *,
    owned_objective_id: int | None = None,
) -> CalendarEventMirror:
    event_id = str(item.get("id") or "").strip()
    if not event_id:
        raise ValueError("Calendar event is missing provider id")
    existing = (
        await db.execute(
            select(CalendarEventMirror).where(CalendarEventMirror.provider_event_id == event_id).limit(1)
        )
    ).scalar_one_or_none()
    previous_attendees = _loads(existing.attendees_json, []) if existing is not None else []
    start_raw, zone = _event_time(item, "start")
    end_raw, _ = _event_time(item, "end")
    organizer = item.get("organizer") if isinstance(item.get("organizer"), dict) else {}
    attendees = _normalize_attendees(item.get("attendees") or [])
    provider_updated = _parse_provider_time(str(item.get("updated") or ""))
    if existing is None:
        existing = CalendarEventMirror(provider_event_id=event_id)
        db.add(existing)
    existing.calendar_id = "primary"
    existing.ical_uid = str(item.get("iCalUID") or "")
    existing.status = str(item.get("status") or "confirmed")
    existing.summary = str(item.get("summary") or "Untitled event")
    existing.description = str(item.get("description") or "")
    existing.location = str(item.get("location") or "")
    existing.start_at = _parse_provider_time(start_raw)
    existing.end_at = _parse_provider_time(end_raw)
    existing.timezone = zone
    existing.start_raw = start_raw
    existing.end_raw = end_raw
    existing.attendees_json = _dump(attendees)
    existing.organizer_json = _dump(organizer)
    existing.html_link = str(item.get("htmlLink") or "")
    existing.etag = str(item.get("etag") or "")
    existing.provider_updated_at = provider_updated
    existing.last_seen_at = utcnow()
    if owned_objective_id:
        existing.owned_objective_id = owned_objective_id
    await db.flush()

    # If all invitees have moved out of needsAction for a VA-owned event, record a
    # durable response event exactly once. The objective engine will cancel any chase.
    if existing.owned_objective_id and attendees:
        statuses = {str(row.get("responseStatus") or "needsAction") for row in attendees}
        previous_statuses = {
            str(row.get("responseStatus") or "needsAction")
            for row in previous_attendees
            if isinstance(row, dict)
        }
        all_answered = statuses and "needsAction" not in statuses
        became_answered = all_answered and (not previous_statuses or "needsAction" in previous_statuses)
        if became_answered:
            event_key = f"calendar:{event_id}:attendee-response:{existing.etag or item.get('updated') or 'state'}"[:255]
            from app.services.autonomous_core import record_event

            await record_event(
                db,
                event_key=event_key,
                source_type="calendar",
                source_id=event_id,
                event_type="calendar_attendee_response_received",
                title=f"Calendar responses received: {existing.summary}",
                payload={
                    "prior_objective_id": existing.owned_objective_id,
                    "provider_event_id": event_id,
                    "attendees": attendees,
                },
                occurred_at=provider_updated or utcnow(),
            )
    return existing


async def sync_calendar(
    db: AsyncSession,
    *,
    days_back: int = 30,
    days_forward: int = 365,
) -> dict[str, Any]:
    state = await _sync_state(db)
    state.last_error = ""
    state.last_sync_attempt_at = utcnow()
    await db.commit()
    try:
        items = await list_calendar_events_window(
            db,
            days_back=max(1, min(days_back, 365)),
            days_forward=max(1, min(days_forward, 730)),
            show_deleted=True,
            max_results=2500,
        )
        owned_map = {
            str(row.provider_event_id): int(row.objective_id or 0)
            for row in (
                await db.execute(
                    select(CalendarMutation).where(
                        CalendarMutation.provider_event_id != "",
                        CalendarMutation.objective_id.is_not(None),
                    )
                )
            ).scalars()
        }
        changed = 0
        for item in items:
            if not str(item.get("id") or ""):
                continue
            await _upsert_mirror(
                db,
                item,
                owned_objective_id=owned_map.get(str(item.get("id") or "")) or None,
            )
            changed += 1
        state.last_sync_at = utcnow()
        state.last_full_sync_at = state.last_sync_at
        state.last_event_count = changed
        await write_audit(
            db,
            "calendar_sync_completed",
            entity_type="calendar",
            entity_id="primary",
            details={"events": changed, "days_back": days_back, "days_forward": days_forward},
        )
        await db.commit()
        return {"events": changed, "last_sync_at": state.last_sync_at.isoformat() + "Z"}
    except Exception as exc:
        state.last_error = str(exc)[:4000]
        state.last_sync_attempt_at = utcnow()
        await db.commit()
        raise


async def find_calendar_conflicts(db: AsyncSession, *, start: str, end: str) -> list[dict[str, str]]:
    result = await query_calendar_freebusy(db, start=start, end=end)
    calendars = result.get("calendars") if isinstance(result, dict) else {}
    primary = calendars.get("primary") if isinstance(calendars, dict) else {}
    busy = primary.get("busy") if isinstance(primary, dict) else []
    return [
        {"start": str(row.get("start") or ""), "end": str(row.get("end") or "")}
        for row in busy or []
        if isinstance(row, dict)
    ]


async def prepare_calendar_mutation(
    db: AsyncSession,
    *,
    idempotency_key: str,
    operation: str,
    desired_event: dict[str, Any],
    objective_id: int | None = None,
    step_id: int | None = None,
    provider_event_id: str = "",
) -> CalendarMutation:
    existing = (
        await db.execute(
            select(CalendarMutation).where(CalendarMutation.idempotency_key == idempotency_key).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    operation = operation.strip().lower()
    if operation not in {"create", "update", "cancel"}:
        raise ValueError(f"Unsupported calendar operation: {operation}")
    if operation == "create":
        provider_event_id = provider_event_id or deterministic_calendar_event_id(idempotency_key)
    row = CalendarMutation(
        idempotency_key=idempotency_key[:255],
        objective_id=objective_id,
        step_id=step_id,
        operation=operation,
        calendar_id="primary",
        provider_event_id=provider_event_id,
        desired_event_json=_dump(desired_event),
        status="pending",
        verify_after=utcnow(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def reconcile_calendar_mutation(db: AsyncSession, row: CalendarMutation) -> bool:
    desired = _loads(row.desired_event_json, {})
    desired = desired if isinstance(desired, dict) else {}
    if not row.provider_event_id:
        return False
    try:
        item = await get_calendar_event(db, row.provider_event_id)
    except HttpError as exc:
        status = _http_status(exc)
        if status in {404, 410} and row.operation == "cancel":
            row.status = "verified"
            row.verified_at = utcnow()
            row.last_error = ""
            await db.commit()
            return True
        if status in {404, 410}:
            return False
        raise
    if row.operation == "cancel":
        verified = str(item.get("status") or "") == "cancelled"
    else:
        verified = calendar_event_matches(item, desired)
    if not verified:
        row.observed_event_json = _dump(item)
        row.etag = str(item.get("etag") or row.etag or "")
        await db.commit()
        return False
    row.status = "verified"
    row.observed_event_json = _dump(item)
    row.etag = str(item.get("etag") or "")
    row.verified_at = utcnow()
    row.last_error = ""
    await _upsert_mirror(db, item, owned_objective_id=row.objective_id)
    await db.commit()
    return True


async def send_or_reconcile_calendar_mutation(db: AsyncSession, row: CalendarMutation) -> CalendarMutation:
    if row.status == "verified":
        return row
    try:
        if await reconcile_calendar_mutation(db, row):
            return row
    except HttpError as exc:
        status = _http_status(exc)
        if status in {401, 403}:
            row.status = "failed_user"
            row.last_error = f"Google Calendar authorization failed (HTTP {status})"
            await db.commit()
            return row
        raise

    desired = _loads(row.desired_event_json, {})
    desired = desired if isinstance(desired, dict) else {}
    if row.operation in {"create", "update"}:
        if not str(desired.get("summary") or "").strip():
            row.status = "failed"
            row.last_error = "Calendar summary is required"
            await db.commit()
            return row
        if not str(desired.get("start") or "").strip() or not str(desired.get("end") or "").strip():
            row.status = "failed"
            row.last_error = "Calendar start and end are required"
            await db.commit()
            return row
        start_at = _parse_provider_time(str(desired.get("start") or ""))
        end_at = _parse_provider_time(str(desired.get("end") or ""))
        if start_at is not None and end_at is not None and end_at <= start_at:
            row.status = "failed"
            row.last_error = "Calendar end must be after start"
            await db.commit()
            return row

    if row.operation == "create" and int(row.attempts or 0) == 0 and bool(desired.get("avoid_conflicts", True)):
        conflicts = await find_calendar_conflicts(
            db,
            start=str(desired.get("start") or ""),
            end=str(desired.get("end") or ""),
        )
        if conflicts:
            row.status = "needs_user_conflict"
            row.last_error = "Requested calendar time overlaps an existing busy period"
            row.observed_event_json = _dump({"conflicts": conflicts})
            await db.commit()
            return row

    row.attempts = int(row.attempts or 0) + 1
    row.status = "submitting"
    row.last_error = ""
    await db.commit()
    try:
        if row.operation == "create":
            result = await insert_calendar_event(
                db,
                desired,
                event_id=row.provider_event_id,
                send_updates=bool(desired.get("send_updates", True)),
                idempotency_key=row.idempotency_key,
            )
            row.provider_event_id = str(result.get("id") or row.provider_event_id)
            row.etag = str(result.get("etag") or "")
            row.observed_event_json = _dump(result)
        elif row.operation == "update":
            if not row.provider_event_id:
                raise ValueError("Calendar update requires provider_event_id")
            result = await update_calendar_event(
                db,
                row.provider_event_id,
                desired,
                etag=row.etag,
                send_updates=bool(desired.get("send_updates", True)),
                idempotency_key=row.idempotency_key,
            )
            row.etag = str(result.get("etag") or row.etag or "")
            row.observed_event_json = _dump(result)
        else:
            if not row.provider_event_id:
                raise ValueError("Calendar cancellation requires provider_event_id")
            await delete_calendar_event(
                db,
                row.provider_event_id,
                etag=row.etag,
                send_updates=bool(desired.get("send_updates", True)),
            )
        row.status = "submitted"
        row.verify_after = utcnow() + timedelta(seconds=2)
        await db.commit()
    except HttpError as exc:
        status = _http_status(exc)
        if status in {401, 403}:
            row.status = "failed_user"
            row.last_error = f"Google Calendar authorization failed (HTTP {status})"
        elif status == 409 and row.operation == "create":
            # Deterministic provider ID means conflict is normally proof the first
            # insert already owns this ID. Reconciliation decides whether it matches.
            row.status = "submitted"
            row.verify_after = utcnow()
            row.last_error = ""
        elif status in {429, 500, 502, 503, 504}:
            # Never switch to a random event ID. The same deterministic ID is reused,
            # so a retry cannot create a duplicate calendar entry.
            row.status = "creation_uncertain" if row.operation == "create" else "submitted"
            row.last_error = f"Google Calendar provider outcome requires reconciliation (HTTP {status})"
            row.verify_after = utcnow() + timedelta(seconds=15)
        elif status in {404, 410} and row.operation == "cancel":
            row.status = "verified"
            row.verified_at = utcnow()
            row.last_error = ""
        else:
            row.status = "failed"
            row.last_error = str(exc)[:4000]
        await db.commit()
    except (ValueError, KeyError) as exc:
        # Invalid local input/configuration is deterministic, not an ambiguous provider
        # outcome. Do not keep retrying a mutation that cannot become valid by waiting.
        row.status = "failed"
        row.last_error = str(exc)[:4000]
        await db.commit()
    except Exception as exc:
        row.status = "creation_uncertain" if row.operation == "create" else "submitted"
        row.last_error = str(exc)[:4000]
        row.verify_after = utcnow() + timedelta(seconds=15)
        await db.commit()
    return row


async def ensure_calendar_mutation_verified(db: AsyncSession, row: CalendarMutation) -> bool:
    if row.status == "verified":
        return True
    if row.status in {"failed", "failed_user", "needs_user_conflict"}:
        return False
    try:
        if await reconcile_calendar_mutation(db, row):
            return True
    except HttpError as exc:
        status = _http_status(exc)
        if status in {401, 403}:
            row.status = "failed_user"
            row.last_error = f"Google Calendar authorization failed (HTTP {status})"
            await db.commit()
            return False
        if status not in {404, 410, 429, 500, 502, 503, 504}:
            row.status = "failed"
            row.last_error = str(exc)[:4000]
            await db.commit()
            return False

    # Calendar mutations are idempotent against one provider event ID. A create
    # always reuses its deterministic ID, while update/cancel reapply the same
    # postcondition to the same event. Bounded resubmission therefore cannot create
    # duplicate meetings and recovers ambiguous provider responses.
    if row.status in {"creation_uncertain", "submitted"} and row.attempts < row.max_attempts:
        await send_or_reconcile_calendar_mutation(db, row)
        if row.status == "verified":
            return True
    row.verify_after = utcnow() + timedelta(seconds=min(120, 15 * max(1, row.attempts or 1)))
    await db.commit()
    return False


async def calendar_status(db: AsyncSession) -> dict[str, Any]:
    state = await _sync_state(db)
    upcoming = int(
        (
            await db.execute(
                select(func.count(CalendarEventMirror.id)).where(
                    CalendarEventMirror.status != "cancelled",
                    CalendarEventMirror.end_at.is_not(None),
                    CalendarEventMirror.end_at >= utcnow(),
                )
            )
        ).scalar_one()
    )
    mutations = {
        status: int(count)
        for status, count in (
            await db.execute(
                select(CalendarMutation.status, func.count(CalendarMutation.id)).group_by(CalendarMutation.status)
            )
        ).all()
    }
    awaiting_response = int(
        (
            await db.execute(
                select(func.count(VAObjectiveStep.id)).where(
                    VAObjectiveStep.verification_type == "calendar_attendee_response",
                    VAObjectiveStep.status.in_(["pending", "waiting"]),
                )
            )
        ).scalar_one()
    )
    return {
        "calendar_id": state.calendar_id,
        "last_sync_at": state.last_sync_at,
        "last_full_sync_at": state.last_full_sync_at,
        "last_sync_attempt_at": state.last_sync_attempt_at,
        "last_error": state.last_error,
        "last_event_count": state.last_event_count,
        "upcoming_events": upcoming,
        "awaiting_attendee_response": awaiting_response,
        "mutations": mutations,
    }


async def list_calendar_mirror(
    db: AsyncSession,
    *,
    days: int = 60,
    limit: int = 250,
) -> list[dict[str, Any]]:
    now = utcnow()
    cutoff = now + timedelta(days=max(1, min(days, 730)))
    rows = list(
        (
            await db.execute(
                select(CalendarEventMirror)
                .where(
                    CalendarEventMirror.status != "cancelled",
                    CalendarEventMirror.end_at.is_not(None),
                    CalendarEventMirror.end_at >= now - timedelta(days=1),
                    CalendarEventMirror.start_at <= cutoff,
                )
                .order_by(CalendarEventMirror.start_at.asc())
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
    )
    return [
        {
            "id": row.id,
            "provider_event_id": row.provider_event_id,
            "status": row.status,
            "summary": row.summary,
            "description": row.description,
            "location": row.location,
            "start": row.start_raw or (row.start_at.isoformat() if row.start_at else ""),
            "end": row.end_raw or (row.end_at.isoformat() if row.end_at else ""),
            "timezone": row.timezone,
            "attendees": _loads(row.attendees_json, []),
            "organizer": _loads(row.organizer_json, {}),
            "html_link": row.html_link,
            "owned_objective_id": row.owned_objective_id,
            "provider_updated_at": row.provider_updated_at,
            "last_seen_at": row.last_seen_at,
        }
        for row in rows
    ]
