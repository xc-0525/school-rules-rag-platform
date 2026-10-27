import { defineStore } from 'pinia'
import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
} from '../api/client'
import { clearSession, getToken, getUser, setSession } from '../utils/session'
import type { UserInfo } from '../types'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: getToken(),
    user: getUser() as UserInfo | null,
  }),

  getters: {
    isLoggedIn: (state) => !!state.token,
    isAdmin: (state) => state.user?.role === 'admin',
    displayName: (state) => state.user?.nickname || state.user?.username || '未登录',
  },

  actions: {
    async login(username: string, password: string): Promise<UserInfo> {
      const response = await apiLogin(username, password)
      this.token = response.token
      this.user = response.user
      setSession(response.token, response.user)
      return response.user
    },

    async register(username: string, password: string, nickname?: string): Promise<UserInfo> {
      const response = await apiRegister(username, password, nickname)
      this.token = response.token
      this.user = response.user
      setSession(response.token, response.user)
      return response.user
    },

    async logout(): Promise<void> {
      try {
        if (this.token) await apiLogout()
      } catch {
        /* 退出失败也要清本地 */
      } finally {
        this.token = null
        this.user = null
        clearSession()
      }
    },

    async refreshMe(): Promise<UserInfo | null> {
      if (!this.token) return null
      try {
        const user = await fetchMe()
        this.user = user
        setSession(this.token, user)
        return user
      } catch {
        this.token = null
        this.user = null
        clearSession()
        return null
      }
    },
  },
})
