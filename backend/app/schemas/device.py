"""단말 스키마."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.mqtt.topics import normalize_mac
from app.schemas.common import ApiModel

DeviceStatusFilter = Literal["online", "offline", "unassigned"]


class DeviceCreate(BaseModel):
    """단말 등록 — 신규 단말 등록 화면(QR 스캔/수동)과 사전 등록이 쓴다.

    p4/c6 모델·버전은 QR 5필드에서 오고, 수동 등록에서는 비워도 된다
    (생산 사양 §3.2 — 강제 입력은 MAC 하나뿐).
    """

    mac: str
    label: str | None = Field(default=None, max_length=100)
    village_id: int | None = None
    zone_id: int | None = None
    p4_model: str | None = Field(default=None, max_length=50)
    p4_version: str | None = Field(default=None, max_length=50)
    c6_model: str | None = Field(default=None, max_length=50)
    c6_version: str | None = Field(default=None, max_length=50)
    #: 등록 화면이 모달을 열 때 미리 발급받은 비밀번호(POST /api/devices/credential)를
    #: 그대로 저장하려고 넘긴다 — 시리얼로 이미 단말에 넣은 값과 DB 가 어긋나면
    #: 안 되기 때문. 비우면 서버가 새로 생성한다.
    mqtt_password: str | None = Field(default=None, min_length=1, max_length=16)

    @field_validator("mac")
    @classmethod
    def _normalize_mac(cls, v: str) -> str:
        # 58:E6:C5:F2:CC:74 로 입력해도 58e6c5f2cc74 로 저장된다.
        return normalize_mac(v)

    @field_validator("mqtt_password")
    @classmethod
    def _password_charset(cls, v: str | None) -> str | None:
        """사양 §1 문자 집합 밖의 문자를 거른다 — `@` 가 섞이면 시리얼 전송이 잘린다."""
        from app.core.mqtt_accounts import PASSWORD_CHARSET

        if v is not None and any(c not in PASSWORD_CHARSET for c in v):
            raise ValueError("비밀번호에 허용되지 않는 문자가 있습니다 (사양 문자 집합 밖).")
        return v


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
    #: 등록(QR 스캔) 시점의 하드웨어 식별값. 출하 당시 값 — 실행 중 버전과 별개.
    p4_model: str | None = None
    p4_version: str | None = None
    c6_model: str | None = None
    c6_version: str | None = None
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
            p4_model=device.p4_model,
            p4_version=device.p4_version,
            c6_model=device.c6_model,
            c6_version=device.c6_version,
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


class NewDevicePasswordOut(ApiModel):
    """신규 단말 등록 모달이 열릴 때 미리 발급받는 비밀번호.

    서버가 아직 MAC 을 모르는 시점이라 계정이 아니라 값만 준다. 등록(POST
    /api/devices)에 mqtt_password 로 되돌려 보내야 DB 에 확정된다.
    """

    password: str


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
