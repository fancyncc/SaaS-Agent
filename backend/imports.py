from __future__ import annotations

import csv
import io
import re

from backend.schemas import FieldMapping, ImportErrorItem, ImportValidationResult

ALIASES = {
    "name": {"name", "姓名", "成员姓名"},
    "email": {"email", "邮箱", "邮件"},
    "department": {"department", "部门", "dept"},
    "role": {"role", "角色"},
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_member_csv(csv_text: str) -> ImportValidationResult:
    stream = io.StringIO(csv_text.lstrip("\ufeff"))
    reader = csv.DictReader(stream)
    headers = reader.fieldnames or []
    canonical: dict[str, str] = {}
    mappings: list[FieldMapping] = []
    for header in headers:
        normalized = header.strip().lower()
        target = next((key for key, aliases in ALIASES.items() if normalized in aliases), None)
        if target:
            canonical[target] = header
            mappings.append(FieldMapping(source=header, target=target, required=True))

    errors: list[ImportErrorItem] = []
    for required in ALIASES:
        if required not in canonical:
            errors.append(ImportErrorItem(row=1, field=required, message="缺少必填列", suggestion=f"添加 {required} 列"))

    seen: dict[str, int] = {}
    rows = list(reader)
    if not errors:
        for line, row in enumerate(rows, start=2):
            for field in ("name", "email", "department", "role"):
                if not (row.get(canonical[field]) or "").strip():
                    errors.append(ImportErrorItem(row=line, field=field, message="值为空", suggestion="补充必填值"))
            email = (row.get(canonical["email"]) or "").strip().lower()
            if email and not EMAIL.match(email):
                errors.append(ImportErrorItem(row=line, field="email", message="邮箱格式无效", suggestion="使用 name@example.com 格式"))
            if email in seen:
                errors.append(ImportErrorItem(row=line, field="email", message=f"与第 {seen[email]} 行重复", suggestion="删除或修改重复邮箱"))
            elif email:
                seen[email] = line

    return ImportValidationResult(valid=not errors, row_count=len(rows), mappings=mappings, errors=errors)

