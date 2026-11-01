import type {
  ConversationDetail,
  ConversationItem,
  DocumentItem,
  HealthInfo,
  LoginResponse,
  NoticeCategoryOption,
  NoticeItem,
  UploadResult,
  UserInfo,
} from '../types'
import { clearSession, getToken } from '../utils/session'

const BASE = (import.meta as any).env?.VITE_API_BASE ?? ''

function withAuth(headersInit?: HeadersInit): Headers {
  const headers = new Headers(headersInit)
  const token = getToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  return headers
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, { ...init, headers: withAuth(init?.headers) })

  if (response.status === 401 && !path.includes('/auth/login')) {
    clearSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }

  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json()
      message = data.detail || data.message || message
    } catch {
      /* ignore */
    }
    throw new Error(message)
  }
  // 204 没有响应体（删除类接口），直接返回，避免 json() 解析空串报错
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function fetchHealth(): Promise<HealthInfo> {
  return request<HealthInfo>('/health')
}

// ---------------- 登录 / 鉴权 ----------------
export function login(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function fetchMe(): Promise<UserInfo> {
  return request<UserInfo>('/api/v1/auth/me')
}

export function register(
  username: string,
  password: string,
  nickname?: string,
): Promise<LoginResponse> {
  return request<LoginResponse>('/api/v1/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, nickname: nickname || undefined }),
  })
}

export function logout(): Promise<{ logged_out: boolean }> {
  return request('/api/v1/auth/logout', { method: 'POST' })
}

// ---------------- 会话 ----------------
export function fetchConversations(limit = 30): Promise<ConversationItem[]> {
  return request<ConversationItem[]>(`/api/v1/conversations?limit=${limit}`)
}

export function fetchConversation(id: number): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/v1/conversations/${id}`)
}

export function sendFeedback(messageId: number, rating: number, comment?: string): Promise<any> {
  return request(`/api/v1/messages/${messageId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating, comment }),
  })
}

// ---------------- 工单 ----------------
export function confirmTicket(payload: {
  title: string
  description: string
  category: string
}): Promise<{ ticket_no: string; status: string; reused: boolean }> {
  return request('/api/v1/tickets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

// ---------------- 校园公告 ----------------
export interface NoticeQuery {
  category?: string
  keyword?: string
  include_expired?: boolean
  limit?: number
}

export function fetchNotices(query: NoticeQuery = {}): Promise<NoticeItem[]> {
  const params = new URLSearchParams()
  if (query.category) params.set('category', query.category)
  if (query.keyword) params.set('keyword', query.keyword)
  if (query.include_expired) params.set('include_expired', 'true')
  if (query.limit) params.set('limit', String(query.limit))
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return request<NoticeItem[]>(`/api/v1/notices${suffix}`)
}

export function fetchNoticeCategories(): Promise<NoticeCategoryOption[]> {
  return request<NoticeCategoryOption[]>('/api/v1/notices/meta/categories')
}

/** 顶部紧急横幅：urgent + 置顶 + 生效中的公告 */
export function fetchBannerNotices(limit = 3): Promise<NoticeItem[]> {
  return request<NoticeItem[]>(`/api/v1/notices/banner?limit=${limit}`)
}

export function createNotice(payload: Record<string, unknown>): Promise<NoticeItem> {
  return request<NoticeItem>('/api/v1/notices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateNotice(id: number, payload: Record<string, unknown>): Promise<NoticeItem> {
  return request<NoticeItem>(`/api/v1/notices/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteNotice(id: number): Promise<void> {
  return request<void>(`/api/v1/notices/${id}`, { method: 'DELETE' })
}

// ---------------- 文档 ----------------
export function fetchDocuments(): Promise<DocumentItem[]> {
  return request<DocumentItem[]>('/api/v1/documents')
}

export function deleteDocument(id: number): Promise<{ deleted: boolean; document_id: number }> {
  return request(`/api/v1/documents/${id}`, { method: 'DELETE' })
}

/** 原始字节流上传：浏览器不做 base64，服务端边收边落盘 */
export async function uploadDocumentRaw(
  file: File,
  options: { title?: string; replace?: boolean } = {},
): Promise<UploadResult> {
  const params = new URLSearchParams({
    filename: file.name,
    replace: String(options.replace ?? true),
  })
  if (options.title) params.set('title', options.title)

  const headers = withAuth()
  headers.set('Content-Type', 'application/octet-stream')

  const response = await fetch(`${BASE}/api/v1/documents/upload-raw?${params.toString()}`, {
    method: 'POST',
    headers,
    body: file,
  })
  if (response.status === 401) {
    clearSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json()
      message = data.detail || message
    } catch {
      /* ignore */
    }
    throw new Error(message)
  }
  return (await response.json()) as UploadResult
}

export function uploadDocument(payload: {
  filename: string
  content?: string
  content_base64?: string
  title?: string
  replace?: boolean
}): Promise<UploadResult> {
  return request<UploadResult>('/api/v1/documents/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ replace: true, ...payload }),
  })
}
