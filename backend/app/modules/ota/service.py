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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import DeviceState, EventType, ResultType, TargetScope
from app.core.ids import new_download_token, next_job_id
from app.core.presence import is_online, online_cutoff
from app.core.scope import VillageScope
from app.db import session_scope
from app.errors import ApiError, NotFound
from app.models.device import Device
from app.models.event import BroadcastEvent, BroadcastRecipient, DeviceEvent
from app.models.org import User
from app.models.ota import OtaPackage, OtaToken
from app.modules.broadcast import service as broadcast_service
from app.modules.device import service as device_service
from app.mqtt import handlers as mqtt_handlers
from app.mqtt.publisher import MqttPublisher
from app.mqtt.status_buffer import StatusBuffer
from app.schemas.broadcast import BroadcastOut
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
async def issue_token(db: AsyncSession, *, package_id: int, job_id: int, mac: str) -> str:
    """단말 한 대의 다운로드 토큰. 토큰 → 단말이라 누가 다 받았는지 안다."""
    token = new_download_token()
    db.add(
        OtaToken(
            token=token,
            package_id=package_id,
            job_id=job_id,
            mac=mac,
            # 다운로드가 몇 분 걸릴 수 있고 단말이 재시도도 하므로 방송 토큰보다 길게.
            expires_at=dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(seconds=settings.ota_timeout_sec),
        )
    )
    await db.flush()
    return token


async def resolve_token(db: AsyncSession, token: str) -> tuple[OtaToken, OtaPackage]:
    row = (
        await db.execute(
            select(OtaToken, OtaPackage)
            .join(OtaPackage, OtaToken.package_id == OtaPackage.id)
            .where(OtaToken.token == token, OtaToken.expires_at > dt.datetime.now(dt.timezone.utc))
        )
    ).first()
    if row is None:
        raise OtaPackageNotFound()
    return row.OtaToken, row.OtaPackage


# ── 단말 다운로드 (서버가 바이트를 보낸다) ─────────────────────────────────
#: 한 번에 읽는 크기. 몇 MB 패키지를 수십 번에 나눠 보낸다.
STREAM_CHUNK = 256 * 1024


