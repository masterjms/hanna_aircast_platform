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

from sqlalchemy import func, select, text
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
from app.models.system import CurrentConfig
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

#: 신형식(2026-08-27~) — 성패를 `ok` 불리언 하나가 정한다(사양 §5.4).
#: FILE_RESULT 는 FILE_END/FILE_ABORT/FILE_STOP_RESULT 셋을 대체했고,
#: LIVE_RESULT 는 라이브 종료 결과(정상 종료 ok=true STOPPED_BY_SERVER)다.
_OK_FIELD_RESULTS = {"FILE_RESULT", "LIVE_RESULT", "OTA_RESULT", "LIVE_READY"}

#: 구형식 호환 — 신형식 이전 펌웨어와 목 단말이 아직 보낸다.
#: LIVE_READY 의 status 판정은 _live_ready_ok() 가 함께 처리한다.
_SUCCESS_RESULTS = {"FILE_END"}
_FAILURE_RESULTS = {"FILE_ABORT", "FILE_STOP_RESULT"}
#: (구형식) LIVE_READY.status — 0 만 준비 완료(1=TIMEOUT, 2=ABORT, 3=FAIL/BUSY).
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
    text = str(
        payload.get("code") or payload.get("reason") or payload.get("fail_reason") or ""
    )
    # 정상 코드는 표시하지 않는다 — 매 행에 "OK" 가 붙으면 실패 사유가 묻힌다.
    if text in ("OK", "STOPPED_BY_SERVER", "0"):
        return None
    return text or None


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


def _phase_of(event: BroadcastEvent, results: list[DeviceResultOut]) -> str:
    """서버가 보는 방송 국면. 화면 머리말에 그대로 쓴다.

    "시작됐다·끝났다"를 서버가 어느 시점에 보는지가 여기 한 곳에 있다(문제점 10번).
      라이브: 준비 중(LIVE_READY 대기) → 송출 중 → 중지 중(LIVE_RESULT 대기) → 종료
      파일  : 전송 중(FILE_RESULT 대기) → 재생 중 | 저장 완료 → 중지 중 → 종료
    """
    if event.ended_at is not None:
        return "종료"
    if event.stop_requested_at is not None:
        return "중지 중"
    expected = event.expected_count or 0
    if event.event_type.startswith("LIVE"):
        ready = sum(1 for r in results if r.ok is True)
        return "송출 중" if expected and ready >= expected else "준비 중"
    if event.playing_since is not None:
        return "재생 중"
    reported = sum(1 for r in results if r.result_type in TERMINAL_RESULTS)
    if expected and reported >= expected and event.autoplay is False:
        return "저장 완료"
    return "전송 중"


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

    # 발행 시점에 명령을 보낸 대수가 있으면 그걸 쓴다 — 진행률의 분모가 방송 중에
    # 흔들리지 않아야 한다(단말이 꺼지거나 배정이 바뀌면 100%가 영영 안 된다).
    if event.expected_count:
        out.target_count = event.expected_count
    # 진행 중일 때만 센다. 끝난 방송은 응답 수가 곧 결과다.
    elif event.ended_at is None:
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
            select(DeviceEvent, Device.label, Device.last_status)
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
    #: 단말별 최근 STATUS 의 live 값. RECONNECTING = 방송은 사는데 무음(사양 §5).
    live_state: dict[str, str | None] = {}
    for de, label, last_status in rows:
        live_state[de.mac] = (last_status or {}).get("live")
        if de.result_type in TELEMETRY_RESULTS:
            telemetry[de.mac] = de
            latest.setdefault(de.mac, (de, label))
        else:
            latest[de.mac] = (de, label)

    results: list[DeviceResultOut] = []
    for mac, (de, label) in latest.items():
        payload = de.payload or {}
        ok: bool | None = None
        if de.result_type in _OK_FIELD_RESULTS and "ok" in payload:
            # 신형식: ok 하나가 성패를 정한다. code 는 사유일 뿐 판정을 뒤집지 않는다
            # (사양 §5.4 규칙 2). STOPPED_BY_SERVER 도 ok=true = 정상 종료다.
            ok = bool(payload["ok"])
        elif de.result_type in _SUCCESS_RESULTS:
            # 구형식: verify_ok 가 있으면 그 값을 믿는다(sha256 검증 결과).
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
                # 진행 중인 라이브에서만 의미가 있다 — 끝난 방송의 live 는 노이즈다.
                live=live_state.get(mac) if event.ended_at is None else None,
                stats=_stats_text(stats.payload or {}) if stats is not None else None,
                received_at=de.received_at,
            )
        )
    results.sort(key=lambda r: (r.ok is not False, r.mac))

    out.results = results
    out.phase = _phase_of(event, results)

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
#: 단말 파일 수신 상한 (사양 §11, 2026-08-30). 넘으면 단말이 FILE_META 단계에서
#: 거절해 다운로드조차 시작되지 않는다. 현재 24kbps 기준 약 14분 33초 분량이다.
FILE_MAX_BYTES = 2_621_440  # 2.5 MiB

