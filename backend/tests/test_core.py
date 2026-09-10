"""핵심 로직 테스트 (DB · 브로커 없이 도는 것만).

여기 있는 것들은 틀리면 조용히 사고가 나는 부분이다:
  · 권한 범위 판정      → 남의 마을 데이터가 새어나간다
  · MAC/토픽 정규화     → 명령이 엉뚱한 곳으로 가거나 아무 데도 안 간다
  · payload 크기 한계   → 단말이 못 받는데 서버는 성공으로 안다

    cd backend && .venv/Scripts/python -m pytest tests -q
"""

from __future__ import annotations

import dataclasses
import json
import unittest.mock

import pytest
from sqlalchemy import select

from app.constants import MQTT_MAX_PAYLOAD_BYTES, Role, TargetScope
from app.core.scope import VillageScope, scope_for
from app.core.security import (
    MAX_PASSWORD_BYTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.errors import ApiError, PayloadTooLarge, Unauthorized, VillageOutOfScope
from app.models.device import Device
from app.mqtt import topics
from app.mqtt.publisher import MqttPublisher, _encode


# ── VillageScope ─────────────────────────────────────────────────────────
class TestVillageScope:
    def test_super_admin_allows_everything(self):
        scope = VillageScope.for_super_admin()
        assert scope.allows(1)
        assert scope.allows(999)
        # 미배정 단말도 super_admin 은 다룰 수 있다.
        assert scope.allows(None)

    def test_village_admin_limited_to_assigned(self):
        scope = VillageScope.for_villages([1, 2])
        assert scope.allows(1)
        assert scope.allows(2)
        assert not scope.allows(3)

    def test_village_admin_cannot_touch_unassigned(self):
        """미배정(village_id=None)은 어느 마을에도 속하지 않는다."""
        scope = VillageScope.for_villages([1, 2])
        assert not scope.allows(None)

    def test_ensure_allowed_raises_with_detail(self):
        scope = VillageScope.for_villages([1])
        with pytest.raises(VillageOutOfScope) as exc:
            scope.ensure_allowed(7)
        assert exc.value.detail == {"village_id": 7}
        assert exc.value.status_code == 403

    def test_empty_scope_is_not_all_access(self):
        """담당 마을이 없는 village_admin 은 아무것도 못 본다 (전체 접근이 아니다)."""
        scope = VillageScope.for_villages([])
        assert scope.is_empty
        assert not scope.all_villages
        assert not scope.allows(1)

    def test_apply_filters_query_for_village_admin(self):
        stmt = VillageScope.for_villages([1, 2]).apply(select(Device), Device.village_id)
        assert "village_id IN" in str(stmt).replace("\n", " ")

    def test_apply_is_noop_for_super_admin(self):
        base = select(Device)
        assert str(VillageScope.for_super_admin().apply(base, Device.village_id)) == str(base)

    def test_scope_for_role(self):
        # super_admin 은 담당 마을 목록을 줘도 무시하고 전체 접근이다.
        assert scope_for(Role.SUPER_ADMIN.value, [5]).all_villages
        assert not scope_for(Role.VILLAGE_ADMIN.value, [5]).all_villages

    def test_is_immutable(self):
        """권한 객체가 중간에 바뀌면 추적이 불가능해진다."""
        scope = VillageScope.for_villages([1])
        with pytest.raises(dataclasses.FrozenInstanceError):
            scope.all_villages = True  # type: ignore[misc]


# ── 토픽 · MAC ───────────────────────────────────────────────────────────
class TestTopics:
    @pytest.mark.parametrize(
        "raw",
        ["58:E6:C5:F2:CC:74", "58e6c5f2cc74", "58-e6-c5-f2-cc-74", " 58E6C5F2CC74 "],
    )
    def test_normalize_mac_accepts_common_forms(self, raw):
        assert topics.normalize_mac(raw) == "58e6c5f2cc74"

    @pytest.mark.parametrize("bad", ["58e6c5f2cc7", "58e6c5f2cc74ff", "zzzzzzzzzzzz", ""])
    def test_normalize_mac_rejects_bad(self, bad):
        with pytest.raises(ValueError):
            topics.normalize_mac(bad)

    def test_village_token_prefers_12_digit_code(self):
        from app.core.village_token import legacy_token, token_for

        # 레지스트리 사양 §2.4: 법정동코드(10)+연번(2). 코드가 있으면 그것.
        assert token_for(5, "128103302101") == "128103302101"
        # 주소 없는 마을은 예전 방식(id 8자리)으로 — 단말은 자릿수를 가리지 않는다.
        assert token_for(1, None) == "00000001"
        assert legacy_token(12345678) == "12345678"

    def test_next_village_code_fills_first_free_seq(self):
        from app.core.village_token import next_village_code

        assert next_village_code("1281033021", []) == "128103302101"
        # 같은 리에 방송 그룹이 하나 더 — 02
        assert next_village_code("1281033021", ["128103302101"]) == "128103302102"
        # 삭제된 연번은 재사용한다(연번은 식별자가 아니다)
        assert next_village_code("1281033021", ["128103302102"]) == "128103302101"

    def test_unassigned_token_is_all_zeros(self):
        from app.core.village_token import UNASSIGNED_TOKEN

        # 사양 §2.2: 전부 0 이면 미배정 — 단말이 마을 topic 을 구독하지 않는다.
        assert set(UNASSIGNED_TOKEN) == {"0"} and 8 <= len(UNASSIGNED_TOKEN) <= 16

    def test_topic_shapes(self):
        mac = "58e6c5f2cc74"
        assert topics.device_cmd(mac) == "iotradio/device/58e6c5f2cc74/cmd"
        assert topics.village_cmd("128103302101") == "iotradio/village/128103302101/cmd"
        assert topics.all_cmd() == "iotradio/all/cmd"
        assert topics.all_config() == "iotradio/all/config"
        assert topics.device_config(mac) == "iotradio/device/58e6c5f2cc74/config"

    def test_parse_inbound(self):
        assert topics.parse_inbound("iotradio/device/58e6c5f2cc74/status") == (
            "58e6c5f2cc74",
            "status",
        )
        assert topics.parse_inbound("iotradio/device/58e6c5f2cc74/result") == (
            "58e6c5f2cc74",
            "result",
        )

    @pytest.mark.parametrize(
        "topic",
        [
            "iotradio/device/58e6c5f2cc74/cmd",  # 서버가 보낸 것 (구독 안 함)
            "iotradio/all/config",
            "iotradio/device/BADMAC/status",
            "완전히/다른/토픽",
        ],
    )
    def test_parse_inbound_rejects_others(self, topic):
        """구독 범위 밖 메시지에 워커가 죽으면 안 된다 — 예외 대신 None."""
        assert topics.parse_inbound(topic) is None


# ── 발행 정책 ────────────────────────────────────────────────────────────
class FakeConnection:
    """raw_publish 만 기록하는 스텁."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, int, bool]] = []

    async def raw_publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        self.sent.append((topic, payload, qos, retain))


class TestPublisher:
    def test_encode_has_no_whitespace(self):
        """1024B 예산이 빠듯해서 구분자 공백도 아깝다."""
        raw = _encode({"type": "LIVE_START", "session_id": 13})
        assert b", " not in raw and b": " not in raw

    def test_encode_keeps_korean_readable(self):
        raw = _encode({"name": "신동마을"})
        assert json.loads(raw)["name"] == "신동마을"

    async def test_global_config_has_no_type_field(self):
        """통신 사양 §3.5 의 CONFIG payload 에는 type 필드가 없다."""
        conn = FakeConnection()
        await MqttPublisher(conn).publish_global_config(  # type: ignore[arg-type]
            config_version=3, status_interval_sec=30, live_stats_interval_sec=10, event_qos=0
        )
        topic, raw, qos, retain = conn.sent[0]
        payload = json.loads(raw)
        assert topic == "iotradio/all/config"
        assert (qos, retain) == (1, True)
        assert "type" not in payload
        # 마을 배정은 단말별 CONFIG 로 나간다 — 공통 설정에 넣으면 전원이 같은 마을이 된다.
        assert "village_id" not in payload

    async def test_device_config_carries_village_token(self):
        """village_id 는 법정동코드(10)+연번(2) 12자리 문자열이다(레지스트리 사양 §2.4)."""
        conn = FakeConnection()
        await MqttPublisher(conn).publish_device_config(  # type: ignore[arg-type]
            mac="58e6c5f2cc74", village_token="128103302101", config_version=3
        )
        topic, raw, qos, retain = conn.sent[0]
        assert topic == "iotradio/device/58e6c5f2cc74/config"
        assert (qos, retain) == (1, True)
        assert json.loads(raw) == {"config_version": 3, "village_id": "128103302101"}

    async def test_unassign_publishes_all_zero_village(self):
        """미배정은 빈 retain 이 아니라 전부 0 인 village_id 를 명시한다.

        단말은 빈 payload 를 무시해서 지우려고 보내도 이전 배정이 남았다(단말 확인
        2026-09-04, 문제점 18번). 전부 0 은 사양 §2.2 의 미배정 값이다.
        """
        conn = FakeConnection()
        await MqttPublisher(conn).publish_device_config(  # type: ignore[arg-type]
            mac="58e6c5f2cc74", village_token=None, config_version=7
        )
        _, raw, _, retain = conn.sent[0]
        payload = json.loads(raw)
        assert retain is True
        assert payload["config_version"] == 7
        assert set(payload["village_id"]) == {"0"} and 8 <= len(payload["village_id"]) <= 16

    def test_stream_url_must_be_https(self):
        """단말 운영 빌드는 http 스트림을 거절한다 (2026-08-29 실물 확인).

        평문으로 보내면 LIVE_READY ok=false code=BAD_FIELD 로 방송이 아예 시작되지
        않는다. 현장에서는 "준비 완료 0대"로만 보여 원인 파악이 어려우므로
        발행 전에 끊는다.
        """
        from app.mqtt.publisher import STREAM_URL_MAX_BYTES

        # https 는 정상 (포트 없이 — 443)
        ok = MqttPublisher.live_start_payload(job_id=1, stream_url="https://x.co.kr/live/1")
        assert ok["stream_url"] == "https://x.co.kr/live/1"

        # 평문은 운영에서만 거절한다 — 사내 시험(목 단말)은 평문 Icecast 를 쓴다(§6.3)
        from app.config import settings

        original = settings.app_env
        try:
            settings.app_env = "prod"
            with pytest.raises(ApiError):
                MqttPublisher.live_start_payload(job_id=1, stream_url="http://x.co.kr/live/1")
            settings.app_env = "dev"
            dev_ok = MqttPublisher.live_start_payload(job_id=1, stream_url="http://x/live/1")
            assert dev_ok["stream_url"] == "http://x/live/1"
        finally:
            settings.app_env = original

        # 512B 초과도 거절 — 단말이 잘라 쓰지 않고 방송을 거절하기 때문
        too_long = "https://x.co.kr/live/" + "9" * STREAM_URL_MAX_BYTES
        with pytest.raises(ApiError):
            MqttPublisher.live_start_payload(job_id=1, stream_url=too_long)

    def test_second_result_for_same_job_is_not_deduped(self):
        """단말은 한 job 에 LIVE_READY 를 두 번 보낸다 (ESP32 회신 260824 §5.1).

            status=0  P4 출력 준비 완료 (Icecast 접속 *전*)
            status=2  스트림 접속 실패로 abort

        예전 dedup 키(mac:type:job_id)는 두 번째를 중복으로 보고 버렸다 — 서버가
        그 단말을 영영 정상으로 알고, 화면은 "준비 완료"인데 스피커는 조용한
        상태가 된다. OTA_STATUS 도 같은 job_id 로 상태가 여러 번 오므로 같은 문제였다.
        """
        from app.mqtt.handlers import _dedup_key

        ready = {"type": "LIVE_READY", "job_id": 7, "status": 0, "reason": 0}
        abort = {"type": "LIVE_READY", "job_id": 7, "status": 2, "reason": 1}

        k_ready = _dedup_key("aabbcc000000", "LIVE_READY", 7, ready)
        k_abort = _dedup_key("aabbcc000000", "LIVE_READY", 7, abort)
        assert k_ready != k_abort, "상태가 다른 결과가 중복으로 묶이면 안 된다"

        # 진짜 QoS1 재전송(내용 동일)은 여전히 걸러진다
        assert k_abort == _dedup_key("aabbcc000000", "LIVE_READY", 7, dict(abort))

    def test_telemetry_keeps_only_latest_row(self):
        """LIVE_STATS 는 주기 telemetry 라 최신값 1행만 남긴다.

        결과(LIVE_READY 등)와 달리 tick 마다 행을 쌓으면 300대 × 10초 주기에
        방송 10분이면 18,000 행이 되고, 화면의 단말별 응답 목록도 같은 단말이
        계속 늘어난다(DeviceEvent docstring 의 STATUS 정책과 같은 이유).

        그래서 telemetry 는 dedup 키에 payload 를 넣지 않는다 — 값이 달라도
        같은 키로 들어가 기존 행을 덮어쓴다.
        """
        from app.constants import TELEMETRY_RESULTS
        from app.mqtt.handlers import _dedup_key

        assert "LIVE_STATS" in TELEMETRY_RESULTS

        early = {"type": "LIVE_STATS", "job_id": 9, "rx_seq_last": 100, "p4_buffer_ms": 400}
        late = {"type": "LIVE_STATS", "job_id": 9, "rx_seq_last": 400, "p4_buffer_ms": 1600}
        # telemetry 는 payload 없이 키를 만든다 → 값이 달라도 같은 키
        assert _dedup_key("aabbcc000000", "LIVE_STATS", 9) == _dedup_key(
            "aabbcc000000", "LIVE_STATS", 9
        )
        # 결과(payload 포함)와는 키가 겹치지 않아야 한다
        assert _dedup_key("aabbcc000000", "LIVE_STATS", 9) != _dedup_key(
            "aabbcc000000", "LIVE_STATS", 9, early
        )
        assert _dedup_key("aabbcc000000", "LIVE_STATS", 9, early) != _dedup_key(
            "aabbcc000000", "LIVE_STATS", 9, late
        )

    def test_stats_text_summarises_quality(self):
        """LIVE_STATS 는 실패 사유가 아니라 수신 품질로 요약한다.

        소리가 끊긴다는 신고가 오면 버퍼 부족인지 디코딩 오류인지 여기서 가른다.
        """
        from app.modules.broadcast.service import _reason_text, _stats_text

        assert _stats_text({"p4_buffer_ms": 1320}) == "버퍼 1.3초"
        assert _stats_text({"p4_buffer_ms": 1480, "underrun_count": 3}) == "버퍼 1.5초 · 끊김 3"
        # 정상이면 0 인 값은 굳이 늘어놓지 않는다
        assert _stats_text({"p4_buffer_ms": 800, "underrun_count": 0}) == "버퍼 0.8초"
        # telemetry 는 실패 사유 칸에 들어가지 않는다
        assert _reason_text("LIVE_STATS", {"p4_buffer_ms": 1320}) is None

    def test_uplink_grace_is_under_icecast_source_timeout(self):
        """워치독 유예시간은 Icecast source-timeout 보다 작아야 한다.

        순서가 뒤집히면 Icecast 가 먼저 mount 를 지운다. 그러면 단말은 정상
        종료가 아니라 스트림 단절로 끝나고(재접속하지 않는다 — ESP32 정정
        260824), 서버는 mount 가 사라진 것도 모른 채 ON AIR 를 유지한다.
        서버가 LIVE_STOP 을 먼저 보내야 단말이 깨끗하게 정리한다.
        """
        import re
        from pathlib import Path as _P

        from app.config import settings

        xml = _P(__file__).resolve().parents[2] / "infra" / "icecast" / "icecast.xml"
        raw = xml.read_text(encoding="utf-8")
        found = re.search(r"<source-timeout>(\d+)</source-timeout>", raw)
        assert found, "icecast.xml 에 source-timeout 이 없다"
        source_timeout = int(found.group(1))

        grace = settings.live_uplink_grace_sec
        assert 0 < grace < source_timeout, (
            f"유예 {grace}s 는 source-timeout {source_timeout}s 보다 작아야 한다"
        )

    def test_live_ready_accepts_both_result_formats(self):
        """LIVE_READY 성공 판정은 두 형식을 모두 받아야 한다.

            옛  {"status": 0, "reason": 0}
            새  {"ok": true,  "code": "..."}

        새 형식만 오는데 status 로만 판정하면 필드가 없어 항상 실패로 표시된다 —
        소리는 정상인데 화면만 "실패"로 나오는 상태가 된다(2026-08-29 실제 발생).
        """
        from app.modules.broadcast.service import _live_ready_ok

        # 옛 형식
        assert _live_ready_ok({"status": 0, "reason": 0}) is True
        assert _live_ready_ok({"status": 2, "reason": 1}) is False

        # 새 형식
        assert _live_ready_ok({"ok": True}) is True
        assert _live_ready_ok({"ok": False, "code": "BAD_FIELD"}) is False

        # 둘 다 있으면 새 형식이 우선
        assert _live_ready_ok({"ok": True, "status": 2}) is True

        # 판정 근거가 없으면 단정하지 않는다
        assert _live_ready_ok({"job_id": 1}) is None

    def test_device_file_name_strips_non_ascii(self):
        """단말에 보내는 파일명에는 한글이 들어가면 안 된다 (통신 사양 §11.2).

        단말은 `0-9 A-Z a-z - _` 외의 **바이트**를 전부 '_' 로 바꾼다. UTF-8 한글은
        글자당 3바이트라 '___' 가 되고, 바이트 길이가 같은 다른 제목끼리
        구분이 사라진다 — 단말 저장소에 밑줄만 남는다.
        """
        from app.modules.file.service import device_file_name

        # ASCII 는 살리고 뒤에 id 를 붙여 되짚을 수 있게 한다
        assert device_file_name("notice.mp3", 73) == "notice-73.mp3"
        assert device_file_name("test-tone.mp3", 5) == "test-tone-5.mp3"

        # 한글이 섞이면 ASCII 부분만 남긴다
        assert device_file_name("공지 notice.mp3", 73) == "notice-73.mp3"

        # 남는 ASCII 가 없으면 id 로만 식별한다
        assert device_file_name("산불방재 안내.mp3", 73) == "file-73.mp3"
        assert device_file_name("마을회의.mp3", 12) == "file-12.mp3"

        # 어떤 입력이든 결과는 ASCII 여야 한다
        for raw in ("한글.mp3", "공지 notice.mp3", "a b c.mp3", "!!!.mp3"):
            out = device_file_name(raw, 1)
            assert out.isascii(), out
            # epoch 와 -W 는 단말이 붙인다 — 서버가 미리 붙이지 않는다
            assert not out.endswith("-W.mp3")

    def test_result_judgment_new_format(self):
        """신형식(2026-08-27) 결과는 ok 불리언 하나가 성패를 정한다 (사양 §5.4).

        FILE_RESULT 가 FILE_END/FILE_ABORT/FILE_STOP_RESULT 를 대체했고,
        LIVE_RESULT 의 정상 종료는 ok=true code=STOPPED_BY_SERVER 다.
        code 로 판정을 뒤집으면 안 된다 — 정상 종료가 실패로 표시된다.
        """
        from app.modules.broadcast.service import _reason_text

        # 정상 코드는 표시하지 않는다 — 매 행 "OK" 는 실패 사유를 묻는다
        assert _reason_text("FILE_RESULT", {"ok": True, "code": "OK"}) is None
        assert _reason_text("LIVE_RESULT", {"ok": True, "code": "STOPPED_BY_SERVER"}) is None
        # 실패 사유는 그대로 보여준다
        assert _reason_text("FILE_RESULT", {"ok": False, "code": "VERIFY_FAIL"}) == "VERIFY_FAIL"
        assert _reason_text("LIVE_RESULT", {"ok": False, "code": "ABORTED"}) == "ABORTED"
        # 구형식 fail_reason 도 여전히 읽는다
        assert _reason_text("FILE_END", {"fail_reason": "SHA256_FAIL"}) == "SHA256_FAIL"

    def test_file_broadcast_size_and_duration_limits(self):
        """FILE_START 발행 전에 단말 상한을 서버가 먼저 검사한다 (사양 §11).

        크기 2.5MiB 초과는 단말이 다운로드 전에 거절하고, 10분 초과는 크기가
        상한 안이어도 단말 워치독(10분 30초)이 재생을 끊는다. 어느 쪽이든
        현장에서는 원인 없는 실패로만 보이므로 발행 전에 끊는다.
        """
        from app.modules.broadcast.service import (
            FILE_MAX_BYTES,
            FILE_MAX_DURATION_SEC,
            _validate_file_for_broadcast,
        )

        # 정상: 상한 이내
        _validate_file_for_broadcast(FILE_MAX_BYTES, float(FILE_MAX_DURATION_SEC))
        # 길이를 모르는 파일(ffprobe 실패)은 크기만 본다
        _validate_file_for_broadcast(1024, None)

        with pytest.raises(ApiError):
            _validate_file_for_broadcast(FILE_MAX_BYTES + 1, 60.0)
        with pytest.raises(ApiError):
            _validate_file_for_broadcast(1024, FILE_MAX_DURATION_SEC + 1.0)

    def test_cmd_payloads_use_job_id_only(self):
        """CMD 의 job 식별자는 job_id 하나다 (통신 사양 2026-08-20 통일).

        이름이 틀리면 단말은 필드를 조용히 무시하고 기본값으로 동작한다 —
        서버는 발행 성공으로 알고, 현장에서는 방송이 안 나간다. 옛 이름
        (session_id/cmd_id/file_id)이 되살아나는 걸 여기서 막는다.
        """
        payloads = [
            MqttPublisher.file_start_payload(
                job_id=7,
                size=1024,
                sha256="a" * 64,
                url="http://x/dl/t",
                file_name="a.mp3",
                store_flash=False,
                autoplay=True,
            ),
            MqttPublisher.file_stop_payload(job_id=7),
            MqttPublisher.live_start_payload(job_id=7, stream_url="https://x/live/00000001/7"),
            MqttPublisher.live_stop_payload(job_id=7),
        ]
        for p in payloads:
            assert p["job_id"] == 7, p
            # file_id 는 단말이 echo 만 하고 안 써서 사양에서 삭제됐다.
            assert not {"session_id", "cmd_id", "file_id"} & set(p), p

    async def test_oversized_payload_is_blocked_before_send(self):
        """단말이 못 받는 크기다. 보내고 성공했다고 착각하는 게 최악이다."""
        conn = FakeConnection()
        pub = MqttPublisher(conn)  # type: ignore[arg-type]
        with pytest.raises(PayloadTooLarge):
            await pub.publish_command(
                payload={"type": "FILE_START", "url": "x" * MQTT_MAX_PAYLOAD_BYTES},
                target_scope=TargetScope.DEVICE,
                scope=VillageScope.for_super_admin(),
                macs=["58e6c5f2cc74"],
            )
        assert conn.sent == []

    async def test_cmd_never_retains(self):
        """cmd 에 retain 을 걸면 단말 재접속 때 지난 방송이 되살아난다."""
        conn = FakeConnection()
        await MqttPublisher(conn).publish_command(  # type: ignore[arg-type]
            payload={"type": "LIVE_STOP", "job_id": 1},
            target_scope=TargetScope.VILLAGE,
            scope=VillageScope.for_super_admin(),
            village_ids=[1],
            village_tokens={v: str(v).zfill(8) for v in [1]},
        )
        assert conn.sent[0][3] is False

    async def test_zone_fans_out_to_each_device(self):
        """구역은 단말이 모르는 개념이라 MAC 별로 펼쳐 보낸다."""
        conn = FakeConnection()
        macs = ["58e6c5f2cc74", "58e6c5f2cc75"]
        sent = await MqttPublisher(conn).publish_command(  # type: ignore[arg-type]
            payload={"type": "FILE_STOP"},
            target_scope=TargetScope.ZONE,
            scope=VillageScope.for_super_admin(),
            macs=macs,
        )
        assert sent == [f"iotradio/device/{m}/cmd" for m in macs]

    async def test_village_admin_cannot_broadcast_to_all(self):
        conn = FakeConnection()
        with pytest.raises(Exception) as exc:
            await MqttPublisher(conn).publish_command(  # type: ignore[arg-type]
                payload={"type": "LIVE_STOP"},
                target_scope=TargetScope.ALL,
                scope=VillageScope.for_villages([1]),
            )
        assert "SUPER_ADMIN_REQUIRED" in str(exc.value.code)  # type: ignore[union-attr]
        assert conn.sent == []

    async def test_village_admin_cannot_target_other_village(self):
        conn = FakeConnection()
        with pytest.raises(VillageOutOfScope):
            await MqttPublisher(conn).publish_command(  # type: ignore[arg-type]
                payload={"type": "LIVE_STOP"},
                target_scope=TargetScope.VILLAGE,
                scope=VillageScope.for_villages([1]),
                village_ids=[2],
            )
        assert conn.sent == []


# ── 인증 ─────────────────────────────────────────────────────────────────
class TestSecurity:
    def test_hash_roundtrip(self):
        hashed = hash_password("village1234!")
        assert hashed != "village1234!"
        assert verify_password("village1234!", hashed)
        assert not verify_password("wrong", hashed)

    def test_hash_is_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_long_password_rejected_not_silently_truncated(self):
        """bcrypt 는 72바이트 초과분을 조용히 버린다. 그러면 뒷부분이 비밀번호 역할을 못 한다."""
        with pytest.raises(ValueError):
            hash_password("a" * (MAX_PASSWORD_BYTES + 1))

    def test_broken_hash_is_auth_failure_not_crash(self):
        assert not verify_password("x", "이건-bcrypt-해시가-아니다")

    def test_token_roundtrip(self):
        token, expires_in = create_access_token(user_id=7, username="admin", role="super_admin")
        claims = decode_access_token(token)
        assert claims["sub"] == "7"
        assert claims["role"] == "super_admin"
        assert expires_in > 0

    def test_tampered_token_rejected(self):
        token, _ = create_access_token(user_id=1, username="a", role="village_admin")
        with pytest.raises(Unauthorized):
            decode_access_token(token[:-4] + "AAAA")


# ── 단말별 MQTT 계정 ─────────────────────────────────────────────────────
class TestMqttAccounts:
    """계정 사양(SERVER_DEVICE_CREDENTIAL_SPEC §1)과 passwd 재생성 규칙.

    비밀번호 규칙이 틀리면 시리얼 전송이 `@END` 에서 잘리거나(@) 단말 화면에서
    입력할 수 없는 문자가 나온다 — 라인에서만 드러나는 실패라 여기서 잡는다.
    """

    def test_password_is_8_chars_from_fixed_charset(self):
        from app.core import mqtt_accounts

        for _ in range(200):
            pw = mqtt_accounts.generate_device_password()
            assert len(pw) == 8
            assert all(c in mqtt_accounts.GENERATION_CHARSET for c in pw)

    def test_charset_excludes_serial_breakers(self):
        """`@` 는 시리얼 @END 충돌, `!` 는 서버가 쓰지 않기로 확정(사양 §1).

        생성에서는 `=` 도 뺀다 — `@KEY=VALUE` 파서가 값 안의 `=` 를 어디서
        자르느냐에 따라 비밀번호가 조용히 잘릴 수 있다(2026-08-31).
        """
        from app.core import mqtt_accounts

        assert "@" not in mqtt_accounts.PASSWORD_CHARSET
        assert "!" not in mqtt_accounts.PASSWORD_CHARSET
        assert "=" not in mqtt_accounts.GENERATION_CHARSET
        # 검증 집합은 사양 원문 그대로 — 기존 발급분(= 포함)을 거부하면 안 된다.
        assert "=" in mqtt_accounts.PASSWORD_CHARSET

    def test_hash_is_mosquitto_pbkdf2_format(self):
        """$7$101$<salt>$<hash> — eclipse-mosquitto:2 인증 실측 통과 형식."""
        import base64

        from app.core import mqtt_accounts

        h = mqtt_accounts.mosquitto_hash("aB3#x9._")
        _, ident, iterations, salt, digest = h.split("$")
        assert ident == "7"
        assert iterations == "101"
        assert len(base64.b64decode(salt)) == 12
        assert len(base64.b64decode(digest)) == 64

    def test_render_passwd_has_server_shared_and_devices(self, monkeypatch):
        from app.config import settings
        from app.core import mqtt_accounts

        monkeypatch.setattr(settings, "mqtt_username", "xwifi-server")
        monkeypatch.setattr(settings, "mqtt_password", "serverpw")
        monkeypatch.setattr(settings, "mqtt_device_password", "sharedpw")
        content = mqtt_accounts.render_passwd(
            {"58e6c5f2cc74": "aB3#x9._", "aabbccddeeff": "zZ9~q.-1"}
        )
        users = [line.split(":", 1)[0] for line in content.splitlines() if ":" in line]
        assert users == ["xwifi-server", "xwifi-device", "58e6c5f2cc74", "aabbccddeeff"]
        # 평문 비밀번호가 파일에 실리면 안 된다 — 해시만 나간다.
        assert "aB3#x9._" not in content
        assert "serverpw" not in content

    def test_render_passwd_drops_shared_account_when_unset(self, monkeypatch):
        """.env 에서 MQTT_DEVICE_PASSWORD 를 지우면 공유 계정이 빠진다(이행 종료)."""
        from app.config import settings
        from app.core import mqtt_accounts

        monkeypatch.setattr(settings, "mqtt_username", "xwifi-server")
        monkeypatch.setattr(settings, "mqtt_password", "serverpw")
        monkeypatch.setattr(settings, "mqtt_device_password", None)
        content = mqtt_accounts.render_passwd({"58e6c5f2cc74": "aB3#x9._"})
        assert "xwifi-device" not in content

    def test_export_noop_when_disabled(self, monkeypatch):
        """경로 미설정(개발·테스트)이면 아무 파일도 만들지 않는다."""
        from app.config import settings
        from app.core import mqtt_accounts

        monkeypatch.setattr(settings, "mosquitto_passwd_export", None)
        assert mqtt_accounts.export_passwd({"58e6c5f2cc74": "pw"}) is False

    def test_export_writes_file_atomically(self, monkeypatch, tmp_path):
        from app.config import settings
        from app.core import mqtt_accounts

        target = tmp_path / "passwd.generated"
        monkeypatch.setattr(settings, "mqtt_username", "xwifi-server")
        monkeypatch.setattr(settings, "mqtt_password", "serverpw")
        monkeypatch.setattr(settings, "mqtt_device_password", None)
        monkeypatch.setattr(settings, "mosquitto_passwd_export", str(target))
        assert mqtt_accounts.export_passwd({"58e6c5f2cc74": "aB3#x9._"}) is True
        content = target.read_text(encoding="utf-8")
        assert "58e6c5f2cc74:$7$101$" in content
        # 임시 파일이 남지 않는다.
        assert [p.name for p in tmp_path.iterdir()] == ["passwd.generated"]

    def test_export_failure_returns_false_not_raise(self, monkeypatch):
        """파일을 못 써도 등록 API 가 500 이 되면 안 된다 — DB 가 정본이다."""
        from app.config import settings
        from app.core import mqtt_accounts

        monkeypatch.setattr(
            settings, "mosquitto_passwd_export", "/no/such/dir/passwd.generated"
        )
        assert mqtt_accounts.export_passwd({"58e6c5f2cc74": "pw"}) is False

    def test_enforcement_only_after_shared_account_removed(self, monkeypatch):
        """이행기(공유 계정 유지)에는 계정 미발행 단말을 방송 대상에서 빼지 않는다."""
        from app.config import settings
        from app.modules.device.service import _device_accounts_enforced

        monkeypatch.setattr(settings, "mosquitto_passwd_export", "/x/passwd.generated")
        monkeypatch.setattr(settings, "mqtt_device_password", "sharedpw")
        assert _device_accounts_enforced() is False  # 이행기
        monkeypatch.setattr(settings, "mqtt_device_password", None)
        assert _device_accounts_enforced() is True  # 전환 완료
        monkeypatch.setattr(settings, "mosquitto_passwd_export", None)
        assert _device_accounts_enforced() is False  # 개발·테스트

    def test_device_create_rejects_bad_password_chars(self):
        """등록 payload 의 사전 발급 비밀번호 — `@` 등 사양 밖 문자는 400."""
        import pydantic

        from app.schemas.device import DeviceCreate

        ok = DeviceCreate(mac="58e6c5f2cc74", mqtt_password="aB3#x9._")
        assert ok.mqtt_password == "aB3#x9._"
        with pytest.raises(pydantic.ValidationError):
            DeviceCreate(mac="58e6c5f2cc74", mqtt_password="aB3@x9._")
        # 스캔 필드도 같이 실리는 형태
        d = DeviceCreate(
            mac="58e6c5f55230",
            p4_model="IOT-1000", p4_version="V.260823-1",
            c6_model="IOT-1000C6", c6_version="V.260823-1",
        )
        assert d.p4_model == "IOT-1000" and d.mqtt_password is None

    def test_server_host_strips_scheme_and_port(self, monkeypatch):
        """@SERVER 는 host 만 받는다 — 스킴·포트가 붙으면 단말이 못 붙는다."""
        from app.config import settings
        from app.core import mqtt_accounts

        cases = {
            "https://hanna-aircast.co.kr": "hanna-aircast.co.kr",
            "https://hanna-aircast.co.kr/": "hanna-aircast.co.kr",
            "http://192.168.0.5:8080": "192.168.0.5",
            # 스킴 없이 적힌 값도 받아준다(.env 실수 대비)
            "hanna-aircast.co.kr:8080": "hanna-aircast.co.kr",
        }
        for raw, expected in cases.items():
            monkeypatch.setattr(settings, "public_base_url", raw)
            assert mqtt_accounts.server_host() == expected, raw


# ── 카카오 주소 검색 ─────────────────────────────────────────────────────
class TestKakaoGeo:
    @pytest.mark.asyncio
    async def test_parses_documents_and_handles_ri_only(self, monkeypatch):
        """리 단위 검색(도로명 없음, h_code 없음)도 좌표·b_code 가 뽑혀야 한다.

        2026-08-31 실측 응답 형태 기준 — 무안군 청계면 월선리 b_code=1281033021
        (전남광주 통합으로 시도 코드 46→12, 지도 설계 §1.1).
        """
        from app.config import settings
        from app.core import kakao_geo

        monkeypatch.setattr(settings, "kakao_rest_api_key", "test-key")
        sample = {
            "documents": [
                {
                    "address_name": "전남광주통합특별시 무안군 청계면 월선리",
                    "x": "126.451371306622",
                    "y": "34.8908358250448",
                    "address": {
                        "address_name": "전남광주통합특별시 무안군 청계면 월선리",
                        "b_code": "1281033021",
                    },
                    "road_address": None,
                },
                {  # 좌표 없는 행은 버린다
                    "address_name": "이상한 행",
                    "address": {},
                    "road_address": None,
                },
            ]
        }
        monkeypatch.setattr(kakao_geo, "_request_kakao", lambda q: sample)
        results = await kakao_geo.search_address("월선리")
        assert len(results) == 1
        r = results[0]
        assert r.b_code == "1281033021"
        assert r.road_address is None
        assert abs(r.lat - 34.89083) < 0.001 and abs(r.lng - 126.45137) < 0.001

    @pytest.mark.asyncio
    async def test_missing_key_raises_clear_error(self, monkeypatch):
        from app.config import settings
        from app.core import kakao_geo

        monkeypatch.setattr(settings, "kakao_rest_api_key", None)
        with pytest.raises(kakao_geo.KakaoKeyMissing):
            await kakao_geo.search_address("서울")


# ── 마을별 ACL 생성 ─────────────────────────────────────────────────────
class TestAclRender:
    """통신 사양 §2.1 "village/<id>/cmd 는 별도 규칙" — 단말마다 자기 마을만.

    와일드카드(`village/+/cmd`)가 다시 들어오면 계정 하나로 전 마을 명령을
    구독할 수 있게 된다(C-1). 여기서 영구히 막는다.
    """

    def test_acl_has_no_village_wildcard_and_one_village_per_device(self, monkeypatch):
        from app.config import settings
        from app.core import mqtt_accounts

        monkeypatch.setattr(settings, "mqtt_username", "xwifi-server")
        acl = mqtt_accounts.render_acl(
            {"58e6c5f2cc74": "128103302101", "aabbccddeeff": "00000012", "001122334455": None}
        )
        assert "village/+/cmd" not in acl
        assert "user xwifi-server\ntopic readwrite iotradio/#" in acl
        # 공통 토픽과 %u 규칙은 치환 없는/있는 pattern 으로
        assert "pattern read iotradio/all/cmd" in acl
        assert "pattern write iotradio/device/%u/status" in acl
        # 배정된 단말은 자기 마을 한 줄만 — 12자리 코드(주소 없는 마을은 legacy 8자리)
        assert "user 58e6c5f2cc74\ntopic read iotradio/village/128103302101/cmd" in acl
        assert "user aabbccddeeff\ntopic read iotradio/village/00000012/cmd" in acl
        # 미배정 단말은 마을 줄이 없다(블록 자체를 만들지 않는다)
        assert "user 001122334455" not in acl
        # %c 규칙은 폐기됐다 — 다시 들어오면 client_id 사칭 구멍이 열린다
        assert "%c" not in acl

    def test_export_acl_beside_passwd(self, monkeypatch, tmp_path):
        from app.config import settings
        from app.core import mqtt_accounts

        monkeypatch.setattr(settings, "mqtt_username", "xwifi-server")
        monkeypatch.setattr(settings, "mosquitto_passwd_export", str(tmp_path / "passwd.generated"))
        assert mqtt_accounts.export_acl({"58e6c5f2cc74": "128103302101"}) is True
        out = (tmp_path / "aclfile.generated").read_text(encoding="utf-8")
        assert "iotradio/village/128103302101/cmd" in out
        # 기능이 꺼져 있으면(개발) 아무것도 쓰지 않는다
        monkeypatch.setattr(settings, "mosquitto_passwd_export", None)
        assert mqtt_accounts.export_acl({"58e6c5f2cc74": "128103302101"}) is False


# ── 파일 서빙 (X-Accel-Redirect) ─────────────────────────────────────────
class TestServeFile:
    """단말 다운로드 바이트는 운영에서 nginx 가 보낸다. 백엔드는 헤더만 얹는다."""

    def _file(self, tmp_path, monkeypatch, *, on_disk=True):
        from app.config import settings
        from app.models.file import File

        monkeypatch.setattr(settings, "file_root", tmp_path)
        file = File(
            filename="산불 안내.mp3",
            size_bytes=3,
            sha256="0" * 64,
            source="upload",
            storage_path="upload/2026/09/abc.mp3",
        )
        if on_disk:
            path = tmp_path / "upload" / "2026" / "09" / "abc.mp3"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"mp3")
        return file

    def test_accel_redirect_when_configured(self, monkeypatch, tmp_path):
        from app.config import settings
        from app.modules.file import service

        monkeypatch.setattr(settings, "file_accel_location", "/_files/")
        resp = service.serve_file(self._file(tmp_path, monkeypatch))
        # 바이트는 없고, nginx 가 볼 internal 경로만 있다
        assert resp.body == b""
        assert resp.headers["x-accel-redirect"] == "/_files/upload/2026/09/abc.mp3"
        assert resp.headers["content-type"] == "audio/mpeg"
        assert "filename*=UTF-8''" in resp.headers["content-disposition"]

    def test_accel_path_is_url_quoted(self, monkeypatch, tmp_path):
        from app.config import settings
        from app.modules.file import service

        monkeypatch.setattr(settings, "file_accel_location", "/_files")
        file = self._file(tmp_path, monkeypatch, on_disk=False)
        file.storage_path = "tts/한 글.mp3"
        (tmp_path / "tts").mkdir()
        (tmp_path / "tts" / "한 글.mp3").write_bytes(b"x")
        resp = service.serve_file(file)
        target = resp.headers["x-accel-redirect"]
        assert target.startswith("/_files/tts/")
        assert " " not in target and "한" not in target

    def test_direct_file_response_without_nginx(self, monkeypatch, tmp_path):
        from pathlib import Path

        from fastapi.responses import FileResponse

        from app.config import settings
        from app.modules.file import service

        monkeypatch.setattr(settings, "file_accel_location", "")
        resp = service.serve_file(self._file(tmp_path, monkeypatch))
        assert isinstance(resp, FileResponse)
        assert Path(resp.path) == tmp_path / "upload" / "2026" / "09" / "abc.mp3"

    def test_missing_on_disk_is_404(self, monkeypatch, tmp_path):
        from app.config import settings
        from app.modules.file import service

        monkeypatch.setattr(settings, "file_accel_location", "/_files/")
        with pytest.raises(service.FileNotFound) as exc:
            service.serve_file(self._file(tmp_path, monkeypatch, on_disk=False))
        assert exc.value.status_code == 404
        assert exc.value.code == "FILE_MISSING_ON_DISK"


# ── 방송 트래픽 추정 (bytes_estimated) ────────────────────────────────────
class TestBroadcastByteEstimate:
    """단말이 전송 바이트 수를 보고하지 않아 서버가 추정한다 (2026-09-02, D-1/A-8)."""

    def test_live_bytes_scale_with_duration_and_recipients(self):
        from app.modules.broadcast.service import estimate_live_bytes

        # 24kbps = 3000 bytes/sec. 43초 · 단말 1대.
        assert estimate_live_bytes(43.0, 1, 24) == 43 * 3000
        # 단말이 늘면 그만큼 배가된다 — 각자 별도 스트림을 받는다.
        assert estimate_live_bytes(43.0, 3, 24) == 43 * 3000 * 3

    def test_live_bytes_follow_the_setting(self):
        from app.modules.broadcast.service import estimate_live_bytes

        # 설정을 16 으로 낮추면 추정도 따라와야 한다. 예전에는 24 로 박혀 있어
        # 16kbps 로 방송해도 트래픽·비용 지표가 1.5배로 부풀었다.
        assert estimate_live_bytes(43.0, 1, 16) == 43 * 2000
        assert estimate_live_bytes(60.0, 2, 16) < estimate_live_bytes(60.0, 2, 24)

    def test_live_bytes_no_recipients_is_zero(self):
        from app.modules.broadcast.service import estimate_live_bytes

        # 아무도 못 받았으면(전부 오프라인) 시간이 있어도 트래픽은 0.
        assert estimate_live_bytes(120.0, 0, 24) == 0

    def test_live_bytes_never_negative(self):
        from app.modules.broadcast.service import estimate_live_bytes

        # 방어적 하한 — 시계가 역행해도(NTP 보정 등) 음수 트래픽은 말이 안 된다.
        assert estimate_live_bytes(-5.0, 2, 24) == 0

    def test_file_bytes_is_size_times_recipients(self):
        from app.modules.broadcast.service import estimate_file_bytes

        assert estimate_file_bytes(644_252, 4) == 644_252 * 4

    def test_file_bytes_no_recipients_is_zero(self):
        from app.modules.broadcast.service import estimate_file_bytes

        assert estimate_file_bytes(644_252, 0) == 0


# ── STATUS 버퍼 (A-2/A-3) ────────────────────────────────────────────────
class TestStatusBuffer:
    """주기 STATUS 를 모아 쓰는 버퍼. DB 를 안 타는 적재 규칙만 여기서 본다."""

    def _buffer(self):
        from app.mqtt.status_buffer import StatusBuffer

        return StatusBuffer(interval_sec=1.0)

    def test_same_mac_coalesces_to_latest(self):
        import datetime as dt

        buf = self._buffer()
        t1 = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)
        buf.offer("aabbccddee01", payload={"state": "IDLE"}, seen_at=t1)
        buf.offer("aabbccddee01", payload={"state": "LIVE"}, seen_at=t1)
        # 같은 단말이 아무리 자주 보내도 대기 항목은 하나다 — 메모리가 단말 수에 유계.
        assert buf.pending_count == 1

    def test_different_macs_accumulate(self):
        import datetime as dt

        buf = self._buffer()
        now = dt.datetime.now(dt.timezone.utc)
        for i in range(5):
            buf.offer(f"aabbccddee{i:02d}", payload={"state": "IDLE"}, seen_at=now)
        assert buf.pending_count == 5

    def test_discard_drops_pending(self):
        """LWT 처리가 먼저 이겨야 한다 — 안 그러면 죽은 단말이 온라인으로 되살아난다."""
        import datetime as dt

        buf = self._buffer()
        now = dt.datetime.now(dt.timezone.utc)
        buf.offer("aabbccddee01", payload={"state": "LIVE"}, seen_at=now)
        buf.discard("aabbccddee01")
        assert buf.pending_count == 0
        # 없는 MAC 을 버려도 조용히 넘어간다
        buf.discard("ffffffffffff")

    def test_max_pending_is_bounded_by_device_count(self):
        import datetime as dt

        from app.mqtt.status_buffer import StatusBuffer

        buf = StatusBuffer(interval_sec=60.0, max_pending=3)
        now = dt.datetime.now(dt.timezone.utc)
        for i in range(10):
            buf.offer(f"aabbccddee{i:02d}", payload={}, seen_at=now)
        # 상한을 넘으면 주기를 기다리지 않고 깨우도록 표시한다(적재 자체는 막지 않는다).
        assert buf.pending_count == 10


class TestResyncDecision:
    """CONFIG 불일치 판정 — 즉시 경로와 버퍼 경로가 같은 함수를 쓴다."""

    def test_matching_echo_needs_no_resync(self):
        from app.mqtt.status_buffer import needs_resync

        assert not needs_resync(
            reported_village="00000005",
            expected_village="00000005",
            reported_version=3,
            expected_version=3,
        )

    def test_village_mismatch_triggers_resync(self):
        from app.mqtt.status_buffer import needs_resync

        # 배정은 됐는데 단말이 못 받은 상태 — retain 유실 등.
        assert needs_resync(
            reported_village="",
            expected_village="00000005",
            reported_version=3,
            expected_version=3,
        )

    def test_version_mismatch_triggers_resync(self):
        from app.mqtt.status_buffer import needs_resync

        assert needs_resync(
            reported_village="00000005",
            expected_village="00000005",
            reported_version=2,
            expected_version=3,
        )

    def test_cooldown_blocks_repeat_publish(self):
        import datetime as dt

        from app.mqtt import status_buffer as sb

        sb._last_resync.clear()
        now = dt.datetime.now(dt.timezone.utc)
        assert sb.resync_allowed("aabbccddee01", now) is True
        # 낡은 펌웨어가 영영 적용하지 않으면 STATUS 마다 재발행이 나간다 — 그걸 막는다.
        assert sb.resync_allowed("aabbccddee01", now + dt.timedelta(seconds=5)) is False
        assert sb.resync_allowed("aabbccddee01", now + dt.timedelta(seconds=61)) is True
        sb._last_resync.clear()


# ── 방송 종료 판정 (문제점 3·4·5번) ──────────────────────────────────────
class TestTerminalResults:
    """어떤 결과가 "이 단말은 끝났다"인가. 잘못 넣으면 방송이 조기 종료된다."""

    def test_terminal_set_matches_spec(self):
        from app.modules.broadcast.service import TERMINAL_RESULTS

        # 통신 사양 §5.4: 종료를 말하는 건 이 둘뿐이다.
        assert {"FILE_RESULT", "LIVE_RESULT"} == TERMINAL_RESULTS

    def test_live_ready_is_not_terminal(self):
        from app.modules.broadcast.service import TERMINAL_RESULTS

        # LIVE_READY 는 "P4 오디오 준비 완료"지 방송이 끝났다는 뜻이 아니다.
        # 여기 들어가면 준비되자마자 방송이 종료돼 버린다.
        assert "LIVE_READY" not in TERMINAL_RESULTS
        # 진행 알림도 마찬가지다.
        assert "OTA_PROGRESS" not in TERMINAL_RESULTS
        assert "LIVE_STATS" not in TERMINAL_RESULTS


class TestServerOnlyConfig:
    """중지 응답 대기는 서버 설정이다 — 단말로 나가지 않는다."""

    def test_stop_wait_is_not_a_device_field(self):
        from app.constants import DEVICE_CONFIG_FIELDS

        # 여기 들어가면 이 값만 바꿔도 config_version 이 올라가고 전 단말이
        # 내용상 같은 CONFIG 를 다시 받는다.
        assert "file_wait_sec" not in DEVICE_CONFIG_FIELDS
        assert "live_stop_wait_sec" not in DEVICE_CONFIG_FIELDS

    def test_device_fields_are_the_spec_three(self):
        from app.constants import DEVICE_CONFIG_FIELDS

        assert {
            "status_interval_sec",
            "live_stats_interval_sec",
            "event_qos",
        } == DEVICE_CONFIG_FIELDS

    def test_live_stop_wait_range_is_10_to_30(self):
        from app.constants import CONFIG_LIMITS

        # 문제점 리스트 5번이 요구한 범위. 화면·API 가 같은 값으로 막는다.
        assert CONFIG_LIMITS["live_stop_wait_sec"] == (10, 30)

    def test_ready_timeout_matches_device_spec_range(self):
        from app.constants import CONFIG_LIMITS, DEVICE_CONFIG_FIELDS

        # 사양: ready_timeout_sec 는 1~60, 벗어나면 단말이 기본 30 으로 되돌린다.
        assert CONFIG_LIMITS["live_ready_timeout_sec"] == (1, 60)
        # LIVE_START 필드로 나가는 값이라 CONFIG 토픽 필드가 아니다 — 바꿔도
        # config_version 이 올라가면 안 된다.
        assert "live_ready_timeout_sec" not in DEVICE_CONFIG_FIELDS

    def test_file_wait_range_follows_device_measurement(self):
        from app.constants import CONFIG_LIMITS

        # 단말 확인(2026-09-04, 문제점 19번): FILE_RESULT 는 받고 검증 완료 시점이고
        # 저장은 백그라운드라 크기와 무관(716KB 3.6초). 기본 30, 10~60.
        # 시작·중지에 같이 쓴다(문제점 8번).
        assert CONFIG_LIMITS["file_wait_sec"] == (10, 60)
        # 셋이어야 한다(단말 요청 §3.4) — 파일을 시작/중지로 쪼개면 넷이 된다.
        timing = {k for k in CONFIG_LIMITS if k.endswith("_sec") and "interval" not in k}
        assert timing == {"live_ready_timeout_sec", "live_stop_wait_sec", "file_wait_sec"}

    def test_every_config_field_has_a_range(self):
        from app.constants import CONFIG_LIMITS, DEVICE_CONFIG_FIELDS

        # 범위를 빠뜨린 설정이 있으면 _check_range 가 KeyError 로 터진다.
        assert set(CONFIG_LIMITS) >= DEVICE_CONFIG_FIELDS


# ── 마을 경계 도형 변환 (지도 4단계) ─────────────────────────────────────
def _boundary_module():
    """scripts/import_boundaries.py 를 불러온다.

    담당자 PC 에서 돌리는 오프라인 도구라 backend 패키지 밖에 있다. pyshp·pyproj
    없이도 도형 계산 부분은 import 되게 해 뒀으므로(그 모듈 상단 주석) CI 에서도 돈다.
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "import_boundaries.py"
    spec = importlib.util.spec_from_file_location("import_boundaries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRingArea:
    """넓이 부호로 바깥 고리와 구멍을 가른다 — 부호가 뒤집히면 경계가 사라진다."""

    def test_clockwise_is_negative(self):
        mod = _boundary_module()
        # SHP 규약: 바깥 고리는 시계방향. y 가 위로 가는 좌표계에서 넓이가 음수다.
        clockwise = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
        assert mod.ring_area(clockwise) < 0

    def test_counter_clockwise_is_positive(self):
        mod = _boundary_module()
        # 반시계 = 구멍.
        counter = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        assert mod.ring_area(counter) > 0

    def test_unit_square_area_is_one(self):
        mod = _boundary_module()
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        assert abs(abs(mod.ring_area(square)) - 1.0) < 1e-12


class TestSimplify:
    """Douglas-Peucker. 여기가 틀리면 경계가 조용히 일그러진다."""

    def test_collinear_points_are_dropped(self):
        mod = _boundary_module()
        line = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
        # 직선 위의 중간 점들은 형태에 기여하지 않는다.
        assert mod.simplify(line, 0.1) == [(0.0, 0.0), (3.0, 0.0)]

    def test_corner_is_kept(self):
        mod = _boundary_module()
        corner = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]
        # 허용 오차보다 크게 튀어나온 꼭짓점은 남아야 한다.
        assert mod.simplify(corner, 0.1) == corner

    def test_bump_below_tolerance_is_dropped(self):
        mod = _boundary_module()
        bump = [(0.0, 0.0), (1.0, 0.001), (2.0, 0.0)]
        assert mod.simplify(bump, 0.1) == [(0.0, 0.0), (2.0, 0.0)]

    def test_endpoints_never_dropped(self):
        mod = _boundary_module()
        pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
        out = mod.simplify(pts, 999.0)
        assert out[0] == pts[0] and out[-1] == pts[-1]

    def test_short_input_untouched(self):
        mod = _boundary_module()
        # 점이 둘뿐이면 줄일 게 없다(빈 배열을 돌려주면 고리가 사라진다).
        assert mod.simplify([(0.0, 0.0), (1.0, 1.0)], 0.1) == [(0.0, 0.0), (1.0, 1.0)]


class TestShapeToGeometry:
    """SHP 의 parts 를 GeoJSON Polygon/MultiPolygon 으로 가른다."""

    class _Shape:
        def __init__(self, parts, points):
            self.parts = parts
            self.points = points

    @staticmethod
    def _identity(x, y):
        return (x, y)

    def _square(self, x0, y0, size=1.0):
        """시계방향 사각형 = 바깥 고리."""
        return [(x0, y0), (x0, y0 + size), (x0 + size, y0 + size), (x0 + size, y0), (x0, y0)]

    def test_single_ring_becomes_polygon(self):
        mod = _boundary_module()
        shape = self._Shape([0], self._square(0, 0))
        geometry = mod.shape_to_geometry(shape, self._identity, 1e-9)
        assert geometry["type"] == "Polygon"
        ring = geometry["coordinates"][0]
        assert ring[0] == ring[-1], "고리는 닫혀 있어야 한다"

    def test_two_outer_rings_become_multipolygon(self):
        mod = _boundary_module()
        # 섬이 있는 리 — 바깥 고리가 둘이면 MultiPolygon 이다.
        first, second = self._square(0, 0), self._square(10, 10)
        shape = self._Shape([0, len(first)], first + second)
        geometry = mod.shape_to_geometry(shape, self._identity, 1e-9)
        assert geometry["type"] == "MultiPolygon"
        assert len(geometry["coordinates"]) == 2

    def test_hole_stays_with_its_outer_ring(self):
        mod = _boundary_module()
        outer = self._square(0, 0, 10)
        # 반시계 = 구멍. 앞선 바깥 고리에 붙어야 한다(별도 폴리곤이 되면 안 된다).
        hole = [(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0), (2.0, 2.0)]
        shape = self._Shape([0, len(outer)], outer + hole)
        geometry = mod.shape_to_geometry(shape, self._identity, 1e-9)
        assert geometry["type"] == "Polygon"
        assert len(geometry["coordinates"]) == 2, "바깥 고리 + 구멍 두 개여야 한다"

    def test_degenerate_shape_returns_none(self):
        mod = _boundary_module()
        # 점 세 개짜리 자투리는 도형이 아니다 — None 이면 호출부가 건너뛴다.
        shape = self._Shape([0], [(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)])
        assert mod.shape_to_geometry(shape, self._identity, 1e-9) is None

    def test_transform_is_applied(self):
        mod = _boundary_module()
        shape = self._Shape([0], self._square(0, 0))
        geometry = mod.shape_to_geometry(shape, lambda x, y: (x + 100, y + 200), 1e-9)
        for lng, lat in geometry["coordinates"][0]:
            assert lng >= 100 and lat >= 200


# ── 종료된 job 의 늦은 telemetry 버리기 (단말 요청 2026-09-03 §2.5) ──────────
class TestEndedJobs:
    """정지 뒤에 도착한 LIVE_STATS 를 적재하면 끝난 방송이 되살아나 보인다."""

    def setup_method(self):
        from app.modules.broadcast import service

        service.ENDED_JOBS.clear()

    def test_marked_job_is_ended(self):
        from app.modules.broadcast import service

        service.mark_job_ended(95)
        assert service.is_job_ended(95)
        assert not service.is_job_ended(96)

    def test_none_job_is_never_ended(self):
        from app.modules.broadcast import service

        # job_id 를 못 뽑은 메시지는 어느 방송의 것인지 모른다 — 버리지 않는다.
        service.mark_job_ended(None)
        assert not service.is_job_ended(None)

    def test_set_is_bounded_and_drops_oldest(self):
        from app.modules.broadcast import service

        for job in range(service._ENDED_JOBS_MAX + 10):
            service.mark_job_ended(job)
        assert len(service.ENDED_JOBS) == service._ENDED_JOBS_MAX
        # 가장 오래된 것부터 밀려난다. 최근 것은 남아 있어야 한다.
        assert not service.is_job_ended(0)
        assert service.is_job_ended(service._ENDED_JOBS_MAX + 9)


# ── 오디오 규격 (문제점 29·30·31번) ─────────────────────────────────────
class TestAudioAction:
    """업로드 mp3 를 받을지·거절할지·변환할지."""

    @staticmethod
    def _act(rate, ch, kbps, target):
        from app.modules.file.service import AudioSpec, audio_action

        return audio_action(AudioSpec(sample_rate=rate, channels=ch, kbps=kbps), target)

    def test_exact_match_passes(self):
        assert self._act(16_000, 1, 24, 24) == "ok"
        assert self._act(16_000, 1, 16, 16) == "ok"

    def test_lower_bitrate_is_rejected(self):
        # 다시 인코딩해도 없는 음질이 생기지는 않는다 — 아예 받지 않는다.
        assert self._act(16_000, 1, 16, 24) == "reject"

    def test_lower_sample_rate_is_rejected(self):
        assert self._act(8_000, 1, 32, 24) == "reject"

    def test_higher_quality_needs_transcode(self):
        # 흔한 경우 — 44.1kHz 스테레오 128kbps 로 만든 안내음성.
        assert self._act(44_100, 2, 128, 24) == "transcode"

    def test_downgrade_target_also_transcodes(self):
        # 설정을 16 으로 낮추면 기존 규격(24)도 변환 대상이 된다.
        assert self._act(16_000, 1, 24, 16) == "transcode"

    def test_stereo_at_target_bitrate_transcodes(self):
        assert self._act(16_000, 2, 24, 24) == "transcode"

    def test_cbr_rounding_is_tolerated(self):
        # CBR 인코더도 헤더 오버헤드로 1kbps 정도 어긋난다. 그걸로 거절하면
        # 정상 파일이 반려된다.
        assert self._act(16_000, 1, 25, 24) == "ok"
        assert self._act(16_000, 1, 23, 24) == "ok"

    def test_describe_reads_like_the_popup(self):
        from app.modules.file.service import AudioSpec

        assert AudioSpec(16_000, 1, 24).describe() == "16kHz mono 24kbps"
        assert AudioSpec(44_100, 2, 128).describe() == "44kHz 2ch 128kbps"


class TestBitrateConfig:
    """비트레이트는 서버·브라우저 설정이지 단말 CONFIG 가 아니다."""

    def test_choices_are_16_and_24(self):
        from app.constants import CONFIG_CHOICES

        assert CONFIG_CHOICES["live_bitrate_kbps"] == (16, 24)
        assert CONFIG_CHOICES["file_bitrate_kbps"] == (16, 24)

    def test_bitrate_is_not_a_device_field(self):
        from app.constants import DEVICE_CONFIG_FIELDS

        # 들어가면 비트레이트만 바꿔도 전 단말이 CONFIG 를 다시 받는다.
        # opus·mp3 모두 헤더에 비트레이트가 있어 단말이 미리 알 필요가 없다.
        assert "live_bitrate_kbps" not in DEVICE_CONFIG_FIELDS
        assert "file_bitrate_kbps" not in DEVICE_CONFIG_FIELDS

    def test_choice_fields_are_not_ranges(self):
        from app.constants import CONFIG_CHOICES, CONFIG_LIMITS

        # 한 필드가 양쪽에 있으면 _check_value 가 어느 쪽으로 막을지 모호해진다.
        assert not set(CONFIG_CHOICES) & set(CONFIG_LIMITS)

    def test_invalid_choice_is_rejected(self):
        from app.modules.system.service import _check_value

        _check_value("live_bitrate_kbps", 16)
        _check_value("live_bitrate_kbps", 24)
        with pytest.raises(ApiError) as exc:
            _check_value("live_bitrate_kbps", 20)
        assert exc.value.code == "CONFIG_INVALID_CHOICE"

    def test_sample_rate_stays_fixed(self):
        from app.constants import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE

        # 통신 사양 고정값. 설정으로 여는 것은 비트레이트뿐이다.
        assert (AUDIO_SAMPLE_RATE, AUDIO_CHANNELS) == (16_000, 1)


# ── 계정 사용 기간 (문제점 26번) ────────────────────────────────────────
class TestAccountExpiry:
    def test_null_expiry_never_expires(self):
        from app.models.org import User

        assert User(username="a", password_hash="x", role="super_admin").is_expired() is False

    def test_future_expiry_is_active(self):
        import datetime as dt

        from app.models.org import User

        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        user = User(username="a", password_hash="x", role="village_admin", expires_at=future)
        assert user.is_expired() is False

    def test_past_expiry_is_expired(self):
        import datetime as dt

        from app.models.org import User

        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        user = User(username="a", password_hash="x", role="village_admin", expires_at=past)
        assert user.is_expired() is True

    def test_expiry_from_days_counts_from_now(self):
        import datetime as dt

        from app.modules.org.service import _expiry_from

        at = _expiry_from(7)
        assert at is not None
        left = at - dt.datetime.now(dt.timezone.utc)
        # "7일" 은 7일째까지 쓸 수 있고 8일째부터 정리 대상이다.
        assert dt.timedelta(days=6, hours=23) < left <= dt.timedelta(days=7)

    def test_expiry_from_none_is_unlimited(self):
        from app.modules.org.service import _expiry_from

        assert _expiry_from(None) is None

    def test_default_is_15_days_within_1_to_30(self):
        from app.schemas.org import VALID_DAYS_DEFAULT, VALID_DAYS_MAX, VALID_DAYS_MIN

        assert (VALID_DAYS_MIN, VALID_DAYS_DEFAULT, VALID_DAYS_MAX) == (1, 15, 30)


# ── 방송 대상 이름 (문제점 33번) ────────────────────────────────────────
class TestTargetLabel:
    def test_three_names_are_listed(self):
        from app.modules.device.service import _fold

        assert _fold(["금산마을", "계곡마을", "산본마을"]) == "금산마을, 계곡마을, 산본마을"

    def test_more_than_three_are_folded(self):
        from app.modules.device.service import _fold

        assert _fold(["가", "나", "다", "라"]) == "가, 나 외 2곳"

    @pytest.mark.asyncio
    async def test_all_scope_needs_no_query(self):
        from app.modules.device.service import describe_targets

        # scope=all 은 id 가 없어 DB 를 보지 않는다 — 화면에 영문 "all" 대신
        # 한글이 나가야 한다.
        assert await describe_targets(None, [("all", [])]) == ["모든 마을"]

    @pytest.mark.asyncio
    async def test_empty_target_is_blank(self):
        from app.modules.device.service import describe_targets

        assert await describe_targets(None, [("village", [])]) == [""]


# ── 관리자 계층 (설계 2026-09-08) ──────────────────────────────────────
class TestAuthzTiers:
    def test_order_is_super_sido_sigungu_village(self):
        from app.core.authz import tier

        assert tier("super_admin") > tier("sido_admin")
        assert tier("sido_admin") > tier("sigungu_admin") > tier("village_admin")

    def test_unknown_role_is_below_everyone(self):
        from app.core.authz import tier

        assert tier("nope") < tier("village_admin")

    def test_manageable_roles_are_strictly_lower(self):
        from app.core.authz import manageable_roles

        # 자기 계층은 못 만든다 — 시·군이 시·군을 만들면 관할이 옆으로 샌다.
        assert manageable_roles("super_admin") == {"sido_admin", "sigungu_admin", "village_admin"}
        assert manageable_roles("sido_admin") == {"sigungu_admin", "village_admin"}
        assert manageable_roles("sigungu_admin") == {"village_admin"}
        assert manageable_roles("village_admin") == frozenset()

    def test_org_roles_need_an_organization(self):
        from app.core.authz import ORG_ADMIN_ROLES, ORG_ROLES

        assert {"sido_admin", "sigungu_admin"} == ORG_ROLES
        # 마을·계정을 관리하는 역할 = 시·군 이상
        assert {"super_admin", "sido_admin", "sigungu_admin"} == ORG_ADMIN_ROLES

    def test_role_enum_matches_tier_table(self):
        from app.constants import Role
        from app.core.authz import ROLE_TIER

        # 역할을 추가하고 계층표를 빠뜨리면 그 역할은 아무것도 못 만드는 계정이 된다.
        assert set(ROLE_TIER) == {r.value for r in Role}


class TestEventVisibility:
    """진행 중 방송의 가시성(설계 §8). DB 를 안 타는 경로만 여기서 본다."""

    @staticmethod
    def _event(scope, ids):
        return dataclasses.make_dataclass("E", ["target_scope", "target_ids"])(scope, ids)

    @pytest.mark.asyncio
    async def test_super_admin_sees_everything(self):
        from app.modules.device.service import events_visible_to

        events = [self._event("device", ["aa"]), self._event("all", [])]
        assert await events_visible_to(None, events, VillageScope.for_super_admin()) == [True, True]

    @pytest.mark.asyncio
    async def test_empty_scope_sees_nothing(self):
        from app.modules.device.service import events_visible_to

        events = [self._event("village", ["1"]), self._event("all", [])]
        flags = await events_visible_to(None, events, VillageScope.for_villages([]))
        assert flags == [False, False]

    @pytest.mark.asyncio
    async def test_all_broadcast_is_visible_to_any_village_admin(self):
        from app.modules.device.service import events_visible_to

        # 전체 방송은 내 마을에도 나갔다. 보여야 멈출 수도 있다.
        events = [self._event("all", []), self._event("village", ["7"])]
        flags = await events_visible_to(None, events, VillageScope.for_villages([1]))
        assert flags == [True, False]

    @pytest.mark.asyncio
    async def test_multi_village_broadcast_visible_if_any_mine(self):
        from app.modules.device.service import events_visible_to

        events = [self._event("village", ["3", "1"])]
        assert await events_visible_to(None, events, VillageScope.for_villages([1])) == [True]


# ── 자동방송 규칙 (스케줄 설계 2026-09-09) ────────────────────────────────
def _kst(y, m, d, hh=0, mm=0):
    import datetime as dt

    from app.modules.schedule.rules import KST

    return dt.datetime(y, m, d, hh, mm, tzinfo=KST)


class TestScheduleRules:
    """규칙 하나만 저장하고 실행 시각은 계산한다 — cron 과 같다."""

    @staticmethod
    def _rule(**kw):
        import datetime as dt

        from app.modules.schedule.rules import Rule

        kw.setdefault("fire_time", dt.time(9, 0))
        return Rule(**kw)

    def test_daily_fires_every_day_at_time(self):
        from app.modules.schedule.rules import occurrences

        got = occurrences(self._rule(repeat="daily"), _kst(2026, 9, 9), _kst(2026, 9, 12))
        assert got == [_kst(2026, 9, 9, 9), _kst(2026, 9, 10, 9), _kst(2026, 9, 11, 9)]

    def test_weekly_uses_korean_weekday_numbering(self):
        from app.modules.schedule.rules import korean_weekday, occurrences

        # 2026-09-09 는 수요일. 0=일 이므로 수=3.
        assert korean_weekday(_kst(2026, 9, 9).date()) == 3
        rule = self._rule(repeat="weekly", weekdays=frozenset({3}))
        got = occurrences(rule, _kst(2026, 9, 7), _kst(2026, 9, 21))
        assert got == [_kst(2026, 9, 9, 9), _kst(2026, 9, 16, 9)]

    def test_monthly_skips_months_without_that_day(self):
        from app.modules.schedule.rules import occurrences

        # 31일은 9월·11월에 없다. cron 과 같이 건너뛴다.
        rule = self._rule(repeat="monthly", month_days=frozenset({31}))
        got = occurrences(rule, _kst(2026, 8, 1), _kst(2026, 12, 31))
        assert [g.date().isoformat() for g in got] == ["2026-08-31", "2026-10-31"]

    def test_monthly_second_day_is_one_row_many_fires(self):
        from app.modules.schedule.rules import occurrences

        # "매월 2일" — DB 에 열두 줄이 아니라 규칙 하나. 1년치를 계산하면 12번.
        rule = self._rule(repeat="monthly", month_days=frozenset({2}))
        got = occurrences(rule, _kst(2026, 1, 1), _kst(2027, 1, 1))
        assert len(got) == 12
        assert all(g.day == 2 and g.hour == 9 for g in got)

    def test_yearly_feb_29_only_in_leap_years(self):
        from app.modules.schedule.rules import occurrences

        rule = self._rule(repeat="yearly", year_dates=frozenset({(2, 29)}))
        got = occurrences(rule, _kst(2026, 1, 1), _kst(2030, 1, 1))
        assert [g.year for g in got] == [2028]

    def test_window_is_half_open(self):
        from app.modules.schedule.rules import occurrences

        rule = self._rule(repeat="daily")
        # end 정각은 포함하지 않는다 — 실행기 창이 [분, 분+1) 이라 겹치지 않게.
        assert occurrences(rule, _kst(2026, 9, 9, 9, 0), _kst(2026, 9, 9, 9, 0)) == []
        assert occurrences(rule, _kst(2026, 9, 9, 9, 0), _kst(2026, 9, 9, 9, 1)) == [
            _kst(2026, 9, 9, 9, 0)
        ]
        assert occurrences(rule, _kst(2026, 9, 9, 8, 0), _kst(2026, 9, 9, 9, 0)) == []

    def test_utc_input_is_interpreted_in_kst(self):
        import datetime as dt

        from app.modules.schedule.rules import occurrences

        # UTC 로 넘겨도 한국 시각으로 본다. KST 09:00 = UTC 00:00.
        rule = self._rule(repeat="daily")
        start = dt.datetime(2026, 9, 8, 23, 0, tzinfo=dt.timezone.utc)
        end = dt.datetime(2026, 9, 9, 1, 0, tzinfo=dt.timezone.utc)
        got = occurrences(rule, start, end)
        assert got == [_kst(2026, 9, 9, 9, 0)]

    def test_naive_datetime_is_rejected(self):
        import datetime as dt

        from app.modules.schedule.rules import occurrences

        with pytest.raises(ValueError):
            occurrences(
                self._rule(repeat="daily"), dt.datetime(2026, 9, 9), dt.datetime(2026, 9, 10)
            )

    def test_next_occurrence_and_empty_rule(self):
        from app.modules.schedule.rules import next_occurrence

        weekly = self._rule(repeat="weekly", weekdays=frozenset({0}))  # 일요일
        assert next_occurrence(weekly, _kst(2026, 9, 9, 10)) == _kst(2026, 9, 13, 9)
        # 요일이 비면 영영 안 나간다 — None.
        assert next_occurrence(self._rule(repeat="weekly"), _kst(2026, 9, 9)) is None


class TestScheduleRunnerWindow:
    def test_window_looks_back_by_grace_and_forward_one_minute(self):
        import datetime as dt

        from app.constants import SCHEDULE_GRACE_SEC
        from app.tasks.schedule_runner import tick_window

        now = dt.datetime(2026, 9, 9, 0, 0, 30, tzinfo=dt.timezone.utc)
        start, end = tick_window(now)
        assert end == dt.datetime(2026, 9, 9, 0, 1, tzinfo=dt.timezone.utc)
        assert start == end - dt.timedelta(minutes=1, seconds=SCHEDULE_GRACE_SEC)


class TestScheduleSchema:
    def test_weekly_without_weekdays_is_rejected(self):
        import datetime as dt

        from app.schemas.schedule import ScheduleCreate

        with pytest.raises(ValueError):
            ScheduleCreate(
                repeat="weekly", fire_time=dt.time(9), file_id=1,
                target_scope="village", target_ids=["1"],
            )

    def test_unrelated_fields_are_cleared(self):
        import datetime as dt

        from app.schemas.schedule import ScheduleCreate

        # 매일로 저장하는데 요일이 남아 있으면 나중에 종류를 바꿨을 때 옛 값이 튀어나온다.
        s = ScheduleCreate(
            repeat="daily", weekdays=[1, 2], month_days=[3], fire_time=dt.time(9, 30, 15),
            file_id=1, target_scope="village", target_ids=["1"],
        )
        assert s.weekdays is None and s.month_days is None and s.year_dates is None
        assert s.fire_time == dt.time(9, 30)  # 초는 버린다

    def test_weekdays_are_deduped_and_sorted(self):
        import datetime as dt

        from app.schemas.schedule import ScheduleCreate

        s = ScheduleCreate(
            repeat="weekly", weekdays=[6, 1, 1], fire_time=dt.time(9), file_id=1,
            target_scope="device", target_ids=["aabbccddeeff"],
        )
        assert s.weekdays == [1, 6]

    def test_target_required(self):
        import datetime as dt

        from app.schemas.schedule import ScheduleCreate

        with pytest.raises(ValueError):
            ScheduleCreate(repeat="daily", fire_time=dt.time(9), file_id=1, target_scope="village")


# ── 방송한 파일 삭제 (0017) ──────────────────────────────────────────────
class TestFileDeleteConstraints:
    """이력은 삭제를 막지 않고, 스케줄만 막는다."""

    def test_history_no_longer_blocks_deletion(self):
        from app.models.event import BroadcastEvent

        # ON DELETE SET NULL 이라야 방송한 파일을 지울 수 있다. 제약이 없으면
        # (NO ACTION) 한 번 방송한 파일이 영영 안 지워진다.
        fk = next(iter(BroadcastEvent.__table__.c.file_id.foreign_keys))
        assert fk.ondelete == "SET NULL"

    def test_schedule_still_blocks_deletion(self):
        from app.models.schedule import Schedule

        # 파일이 사라진 스케줄은 걸릴 때마다 조용히 실패한다. 막는 쪽이 맞다.
        fk = next(iter(Schedule.__table__.c.file_id.foreign_keys))
        assert fk.ondelete is None

    def test_download_token_cascades(self):
        from app.models.file import DownloadToken

        # 단기 토큰은 파일과 함께 사라져야 한다.
        fk = next(iter(DownloadToken.__table__.c.file_id.foreign_keys))
        assert fk.ondelete == "CASCADE"

    def test_history_keeps_its_own_file_name(self):
        from app.models.event import BroadcastEvent

        # 파일을 지운 뒤에도 이력의 「무엇을」이 남아야 한다. 조회 때 files 를
        # 찾아가면 빈칸이 되므로 시작 시점 이름을 이력에 박아둔다.
        assert "file_name" in BroadcastEvent.__table__.c
        assert BroadcastEvent.__table__.c.file_name.nullable

    def test_every_user_facing_fk_is_non_blocking(self):
        """이력은 불변 로그다 — 참조당하는 쪽의 삭제를 막지 않는다(0006·0014·0017)."""
        from app.models.event import BroadcastEvent
        from app.models.file import File

        for col in (BroadcastEvent.__table__.c.file_id, BroadcastEvent.__table__.c.triggered_by):
            fk = next(iter(col.foreign_keys))
            assert fk.ondelete == "SET NULL", col.name
        fk = next(iter(File.__table__.c.uploaded_by.foreign_keys))
        assert fk.ondelete == "SET NULL"


class TestRepeatLabel:
    def test_covers_every_repeat(self):
        from app.constants import REPEAT_LABEL, Repeat

        # 빠진 종류가 있으면 삭제 거절 사유에 "monthly" 같은 영문이 그대로 나간다.
        assert set(REPEAT_LABEL) == {r.value for r in Repeat}


# ── mp3 muxer 옵션 (문제점 30번, 2026-09-08) ────────────────────────────
class TestMp3MuxerArgs:
    """첫 프레임이 파일 전체의 비트레이트를 대신 말한다 — 거짓말을 하면 안 된다."""

    def test_xing_header_is_disabled(self):
        from app.tts.engine import MP3_MUXER_ARGS

        # LAME 은 Xing 태그를 담으려고 첫 프레임만 40kbps 로 올린다. 16kHz 16kbps
        # 프레임은 72바이트뿐이라 태그가 안 들어가서다. 그러면 첫 프레임만 읽는
        # 도구가 파일을 40kbps 로 보고한다(단말팀 2026-09-07 실측).
        assert "-write_xing" in MP3_MUXER_ARGS
        assert MP3_MUXER_ARGS[MP3_MUXER_ARGS.index("-write_xing") + 1] == "0"

    def test_id3v2_shell_is_disabled(self):
        from app.tts.engine import MP3_MUXER_ARGS

        # -map_metadata -1 로도 44바이트 빈 ID3v2 가 남는다.
        # "변환 시 내부 tag 등 정보는 전부 삭제"(문제점 31번)를 글자대로 지킨다.
        assert "-id3v2_version" in MP3_MUXER_ARGS
        assert MP3_MUXER_ARGS[MP3_MUXER_ARGS.index("-id3v2_version") + 1] == "0"

    def test_transcode_uses_the_same_args(self):
        import inspect

        from app.modules.file import service as file_service

        # TTS 와 업로드 재인코딩이 갈라지면 한쪽만 40kbps 로 남는다.
        assert "MP3_MUXER_ARGS" in inspect.getsource(file_service.transcode_in_place)


# ── TTS 결과가 규격과 같은지 (문제점 30번 후속, 2026-09-09) ───────────────
class TestTtsFormatVersion:
    """만드는 방식을 고쳐도 캐시가 옛 파일을 계속 내보내면 아무것도 안 고쳐진다."""

    def test_format_version_is_in_the_cache_key(self):
        from app.tts.service import cache_key

        with unittest.mock.patch("app.tts.service.MP3_FORMAT_VERSION", 1):
            v1 = cache_key("안녕하세요", "ko-KR", "ko-KR-A", 16)
        with unittest.mock.patch("app.tts.service.MP3_FORMAT_VERSION", 2):
            v2 = cache_key("안녕하세요", "ko-KR", "ko-KR-A", 16)
        assert v1 != v2

    def test_bitrate_still_separates_the_key(self):
        from app.tts.service import cache_key

        assert cache_key("안녕", "ko-KR", "v", 16) != cache_key("안녕", "ko-KR", "v", 24)

    def test_same_inputs_same_key(self):
        from app.tts.service import cache_key

        assert cache_key(" 안녕 ", "ko-KR", "v", 16) == cache_key("안녕", "ko-KR", "v", 16)

    def test_version_is_at_least_two(self):
        from app.tts.engine import MP3_FORMAT_VERSION

        # Xing 제거(2026-09-09)로 1 → 2. 되돌리면 옛 캐시가 되살아난다.
        assert MP3_FORMAT_VERSION >= 2

    def test_normalize_refuses_without_ffmpeg(self):
        from app.errors import ApiError
        from app.tts import engine

        # 예전에는 원본을 그대로 파일함에 넣고 로그 한 줄만 남겼다 — 규격 밖 파일이
        # 아무도 모르게 단말로 나가는 경로였다.
        with (
            unittest.mock.patch.object(engine.shutil, "which", return_value=None),
            pytest.raises(ApiError),
        ):
            engine.normalize_mp3(b"not-an-mp3", 16)


# ── 목록 조회 성능 (2026-09-10) ─────────────────────────────────────────
class TestNextOccurrenceEarlyExit:
    """찾는 즉시 멈춘다. 답은 예전과 같아야 한다."""

    @staticmethod
    def _rule(**kw):
        import datetime as dt

        from app.modules.schedule.rules import Rule

        kw.setdefault("fire_time", dt.time(9, 0))
        return Rule(**kw)

    def _slow(self, rule, after):
        """예전 구현 — 범위 전체를 모아 첫 번째를 꺼낸다."""
        import datetime as dt

        from app.modules.schedule.rules import LOOKAHEAD_DAYS, occurrences

        found = occurrences(rule, after, after + dt.timedelta(days=LOOKAHEAD_DAYS))
        return found[0] if found else None

    def test_matches_the_old_implementation(self):

        from app.modules.schedule.rules import next_occurrence

        rules_to_check = [
            self._rule(repeat="daily"),
            self._rule(repeat="weekly", weekdays=frozenset({0, 3})),
            self._rule(repeat="monthly", month_days=frozenset({1, 31})),
            self._rule(repeat="yearly", year_dates=frozenset({(2, 29), (12, 25)})),
        ]
        # 하루 안의 여러 시각에서 확인한다 — 경계(발사 시각 직전·정각·직후)가 중요하다.
        for rule in rules_to_check:
            for hour in (0, 8, 9, 10, 23):
                after = _kst(2026, 9, 10, hour, 0)
                assert next_occurrence(rule, after) == self._slow(rule, after), (rule, hour)

    def test_exact_fire_time_returns_itself(self):
        from app.modules.schedule.rules import next_occurrence

        # 지금이 정확히 발사 시각이면 그 시각을 돌려준다(예전 경계와 같다).
        rule = self._rule(repeat="daily")
        assert next_occurrence(rule, _kst(2026, 9, 10, 9, 0)) == _kst(2026, 9, 10, 9, 0)

    def test_rule_that_can_never_match_is_none(self):
        from app.modules.schedule.rules import can_ever_match, next_occurrence

        empty = self._rule(repeat="weekly")
        assert can_ever_match(empty) is False
        assert next_occurrence(empty, _kst(2026, 9, 10)) is None

    def test_naive_datetime_is_rejected(self):
        import datetime as dt

        from app.modules.schedule.rules import next_occurrence

        with pytest.raises(ValueError):
            next_occurrence(self._rule(repeat="daily"), dt.datetime(2026, 9, 10))

    def test_daily_is_fast(self):
        import time

        from app.modules.schedule.rules import next_occurrence

        rule = self._rule(repeat="daily")
        after = _kst(2026, 9, 10, 10, 0)
        t = time.perf_counter()
        for _ in range(200):
            next_occurrence(rule, after)
        per_call_ms = (time.perf_counter() - t) / 200 * 1000
        # 예전에는 1.1ms 였다. 범위 전체를 훑는 구현으로 되돌아가면 여기서 걸린다.
        assert per_call_ms < 0.1, per_call_ms


class TestScheduleResolveBatch:
    """배치 해석이 건별 해석과 **같은 답**을 내야 한다.

    다르면 권한이 넓어지거나 좁아진다 — 기관별 마을을 한 덩어리로 합치는 실수가
    가장 위험하다(A 기관 스케줄이 B 기관 마을에도 닿게 된다).
    """

    @pytest.mark.asyncio
    async def test_empty_input_makes_no_query(self):
        from app.modules.schedule.service import resolve_batch

        assert await resolve_batch(None, []) == {}

    @staticmethod
    def _counting_db():
        class Rows:
            def all(self):
                return []

            def __iter__(self):
                return iter(())

        class Stub:
            def __init__(self):
                self.count = 0

            async def execute(self, stmt):
                self.count += 1
                return Rows()

            async def scalars(self, stmt):
                self.count += 1
                return Rows()

        return Stub()

    @pytest.mark.asyncio
    async def test_queries_do_not_grow_with_schedule_count(self):
        import types

        from app.modules.schedule.service import resolve_batch

        def scheds(n):
            return [
                types.SimpleNamespace(id=i + 1, target_scope="village", target_ids=[str(i + 1)])
                for i in range(n)
            ]

        db10 = self._counting_db()
        await resolve_batch(db10, scheds(10))
        db100 = self._counting_db()
        await resolve_batch(db100, scheds(100))
        # 규칙이 10배가 돼도 질의 수는 같아야 한다 — 이게 안 지켜지면 N+1 로 되돌아간 것.
        assert db10.count == db100.count, (db10.count, db100.count)
        assert db100.count <= 6, db100.count

    @pytest.mark.asyncio
    async def test_village_ids_are_the_villages(self):
        import types

        from app.modules.schedule.service import resolve_batch

        sched = types.SimpleNamespace(id=1, target_scope="village", target_ids=["3", "7"])
        got = await resolve_batch(self._counting_db(), [sched])
        assert got[1][0] == {3, 7}

    def test_org_villages_are_kept_per_organization(self):
        """기관별 분리 — 합치면 권한이 넓어진다."""
        import inspect

        from app.modules.schedule import service

        src = inspect.getsource(service.resolve_batch)
        # 대상 기관마다 자기 마을 집합을 따로 담는 구조여야 한다.
        assert "org_villages[target_org].add(vid)" in src
        assert "org_villages: dict[int, set[int]]" in src


class TestUserListBatch:
    def test_villages_by_user_groups_rows(self):
        import inspect

        from app.modules.org import service

        # 목록은 배치로, 단건(생성·수정)은 예전처럼 직접 읽는 두 경로가 있어야 한다.
        src = inspect.getsource(service._to_user_out)
        assert "villages.get(user.id" in src
        assert "await villages_of_user(db, user.id)" in src

    @pytest.mark.asyncio
    async def test_empty_input_makes_no_query(self):
        from app.modules.org.service import villages_by_user

        assert await villages_by_user(None, []) == {}
