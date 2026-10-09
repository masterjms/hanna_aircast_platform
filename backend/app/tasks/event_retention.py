"""방송 기록 보관 기간 정리 (문제점 50번 5항, 2026-10-04).

끝난 방송(broadcast_events.ended_at 이 보관 기간보다 오래된 행)을 지운다. 단말 응답(device_events)과
수신 단말 스냅숏(broadcast_recipients)은 FK CASCADE 로 함께 사라진다. 방송에 붙지 않은 단말
이벤트(OFFLINE 알림 등, event_id NULL)도 같은 기간이 지나면 지운다. 만료된 다운로드·OTA 토큰도
여기서 치운다.

기간은 EVENT_RETENTION_DAYS(기본 150일 = 5개월). 진행 중(ended_at NULL)인 방송은 건드리지 않는다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import delete

from app.config import settings
from app.db import session_scope
from app.models.auth_log import LoginEvent
from app.models.event import BroadcastEvent, DeviceEvent
from app.models.file import DownloadToken
from app.models.ota import OtaToken

log = logging.getLogger(__name__)


async def sweep(now: dt.datetime | None = None) -> dict[str, int]:
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=settings.event_retention_days)
    async with session_scope() as db:
        events = (
            await db.execute(
                delete(BroadcastEvent).where(
                    BroadcastEvent.ended_at.is_not(None), BroadcastEvent.ended_at < cutoff
                )
            )
        ).rowcount
        loose = (
            await db.execute(
                delete(DeviceEvent).where(
                    DeviceEvent.event_id.is_(None), DeviceEvent.received_at < cutoff
                )
            )
        ).rowcount
        tokens = (
            await db.execute(delete(DownloadToken).where(DownloadToken.expires_at < now))
        ).rowcount
        tokens += (await db.execute(delete(OtaToken).where(OtaToken.expires_at < now))).rowcount
        # 로그인 기록은 더 오래 둔다(문제점 65·68번, 접속기록 보관 기준).
        login_cutoff = now - dt.timedelta(days=settings.login_retention_days)
        logins = (
            await db.execute(delete(LoginEvent).where(LoginEvent.logged_in_at < login_cutoff))
        ).rowcount
    return {
        "events": int(events or 0),
        "loose_device_events": int(loose or 0),
        "tokens": int(tokens or 0),
        "logins": int(logins or 0),
    }


async def run() -> None:
    """스케줄러 진입점. 실패해도 다음 주기에 다시 한다."""
    try:
        counts = await sweep()
    except Exception:  # noqa: BLE001
        log.exception("방송 기록 보관 정리 실패")
        return
    if any(counts.values()):
        log.info("방송 기록 보관 정리(%d일): %s", settings.event_retention_days, counts)
