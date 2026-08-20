"""Icecast source 연결.

브라우저에서 받은 Ogg/Opus 바이트를 Icecast 마운트로 밀어 넣는다.

    브라우저 --(WSS /ingest)--> 서버 --(HTTP PUT)--> Icecast --(HTTP GET)--> 단말

HTTP 클라이언트 라이브러리를 쓰지 않고 asyncio 스트림으로 직접 말한다. 이유:
  · Icecast source 는 Content-Length 도 chunked 도 쓰지 않는다. 헤더를 보내면
    즉시 `HTTP/1.0 200 OK` 로 답하고, 그 뒤로는 연결이 끊길 때까지 raw 바이트를
    받는다. aiohttp/httpx 는 스트리밍 바디에 chunked 를 붙이는데 Icecast 가
    그걸 오디오 데이터로 읽어버려 스트림이 깨진다.
  · 프로토콜이 이 정도로 단순하면 직접 쓰는 편이 오히려 예측 가능하다.

핵심 원칙: **오디오 바이트를 파싱하거나 다시 자르지 않는다.**
opus-recorder 가 만든 Ogg 페이지를 받은 그대로 흘려보낸다. 중간에서
재인코딩하면 지연이 붙고 프레임 경계가 어긋나 단말 지터 버퍼가 깨진다.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging

from app.config import settings

log = logging.getLogger(__name__)

#: 소켓 연결 + 첫 응답을 기다리는 시간.
_CONNECT_TIMEOUT = 10.0
#: 큐가 이만큼 쌓이면 오래된 것부터 버린다.
#: 실시간이라 밀린 오디오를 나중에 보내는 건 의미가 없다 — 지연만 쌓인다.
_MAX_QUEUE_CHUNKS = 64


class IcecastSource:
    """마운트 하나에 대한 source 연결.

    feed() 로 넣으면 백그라운드 태스크가 Icecast 로 흘려보낸다.
    """

    def __init__(self, mount: str) -> None:
        self.mount = mount
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_MAX_QUEUE_CHUNKS)
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ready = asyncio.Event()
        self._failed: str | None = None
        self.bytes_sent = 0
        self.dropped_chunks = 0

    @property
    def url(self) -> str:
        return f"http://{settings.icecast_host}:{settings.icecast_port}{self.mount}"

    @property
    def is_connected(self) -> bool:
        return self._ready.is_set() and self._failed is None

    @property
    def error(self) -> str | None:
        return self._failed

    # ── 수명주기 ────────────────────────────────────────────────────────
    async def start(self) -> None:
        """연결을 세우고 Icecast 가 받아들일 때까지 기다린다.

        여기서 실패하면 호출자가 방송 자체를 시작하지 않는다 — 마운트가 없는데
        단말을 붙이면 404 만 받고 재시도가 돈다.
        """
        self._task = asyncio.create_task(self._run(), name=f"icecast{self.mount}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=_CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            self._failed = self._failed or "Icecast 응답 시간 초과"

    async def stop(self) -> None:
        """큐에 종료 신호를 넣고 전송이 끝나길 기다린다."""
        if self._task is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        finally:
            self._task = None

    def feed(self, chunk: bytes) -> None:
        """오디오 조각을 큐에 넣는다. 큐가 차 있으면 가장 오래된 것을 버린다."""
        if not chunk:
            return
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                self._queue.get_nowait()
                self._queue.put_nowait(chunk)
            self.dropped_chunks += 1

    # ── 내부 ────────────────────────────────────────────────────────────
    def _request_headers(self) -> bytes:
        auth = base64.b64encode(
            f"{settings.icecast_source_user}:{settings.icecast_source_password}".encode()
        ).decode()
        return (
            f"PUT {self.mount} HTTP/1.1\r\n"
            f"Host: {settings.icecast_host}:{settings.icecast_port}\r\n"
            f"Authorization: Basic {auth}\r\n"
            "Content-Type: application/ogg\r\n"
            f"Ice-Name: xWIFI {self.mount}\r\n"
            "Ice-Public: 0\r\n"
            "\r\n"
        ).encode()

    async def _run(self) -> None:
        reader = writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.icecast_host, settings.icecast_port),
                timeout=_CONNECT_TIMEOUT,
            )
            writer.write(self._request_headers())
            await writer.drain()

            # Icecast 는 헤더를 받으면 바로 상태줄을 준다. 여기서 인증 실패 같은
            # 것이 드러난다 — 오디오를 보내기 전에 알아야 한다.
            status_line = await asyncio.wait_for(reader.readline(), timeout=_CONNECT_TIMEOUT)
            status = status_line.decode("latin-1").strip()
            if " 200 " not in status:
                self._failed = f"Icecast 거절: {status or '응답 없음'}"
                log.error("Icecast source 실패 %s — %s", self.mount, self._failed)
                return

            self._writer = writer
            self._ready.set()
            log.info("Icecast source 연결 %s (%s)", self.mount, status)

            while True:
                chunk = await self._queue.get()
                if chunk is None:
                    break
                writer.write(chunk)
                await writer.drain()
                self.bytes_sent += len(chunk)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._failed = f"Icecast 연결 실패: {exc}"
            log.warning("Icecast source 오류 %s — %s", self.mount, exc)
        finally:
            # 어떤 경로로 끝나든 start() 가 영원히 기다리지 않게 깨운다.
            self._ready.set()
            self._writer = None
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            log.info(
                "Icecast source 종료 %s (%d bytes, drop %d)",
                self.mount, self.bytes_sent, self.dropped_chunks,
            )
