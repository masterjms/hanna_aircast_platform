/**
 * 방송하기 — 이장님 한 화면(2026-09-21 개편).
 *
 * 예전에는 방송이 세 화면에 흩어져 있었다: 방송 제어(대상·송출), 파일함(TTS 만들기),
 * 스케줄(예약). 쓰는 사람은 대부분 50대 이상 마을 이장님이라, 한 화면에서 위에서 아래로
 * 내려가며 끝나게 모았다.
 *
 *   ① 어떻게   마이크로 말하기 · 글로 써서 방송 · 저장된 소리 틀기  (큰 버튼 셋)
 *   ② 어디에   담당 마을이 하나면 저절로 골라진다(누를 것 없음)
 *   ③ 무엇을   글이면 큰 입력칸 + 「먼저 들어보기」, 소리면 목록에서 고르기
 *   ④ 언제     지금 바로 · 예약(한 번 · 매일 · 매주)
 *   맨 아래    한 문장 요약 + 큰 버튼 하나
 *
 * 방송 중이면 맨 위에 빨간 「방송 끄기」가 늘 보인다.
 *
 * 기능은 그대로다 — 서버에 보내는 요청은 예전 방송 제어·TTS·스케줄과 같다. 세부 조작
 * (구역·단말 골라 보내기, 단말 저장, 녹음 저장)은 「자세히」 안으로 넣었을 뿐 지우지 않았다.
 * 대상 단말이 이미 다른 방송에 잡혀 있으면 서버가 409 로 거절한다 — 진행 중인 방송을
 * 자동으로 끊지 않고 어느 방송과 겹치는지 보여준다.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api, getToken } from '../api/client';
import type {
  AudioFile,
  BroadcastDetail,
  BroadcastOverlapDetail,
  Device,
  Organization,
  Repeat,
  ScheduleInput,
  TargetScope,
  Village,
  VoiceCatalog,
  Zone,
} from '../api/types';
import { useAuth } from '../auth/AuthContext';
import {
  EMPTY_PICK,
  TargetTreePicker,
  summarize,
  type PickMode,
  type Picked,
} from '../components/broadcast/TargetTreePicker';
import { uplinkBlockedReason, useMicUplink } from '../hooks/useMicUplink';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';
import { WEEKDAY_LABELS, dateLabel, formatTime as clock, kstToday } from '../lib/schedule';

type Method = 'mic' | 'tts' | 'file';
type When = 'now' | 'reserve';
/** 시선을 끌 곳 — 아직 안 끝난 첫 단계. 다 됐으면 맨 아래 버튼. */
type Guide = 1 | 2 | 3 | 4 | 'go';
type ReserveKind = 'once' | 'daily' | 'weekly';

const MAX_TEXT = 1000;

/** 자주 쓰는 첫마디·끝말 — 누르면 글 입력칸에 붙는다. 이장님이 매번 치지 않게. */
const PHRASES = [
  '주민 여러분께 알려드립니다.',
  '다시 한번 알려드립니다.',
  '이상 마을회관에서 알려드렸습니다.',
];

/** 대상 대수 표시. 방송은 온라인 단말에만 나가므로 꺼진 단말이 있으면 눈에 띄게 한다. */
function DeviceCount({ online, total }: { online: number; total: number }) {
  if (total === 0) return <span className="dim">(단말 없음)</span>;
  if (online === 0) return <span className="count count--none">(전부 꺼짐 · {total}대)</span>;
  if (online < total)
    return (
      <span className="count count--partial">
        (켜짐 {online}/{total}대)
      </span>
    );
  return <span className="dim">(켜짐 {online}대)</span>;
}

/** 지금 몇 초째인지. 1초마다 다시 그린다. */
function useElapsedSec(since: string): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
}

