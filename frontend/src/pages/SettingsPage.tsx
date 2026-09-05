/**
 * 설정 (super_admin).
 *
 * 두 종류가 한 화면에 있다:
 *   · 단말 공통 설정 — 저장하면 config_version 이 올라가고 서버가 MQTT CONFIG(retain)를
 *     재발행한다. 단말은 config_version 이 바뀔 때만 값을 다시 적용하고 STATUS 로 echo 한다
 *     (단말 관리 화면의 CFG 열에서 반영 여부를 볼 수 있다).
 *   · 서버 설정 — 방송 중지 후 단말 응답을 기다리는 시간. 단말로 나가지 않으므로
 *     이 값만 바꾸면 config_version 이 올라가지 않는다.
 */

import { useEffect, useState, type FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import type { SystemConfig } from '../api/types';

/**
 * 설정은 두 묶음이다(문제점 리스트 7번). 섞어 두면 "저장하면 전 단말에 발행된다"는
 * 안내가 응답 시간 항목에도 붙어 보여서 헷갈린다.
 *
 *   device — 단말 공통 CONFIG. 통신 사양 §3.5 의 clamp 범위. 저장하면 config_version
 *            이 올라가고 MQTT CONFIG 가 재발행된다.
 *   timing — 방송 응답 시간. 서버가 "시작됐다·끝났다"를 언제 확정할지 정한다.
 *            단말에 CONFIG 로 나가지 않아 config_version 이 바뀌지 않는다.
 *            (라이브 준비 제한만 LIVE_START 명령 필드로 실려 나간다.)
 *
 * 문구는 시작을 먼저, 종료를 뒤에 쓴다(문제점 8·9번). 항목 순서는 파일 → 라이브
 * 준비 → 라이브 종료(문제점 12번 — 방송 흐름 순서).
 */
const GROUPS = [
  {
    key: 'device',
    title: '단말 공통 CONFIG',
    hint: '저장하면 config_version 이 올라가고 전 단말에 MQTT CONFIG 가 다시 발행됩니다.',
  },
  {
    key: 'timing',
    title: '방송 응답 시간',
    hint: '서버가 방송의 시작·종료를 확정하기까지 단말 응답을 기다리는 시간입니다. 단말 CONFIG 로 나가지 않습니다. 단말이 모두 응답하면 그 자리에서 끝나므로, 넉넉히 잡아도 정상 동작에서는 비용이 없습니다.',
  },
] as const;

const FIELDS = [
  {
    group: 'device',
    key: 'status_interval_sec',
    label: 'STATUS 주기',
    hint: '단말이 상태를 올려보내는 간격입니다.',
    min: 10,
    max: 3600,
    unit: '초',
  },
  {
    group: 'device',
    key: 'live_stats_interval_sec',
    label: 'LIVE_STATS 주기',
    hint: '방송 중 품질 지표를 보내는 간격입니다.',
    min: 1,
    max: 60,
    unit: '초',
  },
  {
    group: 'device',
    key: 'event_qos',
    label: '이벤트 QoS',
    hint: '주기 STATUS · LIVE_STATS 에만 적용됩니다.',
    min: 0,
    max: 1,
    unit: '',
  },
  {
    group: 'timing',
    key: 'file_wait_sec',
    label: '파일방송 응답 대기',
    hint: '방송 시작 후 단말이 파일을 다 받고 무결성 검증을 마쳤다고(FILE_RESULT — 이때 재생이 시작됩니다) 응답하기까지 기다리는 시간이자, 방송 중지 후 종료 응답을 기다리는 시간입니다. 저장은 방송 중에 단말이 알아서 하므로 이 시간과 무관하며, 파일 크기가 커져도 응답은 늦어지지 않습니다. 716KB 실측 3.6초.',
    min: 10,
    max: 60,
    unit: '초',
  },
  {
    group: 'timing',
    key: 'live_ready_timeout_sec',
    label: '라이브 준비 제한',
    hint: '방송 시작 후 단말이 준비(LIVE_READY)를 마쳐야 하는 시간입니다. LIVE_START 로 단말에 전달되고, 단말은 이 값 + 5초까지 기다리므로 화면의 「준비 지연」 알림도 이 값 + 5초에 뜹니다.',
    min: 1,
    max: 60,
    unit: '초',
  },
  {
    group: 'timing',
    key: 'live_stop_wait_sec',
    label: '라이브 종료 대기',
    hint: '방송 중지 후 단말의 종료 응답(LIVE_RESULT)을 기다리는 시간입니다. 다 오면 즉시 끝내고 그때 스트림을 닫습니다. 실측 1.5초.',
    min: 10,
    max: 30,
    unit: '초',
  },
] as const;

type FieldKey = (typeof FIELDS)[number]['key'];

export function SettingsPage() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [form, setForm] = useState<Record<FieldKey, number>>({
    status_interval_sec: 30,
    live_stats_interval_sec: 10,
    event_qos: 0,
    live_ready_timeout_sec: 30,
    live_stop_wait_sec: 10,
    file_wait_sec: 30,
  });
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const c = await api.config.get();
        setConfig(c);
        setForm({
          status_interval_sec: c.status_interval_sec,
          live_stats_interval_sec: c.live_stats_interval_sec,
          event_qos: c.event_qos,
          live_ready_timeout_sec: c.live_ready_timeout_sec,
          live_stop_wait_sec: c.live_stop_wait_sec,
          file_wait_sec: c.file_wait_sec,
        });
      } catch (err) {
        setMessage({
          tone: 'error',
          text: err instanceof ApiError ? err.message : '설정을 불러오지 못했습니다.',
        });
      }
    })();
  }, []);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const updated = await api.config.update(form);
      // 단말 CONFIG 가 바뀌었을 때만 버전이 오르고 재발행된다. 응답 시간만 바꿨으면
      // 서버 안에서만 바뀐 것이라 "재발행" 이라고 말하면 거짓이다.
      const republished = config !== null && updated.config_version !== config.config_version;
      setConfig(updated);
      setMessage({
        tone: 'ok',
        text: republished
          ? `저장했습니다. config_version ${updated.config_version} 으로 전 단말에 재발행되었습니다.`
          : '저장했습니다. 방송 응답 시간만 바뀌어 단말 재발행은 없습니다.',
      });
    } catch (err) {
      setMessage({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '저장에 실패했습니다.',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <h1>설정</h1>
        <p>
          단말 공통 CONFIG 와 방송 응답 시간.
          {config && ` 현재 config_version ${config.config_version}`}
        </p>
      </div>

      {message && (
        <div
          className={`alert${message.tone === 'ok' ? ' alert--ok' : ''}`}
          style={{ marginBottom: 16 }}
          role="status"
        >
          {message.text}
        </div>
      )}

      <div className="settings-grid">
        <form className="settings-card" onSubmit={onSubmit}>
          {GROUPS.map((g) => (
            <section key={g.key} className="settings-group">
              <h2 className="settings-group__title">{g.title}</h2>
              <p className="settings-group__hint">{g.hint}</p>
              {FIELDS.filter((f) => f.group === g.key).map((f) => (
                <div className="settings-row" key={f.key}>
                  <div className="settings-row__text">
                    <div className="settings-row__label">{f.label}</div>
                    <div className="settings-row__hint">{f.hint}</div>
                  </div>
                  <input
                    id={f.key}
                    type="number"
                    aria-label={f.label}
                    min={f.min}
                    max={f.max}
                    value={form[f.key]}
                    onChange={(e) => setForm({ ...form, [f.key]: Number(e.target.value) })}
                    required
                  />
                  <span className="settings-row__range">
                    {f.min}~{f.max}
                    {f.unit}
                  </span>
                </div>
              ))}
            </section>
          ))}

          <button type="submit" className="btn btn--primary btn--block" disabled={busy || !config}>
            {busy ? '저장 중…' : '저장'}
          </button>
        </form>

        <aside className="settings-aside">
          <h2>저장하면 어떻게 되나요</h2>
          <p>
            <strong>단말 공통 CONFIG</strong>를 바꾸면 config_version 이 올라가고 서버가 MQTT
            CONFIG(retain)를 다시 발행합니다. 단말은 버전이 바뀔 때만 값을 적용하고, 적용 직후
            STATUS 로 echo 합니다. 반영 여부는 단말 관리 화면의 CFG 열에서 확인하세요.
          </p>
          <p>
            <strong>방송 응답 시간</strong>은 서버 안에서만 쓰이는 값이라 단말에 발행되지
            않습니다. 라이브 준비 제한만 방송을 시작할 때 LIVE_START 명령에 실려 나갑니다.
          </p>
          {config && (
            <div className="settings-aside__meta">
              마지막 저장 {new Date(config.updated_at).toLocaleString('ko-KR')} · v
              {config.config_version}
            </div>
          )}
        </aside>
      </div>
    </>
  );
}
