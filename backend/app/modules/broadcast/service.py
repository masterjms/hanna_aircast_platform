"""방송 제어 서비스.

broadcast_events 를 소유한다. 흐름은 항상 같다:

    요청 검증 → 권한 확인 → 대상 해석 → 겹침 검사(409) → job_id 발번
      → 토큰 발급 → MQTT 발행 → broadcast_events insert → 응답

핵심 규칙 두 가지:
  · 진행 중인 방송을 서버가 자동으로 끊지 않는다. 겹치면 409 로 거절하고
    사용자가 판단하게 한다.
  · 대상은 온라인 단말만 센다(통신 사양). 꺼진 단말을 기다리며 진행률이
    영원히 100%가 안 되는 상황을 만들지 않는다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import TELEMETRY_RESULTS, EventType, TargetScope
from app.core.ids import next_job_id
from app.core.scope import VillageScope
from app.db import session_scope
from app.errors import ApiError, BroadcastOverlap, NotFound
from app.live.icecast import IcecastSource
from app.live.mount import mount_path, stream_url
from app.live.registry import LiveRegistry, LiveSession
from app.models.device import Device
from app.models.event import BroadcastEvent, DeviceEvent
from app.models.file import File
from app.modules.device import service as device_service
from app.modules.file import service as file_service
from app.mqtt.publisher import MqttPublisher
from app.schemas.broadcast import (
    BroadcastOut,
    DeviceResultOut,
    FileBroadcastRequest,
    LiveBroadcastRequest,
)

log = logging.getLogger(__name__)

#: 단말이 "성공"으로 보고하는 result_type.
#: LIVE_READY 는 status 로 성패가 갈려서 별도 처리한다.
_SUCCESS_RESULTS = {"FILE_END"}
#: "실패/중단"으로 보고하는 result_type.
_FAILURE_RESULTS = {"FILE_ABORT", "FILE_STOP_RESULT"}
#: LIVE_READY.status — 0 만 준비 완료다(1=TIMEOUT, 2=ABORT, 3=FAIL/BUSY).
_LIVE_READY_OK = 0

#: 방송 시작을 직렬화하는 어드바이저리 락 키.
#: 값 자체에 의미는 없고, 이 서버 안에서 유일하기만 하면 된다.
_BROADCAST_LOCK_KEY = 0x78776966_69627263  # "xwifibrc"


class BroadcastNotFound(NotFound):
    code = "BROADCAST_NOT_FOUND"
    message = "존재하지 않는 방송입니다."


# ── 대상 해석 ────────────────────────────────────────────────────────────
async def _resolve_targets(
    db: AsyncSession,
    *,
    target_scope: TargetScope,
    target_ids: list[str],
    scope: VillageScope,
) -> list[str]:
    """방송 대상 → 온라인 MAC 목록."""
    if target_scope is TargetScope.ALL and not scope.all_villages:
        raise ApiError(
            "전체 방송은 최고 관리자만 실행할 수 있습니다.", code="SUPER_ADMIN_REQUIRED"
        )

    macs = await device_service.macs_for_target(
        db,
        target_scope=target_scope.value,
        target_ids=target_ids,
        scope=scope,
        online_only=True,
    )
    if not macs:
        raise ApiError(
            "대상 중 온라인 단말이 없습니다. 단말 상태를 확인해 주세요.",
            code="NO_ONLINE_TARGET",
        )
    return macs


async def _active_events(db: AsyncSession) -> list[BroadcastEvent]:
    """진행 중인 방송 전부. 겹침 검사의 기준이다."""
    rows = await db.scalars(
        select(BroadcastEvent)
        .where(BroadcastEvent.ended_at.is_(None))
        .order_by(BroadcastEvent.triggered_at)
    )
    return list(rows.all())


async def _lock_broadcast_start(db: AsyncSession) -> None:
    """방송 시작을 한 줄로 세운다.

    겹침 검사와 이벤트 생성 사이에 다른 요청이 끼어들면, 그 요청은 아직
    커밋되지 않은 앞 방송을 보지 못하고 검사를 통과한다. 그러면 같은 단말에
    두 방송이 동시에 걸린다 — 실제로 32ms 차이로 재현됐다.

    트랜잭션 락이라 커밋·롤백 시 자동으로 풀린다. 방송 시작은 초당 수십 번
    일어나는 일이 아니라 직렬화 비용은 무시할 수 있다.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _BROADCAST_LOCK_KEY}
    )


