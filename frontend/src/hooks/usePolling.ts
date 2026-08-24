/**
 * 폴링 훅.
 *
 * 대시보드·단말 목록은 폴링으로 갱신한다(기본 5초, 방송 중 2초).
 * WebSocket 을 쓰지 않는 이유는 갱신 주기가 느슨하고, 연결 관리 비용이
 * 얻는 것보다 크기 때문이다.
 *
 * 두 가지를 신경 쓴다:
 *   · 탭이 백그라운드면 멈춘다 — 안 보는 화면 때문에 서버를 때리지 않는다.
 *   · 이전 응답이 늦게 도착해도 최신 것을 덮어쓰지 않는다(경쟁 상태 방지).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface PollingResult<T> {
  data: T | null;
  error: Error | null;
  /** 최초 1회 로딩. 이후 갱신에서는 false 를 유지해 화면이 깜빡이지 않게 한다. */
  loading: boolean;
  /**
   * 마지막으로 응답을 받은 시각(ms). 아직 못 받았으면 0.
   *
   * "지금 보고 있는 data 가 언제 것인가"를 알아야 하는 화면이 있다. 예를 들어
   * 방송을 막 시작한 직후에는 목록이 아직 그 방송을 모르는데, 그걸 "끝났다"로
   * 오해하면 안 된다.
   */
  fetchedAt: number;
  reload: () => void;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = [],
): PollingResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  // 응답 순서가 뒤바뀌어도 오래된 결과가 새 결과를 덮지 않게 한다.
  const generation = useRef(0);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    const mine = ++generation.current;

    const run = async () => {
      try {
        const next = await fetcherRef.current();
        if (!stopped && mine === generation.current) {
          setData(next);
          setError(null);
          setFetchedAt(Date.now());
        }
      } catch (err) {
        if (!stopped && mine === generation.current) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!stopped && mine === generation.current) setLoading(false);
      }
    };

    const schedule = () => {
      window.clearTimeout(timer);
      // 탭이 안 보이면 다음 폴링을 걸지 않는다. 돌아오면 visibilitychange 가 즉시 깨운다.
      if (document.visibilityState !== 'visible') return;
      timer = window.setTimeout(async () => {
        await run();
        schedule();
      }, intervalMs);
    };

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void run();
        schedule();
      } else {
        window.clearTimeout(timer);
      }
    };

    void run();
    schedule();
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  return { data, error, loading, fetchedAt, reload };
}

/** 화면 갱신 주기 (ms). 사양: 기본 5초, 방송 진행 중 2초. */
export const POLL_INTERVAL = {
  normal: 5000,
  broadcasting: 2000,
} as const;
