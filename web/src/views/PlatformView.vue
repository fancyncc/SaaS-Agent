<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'

const auth = useAuthStore()
const tab = ref('overview'); const error = ref(''); const message = ref('')
const dashboard = ref<any>({}); const tenants = ref<any[]>([]); const users = ref<any[]>([])
const staff = ref<any[]>([]); const audit = ref<any[]>([]); const system = ref<any>({})
const supportGrants = ref<any[]>([]); const supportTargets = ref<any[]>([])
const query = ref(''); const selectedUser = ref<any>(null); const inspectedTenant = ref('')
const inspectedProjects = ref<any[]>([]); const inspectedProject = ref<any>(null)
const latestLink = ref('')
const tenantForm = ref({name:'', slug:'', admin_email:'', admin_name:'公司管理员'})
const transferForm = ref({target_tenant_id:'', company_role:'tenant_member', reason:''})
const staffForm = ref({email:'', display_name:'', role_code:'platform_support'})
const supportForm = ref({target_tenant_id:'', target_project_id:'', reason:'', permission_codes:['project.view'], expires_at:''})
const isSuper = computed(() => auth.user?.platform_roles.includes('platform_super_admin'))
const canGovern = computed(() => isSuper.value || auth.user?.platform_roles.includes('platform_operator'))

async function load() {
  error.value = ''
  try {
    dashboard.value = await api('/api/platform/dashboard')
    tenants.value = await api('/api/platform/tenants')
    users.value = await api(`/api/platform/users${query.value ? `?q=${encodeURIComponent(query.value)}` : ''}`)
    if (isSuper.value) staff.value = await api('/api/platform/staff')
    if (isSuper.value || auth.user?.platform_roles.includes('platform_operator') || auth.user?.platform_roles.includes('platform_auditor')) {
      audit.value = await api('/api/platform/audit-events')
    }
    if (isSuper.value || auth.user?.platform_roles.includes('platform_support') || auth.user?.platform_roles.includes('platform_auditor')) {
      system.value = await api('/api/platform/system-status')
    }
    if (isSuper.value || auth.user?.platform_roles.includes('platform_support')) {
      supportGrants.value = await api('/api/platform/support-access-grants')
    }
  } catch (e:any) { error.value = e.message }
}

async function createTenant() {
  try {
    const data = await api<any>('/api/platform/tenants', {method:'POST', headers:writeHeaders(), body:JSON.stringify(tenantForm.value)})
    latestLink.value = data.invitation_url || ''; message.value = '公司已创建，首位管理员邀请已生成。'
    tenantForm.value = {name:'', slug:'', admin_email:'', admin_name:'公司管理员'}; await load()
  } catch(e:any) { error.value = e.message }
}
async function transferUser() {
  if (!selectedUser.value) return
  if (!window.confirm(`确定将 ${selectedUser.value.email} 转移到所选公司吗？旧会话和旧项目权限将立即失效。`)) return
  try {
    await api(`/api/platform/users/${selectedUser.value.id}/transfer-company`, {
      method:'POST', headers:writeHeaders(), body:JSON.stringify(transferForm.value),
    })
    message.value='公司归属已纠正，用户需要重新登录。'; selectedUser.value=null; await load()
  } catch(e:any) { error.value=e.message }
}
async function revokeSessions(item:any) {
  if (!window.confirm(`撤销 ${item.email} 的全部登录会话吗？`)) return
  try { await api(`/api/platform/users/${item.id}/revoke-sessions`, {method:'POST',headers:writeHeaders()}); message.value='会话已撤销。' }
  catch(e:any){error.value=e.message}
}
async function inviteStaff() {
  try {
    const data=await api<any>('/api/platform/staff/invitations',{method:'POST',headers:writeHeaders(),body:JSON.stringify(staffForm.value)})
    latestLink.value=data.invitation_url||''; message.value='平台人员邀请已创建。'; await load()
  } catch(e:any){error.value=e.message}
}
async function inspectTenant() {
  inspectedProject.value=null
  inspectedProjects.value=inspectedTenant.value ? await api(`/api/platform/inspect/tenants/${inspectedTenant.value}/projects`) : []
}
async function inspect(item:any) { inspectedProject.value=await api(`/api/platform/inspect/projects/${item.id}`) }
async function loadSupportTargets(){supportForm.value.target_project_id='';supportTargets.value=supportForm.value.target_tenant_id?await api(`/api/platform/support-access-grants/targets/${supportForm.value.target_tenant_id}`):[]}
async function requestSupport(){
  try {
    const expiresAt = supportForm.value.expires_at
      ? new Date(supportForm.value.expires_at).toISOString()
      : null
    await api('/api/platform/support-access-grants', {
      method: 'POST',
      headers: writeHeaders(),
      body: JSON.stringify({ ...supportForm.value, expires_at: expiresAt }),
    })
    message.value = '只读支持访问申请已提交。'
    supportGrants.value = await api('/api/platform/support-access-grants')
  } catch (e:any) { error.value = e.message }
}
async function revokeSupport(item:any){await api(`/api/platform/support-access-grants/${item.id}/revoke`,{method:'POST',headers:writeHeaders()});message.value='支持访问已撤销。';supportGrants.value=await api('/api/platform/support-access-grants')}
async function copyLink(){if(latestLink.value){await navigator.clipboard.writeText(latestLink.value);message.value='邀请链接已复制。'}}
onMounted(load)
</script>

