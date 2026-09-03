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
    #: 중지 후 단말 응답을 기다리는 시간(초). 단말에 나가지 않는 서버 설정이다.
    file_stop_wait_sec: int
    live_stop_wait_sec: int
    #: LIVE_START.ready_timeout_sec 로 단말에 전달. 화면의 준비 지연 기준은 이 값 + 5.
    live_ready_timeout_sec: int
    #: 파일 시작 후 FILE_RESULT(저장 완료)를 기다리는 상한.
    file_result_wait_sec: int
    updated_at: dt.datetime


class ConfigUpdate(BaseModel):
    """clamp 범위는 통신 사양 §3.5 와 같다. 서버가 먼저 막고 단말이 한 번 더 막는다."""

    status_interval_sec: int | None = Field(default=None, ge=10, le=3600)
    live_stats_interval_sec: int | None = Field(default=None, ge=1, le=60)
    event_qos: int | None = Field(default=None, ge=0, le=1)
    #: 중지를 누른 뒤 단말의 종료 응답을 기다리는 시간. 다 오면 그 즉시 끝내고,
    #: 이 시간을 넘기면 못 받은 단말이 있어도 종료로 확정한다.
    file_stop_wait_sec: int | None = Field(default=None, ge=10, le=30)
    live_stop_wait_sec: int | None = Field(default=None, ge=10, le=30)
    #: 단말 준비 제한. LIVE_START 에 실려 나가고 단말은 +5초까지 기다린다(사양 1~60).
    live_ready_timeout_sec: int | None = Field(default=None, ge=1, le=60)
    #: 파일 저장 완료(FILE_RESULT) 대기 상한. 크기에 비례해 길어진다 — 3MB 면 40초.
    file_result_wait_sec: int | None = Field(default=None, ge=30, le=180)


class HealthOut(BaseModel):
    status: str
    database: bool
    mqtt: bool
