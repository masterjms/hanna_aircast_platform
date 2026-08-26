"""WSS /ingest — 브라우저 마이크 업링크.

    브라우저 마이크
      → opus-recorder (Ogg/Opus, 16kHz mono, 40ms, maxFramesPerPage=1)
      → WSS /ingest?session=<id>          ← 여기
      → IcecastSource (HTTP PUT)
      → Icecast 마운트
      → 단말 HTTP GET

WebSocket 을 쓰는 이유: 브라우저는 HTTP 요청 바디를 스트리밍할 수 없다.
fetch 의 ReadableStream 업로드는 아직 어디서나 되지 않는다.

인증: 브라우저 WebSocket 은 커스텀 헤더를 못 붙이므로 연결 직후 첫 메시지로
      {"type":"auth","token":"<JWT>"} 를 받는다.

거절 코드:
    4001  인증 실패
    4004  세션 없음 (이미 끝났거나 잘못된 id)
    4009  이미 다른 업링크가 붙어 있음
"""

from __future__ import annotations

import contextlib
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.core.security import decode_access_token
from app.errors import ApiError
from app.live.registry import LiveRegistry

log = logging.getLogger(__name__)

#: 첫 메시지(인증)를 기다리는 시간.
AUTH_TIMEOUT_SEC = 10.0
#: 한 조각의 상한. Ogg 페이지 하나는 보통 수백 바이트라 넉넉하다.
MAX_CHUNK_BYTES = 64 * 1024

CLOSE_AUTH_FAILED = 4001
CLOSE_NO_SESSION = 4004
CLOSE_ALREADY_CONNECTED = 4009


async def _close_quietly(ws: WebSocket, code: int, reason: str) -> None:
    """연결을 닫는다.

    상대가 먼저 끊었으면 close() 자체가 WebSocketDisconnect 를 던진다.
    브라우저 새로고침·탭 닫기에서 늘 일어나는 일이라 트레이스백을 남기지 않는다.
    """
    with contextlib.suppress(Exception):
        await ws.close(code=code, reason=reason)


async def _authenticate(ws: WebSocket) -> bool:
    """첫 메시지로 JWT 를 검증한다."""
    try:
        raw = await ws.receive_text()
        message = json.loads(raw)
    except (WebSocketDisconnect, json.JSONDecodeError, KeyError):
        return False

    if message.get("type") != "auth" or not message.get("token"):
        return False
    try:
        decode_access_token(message["token"])
    except ApiError:
        return False
    return True


async def ingest_endpoint(ws: WebSocket, session_id: int, registry: LiveRegistry) -> None:
    """업링크 한 건. 연결이 끊기면 방송은 계속되지만 무음이 나간다.

    끊김을 방송 종료로 해석하지 않는다 — 네트워크가 잠깐 튀었을 때 방송이
    통째로 죽으면 안 되고, 종료는 사용자가 명시적으로 누르는 것이다.
    """
    await ws.accept()

    if not await _authenticate(ws):
        await _close_quietly(ws, CLOSE_AUTH_FAILED, "auth failed")
        log.info("/ingest 인증 없이 끊김 session=%s", session_id)
        return

    session = registry.get(session_id)
    if session is None:
        await _close_quietly(ws, CLOSE_NO_SESSION, "unknown session")
        log.warning("/ingest 알 수 없는 세션 %s", session_id)
        return

    if session.uplink_connected:
        # 같은 방송에 두 개의 마이크가 붙으면 오디오가 섞여 못 듣게 된다.
        await _close_quietly(ws, CLOSE_ALREADY_CONNECTED, "uplink already connected")
        log.warning("/ingest 중복 업링크 session=%s", session_id)
        return

    session.uplink_connected = True
    session.uplink_seen = True
    await ws.send_text(json.dumps({"type": "ready", "mount": session.mount}))
    log.info("/ingest 연결 session=%d mount=%s", session_id, session.mount)

    received = 0
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break

            chunk = message.get("bytes")
            if chunk is None:
                # 텍스트는 하트비트 용도로만 받는다.
                continue
            if len(chunk) > MAX_CHUNK_BYTES:
                log.warning("/ingest 과대 청크 %dB 무시", len(chunk))
                continue

            # 파싱하지 않는다. 받은 Ogg 페이지를 그대로 흘려보낸다.
            session.source.feed(chunk)
            received += len(chunk)
            session.touch_audio()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("/ingest 오류 session=%d", session_id)
    finally:
        session.uplink_connected = False
        log.info("/ingest 종료 session=%d (%d bytes 수신)", session_id, received)
