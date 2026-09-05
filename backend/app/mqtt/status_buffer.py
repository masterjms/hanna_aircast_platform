"""STATUS 수신 버퍼 — 주기 telemetry 를 모아서 한 트랜잭션에 쓴다 (A-2/A-3, 2026-09-02).

왜 필요한가:

    수신 루프는 메시지를 한 건씩 처리하고(`connection.py` 의 `async for`), 처리
    1건이 트랜잭션 1개였다. STATUS 하나에 쿼리가 세 개 나갔다 —
    캐시 upsert, `Device` 재조회, `CurrentConfig` 조회. 마지막 것은 전 단말이
    30초마다 **같은 전역 행 하나**를 다시 읽는 일이다.

    3000대 × 30초 = 100 msg/s. 메시지당 왕복이 서너 번이면 DB 왕복 지연이
    그대로 처리량 상한이 된다. 밀리기 시작하면 화면 상태가 실제보다 늦어지는
    식으로 조용히 나빠져서, 원인을 찾기 어렵다.

무엇을 바꿨나:

    STATUS 는 "이력"이 아니라 **캐시 갱신**이다(같은 단말의 최신값만 의미가 있다).
    그래서 MAC 별로 최신 것만 모았다가 주기적으로 한 번에 쓴다:

        · 다중 행 upsert 1회 + config 조회 1회 = **초당 트랜잭션 1개**
        · 단말이 3000대든 100대든 초당 쿼리 수가 같다(대수에 비례하지 않는다)

    결과(LIVE_READY·FILE_RESULT·OTA_STATUS)와 LWT 는 버퍼를 타지 않는다.
    드물고, 화면이 기다리는 값이고, 이력 행이라 합칠 수 없다.

안전장치:

    · `last_seen_at` 은 GREATEST 로 쓴다 — 버퍼가 늦게 flush 돼도 시각이
      뒤로 가지 않는다(결과 메시지가 그 사이 더 최근 값을 썼을 수 있다).
    · LWT 가 오면 그 MAC 의 대기분을 버린다. 안 버리면 죽었다고 기록한 뒤에
      낡은 STATUS 가 덮어써서 죽은 단말이 온라인으로 되살아난다.
    · 종료 시 남은 것을 flush 한다. 배포로 컨테이너가 교체돼도 마지막 1초를
      잃지 않는다.
    · `settings.status_flush_interval_sec` 가 0 이면 버퍼를 끄고 예전처럼
      메시지마다 바로 쓴다(운영 중 문제가 생기면 되돌릴 수 있는 스위치).
    · flush 가 실패하면 그 묶음은 버린다(다시 큐에 넣지 않는다). STATUS 는
      30초마다 다시 오는 캐시값이라 한 주기를 잃어도 스스로 복구되지만,
      되돌려 넣으면 DB 가 아픈 동안 대기열이 무한정 자란다.

실측 (2026-09-02, 로컬 Postgres·단말 3000대 한 주기):
    옛 경로 152 msg/s → 새 경로 11,928 msg/s. 30초 주기 점유율 65.7% → 0.84%.
    옛 경로는 3000대에 필요한 100 msg/s 를 겨우 넘기는 수준이었고, RDS 는
    네트워크 왕복이 더 붙어서 그 아래로 내려간다.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.village_token import village_tokens
from app.db import session_scope
from app.models.device import Device
from app.models.system import CurrentConfig
from app.mqtt.publisher import MqttPublisher

log = logging.getLogger(__name__)

#: 자동 복구를 방금 보낸 MAC → 시각. 같은 단말에 초당 몇 번씩 다시 쏘지 않게 한다.
#: 낡은 펌웨어처럼 영영 적용하지 않는 단말이 있으면 STATUS 마다 재발행이 나가는데,
#: 그게 3000대면 로그와 브로커가 지저분해진다.
_last_resync: dict[str, dt.datetime] = {}
_RESYNC_COOLDOWN_SEC = 60.0


def needs_resync(
    *,
    reported_village: str,
    expected_village: str,
    reported_version: Any,
    expected_version: int | None,
) -> bool:
    """단말이 echo 한 값이 서버 정본과 다른가 (사양 §4.3).

    브로커 retained 유실, 배정 시점의 연결 끊김처럼 "서버는 보냈다고 아는데
    단말은 못 받은" 상황을 STATUS 를 볼 때마다 알아채는 판정이다.
    """
    return reported_village != expected_village or reported_version != expected_version


def resync_allowed(mac: str, now: dt.datetime) -> bool:
    """쿨다운 통과 여부. 통과하면 발행 시각을 기록한다(다음 호출은 막힌다)."""
    last = _last_resync.get(mac)
    if last is not None and (now - last).total_seconds() < _RESYNC_COOLDOWN_SEC:
        return False
    _last_resync[mac] = now
    return True


async def publish_resync(
    publisher: MqttPublisher, *, mac: str, village_token: str, config_version: int
) -> None:
    """단말 CONFIG 재발행. 실패해도 삼킨다 — 다음 STATUS 에서 다시 시도된다."""
    log.info(
        "CONFIG 불일치 자동 복구 %s (village=%s, version=%d)", mac, village_token, config_version
    )
    try:
        await publisher.publish_device_config(
            mac=mac, village_token=village_token, config_version=config_version
        )
    except Exception:  # noqa: BLE001
        log.exception("CONFIG 자동 복구 발행 실패: %s", mac)


class StatusBuffer:
    """MAC 별 최신 STATUS 를 모았다가 주기적으로 한 트랜잭션에 쓴다.

    메모리 사용량은 단말 수에 유계다 — MAC 이 키라서 같은 단말이 아무리 자주
    보내도 항목 하나로 합쳐진다.
    """

    def __init__(self, *, interval_sec: float, max_pending: int = 20_000) -> None:
        self._interval = interval_sec
        self._max_pending = max_pending
        #: mac → (payload, seen_at). 나중 것이 앞의 것을 덮는다.
        self._pending: dict[str, tuple[dict[str, Any], dt.datetime]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False

    # ── 적재 ────────────────────────────────────────────────────────────
    def offer(self, mac: str, *, payload: dict[str, Any], seen_at: dt.datetime) -> None:
        """STATUS 한 건을 대기열에 넣는다. DB 를 만지지 않는다(마이크로초 단위)."""
        self._pending[mac] = (payload, seen_at)
        if len(self._pending) >= self._max_pending:
            # 이 정도면 flush 주기를 기다릴 게 아니라 지금 비워야 한다.
            self._wake.set()

    def discard(self, mac: str) -> None:
        """대기 중인 STATUS 를 버린다. LWT 처리가 먼저 이겨야 할 때 쓴다."""
        self._pending.pop(mac, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ── 수명주기 ────────────────────────────────────────────────────────
    async def start(self, publisher: MqttPublisher | None) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run(publisher), name="status-buffer")

    async def stop(self, publisher: MqttPublisher | None = None) -> None:
        """주기 태스크를 접고 남은 것을 마지막으로 쓴다."""
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.flush(publisher)
        except Exception:  # noqa: BLE001 - 종료 경로에서 예외를 올리지 않는다
            log.exception("종료 시 STATUS flush 실패 (최근 상태 일부 유실)")

    async def _run(self, publisher: MqttPublisher | None) -> None:
        while not self._stopping:
            # 주기가 되거나(TimeoutError), 대기분이 상한에 닿으면 깬다.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            self._wake.clear()
            try:
                await self.flush(publisher)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 한 번 실패해도 루프는 계속 돈다
                log.exception("STATUS flush 실패 (다음 주기에 재시도)")

    # ── 쓰기 ────────────────────────────────────────────────────────────
    async def flush(self, publisher: MqttPublisher | None) -> int:
        """대기분을 한 트랜잭션에 쓰고, CONFIG 불일치 단말에 재발행한다.

        반환값은 쓴 단말 수. 대기분을 먼저 통째로 떼어내므로(swap), 쓰는 동안
        들어온 STATUS 는 다음 주기로 넘어간다.
        """
        if not self._pending:
            return 0
        batch = self._pending
        self._pending = {}

        now = dt.datetime.now(dt.timezone.utc)
        rows = [
            {"mac": mac, "last_status": payload, "last_seen_at": seen_at}
            for mac, (payload, seen_at) in batch.items()
        ]

        async with session_scope() as db:
            stmt = pg_insert(Device).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Device.mac],
                set_={
                    # 시각이 뒤로 가지 않게 한다 — 결과 메시지가 그 사이 더 최근
                    # 값을 썼을 수 있다. GREATEST 는 NULL(신규 단말)을 무시한다.
                    "last_seen_at": func.greatest(
                        Device.last_seen_at, stmt.excluded.last_seen_at
                    ),
                    "last_status": stmt.excluded.last_status,
                },
            ).returning(Device.mac, Device.village_id)
            # 처음 보는 MAC 은 여기서 미배정 상태로 자동 등록된다(기존 동작과 같다).
            assigned = {
                mac: village_id
                for mac, village_id in (await db.execute(stmt)).all()
                if village_id is not None
            }

            if not assigned or publisher is None:
                return len(rows)

            # 전 단말이 같은 값을 읽던 조회를 flush 당 한 번으로 줄인 지점이다.
            config = await db.get(CurrentConfig, 1)
            expected_version = config.config_version if config is not None else None
            # 마을 id → MQTT 문자열(12자리 코드). 마을 수만큼이라 한 번의 IN 조회다.
            tokens = await village_tokens(db, set(assigned.values()))

        stale: list[tuple[str, str]] = []
        for mac, village_id in assigned.items():
            payload, _ = batch[mac]
            expected_village = tokens.get(village_id)
            if expected_village is None:
                continue
            if needs_resync(
                reported_village=str(payload.get("village_id") or ""),
                expected_village=expected_village,
                reported_version=payload.get("config_version"),
                expected_version=expected_version,
            ) and resync_allowed(mac, now):
                stale.append((mac, expected_village))

        for mac, token in stale:
            await publish_resync(
                publisher, mac=mac, village_token=token,
                config_version=expected_version or 1,
            )
        return len(rows)
