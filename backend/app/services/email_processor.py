from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import pymupdf as fitz
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ai_client import (
    AIConfigurationError,
    AIQuotaDeferred,
    analyze_email,
    mark_ai_deferred,
    mark_fingerprint_hit,
    mark_rule_shortcut,
)
from app.integrations.google_api import (
    create_calendar_event,
    extract_gmail_body,
    gmail_service,
    headers_to_dict,
    modify_gmail_message,
)
from app.models.entities import AuditLog, AutomationRule, Bill, Creditor, EmailMessage, Task
from app.schemas.api import AutomationDecision
from app.services.action_reconciler import reconcile_action_queue
from app.services.ai_policy import (
    cache_decision,
    cached_decision,
    content_fingerprint,
    deterministic_shortcut,
    learn_sender_rule,
    local_extract,
    protected_hint,
    safe_fallback_decision,
    sender_rule_for,
    strip_quoted_history_and_signature,
    urgent_hint,
)
from app.services.audit import write_audit
from app.services.autonomy_policy import learn_successful_reply, reply_autonomy_decision, task_requires_human
from app.services.runtime_config import get_runtime_value
from app.services.financial_document_policy import (
    PAYABLE_INVOICE,
    assess_financial_document,
    infer_recurring_subscription,
    receipt_label,
)
from app.services.financial_reconciliation import upsert_financial_record
from app.services.workflow_engine import failure_recovery_class
from app.services.operations_service import (
    archive_email_attachments,
    upsert_order,
    upsert_subscription,
    upsert_support_case,
)
from app.services.communication_ownership import register_email_inbound

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
    """Return whether the classified content belongs to a retention-protected domain.

    `preserve` is deliberately *not* consulted here. Older AI decisions sometimes set
    preserve=True too broadly, which previously turned a retention hint into a permanent
    Inbox pin. Protection is derived from the actual category/labels instead.
    """
    lower = f"{decision.category} {' '.join(decision.labels)}".lower()
    return any(term in lower for term in PROTECTED_CATEGORY_TERMS)


def _is_low_value_routine(decision: AutomationDecision) -> bool:
    category = str(decision.category or "").casefold()
    low_value_category = any(
        term in category
        for term in (
            "newsletter",
            "promotion",
            "reclame",
            "social",
            "communit",
            "notification",
            "melding",
        )
    )
    return bool(
        low_value_category
        and str(decision.priority).lower() == "low"
        and not decision.action_required
        and decision.financial_document_type == "none"
        and decision.task is None
        and decision.bill is None
        and decision.calendar_event is None
        and decision.reply is None
        and decision.support_case is None
        and decision.order is None
        and decision.subscription is None
    )


def _normalize_retention_policy(decision: AutomationDecision, *, protected: bool) -> None:
    """Keep retention semantics independent from Inbox semantics.

    Protected domains are retained even when archived. Conversely, confidently low-value
    routine mail must not inherit a stale preserve=True decision, otherwise it can never
    graduate to the delayed Trash cleanup.
    """
    if protected:
        decision.preserve = True
    elif _is_low_value_routine(decision):
        decision.preserve = False


def _apply_inbox_policy(decision: AutomationDecision, *, protected: bool) -> None:
    """Turn classification into deterministic Gmail Inbox behavior.

    `preserve` means retain/not-trash; it does not mean keep in Inbox. The Inbox is only
    for unresolved attention. Routine informational mail is archived after classification
    even when it belongs to a protected domain and is retained under its VA labels.
    """
    del protected  # retention protection is intentionally independent from Inbox placement
    task_waiting = bool(decision.task and decision.task.get("requires_approval"))
    requires_attention = (
        decision.action_required
        or str(decision.priority).lower() in {"high", "urgent"}
        or task_waiting
        or decision.reply is not None
    )
    if decision.trash:
        decision.archive = False
    elif requires_attention:
        decision.archive = False
    else:
        decision.archive = True


