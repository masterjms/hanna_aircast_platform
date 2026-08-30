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

    def test_village_token_is_zero_padded_8(self):
        assert topics.village_token(1) == "00000001"
        assert topics.village_token(12345678) == "12345678"

    def test_topic_shapes(self):
        mac = "58e6c5f2cc74"
        assert topics.device_cmd(mac) == "iotradio/device/58e6c5f2cc74/cmd"
        assert topics.village_cmd(1) == "iotradio/village/00000001/cmd"
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

    async def test_device_config_carries_padded_village(self):
        conn = FakeConnection()
        await MqttPublisher(conn).publish_device_config(  # type: ignore[arg-type]
            mac="58e6c5f2cc74", village_id=12, config_version=3
        )
        topic, raw, qos, retain = conn.sent[0]
        assert topic == "iotradio/device/58e6c5f2cc74/config"
        assert (qos, retain) == (1, True)
        assert json.loads(raw)["village_id"] == "00000012"

    async def test_unassign_clears_retain_with_empty_payload(self):
        """빈 payload + retain=True = 브로커의 보관 메시지 삭제."""
        conn = FakeConnection()
        await MqttPublisher(conn).publish_device_config(  # type: ignore[arg-type]
            mac="58e6c5f2cc74", village_id=None, config_version=0
        )
        _, raw, _, retain = conn.sent[0]
        assert raw == b"" and retain is True

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

        # 평문은 거절
        with pytest.raises(ApiError):
            MqttPublisher.live_start_payload(job_id=1, stream_url="http://x.co.kr/live/1")

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
