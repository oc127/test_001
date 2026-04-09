from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import get_settings


engine = create_async_engine(get_settings().DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    """Create all tables and seed VAT config."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed VAT rates
    from models import VatConfig
    async with async_session() as session:
        for marketplace, rate in get_settings().VAT_RATES.items():
            existing = await session.get(VatConfig, marketplace)
            if not existing:
                session.add(VatConfig(marketplace=marketplace, standard_rate=rate))
        await session.commit()
