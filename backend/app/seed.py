"""초기 데이터 투입.

    운영:  docker compose exec backend python -m app.seed
    개발:  cd backend && .venv/Scripts/python -m app.seed --demo

앱 안에 두는 이유: 배포 이미지에는 `app` 과 `alembic` 만 들어간다(빌드 컨텍스트가
backend/ 라 저장소 루트의 scripts/ 는 애초에 복사할 수 없다). 시드는 배포 절차의
일부이므로 이미지에 함께 실려야 CI/CD 에서도 쓸 수 있다.

멱등이다. 여러 번 돌려도 중복이 생기지 않는다.

기본값은 **super_admin 계정 하나만** 만든다.
  · 마을·구역은 화면에서 등록하거나 엑셀 사전 등록으로 넣는다.
  · 예시 마을과 village_admin 계정은 --demo 로 명시할 때만 생긴다.
    인터넷에 열린 서버에 알려진 비밀번호 계정을 남기지 않기 위해서다.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select

from app.constants import Role
from app.core.security import hash_password
from app.db import engine, session_scope
from app.models.org import User, UserVillage, Village, Zone

#: --demo 전용. 개발 PC 에서만 쓰는 값이라 알려진 비밀번호로 둔다.
DEMO_SUPER_ADMIN = ("admin", "admin1234!")
DEMO_VILLAGE_ADMIN = ("sindong", "village1234!")

DEMO_VILLAGES = [
    # (이름, 시도, 시군구, 위도, 경도, [구역...])
    ("신동마을", "경상북도", "안동시", 36.5684, 128.7294, ["마을회관", "정자나무", "버스정류장"]),
    ("하회마을", "경상북도", "안동시", 36.5390, 128.5180, ["입구", "강변"]),
]


def _generated_password() -> str:
    """운영용 초기 비밀번호. 사람이 옮겨 적을 수 있는 문자만 쓴다."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


async def _seed_demo_villages(db) -> list[int]:
    village_ids: list[int] = []
    for name, sido, sigungu, lat, lng, zone_names in DEMO_VILLAGES:
        village = await db.scalar(select(Village).where(Village.name == name))
        if village is None:
            village = Village(name=name, sido=sido, sigungu=sigungu, lat=lat, lng=lng)
            db.add(village)
            await db.flush()
            print(f"  + 마을 {name} (id={village.id})")
        village_ids.append(village.id)

        for zone_name in zone_names:
            exists = await db.scalar(
                select(Zone.id).where(Zone.village_id == village.id, Zone.name == zone_name)
            )
            if exists is None:
                db.add(Zone(village_id=village.id, name=zone_name))
                print(f"      + 구역 {name}/{zone_name}")
        await db.flush()
    return village_ids


async def seed(*, demo: bool = False, admin_username: str, admin_password: str | None) -> None:
    created_password: str | None = None

    async with session_scope() as db:
        village_ids: list[int] = []
        if demo:
            village_ids = await _seed_demo_villages(db)

        # ── super_admin ─────────────────────────────────────────────
        if await db.scalar(select(User.id).where(User.username == admin_username)) is None:
            password = admin_password or _generated_password()
            created_password = password
            db.add(
                User(
                    username=admin_username,
                    password_hash=hash_password(password),
                    role=Role.SUPER_ADMIN.value,
                )
            )
            print(f"  + 계정 {admin_username} (super_admin)")
        else:
            print(f"  = 계정 {admin_username} 이미 있음 — 비밀번호는 건드리지 않는다")

        # ── village_admin (데모 전용) ───────────────────────────────
        if demo and village_ids:
            username, password = DEMO_VILLAGE_ADMIN
            user = await db.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    role=Role.VILLAGE_ADMIN.value,
                )
                db.add(user)
                await db.flush()
                # 첫 번째 마을만 담당시킨다 — 범위 제한이 실제로 동작하는지 보려면
                # 담당 밖 마을이 하나는 있어야 한다.
                db.add(UserVillage(user_id=user.id, village_id=village_ids[0]))
                print(f"  + 계정 {username} / {password}  (village_admin → {DEMO_VILLAGES[0][0]})")

    # 엔진을 명시적으로 닫는다. 안 닫으면 Windows + asyncio 에서
    # 이벤트 루프가 먼저 닫히며 SSL transport 정리 에러가 시끄럽게 뜬다.
    await engine.dispose()

    print("시드 완료")
    if created_password and not admin_password:
        print()
        print("  ┌─ 초기 관리자 비밀번호 (이 출력에만 나온다) ─")
        print(f"  │   {admin_username} / {created_password}")
        print("  └─ 로그인 후 즉시 변경할 것")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="초기 데이터 투입 (멱등)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="예시 마을·구역과 village_admin 계정까지 만든다 (개발 전용)",
    )
    parser.add_argument("--admin-username", default="admin", help="super_admin 계정 이름")
    parser.add_argument(
        "--admin-password",
        default=None,
        help="지정하지 않으면 무작위로 만들어 출력한다. --demo 면 알려진 개발용 값을 쓴다.",
    )
    args = parser.parse_args(argv)

    password = args.admin_password
    if password is None and args.demo:
        password = DEMO_SUPER_ADMIN[1]

    try:
        asyncio.run(
            seed(
                demo=args.demo,
                admin_username=args.admin_username,
                admin_password=password,
            )
        )
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
