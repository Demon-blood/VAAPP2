import base64
import os

os.environ.setdefault("PUBLIC_BASE_URL", "https://va.example.test")
os.environ.setdefault("PAIRING_SECRET", "x" * 32)
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"1" * 32).decode())

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    Bill,
    BrowserPortal,
    DocumentRecord,
    DocumentSourceReference,
    PortalDocumentSource,
)
from app.schemas.api import PortalDocumentRecipe
from app.schemas.api import AutomationDecision
from app.services.browser_operator import BrowserSecurityError, assert_portal_url
from app.services.document_ingestion import ingest_document_bytes, safe_document_name
from app.services.portal_document_sync import _safe_error, extract_candidates, set_source_auth_code, source_public, upsert_source


def test_recipe_is_declarative_and_fails_closed() -> None:
    with pytest.raises(ValidationError):
        PortalDocumentRecipe.model_validate(
            {
                "start_url": "http://portal.example.test/documents",
                "item_selector": ".document",
                "external_id_selector": "[data-id]",
                "link_selector": "a.download",
            }
        )
    with pytest.raises(ValidationError):
        PortalDocumentRecipe.model_validate(
            {
                "start_url": "https://portal.example.test/documents",
                "item_selector": "",
                "external_id_selector": "[data-id]",
                "link_selector": "a.download",
            }
        )


def test_download_targets_remain_inside_the_portal_allowlist() -> None:
    portal = BrowserPortal(
        slug="example",
        name="Example",
        base_url="https://portal.example.test/",
        login_url="https://login.example.test/",
        allowed_hosts_json='["files.example.test"]',
    )
    assert_portal_url(portal, "https://files.example.test/document/123")
    with pytest.raises(BrowserSecurityError):
        assert_portal_url(portal, "https://attacker.invalid/document/123")
    with pytest.raises(BrowserSecurityError):
        assert_portal_url(portal, "https://127.0.0.1/document/123")


def test_filename_sanitization_drops_paths_and_controls() -> None:
    assert safe_document_name("../../secret\x00/invoice.pdf") == "invoice.pdf"
    assert safe_document_name(" .. ") == "document"


def test_signed_urls_and_auth_values_are_not_exposed() -> None:
    error = RuntimeError("download failed at https://files.example.test/invoice.pdf?signature=secret-token")
    assert "signature" not in _safe_error(error)
    source = PortalDocumentSource(
        portal_id=1,
        slug="safe",
        name="Safe",
        recipe_json='{"start_url":"https://portal.example.test/documents"}',
        pending_auth_value_encrypted="encrypted-otp",
    )
    public = source_public(source)
    assert "pending_auth_value_encrypted" not in public
    assert "challenge_selector" not in public


@pytest.mark.asyncio
async def test_oversized_and_unsupported_documents_fail_before_storage() -> None:
    with pytest.raises(ValueError, match="12 MB"):
        await ingest_document_bytes(
            None,
            content=b"x" * (12 * 1024 * 1024 + 1),
            filename="large.pdf",
            mime_type="application/pdf",
            source_type="portal",
            source_id="source:item",
        )
    with pytest.raises(ValueError, match="unsupported"):
        await ingest_document_bytes(
            None,
            content=b"alert(1)",
            filename="asset.js",
            mime_type="application/javascript",
            source_type="portal",
            source_id="source:item",
        )


@pytest.mark.asyncio
async def test_declarative_discovery_extracts_three_stable_candidates() -> None:
    rows = [
        {"id": f"doc-{index}", "title": f"Invoice {index}", "href": f"/download/{index}.pdf"}
        for index in range(1, 4)
    ]

    class Leaf:
        def __init__(self, row, selector):
            self.row = row
            self.selector = selector

        @property
        def first(self):
            return self

        async def inner_text(self):
            return self.row["title"] if self.selector == ".title" else self.row["id"]

        async def get_attribute(self, attribute):
            if attribute == "data-id":
                return self.row["id"]
            if attribute == "href":
                return self.row["href"]
            return None

    class Item:
        def __init__(self, row):
            self.row = row

        def locator(self, selector):
            return Leaf(self.row, selector)

    class Collection:
        async def count(self):
            return len(rows)

        def nth(self, index):
            return Item(rows[index])

    class Page:
        url = "https://portal.example.test/documents"

        def locator(self, selector):
            assert selector == ".document"
            return Collection()

    recipe = PortalDocumentRecipe(
        start_url=Page.url,
        item_selector=".document",
        external_id_selector=".identity",
        external_id_attribute="data-id",
        title_selector=".title",
        link_selector="a.download",
    )
    candidates = await extract_candidates(Page(), recipe)
    assert [row["external_id"] for row in candidates] == ["doc-1", "doc-2", "doc-3"]
    assert candidates[0]["reference"]["url"] == "https://portal.example.test/download/1.pdf"


