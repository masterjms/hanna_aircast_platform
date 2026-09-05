/**
 * 신규 단말 등록 — 서버가 MAC 을 모르는 단말을 처음 넣는 화면.
 *
 * 흐름 (생산 사양 §3.2, 등록 흐름 사양 §B):
 *   모달 열림 → 비밀번호 사전 발급 → QR 스캔(또는 직접 쓰기) → [등록]
 *   → 시리얼 주입(USB 또는 복사) → (옵션) 재부팅 테스트 → 테스트 방송
 *
 * 스캔 모드는 입력칸 하나로 받는다(생산 사양 §3.2.1 B 권장) — HID 스캐너가
 * 키보드처럼 문자열을 치고 Enter(기본 접미사)로 끝낸다. 붙여넣기도 같은 칸.
 * QR 은 `|` 구분 — 앞 5개만 해석하고 뒤는 무시한다(항목이 늘어도 안 깨지게).
 */

import { useEffect, useRef, useState } from 'react';

import { api } from '../api/client';
import { Modal } from './Modal';
import { provisioningFrame, rebootFrame } from '../lib/serial';
import type { AudioFile } from '../api/types';

/* Web Serial 은 Chrome/Edge 전용이라 표준 DOM 타입에 없다 — 최소한만 선언 */
interface SerialPortLike {
  open(options: { baudRate: number }): Promise<void>;
  close(): Promise<void>;
  readable: ReadableStream<Uint8Array> | null;
  writable: WritableStream<Uint8Array> | null;
}

function webSerial(): { requestPort(): Promise<SerialPortLike> } | null {
  return (navigator as unknown as { serial?: { requestPort(): Promise<SerialPortLike> } })
    .serial ?? null;
}

const MAC_RE = /^[0-9a-fA-F]{12}$/;

/** QR 문자열 → 5필드. 앞 5개만 해석, 뒤는 무시(생산 사양 §3.2.2). */
/**
 * 물리 키 코드 → 스캔 문자열의 한 글자. IME 상태와 무관하다.
 *
 * QR 내용은 `mac|모델|버전|모델|버전` 이라 영문·숫자·`|:-._` 만 나온다. 그 밖의
 * 키(방향키·Tab 등)는 null 을 돌려 브라우저 기본 동작에 맡긴다.
 */
function scanKeyToChar(code: string, shift: boolean): string | null {
  if (code === 'Backspace') return '\b';
  if (/^Key[A-Z]$/.test(code)) {
    const letter = code.slice(3);
    return shift ? letter : letter.toLowerCase();
  }
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^Numpad[0-9]$/.test(code)) return code.slice(6);
  switch (code) {
    case 'Backslash':
      return shift ? '|' : '\\';
    case 'Semicolon':
      return shift ? ':' : ';';
    case 'Minus':
    case 'NumpadSubtract':
      return shift ? '_' : '-';
    case 'Period':
    case 'NumpadDecimal':
      return '.';
    case 'Space':
      return ' ';
    default:
      return null;
  }
}

