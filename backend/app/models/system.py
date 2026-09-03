"""시스템 — current_config, daily_cost_summary."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CurrentConfig(Base):
    """단말 공통 CONFIG 의 정본. 항상 한 행(id=1)만 존재한다.

    MQTT retain 은 캐시일 뿐이고 진짜 값은 여기다.
    백엔드 기동 시 1회 + 주기적으로 이 값을 CONFIG 로 재발행한다
    (app/tasks/config_reconcile.py).
    """

    __tablename__ = "current_config"
    __table_args__ = (CheckConstraint("id = 1", name="ck_current_config_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default="1")
    #: 값이 바뀔 때마다 증가. 단말이 적용 여부를 판단하는 기준.
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    live_stats_interval_sec: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="10"
    )
    event_qos: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    # ── 아래 셋은 방송 응답 시간이다. CONFIG 토픽으로 나가지 않으므로 이 값만 바뀔 때는
    #    config_version 을 올리지 않는다(올리면 전 단말이 의미 없는 CONFIG 를 다시 받는다).
    #    단말 요청 2026-09-03 §3.4 — "서버가 관리할 값은 셋": 라이브 준비 제한, 라이브
    #    종료 대기, 파일 대기.

    #: LIVE_START 에 실어 보내는 단말 준비 제한(사양 1~60). 단말은 이 값 + 5초까지
    #: 기다렸다가 LIVE_READY 를 보내므로, 서버 화면의 "준비 지연" 기준은 이 값 + 5 다.
    live_ready_timeout_sec: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="30"
    )
    #: 라이브 중지 후 단말별 LIVE_RESULT 를 기다리는 상한. 다 오면 그 순간 스트림을 닫는다.
    live_stop_wait_sec: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10"
    )
    #: 파일 방송 응답 대기 — **시작과 중지에 같이 쓴다**(문제점 8번). 시작 후에는
    #: 단말의 FILE_RESULT ok=true(받아서 저장까지 끝냄 → 재생 시작)를, 중지 후에는
    #: 종료 응답을 이 시간까지 기다린다. 저장이 느려서(LittleFS 80~100KB/s, 3MB 면
    #: 40초) 짧게 잡으면 정상 저장 중인 단말을 실패로 본다. 단말 자체 포기가 120초.
    file_wait_sec: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="120"
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailyCostSummary(Base):
    """일별 비용 집계. 매일 새벽 배치가 전날 이력을 마을별로 접어 넣는다.

    village_id 가 NULL 인 행 = 전체 집계(AWS 실비용 포함, super_admin 전용).
    village_id 가 있는 행 = 마을별 추정치만(AWS 가 마을 단위로 청구를 쪼개주지 않는다).
    """

    __tablename__ = "daily_cost_summary"
    __table_args__ = (
        # NULL 을 같은 값으로 취급해야 "그날의 전체 행"이 하나로 유지된다. (PG15+)
        UniqueConstraint(
            "summary_date",
            "village_id",
            name="uq_daily_cost_summary_date_village",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    summary_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    village_id: Mapped[int | None] = mapped_column(
        ForeignKey("villages.id", ondelete="CASCADE")
    )

    broadcast_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    #: 그날 방송에 참여한 단말 연인원.
    device_broadcast_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    estimated_egress_mb: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    estimated_cost_krw: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    #: village_id 가 NULL 인 행에만 채운다 (Cost Explorer 실비용).
    actual_total_cost_krw: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
