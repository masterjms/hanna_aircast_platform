"""비밀번호 해시 · JWT.

bcrypt 를 직접 쓴다(passlib 는 bcrypt 4.x 와 버전 호환 문제가 있다).
토큰은 상태를 두지 않는다 — 로그아웃은 클라이언트가 토큰을 버리는 것으로 끝난다.
강제 무효화가 필요해지면 그때 블랙리스트 테이블을 추가한다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import bcrypt
import jwt

from app.config import settings
from app.errors import Unauthorized

_BCRYPT_ROUNDS = 12
#: bcrypt 는 72바이트를 넘는 입력을 조용히 잘라낸다. 미리 막아서 혼선을 없앤다.
MAX_PASSWORD_BYTES = 72


def hash_password(plain: str) -> str:
    raw = plain.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"비밀번호는 {MAX_PASSWORD_BYTES}바이트를 넘을 수 없습니다.")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    raw = plain.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        # 해시 형식이 깨진 경우. 인증 실패로 취급한다.
        return False


def create_access_token(*, user_id: int, username: str, role: str) -> tuple[str, int]:
    """(토큰, 만료까지 남은 초) 를 돌려준다."""
    expires_in = settings.jwt_expire_minutes * 60
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    """검증 실패는 전부 401 로 접는다 — 왜 실패했는지 밖에 알려주지 않는다."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("세션이 만료되었습니다. 다시 로그인해 주세요.") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized() from exc
