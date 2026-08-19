export type TaskStatus = 'processing' | 'completed' | 'failed';

export type TaskRecord = {
  id: string;
  title: string;
  fileName: string;
  status: TaskStatus;
  createdAt: string;
  paperId?: string;
  accessToken?: string;
};

export type ReviewSubmission = {
  task_id: string;
  status: string;
  paper_id: string;
  title: string;
  chapter_count: number;
  batch_id: string;
  access_token: string;
};

const ACCESS_KEY = 'debate-student-task-access';

function readAccessMap(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(ACCESS_KEY) || '{}');
  } catch {
    return {};
  }
}

export function rememberTaskAccess(taskId: string, accessToken: string): void {
  localStorage.setItem(
    ACCESS_KEY,
    JSON.stringify({ ...readAccessMap(), [taskId]: accessToken }),
  );
}

export function getTaskAccess(taskId: string): string | undefined {
  return readAccessMap()[taskId];
}

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

export async function getRunSnapshot(taskId: string, accessToken?: string) {
  const token = accessToken || getTaskAccess(taskId);
  if (!token) throw new Error('当前浏览器没有该任务的访问码，请先找回任务');
  const response = await fetch(`/api/debate/runs/${encodeURIComponent(taskId)}`, {
    headers: { 'X-Submission-Token': token },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail || '任务详情获取失败');
  }

  return response.json();
}

export async function recoverTask(taskId: string, accessToken: string) {
  const snapshot = await getRunSnapshot(taskId, accessToken);
  rememberTaskAccess(taskId, accessToken);
  return snapshot;
}
