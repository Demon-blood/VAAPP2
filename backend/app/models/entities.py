from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fcm_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthConnection(Base):
    __tablename__ = "oauth_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    account_key: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("provider", "account_key", name="uq_connection_provider_account"),)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(120), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    thread_id: Mapped[str] = mapped_column(String(255), index=True)
    sender: Mapped[str] = mapped_column(Text, default="")
    recipients: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(120), default="unclassified")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    action_required: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="new")
    analysis_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(40), default="manual")
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(40), default="open")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Creditor(Base):
    __tablename__ = "creditors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    iban: Mapped[str] = mapped_column(String(34), unique=True, index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    auto_pay_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_auto_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    normal_min_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    normal_max_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    creditor_id: Mapped[int | None] = mapped_column(ForeignKey("creditors.id"), nullable=True)
    creditor_name: Mapped[str] = mapped_column(String(255))
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reference: Mapped[str] = mapped_column(Text, default="")
    invoice_number: Mapped[str] = mapped_column(String(120), default="")
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    status: Mapped[str] = mapped_column(String(40), default="detected")
    risk_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    creditor: Mapped[Creditor | None] = relationship()

    __table_args__ = (
        Index("ix_bills_duplicate_check", "creditor_name", "amount", "invoice_number"),
    )


class BankConnection(Base):
    __tablename__ = "bank_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="enable_banking")
    institution_country: Mapped[str] = mapped_column(String(2))
    institution_name: Mapped[str] = mapped_column(String(120))
    psu_type: Mapped[str] = mapped_column(String(20), default="personal")
    session_id_encrypted: Mapped[str] = mapped_column(Text)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_connection_id: Mapped[int] = mapped_column(ForeignKey("bank_connections.id"), index=True)
    external_account_id: Mapped[str] = mapped_column(String(255), unique=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    name: Mapped[str] = mapped_column(String(255), default="")
    iban: Mapped[str] = mapped_column(String(34), default="")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    current_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    available_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    safety_reserve: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    enabled_for_payments: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id"), nullable=True)
    external_payment_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_user_action: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(255))
    conditions_json: Mapped[str] = mapped_column(Text, default="{}")
    actions_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(120), default="")
    result: Mapped[str] = mapped_column(String(40), default="success")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), default="email")
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(120), default="general")
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    drive_file_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    drive_web_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "checksum_sha256", name="uq_document_source_checksum"),
    )


class ContactRecord(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    emails_json: Mapped[str] = mapped_column(Text, default="[]")
    phones_json: Mapped[str] = mapped_column(Text, default="[]")
    organization: Mapped[str] = mapped_column(String(255), default="")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SupportCase(Base):
    __tablename__ = "support_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    requester: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(120), default="general")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(40), default="open")
    last_action: Mapped[str] = mapped_column(Text, default="")
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[str] = mapped_column(String(255), index=True)
    merchant: Mapped[str] = mapped_column(String(255), default="")
    order_number: Mapped[str] = mapped_column(String(160), default="", index=True)
    status: Mapped[str] = mapped_column(String(60), default="detected")
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    expected_delivery_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tracking_url: Mapped[str] = mapped_column(Text, default="")
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("merchant", "order_number", name="uq_order_merchant_number"),
    )


class SubscriptionRecord(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[str] = mapped_column(String(255), index=True)
    provider_name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    billing_cycle: Mapped[str] = mapped_column(String(40), default="unknown")
    next_charge_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("provider_name", "description", "account_scope", name="uq_subscription_identity"),
    )


class FinancialRecord(Base):
    __tablename__ = "financial_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    record_type: Mapped[str] = mapped_column(String(40), index=True)
    provider_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    order_number: Mapped[str] = mapped_column(String(160), default="", index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="recorded", index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal")
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True, index=True)
    matched_bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id"), nullable=True, index=True)
    matched_transaction_id: Mapped[str] = mapped_column(String(255), default="")
    matched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    provider_transaction_id: Mapped[str] = mapped_column(String(255))
    booking_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    value_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    direction: Mapped[str] = mapped_column(String(10), default="debit", index=True)
    counterparty_name: Mapped[str] = mapped_column(String(255), default="")
    counterparty_iban: Mapped[str] = mapped_column(String(34), default="")
    remittance: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="other", index=True)
    is_internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("bank_account_id", "provider_transaction_id", name="uq_bank_transaction_account_provider_id"),
        Index("ix_bank_transactions_budget_window", "bank_account_id", "booking_date", "direction"),
    )


