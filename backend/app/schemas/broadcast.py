"""방송 제어 스키마."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.constants import TargetScope
from app.schemas.common import ApiModel


class FileBroadcastRequest(BaseModel):
    file_id: int
    target_scope: TargetScope
    #: scope 에 맞는 mac / zone_id / village_id. scope=all 이면 생략한다.
    target_id: str | None = Field(default=None, max_length=50)
    #: P4 플래시에 저장할지. 반복 재생할 안내음성이면 True 가 유리하다.
    store_flash: bool = False
    #: 다운로드 완료 후 자동 재생. 기본은 True — 방송 버튼을 눌렀으니 틀리길 원한다.
    autoplay: bool = True


class LiveBroadcastRequest(BaseModel):
    target_scope: TargetScope
    target_id: str | None = Field(default=None, max_length=50)


class BroadcastStopRequest(BaseModel):
    broadcast_id: int


class DeviceResultOut(BaseModel):
    """단말 하나의 응답 상태."""

    mac: str
    label: str | None = None
    result_type: str | None = None
    #: True=성공, False=실패, None=아직 응답 없음.
    ok: bool | None = None
    reason: str | None = None
    received_at: dt.datetime | None = None


class BroadcastOut(ApiModel):
    id: int
    job_id: int | None
    event_type: str
    target_scope: TargetScope
    target_id: str | None
    file_id: int | None
    file_name: str | None = None
    triggered_at: dt.datetime
    ended_at: dt.datetime | None
    #: 발행 시점에 온라인이던 대상 단말 수.
    target_count: int = 0
    results: list[DeviceResultOut] = Field(default_factory=list)

    # ── 실시간 방송에만 채워진다 ─────────────────────────────────────
    #: 단말이 GET 하는 Icecast 주소. /live/<마을8자리>/<세션id>
    stream_url: str | None = None
    #: 브라우저가 마이크를 밀어 넣을 WebSocket 경로.
    ingest_path: str | None = None
    #: 업링크(브라우저)가 붙어 있는지. False 면 무음이 나가는 중이다.
    uplink_connected: bool = False
