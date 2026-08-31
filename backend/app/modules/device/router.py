"""단말 라우터."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import Db, Publisher, Scope, SuperAdmin
from app.core import mqtt_accounts
from app.modules.device import service
from app.modules.system import service as system_service
from app.mqtt.topics import normalize_mac
from app.schemas.device import (
    DeviceCreate,
    DeviceCredentialIssue,
    DeviceCredentialOut,
    DeviceDetail,
    DeviceOut,
    DeviceStatusFilter,
    DeviceUpdate,
    NewDevicePasswordOut,
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


@router.post("/credential", response_model=NewDevicePasswordOut)
async def new_device_password(_: SuperAdmin) -> NewDevicePasswordOut:
    """신규 단말 등록용 비밀번호 사전 발급 (모달을 여는 시점에 호출).

    서버가 아직 MAC 을 모르는 시점이라 값만 만들어 준다 — DB 에는 아무것도
    안 남고, 등록(POST /api/devices)의 mqtt_password 로 돌아와야 확정된다.

    ⚠ 라우트 순서 — /{mac}/credential 보다 위에 있어야 'credential' 이 MAC 으로
    잡히지 않는다 (/unassigned 와 같은 이유).
    """
    return NewDevicePasswordOut(
        password=mqtt_accounts.generate_device_password(),
        server_host=mqtt_accounts.server_host(),
    )


@router.post("/{mac}/credential", response_model=DeviceCredentialOut)
async def issue_credential(
    mac: MacPath, payload: DeviceCredentialIssue, db: Db, _: SuperAdmin
) -> DeviceCredentialOut:
    """단말별 MQTT 계정 발행/조회. super_admin 전용 — 비밀번호가 응답에 실린다.

    이미 발행된 단말이면 기존 값을 돌려준다(재사용이 기본, 계정 사양 §4).
    reissue=true 는 라인 재작업 전용이다.
    """
    return await service.issue_credential(db, mac, reissue=payload.reissue)


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
