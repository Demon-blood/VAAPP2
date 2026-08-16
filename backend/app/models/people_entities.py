from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class ContactSourceRecord(Base):
    """Source-specific contact metadata linked to a canonical relationship by exact identity."""

    __tablename__ = "contact_source_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_account: Mapped[str] = mapped_column(String(255), default="", index=True)
    source_id: Mapped[str] = mapped_column(String(320), index=True)
    relationship_id: Mapped[int | None] = mapped_column(
        ForeignKey("relationship_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    emails_json: Mapped[str] = mapped_column(Text, default="[]")
    phones_json: Mapped[str] = mapped_column(Text, default="[]")
    organization: Mapped[str] = mapped_column(String(255), default="", index=True)
    job_title: Mapped[str] = mapped_column(String(255), default="")
    department: Mapped[str] = mapped_column(String(255), default="")
    nickname: Mapped[str] = mapped_column(String(255), default="")
    groups_json: Mapped[str] = mapped_column(Text, default="[]")
    relations_json: Mapped[str] = mapped_column(Text, default="[]")
    starred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sync_marker: Mapped[str] = mapped_column(String(120), default="", index=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_account",
            "source_id",
            name="uq_contact_source_identity",
        ),
        Index(
            "ix_contact_source_relationship_active",
            "relationship_id",
            "active",
            "source_type",
        ),
    )
