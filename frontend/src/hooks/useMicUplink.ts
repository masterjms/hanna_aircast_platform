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

/** WASM 인코더라 브라우저를 가리지 않는다. WebAudio 만 있으면 된다. */
export function isUplinkSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof navigator.mediaDevices?.getUserMedia === 'function' &&
    typeof (window.AudioContext ?? (window as never as { webkitAudioContext?: unknown }).webkitAudioContext) !==
      'undefined'
  );
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

      if (!isUplinkSupported()) {
        setError('이 브라우저에서는 마이크를 사용할 수 없습니다.');
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

        // 2) 인코더 시작. 방송용이라 브라우저 후처리를 전부 끈다 —
        //    에코 제거·자동 게인이 켜져 있으면 스피커로 나가는 소리가
        //    예측 불가능하게 변한다.
        const recorder = new Recorder({
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
          mediaTrackConstraints: {
            channelCount: 1,
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          },
        });
        recorderRef.current = recorder;

        recorder.ondataavailable = (page: Uint8Array) => {
          if (ws.readyState !== WebSocket.OPEN || page.byteLength === 0) return;
          ws.send(page);
          setBytesSent((n) => n + page.byteLength);
        };

        await recorder.start();

        // 3) 레벨 미터. 말하고 있는데 소리가 안 나가는 상황을 눈으로 잡으려는 것이다.
        //    인코더와 별개의 스트림을 쓴다 — opus-recorder 내부 스트림을 건드리지 않는다.
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        });
        streamRef.current = stream;

        const ctx = new AudioContext();
        analyserCtxRef.current = ctx;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
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
        setError(
          err instanceof Error
            ? err.message
            : '마이크를 사용할 수 없습니다. 브라우저 권한을 확인해 주세요.',
        );
        setState('error');
      }
    },
    [cleanup],
  );

  return { state, error, level, bytesSent, start, stop };
}
