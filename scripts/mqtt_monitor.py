#!/usr/bin/env python
"""실단말 MQTT 감시 · 사양 검증기.

    cd backend && .venv/Scripts/python ../scripts/mqtt_monitor.py

브로커에 흐르는 모든 메시지를 보여주고, 단말이 보낸 것은 통신 사양과
대조해서 어긋나면 그 자리에서 표시한다. 실물 ESP32 를 붙이고 이 창을
띄워두면 "MQTT 가 되는지"를 눈으로 확인할 수 있다.

문서의 monitor.bat 과 같은 역할이지만 두 가지가 다르다:
  · 사양 위반을 자동으로 잡는다 — 필드명·state 값·MAC 표기까지 확인한다.
  · 옛 필드명(session_id/cmd_id/file_id)이 오면 경고한다. 그게 보이면
    단말 펌웨어가 2026-08-20 job_id 통일 이전 버전이라는 뜻이다.

Ctrl+C 로 끝내면 무엇을 봤는지 요약한다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

import aiomqtt

from app.config import settings

ROOT = settings.mqtt_topic_root

#: 통신 사양 §3.4. 이 밖의 값이 오면 펌웨어와 서버 중 하나가 낡은 것이다.
VALID_STATES = {"IDLE", "LIVE", "FILE", "RF", "OTA", "OFFLINE"}
#: 통신 사양 §3.6 (2026-08-20 간소화). DOWNLOAD_DONE/WAIT_APPLY/APPLYING 은 폐지됐다.
VALID_OTA_STATES = {"ACCEPTED", "PREPARE", "DOWNLOADING", "VERIFYING", "COMPLETED", "FAIL"}
#: 2026-08-20 에 job_id 로 통일되면서 사라진 이름들.
RETIRED_ID_FIELDS = {"session_id", "cmd_id", "file_id"}

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")
_VILLAGE_RE = re.compile(r"^[0-9]{8}$")

# ── 화면 ─────────────────────────────────────────────────────────────
_C = {
    "dim": "\033[90m", "red": "\033[91m", "green": "\033[92m",
    "yellow": "\033[93m", "blue": "\033[94m", "cyan": "\033[96m", "off": "\033[0m",
}


def paint(text: str, color: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_C[color]}{text}{_C['off']}"


class Monitor:
    def __init__(self, *, color: bool, raw: bool) -> None:
        self.color = color
        self.raw = raw
        self.macs: set[str] = set()
        self.types: Counter[str] = Counter()
        self.problems: list[str] = []
        self.states: dict[str, str] = {}
        #: MAC 별 마지막 config_version — CONFIG 가 실제로 먹었는지 보는 값.
        self.config_versions: dict[str, int] = {}
        self.per_mac: defaultdict[str, Counter[str]] = defaultdict(Counter)

    def flag(self, mac: str, message: str) -> None:
        line = f"{mac}: {message}"
        self.problems.append(line)
        print("      " + paint(f"⚠ {message}", "red", enabled=self.color))

    # ── 검증 ─────────────────────────────────────────────────────────
    def check_device_message(self, mac: str, topic: str, data: dict) -> None:
        """단말 → 서버 메시지를 사양과 대조한다."""
        kind = str(data.get("type") or "")

        retired = RETIRED_ID_FIELDS & set(data)
        if retired:
            self.flag(
                mac,
                f"옛 ID 필드 {sorted(retired)} 사용 — "
                "펌웨어가 job_id 통일(2026-08-20) 이전 버전이다",
            )

        if kind == "STATUS":
            state = str(data.get("state") or "")
            if state not in VALID_STATES:
                self.flag(mac, f"모르는 state '{state}' (사양: {sorted(VALID_STATES)})")
            else:
                previous = self.states.get(mac)
                if previous and previous != state:
                    print(
                        "      "
                        + paint(f"state {previous} → {state}", "cyan", enabled=self.color)
                    )
                self.states[mac] = state

            village = str(data.get("village_id") or "")
            if village and not _VILLAGE_RE.match(village):
                self.flag(mac, f"village_id 가 8자리 숫자가 아니다: '{village}'")
            elif village == "00000000":
                print("      " + paint("미배정 상태 (village_id=00000000)", "yellow",
                                       enabled=self.color))

            version = data.get("config_version")
            if isinstance(version, int):
                if self.config_versions.get(mac) != version:
                    print("      " + paint(f"config_version = {version}", "cyan",
                                           enabled=self.color))
                self.config_versions[mac] = version

            # device 필드는 콜론 있는 표기, 토픽은 콜론 없는 표기다(사양 §3.1).
            reported = str(data.get("device") or "").replace(":", "").lower()
            if reported and reported != mac:
                self.flag(mac, f"payload 의 device({reported}) 가 토픽 MAC 과 다르다")

        elif kind == "OTA_STATUS":
            state = str(data.get("state") or "")
            if state not in VALID_OTA_STATES:
                self.flag(mac, f"모르는 OTA state '{state}' (사양: {sorted(VALID_OTA_STATES)})")

        elif kind in {"LIVE_READY", "FILE_END", "FILE_ABORT", "FILE_STOP_RESULT", "LIVE_STATS"}:
            if "job_id" not in data:
                self.flag(mac, f"{kind} 에 job_id 가 없다")

        elif kind:
            self.flag(mac, f"사양에 없는 type '{kind}'")

    # ── 출력 ─────────────────────────────────────────────────────────
    def show(self, topic: str, raw: bytes) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        parts = topic.split("/")
        # iotradio/device/<mac>/<leaf> · iotradio/village/<id>/cmd · iotradio/all/<leaf>
        mac = parts[2] if len(parts) > 3 and parts[1] == "device" else ""
        leaf = parts[-1]
        to_device = leaf in {"cmd", "config"}
        arrow = "S→D" if to_device else "D→S"

        if not raw:
            print(f"{paint(now, 'dim', enabled=self.color)} {arrow} {topic} "
                  f"{paint('(빈 payload = retain 삭제)', 'dim', enabled=self.color)}")
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"{paint(now, 'dim', enabled=self.color)} {arrow} {topic} "
                  f"{paint('JSON 아님', 'red', enabled=self.color)}: {raw[:120]!r}")
            if mac:
                self.flag(mac, "JSON 으로 파싱되지 않는 payload")
            return

        kind = str(data.get("type") or ("CONFIG" if leaf == "config" else "?"))
        self.types[kind] += 1

        if mac:
            if not _MAC_RE.match(mac):
                self.problems.append(f"{mac}: 토픽 MAC 표기가 콜론 없는 소문자 12자리가 아니다")
            if not to_device:
                self.macs.add(mac)
                self.per_mac[mac][kind] += 1

        color = "blue" if to_device else "green"
        head = f"{paint(now, 'dim', enabled=self.color)} {paint(arrow, color, enabled=self.color)} "
        body = json.dumps(data, ensure_ascii=False) if self.raw else _brief(data)
        print(f"{head}{paint(kind, color, enabled=self.color)}  {topic}\n      {body}")

        if mac and not to_device:
            self.check_device_message(mac, topic, data)

    # ── 요약 ─────────────────────────────────────────────────────────
    def summary(self) -> int:
        print("\n" + "=" * 62)
        print("  요약")
        print("=" * 62)

        if not self.macs:
            print(paint("  단말에서 온 메시지가 하나도 없다.", "red", enabled=self.color))
            print("""
  확인할 것:
    1. 단말이 이 PC 의 LAN IP:1883 으로 접속하도록 설정됐는가
       (localhost 가 아니다 — 단말 입장에서 자기 자신이 된다)
    2. 방화벽 1883 인바운드가 열려 있는가
    3. 단말과 이 PC 가 같은 네트워크에 있는가
    4. 브로커가 0.0.0.0 으로 듣고 있는가 (docker port xwifi-mosquitto-dev)""")
            return 1

        print(f"  단말 {len(self.macs)}대: {', '.join(sorted(self.macs))}")
        for mac in sorted(self.macs):
            state = self.states.get(mac, "?")
            version = self.config_versions.get(mac, "?")
            kinds = ", ".join(f"{k}×{v}" for k, v in self.per_mac[mac].most_common())
            print(f"    {mac}  state={state}  config_version={version}")
            print(f"      {kinds}")

        print(f"\n  메시지 종류: {', '.join(f'{k}×{v}' for k, v in self.types.most_common())}")

        if self.problems:
            print(paint(f"\n  사양 위반 {len(self.problems)}건:", "red", enabled=self.color))
            for line in dict.fromkeys(self.problems):  # 중복 제거, 순서 유지
                print(f"    - {line}")
            return 1

        print(paint("\n  사양 위반 없음.", "green", enabled=self.color))
        return 0


def _brief(data: dict) -> str:
    """type 을 뺀 나머지를 한 줄로. 긴 값(sha256·url)은 줄인다."""
    out = []
    for k, v in data.items():
        if k == "type":
            continue
        text = str(v)
        if len(text) > 46:
            text = text[:43] + "…"
        out.append(f"{k}={text}")
    return "  ".join(out)


async def watch(mon: Monitor, host: str, port: int, seconds: float | None) -> None:
    print(f"브로커 {host}:{port} · 토픽 루트 {ROOT}")
    print("단말 메시지를 기다린다. Ctrl+C 로 종료하면 요약이 나온다.\n")
    async with aiomqtt.Client(host, port) as client:
        await client.subscribe(f"{ROOT}/#", qos=1)

        async def loop() -> None:
            async for message in client.messages:
                mon.show(str(message.topic), bytes(message.payload or b""))

        if seconds is None:
            await loop()
            return
        # asyncio.timeout 은 3.11+ 라 wait_for 를 쓴다.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(loop(), timeout=seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="실단말 MQTT 감시 · 사양 검증기")
    parser.add_argument("--host", default=settings.mqtt_host, help="브로커 주소")
    parser.add_argument("--port", type=int, default=settings.mqtt_port)
    parser.add_argument("--raw", action="store_true", help="payload 를 줄이지 않고 전부 보여준다")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument(
        "--seconds", type=float, default=None,
        help="이 시간(초)만 보고 요약을 낸다. 생략하면 Ctrl+C 까지 계속 본다.",
    )
    args = parser.parse_args()

    mon = Monitor(color=not args.no_color, raw=args.raw)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(watch(mon, args.host, args.port, args.seconds))
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - 접속 실패 원인을 그대로 보여준다
        print(f"\n브로커 접속 실패: {exc}")
        return 1
    return mon.summary()


if __name__ == "__main__":
    raise SystemExit(main())