async def _assert_no_overlap(db: AsyncSession, macs: list[str]) -> None:
    """대상 단말이 이미 다른 방송에 잡혀 있으면 409.

    진행 중 이벤트의 target_scope/target_id 를 다시 해석해서 MAC 집합을 구한다.
    발행 시점의 대상을 따로 저장하지 않는 이유는, 그 사이 배정이 바뀌면
    저장해 둔 목록이 현실과 어긋나기 때문이다 — 지금 기준으로 다시 푸는 게 맞다.
    """
    wanted = set(macs)
    conflicts = []

    # 겹침 검사는 시스템 전체를 봐야 한다 — 남의 마을 방송과도 단말이 겹칠 수 있다.
    # (village_admin 이 자기 범위 밖 방송을 "보는" 게 아니라, 단말 점유만 확인한다.)
    admin_scope = VillageScope.for_super_admin()

    for event in await _active_events(db):
        try:
            busy = await device_service.macs_for_target(
                db,
                target_scope=event.target_scope,
                target_ids=event.target_ids,
                scope=admin_scope,
                online_only=False,
            )
        except ApiError:
            # 대상이 이미 삭제된 경우. 겹칠 단말이 없다고 본다.
            continue

        overlap = wanted & set(busy)
        if overlap:
            conflicts.append(
                {
                    "id": event.id,
                    "job_id": event.job_id,
                    "event_type": event.event_type,
                    "macs": sorted(overlap),
                }
            )

    if conflicts:
        raise BroadcastOverlap(detail={"conflicts": conflicts})


def _live_ready_ok(payload: dict) -> bool | None:
    """LIVE_READY 성공 판정. 두 가지 형식을 모두 받는다.

    펌웨어가 결과 표현을 바꿨다:
        옛  {"status": 0, "reason": 0}          status 0 이면 준비 완료
        새  {"ok": true,  "code": "..."}        불리언 + 사유 문자열

    새 형식만 보고 status 로 판정하면 필드가 없어 None != 0 → **항상 실패**로
    표시된다. 실제로 소리는 나오는데 화면만 "실패 1"로 나왔다(2026-08-29).
    둘 다 받아 두면 펌웨어 버전이 섞여 있어도 화면이 맞는다.
    """
    if "ok" in payload:
        return bool(payload["ok"])
    if "status" in payload:
        return payload["status"] == _LIVE_READY_OK
    return None


def _reason_text(result_type: str | None, payload: dict) -> str | None:
    """실패 사유 등 결과에 붙는 짧은 설명."""
    if result_type in TELEMETRY_RESULTS:
        return None
    # code 는 새 형식(문자열 사유), reason/fail_reason 은 옛 형식.
    return str(
        payload.get("code") or payload.get("reason") or payload.get("fail_reason") or ""
    ) or None


def _stats_text(payload: dict) -> str | None:
    """LIVE_STATS 를 한 줄로 요약한다.

    버퍼가 얼마나 차 있고 끊김(언더런)이 있었는지가 운영자가 볼 값이다.
    소리가 끊긴다는 신고가 오면 여기 숫자로 원인을 가른다.
    """
    parts = []
    buffer_ms = payload.get("p4_buffer_ms")
    if buffer_ms is not None:
        parts.append(f"버퍼 {int(buffer_ms) / 1000:.1f}초")
    for key, label in (("underrun_count", "끊김"), ("decode_error_count", "디코딩오류")):
        count = payload.get(key)
        if count:
            parts.append(f"{label} {count}")
    return " · ".join(parts) or None


