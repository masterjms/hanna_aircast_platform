/**
 * API 클라이언트.
 *
 * fetch 를 한 겹만 감싼다. 여기서 하는 일:
 *   · Authorization 헤더 부착
 *   · 에러 응답을 ApiError 로 통일
 *   · 401 이면 토큰을 버리고 구독자에게 알린다(AuthContext 가 로그인 화면으로 보낸다)
 *
 * 데이터 캐싱은 하지 않는다. 화면 갱신이 폴링이라 캐시가 오히려 방해가 된다.
 */

import type {
  ApiErrorBody,
  ApiErrorCode,
  AudioFile,
  BroadcastDetail,
  DashboardSummary,
  Device,
  DeviceCredential,
  DeviceDetail,
  DeviceStatusFilter,
  FileBroadcastRequest,
  LiveBroadcastRequest,
  LoginResponse,
  Me,
  SystemConfig,
  TtsRequest,
  TtsResult,
  User,
  UserCreate,
  UserUpdate,
  Village,
  VillageInput,
  VoiceCatalog,
  Zone,
  ZoneInput,
} from './types';

const TOKEN_KEY = 'xwifi.token';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ApiErrorCode,
    message: string,
    readonly detail?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ── 토큰 보관 ────────────────────────────────────────────────────────────
// localStorage 를 쓴다. XSS 에 노출되는 대신 새로고침에 살아남는다.
// 운영 배포 전에 httpOnly 쿠키로 옮길지 재검토한다.
let token: string | null = localStorage.getItem(TOKEN_KEY);
let onUnauthorized: (() => void) | null = null;

