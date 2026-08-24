/**
 * 마이크 → WSS /ingest 업링크.
 *
 *   getUserMedia → opus-recorder(Ogg/Opus) → WebSocket binary → 서버 → Icecast
 *
 * opus-recorder(WASM 인코더)를 쓴다. 브라우저 내장 MediaRecorder 는 안 된다 —
 * Chrome 계열이 Ogg 컨테이너를 지원하지 않아 WebM 이 나가고, Icecast 는 그걸
 * Ogg 로 알고 받아 단말 디코더가 깨진다. 인코딩을 WASM 으로 직접 하면
 * 브라우저와 무관하게 항상 같은 Ogg/Opus 가 나간다.
 *
 * 인코더 파라미터는 통신 사양 §채널 B 와 일치해야 한다:
 *   16 kHz mono · 24 kbps · 40 ms 프레임
 * LIVE_START 로 단말에 보내는 codec/frame_ms/sample_rate 도 같은 값이다.
 * 한쪽만 바꾸면 단말 지터 버퍼가 깨진다.
 *
 * maxFramesPerPage: 1 — Ogg 페이지 하나에 프레임 하나만 담는다. 페이지를
 * 채우려고 기다리지 않으므로 지연이 프레임 하나(40ms)로 유지된다.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import Recorder from 'opus-recorder';
// Vite 가 워커 파일을 번들에 포함시키고 URL 을 준다.
// 폐쇄망에서도 CDN 없이 동작한다.
import encoderPath from 'opus-recorder/dist/encoderWorker.min.js?url';

/** 통신 사양 §채널 B 와 반드시 일치. */
const SAMPLE_RATE = 16_000;
const BITRATE = 24_000;
const FRAME_MS = 40;

export type UplinkState = 'idle' | 'connecting' | 'live' | 'error';

export interface MicUplink {
  state: UplinkState;
  error: string | null;
  /** 마이크 입력 세기 0~1. 레벨 미터에 쓴다. */
  level: number;
  bytesSent: number;
  start: (sessionId: number, token: string) => Promise<void>;
  stop: () => void;
}

/**
 * 업링크를 쓸 수 없는 이유. 쓸 수 있으면 null.
 *
 * "브라우저가 낡아서"와 "주소가 http 라서"를 반드시 구분해야 한다. 후자는
 * 브라우저를 바꿔도 안 고쳐지는데, 뭉뚱그리면 사용자가 엉뚱한 곳을 헤맨다.
 */
export function uplinkBlockedReason(): string | null {
  if (typeof window === 'undefined') return '브라우저 환경이 아닙니다.';

  const hasAudioContext =
    typeof (window.AudioContext ??
      (window as never as { webkitAudioContext?: unknown }).webkitAudioContext) !== 'undefined';
  if (!hasAudioContext) return '이 브라우저는 WebAudio 를 지원하지 않습니다.';

  // 브라우저는 보안 컨텍스트(https 또는 localhost)에서만 마이크를 준다.
  // LAN IP 로 열면 navigator.mediaDevices 자체가 없다.
  if (typeof navigator.mediaDevices?.getUserMedia !== 'function') {
    if (!window.isSecureContext) {
      return (
        `주소가 ${window.location.origin} 이라 브라우저가 마이크를 막습니다. ` +
        'http 로 접속하면 localhost 일 때만 마이크를 쓸 수 있습니다 — ' +
        '이 PC 에서 http://localhost:5173 으로 열거나, 원격에서 쓰려면 HTTPS 가 필요합니다.'
      );
    }
    return '이 브라우저는 마이크 입력을 지원하지 않습니다.';
  }
  return null;
}

/** 위 판정의 불리언 판. 화면이 버튼을 잠글 때 쓴다. */
export function isUplinkSupported(): boolean {
  return uplinkBlockedReason() === null;
}

/** getUserMedia 예외를 사람이 읽고 조치할 수 있는 문장으로 바꾼다. */
function micErrorMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : '';
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return (
        '마이크 권한이 거부되었습니다. 주소창 왼쪽 자물쇠(또는 ⓘ) 아이콘에서 ' +
        '마이크를 "허용"으로 바꾸고 새로고침해 주세요. ' +
        '윈도우 설정 > 개인 정보 > 마이크 도 함께 확인하세요.'
      );
    case 'NotFoundError':
    case 'OverconstrainedError':
      return '마이크를 찾을 수 없습니다. 장치가 연결되어 있는지 확인해 주세요.';
    case 'NotReadableError':
    case 'AbortError':
      return (
        '마이크를 다른 프로그램이 쓰고 있어 열 수 없습니다. ' +
        '줌·팀즈·녹음기 등을 끄고 다시 시도해 주세요.'
      );
    default:
      return err instanceof Error
        ? `마이크를 사용할 수 없습니다: ${err.message}`
        : '마이크를 사용할 수 없습니다. 브라우저 권한을 확인해 주세요.';
  }
}

