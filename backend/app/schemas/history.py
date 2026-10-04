"""방송 기록(단말별) 응답 모양."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class HistoryRow(BaseModel):
    event_id: int
    #: file · schedule · live · ota
    kind: str
    kind_label: str
    #: 파일 방송이면 파일 이름, 실시간이면 "라이브", OTA 면 패키지 이름.
    source: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None
    mac: str
    label: str | None
    village_name: str | None
    #: 보낼 때 온라인이라 명령을 보낸 단말인가. False 면 「오프라인」.
    sent: bool
    result_type: str | None
    responded_at: dt.datetime | None
    #: 정상 · 실패 · 응답 없음 · 오프라인 · 진행 중
    verdict: str
    reason: str | None


class HistoryPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[HistoryRow]
