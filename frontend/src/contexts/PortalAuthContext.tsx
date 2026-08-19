import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

import {
  login as portalLogin,
  getToken,
  PORTAL_AUTH_INVALID_EVENT,
  portalApi,
  PortalUser,
} from '../lib/portalApi';

type PortalAuthValue = {
  user: PortalUser | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<PortalUser>;
  signOut: () => Promise<void>;
  refresh: () => Promise<PortalUser | null>;
};

const PortalAuthContext = createContext<PortalAuthValue | null>(null);

export function PortalAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<PortalUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return null;
    }
    try {
      const current = await portalApi.me();
      setUser(current);
      return current;
    } catch {
      setUser(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const invalidate = () => {
      setUser(null);
      setLoading(false);
    };
    window.addEventListener(PORTAL_AUTH_INVALID_EVENT, invalidate);
    return () => window.removeEventListener(PORTAL_AUTH_INVALID_EVENT, invalidate);
  }, []);

  const signIn = async (username: string, password: string) => {
    const current = await portalLogin(username, password);
    setUser(current);
    return current;
  };

  const signOut = async () => {
    await portalApi.logout();
    setUser(null);
  };

  return (
    <PortalAuthContext.Provider value={{ user, loading, signIn, signOut, refresh }}>
      {children}
    </PortalAuthContext.Provider>
  );
}

export function usePortalAuth(): PortalAuthValue {
  const value = useContext(PortalAuthContext);
  if (!value) throw new Error('usePortalAuth must be used inside PortalAuthProvider');
  return value;
}
