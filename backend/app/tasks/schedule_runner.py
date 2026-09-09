"""자동방송 실행기 — 매분 규칙을 평가해 걸리는 회차를 방송으로 건다.

스케줄 설계 2026-09-09.

  tick(매분) → 켜진 규칙 읽기
            → 규칙 엔진으로 [지금-유예, 다음 분) 창의 실행 시각 계산
            → schedule_runs INSERT 시도 (유니크 충돌 = 이미 처리됨 → 물러남)
            → 유예(SCHEDULE_GRACE_SEC)를 넘긴 회차는 skipped(늦음)
            → organization 대상은 마을로 펼침
            → start_file_broadcast(triggered_by=None, schedule_id=…)
            → 겹침·온라인 없음·파일 문제는 skipped + 사유, 그 밖의 예외는 failed

중복 실행 방지의 실체는 코드가 아니라 schedule_runs 의 UNIQUE(schedule_id, fire_at) 다.
INSERT 를 방송보다 **먼저** 하므로 서버가 재시작해도, 같은 분에 tick 이 두 번 돌아도
한 회차는 한 번만 나간다. 대신 INSERT 직후 프로세스가 죽으면 그 회차는 pending 으로
남고 나가지 않는다 — 마을 방송에서는 두 번 나가는 것보다 한 번 빠지는 것이 낫다.

재시도는 하지 않는다. 늦은 마을방송은 사고다.

창에 유예만큼 과거를 포함하는 이유: tick 이 밀려 한 분을 건너뛰어도 그 회차가 다음
tick 의 창에 들어온다. 이미 처리한 회차는 유니크가 걸러 준다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.constants import SCHEDULE_GRACE_SEC, ScheduleTarget, TargetScope
from app.core.scope import VillageScope
from app.db import session_scope
from app.errors import ApiError
from app.models.schedule import Schedule, ScheduleRun
from app.modules.broadcast import service as broadcast_service
from app.modules.schedule import rules
from app.modules.schedule import service as schedule_service
from app.mqtt.publisher import MqttPublisher
from app.schemas.broadcast import FileBroadcastRequest

log = logging.getLogger(__name__)


def tick_window(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """이번 tick 이 평가할 [start, end). 분 단위로 자르고 과거로 유예만큼 넓힌다."""
    floor = now.replace(second=0, microsecond=0)
    return floor - dt.timedelta(seconds=SCHEDULE_GRACE_SEC), floor + dt.timedelta(minutes=1)


async def _claim(db, schedule_id: int, fire_at: dt.datetime) -> ScheduleRun | None:
    """이 회차를 내가 처리한다고 표시한다. 이미 누가 했으면 None."""
    run = ScheduleRun(schedule_id=schedule_id, fire_at=fire_at, status="pending")
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
    except IntegrityError:
        return None
    return run


async def _fire(db, publisher: MqttPublisher, s: Schedule, run: ScheduleRun) -> None:
    target_scope = s.target_scope
    target_ids = [str(t) for t in s.target_ids]
    if target_scope == ScheduleTarget.ORGANIZATION.value:
        villages = await schedule_service.expand_org_villages(
            db, [int(t) for t in target_ids]
        )
        if not villages:
            run.status, run.reason = "skipped", "관할에 마을이 없음"
            return
        target_scope, target_ids = TargetScope.VILLAGE.value, [str(v) for v in villages]

    payload = FileBroadcastRequest(
        file_id=s.file_id,
        target_scope=TargetScope(target_scope),
        target_ids=target_ids,
        store_flash=s.store_flash,
        autoplay=True,
    )
    try:
        async with db.begin_nested():
            out = await broadcast_service.start_file_broadcast(
                db,
                payload,
                VillageScope.for_super_admin(),
                publisher,
                user_id=None,
                schedule_id=s.id,
            )
    except ApiError as exc:
        # 겹침·온라인 단말 없음·파일 문제 — 규칙은 정상이고 이번 회차만 못 나간 것.
        run.status, run.reason = "skipped", f"{exc.code}: {exc.message}"[:200]
        log.info("스케줄 #%d %s 건너뜀: %s", s.id, run.fire_at, run.reason)
        return
    except Exception as exc:  # noqa: BLE001 - 한 규칙의 실패가 다음 규칙을 막으면 안 된다
        run.status, run.reason = "failed", str(exc)[:200]
        log.exception("스케줄 #%d %s 실패", s.id, run.fire_at)
        return
    run.status, run.event_id = "started", out.id
    log.info("스케줄 #%d %s → 방송 #%d", s.id, run.fire_at, out.id)


async def tick(publisher: MqttPublisher, now: dt.datetime | None = None) -> dict[str, int]:
    """한 번 평가한다. 반환값은 건수 — 로그·테스트용."""
    now = now or dt.datetime.now(dt.timezone.utc)
    start, end = tick_window(now)
    counts = {"started": 0, "skipped": 0, "failed": 0, "claimed_elsewhere": 0}

    async with session_scope() as db:
        schedules = (
            await db.scalars(select(Schedule).where(Schedule.enabled.is_(True)))
        ).all()
        for s in schedules:
            for at in rules.occurrences(rules.rule_of(s), start, end):
                if at > now:
                    continue  # 같은 분 안이지만 아직 그 시각이 아니다
                run = await _claim(db, s.id, at)
                if run is None:
                    counts["claimed_elsewhere"] += 1
                    continue
                late = (now - at).total_seconds()
                if late > SCHEDULE_GRACE_SEC:
                    run.status, run.reason = "skipped", f"{int(late)}초 늦어 건너뜀"
                    log.warning("스케줄 #%d %s 늦어 건너뜀 (%d초)", s.id, at, int(late))
                else:
                    await _fire(db, publisher, s, run)
                counts[run.status] += 1
                await db.flush()
    return counts


async def run(publisher: MqttPublisher) -> None:
    """스케줄러가 부르는 진입점. 실패해도 서버는 계속 돈다."""
    try:
        counts = await tick(publisher)
    except Exception:  # noqa: BLE001 - 다음 분에 다시 돈다
        log.exception("자동방송 tick 실패")
        return
    if counts["started"] or counts["skipped"] or counts["failed"]:
        log.info("자동방송 tick: %s", counts)
