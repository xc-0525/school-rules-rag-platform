<template>
  <div class="panel panel-pad" style="height: 100%; overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">校园服务文档管理</div>
        <div class="muted">上传后自动解析「第X章 / 第N条」，写入 MySQL 元数据、Qdrant 向量和 Neo4j 图谱</div>
      </div>
      <div class="document-actions">
        <template v-if="auth.isAdmin">
          <el-input v-model="title" placeholder="自定义标题（可选）" clearable style="width: 200px" />
          <el-checkbox v-model="replace">同名覆盖</el-checkbox>
          <el-button type="primary" :loading="uploading" @click="pickFile">上传文档</el-button>
        </template>
        <el-tag v-else type="info" effect="plain">学生账号只读</el-tag>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <input
          ref="fileInput"
          type="file"
          accept=".md,.txt,.pdf"
          style="display: none"
          @change="onFileChange"
        />
      </div>
    </div>

    <div class="doc-summary">
      <el-tag effect="plain" round>共 {{ documents.length }} 篇</el-tag>
      <el-tag effect="plain" round type="success">{{ totalChunks }} 条条款</el-tag>
      <el-tag v-if="typeSummary" effect="plain" round type="info">{{ typeSummary }}</el-tag>
      <span class="muted" style="font-size: 12px">
        点任意一行右侧「详情」可查看切分后的条款原文——问答只会在这些条款里找答案
      </span>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>上传说明</template>
      <div class="upload-hint">
        支持 <b>.md / .txt / 文字版 .pdf</b>；上传走原始字节流（不做 base64），服务端边收边落盘。<br />
        后端需要以真实模式运行（<code>VECTOR_MODE=qdrant</code> + Neo4j 已启动），首次入库会预热 BGE，可能较慢。<br />
        扫描件 PDF 需要额外安装 OCR 引擎（<code>rapidocr_onnxruntime</code>），否则会返回"需要 OCR"的提示。
      </div>
    </el-alert>

    <el-table v-loading="loading" :data="documents" stripe style="width: 100%">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="file_name" label="文件名（点击预览）" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <el-link type="primary" :underline="false" @click="preview(row)">
            {{ row.file_name }}
          </el-link>
        </template>
      </el-table-column>
      <el-table-column prop="file_type" label="类型" width="90">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ (row.file_type || '').toUpperCase() }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="条款数" width="90" align="center" />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'done' ? 'success' : 'warning'">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="上传时间" width="180" />
      <el-table-column v-if="auth.isAdmin" label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="danger" :disabled="deletingId === row.id" @click="remove(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <div class="muted" style="padding: 20px 0">还没有文档，先上传一份校规文件。</div>
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteDocument, fetchDocumentPreviewUrl, fetchDocuments, uploadDocumentRaw } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { DocumentItem } from '../types'

const auth = useAuthStore()
const documents = ref<DocumentItem[]>([])

const totalChunks = computed(() =>
  documents.value.reduce((sum, item) => sum + (item.chunk_count || 0), 0),
)
const typeSummary = computed(() => {
  const counter = new Map<string, number>()
  documents.value.forEach((item) => {
    const key = (item.file_type || 'md').toLowerCase()
    counter.set(key, (counter.get(key) || 0) + 1)
  })
  return Array.from(counter.entries())
    .map(([type, count]) => `${type} ${count}`)
    .join(' · ')
})
const loading = ref(false)
const uploading = ref(false)
const deletingId = ref<number | null>(null)
const title = ref('')
const replace = ref(true)
const fileInput = ref<HTMLInputElement | null>(null)

function pickFile() {
  if (!auth.isAdmin) {
    ElMessage.warning('仅管理员可上传文档')
    return
  }
  fileInput.value?.click()
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  await upload(file)
}

async function upload(file: File) {
  if (!auth.isAdmin) {
    ElMessage.warning('仅管理员可上传文档')
    return
  }
  uploading.value = true
  const lower = file.name.toLowerCase()
  try {
    if (!lower.endsWith('.pdf') && !lower.endsWith('.md') && !lower.endsWith('.txt')) {
      ElMessage.warning('只支持 .md / .txt / .pdf')
      return
    }
    // 统一走原始字节流：不再 FileReader → base64（20MB 文件可省掉近百兆内存）
    const result = await uploadDocumentRaw(file, {
      title: title.value.trim() || undefined,
      replace: replace.value,
    })
    ElMessage.success(`入库成功：#${result.document_id} ${result.title}，共 ${result.chunk_count} 条`)
    title.value = ''
    await load()
  } catch (error: any) {
    ElMessage.error(`入库失败：${error?.message || error}`)
  } finally {
    uploading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    documents.value = await fetchDocuments()
  } catch (error: any) {
    ElMessage.error(`加载文档失败：${error?.message || error}`)
  } finally {
    loading.value = false
  }
}

/** 在线预览原件：fetch 带 token 拉字节流 → objectURL 新窗口打开（只读） */
const previewingId = ref(0)
async function preview(row: DocumentItem) {
  if (previewingId.value) return
  previewingId.value = row.id
  try {
    const url = await fetchDocumentPreviewUrl(row.id)
    window.open(url, '_blank', 'noopener')
    // 延迟回收，给新窗口加载留时间
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (error: any) {
    if (error?.unsupported) {
      ElMessageBox.alert(
        `「${row.file_name}」为 ${error.suffix || '该'} 类型，浏览器无法内嵌预览，请下载后查看。`,
        '暂不支持在线预览',
        { type: 'info' },
      )
    } else {
      ElMessage.error(`预览失败：${error?.message || error}`)
    }
  } finally {
    previewingId.value = 0
  }
}

async function remove(row: DocumentItem) {
  if (!auth.isAdmin) {
    ElMessage.warning('仅管理员可删除文档')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.title}」吗？会同时清理 Qdrant 向量和 Neo4j 图谱。`,
      '删除文档',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deletingId.value = row.id
  try {
    await deleteDocument(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (error: any) {
    ElMessage.error(`删除失败：${error?.message || error}`)
  } finally {
    deletingId.value = null
  }
}

onMounted(load)
</script>
