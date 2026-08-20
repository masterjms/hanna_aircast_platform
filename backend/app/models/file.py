"""파일 라이브러리 — files, download_tokens."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import FileSource
from app.models.base import Base

_SOURCES = ", ".join(f"'{s.value}'" for s in FileSource)


class File(Base):
    """방송용 오디오 파일. 바이너리는 DB 에 넣지 않고 로컬 디스크에 둔다."""

    __tablename__ = "files"
    __table_args__ = (CheckConstraint(f"source IN ({_SOURCES})", name="ck_files_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 단말이 다운로드 후 검증하는 값. FILE_START payload 에 그대로 실린다.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=FileSource.UPLOAD.value
    )

    #: FILE_ROOT 기준 상대 경로. 절대 경로를 넣지 않는다(온프레미스 이전 시 깨진다).
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    # source='tts' 일 때만 채운다. (text|lang|voice) 가 캐시 키다.
    tts_text: Mapped[str | None] = mapped_column(Text)
    tts_lang: Mapped[str | None] = mapped_column(String(10))
    tts_voice: Mapped[str | None] = mapped_column(String(50))

    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DownloadToken(Base):
    """단말 다운로드용 단기 토큰.

    MQTT CMD payload 가 1024바이트를 넘으면 안 되므로 긴 서명 URL 을 쓸 수 없다.
    짧은 토큰만 내려보내고 서버가 /dl/<token> 에서 검증·서빙한다.
    """

    __tablename__ = "download_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 어느 방송 명령 때문에 발급됐는지. 이력 추적용.
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
