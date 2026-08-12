from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.integrations.google_api import delete_drive_file, list_google_contacts, upload_drive_file
from app.models.entities import (
    ContactRecord,
    DocumentRecord,
    OrderRecord,
    SubscriptionRecord,
    SupportCase,
)
from app.services.audit import write_audit
from app.services.document_policy import document_category_decision, document_retention_decision

settings = get_settings()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    normalized = str(value).replace("€", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


async def sync_google_contacts(db: AsyncSession) -> int:
    people = await list_google_contacts(db)
    updated = 0
    now = datetime.utcnow()
    for person in people:
        resource_name = str(person.get("resourceName") or "").strip()
        if not resource_name:
            continue
        names = person.get("names") or []
        emails = [str(item.get("value") or "").strip() for item in person.get("emailAddresses") or []]
        phones = [str(item.get("value") or "").strip() for item in person.get("phoneNumbers") or []]
        organizations = person.get("organizations") or []
        display_name = str(names[0].get("displayName") or "") if names else ""
        organization = str(organizations[0].get("name") or "") if organizations else ""
        record = (
            await db.execute(select(ContactRecord).where(ContactRecord.resource_name == resource_name))
        ).scalar_one_or_none()
        if record is None:
            record = ContactRecord(resource_name=resource_name)
            db.add(record)
        record.display_name = display_name
        record.emails_json = json.dumps([value for value in emails if value], ensure_ascii=False)
        record.phones_json = json.dumps([value for value in phones if value], ensure_ascii=False)
        record.organization = organization
        record.last_synced_at = now
        updated += 1
    await write_audit(db, "google_contacts_synced", entity_type="contact", details={"count": updated})
    await db.commit()
    return updated


async def archive_email_attachments(
    db: AsyncSession,
    *,
    message_id: str,
    attachments: list[dict[str, Any]],
    category: str,
    account_scope: str,
    received_at: datetime | None,
) -> int:
    archived = 0
    date = received_at or datetime.utcnow()
    for attachment in attachments:
        content = attachment.get("_content")
        if not isinstance(content, bytes) or not content:
            continue
        filename = str(attachment.get("filename") or "attachment")
        mime_type = str(attachment.get("mime_type") or "application/octet-stream")
        extracted_text = str(attachment.get("extracted_text") or "")
        keep, reason = document_retention_decision(filename, mime_type, len(content), extracted_text)
        if not keep:
            await write_audit(
                db,
                "document_archive_skipped",
                entity_type="email",
                entity_id=message_id,
                result="filtered",
                details={"name": filename, "mime_type": mime_type, "reason": reason},
            )
            continue

        checksum = hashlib.sha256(content).hexdigest()
        existing = (
            await db.execute(
                select(DocumentRecord).where(
                    DocumentRecord.source_type == "email",
                    DocumentRecord.source_id == message_id,
                    DocumentRecord.checksum_sha256 == checksum,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        document_category = document_category_decision(
            name=filename,
            extracted_text=extracted_text,
            parent_category=category,
        )
        document_folder_path = [
            settings.google_drive_archive_folder,
            "Professional" if account_scope == "pro" else "Personal",
            document_category.replace("/", "-")[:80] or "General",
            str(date.year),
        ]
        uploaded = await upload_drive_file(
            db,
            name=filename,
            mime_type=mime_type,
            content=content,
            folder_path=document_folder_path,
            app_properties={
                "va_managed": "true",
                "source_type": "email",
                "source_id": message_id,
                "category": document_category[:120],
                "account_scope": account_scope,
                "checksum_sha256": checksum,
            },
        )
        record = DocumentRecord(
            source_type="email",
            source_id=message_id,
            name=str(uploaded.get("name") or filename),
            mime_type=str(uploaded.get("mimeType") or mime_type),
            size_bytes=int(uploaded.get("size") or len(content)),
            category=document_category,
            account_scope=account_scope,
            checksum_sha256=checksum,
            drive_file_id=str(uploaded["id"]),
            drive_web_url=str(uploaded.get("webViewLink") or ""),
        )
        db.add(record)
        archived += 1
        await write_audit(
            db,
            "document_archived",
            entity_type="document",
            entity_id=str(uploaded["id"]),
            details={"message_id": message_id, "name": record.name, "category": document_category},
        )
    return archived


async def cleanup_low_value_documents(db: AsyncSession) -> dict[str, int]:
    """Remove legacy VA-managed boilerplate from both Drive and the document index."""
    rows = list((await db.execute(select(DocumentRecord).order_by(DocumentRecord.id))).scalars())
    result = {"removed": 0, "kept": 0, "failed": 0}
    for row in rows:
        keep, reason = document_retention_decision(row.name, row.mime_type, row.size_bytes)
        if keep:
            result["kept"] += 1
            continue
        try:
            await delete_drive_file(db, row.drive_file_id)
            await write_audit(
                db,
                "document_removed_by_retention_policy",
                entity_type="document",
                entity_id=str(row.id),
                details={"name": row.name, "drive_file_id": row.drive_file_id, "reason": reason},
            )
            await db.delete(row)
            result["removed"] += 1
        except Exception as exc:
            result["failed"] += 1
            await write_audit(
                db,
                "document_retention_cleanup_failed",
                entity_type="document",
                entity_id=str(row.id),
                result="failed",
                details={"name": row.name, "reason": reason, "error": str(exc)},
            )
    await db.commit()
    return result


async def upsert_support_case(
    db: AsyncSession,
    *,
    message_id: str,
    sender: str,
    subject: str,
    data: dict[str, Any],
) -> SupportCase:
    case = (
        await db.execute(select(SupportCase).where(SupportCase.source_message_id == message_id))
    ).scalar_one_or_none()
    if case is None:
        case = SupportCase(source_message_id=message_id)
        db.add(case)
    case.requester = str(data.get("requester") or sender)
    case.subject = str(data.get("subject") or subject)
    case.category = str(data.get("category") or "general")
    case.priority = str(data.get("priority") or "normal")
    case.status = str(data.get("status") or "open")
    case.last_action = str(data.get("last_action") or "Detected from Gmail")
    case.next_follow_up_at = _parse_datetime(data.get("next_follow_up_at"))
    await db.flush()
    await write_audit(db, "support_case_upserted", entity_type="support_case", entity_id=str(case.id))
    return case


async def upsert_order(db: AsyncSession, *, message_id: str, data: dict[str, Any]) -> OrderRecord | None:
    merchant = str(data.get("merchant") or "").strip()
    order_number = str(data.get("order_number") or "").strip()
    if not merchant or not order_number:
        return None
    order = (
        await db.execute(
            select(OrderRecord).where(
                OrderRecord.merchant == merchant,
                OrderRecord.order_number == order_number,
            )
        )
    ).scalar_one_or_none()
    if order is None:
        order = OrderRecord(source_message_id=message_id, merchant=merchant, order_number=order_number)
        db.add(order)
    order.source_message_id = message_id
    order.status = str(data.get("status") or order.status or "detected")
    order.total_amount = _parse_decimal(data.get("total_amount"))
    order.currency = str(data.get("currency") or "EUR").upper()[:3]
    order.expected_delivery_at = _parse_datetime(data.get("expected_delivery_at"))
    order.tracking_url = str(data.get("tracking_url") or "")
    order.account_scope = str(data.get("account_scope") or "personal")
    await db.flush()
    await write_audit(db, "order_upserted", entity_type="order", entity_id=str(order.id))
    return order


async def upsert_subscription(
    db: AsyncSession,
    *,
    message_id: str,
    data: dict[str, Any],
) -> SubscriptionRecord | None:
    provider = str(data.get("provider_name") or "").strip()
    description = str(data.get("description") or "").strip()
    account_scope = str(data.get("account_scope") or "personal")
    if not provider or not description:
        return None
    subscription = (
        await db.execute(
            select(SubscriptionRecord).where(
                SubscriptionRecord.provider_name == provider,
                SubscriptionRecord.description == description,
                SubscriptionRecord.account_scope == account_scope,
            )
        )
    ).scalar_one_or_none()
    if subscription is None:
        subscription = SubscriptionRecord(
            source_message_id=message_id,
            provider_name=provider,
            description=description,
            account_scope=account_scope,
        )
        db.add(subscription)
    subscription.source_message_id = message_id
    subscription.amount = _parse_decimal(data.get("amount"))
    subscription.currency = str(data.get("currency") or "EUR").upper()[:3]
    subscription.billing_cycle = str(data.get("billing_cycle") or "unknown")
    subscription.next_charge_at = _parse_datetime(data.get("next_charge_at"))
    subscription.status = str(data.get("status") or "active")
    await db.flush()
    await write_audit(db, "subscription_upserted", entity_type="subscription", entity_id=str(subscription.id))
    return subscription
