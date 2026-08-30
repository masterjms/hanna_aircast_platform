"""방송 제어 스키마."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.constants import TargetScope
from app.schemas.common import ApiModel


class FileBroadcastRequest(BaseModel):
    file_id: int
    target_scope: TargetScope
    #: scope 에 맞는 id 목록 — village 면 마을 id 들, device 면 MAC 들.
    #: scope=all 이면 빈 목록. "마을 2곳 동시 방송" 같은 다중 대상을 위해 목록이다.
    target_ids: list[str] = Field(default_factory=list, max_length=200)
    #: P4 플래시에 저장할지. 반복 재생할 안내음성이면 True 가 유리하다.
    store_flash: bool = False
    #: 다운로드 완료 후 자동 재생. 기본은 True — 방송 버튼을 눌렀으니 틀리길 원한다.
    autoplay: bool = True


class LiveBroadcastRequest(BaseModel):
    target_scope: TargetScope
    target_ids: list[str] = Field(default_factory=list, max_length=200)
    #: 단말 flash 에 방송을 녹음할지. 10분을 넘길 방송은 꺼야 한다(사양 §11.2).
    record_flash: bool = True


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
    #: 단말 STATUS 의 live 값. RECONNECTING 이면 "방송 중인데 무음" — 재접속 중이다.
    live: str | None = None
    #: LIVE_STATS 요약(버퍼·끊김). 결과가 아니라 수신 품질이라 따로 둔다.
    stats: str | None = None
    received_at: dt.datetime | None = None


class BroadcastOut(ApiModel):
    id: int
    job_id: int | None
    event_type: str
    target_scope: TargetScope
    target_ids: list[str]
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
