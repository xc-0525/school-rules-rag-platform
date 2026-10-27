<template>
  <div class="login-page">
    <div class="login-card panel">
      <div class="login-brand">
        <div class="brand-logo">规</div>
        <div>
          <div class="brand-title">校纪校规问答平台</div>
          <div class="brand-sub">RAG · 多智能体协作 · 课程答辩版</div>
        </div>
      </div>

      <el-form :model="form" label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="admin" size="large" clearable />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="admin123"
            size="large"
            show-password
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="submit">
          登 录
        </el-button>
      </el-form>

      <div v-if="error" class="error-text" style="margin-top: 12px">{{ error }}</div>

      <div class="login-demo">
        <span class="muted">演示账号：</span>
        <el-button link type="primary" @click="fill('admin')">admin / admin123（管理员）</el-button>
        <el-button link type="primary" @click="fill('student')">student / student123（学生）</el-button>
      </div>

      <div class="login-tip muted">
        管理员可上传/删除文档；学生可问答、查看自己的历史会话。登录态 8 小时有效。
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ username: 'admin', password: 'admin123' })
const loading = ref(false)
const error = ref('')

async function submit() {
  if (loading.value) return
  error.value = ''
  if (!form.username.trim() || !form.password) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    ElMessage.success(`欢迎，${auth.displayName}`)
    const redirect = (route.query.redirect as string) || '/chat'
    router.push(redirect)
  } catch (err: any) {
    error.value = err?.message || '登录失败'
  } finally {
    loading.value = false
  }
}

function fill(username: string) {
  form.username = username
  form.password = username === 'admin' ? 'admin123' : 'student123'
}
</script>
