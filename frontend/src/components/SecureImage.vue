<template>
  <img
    v-if="src"
    :src="src"
    :style="{ width: size + 'px', height: size + 'px' }"
    class="secure-img"
    title="点击放大"
    @click="openFull"
  />
  <span v-else class="secure-img secure-loading" :style="{ width: size + 'px', height: size + 'px' }" />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchAuthedObjectUrl } from '../api/client'

/**
 * 受保护图片：/api/v1 下的图片接口需要 Authorization 头，
 * <img> 标签带不了，因此先用 fetch 取 blob 转 objectURL 再展示。
 */
const props = defineProps<{ url: string; size?: number }>()
const src = ref('')
let objectUrl = ''

async function load() {
  src.value = ''
  try {
    objectUrl = await fetchAuthedObjectUrl(props.url)
    src.value = objectUrl
  } catch {
    // 加载失败显示占位，不阻塞列表
  }
}

function openFull() {
  if (src.value) window.open(src.value, '_blank', 'noopener')
}

function cleanup() {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
}

watch(() => props.url, load)
onMounted(load)
onBeforeUnmount(cleanup)
</script>

<style scoped>
.secure-img {
  border-radius: 8px;
  border: 1px solid var(--border);
  object-fit: cover;
  cursor: zoom-in;
  background: #f3f5fa;
}

.secure-loading {
  display: inline-block;
}
</style>
