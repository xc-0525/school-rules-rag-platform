import type {
  ConversationDetail,
  ConversationItem,
  DocumentItem,
  HealthInfo,
  LoginResponse,
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

// ---------------- 文档 ----------------
export function fetchDocuments(): Promise<DocumentItem[]> {
  return request<DocumentItem[]>('/api/v1/documents')
}

export function deleteDocument(id: number): Promise<{ deleted: boolean; document_id: number }> {
  return request(`/api/v1/documents/${id}`, { method: 'DELETE' })
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
