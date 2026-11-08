<template>
  <div :class="['message-row', message.role]">
    <el-avatar :size="34" class="message-avatar" :class="message.role === 'user' ? 'user-avatar' : 'ai-avatar'">
      {{ message.role === 'user' ? '我' : 'AI' }}
    </el-avatar>

    <div class="message-body">
      <div v-if="message.role === 'assistant'" class="message-meta">
        <el-tag v-if="message.cached" size="small" type="info" effect="plain">缓存命中</el-tag>
        <el-tag v-if="message.refused" size="small" type="warning" effect="plain">防幻觉拒答</el-tag>
        <el-tag v-if="message.llmFailed" size="small" type="danger" effect="plain">模型不可用</el-tag>
        <el-tag v-else-if="message.degraded" size="small" type="danger" effect="plain">降级回答</el-tag>
        <span v-if="message.latency_ms" class="meta-text">耗时 {{ message.latency_ms }}ms</span>
      </div>

      <div v-if="message.role === 'user'" class="user-text">{{ message.content }}</div>

      <template v-else>
        <div v-if="!message.content && message.pending" class="typing">
          正在召回证据并生成回答<span class="dot">.</span>
        </div>
        <div
          v-else
          class="assistant-text markdown-body"
          v-html="renderMarkdown(message.content)"
        ></div>
        <div v-if="message.pending && message.content" class="typing">
          生成中<span class="dot">.</span>
        </div>
      </template>

      <div v-if="message.error" class="error-text">出错了：{{ message.error }}</div>

      <AgentTrace
        v-if="message.role === 'assistant' && message.trace && message.trace.length"
        :trace="message.trace"
        :runtime="message.runtime"
      />
      <div v-if="message.refused && message.sufficiencyReason" class="reject-reason">
        判定原因：{{ message.sufficiencyReason }}
      </div>

      <SourceList
        v-if="message.role === 'assistant'"
        :sources="message.sources"
        :refused="message.refused"
      />

      <!-- M-04：把引用校验结果显式告诉用户，"可溯源"才算闭环 -->
      <div
        v-if="message.role === 'assistant' && groundingBadge"
        class="grounding-badge"
        :class="`is-${message.groundingStatus}`"
        :title="message.groundingReason || ''"
      >
        {{ groundingBadge }}
      </div>

      <div
        v-if="message.role === 'assistant' && message.toolDrafts && message.toolDrafts.length"
        class="ticket-draft"
      >
        <div v-for="(draft, index) in message.toolDrafts" :key="index" class="ticket-draft-item">
          <div class="ticket-draft-title">{{ draftTitle(draft) }}</div>
          <div class="ticket-draft-desc">{{ draft.description }}</div>
          <div class="ticket-draft-actions">
            <el-tag v-if="draftDone(draft, index)" type="success" effect="plain">
              {{ draftDone(draft, index) }}
            </el-tag>
            <el-button
              v-else
              size="small"
              type="primary"
              :loading="confirming"
              @click="confirmDraft(draft, index)"
            >
              {{ draftButtonText(draft) }}
            </el-button>
            <span v-if="!draftDone(draft, index)" class="ticket-draft-hint">{{ draftHint(draft) }}</span>
          </div>
        </div>
      </div>

      <div v-if="message.role === 'assistant' && !message.pending && !message.error && message.id" class="feedback-row">
        <span>这个回答有帮助吗？</span>
        <el-button text size="small" :type="rating === 1 ? 'success' : ''" @click="submitFeedback(1)">
          👍 有帮助
        </el-button>
        <el-button text size="small" :type="rating === -1 ? 'danger' : ''" @click="submitFeedback(-1)">
          👎 没帮助
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Message, ToolDraft } from '../types'
import { renderMarkdown } from '../utils/markdown'
import AgentTrace from './AgentTrace.vue'
import SourceList from './SourceList.vue'

const props = defineProps<{ message: Message; rating?: number }>()
const emit = defineEmits<{
  (event: 'feedback', rating: number): void
  /** index 为该草稿在 toolDrafts 中的下标；done 由父组件在请求结束后调用，用于兜底关闭 loading */
  (event: 'confirmDraft', draft: ToolDraft, index: number, done: () => void): void
}>()

// 受控：高亮由父组件决定。点踩要先选原因，取消时不能留下"已提交"的观感
const rating = computed(() => props.rating ?? 0)
const confirming = ref(false)

function submitFeedback(value: number) {
  emit('feedback', value)
}

function confirmDraft(draft: ToolDraft, index: number) {
  confirming.value = true
  emit('confirmDraft', draft, index, () => {
    confirming.value = false
  })
}

const GROUNDING_LABELS: Record<string, string> = {
  verified: '引用已核对',
  unverified: '引用未通过校验，仅供参考',
  skipped: '工具直答，未参与引用校验',
}

const groundingBadge = computed(() => GROUNDING_LABELS[String(props.message.groundingStatus || '')] || '')

/** 卡片标题：每个 action 一种业务语义，不做通用拼串 */
function draftTitle(draft: ToolDraft): string {
  const labels: Record<string, string> = {
    create_reservation: '预约草稿',
    create_lost_found: '失物招领草稿',
    create_repair: '报修申请草稿',
    cancel_reservation: '取消预约确认',
    resolve_lost_found: '失物结案确认',
  }
  const label = labels[draft.action ?? ''] ?? '工单草稿'
  const suffix = (draft.action ?? 'create_ticket') === 'create_ticket' ? `（${draft.category}）` : ''
  return `${label} · ${draft.title}${suffix}`
}

function draftButtonText(draft: ToolDraft): string {
  switch (draft.action) {
    case 'create_reservation':
      return '确认预约'
    case 'create_lost_found':
      return '确认发布'
    case 'create_repair':
      return '确认提交报修'
    case 'cancel_reservation':
      return '确认取消'
    case 'resolve_lost_found':
      return '确认已找回/已归还'
    default:
      return '确认提交工单'
  }
}

function draftHint(draft: ToolDraft): string {
  switch (draft.action) {
    case 'create_reservation':
      return '未确认前不会占用座位'
    case 'create_lost_found':
      return '确认后提交管理员审核，通过才公示'
    case 'create_repair':
      return '确认后才进入待受理'
    case 'cancel_reservation':
      return '确认后才会释放该时段'
    case 'resolve_lost_found':
      return '确认后不再出现在公示栏'
    default:
      return '未确认前不会写入工单库'
  }
}

/** 已办结文案：工单/预约沿用消息级字段，其余按草稿下标取结果 */
function draftDone(draft: ToolDraft, index: number): string {
  if (draft.action === 'create_reservation' && props.message.reservationNo) {
    return `已预约：#${props.message.reservationNo}`
  }
  if ((draft.action ?? 'create_ticket') === 'create_ticket' && props.message.ticketNo) {
    return `已提交：${props.message.ticketNo}`
  }
  return props.message.draftResults?.[index] ?? ''
}
</script>
