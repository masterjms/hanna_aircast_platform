"""단말 → 서버 메시지 처리.

통신 사양 §3.4 기준:
  · status 토픽에 STATUS · LWT · LIVE_STATS 가 전부 온다. type 으로 구분한다.
  · LWT 는 별도 type 이 아니라 type=STATUS + state=OFFLINE 이다.
  · LIVE_STATS 에는 device 필드가 없다 — MAC 은 토픽에서 뽑는다.

저장 정책:
  STATUS      → devices 캐시만 갱신. device_events 에 쌓지 않는다.
                (300대 × 30초 = 하루 86만 행. 최신값만 있으면 충분하다.)
  OFFLINE     → 캐시 갱신 + device_events 1행. 끊긴 시점은 이력으로 남길 가치가 있다.
  LIVE_STATS  → device_events 1행. 방송 중에만 오므로 양이 유계이고,
                underrun 을 사후에 확인할 유일한 근거다.
  result 토픽 → device_events 1행.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import TELEMETRY_RESULTS, DeviceState, ResultType
from app.db import session_scope
from app.models.device import Device
from app.models.event import BroadcastEvent, DeviceEvent
from app.models.system import CurrentConfig
from app.modules.broadcast import service as broadcast_service
from app.mqtt import topics
from app.mqtt.publisher import MqttPublisher
from app.mqtt.status_buffer import (
    StatusBuffer,
    needs_resync,
    publish_resync,
    resync_allowed,
)

log = logging.getLogger(__name__)

#: payload 에서 job 식별자를 찾을 때 볼 키 (통신 사양의 메시지별 명칭이 다르다).
#:   LIVE_*  → session_id      FILE_*  → cmd_id      OTA_*  → job_id
#: job_id 로 통일하는 안이 코덱스 협의 중이라 당분간 전부 받아준다.
_JOB_ID_KEYS = ("job_id", "session_id", "cmd_id")


def _parse(payload: bytes) -> dict[str, Any] | None:
    """깨진 payload 때문에 워커가 죽지 않도록 None 으로 흡수한다."""
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("JSON 파싱 실패 (%d bytes)", len(payload))
        return None
    return data if isinstance(data, dict) else None


def _job_id_of(data: dict[str, Any]) -> int | None:
    for key in _JOB_ID_KEYS:
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _dedup_key(mac: str, result_type: str | None, job_id: int | None,
               payload: dict[str, Any] | None = None) -> str | None:
    """QoS 1 중복 수신 방어 키. job_id 를 못 뽑으면 중복 검사를 포기한다.

    ⚠ payload 내용까지 키에 넣는다. 예전에는 mac:type:job_id 만 썼는데, 그러면
      **같은 job 에 대한 두 번째 결과가 중복으로 오인돼 조용히 버려진다.**

      단말은 한 job 에 LIVE_READY 를 두 번 보낸다(ESP32 회신 260824 §5.1):
        1) status=0  P4 출력 준비 완료 — Icecast 접속 *전*
        2) status=2  스트림 접속 실패로 abort
      옛 키에서는 2번이 사라져서 서버가 그 단말을 영영 정상으로 안다. 화면에는
      "준비 완료"인데 스피커는 조용한, 프로덕션에서 가장 위험한 상태다.
      OTA_STATUS 도 같은 job_id 로 상태가 여러 번 오므로 같은 문제였다.

      진짜 QoS1 재전송은 payload 가 바이트 단위로 같으므로 여전히 걸러진다.
    """
    if job_id is None or result_type is None:
        return None
    base = f"{mac}:{result_type}:{job_id}"
    if payload is None:
        # telemetry 처럼 "최신값 1행"으로 관리하는 종류다. 내용을 키에 넣지 않는다.
        return base
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{base}:{hashlib.sha1(canonical.encode('utf-8')).hexdigest()[:16]}"


async def _touch_device(
    db: AsyncSession,
    mac: str,
    *,
    payload: dict[str, Any] | None,
    seen_at: dt.datetime | None,
) -> None:
    """단말 캐시 갱신. 처음 보는 MAC 이면 미배정 상태로 자동 등록한다.

    village_id 는 건드리지 않는다 — 배정 권한은 서버(관리자)에 있고,
    STATUS 의 village_id 는 단말이 CONFIG 를 제대로 받았는지 확인하는 echo 일 뿐이다.

    seen_at 을 None 으로 주면 마지막 통신 시각을 건드리지 않는다 — LWT 가 그렇다.
    LWT 는 단말이 아니라 브로커가 대신 보내는 사망 통지라, 그걸 "방금 통신함"으로
    기록하면 죽은 단말이 온라인으로 잡힌다.
    """
    values: dict[str, Any] = {"mac": mac}
    if seen_at is not None:
        values["last_seen_at"] = seen_at
    if payload is not None:
        values["last_status"] = payload
    if len(values) == 1:
        return

    stmt = pg_insert(Device).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Device.mac],
        set_={k: stmt.excluded[k] for k in values if k != "mac"},
    )
    await db.execute(stmt)


#: job_id → broadcast_events.id 캐시.
#:
#: 이 조회는 결과 메시지마다 나간다. 방송 하나에 단말 3000대가 응답하면 **같은
#: 답을 3000번** 묻는 셈이라, 메시지당 쿼리 수를 그대로 두 배로 만든다(A-3).
#: job_id 는 DB 시퀀스로 발번되고 한 번 정해진 event_id 가 바뀌지 않으므로
#: 캐시해도 틀릴 일이 없다. 방송이 끝나도 늦게 오는 결과가 있어 바로 지우지 않고,
#: 최근 것 위주로 상한만 둔다(FIFO).
_event_id_cache: dict[int, int] = {}
_EVENT_ID_CACHE_MAX = 256


async def _event_id_for(db: AsyncSession, job_id: int) -> int | None:
    cached = _event_id_cache.get(job_id)
    if cached is not None:
        return cached
    event_id = await db.scalar(
        select(BroadcastEvent.id).where(BroadcastEvent.job_id == job_id).limit(1)
    )
    if event_id is not None:
        if len(_event_id_cache) >= _EVENT_ID_CACHE_MAX:
            # 가장 오래 전에 넣은 것부터 버린다(dict 는 삽입 순서를 유지한다).
            del _event_id_cache[next(iter(_event_id_cache))]
        _event_id_cache[job_id] = event_id
    return event_id


async def _insert_device_event(
    db: AsyncSession,
    *,
    mac: str,
    result_type: str | None,
    payload: dict[str, Any],
    job_id: int | None,
) -> None:
    """이력 1행. dedup_key 충돌은 조용히 버린다(같은 메시지의 재전송)."""
    event_id = await _event_id_for(db, job_id) if job_id is not None else None

    if result_type in TELEMETRY_RESULTS:
        # 주기 telemetry 는 tick 마다 행을 쌓지 않고 최신값만 덮어쓴다.
        # DeviceEvent docstring 의 STATUS 정책과 같은 이유다 — LIVE_STATS 를
        # 그대로 쌓으면 300대 × 10초 주기면 방송 10분에 18,000 행이 되고,
        # 화면의 단말별 응답 목록도 같은 단말이 계속 늘어난다.
        # 최신값만 남기므로 dedup 키에는 payload 를 넣지 않는다.
        stmt = pg_insert(DeviceEvent).values(
            event_id=event_id,
            mac=mac,
            result_type=result_type,
            payload=payload,
            dedup_key=_dedup_key(mac, result_type, job_id),
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[DeviceEvent.dedup_key],
                # 유니크 인덱스가 부분 인덱스(WHERE dedup_key IS NOT NULL)라
                # 조건을 같이 줘야 Postgres 가 그 인덱스를 찾는다.
                index_where=DeviceEvent.dedup_key.is_not(None),
                set_={"payload": payload, "received_at": func.now(), "event_id": event_id},
            )
        )
        return

    stmt = pg_insert(DeviceEvent).values(
        event_id=event_id,
        mac=mac,
        result_type=result_type,
        payload=payload,
        dedup_key=_dedup_key(mac, result_type, job_id, payload),
    )
    await db.execute(stmt.on_conflict_do_nothing())


async def _resync_if_stale(
    db: AsyncSession, mac: str, data: dict[str, Any], publisher: MqttPublisher | None
) -> None:
    """단말이 echo 한 값이 DB 와 다르면 그 단말 CONFIG 를 다시 내린다 (사양 §4.3 권장).

    버퍼를 끈 경우(status_flush_interval_sec=0)에만 이 경로로 온다. 버퍼가 켜져
    있으면 같은 판정을 flush 에서 묶어서 한다(status_buffer.StatusBuffer.flush) —
    판정·쿨다운·발행 코드는 그쪽 모듈이 정본이고 여기서는 그걸 부른다.

    공통 CONFIG 는 건드리지 않는다 — 마을은 단말별 토픽으로만 가고(사양 §4.2),
    한 대 때문에 전체 설정을 다시 뿌릴 이유가 없다.
    """
    if publisher is None:
        return

    device = await db.get(Device, mac)
    if device is None or device.village_id is None:
        # 미배정 단말은 보낼 게 없다. 배정 해제는 이미 retain 을 지워뒀다.
        return

    config = await db.get(CurrentConfig, 1)
    expected_version = config.config_version if config is not None else None

    if not needs_resync(
        reported_village=str(data.get("village_id") or ""),
        expected_village=topics.village_token(device.village_id),
        reported_version=data.get("config_version"),
        expected_version=expected_version,
    ):
        return
    if not resync_allowed(mac, dt.datetime.now(dt.timezone.utc)):
        return

    await publish_resync(
        publisher,
        mac=mac,
        village_id=device.village_id,
        config_version=expected_version or 1,
    )


# ── 토픽별 처리 ──────────────────────────────────────────────────────────
async def handle_status(
    db: AsyncSession,
    mac: str,
    data: dict[str, Any],
    publisher: MqttPublisher | None = None,
    buffer: StatusBuffer | None = None,
) -> None:
    """status 토픽 (STATUS · LWT · LIVE_STATS)."""
    now = dt.datetime.now(dt.timezone.utc)
    msg_type = str(data.get("type") or "")

    if msg_type == ResultType.LIVE_STATS.value:
        # 방송 품질 지표. 캐시(last_status)를 덮어쓰면 안 된다 — STATUS 와 필드가 다르다.
        await _touch_device(db, mac, payload=None, seen_at=now)
        await _insert_device_event(
            db,
            mac=mac,
            result_type=ResultType.LIVE_STATS.value,
            payload=data,
            job_id=_job_id_of(data),
        )
        return

    # type=STATUS. state=OFFLINE 이면 LWT(브로커가 대신 보낸 사망 통지)다.
    is_lwt = str(data.get("state") or "") == DeviceState.OFFLINE.value

    if is_lwt:
        # 대기 중인 STATUS 를 버린다. 안 버리면 죽었다고 쓴 뒤에 낡은 STATUS 가
        # 덮어써서 죽은 단말이 온라인으로 되살아난다.
        if buffer is not None:
            buffer.discard(mac)
        await _touch_device(db, mac, payload=data, seen_at=None)
        await _insert_device_event(
            db,
            mac=mac,
            result_type=ResultType.OFFLINE.value,
            payload=data,
            job_id=None,
        )
        return

    if buffer is not None:
        # 여기서는 DB 를 만지지 않는다 — 대기열에만 넣고 flush 가 몰아서 쓴다.
        # 캐시 갱신도, CONFIG 불일치 검사도 그쪽에서 묶여서 돈다(A-2/A-3).
        buffer.offer(mac, payload=data, seen_at=now)
        return

    await _touch_device(db, mac, payload=data, seen_at=now)
    # 살아있는 STATUS 에만 자동 복구를 건다. 죽은 단말에 다시 보내봐야 소용없다.
    await _resync_if_stale(db, mac, data, publisher)


async def handle_result(db: AsyncSession, mac: str, data: dict[str, Any]) -> None:
    """result 토픽 (LIVE_READY · LIVE_RESULT · FILE_RESULT · OTA_PROGRESS · OTA_RESULT)."""
    now = dt.datetime.now(dt.timezone.utc)
    result_type = str(data.get("type") or "") or None
    job_id = _job_id_of(data)

    # 결과가 왔다는 건 살아 있다는 뜻이다. 미등록 MAC 이면 여기서도 등록된다.
    await _touch_device(db, mac, payload=None, seen_at=now)
    await _insert_device_event(
        db,
        mac=mac,
        result_type=result_type,
        payload=data,
        job_id=job_id,
    )

    # 종료 결과(FILE_RESULT · LIVE_RESULT)라면 이걸로 방송이 다 끝났는지 본다.
    # 이게 없으면 단말은 재생을 마쳤는데 화면은 계속 방송 중이다(문제점 3번).
    if job_id is not None and result_type in broadcast_service.TERMINAL_RESULTS:
        try:
            await broadcast_service.finish_if_all_reported(db, job_id)
        except Exception:  # noqa: BLE001 - 이력 적재는 이미 끝났다. 종료 판정만 포기한다.
            log.exception("방송 종료 판정 실패: job_id=%s", job_id)


async def dispatch(
    topic: str,
    payload: bytes,
    publisher: MqttPublisher | None = None,
    buffer: StatusBuffer | None = None,
) -> None:
    """MqttConnection 이 부르는 진입점.

    결과·LWT 는 메시지 1건 = 트랜잭션 1개다(드물고, 화면이 기다리는 값이다).
    주기 STATUS 는 buffer 가 있으면 DB 를 건드리지 않고 대기열에만 들어가고,
    StatusBuffer 가 주기적으로 한 트랜잭션에 몰아 쓴다 (A-2/A-3).

    publisher 는 STATUS 자동 복구(사양 §4.3)에만 쓴다. 없으면 복구를 건너뛴다 —
    테스트에서 발행 없이 수신만 확인할 때가 있다.
    """
    parsed_topic = topics.parse_inbound(topic)
    if parsed_topic is None:
        log.debug("구독 범위 밖 토픽 무시: %s", topic)
        return

    mac, kind = parsed_topic
    data = _parse(payload)
    if data is None:
        return

    if kind == "status" and buffer is not None:
        # 버퍼로 갈 수 있는 종류(주기 STATUS)면 세션 자체를 열지 않는다.
        # LWT·LIVE_STATS 는 아래 트랜잭션 경로로 내려간다.
        is_lwt = str(data.get("state") or "") == DeviceState.OFFLINE.value
        is_stats = str(data.get("type") or "") == ResultType.LIVE_STATS.value
        if not is_lwt and not is_stats:
            buffer.offer(mac, payload=data, seen_at=dt.datetime.now(dt.timezone.utc))
            return

    async with session_scope() as db:
        if kind == "status":
            await handle_status(db, mac, data, publisher, buffer)
        else:
            await handle_result(db, mac, data)
