import { FormEvent, useState } from 'react';
import { ArrowRight, LockKeyhole, UserRound } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { login, portalApi } from '../lib/portalApi';

export default function LoginPage({ expectedRole }: { expectedRole: 'teacher' | 'admin' }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const user = await login(username.trim(), password);
      if (user.role !== expectedRole) {
        await portalApi.logout();
        throw new Error(expectedRole === 'teacher' ? '该账号不是教师账号' : '该账号不是教务管理员账号');
      }
      navigate(expectedRole === 'admin' ? '/admin' : '/teacher');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="portal-login">
      <main>
        <div className="login-brand"><span>RW</span><div><b>睿文智评</b><small>评审工作台</small></div></div>
        <div className="login-copy">
          <p>HUMAN REVIEW PORTAL</p>
          <h1>人工复核，让每个结论都有责任边界。</h1>
          <span>教师在多智能体初评基础上完成独立判断，教务人员负责分配、进度与结果管理。</span>
        </div>
      </main>
      <section>
        <form onSubmit={submit}>
          <div><small>SECURE ACCESS</small><h2>{expectedRole === 'teacher' ? '教师端登录' : '教务端登录'}</h2><p>使用教务管理员创建的工作台账号</p></div>
          <label><span>用户名</span><div><UserRound size={18}/><input autoFocus value={username} onChange={(event) => setUsername(event.target.value)} required /></div></label>
          <label><span>密码</span><div><LockKeyhole size={18}/><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required /></div></label>
          {error && <p className="form-error">{error}</p>}
          <button disabled={loading}>{loading ? '正在登录...' : '登录'}<ArrowRight size={18}/></button>
        </form>
      </section>
    </div>
  );
}
