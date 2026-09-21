"""자동방송 스케줄 스키마."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from app.constants import Repeat, ScheduleTarget
from app.schemas.common import ApiModel

_KST = ZoneInfo("Asia/Seoul")


class YearDate(BaseModel):
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)


def _check_shape(
    repeat: Repeat,
    weekdays: list[int] | None,
    month_days: list[int] | None,
    year_dates: list[YearDate] | None,
    once_date: dt.date | None = None,
) -> None:
    """반복 종류에 맞는 값만 허용한다. 매주인데 요일이 비면 영영 안 나가는 규칙이 된다."""
    if repeat is Repeat.ONCE and once_date is None:
        raise ValueError("한 번만 예약에는 날짜가 필요합니다.")
    if repeat is Repeat.WEEKLY and not weekdays:
        raise ValueError("매주 반복에는 요일이 하나 이상 필요합니다.")
    if repeat is Repeat.MONTHLY and not month_days:
        raise ValueError("매월 반복에는 날짜가 하나 이상 필요합니다.")
    if repeat is Repeat.YEARLY and not year_dates:
        raise ValueError("매년 반복에는 날짜가 하나 이상 필요합니다.")


class ScheduleCreate(BaseModel):
    repeat: Repeat
    #: 0=일 … 6=토 (weekly)
    weekdays: list[int] | None = None
    #: 1~31 (monthly)
    month_days: list[int] | None = None
    #: (yearly)
    year_dates: list[YearDate] | None = None
    #: (once) 그 날짜(KST)에 한 번
    once_date: dt.date | None = None
    #: KST. 초는 버린다.
    fire_time: dt.time
    file_id: int
    target_scope: ScheduleTarget
    #: village 면 마을 id, device 면 MAC, organization 이면 기관 id.
    target_ids: list[str] = Field(default_factory=list, max_length=200)
    store_flash: bool = False
    enabled: bool = True

    @model_validator(mode="after")
    def _validate(self) -> ScheduleCreate:
        if self.weekdays is not None and any(not 0 <= d <= 6 for d in self.weekdays):
            raise ValueError("요일은 0(일)~6(토) 사이여야 합니다.")
        if self.month_days is not None and any(not 1 <= d <= 31 for d in self.month_days):
            raise ValueError("날짜는 1~31 사이여야 합니다.")
        _check_shape(
            self.repeat, self.weekdays, self.month_days, self.year_dates, self.once_date
        )
        if not self.target_ids:
            raise ValueError("대상이 필요합니다.")
        self.fire_time = self.fire_time.replace(second=0, microsecond=0)
        # 한 번만 예약이 이미 지난 시각이면 영영 안 나가는 규칙이 된다 — 저장 전에 막는다.
        if self.repeat is Repeat.ONCE and self.once_date is not None:
            at = dt.datetime.combine(self.once_date, self.fire_time, tzinfo=_KST)
            if at <= dt.datetime.now(_KST):
                raise ValueError("이미 지난 시각입니다. 앞으로의 날짜와 시각을 골라 주세요.")
        # 종류에 안 맞는 값은 비워 저장한다 — 나중에 종류를 바꿨을 때 옛 값이 남지 않게.
        if self.repeat is not Repeat.WEEKLY:
            self.weekdays = None
        if self.repeat is not Repeat.MONTHLY:
            self.month_days = None
        if self.repeat is not Repeat.YEARLY:
            self.year_dates = None
        if self.repeat is not Repeat.ONCE:
            self.once_date = None
        self.weekdays = sorted(set(self.weekdays)) if self.weekdays else self.weekdays
        self.month_days = sorted(set(self.month_days)) if self.month_days else self.month_days
        return self


class ScheduleUpdate(BaseModel):
    """부분 수정. 반복 종류·날짜·시각·대상·파일을 바꾸면 service 가 전체 모양을 다시 검사한다."""

    repeat: Repeat | None = None
    weekdays: list[int] | None = None
    month_days: list[int] | None = None
    year_dates: list[YearDate] | None = None
    once_date: dt.date | None = None
    fire_time: dt.time | None = None
    file_id: int | None = None
    target_scope: ScheduleTarget | None = None
    target_ids: list[str] | None = Field(default=None, max_length=200)
    store_flash: bool | None = None
    enabled: bool | None = None


class ScheduleRunOut(ApiModel):
    fire_at: dt.datetime
    #: started | skipped | failed
    status: str
    reason: str | None
    event_id: int | None
    created_at: dt.datetime


class ScheduleOut(ApiModel):
    id: int
    repeat: Repeat
    weekdays: list[int] | None
    month_days: list[int] | None
    year_dates: list[YearDate] | None
    once_date: dt.date | None = None
    fire_time: dt.time
    file_id: int
    file_name: str | None = None
    target_scope: ScheduleTarget
    target_ids: list[str]
    #: 사람이 읽는 대상 — "계곡마을", "금산군청 관할 전체", "12번, 13번".
    target_label: str = ""
    store_flash: bool
    enabled: bool
    created_by: int | None
    created_at: dt.datetime
    #: 다음 실행 시각(KST). 꺼져 있거나 규칙이 비어 있으면 null.
    next_fire_at: dt.datetime | None = None
    last_run: ScheduleRunOut | None = None
    #: 이 계정이 고치고 지울 수 있는가 — 대상 전체가 내 범위 안일 때만.
    editable: bool = False


class OccurrenceOut(BaseModel):
    """예정표·오늘 일정의 한 칸."""

    schedule_id: int
    fire_at: dt.datetime
    repeat: Repeat
    target_label: str
    file_name: str | None
