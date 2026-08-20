"""TTS 합성 서비스.

    [브라우저] --POST /api/files/tts {text, voice}--> [백엔드]
                                                        │ 캐시 확인
                                                        │ 없으면 엔진 호출 → mp3
                                                        │ 디스크 저장 + files 행 생성
                                          ◄─────────────┘
    [브라우저] <audio src=/api/files/<id>/audio>   미리듣기
    [관리자]   방송 제어에서 이 파일을 골라 송출  → 기존 FILE_START 경로

합성과 방송을 나눈 이유:
  · 오타를 미리듣기로 잡을 수 있다. 안 나누면 실수한 문구가 곧바로 마을
    스피커 300대로 나간다.
  · 만든 문구를 재방송하거나 스케줄에 걸 수 있다.
  · 겹침 검사·권한·이력이 이미 방송 경로에 있다. TTS 가 따로 쏘면 전부 복제해야 한다.

캐시:
  키는 sha256(text|language|voice) 다. 같은 문구를 다시 만들면 Polly 를 부르지 않고
  기존 파일을 그대로 돌려준다 — 미리듣기와 저장이 같은 키를 타므로 호출은 1회다.
  (사양 문서는 sha1 이라고 적었지만, 파일 해시로 이미 sha256 을 쓰고 있어
   해시를 두 종류 두지 않았다. 캐시 키라 보안 의미는 없다.)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import FileSource
from app.errors import ApiError
from app.models.file import File
from app.models.org import User
from app.modules.file import service as file_service
from app.schemas.file import FileOut
from app.tts import voices as voice_catalog
from app.tts.engine import get_engine, normalize_mp3

log = logging.getLogger(__name__)

#: 한 번에 합성할 수 있는 글자 수. Polly 표준 요청 상한(3000자)보다 낮게 잡는다 —
#: 마을 안내방송은 길어야 몇 문장이고, 긴 문구는 요금과 실수 위험이 같이 커진다.
MAX_TEXT_LENGTH = 1000


def cache_key(text: str, language: str, voice_id: str) -> str:
    """같은 문구·언어·보이스면 같은 키. 앞뒤 공백은 무시한다."""
    raw = f"{text.strip()}|{language}|{voice_id}".encode()
    return hashlib.sha256(raw).hexdigest()


def _storage_path(key: str) -> Path:
    """FILE_ROOT 기준 상대 경로. 캐시 키가 곧 파일명이다."""
    return Path("tts") / f"{key}.mp3"


async def _find_cached(db: AsyncSession, key: str) -> File | None:
    """같은 캐시 키로 만든 파일이 이미 있는지.

    DB 와 디스크가 어긋났으면(파일만 지워진 경우) 없는 것으로 본다 —
    있다고 답했다가 방송할 때 404 가 나는 쪽이 훨씬 나쁘다.
    """
    rel = _storage_path(key).as_posix()
    file = await db.scalar(select(File).where(File.storage_path == rel))
    if file is None:
        return None
    if not file_service.absolute_path(file).exists():
        log.warning("TTS 캐시 항목의 원본이 없어 다시 합성한다: %s", rel)
        return None
    return file


async def synthesize(
    db: AsyncSession,
    *,
    text: str,
    language: str,
    voice_id: str | None,
    uploader: User,
    filename: str | None = None,
) -> tuple[FileOut, bool]:
    """문구 → 파일함의 파일. (파일, 캐시적중여부) 를 돌려준다."""
    text = text.strip()
    if not text:
        raise ApiError("합성할 문구를 입력해 주세요.", code="TTS_EMPTY_TEXT")
    if len(text) > MAX_TEXT_LENGTH:
        raise ApiError(
            f"문구는 {MAX_TEXT_LENGTH}자를 넘을 수 없습니다. (현재 {len(text)}자)",
            code="TTS_TEXT_TOO_LONG",
        )
    if language not in voice_catalog.LANGUAGES:
        raise ApiError("지원하지 않는 언어입니다.", code="TTS_UNSUPPORTED_LANGUAGE")

    voice = (
        voice_catalog.get_voice(voice_id)
        if voice_id
        else voice_catalog.default_voice(language)
    )
    if voice is None or voice.language != language:
        raise ApiError("선택한 언어에 없는 보이스입니다.", code="TTS_INVALID_VOICE")

    key = cache_key(text, language, voice.id)

    cached = await _find_cached(db, key)
    if cached is not None:
        log.info("TTS 캐시 적중 %s (%s)", key[:12], voice.id)
        out = FileOut.model_validate(cached)
        out.uploaded_by_name = uploader.username
        return out, True

    # 합성 · 정규화 · 디스크 쓰기는 전부 블로킹이라 스레드로 뺀다.
    engine = get_engine()
    raw = await asyncio.to_thread(engine.synthesize, text, voice)
    if not raw:
        raise ApiError("합성 결과가 비어 있습니다.", code="TTS_EMPTY_RESULT")
    audio = await asyncio.to_thread(normalize_mp3, raw)

    rel = _storage_path(key)
    dest = settings.file_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(dest.write_bytes, audio)

    duration = await asyncio.to_thread(file_service.probe_duration, dest)

    # 파일명이 없으면 문구 앞부분으로 만든다 — 목록에서 무슨 방송인지 보여야 한다.
    # 말줄임표는 붙이지 않는다. safe_filename 의 허용 문자에 없어서 '_' 로 치환되고,
    # 잘렸다는 사실은 어차피 길이로 드러난다.
    display = filename or text[:20].rstrip(" .,·")

    file = File(
        filename=file_service.safe_filename(f"{display}.mp3"),
        size_bytes=len(audio),
        sha256=hashlib.sha256(audio).hexdigest(),
        source=FileSource.TTS.value,
        storage_path=rel.as_posix(),
        duration_sec=duration,
        tts_text=text,
        tts_lang=language,
        tts_voice=voice.id,
        uploaded_by=uploader.id,
    )
    db.add(file)
    await db.flush()

    log.info(
        "TTS 합성 #%d %s (%s/%s, %d bytes, engine=%s)",
        file.id, file.filename, language, voice.id, len(audio), engine.name,
    )
    out = FileOut.model_validate(file)
    out.uploaded_by_name = uploader.username
    return out, False
