from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import Config


async def get_all_configs(db: AsyncSession) -> list[Config]:
    result = await db.execute(select(Config).order_by(Config.source, Config.key))
    return list(result.scalars().all())


async def get_configs_by_source(db: AsyncSession, source: str) -> list[Config]:
    result = await db.execute(
        select(Config).where(Config.source == source).order_by(Config.key)
    )
    return list(result.scalars().all())


async def get_config(db: AsyncSession, source: str, key: str) -> Config | None:
    result = await db.execute(
        select(Config).where(Config.source == source, Config.key == key)
    )
    return result.scalar_one_or_none()


async def get_config_value(db: AsyncSession, source: str, key: str, default: Any = None) -> Any:
    cfg = await get_config(db, source, key)
    return cfg.value if cfg else default


async def upsert_config(db: AsyncSession, source: str, key: str, value: Any) -> Config:
    cfg = await get_config(db, source, key)
    if cfg is None:
        cfg = Config(source=source, key=key, value=value)
        db.add(cfg)
    else:
        cfg.value = value
    await db.flush()
    return cfg


async def delete_config(db: AsyncSession, source: str, key: str) -> bool:
    result = await db.execute(
        delete(Config).where(Config.source == source, Config.key == key)
    )
    return result.rowcount > 0
