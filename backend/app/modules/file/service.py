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
import json
import logging
import re
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE, FileSource
from app.core.ids import new_download_token
from app.errors import ApiError, NotFound
from app.models.file import DownloadToken, File
from app.models.org import User
from app.models.schedule import Schedule
from app.modules.schedule.rules import schedule_when
from app.schemas.file import FileOut
from app.tasks.config_reconcile import load_config
from app.tts.engine import MP3_MUXER_ARGS

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


#: 단말이 파일명에서 그대로 두는 문자. 나머지는 전부 '_' 로 바꾼다
#: (통신 사양 §11.2). UTF-8 한글은 바이트마다 걸려서 글자당 '_' 세 개가 된다.
_DEVICE_SAFE = re.compile(r"[^0-9A-Za-z_-]+")


def device_file_name(filename: str, file_id: int) -> str:
    """단말에 보낼 파일명. 화면에 보이는 이름과 다르다.

    단말은 받은 이름을 그대로 저장하지 않고 `0-9 A-Z a-z - _` 외의 **바이트**를
    전부 '_' 로 바꾼다(통신 사양 §11.2). 그래서 한글 제목을 그대로 보내면:

        산불방재 안내.mp3  →  ___________________-<epoch>-W.mp3
        마을회의.mp3       →  ____________-<epoch>-W.mp3

    읽을 수 없을 뿐 아니라 **바이트 길이가 같은 다른 제목끼리 구분이 사라진다.**
    현장에서 단말 저장소를 열어보면 밑줄만 늘어선 파일들이 남는다.

    사양의 권고대로 한글 제목은 서버가 들고 있고(File.filename), 단말에는
    ASCII 만 보낸다. file_id 를 붙여 어느 파일인지 되짚을 수 있게 한다.

        notice.mp3        →  notice-73.mp3
        공지 notice.mp3   →  notice-73.mp3
        산불방재 안내.mp3 →  file-73.mp3      (남는 ASCII 가 없을 때)

    epoch 와 '-W' 는 붙이지 않는다 — 단말이 알아서 붙인다.
    """
    stem = Path(filename).stem
    safe = _DEVICE_SAFE.sub("-", stem).strip("-")
    safe = re.sub(r"-{2,}", "-", safe)
    # 한글만 있던 이름은 여기서 빈 문자열이 된다. 그때는 id 로만 식별한다.
    if len(safe) < 3:
        return f"file-{file_id}.mp3"
    return f"{safe[:30]}-{file_id}.mp3"


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


@dataclass(frozen=True)
class AudioSpec:
    """업로드된 mp3 의 실제 규격."""

    sample_rate: int
    channels: int
    kbps: int

    def describe(self) -> str:
        ch = "mono" if self.channels == 1 else f"{self.channels}ch"
        return f"{self.sample_rate // 1000}kHz {ch} {self.kbps}kbps"


def probe_audio(path: Path) -> AudioSpec | None:
    """ffprobe 로 표본율·채널·비트레이트를 읽는다.

    ffprobe 가 없거나 값을 못 읽으면 None 이다. 그때는 규격 검사를 건너뛰고
    파일을 그대로 받는다 — 길이 계산과 같은 방침이다(도구가 없다고 업로드를
    막지는 않는다).
    """
    exe = shutil.which("ffprobe")
    if exe is None:
        log.info("ffprobe 없음 — 오디오 규격 검사를 건너뛴다")
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels,bit_rate:format=bit_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        data = json.loads(out.stdout)
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        # VBR mp3 는 stream.bit_rate 가 없다 — 그때는 format 의 평균값을 쓴다.
        raw_rate = stream.get("bit_rate") or fmt.get("bit_rate")
        spec = AudioSpec(
            sample_rate=int(stream.get("sample_rate") or 0),
            channels=int(stream.get("channels") or 0),
            kbps=round(int(raw_rate) / 1000) if raw_rate else 0,
        )
    except Exception:  # noqa: BLE001 - 못 읽으면 검사를 건너뛴다
        log.warning("ffprobe 규격 확인 실패: %s", path.name)
        return None
    if spec.sample_rate == 0 or spec.channels == 0 or spec.kbps == 0:
        return None
    return spec


#: 비트레이트 비교 여유(kbps). CBR 인코더도 헤더 오버헤드 때문에 1 정도 어긋난다.
_KBPS_TOLERANCE = 1


