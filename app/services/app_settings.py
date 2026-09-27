"""
Runtime-configurable app settings backed by the `app_settings` key/value table.

Unlike every sibling app, there's no roster-sync watermark to hold here — Alumni runs no
legion_sync job (see config.py's `legion_base_url` docstring). Kept as a generic
key/value store in case a future setting wants one rather than its own column.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting


async def get_setting(db: AsyncSession, key: str) -> Optional[str]:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalars().first()
    return row.value if row else None


async def set_setting(db: AsyncSession, key: str, value: Optional[str]) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalars().first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()
