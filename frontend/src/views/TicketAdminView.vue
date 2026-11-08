<template>
  <div class="panel panel-pad ticket-admin" style="height: 100%; overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">工单处理台</div>
        <div class="muted">
          转人工 / 投诉工单的受理与关闭；关闭必须填写处理意见，学生可在“校园服务 → 我的工单”查看
        </div>
      </div>
      <div class="ta-actions">
        <el-input
          v-model="keyword"
          placeholder="工单号 / 标题 / 描述"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 122px" @change="reload">
          <el-option label="待受理" value="open" />
          <el-option label="处理中" value="processing" />
          <el-option label="已关闭" value="closed" />
        </el-select>
        <el-button :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>

    <div class="ta-summary">
      <el-tag effect="plain" round>共 {{ total }} 张工单</el-tag>
      <el-tag v-if="openCount" effect="plain" round type="warning">本页待受理 {{ openCount }}</el-tag>
      <span class="muted" style="font-size: 12px">
        报修单请到“校园服务 → 寝室报修”处理，两类单据状态机互不影响
      </span>
    </div>

    <el-table v-loading="loading" :data="rows" style="width: 100%" empty-text="没有匹配的工单">
      <el-table-column prop="ticket_no" label="工单号" width="170" />
      <el-table-column label="提交人" width="120">
        <template #default="{ row }">{{ row.username || `用户#${row.user_id}` }}</template>
      </el-table-column>
      <el-table-column label="标题" min-width="200">
        <template #default="{ row }">
          <div class="ta-title">{{ row.title }}</div>
          <div class="muted ta-desc">{{ row.description }}</div>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="90">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" type="info">{{ row.category || '其他' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="96">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ row.status_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="160">
        <template #default="{ row }">{{ row.created_at || '-' }}</template>
      </el-table-column>
      <el-table-column label="处理意见" min-width="180">
        <template #default="{ row }">
          <span v-if="row.handler_remark">{{ row.handler_remark }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="188" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'open'"
            link
            type="primary"
            size="small"
            @click="openDialog(row, 'accept')"
          >
            受理
          </el-button>
          <el-button
            v-if="row.status === 'processing'"
            link
            type="primary"
            size="small"
            @click="openDialog(row, 'process')"
          >
            记录进展
          </el-button>
          <el-button
            v-if="row.status !== 'closed'"
            link
            type="danger"
            size="small"
            @click="openDialog(row, 'close')"
          >
            关闭
          </el-button>
          <span v-if="row.status === 'closed'" class="muted" style="font-size: 12px">已结束</span>
        </template>
      </el-table-column>
    </el-table>

    <div class="ta-pager">
      <el-pagination
        layout="prev, pager, next, total"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPageChange"
      />
    </div>

    <el-dialog v-model="dialog" :title="dialogTitle" width="520px" :close-on-click-modal="false">
      <el-form label-width="82px">
        <el-form-item label="工单">
          <span>{{ target?.ticket_no }} · {{ target?.title }}</span>
        </el-form-item>
        <el-form-item v-if="action !== 'accept'" label="处理意见" required>
          <el-select
            v-if="action === 'close'"
            v-model="remark"
            filterable
            allow-create
            default-first-option
            placeholder="选择常见原因，或直接输入"
            style="width: 100%"
          >
            <el-option v-for="reason in CLOSE_PRESETS" :key="reason" :label="reason" :value="reason" />
          </el-select>
          <el-input
            v-else
            v-model="remark"
            type="textarea"
            :rows="2"
            maxlength="500"
            placeholder="如：已联系宿管核实，正在协调"
          />
        </el-form-item>
        <el-form-item v-else label="说明">
          <span class="muted">受理后状态变为“处理中”，学生可在“我的工单”看到进度</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">确认{{ actionLabel }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchTickets, transitionTicket } from '../api/client'
import type { TicketItem } from '../types'

const CLOSE_PRESETS = [
  '已协调相关部门处理完毕',
  '已电话回访，学生表示问题已解决',
  '信息不实或重复提交',
  '非本部门职责范围，已转交',
  '多次联系不上提交人',
]

const loading = ref(false)
const saving = ref(false)
const rows = ref<TicketItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const statusFilter = ref('')
const keyword = ref('')

const dialog = ref(false)
const target = ref<TicketItem | null>(null)
const action = ref<'accept' | 'process' | 'close'>('accept')
const remark = ref('')

const openCount = computed(() => rows.value.filter((r) => r.status === 'open').length)
const actionLabel = computed(() =>
  action.value === 'accept' ? '受理' : action.value === 'process' ? '保存' : '关闭',
)
const dialogTitle = computed(() => {
  if (action.value === 'accept') return '受理工单'
  if (action.value === 'process') return '记录处理进展'
  return '关闭工单'
})

const statusType = (status: string) =>
  status === 'open' ? 'warning' : status === 'processing' ? 'primary' : 'info'

async function load() {
  loading.value = true
  try {
    const data = await fetchTickets({
      status: statusFilter.value,
      keyword: keyword.value.trim(),
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    rows.value = data.items
    total.value = data.total
  } catch (error: any) {
    ElMessage.error(`加载工单失败：${error?.message || error}`)
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  load()
}

function onPageChange(next: number) {
  page.value = next
  load()
}

function openDialog(row: TicketItem, next: 'accept' | 'process' | 'close') {
  target.value = row
  action.value = next
  remark.value = ''
  dialog.value = true
}

async function submit() {
  if (!target.value) return
  const text = remark.value.trim()
  if (action.value === 'close' && text.length < 5) {
    ElMessage.warning('关闭工单必须填写至少 5 个字的处理意见')
    return
  }
  if (action.value === 'process' && !text) {
    ElMessage.warning('记录处理进展时必须填写处理意见')
    return
  }
  saving.value = true
  try {
    await transitionTicket(target.value.id, action.value, text)
    ElMessage.success('已更新')
    dialog.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ta-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.ta-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 0 12px;
  flex-wrap: wrap;
}

.ta-title {
  font-weight: 600;
  color: var(--text-strong, #1f2937);
}

.ta-desc {
  font-size: 12px;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}

.ta-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
</style>
