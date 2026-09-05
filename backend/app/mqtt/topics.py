"""MQTT 토픽 문자열.

토픽을 문자열 리터럴로 흩뿌리지 않는다. 오타 하나가 "명령이 조용히 안 감"으로 나타나서
디버깅이 매우 어렵기 때문이다.

토픽 표 (통신 사양 §MQTT 토픽):
    iotradio/device/<mac>/cmd        S→D  개별 명령        QoS1  retain=False
    iotradio/village/<village_id>/cmd S→D 마을 명령        QoS1  retain=False
        village_id 는 법정동코드(10)+연번(2) 12자리 문자열 — app/core/village_token.py
    iotradio/all/cmd                 S→D  전체 명령        QoS1  retain=False
    iotradio/device/<mac>/result     D→S  명령 결과        QoS1
    iotradio/device/<mac>/status     D→S  STATUS/STATS/LWT QoS0|1
    iotradio/all/config              S→D  공통 설정        QoS1  retain=True
    iotradio/device/<mac>/config     S→D  단말별 설정      QoS1  retain=True   ※ 코덱스 협의 중

cmd 에는 retain 을 쓰지 않는다 — 단말이 재접속할 때 지난 방송 명령이 되살아난다.
"""

from __future__ import annotations

import re

from app.config import settings
from app.constants import MAC_LENGTH

ROOT = settings.mqtt_topic_root

_MAC_RE = re.compile(rf"^[0-9a-f]{{{MAC_LENGTH}}}$")


def normalize_mac(raw: str) -> str:
    """어떤 표기로 들어와도 콜론 없는 소문자 12자리로 접는다.

    58:E6:C5:F2:CC:74 → 58e6c5f2cc74
    """
    mac = raw.replace(":", "").replace("-", "").strip().lower()
    if not _MAC_RE.match(mac):
        raise ValueError(f"MAC 형식이 아닙니다: {raw!r}")
    return mac


# ── 서버 → 단말 ──────────────────────────────────────────────────────────
def device_cmd(mac: str) -> str:
    return f"{ROOT}/device/{mac}/cmd"


def village_cmd(village_token: str) -> str:
    """village_token 은 마을의 MQTT 문자열(core.village_token.token_for)이다 — id 가 아니다."""
    return f"{ROOT}/village/{village_token}/cmd"


def all_cmd() -> str:
    return f"{ROOT}/all/cmd"


def all_config() -> str:
    return f"{ROOT}/all/config"


def device_config(mac: str) -> str:
    return f"{ROOT}/device/{mac}/config"


# ── 단말 → 서버 ──────────────────────────────────────────────────────────
def device_result(mac: str) -> str:
    return f"{ROOT}/device/{mac}/result"


def device_status(mac: str) -> str:
    return f"{ROOT}/device/{mac}/status"


#: 워커가 구독하는 패턴. (토픽, QoS)
SUBSCRIPTIONS: tuple[tuple[str, int], ...] = (
    (f"{ROOT}/device/+/result", 1),
    (f"{ROOT}/device/+/status", 1),
)

_INBOUND_RE = re.compile(rf"^{re.escape(ROOT)}/device/([0-9a-f]{{{MAC_LENGTH}}})/(result|status)$")


def parse_inbound(topic: str) -> tuple[str, str] | None:
    """수신 토픽 → (mac, kind). 형식이 안 맞으면 None.

    브로커에 엉뚱한 토픽이 섞여도 워커가 죽지 않도록 예외 대신 None 을 준다.
    """
    match = _INBOUND_RE.match(topic)
    if match is None:
        return None
    return match.group(1), match.group(2)
