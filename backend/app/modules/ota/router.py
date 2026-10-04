"""OTA 관리 라우터 — 최고 관리자만 (문제점 48번). 단말 다운로드(/dl/ota/<token>)만 인증이 없다."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Response, UploadFile, status
from fastapi import File as FileParam

from app.core.deps import Db, Publisher, SuperAdmin
from app.modules.file import service as file_service
from app.modules.ota import service
from app.schemas.ota import OtaJobOut, OtaPackageOut, OtaStartRequest

router = APIRouter(tags=["ota"])


@router.get("/api/ota/packages", response_model=list[OtaPackageOut])
async def list_packages(db: Db, _: SuperAdmin) -> list[OtaPackageOut]:
    return await service.list_packages(db)


@router.post("/api/ota/packages", response_model=OtaPackageOut, status_code=status.HTTP_201_CREATED)
async def upload_package(
    db: Db,
    user: SuperAdmin,
    file: Annotated[UploadFile, FileParam()],
    version: Annotated[str, Form(max_length=50)],
    pkg_version: Annotated[int, Form(ge=1)],
    note: Annotated[str | None, Form(max_length=500)] = None,
) -> OtaPackageOut:
    """IOT_RADIO.pkg 업로드. version 은 STATUS 의 p4_fw/c6_fw 와 비교할 문자열(적용 확인용)."""
    return await service.upload_package(
        db, file, version=version, pkg_version=pkg_version, note=note, uploader=user
    )


@router.delete("/api/ota/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(package_id: int, db: Db, _: SuperAdmin) -> None:
    await service.delete_package(db, package_id)


@router.post("/api/ota/start", response_model=OtaJobOut, status_code=status.HTTP_201_CREATED)
async def start(
    payload: OtaStartRequest, db: Db, user: SuperAdmin, publisher: Publisher
) -> OtaJobOut:
    """OTA_START 발행 — 마을 하나 또는 단말 하나. 방송 중인 단말은 겹침(409)으로 막힌다."""
    return await service.start(db, payload, publisher, user=user)


@router.get("/api/ota/jobs", response_model=list[OtaJobOut])
async def list_jobs(db: Db, _: SuperAdmin, limit: int = 20) -> list[OtaJobOut]:
    return await service.list_jobs(db, limit=min(max(limit, 1), 100))


@router.get("/api/ota/jobs/{event_id}", response_model=OtaJobOut)
async def get_job(event_id: int, db: Db, _: SuperAdmin) -> OtaJobOut:
    return await service.get_job(db, event_id)


@router.get("/dl/ota/{token}")
async def download_for_device(token: str, db: Db) -> Response:
    """단말 전용 패키지 다운로드. 토큰이 없거나 만료면 404. 바이트는 nginx(X-Accel)가 보낸다."""
    pkg = await service.resolve_token(db, token)
    return file_service.serve_path(
        pkg.storage_path, filename=service.DEVICE_FILE_NAME, media_type="application/octet-stream"
    )
