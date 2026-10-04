"""OTA 관리 — 패키지 업로드·배포(OTA_START)·진행 (문제점 48번, 2026-10-04).

단말 계약(현행 02 §10)은 좁다: 서버는 OTA_START 하나(job_id·url·size·sha256·pkg_version)만
보내고, 단말이 다운로드·검증·적용·재부팅을 알아서 한다. 진행은 OTA_PROGRESS(최신값만), 끝은
OTA_RESULT(한 번). 재부팅 뒤 STATUS 의 p4_fw/c6_fw 가 목표 버전이어야 최종 성공이다.

서버 쪽은 방송 경로를 그대로 탄다:
  · OTA 작업 = broadcast_events 한 행(event_type OTA_START, ota_package_id). 단말 응답은 방송과
    같은 수신 경로로 device_events 에 쌓이고(handlers 는 type 을 가리지 않는다), 전원 OTA_RESULT 가
    오거나 OTA_TIMEOUT_SEC 이 지나면 종료 확정(broadcast.service.finish_if_all_reported /
    _force_end_after).
  · 대상 범위·겹침 검사·커밋 후 발행·수신 단말 스냅숏도 방송 함수들을 그대로 쓴다.
  · 방송 중인 단말은 단말이 BUSY 로 거절한다. 반대로 OTA 중 방송 시작은 겹침 검사(_active_events 에
    OTA 행이 있다)에 걸려 409 가 난다 — 방송하기 화면의 진행 중 목록에서는 OTA 를 빼서(list_active)
    「방송 끄기」가 FILE_STOP 을 보내는 일이 없게 했다.

범위(문제점 48번): 최고 관리자만, 한 번에 마을 하나 또는 단말 하나.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import EventType, TargetScope
from app.core.ids import new_download_token, next_job_id
from app.core.presence import is_online, online_cutoff
from app.core.scope import VillageScope
from app.errors import ApiError, NotFound
from app.models.device import Device
from app.models.event import BroadcastEvent, BroadcastRecipient
from app.models.org import User
from app.models.ota import OtaPackage, OtaToken
from app.modules.broadcast import service as broadcast_service
from app.mqtt.publisher import MqttPublisher
from app.schemas.ota import OtaDeviceOut, OtaJobOut, OtaPackageOut, OtaStartRequest

log = logging.getLogger(__name__)

#: 단말 쪽 PSRAM 사정으로 패키지가 아주 클 수는 없다. 50 MiB 는 넉넉한 상한일 뿐이다.
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024
#: 단말이 받는 파일 이름(계약: IOT_RADIO.pkg). 서버 저장 이름은 패키지마다 다르다.
DEVICE_FILE_NAME = "IOT_RADIO.pkg"


class OtaPackageNotFound(NotFound):
    code = "OTA_PACKAGE_NOT_FOUND"
    message = "존재하지 않는 OTA 패키지입니다."


def package_path(pkg: OtaPackage) -> Path:
    return settings.file_root / pkg.storage_path


# ── 패키지 ───────────────────────────────────────────────────────────────
async def _active_job_counts(db: AsyncSession, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = await db.execute(
        select(BroadcastEvent.ota_package_id, func.count())
        .where(
            BroadcastEvent.ota_package_id.in_(ids),
            BroadcastEvent.ended_at.is_(None),
            BroadcastEvent.event_type == EventType.OTA_START.value,
        )
        .group_by(BroadcastEvent.ota_package_id)
    )
    return {int(pid): int(n) for pid, n in rows.all()}


def _out(pkg: OtaPackage, name: str | None, active: int) -> OtaPackageOut:
    out = OtaPackageOut.model_validate(pkg, from_attributes=True)
    out.uploaded_by_name = name
    out.active_jobs = active
    return out


async def list_packages(db: AsyncSession) -> list[OtaPackageOut]:
    rows = (
        await db.execute(
            select(OtaPackage, User.username)
            .outerjoin(User, User.id == OtaPackage.uploaded_by)
            .order_by(OtaPackage.created_at.desc())
        )
    ).all()
    active = await _active_job_counts(db, [p.id for p, _ in rows])
    return [_out(p, name, active.get(p.id, 0)) for p, name in rows]


async def get_package(db: AsyncSession, package_id: int) -> OtaPackage:
    pkg = await db.get(OtaPackage, package_id)
    if pkg is None:
        raise OtaPackageNotFound(detail={"id": package_id})
    return pkg


def _write_and_hash(upload: UploadFile, dest: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while chunk := upload.file.read(_CHUNK):
            size += len(chunk)
            if size > MAX_PACKAGE_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise ApiError(
                    f"패키지가 너무 큽니다 ({MAX_PACKAGE_BYTES // (1024 * 1024)} MiB 까지).",
                    code="OTA_PACKAGE_TOO_LARGE",
                )
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest()


async def upload_package(
    db: AsyncSession,
    upload: UploadFile,
    *,
    version: str,
    pkg_version: int,
    note: str | None,
    uploader: User,
) -> OtaPackageOut:
    original = upload.filename or DEVICE_FILE_NAME
    if not original.lower().endswith(".pkg"):
        raise ApiError("OTA 패키지는 .pkg 파일만 올릴 수 있습니다.", code="UNSUPPORTED_FILE_TYPE")
    version = version.strip()
    if not version:
        raise ApiError("펌웨어 버전을 적어 주세요(예: V.260905-1).", code="VALIDATION_ERROR")
    if pkg_version < 1:
        raise ApiError("pkg_version 은 1 이상이어야 합니다.", code="VALIDATION_ERROR")

    rel = Path("update") / f"{dt.date.today():%Y%m}" / f"{uuid.uuid4().hex}.pkg"
    dest = settings.file_root / rel
    size, sha256 = await asyncio.to_thread(_write_and_hash, upload, dest)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise ApiError("빈 파일은 올릴 수 없습니다.", code="EMPTY_FILE")

    pkg = OtaPackage(
        filename=original,
        version=version,
        pkg_version=pkg_version,
        size_bytes=size,
        sha256=sha256,
        storage_path=str(rel).replace("\\", "/"),
        note=(note or "").strip() or None,
        uploaded_by=uploader.id,
    )
    db.add(pkg)
    await db.flush()
    log.info("OTA 패키지 올림 #%d %s v%s pkg_version=%d (%d bytes)", pkg.id, original, version,
             pkg_version, size)
    return _out(pkg, uploader.username, 0)


async def delete_package(db: AsyncSession, package_id: int) -> None:
    pkg = await get_package(db, package_id)
    active = await _active_job_counts(db, [pkg.id])
    if active.get(pkg.id):
        raise ApiError(
            "이 패키지로 진행 중인 OTA 가 있어 지울 수 없습니다. 끝난 뒤 지워 주세요.",
            code="OTA_PACKAGE_IN_USE",
        )
    path = package_path(pkg)
    await db.delete(pkg)
    await db.flush()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.exception("OTA 패키지 디스크 삭제 실패(고아 파일): %s", path)


# ── 다운로드 토큰 ─────────────────────────────────────────────────────────
async def issue_token(db: AsyncSession, *, package_id: int, job_id: int) -> str:
    token = new_download_token()
    db.add(
        OtaToken(
            token=token,
            package_id=package_id,
            job_id=job_id,
            # 다운로드가 몇 분 걸릴 수 있고 단말이 재시도도 하므로 방송 토큰보다 길게.
            expires_at=dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(seconds=settings.ota_timeout_sec),
        )
    )
    await db.flush()
    return token


async def resolve_token(db: AsyncSession, token: str) -> OtaPackage:
    pkg = (
        await db.execute(
            select(OtaPackage)
            .join(OtaToken, OtaToken.package_id == OtaPackage.id)
            .where(OtaToken.token == token, OtaToken.expires_at > dt.datetime.now(dt.timezone.utc))
        )
    ).scalar_one_or_none()
    if pkg is None:
        raise OtaPackageNotFound()
    return pkg


# ── 배포 ─────────────────────────────────────────────────────────────────
def ota_start_payload(
    *, job_id: int, pkg_version: int, url: str, size: int, sha256: str
) -> dict[str, Any]:
    """OTA_START (통신 사양 §3.6 · 현행 02 §10). targets·reboot 는 없다 — 단말이 알아서 한다."""
    return {
        "type": EventType.OTA_START.value,
        "job_id": job_id,
        "pkg_version": pkg_version,
        "url": url,
        "size": size,
        "sha256": sha256,
    }


async def start(
    db: AsyncSession, payload: OtaStartRequest, publisher: MqttPublisher, *, user: User
) -> OtaJobOut:
    if payload.target_scope not in (TargetScope.VILLAGE, TargetScope.DEVICE):
        raise ApiError(
            "OTA 는 마을 하나 또는 단말 하나에만 보낼 수 있습니다.", code="OTA_TARGET_SCOPE"
        )
    pkg = await get_package(db, payload.package_id)
    if not package_path(pkg).exists():
        raise ApiError(
            "패키지 파일이 디스크에 없습니다. 다시 올려 주세요.", code="FILE_MISSING_ON_DISK"
        )

    scope = VillageScope.for_super_admin()
    broadcast_service._ensure_mqtt(publisher)  # noqa: SLF001 — 방송과 같은 선검사
    await broadcast_service._lock_broadcast_start(db)  # noqa: SLF001
    macs = await broadcast_service._resolve_targets(  # noqa: SLF001
        db, target_scope=payload.target_scope, target_ids=payload.target_ids, scope=scope
    )
    await broadcast_service._assert_no_overlap(db, macs)  # noqa: SLF001

    job_id = await next_job_id(db)
    token = await issue_token(db, package_id=pkg.id, job_id=job_id)
    url = f"{settings.public_base_url.rstrip('/')}/dl/ota/{token}"
    cmd = ota_start_payload(
        job_id=job_id, pkg_version=pkg.pkg_version, url=url, size=pkg.size_bytes, sha256=pkg.sha256
    )

    event = BroadcastEvent(
        event_type=EventType.OTA_START.value,
        job_id=job_id,
        target_scope=payload.target_scope.value,
        target_ids=payload.target_ids,
        # 기록의 「무엇을」 — 패키지를 지워도 남는다.
        file_name=f"{pkg.filename} ({pkg.version})",
        triggered_by=user.id,
        expected_count=len(macs),
        ota_package_id=pkg.id,
    )
    db.add(event)
    await db.flush()
    await broadcast_service.snapshot_recipients(db, event, sent_macs=macs)
    village_kw = await broadcast_service._village_kw(  # noqa: SLF001
        db, payload.target_scope, payload.target_ids
    )
    await broadcast_service._commit_before_publish(db)  # noqa: SLF001

    try:
        await publisher.publish_command(
            payload=cmd, target_scope=payload.target_scope, scope=scope, **village_kw, macs=macs
        )
    except Exception:
        await broadcast_service.end_event(db, event, reason="발행 실패")
        await db.commit()
        raise
    log.info("OTA 시작 job_id=%d 패키지 #%d %s 대상 %d대", job_id, pkg.id, pkg.version, len(macs))

    asyncio.create_task(
        broadcast_service._force_end_after(  # noqa: SLF001
            event.id, settings.ota_timeout_sec, reason="OTA 응답 대기 시간 초과"
        ),
        name=f"ota-end-{job_id}",
    )
    return await job_out(db, event)


# ── 조회 ─────────────────────────────────────────────────────────────────
def _version_from_name(file_name: str | None) -> str | None:
    """start() 가 적는 `"<파일> (<버전>)"` 에서 버전을 되찾는다. 모양이 다르면 None."""
    if not file_name or not file_name.endswith(")") or " (" not in file_name:
        return None
    return file_name.rsplit(" (", 1)[1][:-1] or None


async def job_out(db: AsyncSession, event: BroadcastEvent) -> OtaJobOut:
    """작업 하나 — 방송 응답(OTA_RESULT·OTA_PROGRESS) + 재부팅 뒤 펌웨어 확인."""
    out = await broadcast_service._to_out(db, event)  # noqa: SLF001
    pkg = await db.get(OtaPackage, event.ota_package_id) if event.ota_package_id else None
    pkg_out = _out(pkg, None, 0) if pkg else None
    # 패키지를 지운 뒤에도 적용 확인은 돼야 한다 — 기록의 「무엇을」(file_name) 끝 괄호가 버전이다.
    target_version = pkg.version if pkg else _version_from_name(event.file_name)

    recipients = (
        await db.execute(
            select(BroadcastRecipient, Device)
            .outerjoin(Device, Device.mac == BroadcastRecipient.mac)
            .where(BroadcastRecipient.event_id == event.id)
            .order_by(BroadcastRecipient.mac)
        )
    ).all()
    by_mac = {r.mac: r for r in out.results}
    cutoff = online_cutoff()
    devices: list[OtaDeviceOut] = []
    for rec, dev in recipients:
        status: dict[str, Any] = (dev.last_status if dev is not None else None) or {}
        p4, c6 = status.get("p4_fw"), status.get("c6_fw")
        res = by_mac.get(rec.mac)
        devices.append(
            OtaDeviceOut(
                mac=rec.mac,
                label=rec.label if rec.label else (dev.label if dev else None),
                village_name=rec.village_name,
                sent=rec.sent,
                result_type=res.result_type if res else None,
                ok=res.ok if res else None,
                reason=res.reason if res else None,
                progress=res.stats if res else None,
                p4_fw=p4,
                c6_fw=c6,
                applied=bool(target_version) and target_version in (p4, c6),
                online=bool(dev is not None and is_online(dev, cutoff)),
            )
        )
    if not recipients:
        # 스냅숏이 없는 옛 작업 — 응답한 단말만
        for r in out.results:
            devices.append(
                OtaDeviceOut(
                    mac=r.mac, label=r.label, village_name=None, sent=True,
                    result_type=r.result_type, ok=r.ok, reason=r.reason, progress=r.stats,
                )
            )
    return OtaJobOut(
        broadcast=out,
        package=pkg_out,
        devices=devices,
        applied_count=sum(1 for d in devices if d.applied),
        sent_count=sum(1 for d in devices if d.sent),
    )


async def list_jobs(db: AsyncSession, *, limit: int = 20) -> list[OtaJobOut]:
    events = (
        await db.scalars(
            select(BroadcastEvent)
            .where(BroadcastEvent.event_type == EventType.OTA_START.value)
            .order_by(BroadcastEvent.triggered_at.desc())
            .limit(limit)
        )
    ).all()
    return [await job_out(db, e) for e in events]


async def get_job(db: AsyncSession, event_id: int) -> OtaJobOut:
    event = await db.get(BroadcastEvent, event_id)
    if event is None or event.event_type != EventType.OTA_START.value:
        raise NotFound("존재하지 않는 OTA 작업입니다.", code="OTA_JOB_NOT_FOUND")
    return await job_out(db, event)