# ── 조회 ─────────────────────────────────────────────────────────────────
async def _to_out(
    db: AsyncSession, event: BroadcastEvent, registry: LiveRegistry | None = None
) -> BroadcastOut:
    """이벤트 + 단말별 응답 + 현재 대상 수를 한 덩어리로 만든다.

    target_count 는 매번 다시 센다. 발행 시점 수를 따로 저장하지 않는 이유는
    그 사이 배정이 바뀌거나 단말이 꺼지면 저장값이 현실과 어긋나기 때문이다 —
    진행률은 "지금 대상인 단말" 기준이 자연스럽다.
    """
    out = BroadcastOut.model_validate(event)

    # 진행 중일 때만 센다. 끝난 방송은 응답 수가 곧 결과다.
    if event.ended_at is None:
        try:
            macs = await device_service.macs_for_target(
                db,
                target_scope=event.target_scope,
                target_ids=event.target_ids,
                scope=VillageScope.for_super_admin(),
                online_only=True,
            )
            out.target_count = len(macs)
        except ApiError:
            out.target_count = 0
    else:
        out.target_count = len(
            {
                r
                for r in await db.scalars(
                    select(DeviceEvent.mac).where(DeviceEvent.event_id == event.id)
                )
            }
        )

    if event.file_id is not None:
        out.file_name = await db.scalar(select(File.filename).where(File.id == event.file_id))

    rows = (
        await db.execute(
            select(DeviceEvent, Device.label)
            .outerjoin(Device, DeviceEvent.mac == Device.mac)
            .where(DeviceEvent.event_id == event.id)
            .order_by(DeviceEvent.received_at)
        )
    ).all()

    # 단말 하나당 한 줄로 접는다.
    #
    # 한 단말이 같은 방송에 메시지를 여러 번 보낸다:
    #   LIVE_READY status=0  준비 완료 (Icecast 접속 전)
    #   LIVE_STATS           수신 품질, 주기적으로
    #   LIVE_READY status=2  접속 실패로 abort
    # 이걸 그대로 나열하면 단말 1대가 화면에 3줄로 보이고, "준비 완료 1/2대"
    # 처럼 대수까지 틀리게 센다(행 수를 대수로 착각).
    #
    # 그래서 단말마다 **마지막 결과**로 상태를 정하고, telemetry 는 그 줄에
    # 덧붙인다. 마지막 결과를 쓰는 게 중요하다 — status=0 뒤에 abort 가 오면
    # 그 단말은 실패다(ESP32 회신 260824 §5.2).
    latest: dict[str, tuple[DeviceEvent, str | None]] = {}
    telemetry: dict[str, DeviceEvent] = {}
    for de, label in rows:
        if de.result_type in TELEMETRY_RESULTS:
            telemetry[de.mac] = de
            latest.setdefault(de.mac, (de, label))
        else:
            latest[de.mac] = (de, label)

    results: list[DeviceResultOut] = []
    for mac, (de, label) in latest.items():
        payload = de.payload or {}
        ok: bool | None = None
        if de.result_type in _SUCCESS_RESULTS:
            # verify_ok 가 있으면 그 값을 믿는다(sha256 검증 결과).
            ok = bool(payload.get("verify_ok", True))
        elif de.result_type == "LIVE_READY":
            ok = _live_ready_ok(payload)
        elif de.result_type in _FAILURE_RESULTS:
            ok = False

        stats = telemetry.get(mac)
        results.append(
            DeviceResultOut(
                mac=mac,
                label=label,
                result_type=de.result_type,
                ok=ok,
                reason=_reason_text(de.result_type, payload),
                stats=_stats_text(stats.payload or {}) if stats is not None else None,
                received_at=de.received_at,
            )
        )
    results.sort(key=lambda r: (r.ok is not False, r.mac))

    out.results = results

    # 실시간 방송이면 살아 있는 세션에서 스트림 정보를 붙인다.
    # 서버가 재기동되면 레지스트리가 비므로 진행 중이던 LIVE 는 여기서 빈다 —
    # 화면에는 "업링크 끊김"으로 보이고 사용자가 종료할 수 있다.
    if registry is not None and event.job_id is not None:
        session = registry.get(event.job_id)
        if session is not None:
            out.stream_url = session.stream_url
            out.ingest_path = f"/ingest?session={session.session_id}"
            out.uplink_connected = session.uplink_connected

    return out


async def list_active(
    db: AsyncSession, scope: VillageScope, registry: LiveRegistry | None = None
) -> list[BroadcastOut]:
    """진행 중인 방송. village_admin 에게는 자기 마을 대상만 보여준다."""
    events = await _active_events(db)
    visible = [e for e in events if _visible_to(e, scope)]
    return [await _to_out(db, e, registry) for e in visible]


