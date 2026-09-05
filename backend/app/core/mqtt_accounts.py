"""단말별 MQTT 계정 — 비밀번호 발행과 브로커 passwd 파일 생성.

username = 콜론 없는 소문자 MAC, password = 8자 랜덤
(사양 `docs/spec/SERVER_DEVICE_CREDENTIAL_SPEC_2026-08-27.md`).

브로커 등록은 파일 경유다: 백엔드가 mosquitto passwd 형식(해시)으로 파일을
공유 볼륨(`MOSQUITTO_PASSWD_EXPORT`)에 떨어뜨리면, mosquitto 컨테이너의
entrypoint 감시 루프(`infra/mosquitto/entrypoint.sh`)가 권한을 맞춰 설치하고
SIGHUP 으로 리로드한다. 백엔드는 mosquitto 컨테이너에 직접 신호를 보낼 수
없어서(도커 소켓을 안 물린다) 이 간접 구조를 쓴다.

경로가 비어 있으면 전부 no-op — 개발·테스트는 anonymous 브로커를 쓴다.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import string
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings

log = logging.getLogger(__name__)

#: 사양 §1 확정 문자 집합. `@` 는 시리얼 프로토콜의 `@END` 와 충돌해서 금지,
#: `!` 는 단말 키보드에 있지만 서버는 쓰지 않기로 확정됐다.
#: 입력 검증(등록 payload)은 이 집합 기준이다.
PASSWORD_CHARSET = string.ascii_letters + string.digits + "#$%^&*-_+=?.~"
#: 생성은 `=` 를 추가로 뺀다("조합은 서버가 정한다" — 사양 §1). 시리얼이
#: `@KEY=VALUE` 형식이라 값 안의 `=` 는 파서 구현이 첫 `=` 에서 자른다는 보장에
#: 기대게 된다 — 방어적으로 안 쓴다. (2026-08-31 실물 인증 실패의 실제 원인은
#: `=` 가 아니라 `@END` 앞 개행 누락으로 판명됐지만, 이 제외는 유지한다.)
GENERATION_CHARSET = PASSWORD_CHARSET.replace("=", "")
#: 사양 §1 — 단말 화면에서 버튼으로 한 글자씩 고를 수 있어야 해서 8자 고정.
PASSWORD_LENGTH = 8

#: 공유 계정(이행기 한시) username. ACL 의 %c 규칙과 세트로 움직인다.
SHARED_DEVICE_USERNAME = "xwifi-device"

# mosquitto_passwd 의 sha512-pbkdf2 형식과 동일한 파라미터 (2.x 기본값).
# eclipse-mosquitto:2 (2.1.2) 인증 실측 통과 — 2026-08-30.
_HASH_ITERATIONS = 101
_SALT_BYTES = 12


def generate_device_password() -> str:
    return "".join(secrets.choice(GENERATION_CHARSET) for _ in range(PASSWORD_LENGTH))


def server_host() -> str:
    """단말 `@SERVER` 에 넣을 호스트. 공개 주소에서 스킴과 포트를 뗀 값이다.

    단말은 host 또는 IP 만 받는다(생산 사양 §4.4.1) — 포트는 펌웨어가 프로토콜별로
    안다(mqtts 8883, https 443). 스트림 주소에서 포트를 빼 달라던 것과 같은 이유다.

        https://hanna-aircast.co.kr      → hanna-aircast.co.kr
        http://192.168.0.5:8080          → 192.168.0.5
    """
    raw = settings.public_base_url.strip()
    # 스킴이 없으면 urlsplit 이 "host:port" 의 host 를 스킴으로 읽는다
    # (`a.co:8080` → scheme='a.co', path='8080'). `//` 를 붙여 netloc 임을 알린다.
    if "//" not in raw:
        raw = "//" + raw
    return urlsplit(raw).hostname or ""


def mosquitto_hash(password: str) -> str:
    """`$7$<iterations>$<salt b64>$<hash b64>` — mosquitto 의 PBKDF2-SHA512 형식."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, _HASH_ITERATIONS, dklen=64)
    salt_b64 = base64.b64encode(salt).decode()
    dk_b64 = base64.b64encode(dk).decode()
    return f"$7${_HASH_ITERATIONS}${salt_b64}${dk_b64}"


def render_passwd(device_accounts: dict[str, str]) -> str:
    """passwd 파일 전체 내용을 만든다. DB 가 정본이고 파일은 항상 통째로 재생성한다.

    부분 수정(기존 파일 읽어서 병합)을 안 하는 이유: 파일과 DB 가 어긋난 상태가
    조용히 굳는 것이 최악이라서다(레지스트리 사양 §3.6 — 동기화는 서버 책임).
    """
    lines = [
        "# 자동 생성 파일 — 손대지 말 것. 정본은 서버 DB 다.",
        "# 백엔드가 계정 발행/삭제 때마다 통째로 다시 만든다 (app/core/mqtt_accounts.py).",
    ]

    if settings.mqtt_username and settings.mqtt_password:
        lines.append(f"{settings.mqtt_username}:{mosquitto_hash(settings.mqtt_password)}")
    else:
        log.warning("MQTT_USERNAME/MQTT_PASSWORD 미설정 — 서버 계정 없이 passwd 를 만든다")

    # 이행기 공유 계정. .env 에서 MQTT_DEVICE_PASSWORD 를 지우면 다음 재생성 때
    # 계정이 사라지고, 그때부터 단말별 계정 없는 단말은 브로커에서 거절된다.
    if settings.mqtt_device_password:
        lines.append(
            f"{SHARED_DEVICE_USERNAME}:{mosquitto_hash(settings.mqtt_device_password)}"
        )

    for mac in sorted(device_accounts):
        lines.append(f"{mac}:{mosquitto_hash(device_accounts[mac])}")
    return "\n".join(lines) + "\n"


