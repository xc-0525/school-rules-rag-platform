<template>
  <router-view v-if="isLoginPage" />

  <el-container v-else class="app-shell">
    <el-aside width="236px" class="app-aside">
      <div class="brand">
        <img class="brand-emblem" :src="emblem" alt="武昌理工学院校徽" />
        <div class="brand-school">武昌理工学院</div>
        <div class="brand-en">WUCHANG UNIVERSITY OF TECHNOLOGY</div>
        <div class="brand-rule"></div>
        <div class="brand-title">校园服务智能问答</div>
        <div class="brand-sub">RAG · 多智能体协作</div>
      </div>

      <el-menu :default-active="activeMenu" router class="app-menu">
        <li class="menu-group">智能问答</li>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
        <el-menu-item index="/history">
          <el-icon><Clock /></el-icon>
          <span>会话历史</span>
        </el-menu-item>

        <li class="menu-group">校园服务</li>
        <el-menu-item index="/notices">
          <el-icon><Bell /></el-icon>
          <span>校园公告</span>
        </el-menu-item>
        <el-menu-item index="/services">
          <el-icon><Suitcase /></el-icon>
          <span>校园服务</span>
        </el-menu-item>

        <li class="menu-group">系统</li>
        <el-menu-item v-if="auth.isAdmin" index="/admin/tickets">
          <el-icon><Tickets /></el-icon>
          <span>工单处理</span>
        </el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/admin/users">
          <el-icon><UserFilled /></el-icon>
          <span>用户管理</span>
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
        <div class="status-grid">
          <div class="status-cell">
            <span>向量库</span>
            <b>{{ health?.modes?.vector || '-' }}</b>
          </div>
          <div class="status-cell">
            <span>知识图谱</span>
            <b>{{ health?.modes?.graph || '-' }}</b>
          </div>
        </div>
        <div v-if="health && !health.vector_ready" class="mode-warn">向量未预热完成</div>
        <div class="aside-motto">追求卓越 · 走向成功</div>
      </div>
    </el-aside>

    <el-container>
      <el-header class="app-header">
        <div class="page-title">{{ route.meta.title || '校园服务智能问答' }}</div>
        <div class="header-right">
          <span class="meta-chip">
            <i class="dot" :class="{ 'is-warn': health?.status !== 'ok' }"></i>
            {{ health ? (health.status === 'ok' ? '服务正常' : `后端 ${health.status}`) : '后端未连接' }}
          </span>
          <span v-if="health?.llm_configured" class="meta-chip is-muted">DeepSeek</span>
          <span v-if="health" class="meta-chip is-muted">{{ health.graph.agent_node_count }} Agent 节点</span>
          <span v-if="auth.user" class="meta-chip is-brand">
            {{ auth.isAdmin ? '管理员' : '学生' }} · {{ auth.displayName }}
          </span>
          <el-button link type="primary" @click="refresh">刷新</el-button>
          <el-button link type="danger" @click="handleLogout">退出</el-button>
        </div>
      </el-header>

      <NoticeBanner />

      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell,
  ChatDotRound,
  Clock,
  Document,
  InfoFilled,
  Suitcase,
  Tickets,
  UserFilled,
} from '@element-plus/icons-vue'
import NoticeBanner from './components/NoticeBanner.vue'
import emblem from './assets/wut-emblem.jpg'
import { fetchHealth } from './api/client'
import { useAuthStore } from './stores/auth'
import type { HealthInfo } from './types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const health = ref<HealthInfo | null>(null)
const activeMenu = computed(() => route.path)
const isLoginPage = computed(() => route.path === '/login')

async function refresh() {
  try {
    health.value = await fetchHealth()
  } catch {
    health.value = null
  }
}

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}

onMounted(async () => {
  await refresh()
  if (auth.isLoggedIn) await auth.refreshMe()
})
</script>
