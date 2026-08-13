from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Import every additive model module explicitly so metadata registration never
    # depends on FastAPI router import order.
    from app.models.entities import Base as EntityBase
    import app.models.telephony_entities  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(EntityBase.metadata.create_all)
