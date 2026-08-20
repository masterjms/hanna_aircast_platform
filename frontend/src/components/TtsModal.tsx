/**
 * TTS 생성 모달.
 *
 * 문구를 합성해 파일함에 넣는다. 방송은 여기서 하지 않는다 — 만든 뒤
 * 들어보고, 방송 제어에서 골라 송출한다. 오타가 마을 스피커 300대로 바로
 * 나가는 일을 막기 위해서다.
 *
 * 합성하면 그 즉시 파일함에 저장된다(별도의 '저장' 단계가 없다).
 * 같은 (문구·언어·보이스)면 서버가 기존 합성본을 돌려주므로 같은 문구를
 * 여러 번 만들어도 요금은 한 번만 나가고 파일도 하나만 생긴다.
 * 문구를 바꿔가며 여러 번 만들면 그만큼 파일이 쌓이니 안 쓸 것은 지운다.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { AudioFile, VoiceCatalog } from '../api/types';
import { Modal } from './Modal';

const MAX_TEXT = 1000;

interface TtsModalProps {
  onClose: () => void;
  /** 생성(또는 캐시 적중)된 파일. 파일함이 목록을 새로고침한다. */
  onCreated: (file: AudioFile) => void;
}

export function TtsModal({ onClose, onCreated }: TtsModalProps) {
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null);
  const [text, setText] = useState('');
  const [language, setLanguage] = useState('ko-KR');
  const [voice, setVoice] = useState('');
  const [filename, setFilename] = useState('');

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 방금 만든 파일. 이미 파일함에 저장된 상태다. */
  const [made, setMade] = useState<AudioFile | null>(null);
  const [cached, setCached] = useState(false);

  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    void api.files
      .voices()
      .then(setCatalog)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : '보이스 목록을 불러오지 못했습니다.'),
      );
  }, []);

  const voicesForLanguage = useMemo(
    () => catalog?.voices.filter((v) => v.language === language) ?? [],
    [catalog, language],
  );

  // 언어를 바꾸면 그 언어의 첫 보이스로 맞춘다 — 남의 언어 보이스가 남으면 서버가 거절한다.
  useEffect(() => {
    setVoice(voicesForLanguage[0]?.id ?? '');
    setMade(null);
  }, [voicesForLanguage]);

  const synthesize = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.files.tts({
        text: text.trim(),
        language,
        voice: voice || null,
        filename: filename.trim() || null,
      });
      setMade(res.file);
      setCached(res.cached);
      // 결과가 나오면 바로 들려준다. 확인이 목적인 화면이라 한 번 더 누르게 하지 않는다.
      queueMicrotask(() => void audioRef.current?.play().catch(() => undefined));
    } catch (err) {
      setMade(null);
      setError(err instanceof ApiError ? err.message : '음성을 만들지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const over = text.length > MAX_TEXT;

  return (
    <Modal
      title="TTS 음성 만들기"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            닫기
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void synthesize()}
            disabled={busy || !text.trim() || over || !catalog}
          >
            {busy ? '만드는 중…' : made ? '다시 만들기' : '음성 만들기'}
          </button>
        </>
      }
    >
      {error && (
        <div className="alert" style={{ marginBottom: 14 }}>
          {error}
        </div>
      )}

      <div className="field">
        <label htmlFor="tts-text">방송 문구</label>
        <textarea
          id="tts-text"
          rows={4}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setMade(null);
          }}
          placeholder="예) 주민 여러분께 알려드립니다. 오늘 오후 두 시에 마을회관에서 반상회가 있겠습니다."
        />
        <p className={`hint${over ? ' hint--warn' : ''}`}>
          {text.length} / {MAX_TEXT}자
        </p>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="tts-lang">언어</label>
          <select
            id="tts-lang"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={!catalog}
          >
            {Object.entries(catalog?.languages ?? {}).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="tts-voice">보이스</label>
          <select
            id="tts-voice"
            value={voice}
            onChange={(e) => {
              setVoice(e.target.value);
              setMade(null);
            }}
            disabled={voicesForLanguage.length === 0}
          >
            {voicesForLanguage.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field">
        <label htmlFor="tts-name">파일 이름</label>
        <input
          id="tts-name"
          type="text"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
          placeholder="비워두면 문구 앞부분으로 만듭니다"
        />
      </div>

      {made && (
        <div className="player" style={{ marginBottom: 0 }}>
          <div className="player__meta">
            <span className="strong">{made.filename}</span>
            <span className="dim">
              {made.duration_sec ? `${made.duration_sec.toFixed(1)}초` : ''}
              {cached && ' · 기존 음성 재사용'}
            </span>
          </div>
          <audio
            ref={audioRef}
            src={api.files.audioUrl(made.id)}
            controls
            style={{ width: '100%' }}
          />
          <p className="hint">
            {cached
              ? '같은 문구가 이미 있어 기존 음성을 그대로 씁니다.'
              : '파일함에 저장되었습니다.'}{' '}
            방송은 방송 제어 화면에서 이 파일을 골라 시작합니다.
          </p>
          <button
            type="button"
            className="btn btn--primary btn--block"
            style={{ marginTop: 10 }}
            onClick={() => {
              onCreated(made);
              onClose();
            }}
          >
            완료
          </button>
        </div>
      )}
    </Modal>
  );
}
