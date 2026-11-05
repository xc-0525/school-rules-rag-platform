<template>
  <div class="panel panel-pad" style="height: 100%; overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">校园公告</div>
        <div class="muted">
          停水停电、放假调课、服务窗口调整等临时通知；问答也会引用这里的内容
        </div>
      </div>
      <div class="document-actions">
        <el-button v-if="auth.isAdmin" type="primary" @click="openEditor()">发布公告</el-button>
        <el-tag v-else type="info" effect="plain">学生账号只读</el-tag>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="notice-filters">
      <el-radio-group v-model="category" size="small" @change="load">
        <el-radio-button label="">全部</el-radio-button>
        <el-radio-button v-for="item in categories" :key="item.code" :label="item.code">
          {{ item.label }}
        </el-radio-button>
      </el-radio-group>
      <el-input
        v-model="keyword"
        placeholder="搜索标题或正文"
        clearable
        style="width: 220px"
        @keyup.enter="load"
        @clear="load"
      />
      <el-checkbox v-if="auth.isAdmin" v-model="includeExpired" @change="load">
        显示已结束
      </el-checkbox>
      <span v-if="hiddenCount > 0" class="restore-dismissed">
        已隐藏 {{ hiddenCount }} 条紧急提示
        <el-button link type="primary" size="small" @click="restoreDismissed">恢复显示</el-button>
      </span>
    </div>

    <el-empty v-if="!loading && !notices.length" description="暂无公告" />

    <div
      v-for="item in notices"
      :id="`notice-${item.id}`"
      :key="item.id"
      class="notice-card"
      :class="{ 'is-expired': item.is_expired, 'is-urgent': item.is_urgent, 'is-focus': item.id === focusId }"
    >
      <div class="notice-head">
        <el-tag v-if="item.is_urgent" type="danger" effect="dark" size="small">紧急</el-tag>
        <el-tag v-else-if="item.is_pinned" type="danger" effect="plain" size="small">置顶</el-tag>
        <el-tag size="small" effect="plain" type="primary">{{ item.category_label }}</el-tag>
        <el-tag v-if="item.status === 'draft'" size="small" type="info" effect="plain">草稿</el-tag>
        <el-tag v-if="item.is_expired" size="small" type="info" effect="plain">已结束</el-tag>
        <span class="notice-title">{{ item.title }}</span>
        <span class="notice-time">{{ item.effective_at || item.created_at }}</span>
      </div>
      <div class="notice-content">{{ item.content }}</div>
      <div class="notice-foot">
        <span v-if="item.expires_at">有效期至 {{ item.expires_at }}</span>
        <span v-else>长期有效</span>
        <span v-if="item.publisher">· 发布人 {{ item.publisher }}</span>
        <span v-if="auth.isAdmin" class="notice-admin">
          <el-button link type="primary" size="small" @click="openEditor(item)">编辑</el-button>
          <el-button link type="danger" size="small" @click="remove(item)">删除</el-button>
        </span>
      </div>
    </div>

    <el-dialog
      v-model="editorVisible"
      :title="editing ? '编辑公告' : '发布公告'"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="96px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="如：关于本周六停水的通知" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category" style="width: 180px">
            <el-option v-for="item in categories" :key="item.code" :label="item.label" :value="item.code" />
          </el-select>
        </el-form-item>
        <el-form-item label="正文" required>
          <el-input v-model="form.content" type="textarea" :rows="5" placeholder="说明影响范围、时间段、注意事项" />
        </el-form-item>
        <el-form-item label="生效时间">
          <el-date-picker
            v-model="form.effective_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空则立即生效"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item label="失效时间">
          <el-date-picker
            v-model="form.expires_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空表示长期有效"
            style="width: 240px"
          />
          <div class="muted" style="font-size: 12px">
            到期后自动从列表和问答中消失，不需要手工下架
          </div>
        </el-form-item>
        <el-form-item label="置顶">
          <el-switch v-model="form.is_pinned" />
        </el-form-item>
        <el-form-item label="紧急横幅">
          <el-switch v-model="form.priority" active-value="urgent" inactive-value="normal" />
          <span class="muted" style="font-size: 12px; margin-left: 8px">
            开启后学生登录后顶部悬挂提示，可点 × 关闭；自动置顶
          </span>
        </el-form-item>
        <el-form-item label="立即发布">
          <el-switch v-model="form.published" />
          <span class="muted" style="font-size: 12px; margin-left: 8px">关闭则保存为草稿</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createNotice,
  deleteNotice,
  fetchBannerNotices,
  fetchNoticeCategories,
  fetchNotices,
  updateNotice,
} from '../api/client'
import { useAuthStore } from '../stores/auth'
import {
  clearDismissedNotices,
  loadDismissedNotices,
  noticeDismissKey,
  subscribeDismissedNotices,
} from '../utils/noticeBanner'
import type { NoticeCategoryOption, NoticeItem } from '../types'