def _visible_to(event: BroadcastEvent, scope: VillageScope) -> bool:
    if scope.all_villages:
        return True
    if event.target_scope == TargetScope.VILLAGE.value and event.target_ids:
        # 대상 마을 중 하나라도 담당이면 보인다 — 다중 마을 방송은 관련된
        # 모든 village_admin 이 봐야 중지도 할 수 있다.
        return any(int(v) in scope.village_ids for v in event.target_ids)
    # device/zone/all 대상은 마을을 바로 알 수 없다. 보수적으로 감춘다.
    return False


async def get_broadcast(
    db: AsyncSession, event_id: int, scope: VillageScope, registry: LiveRegistry | None = None
) -> BroadcastOut:
    event = await db.get(BroadcastEvent, event_id)
    if event is None:
        raise BroadcastNotFound()
    if not _visible_to(event, scope):
        raise BroadcastNotFound()
    return await _to_out(db, event, registry)


# ── 파일 방송 ────────────────────────────────────────────────────────────
async def start_file_broadcast(
    db: AsyncSession,
    payload: FileBroadcastRequest,
    scope: VillageScope,
    publisher: MqttPublisher,
    *,
    user_id: int,
) -> BroadcastOut:
    audio = await file_service.get_file(db, payload.file_id)
    if not file_service.absolute_path(audio).exists():
        raise ApiError(
            "파일 원본이 디스크에 없습니다. 다시 업로드해 주세요.",
            code="FILE_MISSING_ON_DISK",
        )

    # 검사부터 이벤트 생성까지를 한 덩어리로 묶는다(아래 함수 주석 참고).
    await _lock_broadcast_start(db)

    macs = await _resolve_targets(
        db, target_scope=payload.target_scope, target_ids=payload.target_ids, scope=scope
    )
    await _assert_no_overlap(db, macs)

    job_id = await next_job_id(db)
    token = await file_service.issue_token(
        db, file_id=audio.id, job_id=job_id, ttl_sec=settings.download_token_ttl_sec
    )
    url = f"{settings.public_base_url.rstrip('/')}/dl/{token}"

    cmd = publisher.file_start_payload(
        job_id=job_id,
        size=audio.size_bytes,
        sha256=audio.sha256,
        url=url,
        file_name=audio.filename,
        store_flash=payload.store_flash,
        autoplay=payload.autoplay,
    )

    # 이력을 먼저 만든다. 발행이 실패해도 "시도했다"는 기록은 남아야 하고,
    # 단말 응답이 발행 직후 곧바로 와도 event_id 로 붙을 자리가 있어야 한다.
    event = BroadcastEvent(
        event_type=EventType.FILE_START.value,
        job_id=job_id,
        target_scope=payload.target_scope.value,
        target_ids=payload.target_ids,
        file_id=audio.id,
        triggered_by=user_id,
    )
    db.add(event)
    await db.flush()

    await publisher.publish_command(
        payload=cmd,
        target_scope=payload.target_scope,
        scope=scope,
        village_ids=_village_ids(payload.target_scope, payload.target_ids),
        macs=macs,
    )
    log.info("파일 방송 시작 job_id=%s 대상 %d대 file=%s", job_id, len(macs), audio.filename)

    return await _to_out(db, event)


async def stop_file_broadcast(
    db: AsyncSession,
    event_id: int,
    scope: VillageScope,
    publisher: MqttPublisher,
) -> BroadcastOut:
    """방송 중지.

    단말은 FILE_ABORT(USER_CANCEL) 또는 FILE_STOP_RESULT(NOT_ACTIVE) 로 답한다.
    둘 다 "요청 처리됨"이므로 서버는 응답을 기다리지 않고 ended_at 을 찍는다 —
    안 그러면 이미 꺼진 단말 때문에 방송이 영영 안 끝난 상태로 남는다.
    """
    event = await db.get(BroadcastEvent, event_id)
    if event is None:
        raise BroadcastNotFound()
    if not _visible_to(event, scope):
        raise BroadcastNotFound()
    if event.ended_at is not None:
        raise ApiError("이미 종료된 방송입니다.", code="BROADCAST_ALREADY_ENDED")

    macs = await device_service.macs_for_target(
        db,
        target_scope=event.target_scope,
        target_ids=event.target_ids,
        scope=scope,
        online_only=True,
    )

    if macs and event.job_id is not None and event.file_id is not None:
        cmd = publisher.file_stop_payload(job_id=event.job_id)
        try:
            await publisher.publish_command(
                payload=cmd,
                target_scope=TargetScope(event.target_scope),
                scope=scope,
                village_ids=_village_ids(TargetScope(event.target_scope), event.target_ids),
                macs=macs,
            )
        except Exception:  # noqa: BLE001
            # 발행이 실패해도 서버 상태는 종료로 정리한다. 안 그러면 겹침 검사가
            # 죽은 방송에 계속 걸려서 다음 방송을 못 하게 된다.
            log.exception("FILE_STOP 발행 실패 (이력은 종료 처리): event=%d", event_id)

    event.ended_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    log.info("파일 방송 종료 job_id=%s", event.job_id)
    return await _to_out(db, event)