def render_acl(device_villages: dict[str, str | None]) -> str:
    # 값은 마을의 MQTT 문자열(core.village_token.token_for) — DB id 가 아니다.
    """aclfile 전체 내용. 단말마다 **자기 마을 topic 만** 읽기를 연다.

    단말측 통신 사양 §2.1: `village/<village_id>/cmd` 자리는 MAC 이 아니라 서버가
    배정한 값이라 `%u` 로 못 잡으니 "별도 규칙" 을 서버가 만들라고 했다.
    와일드카드(`village/+/cmd`)로 뭉개면 계정 하나만 뽑혀도 전 마을 명령을
    구독(도청)할 수 있으므로, 배정이 바뀔 때마다 이 파일을 다시 만든다 —
    passwd 와 같은 통로(공유 볼륨 → entrypoint 감시 루프 → SIGHUP).

    - 치환 없는 `pattern` = 인증된 전 계정 공통 허용 (mosquitto 2.1 실측)
    - `pattern ... %u` = 자기 MAC 토픽만
    - `user <mac>` 블록 = 그 단말의 마을 한 줄. 미배정은 블록을 만들지 않는다
    """
    lines = [
        "# 자동 생성 파일 — 손대지 말 것. 정본은 서버 DB 다.",
        "# 등록·삭제·마을 배정 변경 때마다 통째로 다시 만든다 (app/core/mqtt_accounts.py).",
        "",
        "# 서버 계정 — cmd/config 발행은 여기서만 한다.",
        f"user {settings.mqtt_username or 'xwifi-server'}",
        "topic readwrite iotradio/#",
        "",
        "# 공통 토픽 — 전 단말. 치환 없는 pattern 은 인증된 모든 클라이언트에 적용된다.",
        "pattern read iotradio/all/cmd",
        "pattern read iotradio/all/config",
        "",
        "# 단말별 토픽. %u = username = 콜론 없는 소문자 MAC (비밀번호로 증명된 값).",
        "pattern write iotradio/device/%u/result",
        "pattern write iotradio/device/%u/status",
        "pattern read  iotradio/device/%u/cmd",
        "pattern read  iotradio/device/%u/config",
        "",
        "# 마을 명령 — 단말마다 배정된 마을 하나만. 와일드카드를 쓰지 않는다.",
    ]
    for mac in sorted(device_villages):
        village_id = device_villages[mac]
        if village_id is None:
            continue
        lines.append(f"user {mac}")
        lines.append(f"topic read iotradio/village/{village_id}/cmd")
    return "\n".join(lines) + "\n"


def _export_file(target: Path, content: str, label: str) -> bool:
    """공유 볼륨에 원자적으로 쓴다. 실패는 로그만 — 정본은 DB 다."""
    try:
        # 같은 디렉터리에 임시 파일 → 원자적 교체. 감시 루프가 반쯤 쓴 파일을
        # 설치하는 일이 없게 한다.
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            os.unlink(tmp)
            raise
        log.info("mosquitto %s 내보냄 → %s", label, target)
        return True
    except OSError:
        log.exception(
            "mosquitto %s 내보내기 실패 (%s) — DB 는 정상, 다음 발행 때 재시도된다",
            label, target,
        )
        return False


def export_passwd(device_accounts: dict[str, str]) -> bool:
    """passwd 를 공유 볼륨에 쓴다. 성공하면 True, 기능이 꺼져 있으면 False.

    실패해도 예외를 위로 던지지 않는다 — 정본은 DB 이고, 다음 발행/기동 때
    다시 시도된다. 호출부가 등록 API 를 500 으로 만들 이유가 없다.
    """
    target = settings.mosquitto_passwd_export
    if not target:
        return False
    return _export_file(
        Path(target), render_passwd(device_accounts), f"passwd(단말 {len(device_accounts)}대)"
    )


def acl_export_path() -> Path | None:
    """aclfile.generated 경로 — passwd 와 같은 디렉터리. 설정 하나로 둘을 다룬다."""
    target = settings.mosquitto_passwd_export
    return Path(target).parent / "aclfile.generated" if target else None


def export_acl(device_villages: dict[str, str | None]) -> bool:
    """aclfile 을 공유 볼륨에 쓴다. passwd 와 같은 규칙(실패는 로그만)."""
    target = acl_export_path()
    if target is None:
        return False
    assigned = sum(1 for v in device_villages.values() if v is not None)
    return _export_file(target, render_acl(device_villages), f"aclfile(배정 {assigned}대)")
