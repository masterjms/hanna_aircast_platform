"""실시간 방송 세션 레지스트리.

진행 중인 LIVE 세션을 프로세스 메모리에 들고 있다. Icecast source 연결과
WebSocket 은 프로세스에 묶인 자원이라 DB 에 넣을 수 없다 — 대신 이력
(broadcast_events)은 DB 에 남기고, 여기는 "지금 살아 있는 연결"만 다룬다.

⚠ 이 구조는 백엔드 프로세스가 하나라는 전제 위에 있다. 나중에 여러 대로
  늘린다면 세션 소유 서버를 찾아 라우팅하는 계층이 필요하다.
  (고객당 서버 1대 배포 모델이라 당분간은 문제가 되지 않는다.)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field

from app.live.icecast import IcecastSource

log = logging.getLogger(__name__)


@dataclass
class LiveSession:
    """진행 중인 실시간 방송 하나."""

    #: broadcast_events.id
    event_id: int
    #: job_id (= 통신 사양의 session_id)
    session_id: int
    mount: str
    stream_url: str
    #: 발행 시점 대상 MAC. 겹침 검사는 DB 쪽에서 다시 푼다.
    macs: list[str]
    source: IcecastSource
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    #: /ingest 웹소켓이 붙었는지. 붙기 전에는 무음이 나간다.
    uplink_connected: bool = False
    #: 한 번이라도 붙은 적이 있는지. 화면 표시와 로그 판별에 쓴다.
    uplink_seen: bool = False
    #: 마지막으로 오디오 바이트가 들어온 시각. 워치독의 기준이다.
    #: 시작 시각으로 초기화한다 — 아직 한 번도 안 붙은 방송도 같은 잣대로 잰다.
    last_audio_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def touch_audio(self) -> None:
        self.last_audio_at = dt.datetime.now(dt.timezone.utc)

    @property
    def silent_for_sec(self) -> float:
        return (dt.datetime.now(dt.timezone.utc) - self.last_audio_at).total_seconds()

    @property
    def bytes_sent(self) -> int:
        return self.source.bytes_sent


class LiveRegistry:
    """세션 id → LiveSession."""

    def __init__(self) -> None:
        self._sessions: dict[int, LiveSession] = {}
        self._lock = asyncio.Lock()

    def get(self, session_id: int) -> LiveSession | None:
        return self._sessions.get(session_id)

    def by_event(self, event_id: int) -> LiveSession | None:
        return next((s for s in self._sessions.values() if s.event_id == event_id), None)

    def all(self) -> list[LiveSession]:
        return list(self._sessions.values())

    async def add(self, session: LiveSession) -> None:
        async with self._lock:
            self._sessions[session.session_id] = session
        log.info("LIVE 세션 등록 #%d %s", session.session_id, session.mount)

    async def remove(self, session_id: int) -> LiveSession | None:
        """세션을 빼고 Icecast 연결을 닫는다."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return None
        await session.source.stop()
        log.info(
            "LIVE 세션 종료 #%d %s (%d bytes)",
            session.session_id, session.mount, session.bytes_sent,
        )
        return session

    async def shutdown(self) -> None:
        """서버 종료 시 모든 소스를 닫는다. 안 닫으면 Icecast 에 유령 마운트가 남는다."""
        for session_id in list(self._sessions):
            await self.remove(session_id)