function parseScan(raw: string): {
  mac: string;
  p4Model: string;
  p4Version: string;
  c6Model: string;
  c6Version: string;
  warning: string | null;
} | null {
  const parts = raw
    .trim()
    .split('|')
    .map((s) => s.trim());
  const mac = (parts[0] ?? '').replace(/[:-]/g, '').toLowerCase();
  if (!MAC_RE.test(mac)) return null;
  return {
    mac,
    p4Model: parts[1] ?? '',
    p4Version: parts[2] ?? '',
    c6Model: parts[3] ?? '',
    c6Version: parts[4] ?? '',
    warning:
      parts.length < 5
        ? `항목이 ${parts.length}개입니다 (기준 5개) — 스캔이 잘렸거나 구형 펌웨어일 수 있습니다.`
        : null,
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * 열린 포트 하나를 감싸고, **리더를 하나만 유지**한다.
 *
 * ⚠ 명령마다 getReader() → cancel() 을 반복하면 안 된다. SerialPort 의 readable 은
 *   cancel 하는 순간 닫히고 `port.readable` 이 null 이 된다 — 그 뒤 명령은 응답을
 *   기다릴 수 없어 write 직후 close 와 경쟁하다 **전송이 통째로 유실된다.**
 *   (2026-08-31: 계정 주입은 되는데 @OFF 재부팅만 안 먹던 원인이 이것이었다.
 *    주입은 3초 응답 대기가 flush 시간을 벌어줘서 우연히 살아 있었다.)
 */
class SerialSession {
  private buf = '';
  private reader: { read(): Promise<{ value?: Uint8Array; done: boolean }>; cancel(): Promise<void>; releaseLock(): void } | null = null;

  constructor(private port: SerialPortLike) {}

  /** 백그라운드 수신 루프. 포트가 살아 있는 동안 계속 돈다. */
  startReading(): void {
    const readable = this.port.readable;
    if (!readable || this.reader) return;
    const reader = readable.getReader();
    this.reader = reader;
    const decoder = new TextDecoder();
    void (async () => {
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (value) this.buf += decoder.decode(value, { stream: true });
        }
      } catch {
        // 포트를 닫거나 단말이 재부팅하면 여기로 온다 — 정상 종료 경로다.
      }
    })();
  }

  clear(): void {
    this.buf = '';
  }

  async write(text: string): Promise<void> {
    if (!this.port.writable) throw new Error('포트에 쓸 수 없습니다.');
    const writer = this.port.writable.getWriter();
    try {
      await writer.write(new TextEncoder().encode(text));
    } finally {
      writer.releaseLock();
    }
  }

  /** 패턴이 올 때까지(또는 타임아웃까지) 기다리고, 그동안 받은 것을 돌려준다. */
  async waitFor(pattern: RegExp, timeoutMs: number): Promise<string> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (pattern.test(this.buf)) {
        await sleep(150); // 뒤에 붙는 꼬리까지
        return this.buf;
      }
      await sleep(50);
    }
    return this.buf;
  }

  async close(): Promise<void> {
    try {
      await this.reader?.cancel();
      this.reader?.releaseLock();
    } catch {
      /* 이미 끊긴 포트 */
    }
    this.reader = null;
    await this.port.close().catch(() => undefined);
  }
}

type TestPhase = 'idle' | 'waiting' | 'connected' | 'timeout';