#: 방송 길이 약속 (사양 §11). 단말 재생 워치독이 10분 30초에서 끊으므로
#: 이보다 긴 파일은 크기가 상한 안이어도 뒷부분이 나오지 않는다.
FILE_MAX_DURATION_SEC = 600

#: 라이브 인코딩 비트레이트(사양 고정, opus 24kbps). 단말이 실제 전송 바이트 수를
#: 보고하지 않아서(통신 사양에 그런 필드가 없다), 트래픽 추정에 이 값을 쓴다.
LIVE_BITRATE_BYTES_PER_SEC = 24_000 // 8


def estimate_live_bytes(duration_sec: float, recipient_count: int) -> int:
    """방송 시간 × 비트레이트 × 수신 단말 수. 실측이 아니라 추정치다."""
    return round(max(duration_sec, 0.0) * LIVE_BITRATE_BYTES_PER_SEC) * max(recipient_count, 0)


def estimate_file_bytes(size_bytes: int, recipient_count: int) -> int:
    """파일 크기 × 수신 단말 수(단말마다 한 번씩 내려받는다). 실측이 아니라 추정치다."""
    return max(size_bytes, 0) * max(recipient_count, 0)


async def _recipient_count(db: AsyncSession, event_id: int) -> int:
    """이 방송에 응답을 남긴 단말 수(중복 제거). 바이트 추정의 분모다."""
    return await db.scalar(
        select(func.count(func.distinct(DeviceEvent.mac))).where(
            DeviceEvent.event_id == event_id
        )
    ) or 0


#: 파일 재생 종료 판정 여유(초). 단말의 재생 시작 지연·디코더 버퍼를 덮는다.
PLAYBACK_TAIL_SEC = 5.0

#: "이 방송에서 이 단말은 끝났다"를 뜻하는 결과 타입 (통신 사양 §5.4).
#: LIVE_READY 는 준비 결과라 여기 없다 — 준비됐다고 방송이 끝난 게 아니다.
TERMINAL_RESULTS = frozenset({"FILE_RESULT", "LIVE_RESULT"})

#: 살아 있는 LIVE 세션(Icecast source). 기동 때 main.py 가 넣어 준다.
#: end_event 가 라이브를 끝낼 때 여기서 스트림을 닫는다 — "종료 확정"과 "스트림
#: 닫기"를 한 곳에 묶어 순서가 어긋날 여지를 없앤다.
_live_registry: LiveRegistry | None = None


def set_live_registry(registry: LiveRegistry | None) -> None:
    global _live_registry  # noqa: PLW0603 - 프로세스에 하나뿐인 자원이다
    _live_registry = registry


#: 최근에 종료 처리한 job_id. 정지 뒤에도 그 방송의 LIVE_STATS 가 한 번 더 도착할
#: 수 있는데(실기 2026-09-03: 정지 보고 367ms 뒤), 그걸 적재하면 끝난 방송이 잠깐
#: 되살아나 보인다. 결과(LIVE_RESULT 등)는 이력이라 남기고, 주기 telemetry 만 버린다.
#: 크기를 묶어 둔다 — 재시작하면 비지만, 옛 job 의 늦은 telemetry 한 줄은 무해하다.
_ENDED_JOBS_MAX = 512
ENDED_JOBS: dict[int, None] = {}


def mark_job_ended(job_id: int | None) -> None:
    if job_id is None:
        return
    ENDED_JOBS[job_id] = None
    while len(ENDED_JOBS) > _ENDED_JOBS_MAX:
        del ENDED_JOBS[next(iter(ENDED_JOBS))]


