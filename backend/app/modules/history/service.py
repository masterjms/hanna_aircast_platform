"""방송 기록 — 단말별 한 줄 (문제점 50번, 2026-10-04).

예전 「방송 기록」은 대시보드 요약의 최근 10건(방송 단위)이었다. 현장은 **단말마다** 그 방송을
받았는지, 못 받았으면 왜인지(오프라인·응답 없음·실패)를 보고 싶어 한다. 그래서 행 단위를
(방송 × 단말)로 바꾸고 페이지로 넘긴다.

행의 출처:
  · broadcast_recipients — 0022 뒤에 건 방송. 보낼 때 범위에 있던 단말 전부(오프라인 포함).
  · 그 전 방송은 스냅숏이 없다 — device_events 에 응답을 남긴 단말만 행이 된다(그때 오프라인
    이었던 단말은 알 수 없으니 빠진다).

판정(verdict)은 그 단말의 **마지막 결과 메시지**(telemetry 제외)로 정한다 — 방송 제어 화면의
단말별 응답과 같은 규칙(broadcast.service.result_ok).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope import VillageScope
from app.modules.broadcast import service as broadcast_service
from app.schemas.history import HistoryPage, HistoryRow

#: 화면이 고르는 페이지 크기(문제점 50번: 10·20·50).
PAGE_SIZES = (10, 20, 50)

#: 종류 필터 → SQL 조건. schedule 은 FILE_START 중 schedule_id 가 있는 것.
_KIND_SQL = {
    "file": "e.event_type = 'FILE_START' AND e.schedule_id IS NULL",
    "schedule": "e.event_type = 'FILE_START' AND e.schedule_id IS NOT NULL",
    "live": "e.event_type = 'LIVE_START'",
    "ota": "e.event_type = 'OTA_START'",
}

#: 기간을 안 주면 최근 이 일수만 본다 — 5개월치를 두 번 훑는 요청을 막는다(병목 감사 H6).
DEFAULT_WINDOW_DAYS = 31

#: 단계별로 좁힌다(병목 감사 H6):
#:   ev   — 방송 먼저 거른다(종류·기간). 여기서 수천 → 수십 건으로 준다.
#:   rec  — 그 방송들의 단말 줄만. 옛 방송(스냅숏 없는 0022 이전)만 device_events 에서 DISTINCT 로
#:          만들고, 그 범위를 event_id < :legacy_before 로 잘라 인덱스로 끝낸다.
#:   rows — 검색어·마을 범위.
#:   page — 정렬 + LIMIT/OFFSET 을 **먼저** 하고, 마지막 결과(LATERAL)는 그 쪽의 10~50줄에만 붙인다.
#:          LATERAL 은 (event_id, mac, received_at DESC) 인덱스로 한 번에 찾는다.
_BASE = """
WITH ev AS (
    SELECT e.id, e.event_type, e.schedule_id, e.file_name, e.triggered_at, e.ended_at
    FROM broadcast_events e
    WHERE e.event_type IN ('FILE_START', 'LIVE_START', 'OTA_START')
      {where_ev}
),
rec AS (
    SELECT r.event_id, r.mac, r.label, r.village_id, r.village_name, r.sent
    FROM broadcast_recipients r
    JOIN ev ON ev.id = r.event_id
    UNION ALL
    SELECT d.event_id, d.mac, dev.label, dev.village_id, v.name, TRUE
    FROM (
        SELECT DISTINCT de.event_id, de.mac
        FROM device_events de
        JOIN ev ON ev.id = de.event_id
        WHERE de.event_id < :legacy_before
    ) d
    LEFT JOIN devices dev ON dev.mac = d.mac
    LEFT JOIN villages v ON v.id = dev.village_id
    WHERE NOT EXISTS (SELECT 1 FROM broadcast_recipients r2 WHERE r2.event_id = d.event_id)
),
rows AS (
    SELECT ev.id AS event_id, ev.event_type, ev.schedule_id, ev.file_name,
           ev.triggered_at, ev.ended_at,
           rec.mac, rec.label, rec.village_id, rec.village_name, rec.sent
    FROM ev
    JOIN rec ON rec.event_id = ev.id
    {where_rows}
)
"""

_PAGE = """
, page AS (
    SELECT * FROM rows ORDER BY triggered_at DESC, event_id DESC, mac LIMIT :limit OFFSET :offset
)
SELECT page.*, last.result_type, last.payload, last.received_at
FROM page
LEFT JOIN LATERAL (
    SELECT de.result_type, de.payload, de.received_at
    FROM device_events de
    WHERE de.event_id = page.event_id AND de.mac = page.mac
      AND (de.result_type IS NULL OR de.result_type NOT IN ('LIVE_STATS', 'OTA_PROGRESS'))
    ORDER BY de.received_at DESC
    LIMIT 1
) last ON TRUE
ORDER BY page.triggered_at DESC, page.event_id DESC, page.mac
"""


def kind_of(event_type: str, schedule_id: int | None) -> str:
    if event_type == "LIVE_START":
        return "live"
    if event_type == "OTA_START":
        return "ota"
    return "schedule" if schedule_id is not None else "file"


KIND_LABEL = {"file": "파일 방송", "schedule": "예약 방송", "live": "실시간 방송", "ota": "OTA"}


def judge(
    *, sent: bool, result_type: str | None, payload: dict[str, Any] | None, ended: bool
) -> tuple[str, str | None]:
    """(판정, 사유). 판정은 화면 글자 그대로 — 정상 / 실패 / 응답 없음 / 오프라인 / 진행 중."""
    if not sent:
        return "오프라인", "미발송 — 방송 당시 꺼져 있던 단말"
    if result_type is None:
        return ("응답 없음" if ended else "진행 중"), None
    ok = broadcast_service.result_ok(result_type, payload or {})
    reason = broadcast_service._reason_text(result_type, payload or {})  # noqa: SLF001
    if ok is True:
        return "정상", None
    if ok is False:
        return "실패", reason
    return "응답", reason


async def page(
    db: AsyncSession,
    scope: VillageScope,
    *,
    page_no: int,
    size: int,
    kind: str | None,
    q: str | None,
    date_from: dt.date | None,
    date_to: dt.date | None,
) -> HistoryPage:
    if size not in PAGE_SIZES:
        size = PAGE_SIZES[0]
    where_ev: list[str] = []
    where_rows: list[str] = []
    params: dict[str, Any] = {}
    if kind in _KIND_SQL:
        where_ev.append(_KIND_SQL[kind])
    if q:
        where_rows.append(
            "(rec.mac ILIKE :q OR rec.label ILIKE :q OR rec.village_name ILIKE :q "
            "OR ev.file_name ILIKE :q)"
        )
        params["q"] = f"%{q.strip()}%"
    if date_from is None and date_to is None:
        # 무제한 조회는 받지 않는다 — 화면은 기본 7일, 비워 보내면 최근 31일.
        date_from = dt.datetime.now(_KST).date() - dt.timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    if date_from is not None:
        where_ev.append("e.triggered_at >= :date_from")
        params["date_from"] = dt.datetime.combine(date_from, dt.time.min, tzinfo=_KST)
    if date_to is not None:
        where_ev.append("e.triggered_at < :date_to")
        params["date_to"] = dt.datetime.combine(
            date_to + dt.timedelta(days=1), dt.time.min, tzinfo=_KST
        )
    if not scope.all_villages:
        # 이장·기관 관리자는 자기 마을 단말의 줄만 본다(스냅숏의 마을 기준).
        if scope.is_empty:
            return HistoryPage(total=0, page=page_no, size=size, items=[])
        where_rows.append("rec.village_id = ANY(:village_ids)")
        params["village_ids"] = list(scope.village_ids)

    # 스냅숏이 없는 옛 방송의 경계. 첫 스냅숏보다 앞선 event_id 만 device_events 에서 만든다.
    params["legacy_before"] = int(
        await db.scalar(
            text("SELECT COALESCE(MIN(event_id), 2147483647) FROM broadcast_recipients")
        )
        or 0
    )

    def clause(items: list[str]) -> str:
        return ("AND " + " AND ".join(f"({w})" for w in items)) if items else ""

    base = _BASE.format(
        where_ev=clause(where_ev), where_rows=clause(where_rows).replace("AND ", "WHERE ", 1)
    )
    total = int(await db.scalar(text(base + "SELECT COUNT(*) FROM rows"), params) or 0)
    offset = max(page_no - 1, 0) * size
    rows = (
        await db.execute(text(base + _PAGE), {**params, "limit": size, "offset": offset})
    ).mappings().all()

    items = []
    for r in rows:
        kind_key = kind_of(r["event_type"], r["schedule_id"])
        verdict, reason = judge(
            sent=bool(r["sent"]),
            result_type=r["result_type"],
            payload=r["payload"],
            ended=r["ended_at"] is not None,
        )
        items.append(
            HistoryRow(
                event_id=r["event_id"],
                kind=kind_key,
                kind_label=KIND_LABEL[kind_key],
                source=r["file_name"] if kind_key != "live" else "라이브",
                started_at=r["triggered_at"],
                ended_at=r["ended_at"],
                mac=r["mac"],
                label=r["label"],
                village_name=r["village_name"],
                sent=bool(r["sent"]),
                result_type=r["result_type"],
                responded_at=r["received_at"],
                verdict=verdict,
                reason=reason,
            )
        )
    return HistoryPage(total=total, page=page_no, size=size, items=items)


_KST = dt.timezone(dt.timedelta(hours=9))