def audio_action(spec: AudioSpec, target_kbps: int) -> str:
    """규격 대비 어떻게 할지 — "ok" | "reject" | "transcode" (문제점 31번).

    목표보다 낮으면 거절한다. 다시 인코딩해도 없는 음질이 생기지는 않고,
    단말에는 규격 하나만 내려보내야 디코더가 한 가지만 다룬다.
    """
    if spec.sample_rate < AUDIO_SAMPLE_RATE or spec.kbps < target_kbps - _KBPS_TOLERANCE:
        return "reject"
    if (
        spec.sample_rate == AUDIO_SAMPLE_RATE
        and spec.channels == AUDIO_CHANNELS
        and abs(spec.kbps - target_kbps) <= _KBPS_TOLERANCE
    ):
        return "ok"
    return "transcode"


def transcode_in_place(path: Path, target_kbps: int) -> tuple[int, str]:
    """파일을 방송 규격으로 다시 인코딩한다. (크기, sha256) 을 돌려준다.

    `-map_metadata -1` 과 `-id3v2_version 0` 으로 tag 를 전부 버리고, `-write_xing 0` 으로
    맨 앞 Xing 헤더 프레임을 없앤다 — 그 프레임만 40kbps 로 써져서 첫 프레임을 읽는
    도구가 파일 전체를 40kbps 로 보고했다(문제점 30번, tts/engine.py 의 MP3_MUXER_ARGS).
    """
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise ApiError(
            "변환 도구(ffmpeg)가 없어 재인코딩할 수 없습니다.",
            code="TRANSCODE_UNAVAILABLE",
        )
    tmp = path.with_suffix(".conv.mp3")
    try:
        subprocess.run(
            [exe, "-y", "-i", str(path),
             "-map_metadata", "-1",
             "-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS),
             "-b:a", f"{target_kbps}k", *MP3_MUXER_ARGS, str(tmp)],
            capture_output=True, check=True, timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise ApiError("파일 변환에 실패했습니다.", code="TRANSCODE_FAILED") from exc
    tmp.replace(path)

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fp:
        while chunk := fp.read(_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


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

    used = await schedules_using(db, [f.id for f, _ in rows])
    result = []
    for file, uploader in rows:
        out = FileOut.model_validate(file)
        out.uploaded_by_name = uploader
        out.schedule_labels = used.get(file.id, [])
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


def serve_file(file: File) -> Response:
    """파일 바이트를 내려보낸다 — 단말 다운로드(/dl)와 관리자 미리듣기가 같이 쓴다.

    운영(file_accel_location 설정)에서는 바이트를 직접 보내지 않는다. 토큰 검증만 하고
    `X-Accel-Redirect` 헤더를 얹은 빈 응답을 돌려주면, nginx 가 internal location 에서
    같은 볼륨의 파일을 sendfile 로 서빙한다. Range 도 nginx 가 처리하므로 통신 사양의
    resume_offset 재개가 그대로 된다.

    이렇게 하는 이유: 마을 단위 FILE_START 는 그 마을 단말 전체가 동시에 /dl 로 몰린다.
    파이썬 워커가 바이트를 흘려보내면 300대만으로도 관리자 화면까지 같이 느려진다.

    개발(빈 값)에서는 앞에 nginx 가 없으니 FileResponse 로 직접 보낸다.
    """
    path = absolute_path(file)
    if not path.exists():
        raise FileNotFound("파일 원본이 디스크에 없습니다.", code="FILE_MISSING_ON_DISK")
    if settings.file_accel_location:
        # storage_path 는 FILE_ROOT 기준 상대 경로 — internal location 뒤에 그대로 붙인다.
        internal = settings.file_accel_location.rstrip("/") + "/" + quote(file.storage_path)
        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": internal,
                # nginx 는 accel 응답에서 Content-Type/Disposition 은 그대로 전달한다.
                "Content-Type": "audio/mpeg",
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
            },
        )
    return FileResponse(path, media_type="audio/mpeg", filename=file.filename)


