"""ID 발번.

job_id 는 반드시 DB 시퀀스로 뽑는다. 프로세스 메모리 카운터를 쓰면 재기동 때 되감기고,
단말이 이미 본 id 가 다시 나와서 멱등 처리가 깨진다.
"""

from __future__ import annotations

import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 마이그레이션 0001 에서 만든다.
JOB_ID_SEQUENCE = "job_id_seq"


async def next_job_id(db: AsyncSession) -> int:
    """LIVE_START / FILE_START / OTA_START 가 공유하는 단일 id 공간.

    통신 사양의 session_id · cmd_id · job_id 를 하나로 통일한 값이다.
    """
    value = await db.scalar(text(f"SELECT nextval('{JOB_ID_SEQUENCE}')"))
    return int(value)


def new_download_token() -> str:
    """단말 다운로드용 토큰.

    URL 에 그대로 들어가고 MQTT payload(1024B) 안에 담겨야 하므로 짧게 만든다.
    32자 = 192비트 엔트로피로 추측 공격에는 충분하다.
    """
    return secrets.token_urlsafe(24)[:32]