def _parse_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    normalized = re.sub(r"[^0-9,.-]", "", str(value).replace("€", "").replace("EUR", ""))
    if not normalized:
        return None
    if "," in normalized and "." in normalized:
        # The right-most separator is the decimal separator; the other is thousands grouping.
        decimal_sep = "," if normalized.rfind(",") > normalized.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        normalized = normalized.replace(thousands_sep, "")
        if decimal_sep == ",":
            normalized = normalized.replace(",", ".")
    elif "," in normalized:
        parts = normalized.split(",")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            normalized = ".".join(parts)
        else:
            normalized = "".join(parts)
    elif normalized.count(".") > 1:
        parts = normalized.split(".")
        normalized = "".join(parts[:-1]) + ("." + parts[-1] if len(parts[-1]) in {1, 2} else parts[-1])
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
    if existing and existing.status in {"processed", "replied"}:
        return existing

    payload = message.get("payload") or {}
    headers = headers_to_dict(payload)
    plain, html = extract_gmail_body(payload)
    raw_body_text = plain or BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    body_text = strip_quoted_history_and_signature(raw_body_text)
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

    extraction = local_extract(body_text, attachments)
    fingerprint = content_fingerprint(record.sender, record.subject, body_text, attachments)
    decision: AutomationDecision | None = await cached_decision(db, fingerprint)
    decision_source = "fingerprint" if decision else ""
    deferred_ai = False

    if decision is not None:
        await mark_fingerprint_hit(db)
    else:
        learned_rule = await sender_rule_for(db, record.sender)
        decision, decision_source = deterministic_shortcut(
            sender=record.sender,
            subject=record.subject,
            body=body_text,
            headers=headers,
            label_ids=label_ids,
            is_read=is_read,
            extraction=extraction,
            sender_rule=learned_rule,
        )
        if decision is not None:
            await mark_rule_shortcut(db)

    if decision is None:
        compact_attachments = []
        for item in attachments[:6]:
            compact_attachments.append(
                {
                    "filename": item.get("filename"),
                    "mime_type": item.get("mime_type"),
                    "size": item.get("size"),
                    "extracted_excerpt": strip_quoted_history_and_signature(
                        str(item.get("extracted_text") or ""), 1_500
                    ),
                }
            )
        analysis_input = {
            "message_id": message["id"],
            "is_read": is_read,
            "sender": record.sender,
            "recipients": record.recipients,
            "subject": record.subject,
            "body": body_text[:12_000],
            "attachments": compact_attachments,
            "local_extraction": extraction,
            "existing_gmail_labels": list(label_ids),
        }
        sensitive = protected_hint(record.sender, record.subject, body_text) is not None
        urgent = urgent_hint(record.subject, body_text, extraction)
        is_backfill = bool(
            record.received_at
            and (datetime.utcnow() - record.received_at).total_seconds() > 48 * 3600
        )
        try:
            decision = await analyze_email(
                db,
                analysis_input,
                urgent=urgent,
                sensitive=sensitive,
                is_backfill=is_backfill,
            )
            decision_source = "ai"
            # Cache the paid AI decision immediately. If a later Gmail/Calendar/Drive action
            # fails, the next retry reuses this decision instead of spending a second AI call.
            await cache_decision(db, fingerprint, message["id"], decision)
            await learn_sender_rule(db, record.sender, decision)
            await db.commit()
        except AIConfigurationError as exc:
            deferred_ai = True
            decision_source = "safe_fallback"
            decision = safe_fallback_decision(
                sender=record.sender,
                subject=record.subject,
                body=body_text,
                is_read=is_read,
                extraction=extraction,
                reason=str(exc),
            )
            await write_audit(
                db,
                "email_processing_ai_deferred",
                entity_type="email",
                entity_id=message["id"],
                result="deferred",
                details={"reason": str(exc), "fallback": "deterministic"},
            )
        except AIQuotaDeferred as exc:
            await mark_ai_deferred(db)
            deferred_ai = True
            decision_source = "safe_fallback"
            decision = safe_fallback_decision(
                sender=record.sender,
                subject=record.subject,
                body=body_text,
                is_read=is_read,
                extraction=extraction,
                reason=str(exc),
            )
            await write_audit(
                db,
                "email_processing_ai_deferred",
                entity_type="email",
                entity_id=message["id"],
                result="deferred",
                details={"reason": str(exc), "retry_after": exc.retry_after, "fallback": "deterministic"},
            )
        except Exception as exc:
            deferred_ai = True
            decision_source = "safe_fallback"
            decision = safe_fallback_decision(
                sender=record.sender,
                subject=record.subject,
                body=body_text,
                is_read=is_read,
                extraction=extraction,
                reason=str(exc),
            )
            await write_audit(
                db,
                "email_processing_ai_failed",
                entity_type="email",
                entity_id=message["id"],
                result="deferred",
                details={"error": str(exc), "fallback": "deterministic"},
            )

    assert decision is not None

    # Financial safety gate: an amount/order/invoice identifier does not by itself mean
    # money is still owed. This deterministic layer overrides AI/cached decisions before
    # any Bill or payment-capable object can be created.
    financial_assessment = assess_financial_document(
        sender=record.sender,
        subject=record.subject,
        body=body_text,
        extraction=extraction,
        bill=decision.bill,
    )
    protected_context = protected_hint(record.sender, record.subject, body_text)
    suppressed_bill_data = None
    if decision.bill is not None and financial_assessment.document_type != PAYABLE_INVOICE:
        suppressed_bill_data = dict(decision.bill)
        decision.bill = None
        await write_audit(
            db,
            "bill_candidate_suppressed_nonpayable",
            entity_type="email",
            entity_id=message["id"],
            details={
                "financial_document_type": financial_assessment.document_type,
                "confidence": financial_assessment.confidence,
                "reasons": list(financial_assessment.reasons),
            },
        )

    decision.financial_document_type = financial_assessment.document_type
    if financial_assessment.is_nonpayable:
        decision.preserve = True
        decision.archive_attachments = True
        decision.labels = list(
            dict.fromkeys(decision.labels + [receipt_label(financial_assessment.document_type)])
        )
        routine_finance_context = protected_context is None or protected_context[0] == "Finance"
        if routine_finance_context:
            decision.category = "Finance"
            decision.archive = True
            # A receipt/statement alone is not a human action. Preserve unrelated actions if
            # the same email also contains a genuine task, reply, calendar or support matter.
            if not any(
                value is not None
                for value in (
                    decision.task,
                    decision.calendar_event,
                    decision.reply,
                    decision.support_case,
                )
            ):
                decision.action_required = False
        if not decision.action_required:
            decision.labels = [
                label for label in decision.labels if label != "Mail/00 Status/Actie nodig"
            ]

    if decision.subscription is None and financial_assessment.is_nonpayable:
        subscription_source = suppressed_bill_data or decision.bill or {}
        amount_candidates = extraction.get("amount_candidates") or []
        inferred_subscription = infer_recurring_subscription(
            subject=record.subject,
            body=body_text,
            assessment=financial_assessment,
            amount=str(subscription_source.get("amount") or (amount_candidates[0] if amount_candidates else "")) or None,
            currency=str(subscription_source.get("currency") or "EUR"),
            account_scope=str(subscription_source.get("account_scope") or "personal"),
        )
        if inferred_subscription is not None:
            decision.subscription = inferred_subscription

    if decision_source == "ai":
        # Replace the pre-side-effect AI cache with the safety-gated decision so retries
        # never reintroduce the suppressed bill candidate.
        await cache_decision(db, fingerprint, message["id"], decision)

    protected = _is_protected(decision)
    if decision.task:
        task_requires_approval, task_gate_reason = task_requires_human(decision)
        decision.task["requires_approval"] = task_requires_approval
        if task_requires_approval and not decision.reasoning_summary:
            decision.reasoning_summary = task_gate_reason.replace("_", " ")

    if decision_source == "ai":
        # Store the final policy-normalized decision before any external side effect.
        # A retry must never resurrect the pre-autonomy approval state from the
        # earlier cost-saving AI cache write.
        await cache_decision(db, fingerprint, message["id"], decision)

    _normalize_retention_policy(decision, protected=protected)
    if not is_read:
        decision.trash = False
    if protected:
        decision.trash = False
    _apply_inbox_policy(decision, protected=protected)

    record.category = decision.category
    record.priority = decision.priority
    record.action_required = decision.action_required
    record.analysis_json = decision.model_dump_json()

    labels = list(dict.fromkeys(decision.labels))
    if decision.action_required:
        labels.append("Mail/00 Status/Actie nodig")
    if decision.preserve:
        labels.append("Mail/00 Status/Belangrijk bewaren")
    remove_labels: list[str] = []
    if not decision.action_required:
        remove_labels.append("Mail/00 Status/Actie nodig")
    if not decision.preserve:
        remove_labels.append("Mail/00 Status/Belangrijk bewaren")
    if decision.archive and "INBOX" in label_ids:
        remove_labels.append("INBOX")
    if labels or remove_labels:
        await modify_gmail_message(
            db,
            message["id"],
            add_labels=list(dict.fromkeys(labels)),
            remove_labels=list(dict.fromkeys(remove_labels)),
        )
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
        existing_task = (
            await db.execute(
                select(Task).where(Task.source_type == "email", Task.source_id == message["id"])
            )
        ).scalars().first()
        if existing_task is None:
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

    financial_record = None
    if financial_assessment.is_nonpayable:
        source_data = suppressed_bill_data or decision.bill or {}
        amount = _parse_amount(source_data.get("amount"))
        if amount is None:
            amount_candidates = extraction.get("amount_candidates") or []
            amount = _parse_amount(amount_candidates[0]) if amount_candidates else None
        financial_record = await upsert_financial_record(
            db,
            message_id=message["id"],
            assessment=financial_assessment,
            description=record.subject or financial_assessment.provider_name,
            amount=amount,
            currency=str(source_data.get("currency") or "EUR"),
            occurred_at=record.received_at,
            account_scope=account_scope,
            order_number=str(
                financial_assessment.order_number
                or extraction.get("order_number")
                or source_data.get("invoice_number")
                or ""
            ),
            subscription_id=subscription.id if subscription is not None else None,
            metadata={
                "decision_source": decision_source,
                "subject": record.subject,
                "sender": record.sender,
            },
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
            recovery_class = failure_recovery_class("google.drive.archive", str(exc))
            if recovery_class in {"transient", "user_required"}:
                # Let the durable Gmail job own provider retries/auth exceptions. Reprocessing is
                # idempotent and avoids converting provider outages into fake document approvals.
                raise
            existing_archive_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "email_archive",
                        Task.source_id == message["id"],
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalars().first()
            if existing_archive_task is None:
                db.add(
                    Task(
                        title=f"Document filing decision needed: {record.subject}",
                        description=f"Autopilot could not deterministically archive this attachment: {exc}",
                        source_type="email_archive",
                        source_id=message["id"],
                        priority="high" if protected else "normal",
                        requires_approval=True,
                    )
                )
            await write_audit(
                db,
                "document_archival_failed",
                entity_type="email",
                entity_id=message["id"],
                result="needs_user",
                details={"error": str(exc), "recovery_class": recovery_class},
            )

    if decision.calendar_event and not deferred_ai:
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
            recovery_class = failure_recovery_class("google.calendar", str(exc))
            if recovery_class in {"transient", "user_required"}:
                raise
            existing_calendar_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "calendar_review", Task.source_id == message["id"]
                    )
                )
            ).scalars().first()
            if existing_calendar_task is None:
                db.add(
                    Task(
                        title=f"Calendar decision needed: {record.subject}",
                        description=f"Autopilot could not determine a safe calendar action: {exc}",
                        source_type="calendar_review",
                        source_id=message["id"],
                        priority="high",
                        requires_approval=True,
                    )
                )

    reply_plan: dict[str, Any] | None = None
    if decision.reply and not deferred_ai:
        rule = await _matching_reply_rule(db, record.sender, decision.category)
        explicit_rule_send = False
        if rule is not None:
            try:
                explicit_rule_send = json.loads(rule.actions_json).get("send") is True
            except json.JSONDecodeError:
                explicit_rule_send = False

        policy_send, policy_reason = await reply_autonomy_decision(db, message=record, decision=decision)
        should_send = explicit_rule_send or policy_send
        if should_send:
            # Phase 2 deliberately does not call Gmail inline.  The reply becomes a
            # durable VA objective with a persisted outbound intent before the external
            # POST.  Ambiguous Gmail outcomes are reconciled by deterministic RFC
            # Message-ID, so a timeout cannot create a blind duplicate reply.
            reply_plan = {
                **dict(decision.reply),
                "to": str(decision.reply.get("to") or record.sender),
                "subject": str(decision.reply.get("subject") or f"Re: {record.subject}"),
                "body": str(decision.reply.get("body") or ""),
                "policy": "explicit_rule" if explicit_rule_send else policy_reason,
                "expect_reply": bool(
                    decision.task
                    or decision.support_case
                    or decision.action_required
                ),
                "follow_up_hours": 48,
            }
            await write_audit(
                db,
                "email_reply_queued",
                entity_type="email",
                entity_id=message["id"],
                details={
                    "rule_id": rule.id if explicit_rule_send and rule is not None else None,
                    "autopilot": True,
                    "policy": reply_plan["policy"],
                    "gmail_thread_id": record.thread_id,
                },
            )
        else:
            existing_reply_task = (
                await db.execute(
                    select(Task).where(Task.source_type == "email_reply", Task.source_id == message["id"])
                )
            ).scalars().first()
            if existing_reply_task is None:
                db.add(
                    Task(
                        title=f"Reply decision needed: {record.subject}",
                        description=(
                            f"Autopilot kept this reply for you because {policy_reason.replace('_', ' ')}.\n\n"
                            f"{str(decision.reply.get('body') or '')}"
                        ),
                        source_type="email_reply",
                        source_id=message["id"],
                        priority=decision.priority,
                        requires_approval=True,
                    )
                )

    if not deferred_ai:
        if decision_source != "ai":
            await cache_decision(db, fingerprint, message["id"], decision)
        record.status = "processed"
    else:
        record.status = "deferred_ai"

    # Every inbound message now updates a persistent conversation thread.  Actionable
    # mail becomes a VA event/objective; routine mail is tracked without creating fake work.
    if not deferred_ai:
        await register_email_inbound(
            db,
            record=record,
            message=message,
            message_id_header=str(headers.get("message-id") or ""),
            decision=decision.model_dump(mode="json"),
            reply_plan=reply_plan,
        )

    await write_audit(
        db,
        "email_processed" if not deferred_ai else "email_processed_safe_fallback",
        entity_type="email",
        entity_id=message["id"],
        result="success" if not deferred_ai else "deferred",
        details={
            "decision_source": decision_source,
            "category": decision.category,
            "priority": decision.priority,
            "archived": decision.archive,
            "trashed": decision.trash,
            "bill_id": bill.id if bill else None,
            "support_case_id": support_case.id if support_case else None,
            "order_id": order.id if order else None,
            "subscription_id": subscription.id if subscription else None,
            "financial_record_id": financial_record.id if financial_record else None,
            "financial_document_type": financial_assessment.document_type,
            "documents_archived": archived_documents,
            "fingerprint": fingerprint,
        },
    )
    await db.commit()

    if bill is not None:
        try:
            from app.services.autopilot_service import dispatch_intent

            await dispatch_intent(
                db,
                {
                    "type": "bill_lifecycle",
                    "bill_id": bill.id,
                    "correlation_key": f"bill:{bill.id}:detected",
                },
            )
        except Exception as exc:
            await db.rollback()
            await write_audit(
                db,
                "bill_lifecycle_enqueue_failed",
                entity_type="bill",
                entity_id=str(bill.id),
                result="failed",
                details={"error": str(exc)},
            )
            await db.commit()
    return record