# ── 실시간 방송 ──────────────────────────────────────────────────────────
def _village_ids(target_scope: TargetScope, target_ids: list[str]) -> list[int]:
    """발행에 넘길 마을 id 목록. 마을 대상이 아니면 빈 목록(발행부가 무시한다)."""
    if target_scope is not TargetScope.VILLAGE:
        return []
    return [int(v) for v in target_ids]


async def start_live_broadcast(
    db: AsyncSession,
    payload: LiveBroadcastRequest,
    scope: VillageScope,
    publisher: MqttPublisher,
    registry: LiveRegistry,
    *,
    user_id: int,
) -> BroadcastOut:
    """실시간 방송 시작.

    순서가 중요하다:
      1) 대상 해석 · 겹침 검사 — 시작하기 전에 거절할 건 거절한다
      2) Icecast source 연결 — 여기서 실패하면 아무것도 발행하지 않는다
      3) LIVE_START 발행 — 단말이 마운트로 붙는다

    2를 3보다 먼저 하는 이유: 마운트가 없는데 단말을 붙이면 404 를 받고
    단말마다 재시도 로직이 돌아간다. 소스를 먼저 세워두면 단말은 붙는 즉시
    (무음이라도) 스트림을 받는다.
    """
    await _lock_broadcast_start(db)

    macs = await _resolve_targets(
        db, target_scope=payload.target_scope, target_ids=payload.target_ids, scope=scope
    )
    await _assert_no_overlap(db, macs)

    session_id = await next_job_id(db)
    # 마운트는 job_id 하나로 정한다 — 다중 마을 방송은 경로에 마을을 담을 수 없다.
    mount = mount_path(session_id)
    url = stream_url(settings.icecast_public_base_url, mount)

    source = IcecastSource(mount)
    await source.start()
    if not source.is_connected:
        await source.stop()
        raise ApiError(
            f"Icecast 연결에 실패했습니다. {source.error or ''}".strip(),
            code="ICECAST_UNAVAILABLE",
        )

    event = BroadcastEvent(
        event_type=EventType.LIVE_START.value,
        job_id=session_id,
        target_scope=payload.target_scope.value,
        target_ids=payload.target_ids,
        triggered_by=user_id,
    )
    db.add(event)
    await db.flush()

    await registry.add(
        LiveSession(
            event_id=event.id,
            session_id=session_id,
            mount=mount,
            stream_url=url,
            macs=macs,
            source=source,
        )
    )

    try:
        await publisher.publish_command(
            payload=publisher.live_start_payload(job_id=session_id, stream_url=url),
            target_scope=payload.target_scope,
            scope=scope,
            village_ids=_village_ids(payload.target_scope, payload.target_ids),
            macs=macs,
        )
    except Exception:
        # 발행이 실패하면 아무 단말도 못 붙는다. 세워둔 소스를 정리하고
        # 이력도 끝난 것으로 표시한다 — 유령 세션이 겹침 검사를 막으면 안 된다.
        await registry.remove(session_id)
        event.ended_at = dt.datetime.now(dt.timezone.utc)
        await db.flush()
        raise

    log.info("실시간 방송 시작 session=%d mount=%s 대상 %d대", session_id, mount, len(macs))

    if settings.live_uplink_grace_sec > 0:
        asyncio.create_task(
            _watch_uplink(session_id, publisher, registry),
            name=f"live-uplink-watch-{session_id}",
        )

    return await _to_out(db, event, registry)


