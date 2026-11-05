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

    <div ref="scrollRef" class="chat-messages" @scroll="onScroll">
      <div v-if="!store.messages.length" class="welcome">
        <h2>问点什么？</h2>
        <p>
          校纪校规、办事流程、校园通知都可以问。系统会同时检索
          <b>Qdrant 向量库</b> 与 <b>Neo4j 知识图谱</b>，
          经证据融合、充分性判定后，由 DeepSeek 生成带条款来源的回答。
        </p>
        <div class="welcome-tags">
          <span class="welcome-tag">向量检索</span>
          <span class="welcome-tag">知识图谱</span>
          <span class="welcome-tag">多智能体协作</span>
        </div>
        <div class="examples">
          <button
            v-for="item in examples"
            :key="item.q"
            class="example-card"
            @click="useExample(item.q)"
          >
            <span class="example-text">{{ item.q }}</span>
            <span v-if="item.tag" class="example-tag">{{ item.tag }}</span>
          </button>
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
        @confirm-draft="(draft, draftIndex, done) => onConfirmDraft(index, draft, draftIndex, done)"
      />
    </div>

    <!-- 用户上翻阅读时不再抢滚动，改为按钮回到最新 -->
    <transition name="fade">
      <button v-if="!nearBottom" class="scroll-bottom" @click="jumpToBottom">
        回到底部 ↓
      </button>
    </transition>

    <div class="chat-input">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        resize="none"
        :placeholder="
          store.busy
            ? '正在生成回答，可以先写下一个问题（完成后发送）'
            : '输入问题，例如：宿舍晚上几点熄灯？（Enter 发送，Shift+Enter 换行）'
        "
        @keydown.enter.exact="onEnterKey"
      />
      <el-button v-if="store.busy" size="large" @click="stop">停止</el-button>
      <el-button v-else type="primary" size="large" @click="send">发送</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MessageBubble from '../components/MessageBubble.vue'
import { useChatStore } from '../stores/chat'
import { streamChat } from '../api/sse'
import {
  confirmTicket,
  createLostFound,
  createRepair,
  createReservation,
  resolveLostFound,
  sendFeedback,
  transitionReservation,
} from '../api/client'
import type { Message, ToolDraft } from '../types'

const store = useChatStore()
const input = ref('')
const scrollRef = ref<HTMLElement | null>(null)
/** 用户是否停留在底部附近；离开后暂停自动跟随，避免边读边被拽回 */
const nearBottom = ref(true)
// 当前在途问答的取消句柄：切页或开新对话时中止，避免前端白等、后端白算
let controller: AbortController | null = null

/**
 * 第 5 条是刻意留的「防幻觉拒答」演示，加角标说明，
 * 避免新用户以为系统答不上来。
 */
const examples = [
  { q: '宿舍晚上几点熄灯？' },
  { q: '考试时带手机进考场会怎么处理？' },
  { q: '国家奖学金需要什么条件？' },
  { q: '图书馆开放时间是什么时候？' },
  { q: '今天天气怎么样？', tag: '拒答演示' },
]

function scrollToBottom(behavior: ScrollBehavior = 'auto') {
  nextTick(() => {
    const element = scrollRef.value
    if (element) element.scrollTo({ top: element.scrollHeight, behavior })
  })
}

