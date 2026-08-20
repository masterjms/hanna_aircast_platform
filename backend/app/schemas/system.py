"""시스템 설정 · 헬스 스키마."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel


class ConfigOut(ApiModel):
    config_version: int
    status_interval_sec: int
    live_stats_interval_sec: int
    event_qos: int
    updated_at: dt.datetime


class ConfigUpdate(BaseModel):
    """clamp 범위는 통신 사양 §3.5 와 같다. 서버가 먼저 막고 단말이 한 번 더 막는다."""

    status_interval_sec: int | None = Field(default=None, ge=10, le=3600)
    live_stats_interval_sec: int | None = Field(default=None, ge=1, le=60)
    event_qos: int | None = Field(default=None, ge=0, le=1)


class HealthOut(BaseModel):
    status: str
    database: bool
    mqtt: bool
