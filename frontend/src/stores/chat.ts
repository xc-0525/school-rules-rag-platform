import { defineStore } from 'pinia'
import type { Message, RuntimeInfo, SourceItem, TraceEvent } from '../types'
import { fetchConversation } from '../api/client'

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversationId: null as number | null,
    messages: [] as Message[],
    busy: false,
    lastRuntime: null as RuntimeInfo | null,
  }),

  actions: {
    resetConversation() {
      this.conversationId = null
      this.messages = []
      this.lastRuntime = null
    },

    loadMessages(conversationId: number, messages: Message[]) {
      this.conversationId = conversationId
      this.messages = messages
    },

    addUserMessage(content: string) {
      this.messages.push({ role: 'user', content })
    },

    addAssistantPlaceholder(): number {
      this.messages.push({
        role: 'assistant',
        content: '',
        sources: [],
        trace: [],
        pending: true,
      })
      return this.messages.length - 1
    },

    addTrace(index: number, event: TraceEvent) {
      const message = this.messages[index]
      if (!message) return
      message.trace = message.trace || []
      message.trace.push(event)
    },

    appendDelta(index: number, text: string) {
      const message = this.messages[index]
      if (message) message.content += text
    },

    setSources(index: number, sources: SourceItem[], cached = false) {
      const message = this.messages[index]
      if (!message) return
      message.sources = sources || []
      if (cached) message.cached = true
    },

    finishMessage(index: number, payload: any) {
      const message = this.messages[index]
      if (!message) return
      message.content = payload.answer ?? message.content
      message.sources = payload.sources ?? message.sources ?? []
      message.refused = !!payload.refused
      message.degraded = !!payload.degraded
      message.cached = !!payload.cached
      message.latency_ms = payload.latency_ms
      message.runtime = payload.runtime
      message.pending = false
      if (payload.runtime) this.lastRuntime = payload.runtime
    },

    failMessage(index: number, error: string) {
      const message = this.messages[index]
      if (!message) return
      message.pending = false
      message.error = error
      if (!message.content) message.content = ''
    },

    async syncMessageIds() {
      if (!this.conversationId) return
      try {
        const detail = await fetchConversation(this.conversationId)
        const persisted = detail.messages || []
        // 把已落库的消息 ID 回填到最后两条（user / assistant），用于点赞点踩
        const tail = this.messages.slice(-2)
        const tailPersisted = persisted.slice(-2)
        tail.forEach((message, offset) => {
          const stored = tailPersisted[offset]
          if (stored && stored.role === message.role) {
            message.id = stored.id
            if (stored.sources?.length) message.sources = stored.sources
          }
        })
      } catch {
        /* 历史同步失败不影响主流程 */
      }
    },
  },
})
