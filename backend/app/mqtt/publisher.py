"""MQTT 발행 단일 창구.

★ 브로커로 나가는 모든 메시지는 이 파일을 거친다. 다른 모듈이
  connection.raw_publish() 를 직접 부르지 않는다.

여기에 몰아둔 것:
  1. payload 직렬화 (compact JSON — 1024B 한계 때문에 공백을 넣지 않는다)
  2. 크기 검사 (초과 시 발행 전에 실패시킨다)
  3. QoS / retain 정책 (cmd=retain False, config=retain True)
  4. 권한 재확인 (라우터에서 이미 봤지만 마지막 방어선)

Phase 3/4 에서 LIVE_START · FILE_START 빌더가 여기 추가된다.
그때도 규칙은 같다 — 빌더는 dict 를 만들고, 실제 전송은 _send() 하나만 쓴다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence

from app.constants import MQTT_MAX_PAYLOAD_BYTES, TargetScope
from app.core.scope import VillageScope
from app.errors import ApiError, PayloadTooLarge
from app.mqtt import topics
from app.mqtt.connection import MqttConnection

log = logging.getLogger(__name__)

#: LIVE_START.stream_url 최대 길이. 단말 버퍼가 512B(NUL 포함)이고, 초과하면
#: 단말이 잘라 쓰지 않고 방송을 거절한다 (ESP32 회신 260824 §1.2).
STREAM_URL_MAX_BYTES = 512

_QOS_CMD = 1
_QOS_CONFIG = 1


def _encode(payload: Mapping[str, object]) -> bytes:
    """구분자에서 공백을 뺀 UTF-8 JSON. 1024B 예산이 빠듯해서 1바이트도 아깝다."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class MqttPublisher:
    def __init__(self, connection: MqttConnection) -> None:
        self._conn = connection

    @property
    def connection(self) -> MqttConnection:
        """연결 상태 조회용(/health, 재조정 작업). 발행에는 쓰지 않는다."""
        return self._conn

    # ── 최하위 전송 ─────────────────────────────────────────────────────
    async def _send(
        self,
        topic: str,
        payload: Mapping[str, object],
        *,
        qos: int,
        retain: bool,
    ) -> None:
        raw = _encode(payload)
        if len(raw) > MQTT_MAX_PAYLOAD_BYTES:
            # 단말이 못 받는 크기다. 보내봐야 조용히 실패하므로 여기서 끊는다.
            raise PayloadTooLarge(
                detail={
                    "topic": topic,
                    "size_bytes": len(raw),
                    "limit_bytes": MQTT_MAX_PAYLOAD_BYTES,
                }
            )
        await self._conn.raw_publish(topic, raw, qos=qos, retain=retain)
        log.info("MQTT → %s (%dB, qos=%d, retain=%s)", topic, len(raw), qos, retain)

    # ── CONFIG ──────────────────────────────────────────────────────────
    async def publish_global_config(
        self,
        *,
        config_version: int,
        status_interval_sec: int,
        live_stats_interval_sec: int,
        event_qos: int,
    ) -> None:
        """전 단말 공통 설정. retain=True 라서 나중에 붙는 단말도 즉시 받는다.

        payload 에 type 필드가 없다 — 사양 §4.1 의 실제 형식이다.
        단말은 모르는 필드를 무시하고, clamp 범위를 벗어난 값은 그 필드만 버린다.

        ⚠ village_id 는 절대 여기 넣지 않는다.
            이 토픽은 전 단말이 구독하고 retain 이라 나중에 붙는 단말도 받는다.
            여기에 마을을 실으면 아직 배정 안 된 단말까지 그 값을 자기 것으로
            읽고 남의 마을 방송을 구독한다 — 실제로 목 단말 3대 중 1대만
            배정했는데 3대가 전부 응답했다. 현장이라면 새로 설치한 단말이
            배정 전에 엉뚱한 마을 방송을 트는 사고다.
            마을 배정은 publish_device_config() 로만 나간다(사양 §4.2).
        """
        await self._send(
            topics.all_config(),
            {
                "config_version": config_version,
                "status_interval_sec": status_interval_sec,
                "live_stats_interval_sec": live_stats_interval_sec,
                "event_qos": event_qos,
            },
            qos=_QOS_CONFIG,
            retain=True,
        )

    async def publish_device_config(
        self,
        *,
        mac: str,
        village_id: int | None,
        config_version: int,
    ) -> None:
        """단말별 마을 배정 (사양 §4.2).

        마을은 오직 이 토픽으로만 내려간다. 토픽 이름에 MAC 이 박혀 있어서
        남의 배정값을 받을 수가 없다 — 그게 all/config 와의 결정적 차이다.

        config_version 은 all/config 와 **같은 값**을 써야 한다. 단말은 토픽별로
        버전을 따로 추적하지 않고 마지막에 받은 값을 그대로 쓰는 단일 카운터
        구조라(사양 §4.3), 두 토픽의 버전이 어긋나면 단말이 낡은 값을 최종본으로
        보고하게 된다.

        village_id=None(미배정)이면 retain 을 지운다 — 빈 payload 를 retain 으로 보내는 것이
        MQTT 에서 "이 토픽의 보관 메시지 삭제"를 뜻한다. 단말이 재접속해도
        지난 배정이 되살아나지 않는다.
        """
        topic = topics.device_config(mac)
        if village_id is None:
            await self._conn.raw_publish(topic, b"", qos=_QOS_CONFIG, retain=True)
            log.info("MQTT → %s (retain 삭제: 미배정)", topic)
            return

        await self._send(
            topic,
            {
                "config_version": config_version,
                "village_id": topics.village_token(village_id),
            },
            qos=_QOS_CONFIG,
            retain=True,
        )

    # ── CMD payload 빌더 ────────────────────────────────────────────────
    # 통신 사양 §3.2 의 필드명을 그대로 쓴다. 단말이 모르는 필드는 무시하지만
    # 이름이 다르면 조용히 기본값으로 동작해 버리므로 오타가 치명적이다.
    @staticmethod
    def file_start_payload(
        *,
        job_id: int,
        size: int,
        sha256: str,
        url: str,
        file_name: str,
        store_flash: bool,
        autoplay: bool,
    ) -> dict[str, object]:
        """FILE_START.

        job 식별자는 job_id 하나다(2026-08-20 통일). 예전에는 cmd_id 와 file_id 를
        같이 보냈지만, file_id 는 단말이 결과에 echo 만 하고 쓰지 않아서 삭제됐다.
        서버가 "어느 파일이었나"를 아는 건 broadcast_events.file_id 로 충분하다.
        """
        return {
            "type": "FILE_START",
            "job_id": job_id,
            "size": size,
            "resume_offset": 0,
            "sha256": sha256,
            "https_url": url,
            "file_name": file_name,
            "store_flash": 1 if store_flash else 0,
            "autoplay": 1 if autoplay else 0,
        }

    @staticmethod
    def file_stop_payload(*, job_id: int) -> dict[str, object]:
        """FILE_STOP. 다운로드 중이든 autoplay 재생 중이든 둘 다 멈춘다."""
        return {"type": "FILE_STOP", "job_id": job_id}

    @staticmethod
    def live_start_payload(
        *,
        job_id: int,
        stream_url: str,
        ready_timeout_sec: int = 30,
    ) -> dict[str, object]:
        """LIVE_START.

        codec/frame_ms/sample_rate 는 실제 Icecast 스트림 파라미터와 반드시
        일치해야 한다. 브라우저 opus-recorder 설정을 바꾸면 여기도 같이 바꾼다.

        stream_url 은 단말이 문자열 그대로 GET 하는 완성된 URL 이다
        (ESP32 회신 260824: 펌웨어 반영 완료, 파싱/재조립 안 함).

        제약 두 가지를 여기서 지킨다:
          · 512 바이트 이하 — 단말 버퍼 크기다. 넘으면 단말이 잘라 쓰지 않고
            방송을 거절하므로, 조용히 실패하기 전에 서버에서 끊는다.
          · https:// 만 — 단말 운영 빌드는 http 를 거절한다(2026-08-29 확인,
            LIVE_READY ok=false code=BAD_FIELD). 통신 사양 §6.3 의 "TLS 전환 이후"
            시점이 지났다. 평문으로 보내면 방송이 아예 시작되지 않는다.
            포트는 붙이지 않는다 — 443 이라 생략이 정상이고, nginx 가 /live/ 를
            Icecast 로 프록시한다.
        """
        if len(stream_url.encode("utf-8")) > STREAM_URL_MAX_BYTES:
            raise ApiError(
                f"stream_url 이 단말 한계({STREAM_URL_MAX_BYTES}B)를 넘었습니다.",
                code="STREAM_URL_TOO_LONG",
            )
        if not stream_url.startswith("https://"):
            # 지금 막지 않으면 현장에서 "LIVE_READY 는 오는데 소리가 안 난다"로만 보인다.
            raise ApiError(
                "단말은 https 스트림만 받습니다. "
                "ICECAST_PUBLIC_BASE_URL 을 https://<도메인> 으로 설정하세요(포트 없이).",
                code="STREAM_URL_MUST_BE_HTTPS",
            )
        return {
            "type": "LIVE_START",
            "job_id": job_id,
            "stream_url": stream_url,
            "codec": "opus",
            "frame_ms": 40,
            "sample_rate": 16000,
            "record_flash": 0,
            "ready_timeout_sec": ready_timeout_sec,
        }

    @staticmethod
    def live_stop_payload(*, job_id: int) -> dict[str, object]:
        """LIVE_STOP. 반드시 현재 송출 중인 job_id 로 보낸다."""
        return {"type": "LIVE_STOP", "job_id": job_id}

    # ── CMD ─────────────────────────────────────────────────────────────
    async def publish_command(
        self,
        *,
        payload: Mapping[str, object],
        target_scope: TargetScope,
        scope: VillageScope,
        village_ids: Sequence[int] = (),
        macs: Sequence[str] = (),
    ) -> list[str]:
        """방송·OTA 명령을 대상에 맞는 토픽으로 발행한다.

        대상별 토픽 선택:
          all      → iotradio/all/cmd            (super_admin 만)
          village  → 마을마다 iotradio/village/<id8>/cmd — 같은 payload(같은
                     job_id·stream_url)를 여러 마을 토픽에 발행한다. 단말들이
                     같은 마운트로 모여 한 방송을 같이 듣는 구조다
          zone     → 소속 단말 MAC 별 개별 토픽으로 펼친다(구역은 단말이 모른다)
          device   → iotradio/device/<mac>/cmd

        zone/device 는 호출자가 macs 를 채워서 넘긴다(대상 해석은 device 모듈의 일이다).
        발행한 토픽 목록을 돌려준다 — 이력 기록과 테스트에 쓴다.

        retain=False 고정. cmd 에 retain 을 걸면 단말 재접속 때 지난 방송이 되살아난다.
        """
        if target_scope is TargetScope.ALL:
            if not scope.all_villages:
                # super_admin 만 전체 발행이 가능하다.
                raise ApiError(
                    "전체 방송은 최고 관리자만 실행할 수 있습니다.",
                    code="SUPER_ADMIN_REQUIRED",
                )
            targets = [topics.all_cmd()]

        elif target_scope is TargetScope.VILLAGE:
            if not village_ids:
                raise ApiError("마을 대상 명령에는 village_ids 가 필요합니다.")
            for village_id in village_ids:
                scope.ensure_allowed(village_id)
            targets = [topics.village_cmd(v) for v in village_ids]

        else:  # ZONE · DEVICE — 둘 다 MAC 단위로 펼쳐서 보낸다
            if not macs:
                raise ApiError("대상 단말이 없습니다.", code="NO_TARGET_DEVICE")
            targets = [topics.device_cmd(mac) for mac in macs]

        for topic in targets:
            await self._send(topic, payload, qos=_QOS_CMD, retain=False)
        return targets
