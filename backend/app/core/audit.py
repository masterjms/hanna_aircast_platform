"""관리 행위 감사 기록 미들웨어 (문제점 70번 10/15 ①, 2026-10-17).

/api 의 변경 요청(POST·PUT·PATCH·DELETE)을 응답이 나간 뒤 audit_events 에 한 줄씩 남긴다.
화면·라우터마다 끼워 넣지 않고 입구 한 곳에서 잡는 이유: 새 기능이 생겨도 빠지지 않는다.

- 누가: 요청의 토큰(sub·username·role). 토큰이 없거나 깨졌으면 비운 채 남긴다
  (시도 자체가 기록할 일).
- 무엇을: 아래 표의 행위 이름 + 경로의 대상(id·MAC). 표에 없는 경로는 「METHOD 경로」 그대로.
- 내용: JSON 본문 요약 — 비밀번호·토큰·키는 지우고 2KB 까지. 파일 올리기(multipart)는 크기만.
- 결과: HTTP 상태. 실패(4xx·5xx)도 남긴다.

로그인·로그아웃은 login_events 가 따로 남기므로 뺀다. 기록이 실패해도 요청에는 영향을 주지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import jwt

from app.config import settings
from app.db import session_scope
from app.models.audit import AuditEvent

log = logging.getLogger(__name__)

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: 로그인·로그아웃은 login_events 가, 월 점검은 그 라우터가 앵커를 담아 직접 남긴다.
_SKIP = frozenset({"/api/auth/login", "/api/auth/logout", "/api/audit/review"})
_BODY_LIMIT = 64 * 1024
_DETAIL_LIMIT = 2000
_SECRET_KEY = re.compile(r"pass|token|secret|key$|^key|credential|^code$|otp", re.IGNORECASE)

#: (METHOD, 경로 정규식, 행위 이름). 위에서부터 처음 맞는 것. 그룹 1 이 있으면 대상.
_ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    (m, re.compile(p), label)
    for m, p, label in [
        ("POST", r"^/api/auth/password$", "내 비밀번호 변경"),
        ("POST", r"^/api/users$", "계정 추가"),
        ("POST", r"^/api/users/(\d+)/temp-password$", "임시 비밀번호 발급"),
        ("POST", r"^/api/users/(\d+)/otp-reset$", "2단계 인증 초기화"),
        ("POST", r"^/api/auth/otp/setup$", "2단계 인증 등록 시작"),
        ("POST", r"^/api/auth/otp/enable$", "2단계 인증 켜기"),
        ("POST", r"^/api/auth/otp/disable$", "2단계 인증 끄기"),
        ("PATCH", r"^/api/users/(\d+)$", "계정 수정"),
        ("DELETE", r"^/api/users/(\d+)$", "계정 삭제"),
        ("POST", r"^/api/organizations$", "기관 추가"),
        ("PATCH", r"^/api/organizations/(\d+)$", "기관 수정·이동"),
        ("DELETE", r"^/api/organizations/(\d+)$", "기관 삭제"),
        ("POST", r"^/api/villages$", "지역 추가"),
        ("POST", r"^/api/villages/(\d+)/zones$", "구역 추가"),
        ("PATCH", r"^/api/villages/(\d+)$", "지역 수정·이동"),
        ("DELETE", r"^/api/villages/(\d+)$", "지역 삭제"),
        ("PATCH", r"^/api/zones/(\d+)$", "구역 수정"),
        ("DELETE", r"^/api/zones/(\d+)$", "구역 삭제"),
        ("POST", r"^/api/devices$", "단말 등록"),
        ("POST", r"^/api/devices/credential$", "단말 비밀번호 사전 발급"),
        ("POST", r"^/api/devices/([^/]+)/credential$", "단말 비밀번호 조회·재발급"),
        ("PATCH", r"^/api/devices/([^/]+)$", "단말 수정(배정·위치·별칭)"),
        ("DELETE", r"^/api/devices/([^/]+)$", "단말 삭제"),
        ("PUT", r"^/api/config$", "환경 설정 변경"),
        ("POST", r"^/api/ota/packages$", "OTA 패키지 올리기"),
        ("DELETE", r"^/api/ota/packages/(\d+)$", "OTA 패키지 삭제"),
        ("POST", r"^/api/ota/start$", "OTA 배포"),
        ("POST", r"^/api/files$", "방송 자료 올리기"),
        ("POST", r"^/api/files/tts$", "글로 음성 만들기"),
        ("DELETE", r"^/api/files/(\d+)$", "방송 자료 삭제"),
        ("POST", r"^/api/schedules$", "예약 추가"),
        ("PATCH", r"^/api/schedules/(\d+)$", "예약 수정"),
        ("DELETE", r"^/api/schedules/(\d+)$", "예약 삭제"),
        ("POST", r"^/api/broadcast/file/start$", "파일 방송 시작"),
        ("POST", r"^/api/broadcast/file/stop$", "파일 방송 중지"),
        ("POST", r"^/api/broadcast/live/start$", "실시간 방송 시작"),
        ("POST", r"^/api/broadcast/live/stop$", "실시간 방송 중지"),
    ]
]


def describe(method: str, path: str) -> tuple[str, str | None]:
    """(행위 이름, 대상). 표에 없으면 「METHOD 경로」."""
    for m, rx, label in _ROUTES:
        if m == method:
            hit = rx.match(path)
            if hit:
                return label, (hit.group(1) if hit.groups() else None)
    return f"{method} {path}"[:60], None


def redact(value: Any) -> Any:
    """비밀값 키를 「***」로. 중첩 dict·list 도."""
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value[:50]]
    return value


def summarize(
    body: bytes, content_type: str, length: int | None, query: str
) -> dict[str, Any] | None:
    detail: dict[str, Any] = {}
    if query:
        detail["query"] = query[:200]
    if "multipart/" in content_type:
        detail["upload_bytes"] = length
    elif body:
        try:
            parsed = redact(json.loads(body))
        except ValueError:
            parsed = None
        if parsed is not None:
            text = json.dumps(parsed, ensure_ascii=False)
            detail["body"] = (
                parsed if len(text) <= _DETAIL_LIMIT else {"_잘림": text[:_DETAIL_LIMIT]}
            )
    return detail or None


def _header(scope: dict[str, Any], name: bytes) -> str:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return ""


def client_ip(scope: dict[str, Any]) -> str | None:
    real = _header(scope, b"x-real-ip")
    if real:
        return real.strip()[:45]
    fwd = _header(scope, b"x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    client = scope.get("client")
    return client[0][:45] if client else None


def actor_of(scope: dict[str, Any]) -> tuple[int | None, str | None, str | None]:
    auth = _header(scope, b"authorization")
    if not auth.lower().startswith("bearer "):
        return None, None, None
    try:
        # 만료된 토큰도 「누가 시도했는지」는 남긴다 — 서명만 확인한다.
        p = jwt.decode(
            auth[7:].strip(), settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
        return int(p["sub"]), str(p.get("username") or "")[:50] or None, p.get("role")
    except Exception:  # noqa: BLE001 - 깨진 토큰은 「누구인지 모름」
        return None, None, None


async def record(**fields: Any) -> None:
    try:
        async with session_scope() as db:
            db.add(AuditEvent(**fields))
    except Exception:  # noqa: BLE001 - 기록 실패가 요청을 깨면 안 된다
        log.exception("감사 기록 실패: %s %s", fields.get("method"), fields.get("path"))


class AuditMiddleware:
    """순수 ASGI — 본문을 지나가는 그대로 엿보고(64KB 까지), 응답 상태를 잡아 끝난 뒤 남긴다."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        method = scope.get("method", "")
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or method not in _MUTATING
            or not path.startswith("/api/")
            or path in _SKIP
        ):
            await self.app(scope, receive, send)
            return

        content_type = _header(scope, b"content-type").lower()
        try:
            length = int(_header(scope, b"content-length") or 0)
        except ValueError:
            length = None
        peek = "json" in content_type and (length or 0) <= _BODY_LIMIT
        chunks: list[bytes] = []
        status_box = {"status": 500}

        async def receive_tee() -> Any:
            message = await receive()
            if peek and message.get("type") == "http.request":
                chunks.append(message.get("body", b""))
            return message

        async def send_tap(message: Any) -> None:
            if message.get("type") == "http.response.start":
                status_box["status"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive_tee, send_tap)
        finally:
            user_id, username, role = actor_of(scope)
            action, target = describe(method, path)
            await record(
                user_id=user_id,
                username=username,
                role=role,
                ip=client_ip(scope),
                method=method,
                path=path[:255],
                action=action,
                target=target[:100] if target else None,
                status=status_box["status"],
                detail=summarize(
                    b"".join(chunks),
                    content_type,
                    length,
                    scope.get("query_string", b"").decode("latin-1"),
                ),
            )
