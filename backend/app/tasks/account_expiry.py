"""사용 기간이 끝난 계정을 지우는 정리 작업 (문제점 26번).

계정을 만들 때 정한 기간(`users.expires_at`)이 지나면 로그인은 그 즉시 막히고
(로그인 라우터·`get_current_user`), 이 작업이 주기적으로 돌면서 계정 자체를 지운다.
`expires_at` 이 NULL 인 계정은 무기한이라 대상이 아니다.

마지막 `super_admin` 은 지우지 않는다. 기간을 잘못 걸어 둔 계정 하나 때문에
아무도 시스템을 관리할 수 없게 되는 상황을 만들지 않는다 — 그 계정은 만료 상태로
남아 로그인만 막히고, 사람이 기간을 늘려 되살릴 수 있다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select

from app.constants import Role
from app.db import session_scope
from app.models.org import User

log = logging.getLogger(__name__)


async def sweep() -> int:
    """만료된 계정을 지우고 지운 수를 돌려준다."""
    now = dt.datetime.now(dt.timezone.utc)
    async with session_scope() as db:
        expired = list(
            (
                await db.scalars(
                    select(User).where(User.expires_at.is_not(None), User.expires_at <= now)
                )
            ).all()
        )
        if not expired:
            return 0

        # 살아남는 super_admin 이 하나도 없게 되는 삭제는 하지 않는다.
        total_supers = await db.scalar(
            select(func.count()).select_from(User).where(User.role == Role.SUPER_ADMIN.value)
        )
        expiring_supers = sum(1 for u in expired if u.role == Role.SUPER_ADMIN.value)

        removed = 0
        for user in expired:
            if user.role == Role.SUPER_ADMIN.value and (total_supers or 0) - expiring_supers < 1:
                log.warning(
                    "만료된 최고 관리자 %s 는 남겨 둔다 — 지우면 관리자가 없어진다", user.username
                )
                continue
            log.info("만료 계정 삭제: %s (만료 %s)", user.username, user.expires_at)
            await db.delete(user)
            removed += 1
        return removed


async def run() -> None:
    """스케줄러가 부르는 진입점. 실패해도 서버는 계속 돈다."""
    try:
        removed = await sweep()
    except Exception:  # noqa: BLE001 - 다음 주기에 다시 시도한다
        log.exception("만료 계정 정리 실패")
        return
    if removed:
        log.info("만료 계정 %d 개 삭제", removed)
