<template>
  <div class="panel panel-pad feedback-admin" style="height: 100%; overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">反馈与未命中工作台</div>
        <div class="muted">
          点踩归因走到“已补语料 → 已复核”；拒答问题按归一化聚合，补完语料后标记处理状态
        </div>
      </div>
      <div class="fa-actions">
        <el-button :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    />

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <!-- ================= 反馈 ================= -->
      <el-tab-pane :label="`反馈${feedbackTotal ? `（${feedbackTotal}）` : ''}`" name="feedback">
        <div class="fa-filters">
          <el-select v-model="ratingFilter" placeholder="全部评分" style="width: 118px" @change="reloadFeedback">
            <el-option label="全部评分" :value="0" />
            <el-option label="点踩" :value="-1" />
            <el-option label="点赞" :value="1" />
          </el-select>
          <el-select v-model="feedbackStatus" clearable placeholder="全部状态" style="width: 122px" @change="reloadFeedback">
            <el-option label="待归因" value="pending" />
            <el-option label="已归因" value="attributed" />
            <el-option label="已补语料" value="resolved" />
            <el-option label="已复核" value="verified" />
          </el-select>
          <el-input
            v-model="feedbackKeyword"
            placeholder="问题 / 评论关键词"
            clearable
            style="width: 210px"
            @keyup.enter="reloadFeedback"
            @clear="reloadFeedback"
          />
        </div>

        <el-table v-loading="loading" :data="feedbackRows" empty-text="没有匹配的反馈" style="width: 100%">
          <el-table-column label="时间" width="158">
            <template #default="{ row }">{{ row.created_at || '-' }}</template>
          </el-table-column>
          <el-table-column label="评分" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.rating > 0 ? 'success' : 'danger'" effect="plain">
                {{ row.rating_label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="问题" min-width="220">
            <template #default="{ row }">
              <div class="fa-question">{{ row.question || '（未找到对应提问）' }}</div>
              <div v-if="row.comment" class="muted fa-comment">用户评论：{{ row.comment }}</div>
            </template>
          </el-table-column>
          <el-table-column label="回答摘要" min-width="220">
            <template #default="{ row }">
              <div class="fa-answer" :title="row.answer || ''">{{ row.answer || '-' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="归因" width="102">
            <template #default="{ row }">
              <el-tag v-if="row.attribution" size="small" effect="plain" type="warning">{{ row.attribution }}</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="feedbackStatusType(row.handle_status)">{{ row.status_label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="处理意见" min-width="150">
            <template #default="{ row }">
              <span v-if="row.handle_remark">{{ row.handle_remark }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.handle_status === 'pending'"
                link
                type="primary"
                size="small"
                @click="openFeedbackDialog(row, 'attribute')"
              >
                归因
              </el-button>
              <el-button
                v-else-if="row.handle_status === 'attributed'"
                link
                type="primary"
                size="small"
                @click="openFeedbackDialog(row, 'resolve')"
              >
                标记已补语料
              </el-button>
              <el-button
                v-else-if="row.handle_status === 'resolved'"
                link
                type="success"
                size="small"
                @click="verifyFeedback(row)"
              >
                复核通过
              </el-button>
              <span v-else class="muted" style="font-size: 12px">已闭环</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="fa-pager">
          <el-pagination
            layout="prev, pager, next, total"
            :total="feedbackTotal"
            :page-size="pageSize"
            :current-page="feedbackPage"
            @current-change="onFeedbackPage"
          />
        </div>
      </el-tab-pane>

      <!-- ================= 未命中 ================= -->
      <el-tab-pane :label="`未命中问题${unmatchedTotal ? `（${unmatchedTotal}）` : ''}`" name="unmatched">
        <div class="fa-filters">
          <el-select v-model="days" style="width: 118px" @change="reloadUnmatched">
            <el-option label="近 7 天" :value="7" />
            <el-option label="近 30 天" :value="30" />
            <el-option label="近 90 天" :value="90" />
          </el-select>
          <el-select v-model="unmatchedStatus" clearable placeholder="全部状态" style="width: 122px" @change="reloadUnmatched">
            <el-option label="待处理" value="pending" />
            <el-option label="已补语料" value="resolved" />
            <el-option label="已忽略" value="ignored" />
          </el-select>
          <el-input
            v-model="unmatchedKeyword"
            placeholder="问题关键词"
            clearable
            style="width: 210px"
            @keyup.enter="reloadUnmatched"
            @clear="reloadUnmatched"
          />
          <span class="muted" style="font-size: 12px">按归一化文本聚合，同一问题不同标点只算一条</span>
        </div>

        <el-table v-loading="loading" :data="unmatchedRows" empty-text="没有未命中问题" style="width: 100%">
          <el-table-column label="问题" min-width="260">
            <template #default="{ row }">
              <div class="fa-question">{{ row.query_text }}</div>
              <div v-if="row.remark" class="muted fa-comment">备注：{{ row.remark }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="count" label="次数" width="80" sortable />
          <el-table-column label="首次出现" width="158">
            <template #default="{ row }">{{ row.first_seen || '-' }}</template>
          </el-table-column>
          <el-table-column label="最近出现" width="158">
            <template #default="{ row }">{{ row.last_seen || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="unmatchedType(row.status)">{{ row.status_label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status !== 'resolved'"
                link
                type="primary"
                size="small"
                @click="openMarkDialog(row, 'resolved')"
              >
                标记已补语料
              </el-button>
              <el-button
                v-if="row.status !== 'ignored'"
                link
                type="info"
                size="small"
                @click="openMarkDialog(row, 'ignored')"
              >
                忽略
              </el-button>
              <el-button v-if="row.status !== 'pending'" link size="small" @click="resetMark(row)">
                恢复待处理
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="fa-pager">
          <el-pagination
            layout="prev, pager, next, total"
            :total="unmatchedTotal"
            :page-size="pageSize"
            :current-page="unmatchedPage"
            @current-change="onUnmatchedPage"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 归因 / 已补语料弹窗 -->
    <el-dialog v-model="feedbackDialog" :title="feedbackDialogTitle" width="500px" :close-on-click-modal="false">
      <el-form label-width="82px">
        <el-form-item label="问题">
          <span>{{ target?.question || '（未找到对应提问）' }}</span>
        </el-form-item>
        <el-form-item v-if="feedbackAction === 'attribute'" label="归因" required>
          <el-select v-model="attribution" placeholder="选择归因" style="width: 100%">
            <el-option v-for="item in ATTRIBUTIONS" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item :label="feedbackAction === 'attribute' ? '备注' : '补充说明'" :required="feedbackAction === 'resolve'">
          <el-input
            v-model="feedbackRemark"
            type="textarea"
            :rows="3"
            maxlength="500"
            :placeholder="feedbackAction === 'attribute' ? '可选，补充判断依据' : '写清补了哪份文档/哪一条款'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="feedbackDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitFeedback">确认</el-button>
      </template>
    </el-dialog>

    <!-- 未命中标记弹窗 -->
    <el-dialog v-model="markDialog" :title="markStatus === 'resolved' ? '标记已补语料' : '忽略该问题'" width="480px" :close-on-click-modal="false">
      <el-form label-width="72px">
        <el-form-item label="问题">
          <span>{{ markTarget?.query_text }}</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="markRemark"
            type="textarea"
            :rows="2"
            maxlength="500"
            :placeholder="markStatus === 'resolved' ? '补了哪份文档/条款' : '忽略原因'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="markDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitMark">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  fetchAdminFeedback,
  fetchUnmatched,
  markUnmatched,
  transitionFeedback,
} from '../api/client'
import type { FeedbackItem, UnmatchedItem } from '../types'

const ATTRIBUTIONS = ['语料缺失', '答案错误', '引用错误', '问题不清']
const pageSize = 20

const tab = ref('feedback')
const loading = ref(false)
const saving = ref(false)
const errorMsg = ref('')

const feedbackRows = ref<FeedbackItem[]>([])
const feedbackTotal = ref(0)
const feedbackPage = ref(1)
const ratingFilter = ref(0)
const feedbackStatus = ref('')
const feedbackKeyword = ref('')

const unmatchedRows = ref<UnmatchedItem[]>([])
const unmatchedTotal = ref(0)
const unmatchedPage = ref(1)
const days = ref(30)
const unmatchedStatus = ref('')
const unmatchedKeyword = ref('')

const feedbackDialog = ref(false)
const target = ref<FeedbackItem | null>(null)
const feedbackAction = ref<'attribute' | 'resolve'>('attribute')
const attribution = ref('')
const feedbackRemark = ref('')

const markDialog = ref(false)
const markTarget = ref<UnmatchedItem | null>(null)
const markStatus = ref<'resolved' | 'ignored'>('resolved')
const markRemark = ref('')

const feedbackDialogTitle = computed(() =>
  feedbackAction.value === 'attribute' ? '标记归因' : '标记已补语料',
)

const feedbackStatusType = (status: string) =>
  status === 'pending' ? 'warning' : status === 'attributed' ? 'primary' : status === 'resolved' ? 'success' : 'info'
const unmatchedType = (status: string) =>
  status === 'pending' ? 'warning' : status === 'resolved' ? 'success' : 'info'

function captureError(error: any) {
  const text = String(error?.message || error)
  errorMsg.value = text.includes('MySQL') ? '未连接 MySQL，工作台暂无数据（内存模式）' : text
}

async function loadFeedback() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await fetchAdminFeedback({
      rating: ratingFilter.value,
      status: feedbackStatus.value,
      keyword: feedbackKeyword.value.trim(),
      limit: pageSize,
      offset: (feedbackPage.value - 1) * pageSize,
    })
    feedbackRows.value = data.items
    feedbackTotal.value = data.total
  } catch (error: any) {
    feedbackRows.value = []
    feedbackTotal.value = 0
    captureError(error)
  } finally {
    loading.value = false
  }
}

async function loadUnmatched() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await fetchUnmatched({
      days: days.value,
      status: unmatchedStatus.value,
      keyword: unmatchedKeyword.value.trim(),
      limit: pageSize,
      offset: (unmatchedPage.value - 1) * pageSize,
    })
    unmatchedRows.value = data.items
    unmatchedTotal.value = data.total
  } catch (error: any) {
    unmatchedRows.value = []
    unmatchedTotal.value = 0
    captureError(error)
  } finally {
    loading.value = false
  }
}

function reload() {
  if (tab.value === 'feedback') reloadFeedback()
  else reloadUnmatched()
}

function reloadFeedback() {
  feedbackPage.value = 1
  loadFeedback()
}

function reloadUnmatched() {
  unmatchedPage.value = 1
  loadUnmatched()
}

function onTabChange(name: string | number) {
  if (name === 'feedback') loadFeedback()
  else loadUnmatched()
}

function onFeedbackPage(next: number) {
  feedbackPage.value = next
  loadFeedback()
}

function onUnmatchedPage(next: number) {
  unmatchedPage.value = next
  loadUnmatched()
}

function openFeedbackDialog(row: FeedbackItem, action: 'attribute' | 'resolve') {
  target.value = row
  feedbackAction.value = action
  attribution.value = row.attribution || ''
  feedbackRemark.value = ''
  feedbackDialog.value = true
}

async function submitFeedback() {
  if (!target.value) return
  if (feedbackAction.value === 'attribute' && !attribution.value) {
    ElMessage.warning('请选择归因')
    return
  }
  if (feedbackAction.value === 'resolve' && feedbackRemark.value.trim().length < 2) {
    ElMessage.warning('请写明补了什么（至少 2 个字）')
    return
  }
  saving.value = true
  try {
    await transitionFeedback(target.value.id, feedbackAction.value, {
      attribution: attribution.value,
      remark: feedbackRemark.value.trim(),
    })
    ElMessage.success('已更新')
    feedbackDialog.value = false
    await loadFeedback()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    saving.value = false
  }
}

async function verifyFeedback(row: FeedbackItem) {
  try {
    await transitionFeedback(row.id, 'verify', {})
    ElMessage.success('已复核，闭环完成')
    await loadFeedback()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

function openMarkDialog(row: UnmatchedItem, status: 'resolved' | 'ignored') {
  markTarget.value = row
  markStatus.value = status
  markRemark.value = row.remark || ''
  markDialog.value = true
}

async function submitMark() {
  if (!markTarget.value) return
  saving.value = true
  try {
    await markUnmatched(markTarget.value.query_hash, {
      status: markStatus.value,
      remark: markRemark.value.trim(),
      query_text: markTarget.value.query_text,
    })
    ElMessage.success('已标记')
    markDialog.value = false
    await loadUnmatched()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    saving.value = false
  }
}

async function resetMark(row: UnmatchedItem) {
  try {
    await markUnmatched(row.query_hash, {
      status: 'pending',
      remark: '',
      query_text: row.query_text,
    })
    ElMessage.success('已恢复待处理')
    await loadUnmatched()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

onMounted(loadFeedback)
</script>

<style scoped>
.fa-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.fa-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.fa-question {
  font-weight: 600;
  color: var(--text-strong, #1f2937);
  line-height: 1.45;
}

.fa-comment {
  font-size: 12px;
  margin-top: 3px;
}

.fa-answer {
  font-size: 12px;
  color: var(--muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.fa-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
</style>
