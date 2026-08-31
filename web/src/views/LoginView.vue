<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../auth'
const email = ref(''), password = ref(''), error = ref(''), loading = ref(false)
const auth = useAuthStore(), router = useRouter(), route = useRoute()
async function submit() {
  error.value = ''; loading.value = true
  try { await auth.login(email.value, password.value); await router.push(String(route.query.redirect || '/')) }
  catch (e:any) { error.value = e.message } finally { loading.value = false }
}
</script>
<template><main class="auth-page"><section class="auth-card"><span class="eyebrow">SECURE WORKSPACE</span><h1>登录客户上线实施中心</h1><p>平台采用管理员邀请制，不开放自由注册。</p><form @submit.prevent="submit"><label><span>邮箱</span><input v-model.trim="email" type="email" required autocomplete="email"></label><label><span>密码</span><input v-model="password" type="password" required autocomplete="current-password"></label><p v-if="error" class="alert alert-danger">{{error}}</p><button class="primary" :disabled="loading">{{loading?'登录中…':'登录'}}</button></form><router-link to="/forgot-password">忘记密码？</router-link></section></main></template>
