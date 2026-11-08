<template>
  <el-dialog
    :model-value="modelValue"
    title="这条回答哪里没帮上忙？"
    width="460px"
    @update:model-value="(value: boolean) => emit('update:modelValue', value)"
  >
    <div class="fb-hint muted">选择最接近的原因，帮助定位是语料缺失、检索跑偏还是表述问题。</div>
    <el-radio-group v-model="picked" class="fb-reasons">
      <el-radio v-for="reason in REASONS" :key="reason" :value="reason">{{ reason }}</el-radio>
    </el-radio-group>
    <el-input
      v-model="extra"
      type="textarea"
      :rows="2"
      maxlength="200"
      show-word-limit
      placeholder="补充说明（选填，例如：问的是专科口径，回答的是本科）"
    />
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="danger" :loading="submitting" @click="confirm">提交反馈</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{ modelValue: boolean; submitting?: boolean }>()
const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'confirm', comment: string): void
}>()

/** 低分原因预设：与 docs/改进建议文档 M-03 的归因分类对齐，便于运营工作台聚合 */
const REASONS = [
  '答非所问',
  '条款引用不对',
  '信息已过时',
  '内容不完整',
  '表述太难懂',
  '其他（请在下方补充）',
]

const picked = ref(REASONS[0])
const extra = ref('')

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      picked.value = REASONS[0]
      extra.value = ''
    }
  },
)

function confirm() {
  const note = extra.value.trim()
  const comment = note ? `${picked.value}｜${note}` : picked.value
  emit('confirm', comment)
}
</script>

<style scoped>
.fb-hint {
  margin-bottom: 12px;
  font-size: 12px;
  line-height: 1.7;
}

.fb-reasons {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 12px;
  margin-bottom: 14px;
}
</style>
