from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text, new_token
from app.core.settings import get_settings
from app.models.entities import OAuthConnection, OAuthState
from app.services.runtime_config import get_runtime_value

settings = get_settings()
logger = logging.getLogger(__name__)
GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/contacts.readonly",
]


class GoogleConfigurationError(RuntimeError):
    pass


async def _google_credentials(db: AsyncSession) -> tuple[str, str]:
    client_id = await get_runtime_value(db, "google_client_id")
    client_secret = await get_runtime_value(db, "google_client_secret")
    if not client_id or not client_secret:
        raise GoogleConfigurationError("Google OAuth client ID and secret are not configured")
    return client_id, client_secret


async def ensure_google_configured(db: AsyncSession) -> None:
    await _google_credentials(db)


async def create_google_authorization(db: AsyncSession, redirect_uri: str) -> str:
    client_id, _ = await _google_credentials(db)
    state = new_token(24)
    db.add(
        OAuthState(
            state=state,
            provider="google",
            payload_json=json.dumps({"redirect_uri": redirect_uri}),
            expires_at=datetime.utcnow() + timedelta(minutes=15),
        )
    )
    await db.commit()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


async def complete_google_authorization(db: AsyncSession, code: str, state: str) -> OAuthConnection:
    client_id, client_secret = await _google_credentials(db)
    result = await db.execute(select(OAuthState).where(OAuthState.state == state, OAuthState.provider == "google"))
    saved_state = result.scalar_one_or_none()
    if saved_state is None or saved_state.expires_at < datetime.utcnow():
        raise ValueError("OAuth state is invalid or expired")
    redirect_uri = json.loads(saved_state.payload_json or "{}").get("redirect_uri")
    if not redirect_uri:
        raise ValueError("OAuth redirect URI is missing")

    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        user_response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user_response.raise_for_status()
        user_data = user_response.json()

    email = str(user_data["email"]).lower()
    existing_result = await db.execute(
        select(OAuthConnection).where(
            OAuthConnection.provider == "google", OAuthConnection.account_key == email
        )
    )
    connection = existing_result.scalar_one_or_none()
    expires_at = datetime.utcnow() + timedelta(seconds=int(token_data.get("expires_in", 3600)))
    if connection is None:
        connection = OAuthConnection(
            provider="google",
            account_key=email,
            display_name=user_data.get("name") or email,
            access_token_encrypted=encrypt_text(token_data["access_token"]),
            refresh_token_encrypted=encrypt_text(token_data["refresh_token"])
            if token_data.get("refresh_token")
            else None,
            expires_at=expires_at,
            scope=token_data.get("scope", ""),
            metadata_json=json.dumps(user_data),
        )
        db.add(connection)
    else:
        connection.display_name = user_data.get("name") or email
        connection.access_token_encrypted = encrypt_text(token_data["access_token"])
        if token_data.get("refresh_token"):
            connection.refresh_token_encrypted = encrypt_text(token_data["refresh_token"])
        connection.expires_at = expires_at
        connection.scope = token_data.get("scope", connection.scope)
        connection.metadata_json = json.dumps(user_data)
        connection.enabled = True
    await db.delete(saved_state)
    await db.commit()
    await db.refresh(connection)
    return connection


async def get_google_connection(db: AsyncSession) -> OAuthConnection:
    result = await db.execute(
        select(OAuthConnection).where(OAuthConnection.provider == "google", OAuthConnection.enabled.is_(True))
    )
    connection = result.scalars().first()
    if connection is None:
        raise GoogleConfigurationError("No Google account is connected")
    return connection


async def get_google_access_token(db: AsyncSession, connection: OAuthConnection | None = None) -> str:
    connection = connection or await get_google_connection(db)
    if connection.expires_at and connection.expires_at > datetime.utcnow() + timedelta(minutes=2):
        return decrypt_text(connection.access_token_encrypted)
    if not connection.refresh_token_encrypted:
        raise GoogleConfigurationError("Google refresh token is missing; reconnect the Google account")

    client_id, client_secret = await _google_credentials(db)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": decrypt_text(connection.refresh_token_encrypted),
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        payload = response.json()
    connection.access_token_encrypted = encrypt_text(payload["access_token"])
    connection.expires_at = datetime.utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600)))
    await db.commit()
    return payload["access_token"]


