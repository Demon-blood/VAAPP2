from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import fitz
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ai_client import AIConfigurationError, analyze_email
from app.integrations.google_api import (
    create_calendar_event,
    extract_gmail_body,
    gmail_service,
    headers_to_dict,
    modify_gmail_message,
    send_gmail_message,
)
from app.models.entities import AutomationRule, Bill, Creditor, EmailMessage, Task
from app.schemas.api import AutomationDecision
from app.services.audit import write_audit
from app.services.operations_service import (
    archive_email_attachments,
    upsert_order,
    upsert_subscription,
    upsert_support_case,
)

PROTECTED_CATEGORY_TERMS = {
    "legal",
    "juridisch",
    "lawyer",
    "advocaat",
    "court",
    "rechtbank",
    "government",
    "overheid",
    "bailiff",
    "deurwaarder",
    "finance",
    "geldzaken",
    "bank",
    "security",
    "beveiliging",
    "medical",
    "gezondheid",
    "family",
    "familie",
}


def _decode_attachment_data(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _extract_pdf_text(content: bytes, max_chars: int = 60_000) -> str:
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            text = "\n".join(page.get_text("text") for page in document)
            return text[:max_chars]
    except Exception:
        return ""


def _parse_received_at(headers: dict[str, str], internal_date: str | None) -> datetime | None:
    if headers.get("date"):
        try:
            parsed = parsedate_to_datetime(headers["date"])
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except Exception:
            pass
    if internal_date:
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000)
        except Exception:
            return None
    return None


def _is_protected(decision: AutomationDecision) -> bool:
    if decision.preserve:
        return True
    lower = f"{decision.category} {' '.join(decision.labels)}".lower()
    return any(term in lower for term in PROTECTED_CATEGORY_TERMS)


def _parse_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    normalized = str(value).replace("€", "").replace(" ", "").replace(",", ".")
    normalized = re.sub(r"[^0-9.\-]", "", normalized)
    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


async def _load_attachments(service, message: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    async def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        mime_type = part.get("mimeType") or "application/octet-stream"
        attachment_id = body.get("attachmentId")
        data = body.get("data")
        if filename and (attachment_id or data):
            if attachment_id:
                response = await asyncio.to_thread(
                    lambda: service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message["id"], id=attachment_id)
                    .execute()
                )
                data = response.get("data")
            if data:
                content = _decode_attachment_data(data)
                extracted_text = _extract_pdf_text(content) if mime_type == "application/pdf" else ""
                output.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "size": len(content),
                        "extracted_text": extracted_text,
                        "_content": content,
                    }
                )
        for child in part.get("parts", []) or []:
            await walk(child)

    await walk(message.get("payload") or {})
    return output


async def _matching_reply_rule(db: AsyncSession, sender: str, category: str) -> AutomationRule | None:
    result = await db.execute(
        select(AutomationRule).where(
            AutomationRule.rule_type == "auto_reply", AutomationRule.enabled.is_(True)
        )
    )
    for rule in result.scalars():
        try:
            conditions = json.loads(rule.conditions_json)
            if conditions.get("sender_contains") and conditions["sender_contains"].lower() not in sender.lower():
                continue
            if conditions.get("category") and conditions["category"] != category:
                continue
            return rule
        except json.JSONDecodeError:
            continue
    return None


