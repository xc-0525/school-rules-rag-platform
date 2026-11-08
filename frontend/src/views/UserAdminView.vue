<template>
  <div class="panel panel-pad user-admin" style="height: 100%; overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">用户账号管理</div>
        <div class="muted">
          可修改昵称、角色与账号状态，并重置密码；改密 / 降权 / 禁用后该用户已签发的登录凭证立即失效
        </div>
      </div>
      <div class="ua-actions">
        <el-input
          v-model="keyword"
          placeholder="搜索用户名或昵称"
          clearable
          style="width: 190px"
          @input="onKeywordInput"
        />
        <el-select
          v-model="roleFilter"
          clearable
          placeholder="全部角色"
          style="width: 118px"
          @change="reload"
        >
          <el-option label="管理员" value="admin" />
          <el-option label="学生" value="student" />
        </el-select>
        <el-select
          v-model="statusFilter"
          clearable
          placeholder="全部状态"
          style="width: 118px"
          @change="reload"
        >
          <el-option label="启用" :value="1" />
          <el-option label="禁用" :value="0" />
        </el-select>
        <el-button :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>

    <div class="ua-summary">
      <el-tag effect="plain" round>共 {{ total }} 个账号</el-tag>
      <el-tag v-if="adminTotal" effect="plain" round type="danger">管理员 {{ adminTotal }}</el-tag>
      <span class="muted" style="font-size: 12px">
        用户名是登录标识，创建后不可修改；账号不做物理删除，只做禁用（历史会话与工单仍可追溯）
      </span>
    </div>

    <el-table v-loading="loading" :data="rows" style="width: 100%" empty-text="没有匹配的账号">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="用户名" min-width="150">
        <template #default="{ row }">
          <span class="ua-username">{{ row.username }}</span>
          <el-tag v-if="row.id === auth.user?.id" size="small" effect="plain" type="info">当前账号</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="nickname" label="昵称" min-width="130" show-overflow-tooltip />
      <el-table-column label="角色" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.role === 'admin' ? 'danger' : 'info'" effect="plain">
            {{ row.role === 'admin' ? '管理员' : '学生' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-switch
            :model-value="row.status === 1"
            :loading="switching === row.id"
            :disabled="row.id === auth.user?.id || switching === row.id"
            :title="row.id === auth.user?.id ? '不能禁用自己' : '切换启用/禁用'"
            @change="(value: string | number | boolean) => toggleStatus(row, Boolean(value))"
          />
        </template>
      </el-table-column>
      <el-table-column label="学历" width="96">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" :type="row.education_level === 'unset' ? 'info' : 'success'">
            {{ EDUCATION_LABELS[row.education_level || 'unset'] || '未设置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="学院 / 年级" min-width="150">
        <template #default="{ row }">
          <span v-if="row.college || row.grade">{{ row.college || '—' }} · {{ row.grade || '—' }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="warning" @click="openReset(row)">重置密码</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="total > pageSize" class="ua-pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        background
        @current-change="load"
      />
    </div>

    <!-- 编辑账号 -->
    <el-dialog v-model="editVisible" title="编辑账号" width="430px">
      <div class="ua-form">
        <div class="ua-form-row">
          <label>用户名</label>
          <span class="ua-static">{{ editing?.username }}</span>
        </div>
        <div class="ua-form-row">
          <label>昵称</label>
          <el-input v-model="editForm.nickname" maxlength="50" show-word-limit />
        </div>
        <div class="ua-form-row">
          <label>角色</label>
          <el-select v-model="editForm.role" :disabled="isSelf" style="width: 100%">
            <el-option label="学生" value="student" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </div>
        <div class="ua-form-row">
          <label>状态</label>
          <el-select v-model="editForm.status" :disabled="isSelf" style="width: 100%">
            <el-option label="启用" :value="1" />
            <el-option label="禁用（无法登录）" :value="0" />
          </el-select>
        </div>
        <div class="ua-form-row">
          <label>学历</label>
          <el-select v-model="editForm.education_level" style="width: 100%">
            <el-option label="未设置" value="unset" />
            <el-option label="本科" value="undergraduate" />
            <el-option label="专科" value="junior" />
          </el-select>
        </div>
        <div class="ua-form-row">
          <label>学院</label>
          <el-input v-model="editForm.college" maxlength="64" placeholder="如：人工智能学院" />
        </div>
        <div class="ua-form-row">
          <label>年级</label>
          <el-input v-model="editForm.grade" maxlength="16" placeholder="如：2024" />
        </div>
        <div v-if="isSelf" class="muted ua-tip">
          不能修改自己的角色与状态，避免把系统锁死在无人可管理的状态。
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetVisible" title="重置密码" width="430px">
      <div class="ua-form">
        <div class="ua-form-row">
          <label>账号</label>
          <span class="ua-static">{{ resetting?.username }}（{{ resetting?.nickname || '未设置昵称' }}）</span>
        </div>
        <div class="ua-form-row">
          <label>新密码</label>
          <el-input
            v-model="resetForm.password"
            type="password"
            show-password
            placeholder="至少 8 位，字母/数字/符号任两类"
            maxlength="128"
          />
        </div>
        <div class="ua-form-row">
          <label>确认密码</label>
          <el-input
            v-model="resetForm.confirm"
            type="password"
            show-password
            placeholder="再输入一次"
            maxlength="128"
            @keyup.enter="submitReset"
          />
        </div>
        <div class="muted ua-tip">
          重置后该用户所有已登录设备会立即掉线，需要用新密码重新登录{{
            isSelfReset ? '；你正在重置自己的密码，保存后当前页面会退出登录' : ''
          }}。
        </div>
      </div>
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="resettingBusy" @click="submitReset">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchAdminUsers, resetUserPassword, updateAdminUser } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { AdminUserItem } from '../types'

const auth = useAuthStore()

const rows = ref<AdminUserItem[]>([])
const total = ref(0)
const adminTotal = ref(0)
const loading = ref(false)
const saving = ref(false)
const switching = ref<number | null>(null)
const resettingBusy = ref(false)

const keyword = ref('')
const roleFilter = ref('')
const statusFilter = ref<number | null>(null)
const page = ref(1)
const pageSize = 20

const editVisible = ref(false)
const editing = ref<AdminUserItem | null>(null)
const editForm = ref({
  nickname: '',
  role: 'student',
  status: 1,
  education_level: 'unset',
  college: '',
  grade: '',
})

const EDUCATION_LABELS: Record<string, string> = {
  unset: '未设置',
  undergraduate: '本科',
  junior: '专科',
  general: '通用',
}

const resetVisible = ref(false)
const resetting = ref<AdminUserItem | null>(null)
const resetForm = ref({ password: '', confirm: '' })

const isSelf = computed(() => !!editing.value && editing.value.id === auth.user?.id)
const isSelfReset = computed(() => !!resetting.value && resetting.value.id === auth.user?.id)

/** 快改词时丢弃过期响应，避免"图书"的结果盖到"宿舍"上 */
let requestSeq = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null

function formatTime(value?: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
}

async function load() {
  const seq = ++requestSeq
  loading.value = true
  try {
    const data = await fetchAdminUsers({
      keyword: keyword.value.trim(),
      role: roleFilter.value,
      status: statusFilter.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    if (seq !== requestSeq) return
    rows.value = data.items
    total.value = data.total
    // 翻到空页（例如筛选后数据变少）时自动回退一页，避免停在空白表格
    if (!data.items.length && page.value > 1) {
      page.value = Math.max(1, Math.ceil(data.total / pageSize))
      await load()
      return
    }
    await refreshAdminCount(seq)
  } catch (error: any) {
    if (seq !== requestSeq) return
    ElMessage.error(error?.message || String(error))
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

async function refreshAdminCount(seq: number) {
  try {
    const data = await fetchAdminUsers({ role: 'admin', limit: 1, offset: 0 })
    if (seq === requestSeq) adminTotal.value = data.total
  } catch {
    /* 计数失败不影响主列表 */
  }
}

function reload() {
  page.value = 1
  load()
}

function onKeywordInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(reload, 300)
}

async function toggleStatus(row: AdminUserItem, enabled: boolean) {
  switching.value = row.id
  try {
    await updateAdminUser(row.id, { status: enabled ? 1 : 0 })
    row.status = enabled ? 1 : 0
    ElMessage.success(enabled ? `已启用 ${row.username}` : `已禁用 ${row.username}，该用户已掉线`)
  } catch (error: any) {
    ElMessage.error(error?.message || String(error))
  } finally {
    switching.value = null
  }
}

function openEdit(row: AdminUserItem) {
  editing.value = row
  editForm.value = {
    nickname: row.nickname || '',
    role: row.role || 'student',
    status: row.status ?? 1,
    education_level: row.education_level || 'unset',
    college: row.college || '',
    grade: row.grade || '',
  }
  editVisible.value = true
}

async function submitEdit() {
  if (!editing.value) return
  const target = editing.value
  const payload: {
    nickname?: string
    role?: string
    status?: number
    education_level?: string
    college?: string
    grade?: string
  } = {
    nickname: editForm.value.nickname.trim(),
    education_level: editForm.value.education_level,
    college: editForm.value.college.trim(),
    grade: editForm.value.grade.trim(),
  }
  // 自己只改昵称：角色和状态后端也会拒绝，前端不发这两个字段
  if (!isSelf.value) {
    payload.role = editForm.value.role
    payload.status = editForm.value.status
  }
  saving.value = true
  try {
    await updateAdminUser(target.id, payload)
    editVisible.value = false
    ElMessage.success('已保存')
    if (target.id === auth.user?.id && payload.nickname) {
      await auth.refreshMe()
    }
    await load()
  } catch (error: any) {
    ElMessage.error(error?.message || String(error))
  } finally {
    saving.value = false
  }
}

function openReset(row: AdminUserItem) {
  resetting.value = row
  resetForm.value = { password: '', confirm: '' }
  resetVisible.value = true
}

async function submitReset() {
  if (!resetting.value) return
  const { password, confirm } = resetForm.value
  if (password.length < 8) {
    ElMessage.warning('密码至少 8 位，且需包含字母、数字、符号中的至少两类')
    return
  }
  if (password !== confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  const target = resetting.value
  if (isSelfReset.value) {
    try {
      await ElMessageBox.confirm(
        '重置自己的密码会让你当前登录立即失效，需要重新登录。确定继续？',
        '重置自己的密码',
        { type: 'warning', confirmButtonText: '继续', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  resettingBusy.value = true
  try {
    await resetUserPassword(target.id, password)
    resetVisible.value = false
    if (target.id === auth.user?.id) {
      ElMessage.success('密码已重置，请用新密码重新登录')
      await auth.logout()
      return
    }
    ElMessage.success(`已重置 ${target.username} 的密码`)
    await load()
  } catch (error: any) {
    ElMessage.error(error?.message || String(error))
  } finally {
    resettingBusy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ua-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.ua-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.ua-username {
  margin-right: 8px;
  font-weight: 500;
  color: var(--ink);
}

.ua-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.ua-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.ua-form-row {
  display: grid;
  grid-template-columns: 74px 1fr;
  align-items: center;
  gap: 12px;
}

.ua-form-row > label {
  font-size: 13px;
  color: var(--muted);
}

.ua-static {
  font-size: 13px;
  color: var(--ink);
}

.ua-tip {
  font-size: 12px;
  line-height: 1.7;
  padding: 8px 10px;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 8px;
}
</style>