export function setToken(next: string | null): void {
  token = next;
  if (next) localStorage.setItem(TOKEN_KEY, next);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getToken(): string | null {
  return token;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// ── 요청 ─────────────────────────────────────────────────────────────────
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const res = await fetch(path, { ...init, headers });

  if (res.status === 401) {
    setToken(null);
    onUnauthorized?.();
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const parsed: unknown = text ? JSON.parse(text) : null;

  if (!res.ok) {
    const body = parsed as ApiErrorBody | null;
    throw new ApiError(
      res.status,
      body?.error?.code ?? 'UNKNOWN',
      body?.error?.message ?? `요청이 실패했습니다 (HTTP ${res.status})`,
      body?.error?.detail,
    );
  }
  return parsed as T;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

// ── 엔드포인트 ───────────────────────────────────────────────────────────
export const api = {
  auth: {
    login: (username: string, password: string) =>
      request<LoginResponse>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      }),
    logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
    me: () => request<Me>('/api/auth/me'),
  },

  villages: {
    list: () => request<Village[]>('/api/villages'),
    get: (id: number) => request<Village>(`/api/villages/${id}`),
    create: (body: VillageInput) =>
      request<Village>('/api/villages', { method: 'POST', body: JSON.stringify(body) }),
    update: (id: number, body: Partial<VillageInput>) =>
      request<Village>(`/api/villages/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    remove: (id: number) => request<void>(`/api/villages/${id}`, { method: 'DELETE' }),

    zones: (villageId: number) => request<Zone[]>(`/api/villages/${villageId}/zones`),
    createZone: (villageId: number, body: ZoneInput) =>
      request<Zone>(`/api/villages/${villageId}/zones`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    updateZone: (zoneId: number, body: Partial<ZoneInput>) =>
      request<Zone>(`/api/zones/${zoneId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    removeZone: (zoneId: number) => request<void>(`/api/zones/${zoneId}`, { method: 'DELETE' }),
  },

  users: {
    list: () => request<User[]>('/api/users'),
    create: (body: UserCreate) =>
      request<User>('/api/users', { method: 'POST', body: JSON.stringify(body) }),
    update: (id: number, body: UserUpdate) =>
      request<User>(`/api/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    remove: (id: number) => request<void>(`/api/users/${id}`, { method: 'DELETE' }),
  },

  files: {
    list: () => request<AudioFile[]>('/api/files'),
    remove: (id: number) => request<void>(`/api/files/${id}`, { method: 'DELETE' }),

    /**
     * 오디오 업로드. multipart 라 request() 를 안 거친다
     * (Content-Type 을 브라우저가 boundary 와 함께 직접 정해야 한다).
     */
    upload: async (file: File): Promise<AudioFile> => {
      const form = new FormData();
      form.append('file', file);

      const headers = new Headers();
      if (token) headers.set('Authorization', `Bearer ${token}`);

      const res = await fetch('/api/files', { method: 'POST', body: form, headers });
      if (res.status === 401) {
        setToken(null);
        onUnauthorized?.();
      }
      const text = await res.text();
      const parsed: unknown = text ? JSON.parse(text) : null;
      if (!res.ok) {
        const body = parsed as ApiErrorBody | null;
        throw new ApiError(
          res.status,
          body?.error?.code ?? 'UNKNOWN',
          body?.error?.message ?? `업로드에 실패했습니다 (HTTP ${res.status})`,
          body?.error?.detail,
        );
      }
      return parsed as AudioFile;
    },

    /** 문구 → 파일함. 방송은 하지 않는다(방송 제어에서 따로 고른다). */
    tts: (body: TtsRequest) =>
      request<TtsResult>('/api/files/tts', { method: 'POST', body: JSON.stringify(body) }),

    voices: () => request<VoiceCatalog>('/api/tts/voices'),

    /** 관리자 미리듣기 URL. <audio src> 에 그대로 넣는다(토큰은 쿼리로 붙인다). */
    audioUrl: (id: number) =>
      `/api/files/${id}/audio${token ? `?access_token=${encodeURIComponent(token)}` : ''}`,
  },

  broadcast: {
    active: () => request<BroadcastDetail[]>('/api/broadcast/active'),
    get: (id: number) => request<BroadcastDetail>(`/api/broadcast/${id}`),
    fileStart: (body: FileBroadcastRequest) =>
      request<BroadcastDetail>('/api/broadcast/file/start', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    fileStop: (id: number) =>
      request<BroadcastDetail>(`/api/broadcast/file/stop`, {
        method: 'POST',
        body: JSON.stringify({ broadcast_id: id }),
      }),

    liveStart: (body: LiveBroadcastRequest) =>
      request<BroadcastDetail>('/api/broadcast/live/start', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    liveStop: (id: number) =>
      request<BroadcastDetail>('/api/broadcast/live/stop', {
        method: 'POST',
        body: JSON.stringify({ broadcast_id: id }),
      }),
  },

  devices: {
    list: (filters: {
      village_id?: number;
      zone_id?: number;
      status?: DeviceStatusFilter;
      q?: string;
    } = {}) => request<Device[]>(`/api/devices${query(filters)}`),
    unassigned: () => request<Device[]>('/api/devices/unassigned'),
    get: (mac: string) => request<DeviceDetail>(`/api/devices/${mac}`),
    update: (
      mac: string,
      patch: { label?: string | null; village_id?: number | null; zone_id?: number | null },
    ) =>
      request<DeviceDetail>(`/api/devices/${mac}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      }),
    remove: (mac: string) => request<void>(`/api/devices/${mac}`, { method: 'DELETE' }),
    /** 계정 발행/조회 (super_admin). 이미 있으면 재사용, reissue 는 라인 재작업 전용 */
    credential: (mac: string, reissue = false) =>
      request<DeviceCredential>(`/api/devices/${mac}/credential`, {
        method: 'POST',
        body: JSON.stringify({ reissue }),
      }),
  },

  dashboard: {
    summary: () => request<DashboardSummary>('/api/dashboard/summary'),
  },

  config: {
    get: () => request<SystemConfig>('/api/config'),
    update: (patch: Partial<Pick<SystemConfig, 'status_interval_sec' | 'live_stats_interval_sec' | 'event_qos'>>) =>
      request<SystemConfig>('/api/config', { method: 'PUT', body: JSON.stringify(patch) }),
  },
};
