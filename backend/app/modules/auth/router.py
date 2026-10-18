"""인증 라우터.

토큰은 상태를 두지 않는다(JWT). 로그아웃은 클라이언트가 토큰을 버리는 것으로 끝나고,
서버는 204 만 돌려준다. 강제 무효화가 필요해지면 그때 블랙리스트를 추가한다.
"""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from app.config import settings
from app.core import authz, totp
from app.core.deps import CurrentUser, Db, Scope, must_enroll_otp, otp_required
from app.core.scope import VillageScope
from app.core.security import create_access_token, verify_password
from app.errors import (
    AccountExpired,
    AccountLocked,
    ApiError,
    InvalidCredentials,
    OtpInvalid,
    OtpRequired,
)
from app.models.auth_log import LoginEvent
from app.models.device import Device
from app.models.org import Organization, User, Village
from app.modules.org import service as org_service
from app.schemas.auth import (
    LoginEventOut,
    LoginEventPage,
    LoginRequest,
    LoginResponse,
    MeResponse,
    OtpCodeIn,
    OtpDisableIn,
    OtpEnabledOut,
    OtpSetupOut,
    PasswordChangeRequest,
    VillageBrief,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _build_me(db: Db, user: User, scope: VillageScope) -> MeResponse:
    """상단바의 '담당 범위 · 전체 12개 마을 · 300대' 표시에 필요한 것들."""
    stmt = select(Village.id, Village.name).order_by(Village.name)
    if not scope.all_villages:
        stmt = stmt.where(Village.id.in_(scope.village_ids))
    villages = [VillageBrief(id=vid, name=name) for vid, name in (await db.execute(stmt)).all()]

    count_stmt = select(func.count()).select_from(Device)
    if scope.all_villages:
        # 미배정 단말도 super_admin 의 관리 대상이므로 함께 센다.
        device_count = await db.scalar(count_stmt)
    else:
        device_count = await db.scalar(
            count_stmt.where(Device.village_id.in_(scope.village_ids))
        )

    org_name = None
    if user.organization_id is not None:
        org_name = await db.scalar(
            select(Organization.name).where(Organization.id == user.organization_id)
        )

    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        villages=villages,
        all_villages=scope.all_villages,
        device_count=int(device_count or 0),
        organization_id=user.organization_id,
        organization_name=org_name,
        must_change_password=user.must_change_password,
        otp_enabled=user.totp_enabled_at is not None,
        otp_required=otp_required(user),
        must_enroll_otp=must_enroll_otp(user),
    )