<template><main class="admin-page page-wrap platform-page">
  <div class="admin-title"><div><span class="eyebrow">PLATFORM GOVERNANCE</span><h1>平台管理后台</h1><p>公司治理、用户纠错、平台人员、审计与只读排障。</p></div></div>
  <p v-if="error" class="alert alert-danger">{{error}}</p><p v-if="message" class="alert alert-success">{{message}}</p>
  <div v-if="latestLink" class="invitation-delivery"><code>{{latestLink}}</code><button class="secondary" @click="copyLink">复制链接</button></div>
  <nav class="admin-tabs"><button v-for="item in [['overview','总览'],['tenants','公司'],['users','客户用户'],['staff','平台人员'],['inspect','客户只读检查'],['support','支持访问'],['audit','全局审计'],['system','系统状态']]" :key="item[0]" :class="{active:tab===item[0]}" @click="tab=item[0]">{{item[1]}}</button></nav>

  <section v-if="tab==='overview'" class="admin-grid"><article class="metric"><strong>{{dashboard.tenant_count||0}}</strong><span>公司</span></article><article class="metric"><strong>{{dashboard.customer_user_count||0}}</strong><span>客户用户</span></article><article class="metric"><strong>{{dashboard.platform_user_count||0}}</strong><span>平台人员</span></article><article class="metric"><strong>{{dashboard.failed_run_count||0}}</strong><span>失败 Run</span></article></section>

  <section v-if="tab==='tenants'" class="panel admin-section"><h2>公司治理</h2>
    <form v-if="canGovern" class="inline-form tenant-create-form" @submit.prevent="createTenant"><input v-model="tenantForm.name" required minlength="2" placeholder="公司名称"><input v-model="tenantForm.slug" required placeholder="company-slug"><input v-model="tenantForm.admin_email" required type="email" placeholder="首位管理员邮箱"><button class="primary">创建并邀请管理员</button></form>
    <table><thead><tr><th>公司</th><th>标识</th><th>状态</th></tr></thead><tbody><tr v-for="x in tenants" :key="x.id"><td>{{x.name}}</td><td>{{x.slug}}</td><td>{{x.status}}</td></tr></tbody></table>
  </section>

  <section v-if="tab==='users'" class="panel admin-section"><h2>客户用户治理</h2><form class="inline-form" @submit.prevent="load"><input v-model="query" placeholder="邮箱或姓名"><button class="secondary">查询</button></form>
    <table><thead><tr><th>用户</th><th>账号类型</th><th>所属公司</th><th>身份</th><th>操作</th></tr></thead><tbody><tr v-for="x in users" :key="x.id"><td>{{x.display_name}}<small>{{x.email}}</small></td><td>{{x.account_type}}</td><td>{{x.tenant_name||'—'}}</td><td>{{x.company_role_code||'—'}}</td><td class="table-actions"><button v-if="canGovern&&x.account_type==='customer'" class="secondary" @click="selectedUser=x">纠正公司</button><button v-if="canGovern" class="secondary" @click="revokeSessions(x)">撤销会话</button></td></tr></tbody></table>
    <div v-if="selectedUser" class="project-access-panel"><h3>纠正 {{selectedUser.email}} 的公司归属</h3><form class="inline-form" @submit.prevent="transferUser"><select v-model="transferForm.target_tenant_id" required><option value="" disabled>目标公司</option><option v-for="x in tenants.filter(t=>t.status==='active')" :key="x.id" :value="x.id">{{x.name}}</option></select><select v-model="transferForm.company_role"><option value="tenant_member">公司成员</option><option value="tenant_admin">公司管理员</option></select><input v-model="transferForm.reason" required minlength="5" placeholder="纠错原因"><button class="primary">确认转移</button><button type="button" class="secondary" @click="selectedUser=null">取消</button></form></div>
  </section>

  <section v-if="tab==='staff'" class="panel admin-section"><h2>平台人员</h2><p v-if="!isSuper">只有平台超级管理员可以管理平台人员。</p><template v-else><form class="inline-form" @submit.prevent="inviteStaff"><input v-model="staffForm.email" type="email" required placeholder="平台人员邮箱"><input v-model="staffForm.display_name" placeholder="姓名"><select v-model="staffForm.role_code"><option value="platform_operator">运营人员</option><option value="platform_support">支持人员</option><option value="platform_auditor">审计人员</option><option value="platform_super_admin">超级管理员</option></select><button class="primary">发送平台邀请</button></form><table><thead><tr><th>人员</th><th>角色</th><th>状态</th></tr></thead><tbody><tr v-for="x in staff" :key="x.user_id+x.role_code"><td>{{x.display_name}}<small>{{x.email}}</small></td><td>{{x.role_code}}</td><td>{{x.status}} / {{x.binding_status}}</td></tr></tbody></table></template></section>

  <section v-if="tab==='inspect'" class="panel admin-section"><h2>客户项目只读检查</h2><p>仅超级管理员可直接读取，页面不提供业务写操作。</p><div v-if="isSuper"><form class="inline-form" @submit.prevent="inspectTenant"><select v-model="inspectedTenant" required><option value="" disabled>选择公司</option><option v-for="x in tenants" :key="x.id" :value="x.id">{{x.name}}</option></select><button class="secondary">加载项目</button></form><table><thead><tr><th>项目</th><th>客户</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="x in inspectedProjects" :key="x.id"><td>{{x.name}}</td><td>{{x.customer_name}}</td><td>{{x.lifecycle_status}}</td><td><button class="secondary" @click="inspect(x)">只读查看</button></td></tr></tbody></table><pre v-if="inspectedProject">{{JSON.stringify(inspectedProject,null,2)}}</pre></div></section>
  <section v-if="tab==='support'" class="panel admin-section"><h2>客户批准的支持访问</h2><form class="inline-form" @submit.prevent="requestSupport"><select v-model="supportForm.target_tenant_id" required @change="loadSupportTargets"><option value="" disabled>目标公司</option><option v-for="x in tenants" :key="x.id" :value="x.id">{{x.name}}</option></select><select v-model="supportForm.target_project_id" required><option value="" disabled>目标项目</option><option v-for="x in supportTargets" :key="x.id" :value="x.id">{{x.name}}</option></select><input v-model="supportForm.reason" required minlength="10" placeholder="排障原因"><input v-model="supportForm.expires_at" type="datetime-local"><button class="primary">提交申请</button></form><table><thead><tr><th>项目</th><th>原因</th><th>权限</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="x in supportGrants" :key="x.id"><td>#{{x.target_project_id.slice(0,8)}}</td><td>{{x.reason}}</td><td>{{x.permission_codes.join('、')}}</td><td>{{x.status}}</td><td><button v-if="x.status==='approved'" class="secondary danger-text" @click="revokeSupport(x)">撤销</button></td></tr></tbody></table></section>
  <section v-if="tab==='audit'" class="panel admin-section"><h2>全局审计</h2><table><thead><tr><th>时间</th><th>公司</th><th>事件</th><th>操作者</th></tr></thead><tbody><tr v-for="x in audit" :key="x.id"><td>{{new Date(x.created_at).toLocaleString()}}</td><td>{{x.tenant_id||'平台'}}</td><td>{{x.event_type}}</td><td>{{x.actor}}</td></tr></tbody></table></section>
  <section v-if="tab==='system'" class="panel admin-section"><h2>系统状态（只读）</h2><pre>{{JSON.stringify(system,null,2)}}</pre></section>
</main></template>
