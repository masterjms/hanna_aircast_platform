/**
 * TTS 생성 모달.
 *
 * 문구를 합성해 파일함에 넣는다. 방송은 여기서 하지 않는다 — 만든 뒤
 * 들어보고, 방송 제어에서 골라 송출한다. 오타가 마을 스피커 300대로 바로
 * 나가는 일을 막기 위해서다.
 *
 * 화면은 하나다(2026-09-02 단순화). 문구·언어·보이스·파일 이름을 넣고
 * [파일 만들기] → 같은 자리에서 들어보고 → [완료] 로 끝난다.
 *
 * 「완료를 눌러야 파일함에 남는다」가 이 화면의 규칙이다:
 *   · 서버는 합성하는 즉시 파일 행을 만든다(별도 저장 단계가 없다).
 *   · 그래서 완료를 안 누르고 닫거나, 문구를 고쳐 다시 만들면
 *     앞서 만든 것을 여기서 지운다. 안 그러면 들어보고 버린 음성이
 *     파일함에 그대로 쌓인다.
 *   · 단, 기존 합성본을 재사용한 경우(cached)는 지우지 않는다 —
 *     전에 만들어 쓰던 남의 파일을 이 화면이 지우면 안 된다.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { AudioFile, VoiceCatalog } from '../api/types';
import { Modal } from './Modal';

const MAX_TEXT = 1000;

interface TtsModalProps {
  onClose: () => void;
  /** 완료로 확정된 파일. 파일함이 목록을 새로고침한다. */
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
  /** 방금 만든 파일. 서버에는 이미 저장돼 있고, 완료를 눌러야 확정된다. */
  const [made, setMade] = useState<AudioFile | null>(null);
  const [cached, setCached] = useState(false);

  const audioRef = useRef<HTMLAudioElement>(null);
  /**
   * 아직 확정되지 않은 파일 id. 닫히거나 다시 만들 때 이걸 지운다.
   * state 가 아니라 ref 인 이유: 언마운트 정리에서 최신값을 봐야 한다.
   */
  const pendingId = useRef<number | null>(null);

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

  /** 확정 안 된 음성을 서버에서 지운다. 실패해도 화면을 막지 않는다. */
  const discardPending = () => {
    const id = pendingId.current;
    if (id === null) return;
    pendingId.current = null;
    void api.files.remove(id).catch(() => undefined);
  };

  // 언어를 바꾸면 그 언어의 첫 보이스로 맞춘다 — 남의 언어 보이스가 남으면 서버가 거절한다.
  useEffect(() => {
    setVoice(voicesForLanguage[0]?.id ?? '');
    setMade(null);
  }, [voicesForLanguage]);

  // 입력을 고치면 앞서 만든 결과는 무효다. 확정 전이면 서버에서도 지운다.
  const invalidate = () => {
    discardPending();
    setMade(null);
  };

  const synthesize = async () => {
    if (!text.trim()) return;
    // 다시 만들기 전에 앞의 것을 정리한다(같은 문구면 서버가 캐시로 돌려주므로 낭비가 없다).
    discardPending();
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
      // 새로 만든 것만 되돌릴 대상이다. 재사용된 기존 파일은 건드리지 않는다.
      pendingId.current = res.cached ? null : res.file.id;
      // 결과가 나오면 바로 들려준다. 확인이 목적인 화면이라 한 번 더 누르게 하지 않는다.
      queueMicrotask(() => void audioRef.current?.play().catch(() => undefined));
    } catch (err) {
      setMade(null);
      setError(err instanceof ApiError ? err.message : '음성을 만들지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  /** 닫기(머리말 X · ESC · 배경 클릭). 확정 안 한 음성은 남기지 않는다. */
  const handleClose = () => {
    discardPending();
    onClose();
  };

  const confirm = () => {
    if (!made) return;
    pendingId.current = null; // 확정됐으니 정리 대상에서 뺀다
    onCreated(made);
    onClose();
  };

  const over = text.length > MAX_TEXT;

  return (
    <Modal
      title="TTS 음성 만들기"
      onClose={handleClose}
      footer={
        // 만들기 전에는 [파일 만들기] 하나만. 만든 뒤에는 아래 [완료] 가 유일한 다음
        // 동작이라 하단 줄을 아예 없앤다(버튼 줄이 둘이면 무엇을 눌러야 할지 헷갈린다).
        made ? undefined : (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void synthesize()}
            disabled={busy || !text.trim() || over || !catalog}
          >
            {busy ? '만드는 중…' : '파일 만들기'}
          </button>
        )
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
            invalidate();
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
              invalidate();
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
              ? '같은 문구가 이미 있어 기존 음성을 그대로 씁니다. 완료를 누르면 파일함에서 확인할 수 있습니다.'
              : '들어보고 완료를 누르면 파일함에 등록됩니다. 그냥 닫으면 등록하지 않습니다.'}
          </p>
          <button
            type="button"
            className="btn btn--primary btn--block"
            style={{ marginTop: 10 }}
            onClick={confirm}
          >
            완료
          </button>
        </div>
      )}
    </Modal>
  );
}
