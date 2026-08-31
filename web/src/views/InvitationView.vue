<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuthStore } from '../auth'
const name=ref(''), password=ref(''), confirm=ref(''), error=ref(''), loading=ref(false)
const route=useRoute(), router=useRouter(), auth=useAuthStore()
const token=computed(()=>String(route.query.token||''))
async function submit(){ error.value=''; if(password.value!==confirm.value){error.value='两次密码不一致';return} loading.value=true; try{await api(`/api/auth/invitations/${String(route.query.token||'')}/accept`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:name.value,password:password.value})});await auth.load();await router.push('/')}catch(e:any){error.value=e.message}finally{loading.value=false} }
</script>
<template><main class="auth-page"><section class="auth-card"><span class="eyebrow">INVITATION</span><h1>接受企业邀请</h1><p>设置姓名和密码后即可进入被邀请的企业空间。邀请链接 48 小时有效且只能使用一次。</p><p v-if="!token" class="alert alert-danger">当前地址缺少邀请令牌，请向管理员索取完整的接受邀请链接。</p><form v-else @submit.prevent="submit"><label><span>姓名</span><input v-model.trim="name" required minlength="2"></label><label><span>密码</span><input v-model="password" type="password" required minlength="10"><small>至少 10 位，同时包含字母和数字</small></label><label><span>确认密码</span><input v-model="confirm" type="password" required></label><p v-if="error" class="alert alert-danger">{{error}}</p><button class="primary" :disabled="loading">{{loading?'处理中…':'接受邀请并登录'}}</button></form><router-link v-if="!token" to="/login">返回登录</router-link></section></main></template>
