<template>
  <el-row :gutter="16" class="page-grid">
    <el-col :span="8" style="height: 100%">
      <div class="panel panel-pad scroll-panel">
        <div class="panel-title">会话列表</div>
        <div v-if="loading" class="muted">加载中…</div>
        <div v-else-if="!conversations.length" class="muted">还没有会话，先去问一个问题。</div>
        <div
          v-for="item in conversations"
          :key="item.id"
          :class="['conversation-item', item.id === activeId ? 'active' : '']"
          @click="selectConversation(item.id)"
        >
          <div class="conversation-title">{{ item.title }}</div>
          <div class="conversation-sub">
            <span>#{{ item.id }} · {{ item.message_count }} 条消息</span>
            <span>{{ item.updated_at || item.created_at }}</span>
          </div>
        </div>
      </div>
    </el-col>

    <el-col :span="16" style="height: 100%">
      <div class="panel panel-pad scroll-panel">
        <div v-if="!detail" class="muted">选择左侧会话查看详情。</div>
        <template v-else>
          <div class="detail-head">
            <div>
              <div class="panel-title" style="margin-bottom: 4px">{{ detail.title }}</div>
              <div class="muted">会话 #{{ detail.id }} · {{ detail.messages.length }} 条消息</div>
            </div>
            <el-button type="primary" plain @click="continueConversation">继续对话</el-button>
          </div>

          <MessageBubble
            v-for="(message, index) in displayMessages"
            :key="index"
            :message="message"
            @feedback="(rating) => onFeedback(message, rating)"
          />
        </template>
      </div>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MessageBubble from '../components/MessageBubble.vue'
import { fetchConversation, fetchConversations, sendFeedback } from '../api/client'
import { useChatStore } from '../stores/chat'
import type { ConversationDetail, ConversationItem, Message } from '../types'

const router = useRouter()
const chatStore = useChatStore()

const conversations = ref<ConversationItem[]>([])
const detail = ref<ConversationDetail | null>(null)
const activeId = ref<number | null>(null)
const loading = ref(false)

const displayMessages = computed<Message[]>(() =>
  (detail.value?.messages || []).map((item) => ({
    id: item.id,
    role: item.role,
    content: item.content,
    sources: item.sources,
    refused: item.refused,
    degraded: item.degraded,
    latency_ms: item.latency_ms,
  })),
)

async function loadConversations() {
  loading.value = true
  try {
    conversations.value = await fetchConversations(30)
    if (!activeId.value && conversations.value.length) {
      await selectConversation(conversations.value[0].id)
    }
  } catch (error: any) {
    ElMessage.error(`加载会话失败：${error?.message || error}`)
  } finally {
    loading.value = false
  }
}

async function selectConversation(id: number) {
  activeId.value = id
  try {
    detail.value = await fetchConversation(id)
  } catch (error: any) {
    ElMessage.error(`加载会话详情失败：${error?.message || error}`)
    detail.value = null
  }
}

function continueConversation() {
  if (!detail.value) return
  chatStore.loadMessages(detail.value.id, displayMessages.value)
  router.push('/chat')
}

async function onFeedback(message: Message, rating: number) {
  if (!message.id) return
  try {
    await sendFeedback(message.id, rating)
    ElMessage.success('反馈已记录')
  } catch (error: any) {
    ElMessage.error(`反馈失败：${error?.message || error}`)
  }
}

onMounted(loadConversations)
</script>
