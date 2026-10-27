<template>
  <div v-if="sources && sources.length" class="source-list">
    <div class="source-title">依据来源（{{ sources.length }} 条）</div>
    <div v-for="(source, index) in sources" :key="index" class="source-card">
      <div class="source-head">
        <el-tag
          size="small"
          :type="source.type === 'graph' ? 'warning' : 'primary'"
          effect="plain"
        >
          {{ source.type === 'graph' ? '图谱' : '向量' }}
        </el-tag>
        <span class="source-name">{{ source.title }}</span>
      </div>
      <div class="source-detail">{{ source.detail }}</div>
      <div class="source-snippet">{{ source.snippet }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { SourceItem } from '../types'

defineProps<{ sources?: SourceItem[] | null }>()
</script>
