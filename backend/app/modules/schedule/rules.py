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
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from app.constants import Repeat

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
    )


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


def next_occurrence(rule: Rule, after: dt.datetime) -> dt.datetime | None:
    """after 이후 첫 실행 시각. LOOKAHEAD_DAYS 안에 없으면 None(예: 빈 요일 목록)."""
    found = occurrences(rule, after, after + dt.timedelta(days=LOOKAHEAD_DAYS))
    return found[0] if found else None
