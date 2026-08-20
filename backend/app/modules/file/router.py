"""파일 라우터.

두 종류의 엔드포인트가 섞여 있다:
  /api/files*      관리자용. 로그인 토큰 필요.
  /dl/<token>      단말용. 로그인 없이 단기 토큰으로만 접근한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, UploadFile, status
from fastapi import File as FileParam
from fastapi.responses import FileResponse

from app.core.deps import CurrentUser, Db, MediaUser
from app.modules.file import service
from app.schemas.file import FileOut
from app.schemas.tts import TtsRequest, TtsResult, VoiceCatalogOut, VoiceOut
from app.tts import service as tts_service
from app.tts import voices as voice_catalog

router = APIRouter(tags=["file"])


@router.get("/api/files", response_model=list[FileOut])
async def list_files(db: Db, _: CurrentUser) -> list[FileOut]:
    """파일함은 전체 공용이다 — 마을 범위로 나누지 않는다."""
    return await service.list_files(db)


@router.post("/api/files", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    db: Db,
    user: CurrentUser,
    file: Annotated[UploadFile, FileParam()],
) -> FileOut:
    return await service.upload_file(db, file, uploader=user)


@router.get("/api/tts/voices", response_model=VoiceCatalogOut)
async def list_voices(_: CurrentUser) -> VoiceCatalogOut:
    """화면 드롭다운용 언어·보이스 목록."""
    return VoiceCatalogOut(
        languages=voice_catalog.LANGUAGES,
        voices=[
            VoiceOut(id=v.id, label=v.label, language=v.language, engine=v.engine)
            for v in voice_catalog.VOICES
        ],
    )


@router.post("/api/files/tts", response_model=TtsResult, status_code=status.HTTP_201_CREATED)
async def create_tts(payload: TtsRequest, db: Db, user: CurrentUser) -> TtsResult:
    """문구를 합성해 파일함에 넣는다.

    방송은 하지 않는다 — 만든 뒤 미리듣기로 확인하고, 방송 제어에서 골라 송출한다.
    합성과 송출을 나눠야 오타가 마을 스피커로 바로 나가는 일을 막을 수 있다.

    같은 (문구·언어·보이스)면 기존 합성본을 그대로 돌려준다(cached=true).
    """
    file, cached = await tts_service.synthesize(
        db,
        text=payload.text,
        language=payload.language,
        voice_id=payload.voice,
        uploader=user,
        filename=payload.filename,
    )
    return TtsResult(file=file, cached=cached)


@router.delete("/api/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: int, db: Db, _: CurrentUser) -> None:
    await service.delete_file(db, file_id)


@router.get("/api/files/{file_id}/audio")
async def stream_audio(file_id: int, db: Db, _: MediaUser) -> FileResponse:
    """관리자 미리듣기.

    <audio src> 는 Authorization 헤더를 못 붙이므로 ?access_token= 도 받는다
    (MediaUser 의존성이 둘 다 처리한다).
    단말 다운로드는 이 경로가 아니라 /dl/<token> 을 쓴다.
    """
    audio = await service.get_file(db, file_id)
    path = service.absolute_path(audio)
    if not path.exists():
        raise service.FileNotFound(
            "파일 원본이 디스크에 없습니다.", code="FILE_MISSING_ON_DISK"
        )
    return FileResponse(path, media_type="audio/mpeg", filename=audio.filename)


@router.get("/dl/{token}")
async def download_for_device(token: str, db: Db) -> FileResponse:
    """단말 전용 다운로드.

    로그인이 없다 — 단말은 계정이 없고, 대신 FILE_START 로 받은 단기 토큰만 안다.
    토큰이 없거나 만료면 404 다(존재 여부를 알려주지 않는다).

    FileResponse 는 Range 요청을 지원한다 — 통신 사양의 resume_offset 재개가 이걸 탄다.
    """
    audio = await service.resolve_token(db, token)
    path = service.absolute_path(audio)
    if not path.exists():
        raise service.FileNotFound()
    return FileResponse(path, media_type="audio/mpeg", filename=audio.filename)
