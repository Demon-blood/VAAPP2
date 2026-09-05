from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.integrations.google_api import (
    find_drive_files_by_app_properties,
    upload_drive_file,
)
from app.models.entities import Bill, Creditor, DocumentRecord, DocumentSourceReference
from app.services.ai_policy import local_extract
from app.services.audit import write_audit
from app.services.document_ownership import MAX_DOCUMENT_BYTES, _extract_text_from_bytes, analyze_document_record
from app.services.document_archive_recovery import ensure_document_archive_upload
from app.services.document_policy import document_category_decision, document_retention_decision
from app.services.financial_document_policy import PAYABLE_INVOICE, assess_financial_document

settings = get_settings()

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/json",
    "application/xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "text/csv",
    "text/html",
    "text/plain",
    "text/xml",
}


@dataclass(frozen=True)
class IngestionResult:
    document: DocumentRecord
    created: bool
    provenance_created: bool
    intelligence: dict[str, Any]
    financial: dict[str, Any]


def safe_document_name(value: str) -> str:
    name = PurePath((value or "document").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return (name or "document")[:240]


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace("€", "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in (None, "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None) if fmt is None else datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


async def _attach_provenance(
    db: AsyncSession,
    *,
    document: DocumentRecord,
    source_type: str,
    source_id: str,
    source_name: str,
    metadata: dict[str, Any],
) -> bool:
    existing = (
        await db.execute(
            select(DocumentSourceReference).where(
                DocumentSourceReference.document_id == document.id,
                DocumentSourceReference.source_type == source_type,
                DocumentSourceReference.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    db.add(
        DocumentSourceReference(
            document_id=document.id,
            source_type=source_type[:40],
            source_id=source_id[:320],
            source_name=source_name[:1000],
            metadata_json=json.dumps(metadata, ensure_ascii=False, default=str)[:12000],
        )
    )
    await db.flush()
    return True


async def _financial_ownership(
    db: AsyncSession,
    *,
    record: DocumentRecord,
    text: str,
    provider_name: str,
) -> dict[str, Any]:
    extraction = local_extract(text, [])
    assessment = assess_financial_document(
        sender=provider_name,
        subject=record.name,
        body=text,
        extraction=extraction,
    )
    if assessment.document_type != PAYABLE_INVOICE:
        return {"classification": assessment.document_type, "bill_id": None}
    amounts = extraction.get("amount_candidates") or []
    amount = _decimal(amounts[0]) if amounts else None
    creditor_name = (provider_name or record.name.rsplit(".", 1)[0]).strip()[:255]
    if amount is None or not creditor_name:
        return {"classification": assessment.document_type, "bill_id": None, "reason": "missing deterministic amount or creditor"}
    invoice_number = str(extraction.get("invoice_number") or "")[:120]
    duplicate_query = select(Bill).where(Bill.amount == amount, Bill.creditor_name == creditor_name)
    if invoice_number:
        duplicate_query = duplicate_query.where(Bill.invoice_number == invoice_number)
    duplicate = (await db.execute(duplicate_query.limit(1))).scalar_one_or_none()
    if duplicate is not None:
        return {"classification": assessment.document_type, "bill_id": duplicate.id, "duplicate": True}
    ibans = extraction.get("iban_candidates") or []
    iban = str(ibans[0]) if ibans else None
    creditor = (
        (await db.execute(select(Creditor).where(Creditor.iban == iban).limit(1))).scalar_one_or_none()
        if iban else None
    )
    due_candidates = extraction.get("due_date_candidates") or []
    bill = Bill(
        source_message_id=f"portal-document:{record.id}",
        creditor_id=creditor.id if creditor else None,
        creditor_name=creditor_name,
        iban=iban,
        amount=amount,
        currency="EUR",
        due_at=_date(due_candidates[0]) if due_candidates else None,
        reference=str(extraction.get("reference") or "")[:500],
        invoice_number=invoice_number,
        account_scope=record.account_scope,
        status="validated" if creditor and creditor.iban == iban else "requires_review",
        risk_reason="" if creditor and creditor.iban == iban else "Creditor or IBAN has not been approved",
    )
    db.add(bill)
    await db.flush()
    return {"classification": assessment.document_type, "bill_id": bill.id, "duplicate": False}


async def ingest_document_bytes(
    db: AsyncSession,
    *,
    content: bytes,
    filename: str,
    mime_type: str,
    source_type: str,
    source_id: str,
    source_name: str = "",
    source_metadata: dict[str, Any] | None = None,
    category: str = "general",
    account_scope: str = "personal",
    document_date: datetime | None = None,
    extracted_text: str = "",
    financial_ownership: bool = True,
) -> IngestionResult:
    if not content:
        raise ValueError("document is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("document exceeds the 12 MB limit")
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if normalized_mime not in ALLOWED_MIME_TYPES:
        raise ValueError(f"unsupported document MIME type: {normalized_mime or 'missing'}")
    safe_name = safe_document_name(filename)
    text = extracted_text or _extract_text_from_bytes(content, normalized_mime, safe_name)
    keep, reason = document_retention_decision(safe_name, normalized_mime, len(content), text)
    if not keep:
        raise ValueError(f"document rejected by retention policy: {reason}")
    checksum = hashlib.sha256(content).hexdigest()
    record = (
        await db.execute(
            select(DocumentRecord).where(
                DocumentRecord.checksum_sha256 == checksum,
                DocumentRecord.account_scope == account_scope,
            ).order_by(DocumentRecord.id).limit(1)
        )
    ).scalar_one_or_none()
    created = record is None
    if record is None:
        resolved_category = document_category_decision(
            name=safe_name,
            extracted_text=text,
            parent_category=category,
        )
        date = document_date or datetime.utcnow()
        folder_path = [
            settings.google_drive_archive_folder,
            "Professional" if account_scope == "pro" else "Personal",
            resolved_category.replace("/", "-")[:80] or "General",
            str(date.year),
        ]
        app_properties = {
            "va_managed": "true",
            "source_type": source_type[:40],
            "source_id": source_id[:255],
            "category": resolved_category[:120],
            "account_scope": account_scope,
            "checksum_sha256": checksum,
        }
        uploaded = await ensure_document_archive_upload(
            db,
            checksum_sha256=checksum,
            account_scope=account_scope,
            source_type=source_type,
            source_id=source_id,
            name=safe_name,
            mime_type=normalized_mime,
            content=content,
            folder_path=folder_path,
            app_properties=app_properties,
            upload_file=upload_drive_file,
            find_files=find_drive_files_by_app_properties,
        )

        record = (
            await db.execute(
                select(DocumentRecord)
                .where(
                    DocumentRecord.checksum_sha256 == checksum,
                    DocumentRecord.account_scope == account_scope,
                )
                .order_by(DocumentRecord.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if record is not None:
            created = False
        else:
            record = DocumentRecord(
                source_type=source_type[:40],
                source_id=source_id[:255],
                name=str(uploaded.get("name") or safe_name),
                mime_type=str(uploaded.get("mimeType") or normalized_mime),
                size_bytes=int(uploaded.get("size") or len(content)),
                category=resolved_category,
                account_scope=account_scope,
                checksum_sha256=checksum,
                drive_file_id=str(uploaded["id"]),
                drive_web_url=str(uploaded.get("webViewLink") or ""),
            )
            db.add(record)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                record = (
                    await db.execute(
                        select(DocumentRecord)
                        .where(
                            DocumentRecord.checksum_sha256 == checksum,
                            DocumentRecord.account_scope == account_scope,
                        )
                        .order_by(DocumentRecord.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if record is None:
                    raise
                created = False
    provenance_created = await _attach_provenance(
        db,
        document=record,
        source_type=source_type,
        source_id=source_id,
        source_name=source_name,
        metadata=source_metadata or {},
    )
    await write_audit(
        db,
        "document_ingested" if created else "document_exact_duplicate_linked",
        entity_type="document",
        entity_id=str(record.id),
        details={"source_type": source_type, "name": safe_name, "checksum_sha256": checksum, "size_bytes": len(content)},
    )
    await db.commit()
    await db.refresh(record)
    intelligence = await analyze_document_record(db, record)
    financial = (
        await _financial_ownership(db, record=record, text=text, provider_name=source_name)
        if financial_ownership and created
        else {"classification": "already_owned" if not created else "deferred", "bill_id": None}
    )
    await db.commit()
    return IngestionResult(record, created, provenance_created, intelligence, financial)
