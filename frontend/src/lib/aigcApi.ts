export type AigcTaskStatus =
  | 'queued'
  | 'parsing'
  | 'detecting'
  | 'succeeded'
  | 'failed'
  | 'interrupted';

export type AigcRiskLevel = 'low' | 'medium' | 'high';

export type AigcLocator = {
  block_id: string;
  page_number?: number | null;
  bbox?: Record<string, number> | null;
};

export type AigcSegmentResult = {
  segment_id: string;
  chapter_id?: string | null;
  chapter_name: string;
  content_preview: string;
  text_sha256: string;
  token_count: number;
  ai_probability: number;
  risk_level: AigcRiskLevel;
  locators: AigcLocator[];
};

export type AigcChapterSummary = {
  chapter_id?: string | null;
  chapter_name: string;
  segment_count: number;
  token_count: number;
  average_risk_score: number;
  medium_risk_ratio: number;
  high_risk_ratio: number;
};

export type AigcDetectionResult = {
  model_id: string;
  model_revision?: string | null;
  preprocessing_version: string;
  calibrated: boolean;
  disclaimer: string;
  segment_count: number;
  token_count: number;
  average_risk_score: number;
  medium_risk_ratio: number;
  high_risk_ratio: number;
  chapters: AigcChapterSummary[];
  segments: AigcSegmentResult[];
};

export type AigcTaskSnapshot = {
  task_id: string;
  source_filename: string;
  status: AigcTaskStatus;
  current_stage: string;
  progress_percent: number;
  result?: AigcDetectionResult | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
};

export type AigcAvailability = {
  enabled: boolean;
  ready: boolean;
  model_id: string;
  dependencies_installed: boolean;
  mineru_configured: boolean;
  message: string;
};

type AigcTaskCreated = {
  task_id: string;
  access_code: string;
  status: AigcTaskStatus;
  created_at: string;
};

export type StoredAigcTask = {
  taskId: string;
  accessCode: string;
  fileName: string;
  createdAt: string;
};

const STORAGE_KEY = 'debate-aigc-tasks-v1';

async function responseError(response: Response, fallback: string): Promise<Error> {
  const payload = await response.json().catch(() => ({}));
  return new Error(payload?.detail || fallback);
}

export async function getAigcAvailability(): Promise<AigcAvailability> {
  const response = await fetch('/api/debate/aigc/availability');
  if (!response.ok) throw await responseError(response, '无法读取 AIGC 检测服务状态');
  return response.json();
}

export async function createAigcTask(file: File): Promise<AigcTaskCreated> {
  const body = new FormData();
  body.append('pdf', file);
  const response = await fetch('/api/debate/aigc/tasks', { method: 'POST', body });
  if (!response.ok) throw await responseError(response, '创建 AIGC 检测任务失败');
  const created = await response.json() as AigcTaskCreated;
  rememberAigcTask({
    taskId: created.task_id,
    accessCode: created.access_code,
    fileName: file.name,
    createdAt: created.created_at,
  });
  return created;
}

export async function getAigcTask(task: StoredAigcTask): Promise<AigcTaskSnapshot> {
  const response = await fetch(`/api/debate/aigc/tasks/${encodeURIComponent(task.taskId)}`, {
    headers: { 'X-AIGC-Access-Code': task.accessCode },
  });
  if (!response.ok) throw await responseError(response, '读取 AIGC 检测任务失败');
  return response.json();
}

export async function retryAigcTask(task: StoredAigcTask): Promise<AigcTaskSnapshot> {
  const response = await fetch(
    `/api/debate/aigc/tasks/${encodeURIComponent(task.taskId)}/retry`,
    { method: 'POST', headers: { 'X-AIGC-Access-Code': task.accessCode } },
  );
  if (!response.ok) throw await responseError(response, '重试 AIGC 检测任务失败');
  return response.json();
}

export async function deleteAigcTask(task: StoredAigcTask): Promise<void> {
  const response = await fetch(`/api/debate/aigc/tasks/${encodeURIComponent(task.taskId)}`, {
    method: 'DELETE',
    headers: { 'X-AIGC-Access-Code': task.accessCode },
  });
  if (!response.ok) throw await responseError(response, '删除 AIGC 检测任务失败');
  forgetAigcTask(task.taskId);
}

export function loadAigcTasks(): StoredAigcTask[] {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(value) ? value.slice(0, 20) : [];
  } catch {
    return [];
  }
}

function rememberAigcTask(task: StoredAigcTask): void {
  const tasks = loadAigcTasks().filter(item => item.taskId !== task.taskId);
  localStorage.setItem(STORAGE_KEY, JSON.stringify([task, ...tasks].slice(0, 20)));
}

export function forgetAigcTask(taskId: string): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify(loadAigcTasks().filter(item => item.taskId !== taskId)),
  );
}
