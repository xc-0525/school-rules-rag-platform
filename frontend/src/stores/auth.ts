import { defineStore } from 'pinia'
import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  updateProfile as apiUpdateProfile,
} from '../api/client'
import { clearSession, getToken, getUser, setSession } from '../utils/session'
import { useChatStore } from './chat'
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
    /** 身份画像未设置：登录后引导补全（不硬猜学历） */
    needsProfile: (state) =>
      !!state.user && (state.user.education_level || 'unset') === 'unset',
    educationLabel: (state) => state.user?.education_label || '未设置',
  },

  actions: {
    async login(username: string, password: string): Promise<UserInfo> {
      const response = await apiLogin(username, password)
      this.token = response.token
      this.user = response.user
      setSession(response.token, response.user)
      // 换号登录必须清掉上一个用户的内存聊天流，否则会串号
      useChatStore().resetConversation()
      return response.user
    },

    async register(
      username: string,
      password: string,
      nickname?: string,
      educationLevel?: string,
    ): Promise<UserInfo> {
      const response = await apiRegister(username, password, nickname, educationLevel)
      this.token = response.token
      this.user = response.user
      setSession(response.token, response.user)
      useChatStore().resetConversation()
      return response.user
    },

    async saveProfile(payload: {
      education_level?: string
      college?: string
      grade?: string
    }): Promise<UserInfo> {
      const response = await apiUpdateProfile(payload)
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
        useChatStore().resetConversation()
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