def is_job_ended(job_id: int | None) -> bool:
    return job_id is not None and job_id in ENDED_JOBS


async def _responded_count(db: AsyncSession, event_id: int) -> int:
    """종료 결과를 보낸 단말 수(중복 제거)."""
    return await db.scalar(
        select(func.count(func.distinct(DeviceEvent.mac))).where(
            DeviceEvent.event_id == event_id,
            DeviceEvent.result_type.in_(TERMINAL_RESULTS),
        )
    ) or 0


async def end_event(db: AsyncSession, event: BroadcastEvent, *, reason: str) -> None:
    """방송을 종료로 확정한다 — ended_at 과 전송량 추정치를 함께 찍는다.

    종료 경로가 여럿이라(사용자 중지·자연 종료·응답 대기 만료·업링크 끊김)
    한 군데로 모은다. 여기 안 거치면 bytes_estimated 가 비는 방송이 생긴다.
    """
    if event.ended_at is not None:
        return
    ended = dt.datetime.now(dt.timezone.utc)
    recipients = await _recipient_count(db, event.id)

    if event.event_type.startswith("LIVE"):
        duration = (ended - event.triggered_at).total_seconds()
        event.bytes_estimated = estimate_live_bytes(duration, recipients)
    elif event.file_id is not None:
        size = await db.scalar(select(File.size_bytes).where(File.id == event.file_id))
        if size is not None:
            event.bytes_estimated = estimate_file_bytes(size, recipients)

    event.ended_at = ended
    await db.flush()
    mark_job_ended(event.job_id)
    log.info(
        "방송 종료 job_id=%s (%s) — 응답 %d/%s대",
        event.job_id, reason, recipients, event.expected_count or "?",
    )

    # 라이브는 여기서 스트림을 닫는다 — 종료가 확정된 뒤에야 닫는다.
    # LIVE_STOP 을 보낸 뒤 단말 응답을 기다리는 동안 mount·source 를 살려 두는 것이
    # 단말 요청(2026-09-03 §1)의 핵심이다: 스트림이 먼저 끊기면 단말은 "끊긴 건지
    # 끝난 건지" 판단해야 하고, 그 판단이 어긋나 정지한 단말이 다시 붙었다.
    if event.event_type.startswith("LIVE") and event.job_id is not None and _live_registry:
        await _live_registry.remove(event.job_id)


