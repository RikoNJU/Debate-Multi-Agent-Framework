import { ClipboardCheck, FileStack, LogOut, ShieldCheck } from 'lucide-react';
import { ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { usePortalAuth } from '../contexts/PortalAuthContext';
import { PortalUser } from '../lib/portalApi';

export default function PortalShell({ user, children }: { user: PortalUser; children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { signOut } = usePortalAuth();
  const items = user.role === 'admin'
    ? [
        { to: '/workspace/reviews', label: '评审任务', icon: ClipboardCheck },
        { to: '/workspace/admin', label: '教务管理', icon: ShieldCheck },
      ]
    : [{ to: '/workspace/reviews', label: '评审任务', icon: ClipboardCheck }];

  return (
    <div className="portal-shell">
      <aside>
        <Link className="portal-brand" to="/workspace"><span>RW</span><div><b>睿文智评</b><small>Review Console</small></div></Link>
        <nav>
          <small>工作空间</small>
          {items.map((item) => <Link className={location.pathname === item.to ? 'active' : ''} to={item.to} key={item.to}><item.icon size={18}/>{item.label}</Link>)}
          <Link to="/student"><FileStack size={18}/>学生端</Link>
        </nav>
        <div className="portal-account">
          <span>{user.display_name.slice(0, 1)}</span>
          <div><b>{user.display_name}</b><small>{user.role === 'admin' ? '评审与教务管理员' : '评审教师'}</small></div>
          <button title="退出登录" onClick={async () => { await signOut(); navigate('/login'); }}><LogOut size={17}/></button>
        </div>
      </aside>
      <div className="portal-content">{children}</div>
    </div>
  );
}
