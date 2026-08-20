import { ChangeEvent, DragEvent, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Clock3, CircleAlert, FileText, FolderOpen, LoaderCircle, Plus, Search, UploadCloud, X } from 'lucide-react';

import {
  createReviewTask,
  rememberTaskAccess,
  TASK_STORAGE_KEY,
  type TaskRecord,
} from '../lib/reviewApi';

const initialTasks: TaskRecord[] = [];

function readStoredTasks(): TaskRecord[] {
  try {
    const raw = localStorage.getItem(TASK_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as TaskRecord[]) : initialTasks;
  } catch {
    return initialTasks;
  }
}

export default function ReviewPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>(readStoredTasks);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ completed: 0, total: 0 });
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [errorText, setErrorText] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem(TASK_STORAGE_KEY, JSON.stringify(tasks));
  }, [tasks]);

  const choose = (candidates: File[]) => {
    if (!candidates.length || submitting) return;
    const accepted = candidates.filter(
      candidate => candidate.type === 'application/pdf' || candidate.name.toLowerCase().endsWith('.pdf'),
    ).filter(candidate => candidate.size <= 20 * 1024 * 1024);
    if (accepted.length !== candidates.length) {
      setErrorText('已忽略非 PDF 或超过 20MB 的文件');
    } else {
      setErrorText(null);
    }
    setBatchMessage(null);
    setFiles(previous => {
      const known = new Set(previous.map(item => `${item.name}:${item.size}:${item.lastModified}`));
      return [...previous, ...accepted.filter(item => !known.has(`${item.name}:${item.size}:${item.lastModified}`))];
    });
  };

  const onInput = (event: ChangeEvent<HTMLInputElement>) => {
    choose(Array.from(event.target.files || []));
    event.target.value = '';
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    choose(Array.from(event.dataTransfer.files || []));
  };

  const startReview = async () => {
    if (!files.length || submitting) return;
    const selectedFiles = [...files];
    const drafts = selectedFiles.map((file, index): TaskRecord => ({
      id: `local-${Date.now()}-${index}`,
      title: file.name.replace(/\.[^.]+$/, '') || '未命名论文',
      fileName: file.name,
      status: 'processing',
      createdAt: '刚刚',
    }));

    setSubmitting(true);
    setErrorText(null);
    setBatchMessage(null);
    setBatchProgress({ completed: 0, total: selectedFiles.length });
    setTasks(previous => [...drafts, ...previous]);

    let succeeded = 0;
    let singleTaskId: string | null = null;
    const failures: string[] = [];
    for (const [index, file] of selectedFiles.entries()) {
      const draft = drafts[index];
      setBatchProgress({ completed: index + 1, total: selectedFiles.length });
      try {
        const submission = await createReviewTask(file, draft.title, draft.id);
        const finalTask: TaskRecord = {
          ...draft,
          id: submission.task_id,
          title: submission.title || draft.title,
          status: submission.status === 'succeeded' ? 'completed' : 'processing',
          paperId: submission.paper_id,
          accessToken: submission.access_token,
        };
        rememberTaskAccess(submission.task_id, submission.access_token);
        setTasks(previous => previous.map(task => task.id === draft.id ? finalTask : task));
        succeeded += 1;
        singleTaskId = submission.task_id;
      } catch (error) {
        console.error('创建评审任务失败', error);
        failures.push(`${file.name}：${error instanceof Error ? error.message : '创建失败'}`);
        setTasks(previous => previous.map(task => task.id === draft.id ? { ...task, status: 'failed' } : task));
      }
    }

    setSubmitting(false);
    setFiles([]);
    setBatchMessage(`已提交 ${succeeded}/${selectedFiles.length} 篇论文`);
    setErrorText(failures.length ? failures.join('；') : null);
    if (inputRef.current) inputRef.current.value = '';
    if (selectedFiles.length === 1 && singleTaskId && !failures.length) {
      navigate(`/student/tasks/${singleTaskId}`);
    }
  };

  const filtered = tasks.filter((task) =>
    `${task.title} ${task.fileName}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="workspace">
      <header className="topbar">
        <div className="brand-mark"><span>RW</span></div>
        <div>
          <strong>睿文智评</strong>
          <small>Academic Review Workspace</small>
        </div>
        <div className="topbar-right">
          <span className="status-dot" /> 系统运行正常
          <Link className="role-link" to="/workspace">工作人员端</Link>
        </div>
      </header>

      <main className="desk-layout">
        <section className="paper-pane">
          <div className="pane-heading">
            <div>
              <span className="eyebrow">NEW REVIEW</span>
              <h1>创建论文评审</h1>
              <p>上传论文后，三位专业评审员将独立分析并进行证据辩论。</p>
            </div>
            <div className="paper-icon"><FileText size={26} /></div>
          </div>

          <div className="process-line">
            <span className="active">1</span>
            <i />
            <span>2</span>
            <i />
            <span>3</span>
            <div>
              <b>上传论文</b>
              <b>多智能体评审</b>
              <b>查看报告</b>
            </div>
          </div>

          <div
            className={`upload-zone ${dragging ? 'dragging' : ''} ${files.length ? 'has-file batch-files' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <input ref={inputRef} type="file" accept=".pdf,application/pdf" multiple disabled={submitting} onChange={onInput} />
            {files.length ? (
              <>
                <div className="batch-file-head"><strong>已选择 {files.length} 篇论文</strong><span>点击空白处可继续添加</span></div>
                <div className="batch-file-list">{files.map((file, index) => <div className="file-ready" key={`${file.name}-${file.lastModified}`}><FileText/><div><strong>{file.name}</strong><span>{Math.max(1, Math.round(file.size / 1024))} KB</span></div><button disabled={submitting} title="移除文件" onClick={event => { event.stopPropagation(); setFiles(current => current.filter((_, itemIndex) => itemIndex !== index)); }}><X size={16}/></button></div>)}</div>
              </>
            ) : (
              <>
                <div className="upload-round"><UploadCloud size={31} /></div>
                <strong>拖拽多篇论文至此处，或点击批量上传</strong>
                <span>支持多选 PDF，每个文件不超过 20MB</span>
                <em>选择论文文件</em>
              </>
            )}
          </div>

          <div className="review-brief">
            <span>评审维度</span>
            <div>
              <b>科学严谨性</b>
              <b>实证证据</b>
              <b>全局质量</b>
            </div>
            <p>系统将保留每一项结论的讨论过程与外部证据来源。</p>
          </div>

          {errorText && (
            <div className="upload-error">
              <CircleAlert size={15} />
              <span>{errorText}</span>
            </div>
          )}
          {batchMessage && <div className="upload-success"><span>{batchMessage}</span></div>}

          <button className="primary-button" disabled={!files.length || submitting} onClick={startReview}>
            {submitting ? <LoaderCircle className="spin" /> : <Plus />}
            {submitting ? `正在提交 ${batchProgress.completed}/${batchProgress.total}` : files.length > 1 ? `批量开始评审（${files.length}）` : '开始智能评审'}
            <ArrowRight size={18} />
          </button>
        </section>

        <aside className="task-pane">
          <div className="task-head">
            <div>
              <span className="eyebrow">REVIEW TASKS</span>
              <h2>评审任务</h2>
            </div>
            <span className="task-count">{tasks.length}</span>
          </div>
          <Link className="recover-link" to="/student/recover">使用任务编号和访问码找回</Link>

          <label className="task-search">
            <Search size={17} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索论文或任务" />
          </label>

          <div className="task-list">
            {filtered.map((task) => (
              <button className="task-card" key={task.id} onClick={() => navigate(`/student/tasks/${task.id}`)}>
                <div className="task-file"><FileText size={19} /></div>
                <div>
                  <strong>{task.title}</strong>
                  <span>{task.fileName}</span>
                  <small>
                    <Clock3 size={12} />
                    {task.createdAt}
                  </small>
                </div>
                <div className={`pill ${task.status}`}>
                  {task.status === 'processing' ? '评审中' : task.status === 'failed' ? '失败' : '已完成'}
                </div>
                <ArrowRight className="task-arrow" size={17} />
              </button>
            ))}
          </div>

          {!filtered.length && (
            <div className="empty">
              <FolderOpen />
              <p>没有找到相关任务</p>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}