export function RegisterDeviceDialog({
  onClose,
  onRegistered,
}: {
  onClose: () => void;
  onRegistered: () => void;
}) {
  const [mode, setMode] = useState<'scan' | 'manual'>('scan');
  const [password, setPassword] = useState<string | null>(null);
  //: 단말 @SERVER 에 넣을 호스트. 서버가 자기 공개 주소에서 뽑아 알려준다.
  const [serverHost, setServerHost] = useState('');
  const [pwError, setPwError] = useState<string | null>(null);

  const [mac, setMac] = useState('');
  const [p4Model, setP4Model] = useState('');
  const [p4Version, setP4Version] = useState('');
  const [c6Model, setC6Model] = useState('');
  const [c6Version, setC6Version] = useState('');
  const [scanned, setScanned] = useState(false);
  const [scanWarning, setScanWarning] = useState<string | null>(null);

  const [registering, setRegistering] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sessionRef = useRef<SerialSession | null>(null);
  const [injectMsg, setInjectMsg] = useState<string | null>(null);
  const [injected, setInjected] = useState(false);
  const [copied, setCopied] = useState(false);

  const [testPhase, setTestPhase] = useState<TestPhase>('idle');
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const testStartRef = useRef<number>(0);
  const [files, setFiles] = useState<AudioFile[]>([]);
  const [fileId, setFileId] = useState<number | ''>('');
  const [broadcastMsg, setBroadcastMsg] = useState<string | null>(null);

  const scanInputRef = useRef<HTMLInputElement>(null);

  // 비밀번호는 모달을 여는 시점에 서버에서 사전 발급받는다(사용자가 못 정한다 —
  // 스캔/수동 모드 공통. 시스템 난수, 사양 문자 집합 8자).
  const fetchPassword = async () => {
    setPwError(null);
    try {
      const issued = await api.devices.newPassword();
      setPassword(issued.password);
      setServerHost(issued.server_host);
    } catch (e) {
      setPwError(e instanceof Error ? e.message : '비밀번호 발급에 실패했습니다.');
    }
  };
  useEffect(() => {
    void fetchPassword();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 테스트 음원 목록은 미리 받아 둔다 — 연결 확인 단계에서만 받으면, 수동으로
  // 전원을 껐다 켠 경로에서 목록이 비어 [테스트 방송] 이 조용히 아무것도 안 한다
  // (2026-08-31 현장에서 실제로 겪음).
  useEffect(() => {
    if (!registered) return;
    let alive = true;
    api.files
      .list()
      .then((f) => {
        if (!alive) return;
        setFiles(f);
        if (f.length > 0) setFileId((prev) => (prev === '' ? f[0].id : prev));
      })
      .catch(() => alive && setFiles([]));
    return () => {
      alive = false;
    };
  }, [registered]);

  // 스캔 모드에서는 스캐너 입력칸에 포커스를 붙잡아 둔다.
  useEffect(() => {
    if (mode === 'scan' && !scanned) scanInputRef.current?.focus();
  }, [mode, scanned]);

  // 모달이 닫힐 때 포트를 정리한다.
  useEffect(
    () => () => {
      void sessionRef.current?.close();
    },
    [],
  );

  const applyScan = (raw: string) => {
    const parsed = parseScan(raw);
    if (!parsed) {
      setScanWarning('MAC(12자리 hex)을 읽지 못했습니다 — 다시 스캔해 주세요.');
      if (scanInputRef.current) scanInputRef.current.value = '';
      return;
    }
    setMac(parsed.mac);
    setP4Model(parsed.p4Model);
    setP4Version(parsed.p4Version);
    setC6Model(parsed.c6Model);
    setC6Version(parsed.c6Version);
    setScanned(true);
    setScanWarning(parsed.warning);
  };

  const macNormalized = mac.replace(/[:-]/g, '').toLowerCase();
  const macValid = MAC_RE.test(macNormalized);
  const canRegister =
    !registered && !registering && macValid && password !== null && (mode === 'manual' || scanned);

  const register = async () => {
    if (!password) return;
    setRegistering(true);
    setError(null);
    try {
      await api.devices.create({
        mac: macNormalized,
        p4_model: p4Model.trim() || null,
        p4_version: p4Version.trim() || null,
        c6_model: c6Model.trim() || null,
        c6_version: c6Version.trim() || null,
        mqtt_password: password,
      });
      setRegistered(true);
      onRegistered();
    } catch (e) {
      const msg = e instanceof Error ? e.message : '등록에 실패했습니다.';
      setError(
        msg.includes('이미') || msg.toUpperCase().includes('EXISTS')
          ? '이미 등록된 단말입니다 — 목록의 [계정] 버튼으로 기존 계정을 확인하세요.'
          : msg,
      );
    } finally {
      setRegistering(false);
    }
  };

  const injectUsb = async () => {
    if (!password) return;
    const serial = webSerial();
    if (!serial) {
      setInjectMsg('이 브라우저는 Web Serial 을 지원하지 않습니다 — Chrome/Edge 를 쓰거나 [시리얼 명령 복사]로 넣어 주세요.');
      return;
    }
    setInjectMsg('전송 중…');
    try {
      if (!sessionRef.current) {
        const port = await serial.requestPort();
        await port.open({ baudRate: 115200 });
        const session = new SerialSession(port);
        session.startReading();
        sessionRef.current = session;
      }
      const session = sessionRef.current;
      session.clear();
      await session.write(provisioningFrame({ serverHost, mac: macNormalized, password }));
      const resp = await session.waitFor(/@RESULT=|@MQTTPW=/, 3000);
      if (/@RESULT=OK/.test(resp) || /@MQTTPW=SET/.test(resp)) {
        setInjected(true);
        setInjectMsg('전송 완료 ✓ — 단말이 @RESULT=OK 로 응답했습니다.');
      } else if (/@RESULT=FAIL/.test(resp)) {
        setInjectMsg('단말이 @RESULT=FAIL 로 거절했습니다 — 값을 확인하고 다시 시도하세요.');
      } else {
        // 응답이 안 잡혀도 전송 자체는 됐을 수 있다 — 확인 방법을 안내하고 다음 단계는 열어 둔다.
        setInjected(true);
        setInjectMsg('전송했지만 응답을 읽지 못했습니다 — 단말 화면 또는 @GET 으로 확인하세요.');
      }
    } catch (e) {
      setInjectMsg(
        e instanceof Error && e.name === 'NotFoundError'
          ? '포트 선택이 취소됐습니다.'
          : `전송 실패: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  };

  const copyCommands = async () => {
    if (!password) return;
    try {
      // trimEnd 를 쓰지 않는다 — 끝의 개행까지가 프레임이고, 붙여넣기로 넣을 때도
      // 그 개행이 있어야 단말이 요청을 마무리한다.
      await navigator.clipboard.writeText(
        provisioningFrame({ serverHost, mac: macNormalized, password }),
      );
      setCopied(true);
      setInjected(true); // 수동 붙여넣기 경로 — 작업자가 터미널로 넣는다
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 클립보드 권한이 없으면 사용자가 직접 드래그해 복사한다 */
    }
  };

  // 테스트: @OFF 재부팅 → 재부팅 후 첫 STATUS(=last_seen_at 갱신)를 기다린다.
  const startTest = async () => {
    setTestMsg(null);
    setBroadcastMsg(null);
    const session = sessionRef.current;
    if (session) {
      try {
        session.clear();
        await session.write(rebootFrame());
        // 응답을 기다리는 것이 곧 전송 flush 시간이다 — 여기서 바로 close 하면
        // @OFF 가 버퍼에 남은 채 포트가 닫혀 명령이 유실된다(2026-08-31 실측).
        const resp = await session.waitFor(/@RESULT=|@END/, 2500);
        setTestMsg(
          /@RESULT=OK|@END/.test(resp)
            ? '재부팅 명령(@OFF) 전송 완료 — 서버 연결을 기다립니다 (보통 10초 안팎)…'
            : '재부팅 명령(@OFF)을 보냈지만 응답이 없습니다 — 안 꺼지면 전원을 껐다 켜 주세요. 연결을 기다립니다…',
        );
        // 재부팅되면 포트가 죽는다 — 정리해 둔다.
        await session.close();
        sessionRef.current = null;
      } catch {
        setTestMsg('재부팅 명령 전송에 실패했습니다 — 단말 전원을 껐다 켜 주세요. 연결을 기다립니다…');
      }
    } else {
      setTestMsg('USB 연결이 없어 재부팅 명령을 못 보냈습니다 — 단말 전원을 껐다 켜 주세요. 연결을 기다립니다…');
    }
    testStartRef.current = Date.now();
    setTestPhase('waiting');
  };

  // 연결 대기 폴링 — 30초 시한(등록 흐름 요구). 시한이 지나면 계속 대기/종료 선택.
  useEffect(() => {
    if (testPhase !== 'waiting') return;
    const timer = setInterval(async () => {
      if (Date.now() - testStartRef.current > 30_000) {
        setTestPhase('timeout');
        return;
      }
      try {
        const d = await api.devices.get(macNormalized);
        if (d.last_seen_at && new Date(d.last_seen_at).getTime() >= testStartRef.current) {
          setTestPhase('connected');
          setTestMsg('서버 연결 확인 ✓ — 발행한 계정으로 브로커에 붙었습니다.');
        }
      } catch {
        /* 일시적 조회 실패는 다음 주기에 재시도 */
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [testPhase, macNormalized]);

  // 테스트 방송 — 단말별 topic 1대에만, store_flash:0 (등록 흐름 사양 §B3.
  // all/cmd 로 보내면 현장 전 단말이 울린다 — 반드시 device 대상).
  const sendTestBroadcast = async () => {
    if (fileId === '') {
      setBroadcastMsg('테스트 음원을 먼저 고르세요 (파일함에 음원이 없으면 하나 올려야 합니다).');
      return;
    }
    setBroadcastMsg('전송 중…');
    try {
      await api.broadcast.fileStart({
        file_id: fileId,
        target_scope: 'device',
        target_ids: [macNormalized],
        store_flash: false,
        autoplay: true,
      });
      setBroadcastMsg('테스트 방송을 보냈습니다 — 소리가 나는지 귀로 확인하세요. (flash 에 저장되지 않습니다)');
    } catch (e) {
      setBroadcastMsg(`테스트 방송 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const fieldRow = (
    label: string,
    value: string,
    setter: (v: string) => void,
    placeholder = '',
  ) => (
    <div className="field">
      <label>{label}</label>
      <input
        className="mono"
        value={value}
        readOnly={mode === 'scan' || registered}
        placeholder={mode === 'scan' ? '스캔하면 채워집니다' : placeholder}
        onChange={(e) => setter(e.target.value)}
      />
    </div>
  );

  return (
    <Modal
      title="신규 단말 등록"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose}>
            닫기
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={register}
            disabled={!canRegister}
          >
            {registered ? '등록됨 ✓' : registering ? '등록 중…' : '등록'}
          </button>
        </>
      }
    >
      {/* ── 1. 단말 정보 (스캔 / 직접 쓰기) ── */}
      {mode === 'scan' && !registered && (
        <div className="field">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="strong">{scanned ? '스캔 완료 ✓ — 다시 스캔하면 덮어씁니다' : '스캔을 해주세요.'}</span>
            <button type="button" className="btn btn--sm" onClick={() => setMode('manual')}>
              직접 쓰기
            </button>
          </div>
          <input
            ref={scanInputRef}
            className="mono"
            lang="en"
            autoComplete="off"
            placeholder="여기에 포커스를 두고 QR/바코드를 스캔 (붙여넣기도 가능)"
            onKeyDown={(e) => {
              // HID 스캐너는 키 입력을 흘려보내고 끝에 Enter(기본 접미사)를 보낸다.
              //
              // 한글 IME 가 켜져 있으면 스캐너의 영문 키가 자모로 조합돼 전부 깨진다
              // (문제점 20번). 문자가 아니라 **물리 키(e.code)** 로 직접 조립하고
              // 기본 동작을 막으면 IME 가 끼어들 틈이 없다 — 어느 입력기 상태든 같다.
              const el = e.target as HTMLInputElement;
              if (e.key === 'Enter') {
                e.preventDefault();
                applyScan(el.value);
                el.value = '';
                return;
              }
              if (e.ctrlKey || e.metaKey || e.altKey) return; // 붙여넣기(Ctrl+V)는 그대로
              const ch = scanKeyToChar(e.code, e.shiftKey);
              if (ch === null) return; // 방향키 등은 브라우저에 맡긴다
              e.preventDefault();
              if (ch === '\b') el.value = el.value.slice(0, -1);
              else el.value += ch;
            }}
            style={{ marginTop: 8 }}
          />
        </div>
      )}
      {mode === 'manual' && !registered && (
        <p className="hint" style={{ marginTop: 0 }}>
          직접 입력 모드 — MAC 만 필수이고 모델/버전은 비워도 됩니다.{' '}
          <button type="button" className="btn btn--sm" onClick={() => setMode('scan')}>
            스캔 모드로
          </button>
        </p>
      )}
      {scanWarning && <p className="hint hint--warn">{scanWarning}</p>}

      {fieldRow('MAC (콜론 없이 12자리)', mac, setMac, '58e6c5f2cc74')}
      {mac && !macValid && <p className="hint hint--warn">MAC 은 hex 12자리여야 합니다.</p>}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 12px' }}>
        {fieldRow('P4 모델 번호', p4Model, setP4Model, 'IOT-1000')}
        {fieldRow('P4 모델 버전', p4Version, setP4Version, 'V.260823-1')}
        {fieldRow('C6 모델 번호', c6Model, setC6Model, 'IOT-1000C6')}
        {fieldRow('C6 모델 버전', c6Version, setC6Version, 'V.260823-1')}
      </div>

      {/* ── 2. 계정 (시스템 난수 — 사용자가 못 정한다) ── */}
      <div className="field">
        <label>서버 주소 (@SERVER)</label>
        <input className="mono" readOnly value={serverHost || '불러오는 중…'} />
      </div>
      <div className="field">
        <label>MQTT 비밀번호 (자동 생성)</label>
        <div style={{ display: 'flex', gap: 6 }}>
          <input className="mono" readOnly value={password ?? '발급 중…'} />
          {!registered && (
            <button type="button" className="btn btn--sm" onClick={fetchPassword} title="새 난수로 다시 발급">
              재생성
            </button>
          )}
        </div>
      </div>
      {pwError && <p className="hint hint--warn">{pwError}</p>}
      {error && <p className="hint hint--warn">{error}</p>}

      {/* ── 3. 시리얼 주입 — 등록 뒤에만 (등록 전에 넣고 재부팅하면 인증 실패) ── */}
      {registered && (
        <>
          <hr style={{ margin: '14px 0', opacity: 0.2 }} />
          <p className="strong" style={{ margin: '0 0 8px' }}>
            단말에 계정 넣기
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button type="button" className="btn" onClick={injectUsb}>
              USB로 단말에 넣기 (COM)
            </button>
            <button type="button" className="btn" onClick={copyCommands}>
              {copied ? '복사됨 ✓' : '시리얼 명령 복사 (@END 포함)'}
            </button>
          </div>
          {injectMsg && <p className="hint">{injectMsg}</p>}

          {/* ── 4. 테스트 (옵션) — 주입 후에만 ── */}
          <hr style={{ margin: '14px 0', opacity: 0.2 }} />
          <p className="strong" style={{ margin: '0 0 8px' }}>
            테스트 (옵션)
          </p>
          {/* ① 재부팅 → 연결 확인 */}
          <button
            type="button"
            className="btn"
            onClick={startTest}
            disabled={!injected || testPhase === 'waiting'}
          >
            {testPhase === 'waiting' ? '연결 대기 중…' : '재부팅 (@OFF) → 연결 확인'}
          </button>
          {!injected && (
            <p className="hint">먼저 계정을 단말에 넣어야 테스트할 수 있습니다.</p>
          )}
          {testPhase === 'waiting' && <p className="hint">⏳ {testMsg}</p>}
          {testPhase === 'timeout' && (
            <>
              <p className="hint hint--warn">
                30초 안에 연결이 확인되지 않았습니다 — 단말이 안 꺼졌으면 전원을 껐다 켜고,
                그래도 안 붙으면 Wi-Fi 설정을 확인하세요.
              </p>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => {
                    testStartRef.current = Date.now() - 1; // 이미 붙었으면 다음 폴링에 잡힌다
                    setTestPhase('waiting');
                  }}
                >
                  연결 계속 대기
                </button>
                <button type="button" className="btn btn--sm" onClick={() => setTestPhase('idle')}>
                  대기 종료
                </button>
              </div>
            </>
          )}
          {testPhase === 'connected' && testMsg && (
            <p className="hint" style={{ color: 'var(--ok-text)' }}>
              {testMsg}
            </p>
          )}

          {/* ② 테스트 방송 — 연결 확인 단계와 독립. 수동으로 전원을 껐다 켠
              경우에도 눌러야 하므로 phase 로 막지 않는다(2026-08-31 현장 피드백). */}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 10 }}>
            <select
              value={fileId}
              onChange={(e) => setFileId(e.target.value === '' ? '' : Number(e.target.value))}
              aria-label="테스트 음원"
              disabled={files.length === 0}
            >
              {files.length === 0 && <option value="">음원 없음</option>}
              {files.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.filename}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn--sm" onClick={sendTestBroadcast}>
              테스트 방송 (이 단말만)
            </button>
          </div>
          {files.length === 0 && (
            <p className="hint">파일함에 음원이 없습니다 — 하나 올린 뒤 다시 열어 주세요.</p>
          )}
          {broadcastMsg && <p className="hint">{broadcastMsg}</p>}
        </>
      )}
    </Modal>
  );
}
