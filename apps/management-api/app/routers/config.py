from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import SourceEnum
from app.schemas.config import ConfigRead, ConfigUpdate
from app.services import config_service

router = APIRouter(prefix="/api/v1/config", tags=["config"])

VALID_SOURCES = {s.value for s in SourceEnum}


@router.get("")
async def get_all(db: AsyncSession = Depends(get_db)):
    configs = await config_service.get_all_configs(db)
    grouped = {}
    for c in configs:
        grouped.setdefault(c.source, []).append(ConfigRead.model_validate(c))
    return grouped


@router.get("/{source}")
async def get_by_source(source: str, db: AsyncSession = Depends(get_db)):
    if source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail={"error": "Invalid source", "code": "invalid_source"})
    configs = await config_service.get_configs_by_source(db, source)
    return [ConfigRead.model_validate(c) for c in configs]


@router.get("/{source}/{key}")
async def get_one(source: str, key: str, db: AsyncSession = Depends(get_db)):
    cfg = await config_service.get_config(db, source, key)
    if cfg is None:
        raise HTTPException(status_code=404, detail={"error": "Config not found", "code": "not_found"})
    return ConfigRead.model_validate(cfg)


@router.put("/{source}/{key}")
async def upsert(source: str, key: str, body: ConfigUpdate, db: AsyncSession = Depends(get_db)):
    if source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail={"error": "Invalid source", "code": "invalid_source"})
    cfg = await config_service.upsert_config(db, source, key, body.value)
    await db.commit()
    await db.refresh(cfg)
    return ConfigRead.model_validate(cfg)


@router.delete("/{source}/{key}")
async def delete_one(source: str, key: str, db: AsyncSession = Depends(get_db)):
    deleted = await config_service.delete_config(db, source, key)
    if not deleted:
        raise HTTPException(status_code=404, detail={"error": "Config not found", "code": "not_found"})
    await db.commit()
    return {"deleted": True}