async def _upsert_bill(db: AsyncSession, message_id: str, decision: AutomationDecision) -> Bill | None:
    data = decision.bill
    if not data:
        return None
    amount = _parse_amount(data.get("amount"))
    creditor_name = str(data.get("creditor_name") or "").strip()
    if amount is None or not creditor_name:
        return None
    iban = re.sub(r"\s+", "", str(data.get("iban") or "")).upper() or None
    invoice_number = str(data.get("invoice_number") or "").strip()
    duplicate_query = select(Bill).where(Bill.amount == amount, Bill.creditor_name == creditor_name)
    if invoice_number:
        duplicate_query = duplicate_query.where(Bill.invoice_number == invoice_number)
    duplicate = (await db.execute(duplicate_query)).scalars().first()
    if duplicate:
        return duplicate

    creditor = None
    if iban:
        creditor = (await db.execute(select(Creditor).where(Creditor.iban == iban))).scalar_one_or_none()
    due_at = None
    if data.get("due_at"):
        try:
            due_at = datetime.fromisoformat(str(data["due_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            due_at = None
    bill = Bill(
        source_message_id=message_id,
        creditor_id=creditor.id if creditor else None,
        creditor_name=creditor_name,
        iban=iban,
        amount=amount,
        currency=str(data.get("currency") or "EUR").upper()[:3],
        due_at=due_at,
        reference=str(data.get("reference") or ""),
        invoice_number=invoice_number,
        account_scope=str(data.get("account_scope") or "personal"),
        status="validated" if creditor and creditor.iban == iban else "requires_review",
        risk_reason="" if creditor and creditor.iban == iban else "Creditor or IBAN has not been approved",
    )
    db.add(bill)
    await db.flush()
    return bill


async def process_single_message(db: AsyncSession, message: dict[str, Any]) -> EmailMessage:
    existing = (
        await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == message["id"]))
    ).scalar_one_or_none()
    if existing and existing.status in {"processed", "failed_safe"}:
        return existing

    payload = message.get("payload") or {}
    headers = headers_to_dict(payload)
    plain, html = extract_gmail_body(payload)
    body_text = plain or BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    service = await gmail_service(db)
    attachments = await _load_attachments(service, message)
    label_ids = set(message.get("labelIds") or [])
    is_read = "UNREAD" not in label_ids
    record = existing or EmailMessage(
        provider_message_id=message["id"],
        thread_id=message.get("threadId", ""),
    )
    record.sender = headers.get("from", "")
    record.recipients = headers.get("to", "")
    record.subject = headers.get("subject", "")
    record.snippet = message.get("snippet", "")
    record.received_at = _parse_received_at(headers, message.get("internalDate"))
    record.is_read = is_read
    record.status = "processing"
    if existing is None:
        db.add(record)
    await db.flush()

    analysis_input = {
        "message_id": message["id"],
        "is_read": is_read,
        "sender": record.sender,
        "recipients": record.recipients,
        "subject": record.subject,
        "body": body_text[:80_000],
        "attachments": [
            {key: value for key, value in item.items() if key != "_content"}
            for item in attachments
        ],
        "existing_gmail_labels": list(label_ids),
    }
    try:
        decision = await analyze_email(db, analysis_input)
    except AIConfigurationError:
        record.status = "waiting_for_ai_configuration"
        await write_audit(
            db,
            "email_processing_blocked",
            entity_type="email",
            entity_id=message["id"],
            result="blocked",
            details={"reason": "AI provider is not configured"},
        )
        await db.commit()
        return record
    except Exception as exc:
        record.status = "failed_safe"
        await write_audit(
            db,
            "email_processing_failed",
            entity_type="email",
            entity_id=message["id"],
            result="failed",
            details={"error": str(exc)},
        )
        await db.commit()
        return record

    protected = _is_protected(decision)
    if not is_read:
        decision.trash = False
    if protected:
        decision.trash = False
        decision.preserve = True

    record.category = decision.category
    record.priority = decision.priority
    record.action_required = decision.action_required
    record.analysis_json = decision.model_dump_json()

    labels = list(dict.fromkeys(decision.labels))
    if decision.action_required:
        labels.append("Mail/00 Status/Actie nodig")
    if decision.preserve:
        labels.append("Mail/00 Status/Belangrijk bewaren")
    if labels:
        await modify_gmail_message(db, message["id"], add_labels=list(dict.fromkeys(labels)))
    if decision.archive and "INBOX" in label_ids:
        await modify_gmail_message(db, message["id"], remove_labels=["INBOX"])
    if decision.trash:
        await asyncio.to_thread(
            lambda: service.users().messages().trash(userId="me", id=message["id"]).execute()
        )

    if decision.task:
        due_at = None
        if decision.task.get("due_at"):
            try:
                due_at = datetime.fromisoformat(str(decision.task["due_at"]).replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError:
                due_at = None
        db.add(
            Task(
                title=str(decision.task.get("title") or record.subject or "Email action"),
                description=str(decision.task.get("description") or decision.reasoning_summary),
                source_type="email",
                source_id=message["id"],
                due_at=due_at,
                priority=decision.priority,
                requires_approval=bool(decision.task.get("requires_approval", False)),
            )
        )

    bill = await _upsert_bill(db, message["id"], decision)

    account_scope = "personal"
    if bill is not None:
        account_scope = bill.account_scope
    elif decision.order:
        account_scope = str(decision.order.get("account_scope") or "personal")
    elif decision.subscription:
        account_scope = str(decision.subscription.get("account_scope") or "personal")

    support_case = None
    order = None
    subscription = None
    if decision.support_case:
        support_case = await upsert_support_case(
            db,
            message_id=message["id"],
            sender=record.sender,
            subject=record.subject,
            data=decision.support_case,
        )
    if decision.order:
        order = await upsert_order(db, message_id=message["id"], data=decision.order)
    if decision.subscription:
        subscription = await upsert_subscription(
            db, message_id=message["id"], data=decision.subscription
        )

    archived_documents = 0
    if decision.archive_attachments and attachments:
        try:
            archived_documents = await archive_email_attachments(
                db,
                message_id=message["id"],
                attachments=attachments,
                category=decision.category,
                account_scope=account_scope,
                received_at=record.received_at,
            )
        except Exception as exc:
            db.add(
                Task(
                    title=f"Archive documents: {record.subject}",
                    description=f"Google Drive archival failed: {exc}",
                    source_type="email",
                    source_id=message["id"],
                    priority="high" if protected else "normal",
                    requires_approval=False,
                )
            )
            await write_audit(
                db,
                "document_archival_failed",
                entity_type="email",
                entity_id=message["id"],
                result="failed",
                details={"error": str(exc)},
            )

    if decision.calendar_event:
        try:
            event_id = await create_calendar_event(db, decision.calendar_event)
            await write_audit(
                db,
                "calendar_event_created",
                entity_type="email",
                entity_id=message["id"],
                details={"calendar_event_id": event_id},
            )
        except Exception as exc:
            db.add(
                Task(
                    title=f"Review calendar item: {record.subject}",
                    description=f"The VA could not create the event automatically: {exc}",
                    source_type="email",
                    source_id=message["id"],
                    priority="high",
                    requires_approval=True,
                )
            )

    if decision.reply:
        rule = await _matching_reply_rule(db, record.sender, decision.category)
        if rule:
            actions = json.loads(rule.actions_json)
            if actions.get("send") is True:
                await send_gmail_message(
                    db,
                    to=str(decision.reply.get("to") or record.sender),
                    subject=str(decision.reply.get("subject") or f"Re: {record.subject}"),
                    body=str(decision.reply.get("body") or ""),
                    reply_to_id=headers.get("message-id"),
                )
                await write_audit(
                    db,
                    "email_reply_sent",
                    entity_type="email",
                    entity_id=message["id"],
                    details={"rule_id": rule.id},
                )
            else:
                db.add(
                    Task(
                        title=f"Approve reply: {record.subject}",
                        description=str(decision.reply.get("body") or ""),
                        source_type="email",
                        source_id=message["id"],
                        priority=decision.priority,
                        requires_approval=True,
                    )
                )
        else:
            db.add(
                Task(
                    title=f"Approve reply: {record.subject}",
                    description=str(decision.reply.get("body") or ""),
                    source_type="email",
                    source_id=message["id"],
                    priority=decision.priority,
                    requires_approval=True,
                )
            )

    record.status = "processed"
    await write_audit(
        db,
        "email_processed",
        entity_type="email",
        entity_id=message["id"],
        details={
            "category": decision.category,
            "priority": decision.priority,
            "archived": decision.archive,
            "trashed": decision.trash,
            "bill_id": bill.id if bill else None,
            "support_case_id": support_case.id if support_case else None,
            "order_id": order.id if order else None,
            "subscription_id": subscription.id if subscription else None,
            "documents_archived": archived_documents,
        },
    )
    await db.commit()
    return record


async def sync_gmail(db: AsyncSession, max_messages: int = 100) -> int:
    service = await gmail_service(db)
    response = await asyncio.to_thread(
        lambda: service.users()
        .messages()
        .list(userId="me", q="in:anywhere newer_than:30d", maxResults=min(max_messages, 500))
        .execute()
    )
    processed = 0
    for item in response.get("messages", []) or []:
        existing = (
            await db.execute(select(EmailMessage.id).where(EmailMessage.provider_message_id == item["id"]))
        ).scalar_one_or_none()
        if existing:
            continue
        message = await asyncio.to_thread(
            lambda item_id=item["id"]: service.users()
            .messages()
            .get(userId="me", id=item_id, format="full")
            .execute()
        )
        await process_single_message(db, message)
        processed += 1
    return processed