@pytest.mark.asyncio
async def test_exact_bytes_upload_once_and_preserve_both_provenance_links(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uploads: list[str] = []

    async def fake_upload(*args, **kwargs):
        uploads.append(kwargs["name"])
        return {
            "id": "drive-1",
            "name": kwargs["name"],
            "mimeType": kwargs["mime_type"],
            "size": len(kwargs["content"]),
            "webViewLink": "https://drive.example.test/drive-1",
        }

    async def fake_find(*args, **kwargs):
        return []

    async def fake_analyze(db, record):
        return {"document_id": record.id, "status": "analyzed"}

    monkeypatch.setattr(
        "app.services.document_ingestion.find_drive_files_by_app_properties",
        fake_find,
    )
    monkeypatch.setattr("app.services.document_ingestion.upload_drive_file", fake_upload)
    monkeypatch.setattr("app.services.document_ingestion.analyze_document_record", fake_analyze)
    content = ("Durable signed contract record. " * 40).encode()
    async with sessions() as db:
        first = await ingest_document_bytes(
            db,
            content=content,
            filename="contract.txt",
            mime_type="text/plain",
            source_type="email",
            source_id="gmail-1",
            source_name="sender@example.test",
            extracted_text=content.decode(),
            financial_ownership=False,
        )
        second = await ingest_document_bytes(
            db,
            content=content,
            filename="same-contract.txt",
            mime_type="text/plain",
            source_type="portal",
            source_id="source-1:item-1",
            source_name="Example portal",
            extracted_text=content.decode(),
            financial_ownership=False,
        )
        assert first.created is True
        assert second.created is False
        assert first.document.id == second.document.id
        assert uploads == ["contract.txt"]
        assert (await db.execute(select(func.count(DocumentRecord.id)))).scalar_one() == 1
        assert (await db.execute(select(func.count(DocumentSourceReference.id)))).scalar_one() == 2
    await engine.dispose()


def test_portal_sync_is_durable_and_exposed_without_a_live_doccle_claim() -> None:
    from pathlib import Path

    root = Path(__file__).parents[2]
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    capability = (root / "backend/app/services/capability_registry.py").read_text()
    sync = (root / "backend/app/services/portal_document_sync.py").read_text()
    android = (root / "android/lib/screens/work_page.dart").read_text()
    assert '@job_handler("portal_documents.sync")' in workflow
    for route in (
        "/api/portal-documents/sources",
        "/api/portal-documents/sources/{source_id}/test",
        "/api/portal-documents/sources/{source_id}/sync",
        "/api/portal-documents/sources/{source_id}/auth-code",
    ):
        assert route in routes
    assert '"portal_document_sync"' in capability
    assert "production_ready\": False" in sync
    assert "max_redirects=0" in sync
    assert 'request.resource_type == "document"' in sync
    assert "source.max_pages" in sync
    assert "source.max_documents_per_sync" in sync
    assert "Portal document sources" in android
    assert "Sync now" in android


@pytest.mark.asyncio
async def test_source_rejects_disabled_portal_and_contradictory_scope() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    recipe = PortalDocumentRecipe(
        start_url="https://portal.example.test/documents",
        item_selector=".document",
        external_id_selector="[data-id]",
        external_id_attribute="data-id",
        link_selector="a.download",
    )
    async with sessions() as db:
        portal = BrowserPortal(
            slug="disabled",
            name="Disabled",
            base_url="https://portal.example.test/",
            account_scope="personal",
            enabled=False,
        )
        db.add(portal)
        await db.commit()
        with pytest.raises(ValueError, match="missing or disabled"):
            await upsert_source(
                db,
                portal_id=portal.id,
                slug="documents",
                name="Documents",
                recipe=recipe,
                preset_key="",
                account_scope="personal",
                enabled=True,
                sync_interval_minutes=60,
                max_pages=3,
                max_documents_per_sync=25,
            )
        portal.enabled = True
        await db.commit()
        with pytest.raises(ValueError, match="scope must match"):
            await upsert_source(
                db,
                portal_id=portal.id,
                slug="documents",
                name="Documents",
                recipe=recipe,
                preset_key="",
                account_scope="pro",
                enabled=True,
                sync_interval_minutes=60,
                max_pages=3,
                max_documents_per_sync=25,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_source_otp_resume_value_is_encrypted() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        portal = BrowserPortal(slug="otp", name="OTP", base_url="https://portal.example.test/")
        db.add(portal)
        await db.flush()
        source = PortalDocumentSource(
            portal_id=portal.id,
            slug="documents",
            name="Documents",
            recipe_json="{}",
            status="needs_user_auth",
            challenge_type="otp",
        )
        db.add(source)
        await db.commit()
        resumed = await set_source_auth_code(db, source.id, "123456")
        assert resumed.status == "ready"
        assert resumed.pending_auth_value_encrypted
        assert "123456" not in resumed.pending_auth_value_encrypted
        assert resumed.challenge_type == "otp"
    await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_enqueues_only_enabled_due_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import scheduler

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        portal = BrowserPortal(slug="scheduled", name="Scheduled", base_url="https://portal.example.test/")
        db.add(portal)
        await db.flush()
        db.add_all(
            [
                PortalDocumentSource(portal_id=portal.id, slug="due", name="Due", enabled=True),
                PortalDocumentSource(portal_id=portal.id, slug="off", name="Off", enabled=False),
                PortalDocumentSource(
                    portal_id=portal.id,
                    slug="auth",
                    name="Auth",
                    enabled=True,
                    status="needs_user_auth",
                ),
            ]
        )
        await db.commit()
    queued: list[dict] = []

    async def fake_enqueue(db, **values):
        queued.append(values)
        return object(), True

    monkeypatch.setattr(scheduler, "SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "enqueue_job", fake_enqueue)
    await scheduler.portal_documents_enqueue_job()
    assert len(queued) == 1
    assert queued[0]["job_type"] == "portal_documents.sync"
    assert queued[0]["payload"]["source_id"] > 0
    assert queued[0]["idempotency_key"].startswith("portal_documents.sync:")
    await engine.dispose()


@pytest.mark.asyncio
async def test_portal_payable_invoice_creates_review_bill_without_approving_creditor() -> None:
    from app.services.email_processor import _upsert_bill
    from app.services.document_ingestion import _financial_ownership

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        record = DocumentRecord(
            source_type="portal",
            source_id="source:item",
            name="Electricity invoice.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
            category="finance",
            account_scope="personal",
            checksum_sha256="a" * 64,
            drive_file_id="drive-invoice",
        )
        db.add(record)
        await db.flush()
        result = await _financial_ownership(
            db,
            record=record,
            provider_name="Example Utility",
            text=(
                "INVOICE INV-2026-44\nAmount due EUR 120.50\n"
                "Pay by 31/08/2026\nIBAN BE68 5390 0754 7034\nPayment required"
            ),
        )
        assert result["bill_id"] is not None
        bill = await db.get(Bill, result["bill_id"])
        assert bill is not None
        assert bill.status == "requires_review"
        assert bill.creditor_id is None
        assert str(bill.amount) == "120.50"
        db.add(
            DocumentSourceReference(
                document_id=record.id,
                source_type="email",
                source_id="gmail-exact-copy",
                source_name="billing@example.test",
            )
        )
        await db.flush()
        gmail_result = await _upsert_bill(
            db,
            "gmail-exact-copy",
            AutomationDecision(
                category="finance",
                financial_document_type="payable_invoice",
                bill={
                    "creditor_name": "Different display name from email",
                    "amount": "120.50",
                    "invoice_number": "INV-2026-44",
                },
            ),
        )
        assert gmail_result is not None
        assert gmail_result.id == bill.id
        assert (await db.execute(select(func.count(Bill.id)))).scalar_one() == 1
    await engine.dispose()
