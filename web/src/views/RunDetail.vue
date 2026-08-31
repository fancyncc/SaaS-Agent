<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, writeHeaders } from '../api'
import type { AgentRun, AgentStep, Approval, Project } from '../types'

const route = useRoute()
const runId = String(route.params.id)
const run = ref<AgentRun | null>(null), project = ref<Project | null>(null)
const steps = ref<AgentStep[]>([]), approvals = ref<Approval[]>([])
const error = ref(''), message = ref(''), decisionComment = ref('')
let eventSource: EventSource | null = null
const pending = computed(() => approvals.value.filter(item => item.status === 'pending'))
const canApprove = computed(() => project.value?.permissions?.includes('approval.decide'))
const projectRoleNames: Record<string,string> = {
  project_manager: '项目负责人', implementation_consultant: '实施顾问', approver: '审批人',
  customer_contact: '客户联系人', viewer: '只读成员', company_admin: '公司管理员',
}
const approvalUnavailableMessage = computed(() => {
  const role = projectRoleNames[project.value?.my_project_role || ''] || project.value?.my_project_role || '当前角色'
  return `当前项目角色为“${role}”，不含审批决定权限。请由公司管理员为另一名成员设置“审批人”项目角色；启动本次 Run 的账号不能审批自己的请求。`
})
const progressedNodeCount = computed(() => run.value?.state.completed_nodes?.length || 0)
const waitingForApproval = computed(() => run.value?.status === 'waiting_approval')
const progressMessage = computed(() => {
  if (!run.value || !waitingForApproval.value) return ''
  const node = nodeNames[run.value.current_node] || run.value.current_node
  return `流程已推进到第 ${progressedNodeCount.value} / 17 个节点，当前暂停等待“${node}”。批准后 Agent 会自动继续，遇到下一个审批门时会再次暂停。`
})
const canCancel = computed(() => project.value?.permissions?.includes('run.cancel') && !!run.value && ['pending','running','waiting_approval'].includes(run.value.status))
const statusNames: Record<string,string> = { pending:'等待执行', running:'执行中', waiting_approval:'等待审批', failed:'执行终止', succeeded:'执行成功', cancelled:'已取消' }
const nodeNames: Record<string,string> = {
  create_project:'创建项目', collect_requirements:'提取客户需求', detect_missing_information:'历史资料检查（已移除）', retrieve_product_knowledge:'检索产品知识', gap_analysis:'生成能力差距', generate_implementation_plan:'生成实施计划', plan_approval:'实施计划审批', inspect_tenant_configuration:'检查租户配置', generate_configuration_changes:'生成配置差异', configuration_approval:'配置变更审批', apply_configuration:'应用租户配置', validate_import_files:'校验导入文件', import_approval:'数据导入审批', execute_import:'执行数据导入', generate_training_materials:'生成培训材料', run_go_live_checks:'运行上线检查', acceptance_approval:'上线验收审批', close_project:'关闭实施项目',
}
const approvalNames: Record<string,string> = { plan:'实施计划', configuration:'配置变更', import:'数据导入', acceptance:'上线验收', evidence:'证据确认' }
const approvalStatusNames: Record<string,string> = { pending:'待审批', approved:'已批准', rejected:'已驳回', expired:'已过期', cancelled:'已取消' }

async function load() {
  run.value = await api<AgentRun>(`/api/runs/${runId}`)
  const [projectData, stepData, approvalData] = await Promise.all([
    api<Project>(`/api/projects/${run.value.project_id}`), api<AgentStep[]>(`/api/runs/${runId}/steps`), api<Approval[]>(`/api/approvals?run_id=${runId}`),
  ])
  project.value = projectData; steps.value = stepData; approvals.value = approvalData
}
async function decide(item: Approval, decision: 'approved'|'rejected') {
  error.value = ''; message.value = ''
  if (decision === 'rejected' && !decisionComment.value.trim()) { error.value = '驳回时必须填写原因，便于项目团队修订。'; return }
  try {
    await api(`/api/approvals/${item.id}/decision`, { method:'POST', headers:writeHeaders(), body:JSON.stringify({ decision, comment:decisionComment.value.trim(), expected_version:item.version }) })
    decisionComment.value = ''; message.value = decision === 'approved' ? '审批已通过，Agent 已继续执行。' : '审批已驳回，项目已标记。'
    await load()
  } catch (e:any) { error.value = e.message }
}
async function cancelRun() {
  if (!run.value || !window.confirm(`确定取消 Run #${run.value.run_number} 吗？项目将进入阻塞状态，可随后创建重试 Run。`)) return
  error.value = ''; message.value = ''
  try {
    await api(`/api/runs/${run.value.id}/cancel`, { method:'POST', headers:writeHeaders() })
    message.value = '本次 Run 已取消，项目可以修订后重试。'
    await load()
  } catch (e:any) { error.value = e.message }
}
function connectEvents() {
  eventSource = new EventSource(`/api/runs/${runId}/events`)
  eventSource.addEventListener('run', () => load().catch(() => {}))
  eventSource.onerror = () => eventSource?.close()
}
onMounted(async () => { try { await load(); connectEvents() } catch(e:any) { error.value=e.message } })
onBeforeUnmount(() => eventSource?.close())
</script>

