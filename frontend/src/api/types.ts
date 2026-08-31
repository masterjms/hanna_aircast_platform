/**
 * 백엔드 응답 타입.
 *
 * 백엔드 Pydantic 스키마와 손으로 맞춘다. 스키마가 커지면
 * openapi.json 에서 생성하는 쪽으로 바꾸는 게 좋다(지금은 과한 도구다).
 */

export type Role = 'super_admin' | 'village_admin';

export type DeviceStatusFilter = 'online' | 'offline' | 'unassigned';

/** 백엔드 errors.py 의 code 와 1:1. 프론트는 message 가 아니라 이 값으로 분기한다. */
export type ApiErrorCode =
  | 'UNAUTHORIZED'
  | 'INVALID_CREDENTIALS'
  | 'FORBIDDEN'
  | 'SUPER_ADMIN_REQUIRED'
  | 'VILLAGE_OUT_OF_SCOPE'
  | 'NOT_FOUND'
  | 'DEVICE_NOT_FOUND'
  | 'VILLAGE_NOT_FOUND'
  | 'ZONE_NOT_FOUND'
  | 'USER_NOT_FOUND'
  | 'CONFLICT'
  | 'DUPLICATE_USERNAME'
  | 'DEVICE_ALREADY_EXISTS'
  | 'BROADCAST_OVERLAP'
  | 'VALIDATION_FAILED'
  | 'MQTT_UNAVAILABLE'
  | (string & {});

export interface ApiErrorBody {
  error: { code: ApiErrorCode; message: string; detail?: Record<string, unknown> };
}

export interface VillageBrief {
  id: number;
  name: string;
}

export interface Me {
  id: number;
  username: string;
  role: Role;
  villages: VillageBrief[];
  all_villages: boolean;
  device_count: number;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: Me;
}

export interface Village extends VillageBrief {
  sido: string | null;
  sigungu: string | null;
  address_detail: string | null;
  /** 법정동코드 10자리(리까지) — 주소 검색이 채움. 리 경계 도형과의 조인 키 */
  b_code: string | null;
  road_address: string | null;
  jibun_address: string | null;
  lat: number | null;
  lng: number | null;
  created_at: string;
  /** MQTT 로 나가는 8자리 표현 */
  village_token: string;
  /** 등록된 단말 수(설치 현황) */
  device_count: number;
  /** 그중 지금 온라인인 수. 방송은 온라인 단말에만 나간다. */
  online_count: number;
}

export interface Zone {
  id: number;
  village_id: number;
  name: string;
  address_detail: string | null;
  lat: number | null;
  lng: number | null;
  created_at: string;
  device_count: number;
  online_count: number;
}

export interface Device {
  mac: string;
  label: string | null;
  village_id: number | null;
  village_name: string | null;
  zone_id: number | null;
  zone_name: string | null;
  firmware_version: string | null;
  last_seen_at: string | null;
  registered_at: string;
  /** 서버가 last_seen_at 으로 계산한 값 */
  online: boolean;
  rssi: number | null;
  state: string | null;
  /** 라이브 수신 상태 — OFF/PLAYING/RECONNECTING. RECONNECTING = 방송 중 무음 */
  live: string | null;
  config_version: number | null;
  ip: string | null;
  /** 단말별 MQTT 계정 발행 여부. false = 「미등록*」(계정 미발행 — 발행 필요) */
  has_credential: boolean;
  /** 등록(QR 스캔) 시점의 하드웨어 식별값 — 출하 당시 값, 실행 중 버전과 별개 */
  p4_model: string | null;
  p4_version: string | null;
  c6_model: string | null;
  c6_version: string | null;
  /** 설치 위치. 전부 null 이면 지도가 마을 좌표로 fallback */
  road_address: string | null;
  jibun_address: string | null;
  address_detail: string | null;
  lat: number | null;
  lng: number | null;
}

/** 신규 단말 등록용 사전 발급 비밀번호 — 등록 요청의 mqtt_password 로 되돌려 보내야 확정 */
export interface NewDevicePassword {
  password: string;
  /** 단말 @SERVER 에 넣을 호스트 (스킴·포트 없음) */
  server_host: string;
}

/** 신규 단말 등록 요청 (QR 스캔 5필드 + 사전 발급 비밀번호) */
export interface DeviceCreateRequest {
  mac: string;
  label?: string | null;
  village_id?: number | null;
  zone_id?: number | null;
  p4_model?: string | null;
  p4_version?: string | null;
  c6_model?: string | null;
  c6_version?: string | null;
  mqtt_password?: string | null;
}

export interface DeviceDetail extends Device {
  last_status: Record<string, unknown> | null;
}

/** 등록 화면이 표시하고 생산 라인이 시리얼(@MQTTID/@MQTTPW)로 단말에 넣는 값 */
export interface DeviceCredential {
  username: string;
  password: string;
  /** 단말 @SERVER 에 넣을 호스트 (스킴·포트 없음) */
  server_host: string;
  /** 이번 호출에서 새로 발행했는가. false = 기존 값 재사용 */
  issued: boolean;
}

export interface DeviceCounts {
  total: number;
  online: number;
  offline: number;
  unassigned: number;
}

export interface AlertItem {
  mac: string;
  label: string | null;
  village_name: string | null;
  reason: string;
  last_seen_at: string | null;
}

export interface ActiveBroadcast {
  id: number;
  job_id: number | null;
  event_type: string;
  target_scope: string;
  target_ids: string[];
  triggered_at: string;
}

export interface RecentEvent {
  id: number;
  event_type: string;
  target_scope: string;
  target_ids: string[];
  triggered_at: string;
  ended_at: string | null;
}

