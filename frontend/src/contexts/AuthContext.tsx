/**
 * 认证上下文 —— 全局管理用户登录状态
 */
import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import type { UserInfo } from '../api/auth';
import { getMe } from '../api/auth';
import { getUser, setToken, setUser, logout as doLogout, isAuthenticated } from '../utils/auth';

interface AuthContextType {
  user: UserInfo | null;
  loading: boolean;
  login: (token: string, user: UserInfo) => void;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>(null!);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<UserInfo | null>(getUser());
  const [loading, setLoading] = useState(true);

  // 初始化：如果有 token 则刷新用户信息
  useEffect(() => {
    if (isAuthenticated()) {
      getMe()
        .then((u) => {
          setUserState(u);
          setUser(u);
        })
        .catch(() => {
          doLogout();
          setUserState(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = useCallback((token: string, userInfo: UserInfo) => {
    setToken(token);
    setUser(userInfo);
    setUserState(userInfo);
  }, []);

  const logout = useCallback(() => {
    doLogout();
    setUserState(null);
  }, []);

  const refresh = useCallback(async () => {
    const u = await getMe();
    setUser(u);
    setUserState(u);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
