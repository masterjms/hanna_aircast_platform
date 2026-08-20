"""단말 라우터."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import Db, Publisher, Scope, SuperAdmin
from app.modules.device import service
from app.modules.system import service as system_service
from app.mqtt.topics import normalize_mac
from app.schemas.device import (
    DeviceCreate,
    DeviceDetail,
    DeviceOut,
    DeviceStatusFilter,
    DeviceUpdate,
)

router = APIRouter(prefix="/api/devices", tags=["device"])


def _mac_path(mac: str) -> str:
    """경로의 MAC 을 정규화한다. 콜론 표기로 들어와도 받아준다."""
    return normalize_mac(mac)


MacPath = Annotated[str, Depends(_mac_path)]


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    db: Db,
    scope: Scope,
    village_id: Annotated[int | None, Query()] = None,
    zone_id: Annotated[int | None, Query()] = None,
    device_status: Annotated[DeviceStatusFilter | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[DeviceOut]:
    return await service.list_devices(
        db, scope, village_id=village_id, zone_id=zone_id, status_filter=device_status, q=q
    )


@router.get("/unassigned", response_model=list[DeviceOut])
async def list_unassigned(db: Db, _: SuperAdmin) -> list[DeviceOut]:
    """미배정 단말. 마을이 없는 단말은 super_admin 만 다룰 수 있다.

    ⚠ 라우트 순서 주의 — /{mac} 보다 위에 있어야 'unassigned' 가 MAC 으로 잡히지 않는다.
    """
    return await service.list_unassigned(db)


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(payload: DeviceCreate, db: Db, scope: Scope) -> DeviceOut:
    return await service.create_device(db, payload, scope)


@router.get("/{mac}", response_model=DeviceDetail)
async def get_device(mac: MacPath, db: Db, scope: Scope) -> DeviceDetail:
    return await service.get_device(db, mac, scope)


@router.patch("/{mac}", response_model=DeviceDetail)
async def update_device(
    mac: MacPath,
    payload: DeviceUpdate,
    db: Db,
    scope: Scope,
    publisher: Publisher,
) -> DeviceDetail:
    """별칭 · 마을 · 구역 수정. 마을이 바뀌면 CONFIG 를 단말에 내려보낸다."""
    version = await system_service.config_version(db)
    return await service.update_device(
        db, mac, payload, scope, publisher, config_version=version
    )


@router.delete("/{mac}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    mac: MacPath,
    db: Db,
    scope: Scope,
    publisher: Publisher,
) -> None:
    await service.delete_device(db, mac, scope, publisher)
