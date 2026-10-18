"""관리자 2단계 인증 — TOTP (RFC 6238, 문제점 70번 2항, 2026-10-18).

Google Authenticator·Microsoft Authenticator 같은 표준 OTP 앱과 같은 계산(HMAC-SHA1, 30초, 6자리).
외부 서비스·라이브러리 없이 서버 시계만 맞으면 된다(폐쇄망 가능). 앞뒤 한 칸(±30초)까지 받아 시계
차이를 견디고, 한 번 쓴 숫자(같은 30초 칸)는 다시 받지 않는다(가로채 재사용 방지).

복구 코드: 휴대폰을 잃었을 때 쓰는 일회용 코드 10개(XXXX-XXXX). 서버에는 SHA-256 만 남는다 —
코드 자체가 무작위 40비트라 bcrypt 까지는 필요 없다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SEC = 30
DIGITS = 6
#: 앞뒤로 받아 주는 칸 수(±30초)
WINDOW = 1
RECOVERY_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0·O·1·I 빼고


def new_secret() -> str:
    """base32 160비트(앱 표준)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP_SEC)


def verify(
    secret: str, code: str, *, last_step: int | None = None, now: float | None = None
) -> int | None:
    """맞으면 그 칸 번호, 아니면 None. last_step 이하 칸은 이미 쓴 것으로 거절한다."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    for step in range(base - WINDOW, base + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, *, username: str, issuer: str) -> str:
    """OTP 앱이 QR 로 읽는 주소(Key Uri Format)."""
    label = quote(f"{issuer}:{username}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SEC}"
    )


def new_recovery_codes() -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
        return f"{raw[:4]}-{raw[4:]}"

    return [one() for _ in range(RECOVERY_COUNT)]


def normalize_recovery(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else raw


def hash_recovery(code: str) -> str:
    return hashlib.sha256(normalize_recovery(code).encode("ascii")).hexdigest()
