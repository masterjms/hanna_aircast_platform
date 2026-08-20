"""단말 — devices."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import MAC_LENGTH
from app.models.base import Base


class Device(Base):
    """ESP32 단말.

    펌웨어에 village_id 를 굽지 않는다. 전 단말 동일 펌웨어로 출하되고,
    첫 STATUS 로 미배정 상태(village_id=NULL)로 자동 등록된 뒤
    관리자가 배정하면 서버가 CONFIG(retain) 로 알려준다.
    """

    __tablename__ = "devices"

    #: 콜론 없는 소문자 12자리. 예: 58e6c5f2cc74
    mac: Mapped[str] = mapped_column(String(MAC_LENGTH), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(100))

    #: NULL = 미배정. 마을이 지워져도 단말은 남기고 미배정으로 되돌린다.
    village_id: Mapped[int | None] = mapped_column(
        ForeignKey("villages.id", ondelete="SET NULL"), index=True
    )
    #: 마을까지만 배정하고 구역은 나중에 정해도 된다.
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), index=True
    )

    firmware_version: Mapped[str | None] = mapped_column(String(50))

    #: 최근 STATUS payload 원본. 대시보드가 이력 테이블을 뒤지지 않게 하는 캐시다.
    last_status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: 온라인 판정 기준. now() - last_seen_at < DEVICE_ONLINE_THRESHOLD_SEC 이면 온라인.
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    registered_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
