<template>
  <el-row :gutter="16" class="page-grid">
    <el-col :span="8" style="height: 100%">
      <div class="panel panel-pad history-side">
        <div class="history-side-head">
          <div class="panel-title" style="margin-bottom: 0">会话列表</div>
          <span v-if="total" class="muted history-count">共 {{ total }} 个</span>
        </div>

        <el-input
          v-model="keyword"
          class="history-search"
          placeholder="搜索会话标题"
          clearable
          :prefix-icon="Search"
        />

        <div v-if="loading" class="muted history-tip">加载中…</div>
        <div v-else-if="!items.length" class="history-empty">
          <template v-if="keyword.trim()">
            <div>没有匹配「{{ keyword.trim() }}」的会话</div>
            <el-button link type="primary" size="small" @click="clearKeyword">清除搜索</el-button>
          </template>
          <template v-else>还没有会话，先去问一个问题。</template>
        </div>

        <div v-else class="history-list">
          <div
            v-for="item in items"
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

        <div v-if="!loading && items.length" class="history-more">
          <el-button
            v-if="items.length < total"
            size="small"
            :loading="loadingMore"
            :disabled="loading"
            @click="loadMore"
          >
            加载更多（还剩 {{ total - items.length }} 条）
          </el-button>
          <span v-else class="muted history-end">已加载全部 {{ total }} 条</span>
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
            :key="message.id ?? `hist-${index}`"
            :message="message"
            @feedback="(rating) => onFeedback(message, rating)"
          />
        </template>
      </div>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import MessageBubble from '../components/MessageBubble.vue'
import { fetchConversation, fetchConversations, sendFeedback } from '../api/client'
import { useChatStore } from '../stores/chat'
import type { ConversationDetail, ConversationItem, Message } from '../types'

const router = useRouter()
const chatStore = useChatStore()

/** 左栏一屏约 8~10 条，取 10 条一页；不在界面暴露页大小，减少无意义选择 */
const PAGE_SIZE = 10

const items = ref<ConversationItem[]>([])
const total = ref(0)
const keyword = ref('')
const loading = ref(false)
const loadingMore = ref(false)
const detail = ref<ConversationDetail | null>(null)
const activeId = ref<number | null>(null)

/** 请求序号：快速改关键词或连点“加载更多”时丢弃过期响应 */
let requestSeq = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined

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

async function loadPage(offset: number, append: boolean) {
  const seq = ++requestSeq
  if (append) loadingMore.value = true
  else loading.value = true
  try {
    const page = await fetchConversations({
      limit: PAGE_SIZE,
      offset,
      keyword: keyword.value,
    })
    if (seq !== requestSeq) return

    if (append) {
      // 后端按 updated_at + id 稳定排序，这里再做一次去重兜底
      const seen = new Set(items.value.map((item) => item.id))
      items.value = [...items.value, ...page.items.filter((item) => !seen.has(item.id))]
    } else {
      items.value = page.items
    }
    total.value = page.total

    if (!append) {
      if (items.value.length) {
        // 当前选中不在新结果里（搜索后）就切到第一条，避免左右两边对不上
        if (!activeId.value || !items.value.some((item) => item.id === activeId.value)) {
          await selectConversation(items.value[0].id)
        }
      } else {
        activeId.value = null
        detail.value = null
      }
    }
  } catch (error: any) {
    if (seq === requestSeq) ElMessage.error(`加载会话失败：${error?.message || error}`)
  } finally {
    if (seq === requestSeq) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

function reload() {
  loadPage(0, false)
}

function loadMore() {
  if (loading.value || loadingMore.value) return
  if (items.value.length >= total.value) return
  loadPage(items.value.length, true)
}

function clearKeyword() {
  keyword.value = ''
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

watch(keyword, () => {
  if (searchTimer) clearTimeout(searchTimer)
  // 输入停顿 300ms 再查，避免每个字符都打一次接口
  searchTimer = setTimeout(reload, 300)
})

onMounted(reload)

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>
