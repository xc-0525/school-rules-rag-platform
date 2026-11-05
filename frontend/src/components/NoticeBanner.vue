<template>
  <transition name="banner-slide">
    <div v-if="current" class="notice-banner" role="alert">
      <span class="notice-banner-badge">
        <el-icon><BellFilled /></el-icon>
        紧急
      </span>
      <div class="notice-banner-body" title="点击查看公告详情" @click="goNotices">
        <span class="notice-banner-title">{{ current.title }}</span>
        <span class="notice-banner-text">{{ summary }}</span>
      </div>
      <div v-if="visible.length > 1" class="notice-banner-pager">
        <button class="pager-btn" title="上一条" @click.stop="step(-1)">‹</button>
        <span>{{ index + 1 }}/{{ visible.length }}</span>
        <button class="pager-btn" title="下一条" @click.stop="step(1)">›</button>
      </div>
      <el-button link type="primary" class="notice-banner-more" @click="goNotices">
        查看公告
      </el-button>
      <button
        class="notice-banner-close"
        title="关闭本次提示（公告板块仍可查看）"
        aria-label="关闭紧急公告"
        @click="dismissCurrent"
      >
        ×
      </button>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { BellFilled } from '@element-plus/icons-vue'
import { fetchBannerNotices } from '../api/client'
import type { NoticeItem } from '../types'
import { dismissNotice, loadDismissedNotices, noticeDismissKey, subscribeDismissedNotices } from '../utils/noticeBanner'

const router = useRouter()
const items = ref<NoticeItem[]>([])
const dismissed = ref<string[]>([])
const index = ref(0)

/** 过滤掉学生已关闭的公告，剩下的按后端排序（最新生效在前）轮播 */
const visible = computed(() =>
  items.value.filter((item) => !dismissed.value.includes(noticeDismissKey(item))),
)
const current = computed(() => visible.value[index.value] || visible.value[0] || null)
const summary = computed(() => {
  const text = (current.value?.content || '').replace(/\s+/g, ' ').trim()
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
})

function step(delta: number) {
  const total = visible.value.length
  if (!total) return
  index.value = (index.value + delta + total) % total
}

function dismissCurrent() {
  if (!current.value) return
  dismissNotice(noticeDismissKey(current.value))
}

function goNotices() {
  if (!current.value) return
  router.push({ path: '/notices', query: { focus: String(current.value.id) } })
}

/** 关闭记录可能来自本页 ×、公告页"恢复显示"或另一个标签页，统一按最新记录重算 */
function applyDismissed(list: string[]) {
  const restored = list.length < dismissed.value.length
  dismissed.value = list
  const remain = items.value.filter((item) => !list.includes(noticeDismissKey(item))).length
  // 关闭后落到同位置的下一条；恢复后从头轮播
  index.value = restored ? 0 : Math.min(index.value, Math.max(remain - 1, 0))
}

let unsubscribe: (() => void) | null = null

onMounted(async () => {
  dismissed.value = loadDismissedNotices()
  unsubscribe = subscribeDismissedNotices(applyDismissed)
  try {
    items.value = await fetchBannerNotices()
  } catch {
    // 横幅是增强体验，加载失败静默即可，不阻塞主界面
  }
})

onBeforeUnmount(() => {
  unsubscribe?.()
})
</script>
