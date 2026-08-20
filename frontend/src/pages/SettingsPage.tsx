/**
 * 단말 공통 설정 (super_admin).
 *
 * 저장하면 config_version 이 올라가고 서버가 MQTT CONFIG(retain)를 재발행한다.
 * 단말은 config_version 이 바뀔 때만 값을 다시 적용하고, 적용 직후 STATUS 로 echo 한다 —
 * 단말 관리 화면의 CFG 열에서 반영 여부를 확인할 수 있다.
 */

import { useEffect, useState, type FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import type { SystemConfig } from '../api/types';

/** 통신 사양 §3.5 의 clamp 범위. 백엔드도 같은 값으로 막는다. */
const FIELDS = [
  {
    key: 'status_interval_sec',
    label: 'STATUS 주기',
    hint: '단말이 상태를 올려보내는 간격입니다.',
    min: 10,
    max: 3600,
    unit: '초',
  },
  {
    key: 'live_stats_interval_sec',
    label: 'LIVE_STATS 주기',
    hint: '방송 중 품질 지표를 보내는 간격입니다.',
    min: 1,
    max: 60,
    unit: '초',
  },
  {
    key: 'event_qos',
    label: '이벤트 QoS',
    hint: '주기 STATUS · LIVE_STATS 에만 적용됩니다.',
    min: 0,
    max: 1,
    unit: '',
  },
] as const;

type FieldKey = (typeof FIELDS)[number]['key'];

export function SettingsPage() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [form, setForm] = useState<Record<FieldKey, number>>({
    status_interval_sec: 30,
    live_stats_interval_sec: 10,
    event_qos: 0,
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
      setConfig(updated);
      setMessage({
        tone: 'ok',
        text: `저장했습니다. config_version ${updated.config_version} 으로 재발행되었습니다.`,
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
          전 단말에 공통으로 적용되는 값입니다.
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
          {FIELDS.map((f) => (
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

          <button type="submit" className="btn btn--primary btn--block" disabled={busy || !config}>
            {busy ? '저장 중…' : '저장하고 단말에 발행'}
          </button>
        </form>

        <aside className="settings-aside">
          <h2>저장하면 어떻게 되나요</h2>
          <p>
            config_version 이 올라가고 서버가 MQTT CONFIG(retain)를 다시 발행합니다. 단말은 버전이
            바뀔 때만 값을 적용하고, 적용 직후 STATUS 로 echo 합니다. 반영 여부는 단말 관리 화면의
            CFG 열에서 확인하세요.
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
