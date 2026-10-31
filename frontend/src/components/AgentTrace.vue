<template>
  <div class="agent-trace">
    <div class="trace-head">
      <span class="trace-title">Agent 执行轨迹</span>
      <el-tag size="small" type="info" effect="plain">{{ trace.length }} 条事件</el-tag>
      <el-tag v-if="runtime" size="small" effect="plain">步数 {{ runtime.steps }}</el-tag>
      <el-tag v-if="runtime?.retrieval_round" size="small" type="warning" effect="plain">
        检索扩展 {{ runtime.retrieval_round }} 轮
      </el-tag>
      <el-tag v-if="runtime?.regen_count" size="small" type="danger" effect="plain">
        重新生成 {{ runtime.regen_count }} 次
      </el-tag>
      <el-tag v-if="runtime?.tool_calls" size="small" type="success" effect="plain">
        工具调用 {{ runtime.tool_calls }} 次
      </el-tag>
    </div>

    <el-timeline class="trace-list">
      <el-timeline-item
        v-for="(item, index) in rendered"
        :key="index"
        :timestamp="item.time"
        :color="item.color"
      >
        <div v-if="item.type === 'tool'" class="trace-row">
          <el-tag size="small" type="success" effect="plain">工具</el-tag>
          <span class="trace-node">{{ item.tool }}</span>
          <span class="trace-text">{{ item.argsText }}</span>
          <el-tag v-if="item.running" size="small" type="warning" effect="plain">调用中</el-tag>
          <el-tag v-else size="small" :type="item.success ? 'success' : 'danger'" effect="plain">
            {{ item.success ? '成功' : '失败' }}
          </el-tag>
          <span v-if="item.summary" class="trace-text">{{ item.summary }}</span>
          <span v-if="item.latency_ms !== null && item.latency_ms !== undefined" class="trace-latency">
            {{ item.latency_ms }}ms
          </span>
        </div>
        <div v-else-if="item.type === 'route'" class="trace-row">
          <el-tag size="small" :type="item.loop ? 'warning' : 'success'" effect="plain">
            {{ item.loop ? '循环' : '选择' }}
          </el-tag>
          <span class="trace-text">{{ item.text }}</span>
        </div>
        <div v-else class="trace-row">
          <el-tag size="small" :type="item.running ? 'warning' : 'info'" effect="plain">
            {{ item.running ? '运行中' : '完成' }}
          </el-tag>
          <span class="trace-node">{{ item.label || item.node }}</span>
          <el-tag v-if="item.control === 'parallel'" size="small" type="success" effect="plain">并行</el-tag>
          <el-tag v-else-if="item.control === 'loop'" size="small" type="warning" effect="plain">循环</el-tag>
          <el-tag v-else-if="item.control === 'selection'" size="small" type="primary" effect="plain">选择</el-tag>
          <span v-if="item.latency_ms !== null && item.latency_ms !== undefined" class="trace-latency">
            {{ item.latency_ms }}ms
          </span>
        </div>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RuntimeInfo, TraceEvent } from '../types'

const props = defineProps<{
  trace: TraceEvent[]
  runtime?: RuntimeInfo | null
}>()

interface RenderedItem {
  type: 'node' | 'route' | 'tool'
  node?: string
  label?: string
  control?: string
  kind?: string
  running?: boolean
  latency_ms?: number | null
  text?: string
  loop?: boolean
  color?: string
  time?: string
  tool?: string
  argsText?: string
  success?: boolean
  summary?: string
}

const rendered = computed<RenderedItem[]>(() => {
  const items: RenderedItem[] = []
  const running = new Map<string, RenderedItem>()

  for (const event of props.trace || []) {
    const time = event.ts_ms !== undefined ? `+${event.ts_ms}ms` : ''
    if (event.phase === 'tool_start') {
      items.push({
        type: 'tool',
        tool: event.tool,
        argsText: event.arguments ? JSON.stringify(event.arguments) : '',
        running: true,
        color: '#409eff',
        time,
      })
    } else if (event.phase === 'tool_end') {
      items.push({
        type: 'tool',
        tool: event.tool,
        argsText: event.arguments ? JSON.stringify(event.arguments) : '',
        success: !!event.success,
        summary: event.summary,
        latency_ms: event.latency_ms ?? null,
        color: event.success ? '#67c23a' : '#f56c6c',
        time,
      })
    } else if (event.phase === 'start') {
      const item: RenderedItem = {
        type: 'node',
        node: event.node,
        label: event.label,
        control: event.control,
        kind: event.kind,
        running: true,
        latency_ms: null,
        color: '#409eff',
        time,
      }
      items.push(item)
      if (event.node) running.set(event.node, item)
    } else if (event.phase === 'end') {
      const item = event.node ? running.get(event.node) : undefined
      if (item) {
        item.running = false
        item.latency_ms = event.latency_ms ?? null
        item.color = '#67c23a'
      }
    } else if (event.phase === 'route') {
      const reason = event.reason || ''
      const loop = /第|轮|重新|循环|次数|未通过|不足|扩/.test(reason)
      items.push({
        type: 'route',
        text: `${event.from} → ${event.to ?? '结束'}${reason ? `（${reason}）` : ''}`,
        loop,
        color: loop ? '#e6a23c' : '#67c23a',
        time,
      })
    }
  }
  return items
})
</script>
