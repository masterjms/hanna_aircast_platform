"""이력 — broadcast_events, device_events.

한 번의 방송 명령(broadcast_events 1행)이 여러 단말(device_events N행)에 결과를 남긴다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import MAC_LENGTH, TargetScope
from app.models.base import Base

_SCOPES = ", ".join(f"'{s.value}'" for s in TargetScope)


class BroadcastEvent(Base):
    """서버가 발행한 명령 1건.

    ended_at 이 NULL 인 행 = 진행 중인 방송. 대상 겹침 검사가 이 조건을 본다.
    """

    __tablename__ = "broadcast_events"
    __table_args__ = (
        CheckConstraint(f"target_scope IN ({_SCOPES})", name="ck_broadcast_events_scope"),
        # 진행 중인 방송만 훑는 부분 인덱스 — 겹침 검사가 가장 잦은 질의다.
        Index(
            "ix_broadcast_events_active",
            "ended_at",
            postgresql_where="ended_at IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)

    #: session_id / cmd_id / job_id 통일본. DB 시퀀스(job_id_seq)로 발번한다.
    job_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    target_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    #: scope 에 맞는 id 목록 — village 면 마을 id 들, device 면 MAC 들, zone 이면
    #: 구역 id 들, all 이면 빈 목록. 목록인 이유: "마을 2곳에 동시 방송" 같은
    #: 다중 대상을 값 하나(String)로는 표현할 수 없다.
    target_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")

    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    #: 스케줄에 의한 자동 실행이면 채우고, 수동이면 NULL.
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id"))
    #: 수동이면 채우고, 스케줄이면 NULL.
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    triggered_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: 실제 전송량이 아니라 추정치다(단말이 바이트 수를 보고하지 않는다) — LIVE 는
    #: 방송 시간 × 24kbps × 수신 단말 수, FILE 은 파일 크기 × 수신 단말 수로 계산해
    #: stop_*_broadcast 가 ended_at 을 찍을 때 같이 채운다. 서버 재시작으로 고아가
    #: 되어 정리된 행은 시간을 신뢰할 수 없어 NULL 로 남긴다(트래픽 산정에서 제외).
    bytes_estimated: Mapped[int | None] = mapped_column(BigInteger)


class DeviceEvent(Base):
    """단말이 올려보낸 응답 1건.

    payload 는 MQTT 원본을 JSONB 로 그대로 넣는다. 단말 프로토콜에 필드가 추가돼도
    스키마를 바꾸지 않기 위해서다.

    STATUS 는 여기 쌓지 않는다 — 300대 × 30초면 하루 86만 행이 된다.
    최신값만 devices.last_status 에 캐시한다(app/mqtt/handlers.py 참고).

    LIVE_STATS 도 같은 성격의 주기 telemetry라 tick 마다 쌓지 않고 방송·단말당
    1행만 두고 덮어쓴다(handlers._TELEMETRY_RESULTS). 반대로 LIVE_READY 같은
    결과는 한 job 에 두 번 올 수 있고(준비 완료 → 접속 실패) 둘 다 남아야 한다.
    """

    __tablename__ = "device_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("broadcast_events.id", ondelete="CASCADE"), index=True
    )
    #: devices.mac 에 FK 를 걸지 않는다(0006 에서 제거). 이력은 불변 로그라
    #: 단말을 삭제(도난·폐기)해도 과거 방송 기록은 남아야 한다 — FK 가 있으면
    #: 이벤트 있는 단말의 삭제가 통째로 거부된다(실제 운영에서 500 발생).
    mac: Mapped[str] = mapped_column(String(MAC_LENGTH), nullable=False, index=True)
    result_type: Mapped[str | None] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    #: QoS 1 중복 수신 방어용 멱등 키. "<mac>:<result_type>:<job_id>" 형태.
    #: job_id 를 못 뽑는 메시지는 NULL 로 두고 중복 검사를 하지 않는다.
    #: (DB 스키마 문서에는 없던 구현 추가분 — 마이그레이션 0001 에 UNIQUE 인덱스가 있다.)
    dedup_key: Mapped[str | None] = mapped_column(String(140))