async def reconcile_v070_processed_inbox(db: AsyncSession, *, max_messages: int = 2000) -> dict[str, int]:
    """One-time archive reconciliation for messages classified before v0.7.0.

    Earlier releases could label a routine message while leaving Gmail's INBOX label
    untouched. Reuse the persisted decision only; do not spend AI tokens or reclassify.
    Action-required/high-priority/reply-pending mail remains in Inbox. Protected content
    can be retained under its labels while still being archived when no attention is due.
    Trash decisions are deliberately not replayed against historical mail: this migration
    only archives and repairs stale status labels.
    """
    marker = "v070_gmail_attention_policy_reconciled_v2"
    already_done = (
        await db.execute(select(AuditLog.id).where(AuditLog.event_type == marker).limit(1))
    ).scalar_one_or_none()
    if already_done is not None:
        return {"examined": 0, "archived": 0}

    service = await gmail_service(db)
    message_ids: list[str] = []
    page_token: str | None = None
    while len(message_ids) < max_messages:
        page = await asyncio.to_thread(
            lambda token=page_token: service.users()
            .messages()
            .list(
                userId="me",
                q="in:inbox",
                maxResults=min(500, max_messages - len(message_ids)),
                **({"pageToken": token} if token else {}),
            )
            .execute()
        )
        message_ids.extend(str(item.get("id")) for item in (page.get("messages") or []) if item.get("id"))
        page_token = str(page.get("nextPageToken") or "").strip() or None
        if not page_token:
            break

    if not message_ids:
        await write_audit(db, marker, entity_type="gmail", details={"examined": 0, "archived": 0})
        await db.commit()
        return {"examined": 0, "archived": 0}

    rows = list(
        (
            await db.execute(
                select(EmailMessage).where(
                    EmailMessage.provider_message_id.in_(message_ids),
                    EmailMessage.status == "processed",
                )
            )
        ).scalars()
    )
    archived = 0
    examined = 0
    for record in rows:
        try:
            decision = AutomationDecision.model_validate_json(record.analysis_json or "{}")
        except Exception:
            continue
        examined += 1
        protected = _is_protected(decision)
        _normalize_retention_policy(decision, protected=protected)
        _apply_inbox_policy(decision, protected=protected)

        remove_labels: list[str] = []
        if not decision.action_required:
            remove_labels.append("Mail/00 Status/Actie nodig")
        if not decision.preserve:
            remove_labels.append("Mail/00 Status/Belangrijk bewaren")
        if decision.archive and not decision.trash:
            remove_labels.append("INBOX")
            archived += 1

        if remove_labels:
            await modify_gmail_message(
                db,
                record.provider_message_id,
                remove_labels=list(dict.fromkeys(remove_labels)),
            )

        # Keep the stored decision aligned with the policy actually applied so the
        # briefing and later cleanup use the corrected retention/Inbox semantics.
        record.analysis_json = decision.model_dump_json()
        if decision.archive and not decision.trash:
            await write_audit(
                db,
                "email_inbox_reconciled",
                entity_type="email",
                entity_id=record.provider_message_id,
                details={
                    "category": decision.category,
                    "archive": True,
                    "preserve": decision.preserve,
                    "policy": "attention_only_inbox_v2",
                },
            )

    await write_audit(db, marker, entity_type="gmail", details={"examined": examined, "archived": archived})
    await db.commit()
    return {"examined": examined, "archived": archived}


