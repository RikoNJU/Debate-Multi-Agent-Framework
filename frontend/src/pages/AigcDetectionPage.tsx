import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileSearch,
  FileText,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import {
  AigcAvailability,
  AigcRiskLevel,
  AigcTaskSnapshot,
  StoredAigcTask,
  createAigcTask,
  deleteAigcTask,
  getAigcAvailability,
  getAigcTask,
  loadAigcTasks,
  retryAigcTask,
} from '../lib/aigcApi';

const ACTIVE = new Set(['queued', 'parsing', 'detecting']);
const stageNames: Record<string, string> = {
  queued: '等待处理',
  parsing: '解析论文结构',
  building_windows: '构建检测文本窗口',
  model_inference: '模型推理',
  completed: '检测完成',
  failed: '检测失败',
  interrupted: '任务已中断',
};

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function riskName(risk: AigcRiskLevel): string {
  return { low: '低风险', medium: '中风险', high: '高风险' }[risk];
}

export default function AigcDetectionPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [availability, setAvailability] = useState<AigcAvailability | null>(null);
  const [storedTasks, setStoredTasks] = useState<StoredAigcTask[]>(loadAigcTasks);
  const [selected, setSelected] = useState<StoredAigcTask | null>(storedTasks[0] || null);
  const [snapshot, setSnapshot] = useState<AigcTaskSnapshot | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getAigcAvailability().then(setAvailability).catch(err => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selected) {
      setSnapshot(null);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await getAigcTask(selected);
        if (cancelled) return;
        setSnapshot(next);
        setError('');
        if (ACTIVE.has(next.status)) timer = window.setTimeout(poll, 1800);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '任务读取失败');
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [selected]);

  const highRiskSegments = useMemo(
    () => [...(snapshot?.result?.segments || [])].sort((a, b) => b.ai_probability - a.ai_probability),
    [snapshot],
  );

  const chooseFile = (candidate?: File) => {
    if (!candidate) return;
    if (candidate.type !== 'application/pdf' && !candidate.name.toLowerCase().endsWith('.pdf')) {
      setError('请选择 PDF 文件');
      return;
    }
    setFile(candidate);
    setError('');
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const created = await createAigcTask(file);
      const tasks = loadAigcTasks();
      setStoredTasks(tasks);
      setSelected(tasks.find(item => item.taskId === created.task_id) || tasks[0]);
      setFile(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败');
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const next = await retryAigcTask(selected);
      setSnapshot(next);
      setSelected({ ...selected });
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '重试失败');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!selected || ACTIVE.has(snapshot?.status || '')) return;
    setBusy(true);
    try {
      await deleteAigcTask(selected);
      const remaining = loadAigcTasks();
      setStoredTasks(remaining);
      setSelected(remaining[0] || null);
      setSnapshot(null);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setBusy(false);
    }
  };

  const usable = Boolean(availability?.ready);

  return (
    <main className="aigc-page">
      <section className="aigc-header">
        <div>
          <span>AUXILIARY SCREENING</span>
          <h1>AIGC 文本检测</h1>
          <p>独立分析论文文本的模型生成风险，不参与论文评审、18 维评分或最终结论。</p>
        </div>
        <div className={`aigc-service ${usable ? 'ready' : 'offline'}`}>
          {usable ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
          <div><b>{usable ? '服务可用' : '服务未启用'}</b><small>{availability?.model_id || '正在读取配置'}</small></div>
        </div>
      </section>

      <section className="aigc-layout">
        <aside className="aigc-side">
          <div className="aigc-upload-title"><FileSearch size={20} /><b>新建检测</b></div>
          <button
            type="button"
            className={`aigc-drop ${dragging ? 'dragging' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={event => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={event => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files[0]); }}
          >
            <input ref={inputRef} type="file" accept="application/pdf,.pdf" onChange={event => chooseFile(event.target.files?.[0])} />
            <UploadCloud size={28} />
            <strong>{file ? file.name : '选择或拖入论文 PDF'}</strong>
            <span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : '使用 MinerU 提取正文与页码定位'}</span>
          </button>
          {file && <button type="button" className="aigc-clear" onClick={() => setFile(null)} title="移除文件"><X size={15} />移除</button>}
          <button className="aigc-primary" type="button" disabled={!file || !usable || busy} onClick={submit}>
            {busy ? <LoaderCircle className="spin" size={18} /> : <ShieldAlert size={18} />}
            开始检测
          </button>
          {!usable && availability && <p className="aigc-unavailable">{availability.message}</p>}

          <div className="aigc-history-head"><b>本浏览器任务</b><span>{storedTasks.length}</span></div>
          <div className="aigc-history">
            {storedTasks.length === 0 && <p>暂无检测记录</p>}
            {storedTasks.map(task => (
              <button key={task.taskId} className={selected?.taskId === task.taskId ? 'active' : ''} onClick={() => setSelected(task)}>
                <FileText size={17} /><div><strong>{task.fileName}</strong><small>{new Date(task.createdAt).toLocaleString()}</small></div><ChevronRight size={15} />
              </button>
            ))}
          </div>
        </aside>

        <div className="aigc-content">
          {selected && snapshot && !ACTIVE.has(snapshot.status) && <button className="aigc-delete" type="button" onClick={remove} disabled={busy} title="删除任务及论文文件"><Trash2 size={15} />删除任务数据</button>}
          {error && <div className="aigc-error"><AlertTriangle size={17} />{error}</div>}
          {!selected && <div className="aigc-empty"><FileSearch size={42} /><h2>等待检测任务</h2><p>上传论文后，可在这里查看章节分布和可追溯的文本窗口。</p></div>}
          {selected && !snapshot && <div className="aigc-empty"><LoaderCircle className="spin" size={38} /><p>正在读取任务...</p></div>}
          {snapshot && ACTIVE.has(snapshot.status) && (
            <div className="aigc-processing">
              <LoaderCircle className="spin" size={35} />
              <div><span>{snapshot.source_filename}</span><h2>{stageNames[snapshot.current_stage] || '正在检测'}</h2></div>
              <b>{snapshot.progress_percent}%</b>
              <div className="aigc-progress"><i style={{ width: `${snapshot.progress_percent}%` }} /></div>
              <p>首次运行需要加载本地模型，耗时会明显长于后续任务。</p>
            </div>
          )}
          {snapshot && (snapshot.status === 'failed' || snapshot.status === 'interrupted') && (
            <div className="aigc-failed"><AlertTriangle size={38} /><h2>{snapshot.status === 'interrupted' ? '任务因服务重启中断' : '检测失败'}</h2><p>{snapshot.error || '未返回错误详情'}</p><button onClick={retry} disabled={busy}><RefreshCw size={16} />重新检测</button></div>
          )}
          {snapshot?.status === 'succeeded' && snapshot.result && (
            <>
              <div className="aigc-result-head">
                <div><span>检测报告</span><h2>{snapshot.source_filename}</h2><p><Clock3 size={13} />完成于 {new Date(snapshot.completed_at || snapshot.updated_at).toLocaleString()}</p></div>
                <div className="aigc-score"><strong>{percent(snapshot.result.average_risk_score)}</strong><span>平均风险值</span></div>
              </div>
              <div className="aigc-disclaimer"><AlertTriangle size={18} /><p><b>辅助筛查，不是鉴定结论</b>{snapshot.result.disclaimer} 当前模型输出未经本项目数据校准。</p></div>
              <div className="aigc-metrics">
                <div><span>文本窗口</span><strong>{snapshot.result.segment_count}</strong><small>{snapshot.result.token_count.toLocaleString()} tokens</small></div>
                <div><span>中风险文本占比</span><strong className="medium">{percent(snapshot.result.medium_risk_ratio)}</strong><small>按 token 加权</small></div>
                <div><span>高风险文本占比</span><strong className="high">{percent(snapshot.result.high_risk_ratio)}</strong><small>按 token 加权</small></div>
              </div>

              <section className="aigc-section">
                <div className="aigc-section-title"><div><b>章节分布</b><span>模型概率按有效 token 加权汇总</span></div></div>
                <div className="aigc-table-wrap"><table><thead><tr><th>章节</th><th>窗口</th><th>平均风险</th><th>中风险占比</th><th>高风险占比</th></tr></thead><tbody>
                  {snapshot.result.chapters.map((chapter, index) => <tr key={`${chapter.chapter_id}-${index}`}><td>{chapter.chapter_name}</td><td>{chapter.segment_count}</td><td>{percent(chapter.average_risk_score)}</td><td>{percent(chapter.medium_risk_ratio)}</td><td>{percent(chapter.high_risk_ratio)}</td></tr>)}
                </tbody></table></div>
              </section>

              <section className="aigc-section">
                <div className="aigc-section-title"><div><b>文本窗口</b><span>按风险值降序，仅展示文本预览与 MinerU 定位信息</span></div></div>
                <div className="aigc-segments">
                  {highRiskSegments.map(segment => <article key={segment.segment_id}>
                    <div className="aigc-segment-top"><span className={`aigc-risk ${segment.risk_level}`}>{riskName(segment.risk_level)}</span><b>{percent(segment.ai_probability)}</b><small>{segment.chapter_name}</small></div>
                    <p>{segment.content_preview}</p>
                    <footer><code>{segment.segment_id}</code><span>{segment.token_count} tokens</span><span>{segment.locators.map(item => item.page_number ? `第 ${item.page_number} 页` : '').filter(Boolean).join('、') || '页码未知'}</span></footer>
                  </article>)}
                </div>
              </section>
            </>
          )}
        </div>
      </section>
    </main>
  );
}
