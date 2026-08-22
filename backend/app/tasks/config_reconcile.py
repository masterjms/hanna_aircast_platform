"""CONFIG 재조정(reconciliation).

브로커의 retain 은 캐시이고 정본은 current_config 테이블이다. 브로커가 재시작되거나
retain 이 유실되면 단말은 영영 낡은 설정으로 남는다. 그래서 기동 시 1회 + 주기적으로
DB 값을 다시 발행한다.

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


async def shared_village_id(db: AsyncSession) -> int | None:
    """공통 CONFIG 에 실을 마을. 배정된 단말이 전부 한 마을일 때만 그 값을 준다.

    현재 펌웨어는 `iotradio/all/config` 하나만 구독한다(통신 사양 §3.5). 토픽이
    하나라 village_id 도 하나뿐이므로, 마을이 둘 이상 섞이면 어느 쪽을 실어도
    나머지 마을 단말이 남의 마을 방송을 받게 된다. 그래서 그럴 땐 None 을 주고
    필드를 뺀다 — 잘못 배정하느니 미배정으로 두는 편이 안전하다.

    단말별 CONFIG 구독(publish_device_config)이 펌웨어에 붙으면 이 제약은
    없어지고, 이 함수도 같이 사라진다.
    """
    rows = (
        await db.execute(
            select(Device.village_id).where(Device.village_id.is_not(None)).distinct()
        )
    ).scalars().all()

    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        log.warning(
            "마을이 %d 개 배정돼 있어 공통 CONFIG 에서 village_id 를 뺀다 — "
            "단말별 CONFIG 구독이 붙기 전까지 다중 마을은 지원되지 않는다 (마을 %s)",
            len(rows),
            sorted(rows),
        )
    return None


async def publish_all(publisher: MqttPublisher) -> None:
    """공통 CONFIG 1회 + 마을이 배정된 단말별 CONFIG N회.

    미배정 단말은 건너뛴다 — 배정될 때 device 모듈이 개별 발행한다.
    """
    async with session_scope() as db:
        config = await load_config(db)
        version = config.config_version

        await publisher.publish_global_config(
            config_version=version,
            status_interval_sec=config.status_interval_sec,
            live_stats_interval_sec=config.live_stats_interval_sec,
            event_qos=config.event_qos,
            village_id=await shared_village_id(db),
        )

        rows = (
            await db.execute(
                select(Device.mac, Device.village_id).where(Device.village_id.is_not(None))
            )
        ).all()

    published = 0
    for mac, village_id in rows:
        try:
            await publisher.publish_device_config(
                mac=mac, village_id=village_id, config_version=version
            )
            published += 1
        except Exception:  # noqa: BLE001
            # 한 대가 실패해도 나머지는 계속 보낸다. 다음 주기에 다시 시도된다.
            log.exception("단말 CONFIG 발행 실패: %s", mac)

    log.info("CONFIG 재발행 완료 (version=%d, 단말 %d/%d대)", version, published, len(rows))


async def run(publisher: MqttPublisher, *, wait_for_connection: bool = True) -> None:
    """스케줄러가 부르는 진입점."""
    if wait_for_connection and not await publisher.connection.wait_connected(_CONNECT_WAIT_SEC):
        log.warning("MQTT 미연결 — CONFIG 재발행을 건너뛴다")
        return
    try:
        await publish_all(publisher)
    except Exception:  # noqa: BLE001
        log.exception("CONFIG 재발행 실패")
