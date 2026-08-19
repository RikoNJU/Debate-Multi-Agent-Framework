import { FormEvent, useEffect, useMemo, useState } from 'react';
import { BarChart3, CheckCircle2, Download, FileText, Plus, Search, UserPlus, UsersRound } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import PortalShell from '../components/PortalShell';
import { AdminPaper, getToken, portalApi, PortalStatistics, PortalUser } from '../lib/portalApi';

type Tab = 'overview' | 'papers' | 'users';

export default function AdminPortalPage() {
  const navigate = useNavigate();
  const [user, setUser] = useState<PortalUser | null>(null);
  const [stats, setStats] = useState<PortalStatistics | null>(null);
  const [papers, setPapers] = useState<AdminPaper[]>([]);
  const [users, setUsers] = useState<PortalUser[]>([]);
  const [tab, setTab] = useState<Tab>('overview');
  const [search, setSearch] = useState('');
  const [message, setMessage] = useState('');
  const [newUserOpen, setNewUserOpen] = useState(false);

  const load = async () => {
    try {
      const [current, statistics, paperList, userList] = await Promise.all([portalApi.me(), portalApi.statistics(), portalApi.papers(), portalApi.users()]);
      if (current.role !== 'admin') return navigate('/teacher');
      setUser(current); setStats(statistics); setPapers(paperList); setUsers(userList);
    } catch { navigate('/login'); }
  };
  useEffect(() => { load(); }, []);

  const teachers = users.filter(item => item.role === 'teacher' && item.is_active);
  const filtered = useMemo(() => papers.filter(item => `${item.title} ${item.paper_id}`.toLowerCase().includes(search.toLowerCase())), [papers, search]);

  const assign = async (paperId: string, reviewerId: string) => {
    if (!reviewerId) return;
    try { await portalApi.assign(paperId, reviewerId); setMessage('教师分配成功'); await load(); }
    catch (exc) { setMessage(exc instanceof Error ? exc.message : '分配失败'); }
  };

  const exportCsv = async () => {
    const response = await fetch(portalApi.exportUrl(), { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!response.ok) return setMessage('导出失败');
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'review-results.csv'; anchor.click(); URL.revokeObjectURL(url);
  };

  const createUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await portalApi.createUser({ username: data.get('username'), display_name: data.get('displayName'), password: data.get('password'), role: data.get('role') });
      setNewUserOpen(false); setMessage('账号创建成功'); await load();
    } catch (exc) { setMessage(exc instanceof Error ? exc.message : '创建失败'); }
  };

  if (!user || !stats) return <div className="portal-loading">正在加载教务工作台...</div>;
  const distribution = [
    ['优秀', stats.score_distribution.excellent || 0, '#198754'], ['良好', stats.score_distribution.good || 0, '#1570ef'],
    ['合格', stats.score_distribution.pass || 0, '#d97706'], ['不合格', stats.score_distribution.fail || 0, '#c2413b'],
  ] as const;
  const maxDistribution = Math.max(1, ...distribution.map(item => item[1]));

  return <PortalShell user={user}>
    <main className="admin-page">
      <header><div><small>ACADEMIC ADMINISTRATION</small><h1>评审管理</h1><p>分配教师、跟踪人工复核并管理结果</p></div><button onClick={exportCsv}><Download size={17}/>导出结果</button></header>
      <nav className="portal-tabs"><button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}><BarChart3 size={17}/>总览</button><button className={tab === 'papers' ? 'active' : ''} onClick={() => setTab('papers')}><FileText size={17}/>论文管理</button><button className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}><UsersRound size={17}/>账号管理</button></nav>
      {message && <div className="admin-message" onClick={() => setMessage('')}><CheckCircle2 size={16}/>{message}</div>}

      {tab === 'overview' && <>
        <section className="metric-grid"><div><span>论文总数</span><b>{stats.total_papers}</b><small>已持久化论文</small></div><div><span>已分配任务</span><b>{stats.total_assignments}</b><small>{stats.pending_assignments} 项待完成</small></div><div><span>终审完成</span><b>{stats.submitted_reviews}</b><small>教师已提交</small></div><div><span>人工平均分</span><b>{stats.average_human_score || '-'}</b><small>百分制</small></div></section>
        <section className="admin-band"><div className="distribution"><h2>人工评分分布</h2>{distribution.map(item => <div key={item[0]}><span>{item[0]}</span><i><em style={{ width: `${item[1] / maxDistribution * 100}%`, background: item[2] }}/></i><b>{item[1]}</b></div>)}</div><div className="recent-papers"><h2>最近论文</h2>{papers.slice(0,5).map(paper => <button onClick={() => { setSearch(paper.paper_id); setTab('papers'); }} key={paper.paper_id}><i><FileText size={17}/></i><span><b>{paper.title}</b><small>{paper.paper_id} · {paper.run_status || '暂无评审'}</small></span><strong>{paper.ai_score ?? '-'}</strong></button>)}</div></section>
      </>}

      {tab === 'papers' && <section className="management-section"><div className="section-tools"><label><Search size={17}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索论文"/></label><span>{filtered.length} 篇论文</span></div><div className="admin-paper-list">{filtered.map(paper => <article key={paper.paper_id}><div className="paper-overview"><i><FileText size={20}/></i><div><h3>{paper.title}</h3><p>{paper.paper_id} · {paper.paper_type || '待分类'} · {paper.source_filename}</p></div><span>AI 评分 <b>{paper.ai_score ?? '-'}</b></span></div><div className="assignment-line"><div>{paper.assignments.length ? paper.assignments.map(item => <span key={item.assignment_id}>{item.reviewer_name}<em className={`status ${item.status}`}>{item.status === 'submitted' ? '已提交' : '进行中'}</em></span>) : <small>尚未分配教师</small>}</div><select defaultValue="" onChange={e => { assign(paper.paper_id, e.target.value); e.target.value = ''; }}><option value="" disabled>分配教师...</option>{teachers.filter(teacher => !paper.assignments.some(a => a.reviewer_id === teacher.id)).map(teacher => <option value={teacher.id} key={teacher.id}>{teacher.display_name}</option>)}</select></div></article>)}</div></section>}

      {tab === 'users' && <section className="management-section"><div className="section-tools"><div><h2>工作台账号</h2><p>账号只能由管理员创建</p></div><button className="primary" onClick={() => setNewUserOpen(true)}><UserPlus size={17}/>创建账号</button></div><div className="user-list"><div className="table-head"><span>用户</span><span>用户名</span><span>角色</span><span>状态</span></div>{users.map(item => <div className="user-row" key={item.id}><span><i>{item.display_name.slice(0,1)}</i><b>{item.display_name}</b></span><span>{item.username}</span><span>{item.role === 'admin' ? '管理员' : '教师'}</span><span className="active-user">启用</span></div>)}</div></section>}
    </main>
    {newUserOpen && <div className="portal-modal" onMouseDown={() => setNewUserOpen(false)}><form onSubmit={createUser} onMouseDown={e => e.stopPropagation()}><header><div><small>NEW ACCOUNT</small><h2>创建工作台账号</h2></div><button type="button" onClick={() => setNewUserOpen(false)}>×</button></header><label><span>显示名称</span><input name="displayName" required/></label><label><span>用户名</span><input name="username" pattern="[A-Za-z0-9_.-]+" required/></label><label><span>初始密码</span><input name="password" type="password" minLength={8} required/></label><label><span>角色</span><select name="role"><option value="teacher">评审教师</option><option value="admin">管理员</option></select></label><button className="primary"><Plus size={17}/>创建账号</button></form></div>}
  </PortalShell>;
}
