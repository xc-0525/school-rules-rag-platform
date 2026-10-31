import type { ChatDonePayload, SourceItem, TraceEvent } from '../types'
import { clearSession, getToken } from '../utils/session'

export interface StreamHandlers {
  onTrace?: (event: TraceEvent) => void
  onTool?: (event: Record<string, any>) => void
  onMeta?: (payload: { sources?: SourceItem[]; cached?: boolean; refused?: boolean }) => void
  onDelta?: (text: string) => void
  onDone?: (payload: ChatDonePayload) => void
  onError?: (message: string) => void
}

function parseBlock(raw: string): { event: string; data: any } | null {
  let event = 'message'
  let data = ''
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) }
  } catch {
    return null
  }
}

/** 调用 /api/v1/chat 并解析 SSE 事件流。 */
export async function streamChat(
  query: string,
  conversationId: number | null,
  handlers: StreamHandlers,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch('/api/v1/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({ query, stream: true, conversation_id: conversationId }),
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
  if (!response.body) throw new Error('浏览器不支持流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatch = (block: string) => {
    const parsed = parseBlock(block)
    if (!parsed) return
    const { event, data } = parsed
    if (event === 'trace') handlers.onTrace?.(data)
    else if (event === 'tool') handlers.onTool?.(data)
    else if (event === 'meta') handlers.onMeta?.(data)
    else if (event === 'delta') handlers.onDelta?.(data.content ?? '')
    else if (event === 'done') handlers.onDone?.(data)
    else if (event === 'error') handlers.onError?.(data.message ?? '未知错误')
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.indexOf('\n\n')
    while (index >= 0) {
      dispatch(buffer.slice(0, index))
      buffer = buffer.slice(index + 2)
      index = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) dispatch(buffer)
}
