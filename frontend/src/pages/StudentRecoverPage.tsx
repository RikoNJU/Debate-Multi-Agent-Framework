import { FormEvent, useState } from 'react';
import { ArrowLeft, KeyRound, Search } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

import { recoverTask, TaskRecord } from '../lib/reviewApi';

const TASKS_KEY = 'debate-review-tasks';

export default function StudentRecoverPage() {
  const navigate = useNavigate();
  const [taskId, setTaskId] = useState('');
  const [accessCode, setAccessCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setLoading(true);
    try {
      const snapshot = await recoverTask(taskId.trim(), accessCode.trim());
      const raw = localStorage.getItem(TASKS_KEY);
      const current: TaskRecord[] = raw ? JSON.parse(raw) : [];
      const record: TaskRecord = {
        id: taskId.trim(),
        paperId: snapshot.paper_id,
        title: snapshot.result?.context?.profile?.title || snapshot.paper_id || '已找回的评审任务',
        fileName: '论文文件',
        status: snapshot.status === 'succeeded' ? 'completed' : snapshot.status === 'failed' || snapshot.status === 'interrupted' ? 'failed' : 'processing',
        createdAt: new Date(snapshot.created_at).toLocaleString('zh-CN'),
        accessToken: accessCode.trim(),
      };
      localStorage.setItem(TASKS_KEY, JSON.stringify([record, ...current.filter(item => item.id !== record.id)]));
      navigate(`/student/tasks/${record.id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '任务找回失败');
    } finally { setLoading(false); }
  };

  return <div className="recover-page">
    <header><Link to="/student"><ArrowLeft size={18}/>返回学生端</Link><b>睿文智评</b></header>
    <main><div className="recover-symbol"><KeyRound/></div><small>RECOVER REVIEW</small><h1>找回评审任务</h1><p>学生不需要注册账号。输入上传后获得的任务编号和访问码，即可在当前浏览器恢复任务。</p>
      <form onSubmit={submit}><label><span>任务编号</span><input value={taskId} onChange={e => setTaskId(e.target.value)} required/></label><label><span>任务访问码</span><input value={accessCode} onChange={e => setAccessCode(e.target.value)} required/></label>{error && <div className="form-error">{error}</div>}<button disabled={loading}><Search size={17}/>{loading ? '正在验证...' : '验证并找回'}</button></form>
      <aside>访问码等同于该任务的查看凭证，请勿公开转发。系统数据库只保存访问码哈希，无法替你查看原始访问码。</aside>
    </main>
  </div>;
}
