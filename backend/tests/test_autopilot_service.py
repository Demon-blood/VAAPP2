from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import AuditLog, BankAccount, Bill, Creditor, OperationPreference, SenderRule, WorkflowJob, WorkflowJobDependency, WorkflowRun
from app.services.autopilot_service import dispatch_intent, operations_profile


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (WorkflowRun, WorkflowJob, WorkflowJobDependency, AuditLog, OperationPreference, SenderRule, Creditor, BankAccount, Bill):
            await connection.run_sync(table.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_gmail_intent_is_durable(db):
    result = await dispatch_intent(
        db,
        {"type": "sync_gmail", "correlation_key": "test:gmail:1"},
    )
    assert result["created"] is True
    job = await db.get(WorkflowJob, result["job_id"])
    assert job is not None
    assert job.job_type == "gmail.sync"
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_explicit_operation_preference_is_returned(db):
    db.add(OperationPreference(domain="email", preference_key="vip", value_json='{"sender":"a@example.com"}'))
    await db.commit()
    # The profile also reads existing learned-policy tables in production. This focused
    # unit test validates explicit profile persistence without fabricating those records.
    profile = await operations_profile(db)
    assert profile["preferences"][0]["domain"] == "email"
    assert profile["preferences"][0]["value"]["sender"] == "a@example.com"
