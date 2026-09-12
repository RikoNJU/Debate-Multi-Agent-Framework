import { Link, useLocation } from 'react-router-dom';

export default function TopNav() {
  const location = useLocation();

  return (
    <header className="topbar topbar-home">
      <div className="topbar-brand">
        <div className="brand-mark">
          <img src="/assets/rwzp-logo.jpg" alt="睿文智评标志" />
        </div>
        <div className="topbar-brand-text">
          <strong>睿文智评</strong>
        </div>
      </div>
      <nav className="topbar-nav" aria-label="主导航">
        <Link className={location.pathname === "/" ? "active" : ""} to="/">首页</Link>
        <Link className={location.pathname.startsWith("/student") ? "active" : ""} to="/student">学生端</Link>
        <Link className={location.pathname.startsWith("/aigc") ? "active" : ""} to="/aigc">AIGC 检测</Link>
        <Link className={location.pathname.startsWith("/workspace") || location.pathname === "/login" ? "active" : ""} to="/workspace">教师端</Link>
      </nav>
    </header>
  );
}
