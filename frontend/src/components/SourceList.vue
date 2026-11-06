<template>
  <div v-if="sources && sources.length" class="source-list">
    <button
      type="button"
      class="source-title"
      :aria-expanded="!collapsed"
      @click="collapsed = !collapsed"
    >
      <span class="source-caret" :class="{ open: !collapsed }">▶</span>
      <span>
        {{
          refused
            ? `已检索到的相近条款（${sources.length} 条，未采用）`
            : `依据来源（${sources.length} 条）`
        }}
      </span>
      <span class="source-hint">{{ collapsed ? '展开' : '收起' }}</span>
    </button>
    <div v-show="!collapsed">
      <div v-for="(source, index) in sources" :key="index" class="source-card">
        <div class="source-head">
          <el-tag
            size="small"
            :type="source.type === 'graph' ? 'warning' : source.type === 'tool' ? 'success' : 'primary'"
            effect="plain"
          >
            {{ source.type === 'graph' ? '图谱' : source.type === 'tool' ? '工具' : '向量' }}
          </el-tag>
          <span class="source-name">{{ source.title }}</span>
        </div>
        <div class="source-detail">{{ source.detail }}</div>
        <div
          :ref="(el) => setSnippetRef(el, index)"
          class="source-snippet"
          :class="{ 'is-expanded': expanded[index] }"
        >
          {{ source.snippet }}
        </div>
        <button
          v-if="hasOverflow[index]"
          type="button"
          class="snippet-toggle"
          @click="toggle(index)"
        >
          {{ expanded[index] ? '收起' : '展开全文' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import type { SourceItem } from '../types'

const props = defineProps<{ sources?: SourceItem[] | null; refused?: boolean }>()

const expanded = ref<Record<number, boolean>>({})
/** 整个来源区块的折叠：默认展开，答辩时一键收起 */
const collapsed = ref(false)
/** 只有真的被 3 行裁掉才给展开入口，避免短条款出现无效按钮 */
const hasOverflow = ref<Record<number, boolean>>({})
const snippetEls = new Map<number, HTMLElement>()

function setSnippetRef(el: unknown, index: number) {
  if (el) snippetEls.set(index, el as HTMLElement)
  else snippetEls.delete(index)
}

function measureOverflow() {
  const next: Record<number, boolean> = {}
  snippetEls.forEach((el, index) => {
    next[index] = el.scrollHeight - el.clientHeight > 2
  })
  hasOverflow.value = next
}

function toggle(index: number) {
  expanded.value = { ...expanded.value, [index]: !expanded.value[index] }
}

onMounted(() => nextTick(measureOverflow))

watch(
  () => props.sources,
  () => {
    expanded.value = {}
    snippetEls.clear()
    nextTick(measureOverflow)
  },
  { deep: true },
)
</script>