export function useMicUplink(): MicUplink {
  const [state, setState] = useState<UplinkState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [bytesSent, setBytesSent] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<InstanceType<typeof Recorder> | null>(null);
  const analyserCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);
  /** 정리 중에는 ws.onclose 가 "끊김 오류"를 띄우지 않게 한다. */
  const closingRef = useRef(false);

  const cleanup = useCallback(() => {
    closingRef.current = true;

    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;

    try {
      recorderRef.current?.stop();
    } catch {
      /* 이미 멈춘 경우 */
    }
    recorderRef.current = null;

    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    void analyserCtxRef.current?.close().catch(() => undefined);
    analyserCtxRef.current = null;

    wsRef.current?.close();
    wsRef.current = null;

    setLevel(0);
  }, []);

  const stop = useCallback(() => {
    cleanup();
    setState('idle');
  }, [cleanup]);

  // 화면을 떠날 때 마이크가 살아 있으면 안 된다.
  useEffect(() => cleanup, [cleanup]);

  const start = useCallback(
    async (sessionId: number, token: string) => {
      setError(null);
      setBytesSent(0);
      setState('connecting');
      closingRef.current = false;

      const blocked = uplinkBlockedReason();
      if (blocked) {
        setError(blocked);
        setState('error');
        return;
      }

      try {
        // 1) WebSocket 을 먼저 연다. 인코더를 돌려놓고 보낼 곳이 없으면 낭비다.
        const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const ws = new WebSocket(`${scheme}://${window.location.host}/ingest?session=${sessionId}`);
        ws.binaryType = 'arraybuffer';
        wsRef.current = ws;

        await new Promise<void>((resolve, reject) => {
          const timer = window.setTimeout(() => reject(new Error('연결 시간 초과')), 10_000);

          ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }));

          ws.onmessage = (e) => {
            // 서버가 ready 를 보내면 그때부터 오디오를 밀어넣는다.
            if (typeof e.data === 'string' && JSON.parse(e.data).type === 'ready') {
              window.clearTimeout(timer);
              resolve();
            }
          };

          ws.onclose = (e) => {
            window.clearTimeout(timer);
            const reasons: Record<number, string> = {
              4001: '인증에 실패했습니다. 다시 로그인해 주세요.',
              4004: '방송 세션을 찾을 수 없습니다.',
              4009: '이미 다른 창에서 마이크가 연결되어 있습니다.',
            };
            reject(new Error(reasons[e.code] ?? '업링크 연결이 끊겼습니다.'));
          };

          ws.onerror = () => {
            window.clearTimeout(timer);
            reject(new Error('업링크에 연결하지 못했습니다.'));
          };
        });

        // 연결이 선 뒤에는 끊김을 오류로 알린다.
        ws.onclose = () => {
          if (closingRef.current) return;
          setError('업링크 연결이 끊겼습니다. 방송을 다시 시작해 주세요.');
          setState('error');
          cleanup();
        };

        // 2) 마이크를 연다. 방송용이라 브라우저 후처리를 전부 끈다 —
        //    에코 제거·자동 게인이 켜져 있으면 스피커로 나가는 소리가
        //    예측 불가능하게 변한다.
        //
        //    스트림은 반드시 하나만 연다. 예전에는 인코더가 자기 것을 열고
        //    레벨 미터가 또 하나를 열었는데, 윈도우 오디오 드라이버 중에는
        //    같은 장치의 동시 캡처를 거부하는 것이 있어(NotReadableError)
        //    "마이크가 안 잡힌다"로 나타났다. sourceNode 로 인코더에 넘겨
        //    한 스트림을 공유한다.
        let stream: MediaStream;
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              channelCount: 1,
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: false,
            },
          });
        } catch (err) {
          throw new Error(micErrorMessage(err));
        }
        streamRef.current = stream;

        const ctx = new AudioContext();
        analyserCtxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);

        const recorder = new Recorder({
          // sourceNode 를 주면 opus-recorder 가 getUserMedia 를 부르지 않는다.
          // 대신 스트림과 AudioContext 정리는 우리가 책임진다(cleanup 참고).
          sourceNode: source,
          encoderPath,
          encoderSampleRate: SAMPLE_RATE,
          numberOfChannels: 1,
          bitRate: BITRATE,
          encoderFrameSize: FRAME_MS,
          // Ogg 페이지에 프레임 하나만 담아 지연을 40ms 로 유지한다.
          maxFramesPerPage: 1,
          // 녹음이 끝날 때까지 모으지 않고 페이지 단위로 바로 흘려보낸다.
          streamPages: true,
          // 2048 = VOIP. 음성에 맞춰 인코딩한다.
          encoderApplication: 2048,
        });
        recorderRef.current = recorder;

        recorder.ondataavailable = (page: Uint8Array) => {
          if (ws.readyState !== WebSocket.OPEN || page.byteLength === 0) return;
          ws.send(page);
          setBytesSent((n) => n + page.byteLength);
        };

        await recorder.start();

        // 3) 레벨 미터. 말하고 있는데 소리가 안 나가는 상황을 눈으로 잡으려는 것이다.
        //    위에서 만든 소스 노드를 그대로 분기해 쓴다(스트림 하나 원칙).
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        const buffer = new Uint8Array(analyser.frequencyBinCount);

        const tick = () => {
          analyser.getByteTimeDomainData(buffer);
          let peak = 0;
          for (const v of buffer) peak = Math.max(peak, Math.abs(v - 128) / 128);
          setLevel(peak);
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);

        setState('live');
      } catch (err) {
        cleanup();
        // 위에서 이미 사람이 읽을 문장으로 바꿔 던진 것은 그대로 쓰고,
        // 그 밖(WebSocket 실패 등)은 메시지를 그대로 보여준다.
        setError(err instanceof Error ? err.message : micErrorMessage(err));
        setState('error');
      }
    },
    [cleanup],
  );

  return { state, error, level, bytesSent, start, stop };
}
