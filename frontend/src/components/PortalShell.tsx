import { ClipboardCheck, FileStack, LogOut, ShieldCheck } from 'lucide-react';
import { ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { portalApi, PortalUser } from '../lib/portalApi';

export default function PortalShell({ user, children }: { user: PortalUser; children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const items = user.role === 'admin'
    ? [{ to: '/admin', label: '教务管理', icon: ShieldCheck }]
    : [{ to: '/teacher', label: '评审任务', icon: ClipboardCheck }];

  return (
    <div className="portal-shell">
      <aside>
        <Link className="portal-brand" to="/"><span>RW</span><div><b>睿文智评</b><small>Review Console</small></div></Link>
        <nav>
          <small>工作空间</small>
          {items.map((item) => <Link className={location.pathname === item.to ? 'active' : ''} to={item.to} key={item.to}><item.icon size={18}/>{item.label}</Link>)}
          <Link to="/"><FileStack size={18}/>智能评审</Link>
        </nav>
        <div className="portal-account">
          <span>{user.display_name.slice(0, 1)}</span>
          <div><b>{user.display_name}</b><small>{user.role === 'admin' ? '教务管理员' : '评审教师'}</small></div>
          <button title="退出登录" onClick={async () => { await portalApi.logout(); navigate('/login'); }}><LogOut size={17}/></button>
        </div>
      </aside>
      <div className="portal-content">{children}</div>
    </div>
  );
}