# ── 업로드 · 삭제 ────────────────────────────────────────────────────────
async def upload_file(
    db: AsyncSession, upload: UploadFile, *, uploader: User, transcode: bool = False
) -> FileOut:
    """파일함 업로드.

    방송 규격(16kHz mono + 설정 비트레이트)보다 음질이 낮으면 받지 않고, 높으면
    변환할지 물어본다(문제점 31번). 화면은 `AUDIO_NEEDS_TRANSCODE` 를 받으면
    확인 팝업을 띄우고 `transcode=true` 로 같은 파일을 다시 보낸다.
    """
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

    target_kbps = (await load_config(db)).file_bitrate_kbps
    spec = await asyncio.to_thread(probe_audio, dest)
    if spec is not None:
        action = audio_action(spec, target_kbps)
        target_text = f"{AUDIO_SAMPLE_RATE // 1000}kHz mono {target_kbps}kbps"
        if action == "reject":
            dest.unlink(missing_ok=True)
            raise ApiError(
                f"방송 규격({target_text})보다 음질이 낮은 파일입니다 "
                f"(현재 {spec.describe()}). 다시 인코딩해도 음질은 살아나지 않으므로 "
                f"받지 않습니다.",
                code="AUDIO_QUALITY_TOO_LOW",
                detail={"current": spec.describe(), "target": target_text},
            )
        if action == "transcode" and not transcode:
            dest.unlink(missing_ok=True)
            raise ApiError(
                f"이 파일은 {spec.describe()} 입니다. 방송 규격({target_text})으로 "
                f"변환해서 등록할까요? 변환하면 파일 안의 tag 정보는 모두 지워집니다.",
                code="AUDIO_NEEDS_TRANSCODE",
                detail={"current": spec.describe(), "target": target_text},
            )
        if action == "transcode":
            size, sha256 = await asyncio.to_thread(transcode_in_place, dest, target_kbps)
            log.info("업로드 재인코딩 %s → %s", spec.describe(), target_text)

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


async def schedules_using(db: AsyncSession, file_ids: Sequence[int]) -> dict[int, list[str]]:
    """파일별로 그 파일을 쓰는 스케줄의 설명. 목록·삭제 양쪽이 쓴다.

    이력(broadcast_events)은 세지 않는다 — 0017 부터 이력은 삭제를 막지 않는다.
    """
    if not file_ids:
        return {}
    rows = (
        await db.execute(
            select(Schedule.file_id, Schedule.repeat, Schedule.fire_time, Schedule.once_date)
            .where(Schedule.file_id.in_(set(file_ids)))
            .order_by(Schedule.file_id, Schedule.fire_time)
        )
    ).all()
    out: dict[int, list[str]] = {}
    for fid, repeat, fire_time, once_date in rows:
        out.setdefault(fid, []).append(schedule_when(repeat, fire_time, once_date))
    return out


async def delete_file(db: AsyncSession, file_id: int) -> None:
    """파일 삭제.

    DB 행을 먼저 지우고 디스크를 지운다. 순서를 뒤집으면 디스크는 비었는데
    목록에는 남는 상태가 생긴다(그쪽이 더 나쁘다 — 방송을 걸면 404 가 난다).

    방송 이력은 삭제를 막지 않는다(0017) — 행은 남고 file_id 만 NULL 이 되며,
    「무엇을」은 이력에 박아 둔 file_name 이 계속 보여준다.

    스케줄은 막는다. 파일이 사라진 스케줄은 걸릴 때마다 조용히 실패하기 때문이다.
    어느 스케줄인지 이름을 대 준다 — 예전에는 「스케줄이나 이력」이라고만 해서
    스케줄에 넣은 적 없는 사람이 이유를 알 수 없었다.
    """
    file = await get_file(db, file_id)
    path = absolute_path(file)

    used = (await schedules_using(db, [file_id])).get(file_id, [])
    if used:
        raise ApiError(
            f"이 파일을 쓰는 스케줄이 {len(used)}건 있습니다 ({', '.join(used[:3])}"
            f"{' 외' if len(used) > 3 else ''}). "
            "스케줄을 먼저 지우거나 다른 파일로 바꿔 주세요.",
            code="FILE_IN_USE",
            detail={"schedules": used},
        )

    await db.delete(file)
    try:
        await db.flush()
    except Exception as exc:  # noqa: BLE001 - 위에서 못 거른 참조가 남아 있을 때
        raise ApiError(
            "다른 곳에서 사용 중인 파일은 삭제할 수 없습니다.",
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
    # 조인 한 번으로 끝낸다 — 마을 전체가 동시에 부르는 경로라 왕복을 아낀다.
    file = (
        await db.execute(
            select(File)
            .join(DownloadToken, DownloadToken.file_id == File.id)
            .where(
                DownloadToken.token == token,
                DownloadToken.expires_at > dt.datetime.now(dt.timezone.utc),
            )
        )
    ).scalar_one_or_none()
    if file is None:
        raise FileNotFound()
    return file


async def purge_expired_tokens(db: AsyncSession) -> int:
    """만료 토큰 청소. 스케줄러가 주기적으로 부른다."""
    result = await db.execute(
        delete(DownloadToken).where(DownloadToken.expires_at <= dt.datetime.now(dt.timezone.utc))
    )
    return result.rowcount or 0
