"""OTA 펌웨어 패키지 — ota_packages · ota_tokens (문제점 48번, 2026-10-04).

단말 계약(현행 문서 02 §10): 서버는 `OTA_START` 하나만 보내고 단말이 다운로드·검증·적용·
재부팅까지 알아서 한다. 패키지는 `IOT_RADIO.pkg` 한 파일(P4·C6 펌웨어가 안에 들어 있고
어느 칩용인지는 P4 가 판단한다). 서버가 아는 것은 파일의 크기·sha256 과 사람이 적은 버전뿐이다.

바이너리는 방송 파일과 같은 볼륨(FILE_ROOT/update)에 둔다 — nginx 의 internal location 이
그대로 sendfile 로 보낸다. 다운로드 주소는 방송 파일처럼 짧은 토큰(`/dl/ota/<token>`)이다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OtaPackage(Base):
    __tablename__ = "ota_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 올린 파일 이름(표시용). 단말 계약상 이름은 IOT_RADIO.pkg 지만 서버 저장 이름은 다르다.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 사람이 읽는 펌웨어 버전. 적용 확인 때 STATUS 의 p4_fw/c6_fw 와 **문자열 그대로** 비교한다.
    #: 예: "V.260905-1".
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    #: OTA_START 의 pkg_version(정수 — 통신 사양 §3.6). 단말이 같은 번호면 거절할 수 있다.
    pkg_version: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: FILE_ROOT 기준 상대 경로(update/…). 절대 경로를 넣지 않는다.
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OtaToken(Base):
    """단말 다운로드용 단기 토큰. 방송 파일의 download_tokens 와 같은 역할(파일 대신 패키지)."""

    __tablename__ = "ota_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("ota_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    #: 토큰은 **단말마다** 하나다(0023). 어느 단말이 다 받아갔는지 토큰으로 안다 — 마을 토픽에
    #: 주소 하나를 실으면 누가 받았는지 구분할 수 없다.
    mac: Mapped[str | None] = mapped_column(String(12))
    #: 첫 GET 시각 · 마지막 바이트까지 보낸 시각 · 보낸 바이트(Range 재개 포함, 누적).
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    bytes_served: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
