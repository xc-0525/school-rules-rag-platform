<template>
  <div class="chat-page">
    <div class="chat-toolbar">
      <div class="chat-title">
        智能问答
        <el-tag v-if="store.conversationId" size="small" effect="plain">会话 #{{ store.conversationId }}</el-tag>
        <el-tag v-if="store.busy" size="small" type="warning" effect="plain">生成中</el-tag>
      </div>
      <div>
        <el-button size="small" :disabled="store.busy" @click="newConversation">新对话</el-button>
      </div>
    </div>

    <div ref="scrollRef" class="chat-messages">
      <div v-if="!store.messages.length" class="welcome">
        <h2>问点什么？</h2>
        <p>
          系统会同时检索 <b>Qdrant 向量库</b> 与 <b>Neo4j 知识图谱</b>，
          经证据融合、充分性判定后，由 DeepSeek 生成带条款来源的回答。
        </p>
        <div class="examples">
          <el-button v-for="question in examples" :key="question" round @click="useExample(question)">
            {{ question }}
          </el-button>
        </div>
        <div class="hint">
          演示路径：Agent 轨迹（并行召回 / 充分性选择 / 查询扩展循环）→ 来源引用 → 点赞点踩
        </div>
      </div>

      <MessageBubble
        v-for="(message, index) in store.messages"
        :key="message.clientId ?? index"
        :message="message"
        @feedback="(rating) => onFeedback(message, rating)"
        @confirm-ticket="(draft) => onConfirmTicket(index, draft)"
      />
    </div>

    <div class="chat-input">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        resize="none"
        :disabled="store.busy"
        placeholder="输入问题，例如：宿舍晚上几点熄灯？（Enter 发送，Shift+Enter 换行）"
        @keydown.enter.exact.prevent="send"
      />
      <el-button type="primary" size="large" :loading="store.busy" @click="send">发送</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MessageBubble from '../components/MessageBubble.vue'
import { useChatStore } from '../stores/chat'
import { streamChat } from '../api/sse'
import { confirmTicket, sendFeedback } from '../api/client'
import type { Message, TicketDraft } from '../types'

const store = useChatStore()
const input = ref('')
const scrollRef = ref<HTMLElement | null>(null)
// 当前在途问答的取消句柄：切页或开新对话时中止，避免前端白等、后端白算
let controller: AbortController | null = null

const examples = [
  '宿舍晚上几点熄灯？',
  '考试时带手机进考场会怎么处理？',
  '国家奖学金需要什么条件？',
  '图书馆开放时间是什么时候？',
  '今天天气怎么样？',
]

function scrollToBottom() {
  nextTick(() => {
    const element = scrollRef.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

watch(
  () => store.messages.map((message) => message.content.length).join('|'),
  scrollToBottom,
)

async function send() {
  const query = input.value.trim()
  if (!query || store.busy) return

  store.addUserMessage(query)
  const assistantIndex = store.addAssistantPlaceholder()
  input.value = ''
  store.busy = true
  scrollToBottom()

  controller = new AbortController()
  try {
    await streamChat(query, store.conversationId, {
      onTrace: (event) => store.addTrace(assistantIndex, event),
      onTool: (event) => store.addTrace(assistantIndex, event as any),
      onMeta: (payload) => {
        if (payload.sources) store.setSources(assistantIndex, payload.sources, !!payload.cached)
      },
      onDelta: (text) => {
        store.appendDelta(assistantIndex, text)
      },
      onReset: () => {
        store.resetMessage(assistantIndex)
      },
      onDone: (payload) => {
        store.finishMessage(assistantIndex, payload)
        if (payload.conversation_id) {
          store.conversationId = payload.conversation_id
        }
        scrollToBottom()
      },
      onError: (message) => store.failMessage(assistantIndex, message),
    }, controller.signal)
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      store.cancelMessage(assistantIndex)
    } else {
      store.failMessage(assistantIndex, error?.message || String(error))
    }
  } finally {
    controller = null
    store.busy = false
    scrollToBottom()
  }
}

function useExample(question: string) {
  input.value = question
  send()
}

function newConversation() {
  controller?.abort()
  store.resetConversation()
  input.value = ''
}

onBeforeUnmount(() => {
  controller?.abort()
})

async function onConfirmTicket(index: number, draft: TicketDraft) {
  try {
    const result = await confirmTicket({
      title: draft.title,
      description: draft.description,
      category: draft.category,
    })
    store.setTicketNo(index, result.ticket_no)
    ElMessage.success(
      result.reused
        ? `已存在相同工单：${result.ticket_no}`
        : `工单已提交：${result.ticket_no}`,
    )
  } catch (error: any) {
    ElMessage.error(`工单提交失败：${error?.message || error}`)
  }
}

async function onFeedback(message: Message, rating: number) {
  if (!message.id) {
    await store.syncMessageIds()
  }
  if (!message.id) {
    ElMessage.warning('消息尚未落库，稍后再试')
    return
  }
  try {
    await sendFeedback(message.id, rating)
    ElMessage.success(rating > 0 ? '感谢反馈：有帮助' : '已记录：没帮助')
  } catch (error: any) {
    ElMessage.error(`反馈失败：${error?.message || error}`)
  }
}
</script>
