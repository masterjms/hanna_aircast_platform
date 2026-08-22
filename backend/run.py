#!/usr/bin/env python
"""개발 서버 진입점.

    cd backend && .venv/Scripts/python run.py

`python -m uvicorn app.main:app` 대신 이걸 쓴다.

이유: uvicorn 은 Windows 에서 이벤트 루프 팩토리로 ProactorEventLoop 를 강제한다
(uvicorn/loops/asyncio.py 의 asyncio_loop_factory). 그런데 aiomqtt 내부의 paho 는
소켓을 loop.add_reader/add_writer 로 감시하고, ProactorEventLoop 에는 그 API 가 없어
NotImplementedError 가 나면서 MQTT 연결이 조용히 죽는다 — REST 는 멀쩡히 뜨는데
단말 통신만 안 되는, 알아채기 어려운 상태가 된다.

asyncio.set_event_loop_policy() 로는 못 고친다. uvicorn 이 정책이 아니라 팩토리로
루프를 만들기 때문이다. 그래서 우리가 루프를 직접 만들어 server.serve() 를 태운다.

운영(Linux 컨테이너)에서는 이 문제가 없어서 Dockerfile 은 uvicorn 을 그대로 부른다.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from app.config import settings


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=settings.app_port,
        log_level=settings.log_level.lower(),
        # reload 를 쓰면 uvicorn 이 자식 프로세스를 띄우며 셀렉터 루프를 고르지만,
        # 아래에서 우리가 루프를 직접 정하므로 여기서는 끄고 간다.
        reload=False,
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(server.serve())
    else:
        server.run()


if __name__ == "__main__":
    main()