export interface DashboardSummary {
  scope: { all_villages: boolean; village_ids: number[] };
  devices: DeviceCounts;
  alerts: AlertItem[];
  active_broadcasts: ActiveBroadcast[];
  recent_events: RecentEvent[];
}

export interface SystemConfig {
  config_version: number;
  status_interval_sec: number;
  live_stats_interval_sec: number;
  event_qos: number;
  updated_at: string;
}

// ── 계정 (Phase 2) ───────────────────────────────────────────────────────
export interface User {
  id: number;
  username: string;
  role: Role;
  created_at: string;
  village_ids: number[];
}

export interface UserCreate {
  username: string;
  password: string;
  role: Role;
  village_ids: number[];
}

export interface UserUpdate {
  password?: string;
  role?: Role;
  village_ids?: number[];
}

export interface VillageInput {
  name: string;
  sido?: string | null;
  sigungu?: string | null;
  address_detail?: string | null;
  /** 아래 넷은 주소 검색 결과에서 그대로 옮겨 넣는다 — 사람이 치지 않는다 */
  b_code?: string | null;
  road_address?: string | null;
  jibun_address?: string | null;
  lat?: number | null;
  lng?: number | null;
}

/** GET /api/geo/address 결과 한 건 — 고르면 그대로 DB 로 들어간다 */
export interface AddressResult {
  address_name: string;
  road_address: string | null;
  jibun_address: string | null;
  /** 법정동코드 10자리 */
  b_code: string | null;
  lat: number;
  lng: number;
}

// ── 지도 (GET /api/dashboard/map) ────────────────────────────────────────
export interface MapPin {
  mac: string;
  label: string | null;
  lat: number;
  lng: number;
  online: boolean;
  village_id: number | null;
  village_name: string | null;
  /** OFF/PLAYING/RECONNECTING — RECONNECTING = 방송 중 무음 */
  live: string | null;
  marker: 'normal' | 'offline' | 'unassigned';
  /** 좌표 출처. village = 마을 좌표 fallback("이 근처 어딘가") */
  position_source: 'device' | 'zone' | 'village';
}

export interface MapVillage {
  id: number;
  name: string;
  b_code: string | null;
  lat: number | null;
  lng: number | null;
}

export interface MapData {
  /** 지도 SDK 로드용 JS 키. null 이면 서버 .env 미설정 */
  kakao_js_key: string | null;
  villages: MapVillage[];
  pins: MapPin[];
  /** 좌표가 전혀 없어 지도에 못 찍는 단말 MAC */
  missing_location: string[];
}

export interface ZoneInput {
  name: string;
  address_detail?: string | null;
  lat?: number | null;
  lng?: number | null;
}

// ── 파일 (Phase 3) ───────────────────────────────────────────────────────
export type FileSource = 'upload' | 'tts';

export interface AudioFile {
  id: number;
  filename: string;
  size_bytes: number;
  sha256: string;
  source: FileSource;
  duration_sec: number | null;
  tts_text: string | null;
  tts_lang: string | null;
  tts_voice: string | null;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  created_at: string;
}

// ── TTS (Phase 5) ────────────────────────────────────────────────────────
export interface TtsVoice {
  id: string;
  label: string;
  language: string;
  engine: string;
}

export interface VoiceCatalog {
  /** 언어 코드 → 표시 이름 */
  languages: Record<string, string>;
  voices: TtsVoice[];
}

export interface TtsRequest {
  text: string;
  language: string;
  voice?: string | null;
  filename?: string | null;
}

export interface TtsResult {
  file: AudioFile;
  /** true 면 기존 합성본을 재사용한 것이다(Polly 호출 없음) */
  cached: boolean;
}

// ── 방송 (Phase 3) ───────────────────────────────────────────────────────
export type TargetScope = 'device' | 'zone' | 'village' | 'all';

export interface FileBroadcastRequest {
  file_id: number;
  target_scope: TargetScope;
  target_ids: string[];
  store_flash?: boolean;
  autoplay?: boolean;
}

/** 단말 하나의 응답 상태. 진행 중 화면이 이걸로 카운트를 만든다. */
export interface DeviceResult {
  mac: string;
  label: string | null;
  result_type: string | null;
  ok: boolean | null;
  reason: string | null;
  /** 단말 STATUS 의 live. RECONNECTING 이면 무음 재접속 중 */
  live: string | null;
  /** LIVE_STATS 요약(버퍼·끊김). 결과가 아니라 수신 품질. */
  stats: string | null;
  received_at: string | null;
}

export interface BroadcastDetail {
  id: number;
  job_id: number | null;
  event_type: string;
  target_scope: TargetScope;
  target_ids: string[];
  file_id: number | null;
  file_name: string | null;
  triggered_at: string;
  ended_at: string | null;
  /** 발행 시점에 온라인이던 대상 단말 수 */
  target_count: number;
  results: DeviceResult[];

  // 실시간 방송에만 채워진다
  /** 단말이 GET 하는 Icecast 주소. /live/<job_id> */
  stream_url: string | null;
  /** 브라우저가 마이크를 밀어 넣을 WebSocket 경로 */
  ingest_path: string | null;
  /** 업링크(브라우저)가 붙어 있는지. false 면 무음이 나가는 중이다 */
  uplink_connected: boolean;
}

export interface LiveBroadcastRequest {
  target_scope: TargetScope;
  target_ids: string[];
  /** 단말 flash 녹음 여부. 10분을 넘길 방송은 꺼야 한다(통신 사양 §11.2). */
  record_flash: boolean;
}

/** 겹침(409) 시 error.detail 에 담겨 오는 모양. */
export interface BroadcastOverlapDetail {
  conflicts: { id: number; job_id: number | null; event_type: string; macs: string[] }[];
}
