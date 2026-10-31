export interface SourceItem {
  type: 'vector' | 'graph' | 'tool'
  title: string
  detail: string
  snippet: string
}

export interface UserInfo {
  id: number
  username: string
  nickname?: string | null
  role: 'admin' | 'student' | string
}

export interface LoginResponse {
  token: string
  expires_in: number
  user: UserInfo
}

export interface TraceEvent {
  ts_ms?: number
  node?: string
  phase?: 'start' | 'end' | 'route' | 'tool_start' | 'tool_end' | string
  kind?: 'agent' | 'parallel' | 'terminal' | string
  control?: '' | 'parallel' | 'selection' | 'loop' | string
  label?: string
  latency_ms?: number
  from?: string
  to?: string | null
  reason?: string
  tool?: string
  arguments?: Record<string, any>
  success?: boolean
  summary?: string
}

export interface RuntimeInfo {
  steps: number
  visited: string[]
  agent_node_runs: number
  retrieval_round: number
  regen_count: number
  tool_calls?: number
  latency_ms: number
}

export interface ChatDonePayload {
  answer: string
  sources: SourceItem[]
  refused: boolean
  degraded: boolean
  cached: boolean
  conversation_id: number | null
  intent: string
  retrieval_round: number
  regen_count: number
  latency_ms: number
  runtime: RuntimeInfo
}

export interface Message {
  id?: number
  role: 'user' | 'assistant' | 'system'
  content: string
  sources?: SourceItem[]
  refused?: boolean
  degraded?: boolean
  cached?: boolean
  latency_ms?: number
  trace?: TraceEvent[]
  runtime?: RuntimeInfo
  pending?: boolean
  error?: string
}

export interface ConversationItem {
  id: number
  title: string
  created_at?: string
  updated_at?: string
  message_count: number
}

export interface MessageItem {
  id: number
  conversation_id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  sources: SourceItem[]
  refused: boolean
  degraded: boolean
  latency_ms: number
  created_at?: string
}

export interface ConversationDetail {
  id: number
  user_id: number
  title: string
  created_at?: string
  updated_at?: string
  message_count: number
  messages: MessageItem[]
}

export interface DocumentItem {
  id: number
  title: string
  file_name: string
  file_type: string
  file_size: number
  chunk_count: number
  status: string
  created_at?: string
  updated_at?: string
}

export interface HealthInfo {
  status: string
  version: string
  modes: Record<string, string>
  llm_configured: boolean
  vector_ready: boolean | null
  graph: {
    node_count: number
    agent_node_count: number
    control_structures: string[]
  }
}

export interface UploadResult {
  document_id: number
  title: string
  file_name: string
  chunk_count: number
  chapters: number
  status: string
  message: string
}
