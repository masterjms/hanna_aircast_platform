"""CONFIG 재조정(reconciliation).

브로커의 retain 은 캐시이고 정본은 current_config 테이블이다. 브로커가 재시작되거나
retain 이 유실되면 단말은 영영 낡은 설정으로 남는다. 그래서 기동 시 1회 + 주기적으로
DB 값을 다시 발행한다.

CONFIG 는 두 토픽으로 나뉜다(사양 §4):

    iotradio/all/config            공통 설정만. **village_id 를 넣지 않는다.**
    iotradio/device/<mac>/config   그 단말 하나의 마을 배정.

두 토픽은 항상 같은 config_version 으로 **함께** 나가야 한다. 단말이 토픽별로
버전을 따로 추적하지 않고 마지막에 받은 값을 쓰는 단일 카운터 구조라(사양 §4.3),
한쪽만 올리면 단말이 낡은 값을 최종본으로 보고하게 된다.

멱등이다. 같은 값을 몇 번을 다시 보내도 config_version 이 그대로면 단말은 무시한다.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.device import Device
from app.models.system import CurrentConfig
from app.mqtt.publisher import MqttPublisher

log = logging.getLogger(__name__)

#: 기동 직후 MQTT 연결을 기다리는 시간. 넘으면 이번 회차는 건너뛰고 다음 주기에 다시 한다.
_CONNECT_WAIT_SEC = 15.0


async def load_config(db: AsyncSession) -> CurrentConfig:
    """정본 설정 1행. 없으면 기본값으로 만든다(최초 부팅)."""
    config = await db.get(CurrentConfig, 1)
    if config is None:
        config = CurrentConfig(id=1)
        db.add(config)
        await db.flush()
    return config


async def assigned_devices(db: AsyncSession) -> list[tuple[str, int]]:
    """마을이 배정된 단말 (mac, village_id) 목록."""
    rows = await db.execute(
        select(Device.mac, Device.village_id).where(Device.village_id.is_not(None))
    )
    return [(mac, village_id) for mac, village_id in rows.all()]


async def unassigned_devices(db: AsyncSession) -> list[str]:
    """마을이 없는 단말 MAC 목록. 이들의 보관본은 지워야 한다."""
    rows = await db.scalars(select(Device.mac).where(Device.village_id.is_(None)))
    return list(rows)


async def publish_all(publisher: MqttPublisher, db: AsyncSession | None = None) -> None:
    """공통 CONFIG 1회 + 마을이 배정된 단말별 CONFIG N회.

    미배정 단말은 보관본을 지운다. DB 가 정본이므로, 브로커에 옛 배정이 남아
    있으면 그 단말이 재접속할 때 없어진 마을을 다시 물려받는다. 빈 payload 는
    이미 비어 있는 토픽에 보내도 아무 일이 없어서 매 주기 돌려도 무해하다.

    db 를 넘기면 그 세션을 쓴다. 요청 처리 중(아직 커밋 전)에 부를 때 필요하다 —
    새 세션을 열면 방금 올린 config_version 이 안 보인다.
    """
    if db is not None:
        await _publish(publisher, db)
        return
    async with session_scope() as own:
        await _publish(publisher, own)


async def _publish(publisher: MqttPublisher, db: AsyncSession) -> None:
    config = await load_config(db)
    version = config.config_version
    devices = await assigned_devices(db)

    await publisher.publish_global_config(
        config_version=version,
        status_interval_sec=config.status_interval_sec,
        live_stats_interval_sec=config.live_stats_interval_sec,
        event_qos=config.event_qos,
    )

    # DB 가 미배정인데 브로커에 보관본이 남아 있으면, 그 단말은 재접속할 때
    # 없어진 배정을 다시 물려받는다. 빈 payload 로 지운다 — 이미 비어 있으면
    # 아무 일도 일어나지 않으므로 매 주기 돌려도 무해하다.
    for mac in await unassigned_devices(db):
        try:
            await publisher.publish_device_config(mac=mac, village_id=None, config_version=0)
        except Exception:  # noqa: BLE001
            log.exception("미배정 단말 CONFIG retain 삭제 실패: %s", mac)

    published = 0
    for mac, village_id in devices:
        try:
            await publisher.publish_device_config(
                mac=mac, village_id=village_id, config_version=version
            )
            published += 1
        except Exception:  # noqa: BLE001
            # 한 대가 실패해도 나머지는 계속 보낸다. 다음 주기에 다시 시도된다.
            log.exception("단말 CONFIG 발행 실패: %s", mac)

    log.info("CONFIG 재발행 완료 (version=%d, 단말 %d/%d대)", version, published, len(devices))


async def run(publisher: MqttPublisher, *, wait_for_connection: bool = True) -> None:
    """스케줄러가 부르는 진입점."""
    if wait_for_connection and not await publisher.connection.wait_connected(_CONNECT_WAIT_SEC):
        log.warning("MQTT 미연결 — CONFIG 재발행을 건너뛴다")
        return
    try:
        await publish_all(publisher)
    except Exception:  # noqa: BLE001
        log.exception("CONFIG 재발행 실패")
