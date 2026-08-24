/**
 * opus-recorder 타입 선언.
 *
 * 패키지가 .d.ts 를 제공하지 않아 우리가 실제로 쓰는 만큼만 선언한다.
 * 전체 API 를 옮겨 적지 않는다 — 안 쓰는 것까지 적으면 유지보수만 는다.
 */
declare module 'opus-recorder' {
  interface RecorderOptions {
    /** 인코더 워커 파일 URL. Vite 의 ?url import 로 넘긴다. */
    encoderPath?: string;
    encoderSampleRate?: number;
    numberOfChannels?: number;
    bitRate?: number;
    /** ms 단위 Opus 프레임 길이. 통신 사양은 40. */
    encoderFrameSize?: number;
    /** Ogg 페이지 하나에 담을 프레임 수. 1 이면 지연이 최소가 된다. */
    maxFramesPerPage?: number;
    /** true 면 녹음 중에 페이지를 그때그때 내보낸다. */
    streamPages?: boolean;
    /** 2048 = VOIP, 2049 = AUDIO */
    encoderApplication?: number;
    mediaTrackConstraints?: MediaTrackConstraints | boolean;
    /**
     * 이미 만들어 둔 소스 노드. 주면 opus-recorder 가 getUserMedia 를
     * 부르지 않는다 — 대신 스트림과 AudioContext 정리는 우리 몫이다.
     * 마이크를 두 번 열면 윈도우에서 NotReadableError 가 난다.
     */
    sourceNode?: MediaStreamAudioSourceNode;
  }

  export default class Recorder {
    constructor(options?: RecorderOptions);
    /** streamPages 가 true 면 Ogg 페이지마다 호출된다. */
    ondataavailable: (page: Uint8Array) => void;
    start(): Promise<void>;
    stop(): Promise<void>;
    pause(): Promise<void>;
    resume(): Promise<void>;
  }
}

declare module 'opus-recorder/dist/encoderWorker.min.js?url' {
  const url: string;
  export default url;
}