const route = useRoute()
const auth = useAuthStore()
const notices = ref<NoticeItem[]>([])
const categories = ref<NoticeCategoryOption[]>([])
const category = ref('')
const keyword = ref('')
const includeExpired = ref(false)
const loading = ref(false)
const saving = ref(false)
const editorVisible = ref(false)
const editing = ref<NoticeItem | null>(null)
/** 从横幅"查看公告"跳来时，高亮目标卡片 */
const focusId = ref<number | null>(null)
/** 被学生点 × 隐藏的紧急提示条数 */
const hiddenCount = ref(0)
/** 顶部横幅当前挂载的紧急公告，用来把"隐藏条数"算准（公告下架后不再计数） */
const bannerItems = ref<NoticeItem[]>([])
const bannerLoaded = ref(false)

const emptyForm = () => ({
  title: '',
  content: '',
  category: 'other',
  is_pinned: false,
  priority: 'normal' as 'normal' | 'urgent',
  published: true,
  effective_at: '',
  expires_at: '',
})
const form = ref(emptyForm())

async function load() {
  loading.value = true
  try {
    notices.value = await fetchNotices({
      category: category.value,
      keyword: keyword.value.trim(),
      include_expired: includeExpired.value,
    })
    await scrollToFocus()
  } catch (error: any) {
    ElMessage.error(`加载公告失败：${error?.message || error}`)
  } finally {
    loading.value = false
  }
}

async function scrollToFocus() {
  if (!focusId.value) return
  await nextTick()
  const target = document.getElementById(`notice-${focusId.value}`)
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function countDismissed(): number {
  const keys = loadDismissedNotices()
  if (!bannerLoaded.value) return keys.length
  // 与横幅口径对齐：只统计当前仍在横幅里的公告，避免下架后残留记录让计数虚高
  return bannerItems.value.filter((item) => keys.includes(noticeDismissKey(item))).length
}

function refreshHiddenCount() {
  hiddenCount.value = countDismissed()
}

async function loadBanner() {
  try {
    bannerItems.value = await fetchBannerNotices()
    bannerLoaded.value = true
  } catch {
    // 横幅接口失败时退化成按记录条数展示，至少保证"恢复显示"入口可点
    bannerLoaded.value = false
  } finally {
    refreshHiddenCount()
  }
}

function restoreDismissed() {
  // 清空后“已隐藏”计数必须归零，否则按钮会一直留在页面上（且第二次点击无意义）
  hiddenCount.value = 0
  const cleared = clearDismissedNotices()
  // 横幅组件订阅了关闭记录变化，会自行重新读取并恢复显示
  ElMessage.success(cleared ? `已恢复显示 ${cleared} 条紧急提示` : '已恢复显示紧急提示')
}

async function loadCategories() {
  try {
    categories.value = await fetchNoticeCategories()
  } catch {
    /* 分类字典加载失败时退回空列表，不影响主流程 */
  }
}

function openEditor(item?: NoticeItem) {
  editing.value = item || null
  form.value = item
    ? {
        title: item.title,
        content: item.content,
        category: item.category,
        is_pinned: item.is_pinned,
        priority: item.priority || 'normal',
        published: item.status === 'published',
        effective_at: item.effective_at || '',
        expires_at: item.expires_at || '',
      }
    : emptyForm()
  editorVisible.value = true
}

async function save() {
  if (!form.value.title.trim() || !form.value.content.trim()) {
    ElMessage.warning('标题和正文不能为空')
    return
  }
  const payload: Record<string, unknown> = {
    title: form.value.title.trim(),
    content: form.value.content.trim(),
    category: form.value.category,
    is_pinned: form.value.is_pinned,
    priority: form.value.priority,
    status: form.value.published ? 'published' : 'draft',
  }
  // 留空表示"立即生效 / 长期有效"，不要传空串给后端
  if (form.value.effective_at) payload.effective_at = form.value.effective_at
  if (form.value.expires_at) payload.expires_at = form.value.expires_at

  saving.value = true
  try {
    if (editing.value) {
      await updateNotice(editing.value.id, payload)
      ElMessage.success('公告已更新')
    } else {
      await createNotice(payload)
      ElMessage.success('公告已发布')
    }
    editorVisible.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(`保存失败：${error?.message || error}`)
  } finally {
    saving.value = false
  }
}

async function remove(item: NoticeItem) {
  try {
    await ElMessageBox.confirm(`确定删除「${item.title}」？`, '删除公告', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteNotice(item.id)
    ElMessage.success('已删除')
    await load()
  } catch (error: any) {
    ElMessage.error(`删除失败：${error?.message || error}`)
  }
}

let unsubscribe: (() => void) | null = null

onMounted(() => {
  const focus = Number(route.query.focus)
  if (Number.isInteger(focus) && focus > 0) focusId.value = focus
  // 先按本地记录出一次入口，等横幅数据回来再校正为准确条数
  refreshHiddenCount()
  unsubscribe = subscribeDismissedNotices(refreshHiddenCount)
  loadBanner()
  loadCategories()
  load()
})

onBeforeUnmount(() => {
  unsubscribe?.()
})
</script>
