"""자동방송 규칙 → 실행 시각.

규칙 하나가 DB 에 한 줄로 있고, "언제 나가나"는 저장하지 않고 **계산**한다 — cron 과
같다. 이 모듈이 그 계산의 유일한 자리다. 실행기와 화면(예정표·오늘 일정)이 같은
함수를 부르므로, 예정표에 보이는 것과 실제로 나가는 것이 어긋날 수 없다.

시각은 전부 Asia/Seoul 로 해석한다. DB 의 fire_time 은 timezone 없는 TIME 이고
그 값이 곧 한국 시각이다(관리자 계층·스케줄 설계).

규칙:
  daily    매일 fire_time
  weekly   weekdays (0=일 … 6=토) 의 fire_time
  monthly  month_days (1~31). 그 날이 없는 달(31일이 없는 달 등)은 건너뛴다 — cron 과 같다
  yearly   year_dates [{month, day}]. 2월 29일은 윤년에만 나간다
  once     once_date 의 fire_time 에 한 번. 지나면 다시 걸리지 않는다(2026-09-21)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from app.constants import REPEAT_LABEL, Repeat

KST = ZoneInfo("Asia/Seoul")

#: 한 번에 내다보는 최대 일수. 다음 실행 시각을 찾을 때 이 이상 뒤지지 않는다 —
#: 매년 2월 29일 규칙은 최대 4년이 걸릴 수 있어 넉넉히 둔다.
LOOKAHEAD_DAYS = 366 * 4 + 1


@dataclass(frozen=True)
class Rule:
    repeat: str
    fire_time: dt.time
    weekdays: frozenset[int] = field(default_factory=frozenset)
    month_days: frozenset[int] = field(default_factory=frozenset)
    year_dates: frozenset[tuple[int, int]] = field(default_factory=frozenset)
    once_date: dt.date | None = None


def rule_of(schedule: Any) -> Rule:
    """ORM 행(또는 같은 속성을 가진 무엇이든) → Rule."""
    year_dates = frozenset(
        (int(d["month"]), int(d["day"])) for d in (schedule.year_dates or [])
    )
    return Rule(
        repeat=str(schedule.repeat),
        fire_time=schedule.fire_time,
        weekdays=frozenset(schedule.weekdays or []),
        month_days=frozenset(schedule.month_days or []),
        year_dates=year_dates,
        once_date=getattr(schedule, "once_date", None),
    )


def schedule_when(repeat: str, fire_time: dt.time, once_date: dt.date | None = None) -> str:
    """「매일 07:00」, 한 번짜리는 「9/22 한 번 07:00」. 거절 사유에 어느 스케줄인지 대 준다."""
    head = REPEAT_LABEL.get(repeat, repeat)
    if once_date is not None:
        head = f"{once_date.month}/{once_date.day} {head}"
    return f"{head} {fire_time:%H:%M}"


def korean_weekday(d: dt.date) -> int:
    """0=일 … 6=토. Python 의 weekday() 는 월=0 이라 바꿔 쓴다."""
    return (d.weekday() + 1) % 7


def matches_date(rule: Rule, d: dt.date) -> bool:
    if rule.repeat == Repeat.DAILY.value:
        return True
    if rule.repeat == Repeat.WEEKLY.value:
        return korean_weekday(d) in rule.weekdays
    if rule.repeat == Repeat.MONTHLY.value:
        return d.day in rule.month_days
    if rule.repeat == Repeat.YEARLY.value:
        return (d.month, d.day) in rule.year_dates
    if rule.repeat == Repeat.ONCE.value:
        return d == rule.once_date
    return False


def occurrences(rule: Rule, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """[start, end) 안에서 이 규칙이 걸리는 시각들. 오름차순. 시각은 KST aware.

    start·end 는 aware datetime 이어야 한다(어느 시간대든 상관없다 — KST 로 바꿔 본다).
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start/end 는 timezone 이 있어야 한다")
    if end <= start:
        return []

    s = start.astimezone(KST)
    e = end.astimezone(KST)
    out: list[dt.datetime] = []
    day = s.date()
    last = e.date()
    # end 가 자정 정각이면 그 날은 포함하지 않는다.
    while day <= last:
        if matches_date(rule, day):
            at = dt.datetime.combine(day, rule.fire_time, tzinfo=KST)
            if s <= at < e:
                out.append(at)
        day += dt.timedelta(days=1)
    return out


def can_ever_match(rule: Rule) -> bool:
    """이 규칙이 언젠가 걸리기는 하는가.

    요일·날짜 목록이 비면 영영 안 걸린다. 그런 규칙에 LOOKAHEAD_DAYS 를 다 훑는 것은
    1465번 헛도는 일이라 먼저 걸러낸다(API 검증이 막지만 옛 데이터가 있을 수 있다).
    """
    if rule.repeat == Repeat.WEEKLY.value:
        return bool(rule.weekdays)
    if rule.repeat == Repeat.MONTHLY.value:
        return bool(rule.month_days)
    if rule.repeat == Repeat.YEARLY.value:
        return bool(rule.year_dates)
    if rule.repeat == Repeat.ONCE.value:
        return rule.once_date is not None
    return rule.repeat == Repeat.DAILY.value


def next_occurrence(rule: Rule, after: dt.datetime) -> dt.datetime | None:
    """after 이후(같은 시각 포함) 첫 실행 시각. LOOKAHEAD_DAYS 안에 없으면 None.

    **찾는 즉시 멈춘다.** 예전에는 occurrences() 로 범위 전체(4년)의 회차를 모아 첫
    번째만 꺼냈다 — 규칙 하나에 1.1~1.6ms 가 들어서, 목록에 100개면 그것만으로
    113ms CPU 였다(2026-09-10 실측). 매일 규칙은 이제 1~2번 반복이면 끝난다.

    경계는 occurrences() 와 같다: `at >= after` 를 만족하는 첫 시각. 지금이 정확히
    발사 시각이면 그 시각을 돌려준다.
    """
    if after.tzinfo is None:
        raise ValueError("after 는 timezone 이 있어야 한다")
    if not can_ever_match(rule):
        return None

    s = after.astimezone(KST)
    if rule.repeat == Repeat.ONCE.value:
        # 한 번짜리는 날짜를 훑을 필요가 없다 — 그 시각이 아직 안 왔는지만 본다.
        at = dt.datetime.combine(rule.once_date, rule.fire_time, tzinfo=KST)
        return at if at >= s else None
    day = s.date()
    last = (s + dt.timedelta(days=LOOKAHEAD_DAYS)).date()
    while day <= last:
        if matches_date(rule, day):
            at = dt.datetime.combine(day, rule.fire_time, tzinfo=KST)
            if at >= s:
                return at
        day += dt.timedelta(days=1)
    return None
