"""OTA 요청·응답 모양 (문제점 48번)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.constants import TargetScope
from app.schemas.broadcast import BroadcastOut
from app.schemas.common import ApiModel


class OtaPackageOut(ApiModel):
    id: int
    filename: str
    version: str
    pkg_version: int
    size_bytes: int
    sha256: str
    note: str | None
    uploaded_by: int | None
    uploaded_by_name: str | None = None
    created_at: dt.datetime
    #: 이 패키지로 진행 중인 OTA 가 있으면 지울 수 없다.
    active_jobs: int = 0


class OtaStartRequest(BaseModel):
    package_id: int
    #: 한 번에 **마을 하나** 또는 **단말 하나**(문제점 48번). 전체·구역·여러 개는 받지 않는다.
    target_scope: TargetScope
    target_ids: list[str] = Field(min_length=1, max_length=1)


class OtaDeviceOut(BaseModel):
    """OTA 작업의 단말 한 대 — 응답과 재부팅 뒤 펌웨어 확인을 합쳐 보여준다."""

    mac: str
    label: str | None
    village_name: str | None
    #: 보낼 때 온라인이라 OTA_START 를 받은 단말인가.
    sent: bool
    #: 마지막 결과(OTA_RESULT)·진행(OTA_PROGRESS) 요약. 방송 제어 화면과 같은 모양.
    result_type: str | None = None
    ok: bool | None = None
    reason: str | None = None
    progress: str | None = None
    #: 패키지를 끝까지 받아갔다(서버가 본 OTA_DOWNLOADED, 또는 OTA_RESULT ok). 단말 쪽 기준의
    #: **OTA 성공** — 이 뒤 단말은 네트워크를 끊고 재부팅한다(문제점 48번 보조설명).
    downloaded: bool = False
    #: 지금 STATUS 가 보고하는 실행 중 펌웨어.
    p4_fw: str | None = None
    c6_fw: str | None = None
    #: p4_fw 나 c6_fw 가 패키지 버전과 같으면 True — 재부팅 뒤 적용 확인(현행 02 §10).
    applied: bool = False
    online: bool = False


class OtaJobOut(BaseModel):
    broadcast: BroadcastOut
    package: OtaPackageOut | None
    devices: list[OtaDeviceOut]
    #: 다 받아간(성공) 대수 / 적용 확인된 대수 / 보낸 대수
    done_count: int
    applied_count: int
    sent_count: int
