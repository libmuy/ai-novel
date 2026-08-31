"""
正文校验规则 (manuscript.py)
校验 10_正文/ 目录的纯净性及对象引用正确性
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.reference_resolver import ReferenceResolver


class ManuscriptRule(AuditRule):
    name = "manuscript"
    code_prefix = "MANUSCRIPT"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        manuscript_files = [fi for fi in context.files if fi.data_domain == "10_正文"]
        if not manuscript_files:
            return findings

        ref_pattern = re.compile(r"@(地名|势力|人物|类型|书籍|伏笔|区域)\.")
        resolver = ReferenceResolver(context)

        for fi in manuscript_files:
            lines = fi.content.splitlines()
            for idx, line in enumerate(lines, 1):
                # 1. 检查数据引用语法残留 (ERROR)
                if ref_pattern.search(line):
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="MANUSCRIPT001",
                        message=f"正文中存在数据引用语法残留「{line.strip()}」",
                        file=fi.relative_path,
                        line=idx,
                        source=line.strip(),
                        suggestion="正文为最终读者成稿，请删除或更正其中的数据层引用语法 @类型.",
                        locations=[f"{fi.relative_path}:第{idx}行"]
                    ))

            # 2. 正文引用对象的有效性校验
            file_refs = resolver.extract_references(fi)
            for ref in file_refs:
                if ref.reference_type == "object" and ref.status == "UNRESOLVED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="MANUSCRIPT002",
                        message=f"正文中引用的对象「{ref.entity_name}」在数据库中不存在",
                        file=fi.relative_path,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion="核对正文中引用的实体名，确保在数据库中存在或订正拼写",
                        category=ref.entity_type,
                        locations=[f"{fi.relative_path}:第{ref.source_line}行"]
                    ))

        return findings
