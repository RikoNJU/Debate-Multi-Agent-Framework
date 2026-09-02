export type TaskStatus = 'processing' | 'completed' | 'failed';

export type TaskRecord = {
  id: string;
  title: string;
  fileName: string;
  status: TaskStatus;
  createdAt: string;
  paperId?: string;
};

export type ReviewSubmission = {
  task_id: string;
  status: string;
  paper_id: string;
  title: string;
  chapter_count: number;
  batch_id: string;
  revision_id?: string | null;
  reused: boolean;
  change_ratio?: number | null;
  changed_chapter_ids: string[];
};

export type RunSnapshot = {
  task_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted';
  created_at: string;
  updated_at: string;
  result?: Record<string, any> | null;
  error?: string | null;
  paper_id?: string | null;
  paper_title?: string | null;
};

export const TASK_STORAGE_KEY = 'debate-review-tasks';

export async function createReviewTask(file: File, title?: string, paperId?: string): Promise<ReviewSubmission> {
  const formData = new FormData();
  formData.append('pdf', file);
  if (title) {
    formData.append('title', title);
  }
  if (paperId) {
    formData.append('paper_id', paperId);
  }

  const response = await fetch('/api/debate/papers/review', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    let message = '创建评审任务失败';
    try {
      const payload = await response.json();
      message = payload?.detail || message;
    } catch {
      // ignore parse errors and keep the fallback message
    }
    throw new Error(message);
  }

  return response.json();
}

export function toTaskStatus(status: RunSnapshot['status'] | string): TaskStatus {
  if (status === 'succeeded') return 'completed';
  if (status === 'failed' || status === 'interrupted') return 'failed';
  return 'processing';
}

/** 把任务写回本地列表：已存在则原位更新，不存在（如换浏览器后直接打开详情页）则补录。 */
export function upsertTaskRecord(taskId: string, snapshot: RunSnapshot): void {
  try {
    const raw = localStorage.getItem(TASK_STORAGE_KEY);
    const current: TaskRecord[] = raw ? JSON.parse(raw) : [];
    const status = toTaskStatus(snapshot.status);
    const realTitle = snapshot.paper_title
      || snapshot.result?.context?.profile?.title || '';
    const existing = current.find(task => task.id === taskId);
    if (existing) {
      localStorage.setItem(TASK_STORAGE_KEY, JSON.stringify(current.map(task =>
        task.id === taskId
          ? {
              ...task,
              status,
              createdAt: snapshot.created_at || task.createdAt,
              paperId: snapshot.paper_id || task.paperId,
              title: realTitle || task.title,
            }
          : task,
      )));
      return;
    }
    const record: TaskRecord = {
      id: taskId,
      title: realTitle || '论文评审任务',
      fileName: '论文文件',
      status,
      createdAt: snapshot.created_at || new Date().toISOString(),
      paperId: snapshot.paper_id || undefined,
    };
    localStorage.setItem(TASK_STORAGE_KEY, JSON.stringify([record, ...current]));
  } catch {
    // 本地存储不可用时任务仍可通过 URL 访问
  }
}

export async function getRunSnapshot(taskId: string): Promise<RunSnapshot> {
  const response = await fetch(`/api/debate/runs/${encodeURIComponent(taskId)}`);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail || '任务详情获取失败');
  }

  return response.json() as Promise<RunSnapshot>;
}

export async function listRecentRuns(limit = 100): Promise<RunSnapshot[]> {
  const response = await fetch(`/api/debate/runs?limit=${limit}`);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail || '任务列表获取失败');
  }
  return response.json() as Promise<RunSnapshot[]>;
}

export async function retryReviewTask(taskId: string): Promise<ReviewSubmission> {
  const response = await fetch(
    `/api/debate/runs/${encodeURIComponent(taskId)}/retry`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail || '重新评审失败');
  }
  return response.json();
}

export async function downloadReviewTable(taskId: string): Promise<void> {
  const response = await fetch(
    `/api/debate/student/tasks/${encodeURIComponent(taskId)}/review-table`,
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail || '导出 18 维评审表失败');
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] || '18维评审表.pdf';
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function replaceRetriedTask(
  previousTaskId: string,
  submission: ReviewSubmission,
): void {
  try {
    const tasks = JSON.parse(
      localStorage.getItem(TASK_STORAGE_KEY) || '[]',
    ) as TaskRecord[];
    // 重试复用同一任务编号（断点续跑），原位更新状态即可
    localStorage.setItem(
      TASK_STORAGE_KEY,
      JSON.stringify(tasks.map(task => task.id === submission.task_id ? {
        ...task,
        status: 'processing',
      } : task)),
    );
  } catch {
    // The new task remains accessible by URL even if the local task list is damaged.
  }
}
