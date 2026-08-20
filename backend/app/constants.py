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
    FILE_END = "FILE_END"
    FILE_ABORT = "FILE_ABORT"
    FILE_STOP_RESULT = "FILE_STOP_RESULT"
    OTA_STATUS = "OTA_STATUS"
    STATUS = "STATUS"
    OFFLINE = "OFFLINE"  # LWT


class DeviceState(StrEnum):
    """STATUS.state 후보 (통신 사양 §3.4)."""

    IDLE = "IDLE"
    LIVE_READY_WAIT = "LIVE_READY_WAIT"
    LIVE = "LIVE"
    FILE = "FILE"
    OFFLINE = "OFFLINE"


#: MQTT CMD payload 상한. 단말 수신 버퍼 제약이라 서버가 반드시 지켜야 한다.
MQTT_MAX_PAYLOAD_BYTES = 1024

#: CONFIG 값의 허용 범위 (통신 사양 §3.5). 단말도 clamp 하지만 서버가 먼저 막는다.
CONFIG_LIMITS: dict[str, tuple[int, int]] = {
    "status_interval_sec": (10, 3600),
    "live_stats_interval_sec": (1, 60),
    "event_qos": (0, 1),
}

#: MQTT 로 나가는 village_id 는 8자리 제로패딩 문자열이다.
VILLAGE_ID_WIDTH = 8

#: MAC 정규형: 콜론 없는 소문자 12자리.
MAC_LENGTH = 12
