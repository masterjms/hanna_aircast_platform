"""방송 제어 라우터."""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket

from app.core.deps import CurrentUser, Db, LiveReg, Publisher, Scope
from app.live.ingest import ingest_endpoint
from app.modules.broadcast import service
from app.schemas.broadcast import (
    BroadcastOut,
    BroadcastStopRequest,
    FileBroadcastRequest,
    LiveBroadcastRequest,
)

router = APIRouter(tags=["broadcast"])


# ── 조회 ─────────────────────────────────────────────────────────────────
@router.get("/api/broadcast/active", response_model=list[BroadcastOut])
async def list_active(db: Db, scope: Scope, registry: LiveReg) -> list[BroadcastOut]:
    """진행 중인 방송. 대시보드와 방송 제어 화면이 폴링한다."""
    return await service.list_active(db, scope, registry)


# ── 파일 방송 ────────────────────────────────────────────────────────────
@router.post("/api/broadcast/file/start", response_model=BroadcastOut)
async def start_file(
    payload: FileBroadcastRequest,
    db: Db,
    scope: Scope,
    user: CurrentUser,
    publisher: Publisher,
) -> BroadcastOut:
    """파일 방송 시작.

    대상 단말이 이미 다른 방송에 잡혀 있으면 409 BROADCAST_OVERLAP 이 나간다.
    서버는 진행 중인 방송을 자동으로 끊지 않는다.
    """
    return await service.start_file_broadcast(db, payload, scope, publisher, user_id=user.id)


@router.post("/api/broadcast/file/stop", response_model=BroadcastOut)
async def stop_file(
    payload: BroadcastStopRequest,
    db: Db,
    scope: Scope,
    publisher: Publisher,
) -> BroadcastOut:
    return await service.stop_file_broadcast(db, payload.broadcast_id, scope, publisher)


# ── 실시간 방송 ──────────────────────────────────────────────────────────
@router.post("/api/broadcast/live/start", response_model=BroadcastOut)
async def start_live(
    payload: LiveBroadcastRequest,
    db: Db,
    scope: Scope,
    user: CurrentUser,
    publisher: Publisher,
    registry: LiveReg,
) -> BroadcastOut:
    """실시간 방송 시작.

    응답의 ingest_path 로 브라우저가 WebSocket 을 열어 마이크를 밀어 넣는다.
    단말은 stream_url(/live/<마을8자리>/<세션id>)로 붙는다.
    """
    return await service.start_live_broadcast(
        db, payload, scope, publisher, registry, user_id=user.id
    )


@router.post("/api/broadcast/live/stop", response_model=BroadcastOut)
async def stop_live(
    payload: BroadcastStopRequest,
    db: Db,
    scope: Scope,
    publisher: Publisher,
    registry: LiveReg,
) -> BroadcastOut:
    return await service.stop_live_broadcast(db, payload.broadcast_id, scope, publisher, registry)


@router.websocket("/ingest")
async def ingest(ws: WebSocket, session: int = Query(...)) -> None:
    """브라우저 마이크 업링크.

    인증은 헤더가 아니라 첫 메시지로 받는다 — 브라우저 WebSocket 은
    커스텀 헤더를 붙일 수 없다. 자세한 규약은 app/live/ingest.py 참고.
    """
    await ingest_endpoint(ws, session, ws.app.state.live_registry)


# ── 상세 (다른 /api/broadcast/* 경로에 가려지지 않도록 맨 아래) ──────────
@router.get("/api/broadcast/{event_id}", response_model=BroadcastOut)
async def get_broadcast(event_id: int, db: Db, scope: Scope, registry: LiveReg) -> BroadcastOut:
    return await service.get_broadcast(db, event_id, scope, registry)
