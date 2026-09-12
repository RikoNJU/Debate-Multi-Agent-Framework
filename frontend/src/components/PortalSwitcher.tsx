import { Link, useLocation } from 'react-router-dom';

const PORTALS = [
  { to: '/', label: '首页', isActive: (path: string) => path === '/' },
  { to: '/student', label: '学生端', isActive: (path: string) => path.startsWith('/student') },
  { to: '/aigc', label: 'AIGC 检测', isActive: (path: string) => path.startsWith('/aigc') },
  {
    to: '/workspace',
    label: '教师端',
    isActive: (path: string) => path.startsWith('/workspace') || path === '/login',
  },
];

export default function PortalSwitcher({ className = '' }: { className?: string }) {
  const { pathname } = useLocation();

  return (
    <nav className={`portal-switcher ${className}`.trim()} aria-label="端口切换">
      {PORTALS.map((portal) => (
        <Link
          key={portal.to}
          to={portal.to}
          className={portal.isActive(pathname) ? 'active' : ''}
        >
          {portal.label}
        </Link>
      ))}
    </nav>
  );
}