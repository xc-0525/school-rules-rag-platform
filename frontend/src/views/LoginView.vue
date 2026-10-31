<template>
  <div class="login-page">
    <div class="login-card panel">
      <div class="login-brand">
        <div class="brand-logo">规</div>
        <div>
          <div class="brand-title">校园服务智能问答系统</div>
          <div class="brand-sub">RAG · 多智能体协作 · 课程答辩版</div>
        </div>
      </div>

      <el-tabs v-model="mode" stretch class="login-tabs">
        <el-tab-pane label="登 录" name="login">
          <el-form :model="loginForm" label-position="top" @submit.prevent="submitLogin">
            <el-form-item label="用户名">
              <el-input v-model="loginForm.username" placeholder="请输入用户名" size="large" clearable />
            </el-form-item>
            <el-form-item label="密码">
              <el-input
                v-model="loginForm.password"
                type="password"
                placeholder="请输入密码"
                size="large"
                show-password
                @keyup.enter="submitLogin"
              />
            </el-form-item>
            <el-button
              type="primary"
              size="large"
              style="width: 100%"
              :loading="loading"
              @click="submitLogin"
            >
              登 录
            </el-button>
          </el-form>

          <div class="login-demo">
            <span class="muted">演示账号（密码见交付说明）：</span>
            <el-button link type="primary" @click="fillUsername('admin')">
              admin（管理员）
            </el-button>
            <el-button link type="primary" @click="fillUsername('student')">
              student（学生）
            </el-button>
          </div>
        </el-tab-pane>

        <el-tab-pane label="注 册" name="register">
          <el-form :model="registerForm" label-position="top" @submit.prevent="submitRegister">
            <el-form-item label="用户名">
              <el-input
                v-model="registerForm.username"
                placeholder="3-20 位字母 / 数字 / 下划线"
                size="large"
                clearable
              />
            </el-form-item>
            <el-form-item label="昵称（可选）">
              <el-input v-model="registerForm.nickname" placeholder="例如：张三" size="large" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input
                v-model="registerForm.password"
                type="password"
                placeholder="至少 6 位"
                size="large"
                show-password
              />
            </el-form-item>
            <el-form-item label="确认密码">
              <el-input
                v-model="registerForm.confirm"
                type="password"
                placeholder="再输入一次密码"
                size="large"
                show-password
                @keyup.enter="submitRegister"
              />
            </el-form-item>
            <el-button
              type="primary"
              size="large"
              style="width: 100%"
              :loading="loading"
              @click="submitRegister"
            >
              注册并登录
            </el-button>
          </el-form>
          <div class="login-tip muted">
            注册账号默认为学生角色：可问答、查看自己的历史会话；文档管理仅管理员可操作。
          </div>
        </el-tab-pane>
      </el-tabs>

      <div v-if="error" class="error-text" style="margin-top: 12px">{{ error }}</div>

      <div class="login-tip muted" v-if="mode === 'login'">
        登录态 8 小时有效；管理员账号为预置账号，不开放注册。
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

const mode = ref<'login' | 'register'>('login')
const loading = ref(false)
const error = ref('')

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({ username: '', nickname: '', password: '', confirm: '' })

function redirectAfterLogin() {
  const redirect = (route.query.redirect as string) || '/chat'
  router.push(redirect)
}

async function submitLogin() {
  if (loading.value) return
  error.value = ''
  if (!loginForm.username.trim() || !loginForm.password) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    await auth.login(loginForm.username.trim(), loginForm.password)
    ElMessage.success(`欢迎，${auth.displayName}`)
    redirectAfterLogin()
  } catch (err: any) {
    error.value = err?.message || '登录失败'
  } finally {
    loading.value = false
  }
}

async function submitRegister() {
  if (loading.value) return
  error.value = ''
  const username = registerForm.username.trim()
  if (!/^[A-Za-z0-9_]{3,20}$/.test(username)) {
    error.value = '用户名需为 3-20 位字母、数字或下划线'
    return
  }
  if (registerForm.password.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (registerForm.password !== registerForm.confirm) {
    error.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    await auth.register(username, registerForm.password, registerForm.nickname.trim() || undefined)
    ElMessage.success(`注册成功，欢迎，${auth.displayName}`)
    redirectAfterLogin()
  } catch (err: any) {
    error.value = err?.message || '注册失败'
  } finally {
    loading.value = false
  }
}

function fillUsername(username: string) {
  loginForm.username = username
  loginForm.password = ''
}
</script>
