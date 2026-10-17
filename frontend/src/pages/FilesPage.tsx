/**
 * 파일함.
 *
 * 파일은 계정별이다(문제점 86번, 2026-10-14) — 내가 올리거나 만든 것만 보인다. 최고 관리자는 전부.
 *
 * 삭제를 막는 것은 **스케줄뿐**이다(0017). 방송 이력은 파일명을 자기 행에 갖고 있어
 * 파일이 지워져도 「무엇을」이 남는다. 어느 스케줄이 막는지는 목록에서 미리 보여준다 —
 * 눌러 보고 나서야 알게 하면 "스케줄에 넣은 적 없는데 왜 안 지워지지"가 된다.
 *
 * 업로드와 TTS 생성 두 경로로 파일이 들어온다. 둘 다 방송은 하지 않는다 —
 * 방송 제어 화면에서 골라 송출한다.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import type { AudioFile } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { PageSizeSelect, PagerBar, usePager } from '../components/Pager';
import { TtsModal } from '../components/TtsModal';
import { PageHead } from '../components/PageHead';

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
  const { isSuperAdmin } = useAuth();
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
        try {
          await api.files.upload(f);
        } catch (err) {
          // 방송 규격보다 음질이 좋은 파일은 변환해서 넣을지 물어본다(문제점 31번).
          // 규격보다 낮은 파일은 서버가 아예 거절하므로 여기로 오지 않는다.
          if (!(err instanceof ApiError) || err.code !== 'AUDIO_NEEDS_TRANSCODE') throw err;
          if (!window.confirm(`"${f.name}"\n\n${err.message}`)) continue;
          await api.files.upload(f, true);
        }
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '업로드에 실패했습니다.');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  // 지우기는 확인 창에서 — 네 자리 숫자 + 내 비밀번호(문제점 84번).
  const [deleting, setDeleting] = useState<AudioFile | null>(null);
  const remove = (f: AudioFile) => setDeleting(f);

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
  // 목록 공통 쪽 넘기기(문제점 64번).
  const pager = usePager(files);

  return (
    <>
      {/* 버튼은 제목 바로 옆 — 오른쪽 끝에 두면 넓은 화면에서 눈에 안 띈다(2026-09-21, 화면 공통 96번). */}
      <PageHead
        title="방송 자료"
        actions={
          <>
          <input
            ref={inputRef}
            type="file"
            accept="audio/mpeg,.mp3"
            multiple
            hidden
            onChange={(e) => void upload(e.target.files)}
          />
          <button type="button" className="btn btn--lg" onClick={() => setTtsOpen(true)}>
            ✎ 글로 음성 만들기
          </button>
          <button
            type="button"
            className="btn btn--primary btn--lg"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? '올리는 중…' : '소리 파일 올리기'}
          </button>
          </>
        }
      >
        {isSuperAdmin ? '모든 계정의 방송용 소리' : '내 방송용 소리'} {files.length}개 · mp3 파일을 올리거나, 글을 적어
        음성(TTS)으로 만듭니다. 방송 자료는 계정마다 따로입니다.
      </PageHead>

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

      {files.length > 0 && (
        <div className="filters" style={{ marginBottom: 8 }}>
          {/* 개수는 제목 아래 설명에 있다 — 여기서 또 쓰지 않는다(96번 ③) */}
          <div className="filters__spacer" />
          <PageSizeSelect value={pager.size} onChange={pager.setSize} />
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
          <table className="files-table">
            <colgroup>
              <col className="files-table__name" />
              <col className="files-table__col" />
              <col className="files-table__col" />
              <col className="files-table__col" />
              <col className="files-table__col" />
              <col className="files-table__col" />
              <col className="files-table__act" />
            </colgroup>
            <thead>
              <tr>
                <th>파일명</th>
                <th>생성</th>
                <th className="num">길이</th>
                <th className="num">크기</th>
                <th>아이디</th>
                <th className="num">등록일</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {pager.rows.map((f) => (
                <tr key={f.id}>
                  <td className="strong" title={f.filename}>{f.filename}</td>
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
                      onClick={() => remove(f)}
                      disabled={f.schedule_labels.length > 0}
                      title={
                        f.schedule_labels.length > 0
                          ? `스케줄 ${f.schedule_labels.length}건이 쓰고 있습니다: ${f.schedule_labels.join(', ')}`
                          : undefined
                      }
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
      {files.length > 0 && <PagerBar total={pager.total} page={pager.page} pages={pager.pages} onPage={pager.setPage} unit="개" />}

      {ttsOpen && (
        <TtsModal
          onClose={() => setTtsOpen(false)}
          onCreated={() => void load()}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="방송 자료 삭제"
          target={deleting.filename}
          warning="파일이 영구히 지워집니다. 방송 기록에는 이름이 그대로 남습니다. 이 파일을 쓰는 예약 방송이 있으면 지워지지 않습니다."
          requireCode
          danger
          confirmLabel="영구 삭제"
          onConfirm={async (pw) => {
            await api.files.remove(deleting.id, pw);
            if (playing === deleting.id) setPlaying(null);
            await load();
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </>
  );
}
