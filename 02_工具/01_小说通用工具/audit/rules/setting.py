"""
设定校验规则 (setting.py)
校验 01_设定/ 目录的文件结构、编码、对象引用与冗余下级定义
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.reference_resolver import ReferenceResolver


class SettingRule(AuditRule):
    name = "setting"
    code_prefix = "SETTING"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        setting_files = [fi for fi in context.files if fi.data_domain == "01_设定"]
        if not setting_files:
            return findings

        resolver = ReferenceResolver(context)

        for fi in setting_files:
            # 1. 空文件/文件异常
            if not fi.content.strip():
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="SETTING001",
                    message="设定文件内容为空",
                    file=fi.relative_path,
                    suggestion="补充设定内容或删除空文件",
                    category="01_设定",
                    locations=[fi.relative_path]
                ))
                continue

            # 2. 验证设定文件引用的实体
            file_refs = resolver.extract_references(fi)
            for ref in file_refs:
                if ref.reference_type == "object" and ref.status == "UNRESOLVED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="SETTING002",
                        message=f"设定文档中引用的 [{ref.entity_type}]「{ref.entity_name}」在数据库中不存在",
                        file=fi.relative_path,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion=f"确认 {ref.entity_name} 是否拼写错误，或在 02_数据库 中补充建卡",
                        category=ref.entity_type,
                        locations=[f"{fi.relative_path}:第{ref.source_line}行"]
                    ))

        return findings
