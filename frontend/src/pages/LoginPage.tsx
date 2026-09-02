import { FormEvent, useState } from 'react';
import { ArrowLeft, ArrowRight, LockKeyhole, UserRound } from 'lucide-react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';

import { usePortalAuth } from '../contexts/PortalAuthContext';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading: restoring, signIn } = usePortalAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      await signIn(username.trim(), password);
      const requested = (location.state as { from?: string } | null)?.from;
      navigate(requested?.startsWith('/workspace') ? requested : '/workspace/reviews', { replace: true });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  if (restoring) return <div className="portal-loading">正在恢复工作台会话...</div>;
  if (user) return <Navigate to="/workspace/reviews" replace />;

  return (
    <div className="portal-login">
      <main>
        <div className="login-brand"><span>南京大学</span><div><b>睿文智评</b><small>评审工作台</small></div></div>
        <div className="login-copy">
          <p>HUMAN REVIEW PORTAL</p>
          <h1>人工复核，让每个结论都有责任边界。</h1>
          <span>教师在多智能体初评基础上完成独立判断，教务人员负责分配、进度与结果管理。</span>
        </div>
      </main>
      <section>
        <form onSubmit={submit}>
          <div><small>SECURE ACCESS</small><h2>工作人员登录</h2><p>一次登录即可进入论文评审与教务管理</p></div>
          <label><span>用户名</span><div><UserRound size={18}/><input autoFocus value={username} onChange={(event) => setUsername(event.target.value)} required /></div></label>
          <label><span>密码</span><div><LockKeyhole size={18}/><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required /></div></label>
          {error && <p className="form-error">{error}</p>}
          <button disabled={loading}>{loading ? '正在登录...' : '登录'}<ArrowRight size={18}/></button>
          <Link className="login-student-link" to="/student"><ArrowLeft size={15}/>返回学生端</Link>
        </form>
      </section>
    </div>
  );
}
