from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class TelephonyCall(Base):
    __tablename__ = "telephony_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    series_key: Mapped[str] = mapped_column(String(255), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    provider: Mapped[str] = mapped_column(String(40), default="twilio", index=True)
    direction: Mapped[str] = mapped_column(String(20), default="outbound", index=True)
    objective_id: Mapped[int | None] = mapped_column(ForeignKey("va_objectives.id"), nullable=True, index=True)
    objective_step_id: Mapped[int | None] = mapped_column(ForeignKey("va_objective_steps.id"), nullable=True, index=True)
    parent_call_id: Mapped[int | None] = mapped_column(ForeignKey("telephony_calls.id"), nullable=True, index=True)
    external_call_sid: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True, index=True)
    webhook_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    webhook_token_encrypted: Mapped[str] = mapped_column(Text)
    target_hash: Mapped[str] = mapped_column(String(64), index=True)
    target_encrypted: Mapped[str] = mapped_column(Text)
    from_number_encrypted: Mapped[str] = mapped_column(Text)
    purpose_encrypted: Mapped[str] = mapped_column(Text)
    expected_outcome_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="creating", index=True)
    provider_status: Mapped[str] = mapped_column(String(40), default="")
    verification_status: Mapped[str] = mapped_column(String(40), default="unverified", index=True)
    last_sequence_number: Mapped[int] = mapped_column(Integer, default=-1)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    empty_turn_count: Mapped[int] = mapped_column(Integer, default=0)
    needs_user: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_user_reason: Mapped[str] = mapped_column(Text, default="")
    result_summary_encrypted: Mapped[str] = mapped_column(Text, default="")
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("series_key", "attempt", name="uq_telephony_series_attempt"),
        Index("ix_telephony_call_status_retry", "status", "next_retry_at"),
    )


class TelephonyTurn(Base):
    __tablename__ = "telephony_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("telephony_calls.id"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(30), index=True)
    transcript_encrypted: Mapped[str] = mapped_column(Text)
    transcript_sha256: Mapped[str] = mapped_column(String(64), index=True)
    provider_ref: Mapped[str] = mapped_column(String(255), default="")
    confidence: Mapped[str] = mapped_column(String(40), default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("call_id", "turn_index", name="uq_telephony_call_turn"),
        UniqueConstraint("call_id", "provider_ref", name="uq_telephony_call_provider_ref"),
    )


class TelephonyEvidence(Base):
    __tablename__ = "telephony_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("telephony_calls.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    provider_status: Mapped[str] = mapped_column(String(40), default="")
    external_ref: Mapped[str] = mapped_column(String(255), default="")
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
