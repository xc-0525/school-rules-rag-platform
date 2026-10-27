<template>
  <div class="panel panel-pad" style="height: calc(100vh - 60px - 36px); overflow-y: auto">
    <div class="detail-head">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">校规文档管理</div>
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

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>上传说明</template>
      <div class="upload-hint">
        支持 <b>.md / .txt / 文字版 .pdf</b>；PDF 前端会读成 base64 上传，不依赖 multipart。<br />
        后端需要以真实模式运行（<code>VECTOR_MODE=qdrant</code> + Neo4j 已启动），首次入库会预热 BGE，可能较慢。<br />
        扫描件 PDF 需要额外安装 OCR 引擎（<code>rapidocr_onnxruntime</code>），否则会返回"需要 OCR"的提示。
      </div>
    </el-alert>

    <el-table v-loading="loading" :data="documents" stripe style="width: 100%">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
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
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteDocument, fetchDocuments, uploadDocument } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { DocumentItem } from '../types'

const auth = useAuthStore()
const documents = ref<DocumentItem[]>([])
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

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize))
  }
  return btoa(binary)
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
    const common = {
      filename: file.name,
      title: title.value.trim() || undefined,
      replace: replace.value,
    }
    let result
    if (lower.endsWith('.pdf')) {
      const buffer = await file.arrayBuffer()
      result = await uploadDocument({
        ...common,
        content_base64: arrayBufferToBase64(buffer),
      })
    } else if (lower.endsWith('.md') || lower.endsWith('.txt')) {
      const text = await file.text()
      result = await uploadDocument({ ...common, content: text })
    } else {
      ElMessage.warning('只支持 .md / .txt / .pdf')
      return
    }
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