<template>
  <main class="run-page page-wrap">
    <router-link class="back-link" to="/app">← 返回实施项目</router-link>
    <p v-if="error" class="alert alert-danger">{{error}}</p><p v-if="message" class="alert alert-success">{{message}}</p>
    <section v-if="run && project" class="run-hero">
      <div><span class="eyebrow">AGENT RUN #{{run.run_number}} · {{run.id.slice(0,8)}}</span><h1>{{project.name}}</h1><p>{{project.customer_name}} · {{project.document?.employee_count || '—'}} 人 · 目标上线 {{project.document?.target_go_live_date || '未填写'}}</p></div>
      <div class="run-status"><span :class="`run-status-${run.status}`">{{statusNames[run.status] || run.status}}</span><strong>{{nodeNames[run.current_node] || run.current_node}}</strong><small>Trace {{run.trace_id}}</small><button v-if="canCancel" class="secondary danger-text" @click="cancelRun">取消本次 Run</button></div>
    </section>

    <div v-if="run" class="run-layout">
      <section class="panel execution-panel">
        <div class="section-heading"><div><span class="step-number">01</span><div><h2>Agent 执行轨迹</h2><p>节点完成后写入数据库，可在中断后恢复。</p></div></div><span class="live-indicator"><i></i>实时同步</span></div>
        <div class="timeline">
          <div v-for="(step,index) in steps" :key="step.id" class="timeline-item" :class="step.node === run.current_node && waitingForApproval ? 'current' : 'completed'"><span class="timeline-index">{{String(index+1).padStart(2,'0')}}</span><div><div class="timeline-title"><strong>{{nodeNames[step.node] || step.node}}</strong><span>{{step.node === run.current_node && waitingForApproval ? '等待审批' : '已完成'}}</span></div><small>{{new Date(step.created_at).toLocaleString()}}</small><div v-if="Object.keys(step.detail || {}).length" class="step-detail"><code>{{JSON.stringify(step.detail,null,2)}}</code></div></div></div>
          <div v-if="!['succeeded','failed','cancelled'].includes(run.status)" class="timeline-item current"><span class="timeline-index">→</span><div><div class="timeline-title"><strong>{{nodeNames[run.current_node] || run.current_node}}</strong><span>当前节点</span></div><small>{{run.status === 'waiting_approval' ? '等待人工审批后继续' : 'Agent 正在处理'}}</small></div></div>
        </div>
      </section>

      <aside class="run-sidebar">
        <section class="panel approval-panel">
          <div class="section-heading compact"><div><span class="step-number">02</span><div><h2>审批流程</h2><p>{{pending.length}} 项待处理</p></div></div></div>
          <div v-if="!approvals.length" class="empty-mini">尚未产生审批记录</div>
          <article v-for="item in approvals" :key="item.id" class="approval-card" :class="`approval-${item.status}`">
            <div class="approval-head"><strong>{{approvalNames[item.kind] || item.kind}}</strong><span>{{approvalStatusNames[item.status] || item.status}}</span></div>
            <small>{{new Date(item.created_at).toLocaleString()}}</small><p v-if="item.comment" class="approval-comment">“{{item.comment}}”</p><p v-if="item.decided_by" class="decided-by">处理人：{{item.decided_by}}</p>
            <div v-if="item.status === 'pending' && canApprove" class="decision-box"><textarea v-model="decisionComment" placeholder="填写审批意见；驳回时必填"></textarea><div><button class="secondary danger-text" @click="decide(item,'rejected')">驳回并终止</button><button class="primary" @click="decide(item,'approved')">批准并继续</button></div></div>
            <p v-else-if="item.status === 'pending'" class="locked-action">{{approvalUnavailableMessage}}</p>
          </article>
        </section>
        <section class="panel summary-panel"><h2>本次执行摘要</h2><p v-if="progressMessage" class="locked-action">{{progressMessage}}</p><dl><div><dt>已推进节点</dt><dd>{{progressedNodeCount}} / 17</dd></div><div><dt>结构化需求</dt><dd>{{run.state.requirements?.length || 0}} 项</dd></div><div><dt>能力差距</dt><dd>{{run.state.gap_items?.length || 0}} 项</dd></div><div><dt>配置变更</dt><dd>{{run.state.configuration_changes?.length || 0}} 项</dd></div></dl><details><summary>查看结构化状态</summary><pre>{{JSON.stringify(run.state,null,2)}}</pre></details></section>
      </aside>
    </div>
  </main>
</template>
