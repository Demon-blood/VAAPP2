from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    CalendarEventMirror,
    CommunicationEvent,
    ContactRecord,
    RelationshipIdentity,
    RelationshipInteraction,
    RelationshipProfile,
)
from app.services.relationship_memory import reconcile_relationship_memory, relationship_detail


@pytest.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_contact_phone_email_and_cross_channel_activity_converge_on_one_person(db):
    db.add(
        ContactRecord(
            resource_name='people/alice',
            display_name='Alice Example',
            emails_json=json.dumps(['alice@example.com']),
            phones_json=json.dumps(['+32 470 12 34 56']),
            organization='Example BV',
        )
    )
    db.add(
        CommunicationEvent(
            external_id='sms:alice:1',
            channel='sms',
            provider='android_sms',
            sender='+32470123456',
            recipient='me',
            body='Can we talk tomorrow?',
            direction='incoming',
            status='processed',
            occurred_at=datetime(2026, 8, 13, 10, 0),
        )
    )
    db.add(
        CalendarEventMirror(
            provider_event_id='calendar-alice-1',
            summary='Catch up',
            start_at=datetime(2026, 8, 14, 10, 0),
            end_at=datetime(2026, 8, 14, 10, 30),
            attendees_json=json.dumps([{'email': 'alice@example.com', 'responseStatus': 'accepted'}]),
        )
    )
    await db.commit()

    result = await reconcile_relationship_memory(db)
    assert result['profiles'] == 1
    profiles = list((await db.execute(select(RelationshipProfile))).scalars())
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.display_name == 'Alice Example'
    assert profile.organization == 'Example BV'
    assert profile.primary_email == 'alice@example.com'
    assert profile.interaction_count == 2

    identities = list((await db.execute(select(RelationshipIdentity))).scalars())
    assert {(row.identity_type, row.normalized_value) for row in identities} == {
        ('email', 'alice@example.com'),
        ('phone', '+32470123456'),
    }
    interactions = list((await db.execute(select(RelationshipInteraction))).scalars())
    assert {row.channel for row in interactions} == {'sms', 'calendar'}


@pytest.mark.asyncio
async def test_same_display_name_without_shared_identity_stays_separate(db):
    db.add_all(
        [
            ContactRecord(
                resource_name='people/alex-one',
                display_name='Alex',
                emails_json=json.dumps(['alex.one@example.com']),
            ),
            ContactRecord(
                resource_name='people/alex-two',
                display_name='Alex',
                emails_json=json.dumps(['alex.two@example.com']),
            ),
        ]
    )
    await db.commit()
    await reconcile_relationship_memory(db)
    profiles = list((await db.execute(select(RelationshipProfile).order_by(RelationshipProfile.id))).scalars())
    assert len(profiles) == 2
    assert {row.primary_email for row in profiles} == {'alex.one@example.com', 'alex.two@example.com'}


@pytest.mark.asyncio
async def test_protected_device_message_does_not_copy_raw_body_into_relationship_summary(db):
    db.add(
        CommunicationEvent(
            external_id='sms:protected:1',
            channel='sms',
            provider='android_sms',
            sender='+32479999999',
            recipient='me',
            body='Authentication code 123456',
            direction='incoming',
            protected=True,
            status='processed',
        )
    )
    await db.commit()
    await reconcile_relationship_memory(db)
    profile = (await db.execute(select(RelationshipProfile))).scalar_one()
    detail = await relationship_detail(db, profile.id)
    assert detail['recent_interactions'][0]['summary'] == 'Protected sms interaction'
    assert '123456' not in detail['memory_summary']
