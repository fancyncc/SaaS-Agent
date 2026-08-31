<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import { useAuthStore } from './auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
async function logout() { await auth.logout(); await router.push('/login') }
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <router-link class="brand" :to="auth.isPlatform ? '/platform' : '/app'">
        <span class="brand-mark">A</span>
        <span><small>IMPLEMENTATION OS</small><strong>客户上线实施中心</strong></span>
      </router-link>
      <div class="header-context">
        <span class="system-dot"></span><span>系统运行正常</span>
        <span v-if="route.name === 'run'" class="context-divider">Agent 执行空间</span>
        <template v-if="auth.user">
          <router-link v-if="auth.isCompanyAdmin" class="header-link" to="/app/company">公司设置</router-link>
          <router-link v-if="auth.isPlatform" class="header-link" to="/platform">平台后台</router-link>
          <span>{{auth.user.display_name}} · {{auth.isPlatform ? '平台人员' : auth.user.tenant_name}}</span>
          <button class="header-button" @click="logout">退出</button>
        </template>
      </div>
    </header>
    <router-view />
  </div>
</template>
