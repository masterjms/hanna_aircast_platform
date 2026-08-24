"""단말 온라인 판정 — 한 곳에서만 정의한다.

판정이 두 벌이면 화면마다 다른 말을 한다(목록은 온라인인데 대시보드는 오프라인).
파이썬 판(is_online)과 SQL 판(online_clause)이 **반드시 같은 규칙**이어야 하므로
둘을 나란히 둔다.

device 모듈에 있던 것을 여기로 옮겼다 — org 모듈도 마을·구역의 온라인 대수를
세느라 필요한데, device 가 이미 org 를 import 하고 있어 순환이 되기 때문이다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, or_

from app.config import settings
from app.constants import DeviceState
from app.models.device import Device


def online_cutoff() -> dt.datetime:
    """이 시각보다 최근에 통신했으면 온라인."""
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        seconds=settings.device_online_threshold_sec
    )


def is_online(device: Device, cutoff: dt.datetime) -> bool:
    """온라인 판정.

    최근 통신만으로 판단하지 않는다. LWT 로 OFFLINE 이 확정된 단말은 마지막 통신이
    아무리 최근이어도 이미 끊긴 상태다 — 5분 임계를 기다릴 이유가 없다.
    """
    status = device.last_status or {}
    if status.get("state") == DeviceState.OFFLINE.value:
        return False
    return device.last_seen_at is not None and device.last_seen_at >= cutoff


def online_clause(cutoff: dt.datetime):
    """온라인 판정의 SQL 판. is_online() 과 반드시 같은 규칙이어야 한다."""
    return and_(
        Device.last_seen_at >= cutoff,
        or_(
            Device.last_status.is_(None),
            Device.last_status["state"].astext != DeviceState.OFFLINE.value,
        ),
    )
