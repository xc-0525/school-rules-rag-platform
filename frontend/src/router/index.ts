import { createRouter, createWebHistory } from 'vue-router'
import { getToken, getUser } from '../utils/session'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { title: '登录', public: true },
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('../views/ChatView.vue'),
      meta: { title: '智能问答' },
    },
    {
      path: '/history',
      name: 'history',
      component: () => import('../views/HistoryView.vue'),
      meta: { title: '会话历史' },
    },
    {
      path: '/notices',
      name: 'notices',
      component: () => import('../views/NoticesView.vue'),
      meta: { title: '校园公告' },
    },
    {
      path: '/services',
      name: 'services',
      component: () => import('../views/ServicesView.vue'),
      meta: { title: '校园服务' },
    },
    {
      path: '/documents',
      name: 'documents',
      component: () => import('../views/DocumentsView.vue'),
      meta: { title: '文档管理' },
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('../views/UserAdminView.vue'),
      // 仅管理员：菜单不显示 + 守卫拦截手输 URL
      meta: { title: '用户管理', requiresAdmin: true },
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('../views/AboutView.vue'),
      meta: { title: '关于与状态' },
    },
  ],
})

router.beforeEach((to) => {
  const loggedIn = !!getToken()
  if (to.meta.public) {
    if (loggedIn && to.path === '/login') return { path: '/chat' }
    return true
  }
  if (!loggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && getUser()?.role !== 'admin') {
    return { path: '/chat' }
  }
  return true
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '校园服务智能问答'} · 校园服务智能问答系统`
})

export default router
