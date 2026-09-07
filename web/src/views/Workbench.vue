<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
const auth = useAuthStore()
const tasks = ref<any[]>([]), documents = ref<any[]>([]), error = ref('')
const form = ref({title:'', version:1, module:'implementation', source:'', license:'', body:''})
async function load() {
  error.value=''
  try { [tasks.value, documents.value] = await Promise.all([api<any[]>('/api/tasks'), api<any[]>('/api/knowledge')]) }
  catch(e:any) { error.value=e.message }
}
async function upload() {
  try { await api('/api/knowledge', {method:'POST', headers:writeHeaders(), body:JSON.stringify(form.value)}); await load() }
  catch(e:any) { error.value=e.message }
}
async function deactivate(id:string) {
  if (!window.confirm('停用后，该版本不再作为新执行的引用依据。是否继续？')) return
  try { await api(`/api/knowledge/${id}/deactivate`, {method:'POST', headers:writeHeaders()}); await load() }
  catch(e:any) { error.value=e.message }
}
onMounted(load)
</script>
<template><main class="page-wrap workspace-workbench">
  <header class="workspace-heading"><div><span class="eyebrow">WORKSPACE HUB</span><h1>待办与知识库</h1><p>处理当前任务，沉淀每一次实施经验。</p></div><router-link class="secondary" to="/app">返回实施项目 →</router-link></header>
  <div v-if="error" class="alert alert-danger" role="alert">{{error === 'Failed to fetch' ? '暂时无法连接服务，请检查连接后重试。' : error}} <button class="secondary" @click="load">重新加载</button></div>
  <section class="panel"><h1>我的待办</h1><p v-if="!tasks.length">暂无需要你处理的任务。</p><article v-for="t in tasks" :key="t.run_id"><router-link :to="`/app/runs/${t.run_id}`">处理任务 {{t.run_id.slice(0,8)}}</router-link><p>{{t.reason || (t.status === 'waiting_approval' ? '等待审批' : '需要整改或补充材料')}}</p></article></section>
  <section class="panel"><h2>公司知识库</h2><p>仅使用有授权的资料。相同标题发布新版本时版本号必须递增。</p>
    <form v-if="auth.isCompanyAdmin" @submit.prevent="upload" class="form-grid cols-2">
      <label>标题<input v-model="form.title" required minlength="2" maxlength="160"></label><label>版本<input v-model.number="form.version" type="number" min="1" required></label>
      <label>模块<input v-model="form.module" required></label><label>来源<input v-model="form.source" required minlength="3" maxlength="500"></label>
      <label class="knowledge-wide">授权说明<input v-model="form.license" required minlength="3" maxlength="500" placeholder="说明资料的使用授权或适用范围"></label><label class="knowledge-wide">资料正文<textarea v-model="form.body" required minlength="20" maxlength="30000" rows="6" placeholder="填写实施方法、操作指引或常见问题…"></textarea></label><div class="knowledge-wide knowledge-submit"><small>同标题的新版本号须递增。</small><button class="primary">发布知识版本</button></div>
    </form>
    <table><thead><tr><th>标题</th><th>版本</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="d in documents" :key="d.id"><td>{{d.title}}</td><td>{{d.version}}</td><td>{{d.source}}</td><td>{{d.active ? '有效' : '停用'}}</td><td><button v-if="auth.isCompanyAdmin && d.active" class="secondary" @click="deactivate(d.id)">停用</button></td></tr></tbody></table>
  </section>
</main></template>