class BankStatementImport(Base):
    __tablename__ = "bank_statement_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), default="beobank", index=True)
    statement_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_filename: Mapped[str] = mapped_column(Text, default="")
    account_iban: Mapped[str] = mapped_column(String(34), index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal", index=True)
    matched_bank_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_accounts.id"), nullable=True, index=True
    )
    statement_number: Mapped[int] = mapped_column(Integer, default=0)
    statement_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    total_credits: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    total_debits: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_status: Mapped[str] = mapped_column(String(40), default="verified", index=True)
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class HistoricalFinancialTransaction(Base):
    __tablename__ = "historical_financial_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_import_id: Mapped[int] = mapped_column(
        ForeignKey("bank_statement_imports.id", ondelete="CASCADE"), index=True
    )
    transaction_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    account_iban: Mapped[str] = mapped_column(String(34), index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal", index=True)
    booking_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    value_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    direction: Mapped[str] = mapped_column(String(10), index=True)
    transaction_type: Mapped[str] = mapped_column(String(120), default="")
    counterparty_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    counterparty_iban: Mapped[str] = mapped_column(String(34), default="")
    remittance: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="other", index=True)
    income_kind: Mapped[str] = mapped_column(String(40), default="", index=True)
    is_internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    merchant_occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    original_currency: Mapped[str] = mapped_column(String(3), default="")
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    matched_bank_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_transactions.id"), nullable=True, unique=True, index=True
    )
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "statement_import_id",
            "sequence_number",
            name="uq_historical_statement_sequence",
        ),
        Index(
            "ix_historical_budget_window",
            "account_scope",
            "booking_date",
            "direction",
        ),
    )


class InvestmentPortfolio(Base):
    __tablename__ = "investment_portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), default="revolut_securities", index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal", index=True)
    portfolio_kind: Mapped[str] = mapped_column(String(40), default="brokerage", index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    external_account_ref: Mapped[str] = mapped_column(Text, default="")
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    source_checksum_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "account_scope", "portfolio_kind", name="uq_investment_portfolio_provider_scope_kind"),
    )


class InvestmentPosition(Base):
    __tablename__ = "investment_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("investment_portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    company: Mapped[str] = mapped_column(String(255), default="")
    isin: Mapped[str] = mapped_column(String(32), default="", index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(Numeric(28, 8), default=Decimal("0"))
    market_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    allocation_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"))
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", "currency", name="uq_investment_position_portfolio_symbol_currency"),
    )


class InvestmentTransaction(Base):
    __tablename__ = "investment_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("investment_portfolios.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    booked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(40), default="", index=True)
    transaction_type: Mapped[str] = mapped_column(String(80), default="", index=True)
    side: Mapped[str] = mapped_column(String(10), default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR", index=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_investment_transactions_portfolio_date", "portfolio_id", "booked_at"),
    )


class InvestmentPnLEvent(Base):
    __tablename__ = "investment_pnl_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("investment_portfolios.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    date_acquired: Mapped[datetime] = mapped_column(DateTime, index=True)
    date_sold: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    security_name: Mapped[str] = mapped_column(String(255), default="")
    isin: Mapped[str] = mapped_column(String(32), default="", index=True)
    country: Mapped[str] = mapped_column(String(8), default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), default=Decimal("0"))
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    gross_proceeds: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InvestmentIncomeEvent(Base):
    __tablename__ = "investment_income_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("investment_portfolios.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    booked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(40), default="", index=True)
    security_name: Mapped[str] = mapped_column(String(255), default="")
    isin: Mapped[str] = mapped_column(String(32), default="", index=True)
    country: Mapped[str] = mapped_column(String(8), default="")
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    withholding_tax: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InvestmentFundingTransfer(Base):
    __tablename__ = "investment_funding_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="kraken", index=True)
    source_bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    recipient_name: Mapped[str] = mapped_column(String(255), default="")
    creditor_iban: Mapped[str] = mapped_column(String(34), default="")
    reference: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="creating", index=True)
    external_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_user_action: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pre_provider_cash: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    observed_provider_cash: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    provider_deposit_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    trade_pair: Mapped[str] = mapped_column(String(40), default="")
    trade_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BudgetEnvelope(Base):
    __tablename__ = "budget_envelopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal", index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    reserve_target: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    income_allocation_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    priority: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("account_scope", "category", name="uq_budget_envelope_scope_category"),
    )