function mmss(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}분 ${String(s).padStart(2, '0')}초` : `${s}초`;
}

/** 방송 한 건의 단말별 응답 — 「자세히」를 눌렀을 때만 보인다. */
function ResultList({ broadcast }: { broadcast: BroadcastDetail }) {
  if (broadcast.results.length === 0) return <p className="hint">아직 응답한 단말이 없습니다.</p>;
  return (
    <ul className="result-list">
      {broadcast.results.map((r) => (
        <li key={r.mac}>
          <span className={`badge badge--${r.ok === true ? 'ok' : r.ok === false ? 'danger' : 'idle'}`}>
            {r.ok === true ? '정상' : r.ok === false ? '실패' : '대기'}
          </span>
          <span className="strong">{r.label ?? r.mac}</span>
          {r.reason && <span className="dim">{r.reason}</span>}
          {r.live === 'RECONNECTING' && <span className="badge badge--warn">재접속 중 · 무음</span>}
        </li>
      ))}
    </ul>
  );
}

/** 지금 나가는 방송 한 건 — 빨간 띠, 큰 「방송 끄기」. */
function OnAir({
  broadcast,
  onStop,
  busy,
  readyWaitSec,
}: {
  broadcast: BroadcastDetail;
  onStop: (b: BroadcastDetail) => void;
  busy: boolean;
  readyWaitSec: number;
}) {
  const [open, setOpen] = useState(false);
  const isLive = broadcast.event_type.startsWith('LIVE');
  const done = broadcast.results.filter((r) => r.ok === true).length;
  const failed = broadcast.results.filter((r) => r.ok === false).length;
  // results 는 단말당 1행이라 행 수가 곧 대수다.
  const total = Math.max(broadcast.expected_count ?? broadcast.target_count, broadcast.results.length);
  const stopping = broadcast.stop_requested_at !== null && broadcast.ended_at === null;
  const elapsed = useElapsedSec(broadcast.triggered_at);
  // 라이브인데 기준 시간이 지나도 전부 준비되지 않았다 — 판단은 방송하는 사람 몫이라 알리기만.
  const readyLagging = isLive && !stopping && elapsed >= readyWaitSec && done < total;

  return (
    <section className="onair" aria-live="polite">
      <div className="onair__main">
        <div className="onair__text">
          <div className="onair__eyebrow">
            <span className="onair__dot" aria-hidden="true" />
            {stopping ? '끄는 중' : '지금 방송 중'} · {mmss(elapsed)}
          </div>
          <div className="onair__title">
            {isLive ? '마이크 방송' : (broadcast.file_name?.replace(/\.mp3$/i, '') ?? '파일 방송')}
          </div>
          <div className="onair__where">
            {broadcast.target_label || '대상'} · {isLive ? '준비' : '재생'} {done}/{total}대
            {failed > 0 && <span className="onair__fail"> · 실패 {failed}대</span>}
          </div>
          {isLive && !broadcast.uplink_connected && !stopping && (
            <div className="onair__warn">마이크가 연결되지 않아 소리가 나가지 않고 있습니다.</div>
          )}
          {readyLagging && (
            <div className="onair__warn">
              {total - done}대가 아직 준비되지 않았습니다. 그대로 말씀하셔도 되고, 끄고 다시
              하셔도 됩니다.
            </div>
          )}
          {stopping && (
            <div className="onair__note">단말이 끝났다고 알려오면 사라집니다.</div>
          )}
        </div>
        <button
          type="button"
          className="bc-btn bc-btn--stop"
          onClick={() => onStop(broadcast)}
          disabled={busy || stopping}
        >
          {stopping ? '끄는 중…' : '방송 끄기'}
        </button>
      </div>
      <button
        type="button"
        className="onair__more"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '단말별 상태 닫기' : '단말별 상태 보기'}
      </button>
      {open && <ResultList broadcast={broadcast} />}
    </section>
  );
}

function StepTitle({ n, done, children }: { n: number; done?: boolean; children: React.ReactNode }) {
  return (
    <h2 className="bc-step__title">
      <span className={`bc-step__num${done ? ' is-done' : ''}`} aria-hidden="true">
        {done ? '✓' : n}
      </span>
      {children}
    </h2>
  );
}

function MethodTile({
  on,
  icon,
  title,
  sub,
  onClick,
  disabled,
}: {
  on: boolean;
  icon: React.ReactNode;
  title: string;
  sub: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`bc-tile${on ? ' is-on' : ''}`}
      aria-pressed={on}
      onClick={onClick}
      disabled={disabled}
    >
      <span className="bc-tile__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="bc-tile__title">{title}</span>
      <span className="bc-tile__sub">{sub}</span>
    </button>
  );
}

const IconMic = (
  <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3M8.5 21h7" />
  </svg>
);
const IconPen = (
  <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 20h4L19 9l-4-4L4 16v4z" />
    <path d="M13.5 6.5l4 4" />
  </svg>
);
const IconSound = (
  <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 9v6h4l5 4V5L8 9H4z" />
    <path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" />
  </svg>
);

export function BroadcastPage() {
  const { isSuperAdmin } = useAuth();

  // ── ① 어떻게 ──
  // 처음엔 아무것도 골라 두지 않는다 — 시선 안내가 ① 부터 차례로 흐르게(2026-09-21 현장 의견).
  const [method, setMethod] = useState<Method | null>(null);

  // ── ② 어디에 ── (트리 선택은 예전 방송 제어와 같다 — 향후검토 7·8·9번)
  const [scope, setScope] = useState<TargetScope>('village');
  const [picks, setPicks] = useState<Record<PickMode, Picked>>({
    village: EMPTY_PICK,
    zone: EMPTY_PICK,
    device: EMPTY_PICK,
  });
  const [zonesOf, setZonesOf] = useState<Record<number, Zone[] | undefined>>({});
  const [pickerOpen, setPickerOpen] = useState(false);

  // ── ③ 무엇을 ──
  const [text, setText] = useState('');
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);
  const [language, setLanguage] = useState('ko-KR');
  const [voice, setVoice] = useState('');
  /** 들어보기로 만든 음성. 문구·목소리가 같으면 방송할 때 다시 만들지 않는다. */
  const [made, setMade] = useState<{ file: AudioFile; key: string; cached: boolean } | null>(null);
  const [fileId, setFileId] = useState<number | ''>('');
  const [fileQuery, setFileQuery] = useState('');
  const [listenId, setListenId] = useState<number | null>(null);

  // ── ④ 언제 ──
  const [when, setWhen] = useState<When>('now');
  const [kind, setKind] = useState<ReserveKind>('once');
  const [onceDate, setOnceDate] = useState(() => kstToday(1));
  const [weekdays, setWeekdays] = useState<number[]>([]);
  const [hour24, setHour24] = useState(7);
  const [minute, setMinute] = useState(0);

  // ── 자세히(드물게 쓰는 것) ──
  const [storeFlash, setStoreFlash] = useState(false);
  // 단말 flash 녹음. 기본 켬 — 10분을 넘길 긴 방송만 끄면 된다(사양 §11.2).
  const [recordFlash, setRecordFlash] = useState(true);

  /**
   * 라이브 준비 지연 알림 기준(초) = 단말에 보낸 ready_timeout_sec + 5(펌웨어 상수).
   * 못 읽으면 기본 30+5.
   */
  const [readyWaitSec, setReadyWaitSec] = useState(35);
  const [liveBitrateKbps, setLiveBitrateKbps] = useState(24);

  const [villages, setVillages] = useState<Village[]>([]);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [files, setFiles] = useState<AudioFile[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [overlap, setOverlap] = useState<BroadcastOverlapDetail | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyText, setBusyText] = useState('');

  const [liveId, setLiveId] = useState<number | null>(null);
  const mic = useMicUplink();
  /** 실시간 방송을 시작한 시각(ms). 시작 직후의 옛 목록으로 "끝났다"고 오판하지 않게 한다. */
  const liveStartedAt = useRef(0);
  const micBlocked = uplinkBlockedReason();
  const previewRef = useRef<HTMLAudioElement>(null);
  const listenRef = useRef<HTMLAudioElement>(null);

  const active = usePolling(() => api.broadcast.active(), POLL_INTERVAL.broadcasting);

  useEffect(() => {
    void (async () => {
      try {
        const [v, f, d, o] = await Promise.all([
          api.villages.list(),
          api.files.list(),
          api.devices.list(),
          api.organizations.list().catch(() => [] as Organization[]),
        ]);
        setVillages(v);
        setOrgs(o);
        setFiles(f);
        setDevices(d);
        // 담당 마을이 하나뿐이면(이장님 대부분) 그 마을을 미리 골라 둔다 — 누를 것이 없다.
        if (v.length === 1 && v[0].online_count > 0) {
          setPicks((p) => ({ ...p, village: { leaves: [String(v[0].id)], groups: [] } }));
        } else {
          // 마을이 여럿이면 고르는 트리를 펼친 채 둔다. 하나 체크했다고 닫히면 여러 곳을 못 고른다.
          setPickerOpen(true);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '기본 정보를 불러오지 못했습니다.');
      }
    })();
    void api.files
      .voices()
      .then(setCatalog)
      .catch(() => undefined);
    // 설정은 자주 바뀌지 않으니 한 번만 읽는다. 실패해도 기본값으로 돈다.
    void api.config
      .get()
      .then((c) => {
        setReadyWaitSec(c.live_ready_timeout_sec + 5);
        setLiveBitrateKbps(c.live_bitrate_kbps);
      })
      .catch(() => undefined);
  }, []);

  const voicesForLanguage = useMemo(
    () => catalog?.voices.filter((v) => v.language === language) ?? [],
    [catalog, language],
  );
  useEffect(() => {
    setVoice((cur) => (voicesForLanguage.some((v) => v.id === cur) ? cur : (voicesForLanguage[0]?.id ?? '')));
  }, [voicesForLanguage]);

  /** 구역은 마을마다 따로 읽는다 — 트리에서 펼칠 때 한 번만. */
  const loadZones = useCallback((villageId: number) => {
    setZonesOf((prev) => (villageId in prev ? prev : { ...prev, [villageId]: undefined }));
    void api.villages
      .zones(villageId)
      .then((z) => setZonesOf((prev) => ({ ...prev, [villageId]: z })))
      .catch(() => setZonesOf((prev) => ({ ...prev, [villageId]: [] })));
  }, []);
  useEffect(() => {
    if (scope !== 'zone' || villages.length > 50) return;
    for (const v of villages) if (!(v.id in zonesOf)) loadZones(v.id);
  }, [scope, villages, zonesOf, loadZones]);

  // ── 들어보기로 만든 음성의 뒷정리 ──
  // 서버는 합성 즉시 파일함에 넣는다. 들어보고 방송하지 않은 음성이 쌓이지 않게, 문구를
  // 바꿔 다시 만들거나 화면을 떠날 때 지운다. 방송·예약에 쓴 것과 예전에 만든 것을
  // 재사용한 것(cached)은 지우지 않는다.
  const unused = useRef<number | null>(null);
  const discardUnused = () => {
    const id = unused.current;
    unused.current = null;
    if (id !== null) void api.files.remove(id).catch(() => undefined);
  };
  useEffect(() => () => discardUnused(), []);

  const pickMode: PickMode | null = scope === 'all' ? null : (scope as PickMode);
  const pick = pickMode ? picks[pickMode] : EMPTY_PICK;
  const targetIds = (): string[] => (pickMode ? picks[pickMode].leaves : []);

  const selectionLabels = useMemo(
    () => (pickMode ? summarize(pick, orgs, villages, pickMode, devices, zonesOf) : []),
    [pick, pickMode, orgs, villages, devices, zonesOf],
  );

  /** 고른 곳의 켜진 단말 수 — 요약 문장과 버튼에 쓴다. */
  const onlineTargets = useMemo(() => {
    if (scope === 'all') return villages.reduce((n, v) => n + v.online_count, 0);
    const ids = new Set(pick.leaves);
    if (scope === 'village') return villages.filter((v) => ids.has(String(v.id))).reduce((n, v) => n + v.online_count, 0);
    if (scope === 'device') return devices.filter((d) => ids.has(d.mac) && d.online).length;
    return Object.values(zonesOf)
      .flatMap((z) => z ?? [])
      .filter((z) => ids.has(String(z.id)))
      .reduce((n, z) => n + z.online_count, 0);
  }, [scope, pick, villages, devices, zonesOf]);

  /** 고른 곳의 단말 — 고르기를 접어 둔 동안 「어느 스피커에서 나가는지」를 보여준다. */
  const targetDevices = useMemo(() => {
    const ids = new Set(pick.leaves);
    const hit =
      scope === 'all'
        ? devices.filter((d) => d.village_id !== null)
        : scope === 'village'
          ? devices.filter((d) => d.village_id !== null && ids.has(String(d.village_id)))
          : scope === 'zone'
            ? devices.filter((d) => d.zone_id !== null && ids.has(String(d.zone_id)))
            : devices.filter((d) => ids.has(d.mac));
    // 켜진 것 먼저 — 꺼진 단말은 방송이 안 나가니 아래에 흐리게.
    return [...hit].sort((x, y) => Number(y.online) - Number(x.online));
  }, [scope, pick, devices]);

  const targetText =
    scope === 'all'
      ? '모든 마을'
      : selectionLabels.length === 0
        ? ''
        : `${selectionLabels.slice(0, 3).join(', ')}${selectionLabels.length > 3 ? ` 외 ${selectionLabels.length - 3}곳` : ''}`;

  // 예약은 마을·단말 대상만 된다 — 스케줄 대상에 구역·전체가 없다.
  const reservable = scope === 'village' || scope === 'device';
  const effectiveWhen: When = method === 'mic' ? 'now' : when;

  const ttsKey = `${language}|${voice}|${text.trim()}`;
  const fireTime = `${String(hour24).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00`;
  const whenText =
    effectiveWhen === 'now'
      ? '지금 바로'
      : kind === 'once'
        ? `${dateLabel(onceDate)} ${clock(fireTime)}에 한 번`
        : kind === 'daily'
          ? `매일 ${clock(fireTime)}에`
          : `매주 ${weekdays.map((d) => WEEKDAY_LABELS[d]).join('·') || '(요일)'} ${clock(fireTime)}에`;

  const selectedFile = files.find((f) => f.id === fileId);

  /** 지금 무엇이 빠졌는지 — 어느 단계인지와 버튼 위 한 줄 안내. 없으면 null. */
  const need: { step: 1 | 2 | 3 | 4; text: string } | null = (() => {
    if (!method) return { step: 1, text: '① 방송 방법을 골라 주세요.' };
    if (scope !== 'all' && pick.leaves.length === 0) return { step: 2, text: '② 방송할 곳을 골라 주세요.' };
    if (method === 'tts' && !text.trim()) return { step: 3, text: '③ 방송할 글을 적어 주세요.' };
    if (method === 'tts' && text.length > MAX_TEXT) return { step: 3, text: `글은 ${MAX_TEXT}자까지입니다.` };
    if (method === 'file' && fileId === '') return { step: 3, text: '③ 틀 소리를 골라 주세요.' };
    if (method === 'mic' && micBlocked) return { step: 1, text: micBlocked };
    if (effectiveWhen === 'reserve') {
      if (!reservable)
        return { step: 2, text: '구역·전체 방송은 예약할 수 없습니다. ②에서 마을이나 단말을 골라 주세요.' };
      if (kind === 'weekly' && weekdays.length === 0) return { step: 4, text: '④ 요일을 골라 주세요.' };
      if (kind === 'once' && !onceDate) return { step: 4, text: '④ 날짜를 골라 주세요.' };
      // 지난 시각이면 서버도 막지만 문구가 막연하다 — 여기서 먼저 알린다.
      if (kind === 'once' && new Date(`${onceDate}T${fireTime}+09:00`).getTime() <= Date.now())
        return { step: 4, text: '④ 이미 지난 시각입니다. 날짜나 시각을 바꿔 주세요.' };
    }
    return null;
  })();
  const missing = need?.text ?? null;

  /** 서버 쪽 장애(방송 서버·MQTT·5xx) — 이장님께 기술 문구를 그대로 보이지 않는다. */
  const OUTAGE_CODES = new Set(['ICECAST_UNAVAILABLE', 'MQTT_UNAVAILABLE']);
  const failWith = (err: unknown, fallback: string) => {
    setErrorDetail(null);
    if (err instanceof ApiError && err.code === 'BROADCAST_OVERLAP') {
      setOverlap(err.detail as unknown as BroadcastOverlapDetail);
      setError('고른 곳 중에 이미 다른 방송이 나가고 있는 단말이 있습니다. 그 방송을 끄고 다시 해 주세요.');
    } else if (err instanceof ApiError && (OUTAGE_CODES.has(err.code) || err.status >= 500)) {
      setError('방송 서버에 연결하지 못했습니다. 잠시 뒤 다시 해 보시고, 계속되면 관리자에게 알려 주세요.');
      setErrorDetail(err.message); // 관리자가 원인을 볼 수 있게 작게 남긴다
    } else {
      setError(err instanceof ApiError ? err.message : fallback);
    }
  };

  /** 글 → 음성. 같은 문구·목소리로 이미 만들었으면 그것을 쓴다. */
  const ensureTts = async (): Promise<AudioFile> => {
    if (made && made.key === ttsKey) return made.file;
    discardUnused();
    const res = await api.files.tts({ text: text.trim(), language, voice: voice || null, filename: null });
    setMade({ file: res.file, key: ttsKey, cached: res.cached });
    unused.current = res.cached ? null : res.file.id;
    return res.file;
  };

  const preview = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setBusyText('음성을 만드는 중…');
    setError(null);
    try {
      await ensureTts();
      queueMicrotask(() => void previewRef.current?.play().catch(() => undefined));
    } catch (err) {
      failWith(err, '음성을 만들지 못했습니다.');
    } finally {
      setBusy(false);
      setBusyText('');
    }
  };

  const startLive = async () => {
    const b = await api.broadcast.liveStart({
      target_scope: scope,
      target_ids: targetIds(),
      record_flash: recordFlash,
    });
    liveStartedAt.current = Date.now();
    setLiveId(b.id);
    active.reload();
    // 마이크를 그 세션에 물린다. 실패하면(권한 거부·장치 없음·연결 실패) 소리 없는 방송이
    // 켜진 채 남지 않게 방송 세션을 곧바로 되돌린다. 이유는 mic.error 가 화면에 보인다.
    const token = getToken();
    const ok = b.job_id !== null && token !== null && (await mic.start(b.job_id, token, liveBitrateKbps));
    if (!ok) {
      setLiveId(null);
      await api.broadcast.liveStop(b.id).catch(() => undefined);
      active.reload();
      return false;
    }
    return true;
  };

  const go = async () => {
    if (missing || !method) return;
    setBusy(true);
    setError(null);
    setOverlap(null);
    setDone(null);
    try {
      if (method === 'mic') {
        setBusyText('마이크를 연결하는 중…');
        if (await startLive()) {
          setDone('마이크 방송을 시작했습니다. 말씀하세요. 끝나면 위의 「방송 끄기」를 누르세요.');
        }
        return;
      }
      let file: AudioFile | undefined = selectedFile;
      if (method === 'tts') {
        setBusyText('음성을 만드는 중…');
        file = await ensureTts();
      }
      if (!file) throw new Error('틀 소리를 찾지 못했습니다.');

      if (effectiveWhen === 'now') {
        setBusyText('방송을 보내는 중…');
        await api.broadcast.fileStart({
          file_id: file.id,
          target_scope: scope,
          target_ids: targetIds(),
          store_flash: storeFlash,
          autoplay: true,
        });
        unused.current = null; // 방송에 썼다 — 지우지 않는다
        active.reload();
        setDone(`방송을 시작했습니다 — ${targetText || '모든 마을'}. 위에서 진행 상황을 볼 수 있습니다.`);
      } else {
        setBusyText('예약하는 중…');
        const body: ScheduleInput = {
          repeat: kind as Repeat,
          once_date: kind === 'once' ? onceDate : null,
          weekdays: kind === 'weekly' ? weekdays : null,
          fire_time: fireTime,
          file_id: file.id,
          target_scope: scope === 'device' ? 'device' : 'village',
          target_ids: targetIds(),
          store_flash: storeFlash || kind !== 'once', // 반복 방송은 단말에 저장해 두는 쪽이 유리하다
          enabled: true,
        };
        await api.schedules.create(body);
        unused.current = null; // 예약에 썼다 — 지우지 않는다
        setDone(`예약했습니다 — ${whenText} ${targetText}에 방송합니다.`);
      }
      // 파일함 목록에 새 음성이 보이도록 다시 읽는다.
      void api.files.list().then(setFiles).catch(() => undefined);
    } catch (err) {
      failWith(err, effectiveWhen === 'reserve' ? '예약하지 못했습니다.' : '방송을 시작하지 못했습니다.');
    } finally {
      setBusy(false);
      setBusyText('');
    }
  };

  const stop = useCallback(
    async (broadcast: BroadcastDetail) => {
      setBusy(true);
      setError(null);
      try {
        if (broadcast.event_type.startsWith('LIVE')) {
          // 마이크를 먼저 끊는다. 서버가 세션을 지운 뒤에 끊으면 업링크 없는 세션이 잠깐 남는다.
          mic.stop();
          setLiveId(null);
          await api.broadcast.liveStop(broadcast.id);
        } else {
          await api.broadcast.fileStop(broadcast.id);
        }
        setDone(null);
        active.reload();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '끄지 못했습니다.');
      } finally {
        setBusy(false);
      }
    },
    [active, mic],
  );

  const running = active.data ?? [];

  // 두 칸 배치에서 칸 높이가 모자라면(낮은 창·방송 중 띠) 촘촘한 배치로 바꾼다. 촘촘해지면
  // 칸이 조금 커지므로, 경계에서 깜빡이지 않게 돌아오는 기준은 더 높게 잡는다.
  const colsRef = useRef<HTMLDivElement>(null);
  const [tight, setTight] = useState(false);
  useEffect(() => {
    const el = colsRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      const h = el.clientHeight;
      setTight((prev) => (prev ? h < 840 : h < 790));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 방송이 서버 쪽에서 끝났는데(다른 창에서 끔 등) 마이크가 남아 있으면 정리한다.
  // 시작 시각보다 오래된 목록으로는 판단하지 않는다(핸드셰이크 도중에 끊지 않게).
  useEffect(() => {
    if (liveId === null) return;
    if (active.fetchedAt <= liveStartedAt.current) return;
    if (running.some((b) => b.id === liveId)) return;
    mic.stop();
    setLiveId(null);
  }, [liveId, running, mic, active.fetchedAt]);

  const filteredFiles = useMemo(() => {
    const q = fileQuery.trim().toLowerCase();
    return q ? files.filter((f) => f.filename.toLowerCase().includes(q)) : files;
  }, [files, fileQuery]);

  const hourOptions = Array.from({ length: 24 }, (_, h) => h);
  const minuteOptions = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];

  const goLabel =
    method === 'mic'
      ? '마이크 방송 시작'
      : effectiveWhen === 'reserve'
        ? '예약하기'
        : '지금 방송하기';

  // 시선 안내 — 아직 안 끝난 첫 단계의 테두리를 은은하게 밝힌다. 다 됐으면 맨 아래 버튼.
  // 방송을 거는 중이거나 방송이 나가는 중에는 끈다(그때 볼 곳은 맨 위 빨간 띠다).
  const guide: Guide | null = busy || liveId !== null || running.length > 0 ? null : need ? need.step : 'go';
  // ✓ 표시는 안내를 끈 동안에도 그대로 둔다 — 채운 단계는 채운 단계다.
  const progress = need ? need.step : 5;
  const isDone = (n: 1 | 2 | 3 | 4) => progress > n;
  const stepClass = (n: 1 | 2 | 3 | 4, extra: string) =>
    `bc-step ${extra}${guide === n ? ' is-guide' : ''}${isDone(n) ? ' is-done' : ''}`;

  return (
    <div className={`bc${tight ? ' bc--tight' : ''}`}>
      <div className="bc-top">
        {running.length > 0 && (
          <div className="onair-list">
            {running.map((b) => (
              <OnAir key={b.id} broadcast={b} onStop={(x) => void stop(x)} busy={busy} readyWaitSec={readyWaitSec} />
            ))}
          </div>
        )}

        {error && (
          <div className="bc-alert" role="alert">
            {error}
            {errorDetail && <div className="bc-alert__detail">{errorDetail}</div>}
            {overlap && (
              <ul>
                {overlap.conflicts.map((c) => (
                  <li key={c.id}>
                    {c.event_type.startsWith('LIVE') ? '마이크 방송' : '파일 방송'} — 겹치는 단말 {c.macs.length}대
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {/* 지금 방송은 빨간 띠가 곧 결과다 — 같은 말을 두 번 띄워 자리를 먹지 않는다. */}
      {done && (done.startsWith('예약') || running.length === 0) && (
          <div className="bc-done" role="status">
            {done}
            {done.startsWith('예약') && (
              <>
                {' '}
                <Link to="/schedules">예약 목록 보기</Link>
              </>
            )}
          </div>
        )}
        {mic.error && <div className="bc-alert">{mic.error}</div>}

      </div>

      <div className="bc-cols" ref={colsRef}>
        <div className="bc-col">
          {/* ① 어떻게 */}
          <section className={stepClass(1, 'bc-step--how')}>
            <StepTitle n={1} done={isDone(1)}>어떻게 방송할까요?</StepTitle>
            <div className="bc-tiles">
              <MethodTile on={method === 'mic'} icon={IconMic} title="마이크로 말하기" sub="지금 바로 말로 방송" onClick={() => setMethod('mic')} />
              <MethodTile on={method === 'tts'} icon={IconPen} title="글로 써서 방송" sub="적은 글을 읽어 줍니다" onClick={() => setMethod('tts')} />
              <MethodTile on={method === 'file'} icon={IconSound} title="저장된 소리 틀기" sub="만들어 둔 방송" onClick={() => setMethod('file')} />
            </div>
          </section>

          {/* ② 어디에 */}
          <section className={stepClass(2, 'bc-step--where')}>
            <StepTitle n={2} done={isDone(2)}>어디에 방송할까요?</StepTitle>
            <div className="bc-where">
              <div className="bc-where__now">
                {scope === 'all' ? (
                  <strong>모든 마을</strong>
                ) : targetText ? (
                  <strong>{targetText}</strong>
                ) : (
                  <span className="dim">아직 고르지 않았습니다</span>
                )}
                {(scope === 'all' || targetText) && <span className="bc-where__count">켜진 단말 {onlineTargets}대</span>}
              </div>
              <button type="button" className="bc-link" aria-expanded={pickerOpen} onClick={() => setPickerOpen((v) => !v)}>
                {pickerOpen ? '고르기 닫기' : villages.length === 1 ? '단말·구역만 골라 보내기' : '방송할 곳 고르기'}
              </button>
            </div>
            {pickerOpen && (
              <div className="bc-picker">
                <div className="bc-seg" role="group" aria-label="고르는 단위">
                  {(['village', 'zone', 'device', ...(isSuperAdmin ? ['all' as const] : [])] as TargetScope[]).map((s) => (
                    <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}>
                      {s === 'village' ? '마을' : s === 'zone' ? '구역' : s === 'device' ? '단말 하나하나' : '모든 마을'}
                    </button>
                  ))}
                </div>
                {pickMode ? (
                  <TargetTreePicker
                    mode={pickMode}
                    orgs={orgs}
                    villages={villages}
                    devices={devices}
                    zonesOf={zonesOf}
                    onNeedZones={loadZones}
                    value={pick}
                    onChange={(next) => setPicks((prev) => ({ ...prev, [pickMode]: next }))}
                    renderCount={(online, total) => <DeviceCount online={online} total={total} />}
                  />
                ) : (
                  <p className="hint hint--warn">배정된 모든 마을의 켜진 단말에 나갑니다. 대상이 넓으니 내용을 다시 확인하세요.</p>
                )}
              </div>
            )}
            {!pickerOpen && targetDevices.length > 0 && (
              <div className="bc-speakers">
                <div className="bc-speakers__head">방송이 나갈 단말</div>
                <ul>
                  {targetDevices.map((d) => (
                    <li key={d.mac} className={d.online ? undefined : 'is-off'}>
                      <span className={`bc-speakers__dot${d.online ? ' is-on' : ''}`} aria-hidden="true" />
                      <span className="bc-speakers__name">{d.label || d.mac}</span>
                      {d.zone_name && <span className="dim">{d.zone_name}</span>}
                      <span className="bc-speakers__state">{d.online ? '켜짐' : '꺼짐 — 방송 안 나감'}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </div>

        <div className="bc-col">
          {/* ③ 자리 — 방법을 고르기 전에는 빈 칸 대신 안내를 둔다(고르면 칸이 바뀐다) */}
          {!method && (
            <section className="bc-step bc-step--what bc-step--wait">
              <StepTitle n={3}>무엇을 방송할까요?</StepTitle>
              <p className="bc-wait">왼쪽 ①에서 방송 방법을 고르면 여기에 내용을 넣는 칸이 나옵니다.</p>
            </section>
          )}

          {/* ③ (마이크) — 말하기 안내와 소리 막대 */}
          {method === 'mic' && (
            <section className={stepClass(3, 'bc-step--what bc-step--mic')}>
              <StepTitle n={3} done={liveId !== null}>마이크에 대고 말씀하세요</StepTitle>
              {liveId !== null && mic.state !== 'idle' ? (
                <div className="bc-live" aria-live="polite">
                  <div className="bc-live__meter" aria-hidden="true">
                    {Array.from({ length: 16 }, (_, i) => (
                      <span key={i} className={mic.level * 16 > i ? 'is-on' : undefined} style={{ height: `${18 + i * 5}%` }} />
                    ))}
                  </div>
                  <div>
                    <div className="bc-live__state">
                      {mic.state === 'live' ? '말씀하세요 — 마을에 나가고 있습니다' : mic.state === 'connecting' ? '마이크 연결 중…' : '마이크 오류'}
                    </div>
                    <div className="dim" title={`Opus 16 kHz · mono · ${liveBitrateKbps} kbps · 40 ms`}>
                      막대가 움직이지 않으면 마이크 소리가 들어가지 않는 것입니다.
                    </div>
                  </div>
                </div>
              ) : (
                <ol className="bc-howto">
                  <li>아래 <strong>「마이크 방송 시작」</strong>을 누릅니다.</li>
                  <li>마이크를 써도 되는지 물으면 <strong>「허용」</strong>을 누릅니다.</li>
                  <li>말씀이 끝나면 맨 위 빨간 띠의 <strong>「방송 끄기」</strong>를 누릅니다.</li>
                </ol>
              )}
              <details className="bc-more">
                <summary>자세히 설정</summary>
                <label className="check">
                  <input type="checkbox" checked={recordFlash} onChange={(e) => setRecordFlash(e.target.checked)} disabled={liveId !== null} />
                  <span>단말에 녹음해 두기 (10분이 넘는 방송은 끄세요)</span>
                </label>
              </details>
            </section>
          )}

          {/* ③ 무엇을 */}
          {method && method !== 'mic' && (
            <section className={stepClass(3, 'bc-step--what')}>
              <StepTitle n={3} done={isDone(3)}>{method === 'tts' ? '무엇이라고 방송할까요?' : '어떤 소리를 틀까요?'}</StepTitle>
              {method === 'tts' ? (
                <>
                  <div className="bc-phrases">
                    {PHRASES.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className="bc-chip"
                        title={p}
                        onClick={() => setText((t) => (t.trim() ? `${t.trimEnd()} ${p}` : p))}
                      >
                        + {p}
                      </button>
                    ))}
                  </div>
                  <div className="bc-textwrap">
                    <textarea
                      className="bc-text"
                      aria-label="방송할 글"
                      rows={5}
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      placeholder="예) 주민 여러분께 알려드립니다. 오늘 오후 두 시에 마을회관에서 반상회가 있겠습니다."
                    />
                    <span className={`bc-textwrap__count ${text.length > MAX_TEXT ? 'count--none' : 'dim'}`}>
                      {text.length} / {MAX_TEXT}자
                    </span>
                  </div>
                  <div className="bc-preview">
                    <button type="button" className="bc-btn bc-btn--ghost" onClick={() => void preview()} disabled={busy || !text.trim() || text.length > MAX_TEXT}>
                      ▶ 먼저 들어보기
                    </button>
                    {made && made.key === ttsKey && (
                      <audio ref={previewRef} src={api.files.audioUrl(made.file.id)} controls />
                    )}
                    <label className="bc-voice">
                      목소리
                      <select value={voice} onChange={(e) => setVoice(e.target.value)} disabled={voicesForLanguage.length === 0}>
                        {voicesForLanguage.map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </>
              ) : files.length === 0 ? (
                <p className="bc-empty">
                  저장된 소리가 없습니다. ① 에서 「글로 써서 방송」을 쓰시거나, <Link to="/files">방송 자료</Link>에서 올려 주세요.
                </p>
              ) : (
                <>
                  {files.length > 6 && (
                    <input
                      type="search"
                      className="bc-search"
                      placeholder="이름으로 찾기"
                      value={fileQuery}
                      onChange={(e) => setFileQuery(e.target.value)}
                      aria-label="저장된 소리 찾기"
                    />
                  )}
                  <ul className="bc-files">
                    {filteredFiles.map((f) => (
                      <li key={f.id} className={fileId === f.id ? 'is-on' : undefined}>
                        <label>
                          <input type="radio" name="bc-file" checked={fileId === f.id} onChange={() => setFileId(f.id)} />
                          <span className="bc-files__name">{f.filename.replace(/\.mp3$/i, '')}</span>
                          <span className="dim">{f.duration_sec ? `${Math.round(f.duration_sec)}초` : ''}</span>
                        </label>
                        <button
                          type="button"
                          className="bc-chip"
                          onClick={() => {
                            setListenId(listenId === f.id ? null : f.id);
                            queueMicrotask(() => void listenRef.current?.play().catch(() => undefined));
                          }}
                        >
                          {listenId === f.id ? '■ 그만 듣기' : '▶ 듣기'}
                        </button>
                      </li>
                    ))}
                    {filteredFiles.length === 0 && <li className="dim">찾는 이름이 없습니다.</li>}
                  </ul>
                  {listenId !== null && <audio ref={listenRef} src={api.files.audioUrl(listenId)} controls className="bc-listen" onEnded={() => setListenId(null)} />}
                </>
              )}
            </section>
          )}

          {/* ④ 언제 */}
          {method && method !== 'mic' && (
            <section className={stepClass(4, 'bc-step--when')}>
              <StepTitle n={4} done={isDone(4)}>언제 방송할까요?</StepTitle>
              <div className="bc-when">
                <button type="button" className={`bc-choice${when === 'now' ? ' is-on' : ''}`} aria-pressed={when === 'now'} onClick={() => setWhen('now')}>
                  지금 바로
                </button>
                {(['once', 'daily', 'weekly'] as ReserveKind[]).map((k) => {
                  const on = when === 'reserve' && kind === k;
                  return (
                    <button
                      key={k}
                      type="button"
                      className={`bc-choice bc-choice--reserve${on ? ' is-on' : ''}`}
                      aria-pressed={on}
                      onClick={() => {
                        setWhen('reserve');
                        setKind(k);
                      }}
                    >
                      {k === 'once' ? '날짜 정해 한 번' : k === 'daily' ? '매일' : '매주'}
                      <small>예약</small>
                    </button>
                  );
                })}
              </div>
              {when === 'reserve' && (
                <div className="bc-reserve">
                  {kind === 'once' && (
                    <div className="bc-row">
                      <span className="bc-row__label">날짜</span>
                      <div className="bc-quickdates">
                        {[0, 1, 2].map((d) => (
                          <button key={d} type="button" className={`bc-chip${onceDate === kstToday(d) ? ' is-on' : ''}`} onClick={() => setOnceDate(kstToday(d))}>
                            {d === 0 ? '오늘' : d === 1 ? '내일' : '모레'}
                          </button>
                        ))}
                        <input type="date" className="date-big" aria-label="날짜 직접 고르기" min={kstToday()} value={onceDate} onChange={(e) => setOnceDate(e.target.value)} />
                      </div>
                    </div>
                  )}
                  {kind === 'weekly' && (
                    <div className="bc-row">
                      <span className="bc-row__label">요일</span>
                      <div className="bc-days">
                        {WEEKDAY_LABELS.map((label, i) => (
                          <button
                            key={label}
                            type="button"
                            className={`bc-chip${weekdays.includes(i) ? ' is-on' : ''}`}
                            aria-pressed={weekdays.includes(i)}
                            onClick={() => setWeekdays((w) => (w.includes(i) ? w.filter((x) => x !== i) : [...w, i].sort()))}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="bc-row">
                    <span className="bc-row__label">시각</span>
                    <div className="bc-time">
                      <select aria-label="시" value={hour24} onChange={(e) => setHour24(Number(e.target.value))}>
                        {hourOptions.map((h) => (
                          <option key={h} value={h}>
                            {h < 12 ? '오전' : '오후'} {h % 12 === 0 ? 12 : h % 12}시
                          </option>
                        ))}
                      </select>
                      <select aria-label="분" value={minute} onChange={(e) => setMinute(Number(e.target.value))}>
                        {minuteOptions.map((m) => (
                          <option key={m} value={m}>
                            {String(m).padStart(2, '0')}분
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>
              )}
              <details className="bc-more">
                <summary>자세히 설정</summary>
                {method === 'tts' && catalog && Object.keys(catalog.languages).length > 1 && (
                  <label className="bc-voice">
                    읽는 언어
                    <select value={language} onChange={(e) => setLanguage(e.target.value)}>
                      {Object.entries(catalog.languages).map(([code, label]) => (
                        <option key={code} value={code}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="check">
                  <input type="checkbox" checked={storeFlash} onChange={(e) => setStoreFlash(e.target.checked)} />
                  <span>단말에 저장해 두기 (반복 재생용 · 예약 반복 방송은 저절로 켜집니다)</span>
                </label>
              </details>
            </section>
          )}

          {/* 맨 아래 — 한 문장 요약 + 큰 버튼 하나 */}
          <div className={`bc-go${guide === 'go' ? ' is-guide' : ''}`}>
            <p className="bc-go__sentence">
              {missing ? (
                <span className="bc-go__missing">{missing}</span>
              ) : method === 'mic' ? (
                <>
                  <strong>{targetText || '모든 마을'}</strong>(켜진 단말 {onlineTargets}대)에 <strong>마이크로 말하는 내용</strong>을 지금 바로 방송합니다.
                </>
              ) : (
                <>
                  <strong>{targetText || '모든 마을'}</strong>(켜진 단말 {onlineTargets}대)에{' '}
                  <strong>{method === 'tts' ? '적은 글' : `「${selectedFile?.filename.replace(/\.mp3$/i, '')}」`}</strong>
                  {method === 'tts' ? '을' : '을(를)'}{' '}
                  <strong>{whenText}</strong> 방송합니다.
                </>
              )}
            </p>
            <button
              type="button"
              className={`bc-btn bc-btn--go${effectiveWhen === 'reserve' ? ' bc-btn--reserve' : ''}`}
              onClick={() => void go()}
              disabled={busy || missing !== null || (method === 'mic' && liveId !== null)}
            >
              {busy ? busyText || '잠시만요…' : method === 'mic' && liveId !== null ? '방송 중 — 위에서 끄세요' : goLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
