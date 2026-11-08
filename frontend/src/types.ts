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

export interface ToolDraft {
  status?: string
  requires_confirmation: boolean
  /**
   * 决定前端确认按钮的行为（一个 action 对应一个后端接口，互不复用）：
   * - create_ticket        → POST /tickets
   * - create_reservation   → POST /reservations
   * - create_lost_found    → POST /lost-found
   * - create_repair        → POST /repairs
   * - cancel_reservation   → POST /reservations/{id}/cancel
   * - resolve_lost_found   → POST /lost-found/{id}/resolve
   */
  action?:
    | 'create_ticket'
    | 'create_reservation'
    | 'create_lost_found'
    | 'create_repair'
    | 'cancel_reservation'
    | 'resolve_lost_found'
  title: string
  description: string
  category: string
  /** 预约草稿专用 */
  location_id?: number
  res_date?: string
  slot?: string
  slot_label?: string
  /** 失物招领草稿专用 */
  kind?: 'lost' | 'found'
  location?: string
  contact?: string
  /** 报修草稿专用 */
  repair_category?: string
  building?: string
  room?: string
  /** 取消预约 / 失物结案专用 */
  reservation_id?: number
  item_id?: number
}

/** 兼容旧命名 */
export type TicketDraft = ToolDraft

export interface ChatDonePayload {
  answer: string
  sources: SourceItem[]
  refused: boolean
  degraded: boolean
  cached: boolean
  grounding_status?: 'verified' | 'unverified' | 'skipped'
  grounding_reason?: string
  llm_failed?: boolean
  conversation_id: number | null
  message_id?: number | null
  tool_drafts?: TicketDraft[]
  intent: string
  retrieval_round: number
  regen_count: number
  latency_ms: number
  runtime: RuntimeInfo
}

export interface Message {
  /** 前端稳定标识：从创建起不变，用作列表 key，避免索引复用导致组件状态串位 */
  clientId?: string
  id?: number
  role: 'user' | 'assistant' | 'system'
  content: string
  sources?: SourceItem[]
  refused?: boolean
  degraded?: boolean
  cached?: boolean
  llmFailed?: boolean
  /** 拒答时的判定依据（"条款未提及…"），用于向用户解释为什么答不了 */
  sufficiencyReason?: string
  latency_ms?: number
  trace?: TraceEvent[]
  runtime?: RuntimeInfo
  pending?: boolean
  error?: string
  /** 待确认的操作草稿（工单 / 预约） */
  toolDrafts?: ToolDraft[]
  /** 确认后拿到的工单号 */
  ticketNo?: string
  /** 确认后拿到的预约记录号 */
  reservationNo?: number
  /** 其他草稿确认后的结果文案，按草稿下标存放（发布/报修/取消/结案） */
  draftResults?: Record<number, string>
}

export interface NoticeItem {
  id: number
  title: string
  content: string
  category: string
  category_label: string
  is_pinned: boolean
  priority: 'normal' | 'urgent'
  is_urgent: boolean
  status: string
  publisher: string
  effective_at: string | null
  expires_at: string | null
  created_at: string | null
  updated_at: string | null
  is_expired: boolean
}

export interface NoticeCategoryOption {
  code: string
  label: string
}

export interface ConversationItem {
  id: number
  title: string
  created_at?: string
  updated_at?: string
  message_count: number
}

/** 会话列表分页结果：total 由后端 COUNT 给出，has_more 不靠“本页是否满页”推断 */
export interface ConversationPage {
  items: ConversationItem[]
  total: number
  limit: number
  offset: number
  has_more: boolean
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

export interface DedupeItem {
  label: string
  chapter: string
  snippet: string
}

export interface DedupeInfo {
  total: number
  added: number
  duplicates: number
  added_items: DedupeItem[]
  duplicate_items: DedupeItem[]
  skipped_all: boolean
}

export interface UploadResult {
  document_id: number
  title: string
  file_name: string
  chunk_count: number
  chapters: number
  status: string
  message: string
  dedupe?: DedupeInfo | null
}

// ---------------- 校园服务：报修 / 预约 / 失物招领 ----------------

export interface RepairItem {
  id: number
  user_id: number
  title: string
  description: string
  category: string
  category_label: string
  building: string
  room: string
  contact: string
  status: 'pending' | 'processing' | 'completed' | 'closed'
  handler_id: number | null
  handler_remark: string | null
  created_at: string | null
  updated_at: string | null
}

export interface LocationItem {
  id: number
  name: string
  zone: string
  capacity: number
  is_active: boolean
}

export interface SlotAvailability {
  slot: string
  label: string
  /** 该时段是否已约满（remaining <= 0），保留兼容旧逻辑 */
  booked: boolean
  /** 位置容量（自习区 40 / 研讨间 6 …） */
  capacity?: number
  /** 已被占用的预约数 */
  booked_count?: number
  /** 剩余可预约数 */
  remaining?: number
}

export interface AvailabilityInfo {
  location_id: number
  date: string
  slots: SlotAvailability[]
  range_note: string
}

export interface ReservationItem {
  id: number
  location_id: number
  location_name: string
  location_zone: string
  user_id: number
  /** 管理员视角显示预约人 */
  username?: string
  res_date: string
  slot: string
  slot_label: string
  /** 日期已过但仍为 reserved */
  is_expired?: boolean
  /** 管理员取消时填写的原因 */
  cancel_reason?: string | null
  status: 'reserved' | 'cancelled' | 'fulfilled' | 'no_show'
  created_at: string | null
  updated_at: string | null
}

export interface LostFoundItem {
  id: number
  user_id: number
  kind: 'lost' | 'found'
  kind_label: string
  title: string
  description: string
  location: string
  contact: string
  images: string[]
  status: 'pending_review' | 'published' | 'rejected' | 'resolved' | 'removed'
  review_remark: string | null
  reviewed_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface UploadedImage {
  url: string
  size_bytes: number
  content_type: string
}

export interface OcrStatus {
  available: boolean
  engine: string
  hint: string
}

// ---------------- 用户账号管理（仅管理员） ----------------
export interface AdminUserItem {
  id: number
  username: string
  nickname: string
  role: string
  status: number
  created_at?: string | null
  updated_at?: string | null
}

export interface AdminUserPage {
  items: AdminUserItem[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export interface AdminUserUpdate {
  nickname?: string
  role?: string
  status?: number
}

// ---------------- 工单闭环（N-01） ----------------

export interface TicketItem {
  id: number
  ticket_no: string
  user_id: number
  /** 管理员视图显示提交人 */
  username?: string | null
  title: string
  description: string
  category: string | null
  status: 'open' | 'processing' | 'closed'
  status_label: string
  handler_id: number | null
  handler_remark: string | null
  created_at: string | null
  updated_at: string | null
  closed_at: string | null
}

export interface TicketPage {
  items: TicketItem[]
  total: number
}

