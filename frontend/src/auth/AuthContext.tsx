/**
 * 로그인 상태.
 *
 * 토큰은 client.ts 가 들고 있고, 여기서는 "누가 로그인했는가"만 관리한다.
 * 새로고침하면 저장된 토큰으로 /api/auth/me 를 다시 불러 복구한다.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { api, getToken, setToken, setUnauthorizedHandler } from '../api/client';
import type { Me } from '../api/types';

interface AuthState {
  user: Me | null;
  /** 저장된 토큰으로 세션을 복구하는 중. 이 동안은 로그인 화면으로 튕기지 않는다. */
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  isSuperAdmin: boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setUser(await api.auth.me());
    } catch {
      // 토큰이 만료·삭제된 경우. client.ts 가 이미 토큰을 비웠다.
      setUser(null);
    }
  }, []);

  // 저장된 토큰이 있으면 세션 복구를 시도한다.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (getToken()) await refresh();
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // 어떤 요청이든 401 을 받으면 즉시 로그아웃 상태로 만든다.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.auth.login(username, password);
    setToken(res.access_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      // 서버 호출이 실패해도 클라이언트에서는 반드시 지운다.
      setToken(null);
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login,
      logout,
      refresh,
      isSuperAdmin: user?.role === 'super_admin',
    }),
    [user, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth 는 AuthProvider 안에서만 쓸 수 있습니다.');
  return ctx;
}
