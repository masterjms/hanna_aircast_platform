"""Icecast 마운트 경로.

    /live/<job_id>          예) /live/141

job_id 하나만 쓴다. 예전에는 /live/<마을8자리>/<job_id> 였는데, 마을 여러 곳을
한 방송으로 묶는 다중 대상이 생기면서 경로에 마을을 담을 수 없게 됐다.
job_id 는 전역 유일이라 그것만으로 마운트가 겹치지 않고, 단일/다중/전체가
전부 한 규칙이라 특례가 없다.

"어느 마을 방송인가"는 URL 이 아니라 이력(broadcast_events.target_ids)이 답한다.

세션마다 마운트를 나누는 이유는 그대로다:
  · 마을 A 와 B 가 동시에 방송해야 한다 — Icecast 는 마운트당 소스 1개라
    마운트가 갈라져야 동시 송출이 된다(실측: 두 번째 소스는 403).
  · 단말이 LIVE_STOP 을 놓치고 이전 마운트에 붙어 있어도, 다음 방송은
    다른 마운트라 그 단말에 들리지 않는다.

단말은 LIVE_START.stream_url 문자열을 그대로 쓰므로 경로 구조는 서버 재량이다
(ESP32_요청_LIVE_START_stream_url_반영_260824.md §3.2).
"""

from __future__ import annotations


def mount_path(job_id: int) -> str:
    """마운트 경로. 항상 / 로 시작한다."""
    return f"/live/{job_id}"


def stream_url(base_url: str, path: str) -> str:
    """공개 스트림 URL. 단말이 그대로 GET 한다."""
    return f"{base_url.rstrip('/')}{path}"
