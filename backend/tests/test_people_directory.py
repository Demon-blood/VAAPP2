from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import RelationshipFact, RelationshipProfile
from app.models.people_entities import ContactSourceRecord
from app.services.contact_directory import (
    category_hint,
    ingest_device_contact_snapshot,
    list_people_directory,
)
from app.services.relationship_preferences import set_relationship_communication_preferences


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
async def test_phone_book_same_name_contacts_do_not_merge_without_shared_identity(db):
    await ingest_device_contact_snapshot(
        db,
        device_id=1,
        snapshot_id="snapshot-0001",
        snapshot_complete=True,
        contacts=[
            {
                "external_id": "alex-one",
                "display_name": "Alex",
                "phones": ["+32470000001"],
                "emails": [],
                "groups": ["Friends"],
                "relations": [],
            },
            {
                "external_id": "alex-two",
                "display_name": "Alex",
                "phones": ["+32470000002"],
                "emails": [],
                "groups": ["Work"],
                "relations": [],
            },
        ],
    )

    profiles = list((await db.execute(select(RelationshipProfile))).scalars())
    assert len(profiles) == 2
    assert {row.primary_phone for row in profiles} == {"+32470000001", "+32470000002"}


@pytest.mark.asyncio
async def test_exact_phone_identity_converges_across_contact_sources(db):
    first = await ingest_device_contact_snapshot(
        db,
        device_id=1,
        snapshot_id="snapshot-device-one",
        snapshot_complete=True,
        contacts=[
            {
                "external_id": "phone-contact-a",
                "display_name": "Laura",
                "phones": ["+32470123456"],
                "emails": ["laura@example.com"],
                "groups": ["Family"],
                "relations": [{"type": "spouse", "person": "Owner"}],
            }
        ],
    )
    second = await ingest_device_contact_snapshot(
        db,
        device_id=2,
        snapshot_id="snapshot-device-two",
        snapshot_complete=True,
        contacts=[
            {
                "external_id": "phone-contact-b",
                "display_name": "Laura Mobile",
                "phones": ["+32470123456"],
                "emails": [],
                "groups": [],
                "relations": [],
            }
        ],
    )

    assert first["linked"] == 1
    assert second["linked"] == 1
    profiles = list((await db.execute(select(RelationshipProfile))).scalars())
    assert len(profiles) == 1
    rows = list((await db.execute(select(ContactSourceRecord))).scalars())
    assert len(rows) == 2
    assert {row.relationship_id for row in rows} == {profiles[0].id}


@pytest.mark.asyncio
async def test_groups_and_relations_are_context_and_configured_filter_is_explicit(db):
    await ingest_device_contact_snapshot(
        db,
        device_id=7,
        snapshot_id="snapshot-family",
        snapshot_complete=True,
        contacts=[
            {
                "external_id": "contact-mom",
                "display_name": "Mom",
                "phones": ["+32479999999"],
                "emails": [],
                "groups": ["Family"],
                "relations": [{"type": "mother", "person": "Mom"}],
                "starred": True,
            }
        ],
    )
    profile = (await db.execute(select(RelationshipProfile))).scalar_one()

    directory = await list_people_directory(db)
    assert directory["total"] == 1
    person = directory["people"][0]
    assert person["suggested_category"] == "family"
    assert person["relationship_category"] == "other"
    assert person["configured"] is False
    assert person["favorite"] is True

    await set_relationship_communication_preferences(
        db,
        profile.id,
        {
            "relationship_category": "family",
            "tone": "warm",
            "routine_auto_send": False,
        },
    )
    configured = await list_people_directory(db, filter_name="configured")
    assert configured["total"] == 1
    assert configured["people"][0]["relationship_category"] == "family"

    facts = list(
        (
            await db.execute(
                select(RelationshipFact).where(
                    RelationshipFact.relationship_id == profile.id,
                    RelationshipFact.fact_key.in_(["contact_groups", "contact_relations"]),
                )
            )
        ).scalars()
    )
    assert {fact.fact_key for fact in facts} == {"contact_groups", "contact_relations"}


def test_category_hint_uses_relationship_metadata_only_as_a_suggestion():
    category, evidence = category_hint(
        ["Coworkers", "Important"],
        [{"type": "colleague", "person": "Sam"}],
    )
    assert category == "colleague"
    assert "relation:colleague" in evidence
