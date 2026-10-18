"""2단계 인증 비상 초기화 — 서버에서 (문제점 70번 2항, 2026-10-18).

최고 관리자가 한 명뿐인데 휴대폰과 복구 코드를 모두 잃었을 때만 쓴다. 보통은 다른 최고 관리자가
계정 관리 화면의 「2단계 인증 초기화」를 누른다.

    docker compose exec backend python -m app.otp_reset <아이디>

그 계정은 다음 로그인 때 등록 화면부터 다시 거친다. 감사 기록에 남지 않는 길이라 운영자 작업
기록(점검표)에 직접 적는다.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import engine, session_scope
from app.models.org import User


async def reset(username: str) -> bool:
    async with session_scope() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            return False
        user.totp_secret = None
        user.totp_enabled_at = None
        user.totp_last_step = None
        user.totp_recovery = []
        user.session_key = None
    await engine.dispose()
    return True


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("사용법: python -m app.otp_reset <아이디>")
        sys.exit(2)
    if asyncio.run(reset(args[0])):
        print(f"{args[0]} 의 2단계 인증을 초기화했습니다. 다음 로그인 때 다시 등록합니다.")
    else:
        print(f"{args[0]} 계정이 없습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