class BankAutopilotPolicy(Base):
    __tablename__ = "bank_autopilot_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(30), default="operating", index=True)
    internal_transfers_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    target_floor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    target_ceiling: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    accept_surplus: Mapped[bool] = mapped_column(Boolean, default=False)
    monthly_outbound_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("5000.00"))
    min_transfer_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("50.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OwnAccountTransfer(Base):
    __tablename__ = "own_account_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    destination_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    reason: Mapped[str] = mapped_column(Text, default="budget_rebalancing")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    external_payment_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="planned", index=True)
    authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_user_action: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_own_transfer_source_status", "source_account_id", "status"),
    )


class CommunicationEvent(Base):
    __tablename__ = "communication_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="device", index=True)
    package_name: Mapped[str] = mapped_column(String(255), default="")
    thread_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    sender: Mapped[str] = mapped_column(Text, default="")
    recipient: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    direction: Mapped[str] = mapped_column(String(20), default="incoming", index=True)
    event_type: Mapped[str] = mapped_column(String(40), default="message", index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(120), default="unclassified", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    action_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="received", index=True)
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CommunicationAction(Base):
    __tablename__ = "communication_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("communication_events.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    requires_user_action: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CommunicationRule(Base):
    __tablename__ = "communication_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    contact_key: Mapped[str] = mapped_column(String(255), index=True)
    disposition: Mapped[str] = mapped_column(String(30), default="allow")
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(40), default="learned")
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0000"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("channel", "contact_key", name="uq_communication_rule_channel_contact"),
    )


class AIUsageDaily(Base):
    __tablename__ = "ai_usage_daily"

    day_key: Mapped[str] = mapped_column(String(10), primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    backfill_requests: Mapped[int] = mapped_column(Integer, default=0)
    rate_limit_count: Mapped[int] = mapped_column(Integer, default=0)
    deferred_count: Mapped[int] = mapped_column(Integer, default=0)
    rule_shortcuts: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint_hits: Mapped[int] = mapped_column(Integer, default=0)
    provider_remaining_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_remaining_tokens_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SenderRule(Base):
    __tablename__ = "sender_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_key: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(120))
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    preserve: Mapped[bool] = mapped_column(Boolean, default=False)
    archive: Mapped[bool] = mapped_column(Boolean, default=False)
    trash_when_read: Mapped[bool] = mapped_column(Boolean, default=False)
    labels_json: Mapped[str] = mapped_column(Text, default="[]")
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    safe_shortcut: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MessageFingerprint(Base):
    __tablename__ = "message_fingerprints"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_message_id: Mapped[str] = mapped_column(String(255), index=True)
    decision_json: Mapped[str] = mapped_column(Text)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    value_encrypted: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ServiceConnector(Base):
    __tablename__ = "service_connectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(80), default="other")
    connector_type: Mapped[str] = mapped_column(String(80), index=True)
    config_json_encrypted: Mapped[str] = mapped_column(Text, default="")
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40), default="not_configured")
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)




class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_type: Mapped[str] = mapped_column(String(120), index=True)
    correlation_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    intent_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(120), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_owner: Mapped[str] = mapped_column(String(255), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_workflow_jobs_due", "status", "run_after", "priority"),
    )


