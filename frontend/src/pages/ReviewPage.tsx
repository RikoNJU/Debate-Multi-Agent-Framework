import { ChangeEvent, DragEvent, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Clock3, CircleAlert, FileText, FolderOpen, LoaderCircle, Plus, Search, UploadCloud } from 'lucide-react';

import { createReviewTask, type TaskRecord } from '../lib/reviewApi';

const STORAGE_KEY = 'debate-review-tasks';

const initialTasks: TaskRecord[] = [
  {
    id: 'demo-review-001',
    title: '面向大语言模型的多智能体论文评审框架研究',
    fileName: 'multi_agent_review.pdf',
    status: 'completed',
    createdAt: '今天 10:24',
  },
  {
    id: 'demo-review-002',
    title: '深度学习在医学图像分割中的应用研究',
    fileName: 'medical_image.pdf',
    status: 'completed',
    createdAt: '昨天 16:42',
  },
];

function readStoredTasks(): TaskRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as TaskRecord[]) : initialTasks;
  } catch {
    return initialTasks;
  }
}

export default function ReviewPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>(readStoredTasks);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [search, setSearch] = useState('');
  const [errorText, setErrorText] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  }, [tasks]);

  const choose = (candidate?: File) => {
    if (!candidate) return;
    setFile(candidate);
  };

  const onInput = (event: ChangeEvent<HTMLInputElement>) => {
    choose(event.target.files?.[0]);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files?.[0]);
  };

  const startReview = async () => {
    if (!file || submitting) return;

    const draft: TaskRecord = {
      id: `local-${Date.now()}`,
      title: file.name.replace(/\.[^.]+$/, '') || '未命名论文',
      fileName: file.name,
      status: 'processing',
      createdAt: '刚刚',
    };

    setSubmitting(true);
    setErrorText(null);
    setTasks((previous) => [draft, ...previous]);

    try {
      const submission = await createReviewTask(file, draft.title, draft.id);
      const finalTask: TaskRecord = {
        ...draft,
        id: submission.task_id,
        title: submission.title || draft.title,
        status: submission.status === 'succeeded' ? 'completed' : 'processing',
        paperId: submission.paper_id,
      };

      setTasks((previous) => previous.map((task) => (task.id === draft.id ? finalTask : task)));
      navigate(`/tasks/${submission.task_id}`);
    } catch (error) {
      console.error('创建评审任务失败', error);
      setErrorText(error instanceof Error ? error.message : '创建评审任务失败，请稍后重试');
      setTasks((previous) =>
        previous.map((task) =>
          task.id === draft.id ? { ...task, status: 'failed' } : task,
        ),
      );
    } finally {
      setSubmitting(false);
      setFile(null);
      if (inputRef.current) {
        inputRef.current.value = '';
      }
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
          <Link className="avatar" title="教师与教务工作台" to="/login">A</Link>
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
            className={`upload-zone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <input ref={inputRef} type="file" accept=".pdf" onChange={onInput} />
            {file ? (
              <>
                <div className="file-ready">
                  <FileText />
                  <div>
                    <strong>{file.name}</strong>
                    <span>{Math.max(1, Math.round(file.size / 1024))} KB · 已准备就绪</span>
                  </div>
                </div>
                <button onClick={(event) => { event.stopPropagation(); setFile(null); }}>重新选择</button>
              </>
            ) : (
              <>
                <div className="upload-round"><UploadCloud size={31} /></div>
                <strong>拖拽论文至此处，或点击上传</strong>
                <span>支持 PDF 格式，文件大小不超过 20MB</span>
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

          <button className="primary-button" disabled={!file || submitting} onClick={startReview}>
            {submitting ? <LoaderCircle className="spin" /> : <Plus />}
            {submitting ? '正在创建评审任务…' : '开始智能评审'}
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

          <label className="task-search">
            <Search size={17} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索论文或任务" />
          </label>

          <div className="task-list">
            {filtered.map((task) => (
              <button className="task-card" key={task.id} onClick={() => navigate(`/tasks/${task.id}`)}>
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
