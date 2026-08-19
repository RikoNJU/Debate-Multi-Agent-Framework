const API_ROOT = '/api/debate/portal';
const TOKEN_KEY = 'debate-portal-token';
export const PORTAL_AUTH_INVALID_EVENT = 'debate-portal-auth-invalid';

export type PortalUser = {
  id: string;
  username: string;
  display_name: string;
  role: 'teacher' | 'admin';
  is_active: boolean;
  created_at: string;
};

export type Criterion = {
  id: number;
  name: string;
  category: 'format' | 'content';
  description: string;
};

export type HumanReview = {
  review_id: string;
  assignment_id: string;
  status: 'draft' | 'submitted';
  section_scores: number[];
  total_score: number;
  advice_content: string;
  teacher_comments: string;
  updated_at: string;
  submitted_at?: string;
  published_at?: string;
};

export type Assignment = {
  assignment_id: string;
  paper_id: string;
  title: string;
  paper_type?: string;
  source_filename: string;
  reviewer_id: string;
  reviewer_name: string;
  status: 'assigned' | 'in_review' | 'submitted';
  ai_task_id?: string;
  ai_status?: string;
  ai_score?: number;
  ai_result?: any;
  human_review?: HumanReview;
  created_at: string;
  updated_at: string;
};

export type AdminPaper = {
  paper_id: string;
  title: string;
  paper_type?: string;
  source_filename: string;
  updated_at: string;
  run_status?: string;
  ai_score?: number;
  assignments: Assignment[];
};

export type PortalStatistics = {
  total_papers: number;
  total_assignments: number;
  submitted_reviews: number;
  pending_assignments: number;
  average_human_score: number;
  score_distribution: Record<string, number>;
};

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      clearToken();
      window.dispatchEvent(new Event(PORTAL_AUTH_INVALID_EVENT));
    }
    throw new Error(payload.detail || '请求失败');
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export async function login(username: string, password: string) {
  const result = await request<{ access_token: string; user: PortalUser }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  localStorage.setItem(TOKEN_KEY, result.access_token);
  return result.user;
}

export const portalApi = {
  me: () => request<PortalUser>('/auth/me'),
  logout: async () => {
    try { await request<void>('/auth/logout', { method: 'POST' }); } finally { clearToken(); }
  },
  criteria: () => request<Criterion[]>('/teacher/criteria'),
  assignments: () => request<Assignment[]>('/teacher/assignments'),
  assignment: (id: string) => request<Assignment>(`/teacher/assignments/${encodeURIComponent(id)}`),
  pdfUrl: (id: string) => `${API_ROOT}/teacher/assignments/${encodeURIComponent(id)}/pdf`,
  saveReview: (id: string, body: object) => request<HumanReview>(`/teacher/assignments/${encodeURIComponent(id)}/review`, { method: 'PUT', body: JSON.stringify(body) }),
  submitReview: (id: string, body: object) => request<HumanReview>(`/teacher/assignments/${encodeURIComponent(id)}/review/submit`, { method: 'POST', body: JSON.stringify(body) }),
  statistics: () => request<PortalStatistics>('/admin/statistics'),
  papers: () => request<AdminPaper[]>('/admin/papers'),
  users: () => request<PortalUser[]>('/admin/users'),
  createUser: (body: object) => request<PortalUser>('/admin/users', { method: 'POST', body: JSON.stringify(body) }),
  assign: (paperId: string, reviewerId: string) => request<Assignment>('/admin/assignments', { method: 'POST', body: JSON.stringify({ paper_id: paperId, reviewer_id: reviewerId }) }),
  publishReview: (reviewId: string) => request<HumanReview>(`/admin/reviews/${encodeURIComponent(reviewId)}/publish`, { method: 'POST' }),
  exportUrl: () => `${API_ROOT}/admin/exports/reviews.csv`,
};
