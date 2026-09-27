from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables. No Alembic, matching every sibling app — an additive column
    on an existing table needs an inspect-guarded `_migration_*(conn)` here (see
    Legion's database.py for the pattern)."""
    from app import models  # noqa: F401 — imported for side-effect (table registration)

    # Apply a staged database restore (if any) before the engine touches the file.
    from app.services.backup import apply_pending_restore
    apply_pending_restore()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migration_add_self_service_sent_at)


def _migration_add_self_service_sent_at(conn) -> None:
    """Add `alumni_surveys.self_service_sent_at`. No-op on a freshly created schema,
    which already has it."""
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(conn).get_columns("alumni_surveys")}
    if "self_service_sent_at" not in cols:
        conn.execute(text("ALTER TABLE alumni_surveys ADD COLUMN self_service_sent_at DATETIME"))
