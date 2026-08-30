from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import BankConnection, OAuthConnection, VAObjective, VAOutcomeEvidence
from app.services.operational_guardian import run_operational_guardian


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_expiring_bank_consent_creates_real_needs_user_and_provider_renewal_verifies_completion(db):
    connection = BankConnection(
        provider="enable_banking",
        institution_country="BE",
        institution_name="Test Bank",
        psu_type="personal",
        session_id_encrypted="encrypted-session",
        valid_until=datetime.utcnow() + timedelta(hours=18),
        status="active",
    )
    db.add(connection)
    await db.commit()

    first = await run_operational_guardian(db)
    assert first["needs_user_count"] == 1
    objective = (
        await db.execute(
            select(VAObjective).where(
                VAObjective.source_type == "operational_guardian",
                VAObjective.source_id == f"bank_connection:{connection.id}",
            )
        )
    ).scalar_one()
    assert objective.status == "needs_user"
    assert "account-holder authorization" in objective.needs_user_reason

    connection.valid_until = datetime.utcnow() + timedelta(days=90)
    await db.commit()
    second = await run_operational_guardian(db)
    await db.refresh(objective)
    assert second["needs_user_count"] == 0
    assert objective.status == "completed"
    evidence = (
        await db.execute(select(VAOutcomeEvidence).where(VAOutcomeEvidence.objective_id == objective.id))
    ).scalar_one()
    assert evidence.evidence_type == "provider_state"
    assert evidence.provider == "enable_banking"


@pytest.mark.asyncio
async def test_refreshable_oauth_expiry_does_not_create_fake_user_work(db):
    oauth = OAuthConnection(
        provider="google",
        account_key="test@example.invalid",
        display_name="Test",
        access_token_encrypted="access",
        refresh_token_encrypted="refresh",
        expires_at=datetime.utcnow() + timedelta(minutes=15),
        scope="gmail calendar",
        enabled=True,
    )
    db.add(oauth)
    await db.commit()

    result = await run_operational_guardian(db)
    assert result["oauth"]["refreshable_expiries"] == 1
    assert result["oauth"]["reconnect_required"] == []
    rows = list((await db.execute(select(VAObjective).where(VAObjective.source_type == "operational_guardian"))).scalars())
    assert rows == []


@pytest.mark.asyncio
async def test_non_refreshable_oauth_expiry_is_a_genuine_provider_authorization_boundary(db):
    oauth = OAuthConnection(
        provider="google",
        account_key="no-refresh@example.invalid",
        display_name="Test",
        access_token_encrypted="access",
        refresh_token_encrypted=None,
        expires_at=datetime.utcnow() + timedelta(minutes=30),
        scope="gmail calendar",
        enabled=True,
    )
    db.add(oauth)
    await db.commit()

    result = await run_operational_guardian(db)
    assert result["needs_user_count"] == 1
    objective = (
        await db.execute(
            select(VAObjective).where(
                VAObjective.source_type == "operational_guardian",
                VAObjective.source_id == f"oauth_connection:{oauth.id}",
            )
        )
    ).scalar_one()
    assert objective.status == "needs_user"
    assert objective.category == "operational_continuity"
    assert "provider authorization" in objective.needs_user_reason
