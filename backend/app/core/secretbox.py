"""저장 데이터 암호화 — 단말 MQTT 비밀번호(문제점 70번 10/15 ⑤, 2026-10-17).

AES-256-GCM. 저장 형식은 `enc1:<base64(nonce 12바이트 + 암호문 + 태그)>`.
`enc1:` 로 시작하지 않는 값은 옛 평문으로 보고 그대로 돌려준다 — 마이그레이션 도중이나 손으로 넣은
값도 읽히게(다음 저장 때 암호화된다).

키: DATA_ENCRYPTION_KEY(base64 32바이트). 비어 있으면 JWT_SECRET 에서 HKDF 로 파생한다.
파생 키를 쓰는 동안 JWT_SECRET 을 바꾸면 저장된 값을 못 푼다 — 바꿀 일이 있으면 먼저
DATA_ENCRYPTION_KEY 를 따로 정할 것.

DB 가 통째로 새어 나가도(백업 파일 유출 등) 단말 비밀번호가 평문으로 보이지 않게 하는 것이 목적이다.
서버 .env 까지 함께 새면 막지 못한다 — 키는 DB 밖에 있어야 의미가 있다.
"""

from __future__ import annotations

import base64
import functools
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

PREFIX = "enc1:"


def _derived_key() -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"xwifi-data-key", info=b"device-secret-v1"
    ).derive(settings.jwt_secret.encode("utf-8"))


@functools.cache
def _keys() -> tuple[bytes, ...]:
    """(암호화 키, 복호화 때 더 시도할 키…).

    DATA_ENCRYPTION_KEY 를 나중에 정해도 그 전에 JWT_SECRET 파생 키로 저장된 값을 계속 읽게
    파생 키를 두 번째로 둔다(다음에 저장될 때 새 키로 바뀐다). JWT_SECRET 까지 바꾸면 옛 값은
    못 읽는다.
    """
    raw = settings.data_encryption_key.strip()
    if not raw:
        return (_derived_key(),)
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ValueError("DATA_ENCRYPTION_KEY 는 base64 로 32바이트여야 합니다.")
    return (key, _derived_key())


def encrypt(plain: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_keys()[0]).encrypt(nonce, plain.encode("utf-8"), None)
    return PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(stored: str) -> str:
    if not stored.startswith(PREFIX):
        return stored  # 옛 평문
    blob = base64.b64decode(stored[len(PREFIX) :])
    keys = _keys()
    for i, key in enumerate(keys):
        try:
            return AESGCM(key).decrypt(blob[:12], blob[12:], None).decode("utf-8")
        except InvalidTag:
            if i == len(keys) - 1:
                raise
    raise AssertionError("unreachable")


class EncryptedText(TypeDecorator[str]):
    """쓰면 암호화, 읽으면 복호화 — 코드에서는 평문 str 로 다룬다. IS NULL 비교는 그대로 된다."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        if value is None or value.startswith(PREFIX):
            return value
        return encrypt(value)

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        return None if value is None else decrypt(value)
