"""
规划校验规则 (planning.py)
校验 03_规划/ 目录下对数据库/伏笔/人物/地理/正文章节的引用
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.reference_resolver import ReferenceResolver

CHAPTER_REF_PATTERN = re.compile(r"第(?P<chap>\d+)章(?:\s*→\s*(?P<path>10_正文/[^\s\n\r]+))?")


class PlanningRule(AuditRule):
    name = "planning"
    code_prefix = "PLAN"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        plan_files = [fi for fi in context.files if fi.data_domain == "03_规划"]
        if not plan_files:
            return findings

        resolver = ReferenceResolver(context)

        for fi in plan_files:
            lines = fi.content.splitlines()
            for idx, line in enumerate(lines, 1):
                # 检查 规划 -> 正文 章节链接
                for m in CHAPTER_REF_PATTERN.finditer(line):
                    chap_num = m.group("chap")
                    chap_path = m.group("path")
                    if chap_path:
                        if not context.file_exists(chap_path):
                            findings.append(Finding(
                                severity=Severity.ERROR,
                                rule=self.name,
                                code="PLAN001",
                                message=f"规划中记录的章节路径「{chap_path}」不存在",
                                file=fi.relative_path,
                                line=idx,
                                source=m.group(0),
                                target=chap_path,
                                suggestion=f"创建对应正文章节 {chap_path} 或更新规划文件中的相对路径",
                                category="03_规划",
                                locations=[f"{fi.relative_path}:第{idx}行"]
                            ))

            # 检查对象引用
            file_refs = resolver.extract_references(fi)
            for ref in file_refs:
                if ref.reference_type == "object" and ref.status == "UNRESOLVED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="PLAN002",
                        message=f"规划文档中引用的 [{ref.entity_type}]「{ref.entity_name}」不存在",
                        file=fi.relative_path,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion=f"确认 {ref.entity_name} 的拼写，或在 02_数据库 中创建对应卡片",
                        category=ref.entity_type,
                        locations=[f"{fi.relative_path}:第{ref.source_line}行"]
                    ))

        return findings
