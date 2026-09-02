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
    #: 아래 두 값은 단말에 보내지 않는다 — 서버가 "중지"를 확정하기까지 단말 응답을
    #: 기다리는 시간이다(문제점 리스트 4·5번, 2026-09-02). 그래서 이 값만 바뀔 때는
    #: config_version 을 올리지 않는다(올리면 전 단말이 의미 없는 CONFIG 를 다시 받는다).
    file_stop_wait_sec: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10"
    )
    live_stop_wait_sec: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10"
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