def _safe_low_value_for_trash(decision: AutomationDecision) -> bool:
    return bool(
        _is_low_value_routine(decision)
        and not decision.action_required
        and not decision.preserve
        and decision.financial_document_type == "none"
        and decision.task is None
        and decision.bill is None
        and decision.calendar_event is None
        and decision.reply is None
        and decision.support_case is None
        and decision.order is None
        and decision.subscription is None
    )


async def cleanup_v070_read_low_value_mail(db: AsyncSession, *, max_messages: int = 250) -> dict[str, int]:
    """Move only read, aged, already-classified low-value mail to Gmail Trash.

    This is intentionally a second-stage cleanup rather than immediate deletion.
    Unread mail and anything protected/actionable/dynamic is never selected. Gmail
    Trash remains recoverable, and the grace period is configurable from Android.
    """
    enabled = (await get_runtime_value(db, "gmail_auto_trash_low_value_enabled", "true")).lower() == "true"
    if not enabled:
        return {"examined": 0, "trashed": 0}
    try:
        days = max(1, min(int(await get_runtime_value(db, "gmail_low_value_trash_after_days", "14")), 365))
    except ValueError:
        days = 14
    service = await gmail_service(db)
    page = await asyncio.to_thread(
        lambda: service.users()
        .messages()
        .list(userId="me", q=f"in:anywhere is:read older_than:{days}d -in:trash", maxResults=min(max_messages, 500))
        .execute()
    )
    ids = [str(item.get("id")) for item in (page.get("messages") or []) if item.get("id")]
    if not ids:
        return {"examined": 0, "trashed": 0}
    rows = list(
        (
            await db.execute(
                select(EmailMessage).where(
                    EmailMessage.provider_message_id.in_(ids),
                    EmailMessage.status == "processed",
                )
            )
        ).scalars()
    )
    examined = 0
    trashed = 0
    for record in rows:
        try:
            decision = AutomationDecision.model_validate_json(record.analysis_json or "{}")
        except Exception:
            continue
        examined += 1
        if not _safe_low_value_for_trash(decision):
            continue
        await asyncio.to_thread(
            lambda message_id=record.provider_message_id: service.users().messages().trash(userId="me", id=message_id).execute()
        )
        decision.trash = True
        decision.archive = False
        record.analysis_json = decision.model_dump_json()
        trashed += 1
        await write_audit(
            db,
            "email_low_value_trashed",
            entity_type="email",
            entity_id=record.provider_message_id,
            details={"category": decision.category, "grace_days": days},
        )
    await db.commit()
    return {"examined": examined, "trashed": trashed}


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
        existing_status = (
            await db.execute(
                select(EmailMessage.status).where(EmailMessage.provider_message_id == item["id"])
            )
        ).scalar_one_or_none()
        if existing_status in {"processed", "replied"}:
            continue
        message = await asyncio.to_thread(
            lambda item_id=item["id"]: service.users()
            .messages()
            .get(userId="me", id=item_id, format="full")
            .execute()
        )
        await process_single_message(db, message)
        processed += 1
    await reconcile_v070_processed_inbox(db)
    await cleanup_v070_read_low_value_mail(db)
    await reconcile_action_queue(db)
    return processed

