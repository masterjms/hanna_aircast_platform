"""앱 조립.

여기서 하는 일은 세 가지뿐이다 — 수명주기 관리, 라우터 등록, 미들웨어 등록.
비즈니스 로직은 한 줄도 두지 않는다.

한 프로세스 안에서 세 가지가 같이 돈다(모듈러 모놀리스):
  · REST API        요청/응답
  · MQTT Worker     브로커 구독 → DB 적재
  · 스케줄러        CONFIG 재조정 (Phase 6 에서 자동방송이 추가된다)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.config import settings
from app.db import SessionFactory, engine, session_scope
from app.errors import register_exception_handlers
from app.live.registry import LiveRegistry
from app.modules.auth.router import router as auth_router
from app.modules.broadcast import service as broadcast_service
from app.modules.broadcast.router import router as broadcast_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.device import service as device_service
from app.modules.device.router import router as device_router
from app.modules.file.router import router as file_router
from app.modules.geo.router import router as geo_router
from app.modules.org.router import router as org_router
from app.modules.system.router import router as system_router
from app.mqtt.connection import MqttConnection
from app.mqtt.handlers import dispatch
from app.mqtt.publisher import MqttPublisher
from app.mqtt.status_buffer import StatusBuffer
from app.tasks import config_reconcile

# ⚠ Windows 에서 이 모듈을 `python -m uvicorn app.main:app` 로 띄우면 MQTT 가 죽는다.
#   uvicorn 이 ProactorEventLoop 를 강제하는데 paho 가 쓰는 add_reader 가 거기 없다.
#   개발 서버는 backend/run.py 로 띄운다(거기서 셀렉터 루프를 직접 만든다).
#   운영 컨테이너는 Linux 라 해당 없음.

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ── 기동 ────────────────────────────────────────────────────────────
    settings.file_root.mkdir(parents=True, exist_ok=True)
    for sub in (settings.upload_dir, settings.tts_dir, settings.update_dir):
        sub.mkdir(parents=True, exist_ok=True)

    # 주기 STATUS 를 모아 쓰는 버퍼. 0 이면 끈다(메시지마다 즉시 쓰기).
    status_buffer = (
        StatusBuffer(interval_sec=settings.status_flush_interval_sec)
        if settings.status_flush_interval_sec > 0
        else None
    )

    # 람다가 publisher 를 늦게 읽는다 — publisher 는 connection 을 필요로 해서
    # 아래에서야 만들어지는데, 클로저라 호출 시점에 해결된다.
    connection = MqttConnection(
        on_message=lambda t, raw: dispatch(t, raw, publisher, status_buffer)
    )
    publisher = MqttPublisher(connection)
    # 라우터는 get_publisher 의존성으로 여기 붙은 인스턴스를 꺼내 쓴다.
    app.state.mqtt = connection
    app.state.publisher = publisher
    # 실시간 방송 세션. 라우터는 LiveReg 의존성으로 꺼내 쓴다.
    app.state.live_registry = LiveRegistry()
    # 방송 종료 확정(end_event)이 스트림도 닫을 수 있게 레지스트리를 넘긴다.
    broadcast_service.set_live_registry(app.state.live_registry)
    app.state.status_buffer = status_buffer
    await connection.start()
    if status_buffer is not None:
        await status_buffer.start(publisher)

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        config_reconcile.run,
        "interval",
        seconds=settings.config_reconcile_interval_sec,
        args=[publisher],
        id="config-reconcile",
        # 기동 직후에도 1회 돈다. interval 트리거는 기본적으로 첫 실행을
        # now + interval 로 잡는데, 그러면 브로커 retain 이 유실된 채 서버가
        # 뜬 경우 최대 1주기(기본 1시간) 동안 전 단말이 미배정으로 남는다.
        # 태스크가 MQTT 연결을 자체적으로 기다리므로 바로 걸어도 안전하다.
        next_run_time=datetime.now(),
        # 서버가 잠깐 멈췄다 살아나도 밀린 실행이 한꺼번에 터지지 않게 한다.
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    app.state.scheduler = scheduler

    # 기동 직후 1회. 브로커 retain 이 유실됐을 수 있으므로 항상 다시 밀어 넣는다.
    await config_reconcile.run(publisher)

    # 단말별 MQTT 계정도 기동 때마다 다시 내보낸다 — DB 복구·볼륨 재생성 뒤에도
    # 브로커 passwd 가 DB(정본)와 같아진다. 실패해도 기동은 계속한다.
    try:
        async with SessionFactory() as session:
            await device_service.export_broker_accounts(session)
    except Exception:  # noqa: BLE001
        log.exception("기동 시 MQTT 계정 내보내기 실패 (다음 계정 발행 때 재시도)")

    # 지난 프로세스가 죽거나 재배포로 교체되면서 남은 '진행 중' 방송을 정리한다.
    # LiveRegistry 는 메모리뿐이라 재시작하면 라이브 세션·워치독이 통째로
    # 사라지고, 파일 방송은 애초에 자동 종료가 없다 — 안 지우면 겹침 검사가
    # 그 대상을 영원히 "방송 중"으로 보고 새 방송을 막는다.
    try:
        async with session_scope() as session:
            closed = await broadcast_service.close_orphaned_events(session)
        if closed:
            log.warning("기동 시 고아 방송 %d건 정리", closed)
    except Exception:  # noqa: BLE001
        log.exception("기동 시 고아 방송 정리 실패")

    log.info("기동 완료 (env=%s)", settings.app_env)

    yield

    # ── 종료 ────────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    # 소스를 안 닫으면 Icecast 에 유령 마운트가 남는다.
    await app.state.live_registry.shutdown()
    # 대기 중인 STATUS 를 마지막으로 쓴다(연결을 끊기 전에 — 재발행이 남아 있을 수 있다).
    if status_buffer is not None:
        await status_buffer.stop(publisher)
    await connection.stop()
    await engine.dispose()
    log.info("종료 완료")


app = FastAPI(
    title="xWIFI 운영서버",
    version="0.1.0",
    description="ESP32 마을방송 단말을 MQTT + Icecast + HTTP 로 제어하는 운영서버 API",
    lifespan=lifespan,
)

@app.middleware("http")
async def db_session_middleware(request: Request, call_next) -> Response:
    """요청 하나 = 트랜잭션 하나.

    커밋을 여기서 하는 이유는 순서 때문이다. FastAPI 의 `yield` 의존성은
    정리 코드가 응답을 보낸 뒤에 돌아서, 쓰기 API 가 200 을 돌려준 직후
    바로 읽으면 커밋 전 상태가 보일 수 있다. call_next 다음은 응답 전송
    전이라 여기서 커밋하면 그 역전이 생기지 않는다.

    4xx·5xx 로 끝난 요청은 롤백한다 — 실패한 요청이 절반만 남으면 안 된다.
    """
    async with SessionFactory() as session:
        request.state.db = session
        try:
            response = await call_next(request)
        except Exception:
            await session.rollback()
            raise

        if response.status_code >= 400:
            await session.rollback()
        else:
            await session.commit()
        return response


register_exception_handlers(app)

if settings.cors_origins:
    # 개발 중 Vite dev 서버(:5173) 전용. prod 는 nginx 가 같은 오리진으로 서빙하므로 빈 값이다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(system_router)
app.include_router(auth_router)
app.include_router(org_router)
app.include_router(device_router)
app.include_router(dashboard_router)
app.include_router(file_router)
app.include_router(broadcast_router)
app.include_router(geo_router)
