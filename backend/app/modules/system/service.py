"""시스템 설정 · 헬스체크 서비스.

current_config 테이블(싱글턴)을 소유한다.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import CONFIG_LIMITS
from app.errors import ApiError
from app.mqtt.publisher import MqttPublisher
from app.schemas.system import ConfigOut, ConfigUpdate, HealthOut
from app.tasks import config_reconcile
from app.tasks.config_reconcile import load_config

log = logging.getLogger(__name__)


async def get_config(db: AsyncSession) -> ConfigOut:
    return ConfigOut.model_validate(await load_config(db))


def _check_range(field: str, value: int) -> None:
    low, high = CONFIG_LIMITS[field]
    if not low <= value <= high:
        raise ApiError(
            f"{field} 는 {low}~{high} 범위여야 합니다.",
            code="CONFIG_OUT_OF_RANGE",
            detail={"field": field, "min": low, "max": high, "value": value},
        )


async def update_config(
    db: AsyncSession, payload: ConfigUpdate, publisher: MqttPublisher
) -> ConfigOut:
    """설정 저장 → config_version 증가 → MQTT 재발행.

    단말은 config_version 이 올라갈 때만 값을 다시 적용하므로 반드시 함께 올린다.
    발행이 실패해도 DB 는 커밋한다 — 정본은 DB 이고, 재조정 주기가 브로커를 따라잡는다.
    """
    config = await load_config(db)

    data = payload.model_dump(exclude_unset=True)
    if not data:
        return ConfigOut.model_validate(config)

    for field, value in data.items():
        _check_range(field, value)
        setattr(config, field, value)

    config.config_version += 1
    await db.flush()

    try:
        # 공통 설정만 바뀌었어도 단말별 CONFIG 까지 같이 내보낸다. 단말이 토픽별로
        # 버전을 따로 추적하지 않아서(사양 §4.3), 한쪽만 올리면 단말이 낡은 값을
        # 최종본으로 보고하게 된다.
        await config_reconcile.publish_all(publisher, db)
    except Exception:  # noqa: BLE001
        log.exception("CONFIG 발행 실패 (재조정 주기가 복구)")

    return ConfigOut.model_validate(config)


async def config_version(db: AsyncSession) -> int:
    """단말 CONFIG 를 개별 발행할 때 실을 버전. device 모듈이 쓴다."""
    return (await load_config(db)).config_version


async def health(db: AsyncSession, publisher: MqttPublisher) -> HealthOut:
    """의존 컴포넌트 상태.

    실패해도 200 으로 응답하고 본문에 담는다 — 로드밸런서가 아니라 사람이 보는 화면이라
    '무엇이 죽었는지'가 '죽었다'보다 중요하다.
    """
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
        log.exception("헬스체크: DB 접속 실패")

    mqtt_ok = publisher.connection.is_connected
    return HealthOut(
        status="ok" if (db_ok and mqtt_ok) else "degraded",
        database=db_ok,
        mqtt=mqtt_ok,
    )