class RangeNotSatisfiable(ApiError):
    status_code = 416
    code = "RANGE_NOT_SATISFIABLE"
    message = "요청한 범위가 파일을 벗어납니다."


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`Range: bytes=a-b` 하나만 받는다(단말 resume_offset). 없으면 None, 못 읽으면 ValueError.

    끝 생략(`bytes=a-`)은 파일 끝까지, 범위를 벗어나면 ValueError(416).
    """
    if not header:
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        raise ValueError(header)
    start_s, _, end_s = spec.strip().partition("-")
    if not start_s:
        raise ValueError(header)  # suffix range(bytes=-N)는 단말이 쓰지 않는다
    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if start < 0 or end < start or start >= size:
        raise ValueError(header)
    return start, min(end, size - 1)


async def stream_package(
    db: AsyncSession, token: str, *, range_header: str | None, buffer: StatusBuffer | None
) -> StreamingResponse:
    """`/dl/ota/<token>` — 패키지 바이트를 **서버가 직접** 보낸다(방송 파일의 X-Accel 과 다르다).

    왜 nginx 에 맡기지 않나: 단말이 마지막 바이트까지 받아간 순간을 서버가 알아야 한다. 실제
    펌웨어는 다 받으면 네트워크를 끊고 재부팅하므로 OTA_RESULT 가 오지 않고, "다 받아감" 이
    곧 성공이다(문제점 48번 보조설명 2026-10-04). nginx 가 보내면 그 순간을 알 수 없다.
    `X-Accel-Buffering: no` 로 nginx 가 응답을 모아 두지 않게 해서, 스트림이 끝난 시점이
    단말이 받은 시점과 거의 같게 한다. 마을 수십 대 × 몇 MB 는 파이썬이 보내도 문제없다.
    """
    tok, pkg = await resolve_token(db, token)
    path = package_path(pkg)
    if not path.exists():
        raise ApiError("패키지 파일이 디스크에 없습니다.", code="FILE_MISSING_ON_DISK")
    size = path.stat().st_size
    try:
        rng = parse_range(range_header, size)
    except ValueError:
        raise RangeNotSatisfiable() from None
    start, end = rng if rng else (0, size - 1)
    if tok.fetched_at is None:
        tok.fetched_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()

    async def body():
        delivered = 0
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = await asyncio.to_thread(f.read, min(STREAM_CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
                    # 여기로 돌아왔다 = 서버가 그 조각을 다 내보내고 다음 조각을 달라고 했다.
                    delivered += len(chunk)
        finally:
            # 기록은 **별도 task** 로. 단말은 마지막 바이트를 받자마자 연결을 끊고, 그러면 이
            # generator 는 취소된다(미들웨어의 cancel scope) — 여기서 DB 를 기다리면 중간에 잘린다.
            # 요청 범위를 끝까지 보냈을 때만. 파일의 마지막 바이트가 포함됐으면 "다 받아감".
            if delivered == end - start + 1:
                asyncio.get_running_loop().create_task(
                    _mark_download_progress(
                        token, sent=delivered, complete=(end == size - 1), buffer=buffer
                    ),
                    name=f"ota-downloaded-{token[:8]}",
                )

    headers = {
        "Content-Disposition": f"attachment; filename=\"{DEVICE_FILE_NAME}\"",
        "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes",
        "X-Accel-Buffering": "no",
    }
    status_code = 200
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status_code = 206
    return StreamingResponse(
        body(), status_code=status_code, media_type="application/octet-stream", headers=headers
    )


async def _mark_download_progress(
    token: str, *, sent: int, complete: bool, buffer: StatusBuffer | None
) -> None:
    """스트림이 끝난 뒤 — 요청 세션은 이미 닫혔으니 새 세션으로.

    다 받아갔으면: ① 서버 결과 OTA_DOWNLOADED 를 그 단말·작업의 이력에 넣고 ② 단말을 지금부터
    오프라인으로 본다(곧 네트워크를 끊고 재부팅한다 — 재부팅 뒤 STATUS 가 오면 다시 온라인)
    ③ 전원이 끝났으면 작업을 종료한다(겹침 잠금 해제).
    """
    try:
        async with session_scope() as db:
            tok = await db.get(OtaToken, token)
            if tok is None:
                return
            tok.bytes_served = (tok.bytes_served or 0) + sent
            if not complete or tok.completed_at is not None:
                return
            tok.completed_at = dt.datetime.now(dt.timezone.utc)
            if not tok.mac or tok.job_id is None:
                return
            await mqtt_handlers._insert_device_event(  # noqa: SLF001 — 수신 경로와 같은 적재
                db,
                mac=tok.mac,
                result_type=ResultType.OTA_DOWNLOADED.value,
                payload={
                    "type": ResultType.OTA_DOWNLOADED.value,
                    "job_id": tok.job_id,
                    "ok": True,
                    "size": tok.bytes_served,
                },
                job_id=tok.job_id,
            )
            dev = await db.get(Device, tok.mac)
            if dev is not None:
                # LWT 와 같은 모양으로 적는다 — is_online() 이 OFFLINE 을 보고 꺼진 걸로 판정한다.
                dev.last_status = {
                    **(dev.last_status or {}),
                    "state": DeviceState.OFFLINE.value,
                    "offline_reason": "OTA_REBOOT",
                }
                if buffer is not None:
                    buffer.discard(tok.mac)  # 대기 중인 낡은 STATUS 가 되살리지 않게(LWT 와 같다)
                # 끊기 직전에 오는 STATUS 도 버린다 — 재부팅 뒤 첫 STATUS 부터 다시 온라인.
                device_service.mark_ota_rebooting(tok.mac)
            log.info(
                "OTA 다 받아감 job_id=%s mac=%s (%d bytes)", tok.job_id, tok.mac, tok.bytes_served
            )
            job_id = tok.job_id
        # 종료 판정은 **커밋한 뒤 새 세션**에서. 마을 OTA 는 단말 여러 대가 거의 동시에 다 받고,
        # 각자의 세션에서 자기 행만 보며 세면 둘 다 "아직 한 대 모자라다"가 되어 작업이 안 끝난다.
        # 커밋 뒤에 세면 마지막으로 커밋한 쪽은 반드시 전원을 본다.
        async with session_scope() as db:
            await broadcast_service.finish_if_all_reported(db, job_id)
    except Exception:  # noqa: BLE001 — 바이트는 이미 다 나갔다. 기록 실패가 단말에 영향 주면 안 된다
        log.exception("OTA 다운로드 완료 기록 실패: token=%s…", token[:8])


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
    # 단말마다 다른 주소(토큰) — 마을 하나라도 마을 토픽이 아니라 단말 토픽으로 한 대씩 보낸다.
    # 주소가 한 개면 누가 다 받아갔는지 구분할 수 없고, "다 받아감"이 OTA 의 성공 신호다.
    base = settings.public_base_url.rstrip("/")
    cmds: dict[str, dict[str, Any]] = {}
    for mac in macs:
        token = await issue_token(db, package_id=pkg.id, job_id=job_id, mac=mac)
        cmds[mac] = ota_start_payload(
            job_id=job_id, pkg_version=pkg.pkg_version, url=f"{base}/dl/ota/{token}",
            size=pkg.size_bytes, sha256=pkg.sha256,
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
    await broadcast_service._commit_before_publish(db)  # noqa: SLF001

    try:
        for mac, cmd in cmds.items():
            await publisher.publish_command(
                payload=cmd, target_scope=TargetScope.DEVICE, scope=scope, macs=[mac]
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
def version_applied(target: str | None, p4: str | None, c6: str | None) -> bool:
    """패키지에 적은 버전이 단말이 보고한 p4_fw/c6_fw 에 반영됐나(문제점 60번).

    적는 법: 칩 하나만 바뀐 패키지는 그 버전 하나(`V.260905-1`) — P4 든 C6 든 어느 한쪽과
    같으면 적용. 둘 다 바뀐 패키지는 `P4버전 / C6버전` — 각각 자기 칩과 같아야 적용.
    한쪽을 비우면(`V.1 /`) 그 칩은 보지 않는다.
    """
    if not target:
        return False
    if "/" in target:
        want_p4, want_c6 = (t.strip() for t in target.split("/", 1))
        return (not want_p4 or want_p4 == p4) and (not want_c6 or want_c6 == c6)
    return target in (p4, c6)


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
        # STATUS 가 버전을 안 실으면 등록 때 적은 값으로(단말 목록과 같은 폴백, 60번 10/9 「-/-」).
        p4 = status.get("p4_fw") or (dev.p4_version if dev is not None else None)
        c6 = status.get("c6_fw") or (dev.c6_version if dev is not None else None)
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
                # 성공 = 다 받아감(OTA_DOWNLOADED) 또는 단말이 보낸 OTA_RESULT ok.
                downloaded=bool(res is not None and res.ok is True),
                p4_fw=p4,
                c6_fw=c6,
                applied=version_applied(target_version, p4, c6),
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
                    downloaded=r.ok is True,
                )
            )
    return OtaJobOut(
        broadcast=out,
        package=pkg_out,
        devices=devices,
        done_count=sum(1 for d in devices if d.downloaded),
        applied_count=sum(1 for d in devices if d.applied),
        sent_count=sum(1 for d in devices if d.sent),
        total_count=len(devices),
    )


async def _summaries(db: AsyncSession, events: Sequence[BroadcastEvent]) -> list[OtaJobOut]:
    """끝난 작업의 요약만(단말 행 없음). 작업 수와 무관하게 쿼리 4개(병목 감사 M3).

    예전에는 끝난 지 몇 주 된 작업까지 매번 단말 행·응답을 다시 만들어, OTA 화면 하나가 2초마다
    쿼리 100개·행 1~2만을 끌어왔다.
    """
    if not events:
        return []
    ids = [e.id for e in events]
    labels = await device_service.describe_targets(
        db, [(e.target_scope, e.target_ids) for e in events]
    )
    sent_rows = (
        await db.execute(
            select(
                BroadcastRecipient.event_id,
                func.count().label("total"),
                func.count().filter(BroadcastRecipient.sent.is_(True)).label("sent"),
            )
            .where(BroadcastRecipient.event_id.in_(ids))
            .group_by(BroadcastRecipient.event_id)
        )
    ).all()
    counts = {eid: (total, sent) for eid, total, sent in sent_rows}
    done_rows = (
        await db.execute(
            select(DeviceEvent.event_id, func.count(func.distinct(DeviceEvent.mac)))
            .where(
                DeviceEvent.event_id.in_(ids),
                DeviceEvent.result_type.in_(("OTA_DOWNLOADED", "OTA_RESULT")),
                DeviceEvent.payload["ok"].astext == "true",
            )
            .group_by(DeviceEvent.event_id)
        )
    ).all()
    done = dict(done_rows)
    pkg_ids = {e.ota_package_id for e in events if e.ota_package_id}
    pkgs = (
        {
            p.id: p
            for p in (await db.scalars(select(OtaPackage).where(OtaPackage.id.in_(pkg_ids)))).all()
        }
        if pkg_ids
        else {}
    )
    out: list[OtaJobOut] = []
    for e, label in zip(events, labels, strict=True):
        b = BroadcastOut.model_validate(e)
        b.target_label = label
        b.phase = "종료"
        b.target_count = e.expected_count or 0
        total, sent = counts.get(e.id, (0, 0))
        pkg = pkgs.get(e.ota_package_id) if e.ota_package_id else None
        out.append(
            OtaJobOut(
                broadcast=b,
                package=_out(pkg, None, 0) if pkg else None,
                devices=[],
                detail=False,
                done_count=int(done.get(e.id, 0)),
                applied_count=0,
                sent_count=int(sent),
                total_count=int(total),
            )
        )
    return out


async def list_jobs(db: AsyncSession, *, limit: int = 20) -> list[OtaJobOut]:
    """최근 작업. 진행 중인 것만 단말별 상세, 끝난 것은 요약(펼치면 GET /jobs/{id})."""
    events = (
        await db.scalars(
            select(BroadcastEvent)
            .where(BroadcastEvent.event_type == EventType.OTA_START.value)
            .order_by(BroadcastEvent.triggered_at.desc())
            .limit(limit)
        )
    ).all()
    active = [e for e in events if e.ended_at is None]
    ended = [e for e in events if e.ended_at is not None]
    by_id = {e.id: await job_out(db, e) for e in active}
    by_id.update({s.broadcast.id: s for s in await _summaries(db, ended)})
    return [by_id[e.id] for e in events]


async def get_job(db: AsyncSession, event_id: int) -> OtaJobOut:
    event = await db.get(BroadcastEvent, event_id)
    if event is None or event.event_type != EventType.OTA_START.value:
        raise NotFound("존재하지 않는 OTA 작업입니다.", code="OTA_JOB_NOT_FOUND")
    return await job_out(db, event)
