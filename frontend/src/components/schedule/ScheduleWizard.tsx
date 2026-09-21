/**
 * 스케줄 추가·수정 위자드 — 스케줄 설계 2026-09-09, 목업 1·2·3.
 *
 * 한 화면에 네 질문이 세로로 있고, 3번(언제)을 고르면 그 아래 상세가 펼쳐진다.
 * 다 채우면 확인 쪽으로 넘어가 문장으로 보여준다:
 *   「앞으로 매주 월·수 오전 9:00마다 안내.mp3을 계곡마을에 방송합니다」
 *
 * 저장하는 것은 규칙 하나다. 개별 날짜는 서버가 계산한다(rules.py).
 */

import { useEffect, useMemo, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import { ko } from 'date-fns/locale';
import 'react-day-picker/style.css';

import { ApiError, api } from '../../api/client';
import type {
  AudioFile,
  Device,
  Organization,
  Repeat,
  Schedule,
  ScheduleInput,
  Village,
} from '../../api/types';
import { buildForest, subtreeStats, type OrgNode } from '../../lib/orgtree';
import { REPEAT_LABEL, WEEKDAY_LABELS, formatTime, kstToday, repeatLabel } from '../../lib/schedule';
import { Modal } from '../Modal';
import { TtsModal } from '../TtsModal';

interface Props {
  initial?: Schedule;
  onClose: () => void;
  onSaved: () => void;
}

/** 오른쪽 상세 패널의 제목. 목업 2 의 「매년|매주|매월|매일 몇일에 방송할까요?」. */
const DETAIL_TITLE: Record<Repeat, string> = {
  once: '며칠에 방송할까요?',
  daily: '매일 몇 시에 방송할까요?',
  weekly: '매주 무슨 요일에 방송할까요?',
  monthly: '매월 며칠에 방송할까요?',
  yearly: '매년 몇 월 며칠에 방송할까요?',
};

/** 1번 질문의 선택 — 기관(관할 전체) 또는 마을 하나. */
type TargetPick = { kind: 'organization'; id: number } | { kind: 'village'; id: number } | null;

// ── 1. 어디에 (기관 → 마을 트리) ─────────────────────────────────────────
function TargetTree({
  orgs,
  villages,
  value,
  onChange,
}: {
  orgs: Organization[];
  villages: Village[];
  value: TargetPick;
  onChange: (v: TargetPick) => void;
}) {
  // 깊이 제한 없는 트리(설계 v2). 지역 관리 화면과 같은 순서로 세운다.
  const forest = buildForest(orgs, villages);

  const isOn = (kind: 'organization' | 'village', id: number) =>
    value !== null && value.kind === kind && value.id === id;

  const villageRow = (v: Village) => (
    <li key={`v${v.id}`}>
      <label className={isOn('village', v.id) ? 'is-on' : undefined}>
        <input
          type="radio"
          name="sched-target"
          checked={isOn('village', v.id)}
          onChange={() => onChange({ kind: 'village', id: v.id })}
        />
        <span className="tree__name">{v.name}</span>
        <span className="tree__kind">단말 {v.device_count}대</span>
      </label>
    </li>
  );

  const orgBlock = (node: OrgNode): React.ReactNode => {
    const o = node.org;
    return (
      <li key={`o${o.id}`}>
        <label className={isOn('organization', o.id) ? 'is-on' : undefined}>
          <input
            type="radio"
            name="sched-target"
            checked={isOn('organization', o.id)}
            onChange={() => onChange({ kind: 'organization', id: o.id })}
          />
          <span className="tree__name strong">{o.name}</span>
          <span className="tree__kind">관할 전체</span>
        </label>
        {(node.children.length > 0 || node.villages.length > 0) && (
          <ul>
            {node.children.map(orgBlock)}
            {node.villages.map(villageRow)}
          </ul>
        )}
      </li>
    );
  };

  if (orgs.length === 0) {
    // 이장 — 기관이 없고 담당 마을만 있다.
    return <ul className="tree">{villages.map(villageRow)}</ul>;
  }

  return (
    <ul className="tree">
      {forest.roots.map(orgBlock)}
      {forest.orphans.length > 0 && (
        <li>
          <div className="tree__group">기관 없음</div>
          <ul>{forest.orphans.map(villageRow)}</ul>
        </li>
      )}
    </ul>
  );
}

// ── 2. 단말 ───────────────────────────────────────────────────────────────
function DevicePicker({
  devices,
  all,
  macs,
  onChange,
  disabled,
}: {
  devices: Device[];
  all: boolean;
  macs: string[];
  onChange: (all: boolean, macs: string[]) => void;
  disabled: boolean;
}) {
  if (disabled) {
    return <p className="hint">관할 전체를 골랐습니다. 소속 마을의 모든 단말에 나갑니다.</p>;
  }
  if (devices.length === 0) {
    return <p className="hint">마을을 먼저 고르세요.</p>;
  }
  return (
    <>
      <div className="choice-row" style={{ marginBottom: 10 }}>
        <button
          type="button"
          className={`choice${all ? ' is-on' : ''}`}
          onClick={() => onChange(true, [])}
        >
          모든 단말 ({devices.length}대)
        </button>
        <button
          type="button"
          className={`choice${!all ? ' is-on' : ''}`}
          onClick={() => onChange(false, macs)}
        >
          골라서
        </button>
      </div>
      {!all && (
        <div className="checks">
          {devices.map((d) => (
            <label key={d.mac} className="check">
              <input
                type="checkbox"
                checked={macs.includes(d.mac)}
                onChange={(e) =>
                  onChange(
                    false,
                    e.target.checked ? [...macs, d.mac] : macs.filter((m) => m !== d.mac),
                  )
                }
              />
              <span>
                {d.label ?? d.mac}
                {!d.online && <span className="dim"> · 오프라인</span>}
              </span>
            </label>
          ))}
        </div>
      )}
    </>
  );
}

// ── 3. 언제 ───────────────────────────────────────────────────────────────
function RepeatDetail({
  repeat,
  weekdays,
  monthDays,
  yearDates,
  onceDate,
  onWeekdays,
  onMonthDays,
  onYearDates,
  onOnceDate,
}: {
  repeat: Repeat;
  weekdays: number[];
  monthDays: number[];
  yearDates: Date[];
  onceDate: string;
  onWeekdays: (v: number[]) => void;
  onMonthDays: (v: number[]) => void;
  onYearDates: (v: Date[]) => void;
  onOnceDate: (v: string) => void;
}) {
  const toggle = (list: number[], v: number) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v].sort((a, b) => a - b);

  if (repeat === 'once') {
    // 정해진 날짜에 한 번(2026-09-21). 지난 날짜는 고를 수 없다 — 서버도 막는다.
    return (
      <input
        type="date"
        className="date-big"
        aria-label="방송할 날짜"
        min={kstToday()}
        value={onceDate}
        onChange={(e) => onOnceDate(e.target.value)}
      />
    );
  }

  if (repeat === 'daily') return <p className="hint">매일 같은 시각에 나갑니다.</p>;
  if (repeat === 'weekly') {
    return (
      <div className="daygrid">
        {WEEKDAY_LABELS.map((label, i) => (
          <button
            key={i}
            type="button"
            className={`choice${weekdays.includes(i) ? ' is-on' : ''}`}
            onClick={() => onWeekdays(toggle(weekdays, i))}
          >
            {label}
          </button>
        ))}
      </div>
    );
  }
  if (repeat === 'monthly') {
    return (
      <>
        <div className="daygrid">
          {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => (
            <button
              key={d}
              type="button"
              className={`choice${monthDays.includes(d) ? ' is-on' : ''}`}
              onClick={() => onMonthDays(toggle(monthDays, d))}
            >
              {d}
            </button>
          ))}
        </div>
        {monthDays.some((d) => d >= 29) && (
          <p className="hint">29·30·31일은 그 날이 없는 달에는 건너뜁니다.</p>
        )}
      </>
    );
  }
  return (
    <>
      <DayPicker
        mode="multiple"
        locale={ko}
        selected={yearDates}
        onSelect={(d) => onYearDates(d ?? [])}
        captionLayout="dropdown"
        startMonth={new Date(new Date().getFullYear(), 0)}
        endMonth={new Date(new Date().getFullYear() + 1, 11)}
      />
      <p className="hint">
        달력에서 고른 <strong>월·일</strong>만 씁니다. 해는 상관없습니다. 2월 29일은 윤년에만 나갑니다.
      </p>
    </>
  );
}

