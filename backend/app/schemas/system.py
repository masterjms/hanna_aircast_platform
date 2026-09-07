"""시스템 설정 · 헬스 스키마."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel


class ConfigOut(ApiModel):
    config_version: int
    status_interval_sec: int
    live_stats_interval_sec: int
    event_qos: int
    # ── 방송 응답 시간. CONFIG 토픽으로 나가지 않는다 ──
    #: LIVE_START.ready_timeout_sec 로 단말에 전달. 화면의 준비 지연 기준은 이 값 + 5.
    live_ready_timeout_sec: int
    #: 라이브 중지 후 LIVE_RESULT 대기 상한.
    live_stop_wait_sec: int
    #: 파일 시작(저장 완료 → 재생 시작)·중지 응답 대기 상한. 둘에 같이 쓴다.
    file_wait_sec: int
    # ── 오디오 품질 ──
    #: 라이브 opus 비트레이트(kbps). 브라우저 인코더가 이 값으로 만든다.
    live_bitrate_kbps: int
    #: 파일함 mp3 비트레이트(kbps). 업로드 재인코딩과 TTS 합성에 쓴다.
    file_bitrate_kbps: int
    updated_at: dt.datetime


class ConfigUpdate(BaseModel):
    """clamp 범위는 통신 사양 §3.5 와 같다. 서버가 먼저 막고 단말이 한 번 더 막는다."""

    status_interval_sec: int | None = Field(default=None, ge=10, le=3600)
    live_stats_interval_sec: int | None = Field(default=None, ge=1, le=60)
    event_qos: int | None = Field(default=None, ge=0, le=1)
    # ── 방송 응답 시간 (범위는 constants.CONFIG_LIMITS 와 같다) ──
    #: 단말 준비 제한. LIVE_START 에 실려 나가고 단말은 +5초까지 기다린다(사양 1~60).
    live_ready_timeout_sec: int | None = Field(default=None, ge=1, le=60)
    #: 라이브 중지 후 LIVE_RESULT 대기 상한. 다 오면 즉시 끝내고 스트림을 닫는다.
    live_stop_wait_sec: int | None = Field(default=None, ge=10, le=30)
    #: 파일 시작(받고 검증 완료)·중지 응답 대기 상한. 저장은 백그라운드라 무관.
    file_wait_sec: int | None = Field(default=None, ge=10, le=60)
    # ── 오디오 품질 (선택지는 constants.CONFIG_CHOICES 와 같다) ──
    live_bitrate_kbps: Literal[16, 24] | None = None
    file_bitrate_kbps: Literal[16, 24] | None = None


class HealthOut(BaseModel):
    status: str
    database: bool
    mqtt: bool
