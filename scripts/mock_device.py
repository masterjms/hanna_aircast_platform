#!/usr/bin/env python
"""목(mock) ESP32 단말.

실물 단말 없이 서버를 개발하려고 만든 것이다. 통신 사양 §3.4 의 STATUS 를 그대로 흉내낸다.

    cd backend && .venv/Scripts/python ../scripts/mock_device.py --count 5

  · 미등록 MAC 자동 등록이 되는지
  · CONFIG(retain) 를 받아 config_version 을 STATUS 로 echo 하는지
  · LWT 로 OFFLINE 이 남는지

를 확인할 수 있다. Ctrl+C 로 끊으면 브로커가 LWT 를 대신 발행한다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import random
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import aiomqtt  # noqa: E402

from app.config import settings  # noqa: E402

ROOT = settings.mqtt_topic_root

#: CONFIG 로 마을을 못 받은 상태의 기본값(통신 사양 §3.5).
UNASSIGNED_VILLAGE = "00000000"


def _fetch(url: str, timeout: float = 30.0) -> bytes:
    """FILE_START 의 https_url 을 그대로 받아온다(단말의 채널 C 흉내)."""
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return res.read()


def _probe_stream(url: str, timeout: float = 8.0) -> bool:
    """마운트가 살아 있고 바이트가 흐르는지 확인한다.

    Icecast 는 소스가 없는 마운트에 404 를 준다 — 그게 곧 "아직 준비 안 됨"이다.
    """
    with urllib.request.urlopen(url, timeout=timeout) as res:
        if res.status != 200:
            return False
        # 헤더만 오고 바이트가 안 나올 수 있으니 실제로 한 조각을 읽어본다.
        return res.read(1) != b""


def _drain(url: str, timeout: float = 8.0) -> int:
    """스트림을 끝까지 읽는다(호출자가 취소하면 스레드가 정리된다)."""
    received = 0
    with urllib.request.urlopen(url, timeout=timeout) as res:
        while chunk := res.read(4096):
            received += len(chunk)
    return received


def with_colons(mac: str) -> str:
    """STATUS 의 device 필드는 콜론 표기다(토픽은 콜론 없음)."""
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


class MockDevice:
    def __init__(self, mac: str, interval: float) -> None:
        self.mac = mac
        self.interval = interval
        # CONFIG 를 받기 전 기본값. 통신 사양의 "미수신 시 하드코딩값"에 해당한다.
        self.village_id = "00000000"
        self.config_version = 0
        self.rssi = random.randint(-75, -35)
        # 단말에서 LIVE·FILE·OTA 는 서로 배타다. 지금 처리 중인 파일 job_id.
        self.busy_file: int | None = None
        self.live_session: int | None = None
        self.live_task: asyncio.Task | None = None
        self.state = "IDLE"

    def status_payload(self, *, offline: bool = False) -> dict:
        if offline:
            return {
                "type": "STATUS",
                "device": with_colons(self.mac),
                "village_id": self.village_id,
                "wifi": 0,
                "mqtt": 0,
                "state": "OFFLINE",
            }
        # 실제 단말처럼 RSSI 가 조금씩 흔들리게 한다.
        self.rssi = max(-90, min(-30, self.rssi + random.randint(-3, 3)))
        return {
            "type": "STATUS",
            "device": with_colons(self.mac),
            "village_id": self.village_id,
            "wifi": 1,
            "mqtt": 1,
            "ip": f"192.168.0.{20 + int(self.mac[-2:], 16) % 200}",
            "rssi": self.rssi,
            "state": self.state,
            "busy": 1 if self.busy_file else 0,
            "reason": 0,
            "config_version": self.config_version,
        }

    def apply_config(self, raw: bytes) -> str | None:
        """CONFIG 수신. 빈 payload 는 retain 삭제이므로 무시한다.

        마을이 바뀌었으면 이전 village_id 를 돌려준다 — 호출자가 토픽을
        갈아끼우는 데 쓴다.
        """
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

        previous = self.village_id
        if "village_id" in data:
            self.village_id = str(data["village_id"])
        if "config_version" in data:
            self.config_version = int(data["config_version"])
        print(f"[{self.mac}] CONFIG 적용 v{self.config_version} village={self.village_id}")
        return previous if previous != self.village_id else None

    async def run(self) -> None:
        lwt = aiomqtt.Will(
            topic=f"{ROOT}/device/{self.mac}/status",
            payload=json.dumps(self.status_payload(offline=True)).encode(),
            qos=1,
            retain=False,
        )
        async with aiomqtt.Client(
            hostname=settings.mqtt_host,
            port=settings.mqtt_port,
            identifier=self.mac,  # ACL 의 %c 치환에 쓰인다
            will=lwt,
        ) as client:
            await client.subscribe(f"{ROOT}/all/config", qos=1)
            await client.subscribe(f"{ROOT}/device/{self.mac}/config", qos=1)
            await client.subscribe(f"{ROOT}/device/{self.mac}/cmd", qos=1)
            await client.subscribe(f"{ROOT}/all/cmd", qos=1)
            # 배정된 마을이 있으면 그 마을 명령도 받는다. 미배정("00000000")이면
            # 구독하지 않는다 — 통신 사양 §3.5.
            await self.resubscribe_village(client, None)
            print(f"[{self.mac}] 접속")

            async def publish_loop() -> None:
                first = True
                while True:
                    # 연결 직후 1회는 QoS1, 이후 주기는 event_qos(기본 0).
                    await client.publish(
                        f"{ROOT}/device/{self.mac}/status",
                        json.dumps(self.status_payload()).encode(),
                        qos=1 if first else 0,
                    )
                    first = False
                    await asyncio.sleep(self.interval)

            task = asyncio.create_task(publish_loop())
            try:
                async for message in client.messages:
                    topic = str(message.topic)
                    raw = bytes(message.payload or b"")
                    if topic.endswith("/config"):
                        previous = self.apply_config(raw)
                        if previous is not None:
                            await self.resubscribe_village(client, previous)
                        # 통신 사양: CONFIG 적용 직후 STATUS 1회(QoS1).
                        await client.publish(
                            f"{ROOT}/device/{self.mac}/status",
                            json.dumps(self.status_payload()).encode(),
                            qos=1,
                        )
                    else:
                        await self.handle_cmd(client, raw)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def resubscribe_village(self, client: aiomqtt.Client, previous: str | None) -> None:
        """마을 명령 토픽을 현재 배정에 맞춘다.

        재배정 시 이전 마을 토픽을 반드시 해제해야 한다. 안 그러면 단말이
        옛 마을 방송까지 같이 받는다 — 코덱스 협의 항목이기도 하다.
        """
        if previous and previous != UNASSIGNED_VILLAGE:
            await client.unsubscribe(f"{ROOT}/village/{previous}/cmd")
            print(f"[{self.mac}]   마을 토픽 해제 {previous}")

        if self.village_id and self.village_id != UNASSIGNED_VILLAGE:
            await client.subscribe(f"{ROOT}/village/{self.village_id}/cmd", qos=1)
            print(f"[{self.mac}]   마을 토픽 구독 {self.village_id}")

    async def set_state(self, client: aiomqtt.Client, state: str) -> None:
        """state 를 바꾸고, 실제로 바뀌었으면 즉시 STATUS 를 1회 발행한다.

        2026-08-20 사양: 단말은 state 가 바뀌면 주기(status_interval_sec)를
        기다리지 않고 바로 STATUS 를 보낸다 — 서버가 단말 상태를 실시간에
        가깝게 파악하기 위함. QoS 는 주기 STATUS 와 같이 event_qos(기본 0)를 따른다.
        """
        if state == self.state:
            return
        self.state = state
        await client.publish(
            f"{ROOT}/device/{self.mac}/status",
            json.dumps(self.status_payload()).encode(),
            qos=0,
        )

    # ── CMD 처리 ────────────────────────────────────────────────────────
    async def publish_result(self, client: aiomqtt.Client, payload: dict) -> None:
        await client.publish(
            f"{ROOT}/device/{self.mac}/result", json.dumps(payload).encode(), qos=1
        )

    async def handle_cmd(self, client: aiomqtt.Client, raw: bytes) -> None:
        try:
            cmd = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[{self.mac}] CMD 파싱 실패")
            return

        kind = cmd.get("type")
        print(f"[{self.mac}] CMD {kind}")

        if kind == "FILE_START":
            await self.do_file_start(client, cmd)
        elif kind == "FILE_STOP":
            await self.do_file_stop(client, cmd)
        elif kind == "LIVE_START":
            await self.do_live_start(client, cmd)
        elif kind == "LIVE_STOP":
            await self.do_live_stop(client, cmd)
        else:
            # OTA_* 는 Phase 7 에서 붙인다.
            print(f"[{self.mac}]   (아직 처리하지 않는 명령)")

    async def do_live_start(self, client: aiomqtt.Client, cmd: dict) -> None:
        """LIVE_START 를 받고 Icecast 마운트에 붙는다.

        stream_url 이 없으면 기본 /live 로 떨어진다(통신 사양의 폴백).
        마운트가 세션마다 다르므로 이 필드가 없으면 여러 마을 동시 방송이 안 된다.

        LIVE_READY.status: 0=READY, 1=TIMEOUT, 2=ABORT, 3=FAIL/BUSY
        """
        job_id = cmd.get("job_id")

        if self.busy_file is not None:
            # 파일 처리 중이면 LIVE 를 거절한다(단말에서 LIVE·FILE 은 배타).
            await self.publish_result(client, {
                "type": "LIVE_READY", "ver": 267, "job_id": job_id,
                "device": with_colons(self.mac), "status": 3, "reason": 8,
            })
            print(f"[{self.mac}]   LIVE_READY status=3 (FILE 처리 중)")
            return

        url = cmd.get("stream_url") or f"{settings.icecast_public_base_url}/live"
        try:
            # 실제 단말처럼 스트림에 붙어 첫 바이트가 오는지 확인한다.
            ok = await asyncio.to_thread(_probe_stream, url)
        except Exception:  # noqa: BLE001
            ok = False

        if ok:
            self.live_session = job_id
            await self.set_state(client, "LIVE")
            self.live_task = asyncio.create_task(self._drain_stream(url))
            status, reason = 0, 0
            print(f"[{self.mac}]   LIVE_READY status=0 ({url})")
        else:
            status, reason = 1, 0
            print(f"[{self.mac}]   LIVE_READY status=1 TIMEOUT ({url})")

        await self.publish_result(client, {
            "type": "LIVE_READY", "ver": 267, "job_id": job_id,
            "device": with_colons(self.mac), "status": status, "reason": reason,
        })

    async def _drain_stream(self, url: str) -> None:
        """스트림을 계속 읽어 통계를 남긴다. 실제 단말의 P4 재생에 해당한다."""
        try:
            received = await asyncio.to_thread(_drain, url)
            print(f"[{self.mac}]   스트림 종료 ({received} bytes 수신)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[{self.mac}]   스트림 끊김: {exc}")

    async def do_live_stop(self, client: aiomqtt.Client, cmd: dict) -> None:
        """LIVE_STOP. 스트림 수신을 끊고 IDLE 로 돌아간다."""
        if self.live_task is not None:
            self.live_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.live_task
            self.live_task = None
        self.live_session = None
        await self.set_state(client, "IDLE")
        print(f"[{self.mac}]   LIVE 종료 (job={cmd.get('job_id')})")

    async def do_file_start(self, client: aiomqtt.Client, cmd: dict) -> None:
        """실제 단말처럼 https_url 을 받아 sha256 을 검증하고 결과를 보고한다.

        LIVE 중이면 PREEMPTED_BY_LIVE, 이미 파일 처리 중이면 BUSY 로 거절한다
        (통신 사양 §3.2 — 단말에서 LIVE·FILE·OTA 는 서로 배타).
        """
        job_id = cmd.get("job_id")

        if self.busy_file is not None:
            await self.publish_result(client, {
                "type": "FILE_ABORT", "job_id": job_id,
                "device": with_colons(self.mac), "reason": "BUSY",
            })
            print(f"[{self.mac}]   거절: BUSY")
            return

        self.busy_file = job_id
        await self.set_state(client, "FILE")
        try:
            url = cmd.get("https_url", "")
            expect = cmd.get("sha256", "")
            # 블로킹 HTTP 라 스레드로 뺀다. 이벤트 루프를 잡으면 STATUS 가 밀린다.
            body = await asyncio.to_thread(_fetch, url)
            got = hashlib.sha256(body).hexdigest()
            verify_ok = (not expect) or (got == expect)

            if len(body) and verify_ok:
                await self.publish_result(client, {
                    "type": "FILE_END", "ver": 267, "job_id": job_id,
                    "device": with_colons(self.mac), "size": len(body), "verify_ok": 1,
                })
                print(f"[{self.mac}]   FILE_END ({len(body)} bytes, sha256 일치)")
            else:
                reason = "SHA_MISMATCH" if len(body) else "DOWNLOAD_FAIL"
                await self.publish_result(client, {
                    "type": "FILE_ABORT", "job_id": job_id,
                    "device": with_colons(self.mac), "reason": reason,
                })
                print(f"[{self.mac}]   FILE_ABORT ({reason})")
        except Exception as exc:  # noqa: BLE001
            await self.publish_result(client, {
                "type": "FILE_ABORT", "job_id": job_id,
                "device": with_colons(self.mac), "reason": "DOWNLOAD_FAIL",
            })
            print(f"[{self.mac}]   FILE_ABORT (다운로드 실패: {exc})")
        finally:
            self.busy_file = None
            await self.set_state(client, "IDLE")

    async def do_file_stop(self, client: aiomqtt.Client, cmd: dict) -> None:
        """멈출 게 있으면 FILE_ABORT(USER_CANCEL), 없으면 FILE_STOP_RESULT(NOT_ACTIVE)."""
        job_id = cmd.get("job_id")

        if self.busy_file is None:
            await self.publish_result(client, {
                "type": "FILE_STOP_RESULT", "ver": 267, "job_id": job_id,
                "device": with_colons(self.mac), "status": 1, "reason": "NOT_ACTIVE",
            })
            print(f"[{self.mac}]   FILE_STOP_RESULT (NOT_ACTIVE)")
        else:
            self.busy_file = None
            await self.set_state(client, "IDLE")
            await self.publish_result(client, {
                "type": "FILE_ABORT", "job_id": job_id,
                "device": with_colons(self.mac), "reason": "USER_CANCEL",
            })
            print(f"[{self.mac}]   FILE_ABORT (USER_CANCEL)")


async def main() -> None:
    parser = argparse.ArgumentParser(description="목 ESP32 단말")
    parser.add_argument("--count", type=int, default=3, help="띄울 단말 수")
    parser.add_argument("--interval", type=float, default=10.0, help="STATUS 주기(초)")
    parser.add_argument(
        "--prefix", default="aabbcc", help="MAC 앞 6자리 (뒤 6자리는 순번으로 채운다)"
    )
    args = parser.parse_args()

    devices = [MockDevice(f"{args.prefix}{i:06x}", args.interval) for i in range(args.count)]
    print(f"{settings.mqtt_host}:{settings.mqtt_port} 로 단말 {len(devices)}대 접속")
    await asyncio.gather(*(d.run() for d in devices))


if __name__ == "__main__":
    # 백엔드와 같은 이유로 Windows 에서는 셀렉터 루프가 필요하다.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
