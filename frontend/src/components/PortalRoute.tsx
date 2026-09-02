import { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { usePortalAuth } from '../contexts/PortalAuthContext';

export function PortalRoute({ children, adminOnly = false }: { children: ReactNode; adminOnly?: boolean }) {
  const { user, loading } = usePortalAuth();
  const location = useLocation();

  if (loading) return <div className="portal-loading">正在恢复工作台会话...</div>;
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  if (adminOnly && user.role !== 'admin') return <Navigate to="/workspace/reviews" replace />;
  return children;
}

export function WorkspaceEntry() {
  const { user, loading } = usePortalAuth();
  if (loading) return <div className="portal-loading">正在恢复工作台会话...</div>;
  return <Navigate to={user ? '/workspace/reviews' : '/login'} replace />;
}
