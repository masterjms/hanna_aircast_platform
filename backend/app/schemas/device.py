"""단말 스키마."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.mqtt.topics import normalize_mac
from app.schemas.common import ApiModel

DeviceStatusFilter = Literal["online", "offline", "unassigned"]


class DeviceCreate(BaseModel):
    """사전 등록. 보통은 단말이 STATUS 를 보내면서 자동 등록되므로 잘 쓰지 않는다."""

    mac: str
    label: str | None = Field(default=None, max_length=100)
    village_id: int | None = None
    zone_id: int | None = None

    @field_validator("mac")
    @classmethod
    def _normalize_mac(cls, v: str) -> str:
        # 58:E6:C5:F2:CC:74 로 입력해도 58e6c5f2cc74 로 저장된다.
        return normalize_mac(v)


class DeviceUpdate(BaseModel):
    """부분 수정.

    village_id / zone_id 는 "null 로 바꾸기"(배정 해제)와 "안 건드리기"를 구분해야 한다.
    서비스는 model_dump(exclude_unset=True) 로 실제로 보낸 필드만 반영한다 —
    필드를 생략하면 미변경, null 을 명시하면 해제다.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=100)
    village_id: int | None = None
    zone_id: int | None = None


class DeviceOut(ApiModel):
    mac: str
    label: str | None
    village_id: int | None
    village_name: str | None = None
    zone_id: int | None
    zone_name: str | None = None
    firmware_version: str | None
    last_seen_at: dt.datetime | None
    registered_at: dt.datetime

    #: 서버가 last_seen_at 으로 계산한 값. 단말이 보고하는 값이 아니다.
    online: bool = False
    #: 최근 STATUS 원본에서 뽑은 표시용 필드.
    rssi: int | None = None
    state: str | None = None
    #: 라이브 수신 상태 — OFF / PLAYING / RECONNECTING (사양 §5).
    #: RECONNECTING = 방송은 살아 있는데 스피커가 무음인 상태. 화면이 경고한다.
    live: str | None = None
    #: 단말별 MQTT 계정 발행 여부. False 면 화면에 「미등록*」 — 붙긴 하는데
    #: 서버가 발행한 계정이 없는 단말이다(레지스트리 사양 §3.6, 동기화 어긋남).
    #: 비밀번호 자체는 여기 싣지 않는다 — 전용 엔드포인트(super_admin)로만 준다.
    has_credential: bool = False
    config_version: int | None = None
    ip: str | None = None

    @classmethod
    def from_row(
        cls,
        device: Any,
        *,
        online: bool,
        village_name: str | None = None,
        zone_name: str | None = None,
    ) -> DeviceOut:
        status: dict[str, Any] = device.last_status or {}
        return cls(
            mac=device.mac,
            label=device.label,
            village_id=device.village_id,
            village_name=village_name,
            zone_id=device.zone_id,
            zone_name=zone_name,
            firmware_version=device.firmware_version,
            last_seen_at=device.last_seen_at,
            registered_at=device.registered_at,
            online=online,
            rssi=status.get("rssi"),
            state=status.get("state"),
            live=status.get("live"),
            config_version=status.get("config_version"),
            ip=status.get("ip"),
            has_credential=device.mqtt_password is not None,
        )


class DeviceDetail(DeviceOut):
    """상세 모달용. 최근 STATUS payload 원본을 그대로 붙인다."""

    last_status: dict[str, Any] | None = None


class DeviceCredentialIssue(BaseModel):
    """계정 발행 요청. 기본은 재사용 — 이미 있으면 그 값을 돌려준다(계정 사양 §4).

    reissue=True 는 라인 재작업 전용: 새 비밀번호를 만들어 덮어쓴다. 현장에 나가
    있는 단말에 쓰면 그 단말은 다시는 브로커에 못 붙는다(새 값을 넣을 케이블이 없다).
    """

    reissue: bool = False


class DeviceCredentialOut(ApiModel):
    """등록 화면이 표시하고 생산 라인이 시리얼(@MQTTID/@MQTTPW)로 넣는 값."""

    username: str
    password: str
    #: 이번 호출에서 새로 만들었는가. False = 기존 값 재사용.
    issued: bool
