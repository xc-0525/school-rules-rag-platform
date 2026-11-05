/**
 * 紧急公告横幅的本地关闭记录。
 *
 * 学生点 × 只影响自己这台浏览器（localStorage），公告本身仍在公告板块可见；
 * 记录键带上 updated_at，管理员更新内容后会重新弹出，避免旧关闭记录把新内容也吞掉。
 */
import type { NoticeItem } from '../types'

const STORAGE_KEY = 'notice-banner-dismissed-v1'
const MAX_RECORDS = 50

/** 关闭记录变化时广播，横幅与公告页的"已隐藏 N 条"据此保持同步 */
export const NOTICE_DISMISSED_EVENT = 'notice-banner:dismissed'

export function noticeDismissKey(item: Pick<NoticeItem, 'id' | 'updated_at' | 'effective_at'>): string {
  return `${item.id}:${item.updated_at || item.effective_at || ''}`
}

export function loadDismissedNotices(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const list = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(list) ? list.filter((v): v is string => typeof v === 'string') : []
  } catch {
    return []
  }
}

function notifyChanged(list: string[]) {
  window.dispatchEvent(new CustomEvent<string[]>(NOTICE_DISMISSED_EVENT, { detail: list }))
}

/** 订阅关闭记录变化：同页面靠自定义事件，跨标签页靠 storage 事件 */
export function subscribeDismissedNotices(handler: (list: string[]) => void): () => void {
  const onCustom = (event: Event) => {
    const detail = (event as CustomEvent<string[]>).detail
    handler(Array.isArray(detail) ? detail : loadDismissedNotices())
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === null) handler(loadDismissedNotices())
  }
  window.addEventListener(NOTICE_DISMISSED_EVENT, onCustom)
  window.addEventListener('storage', onStorage)
  return () => {
    window.removeEventListener(NOTICE_DISMISSED_EVENT, onCustom)
    window.removeEventListener('storage', onStorage)
  }
}

/** 关闭一条公告，返回最新记录（超出上限丢弃最旧的） */
export function dismissNotice(key: string): string[] {
  const next = [...loadDismissedNotices().filter((k) => k !== key), key].slice(-MAX_RECORDS)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    /* 隐私模式等场景写不进去就只当本次会话生效 */
  }
  notifyChanged(next)
  return next
}

/** 恢复显示全部被关闭的紧急公告，返回清掉的条数 */
export function clearDismissedNotices(): number {
  const count = loadDismissedNotices().length
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
  notifyChanged([])
  return count
}
