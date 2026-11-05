import type {
  AvailabilityInfo,
  ConversationDetail,
  ConversationItem,
  DocumentItem,
  HealthInfo,
  LocationItem,
  LoginResponse,
  LostFoundItem,
  NoticeCategoryOption,
  NoticeItem,
  OcrStatus,
  RepairItem,
  ReservationItem,
  UploadedImage,
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
  let response = await fetch(BASE + path, { ...init, headers: withAuth(init?.headers) })

  // 429 只读请求自动重试一次：限流偶尔打满时不应让整页列表变空白
  if (response.status === 429 && (!init?.method || init.method === 'GET')) {
    const waitSec = Math.min(Math.max(Number(response.headers.get('Retry-After') || 2), 1), 10)
    await new Promise((resolve) => setTimeout(resolve, waitSec * 1000))
    response = await fetch(BASE + path, { ...init, headers: withAuth(init?.headers) })
  }

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
      // 409 等冲突的 detail 是对象（{message, conflict_reservation_id}），取 message 避免 [object Object]
      const detail = data.detail ?? data.message
      message = typeof detail === 'object' && detail !== null ? detail.message || message : detail || message
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

/** OCR 引擎是否可用（扫描件 PDF 入库依赖它） */
export function fetchOcrStatus(): Promise<OcrStatus> {
  return request<OcrStatus>('/api/v1/documents/meta/ocr-status')
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

// ---------------- 校园服务：报修 / 预约 / 失物招领 / 文档预览 ----------------

export function createRepair(payload: Record<string, unknown>): Promise<RepairItem> {
  return request<RepairItem>('/api/v1/repairs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchRepairs(status = ''): Promise<RepairItem[]> {
  return request<RepairItem[]>(`/api/v1/repairs${status ? `?status=${status}` : ''}`)
}

export function transitionRepair(
  id: number,
  action: 'process' | 'complete' | 'close' | 'cancel',
  remark = '',
): Promise<RepairItem> {
  return request<RepairItem>(`/api/v1/repairs/${id}/transition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, remark }),
  })
}

export function fetchLocations(): Promise<LocationItem[]> {
  return request<LocationItem[]>('/api/v1/reservations/locations')
}

export function fetchAvailability(locationId: number, date: string): Promise<AvailabilityInfo> {
  return request<AvailabilityInfo>(
    `/api/v1/reservations/availability?location_id=${locationId}&date=${date}`,
  )
}

export function fetchReservations(): Promise<ReservationItem[]> {
  return request<ReservationItem[]>('/api/v1/reservations')
}

export function createReservation(payload: {
  location_id: number
  res_date: string
  slot: string
}): Promise<ReservationItem> {
  return request<ReservationItem>('/api/v1/reservations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function transitionReservation(
  id: number,
  action: 'cancel' | 'fulfill' | 'no_show',
  remark = '',
): Promise<ReservationItem> {
  return request<ReservationItem>(`/api/v1/reservations/${id}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ remark }),
  })
}

export function fetchLostFound(scope = 'public', kind = ''): Promise<LostFoundItem[]> {
  const params = new URLSearchParams({ scope })
  if (kind) params.set('kind', kind)
  return request<LostFoundItem[]>(`/api/v1/lost-found?${params}`)
}

export function createLostFound(payload: Record<string, unknown>): Promise<LostFoundItem> {
  return request<LostFoundItem>('/api/v1/lost-found', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function reviewLostFound(
  id: number,
  action: 'approve' | 'reject' | 'remove',
  remark = '',
): Promise<LostFoundItem> {
  return request<LostFoundItem>(`/api/v1/lost-found/${id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, remark }),
  })
}

export function resolveLostFound(id: number): Promise<LostFoundItem> {
  return request<LostFoundItem>(`/api/v1/lost-found/${id}/resolve`, { method: 'POST' })
}

export function uploadLostFoundImage(file: File): Promise<UploadedImage> {
  const form = new FormData()
  form.append('file', file)
  return request<UploadedImage>('/api/v1/lost-found/images', { method: 'POST', body: form })
}

/**
 * 带 Authorization 拉取受保护资源为 objectURL（图片/文档预览共用）。
 * <img>/window.open 无法带请求头，所以先取 blob 再转 URL；调用方负责 revoke。
 */
export async function fetchAuthedObjectUrl(path: string): Promise<string> {
  const response = await fetch(`${BASE}${path}`, { headers: withAuth() })
  if (response.status === 401) {
    clearSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('登录已过期')
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json()
      message = data.detail?.message || data.detail || message
    } catch {
      /* ignore */
    }
    throw new Error(typeof message === 'string' ? message : '加载失败')
  }
  const blob = await response.blob()
  return URL.createObjectURL(blob)
}

/**
 * 文档在线预览：返回 objectURL 供新窗口打开（415 = 类型不支持内嵌）。
 * 调用方负责 URL.revokeObjectURL。
 */
export async function fetchDocumentPreviewUrl(id: number): Promise<string> {
  const response = await fetch(`${BASE}/api/v1/documents/${id}/preview`, {
    headers: withAuth(),
  })
  if (response.status === 415) {
    let detail: any = null
    try {
      detail = (await response.json()).detail
    } catch {
      /* ignore */
    }
    throw Object.assign(new Error(detail?.message || '该类型不支持在线预览'), {
      unsupported: true,
      suffix: detail?.suffix || '',
    })
  }
  if (response.status === 401) {
    clearSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('登录已过期')
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json()
      message = data.detail?.message || data.detail || message
    } catch {
      /* ignore */
    }
    throw new Error(typeof message === 'string' ? message : '预览失败')
  }
  const blob = await response.blob()
  return URL.createObjectURL(blob)
}
