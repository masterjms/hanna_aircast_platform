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
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DeviceState, ResultType
from app.db import session_scope
from app.models.device import Device
from app.models.event import BroadcastEvent, DeviceEvent
from app.mqtt import topics

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


def _dedup_key(mac: str, result_type: str | None, job_id: int | None) -> str | None:
    """QoS 1 중복 수신 방어 키. job_id 를 못 뽑으면 중복 검사를 포기한다."""
    if job_id is None or result_type is None:
        return None
    return f"{mac}:{result_type}:{job_id}"


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


async def _insert_device_event(
    db: AsyncSession,
    *,
    mac: str,
    result_type: str | None,
    payload: dict[str, Any],
    job_id: int | None,
) -> None:
    """이력 1행. dedup_key 충돌은 조용히 버린다(같은 메시지의 재전송)."""
    event_id: int | None = None
    if job_id is not None:
        event_id = await db.scalar(
            select(BroadcastEvent.id).where(BroadcastEvent.job_id == job_id).limit(1)
        )

    stmt = pg_insert(DeviceEvent).values(
        event_id=event_id,
        mac=mac,
        result_type=result_type,
        payload=payload,
        dedup_key=_dedup_key(mac, result_type, job_id),
    )
    await db.execute(stmt.on_conflict_do_nothing())


# ── 토픽별 처리 ──────────────────────────────────────────────────────────
async def handle_status(db: AsyncSession, mac: str, data: dict[str, Any]) -> None:
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
    await _touch_device(db, mac, payload=data, seen_at=None if is_lwt else now)

    if is_lwt:
        await _insert_device_event(
            db,
            mac=mac,
            result_type=ResultType.OFFLINE.value,
            payload=data,
            job_id=None,
        )


async def handle_result(db: AsyncSession, mac: str, data: dict[str, Any]) -> None:
    """result 토픽 (LIVE_READY · FILE_END · FILE_ABORT · FILE_STOP_RESULT · OTA_STATUS)."""
    now = dt.datetime.now(dt.timezone.utc)
    result_type = str(data.get("type") or "") or None

    # 결과가 왔다는 건 살아 있다는 뜻이다. 미등록 MAC 이면 여기서도 등록된다.
    await _touch_device(db, mac, payload=None, seen_at=now)
    await _insert_device_event(
        db,
        mac=mac,
        result_type=result_type,
        payload=data,
        job_id=_job_id_of(data),
    )


async def dispatch(topic: str, payload: bytes) -> None:
    """MqttConnection 이 부르는 진입점. 메시지 1건 = 트랜잭션 1개."""
    parsed_topic = topics.parse_inbound(topic)
    if parsed_topic is None:
        log.debug("구독 범위 밖 토픽 무시: %s", topic)
        return

    mac, kind = parsed_topic
    data = _parse(payload)
    if data is None:
        return

    async with session_scope() as db:
        if kind == "status":
            await handle_status(db, mac, data)
        else:
            await handle_result(db, mac, data)