async def gmail_service(db: AsyncSession):
    client_id, client_secret = await _google_credentials(db)
    connection = await get_google_connection(db)
    access_token = await get_google_access_token(db, connection)
    credentials = Credentials(
        token=access_token,
        refresh_token=decrypt_text(connection.refresh_token_encrypted)
        if connection.refresh_token_encrypted
        else None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_SCOPES,
    )
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


async def calendar_service(db: AsyncSession):
    client_id, client_secret = await _google_credentials(db)
    connection = await get_google_connection(db)
    access_token = await get_google_access_token(db, connection)
    credentials = Credentials(
        token=access_token,
        refresh_token=decrypt_text(connection.refresh_token_encrypted)
        if connection.refresh_token_encrypted
        else None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_SCOPES,
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def decode_base64url(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def extract_gmail_body(payload: dict[str, Any]) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if body_data:
            decoded = decode_base64url(body_data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                plain_parts.append(decoded)
            elif mime == "text/html":
                html_parts.append(decoded)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(plain_parts), "\n".join(html_parts)


def headers_to_dict(payload: dict[str, Any]) -> dict[str, str]:
    return {item.get("name", "").lower(): item.get("value", "") for item in payload.get("headers", [])}


def _google_http_status(exc: HttpError) -> int:
    try:
        return int(getattr(exc.resp, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _google_http_reason(exc: HttpError) -> str:
    try:
        payload = json.loads(exc.content.decode("utf-8", errors="replace"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("reason") or "")
    return str(error.get("status") or "")


async def _execute_google_request(
    request_factory,
    *,
    attempts: int = 4,
    retry_statuses: set[int] | None = None,
):
    """Execute a synchronous Google API request without blocking the event loop.

    Gmail can return HTTP 409 for an aborted/concurrent mutation. 409, rate limits,
    and provider 5xx responses are retried at the smallest possible API-operation
    boundary so a harmless label/message race never restarts the entire mailbox sync.
    """

    statuses = retry_statuses if retry_statuses is not None else {409, 429, 500, 502, 503, 504}
    total_attempts = max(1, attempts)
    for attempt in range(total_attempts):
        try:
            return await asyncio.to_thread(lambda: request_factory().execute())
        except HttpError as exc:
            status = _google_http_status(exc)
            if status not in statuses or attempt + 1 >= total_attempts:
                raise
            await asyncio.sleep(min(2.0, 0.25 * (2**attempt)))


async def _list_gmail_labels(service) -> list[dict[str, Any]]:
    response = await _execute_google_request(
        lambda: service.users().labels().list(userId="me"),
        attempts=4,
    )
    return list(response.get("labels", []) or [])


def _gmail_label_key(value: str) -> str:
    """Return a stable comparison key for Gmail display names.

    Gmail may reject a create because an equivalent label already exists while
    labels.list() returns a display name with different Unicode composition,
    case, or surrounding whitespace. The immutable label ID is what matters for
    message mutations, so conflict reconciliation compares a conservative
    normalized representation while preserving the original display name.
    """

    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _find_gmail_label(labels: list[dict[str, Any]], requested_name: str) -> dict[str, Any] | None:
    exact = next(
        (label for label in labels if label.get("name") == requested_name and label.get("id")),
        None,
    )
    if exact is not None:
        return exact
    requested_key = _gmail_label_key(requested_name)
    if not requested_key:
        return None
    return next(
        (
            label
            for label in labels
            if label.get("id") and _gmail_label_key(str(label.get("name") or "")) == requested_key
        ),
        None,
    )


async def ensure_gmail_labels(
    db: AsyncSession,
    label_names: list[str],
    *,
    service=None,
) -> dict[str, str]:
    """Resolve/create Gmail labels without letting a cosmetic conflict abort sync.

    Gmail can answer label creation with HTTP 409 when another actor already owns
    the name while labels.list() has not exposed that label to this request yet.
    After one create attempt, reconcile by re-listing only; repeating the POST is
    unnecessary and can amplify the conflict. If no immutable label ID becomes
    visible after bounded reconciliation, skip that one cosmetic label and let the
    mailbox sync continue.
    """

    service = service or await gmail_service(db)
    requested = [name.strip() for name in dict.fromkeys(label_names) if name.strip()]
    if not requested:
        return {}

    labels = await _list_gmail_labels(service)
    resolved: dict[str, str] = {}
    for name in requested:
        winner = _find_gmail_label(labels, name)
        if winner is not None:
            resolved[name] = str(winner["id"])

    for normalized_name in requested:
        if normalized_name in resolved:
            continue

        try:
            created = await _execute_google_request(
                lambda normalized_name=normalized_name: service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": normalized_name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                ),
                attempts=1,
                retry_statuses=set(),
            )
            resolved[normalized_name] = str(created["id"])
            continue
        except HttpError as exc:
            if _google_http_status(exc) != 409:
                raise

        # Do not repeat the create POST. Gmail already told us that the name
        # exists/conflicts; wait for labels.list() to expose the winning ID.
        for attempt in range(6):
            labels = await _list_gmail_labels(service)
            winner = _find_gmail_label(labels, normalized_name)
            if winner is not None:
                resolved[normalized_name] = str(winner["id"])
                break
            if attempt + 1 < 6:
                await asyncio.sleep(min(3.0, 0.25 * (2**attempt)))

        if normalized_name not in resolved:
            logger.warning(
                "Skipping unresolved Gmail label conflict after reconciliation: %r",
                normalized_name,
            )

    return resolved


async def ensure_gmail_label(
    db: AsyncSession,
    label_name: str,
    *,
    service=None,
) -> str:
    normalized_name = label_name.strip()
    if not normalized_name:
        raise ValueError("Gmail label name cannot be empty")
    resolved = await ensure_gmail_labels(db, [normalized_name], service=service)
    if normalized_name not in resolved:
        raise RuntimeError(f"Gmail label conflict could not be resolved: {normalized_name}")
    return resolved[normalized_name]


async def modify_gmail_message(
    db: AsyncSession,
    message_id: str,
    *,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    service = await gmail_service(db)
    requested_labels = [name.strip() for name in dict.fromkeys(add_labels or []) if name.strip()]
    label_ids = await ensure_gmail_labels(db, requested_labels, service=service)
    # A persistent Gmail 409 for one cosmetic label must not abort processing of
    # the message or the entire durable gmail.sync job. Apply every label whose
    # immutable ID was resolved and continue with the business action.
    add_ids = [label_ids[name] for name in requested_labels if name in label_ids]

    requested_remove = [name.strip() for name in dict.fromkeys(remove_labels or []) if name.strip()]
    system_label_ids = {
        "CHAT",
        "SENT",
        "INBOX",
        "IMPORTANT",
        "TRASH",
        "DRAFT",
        "SPAM",
        "STARRED",
        "UNREAD",
    }
    remove_ids: list[str] = []
    custom_remove_names: list[str] = []
    for value in requested_remove:
        if value in system_label_ids or value.startswith("CATEGORY_") or value.startswith("Label_"):
            remove_ids.append(value)
        else:
            custom_remove_names.append(value)
    if custom_remove_names:
        existing_labels = await _list_gmail_labels(service)
        for name in custom_remove_names:
            winner = _find_gmail_label(existing_labels, name)
            if winner is not None:
                remove_ids.append(str(winner["id"]))

    if not add_ids and not remove_ids:
        return

    # Retry a conflicting message mutation in place. This is deliberately narrower
    # than retrying sync_gmail(), which may have already processed many messages.
    await _execute_google_request(
        lambda: service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
        ),
        attempts=4,
    )


async def send_gmail_message(
    db: AsyncSession,
    to: str,
    subject: str,
    body: str,
    reply_to_id: str | None = None,
    *,
    thread_id: str | None = None,
    message_id_header: str | None = None,
    references: str | None = None,
) -> str:
    """Send one Gmail message without blind transport-level retries.

    The higher-level delivery service persists an idempotency record and reconciles
    ambiguous outcomes by RFC Message-ID without automatically resubmitting that
    provider intent. Gmail's messages.send endpoint itself has no idempotency key,
    so retrying this request inside the HTTP boundary could create duplicate mail.
    """
    service = await gmail_service(db)
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if message_id_header:
        message["Message-ID"] = message_id_header
    if reply_to_id:
        message["In-Reply-To"] = reply_to_id
        message["References"] = references or reply_to_id
    elif references:
        message["References"] = references
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    request_body: dict[str, Any] = {"raw": raw}
    if thread_id:
        request_body["threadId"] = thread_id
    result = await asyncio.to_thread(
        lambda: service.users().messages().send(userId="me", body=request_body).execute()
    )
    return str(result["id"])


async def get_gmail_profile(db: AsyncSession) -> dict[str, Any]:
    service = await gmail_service(db)
    result = await asyncio.to_thread(lambda: service.users().getProfile(userId="me").execute())
    return dict(result or {})


async def get_gmail_message(
    db: AsyncSession,
    message_id: str,
    *,
    format: str = "full",
) -> dict[str, Any]:
    service = await gmail_service(db)
    return dict(
        await asyncio.to_thread(
            lambda: service.users().messages().get(userId="me", id=message_id, format=format).execute()
        )
        or {}
    )


async def find_gmail_message_by_rfc_message_id(
    db: AsyncSession,
    rfc_message_id: str,
    *,
    sent_only: bool = True,
) -> dict[str, Any] | None:
    """Find a message using Gmail's rfc822msgid search operator.

    This is the postcondition used to reconcile an ambiguous messages.send result.
    """
    normalized = rfc_message_id.strip()
    if not normalized:
        return None
    query = f'rfc822msgid:{normalized}'
    if sent_only:
        query = f'in:sent {query}'
    service = await gmail_service(db)
    result = await asyncio.to_thread(
        lambda: service.users().messages().list(userId="me", q=query, maxResults=10).execute()
    )
    rows = list((result or {}).get("messages") or [])
    if not rows:
        return None
    # Retrieve metadata so verification can prove the SENT label and thread id.
    message_id = str(rows[0].get("id") or "")
    if not message_id:
        return None
    return dict(
        await asyncio.to_thread(
            lambda: service.users().messages().get(userId="me", id=message_id, format="metadata").execute()
        )
        or {}
    )


async def list_gmail_history_added_message_ids(
    db: AsyncSession,
    *,
    start_history_id: str,
    label_id: str = "INBOX",
    max_pages: int = 25,
) -> tuple[list[str], str]:
    """Return unique message IDs added since a persisted Gmail history cursor.

    Gmail can return the same message across multiple history records.  The result is
    therefore de-duplicated while preserving chronological discovery order.  A stale
    cursor intentionally propagates Gmail's HTTP 404 so the caller can perform the
    documented full-sync recovery path.
    """
    if not str(start_history_id or "").strip():
        raise ValueError("start_history_id is required")
    service = await gmail_service(db)
    page_token: str | None = None
    newest_history_id = str(start_history_id)
    message_ids: list[str] = []
    seen: set[str] = set()
    pages = 0
    while True:
        pages += 1
        response = await asyncio.to_thread(
            lambda token=page_token: service.users().history().list(
                userId="me",
                startHistoryId=str(start_history_id),
                historyTypes=["messageAdded"],
                labelId=label_id,
                maxResults=500,
                pageToken=token,
            ).execute()
        )
        response = dict(response or {})
        newest_history_id = str(response.get("historyId") or newest_history_id)
        for history in response.get("history") or []:
            for added in history.get("messagesAdded") or []:
                message = added.get("message") or {}
                message_id = str(message.get("id") or "")
                labels = set(message.get("labelIds") or [])
                if not message_id or (label_id and labels and label_id not in labels):
                    continue
                if message_id not in seen:
                    seen.add(message_id)
                    message_ids.append(message_id)
        page_token = response.get("nextPageToken")
        if not page_token or pages >= max(1, max_pages):
            break
    return message_ids, newest_history_id


def _calendar_rfc3339(value: str, *, timezone_name: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name or settings.default_timezone))
    return parsed.isoformat()


async def _calendar_event_body(event: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
    timezone_name = str(event.get("timezone") or settings.default_timezone)
    start_value = str(event.get("start") or "")
    end_value = str(event.get("end") or "")
    all_day = bool(event.get("all_day")) or (len(start_value) == 10 and len(end_value) == 10)
    if all_day:
        start = {"date": start_value[:10]}
        end = {"date": end_value[:10]}
    else:
        start = {"dateTime": _calendar_rfc3339(start_value, timezone_name=timezone_name), "timeZone": timezone_name}
        end = {"dateTime": _calendar_rfc3339(end_value, timezone_name=timezone_name), "timeZone": timezone_name}
    body: dict[str, Any] = {
        "summary": str(event.get("summary") or "Untitled event"),
        "description": str(event.get("description") or "Created by Full-Time VA"),
        "start": start,
        "end": end,
    }
    if event.get("location"):
        body["location"] = str(event["location"])
    attendees: list[dict[str, Any]] = []
    for item in event.get("attendees") or []:
        if isinstance(item, str):
            email = item.strip()
            if email:
                attendees.append({"email": email})
        elif isinstance(item, dict) and str(item.get("email") or "").strip():
            attendee = {"email": str(item.get("email") or "").strip()}
            if item.get("displayName"):
                attendee["displayName"] = str(item["displayName"])
            if "optional" in item:
                attendee["optional"] = bool(item["optional"])
            attendees.append(attendee)
    if attendees:
        body["attendees"] = attendees
    if event.get("transparency") in {"opaque", "transparent"}:
        body["transparency"] = str(event["transparency"])
    if idempotency_key:
        body["extendedProperties"] = {
            "private": {"vaappIdempotencyKey": idempotency_key[:200]}
        }
    return body


async def insert_calendar_event(
    db: AsyncSession,
    event: dict[str, Any],
    *,
    event_id: str | None = None,
    send_updates: bool = True,
    idempotency_key: str = "",
) -> dict[str, Any]:
    service = await calendar_service(db)
    body = await _calendar_event_body(event, idempotency_key=idempotency_key)
    if event_id:
        body["id"] = event_id
    return await _execute_google_request(
        lambda: service.events().insert(
            calendarId="primary",
            body=body,
            sendUpdates="all" if send_updates and body.get("attendees") else "none",
        ),
        attempts=1,
        retry_statuses=set(),
    )


async def create_calendar_event(db: AsyncSession, event: dict[str, Any]) -> str:
    """Legacy-compatible one-shot wrapper.

    Phase 3 uses insert_calendar_event through the durable CalendarMutation ledger;
    older manual task execution keeps receiving just the provider event id.
    """

    result = await insert_calendar_event(db, event)
    return str(result["id"])


async def get_calendar_event(db: AsyncSession, event_id: str) -> dict[str, Any]:
    service = await calendar_service(db)
    return await _execute_google_request(
        lambda: service.events().get(calendarId="primary", eventId=event_id),
        attempts=1,
        retry_statuses=set(),
    )


async def update_calendar_event(
    db: AsyncSession,
    event_id: str,
    event: dict[str, Any],
    *,
    etag: str = "",
    send_updates: bool = True,
    idempotency_key: str = "",
) -> dict[str, Any]:
    service = await calendar_service(db)
    body = await _calendar_event_body(event, idempotency_key=idempotency_key)

    def request():
        req = service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body=body,
            sendUpdates="all" if send_updates and body.get("attendees") else "none",
        )
        if etag and hasattr(req, "headers"):
            req.headers["If-Match"] = etag
        return req

    return await _execute_google_request(request, attempts=1, retry_statuses=set())


async def delete_calendar_event(
    db: AsyncSession,
    event_id: str,
    *,
    etag: str = "",
    send_updates: bool = True,
) -> None:
    service = await calendar_service(db)

    def request():
        req = service.events().delete(
            calendarId="primary",
            eventId=event_id,
            sendUpdates="all" if send_updates else "none",
        )
        if etag and hasattr(req, "headers"):
            req.headers["If-Match"] = etag
        return req

    await _execute_google_request(request, attempts=1, retry_statuses=set())


async def list_calendar_events_window(
    db: AsyncSession,
    *,
    days_back: int = 30,
    days_forward: int = 365,
    show_deleted: bool = True,
    max_results: int = 2500,
) -> list[dict[str, Any]]:
    service = await calendar_service(db)
    now = datetime.utcnow()
    time_min = (now - timedelta(days=max(1, days_back))).isoformat(timespec="seconds") + "Z"
    time_max = (now + timedelta(days=max(1, days_forward))).isoformat(timespec="seconds") + "Z"
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(items) < max_results:
        response = await _execute_google_request(
            lambda page_token=page_token: service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                showDeleted=show_deleted,
                orderBy="startTime",
                maxResults=min(250, max_results - len(items)),
                pageToken=page_token,
            ),
            attempts=4,
        )
        items.extend(list(response.get("items") or []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items


async def query_calendar_freebusy(db: AsyncSession, *, start: str, end: str) -> dict[str, Any]:
    service = await calendar_service(db)
    body = {
        "timeMin": _calendar_rfc3339(start),
        "timeMax": _calendar_rfc3339(end),
        "timeZone": settings.default_timezone,
        "items": [{"id": "primary"}],
    }
    return await _execute_google_request(
        lambda: service.freebusy().query(body=body),
        attempts=4,
    )


async def list_upcoming_calendar_events(db: AsyncSession, *, days: int = 7, max_results: int = 20) -> list[dict[str, Any]]:
    items = await list_calendar_events_window(
        db,
        days_back=1,
        days_forward=max(1, min(days, 30)),
        show_deleted=False,
        max_results=max(1, min(max_results, 100)),
    )
    events = []
    for item in items:
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary") or "Untitled event",
                "start": (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date"),
                "end": (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"),
                "location": item.get("location") or "",
                "html_link": item.get("htmlLink") or "",
            }
        )
    return events


async def start_gmail_watch(db: AsyncSession, topic_name: str) -> dict[str, Any]:
    service = await gmail_service(db)
    return service.users().watch(
        userId="me",
        body={"topicName": topic_name, "labelFilterBehavior": "INCLUDE", "labelIds": ["INBOX"]},
    ).execute()


async def drive_service(db: AsyncSession):
    client_id, client_secret = await _google_credentials(db)
    connection = await get_google_connection(db)
    access_token = await get_google_access_token(db, connection)
    credentials = Credentials(
        token=access_token,
        refresh_token=decrypt_text(connection.refresh_token_encrypted)
        if connection.refresh_token_encrypted
        else None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_SCOPES,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


async def people_service(db: AsyncSession):
    client_id, client_secret = await _google_credentials(db)
    connection = await get_google_connection(db)
    access_token = await get_google_access_token(db, connection)
    credentials = Credentials(
        token=access_token,
        refresh_token=decrypt_text(connection.refresh_token_encrypted)
        if connection.refresh_token_encrypted
        else None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_SCOPES,
    )
    return build("people", "v1", credentials=credentials, cache_discovery=False)


async def ensure_drive_folder(db: AsyncSession, folder_name: str, parent_id: str | None = None) -> str:
    service = await drive_service(db)
    escaped = folder_name.replace("'", "\\'")
    clauses = [
        "mimeType='application/vnd.google-apps.folder'",
        "trashed=false",
        f"name='{escaped}'",
    ]
    if parent_id:
        clauses.append(f"'{parent_id}' in parents")
    response = service.files().list(
        q=" and ".join(clauses),
        spaces="drive",
        fields="files(id,name)",
        pageSize=10,
    ).execute()
    files = response.get("files", [])
    if files:
        return files[0]["id"]
    metadata: dict[str, Any] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]
    created = service.files().create(body=metadata, fields="id").execute()
    return created["id"]


def _drive_query_literal(value: str) -> str:
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


async def upload_drive_file(
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
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
    result = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,mimeType,size,webViewLink,createdTime",
    ).execute()
    return result


async def delete_drive_file(db: AsyncSession, file_id: str) -> None:
    """Delete one VA-managed Drive file; a missing file is already effectively cleaned."""
    if not file_id:
        return
    service = await drive_service(db)
    try:
        service.files().delete(fileId=file_id).execute()
    except HttpError as exc:
        if getattr(exc.resp, "status", None) == 404:
            return
        raise


async def list_drive_archive(db: AsyncSession, page_size: int = 100) -> list[dict[str, Any]]:
    service = await drive_service(db)
    response = service.files().list(
        q="trashed=false and 'va_managed' in appProperties",
        spaces="drive",
        fields="files(id,name,mimeType,size,webViewLink,createdTime,appProperties)",
        orderBy="createdTime desc",
        pageSize=min(page_size, 1000),
    ).execute()
    return list(response.get("files", []))


async def list_google_contacts(db: AsyncSession, page_size: int = 1000) -> list[dict[str, Any]]:
    service = await people_service(db)
    contacts: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        response = service.people().connections().list(
            resourceName="people/me",
            pageSize=min(page_size, 1000),
            pageToken=page_token,
            personFields="names,emailAddresses,phoneNumbers,organizations,metadata",
            sortOrder="LAST_MODIFIED_DESCENDING",
        ).execute()
        contacts.extend(response.get("connections", []) or [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return contacts
