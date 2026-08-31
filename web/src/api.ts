const fieldNames: Record<string, string> = {
  name: '企业名称', slug: '企业标识', admin_email: '管理员邮箱', admin_name: '管理员姓名',
  email: '邮箱', display_name: '姓名', company_role: '公司身份', tenant_id: '所属公司',
  requirements_text: '具体实施需求', departments: '部门清单', contact_email: '联系邮箱',
}

function validationMessage(item: any): string {
  const field = Array.isArray(item?.loc) ? String(item.loc[item.loc.length - 1] || '') : ''
  const label = fieldNames[field] || field || '提交内容'
  if (field === 'slug' && item?.type === 'string_pattern_mismatch') {
    return `${label}只能使用小写字母、数字和连字符（-），例如 company-a`
  }
  const translations: Record<string, string> = {
    missing: '为必填项', string_too_short: '填写内容太短', string_too_long: '填写内容太长',
    value_error: '格式不正确', greater_than_equal: '数值低于允许范围', less_than_equal: '数值超过允许范围',
  }
  return `${label}：${translations[item?.type] || item?.msg || '格式不正确'}`
}

function apiErrorMessage(body: any, status: number): string {
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(validationMessage).join('；')
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string') return detail.message
    return '提交内容格式不正确，请检查各字段后重试'
  }
  return `请求失败（${status}）`
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {})
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(apiErrorMessage(body, response.status))
  return (body.data ?? body) as T
}

export function writeHeaders(): HeadersInit {
  const csrf = document.cookie.split('; ').find(item => item.startsWith('saas_csrf='))?.split('=')[1] || ''
  return { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID(), 'X-CSRF-Token': decodeURIComponent(csrf) }
}
