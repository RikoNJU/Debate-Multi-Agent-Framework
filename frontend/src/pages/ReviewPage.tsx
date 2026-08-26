import { ChangeEvent, DragEvent, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Clock3, CircleAlert, FileText, FolderOpen, LoaderCircle, Plus, Search, UploadCloud, X } from 'lucide-react';

import {
  createReviewTask,
  getRunSnapshot,
  TASK_STORAGE_KEY,
  toTaskStatus,
  type TaskRecord,
} from '../lib/reviewApi';

const initialTasks: TaskRecord[] = [];

/** 上传中的草稿保留 30 分钟，期间在页面间跳转不会丢失；超时视为中断残留。 */
const DRAFT_TTL_MS = 30 * 60 * 1000;

export function isDraftTask(task: TaskRecord): boolean {
  return task.id.startsWith('local-');
}

function readStoredTasks(): TaskRecord[] {
  try {
    const raw = localStorage.getItem(TASK_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as TaskRecord[]) : initialTasks;
    const now = Date.now();
    return parsed.filter(task => {
      if (!isDraftTask(task)) return true;
      const created = Date.parse(task.createdAt);
      return Number.isFinite(created) && now - created < DRAFT_TTL_MS;
    });
  } catch {
    return initialTasks;
  }
}

/** 每次都基于最新的 localStorage 计算并同步落盘，避免并发上传流相互覆盖。 */
function applyTaskList(
  tasksRef: { current: TaskRecord[] },
  setTasks: (list: TaskRecord[]) => void,
  updater: (list: TaskRecord[]) => TaskRecord[],
): void {
  const next = updater(readStoredTasks());
  tasksRef.current = next;
  setTasks(persistTasks(next));
}

/** 立即写入 localStorage，避免组件卸载时持久化 effect 未执行。 */
function persistTasks(list: TaskRecord[]): TaskRecord[] {
  try {
    localStorage.setItem(TASK_STORAGE_KEY, JSON.stringify(list));
  } catch {
    // 存储不可用时保留内存态即可
  }
  return list;
}

function formatTaskTime(value: string, now: number): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value || '时间未知';
  const elapsedSeconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (elapsedSeconds < 60) return '刚刚';
  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) return `${elapsedMinutes} 分钟前`;
  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) return `${elapsedHours} 小时前`;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp));
}

export default function ReviewPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>(readStoredTasks);
  const tasksRef = useRef<TaskRecord[]>(tasks);
  const [clock, setClock] = useState(Date.now());
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ completed: 0, total: 0 });
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [errorText, setErrorText] = useState<string | null>(null);

  useEffect(() => {
    tasksRef.current = tasks;
    localStorage.setItem(TASK_STORAGE_KEY, JSON.stringify(tasks));
  }, [tasks]);

  useEffect(() => {
    let active = true;
    let syncing = false;

    const syncTasks = async (includeFinished: boolean) => {
      if (syncing) return;
      const candidates = tasksRef.current.filter(task =>
        !task.id.startsWith('local-') && (includeFinished || task.status === 'processing'),
      );
      if (!candidates.length) return;
      syncing = true;
      const snapshots = await Promise.all(candidates.map(async task => {
        try {
          return await getRunSnapshot(task.id);
        } catch {
          // A missing or expired access code must not erase the local task receipt.
          return null;
        }
      }));
      syncing = false;
      if (!active) return;
      const snapshotById = new Map(
        snapshots.filter(snapshot => snapshot !== null).map(snapshot => [snapshot.task_id, snapshot]),
      );
      if (!snapshotById.size) return;
      setTasks(previous => persistTasks(previous.map(task => {
        const snapshot = snapshotById.get(task.id);
        if (!snapshot) return task;
        return {
          ...task,
          status: toTaskStatus(snapshot.status),
          createdAt: snapshot.created_at || task.createdAt,
          paperId: snapshot.paper_id || task.paperId,
          title: snapshot.paper_title || snapshot.result?.context?.profile?.title || task.title,
        };
      })));
    };

    void syncTasks(true);
    const statusTimer = window.setInterval(() => void syncTasks(false), 5000);
    const clockTimer = window.setInterval(() => setClock(Date.now()), 30000);
    return () => {
      active = false;
      window.clearInterval(statusTimer);
      window.clearInterval(clockTimer);
    };
  }, []);

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
      createdAt: new Date().toISOString(),
    }));

    setSubmitting(true);
    setErrorText(null);
    setBatchMessage(null);
    setBatchProgress({ completed: 0, total: selectedFiles.length });
    // 同步写入 localStorage，确保上传期间刷新/跳转页面后草稿不丢失
    applyTaskList(tasksRef, setTasks, stored => [...drafts, ...stored]);

    let succeeded = 0;
    const failures: string[] = [];
    for (const [index, file] of selectedFiles.entries()) {
      const draft = drafts[index];
      setBatchProgress({ completed: index + 1, total: selectedFiles.length });
      try {
        const submission = await createReviewTask(file, draft.title, draft.id);
        applyTaskList(tasksRef, setTasks, stored => stored.map(task =>
          task.id === draft.id
            ? {
              ...task,
              id: submission.task_id,
              title: submission.title || task.title,
              status: submission.status === 'succeeded' ? 'completed' : 'processing',
              paperId: submission.paper_id,
            }
            : task,
        ));
        succeeded += 1;
      } catch (error) {
        console.error('创建评审任务失败', error);
        failures.push(`${file.name}：${error instanceof Error ? error.message : '创建失败'}`);
        applyTaskList(tasksRef, setTasks, stored => stored.filter(task => task.id !== draft.id));
      }
    }

    setSubmitting(false);
    setFiles([]);
    setBatchMessage(`已提交 ${succeeded}/${selectedFiles.length} 篇论文`);
    setErrorText(failures.length ? failures.join('；') : null);
    if (inputRef.current) inputRef.current.value = '';
  };

  const filtered = tasks.filter((task) =>
    `${task.title} ${task.fileName}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="workspace">

      <main className="desk-layout">
        <section className="paper-pane">
          <div className="pane-heading">
            <div>
              <h1>创建论文评审</h1>
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

          <label className="task-search">
            <Search size={17} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索论文或任务" />
          </label>

          <div className="task-list">
            {filtered.map((task) => (
              <button
                className="task-card"
                key={task.id}
                onClick={() => {
                  // 本地草稿还没有后端任务编号，点击不跳转，避免详情页 404
                  if (isDraftTask(task)) return;
                  navigate(`/student/tasks/${task.id}`);
                }}
              >
                <div className="task-file"><FileText size={19} /></div>
                <div>
                  <strong>{task.title}</strong>
                  <span>{task.fileName}</span>
                  <small>
                    <Clock3 size={12} />
                    {formatTaskTime(task.createdAt, clock)}
                  </small>
                </div>
                <div className={`pill ${task.status}`}>
                  {isDraftTask(task)
                    ? '解析中'
                    : task.status === 'processing' ? '评审中' : task.status === 'failed' ? '失败' : '已完成'}
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