def _client_ip(request: Request) -> str | None:
    """nginx 뒤라 X-Real-IP(또는 X-Forwarded-For 첫 값)가 실제 접속 주소다."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:45]
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


async def _record_login(
    db: Db, request: Request, *, username: str, user: User | None, result: str
) -> None:
    """로그인 시도 한 줄(문제점 65번). 실패도 남긴다.

    예외로 응답하면 미들웨어가 롤백하므로 여기서 먼저 커밋한다.
    """
    db.add(
        LoginEvent(
            user_id=user.id if user else None,
            username=username[:50],
            ip=_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
            result=result,
        )
    )
    await db.commit()


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: Db, request: Request) -> LoginResponse:
    user = await db.scalar(select(User).where(User.username == payload.username))

    # 아이디가 없을 때와 비밀번호가 틀릴 때를 같은 에러로 돌려준다(계정 존재 여부 노출 방지).
    if user is None:
        await _record_login(
            db, request, username=payload.username, user=None, result="unknown_user"
        )
        raise InvalidCredentials()

    now = dt.datetime.now(dt.timezone.utc)
    # 연속 실패 잠금(문제점 70번 ②). 잠긴 동안은 비밀번호가 맞아도 들이지 않는다.
    if user.locked_until is not None and user.locked_until > now:
        await _record_login(db, request, username=payload.username, user=user, result="locked")
        raise AccountLocked()
    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if (
            settings.login_lock_threshold > 0
            and user.failed_login_count >= settings.login_lock_threshold
        ):
            user.locked_until = now + dt.timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_count = 0
        await _record_login(
            db, request, username=payload.username, user=user, result="bad_password"
        )
        raise InvalidCredentials()
    # OTP 단계에서 틀리면 비밀번호 실패와 같은 잠금 수에 더한다 — 맞은 뒤 0 으로.
    failed_before = user.failed_login_count or 0
    user.failed_login_count = 0
    user.locked_until = None

    # 정리 작업은 주기적으로 돈다 — 그 사이에 만료된 계정으로 들어오는 것을 막는다.
    if user.is_expired():
        await _record_login(db, request, username=payload.username, user=user, result="expired")
        raise AccountExpired()

    # 2단계 인증(문제점 70번 2항) — 켠 계정은 비밀번호 다음 OTP 숫자(또는 복구 코드)가 맞아야 한다.
    if user.totp_enabled_at is not None and user.totp_secret:
        if not payload.otp:
            # 비밀번호까지는 맞았다 — 실패로 세지 않고 6자리 칸을 열게 한다. 실패 수는 그대로 둔다.
            user.failed_login_count = failed_before
            await db.commit()
            raise OtpRequired()
        if not _accept_otp(user, payload.otp):
            user.failed_login_count = failed_before + 1
            if (
                settings.login_lock_threshold > 0
                and user.failed_login_count >= settings.login_lock_threshold
            ):
                user.locked_until = now + dt.timedelta(minutes=settings.login_lock_minutes)
                user.failed_login_count = 0
            await _record_login(db, request, username=payload.username, user=user, result="bad_otp")
            raise OtpInvalid()

    # 비밀번호 사용 기간(문제점 70번 ②) — 지나면 새 비밀번호를 정하기 전까지 다른 화면을 막는다.
    if (
        settings.password_max_age_days > 0
        and user.password_changed_at is not None
        and now - user.password_changed_at > dt.timedelta(days=settings.password_max_age_days)
    ):
        user.must_change_password = True

    await _record_login(db, request, username=payload.username, user=user, result="ok")

    scope = await authz.resolve_scope(db, user)

    # 동시 접속 제한(문제점 70번 10/15 ③) — 새 세션 키. 먼저 쓰던 곳의 토큰은 다음 요청에서 401.
    sid = secrets.token_hex(16) if settings.single_session else None
    user.session_key = sid
    token, expires_in = create_access_token(
        user_id=user.id, username=user.username, role=user.role, sid=sid
    )
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=await _build_me(db, user, scope),
    )


def _accept_otp(user: User, code: str) -> bool:
    """6자리 숫자면 TOTP, 아니면 복구 코드(쓰면 지운다)."""
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) == totp.DIGITS and len(code.strip()) <= totp.DIGITS + 1:
        step = totp.verify(user.totp_secret or "", digits, last_step=user.totp_last_step)
        if step is None:
            return False
        user.totp_last_step = step
        return True
    h = totp.hash_recovery(code)
    if h in (user.totp_recovery or []):
        user.totp_recovery = [x for x in user.totp_recovery if x != h]
        return True
    return False


@router.post("/otp/setup", response_model=OtpSetupOut)
async def otp_setup(user: CurrentUser, db: Db) -> OtpSetupOut:
    """등록 시작 — 새 비밀을 만들어 돌려준다. 앱의 숫자로 확인(/otp/enable)해야 켜진다.

    이미 켠 계정은 다시 시작하지 못한다(먼저 끄거나 관리자 초기화) — 켜진 비밀을 몰래 바꾸지 못하게.
    """
    if user.totp_enabled_at is not None:
        raise ApiError("이미 2단계 인증을 쓰고 있습니다.", code="OTP_ALREADY_ENABLED")
    secret = totp.new_secret()
    user.totp_secret = secret
    user.totp_last_step = None
    await db.flush()
    return OtpSetupOut(
        secret=secret,
        otpauth_uri=totp.otpauth_uri(secret, username=user.username, issuer=settings.totp_issuer),
    )


@router.post("/otp/enable", response_model=OtpEnabledOut)
async def otp_enable(payload: OtpCodeIn, user: CurrentUser, db: Db) -> OtpEnabledOut:
    """앱에 뜬 6자리로 확인하고 켠다. 복구 코드 10개를 이 응답에서 한 번만 준다."""
    if user.totp_enabled_at is not None:
        raise ApiError("이미 2단계 인증을 쓰고 있습니다.", code="OTP_ALREADY_ENABLED")
    if not user.totp_secret:
        raise ApiError("먼저 등록을 시작하세요.", code="OTP_NOT_STARTED")
    step = totp.verify(user.totp_secret, payload.code)
    if step is None:
        raise ApiError(
            "인증 번호가 맞지 않습니다. 앱에 지금 뜬 숫자를 넣어 주세요"
            "(휴대폰 시계가 자동인지 확인).",
            code="OTP_INVALID",
        )
    codes = totp.new_recovery_codes()
    user.totp_last_step = step
    user.totp_recovery = [totp.hash_recovery(c) for c in codes]
    user.totp_enabled_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    return OtpEnabledOut(recovery_codes=codes)


@router.post("/otp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def otp_disable(payload: OtpDisableIn, user: CurrentUser, db: Db) -> None:
    """스스로 끄기 — 필수 역할은 못 끈다(관리자 초기화 뒤 다시 등록)."""
    if otp_required(user):
        raise ApiError("이 계정은 2단계 인증이 필수라 끌 수 없습니다.", code="OTP_REQUIRED_ROLE")
    if not verify_password(payload.password, user.password_hash):
        raise ApiError("비밀번호가 맞지 않습니다.", code="WRONG_PASSWORD")
    clear_otp(user)
    return None


def clear_otp(user: User) -> None:
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.totp_recovery = []


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, db: Db) -> None:
    """「로그아웃」을 눌렀을 때만 온다. 그 계정의 가장 최근 열린 로그인 줄에 해지 시각을 적는다."""
    last = await db.scalar(
        select(LoginEvent)
        .where(
            LoginEvent.user_id == user.id,
            LoginEvent.result == "ok",
            LoginEvent.logged_out_at.is_(None),
        )
        .order_by(LoginEvent.logged_in_at.desc())
        .limit(1)
    )
    if last is not None:
        last.logged_out_at = func.now()
    # 이 토큰도 더는 못 쓴다(토큰 자체는 만료 전이라도) — 동시 접속 제한과 같은 세션 키.
    user.session_key = None
    return None


@router.get("/logins", response_model=LoginEventPage)
async def login_history(
    db: Db,
    actor: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: int = 10,
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> LoginEventPage:
    """로그인 기록(문제점 65번). 최고 관리자는 전부, 그 외는 자기 기록만. size 10·20·50."""
    if size not in (10, 20, 50):
        size = 10
    stmt = select(LoginEvent)
    if actor.role != "super_admin":
        stmt = stmt.where(LoginEvent.user_id == actor.id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(LoginEvent.username.ilike(like) | LoginEvent.ip.ilike(like))
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(
            stmt.order_by(LoginEvent.logged_in_at.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    return LoginEventPage(
        total=total, page=page, size=size, items=[LoginEventOut.model_validate(r) for r in rows]
    )


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, db: Db, scope: Scope) -> MeResponse:
    return await _build_me(db, user, scope)


@router.post("/password", response_model=MeResponse)
async def change_password(
    payload: PasswordChangeRequest, user: CurrentUser, db: Db
) -> MeResponse:
    """자기 비밀번호 변경. 임시 비밀번호 계정은 여기서만 풀린다(향후검토 10번)."""
    await org_service.change_own_password(
        db,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return await _build_me(db, user, await authz.resolve_scope(db, user))