async def _watch_uplink(
    session_id: int, publisher: MqttPublisher, registry: LiveRegistry
) -> None:
    """오디오가 끊긴 실시간 방송을 서버가 직접 끝낸다.

    왜 "끊기면 기다린다"가 아니라 "끊기면 끝낸다"인가:

      · 단말은 스트림이 끊겨도 **재접속하지 않는다**(ESP32 정정 260824).
        단절을 감지하면 LIVE_READY status=2 를 올리고 IDLE 로 돌아가며,
        그 방송은 그 단말에서 영영 끝난다. 서버가 마운트를 붙들고 기다려도
        돌아올 단말이 없다.
      · 화면(useMicUplink)도 업링크가 끊기면 재연결하지 않고 "방송을 다시
        시작해 주세요"를 띄운다. 양쪽 다 회복 경로가 없다.
      · 그런데도 서버가 방송을 열어두면 ON AIR 인데 소리는 안 나가고,
        대상 단말이 겹침 검사에 묶여 재방송까지 막힌다.

    Icecast 가 먼저 마운트를 지워버리기 전에 우리가 LIVE_STOP 을 보내야 한다 —
    그래야 단말이 오류가 아니라 정상 종료로 정리한다. 그래서 icecast.xml 의
    source-timeout 은 이 유예시간보다 크게 잡혀 있다.

    기준은 "마지막 오디오 바이트 이후 경과 시간"이다. 한 번도 안 붙은 방송과
    붙었다 끊긴 방송을 같은 잣대로 잰다 — 결과가 같기 때문이다.
    """
    grace = settings.live_uplink_grace_sec
    while True:
        session = registry.get(session_id)
        if session is None:
            return  # 이미 종료됐다

        silent = session.silent_for_sec
        if silent < grace:
            await asyncio.sleep(min(grace - silent, 5.0) or 1.0)
            continue

        log.warning(
            "실시간 방송 자동 종료 session=%d — 오디오가 %.0f초간 끊겼다 (업링크 연결된 적 %s)",
            session_id, silent, "있음" if session.uplink_seen else "없음",
        )
        try:
            async with session_scope() as db:
                event = await db.get(BroadcastEvent, session.event_id)
                if event is None or event.ended_at is not None:
                    return
                await stop_live_broadcast(
                    db, event.id, VillageScope.for_super_admin(), publisher, registry
                )
        except Exception:  # noqa: BLE001 - 실패해도 화면의 무음 경고는 남는다
            log.exception("실시간 방송 자동 종료 실패 session=%d", session_id)
        return


async def stop_live_broadcast(
    db: AsyncSession,
    event_id: int,
    scope: VillageScope,
    publisher: MqttPublisher,
    registry: LiveRegistry,
) -> BroadcastOut:
    """실시간 방송 종료.

    LIVE_STOP 을 먼저 보내고 Icecast 소스를 닫는다. 순서를 뒤집으면 단말이
    스트림 끊김을 먼저 겪고 재연결을 시도한다.
    """
    event = await db.get(BroadcastEvent, event_id)
    if event is None:
        raise BroadcastNotFound()
    if not _visible_to(event, scope):
        raise BroadcastNotFound()
    if event.ended_at is not None:
        raise ApiError("이미 종료된 방송입니다.", code="BROADCAST_ALREADY_ENDED")

    session_id = event.job_id
    macs = await device_service.macs_for_target(
        db,
        target_scope=event.target_scope,
        target_ids=event.target_ids,
        scope=scope,
        online_only=True,
    )

    if macs and session_id is not None:
        try:
            await publisher.publish_command(
                payload=publisher.live_stop_payload(job_id=session_id),
                target_scope=TargetScope(event.target_scope),
                scope=scope,
                village_ids=_village_ids(TargetScope(event.target_scope), event.target_ids),
                macs=macs,
            )
        except Exception:  # noqa: BLE001
            log.exception("LIVE_STOP 발행 실패 (이력은 종료 처리): event=%d", event_id)

    if session_id is not None:
        await registry.remove(session_id)

    event.ended_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    log.info("실시간 방송 종료 session=%s", session_id)
    return await _to_out(db, event, registry)
