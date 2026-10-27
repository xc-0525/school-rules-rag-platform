<template>
  <el-container class="app-shell">
    <el-aside width="228px" class="app-aside">
      <div class="brand">
        <div class="brand-logo">规</div>
        <div>
          <div class="brand-title">校纪校规问答</div>
          <div class="brand-sub">RAG · 多智能体协作</div>
        </div>
      </div>

      <el-menu :default-active="activeMenu" router class="app-menu">
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
        <el-menu-item index="/history">
          <el-icon><Clock /></el-icon>
          <span>会话历史</span>
        </el-menu-item>
        <el-menu-item index="/documents">
          <el-icon><Document /></el-icon>
          <span>文档管理</span>
        </el-menu-item>
        <el-menu-item index="/about">
          <el-icon><InfoFilled /></el-icon>
          <span>关于与状态</span>
        </el-menu-item>
      </el-menu>

      <div class="aside-footer">
        <div class="mode-line">
          <span>向量</span><b>{{ health?.modes?.vector || '-' }}</b>
        </div>
        <div class="mode-line">
          <span>图谱</span><b>{{ health?.modes?.graph || '-' }}</b>
        </div>
        <div class="mode-line">
          <span>存储</span><b>{{ health?.modes?.persistence || '-' }}</b>
        </div>
        <div v-if="health && !health.vector_ready" class="mode-warn">向量未预热完成</div>
      </div>
    </el-aside>

    <el-container>
      <el-header class="app-header">
        <div class="page-title">{{ route.meta.title || '校纪校规问答' }}</div>
        <div class="header-right">
          <el-tag v-if="health" :type="health.status === 'ok' ? 'success' : 'danger'" effect="light" round>
            后端 {{ health.status }}
          </el-tag>
          <el-tag v-else type="danger" effect="light" round>后端未连接</el-tag>
          <el-tag v-if="health?.llm_configured" type="info" effect="plain" round>DeepSeek</el-tag>
          <el-tag v-if="health" type="success" effect="plain" round>
            {{ health.graph.agent_node_count }} Agent 节点
          </el-tag>
          <el-button link type="primary" @click="refresh">刷新</el-button>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ChatDotRound, Clock, Document, InfoFilled } from '@element-plus/icons-vue'
import { fetchHealth } from './api/client'
import type { HealthInfo } from './types'

const route = useRoute()
const health = ref<HealthInfo | null>(null)
const activeMenu = computed(() => route.path)

async function refresh() {
  try {
    health.value = await fetchHealth()
  } catch {
    health.value = null
  }
}

onMounted(refresh)
</script>
