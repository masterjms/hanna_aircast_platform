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
from app.errors import PayloadTooLarge, Unauthorized, VillageOutOfScope
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
            payload={"type": "LIVE_STOP", "session_id": 1},
            target_scope=TargetScope.VILLAGE,
            scope=VillageScope.for_super_admin(),
            village_id=1,
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
                village_id=2,
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
