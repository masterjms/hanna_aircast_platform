"""파일 라이브러리 스키마."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import field_serializer

from app.constants import FileSource
from app.schemas.common import ApiModel


class FileOut(ApiModel):
    id: int
    filename: str
    size_bytes: int
    sha256: str
    source: FileSource
    duration_sec: Decimal | None
    tts_text: str | None
    tts_lang: str | None
    tts_voice: str | None
    uploaded_by: int | None
    #: 목록에서 "누가 올렸는지"를 보여주려고 조인해 채운다.
    uploaded_by_name: str | None = None
    #: 이 파일을 쓰는 스케줄들("매주 09:00"). 비어 있지 않으면 삭제가 막힌다.
    #: 방송 이력은 여기 세지 않는다 — 이력은 삭제를 막지 않는다(0017).
    schedule_labels: list[str] = []
    created_at: dt.datetime

    @field_serializer("duration_sec")
    def _duration_as_float(self, v: Decimal | None) -> float | None:
        """JSON 에 문자열이 아니라 숫자로 나가게 한다 — 프론트가 바로 포맷할 수 있다."""
        return None if v is None else float(v)
