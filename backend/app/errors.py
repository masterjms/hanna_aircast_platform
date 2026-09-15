"""API 에러 규약.

응답은 항상 이 모양이다:
    {"error": {"code": "DEVICE_NOT_FOUND", "message": "...", "detail": {...}}}

code 는 프론트가 분기할 수 있는 안정적인 식별자다. message 는 사람이 읽는 한국어이고
문구는 언제든 바뀔 수 있으니 프론트가 문자열 매칭을 하면 안 된다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """도메인 에러의 베이스. 서비스 계층에서 이걸 던지면 핸들러가 응답으로 바꾼다."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"
    message: str = "잘못된 요청입니다."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.detail = detail or {}
        super().__init__(self.message)

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            body["detail"] = self.detail
        return JSONResponse(status_code=self.status_code, content={"error": body})


# ── 401 / 403 ────────────────────────────────────────────────────────────
class Unauthorized(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    message = "로그인이 필요합니다."


class InvalidCredentials(Unauthorized):
    code = "INVALID_CREDENTIALS"
    message = "아이디 또는 비밀번호가 올바르지 않습니다."


class AccountExpired(Unauthorized):
    """사용 기간이 끝난 계정(문제점 26번). 정리 작업이 지우기 전에도 막는다."""

    code = "ACCOUNT_EXPIRED"
    message = "사용 기간이 끝난 계정입니다. 관리자에게 기간 연장을 요청하세요."


class Forbidden(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "권한이 없습니다."


class SuperAdminRequired(Forbidden):
    code = "SUPER_ADMIN_REQUIRED"
    message = "최고 관리자만 사용할 수 있는 기능입니다."


class OrgAdminRequired(Forbidden):
    code = "ORG_ADMIN_REQUIRED"
    message = "기관 관리자 이상만 할 수 있습니다."


class TierTooLow(Forbidden):
    """자기 계층 이상의 계정을 만들거나 고치려 할 때."""

    code = "TIER_TOO_LOW"
    message = "자기보다 낮은 계층의 계정만 관리할 수 있습니다."


class OrganizationOutOfScope(Forbidden):
    code = "ORGANIZATION_OUT_OF_SCOPE"
    message = "관할 밖 기관입니다."


class VillageOutOfScope(Forbidden):
    code = "VILLAGE_OUT_OF_SCOPE"
    message = "담당 마을이 아닙니다."


# ── 404 ──────────────────────────────────────────────────────────────────
class NotFound(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "대상을 찾을 수 없습니다."


class DeviceNotFound(NotFound):
    code = "DEVICE_NOT_FOUND"
    message = "등록되지 않은 단말입니다."


class VillageNotFound(NotFound):
    code = "VILLAGE_NOT_FOUND"
    message = "존재하지 않는 마을입니다."


class ZoneNotFound(NotFound):
    code = "ZONE_NOT_FOUND"
    message = "존재하지 않는 구역입니다."


class OrganizationNotFound(NotFound):
    code = "ORGANIZATION_NOT_FOUND"
    message = "존재하지 않는 기관입니다."


class UserNotFound(NotFound):
    code = "USER_NOT_FOUND"
    message = "존재하지 않는 계정입니다."


# ── 409 ──────────────────────────────────────────────────────────────────
class Conflict(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "현재 상태와 충돌합니다."


class OrganizationInUse(Conflict):
    code = "ORGANIZATION_IN_USE"
    message = "소속 마을이나 계정이 있는 기관은 삭제할 수 없습니다. 먼저 옮기거나 지우세요."


class ScheduleTargetInUse(Conflict):
    """자동방송이 대상으로 가리키는 기관·마을·단말 삭제(2026-09-15 시나리오 검증).

    지우면 스케줄이 대상 없는 규칙으로 남아 매번 조용히 건너뛰고, 기관 관리자 화면에서는
    보이지도 않았다. 파일함의 FILE_IN_USE 와 같은 원칙 — 어느 스케줄인지 대 주고 막는다.
    """

    code = "SCHEDULE_TARGET_IN_USE"
    message = "자동방송 스케줄이 대상으로 쓰고 있어 삭제할 수 없습니다."


class DuplicateUsername(Conflict):
    code = "DUPLICATE_USERNAME"
    message = "이미 사용 중인 아이디입니다."


class DeviceAlreadyExists(Conflict):
    code = "DEVICE_ALREADY_EXISTS"
    message = "이미 등록된 단말입니다."


class BroadcastOverlap(Conflict):
    """대상 단말이 이미 다른 방송에 잡혀 있을 때. detail 에 겹치는 세션을 담는다.

    서버는 진행 중인 방송을 자동으로 끊지 않는다 — 사용자가 판단하게 한다.
    """

    code = "BROADCAST_OVERLAP"
    message = "대상 단말이 이미 다른 방송에 포함되어 있습니다."


# ── 422 ──────────────────────────────────────────────────────────────────
class ValidationFailed(ApiError):
    # Starlette 이 상수명을 바꾸는 중이라(ENTITY -> CONTENT) 정수로 고정한다.
    status_code = 422
    code = "VALIDATION_FAILED"
    message = "입력값이 올바르지 않습니다."


# ── 503 ──────────────────────────────────────────────────────────────────
class ServiceUnavailable(ApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"
    message = "일시적으로 사용할 수 없습니다."


class MqttUnavailable(ServiceUnavailable):
    code = "MQTT_UNAVAILABLE"
    message = "MQTT 브로커에 연결되어 있지 않아 명령을 보낼 수 없습니다."


class PayloadTooLarge(ApiError):
    """MQTT CMD payload 가 1024바이트를 넘었을 때. 단말이 못 받으므로 발행 전에 막는다."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "MQTT_PAYLOAD_TOO_LARGE"
    message = "명령 payload 가 단말 수신 한계를 초과했습니다."


def register_exception_handlers(app: FastAPI) -> None:
    """앱 전역 예외 핸들러 등록. main.py 에서 한 번 호출한다."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI 기본 422 형식을 위 규약으로 통일한다.
        errors = jsonable_encoder(exc.errors())
        return ValidationFailed(detail={"errors": errors}).to_response()