function onScroll() {
  const element = scrollRef.value
  if (!element) return
  nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

function jumpToBottom() {
  nearBottom.value = true
  scrollToBottom('smooth')
}

/** 中文输入法选词回车时 keydown 仍是 Enter，必须放行让候选词上屏 */
function onEnterKey(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  send()
}

function stop() {
  controller?.abort()
}

watch(
  () => store.messages.map((message) => message.content.length).join('|'),
  () => {
    if (nearBottom.value) scrollToBottom()
  },
)

async function send() {
  const query = input.value.trim()
  if (!query || store.busy) return

  store.addUserMessage(query)
  const assistantIndex = store.addAssistantPlaceholder()
  input.value = ''
  store.busy = true
  nearBottom.value = true
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
        if (nearBottom.value) scrollToBottom()
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
    if (nearBottom.value) scrollToBottom()
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

async function onConfirmDraft(
  index: number,
  draft: ToolDraft,
  draftIndex: number,
  done?: () => void,
) {
  try {
    await confirmDraftByAction(index, draft, draftIndex)
  } finally {
    // 无论成功失败都要恢复按钮，避免请求失败后一直转圈
    done?.()
  }
}

/** 一个 action 对应一个后端接口，各自独立处理，不做跨业务复用 */
async function confirmDraftByAction(index: number, draft: ToolDraft, draftIndex: number) {
  switch (draft.action) {
    case 'create_lost_found':
      return confirmLostFound(index, draft, draftIndex)
    case 'create_repair':
      return confirmRepair(index, draft, draftIndex)
    case 'cancel_reservation':
      return confirmCancelReservation(index, draft, draftIndex)
    case 'resolve_lost_found':
      return confirmResolveLostFound(index, draft, draftIndex)
    case 'create_reservation':
      return confirmReservation(index, draft)
    default:
      return confirmTicketDraft(index, draft)
  }
}

async function confirmReservation(index: number, draft: ToolDraft) {
  if (!draft.location_id || !draft.res_date || !draft.slot) {
    ElMessage.error('预约草稿缺少位置/日期参数，请重新发起对话')
    return
  }
  try {
    const result = await createReservation({
      location_id: draft.location_id,
      res_date: draft.res_date,
      slot: draft.slot,
    })
    store.setReservationNo(index, result.id)
    ElMessage.success(`已预约：${result.location_name} ${result.res_date} ${result.slot_label}`)
  } catch (error: any) {
    // 409 = 时段被抢，提示换时段
    ElMessage.error(`预约失败：${error?.message || error}`)
  }
}

async function confirmLostFound(index: number, draft: ToolDraft, draftIndex = 0) {
  if (!draft.kind || !draft.contact) {
    ElMessage.error('失物招领草稿缺少类型或联系方式，请重新发起对话')
    return
  }
  try {
    const result = await createLostFound({
      kind: draft.kind,
      title: draft.title,
      description: draft.description,
      location: draft.location || '',
      contact: draft.contact,
    })
    store.setDraftResult(index, draftIndex, `已提交审核：#${result.id}`)
    ElMessage.success(`已提交审核（#${result.id}），管理员通过后在公示栏展示`)
  } catch (error: any) {
    ElMessage.error(`发布失败：${error?.message || error}`)
  }
}

async function confirmRepair(index: number, draft: ToolDraft, draftIndex = 0) {
  if (!draft.building || !draft.room || !draft.contact) {
    ElMessage.error('报修草稿缺少楼栋/房间/联系方式，请重新发起对话')
    return
  }
  try {
    const result = await createRepair({
      title: draft.title,
      description: draft.description,
      category: draft.repair_category || 'other',
      building: draft.building,
      room: draft.room,
      contact: draft.contact,
    })
    store.setDraftResult(index, draftIndex, `已提交报修：#${result.id}`)
    ElMessage.success(`报修已提交（#${result.id}），状态：待受理`)
  } catch (error: any) {
    // 429 限流 / 409 重复，后端已给出中文原因
    ElMessage.error(`提交失败：${error?.message || error}`)
  }
}

async function confirmCancelReservation(index: number, draft: ToolDraft, draftIndex = 0) {
  if (!draft.reservation_id) {
    ElMessage.error('取消预约草稿缺少预约编号，请重新发起对话')
    return
  }
  try {
    await transitionReservation(draft.reservation_id, 'cancel')
    store.setDraftResult(index, draftIndex, `已取消：#${draft.reservation_id}`)
    ElMessage.success('预约已取消，该时段已释放')
  } catch (error: any) {
    ElMessage.error(`取消失败：${error?.message || error}`)
  }
}

async function confirmResolveLostFound(index: number, draft: ToolDraft, draftIndex = 0) {
  if (!draft.item_id) {
    ElMessage.error('结案草稿缺少条目编号，请重新发起对话')
    return
  }
  try {
    await resolveLostFound(draft.item_id)
    store.setDraftResult(index, draftIndex, `已结案：#${draft.item_id}`)
    ElMessage.success('已标记为已找回/已归还')
  } catch (error: any) {
    ElMessage.error(`结案失败：${error?.message || error}`)
  }
}

async function confirmTicketDraft(index: number, draft: ToolDraft) {
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
