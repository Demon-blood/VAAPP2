from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    BankAccount,
    BankConnection,
    Bill,
    EmailMessage,
    FinancialRecord,
    Payment,
    Task,
)
from app.schemas.api import AutomationDecision
from app.services.financial_reconciliation import (
    reconcile_receipts_with_bank_transactions,
    reclassify_existing_nonpayable_bills,
)


@pytest.fixture
async def financial_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_google_play_false_bill_is_reclassified(financial_db) -> None:
    decision = AutomationDecision(
        category="Finance",
        priority="normal",
        action_required=True,
        preserve=True,
        labels=["Mail/02 Geldzaken & betalingen/Facturen & betalingen"],
        bill={
            "creditor_name": "Google Commerce Limited",
            "amount": "3.19",
            "currency": "EUR",
            "due_at": None,
            "iban": None,
            "reference": "GPA.3368-6930-9897-52444",
            "invoice_number": "GPA.3368-6930-9897-52444",
            "account_scope": "personal",
        },
    )
    email = EmailMessage(
        provider_message_id="google-receipt-1",
        thread_id="thread-1",
        sender="Google Commerce Limited <payments-noreply@google.com>",
        subject="Your Google Play order receipt",
        snippet="Thank you for your purchase. Order GPA.3368-6930-9897-52444. EUR 3.19 payment successful.",
        action_required=True,
        status="processed",
        analysis_json=decision.model_dump_json(),
    )
    financial_db.add(email)
    bill = Bill(
        source_message_id="google-receipt-1",
        creditor_name="Google Commerce Limited",
        amount=Decimal("3.19"),
        currency="EUR",
        reference="GPA.3368-6930-9897-52444",
        invoice_number="GPA.3368-6930-9897-52444",
        account_scope="personal",
        status="requires_review",
        risk_reason="Creditor or IBAN has not been approved",
    )
    financial_db.add(bill)
    await financial_db.flush()
    financial_db.add_all(
        [
            Task(
                title="Review bill",
                source_type="bill_review",
                source_id="google-receipt-1",
                requires_approval=True,
            ),
            Task(
                title="Approve creditor",
                source_type="creditor_review",
                source_id=str(bill.id),
                requires_approval=True,
            ),
        ]
    )
    await financial_db.commit()

    result = await reclassify_existing_nonpayable_bills(financial_db)
    assert result["reclassified"] == 1
    await financial_db.refresh(bill)
    await financial_db.refresh(email)
    assert bill.status == "reclassified_nonpayable"
    assert email.action_required is False

    receipt = await financial_db.get(FinancialRecord, 1)
    assert receipt is not None
    assert receipt.record_type == "paid_receipt"
    assert receipt.order_number == "GPA.3368-6930-9897-52444"
    assert receipt.amount == Decimal("3.19")

    tasks = (await financial_db.execute(Task.__table__.select())).all()
    assert tasks
    assert all(row.status == "completed" for row in tasks)


@pytest.mark.asyncio
async def test_reclassification_never_hides_bill_with_active_payment(financial_db) -> None:
    email = EmailMessage(
        provider_message_id="google-receipt-active-payment",
        thread_id="thread-2",
        sender="Google Commerce Limited <payments-noreply@google.com>",
        subject="Google Play receipt",
        snippet="Order GPA.1111-2222-3333-44444. Thank you for your purchase. EUR 1.09.",
        status="processed",
    )
    financial_db.add(email)
    bill = Bill(
        source_message_id=email.provider_message_id,
        creditor_name="Google Commerce Limited",
        amount=Decimal("1.09"),
        currency="EUR",
        invoice_number="GPA.1111-2222-3333-44444",
        status="requires_review",
    )
    financial_db.add(bill)
    await financial_db.flush()
    financial_db.add(
        Payment(
            bill_id=bill.id,
            amount=bill.amount,
            currency="EUR",
            status="pending",
        )
    )
    await financial_db.commit()

    result = await reclassify_existing_nonpayable_bills(financial_db)
    assert result["reclassified"] == 0
    assert result["skipped_active_payment"] == 1
    await financial_db.refresh(bill)
    assert bill.status == "requires_review"


@pytest.mark.asyncio
async def test_receipt_matches_unique_real_bank_transaction(financial_db, monkeypatch) -> None:
    connection = BankConnection(
        institution_country="BE",
        institution_name="Test Bank",
        psu_type="personal",
        session_id_encrypted="encrypted-session",
        status="active",
    )
    financial_db.add(connection)
    await financial_db.flush()
    account = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="account-1",
        account_scope="personal",
        name="Main",
        iban="BE00000000000000",
        currency="EUR",
    )
    financial_db.add(account)
    now = datetime.utcnow()
    receipt = FinancialRecord(
        source_message_id="receipt-match-1",
        record_type="paid_receipt",
        provider_name="Google Commerce Limited",
        description="Google Play purchase",
        order_number="GPA.9999-8888-7777-66666",
        amount=Decimal("3.19"),
        currency="EUR",
        occurred_at=now,
        status="paid",
    )
    financial_db.add(receipt)
    await financial_db.commit()

    async def fake_transactions(db, account_id, date_from=None):
        assert account_id == "account-1"
        return {
            "transactions": [
                {
                    "transaction_id": "tx-google-1",
                    "transaction_amount": {"amount": "-3.19", "currency": "EUR"},
                    "booking_date": now.date().isoformat(),
                    "merchant_name": "GOOGLE",
                }
            ]
        }

    monkeypatch.setattr(
        "app.services.financial_reconciliation.enable_banking.get_account_transactions",
        fake_transactions,
    )
    result = await reconcile_receipts_with_bank_transactions(financial_db)
    assert result["matched"] == 1
    await financial_db.refresh(receipt)
    assert receipt.status == "reconciled"
    assert receipt.matched_bank_account_id == account.id
    assert receipt.matched_transaction_id == "tx-google-1"
