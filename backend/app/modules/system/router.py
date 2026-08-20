"""시스템 라우터 — 헬스체크와 단말 공통 설정."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import Db, Publisher, SuperAdmin
from app.modules.system import service
from app.schemas.system import ConfigOut, ConfigUpdate, HealthOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
async def health(db: Db, publisher: Publisher) -> HealthOut:
    """인증 없이 열어둔다. 배포 스크립트와 모니터링이 부른다."""
    return await service.health(db, publisher)


@router.get("/api/config", response_model=ConfigOut)
async def get_config(db: Db, _: SuperAdmin) -> ConfigOut:
    return await service.get_config(db)


@router.put("/api/config", response_model=ConfigOut)
async def update_config(
    payload: ConfigUpdate,
    db: Db,
    _: SuperAdmin,
    publisher: Publisher,
) -> ConfigOut:
    return await service.update_config(db, payload, publisher)
