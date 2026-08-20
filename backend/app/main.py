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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.config import settings
from app.db import SessionFactory, engine
from app.errors import register_exception_handlers
from app.live.registry import LiveRegistry
from app.modules.auth.router import router as auth_router
from app.modules.broadcast.router import router as broadcast_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.device.router import router as device_router
from app.modules.file.router import router as file_router
from app.modules.org.router import router as org_router
from app.modules.system.router import router as system_router
from app.mqtt.connection import MqttConnection
from app.mqtt.handlers import dispatch
from app.mqtt.publisher import MqttPublisher
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

    connection = MqttConnection(on_message=dispatch)
    publisher = MqttPublisher(connection)
    # 라우터는 get_publisher 의존성으로 여기 붙은 인스턴스를 꺼내 쓴다.
    app.state.mqtt = connection
    app.state.publisher = publisher
    # 실시간 방송 세션. 라우터는 LiveReg 의존성으로 꺼내 쓴다.
    app.state.live_registry = LiveRegistry()
    await connection.start()

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        config_reconcile.run,
        "interval",
        seconds=settings.config_reconcile_interval_sec,
        args=[publisher],
        id="config-reconcile",
        # 서버가 잠깐 멈췄다 살아나도 밀린 실행이 한꺼번에 터지지 않게 한다.
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    app.state.scheduler = scheduler

    # 기동 직후 1회. 브로커 retain 이 유실됐을 수 있으므로 항상 다시 밀어 넣는다.
    await config_reconcile.run(publisher)
    log.info("기동 완료 (env=%s)", settings.app_env)

    yield

    # ── 종료 ────────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    # 소스를 안 닫으면 Icecast 에 유령 마운트가 남는다.
    await app.state.live_registry.shutdown()
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
