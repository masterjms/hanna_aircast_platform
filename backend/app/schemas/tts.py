"""TTS 스키마."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.file import FileOut


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="ko-KR", max_length=10)
    #: 생략하면 언어의 기본 보이스를 쓴다.
    voice: str | None = Field(default=None, max_length=50)
    #: 목록에 보일 이름. 생략하면 문구 앞부분으로 만든다.
    filename: str | None = Field(default=None, max_length=100)


class VoiceOut(BaseModel):
    id: str
    label: str
    language: str
    engine: str


class VoiceCatalogOut(BaseModel):
    """화면 드롭다운이 쓰는 목록. 서버와 같은 표를 보게 한다."""

    languages: dict[str, str]
    voices: list[VoiceOut]


class TtsResult(BaseModel):
    file: FileOut
    #: True 면 기존 합성본을 재사용한 것이다(Polly 호출 없음).
    cached: bool
