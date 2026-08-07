from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text, new_token
from app.core.settings import get_settings
from app.models.entities import OAuthConnection, OAuthState
from app.services.runtime_config import get_runtime_value

settings = get_settings()
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


async def ensure_gmail_label(db: AsyncSession, label_name: str) -> str:
    service = await gmail_service(db)
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == label_name:
            return label["id"]
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return created["id"]


async def modify_gmail_message(
    db: AsyncSession,
    message_id: str,
    *,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    service = await gmail_service(db)
    add_ids = [await ensure_gmail_label(db, name) for name in add_labels or []]
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": add_ids, "removeLabelIds": remove_labels or []},
    ).execute()


async def send_gmail_message(db: AsyncSession, to: str, subject: str, body: str, reply_to_id: str | None = None) -> str:
    service = await gmail_service(db)
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if reply_to_id:
        message["In-Reply-To"] = reply_to_id
        message["References"] = reply_to_id
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return result["id"]


async def create_calendar_event(db: AsyncSession, event: dict[str, Any]) -> str:
    service = await calendar_service(db)
    body = {
        "summary": event["summary"],
        "description": event.get("description", "Created by Full-Time VA"),
        "start": {"dateTime": event["start"], "timeZone": settings.default_timezone},
        "end": {"dateTime": event["end"], "timeZone": settings.default_timezone},
    }
    if event.get("location"):
        body["location"] = event["location"]
    result = service.events().insert(calendarId="primary", body=body).execute()
    return result["id"]


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
