from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    title: str
    module: str
    text: str
    version: str = "2026.1"


# Deterministic local corpus keeps the demo usable without an embedding API.
# Production deployments can replace `search` with pgvector + FTS using this interface.
CORPUS = [
    KnowledgeChunk("product-rbac", "角色与权限", "security", "支持管理员、项目经理、成员、访客角色和自定义权限组。"),
    KnowledgeChunk("product-sso", "企业身份认证", "security", "企业版支持 OIDC 单点登录；暂不支持 SAML 即时配置。"),
    KnowledgeChunk("product-workflow", "任务状态流", "workflow", "项目模板可定义待办、进行中、审核中和完成状态。"),
    KnowledgeChunk("product-fields", "自定义字段", "project", "任务支持文本、数字、日期、单选和成员类型自定义字段。"),
    KnowledgeChunk("product-notify", "通知规则", "notification", "支持任务分配、到期和状态变化的站内通知。"),
    KnowledgeChunk("import-members", "成员 CSV 导入", "import", "必填列为 name、email、department、role；邮箱必须唯一。"),
    KnowledgeChunk("import-projects", "项目 CSV 导入", "import", "项目导入支持 name、owner_email、template 列。"),
    KnowledgeChunk("sop-discovery", "需求调研 SOP", "implementation", "实施前确认人数、部门、角色、模板、审批人和目标上线日期。"),
    KnowledgeChunk("sop-config", "配置变更 SOP", "implementation", "权限、工作流和通知配置变更必须预览、审批、快照和验证。"),
    KnowledgeChunk("sop-go-live", "上线检查 SOP", "implementation", "上线前检查管理员、成员、权限、模板、导入结果、培训和未关闭阻塞项。"),
    KnowledgeChunk("case-design", "设计公司实施案例", "case", "80 人设计公司采用三阶段：调研规划、配置迁移、培训上线。"),
    KnowledgeChunk("error-duplicate", "重复邮箱错误", "import", "重复邮箱行不会导入；合并或修改重复记录后重新校验。"),
]


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.lower()))


def search(query: str, module: str | None = None, limit: int = 5) -> list[dict]:
    query_tokens = _tokens(query)
    ranked: list[tuple[float, KnowledgeChunk]] = []
    for chunk in CORPUS:
        if module and chunk.module != module:
            continue
        text_tokens = _tokens(f"{chunk.title} {chunk.text}")
        overlap = len(query_tokens & text_tokens)
        score = overlap / max(len(query_tokens), 1)
        if score > 0:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return [
        {"id": c.id, "title": c.title, "module": c.module, "text": c.text, "score": round(score, 3)}
        for score, c in ranked[:limit]
    ]