class WorkflowJobDependency(Base):
    __tablename__ = "workflow_job_dependencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("workflow_jobs.id", ondelete="CASCADE"), index=True)
    depends_on_job_id: Mapped[int] = mapped_column(ForeignKey("workflow_jobs.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("job_id", "depends_on_job_id", name="uq_workflow_job_dependency"),
    )



# v0.9.0 Autonomous Core ------------------------------------------------------
# Additive tables only. Existing deployments use metadata.create_all(), so the
# autonomous core can be introduced without mutating legacy task/workflow rows.


class VAEvent(Base):
    __tablename__ = "va_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VAObjective(Base):
    __tablename__ = "va_objectives"

    id: Mapped[int] = mapped_column(primary_key=True)
    correlation_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source_event_id: Mapped[int | None] = mapped_column(ForeignKey("va_events.id"), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(80), default="manual", index=True)
    source_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    title: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(120), default="general", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    status: Mapped[str] = mapped_column(String(40), default="detected", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    needs_user_reason: Mapped[str] = mapped_column(Text, default="")
    user_intervention_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VAObjectiveStep(Base):
    __tablename__ = "va_objective_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    objective_id: Mapped[int] = mapped_column(ForeignKey("va_objectives.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(120), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    policy_json: Mapped[str] = mapped_column(Text, default="{}")
    capability_json: Mapped[str] = mapped_column(Text, default="{}")
    verification_type: Mapped[str] = mapped_column(String(120), default="internal_state")
    workflow_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True, index=True)
    external_ref: Mapped[str] = mapped_column(Text, default="")
    outcome_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("objective_id", "position", name="uq_va_objective_step_position"),
        Index("ix_va_objective_steps_due", "status", "run_after", "objective_id"),
    )


