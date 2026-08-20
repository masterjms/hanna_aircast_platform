"""Icecast 마운트 경로.

    /live/<마을 8자리>/<세션 id>          예) /live/00000001/43

마운트에 마을을 넣는 이유:
  · 운영/디버깅에서 URL 만 보고 어느 마을 방송인지 바로 안다.
  · 나중에 Icecast 쪽에서 마을 단위로 접근 제어·통계를 나눌 여지가 생긴다.

세션 id 를 넣는 이유:
  · 마을 A 와 마을 B 가 동시에 방송해야 한다. 마운트가 하나면 나중 방송이
    앞 방송을 덮어써 버린다. 세션마다 마운트가 갈라져야 동시 송출이 된다.

전체(all) 방송은 여러 마을에 걸쳐 있어 마을 번호를 정할 수 없다.
마을 토큰이 항상 8자리 숫자라 겹치지 않는 "all" 을 쓴다.
"""

from __future__ import annotations

from app.constants import TargetScope
from app.mqtt.topics import village_token

#: 전체 방송용 마운트 세그먼트. 8자리 숫자와 절대 겹치지 않는다.
ALL_SEGMENT = "all"


def mount_path(*, village_id: int | None, session_id: int) -> str:
    """마운트 경로. 항상 / 로 시작한다."""
    segment = ALL_SEGMENT if village_id is None else village_token(village_id)
    return f"/live/{segment}/{session_id}"


def village_for_target(target_scope: TargetScope, village_id: int | None) -> int | None:
    """마운트에 넣을 마을. 전체 방송이면 None.

    zone/device 대상도 결국 어느 한 마을에 속하므로 호출자가 그 마을을 넘긴다.
    """
    return None if target_scope is TargetScope.ALL else village_id


def stream_url(base_url: str, path: str) -> str:
    """공개 스트림 URL. 단말이 그대로 GET 한다."""
    return f"{base_url.rstrip('/')}{path}"