function TimePick({
  hour24,
  minute,
  onChange,
}: {
  hour24: number;
  minute: number;
  onChange: (hour24: number, minute: number) => void;
}) {
  const pm = hour24 >= 12;
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  const set = (nextPm: boolean, nextHour12: number, nextMinute: number) =>
    onChange((nextHour12 % 12) + (nextPm ? 12 : 0), nextMinute);
  return (
    <div className="timepick">
      <select value={pm ? 'pm' : 'am'} onChange={(e) => set(e.target.value === 'pm', hour12, minute)}>
        <option value="am">오전</option>
        <option value="pm">오후</option>
      </select>
      <select value={hour12} onChange={(e) => set(pm, Number(e.target.value), minute)}>
        {Array.from({ length: 12 }, (_, i) => i + 1).map((h) => (
          <option key={h} value={h}>
            {h}시
          </option>
        ))}
      </select>
      <select value={minute} onChange={(e) => set(pm, hour12, Number(e.target.value))}>
        {Array.from({ length: 12 }, (_, i) => i * 5).map((m) => (
          <option key={m} value={m}>
            {String(m).padStart(2, '0')}분
          </option>
        ))}
      </select>
    </div>
  );
}

// ── 4. 무엇을 ─────────────────────────────────────────────────────────────
function FilePicker({
  files,
  fileId,
  onChange,
  onReload,
}: {
  files: AudioFile[];
  fileId: number | '';
  onChange: (id: number) => void;
  onReload: () => Promise<AudioFile[]>;
}) {
  const [ttsOpen, setTtsOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const upload = async (picked: FileList | null) => {
    const f = picked?.[0];
    if (!f) return;
    setUploading(true);
    setMsg(null);
    try {
      let saved: AudioFile;
      try {
        saved = await api.files.upload(f);
      } catch (err) {
        if (!(err instanceof ApiError) || err.code !== 'AUDIO_NEEDS_TRANSCODE') throw err;
        if (!window.confirm(`"${f.name}"\n\n${err.message}`)) return;
        saved = await api.files.upload(f, true);
      }
      await onReload();
      onChange(saved.id);
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : '업로드에 실패했습니다.');
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <select value={fileId} onChange={(e) => onChange(Number(e.target.value))} style={{ width: '100%' }}>
        <option value="">파일함에서 고르세요</option>
        {files.map((f) => (
          <option key={f.id} value={f.id}>
            {f.filename}
            {f.duration_sec != null ? ` · ${Math.round(Number(f.duration_sec))}초` : ''}
          </option>
        ))}
      </select>
      <div className="choice-row" style={{ marginTop: 10 }}>
        <label className="btn btn--sm" style={{ cursor: 'pointer' }}>
          {uploading ? '올리는 중…' : 'MP3 올리기'}
          <input
            type="file"
            accept=".mp3,audio/mpeg"
            hidden
            disabled={uploading}
            onChange={(e) => {
              void upload(e.target.files);
              e.target.value = '';
            }}
          />
        </label>
        <button type="button" className="btn btn--sm" onClick={() => setTtsOpen(true)}>
          TTS 로 만들기
        </button>
      </div>
      {msg && <p className="hint hint--warn">{msg}</p>}
      {ttsOpen && (
        <TtsModal
          onClose={() => setTtsOpen(false)}
          onCreated={(made) => {
            void onReload().then(() => onChange(made.id));
          }}
        />
      )}
    </>
  );
}

// ── 위자드 본체 ───────────────────────────────────────────────────────────
export function ScheduleWizard({ initial, onClose, onSaved }: Props) {
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [files, setFiles] = useState<AudioFile[]>([]);

  const [target, setTarget] = useState<TargetPick>(() => {
    if (!initial) return null;
    if (initial.target_scope === 'organization') return { kind: 'organization', id: Number(initial.target_ids[0]) };
    if (initial.target_scope === 'village') return { kind: 'village', id: Number(initial.target_ids[0]) };
    return null; // device — 아래 effect 가 단말의 마을로 채운다
  });
  const [allDevices, setAllDevices] = useState(initial?.target_scope !== 'device');
  const [macs, setMacs] = useState<string[]>(initial?.target_scope === 'device' ? initial.target_ids : []);

  const [repeat, setRepeat] = useState<Repeat | null>(initial?.repeat ?? null);
  const [weekdays, setWeekdays] = useState<number[]>(initial?.weekdays ?? []);
  const [monthDays, setMonthDays] = useState<number[]>(initial?.month_days ?? []);
  const [onceDate, setOnceDate] = useState<string>(initial?.once_date ?? kstToday(1));
  const [yearDates, setYearDates] = useState<Date[]>(
    (initial?.year_dates ?? []).map((d) => new Date(new Date().getFullYear(), d.month - 1, d.day)),
  );
  const [hour24, setHour24] = useState(initial ? Number(initial.fire_time.slice(0, 2)) : 9);
  const [minute, setMinute] = useState(initial ? Number(initial.fire_time.slice(3, 5)) : 0);
  const [fileId, setFileId] = useState<number | ''>(initial?.file_id ?? '');
  const [storeFlash, setStoreFlash] = useState(initial?.store_flash ?? false);

  const [step, setStep] = useState<'edit' | 'confirm'>('edit');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reloadFiles = async () => {
    const list = await api.files.list();
    setFiles(list);
    return list;
  };

  useEffect(() => {
    void (async () => {
      try {
        const [o, v] = await Promise.all([api.organizations.list(), api.villages.list()]);
        setOrgs(o);
        setVillages(v);
        await reloadFiles();
        // 단말 대상으로 저장돼 있던 스케줄을 수정할 때 — 그 단말들의 마을을 찾아 트리를 채운다.
        if (initial?.target_scope === 'device' && initial.target_ids.length > 0) {
          const first = await api.devices.get(initial.target_ids[0]);
          if (first.village_id !== null) setTarget({ kind: 'village', id: first.village_id });
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '목록을 불러오지 못했습니다.');
      }
    })();
  }, [initial]);

  // 마을을 고르면 그 마을 단말을 읽는다. 기관을 고르면 단말 선택은 의미가 없다.
  useEffect(() => {
    if (target?.kind !== 'village') {
      setDevices([]);
      return;
    }
    let alive = true;
    void api.devices
      .list({ village_id: target.id })
      .then((d) => alive && setDevices(d))
      .catch(() => alive && setDevices([]));
    return () => {
      alive = false;
    };
  }, [target]);

  const rule = useMemo(
    () =>
      repeat === null
        ? null
        : {
            repeat,
            weekdays: repeat === 'weekly' ? weekdays : null,
            month_days: repeat === 'monthly' ? monthDays : null,
            year_dates:
              repeat === 'yearly'
                ? yearDates.map((d) => ({ month: d.getMonth() + 1, day: d.getDate() }))
                : null,
            once_date: repeat === 'once' ? onceDate : null,
            fire_time: `${String(hour24).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00`,
          },
    [repeat, weekdays, monthDays, yearDates, onceDate, hour24, minute],
  );

  const repeatComplete =
    rule !== null &&
    (rule.repeat === 'daily' ||
      (rule.repeat === 'once' && onceDate !== '') ||
      (rule.repeat === 'weekly' && weekdays.length > 0) ||
      (rule.repeat === 'monthly' && monthDays.length > 0) ||
      (rule.repeat === 'yearly' && yearDates.length > 0));
  const targetComplete =
    target !== null && (target.kind === 'organization' || allDevices || macs.length > 0);
  const canConfirm = targetComplete && repeatComplete && fileId !== '';

  // 관할(아래 전부)에 마을이 하나도 없으면 저장은 되지만 방송이 영영 안 나간다. 미리 알린다.
  const emptyOrg = (() => {
    if (target?.kind !== 'organization') return false;
    const node = buildForest(orgs, villages).byId.get(target.id);
    return node !== undefined && subtreeStats(node).villages === 0;
  })();

  const targetLabel = (() => {
    if (!target) return '';
    if (target.kind === 'organization')
      return `${orgs.find((o) => o.id === target.id)?.name ?? '기관'} 관할 전체`;
    const name = villages.find((v) => v.id === target.id)?.name ?? '마을';
    if (allDevices) return `${name} 모든 단말`;
    const labels = devices.filter((d) => macs.includes(d.mac)).map((d) => d.label ?? d.mac);
    return `${name} ${labels.length <= 3 ? labels.join(', ') : `${labels.slice(0, 2).join(', ')} 외 ${labels.length - 2}대`}`;
  })();
  const fileName = files.find((f) => f.id === fileId)?.filename ?? '';

  const save = async () => {
    if (!rule || !target || fileId === '') return;
    const body: ScheduleInput = {
      ...rule,
      file_id: fileId,
      target_scope: target.kind === 'organization' ? 'organization' : allDevices ? 'village' : 'device',
      target_ids:
        target.kind === 'organization' || allDevices ? [String(target.id)] : macs,
      store_flash: storeFlash,
      enabled: initial?.enabled ?? true,
    };
    setBusy(true);
    setError(null);
    try {
      if (initial) await api.schedules.update(initial.id, body);
      else await api.schedules.create(body);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '저장에 실패했습니다.');
      setStep('edit');
    } finally {
      setBusy(false);
    }
  };

  // ── 확인 쪽 (목업 3) ──
  if (step === 'confirm' && rule) {
    return (
      <Modal
        title={initial ? '스케줄 수정 확인' : '스케줄 확인'}
        size="mid"
        onClose={onClose}
        footer={
          <>
            <button type="button" className="btn" onClick={() => setStep('edit')} disabled={busy}>
              이전으로
            </button>
            <button type="button" className="btn btn--primary" onClick={() => void save()} disabled={busy}>
              {busy ? '저장 중…' : '스케줄 확정'}
            </button>
          </>
        }
      >
        {error && <div className="alert" style={{ marginBottom: 12 }}>{error}</div>}
        <p className="sentence">
          {rule.repeat === 'once' ? (
            <>
              <span className="chip">{repeatLabel(rule)}</span>{' '}
              <span className="chip">{formatTime(rule.fire_time)}</span>에 한 번{' '}
            </>
          ) : (
            <>
              앞으로 <span className="chip">{repeatLabel(rule)}</span>{' '}
              <span className="chip">{formatTime(rule.fire_time)}</span>마다{' '}
            </>
          )}
          <span className="chip">{fileName}</span>을(를){' '}
          <span className="chip">{targetLabel}</span>에 방송합니다.
        </p>
        <p className="hint">
          {storeFlash
            ? '단말 플래시에 저장해 두고 재생합니다.'
            : '매번 파일을 내려받아 재생합니다.'}{' '}
          방송 시각은 한국 시간입니다. 방송 중인 단말과 겹치면 그 회차는 건너뛰고 기록에 남깁니다.
        </p>
        <p className="hint">방송 스케줄은 스케줄 목록과 예정표에서 확인하실 수 있습니다.</p>
      </Modal>
    );
  }

  // ── 입력 쪽 (목업 1·2) ──
  return (
    <Modal
      title={initial ? '스케줄 수정' : '스케줄 추가하기'}
      size="wide"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            취소
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canConfirm}
            onClick={() => setStep('confirm')}
          >
            다음 — 확인
          </button>
        </>
      }
    >
      {error && <div className="alert" style={{ marginBottom: 12 }}>{error}</div>}
      <div className="wizard-two">
        {/* 왼쪽 — 목업대로 1·2·3·4 가 번호 순서로 내려간다. */}
        <div>
          <div className="wizard-q">
            <div className="wizard-q__title">1. 어디에 방송할까요?</div>
            <div className="wizard-q__body wizard-q__body--scroll">
              <TargetTree
                orgs={orgs}
                villages={villages}
                value={target}
                onChange={(t) => {
                  setTarget(t);
                  setAllDevices(true);
                  setMacs([]);
                }}
              />
            </div>
            {orgs.length > 0 && (
              <p className="hint" style={{ marginBottom: 0 }}>
                기관을 고르면 관할 전체입니다. 나중에 영입한 마을도 자동으로 들어갑니다.
              </p>
            )}
            {emptyOrg && (
              <p className="hint hint--warn" style={{ marginBottom: 0 }}>
                이 기관에 속한 마을이 없습니다. 지금 저장하면 방송이 나가지 않습니다. 마을
                관리에서 관리 기관을 먼저 지정하세요.
              </p>
            )}
          </div>

          <div className="wizard-q">
            <div className="wizard-q__title">2. 어느 단말에 방송할까요?</div>
            <div className="wizard-q__body wizard-q__body--scroll">
              <DevicePicker
                devices={devices}
                all={allDevices}
                macs={macs}
                onChange={(all, m) => {
                  setAllDevices(all);
                  setMacs(m);
                }}
                disabled={target?.kind === 'organization'}
              />
            </div>
          </div>

          <div className="wizard-q">
            <div className="wizard-q__title">3. 언제 방송할까요?</div>
            <div className="wizard-q__body">
              <div className="choice-row">
                {(['once', 'daily', 'weekly', 'monthly', 'yearly'] as Repeat[]).map((r) => (
                  <button
                    key={r}
                    type="button"
                    className={`choice${repeat === r ? ' is-on' : ''}`}
                    onClick={() => setRepeat(r)}
                  >
                    {REPEAT_LABEL[r]}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="wizard-q">
            <div className="wizard-q__title">4. 무엇을 방송할까요?</div>
            <div className="wizard-q__body">
              <FilePicker
                files={files}
                fileId={fileId}
                onChange={setFileId}
                onReload={reloadFiles}
              />
              <label className="check" style={{ marginTop: 10 }}>
                <input
                  type="checkbox"
                  checked={storeFlash}
                  onChange={(e) => setStoreFlash(e.target.checked)}
                />
                <span>단말 플래시에 저장</span>
              </label>
              <p className="hint" style={{ marginBottom: 0 }}>
                반복 방송이면 켜는 쪽이 유리합니다.
              </p>
            </div>
          </div>
        </div>

        {/* 오른쪽 — 3번을 고르면 그 상세와 시각이 여기 펼쳐진다(목업 2). */}
        <div>
          <div className="wizard-q">
            <div className="wizard-q__title">
              {repeat ? DETAIL_TITLE[repeat] : '언제 방송할까요?'}
            </div>
            <div className="wizard-q__body">
              {!repeat ? (
                <p className="hint" style={{ marginBottom: 0 }}>
                  왼쪽 3번에서 한 번·매일·매주·매월·매년 중 하나를 고르면 여기에 상세가 나옵니다.
                </p>
              ) : (
                <>
                  <RepeatDetail
                    repeat={repeat}
                    weekdays={weekdays}
                    monthDays={monthDays}
                    yearDates={yearDates}
                    onceDate={onceDate}
                    onWeekdays={setWeekdays}
                    onMonthDays={setMonthDays}
                    onYearDates={setYearDates}
                    onOnceDate={setOnceDate}
                  />
                  <div className="wizard-q__sub">몇 시에 방송할까요?</div>
                  <TimePick
                    hour24={hour24}
                    minute={minute}
                    onChange={(h, m) => {
                      setHour24(h);
                      setMinute(m);
                    }}
                  />
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
