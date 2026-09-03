"""도메인 상수.

DB CHECK 제약, Pydantic 검증, MQTT payload 가 같은 문자열을 쓰도록 여기 한 곳에 모은다.
단말 프로토콜에 나가는 값은 통신 사양(xWIFI_통신_사양_최종_260813.md)과 반드시 일치해야 한다.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """py3.10 호환 StrEnum. 값이 곧 문자열."""

    def __str__(self) -> str:
        return self.value


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    VILLAGE_ADMIN = "village_admin"


class TargetScope(StrEnum):
    """방송·스케줄 대상 범위. 좁은 것부터."""

    DEVICE = "device"
    ZONE = "zone"
    VILLAGE = "village"
    ALL = "all"


class FileSource(StrEnum):
    UPLOAD = "upload"
    TTS = "tts"


class EventType(StrEnum):
    """broadcast_events.event_type — 서버가 발행한 명령의 종류."""

    LIVE_START = "LIVE_START"
    LIVE_STOP = "LIVE_STOP"
    FILE_START = "FILE_START"
    FILE_STOP = "FILE_STOP"
    OTA_START = "OTA_START"
    OTA_APPLY = "OTA_APPLY"
    CONFIG = "CONFIG"


class ResultType(StrEnum):
    """device_events.result_type — 단말이 올려보낸 응답의 종류."""

    LIVE_READY = "LIVE_READY"
    LIVE_STATS = "LIVE_STATS"
    # 신형식 (2026-08-27~, 사양 §5.4) — 성패는 payload 의 ok 불리언이 정한다.
    LIVE_RESULT = "LIVE_RESULT"      # 라이브 종료 결과 (정상 종료 = ok:true STOPPED_BY_SERVER)
    FILE_RESULT = "FILE_RESULT"      # FILE_END/FILE_ABORT/FILE_STOP_RESULT 셋을 대체
    OTA_PROGRESS = "OTA_PROGRESS"    # 진행 알림 (25% 단위, 최종 아님)
    OTA_RESULT = "OTA_RESULT"        # OTA 최종 결과
    # 구형식 — 신형식 이전 펌웨어 호환으로 남긴다.
    FILE_END = "FILE_END"
    FILE_ABORT = "FILE_ABORT"
    FILE_STOP_RESULT = "FILE_STOP_RESULT"
    OTA_STATUS = "OTA_STATUS"
    STATUS = "STATUS"
    OFFLINE = "OFFLINE"  # LWT


class DeviceState(StrEnum):
    """STATUS.state 후보 (통신 사양 §3.4, 2026-08-20 개정).

    단말 우선순위는 OTA > LIVE > FILE > RF > IDLE 이다.

    LIVE_READY_WAIT 는 삭제됐다 — LIVE_READY 발행 전의 "준비 중" 구간도 이제
    LIVE 로 통합된다. 외부 AMP 모델은 AMP 안정화 대기 동안 계속 LIVE 로 보인다.
    서버가 "준비 중"과 "실제 송출 중"을 구분해야 하면 LIVE_START 발행 시각과
    LIVE_READY 수신 여부로 애플리케이션 레벨에서 추적한다.
    """

    IDLE = "IDLE"
    LIVE = "LIVE"
    FILE = "FILE"
    #: P4 RF 모듈이 자체적으로 켜고 끄는 상태. 서버는 관측만 하고 제어하지 않는다.
    RF = "RF"
    OTA = "OTA"
    OFFLINE = "OFFLINE"


#: MQTT CMD payload 상한. 단말 수신 버퍼 제약이라 서버가 반드시 지켜야 한다.
MQTT_MAX_PAYLOAD_BYTES = 1024

#: CONFIG 값의 허용 범위 (통신 사양 §3.5). 단말도 clamp 하지만 서버가 먼저 막는다.
CONFIG_LIMITS: dict[str, tuple[int, int]] = {
    "status_interval_sec": (10, 3600),
    "live_stats_interval_sec": (1, 60),
    "event_qos": (0, 1),
    # ── 방송 응답 시간 (CONFIG 토픽으로 안 나간다) ──
    # LIVE_START.ready_timeout_sec 로 나간다(CONFIG 가 아니라 명령 필드). 사양 1~60.
    "live_ready_timeout_sec": (1, 60),
    # 라이브 중지 후 LIVE_RESULT 대기 상한.
    "live_stop_wait_sec": (10, 30),
    # 파일 시작(저장 완료)·중지 응답 대기 상한. 단말 자체 포기(120초)를 넘길 이유가 없다.
    "file_wait_sec": (30, 180),
}

#: 이 중 단말로 나가는 값들. 여기 없는 설정은 서버 안에서만 쓰이므로 바뀌어도
#: config_version 을 올리지 않는다(올리면 전 단말이 CONFIG 를 다시 받는다).
DEVICE_CONFIG_FIELDS = frozenset({"status_interval_sec", "live_stats_interval_sec", "event_qos"})

#: MQTT 로 나가는 village_id 는 8자리 제로패딩 문자열이다.
VILLAGE_ID_WIDTH = 8

#: MAC 정규형: 콜론 없는 소문자 12자리.
MAC_LENGTH = 12


#: 주기적으로 계속 올라오는 telemetry. 1회성 "결과"와 달리 최신값만 뜻이 있다.
#: 서버는 이력에 tick 마다 쌓지 않고 방송·단말당 1행만 두고 덮어쓴다.
# OTA_PROGRESS 는 25% 단위로 여러 번 오는 진행 알림이라 최신값만 의미가 있다.
TELEMETRY_RESULTS = frozenset({"LIVE_STATS", "OTA_PROGRESS"})