async def finish_if_all_reported(db: AsyncSession, job_id: int) -> bool:
    """단말 종료 결과가 도착할 때마다 부른다. 다 왔으면 방송을 끝낸다.

    이게 없으면 파일 방송은 사람이 "중지"를 누를 때까지 영원히 진행 중이다 —
    단말에서는 이미 재생이 끝났는데 화면만 ON AIR 로 남는다(문제점 3번).

    중지 대기 중(stop_requested_at)인 방송도 여기서 조기 종료된다. 대기 시간을
    다 쓰지 않고 마지막 단말이 답한 순간 끝난다.

    ⚠ 세션을 새로 열지 않고 호출부 것을 받는다. 방금 넣은 DeviceEvent 가 아직
      커밋 전이라, 다른 세션에서는 그 행이 안 보여서 영원히 한 대가 모자라다.
    """
    event = (
        await db.execute(
            select(BroadcastEvent).where(
                BroadcastEvent.job_id == job_id, BroadcastEvent.ended_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if event is None or not event.expected_count:
        # 대상 대수를 모르면 셀 수 없다(구버전 이력). 시간 기반 종료에 맡긴다.
        return False
    if await _responded_count(db, event.id) < event.expected_count:
        return False

    # 라이브의 LIVE_RESULT, 중지 요청 뒤의 응답, 저장만 하는 파일은 "전원 응답 = 끝".
    if (
        event.event_type.startswith("LIVE")
        or event.stop_requested_at is not None
        or event.autoplay is False
    ):
        await end_event(db, event, reason="전 단말 응답 완료")
        return True

    # 재생하는 파일 방송: FILE_RESULT ok=true 는 "저장 끝, 지금부터 재생"이다
    # (문제점 10번, 단말 요청 2026-09-03 §2.3). 여기서 끝내면 스피커가 나오는 중에
    # 화면은 "종료"가 된다. 재생 중으로 넘기고 재생 길이 뒤에 끝낸다 — 마지막 단말이
    # 가장 늦게 시작하니 그 시각 기준이면 전원이 끝난 뒤다.
    if event.playing_since is not None:
        return False  # 이미 재생 타이머가 걸려 있다(늦은 중복 결과)
    event.playing_since = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    duration = await db.scalar(select(File.duration_sec).where(File.id == event.file_id))
    # 길이를 못 잰 파일(ffprobe 없음)은 사양 상한 10분으로 본다 — 짧게 잡아 재생 중에
    # 끊는 것보다 길게 잡아 조금 늦게 끝내는 편이 낫다.
    play_sec = float(duration) if duration is not None else float(FILE_MAX_DURATION_SEC)
    asyncio.create_task(
        _force_end_after(event.id, play_sec + PLAYBACK_TAIL_SEC, reason="재생 완료"),
        name=f"file-playback-end-{job_id}",
    )
    log.info("파일 방송 재생 시작 job_id=%s — %.0f초 뒤 종료 예정", job_id, play_sec)
    return False


async def _force_end_after(
    event_id: int, wait_sec: float, *, reason: str, only_if_missing: bool = False
) -> None:
    """기다렸다가, 그때도 안 끝났으면 종료로 확정한다.

    중지 응답 대기(문제점 4·5번)·파일 저장 완료 상한(3번)·파일 재생 종료(10번)가
    같은 모양이라 하나로 쓴다. 먼저 전 단말이 답하면 finish_if_all_reported 가
    이미 끝냈고, 여기서는 아무 일도 하지 않는다.

    only_if_missing: 응답 안 한 단말이 있을 때만 끝낸다. 파일 저장 대기 상한이
    이걸 쓴다 — 전원이 저장을 마치고 재생 중인 방송을 "저장 대기 만료"로 끊으면
    안 되기 때문이다(그 방송은 재생 타이머가 따로 끝낸다).
    """
    try:
        await asyncio.sleep(wait_sec)
        async with session_scope() as db:
            event = await db.get(BroadcastEvent, event_id)
            if event is None or event.ended_at is not None:
                return
            missing = (event.expected_count or 0) - await _responded_count(db, event.id)
            if missing <= 0 and only_if_missing:
                return
            if missing > 0:
                log.warning(
                    "방송 %d: %d대가 %s초 안에 응답하지 않아 종료로 확정한다 (%s)",
                    event_id, missing, wait_sec, reason,
                )
            await end_event(db, event, reason=reason)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 종료 확정 실패가 워커를 죽이면 안 된다
        log.exception("방송 종료 확정 실패: event=%d", event_id)


async def _config_int(db: AsyncSession, field: str, default: int) -> int:
    """current_config 의 정수 설정 하나. 행이 없으면(첫 기동) 기본값."""
    config = await db.get(CurrentConfig, 1)
    return default if config is None else int(getattr(config, field))


async def close_orphaned_events(db: AsyncSession) -> int:
    """서버 재시작 뒤 남은 '진행 중' 방송을 정리한다 (A-8, 2026-09-02).

    LiveRegistry 는 프로세스 메모리에만 있다 — 재시작하면 라이브 세션도, 무음
    워치독(_watch_uplink) asyncio 태스크도 통째로 사라진다. 파일 방송은 애초에
    자동 종료 경로가 없다(수동 stop 뿐). 둘 다 프로세스가 죽으면 ended_at 이
    영원히 NULL 로 남는다.

    남겨두면 겹침 검사(BroadcastOverlap)가 그 대상을 계속 "방송 중"으로 보고
    새 방송을 막는다 — 재배포할 때마다 누적된다.

    바이트 추정치는 채우지 않는다. triggered_at 부터 지금까지를 실제 방송
    시간으로 계산하면(서버가 몇 시간 떠 있다 재시작했을 수도 있다) 트래픽이
    크게 부풀려진다 — 신뢰할 수 없는 값보다는 없는 편이 낫다.
    """
    rows = (
        await db.execute(select(BroadcastEvent).where(BroadcastEvent.ended_at.is_(None)))
    ).scalars().all()
    if not rows:
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    for event in rows:
        log.warning(
            "고아 방송 정리 id=%d type=%s job_id=%s 시작=%s",
            event.id, event.event_type, event.job_id, event.triggered_at,
        )
        event.ended_at = now
    await db.flush()
    return len(rows)


def _validate_file_for_broadcast(size_bytes: int, duration_sec: float | None) -> None:
    """발행 전 파일 검사. 단말이 거절하거나 잘려 나갈 파일을 서버에서 먼저 끊는다.

    여기서 안 막으면 현장에서는 "방송이 안 나간다" 또는 "10분에서 뚝 끊긴다"로만
    보이고, 단말 로그를 열어야 원인이 나온다.
    """
    if size_bytes > FILE_MAX_BYTES:
        raise ApiError(
            f"파일이 단말 상한(2.5MB)을 넘습니다({size_bytes / 1048576:.1f}MB). "
            "24kbps 로 다시 인코딩하거나 나눠서 올려 주세요.",
            code="FILE_TOO_LARGE",
        )
    if duration_sec is not None and duration_sec > FILE_MAX_DURATION_SEC:
        minutes = duration_sec / 60
        raise ApiError(
            f"방송은 10분을 넘길 수 없습니다(이 파일 {minutes:.1f}분). "
            "단말이 10분 30초에서 재생을 끊어 뒷부분이 나가지 않습니다.",
            code="FILE_TOO_LONG",
        )


async def start_file_broadcast(
    db: AsyncSession,
    payload: FileBroadcastRequest,
    scope: VillageScope,
    publisher: MqttPublisher,
    *,
    user_id: int,
) -> BroadcastOut:
    audio = await file_service.get_file(db, payload.file_id)
    _validate_file_for_broadcast(
        audio.size_bytes, float(audio.duration_sec) if audio.duration_sec is not None else None
    )
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
        # 화면에 보이는 이름(한글 가능)이 아니라 단말이 저장할 수 있는 이름을 보낸다.
        # 한글을 그대로 보내면 단말에서 밑줄만 남고 파일끼리 구분이 사라진다(§11.2).
        file_name=file_service.device_file_name(audio.filename, audio.id),
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
        # 종료 판정의 분모. 지금 명령을 보낸 대수를 그대로 박아둔다.
        expected_count=len(macs),
        # 저장만 하는 방송(autoplay=False)은 FILE_RESULT 가 곧 끝이고, 재생하는
        # 방송은 그 뒤 재생 길이만큼 더 이어진다 — finish_if_all_reported 가 가른다.
        autoplay=payload.autoplay,
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

    # 단말은 파일을 받아 검증·저장까지 끝내면 FILE_RESULT ok=true 를 보내고 그때
    # 재생을 시작한다(단말 요청 2026-09-03 §2.3 — 재생 완료 신호가 아니다). 전 단말이
    # 보내면 finish_if_all_reported 가 "재생 중"으로 넘기고 재생 길이 뒤에 끝낸다.
    # 도중에 꺼진 단말이 있으면 그 신호가 영영 안 오므로 상한을 둔다. 저장이 느려서
    # (LittleFS 80~100KB/s, 3MB 면 40초) 짧게 끊으면 정상 동작을 실패로 본다 — 단말
    # 자체 포기 시간(120초)이 기본이다. 전원이 응답했으면 이 워치독은 손대지 않는다
    # (재생 중인 방송을 저장 대기 만료로 끊으면 안 된다).
    wait_sec = await _config_int(db, "file_wait_sec", 120)
    asyncio.create_task(
        _force_end_after(
            event.id, wait_sec, reason="저장 완료 응답 대기 시간 초과", only_if_missing=True
        ),
        name=f"file-broadcast-end-{job_id}",
    )

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
    if event.stop_requested_at is not None:
        raise ApiError(
            "이미 중지를 요청했습니다. 단말 응답을 기다리는 중입니다.",
            code="BROADCAST_STOP_PENDING",
        )

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

    # 예전에는 여기서 바로 ended_at 을 찍었다. 단말이 실제로 멈췄는지 확인하지 않고
    # 화면만 "중지됨"이 되는 게 문제였다(문제점 4번). 이제 중지 요청 시각만 남기고
    # 단말의 FILE_RESULT 를 기다린다 — 다 오면 그 순간, 안 오면 대기 시간 뒤에 끝난다.
    wait_sec = await _config_int(db, "file_wait_sec", 120)
    event.stop_requested_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    log.info("파일 방송 중지 요청 job_id=%s — 단말 응답 %d초 대기", event.job_id, wait_sec)
    asyncio.create_task(
        _force_end_after(event.id, wait_sec, reason="중지 응답 대기 시간 초과"),
        name=f"file-stop-wait-{event.job_id}",
    )
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
        # 종료 판정의 분모. 지금 명령을 보낸 대수를 그대로 박아둔다.
        expected_count=len(macs),
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
            payload=publisher.live_start_payload(
                job_id=session_id,
                stream_url=url,
                record_flash=payload.record_flash,
                # 단말은 이 값 + 5초까지 기다렸다가 LIVE_READY 를 보낸다(§3.1).
                # 화면의 "준비 지연" 기준도 같은 설정에서 +5 로 계산한다.
                ready_timeout_sec=await _config_int(db, "live_ready_timeout_sec", 30),
            ),
            target_scope=payload.target_scope,
            scope=scope,
            village_ids=_village_ids(payload.target_scope, payload.target_ids),
            macs=macs,
        )
    except Exception:
        # 발행이 실패하면 아무 단말도 못 붙는다. 세워둔 소스를 정리하고
        # 이력도 끝난 것으로 표시한다 — 유령 세션이 겹침 검사를 막으면 안 된다.
        # end_event 가 레지스트리에서 세션을 빼고 소스를 닫는다.
        await end_event(db, event, reason="발행 실패")
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
        if session.stopping:
            # LIVE_STOP 을 보내고 응답을 기다리는 중이다. 마이크는 이미 끊겼으니
            # 무음이 쌓이지만, 그건 정지 절차의 일부다 — 여기서 또 중지하면
            # BROADCAST_STOP_PENDING 만 난다. 종료는 end_event 가 확정한다.
            return

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
    """실시간 방송 중지 요청.

    순서가 전부다(단말 요청 2026-09-03 §1):

        LIVE_STOP 발행 → 단말별 LIVE_RESULT 대기(최대 live_stop_wait_sec) → 스트림 닫기

    스트림은 여기서 닫지 않는다. 종료가 확정될 때 end_event 가 닫는다 — 전 단말이
    답한 순간이거나, 대기 시간이 지난 뒤다. 그 사이 mount·source 는 살아 있다.

    예전에는 LIVE_STOP 과 스트림 닫기가 거의 동시에 나갔고, 그래서 단말이 "끊긴
    건지 끝난 건지"를 판단해야 했다. 판단이 어긋나면 정지한 단말이 다시 붙었다
    (실기 2026-09-02: 정지 571ms 뒤 재접속). 정지 명령이 먼저 도착하면 단말은
    멀쩡한 스트림에서 스스로 끊는다 — 해석할 일이 없다.
    """
    event = await db.get(BroadcastEvent, event_id)
    if event is None:
        raise BroadcastNotFound()
    if not _visible_to(event, scope):
        raise BroadcastNotFound()
    if event.ended_at is not None:
        raise ApiError("이미 종료된 방송입니다.", code="BROADCAST_ALREADY_ENDED")
    if event.stop_requested_at is not None:
        raise ApiError(
            "이미 중지를 요청했습니다. 단말 응답을 기다리는 중입니다.",
            code="BROADCAST_STOP_PENDING",
        )

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

    # 스트림은 아직 닫지 않는다. 세션에 "중지 중" 표시만 해 두면 무음 워치독이
    # 비켜 준다(마이크가 끊겨 무음이 쌓여도 정지 절차의 일부다).
    if session_id is not None:
        session = registry.get(session_id)
        if session is not None:
            session.stopping = True

    # 단말의 LIVE_RESULT 를 기다린다(문제점 5번). 다 오면 그 순간 끝나고,
    # 못 받은 단말이 있어도 대기 시간이 지나면 종료로 확정한다. 실측 1.5초라
    # 10초는 여유가 크지만, 타임아웃은 상한이지 고정 대기가 아니다.
    wait_sec = await _config_int(db, "live_stop_wait_sec", 10)
    event.stop_requested_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    log.info("실시간 방송 중지 요청 session=%s — 단말 응답 %d초 대기", session_id, wait_sec)
    asyncio.create_task(
        _force_end_after(event.id, wait_sec, reason="중지 응답 대기 시간 초과"),
        name=f"live-stop-wait-{session_id}",
    )
    return await _to_out(db, event, registry)
