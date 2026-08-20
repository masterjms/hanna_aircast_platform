"""파일 라이브러리 서비스.

files / download_tokens 테이블을 소유한다.

저장 규칙:
  · 바이너리는 DB 에 넣지 않는다. FILE_ROOT 아래 로컬 디스크에 둔다.
  · files.storage_path 는 FILE_ROOT 기준 **상대 경로**다. 절대 경로를 넣으면
    온프레미스로 옮길 때 전부 깨진다.
  · 단말에게는 짧은 토큰만 내려보낸다(MQTT payload 1024B 제약).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import shutil
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import FileSource
from app.core.ids import new_download_token
from app.errors import ApiError, NotFound
from app.models.file import DownloadToken, File
from app.models.org import User
from app.schemas.file import FileOut

log = logging.getLogger(__name__)

#: 업로드 허용 확장자. 단말 디코더가 mp3 만 확실히 받는다.
ALLOWED_SUFFIXES = {".mp3"}
#: 업로드 상한. 마을방송 안내음성은 길어야 수 분이라 넉넉하다.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
#: 한 번에 읽어 해시할 크기.
_CHUNK = 1024 * 1024

_SAFE_NAME = re.compile(r"[^\w가-힣.\- ]")


class FileNotFound(NotFound):
    code = "FILE_NOT_FOUND"
    message = "존재하지 않는 파일입니다."


def safe_filename(raw: str) -> str:
    """표시용 파일명 정리.

    경로 구분자를 떼고(디렉터리 탈출 방지) 위험한 문자를 지운다.
    FILE_START payload 에도 실리므로 길이를 제한한다 — 1024B 예산을 파일명이 먹으면 안 된다.
    """
    name = Path(raw).name.strip() or "audio.mp3"
    name = _SAFE_NAME.sub("_", name)
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    return f"{stem[:60]}{suffix or '.mp3'}"


def probe_duration(path: Path) -> Decimal | None:
    """ffprobe 로 길이(초)를 구한다.

    ffprobe 가 없으면 None 을 돌려준다 — 길이는 화면 표시용이라 없어도 방송은 된다.
    운영 컨테이너에는 ffmpeg 이 들어 있다(backend/Dockerfile).
    """
    exe = shutil.which("ffprobe")
    if exe is None:
        log.info("ffprobe 없음 — 재생 시간 계산을 건너뛴다")
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return Decimal(out.stdout.strip()).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 - 길이를 못 구해도 업로드는 성공시킨다
        log.warning("ffprobe 실패: %s", path.name)
        return None


def _write_and_hash(upload: UploadFile, dest: Path) -> tuple[int, str]:
    """업로드를 디스크에 흘려 쓰면서 동시에 sha256 을 계산한다.

    파일 전체를 메모리에 올리지 않는다. 반환값은 (바이트 수, sha256 hex).
    """
    digest = hashlib.sha256()
    size = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while chunk := upload.file.read(_CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise ApiError(
                    f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // 1024 // 1024}MB).",
                    code="FILE_TOO_LARGE",
                )
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest()


# ── 조회 ─────────────────────────────────────────────────────────────────
async def list_files(db: AsyncSession) -> list[FileOut]:
    """파일함 목록. 마을 범위를 타지 않는다 — 파일은 전체 공용이다."""
    rows = (
        await db.execute(
            select(File, User.username)
            .outerjoin(User, File.uploaded_by == User.id)
            .order_by(File.created_at.desc())
        )
    ).all()

    result = []
    for file, uploader in rows:
        out = FileOut.model_validate(file)
        out.uploaded_by_name = uploader
        result.append(out)
    return result


async def get_file(db: AsyncSession, file_id: int) -> File:
    file = await db.get(File, file_id)
    if file is None:
        raise FileNotFound()
    return file


def absolute_path(file: File) -> Path:
    """상대 경로 → 실제 경로. 디스크를 만지는 곳은 전부 이걸 거친다."""
    return settings.file_root / file.storage_path


# ── 업로드 · 삭제 ────────────────────────────────────────────────────────
async def upload_file(db: AsyncSession, upload: UploadFile, *, uploader: User) -> FileOut:
    original = upload.filename or "audio.mp3"
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ApiError(
            f"지원하지 않는 형식입니다. {', '.join(sorted(ALLOWED_SUFFIXES))} 만 올릴 수 있습니다.",
            code="UNSUPPORTED_FILE_TYPE",
            detail={"suffix": suffix},
        )

    today = dt.date.today()
    rel = Path("upload") / f"{today:%Y}" / f"{today:%m}" / f"{uuid.uuid4().hex}{suffix}"
    dest = settings.file_root / rel

    # 디스크 I/O 는 블로킹이라 스레드로 뺀다. 큰 파일에서 이벤트 루프가 멈추면 안 된다.
    size, sha256 = await asyncio.to_thread(_write_and_hash, upload, dest)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise ApiError("빈 파일은 올릴 수 없습니다.", code="EMPTY_FILE")

    duration = await asyncio.to_thread(probe_duration, dest)

    file = File(
        filename=safe_filename(original),
        size_bytes=size,
        sha256=sha256,
        source=FileSource.UPLOAD.value,
        storage_path=rel.as_posix(),
        duration_sec=duration,
        uploaded_by=uploader.id,
    )
    db.add(file)
    await db.flush()

    out = FileOut.model_validate(file)
    # 목록 응답과 모양을 맞춘다. 업로드 직후 화면이 다시 조회하지 않아도 되게.
    out.uploaded_by_name = uploader.username
    log.info("파일 업로드 #%d %s (%d bytes)", file.id, file.filename, size)
    return out


async def delete_file(db: AsyncSession, file_id: int) -> None:
    """파일 삭제.

    DB 행을 먼저 지우고 디스크를 지운다. 순서를 뒤집으면 디스크는 비었는데
    목록에는 남는 상태가 생긴다(그쪽이 더 나쁘다 — 방송을 걸면 404 가 난다).

    스케줄·이력이 참조 중이면 DB 가 FK 로 막는다.
    """
    file = await get_file(db, file_id)
    path = absolute_path(file)

    await db.delete(file)
    try:
        await db.flush()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            "스케줄이나 이력에서 사용 중인 파일은 삭제할 수 없습니다.",
            code="FILE_IN_USE",
        ) from exc

    # 여기서 실패해도 목록에서는 이미 사라졌다. 고아 파일은 로그만 남기고 넘어간다.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.exception("디스크 삭제 실패 (고아 파일로 남음): %s", path)


# ── 다운로드 토큰 ────────────────────────────────────────────────────────
async def issue_token(db: AsyncSession, *, file_id: int, job_id: int, ttl_sec: int) -> str:
    """단말이 쓸 단기 토큰을 발급한다.

    URL 이 MQTT payload(1024B) 안에 들어가야 해서 서명 URL 대신 짧은 토큰을 쓴다.
    """
    token = new_download_token()
    db.add(
        DownloadToken(
            token=token,
            file_id=file_id,
            job_id=job_id,
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_sec),
        )
    )
    await db.flush()
    return token


async def resolve_token(db: AsyncSession, token: str) -> File:
    """토큰 → 파일. 없거나 만료됐으면 404 로 접는다.

    "만료됨"과 "없음"을 구분해서 알려주지 않는다 — 토큰을 긁는 쪽에 힌트를 주지 않는다.
    """
    row = await db.get(DownloadToken, token)
    if row is None or row.expires_at <= dt.datetime.now(dt.timezone.utc):
        raise FileNotFound()
    return await get_file(db, row.file_id)


async def purge_expired_tokens(db: AsyncSession) -> int:
    """만료 토큰 청소. 스케줄러가 주기적으로 부른다."""
    result = await db.execute(
        delete(DownloadToken).where(DownloadToken.expires_at <= dt.datetime.now(dt.timezone.utc))
    )
    return result.rowcount or 0
