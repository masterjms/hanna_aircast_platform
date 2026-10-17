"""감사 기록 · 기록 무결성 (문제점 70번 10/15 ①④, 2026-10-17).

GET  /api/audit           관리 행위 기록 목록 — 최고 관리자는 전부, 그 외는 자기 것만
GET  /api/audit/verify    접속기록·감사 기록 해시 체인 다시 계산(최고 관리자)
POST /api/audit/review    월 점검 — 확인하고 두 기록의 끝 해시(앵커)를 감사 기록에 남김(최고 관리자)
GET  /api/audit/export    한 달치 CSV(엑셀) — 응답 머리 X-Content-SHA256 = 파일 해시(최고 관리자)
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
from typing import Annotated, Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select, text

from app.core.audit import client_ip
from app.core.deps import CurrentUser, Db, SuperAdmin
from app.errors import ApiError
from app.models.audit import AuditEvent
from app.models.auth_log import LoginEvent
from app.schemas.audit import AuditEventOut, AuditEventPage, ChainCheck, VerifyResult

router = APIRouter(prefix="/api/audit", tags=["audit"])

KST = ZoneInfo("Asia/Seoul")
REVIEW_ACTION = "기록 점검"

#: 표 이름은 이 목록에서만 — SQL 에 끼워 넣으므로 바깥 값을 받지 않는다.
_CHAINS = {
    "login_events": ("접속기록", "xwifi_login_event_hash"),
    "audit_events": ("감사 기록", "xwifi_audit_event_hash"),
}


@router.get("", response_model=AuditEventPage)
async def list_audit(
    db: Db,
    actor: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: int = 10,
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> AuditEventPage:
    if size not in (10, 20, 50):
        size = 10
    stmt = select(AuditEvent)
    if actor.role != "super_admin":
        stmt = stmt.where(AuditEvent.user_id == actor.id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                AuditEvent.username.ilike(like),
                AuditEvent.action.ilike(like),
                AuditEvent.target.ilike(like),
                AuditEvent.ip.ilike(like),
            )
        )
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(stmt.order_by(AuditEvent.id.desc()).offset((page - 1) * size).limit(size))
    ).all()
    return AuditEventPage(
        total=total, page=page, size=size, items=[AuditEventOut.model_validate(r) for r in rows]
    )


async def _check_chain(db: Db, table: str, anchors: list[dict[str, Any]]) -> ChainCheck:
    label, fn = _CHAINS[table]
    rows, first_bad, last_seq, first_seq = (
        await db.execute(
            text(
                f"""
                WITH c AS (
                  SELECT chain_seq, row_hash, prev_hash, {fn}(e) AS calc,
                         lag(row_hash) OVER (ORDER BY chain_seq) AS lag_hash,
                         row_number() OVER (ORDER BY chain_seq) AS rn
                    FROM {table} e WHERE chain_seq IS NOT NULL
                )
                SELECT count(*),
                       min(chain_seq) FILTER (
                         WHERE row_hash IS DISTINCT FROM calc
                            OR (rn > 1 AND prev_hash IS DISTINCT FROM lag_hash)),
                       max(chain_seq),
                       min(chain_seq)
                  FROM c
                """
            )
        )
    ).one()
    unchained = int(
        await db.scalar(text(f"SELECT count(*) FROM {table} WHERE chain_seq IS NULL")) or 0
    )
    hash_at = text(f"SELECT row_hash FROM {table} WHERE chain_seq = :s")
    last_hash = await db.scalar(hash_at, {"s": last_seq}) if last_seq is not None else None
    mismatch: list[int] = []
    for a in anchors:
        if a.get("table") != table or a.get("seq") is None:
            continue
        seq = int(a["seq"])
        if first_seq is not None and seq < first_seq:
            continue  # 보관 기간이 지나 지워진 구간
        if await db.scalar(hash_at, {"s": seq}) != a.get("hash"):
            mismatch.append(seq)
    return ChainCheck(
        table=table,
        label=label,
        rows=int(rows or 0),
        ok=first_bad is None and unchained == 0 and not mismatch,
        first_bad_seq=first_bad,
        unchained=unchained,
        last_seq=last_seq,
        last_hash=last_hash,
        anchor_mismatch=mismatch,
    )


async def _verify(db: Db) -> VerifyResult:
    reviews = (
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == REVIEW_ACTION, AuditEvent.status < 400)
            .order_by(AuditEvent.id.desc())
        )
    ).all()
    anchors = [a for r in reviews for a in ((r.detail or {}).get("anchors") or [])]
    chains = [await _check_chain(db, t, anchors) for t in _CHAINS]
    last = reviews[0] if reviews else None
    return VerifyResult(
        ok=all(c.ok for c in chains),
        checked_at=dt.datetime.now(dt.timezone.utc),
        chains=chains,
        last_review_at=last.at if last else None,
        last_review_by=last.username if last else None,
    )


@router.get("/verify", response_model=VerifyResult)
async def verify(db: Db, _: SuperAdmin) -> VerifyResult:
    return await _verify(db)


@router.post("/review", response_model=VerifyResult)
async def review(db: Db, user: SuperAdmin, request: Request) -> VerifyResult:
    """월 점검. 결과와 두 기록의 끝 해시(앵커)를 감사 기록 한 줄로 남긴다.

    앵커가 있으면, 누군가 DB 에서 트리거를 끄고 기록을 고친 뒤 체인을 통째로 다시 계산해 덮어도
    그 앵커 자리의 해시가 달라져 드러난다. 미들웨어는 이 경로를 따로 남기지 않는다(_SKIP).
    """
    result = await _verify(db)
    db.add(
        AuditEvent(
            user_id=user.id,
            username=user.username,
            role=user.role,
            ip=client_ip(request.scope),
            method="POST",
            path="/api/audit/review",
            action=REVIEW_ACTION,
            status=200,
            detail={
                "ok": result.ok,
                "anchors": [
                    {"table": c.table, "seq": c.last_seq, "hash": c.last_hash}
                    for c in result.chains
                    if c.last_seq is not None
                ],
                "problems": [
                    {
                        "table": c.table,
                        "first_bad_seq": c.first_bad_seq,
                        "unchained": c.unchained,
                        "anchor_mismatch": c.anchor_mismatch,
                    }
                    for c in result.chains
                    if not c.ok
                ],
            },
        )
    )
    await db.flush()
    result.last_review_at = dt.datetime.now(dt.timezone.utc)
    result.last_review_by = user.username
    return result


def _month_range(month: str) -> tuple[dt.datetime, dt.datetime]:
    try:
        start = dt.datetime.strptime(month, "%Y-%m").replace(tzinfo=KST)
    except ValueError as exc:
        raise ApiError("month 는 YYYY-MM 형식입니다.", code="BAD_MONTH") from exc
    end = (start + dt.timedelta(days=32)).replace(day=1)
    return start, end


def _kst(t: dt.datetime | None) -> str:
    return t.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S") if t else ""


@router.get("/export")
async def export(
    db: Db,
    _: SuperAdmin,
    kind: Annotated[str, Query(pattern="^(login|audit)$")],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> Response:
    """한 달치 CSV. 엑셀에서 바로 열리게 UTF-8 BOM.

    파일 해시를 응답 머리(X-Content-SHA256)에 실어 점검표에 적게 한다.
    """
    start, end = _month_range(month)
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "login":
        w.writerow(
            ["순번", "연결 시각", "연결 해지 시각", "아이디", "결과", "IP", "브라우저", "해시"]
        )
        logins = (
            await db.scalars(
                select(LoginEvent)
                .where(LoginEvent.logged_in_at >= start, LoginEvent.logged_in_at < end)
                .order_by(LoginEvent.chain_seq)
            )
        ).all()
        for r in logins:
            w.writerow(
                [r.chain_seq, _kst(r.logged_in_at), _kst(r.logged_out_at), r.username,
                 r.result, r.ip or "", r.user_agent or "", r.row_hash or ""]
            )
        name = f"접속기록_{month}.csv"
    else:
        w.writerow(["순번", "시각", "아이디", "역할", "IP", "행위", "대상", "결과", "요청", "해시"])
        audits = (
            await db.scalars(
                select(AuditEvent)
                .where(AuditEvent.at >= start, AuditEvent.at < end)
                .order_by(AuditEvent.chain_seq)
            )
        ).all()
        for a in audits:
            w.writerow(
                [a.chain_seq, _kst(a.at), a.username or "", a.role or "", a.ip or "",
                 a.action, a.target or "", a.status, f"{a.method} {a.path}", a.row_hash or ""]
            )
        name = f"감사기록_{month}.csv"
    body = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
            "Access-Control-Expose-Headers": "X-Content-SHA256, Content-Disposition",
        },
    )
