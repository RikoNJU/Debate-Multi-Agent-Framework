import { useEffect, useMemo, useState } from 'react';
import { Check, ChevronLeft, FileText, Save, Search, Send, Sparkles } from 'lucide-react';

import PortalShell from '../components/PortalShell';
import { usePortalAuth } from '../contexts/PortalAuthContext';
import { Assignment, Criterion, getToken, portalApi } from '../lib/portalApi';

const emptyScores = () => Array(18).fill(0);

function scoresFromTotal(total?: number): number[] {
  if (total === undefined) return emptyScores();
  let remaining = Math.max(0, Math.min(54, Math.round(total / 100 * 54)));
  return Array.from({ length: 18 }, () => {
    const score = Math.min(3, remaining);
    remaining -= score;
    return score;
  });
}

export default function TeacherPortalPage() {
  const { user } = usePortalAuth();
  const [items, setItems] = useState<Assignment[]>([]);
  const [criteria, setCriteria] = useState<Criterion[]>([]);
  const [selected, setSelected] = useState<Assignment | null>(null);
  const [scores, setScores] = useState<number[]>(emptyScores);
  const [advice, setAdvice] = useState('');
  const [comments, setComments] = useState('');
  const [search, setSearch] = useState('');
  const [message, setMessage] = useState('');
  const [pdfUrl, setPdfUrl] = useState('');

  const load = async () => {
    try {
      const [assignments, criterionList] = await Promise.all([portalApi.assignments(), portalApi.criteria()]);
      setItems(assignments); setCriteria(criterionList);
    } catch (exc) { setMessage(exc instanceof Error ? exc.message : '工作台加载失败'); }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => () => { if (pdfUrl) URL.revokeObjectURL(pdfUrl); }, [pdfUrl]);

  const choose = async (item: Assignment) => {
    const detail = await portalApi.assignment(item.assignment_id);
    setSelected(detail);
    setScores(
      detail.human_review?.section_scores
      || detail.ai_section_scores
      || scoresFromTotal(detail.ai_score),
    );
    setAdvice(detail.human_review?.advice_content || '');
    setComments(detail.human_review?.teacher_comments || '');
    setMessage('');
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    const response = await fetch(portalApi.pdfUrl(item.assignment_id), { headers: { Authorization: `Bearer ${getToken()}` } });
    setPdfUrl(response.ok ? URL.createObjectURL(await response.blob()) : '');
  };

  const total = Math.round(scores.reduce((sum, score) => sum + score, 0) / 54 * 100);
  const save = async (submit: boolean) => {
    if (!selected) return;
    try {
      const body = { section_scores: scores, advice_content: advice, teacher_comments: comments };
      const review = submit ? await portalApi.submitReview(selected.assignment_id, body) : await portalApi.saveReview(selected.assignment_id, body);
      setSelected({ ...selected, status: submit ? 'submitted' : 'in_review', human_review: review });
      setMessage(submit ? '终审已提交，结果已锁定' : '草稿已保存');
      await load();
    } catch (exc) { setMessage(exc instanceof Error ? exc.message : '保存失败'); }
  };

  const filtered = useMemo(() => items.filter((item) => `${item.title} ${item.paper_id}`.toLowerCase().includes(search.toLowerCase())), [items, search]);
  if (!user) return <div className="portal-loading">正在加载教师工作台...</div>;

  return <PortalShell user={user}>
    {!selected ? <main className="portal-list-page">
      <header><div><small>TEACHER WORKSPACE</small><h1>我的评审任务</h1><p>基于多智能体初评完成独立人工复核</p></div><div className="summary-strip"><span><b>{items.length}</b>全部任务</span><span><b>{items.filter(i => i.status === 'submitted').length}</b>已完成</span><span><b>{items.filter(i => i.status !== 'submitted').length}</b>待处理</span></div></header>
      <div className="portal-toolbar"><label><Search size={17}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索论文标题或编号"/></label></div>
      <div className="portal-table">
        <div className="table-head"><span>论文</span><span>类型</span><span>AI 初评分</span><span>人工评审</span><span></span></div>
        {filtered.map(item => <button className="table-row" key={item.assignment_id} onClick={() => choose(item)}>
          <span className="paper-cell"><i><FileText size={18}/></i><span><b>{item.title}</b><small>{item.paper_id} · {item.source_filename}</small></span></span>
          <span>{item.paper_type || '待分类'}</span><span className="numeric">{item.ai_score ?? '-'}</span>
          <span><em className={`status ${item.status}`}>{item.status === 'submitted' ? '已提交' : item.status === 'in_review' ? '草稿' : '待评审'}</em></span>
          <span>开始评审</span>
        </button>)}
        {!filtered.length && <div className="portal-empty">暂无符合条件的评审任务</div>}
      </div>
    </main> : <main className="teacher-review-page">
      <header><button onClick={() => setSelected(null)}><ChevronLeft size={18}/>返回任务</button><div><small>{selected.paper_id}</small><h1>{selected.title}</h1></div><div className="score-comparison"><span>AI 初评 <b>{selected.ai_score ?? '-'}</b></span><span>教师评分 <b>{total}</b></span></div></header>
      <div className="review-workbench">
        <section className="pdf-pane">{pdfUrl ? <iframe title="论文原文" src={pdfUrl}/> : <div>论文 PDF 暂不可用</div>}</section>
        <section className="evaluation-pane">
          <div className="ai-note"><Sparkles size={17}/><div><b>多智能体评审摘要</b><p>{selected.ai_result?.synthesis?.global_review?.overall_summary || 'AI 评审尚未完成或暂无摘要。'}</p></div></div>
          <div className="criteria-head"><div><h2>人工评分</h2><p>已载入系统初评分，每项可由教师复核调整</p></div><strong>{total}<small>/100</small></strong></div>
          <div className="criteria-list">{criteria.map((item, index) => <div className="criterion" key={item.id}><div><b>{item.id}. {item.name}</b><p>{item.description}</p></div><div className="score-options">{[0,1,2,3].map(value => <button className={scores[index] === value ? 'active' : ''} disabled={selected.status === 'submitted'} onClick={() => setScores(scores.map((score, i) => i === index ? value : score))} key={value}>{value}</button>)}</div></div>)}</div>
          <label className="review-text"><span>修改建议</span><textarea disabled={selected.status === 'submitted'} value={advice} onChange={e => setAdvice(e.target.value)} placeholder="填写可执行的修改建议"/></label>
          <label className="review-text"><span>教师备注</span><textarea disabled={selected.status === 'submitted'} value={comments} onChange={e => setComments(e.target.value)} placeholder="填写内部复核说明"/></label>
          {message && <div className="save-message"><Check size={16}/>{message}</div>}
          <div className="review-actions"><button disabled={selected.status === 'submitted'} onClick={() => save(false)}><Save size={17}/>保存草稿</button><button className="primary" disabled={selected.status === 'submitted'} onClick={() => confirm('提交后将锁定评审结果，确认提交吗？') && save(true)}><Send size={17}/>提交终审</button></div>
        </section>
      </div>
    </main>}
  </PortalShell>;
}
