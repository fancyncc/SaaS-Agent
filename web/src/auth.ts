import { defineStore } from 'pinia'
import { api, writeHeaders } from './api'

export interface CurrentUser {
  id: string; email: string; display_name: string; tenant_id: string | null; tenant_name: string | null
  account_type: 'platform' | 'customer'; session_context: 'platform' | 'customer'
  role: 'tenant_admin' | 'tenant_member' | 'platform_admin'; is_platform_admin: boolean
  company_role_code: 'company_admin' | 'company_member' | ''; platform_roles: string[]
  memberships: { tenant_id: string; tenant_name: string; role: string; company_role_code: string }[]
}

export const useAuthStore = defineStore('auth', {
  state: () => ({ user: null as CurrentUser | null, loaded: false }),
  getters: {
    isPlatform: state => state.user?.session_context === 'platform',
    isCompanyAdmin: state => state.user?.session_context === 'customer' && state.user?.company_role_code === 'company_admin',
  },
  actions: {
    async load() {
      try { this.user = await api<CurrentUser>('/api/auth/me') } catch { this.user = null }
      this.loaded = true
      return this.user
    },
    async login(email: string, password: string) {
      await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })
      await this.load()
    },
    async platformLogin(email: string, password: string) {
      await api('/api/auth/platform/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })
      await this.load()
    },
    async logout() {
      await api('/api/auth/logout', { method: 'POST', headers: writeHeaders() })
      this.user = null
    },
  },
})
