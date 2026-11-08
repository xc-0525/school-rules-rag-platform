<template>
  <div class="panel panel-pad dashboard" style="height: 100%; overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">运营看板</div>
        <div class="muted">
          数据来自问答记录与反馈，不额外埋点；统计窗口内数据每 60 秒缓存一次
        </div>
      </div>
      <div class="db-actions">
        <el-radio-group v-model="days" @change="load">
          <el-radio-button :label="7">近 7 天</el-radio-button>
          <el-radio-button :label="30">近 30 天</el-radio-button>
          <el-radio-button :label="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 14px"
    />

    <template v-if="data">
      <div class="db-generated muted">数据时间：{{ data.generated_at }} · 统计窗口 {{ data.days }} 天</div>

      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-label">提问量</span>
          <b class="kpi-value">{{ data.scale.questions }}</b>
          <span class="kpi-sub">
            上一周期 {{ data.scale.prev_questions }}
            <em v-if="data.scale.delta_rate !== null" :class="data.scale.delta_rate >= 0 ? 'is-up' : 'is-down'">
              {{ data.scale.delta_rate >= 0 ? '+' : '' }}{{ percent(data.scale.delta_rate) }}
            </em>
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">活跃用户</span>
          <b class="kpi-value">{{ data.scale.active_users }}</b>
          <span class="kpi-sub">会话 {{ data.scale.conversations }} 个 · 人均 {{ data.scale.avg_questions_per_conversation }} 问/会话</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">拒答率</span>
          <b class="kpi-value">{{ percent(data.quality.refusal_rate) }}</b>
          <span class="kpi-sub">回答 {{ data.quality.answers }} 条 · 拒答 {{ data.quality.refused }} 条</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">点赞率</span>
          <b class="kpi-value">{{ percent(data.quality.like_rate) }}</b>
          <span class="kpi-sub">
            反馈 {{ data.quality.feedback_total }} 条 · 赞 {{ data.quality.likes }} / 踩 {{ data.quality.dislikes }}
          </span>
        </div>
      </div>

      <div class="db-efficiency">
        <div class="eff-item"><span>P50 时延</span><b>{{ data.efficiency.p50_ms }} ms</b></div>
        <div class="eff-item"><span>P95 时延</span><b>{{ data.efficiency.p95_ms }} ms</b></div>
        <div class="eff-item"><span>工具调用</span><b>{{ data.efficiency.tool_calls }} 次</b></div>
        <div class="eff-item"><span>工具成功率</span><b>{{ percent(data.efficiency.tool_success_rate) }}</b></div>
      </div>

      <div class="db-section">
        <div class="db-section-head">
          <h3>提问趋势</h3>
          <span class="muted">柱高=提问量，红色段=其中被拒答</span>
        </div>
        <div class="trend-wrap">
          <div class="trend-bars" :style="{ minWidth: `${data.trend.length * 18}px` }">
            <div v-for="point in data.trend" :key="point.date" class="trend-col" :title="`${point.date}：${point.questions} 问 / ${point.refused} 拒答`">
              <div class="trend-bar" :style="{ height: `${barHeight(point.questions)}%` }">
                <span class="trend-refused" :style="{ height: `${refusedHeight(point)}%` }"></span>
              </div>
              <span class="trend-label">{{ shortDate(point.date) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="db-lists">
        <div class="db-section">
          <div class="db-section-head">
            <h3>最答不上来的问题</h3>
            <span class="muted">按出现次数排序，补语料后到反馈工作台标记</span>
          </div>
          <el-table :data="data.top_unmatched" size="small" empty-text="窗口内没有拒答记录">
            <el-table-column type="index" width="46" />
            <el-table-column prop="query_text" label="问题" min-width="240" show-overflow-tooltip />
            <el-table-column prop="count" label="次数" width="70" />
            <el-table-column label="最近出现" width="150">
              <template #default="{ row }">{{ row.last_seen || '-' }}</template>
            </el-table-column>
          </el-table>
        </div>

        <div class="db-section">
          <div class="db-section-head">
            <h3>最近点踩</h3>
            <span class="muted">归因与处理在反馈工作台完成</span>
          </div>
          <el-table :data="data.low_rated" size="small" empty-text="窗口内没有点踩">
            <el-table-column type="index" width="46" />
            <el-table-column label="问题" min-width="220">
              <template #default="{ row }">{{ row.question || '（未找到对应提问）' }}</template>
            </el-table-column>
            <el-table-column label="评论" min-width="150">
              <template #default="{ row }">
                <span :class="{ muted: !row.comment }">{{ row.comment || '未填写' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="150">
              <template #default="{ row }">{{ row.created_at || '-' }}</template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchOverview } from '../api/client'
import type { OverviewData, TrendPoint } from '../types'

const days = ref(7)
const loading = ref(false)
const errorMsg = ref('')
const data = ref<OverviewData | null>(null)

const maxQuestions = computed(() =>
  Math.max(1, ...(data.value?.trend || []).map((point) => point.questions)),
)

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function shortDate(date: string): string {
  return date.slice(5)
}

function barHeight(questions: number): number {
  return Math.max(questions > 0 ? 4 : 1, Math.round((questions / maxQuestions.value) * 100))
}

function refusedHeight(point: TrendPoint): number {
  if (!point.questions) return 0
  return Math.min(100, Math.round((point.refused / point.questions) * 100))
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    data.value = await fetchOverview(days.value)
  } catch (error: any) {
    const text = String(error?.message || error)
    errorMsg.value = text.includes('MySQL') ? '未连接 MySQL，看板暂无数据（内存模式）' : text
    ElMessage.error('看板加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.db-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.db-generated {
  font-size: 12px;
  margin-bottom: 10px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.kpi-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kpi-label {
  font-size: 12px;
  color: var(--muted);
  letter-spacing: 0.04em;
}

.kpi-value {
  font-family: var(--font-serif, Georgia, serif);
  font-size: 30px;
  line-height: 1.15;
  color: var(--ink);
}

.kpi-sub {
  font-size: 12px;
  color: var(--muted);
}

.kpi-sub em {
  font-style: normal;
  margin-left: 4px;
}

.is-up {
  color: var(--brand);
}

.is-down {
  color: #2f7d4f;
}

.db-efficiency {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 14px 0 4px;
}

.eff-item {
  border: 1px dashed var(--border-strong, #d9d2c5);
  border-radius: 10px;
  padding: 8px 14px;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  color: var(--muted);
}

.eff-item b {
  color: var(--ink);
  font-size: 15px;
}

.db-section {
  margin-top: 18px;
}

.db-section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.db-section-head h3 {
  margin: 0;
  font-family: var(--font-serif, Georgia, serif);
  font-size: 16px;
  color: var(--ink);
}

.db-section-head span {
  font-size: 12px;
}

.trend-wrap {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel);
  padding: 14px 12px 8px;
  overflow-x: auto;
}

.trend-bars {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 160px;
}

.trend-col {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  min-width: 12px;
}

.trend-bar {
  width: 100%;
  max-width: 18px;
  min-height: 2px;
  border-radius: 3px 3px 0 0;
  background: linear-gradient(180deg, #c9a2a2, var(--brand));
  position: relative;
  overflow: hidden;
}

.trend-refused {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: rgba(20, 24, 31, 0.55);
}

.trend-label {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
  white-space: nowrap;
}

.db-lists {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

@media (max-width: 1080px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .db-lists {
    grid-template-columns: 1fr;
  }
}
</style>
