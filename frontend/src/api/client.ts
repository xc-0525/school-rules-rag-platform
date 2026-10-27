import type {
  ConversationDetail,
  ConversationItem,
  DocumentItem,
  HealthInfo,
  UploadResult,
} from '../types'

const BASE = (import.meta as any).env?.VITE_API_BASE ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, init)
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