class VAOutcomeEvidence(Base):
    __tablename__ = "va_outcome_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    objective_id: Mapped[int] = mapped_column(ForeignKey("va_objectives.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("va_objective_steps.id", ondelete="CASCADE"), nullable=True, index=True)
    evidence_type: Mapped[str] = mapped_column(String(120), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="")
    external_ref: Mapped[str] = mapped_column(Text, default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class VAFollowUp(Base):
    __tablename__ = "va_follow_ups"

    id: Mapped[int] = mapped_column(primary_key=True)
    objective_id: Mapped[int] = mapped_column(ForeignKey("va_objectives.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(40), default="internal", index=True)
    target: Mapped[str] = mapped_column(Text, default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    recurrence_hours: Mapped[int] = mapped_column(Integer, default=48)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    last_external_ref: Mapped[str] = mapped_column(Text, default="")
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AutonomyMetricDaily(Base):
    __tablename__ = "autonomy_metrics_daily"

    day_key: Mapped[str] = mapped_column(String(10), primary_key=True)
    events_ingested: Mapped[int] = mapped_column(Integer, default=0)
    objectives_created: Mapped[int] = mapped_column(Integer, default=0)
    objectives_completed: Mapped[int] = mapped_column(Integer, default=0)
    user_interventions: Mapped[int] = mapped_column(Integer, default=0)
    provider_failures: Mapped[int] = mapped_column(Integer, default=0)
    automatic_recoveries: Mapped[int] = mapped_column(Integer, default=0)
    followups_due: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OperationPreference(Base):
    __tablename__ = "operation_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(80), index=True)
    preference_key: Mapped[str] = mapped_column(String(255), index=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0000"))
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(80), default="explicit")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("domain", "preference_key", name="uq_operation_preference"),
    )


# v0.9.1 Inbox & Communications Ownership -----------------------------------
# These are additive tables so existing deployments can continue to use
# metadata.create_all() without mutating the older communication/task schemas.


class GmailMailboxState(Base):
    __tablename__ = "gmail_mailbox_states"

    account_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    history_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    watch_topic: Mapped[str] = mapped_column(Text, default="")
    watch_expiration_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_history_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_watch_renewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VACommunicationThread(Base):
    __tablename__ = "va_communication_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="", index=True)
    thread_key: Mapped[str] = mapped_column(String(255), index=True)
    objective_id: Mapped[int | None] = mapped_column(ForeignKey("va_objectives.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    participant: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    waiting_on: Mapped[str] = mapped_column(String(40), default="va", index=True)
    last_message_ref: Mapped[str] = mapped_column(String(255), default="")
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("channel", "provider", "thread_key", name="uq_va_communication_thread"),
        Index("ix_va_communication_threads_followup", "status", "waiting_on", "next_follow_up_at"),
    )


class GmailOutboundMessage(Base):
    __tablename__ = "gmail_outbound_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    objective_id: Mapped[int | None] = mapped_column(ForeignKey("va_objectives.id"), nullable=True, index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("va_objective_steps.id"), nullable=True, index=True)
    source_message_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    gmail_thread_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    recipient: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    rfc_message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    in_reply_to: Mapped[str] = mapped_column(Text, default="")
    references: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="prepared", index=True)
    external_message_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    external_thread_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    verify_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CommunicationDeliveryEvidence(Base):
    __tablename__ = "communication_delivery_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    communication_action_id: Mapped[int] = mapped_column(ForeignKey("communication_actions.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(80), index=True)
    external_ref: Mapped[str] = mapped_column(Text, default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("communication_action_id", "evidence_type", name="uq_communication_delivery_evidence"),
    )


# v0.9.2 Calendar & Scheduling Agent ----------------------------------------
# Durable Google Calendar ownership. The provider mirror and mutation ledger are
# additive; existing deployments continue to use metadata.create_all().


class CalendarSyncState(Base):
    __tablename__ = "calendar_sync_states"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    last_sync_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_event_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CalendarEventMirror(Base):
    __tablename__ = "calendar_event_mirrors"

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary", index=True)
    provider_event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ical_uid: Mapped[str] = mapped_column(String(255), default="", index=True)
    status: Mapped[str] = mapped_column(String(40), default="confirmed", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    start_raw: Mapped[str] = mapped_column(Text, default="")
    end_raw: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(120), default="Europe/Brussels")
    attendees_json: Mapped[str] = mapped_column(Text, default="[]")
    organizer_json: Mapped[str] = mapped_column(Text, default="{}")
    html_link: Mapped[str] = mapped_column(Text, default="")
    etag: Mapped[str] = mapped_column(Text, default="")
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    owned_objective_id: Mapped[int | None] = mapped_column(ForeignKey("va_objectives.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_calendar_event_mirrors_upcoming", "status", "start_at", "end_at"),
    )


class CalendarMutation(Base):
    __tablename__ = "calendar_mutations"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    objective_id: Mapped[int | None] = mapped_column(ForeignKey("va_objectives.id"), nullable=True, index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("va_objective_steps.id"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(20), default="create", index=True)
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    provider_event_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    desired_event_json: Mapped[str] = mapped_column(Text, default="{}")
    observed_event_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    etag: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    verify_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# v0.9.3 CRM / Relationship Memory -----------------------------------------
# Canonical people, source-backed identities, interaction history and factual
# memory are additive. Identity uniqueness is global so cross-channel evidence
# can safely converge on one real person without name-based guessing.


class RelationshipMemoryState(Base):
    __tablename__ = "relationship_memory_states"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    last_reconcile_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    profile_count: Mapped[int] = mapped_column(Integer, default=0)
    identity_count: Mapped[int] = mapped_column(Integer, default=0)
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RelationshipProfile(Base):
    __tablename__ = "relationship_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    organization: Mapped[str] = mapped_column(String(255), default="", index=True)
    primary_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    primary_phone: Mapped[str] = mapped_column(String(80), default="", index=True)
    preferred_channel: Mapped[str] = mapped_column(String(40), default="", index=True)
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    engagement_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    waiting_on_counterparty: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    memory_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_relationship_profiles_followup", "waiting_on_counterparty", "next_follow_up_at"),
    )


class RelationshipIdentity(Base):
    __tablename__ = "relationship_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    relationship_id: Mapped[int] = mapped_column(ForeignKey("relationship_profiles.id", ondelete="CASCADE"), index=True)
    identity_type: Mapped[str] = mapped_column(String(40), index=True)
    normalized_value: Mapped[str] = mapped_column(String(320), index=True)
    display_value: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(80), default="observed", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("identity_type", "normalized_value", name="uq_relationship_identity_global"),
        Index("ix_relationship_identity_profile_type", "relationship_id", "identity_type"),
    )


class RelationshipInteraction(Base):
    __tablename__ = "relationship_interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    relationship_id: Mapped[int] = mapped_column(ForeignKey("relationship_profiles.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    source_ref: Mapped[str] = mapped_column(String(320), index=True)
    channel: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    direction: Mapped[str] = mapped_column(String(20), default="shared", index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("relationship_id", "source_type", "source_ref", name="uq_relationship_interaction_source"),
        Index("ix_relationship_interaction_timeline", "relationship_id", "occurred_at"),
    )


class RelationshipFact(Base):
    __tablename__ = "relationship_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    relationship_id: Mapped[int] = mapped_column(ForeignKey("relationship_profiles.id", ondelete="CASCADE"), index=True)
    fact_key: Mapped[str] = mapped_column(String(120), index=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    source_ref: Mapped[str] = mapped_column(String(320), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0000"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint(
            "relationship_id",
            "fact_key",
            "source_type",
            "source_ref",
            name="uq_relationship_fact_provenance",
        ),
    )
