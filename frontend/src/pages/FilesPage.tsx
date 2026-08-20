/**
 * 파일함.
 *
 * 파일은 전체 공용이다 — 마을 범위로 나누지 않는다. 삭제는 이력이 참조하지
 * 않는 파일만 가능하고, 참조 중이면 백엔드가 FILE_IN_USE 로 막는다.
 *
 * 업로드와 TTS 생성 두 경로로 파일이 들어온다. 둘 다 방송은 하지 않는다 —
 * 방송 제어 화면에서 골라 송출한다.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { AudioFile } from '../api/types';
import { TtsModal } from '../components/TtsModal';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(sec: number | null): string {
  if (sec === null) return '—';
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export function FilesPage() {
  const [files, setFiles] = useState<AudioFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [playing, setPlaying] = useState<number | null>(null);
  const [ttsOpen, setTtsOpen] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const load = useCallback(async () => {
    try {
      setFiles(await api.files.list());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '파일 목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const upload = async (picked: FileList | null) => {
    if (!picked?.length) return;
    setUploading(true);
    setError(null);
    try {
      for (const f of Array.from(picked)) {
        await api.files.upload(f);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '업로드에 실패했습니다.');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const remove = async (f: AudioFile) => {
    if (!window.confirm(`"${f.filename}" 을(를) 삭제할까요?`)) return;
    setError(null);
    try {
      await api.files.remove(f.id);
      if (playing === f.id) setPlaying(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '삭제에 실패했습니다.');
    }
  };

  const preview = (f: AudioFile) => {
    // 같은 파일을 다시 누르면 정지 — 재생 버튼이 토글로 동작한다.
    if (playing === f.id) {
      audioRef.current?.pause();
      setPlaying(null);
      return;
    }
    setPlaying(f.id);
    // src 가 바뀐 뒤 재생해야 해서 다음 틱으로 미룬다.
    queueMicrotask(() => void audioRef.current?.play().catch(() => setPlaying(null)));
  };

  const playingFile = files.find((f) => f.id === playing) ?? null;

  return (
    <>
      <div className="page-head page-head--row">
        <div>
          <h1>파일함</h1>
          <p>방송용 오디오 {files.length}개 · mp3 업로드 또는 TTS 로 만듭니다.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            ref={inputRef}
            type="file"
            accept="audio/mpeg,.mp3"
            multiple
            hidden
            onChange={(e) => void upload(e.target.files)}
          />
          <button type="button" className="btn" onClick={() => setTtsOpen(true)}>
            TTS 만들기
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? '업로드 중…' : '파일 업로드'}
          </button>
        </div>
      </div>

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* 미리듣기 플레이어. 재생 중일 때만 보인다. */}
      {playingFile && (
        <div className="player">
          <div className="player__meta">
            <span className="strong">{playingFile.filename}</span>
            <span className="dim">{formatDuration(playingFile.duration_sec)}</span>
          </div>
          <audio
            ref={audioRef}
            src={api.files.audioUrl(playingFile.id)}
            controls
            onEnded={() => setPlaying(null)}
            style={{ width: '100%' }}
          />
        </div>
      )}

      <div className="table-wrap table-wrap--scroll">
        {loading ? (
          <div className="empty">불러오는 중…</div>
        ) : files.length === 0 ? (
          <div className="empty">
            아직 파일이 없습니다. mp3 를 올리거나 TTS 로 문구를 음성으로 만드세요.
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>파일명</th>
                <th>생성</th>
                <th className="num">길이</th>
                <th className="num">크기</th>
                <th>올린 사람</th>
                <th className="num">등록일</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.id}>
                  <td className="strong">{f.filename}</td>
                  <td>
                    <span className={`badge badge--${f.source === 'tts' ? 'warn' : 'idle'}`}>
                      {f.source === 'tts' ? `TTS · ${f.tts_lang ?? ''}` : '업로드'}
                    </span>
                  </td>
                  <td className="num">{formatDuration(f.duration_sec)}</td>
                  <td className="num dim">{formatSize(f.size_bytes)}</td>
                  <td className="dim">{f.uploaded_by_name ?? '—'}</td>
                  <td className="num dim">
                    {new Date(f.created_at).toLocaleDateString('ko-KR')}
                  </td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button type="button" className="btn btn--ghost" onClick={() => preview(f)}>
                      {playing === f.id ? '정지' : '미리듣기'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost btn--danger"
                      onClick={() => void remove(f)}
                    >
                      삭제
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {ttsOpen && (
        <TtsModal
          onClose={() => setTtsOpen(false)}
          onCreated={() => void load()}
        />
      )}
    </>
  );
}
