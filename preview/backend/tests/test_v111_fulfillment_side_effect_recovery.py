from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import BrowserOperation, BrowserPortal
from app.models.fulfillment_entities import FulfillmentAction, FulfillmentProvider, FulfillmentRequest
from app.services import browser_operator, fulfillment_service


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _uncertain_fixture(db):
    portal = BrowserPortal(
        slug="example-provider",
        name="Example Provider",
        base_url="https://example.invalid/",
        login_url="",
        allowed_hosts_json='["example.invalid"]',
        account_scope="personal",
        enabled=True,
    )
    db.add(portal)
    await db.flush()
    provider = FulfillmentProvider(
        slug="example-provider",
        name="Example Provider",
        provider_type="merchant",
        browser_portal_id=portal.id,
        account_scope="personal",
        enabled=True,
    )
    db.add(provider)
    await db.flush()
    request = FulfillmentRequest(
        idempotency_key="test-v111-cancel-request",
        request_type="cancel",
        provider_id=provider.id,
        account_scope="personal",
        title="Cancel provider order",
        goal_encrypted="",
        details_encrypted="",
        currency="EUR",
        status="dispatching",
        next_action_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(request)
    await db.flush()
    operation = BrowserOperation(
        idempotency_key="fulfillment-v111-operation",
        portal_id=portal.id,
        title="Cancel provider order",
        plan_json='{"step_count":1,"kinds":["click_action"],"material_commitment":false}',
        verification_json='{"keys":["text_contains"]}',
        status="creation_uncertain",
        current_step=1,
        side_effect_step=0,
        side_effect_started_at=datetime.now(UTC).replace(tzinfo=None),
        last_error="Provider postcondition is not yet verified",
    )
    db.add(operation)
    await db.flush()
    action = FulfillmentAction(
        request_id=request.id,
        sequence=1,
        idempotency_key="fulfillment:1:action:1:browser_operation",
        action_type="browser_operation",
        status="dispatching",
        browser_operation_id=operation.id,
    )
    db.add(action)
    await db.commit()
    return request, action, operation


def test_completed_non_replay_safe_step_stays_in_reconciliation_mode():
    operation = BrowserOperation(
        idempotency_key="v111-marker-check",
        portal_id=1,
        title="Marker check",
        status="running",
        current_step=2,
        side_effect_step=1,
        side_effect_started_at=datetime.now(UTC).replace(tzinfo=None),
    )

    assert browser_operator.operation_requires_postcondition_reconciliation(operation) is True
    operation.side_effect_step = None
    assert browser_operator.operation_requires_postcondition_reconciliation(operation) is False


@pytest.mark.asyncio
async def test_uncertain_side_effect_reuses_same_operation_without_fake_needs_you(db, monkeypatch):
    request, action, operation = await _uncertain_fixture(db)
    resumed: list[int] = []

    async def fake_resume(_db, operation_id: int):
        resumed.append(operation_id)
        operation.resume_sequence += 1
        operation.status = "pending"
        return operation

    monkeypatch.setattr(fulfillment_service, "resume_browser_operation", fake_resume)

    await fulfillment_service._reconcile_existing_action(db, request, action)
    await db.commit()

    assert resumed == [operation.id]
    assert action.status == "waiting_provider"
    assert request.status == "waiting_provider"
    assert request.requires_user_action is False
    assert request.needs_user_reason == ""
    assert request.next_action_at is not None
    assert operation.resume_sequence == 1
    same_action = await fulfillment_service._ensure_action(
        db, request, action_type="browser_operation", details={"provider_id": request.provider_id}
    )
    assert same_action.id == action.id
    count = int(
        (
            await db.execute(
                select(func.count(FulfillmentAction.id)).where(FulfillmentAction.request_id == request.id)
            )
        ).scalar_one()
    )
    assert count == 1


@pytest.mark.asyncio
async def test_uncertain_side_effect_without_safe_resume_stays_system_owned(db, monkeypatch):
    request, action, _ = await _uncertain_fixture(db)

    async def reject_resume(_db, _operation_id: int):
        raise ValueError("uncertain operation is missing its durable side-effect marker")

    monkeypatch.setattr(fulfillment_service, "resume_browser_operation", reject_resume)

    await fulfillment_service._reconcile_existing_action(db, request, action)
    await db.commit()

    assert action.status == "blocked_system"
    assert request.status == "blocked_system"
    assert request.requires_user_action is False
    assert request.needs_user_reason == ""
    assert request.next_action_at is not None
