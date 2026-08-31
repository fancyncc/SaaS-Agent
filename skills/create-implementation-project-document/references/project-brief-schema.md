# Implementation Project Brief Schema

## Required fields

| Field | Type | Rule | Purpose |
|---|---|---|---|
| `name` | string | 2–120 characters | Unique, action-oriented project name |
| `customer_name` | string | 2–120 characters | Customer legal or commonly used name |
| `customer_contact` | string | 2–80 characters | Primary customer coordinator |
| `contact_email` | string | Valid email | Notifications and material follow-up |
| `employee_count` | integer | 1–100000 | Implementation scale and migration planning |
| `target_go_live_date` | date | `YYYY-MM-DD` | Schedule and milestone baseline |
| `departments` | string list | At least one, unique | Organization and permission design |
| `requirements_text` | string | 20–10000 characters | Concrete implementation requirements and scope boundary |

## Optional fields

| Field | Type | Purpose |
|---|---|---|
| `industry` | string | Industry-specific assumptions and terminology |
| `contact_phone` | string | Alternative coordination channel |
| `consultant_name` | string | Internal implementation owner |
| `migration_scope` | string | Data types, sources, volume, quality, exclusions |
| `acceptance_criteria` | string | Verifiable go-live and customer sign-off conditions |
| `notes` | string | Risks, constraints, dependencies, or open questions |

## Required requirement structure

Write complete statements instead of module labels. Each applicable statement should identify the actor, scenario, action, constraints or permission boundary, and expected result. Cover the applicable dimensions:

1. Organization: departments, employee scale, workspace boundaries.
2. Permissions: roles, restricted actions, approval separation.
3. Workflow: templates, task statuses, custom fields, notifications.
4. Migration: source format, rows, required fields, duplicates, exclusions.
5. Training: audiences, delivery format, materials, schedule.
6. Go-live: target date, checklist owner, acceptance decision maker.

## Example API payload

```json
{
  "name": "星河设计客户上线实施",
  "customer_name": "星河设计公司",
  "customer_contact": "林岚",
  "contact_email": "linlan@example.com",
  "employee_count": 80,
  "target_go_live_date": "2026-10-15",
  "departments": ["设计部", "市场部", "财务部"],
  "requirements_text": "设计部负责人可以创建项目并审批设计交付物；市场部成员只能查看本部门项目，不能删除项目；项目任务按待办、进行中、待审核、完成流转；通过 CSV 导入 80 名成员并按部门和角色分配权限；上线前完成管理员与普通成员培训。",
  "industry": "创意设计",
  "contact_phone": "",
  "consultant_name": "",
  "migration_scope": "导入 80 名成员及部门、角色关系，不迁移历史项目。",
  "acceptance_criteria": "成员导入成功率 100%，权限抽查通过，关键模板可创建项目。",
  "notes": "权限方案需由客户主管审批。"
}
```
