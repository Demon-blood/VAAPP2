from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class FulfillmentProvider(Base):
    __tablename__ = "fulfillment_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    provider_type: Mapped[str] = mapped_column(String(40), default="merchant", index=True)
    browser_portal_id: Mapped[int | None] = mapped_column(ForeignKey("browser_portals.id"), nullable=True, index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal", index=True)
    support_phone_encrypted: Mapped[str] = mapped_column(Text, default="")
    recipe_encrypted: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FulfillmentRequest(Base):
    __tablename__ = "fulfillment_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    request_type: Mapped[str] = mapped_column(String(40), index=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_providers.id"), nullable=True, index=True)
    account_scope: Mapped[str] = mapped_column(String(30), default="personal", index=True)
    title: Mapped[str] = mapped_column(Text)
    goal_encrypted: Mapped[str] = mapped_column(Text, default="")
    details_encrypted: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(40), default="planned", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    source_type: Mapped[str] = mapped_column(String(60), default="manual", index=True)
    source_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    va_objective_id: Mapped[int | None] = mapped_column(ForeignKey("va_objectives.id"), nullable=True, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    support_case_id: Mapped[int | None] = mapped_column(ForeignKey("support_cases.id"), nullable=True, index=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    requires_user_action: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    needs_user_reason: Mapped[str] = mapped_column(Text, default="")
    material_authorized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    authorization_basis: Mapped[str] = mapped_column(String(80), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FulfillmentAction(Base):
    __tablename__ = "fulfillment_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("fulfillment_requests.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    action_type: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(40), default="planned", index=True)
    browser_operation_id: Mapped[int | None] = mapped_column(ForeignKey("browser_operations.id"), nullable=True, index=True)
    telephony_call_id: Mapped[int | None] = mapped_column(ForeignKey("telephony_calls.id"), nullable=True, index=True)
    details_encrypted: Mapped[str] = mapped_column(Text, default="")
    verification_type: Mapped[str] = mapped_column(String(80), default="provider_postcondition")
    last_error: Mapped[str] = mapped_column(Text, default="")
    run_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_fulfillment_request_sequence"),
    )


class FulfillmentEvidence(Base):
    __tablename__ = "fulfillment_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("fulfillment_requests.id"), index=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_actions.id"), nullable=True, index=True)
    evidence_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    evidence_type: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="")
    external_ref: Mapped[str] = mapped_column(String(500), default="")
    details_encrypted: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
