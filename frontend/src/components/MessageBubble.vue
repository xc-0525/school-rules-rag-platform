<template>
  <div :class="['message-row', message.role]">
    <el-avatar :size="34" class="message-avatar" :class="message.role === 'user' ? 'user-avatar' : 'ai-avatar'">
      {{ message.role === 'user' ? '我' : 'AI' }}
    </el-avatar>

    <div class="message-body">
      <div v-if="message.role === 'assistant'" class="message-meta">
        <el-tag v-if="message.cached" size="small" type="info" effect="plain">缓存命中</el-tag>
        <el-tag v-if="message.refused" size="small" type="warning" effect="plain">防幻觉拒答</el-tag>
        <el-tag v-if="message.degraded" size="small" type="danger" effect="plain">降级回答</el-tag>
        <span v-if="message.latency_ms" class="meta-text">耗时 {{ message.latency_ms }}ms</span>
      </div>

      <div v-if="message.role === 'user'" class="user-text">{{ message.content }}</div>

      <template v-else>
        <div v-if="!message.content && message.pending" class="typing">
          正在召回证据并生成回答<span class="dot">.</span>
        </div>
        <div
          v-else
          class="assistant-text markdown-body"
          v-html="renderMarkdown(message.content)"
        ></div>
        <div v-if="message.pending && message.content" class="typing">
          生成中<span class="dot">.</span>
        </div>
      </template>

      <div v-if="message.error" class="error-text">出错了：{{ message.error }}</div>

      <AgentTrace
        v-if="message.role === 'assistant' && message.trace && message.trace.length"
        :trace="message.trace"
        :runtime="message.runtime"
      />
      <SourceList v-if="message.role === 'assistant'" :sources="message.sources" />

      <div v-if="message.role === 'assistant' && !message.pending && !message.error && message.id" class="feedback-row">
        <span>这个回答有帮助吗？</span>
        <el-button text size="small" :type="rating === 1 ? 'success' : ''" @click="submitFeedback(1)">
          👍 有帮助
        </el-button>
        <el-button text size="small" :type="rating === -1 ? 'danger' : ''" @click="submitFeedback(-1)">
          👎 没帮助
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { Message } from '../types'
import { renderMarkdown } from '../utils/markdown'
import AgentTrace from './AgentTrace.vue'
import SourceList from './SourceList.vue'

const props = defineProps<{ message: Message }>()
const emit = defineEmits<{ (event: 'feedback', rating: number): void }>()
const rating = ref(0)

function submitFeedback(value: number) {
  rating.value = value
  emit('feedback', value)
}
</script>
